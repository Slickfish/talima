"""W0b part 2 — the replay capture on every record (RUN_WORKLIST.md W0b).

What a later re-run of a turn needs, captured now, because none of it can be
added afterwards: the seed sent with the answering request, the model's
content digest as served, the exact options, the observable environment, and
the exact request bytes `request_hash` covers.

The pins, in the worklist's order:

  W0b-1  a console record carries the four fields and its request is kept;
  W0b-2  the seed is drawn per ANSWERING request, never for extraction;
  W0b-3  `NEXUS_INFERENCE_SEED=off` is the pre-W0b request (no seed kwarg);
  W0b-4  `NEXUS_KEEP_REQUESTS=off` keeps no bytes and changes nothing else;
  W0b-5  an `mcp_block` record carries the four NULL and declared; a planted
         defaulted value is named (verify_scope and /lineage/{id});
  W0b-6  kept bytes hash to `request_hash`; a planted byte flip is named by
         the lineage verifier, beside the three layers, never inside them;
  W0b-7  the one writer refuses bytes that do not hash to `request_hash`;
  W0b-8  the seed comes from one function using OS entropy and no literal
         seed exists in the backend (AST; planted literals named);
  W0b-9  tests set the seed only through the one declared hook (AST);
  W0b-10 the wire bodies with the seed, against the pre-W0b bodies printed
         failing (the request-hash pins cover the canonical messages only,
         so they do not move — establish item 4);
  W0b-11 Ollama's digest by its own name rule, a found one cached, absent with a reason;
  W0b-12 the environment declares what it cannot observe;
  W0b-13 the surfaces (/lineage/{id}, /cortex/stats, nexus_status);
  W0b-14 every pre-W0b record recomputes and reads as uncaptured.
"""
from __future__ import annotations

import ast
import asyncio
import dataclasses
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.loop.agentic_loop import MCP_CAPTURE_REASON
from backend.loop.compaction import STABLE_SYSTEM_PREFIX, TOKEN_BUDGET
from backend.loop.prompt_render import context_hash, request_bytes_of, request_hash_of
from backend.mcp.scope import verify_scope
from backend.memory.lineage import (REQUEST_ENCODING_BLOCK, REQUEST_ENCODING_MESSAGES, LineageStore,
                                    canonical_json, verify_sealed_lineage)
from backend.providers import replay_capture as rc
from backend.providers.lmstudio import LMStudioProvider
from backend.providers.ollama import OllamaProvider
from backend.server import create_app, create_services
from tests._tracked import tracked

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "w0b-capture"
H = {"Authorization": f"Bearer {TOKEN}"}
FIXTURE = ROOT / "tests" / "fixtures" / "pre_run_data"
ANSWER = "Interval training improves VO2 max."


class _Capturing:
    """A seed-taking stub that declares what a real provider declares: it
    takes a seed, names the options it sends, the content digest it serves
    and one runtime fact. Every call is recorded with its keyword arguments,
    so the test reads what the provider was actually handed."""
    takes_seed = True
    DIGEST = "sha256:" + "ab" * 32
    CONFIG = {"provider": "stub", "endpoint": "/stub/chat", "stream": True}

    def __init__(self):
        self.calls: list[dict] = []

    def request_config(self, model, num_ctx, *, separate):
        return {**self.CONFIG, "model": model, "options": {"num_ctx": num_ctx}}

    async def served_model_digest(self, model):
        return self.DIGEST, ""

    async def inference_environment(self):
        return {"stub_runtime": "1.0"}

    async def stream_chat(self, model, messages, num_ctx, **kw):
        answering = bool(messages) and str(messages[0].get("content", "")).startswith(STABLE_SYSTEM_PREFIX)
        self.calls.append({"answering": answering, "messages": [dict(m) for m in messages], "kw": dict(kw)})
        yield ANSWER if answering else "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return False

    def answering(self) -> list[dict]:
        return [c for c in self.calls if c["answering"]]


def _done(resp) -> dict:
    frame = [f for f in resp.text.split("\n\n") if f.startswith("event: done")][-1]
    return json.loads(frame.split("data: ", 1)[1])


def _services(root: Path, provider, *, claim: bool = True):
    svc = create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma",
                          eval_log_path=root / "m.jsonl", archive_db_path=root / "a.db",
                          snapshots_dir=root / "snap", provider=provider)
    if claim:
        svc.store.add(subject="interval training", relation="improves", object="VO2 max", weight=0.9,
                      tier=1, granularity=Granularity.TOPIC, source="human_assertion")
    return svc


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    for key in (rc.SEED_SETTING, rc.KEEP_SETTING):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


@pytest.fixture
def rig(env):
    stub = _Capturing()
    svc = _services(env / "s", stub)
    return svc, TestClient(create_app(svc)), stub


def _chat(client, message="what does interval training improve?") -> dict:
    return _done(client.post("/chat", headers=H, json={"message": message}))


# ---------------------------------------------------------------------------
# W0b-1 .. W0b-4 — the console's record
# ---------------------------------------------------------------------------

def test_a_console_record_carries_the_capture_and_its_request_is_kept(rig):
    svc, client, stub = rig
    done = _chat(client)
    rec = svc.lineage.get(done["lineage_id"])
    sent = stub.answering()
    assert len(sent) == 1, stub.calls
    seed = sent[0]["kw"].get("seed")
    print(f"\nW0b-1 ({sys.platform}) seed={rec.inference_seed} digest={rec.model_digest} "
          f"config={svc.lineage.inference_config(rec.inference_config_hash)} "
          f"environment={svc.lineage.environment(rec.environment_hash)} notes={rec.capture_notes}")
    assert isinstance(seed, int) and 0 <= seed < 2 ** rc.SEED_BITS
    assert rec.inference_seed == seed, "the record carries the seed the provider was handed"
    assert rec.model_digest == _Capturing.DIGEST
    stored_config = svc.lineage.inference_config(rec.inference_config_hash)
    assert stored_config == {**stub.request_config(rec.model_id, TOKEN_BUDGET, separate=True),
                             rc.SEPARATION_KEY: True}, \
        "the options recorded are the ones the provider declares for the model and context it was asked for"
    assert rec.inference_config_hash == hashlib.sha256(canonical_json(stored_config).encode()).hexdigest()
    environment = svc.lineage.environment(rec.environment_hash)
    assert environment["provider"] == "_Capturing" and environment["stub_runtime"] == "1.0"
    assert environment["python"] and environment["os"] == sys.platform
    assert rec.environment_hash == hashlib.sha256(canonical_json(environment).encode()).hexdigest()
    assert rec.capture_notes is None, "nothing was absent, so no reason is recorded"
    # The kept bytes are what the provider was sent, in the form request_hash covers.
    kept = svc.lineage.request_body(rec.request_hash)
    assert kept == request_bytes_of(sent[0]["messages"])
    assert hashlib.sha256(kept).hexdigest() == rec.request_hash == request_hash_of(sent[0]["messages"])
    assert svc.lineage.verify_record(rec.lineage_id)["record_intact"] is True
    row = svc.lineage.conn.execute("SELECT encoding FROM request_bodies WHERE request_hash = ?",
                                   (rec.request_hash,)).fetchone()
    assert row["encoding"] == REQUEST_ENCODING_MESSAGES


def test_the_seed_is_drawn_per_answering_request_and_never_for_extraction(rig):
    svc, client, stub = rig
    done = [_chat(client), _chat(client, "and what else does it improve?")]
    recs = [svc.lineage.get(d["lineage_id"]) for d in done]
    answering = stub.answering()
    extraction = [c for c in stub.calls if not c["answering"]]
    print(f"\nW0b-2 ({sys.platform}) answering seeds {[c['kw'].get('seed') for c in answering]}; "
          f"extraction calls {len(extraction)} with kwargs {[c['kw'] for c in extraction]}")
    assert len(answering) == 2 and extraction, "the rig exercises both kinds of call"
    assert [r.inference_seed for r in recs] == [c["kw"]["seed"] for c in answering]
    assert all(set(c["kw"]) == {"seed"} for c in answering)
    assert all(c["kw"] == {} for c in extraction), "extraction requests carry no seed, exactly as before"


