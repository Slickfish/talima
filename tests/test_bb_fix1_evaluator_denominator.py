"""BB Part 1 — the evaluator's empty denominator.

THE DEFECT: `faithfulness_score` and `context_precision_score` divide by the
injected count and returned 1.0 when it was zero — "nothing to be unfaithful
to". Every turn that retrieved nothing scored perfect, in all three history
shapes, so the shape that injected less scored higher for it. It is the
aborted-cycle defect (`edges_promoted: 0` built from a default) in the tool
the harness used to judge shapes.

THE FIX: the value is None ("not computed") on the dataclass, in the JSONL
record, on the `eval_metric` frame (JSON null) and on `/eval/recent`; the
composite is None whenever a component is; the frontend renders null as the
words "not computed", never as `0.00`. `n_injected_triples` on the record
says why.

THE SECOND PROBLEM, characterised and not fixed: `answer_relevancy` is
Jaccard between a handful of query terms and up to 64 response terms, so it
is the answer's brevity. Pinned here so the property is on record; the
column is out of every shape report.

GUARDS: an AST check that the empty-input guard in each fraction returns no
numeric constant, with a must-fail companion fed the old shape; the
guard/must-fail pairs are enumerated over this file.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

from backend.eval.ragas import (
    NOT_COMPUTED_TEXT,
    TurnEvaluator,
    answer_relevancy_score,
    context_precision_score,
    faithfulness_score,
    render_metric,
)
from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import run_turn
from backend.loop.state import NexusLoopState
from tests._tracked import tracked
from tests.test_aw_prompt_render import QUERY, _rig

ROOT = Path(__file__).resolve().parent.parent
RAGAS = ROOT / "backend" / "eval" / "ragas.py"

GUARDS = [
    ("test_the_empty_guards_return_no_number", "test_the_checker_flags_the_old_shape"),
]


class _Provider:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    async def stream_chat(self, model, messages, num_ctx):
        self.calls += 1
        yield self.answer if self.calls % 2 == 1 else "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _frames(store, hippo, lineage, query, evaluator):
    frames = []

    async def drive():
        async for f, _s in run_turn(NexusLoopState(), query, retriever=TripleRetriever(store),
                                    hippocampus=hippo, provider=_Provider("Training improves vo2 max."),
                                    lineage=lineage, evaluator=evaluator):
            frames.append(f)
    asyncio.run(drive())

    def one(name):
        return next(json.loads(f.split("data: ", 1)[1].strip())
                    for f in reversed(frames) if f.startswith(f"event: {name}\n"))
    return one("eval_metric"), one("done")


# ---------------------------------------------------------------------------
# The value: None, not 1.0 — and unchanged where there is a denominator
# ---------------------------------------------------------------------------

def test_no_denominator_is_not_computed_and_a_denominator_still_divides(tmp_path):
    assert faithfulness_score("anything", []) is None
    assert context_precision_score([], set()) is None
    assert context_precision_score([], {"python"}) is None
    store = TripleStore(tmp_path / "kb.db")
    store.add(subject="python", relation="is_a", object="language", weight=0.9, tier=1,
              granularity=Granularity.TOPIC, source="t")
    hits = TripleRetriever(store).retrieve("python language")
    assert hits
    assert faithfulness_score("python is a programming language", hits) == 1.0
    assert faithfulness_score("kafka is a streaming platform", hits) == 0.0
    assert context_precision_score(hits, {"python"}) == 1.0
    assert context_precision_score(hits, set()) == 0.0
    store.close()


def test_the_record_and_the_log_carry_null_and_the_composite_follows(tmp_path):
    ev = TurnEvaluator(log_path=tmp_path / "m.jsonl")
    m = ev.evaluate(turn=1, query="what is python", response="python is a language", hits=[])
    assert m.faithfulness is None and m.context_precision is None and m.composite() is None
    assert m.n_injected_triples == 0
    assert 0.0 <= m.answer_relevancy <= 1.0, "relevancy has its own denominator and still computes"
    rec = json.loads((tmp_path / "m.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["faithfulness"] is None and rec["context_precision"] is None
    assert rec["n_injected_triples"] == 0


def test_render_never_prints_a_numeral_for_an_absent_value():
    assert render_metric(None) == NOT_COMPUTED_TEXT
    assert render_metric(None, 2) == NOT_COMPUTED_TEXT
    assert render_metric(0.0) == "0.000" and render_metric(1.0, 2) == "1.00"


# ---------------------------------------------------------------------------
# The surfaces: the frame, the route, the frontend
# ---------------------------------------------------------------------------

def test_the_frame_is_null_at_injected_zero_and_a_number_otherwise(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    ev = TurnEvaluator()
    metric, done = _frames(store, hippo, lineage, "what about zebras?", ev)
    assert done["reinforcement"]["injected"] == 0, "the control turn must inject nothing"
    assert metric["faithfulness"] is None and metric["context_precision"] is None
    assert metric["composite"] is None and metric["n_injected_triples"] == 0
    assert isinstance(metric["answer_relevancy"], float)
    metric2, done2 = _frames(store, hippo, lineage, QUERY, ev)
    assert done2["reinforcement"]["injected"] > 0
    for k in ("faithfulness", "context_precision", "composite"):
        assert isinstance(metric2[k], float) and 0.0 <= metric2[k] <= 1.0, (k, metric2)
    assert metric2["n_injected_triples"] == done2["reinforcement"]["injected"]
    store.close(); lineage.close()


def test_eval_recent_serves_null_not_a_score(tmp_path):
    from fastapi.testclient import TestClient
    from backend.server import create_app, create_services
    services = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                               eval_log_path=tmp_path / "m.jsonl",
                               archive_db_path=tmp_path / "archive.db",
                               snapshots_dir=tmp_path / "snapshots")
    services.evaluator.evaluate(turn=1, query="what is python", response="a language", hits=[])
    with TestClient(create_app(services)) as c:
        body = c.get("/eval/recent").json()
    rec = body["records"][-1]
    assert rec["faithfulness"] is None and rec["composite"] is None
    assert rec["n_injected_triples"] == 0


def test_the_frontend_renders_null_as_words_not_as_a_score():
    """The handler is JavaScript, so this is a substring check on the one
    frontend that binds `eval_metric` (the SSE contract test names it)."""
    bound = [p for p in tracked("frontend/*.html")
             if "case 'eval_metric'" in p.read_text(encoding="utf-8")]
    assert bound, "no frontend binds eval_metric"
    for p in bound:
        src = p.read_text(encoding="utf-8")
        handler = src.split("case 'eval_metric'", 1)[1].split("case '", 1)[0]
        assert NOT_COMPUTED_TEXT in handler, f"{p.name}: null would render through fmt() as 0.00"
        for key in ("composite", "answer_relevancy", "faithfulness", "context_precision"):
            assert f"fmt(data.{key}" not in handler, f"{p.name}: {key} bypasses the null rendering"


# ---------------------------------------------------------------------------
# What answer_relevancy computes — pinned, not repaired
# ---------------------------------------------------------------------------

def test_answer_relevancy_is_the_answers_brevity():
    q = "what does interval training improve?"
    short = "Interval training improves vo2 max and reduces resting heart rate."
    essay = short + (" It raises the maximal rate of oxygen uptake through repeated bouts of "
                     "high-intensity effort separated by recovery periods, lowers blood pressure "
                     "modestly, improves insulin sensitivity and mitochondrial density in skeletal "
                     "muscle, raises lactate threshold so a runner sustains a faster pace before "
                     "fatigue accumulates, and is time-efficient because sessions are shorter than "
                     "steady-state cardio; sleep and nutrition support the adaptation.")
    echo = "interval training improve"
    assert answer_relevancy_score(q, echo) == 1.0, "the question repeated back scores perfect"
    assert answer_relevancy_score(q, short) > 3 * answer_relevancy_score(q, essay), (
        "the same correct claim scores lower the more the answer says around it")
    assert answer_relevancy_score(q, essay) < 0.05, "a thorough correct answer sits near the 0.03 observed"


# ---------------------------------------------------------------------------
# The static guard and its must-fail
# ---------------------------------------------------------------------------

def empty_guard_numeric_returns(source: str, function: str, name: str = "hits") -> list[int]:
    """Lines of `return <number>` inside an `if not <name>:` guard of `function`."""
    out = []
    for fn in ast.walk(ast.parse(source)):
        if not (isinstance(fn, ast.FunctionDef) and fn.name == function):
            continue
        for node in ast.walk(fn):
            if not (isinstance(node, ast.If) and isinstance(node.test, ast.UnaryOp)
                    and isinstance(node.test.op, ast.Not)
                    and isinstance(node.test.operand, ast.Name) and node.test.operand.id == name):
                continue
            for inner in node.body:
                for r in ast.walk(inner):
                    if isinstance(r, ast.Return) and isinstance(r.value, ast.Constant) \
                            and isinstance(r.value.value, (int, float)) \
                            and not isinstance(r.value.value, bool):
                        out.append(r.lineno)
    return out


def _has_empty_guard(source: str, function: str, name: str = "hits") -> bool:
    for fn in ast.walk(ast.parse(source)):
        if isinstance(fn, ast.FunctionDef) and fn.name == function:
            return any(isinstance(n, ast.If) and isinstance(n.test, ast.UnaryOp)
                       and isinstance(n.test.operand, ast.Name) and n.test.operand.id == name
                       for n in ast.walk(fn))
    return False


def test_the_empty_guards_return_no_number():
    src = RAGAS.read_text(encoding="utf-8")
    for fn in ("faithfulness_score", "context_precision_score"):
        assert _has_empty_guard(src, fn), f"{fn} lost its empty-input guard — the checker would be vacuous"
        assert empty_guard_numeric_returns(src, fn) == [], fn


def test_the_checker_flags_the_old_shape():
    old = ("def faithfulness_score(response, hits):\n"
           "    if not hits:\n        return 1.0\n"
           "    return 0.5\n")
    assert empty_guard_numeric_returns(old, "faithfulness_score") == [3]
    fixed = old.replace("return 1.0", "return None")
    assert empty_guard_numeric_returns(fixed, "faithfulness_score") == []


def test_every_guard_here_has_its_must_fail_in_this_file():
    names = {n.name for n in ast.walk(ast.parse(Path(__file__).read_text(encoding="utf-8")))
             if isinstance(n, ast.FunctionDef)}
    for guard, must_fail in GUARDS:
        assert guard in names and must_fail in names
