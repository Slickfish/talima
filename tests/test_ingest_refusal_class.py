"""Every `store.add` in a loop must survive one refused belief.

THE DEFECT, MEASURED LIVE
    Packet K made `add()` RAISE on refusal. `consolidate_from_hippocampus`
    calls it in a loop with no handler, and it is the FIRST statement of
    `KairosProcess._run_cycle_body`. So one ungated belief aborted the entire
    cycle — consolidation, rebalance, decay, procedural promotion, the dedup
    scan, semantic indexing, the graph persist, the snapshot and the
    governance seal — and F.2's blanket handler logged it and kept the daemon
    alive. The offending edge stays in the hippocampus, so the next poll
    retried it. Forever.

    Reproduced with one hippocampus edge whose target is 12 tokens against a
    cap of 8:

        cycle_error : BeliefRejected [too_many_tokens] on object='TAK-441 …'
        edges_promoted 0 | rebalanced {} | snapshot False | sealed 0 | rows 0
        cycle 2 aborts identically

WHY THIS IS A CLASS AND NOT AN INSTANCE
    Eight `add()` sites exist. Three already handle refusal per belief
    (`agentic_loop.py` twice, `server.py` once) — the pattern was in the
    codebase the whole time. Five did not, and two of those are inside the
    same KAIROS cycle: `consolidate_from_hippocampus`, and
    `store_pattern_triple` via the nested loop in `ProceduralMemory.promote`.
    The second had never fired only because the first aborted before reaching
    it, so fixing one would have surfaced the other as a NEW defect on the
    operator's next poll.

WHAT THIS FILE ASSERTS
    Behaviour first — a real store, a real gate, a real refused belief, and
    the survivors must still be written. The AST guard is second, and it
    exists so a NINTH site cannot ship unguarded; the three long-correct sites
    are what prove its population is not empty.
"""
from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path

import pytest

from backend.cortex.edges import SynapticEdge
from backend.kb.gate import MAX_TERM_TOKENS
from backend.kb.granularity import Granularity
from backend.memory.hippocampus import Hippocampus
from backend.memory.neocortex import Neocortex
from backend.memory.procedural import ProceduralMemory

ROOT = Path(__file__).resolve().parent.parent

# The operator's own row, and the shape of it: a phrase where a term belongs.
TOO_LONG = "TAK-441 prolonged the QT interval as a potential indicator of cardiac risk"
FINE = "qtc interval"
# Single letters are refused as `too_short`, so the SOURCE terms have to be
# real too — the first draft used "a" and "b" and got two refusals where it
# expected one, which is the counter doing its job on the fixture.
SRC_BAD, SRC_GOOD = "tak441", "cardiac"


def test_the_fixture_is_genuinely_refused():
    """NON-VACUITY. If the gate ever stops refusing this, every test below
    passes while exercising nothing."""
    from backend.kb.gate import inspect_triple
    assert len(TOO_LONG.split()) > MAX_TERM_TOKENS
    reason = inspect_triple("qtc", "co_activated", TOO_LONG,
                            evidence_class="consolidation", weight=0.9)
    assert reason and "too_many_tokens" in reason, (
        f"the gate no longer refuses the fixture ({reason!r}) — pick a term "
        f"it does refuse, or this whole file is theatre")


# ---------------------------------------------------------------------------
# consolidation
# ---------------------------------------------------------------------------

def _hippocampus_with(bad: str, good: str) -> Hippocampus:
    h = Hippocampus()
    for node in (SRC_BAD, bad, SRC_GOOD, good):
        h.upsert_node(node)
    # Both clear E.2's two-stage gate: intensity AND refractory recurrence.
    h.add_edge(SynapticEdge(source=SRC_BAD, target=bad, weight=0.9,
                            fire_count=9, recurrence_count=5))
    h.add_edge(SynapticEdge(source=SRC_GOOD, target=good, weight=0.9,
                            fire_count=9, recurrence_count=5))
    return h


