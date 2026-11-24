"""Packet AF.1 — the citation forecast.

WHAT THIS REPLACES, AND WHY THE NAME IS DULL
    "Prediction error" contained no prediction: `predicted_top` was seeded
    from the retrieved hits (agentic_loop's assemble_context), so the system
    "predicted" what it had already looked up — a reordering, not a forecast.
    P.4 quarantined `predict()` and RESERVED the `prediction` calibration
    mechanism for a real replacement. This is that replacement, named for
    exactly what it does (I-7): it forecasts WHICH CLAIMS THE ANSWER WILL
    CITE, seeded from the query's terms alone, persisted BEFORE retrieval
    runs, scored precision@k with k committed at forecast time, and always
    beside the prominence null — propagation is itself prominence-flavoured,
    so a forecast score without its control is not evidence.

WHAT CLEARED IT
    The decision rule was fixed before the number existed: score the
    degenerate predictor (cited = top-k of injected by weight-at-injection)
    on every scorable recorded turn; above ~0.8, build nothing. Measured on
    the operator's store: mean 0.399, median 0.333, 7 of 17 scorable turns at
    ZERO, no all-cited turns propping the mean. Headroom exists. (Seventeen
    turns is thin, and that number is the CONDITIONAL half — citation given
    the injected set — so it is necessary headroom, not proof.)

THE SENTINELS, per the packet:
    - persisted before the outcome exists (before `retrieve()` returns), and
      a forecast cannot be amended after scoring;
    - seeded from query terms only — the circularity being fixed must not
      silently return;
    - the null is computed for every scored forecast, never optionally;
    - an absent citation flag EXCLUDES the turn and never scores as a hit —
      counting it would manufacture the signal being tested;
    - reducibility: NEXUS_CITATION_FORECAST=off is exactly today's turn and
      writes no forecast;
    - non-vacuity: the fixture turn produces a non-empty forecast AND a
      non-empty cited set, or the scoring passes on turns with nothing to
      score — which is precisely how the sandbox null-script run returned
      zero.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from backend.cortex.edges import SynapticEdge
from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import run_turn
from backend.loop.state import NexusLoopState
from backend.memory.hippocampus import Hippocampus

ROOT = Path(__file__).resolve().parent.parent


class _Provider:
    """Two stream_chat calls per turn: the answer, then extraction."""

    def __init__(self, answer: str):
        self.answer = answer
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        if self.calls == 1:
            for tok in self.answer.split(" "):
                yield tok + " "
        else:
            yield "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


ANSWER = "Interval training improves vo2 max."
QUERY = "what does training improve?"


def _rig(tmp_path, monkeypatch):
    """A store + session graph where the forecast has something real to say.

    The graph holds a path from a bare query term ("training") to the anchor
    belief's subject node, laid in a PRIOR session — which is the forecast's
    entire premise: what the graph already associates with the question,
    before retrieval looks anything up. The decoy belief is MORE prominent
    (tier 0) and retrievable by the same term, but has no graph presence and
    is not used by the answer — so forecast and null genuinely diverge.
    """
    log = tmp_path / "calibration.jsonl"
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(log))
    store = TripleStore(tmp_path / "kb.db")
    anchor = store.add(subject="interval training", relation="improves",
                       object="vo2 max", weight=0.8, tier=1,
                       granularity=Granularity.TOPIC, source="human_assertion")
    decoy = store.add(subject="strength training", relation="requires",
                      object="rest days", weight=0.95, tier=0,
                      granularity=Granularity.TOPIC, source="wikidata")
    hippo = Hippocampus()
    hippo.upsert_node("training")
    hippo.ingest_triple("interval training", "improves", "vo2 max", weight=0.9)
    hippo.add_edge(SynapticEdge(source="training", target="interval training",
                                weight=0.9))
    return store, hippo, anchor, decoy, log


def _prediction_rows(log: Path) -> list[dict]:
    if not log.exists():
        return []
    rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    return [r for r in rows if r.get("mechanism") == "prediction"]


def _resolution_rows(log: Path) -> list[dict]:
    if not log.exists():
        return []
    rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    return [r for r in rows if r.get("mechanism") == "resolution"]


def _turn(store, hippo, *, query=QUERY, answer=ANSWER, retriever=None):
    retriever = retriever or TripleRetriever(store)
    frames = []

    async def drive():
        async for frame, _state in run_turn(
            NexusLoopState(), query, retriever=retriever,
            hippocampus=hippo, provider=_Provider(answer),
        ):
            frames.append(frame)

    asyncio.run(drive())
    for frame in reversed(frames):
        if frame.startswith("event: done\n"):
            return json.loads(frame.split("data: ", 1)[1].strip())
    raise AssertionError("no done frame emitted")


# ---------------------------------------------------------------------------
# Non-vacuity FIRST: the fixture must give the scorer something to score.
# ---------------------------------------------------------------------------

def test_the_fixture_turn_forecasts_something_and_cites_something(
        tmp_path, monkeypatch):
    """A non-empty forecast AND a non-empty cited set, in one turn. Without
    this, every sentinel below can pass on turns where there was nothing to
    score — which is precisely how the sandbox run of the null script
    returned zero and had to be reported as vacuous."""
    store, hippo, anchor, _decoy, log = _rig(tmp_path, monkeypatch)
    done = _turn(store, hippo)
    fc = done["citation_forecast"]

    assert fc["predicted"], "the forecast was empty — the fixture is vacuous"
    assert done["reinforcement"]["cited"] >= 1, (
        "nothing was cited — the fixture is vacuous")
    assert anchor in fc["predicted"]
    assert fc["scorable"] is True
    assert fc["precision"] == pytest.approx(1.0)
    store.close()


def test_coverage_is_declared_on_every_forecast(tmp_path, monkeypatch):
    """Establish-1: query terms that are not graph nodes seed nothing, so
    early-domain forecasts are near-empty BY THE SIGNAL'S NATURE. The
    forecast must say how much of the query it could see, or phrasing drift
    reads as forgetting."""
    store, hippo, _anchor, _decoy, _log = _rig(tmp_path, monkeypatch)
    done = _turn(store, hippo)
    cov = done["citation_forecast"]["coverage"]
    assert cov["total"] >= 2
    assert 1 <= cov["forecastable"] < cov["total"], (
        "the fixture query must contain BOTH a term the graph knows and one "
        "it does not, or this declares coverage only in the easy case")
    store.close()


def test_an_empty_forecast_is_declared_not_silent(tmp_path, monkeypatch):
    """A graph that knows none of the query's terms forecasts nothing —
    a property of the signal, not a bug, and DECLARED as such (I-6)."""
    store, _hippo, _anchor, _decoy, log = _rig(tmp_path, monkeypatch)
    done = _turn(store, Hippocampus())          # no session graph at all
    fc = done["citation_forecast"]
    assert fc["predicted"] == []
    assert fc["k"] == 0
    assert fc["scorable"] is False
    assert fc["reason"] == "empty_forecast"
    assert fc["coverage"]["forecastable"] == 0
    assert _prediction_rows(log) == [], (
        "an empty forecast wrote rows — there was nothing to commit to")
    store.close()


# ---------------------------------------------------------------------------
# THE ORDERING: persisted before the outcome exists.
# ---------------------------------------------------------------------------

def test_the_forecast_is_persisted_before_retrieve_returns(
        tmp_path, monkeypatch):
    """Not merely computed early — ON DISK before `retrieve()` runs, so the
    score cannot be fitted to what retrieval found. Proven at runtime: the
    retriever itself reads the ledger at the moment it is called."""
    store, hippo, _anchor, _decoy, log = _rig(tmp_path, monkeypatch)

    class _Witness(TripleRetriever):
        rows_at_retrieve = None

        def retrieve(self, *a, **kw):
            if _Witness.rows_at_retrieve is None:      # first call = the turn's
                _Witness.rows_at_retrieve = _prediction_rows(log)
            return super().retrieve(*a, **kw)

    done = _turn(store, hippo, retriever=_Witness(store))
    assert _Witness.rows_at_retrieve is not None, "retrieve() never ran"
    assert len(_Witness.rows_at_retrieve) >= 1, (
        "no forecast row was on disk when retrieval started — the forecast "
        "is being written after the outcome machinery begins")
    ids = {r["belief_id"] for r in _Witness.rows_at_retrieve}
    assert set(done["citation_forecast"]["predicted"]) <= ids, (
        "the reported forecast is not the one persisted pre-retrieval")
    store.close()


def test_k_is_committed_at_forecast_time_in_every_row(tmp_path, monkeypatch):
    """Each persisted row carries the k it was committed under, so the score
    cannot be re-denominated afterwards."""
    store, hippo, _anchor, _decoy, log = _rig(tmp_path, monkeypatch)
    done = _turn(store, hippo)
    rows = _prediction_rows(log)
    assert rows
    ks = {r["context"]["k"] for r in rows}
    assert ks == {done["citation_forecast"]["k"]}
    ranks = sorted(r["context"]["rank"] for r in rows)
    assert ranks == list(range(len(rows))), "ranks are not committed 0..n-1"
    store.close()


def test_the_forecast_call_precedes_retrieval_in_run_turn():
    """The static half of the ordering, the way P.4's accrual-ordering guard
    is written: line numbers from a parser, not a text search. The forecast
    call must sit before the first assemble_context call AND before `hits`
    exists — it cannot even see the name it must not use.

    MCP-2: the stages before the model call live in `prepare_turn` (the
    console's `run_turn` composes it), so that is the body read; the
    console and MCP both run the forecast from there."""
    path = ROOT / "backend" / "loop" / "agentic_loop.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "prepare_turn")

    def _calls(name):
        return [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                and (getattr(n.func, "id", None) == name
                     or getattr(n.func, "attr", None) == name)]

    forecast_lines = _calls("forecast_citations")
    ctx_lines = _calls("assemble_context")
    hits_lines = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", None) == "hits" for t in n.targets)]
    assert forecast_lines, "prepare_turn does not call forecast_citations"
    assert ctx_lines and hits_lines
    assert max(forecast_lines) < min(ctx_lines), (
        "the forecast fires after retrieval — the circularity is back")
    assert max(forecast_lines) < min(hits_lines)


# ---------------------------------------------------------------------------
# SEEDED FROM QUERY TERMS ONLY — the defect being fixed must not return.
# ---------------------------------------------------------------------------

def test_the_forecast_module_cannot_reach_retrieval():
    """AST over the forecast module: no call to retrieve / search_terms /
    get_relevant, anywhere. Its inputs are the graph, the store's exact-match
    anchor lookup, and the query's own terms — nothing retrieval produced."""
    path = ROOT / "backend" / "cortex" / "forecast.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {"retrieve", "search_terms", "get_relevant"}
    offenders = [
        f"forecast.py:{n.lineno}" for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) in forbidden
             or getattr(n.func, "attr", None) in forbidden)]
    assert not offenders, (
        f"the forecast touches retrieval machinery: {offenders} — seeded "
        f"from query terms only is the packet's whole point")