def test_seed_off_is_the_pre_w0b_request_and_the_record_says_why(rig, monkeypatch):
    svc, client, stub = rig
    monkeypatch.setenv("NEXUS_INFERENCE_SEED", "off")
    rec = svc.lineage.get(_chat(client)["lineage_id"])
    (sent,) = stub.answering()
    print(f"\nW0b-3 ({sys.platform}) seed off: kwargs handed to the provider {sent['kw']}; "
          f"record seed {rec.inference_seed}; notes {rec.capture_notes}")
    assert sent["kw"] == {}, "the provider is called exactly as before W0b"
    assert rec.inference_seed is None
    assert rec.capture_notes == {"inference_seed": "seed off (NEXUS_INFERENCE_SEED=off)"}
    assert rec.model_digest and rec.inference_config_hash and rec.environment_hash


def test_keep_off_keeps_no_bytes_and_changes_nothing_else(env, monkeypatch):
    """Two identical rigs under one fixed seed; only the switch differs."""
    out = {}
    for arm in ("on", "off"):
        if arm == "off":
            monkeypatch.setenv("NEXUS_KEEP_REQUESTS", "off")
        else:
            monkeypatch.setenv("NEXUS_KEEP_REQUESTS", "on")
        stub = _Capturing()
        svc = _services(env / arm, stub, claim=False)
        with rc.fixed_seed(1234):
            rec = svc.lineage.get(_chat(TestClient(create_app(svc)))["lineage_id"])
        bodies = svc.lineage.conn.execute("SELECT COUNT(*) FROM request_bodies").fetchone()[0]
        out[arm] = (rec.to_dict(), bodies)
    on, off = out["on"][0], out["off"][0]
    differ = sorted(k for k in on if on[k] != off[k])
    print(f"\nW0b-4 ({sys.platform}) request_bodies on={out['on'][1]} off={out['off'][1]}; "
          f"fields that differ: {differ}; off notes {off['capture_notes']}")
    assert out["on"][1] == 1 and out["off"][1] == 0
    assert off["capture_notes"] == {"request_body": "not kept: NEXUS_KEEP_REQUESTS=off"}
    assert on["capture_notes"] is None
    assert set(differ) <= {"lineage_id", "ts", "record_hash", "capture_notes"}, differ
    assert on["inference_seed"] == off["inference_seed"] == 1234


# ---------------------------------------------------------------------------
# W0b-5 — the mcp_block record: NULL and declared
# ---------------------------------------------------------------------------

LEARNING_KEYS = ("NEXUS_LEARNING", "NEXUS_LEARNING_FROZEN_SINCE", "NEXUS_MCP_OWNER",
                 "NEXUS_MCP_DATA_ROOT", "NEXUS_MCP_HANDLE_TIMEOUT")


@pytest.fixture
def mcp_runtime(env):
    """The MCP runtime writes NEXUS_LEARNING into the process environment on
    purpose (test_mcp2_build.py's note), so the keys are restored by hand.
    Yields (runtime, server, root): the HTTP app, when a test reads it, is
    built on its own services over the same files, as test_mcp2_build does."""
    from backend.mcp.mcp_server import NexusRuntime, build_server
    before = {k: os.environ.get(k) for k in LEARNING_KEYS}
    for k in before:
        os.environ.pop(k, None)
    root = env / "mcp"
    rt = NexusRuntime.open(root=root, owner="alice")
    rt.services.store.add(subject="python", relation="is_a", object="programming_language", weight=0.8,
                          tier=1, granularity=Granularity.TOPIC, source="human_assertion")
    yield rt, build_server(rt, with_lifespan=False), root
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _call(server, name, **args):
    return asyncio.run(server.call_tool(name, args)).model_dump()["structured_content"]


def test_an_mcp_record_carries_the_capture_null_and_declared(mcp_runtime):
    rt, srv, _root = mcp_runtime
    r = _call(srv, "nexus_retrieve", question="tell me about python")
    out = _call(srv, "nexus_record", retrieval_id=r["retrieval_id"], answer="Python is a programming_language.")
    rec = rt.services.lineage.get(out["lineage_id"])
    kept = rt.services.lineage.request_body(rec.request_hash)
    status = _call(srv, "nexus_status")
    print(f"\nW0b-5 ({sys.platform}) mcp record: seed={rec.inference_seed} digest={rec.model_digest} "
          f"config={rec.inference_config_hash} env={rec.environment_hash}; notes={rec.capture_notes}; "
          f"kept {len(kept)} bytes of the block; nexus_status.replay_capture={status['replay_capture']}")
    for f in rc.CAPTURE_FIELDS:
        assert getattr(rec, f) is None, f
        assert rec.capture_notes[f] == MCP_CAPTURE_REASON, f
    assert verify_scope(rec) == []
    # NEXUS's block — what entered the client's context — is kept under its hash.
    assert kept == r["block"].encode("utf-8")
    assert rec.request_hash == context_hash(r["block"]) == hashlib.sha256(kept).hexdigest()
    enc = rt.services.lineage.conn.execute("SELECT encoding FROM request_bodies WHERE request_hash = ?",
                                           (rec.request_hash,)).fetchone()[0]
    assert enc == REQUEST_ENCODING_BLOCK
    assert status["replay_capture"]["with_request_kept"] == 1
    assert status["contract_version"] == 5      # W6: 2 -> 3 (record_failed); W9: 3 -> 4 (nexus_commit, identity);
    #                                            W10b: 4 -> 5 (nexus_record_action)


def test_companion_a_defaulted_capture_value_on_an_mcp_record_is_named(mcp_runtime):
    """COMPANION (OQ-16 rules 1 and 3). Planted values the guard's source does
    not name: a zero seed (a falsy default a truthiness check would pass), an
    empty digest, the hash of an empty environment, and notes with the
    reasons stripped. Each is named; and written into the sealed row, the
    consequence is on /lineage/{id} — the violation and a broken row."""
    rt, srv, root = mcp_runtime
    r = _call(srv, "nexus_retrieve", question="tell me about python")
    out = _call(srv, "nexus_record", retrieval_id=r["retrieval_id"], answer="Python is a programming_language.")
    rec = rt.services.lineage.get(out["lineage_id"])
    planted = {
        "inference_seed": dataclasses.replace(rec, inference_seed=0),
        "model_digest": dataclasses.replace(rec, model_digest=""),
        "environment_hash": dataclasses.replace(rec, environment_hash=hashlib.sha256(b"{}").hexdigest()),
    }
    for f, bad in planted.items():
        named = verify_scope(bad)
        assert any(p.startswith(f"{f} is set") for p in named), (f, named)
    stripped = verify_scope(dataclasses.replace(rec, capture_notes={}))
    assert sorted(p.split(" ")[0] for p in stripped) == sorted(rc.CAPTURE_FIELDS), stripped
    # The consequence: the planted value written into the sealed row's NULL column.
    rt.services.lineage.conn.execute("UPDATE lineage_records SET inference_seed = 0 WHERE lineage_id = ?",
                                     (rec.lineage_id,))
    rt.services.lineage.conn.commit()
    http = create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma-http", eval_log_path=root / "h.jsonl",
                           archive_db_path=root / "archive.db", snapshots_dir=root / "snapshots")
    page = TestClient(create_app(http)).get(f"/lineage/{rec.lineage_id}", headers=H).json()
    print(f"\nW0b-5c ({sys.platform}) planted seed 0: violations {page['scope_declaration']['violations']}; "
          f"verify {page['verify']}")
    assert any("inference_seed is set" in v for v in page["scope_declaration"]["violations"])
    assert page["verify"]["record_intact"] is False, "a value written into a sealed NULL column breaks the row"


