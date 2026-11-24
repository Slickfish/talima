"""Packet AA — one commit per consolidation pass, not per row.

THE NUMBER THIS PACKET IS ABOUT
    OQ-4, measured on the operator's store: the steady-state pass is 13,183
    no-op MAX upserts, each with its own commit under `journal_mode=delete` /
    `synchronous=FULL` — 13,183 fsyncs, 15.43s of held write lock, 97% of
    the cycle. The refusals were 3%. The spike's Q3(a) added the second half:
    commit-per-row makes a half-done pass PUBLIC — a reader watched it grow
    through seventeen partial states.

WHAT DID NOT CHANGE, AND THE SENTINELS PROVE IT
    `add()` is still the single chokepoint and the gate still runs per
    belief — the batch changes WHEN work becomes durable, never what is
    admitted. A batched commit that skipped the gate would look like a
    performance win and be catastrophic.

FAILURE SEMANTICS: ROLL BACK THE WHOLE PASS
    rebalance() decided this question in F.2 and the reasoning transfers
    verbatim: "a half-applied pass is a store state no policy produced".
    What makes rollback CHEAP here is OQ-4's own re-offer design — promoted
    edges stay in the hippocampus until session-clear, so a rolled-back pass
    loses nothing permanently; every edge is re-offered on the next poll.
    Refusals do not trigger rollback — a refused belief is caught per belief
    (Packet Z) and writes nothing; only an infrastructure failure rolls back.

REDUCIBLE
    NEXUS_CONSOLIDATION_BATCH=off restores per-row commits exactly —
    including the old failure semantics, where rows before a mid-pass crash
    stay committed. Both directions are pinned, because a rollback switch
    that does not restore the old behaviour is worse than none (V.1).
"""
from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from backend.cortex.edges import SynapticEdge
from backend.memory.hippocampus import Hippocampus
from backend.memory.neocortex import Neocortex

TOO_LONG = ("TAK-441 prolonged the QT interval as a potential indicator of "
            "cardiac risk")


def _hippo(n_good: int, refused_positions: set[int] = frozenset(),
           prefix: str = "a") -> Hippocampus:
    """Edges in insertion order: refusable targets at the named positions.
    `prefix` keeps two fixtures' pairs DISTINCT — the store upserts on
    (s, r, o, source), so a second fixture with the same names would write
    zero new rows and a count assertion would measure nothing."""
    h = Hippocampus()
    for i in range(n_good + len(refused_positions)):
        src = f"{prefix} src{i:03d}"
        tgt = TOO_LONG if i in refused_positions else f"{prefix} target {i:03d}"
        h.upsert_node(src), h.upsert_node(tgt)
        h.add_edge(SynapticEdge(source=src, target=tgt, weight=0.9,
                                fire_count=9, recurrence_count=5))
    return h


def _count_commits(store, monkeypatch) -> list:
    calls = []
    orig = store.conn.commit
    monkeypatch.setattr(store.conn, "commit",
                        lambda: (calls.append(1), orig())[1])
    return calls


def test_the_gate_still_runs_per_belief_inside_the_batch(tmp_path):
    """THE CATASTROPHIC-LOOKS-LIKE-A-WIN CASE. A refused belief mid-batch is
    refused, counted, and the REST still land."""
    neo = Neocortex(tmp_path / "kb.db")
    promoted = neo.consolidate_from_hippocampus(_hippo(5, {2}))
    assert promoted == 5, "good beliefs were lost to the batch"
    assert neo.store.count() == 5
    assert neo.last_refusals["count"] == 1, "the refusal was not counted"
    assert "too_many_tokens" in neo.last_refusals["first_reason"]
    # NON-VACUITY: more than one belief batched, at least one refused.
    assert promoted > 1 and neo.last_refusals["count"] >= 1


def test_a_clean_batched_pass_commits_exactly_once(tmp_path, monkeypatch):
    """The claim of the packet, counted rather than described: N beliefs,
    one commit."""
    neo = Neocortex(tmp_path / "kb.db")
    commits = _count_commits(neo.store, monkeypatch)
    promoted = neo.consolidate_from_hippocampus(_hippo(8))
    assert promoted == 8, "non-vacuity: the batch must contain many beliefs"
    assert len(commits) == 1, (
        f"{len(commits)} commits for one pass — the per-row pattern is back")


