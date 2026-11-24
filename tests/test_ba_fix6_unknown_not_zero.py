"""BA fix 6 — three defaults declared as unknown, not reported as zero.

THE RULE is the aborted-cycle rule (kairos.py): not measured is not
reported as a number. Three sites broke it (audit A5):

  extract_triples   a provider failure returned [], so `extracted: 0`
                    read as "the model produced no claims";
  storage failure   after the gate admitted a belief, a bare `except: pass`
                    dropped the count; `extracted 5, stored 2` with no
                    reason was the only trace;
  vector query      a failure returned [], the success branch's "nothing
                    found", silently.

THE FIX: `extract_triples` returns None on failure and the turn declares
`extraction_failed: true` with `extracted: null` on `done` and on the
lineage record; storage failures are counted as `storage_failed` and the
per-candidate `outcomes` sum to `extracted` — an identity asserted here;
the vector query returns None, counted monotonically and declared on
/healthz as `vector_query_failures`.

GUARDS: an AST check per site that the failure branch does not construct
the success branch's zero (a `return []`, a `vector_chunks = []`, a bare
`pass`), each with a must-fail companion that feeds the checker the old
shape; and a runtime test per site.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from backend.cortex.extraction import extract_triples
from backend.kb.retriever import TripleRetriever
from backend.loop.agentic_loop import run_turn
from backend.loop.state import NexusLoopState
from backend.vector import chroma
from backend.vector.chroma import mark_vector_failure, vector_status
from tests.test_aw_prompt_render import _rig

ROOT = Path(__file__).resolve().parent.parent
LOOP = ROOT / "backend" / "loop" / "agentic_loop.py"
EXTRACTION = ROOT / "backend" / "cortex" / "extraction.py"


class _Provider:
    def __init__(self, answer, *, extraction="[]", fail_extract=False):
        self.answer, self.extraction, self.fail, self.calls = answer, extraction, fail_extract, 0

    async def stream_chat(self, model, messages, num_ctx):
        self.calls += 1
        if self.calls % 2 == 1:
            yield self.answer
        else:
            if self.fail:
                raise RuntimeError("extractor down")
            yield self.extraction

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _done(store, hippo, lineage, prov, **kw):
    frames = []

    async def drive():
        async for f, _s in run_turn(NexusLoopState(), "does caffeine improve endurance?",
                                    retriever=TripleRetriever(store), hippocampus=hippo,
                                    provider=prov, lineage=lineage, **kw):
            frames.append(f)
    asyncio.run(drive())
    return next(json.loads(f.split("data: ", 1)[1].strip())
                for f in reversed(frames) if f.startswith("event: done\n"))


# ---------------------------------------------------------------------------
# Site 1 — extraction failure is null, a zero-claim answer is 0
# ---------------------------------------------------------------------------

def test_a_provider_failure_is_none_and_a_zero_answer_is_a_list():
    class _Boom:
        async def stream_chat(self, *a, **k):
            raise RuntimeError("down")
            yield ""
    assert asyncio.run(extract_triples("anything", _Boom())) is None

    class _Nothing:
        async def stream_chat(self, *a, **k):
            yield "[]"
    assert asyncio.run(extract_triples("anything", _Nothing())) == []
    assert asyncio.run(extract_triples("   ", _Boom())) == []          # blank input is known


def test_done_declares_extraction_failed_with_a_null_count(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    done = _done(store, hippo, lineage, _Provider("Caffeine improves endurance.", fail_extract=True))
    g = done["extraction_gate"]
    assert g["extracted"] is None and g["extraction_failed"] is True
    assert g["stored"] == 0 and g["storage_failed"] == 0
    rec = lineage.get(done["lineage_id"])
    assert rec.context["extraction_failed"] is True
    # A model that answered and produced nothing is a measurement.
    done2 = _done(store, hippo, lineage, _Provider("Caffeine improves endurance."))
    assert done2["extraction_gate"]["extracted"] == 0
    assert done2["extraction_gate"]["extraction_failed"] is False
    assert lineage.get(done2["lineage_id"]).context["extraction_failed"] is False
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# Site 2 — storage failures are counted and the outcomes sum to extracted
# ---------------------------------------------------------------------------

EXTRACTION_JSON = json.dumps([
    {"subject": "caffeine", "relation": "improves", "object": "endurance", "confidence": 0.8},
    {"subject": "caffeine", "relation": "raises", "object": "heart rate", "confidence": 0.7},
    # The parser's own stopword filter drops pronoun subjects before the gate
    # sees them; a null-literal OBJECT reaches the gate and is refused there.
    {"subject": "caffeine", "relation": "has", "object": "None", "confidence": 0.5},
])


def test_storage_failures_are_counted_and_outcomes_sum_to_extracted(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    calls = {"n": 0}
    real_add = store.add

    def flaky_add(**kw):
        calls["n"] += 1
        if kw.get("object") == "heart rate":
            raise RuntimeError("disk full")
        return real_add(**kw)
    monkeypatch.setattr(store, "add", flaky_add)
    done = _done(store, hippo, lineage, _Provider("Caffeine improves endurance.",
                                                  extraction=EXTRACTION_JSON))
    g = done["extraction_gate"]
    assert g["extracted"] == 3 and g["extraction_failed"] is False
    assert g["storage_failed"] == 1
    assert g["outcomes"]["storage_failed"] == 1 and g["outcomes"]["rejected"] >= 1
    assert sum(g["outcomes"].values()) == g["extracted"], g
    store.close(); lineage.close()


def test_the_identity_holds_on_the_happy_path_too(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    done = _done(store, hippo, lineage, _Provider("Caffeine improves endurance.",
                                                  extraction=EXTRACTION_JSON))
    g = done["extraction_gate"]
    assert sum(g["outcomes"].values()) == g["extracted"] == 3
    assert g["storage_failed"] == 0
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# Site 3 — the vector channel: None, counted, declared
# ---------------------------------------------------------------------------

def test_a_vector_query_failure_is_none_counted_and_declared(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    monkeypatch.setattr(chroma, "_VECTOR_STATE", {"failures": 0, "last_error": "", "ts": 0.0})

    class _BrokenVectors:
        def query_sync(self, *a, **k):
            raise RuntimeError("chroma unreachable")
    from backend.loop.agentic_loop import assemble_context
    ctx = assemble_context("does caffeine improve endurance?", TripleRetriever(store), hippo,
                           _BrokenVectors())
    assert ctx.vector_chunks is None
    assert vector_status()["failures"] == 1 and "unreachable" in vector_status()["last_error"]
    ctx2 = assemble_context("does caffeine improve endurance?", TripleRetriever(store), hippo,
                            _BrokenVectors())
    assert vector_status()["failures"] == 2, "the counter must be monotone"
    # And a turn with the broken channel still completes and declares.
    done = _done(store, hippo, lineage, _Provider("Caffeine improves endurance."),
                 vector_store=_BrokenVectors())
    assert done["turn"] == 1
    store.close(); lineage.close()


def test_healthz_carries_the_vector_counters(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.server import create_app, create_services
    monkeypatch.setattr(chroma, "_VECTOR_STATE", {"failures": 0, "last_error": "", "ts": 0.0})
    mark_vector_failure("RuntimeError: boom")
    services = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                               eval_log_path=tmp_path / "m.jsonl",
                               archive_db_path=tmp_path / "archive.db",
                               snapshots_dir=tmp_path / "snapshots")
    with TestClient(create_app(services)) as c:
        h = c.get("/healthz").json()
    assert h["vector_query_failures"] == 1 and h["vector_query_last_error"] == "RuntimeError: boom"


# ---------------------------------------------------------------------------
# The static guards, each with a must-fail
# ---------------------------------------------------------------------------

def handlers_returning_empty_list(source: str, function: str) -> list[int]:
    """Lines of `return []` inside an except handler of `function`."""
    out = []
    for fn in ast.walk(ast.parse(source)):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == function:
            for h in [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]:
                for node in ast.walk(h):
                    if isinstance(node, ast.Return) and isinstance(node.value, ast.List) \
                            and not node.value.elts:
                        out.append(node.lineno)
    return out


def handlers_assigning_empty_list(source: str, function: str, name: str) -> list[int]:
    out = []
    for fn in ast.walk(ast.parse(source)):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == function:
            for h in [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]:
                for node in ast.walk(h):
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.List) \
                            and not node.value.elts \
                            and any(getattr(t, "id", None) == name for t in node.targets):
                        out.append(node.lineno)
    return out


def bare_pass_handlers(source: str, function: str) -> list[int]:
    """Lines of except handlers whose body is only `pass`, inside `function`."""
    out = []
    for fn in ast.walk(ast.parse(source)):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == function:
            for h in [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]:
                if all(isinstance(b, ast.Pass) for b in h.body):
                    out.append(h.lineno)
    return out


def test_the_failure_branches_no_longer_construct_the_zero():
    loop = LOOP.read_text(encoding="utf-8")
    assert handlers_returning_empty_list(EXTRACTION.read_text(encoding="utf-8"),
                                         "extract_triples") == []
    assert handlers_assigning_empty_list(loop, "assemble_context", "vector_chunks") == []
    # MCP-2: the turn is three functions now — the two halves and the
    # console's composition — and a bare `pass` in either half is the same
    # defect. Each must exist, or the check is vacuous over a renamed body.
    defined = {n.name for n in ast.walk(ast.parse(loop))
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for fn in ("prepare_turn", "complete_turn", "run_turn"):
        assert fn in defined, f"{fn} is not defined: the bare-pass check would read nothing"
        assert bare_pass_handlers(loop, fn) == [], fn


def test_the_checkers_flag_the_old_shapes():
    """Must-fail companions: the three sites as they were."""
    old_extract = ("async def extract_triples(text, provider):\n"
                   "    try:\n        return await provider.go(text)\n"
                   "    except Exception:\n        return []\n")
    assert handlers_returning_empty_list(old_extract, "extract_triples") == [5]
    old_vector = ("def assemble_context(q, vector_store):\n"
                  "    try:\n        vector_chunks = vector_store.query_sync(q)\n"
                  "    except Exception:\n        vector_chunks = []\n")
    assert handlers_assigning_empty_list(old_vector, "assemble_context", "vector_chunks") == [5]
    old_store = ("async def run_turn(store):\n"
                 "    try:\n        store.add()\n    except Exception:\n        pass\n")
    assert bare_pass_handlers(old_store, "run_turn") == [4]