# ---------------------------------------------------------------------------
# W0b-6, W0b-7 — the kept bytes are verified by the record
# ---------------------------------------------------------------------------

def test_kept_bytes_hash_to_request_hash_and_a_flipped_byte_is_named(rig):
    svc, client, _stub = rig
    recs = [svc.lineage.get(_chat(client, q)["lineage_id"])
            for q in ("what does interval training improve?", "what is VO2 max?")]
    clean = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    assert clean["request_bodies"]["verdict"] == "intact" and clean["request_bodies"]["checked"] == 2
    assert clean["request_bodies"]["records_with_body"] == 2 and clean["request_bodies"]["orphans"] == 0
    for rec in recs:
        assert hashlib.sha256(svc.lineage.request_body(rec.request_hash)).hexdigest() == rec.request_hash
    victim = recs[0]
    conn = svc.lineage.conn
    blob = bytearray(conn.execute("SELECT body FROM request_bodies WHERE request_hash = ?",
                                  (victim.request_hash,)).fetchone()[0])
    blob[len(blob) // 2] ^= 0x01                                   # one bit, mid-stream
    conn.execute("UPDATE request_bodies SET body = ? WHERE request_hash = ?", (bytes(blob), victim.request_hash))
    conn.commit()
    flipped = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    one = verify_sealed_lineage(svc.lineage, svc.archive_chain, lineage_id=victim.lineage_id)
    print(f"\nW0b-6 ({sys.platform}) flipped bit: {flipped['request_bodies']}; the record's layers: "
          f"row {flipped['layers']['row']['ok']} ok, verdict {flipped['verdict']}")
    (failure,) = flipped["request_bodies"]["failures"]
    assert failure["request_hash"] == victim.request_hash and failure["lineage_ids"] == [victim.lineage_id]
    assert flipped["request_bodies"]["verdict"] == "copy_corrupted"
    assert one["request_bodies"]["failed"] == 1
    # Beside the three layers, never inside them: the record itself is intact.
    assert set(flipped["layers"]) == {"row", "seal", "chain"}
    assert flipped["layers"]["row"]["failed"] == 0 and flipped["verdict"] == "intact"
    # The HTTP surface carries the same block.
    http = client.get("/lineage/verify", headers=H).json()
    assert http["request_bodies"]["failures"][0]["lineage_ids"] == [victim.lineage_id]


def test_companion_a_validly_compressed_altered_body_is_named(rig):
    """COMPANION: bytes that decompress cleanly but are not the request (an
    edit made by someone who knows the encoding), and a forged length — the
    two members of the class a decompression check alone would pass."""
    svc, client, _stub = rig
    rec = svc.lineage.get(_chat(client)["lineage_id"])
    conn = svc.lineage.conn
    raw = svc.lineage.request_body(rec.request_hash)
    altered = raw.replace(b"interval", b"INTERVAL", 1)
    assert altered != raw and len(altered) == len(raw)
    conn.execute("UPDATE request_bodies SET body = ? WHERE request_hash = ?",
                 (zlib.compress(altered, 9), rec.request_hash))
    conn.commit()
    (f1,) = svc.lineage.verify_request_bodies()["failures"]
    conn.execute("UPDATE request_bodies SET body = ?, raw_bytes = raw_bytes + 1 WHERE request_hash = ?",
                 (zlib.compress(raw, 9), rec.request_hash))
    conn.commit()
    (f2,) = svc.lineage.verify_request_bodies()["failures"]
    print(f"\nW0b-6c ({sys.platform}) altered: {f1['reason']}; forged length: {f2['reason']}")
    assert f1["reason"] == "bytes do not hash to request_hash"
    assert f2["reason"] == "raw_bytes differs from the decompressed length"


def test_the_writer_refuses_bytes_that_do_not_hash_to_request_hash(env):
    lineage = LineageStore(env / "kb.db")
    good = b'[{"content":"x","role":"user"}]'
    rec = lineage.record_turn(query="q", injected=[], contradictions=[], model_id="m", response_text="a",
                              prompt_prefix="p", request_hash=hashlib.sha256(good).hexdigest(),
                              request_body=b'[{"content":"y","role":"user"}]')
    n = lineage.conn.execute("SELECT COUNT(*) FROM request_bodies").fetchone()[0]
    print(f"\nW0b-7 ({sys.platform}) mismatched bytes: kept {n}; notes {rec.capture_notes}")
    assert n == 0 and rec.capture_notes == {"request_body": "not kept: the bytes did not hash to request_hash"}
    assert lineage.verify_record(rec.lineage_id)["record_intact"] is True
    with pytest.raises(ValueError, match="encoding"):
        lineage.record_turn(query="q", injected=[], contradictions=[], model_id="m", response_text="a",
                            prompt_prefix="p", request_body=good, request_body_encoding="gzip")
    lineage.close()


# ---------------------------------------------------------------------------
# W0b-8, W0b-9 — one seed source, one test hook (AST)
# ---------------------------------------------------------------------------

ENTROPY_SITE = ("backend/providers/replay_capture.py", "draw_seed")
DRAW_CALLER = ("backend/providers/base.py", "_draw_seed")
HOOK_SITE = ("backend/providers/replay_capture.py", "fixed_seed")
SEED_KEYWORDS = ("seed", "inference_seed")


def _enclosing(tree) -> dict[int, str]:
    """id(node) -> the innermost function it sits in (ast.walk is
    breadth-first, so a later, deeper function overwrites its parent)."""
    owner: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                owner[id(n)] = fn.name
    return owner


def _is_literal(node, consts: frozenset = frozenset()) -> bool:
    """A seed written in the source: a number, a signed one, arithmetic over
    numbers (`-1`, `2**31 - 1`), a string of digits, or a name the module
    binds to one of those (`SEED = 42`). A reason string (the notes'
    `"seed off (...)"`) is not a seed."""
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, str):
            return v.strip().lstrip("+-").isdigit()
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if isinstance(node, ast.Name):
        return node.id in consts
    if isinstance(node, ast.UnaryOp):
        return _is_literal(node.operand, consts)
    if isinstance(node, ast.BinOp):
        return _is_literal(node.left, consts) and _is_literal(node.right, consts)
    return False


def _module_literals(tree) -> frozenset:
    """Names the module binds, at its top level, to a literal number."""
    out = set()
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) and node.value is not None else [])
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if value is not None and _is_literal(value):
            out |= {n.id for t in targets for n in _target_names(t)}
    return frozenset(out)


def _dotted(node) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _target_names(target):
    """Every Name a target binds, through tuples, lists and starred targets
    (`previous, _fixed_seed = ...` binds `_fixed_seed`)."""
    if isinstance(target, ast.Name):
        yield target
    elif isinstance(target, (ast.Tuple, ast.List)):
        for e in target.elts:
            yield from _target_names(e)
    elif isinstance(target, ast.Starred):
        yield from _target_names(target.value)


# Bare names an entropy draw goes by after `from secrets import randbits`.
BARE_DRAWS = ("randbits", "randbelow", "getrandbits", "randint", "randrange", "urandom")