def test_consolidation_survives_a_refused_belief_and_promotes_the_rest(tmp_path):
    neo = Neocortex(tmp_path / "kb.db")
    promoted = neo.consolidate_from_hippocampus(_hippocampus_with(TOO_LONG, FINE))
    assert promoted == 1, (
        "the good edge was not promoted — one refused belief still costs the "
        "whole pass")
    assert neo.store.count() == 1


def test_consolidation_declares_what_it_refused(tmp_path):
    """Counted, not logged. A pass that silently promotes 4 of 5 is the same
    defect one level down (I-6)."""
    neo = Neocortex(tmp_path / "kb.db")
    neo.consolidate_from_hippocampus(_hippocampus_with(TOO_LONG, FINE))
    assert neo.last_refusals["count"] == 1
    assert "too_many_tokens" in neo.last_refusals["first_reason"]


def test_a_clean_pass_reports_no_refusals(tmp_path):
    """The counter must not be a permanent scar — a later clean cycle has to
    read clean, or `refused` stops meaning "this cycle"."""
    neo = Neocortex(tmp_path / "kb.db")
    neo.consolidate_from_hippocampus(_hippocampus_with(TOO_LONG, FINE))
    assert neo.last_refusals["count"] == 1
    neo.consolidate_from_hippocampus(_hippocampus_with("zebra crossing", "yellow line"))
    assert neo.last_refusals["count"] == 0
    assert neo.last_refusals["first_reason"] == ""


# ---------------------------------------------------------------------------
# procedural promotion — the one that had never fired
# ---------------------------------------------------------------------------

# What the procedural path can actually be refused on — established by
# probing the gate rather than assumed. `promote()` writes one triple per TERM,
# and `extract_terms` yields single lowercase words of >=2 chars, so
# `too_many_tokens` and `too_short` are BOTH unreachable there. The reachable
# rule is `null_literal`: a query containing "none", "null", "true",
# "undefined" or "nan" produces a term the gate refuses.
#
# The first draft of this test used the long phrase and asserted a refusal
# that could never happen — it would have passed against unfixed code for the
# wrong reason. Recorded because "the identical defect" was a claim I made
# before checking, and checking changed which rule it is, not whether it bites.
REFUSED_TERM_QUERY = "does none of this apply to the cardiac interval"


def test_procedural_promotion_survives_a_refused_term(tmp_path):
    """`promote()` writes one triple per TERM of each pattern, and the terms
    come from user queries. It had never been seen to fail because
    consolidation aborts three lines earlier in the same cycle body."""
    from backend.kb.retriever import extract_terms
    assert "none" in extract_terms(REFUSED_TERM_QUERY), (
        "the fixture no longer produces the refused term")

    neo = Neocortex(tmp_path / "kb.db")
    proc = ProceduralMemory(threshold=1)
    for _ in range(3):
        proc.observe(REFUSED_TERM_QUERY)
    promoted = proc.promote(neo)

    assert promoted == 1, "the pattern was lost entirely to one refused term"
    assert neo.last_refusals["count"] == 1
    assert "null_literal" in neo.last_refusals["first_reason"]
    # The other terms of the same pattern were still written.
    assert neo.store.count() == len(extract_terms(REFUSED_TERM_QUERY)) - 1


# ---------------------------------------------------------------------------
# the cycle, end to end
# ---------------------------------------------------------------------------

def test_the_kairos_cycle_completes_with_a_refused_belief(tmp_path, monkeypatch):
    """The blast radius, closed. Everything downstream of line 325 must run."""
    monkeypatch.setenv("NEXUS_API_TOKEN", "refusal-class-token")
    from backend.server import create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    h = svc.hippocampus
    for node in (SRC_BAD, TOO_LONG, SRC_GOOD, FINE):
        h.upsert_node(node)
    h.add_edge(SynapticEdge(source=SRC_BAD, target=TOO_LONG, weight=0.9,
                            fire_count=9, recurrence_count=5))
    h.add_edge(SynapticEdge(source=SRC_GOOD, target=FINE, weight=0.9,
                            fire_count=9, recurrence_count=5))

    svc.kairos.session_ended()
    r = asyncio.run(svc.kairos.run_one_cycle(force_snapshot=True))

    assert r.skipped is False, f"the cycle still aborts: {r.cycle_error}"
    assert r.cycle_error == ""
    assert r.edges_promoted == 1
    assert r.consolidation_refused == 1
    assert "too_many_tokens" in r.refusal_first_reason
    # Everything downstream of the abort point now runs.
    assert r.rebalanced != {}, "rebalance never ran"
    assert r.snapshot_taken is True, "the snapshot never ran"