def test_an_infrastructure_failure_rolls_back_the_whole_pass(
        tmp_path, monkeypatch, caplog):
    """Chosen semantics, proven — and the partial commit proven ABSENT.
    Rollback is cheap by design: the hippocampus re-offers every edge next
    poll (OQ-4), so nothing is lost, only deferred."""
    import logging
    neo = Neocortex(tmp_path / "kb.db")
    orig_add = neo.store.add
    calls = {"n": 0}

    def poisoned(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 4:
            raise sqlite3.OperationalError("database is locked")
        return orig_add(*a, **kw)

    monkeypatch.setattr(neo.store, "add", poisoned)
    with caplog.at_level(logging.WARNING, logger="nexus.neocortex"):
        with pytest.raises(sqlite3.OperationalError):
            neo.consolidate_from_hippocampus(_hippo(8))
    assert neo.store.count() == 0, (
        "rows from before the failure were committed — the pass tore, which "
        "is the state F.2 calls 'a store state no policy produced'")
    # DECLARED (I-6): a discarded pass must say so and say how much.
    assert any("rolled back" in r.getMessage() and "3" in r.getMessage()
               for r in caplog.records), (
        "the rollback was silent — a cycle that discarded its promotions "
        "must not look like one that made them")


def test_batch_off_restores_per_row_semantics_exactly(tmp_path, monkeypatch):
    """REDUCIBILITY, both halves. Off = commit per row AND the old failure
    shape: rows before a mid-pass crash STAY. A switch that restored the
    commits but not the semantics would not be the old behaviour (V.1)."""
    monkeypatch.setenv("NEXUS_CONSOLIDATION_BATCH", "off")
    neo = Neocortex(tmp_path / "kb.db")
    commits = _count_commits(neo.store, monkeypatch)
    promoted = neo.consolidate_from_hippocampus(_hippo(5))
    assert promoted == 5
    assert len(commits) == 5, (
        f"batch off gave {len(commits)} commits for 5 rows — not the "
        f"pre-AA behaviour")

    orig_add = neo.store.add
    calls = {"n": 0}

    def poisoned(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise sqlite3.OperationalError("database is locked")
        return orig_add(*a, **kw)

    monkeypatch.setattr(neo.store, "add", poisoned)
    with pytest.raises(sqlite3.OperationalError):
        neo.consolidate_from_hippocampus(_hippo(5, prefix="b"))
    assert neo.store.count() == 5 + 2, (
        "with batch off, the two rows written before the failure must stay "
        "committed — that IS the old semantics, restored exactly")


def test_rebalance_is_still_idempotent_after_a_batched_pass(tmp_path):
    """Pinned elsewhere against per-row writes; confirmed here against the
    batch rather than assumed."""
    neo = Neocortex(tmp_path / "kb.db")
    assert neo.consolidate_from_hippocampus(_hippo(6)) == 6
    now = time.time()
    first = neo.store.rebalance(now=now)
    second = neo.store.rebalance(now=now)
    assert second["promoted"] == 0 and second["demoted"] == 0, (
        f"rebalance moved rows on the second pass after a batched "
        f"consolidation: {first} then {second}")


def test_the_refusal_count_is_none_on_a_rolled_back_cycle(tmp_path, monkeypatch):
    """§4 — the ledger across a rollback. Refusals counted DURING a pass
    whose writes were then discarded must not surface as that cycle's
    numbers: the abort path reports None (not measured), the same rule
    edges_promoted follows. The refusals were real judgements, but the pass
    they describe did not happen; next poll re-offers and re-counts."""
    monkeypatch.setenv("NEXUS_API_TOKEN", "one-commit-token")
    from backend.server import create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    # refused edge FIRST, so the ledger has counted before the poison fires
    h = svc.hippocampus
    for i, tgt in enumerate([TOO_LONG, "target a", "target b", "target c"]):
        src = f"s{i}"
        h.upsert_node(src), h.upsert_node(tgt)
        h.add_edge(SynapticEdge(source=src, target=tgt, weight=0.9,
                                fire_count=9, recurrence_count=5))

    orig_add = svc.neocortex.store.add
    calls = {"n": 0}

    def poisoned(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise sqlite3.OperationalError("database is locked")
        return orig_add(*a, **kw)

    monkeypatch.setattr(svc.neocortex.store, "add", poisoned)
    svc.kairos.session_ended()
    r = asyncio.run(svc.kairos.run_one_cycle(force_snapshot=True))

    assert r.skipped is True and "OperationalError" in r.cycle_error
    assert r.edges_promoted is None
    assert r.consolidation_refused is None, (
        f"a rolled-back pass reported consolidation_refused="
        f"{r.consolidation_refused} — numbers from work that was discarded")
    assert svc.neocortex.store.count() == 0, "the rollback did not hold"
    # NON-VACUITY: the ledger genuinely counted before the poison fired.
    assert svc.neocortex.last_refusals["count"] == 1