def seed_violations(source: str, rel: str) -> list[str]:
    """Every way `source` (the tracked file at `rel`) draws or writes a seed
    other than through `draw_seed`'s one entropy call."""
    tree = ast.parse(source)
    owner = _enclosing(tree)
    consts = _module_literals(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        where = f"{rel}:{getattr(node, 'lineno', '?')}"
        fn = owner.get(id(node))
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if (name.split(".")[-1] == "setdefault" and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value in SEED_KEYWORDS
                    and _is_literal(node.args[1], consts)):
                out.append(f"{where} literal .setdefault({node.args[0].value!r}, {ast.unparse(node.args[1])})")
            entropy = (name.startswith(("secrets.rand", "random.", "np.random.", "numpy.random."))
                       or name in ("os.urandom",) + BARE_DRAWS)
            if entropy and (rel, fn) != ENTROPY_SITE:
                out.append(f"{where} {name}() outside draw_seed")
            if name.split(".")[-1] == "draw_seed" and (rel, fn) != DRAW_CALLER:
                out.append(f"{where} draw_seed() called from {fn} (only ReplyStream._draw_seed may)")
            if name.split(".")[-1] == "fixed_seed":
                out.append(f"{where} fixed_seed() in the backend (the hook is for tests)")
            for kw in node.keywords:
                if kw.arg in SEED_KEYWORDS and _is_literal(kw.value, consts):
                    out.append(f"{where} literal {kw.arg}={ast.unparse(kw.value)}")
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value in SEED_KEYWORDS and _is_literal(v, consts):
                    out.append(f"{where} literal {{'{k.value}': {ast.unparse(v)}}}")
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                        and t.slice.value in SEED_KEYWORDS and _is_literal(node.value, consts)):
                    out.append(f"{where} literal [{t.slice.value!r}] = {ast.unparse(node.value)}")
                for n in _target_names(t):
                    if n.id == "_fixed_seed" and fn is not None and (rel, fn) != HOOK_SITE:
                        out.append(f"{where} _fixed_seed assigned outside fixed_seed")
    return out


def test_the_seed_comes_from_one_function_and_no_literal_seed_exists():
    files = [p for p in tracked("backend") if p.suffix == ".py"]
    found = []
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        found += seed_violations(p.read_text(encoding="utf-8"), rel)
    src = (ROOT / ENTROPY_SITE[0]).read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "draw_seed")
    draws = [_dotted(c.func) for c in ast.walk(fn) if isinstance(c, ast.Call)]
    print(f"\nW0b-8 ({sys.platform}) {len(files)} tracked backend files; draw_seed calls {draws}; "
          f"violations {found}")
    assert draws == ["secrets.randbits"], "the one draw is OS entropy, 31 bits"
    assert found == [], found


PLANTED_BACKEND = '''
import random, secrets
def build(model, msgs, client):
    body = {"model": model, "seed": 42, "inference_seed": "17"}
    body["options"] = {"num_ctx": 4096, "seed": -1}
    body["options"]["seed"] = 2**31 - 1
    random.seed(7)
    s = secrets.randbelow(10)
    return client.stream_chat(model, msgs, num_ctx=4096, seed=3)
def answer():
    return draw_seed()
def sneak():
    global _fixed_seed
    previous, _fixed_seed = _fixed_seed, 11
SEED = 4242
def named(options):
    options["seed"] = SEED
def defaulted(options):
    options.setdefault("seed", 99)
'''


def test_companion_planted_seeds_are_named():
    """COMPANION: literal seeds the guard's source does not name (42, a
    negative one, an arithmetic one, a keyword one), a global RNG seeded, a
    second entropy draw, and a second caller of draw_seed — every one named,
    and the guard's assertion fails on them (the build fails)."""
    named = seed_violations(PLANTED_BACKEND, "backend/planted.py")
    print(f"\nW0b-8c ({sys.platform}) planted: {named}")
    for fragment in ("{'seed': 42}", "{'seed': -1}", "{'inference_seed': '17'}", "['seed'] = 2 ** 31 - 1",
                     "random.seed()",
                     "secrets.randbelow()", "literal seed=3", "draw_seed() called from answer",
                     "_fixed_seed assigned outside fixed_seed", "['seed'] = SEED",
                     ".setdefault('seed', 99)"):
        assert any(fragment in v for v in named), (fragment, named)


HOOK_NAMES = ("draw_seed", "_fixed_seed", "randbits")


def hook_violations(source: str, rel: str) -> list[str]:
    """Any way a test sets a seed other than `rc.fixed_seed(...)`: touching
    the hook's state, replacing the draw, or patching the entropy source."""
    out = []
    for node in ast.walk(ast.parse(source)):
        where = f"{rel}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Attribute) and node.attr in ("_fixed_seed", "randbits"):
            out.append(f"{where} touches .{node.attr}")
        if isinstance(node, ast.Call):
            # Any call handed a hook's name as text: setattr, monkeypatch,
            # mock.patch("...draw_seed"), mock.patch.object(rc, "draw_seed").
            for a in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                        and any(h in a.value for h in HOOK_NAMES):
                    out.append(f"{where} patches {a.value!r}")
        if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str) and node.slice.value in HOOK_NAMES):
            out.append(f"{where} subscripts {node.slice.value!r}")      # rc.__dict__["_fixed_seed"]
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Attribute) and t.attr in HOOK_NAMES:
                    out.append(f"{where} assigns .{t.attr}")
    return out


def test_tests_set_the_seed_only_through_the_declared_hook():
    files = [p for p in tracked("tests") if p.suffix == ".py"]
    found = []
    for p in files:
        found += hook_violations(p.read_text(encoding="utf-8"), p.relative_to(ROOT).as_posix())
    users = [p.name for p in files if "fixed_seed(" in p.read_text(encoding="utf-8")]
    print(f"\nW0b-9 ({sys.platform}) {len(files)} tracked test files; fixed_seed used in {users}; violations {found}")
    assert found == [], found
    assert "test_w0b_capture.py" in users


def test_companion_planted_test_seeds_are_named():
    planted = ('monkeypatch.setattr(rc, "draw_seed", lambda: 5)\n'
               'rc._fixed_seed = 9\n'
               'monkeypatch.setattr("backend.providers.replay_capture.secrets.randbits", lambda n: 1)\n'
               'rc.draw_seed = lambda: 2\n'
               'mock.patch("backend.providers.replay_capture.draw_seed", return_value=3)\n'
               'mock.patch.object(rc, "draw_seed", return_value=4)\n'
               'rc.__dict__["_fixed_seed"] = 5\n')
    named = hook_violations(planted, "tests/planted.py")
    print(f"\nW0b-9c ({sys.platform}) planted: {named}")
    assert all(any(f"planted.py:{i} " in v for v in named) for i in range(1, 8)), named


# ---------------------------------------------------------------------------
# W0b-10 — the wire bodies, against the pre-W0b bodies
# ---------------------------------------------------------------------------

MSGS = [{"role": "system", "content": "prefix"}, {"role": "user", "content": "q"}]
# The bodies the providers sent before W0b, from the code at 33055ec (no seed
# anywhere); printed failing against the new bytes, the worklist's rule.
PRE_W0B_OLLAMA = {"model": "m:1", "messages": MSGS, "stream": True, "options": {"num_ctx": 4096}}
PRE_W0B_LMSTUDIO = {"model": "m:1", "messages": MSGS, "stream": True}
W0B_OLLAMA = {"model": "m:1", "messages": MSGS, "stream": True, "options": {"num_ctx": 4096, "seed": 12345}}
W0B_LMSTUDIO = {"model": "m:1", "messages": MSGS, "stream": True, "seed": 12345}


def _wire(body) -> bytes:
    """The exact bytes httpx sends for `json=body`, from httpx itself (0.28:
    compact separators, non-ASCII unescaped, insertion order): never a
    re-implementation of its encoder."""
    import httpx
    return httpx.Request("POST", "http://wire.invalid/api/chat", json=body).content