def test_the_retrieval_reach_check_can_actually_fail():
    """NON-VACUITY for the guard above, in the exact shape it must catch."""
    tree = ast.parse("hits = store.search_terms(['training'])\n")
    forbidden = {"retrieve", "search_terms", "get_relevant"}
    hits = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "attr", None) in forbidden]
    assert len(hits) == 1


def test_the_forecast_does_not_mutate_the_graph(tmp_path, monkeypatch):
    """Establish-3, at the mechanism: the forecast READS the graph. The main
    propagation excites nodes, advances fire counters and marks edges fired
    (which drives E.2 recurrence); a forecast that did any of that would
    change the turn it is trying to predict. Every mutable field on every
    node and edge must be byte-identical across a forecast call."""
    from backend.cortex.forecast import forecast_citations
    store, hippo, _anchor, _decoy, _log = _rig(tmp_path, monkeypatch)

    def _snapshot():
        nodes = {nid: (n.activation, n.fire_count)
                 for nid in hippo.nodes for n in (hippo.get_node(nid),)}
        edges = {(e.source, e.target): (e.weight, e.last_fired, e.fire_count,
                                        e.recurrence_count, e.last_counted_at,
                                        e.demoted)
                 for e in hippo.edges()}
        return nodes, edges

    before = _snapshot()
    fc = forecast_citations(hippo, store, QUERY, turn=1, model_id="m")
    assert fc is not None and fc.rows, "vacuous: the forecast found nothing"
    assert _snapshot() == before, (
        "the forecast mutated the session graph — the turn it predicts is "
        "no longer the turn that runs")
    store.close()


