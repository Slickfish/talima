"""Packet D — declared loss (Law 8) and honest cycle telemetry.

The cycle previously emitted an identical `snapshot_taken: false,
snapshot_path: ""` whether the interval had not elapsed, the stack was
misconfigured, or an exception had been swallowed. That is undeclared loss:
a skipped seal is dropped governance evidence. These tests pin the reason
vocabulary, the honest counter names, the force override, and the
non-vacuous first block.

The last test is a DRIFT GUARD: it pins the consolidation mechanism this
packet must NOT change.
"""
import asyncio
import logging
from pathlib import Path

import pytest

from backend.cortex.hebbian import HebbianUpdater
from backend.memory.chain import NexusArchiveChain
from backend.memory.fingerprint import CortexFingerprinter
from backend.memory.hippocampus import Hippocampus
from backend.memory.kairos import KairosProcess, _default_snapshot_interval_days
from backend.memory.neocortex import Neocortex
from backend.memory.procedural import ProceduralMemory


def _kairos(tmp_path: Path, *, hippo=None, wired=True, interval=None):
    hippo = hippo if hippo is not None else Hippocampus()
    return KairosProcess(
        hippo,
        Neocortex(tmp_path / "kb.db"),
        HebbianUpdater(),
        ProceduralMemory(),
        fingerprinter=CortexFingerprinter() if wired else None,
        archive_chain=NexusArchiveChain(tmp_path / "archive.db") if wired else None,
        snapshots_dir=tmp_path / "snapshots",
        snapshot_interval_days=interval,
    )


def _populated_hippocampus() -> Hippocampus:
    h = Hippocampus()
    h.ingest_triple("alpha", "is_a", "thing", weight=0.6)
    h.ingest_triple("beta", "is_a", "thing", weight=0.6)
    return h


# ---------- D.1: every skip path declares its reason ----------

def test_skip_reason_not_due_includes_numbers(tmp_path, caplog):
    """Interval not elapsed -> "not_due", logged at DEBUG with the numbers."""
    k = _kairos(tmp_path, hippo=_populated_hippocampus(), interval=30)
    # Seed a block so `latest` is not None, then immediately re-gate.
    asyncio.run(k.maybe_snapshot_and_fingerprint(force=True))

    with caplog.at_level(logging.DEBUG, logger="nexus.kairos"):
        res = asyncio.run(k.run_one_cycle())

    assert res.snapshot_taken is False
    assert res.snapshot_skipped_reason == "not_due"
    msg = "\n".join(r.getMessage() for r in caplog.records)
    assert "not_due" in msg
    assert "days elapsed" in msg and "configured" in msg   # the numbers


def test_skip_reason_unwired_logs_warning(tmp_path, caplog):
    k = _kairos(tmp_path, wired=False)
    with caplog.at_level(logging.WARNING, logger="nexus.kairos"):
        res = asyncio.run(k.run_one_cycle())

    assert res.snapshot_skipped_reason == "unwired"
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_skip_reason_graph_empty(tmp_path):
    """Empty chain + empty graph -> no block, reason graph_empty (D.4)."""
    k = _kairos(tmp_path)                       # fresh Hippocampus == empty
    res = asyncio.run(k.run_one_cycle())

    assert res.snapshot_taken is False
    assert res.snapshot_skipped_reason == "graph_empty"
    assert k.archive_chain.count() == 0


def test_skip_reason_error_is_reachable(tmp_path, caplog):
    """C10 — I-6 names "error" as a skip reason, so something must emit it.

    Before Packet E.4 nothing did: a throw inside the snapshot path
    propagated uncaught out of `run_one_cycle`, so the cycle produced no
    result at all rather than a declared skip. An invariant naming a state
    the code cannot reach reads as satisfied on inspection and fails only
    when the untested path fires. Law 8: the loss must be declared.
    """
    k = _kairos(tmp_path, hippo=_populated_hippocampus())

    async def _boom(force):
        raise RuntimeError("fingerprint exploded")

    k._snapshot_and_seal = _boom

    with caplog.at_level(logging.WARNING, logger="nexus.kairos"):
        res = asyncio.run(k.run_one_cycle())        # must NOT raise

    assert res.snapshot_taken is False
    assert res.snapshot_skipped_reason == "error"
    assert k.last_snapshot_skip_reason == "error"
    msg = "\n".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in msg and "fingerprint exploded" in msg
    assert k.archive_chain.count() == 0             # nothing half-sealed


def test_dedup_scan_failure_is_visible_in_the_result(tmp_path, caplog):
    """A scan that threw and returned [] must not look like "found nothing"."""
    class _Boom:
        async def scan(self):
            raise RuntimeError("scan exploded")

    class _Services:
        deduplicator = _Boom()
        last_dedup_candidates: list = []

    k = _kairos(tmp_path, hippo=_populated_hippocampus())
    k.bind_services(_Services())

    with caplog.at_level(logging.WARNING, logger="nexus.kairos"):
        res = asyncio.run(k.run_one_cycle())

    assert res.dedup_candidates == 0
    assert res.dedup_scan_failed is True             # distinguishable
    assert "RuntimeError" in res.dedup_error
    assert any("dedup scan failed" in r.message for r in caplog.records)


# ---------- D.2: honest counter names, deprecated aliases ----------