def test_edges_promoted_is_none_on_an_aborted_cycle(tmp_path, monkeypatch):
    """THE ONE THAT MATTERED MOST.

    `edges_promoted` is assigned at the END of the cycle body. On an abort the
    handler built a result from dataclass DEFAULTS, so `edges_promoted` read
    `0` — and OQ-1 took that as evidence consolidation had never fired. It was
    not a measurement; it was a default wearing one's clothes. `0` is a claim
    the abort path has no right to make.

    Same class as the seal that reported itself unset while three records had
    just been sealed (U.1).
    """
    monkeypatch.setenv("NEXUS_API_TOKEN", "refusal-class-token")
    from backend.memory.kairos import KairosCycleResult
    from backend.server import create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")

    def _boom(*a, **kw):
        raise RuntimeError("something the cycle cannot survive")

    monkeypatch.setattr(svc.neocortex, "consolidate_from_hippocampus", _boom)
    svc.kairos.session_ended()
    r = asyncio.run(svc.kairos.run_one_cycle(force_snapshot=True))

    assert r.skipped is True and r.skipped_reason == "error"
    assert r.edges_promoted is None, (
        "an aborted cycle reported edges_promoted as a number. A reader "
        "cannot tell that from 'consolidation ran and promoted none', which "
        "is exactly the reading that made OQ-1's evidence wrong.")
    assert r.consolidation_refused is None
    assert r.refusal_by_reason is None, (
        "an aborted cycle reported an empty breakdown, which reads as "
        "'nothing was refused' — the same claim `0` made")
    # And `None` must mean "not measured", not "sometimes null": on a cycle
    # that ran, the same fields are numbers. That is asserted a few lines up
    # in test_the_kairos_cycle_completes_with_a_refused_belief; here we pin
    # only that the field's DECLARED default is still a number, so a future
    # `= None` default would not make every abort indistinguishable from a
    # healthy zero again.
    from dataclasses import fields
    decl = {f.name: f.default for f in fields(KairosCycleResult)}
    assert decl["edges_promoted"] == 0
    assert decl["consolidation_refused"] == 0


def test_the_two_refusal_counters_are_independent(tmp_path):
    """THE 5235 COINCIDENCE.

    The ledger is shared — `consolidate_from_hippocampus` and
    `store_pattern_triple` both write it — and only the first re-scoped it.
    `ProceduralMemory.promote()` does not own the ledger and cannot reset it,
    so KAIROS's second read was a RUNNING TOTAL. In steady state `promote()`
    writes nothing (`self.promoted` suppresses re-promotion), so the second
    read equalled the first exactly and both counters printed 5235 on the
    operator's cycle. Two counters agreeing perfectly is not corroboration.
    """
    neo = Neocortex(tmp_path / "kb.db")
    neo.consolidate_from_hippocampus(_hippocampus_with(TOO_LONG, FINE))
    consolidation = dict(neo.last_refusals)
    assert consolidation["count"] == 1 and consolidation["stage"] == "consolidation"

    # A procedural pass that refuses NOTHING must read zero, not inherit one.
    neo.begin_refusal_pass("procedural")
    ProceduralMemory(threshold=1).promote(neo)
    procedural = dict(neo.last_refusals)
    assert procedural["stage"] == "procedural"
    assert procedural["count"] == 0, (
        "the procedural counter inherited consolidation's — that is how both "
        "read 5235")
    assert procedural["first_reason"] == ""