def test_retrieval_is_unchanged_by_the_forecast(tmp_path, monkeypatch):
    """Establish-3, end to end: the same store retrieves the same beliefs in
    the same order whether the forecast ran first or not."""
    from backend.cortex.forecast import forecast_citations
    store, hippo, _anchor, _decoy, _log = _rig(tmp_path, monkeypatch)
    retriever = TripleRetriever(store)
    bare = [(h.triple.id, h.channel) for h in retriever.retrieve(QUERY)]
    forecast_citations(hippo, store, QUERY, turn=1, model_id="m")
    again = [(h.triple.id, h.channel) for h in retriever.retrieve(QUERY)]
    assert bare == again and len(bare) >= 2
    store.close()


# ---------------------------------------------------------------------------
# SCORING: the null is mandatory; absent flags exclude; no amendment.
# ---------------------------------------------------------------------------

def test_the_null_is_computed_for_every_scored_forecast(tmp_path, monkeypatch):
    """Propagation is itself prominence-flavoured, so a forecast score
    reported without the prominence null for the SAME turn is not evidence.
    The two travel together or neither is reported."""
    store, hippo, _anchor, _decoy, _log = _rig(tmp_path, monkeypatch)
    done = _turn(store, hippo)
    fc = done["citation_forecast"]
    assert fc["scorable"] is True
    assert fc["precision"] is not None
    assert fc["null_precision"] is not None, (
        "a forecast score shipped without its control")
    assert 0.0 <= fc["null_precision"] <= 1.0
    store.close()