def test_counters_renamed_with_deprecated_aliases(tmp_path):
    k = _kairos(tmp_path, hippo=_populated_hippocampus())
    res = asyncio.run(k.run_one_cycle())

    assert res.edges_promoted == res.consolidated
    assert res.hebbian_edges_visited == res.decayed
    assert res.counters_deprecated is True
    # rebalanced is already accurate and must keep its name
    assert isinstance(res.rebalanced, dict)


def test_decay_pass_docstring_records_pre_prune_census():
    doc = HebbianUpdater.decay_pass.__doc__ or ""
    assert "census" in doc
    assert "prune" in doc


# ---------- D.3: force honoured ----------

def test_force_snapshot_overrides_interval(tmp_path):
    k = _kairos(tmp_path, hippo=_populated_hippocampus(), interval=30)
    first = asyncio.run(k.maybe_snapshot_and_fingerprint(force=True))
    assert first is not None

    # Not due — but forced anyway.
    assert asyncio.run(k.run_one_cycle()).snapshot_taken is False
    forced = asyncio.run(k.run_one_cycle(force_snapshot=True))
    assert forced.snapshot_taken is True
    assert forced.snapshot_skipped_reason == ""
    assert k.archive_chain.count() == 2


def test_background_path_still_respects_interval(tmp_path):
    """run_one_cycle() default must NOT force — cadence is unchanged."""
    k = _kairos(tmp_path, hippo=_populated_hippocampus(), interval=30)
    asyncio.run(k.maybe_snapshot_and_fingerprint(force=True))
    before = k.archive_chain.count()

    res = asyncio.run(k.run_one_cycle())          # default force_snapshot=False

    assert res.snapshot_taken is False
    assert res.snapshot_skipped_reason == "not_due"
    assert k.archive_chain.count() == before


def test_endpoint_forces_by_default():
    """POST /kairos/cycle is the manual control; it forces unless told not to."""
    import inspect
    from backend.server import create_app
    app = create_app()
    route = next(r for r in app.routes
                 if getattr(r, "path", "") == "/kairos/cycle")
    sig = inspect.signature(route.endpoint)
    assert sig.parameters["force_snapshot"].default is True


# ---------- D.4: a meaningful first block ----------

def test_first_block_records_a_real_topology(tmp_path):
    """Empty chain + NON-empty graph -> block appended, fingerprint non-zero."""
    k = _kairos(tmp_path, hippo=_populated_hippocampus())
    res = asyncio.run(k.run_one_cycle())

    assert res.snapshot_taken is True
    assert res.snapshot_skipped_reason == ""
    block = k.archive_chain.latest()
    assert block.fingerprint["n_nodes"] > 0
    assert any(v != 0 for v in block.fingerprint["signature"])


def test_force_seals_empty_graph_as_explicit_override(tmp_path, caplog):
    k = _kairos(tmp_path)                        # empty graph, empty chain
    with caplog.at_level(logging.INFO, logger="nexus.kairos"):
        res = asyncio.run(k.run_one_cycle(force_snapshot=True))

    assert res.snapshot_taken is True            # explicit act is honoured
    assert k.archive_chain.latest().fingerprint["n_nodes"] == 0
    assert any("forced" in r.message for r in caplog.records)


# ---------- D.5: configurable interval ----------

def test_interval_respects_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_SNAPSHOT_INTERVAL_DAYS", "0.5")
    assert _default_snapshot_interval_days() == 0.5
    assert _kairos(tmp_path).snapshot_interval_days == 0.5

    monkeypatch.delenv("NEXUS_SNAPSHOT_INTERVAL_DAYS")
    assert _default_snapshot_interval_days() == 30.0

    monkeypatch.setenv("NEXUS_SNAPSHOT_INTERVAL_DAYS", "not-a-number")
    assert _default_snapshot_interval_days() == 30.0     # falls back, warns


# ---------- DRIFT GUARD — the mechanism this packet must not change ----------

def test_consolidation_still_reads_synaptic_edge_fire_count():
    """Packet D changes only self-reporting.

    Consolidation must keep iterating in-memory SynapticEdge.fire_count
    (co-activation strength) at threshold 7 — NOT TripleStore.fire()'s
    retrieval-frequency counter, which is salience wearing the same name.
    Swapping them would preserve the label and silently change the meaning:
    the F-02 error in a new location. Fails loudly if a later session
    "helpfully" substitutes one for the other.
    """
    import inspect
    from backend.memory import neocortex as neo

    assert neo.CONSOLIDATION_THRESHOLD == 7

    # Packet AA moved the edge loop into `_consolidation_loop` so the batch
    # wrapper stays readable; the mechanism under guard lives there now, and
    # this reads BOTH so a later split cannot move it out of view again.
    src = (inspect.getsource(neo.Neocortex.consolidate_from_hippocampus)
           + inspect.getsource(neo.Neocortex._consolidation_loop))
    assert "hippocampus.edges()" in src        # reads the in-memory graph
    assert "edge.fire_count < threshold" in src
    assert "store.fire" not in src             # never the retrieval counter

    # And the store's counter remains a separate, untouched mechanism.
    from backend.kb.tiered_store import TripleStore
    assert "fire_count = fire_count + 1" in inspect.getsource(TripleStore.fire)