def test_the_wire_bodies_carry_the_seed_and_nothing_else_moved():
    ollama, lms = OllamaProvider(), LMStudioProvider()
    o_new = ollama._payload("m:1", MSGS, 4096, separate=True, seed=12345)
    l_new = lms._payload("m:1", MSGS, 4096, seed=12345)         # W3: num_ctx stated (Law 5 refuses above the ceiling)
    o_off = ollama._payload("m:1", MSGS, 4096, separate=True)
    l_off = lms._payload("m:1", MSGS, 4096)
    print(f"\nW0b-10 ({sys.platform}) Ollama pre-W0b pin {_wire(PRE_W0B_OLLAMA)!r}\n"
          f"   against the new bytes {_wire(o_new)!r}: {'FAILS' if _wire(o_new) != _wire(PRE_W0B_OLLAMA) else 'holds'}\n"
          f"   LM Studio pre-W0b pin {_wire(PRE_W0B_LMSTUDIO)!r}\n"
          f"   against the new bytes {_wire(l_new)!r}: {'FAILS' if _wire(l_new) != _wire(PRE_W0B_LMSTUDIO) else 'holds'}\n"
          f"   request_hash covers {request_bytes_of(MSGS)!r} — the canonical messages only, so its pins did not move")
    assert _wire(o_new) == _wire(W0B_OLLAMA) and _wire(l_new) == _wire(W0B_LMSTUDIO)
    assert _wire(o_new) != _wire(PRE_W0B_OLLAMA) and _wire(l_new) != _wire(PRE_W0B_LMSTUDIO)
    # With no seed (NEXUS_INFERENCE_SEED=off), the pre-W0b bytes exactly.
    assert _wire(o_off) == _wire(PRE_W0B_OLLAMA) and _wire(l_off) == _wire(PRE_W0B_LMSTUDIO)
    # The recorded options are the body minus messages and seed, as built.
    assert ollama.request_config("m:1", 4096, separate=True) == {
        "provider": "ollama", "endpoint": "/api/chat", "model": "m:1", "stream": True,
        "options": {"num_ctx": 4096}}
    assert lms.request_config("m:1", 4096, separate=True) == {
        "provider": "lmstudio", "endpoint": "/v1/chat/completions", "model": "m:1", "stream": True}
    # The recorded options, read back from their canonical JSON, rebuild the
    # exact body with the recorded seed — the premise the replay check rests on.
    for prov, want in ((ollama, W0B_OLLAMA), (lms, W0B_LMSTUDIO)):
        cfg = json.loads(canonical_json({**prov.request_config("m:1", 4096, separate=True),
                                         rc.SEPARATION_KEY: True}))
        assert rc.wire_body(cfg, MSGS, 12345) == want, type(prov).__name__
        assert rc.wire_body(cfg, MSGS, None) == (PRE_W0B_OLLAMA if prov is ollama else PRE_W0B_LMSTUDIO)
    with pytest.raises(ValueError, match="no seed placement"):
        rc.wire_body({"provider": "nowhere", "model": "m:1"}, MSGS, 1)
    # W3 — the stub takes no seed (`None` in SEED_PLACEMENT): its record's body
    # rebuilds without one, and a seed on such a record is refused by name.
    assert rc.wire_body({"provider": "stub", "endpoint": None, "model": "m:1", "num_ctx": 4096,
                         rc.SEPARATION_KEY: True}, MSGS, None) == {"model": "m:1", "num_ctx": 4096, "messages": MSGS}
    with pytest.raises(ValueError, match="takes no seed"):
        rc.wire_body({"provider": "stub", "model": "m:1"}, MSGS, 1)


# ---------------------------------------------------------------------------
# W0b-11, W0b-12 — the digest and the environment
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def test_ollama_reads_the_digest_by_exact_tag_cached_per_tag(monkeypatch):
    import backend.providers.ollama as ollama_mod
    calls = []

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, **kw):
            calls.append(url)
            if url.endswith("/api/tags"):
                return _Resp(200, {"models": [
                    {"name": "qwen:7b", "model": "qwen:7b", "digest": "d" * 64},
                    {"name": "qwen:7b-instruct", "model": "qwen:7b-instruct", "digest": "e" * 64},
                    {"name": "qwen3:latest", "model": "qwen3:latest", "digest": "f" * 64}]})
            return _Resp(200, {"version": "0.12.6"})

    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", _Client)
    p = ollama_mod.OllamaProvider()
    got = [asyncio.run(p.served_model_digest(t)) for t in ("qwen:7b", "qwen:7b", "qwen", "qwen")]
    env = asyncio.run(p.inference_environment())
    print(f"\nW0b-11 ({sys.platform}) digests {got}; /api/tags reads {calls.count(p.BASE_URL + '/api/tags')}; env {env}")
    assert got[0] == got[1] == ("d" * 64, "")
    assert got[2] == got[3] == (None, "tag not listed by /api/tags"), "a prefix never matches"
    # A found digest is read once per tag per process; an absent tag is read
    # again every time (part 4, S2: a pull must be seen), so 1 + 2 reads.
    assert calls.count(p.BASE_URL + "/api/tags") == 3, "found: cached; absent: read each time"
    assert env == {"ollama_version": "0.12.6"}
    # Ollama's own name equality (defaults filled, case folded): the names the
    # daemon serves under are found; a family or a different tag never is.
    same = {t: asyncio.run(p.served_model_digest(t))[0] for t in (
        "Qwen:7B", "library/qwen:7b", "registry.ollama.ai/library/qwen:7b", "qwen3", "QWEN3:latest",
        "qwen:7b-instruct", "qwen:7", "qwen3:8b", "other/qwen:7b")}
    print(f"   by Ollama's name rule: {same}")
    assert same["Qwen:7B"] == same["library/qwen:7b"] == same["registry.ollama.ai/library/qwen:7b"] == "d" * 64
    assert same["qwen3"] == same["QWEN3:latest"] == "f" * 64 and same["qwen:7b-instruct"] == "e" * 64
    assert same["qwen:7"] is None and same["qwen3:8b"] is None and same["other/qwen:7b"] is None


def test_ollama_name_key_is_the_daemons_rule():
    k = ollama_mod_key = __import__("backend.providers.ollama", fromlist=["x"]).ollama_name_key
    assert k("qwen3") == ("registry.ollama.ai", "library", "qwen3", "latest")
    assert k("localhost:5000/ns/m:v1") == ("localhost:5000", "ns", "m", "v1"), "a port is never a tag"
    assert k("m@sha256:" + "a" * 64) == k("m:latest")
    assert k("") is None and k(None) is None and k("/") is None
    assert ollama_mod_key("A/B:C") == ("registry.ollama.ai", "a", "b", "c")


def test_an_unreachable_daemon_is_absent_with_a_reason_and_not_cached(monkeypatch):
    import backend.providers.ollama as ollama_mod

    class _Down:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw): raise ConnectionRefusedError("down")

    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", _Down)
    p = ollama_mod.OllamaProvider()
    got = asyncio.run(p.served_model_digest("qwen:7b"))
    env = asyncio.run(p.inference_environment())
    print(f"\nW0b-11b ({sys.platform}) unreachable: digest {got}; env {env}")
    assert got == (None, "/api/tags unavailable: ConnectionRefusedError") and "qwen:7b" not in p._digests
    assert env == {"ollama_version_absent": "/api/version unavailable: ConnectionRefusedError"}


def test_the_environment_declares_what_it_cannot_observe():
    env = rc.nexus_environment()
    print(f"\nW0b-12 ({sys.platform}) environment {env}")
    assert env["python"] and env["os"] == sys.platform and env["platform"]
    for fact in ("nexus_commit", "gpu"):
        assert (fact in env) != (f"{fact}_absent" in env), f"{fact}: observed or absent with a reason, never both"
    assert env["provider_parallelism_absent"]
    assert ("httpx" in env) != ("httpx_absent" in env), "the wire encoder's version, or why not"
    if "nexus_commit" in env:
        assert len(env["nexus_commit"]) == 40 and isinstance(env["nexus_tree_modified"], bool)