def test_scoring_refuses_a_precision_without_its_null():
    """The pairing is structural, not a habit of the one caller: the scorer's
    result can never carry a precision with a missing null."""
    from backend.cortex import forecast as f
    import inspect
    src = inspect.getsource(f.score_forecast)
    assert "null_precision" in src
    # And behaviourally, on every path the fixture exercises elsewhere:
    # asserted in test_the_null_is_computed_for_every_scored_forecast and in
    # the absent-flag / none-cited paths below, where BOTH are None.


def test_an_absent_citation_flag_excludes_the_turn(tmp_path, monkeypatch):
    """Nine of the operator's 41 records carried `cited: None` — pre-F.5
    shape. Treating an absent flag as cited would make precision trivially
    1.0 and MANUFACTURE the signal under test. The turn is excluded, by
    name, and nothing resolves."""
    from backend.cortex.forecast import forecast_citations, score_forecast
    store, hippo, _anchor, _decoy, log = _rig(tmp_path, monkeypatch)
    fc = forecast_citations(hippo, store, QUERY, turn=1, model_id="m")
    assert fc.rows, "vacuous"
    hits = TripleRetriever(store).retrieve(QUERY)

    block = score_forecast(fc, hits, cited=None)
    assert block["scorable"] is False
    assert block["reason"] == "flags_absent"
    assert block["precision"] is None and block["null_precision"] is None
    assert _resolution_rows(log) == [], (
        "an unknowable outcome was adjudicated — an absent flag must never "
        "score, as cited OR as uncited")
    store.close()


def test_a_turn_with_nothing_cited_is_recorded_but_not_scored(
        tmp_path, monkeypatch):
    """Establish-2's other half. Rows still resolve — each belief factually
    was not cited, and the ledger says so — but the turn-level precision
    comparison is declared unscorable, matching the exclusion the clearance
    measurement applied (`none_cited: 9`)."""
    from backend.cortex.forecast import forecast_citations, score_forecast
    store, hippo, _anchor, _decoy, log = _rig(tmp_path, monkeypatch)
    fc = forecast_citations(hippo, store, QUERY, turn=1, model_id="m")
    hits = TripleRetriever(store).retrieve(QUERY)

    block = score_forecast(fc, hits, cited=set())
    assert block["scorable"] is False and block["reason"] == "none_cited"
    assert block["precision"] is None and block["null_precision"] is None
    res = _resolution_rows(log)
    assert len(res) == len(fc.rows)
    assert all(r["resolved_outcome"] is False for r in res)
    store.close()