def test_the_kairos_cycle_scopes_the_two_counters_separately(tmp_path, monkeypatch):
    """The same property through the real cycle body, which is where the two
    reads actually happen. Non-vacuous: consolidation must refuse something,
    or "both read zero" would pass for the wrong reason."""
    monkeypatch.setenv("NEXUS_API_TOKEN", "refusal-class-token")
    from backend.server import create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    for node in (SRC_BAD, TOO_LONG, SRC_GOOD, FINE):
        svc.hippocampus.upsert_node(node)
    svc.hippocampus.add_edge(SynapticEdge(source=SRC_BAD, target=TOO_LONG,
                                          weight=0.9, fire_count=9,
                                          recurrence_count=5))
    svc.kairos.session_ended()
    r = asyncio.run(svc.kairos.run_one_cycle(force_snapshot=True))

    assert r.consolidation_refused == 1, "nothing was refused — vacuous"
    assert r.procedural_refused == 0, (
        f"procedural_refused is {r.procedural_refused}, the same running "
        f"total that made both counters read 5235")
    assert r.refusal_by_reason == {"object:too_many_tokens": 1}


def test_the_refusal_log_is_one_line_per_pass_not_one_per_belief(tmp_path, caplog):
    """THE CONSOLE. 5,235 WARNING lines every five minutes buried the stream,
    and the KAIROS abort this packet fixed was visible in exactly that stream.

    The summary must carry what the individual lines carried — how many, by
    which rule, and one worked example — or this is suppression rather than
    summarisation.
    """
    import logging
    neo = Neocortex(tmp_path / "kb.db")
    h = Hippocampus()
    for i in range(40):
        s = f"source{i}"
        h.upsert_node(s), h.upsert_node(TOO_LONG)
        h.add_edge(SynapticEdge(source=s, target=TOO_LONG, weight=0.9,
                                fire_count=9, recurrence_count=5))
    h.upsert_node(SRC_GOOD), h.upsert_node(FINE)
    h.add_edge(SynapticEdge(source=SRC_GOOD, target=FINE, weight=0.9,
                            fire_count=9, recurrence_count=5))

    with caplog.at_level(logging.WARNING, logger="nexus.neocortex"):
        neo.consolidate_from_hippocampus(h)
    lines = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(lines) == 1, (
        f"{len(lines)} warning lines for 40 refusals — the wall is back")

    msg = lines[0].getMessage()
    assert "refused 40 of 41" in msg, f"the summary lost the counts: {msg}"
    assert "too_many_tokens×40" in msg, f"the summary lost the breakdown: {msg}"
    assert "TAK-441" in msg, f"the summary lost the worked example: {msg}"

    # And the individual lines still exist one level down, for when they are
    # what you actually want.
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="nexus.neocortex"):
        neo.consolidate_from_hippocampus(h)
    debug = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debug) == 40, f"per-belief detail was deleted, not demoted ({len(debug)})"


def test_a_clean_pass_logs_nothing_at_all(tmp_path, caplog):
    """NON-VACUITY the other way: the summary must be silent when there is
    nothing to summarise, or it becomes its own wall — one line per cycle
    forever, saying zero."""
    import logging
    neo = Neocortex(tmp_path / "kb.db")
    with caplog.at_level(logging.WARNING, logger="nexus.neocortex"):
        neo.consolidate_from_hippocampus(_hippocampus_with("zebra crossing",
                                                           "yellow line"))
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_kairos_status_carries_the_fields_oq1_reads(tmp_path, monkeypatch):
    """OQ-1's check is `edges_promoted` in `/kairos/status`. That check was
    read without `skipped`, which is how a default got recorded as evidence.
    Both must be on the surface, or the corrected reading is not performable
    against the thing it names."""
    monkeypatch.setenv("NEXUS_API_TOKEN", "refusal-class-token")
    from fastapi.testclient import TestClient

    from backend.server import create_app, create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    svc.kairos.session_ended()
    asyncio.run(svc.kairos.run_one_cycle(force_snapshot=True))

    body = TestClient(create_app(svc)).get("/kairos/status").json()
    last = body["last_cycle"]
    assert last is not None
    for key in ("skipped", "skipped_reason", "cycle_error", "edges_promoted",
                "consolidation_refused", "procedural_refused",
                "refusal_first_reason"):
        assert key in last, f"/kairos/status does not report {key}"