# ---------------------------------------------------------------------------
# W0b-13, W0b-14 — the surfaces, and every pre-W0b record
# ---------------------------------------------------------------------------

def test_the_surfaces_show_the_capture(rig):
    svc, client, _stub = rig
    rec = svc.lineage.get(_chat(client)["lineage_id"])
    page = client.get(f"/lineage/{rec.lineage_id}", headers=H).json()["replay_capture"]
    stats = client.get("/cortex/stats").json()["replay_capture"]
    print(f"\nW0b-13 ({sys.platform}) /lineage/{{id}}.replay_capture {page}\n   /cortex/stats.replay_capture {stats}")
    assert page["captured"] is True and page["note"] is None
    assert page["inference_seed"] == rec.inference_seed and page["model_digest"] == _Capturing.DIGEST
    assert page["inference_config"]["provider"] == "stub" and page["environment"]["provider"] == "_Capturing"
    assert page["request_kept"] is True and page["request_encoding"] == REQUEST_ENCODING_MESSAGES
    assert page["request_bytes_raw"] == len(svc.lineage.request_body(rec.request_hash))
    assert STABLE_SYSTEM_PREFIX[:40] not in json.dumps(page), "sizes on the page, never the request's text"
    assert stats["keep_requests"] == "on" and stats["inference_seed"] == "on"
    assert stats["records"] == stats["with_seed"] == stats["with_request_kept"] == stats["request_bodies"] == 1
    assert stats["request_bytes_raw"] == page["request_bytes_raw"] and 0 < stats["request_bytes_stored"]
    assert "exact text it sent to the model" in stats["note"]


@pytest.mark.sqlite_only("opens a copy of the committed pre-run SQLite fixture (tests/fixtures/pre_run_data); its Postgres twin is the migration round trip (W3b part 4, T10)")
def test_every_pre_w0b_record_recomputes_and_reads_as_uncaptured(env):
    copy = env / "pre_run"
    shutil.copytree(FIXTURE, copy)
    lineage = LineageStore(copy / "kb.db")
    ids = [r["lineage_id"] for r in lineage.conn.execute("SELECT lineage_id FROM lineage_records ORDER BY ts")]
    views = [lineage.replay_capture_view(lineage.get(i)) for i in ids]
    intact = [lineage.verify_record(i)["record_intact"] for i in ids]
    bodies = lineage.verify_request_bodies()
    print(f"\nW0b-14 ({sys.platform}) pre-W0b records {len(ids)}: intact {intact}; captured "
          f"{[v['captured'] for v in views]}; bodies {bodies}")
    assert len(ids) == 5 and all(intact)
    assert not any(v["captured"] for v in views) and all(v["note"] for v in views)
    assert bodies["checked"] == 0 and bodies["records_without_body"] == 5
    # And a record written now, beside them, carries its capture and verifies.
    new = lineage.record_turn(query="q", injected=[], contradictions=[], model_id="m", response_text="a",
                              prompt_prefix="p", inference_seed=7, model_digest="sha256:" + "0" * 64,
                              inference_config={"options": {"num_ctx": 1}}, environment={"os": "x"},
                              request_hash=hashlib.sha256(b"r").hexdigest(), request_body=b"r")
    assert lineage.verify_record(new.lineage_id)["record_intact"] is True
    assert all(lineage.verify_record(i)["record_intact"] for i in ids)
    lineage.close()


# ---------------------------------------------------------------------------
# W0b part 4 — the verifier's findings, each pinned
# ---------------------------------------------------------------------------

from tests import _w0b_wire as wire                                   # noqa: E402


def _real_provider_turn(env, monkeypatch, provider, tag: str):
    """One /chat turn through a REAL provider class, the network replaced at
    the transport. Returns (record, kept messages, answering body, the other
    bodies, the services)."""
    daemon = wire.install(monkeypatch)
    svc = _services(env / tag, provider)
    rec = svc.lineage.get(_chat(TestClient(create_app(svc)))["lineage_id"])
    answering = [b for _u, b in daemon.bodies if wire.answering(b)]
    others = [b for _u, b in daemon.bodies if not wire.answering(b)]
    kept = json.loads(svc.lineage.request_body(rec.request_hash))
    return rec, kept, answering, others, svc


@pytest.mark.parametrize("separation", ["on", "off"])
def test_the_real_providers_put_the_recorded_seed_on_the_wire(env, monkeypatch, separation):
    """W0b-16 (the verifier's M2). Through the real Ollama and LM Studio
    classes: the seed on the wire is the record's seed, in each provider's
    place; extraction carries none; and `wire_body` rebuilt from the record
    alone equals the body that went (the same JSON object; httpx's bytes
    order keys as built, the canonical form sorts them)."""
    # Both positions of C-38's switch: on, the answering call goes through
    # `stream_pieces`; off, through `stream_chat` (part 5: dropping the seed
    # there passed everything).
    monkeypatch.setenv("NEXUS_REPLY_SEPARATION", separation)
    for provider, tag in ((OllamaProvider(), "ollama"), (LMStudioProvider(), "lmstudio")):
        rec, kept, answering, others, svc = _real_provider_turn(env, monkeypatch, provider, f"{tag}-{separation}")
        (sent,) = answering
        config = svc.lineage.inference_config(rec.inference_config_hash)
        assert config[rc.SEPARATION_KEY] is (separation == "on")
        print(f"\nW0b-16 ({sys.platform}) {tag}, separation {separation}: record seed {rec.inference_seed}, wire seed "
              f"{wire.wire_seed(sent)}; extraction bodies {len(others)} with seeds "
              f"{[wire.wire_seed(b) for b in others]}; digest {rec.model_digest}")
        assert isinstance(rec.inference_seed, int)
        assert wire.wire_seed(sent) == rec.inference_seed, f"{tag}: the record's seed is not the one sent"
        assert others and all(wire.wire_seed(b) is None for b in others), f"{tag}: extraction carried a seed"
        assert rc.wire_body(config, kept, rec.inference_seed) == sent, f"{tag}: the record does not rebuild the body"
        if tag == "ollama":
            assert sent["options"]["seed"] == rec.inference_seed and rec.model_digest == wire.DIGEST
        else:
            assert sent["seed"] == rec.inference_seed and "options" not in sent
            assert rec.model_digest is None and rec.capture_notes["model_digest"] == "provider exposes no content digest"


class _Bare:
    """A provider that declares nothing W0b reads: no seed, no options, no
    digest, no runtime facts (every pre-W0b stub has this shape)."""

    async def stream_chat(self, model, messages, num_ctx):
        yield ANSWER

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return False


def test_every_null_capture_field_carries_its_reason(env):
    """W0b-15 (S7). A provider that declares nothing: the seed, the digest
    and the options are NULL, each with the reason the provider boundary
    gave, and the environment is still recorded (NEXUS's own facts)."""
    svc = _services(env / "bare", _Bare())
    rec = svc.lineage.get(_chat(TestClient(create_app(svc)))["lineage_id"])
    print(f"\nW0b-15 ({sys.platform}) notes {rec.capture_notes}")
    assert (rec.inference_seed, rec.model_digest, rec.inference_config_hash) == (None, None, None)
    assert rec.capture_notes == {"inference_seed": "provider takes no seed",
                                 "inference_config_hash": "provider declares no request options",
                                 "model_digest": "provider exposes no content digest"}
    env_row = svc.lineage.environment(rec.environment_hash)
    assert env_row["provider"] == "_Bare" and env_row["provider_environment_absent"]