def test_a_forecast_cannot_be_amended_after_scoring(tmp_path, monkeypatch):
    """Append-only latest-row-wins means a second resolve WOULD rewrite the
    adjudication. The scorer refuses rows already resolved, so the first
    outcome stands — same discipline as Z.2's resolve_for_outcome."""
    from backend.cortex.forecast import forecast_citations, score_forecast
    from backend.eval.calibration_log import latest_by_entry
    store, hippo, anchor, _decoy, log = _rig(tmp_path, monkeypatch)
    fc = forecast_citations(hippo, store, QUERY, turn=1, model_id="m")
    hits = TripleRetriever(store).retrieve(QUERY)

    first = score_forecast(fc, hits, cited={anchor})
    assert first["scorable"] is True
    n_res = len(_resolution_rows(log))
    assert n_res == len(fc.rows) >= 1

    # The amendment attempt: rescore claiming nothing was cited.
    second = score_forecast(fc, hits, cited=set())
    assert len(_resolution_rows(log)) == n_res, (
        "rescoring appended new resolutions — the adjudication was rewritten")
    assert second["resolved"] == 0
    merged = latest_by_entry(log)
    outcomes = {merged[r.entry_id]["resolved_outcome"] for r in fc.rows}
    assert outcomes == {True}, "the first adjudication did not stand"
    store.close()


# ---------------------------------------------------------------------------
# REDUCIBILITY — off is exactly today, and writes nothing.
# ---------------------------------------------------------------------------

def test_off_writes_no_forecast_and_declares_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_CITATION_FORECAST", "off")
    store, hippo, _anchor, _decoy, log = _rig(tmp_path, monkeypatch)
    done = _turn(store, hippo)
    assert "citation_forecast" not in done, (
        "off still surfaces a forecast block — the rollback is cosmetic")
    assert _prediction_rows(log) == []
    assert done["reinforcement"]["cited"] >= 1, (
        "vacuity check: the turn itself must still work under off")
    store.close()


def test_on_and_off_turns_agree_on_everything_but_the_forecast(
        tmp_path, monkeypatch):
    """The forecast is a read plus a ledger append — the turn's own outcome
    (what was injected, cited, reinforced, stored) must be identical either
    way, or 'reads the graph' is a claim rather than a property."""
    payloads = {}
    for mode in ("on", "off"):
        sub = tmp_path / mode
        sub.mkdir()
        monkeypatch.setenv("NEXUS_CITATION_FORECAST", mode)
        monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(sub / "c.jsonl"))
        store = TripleStore(sub / "kb.db")
        store.add(subject="interval training", relation="improves",
                  object="vo2 max", weight=0.8, tier=1,
                  granularity=Granularity.TOPIC, source="human_assertion")
        store.add(subject="strength training", relation="requires",
                  object="rest days", weight=0.95, tier=0,
                  granularity=Granularity.TOPIC, source="wikidata")
        hippo = Hippocampus()
        hippo.upsert_node("training")
        hippo.ingest_triple("interval training", "improves", "vo2 max",
                            weight=0.9)
        hippo.add_edge(SynapticEdge(source="training",
                                    target="interval training", weight=0.9))
        done = _turn(store, hippo)
        done.pop("citation_forecast", None)
        done.pop("lineage_id", None)                       # uuid per run
        payloads[mode] = done
        store.close()
    assert payloads["on"] == payloads["off"]


# ---------------------------------------------------------------------------
# LANDING WHERE IT WAS RESERVED
# ---------------------------------------------------------------------------

def test_the_forecast_writes_the_reserved_mechanism_and_only_it(
        tmp_path, monkeypatch):
    """The rows land in `prediction` — the channel P.4 reserved for exactly
    this — with the stated confidence being the anchor activation the
    forecast actually ranked by, not a constant."""
    store, hippo, _anchor, _decoy, log = _rig(tmp_path, monkeypatch)
    _turn(store, hippo)
    rows = _prediction_rows(log)
    assert rows, "nothing landed in the reserved channel"
    assert all(0.0 < r["stated_confidence"] <= 1.0 for r in rows)
    assert all(r["context"]["forecast_id"] for r in rows)
    assert all(r["resolved_outcome"] is None for r in rows), (
        "a forecast row was born resolved — the outcome did not exist yet")
    store.close()