# ---------------------------------------------------------------------------
# seeding — the biggest loop in the codebase
# ---------------------------------------------------------------------------

# WHAT A SEEDER CAN ACTUALLY BE REFUSED ON — probed, not assumed.
# `classify()` puts conceptnet and wikidata in `external` and the fixture in
# `fixture`; none is in `STRICT_CLASSES`, so `too_many_tokens`, `multi_
# preposition`, `pronoun` and the rest of the strict band are ALL unreachable
# from a seeder. `TOO_LONG` — 12 tokens — sails straight in, and the first
# draft of this test asserted a refusal that cannot happen. Second time in
# this file: a fixture built from the defect's own shape rather than from the
# rule that governs the path.
#
# The universal rules do apply, and `too_long` (>120 chars) is the one a real
# Wikidata `?valueLabel` meets — some labels are a sentence.
SEED_REFUSED_OBJECT = (
    "a chronic progressive condition of the myocardium characterised by "
    "impaired ventricular filling and reduced ejection fraction across "
    "successive clinical stages")


def test_the_seed_fixture_is_genuinely_refused():
    """NON-VACUITY, and the assertion that would have caught the first draft."""
    from backend.kb.gate import MAX_TERM_CHARS, inspect_triple
    assert len(SEED_REFUSED_OBJECT) > MAX_TERM_CHARS
    assert inspect_triple("cardiac", "described_by", SEED_REFUSED_OBJECT,
                          evidence_class="external") == "object:too_long"
    assert inspect_triple("cardiac", "described_by", TOO_LONG,
                          evidence_class="external") is None, (
        "a seeder is not a strict evidence class — if that changed, say so "
        "here rather than letting this file quietly test a different rule")


def test_a_seed_run_survives_a_refused_row_and_reports_it(tmp_path):
    """The other three unguarded loops. A ConceptNet seed of 20 concepts is
    up to 400 `add()` calls over labels from a crowd-sourced graph that knows
    nothing of this store's term cap — the single most likely place to meet a
    refusal, and it would have 500'd the endpoint mid-seed with rows already
    written and no way to tell which.

    Wikidata is driven here rather than ConceptNet because its object is free
    text straight off `?valueLabel`; the mechanism is the same loop.
    """
    from backend.kb.seeder import seed_wikidata
    from backend.kb.tiered_store import TripleStore

    class _Client:
        """Answers wbsearchentities, then SPARQL: one refusable row, one not."""
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def get(self, url, **kw):
            is_search = "api.php" in url
            payload = ({"search": [{"id": "Q1"}]} if is_search else
                       {"results": {"bindings": [
                           {"propLabel": {"value": "described by"},
                            "valueLabel": {"value": SEED_REFUSED_OBJECT}},
                           {"propLabel": {"value": "instance of"},
                            "valueLabel": {"value": FINE}}]}})
            return _Resp(payload)

    class _Resp:
        status_code = 200                                   # W10b: the recorder reads the status and the body
        def __init__(self, payload): self._p = payload; self.content = json.dumps(payload).encode("utf-8")
        def raise_for_status(self): return None
        def json(self): return self._p

    from backend.memory import actions as recorder     # W10b: the recorder owns the seeder's transport
    real = recorder.httpx.AsyncClient
    recorder.httpx.AsyncClient = _Client
    try:
        store = TripleStore(tmp_path / "kb.db")
        from backend.memory.actions import Trigger
        from backend.memory.lineage import LineageStore
        res = asyncio.run(seed_wikidata(["cardiac"], confirmed=True, store=store,     # W10b: recorded actions
                                        action_store=LineageStore(tmp_path / "kb.db"),
                                        trigger=Trigger(route="/kb/seed/wikidata")))
    finally:
        recorder.httpx.AsyncClient = real

    assert res.seeded == 1, "the whole seed died on one refused row"
    assert store.count() == 1
    assert len(res.refusals) == 1
    assert "too_long" in res.refusals[0]
    assert "cardiac" in res.refusals[0], "the refusal does not say which concept"
    assert res.warnings == [], (
        "a gate refusal was filed as a network warning — they are different "
        "facts and an operator acts on them differently")
    store.close()