class _Awkward(_Capturing):
    """Options JSON cannot carry (a set) and a runtime fact holding a lone
    surrogate (a daemon's answer can): the capture must not cost the record."""

    def request_config(self, model, num_ctx, *, separate):
        return {"provider": "stub", "model": model, "stop": {"\n"}}

    async def inference_environment(self):
        return {"stub_runtime": "1.0-\udc80"}


def test_the_capture_never_costs_the_record(env):
    """W0b-17 (S1). Before part 4 both inputs made `record_turn` raise and the
    turn wrote no record (the verifier measured 0 records of 3 turns); the
    pre-W0b code wrote them. Now the record is written and verifies, the
    options are absent with the reason, and the environment is kept with its
    surrogate escaped (ASCII canonical JSON)."""
    stub = _Awkward()
    svc = _services(env / "awkward", stub)
    client = TestClient(create_app(svc))
    ids = [_chat(client, q)["lineage_id"] for q in ("what does interval training improve?", "and VO2 max?")]
    recs = [svc.lineage.get(i) for i in ids]
    print(f"\nW0b-17 ({sys.platform}) records {len([r for r in recs if r])}; notes {recs[0].capture_notes}; "
          f"environment {svc.lineage.environment(recs[0].environment_hash)}")
    assert all(r is not None for r in recs), "a record was lost to the capture"
    assert all(svc.lineage.verify_record(r.lineage_id)["record_intact"] for r in recs)
    assert recs[0].inference_config_hash is None
    assert recs[0].capture_notes["inference_config_hash"] == "not recorded: TypeError while serialising it"
    # Part 5 (SF2): the surrogate is kept escaped and said so, so the page
    # that shows it answers (it was a 500).
    assert svc.lineage.environment(recs[0].environment_hash)["stub_runtime"] == "1.0-\\udc80"
    assert recs[0].capture_notes["environment_hash_escaped"].startswith("values JSON cannot carry")
    assert recs[0].inference_seed is not None and recs[0].model_digest == _Capturing.DIGEST
    page = client.get(f"/lineage/{recs[0].lineage_id}", headers=H)
    assert page.status_code == 200 and page.json()["replay_capture"]["environment"]["stub_runtime"] == "1.0-\\udc80"


def test_an_absent_tag_is_not_cached_so_a_pull_is_seen(monkeypatch):
    """W0b-18 (S2). The tag is missing, then pulled: the same provider finds
    it on the next read (only a found digest is cached)."""
    import backend.providers.ollama as ollama_mod
    daemon = wire.install(monkeypatch)
    daemon.reset(tags={})
    p = ollama_mod.OllamaProvider()
    before = asyncio.run(p.served_model_digest(wire.MODEL))
    daemon.tags = {wire.MODEL: wire.DIGEST}                         # `ollama pull`
    after = asyncio.run(p.served_model_digest(wire.MODEL))
    print(f"\nW0b-18 ({sys.platform}) before the pull {before}; after {after}")
    assert before == (None, "tag not listed by /api/tags") and after == (wire.DIGEST, "")


def test_an_edited_options_or_environment_row_is_named(rig):
    """W0b-19 (S3). The rows the record's keys name are verified against
    those keys, beside the layers: an edited options row and a missing
    environment row are each named with the records resting on them."""
    svc, client, _stub = rig
    rec = svc.lineage.get(_chat(client)["lineage_id"])
    clean = verify_sealed_lineage(svc.lineage, svc.archive_chain)["capture_rows"]
    conn = svc.lineage.conn
    conn.execute("UPDATE inference_configs SET config_json = replace(config_json, '8192', '2048')")
    conn.execute("DELETE FROM environments")
    conn.commit()
    bad = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    print(f"\nW0b-19 ({sys.platform}) clean {clean}; edited {bad['capture_rows']}")
    assert clean["verdict"] == "intact" and clean["checked"] == 2
    reasons = {f["table"]: f["reason"] for f in bad["capture_rows"]["failures"]}
    assert reasons == {"inference_configs": "the row does not hash to its key",
                       "environments": "the record's key has no row"}
    assert all(f["lineage_ids"] == [rec.lineage_id] for f in bad["capture_rows"]["failures"])
    assert bad["capture_rows"]["verdict"] == "row_corrupted" and bad["verdict"] == "intact"


@pytest.mark.sqlite_only("opens a copy of the committed pre-run SQLite fixture (tests/fixtures/pre_run_data); its Postgres twin is the migration round trip (W3b part 4, T10)")
def test_a_record_with_nothing_kept_does_not_read_intact(env, monkeypatch):
    """W0b-20 (S4). The per-record verify of a record whose request was not
    kept says so, with the record's own reason; a pre-W0b record says it
    predates the capture."""
    monkeypatch.setenv("NEXUS_KEEP_REQUESTS", "off")
    svc = _services(env / "nokeep", _Capturing())
    rec = svc.lineage.get(_chat(TestClient(create_app(svc)))["lineage_id"])
    one = svc.lineage.verify_request_bodies(lineage_id=rec.lineage_id)
    copy = env / "pre_run"
    shutil.copytree(FIXTURE, copy)
    old = LineageStore(copy / "kb.db")
    first = old.conn.execute("SELECT lineage_id FROM lineage_records ORDER BY ts").fetchone()[0]
    pre = old.verify_request_bodies(lineage_id=first)
    old.close()
    print(f"\nW0b-20 ({sys.platform}) keep off: {one}; pre-W0b: {pre}")
    assert one == {"checked": 0, "failed": 0, "failures": [], "verdict": "none kept",
                   "reason": "not kept: NEXUS_KEEP_REQUESTS=off"}
    assert pre["verdict"] == "none kept" and pre["reason"] == "written before the replay capture (W0b)"


class _DigestOf(_Capturing):
    """A provider whose digest is whatever a test plants."""

    def __init__(self, digest):
        super().__init__()
        self._d = digest

    async def served_model_digest(self, model):
        return self._d, ("" if self._d else "")


@pytest.mark.parametrize("digest,named", [("sha256:ab\udc80", "str"), (b"sha256:ab", "bytes"),
                                          (12345, "int"), ("", None)])
def test_a_digest_the_record_cannot_carry_never_costs_it(env, digest, named):
    """W0b-21 (verifier 2's SF1, and N6). A digest that is not printable
    ASCII text is not recorded, with the reason; an empty one is an absence
    with its reason. Every record is written and verifies, and the turn after
    it writes too (the store is not left inside a failed transaction)."""
    svc = _services(env / f"digest-{named}", _DigestOf(digest))
    client = TestClient(create_app(svc))
    ids = [_chat(client, q)["lineage_id"] for q in ("what does interval training improve?", "and VO2 max?")]
    recs = [svc.lineage.get(i) for i in ids]
    print(f"\nW0b-21 ({sys.platform}) digest {digest!r}: records {sum(r is not None for r in recs)}; "
          f"notes {recs[0].capture_notes}")
    assert all(r is not None and svc.lineage.verify_record(r.lineage_id)["record_intact"] for r in recs)
    assert recs[0].model_digest is None
    want = (f"not recorded: the provider's digest is not printable ASCII ({named})" if named
            else "the provider returned an empty digest")
    assert recs[0].capture_notes["model_digest"] == want