def test_a_clean_seed_reports_no_refusals(tmp_path):
    """NON-VACUITY for the pair above: the offline fixture's ten rows pass the
    gate today, so `refusals` is empty when nothing was refused rather than
    always populated."""
    from backend.kb.seeder import seed_offline_fixture
    from backend.kb.tiered_store import TripleStore
    store = TripleStore(tmp_path / "kb.db")
    res = seed_offline_fixture(store, confirmed=True)
    assert res.seeded == 10 and res.refusals == []
    assert store.count() == 10
    store.close()


# ---------------------------------------------------------------------------
# THE CLASS GUARD
# ---------------------------------------------------------------------------

def _add_sites() -> list[tuple[str, int, str, bool, bool]]:
    """(file, line, function, in_loop, handles_refusal) for every store.add."""
    sys.path.insert(0, str(ROOT / "tests"))
    from _tracked import tracked_backend

    def handled(try_node) -> bool:
        for h in try_node.handlers:
            t = h.type
            names = []
            if t is None:
                names.append("BARE")
            elif isinstance(t, ast.Name):
                names.append(t.id)
            elif isinstance(t, ast.Attribute):
                names.append(t.attr)
            elif isinstance(t, ast.Tuple):
                names += [e.id for e in t.elts if isinstance(e, ast.Name)]
            if {"BeliefRejected", "Exception", "BARE"} & set(names):
                return True
        return False

    out = []
    for path in tracked_backend():
        p = Path(path)
        tree = ast.parse(p.read_text(encoding="utf-8"))
        rel = p.relative_to(ROOT).as_posix()
        stack: list = []

        def walk(node, stack=stack):
            kind = None
            if isinstance(node, (ast.For, ast.AsyncFor, ast.While,
                                 ast.ListComp, ast.GeneratorExp)):
                kind = ("loop", node)
            elif isinstance(node, ast.Try):
                kind = ("try", node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = ("func", node)
            if kind:
                stack.append(kind)
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("add", "add_many")):
                kws = {k.arg for k in node.keywords}
                if {"subject", "relation", "object"} <= kws:
                    fn = next((n.name for k, n in reversed(stack) if k == "func"), "?")
                    in_loop = any(k == "loop" for k, _ in stack)
                    guarded = any(k == "try" and handled(n) for k, n in stack)
                    out.append((rel, node.lineno, fn, in_loop, guarded))
            for child in ast.iter_child_nodes(node):
                walk(child)
            if kind:
                stack.pop()

        walk(tree)
    return out


def test_the_population_is_not_empty():
    """NON-VACUITY for the guard below. If the detector stops finding the
    sites it is silently green."""
    sites = _add_sites()
    assert len(sites) >= 6, f"only found {len(sites)} add() sites"
    assert any(g for *_r, g in sites), "no guarded site found — detector broken"
    assert any(loop for *_r, loop, _g in sites), "no looping site found"


def test_every_add_in_a_loop_handles_refusal():
    """THE CLASS. `add()` raises on refusal (Packet K), so an unguarded call
    inside a loop costs every belief after it — and if the loop is inside a
    swallowed cycle, it costs that work forever, silently, on every poll."""
    offenders = [f"{rel}:{ln} in {fn}()"
                 for rel, ln, fn, in_loop, guarded in _add_sites()
                 if in_loop and not guarded]
    assert not offenders, (
        "store.add() inside a loop with no BeliefRejected handler:\n  "
        + "\n  ".join(offenders)
        + "\n\nOne refused belief ends the loop. Catch per belief, count the "
          "refusal, and continue — the pattern is at agentic_loop.py:686.")