class _FailRecordInsertOnce:
    """The lineage connection, with the record's own INSERT failing once,
    after its companions (options, environment, request body) have been
    written in the same transaction."""

    def __init__(self, conn):
        self._conn, self.failed = conn, False

    def execute(self, sql, *a, **kw):
        if not self.failed and sql.lstrip().startswith("INSERT INTO lineage_records"):
            self.failed = True
            raise sqlite3.OperationalError("planted failure inside the record's transaction")
        return self._conn.execute(sql, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_failed_record_write_is_rolled_back_and_the_next_one_lands(env, monkeypatch):
    """W0b-22 (SF1's second half). The record's INSERT fails after its
    companions were written: the whole transaction is rolled back (no
    companion row survives it) and the connection is not left open. Before
    part 5 every later record on the connection failed with "database is
    locked".

    W6 changed what the first turn tells the client, not what this holds:
    the turn whose write failed now ends in one `record_failed` frame where
    it used to end in `done` with a null `lineage_id`."""
    svc = _services(env / "rollback", _Capturing())
    client = TestClient(create_app(svc))
    proxy = _FailRecordInsertOnce(svc.lineage.conn)
    monkeypatch.setattr(svc.lineage, "conn", proxy)
    failed = client.post("/chat", headers=H, json={"message": "what does interval training improve?"})
    events = [f.split("\n", 1)[0].removeprefix("event: ") for f in failed.text.split("\n\n") if f.startswith("event: ")]
    assert events[-1] == "record_failed" and "done" not in events and "token" not in events, events
    rows_after_failure = {tb: proxy._conn.execute(f"SELECT COUNT(*) FROM {tb}").fetchone()[0]
                          for tb in ("lineage_records", "request_bodies", "inference_configs", "environments")}
    open_after_failure = proxy._conn.in_transaction
    second = _chat(client, "and VO2 max?")
    print(f"\nW0b-22 ({sys.platform}) the failed turn ended in {events[-1]}; rows after the failure {rows_after_failure}; "
          f"open {open_after_failure}; second {second['lineage_id']}")
    assert proxy.failed, "the planted write failure did not happen, so nothing above was tested"
    assert rows_after_failure == {"lineage_records": 0, "request_bodies": 0, "inference_configs": 0,
                                  "environments": 0}, "a companion row outlived its record's failed write"
    assert open_after_failure is False
    assert second["lineage_id"] is not None and svc.lineage.get(second["lineage_id"]) is not None


def test_a_missing_copy_of_a_captured_record_gets_its_true_reason(rig):
    """W0b-23 (SF3). A fully captured record has no notes; with its kept
    copy deleted it reads "missing", never "written before the capture"."""
    svc, client, _stub = rig
    rec = svc.lineage.get(_chat(client)["lineage_id"])
    assert rec.capture_notes is None
    svc.lineage.conn.execute("DELETE FROM request_bodies")
    svc.lineage.conn.commit()
    one = svc.lineage.verify_request_bodies(lineage_id=rec.lineage_id)
    print(f"\nW0b-23 ({sys.platform}) {one}")
    assert one["verdict"] == "none kept"
    assert one["reason"].startswith("the kept copy is missing")


@pytest.mark.sqlite_only("plants a str in the BLOB column: SQLite stores it, Postgres's bytea refuses it (a declared difference: Postgres types its columns)")
def test_capture_rows_per_record_and_a_non_text_row(rig):
    """W0b-24 (SF4, SF5). The per-record form checks that record's keys only;
    a BLOB where the text belongs is named, and /lineage/verify still answers
    with its layers."""
    svc, client, _stub = rig
    a = svc.lineage.get(_chat(client)["lineage_id"])
    # Record b gets keys of its own (verifier 3's S3): a provider declaring
    # nothing, so b keys one environment row (its provider is named in it)
    # and no options row. The per-record form must then check b's one key,
    # where the store-wide form checks three.
    svc.provider = _Bare()
    b_id = _chat(client, "and VO2 max?")["lineage_id"]
    conn = svc.lineage.conn
    conn.execute("UPDATE environments SET descriptor_json = ? WHERE environment_hash = ?",
                 (b"\x00blob", a.environment_hash))
    conn.commit()
    full = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    one = svc.lineage.verify_capture_rows(lineage_id=b_id)
    page = client.get("/lineage/verify", headers=H)
    print(f"\nW0b-24 ({sys.platform}) full {full['capture_rows']}; record b {one}; http {page.status_code}")
    reasons = {f["reason"] for f in full["capture_rows"]["failures"]}
    assert reasons == {"the row is not text"} and set(full["layers"]) == {"row", "seal", "chain"}
    assert page.status_code == 200
    assert one["checked"] == 1 and one["verdict"] == "intact", "record b checks its own key only"
    assert full["capture_rows"]["checked"] == 3


def test_an_unavailable_version_is_not_cached(monkeypatch):
    """W0b-25 (note A). /api/version answers 503 while the daemon loads and
    200 a turn later: the second read finds the version."""
    import backend.providers.ollama as ollama_mod
    answers = iter([503, 200])

    class _Client(wire.FakeDaemon):
        async def get(self, url, **kw):
            status = next(answers)
            return wire._Resp(status, {"version": "0.12.6"} if status == 200 else {})

    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", _Client)
    p = ollama_mod.OllamaProvider()
    got = [asyncio.run(p.inference_environment()) for _ in range(2)]
    print(f"\nW0b-25 ({sys.platform}) {got}")
    assert got == [{"ollama_version_absent": "/api/version answered 503"}, {"ollama_version": "0.12.6"}]


@pytest.mark.sqlite_only("plants a str in the BLOB column: SQLite stores it, Postgres's bytea refuses it (a declared difference: Postgres types its columns)")
def test_a_body_that_is_not_bytes_is_named_not_raised(rig):
    """W0b-26 (verifier 3's S1). A kept body stored as text or as a number is
    named as corrupted by the verifier and by the replay check; neither
    raises, and /lineage/verify still answers."""
    from scripts import replay_check
    from scripts.cp_b49_tally import open_read_only
    svc, client, _stub = rig
    recs = [svc.lineage.get(_chat(client, q)["lineage_id"]) for q in ("one?", "two?")]
    conn = svc.lineage.conn
    conn.execute("UPDATE request_bodies SET body = ? WHERE request_hash = ?", ("text", recs[0].request_hash))
    conn.execute("UPDATE request_bodies SET body = ? WHERE request_hash = ?", (42, recs[1].request_hash))
    conn.commit()
    got = svc.lineage.verify_request_bodies()
    ro = open_read_only(svc.lineage.db_path)
    reasons = {c.lineage_id: c.reason for c in replay_check.candidates(ro, 10)}
    ro.close()
    page = client.get("/lineage/verify", headers=H)
    print(f"\nW0b-26 ({sys.platform}) {[f['reason'] for f in got['failures']]}; replay {sorted(set(reasons.values()))}")
    assert {f["reason"] for f in got["failures"]} == {"the body is not bytes"} and got["failed"] == 2
    assert {reasons[r.lineage_id] for r in recs} == {"the kept request is corrupted: the body is not bytes"}
    assert page.status_code == 200


class _OddReason(_Capturing):
    """A provider whose reason for a missing digest carries a lone surrogate."""

    async def served_model_digest(self, model):
        return None, "tag \udc80 not listed"


def test_a_note_with_a_lone_surrogate_is_escaped_and_the_pages_answer(env):
    """W0b-27 (verifier 3's S2). The notes are escaped like every other
    companion, so /lineage/{id} and /lineage/recent answer for the record."""
    svc = _services(env / "oddreason", _OddReason())
    client = TestClient(create_app(svc))
    rec = svc.lineage.get(_chat(client)["lineage_id"])
    one = client.get(f"/lineage/{rec.lineage_id}", headers=H)
    recent = client.get("/lineage/recent", headers=H)
    print(f"\nW0b-27 ({sys.platform}) note {rec.capture_notes['model_digest']!r}; pages {one.status_code} {recent.status_code}")
    assert rec.capture_notes["model_digest"] == "tag \\udc80 not listed"
    assert one.status_code == 200 and recent.status_code == 200
    assert svc.lineage.verify_record(rec.lineage_id)["record_intact"] is True
