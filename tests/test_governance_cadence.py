"""Packet S.2b — the governance seal is not the snapshot cadence.

THE PROBLEM S.2 LEFT
    `SNAPSHOT_INTERVAL_DAYS` defaults to 30 and the seal rode it. So a
    recalibration could move what a belief is worth and sit unsealed for a
    month — which undercuts the one claim S.2 exists to make: that no weight
    moved outside a limit, PROVABLY. An unsealed record proves nothing.

    `kairos.py` had recorded this as "a known design question, not a magic
    number" since Phase 9. S.2 is what made it load-bearing.

TWO PURPOSES, NOW TWO MECHANISMS
    snapshot    periodic structural report of the hippocampus. Monthly, and
                unchanged — that was always the operator's intent.
    governance  digest seal over lineage and recalibration records. Every idle
                cycle that has something to seal, so the worst-case unsealed
                window is one KAIROS poll rather than thirty days.

WHAT A GOVERNANCE BLOCK CARRIES
    The seal keys and its chain link. No snapshot, no fingerprint. That is
    sufficient because neither verifier needs more: `verify_sealed_lineage`
    resolves `sealed_block -> get_block(index)` and checks a record hash
    against `record_hashes`, and `verify_chain()` recomputes stored fields and
    has never re-read a snapshot file. Both asserted below rather than argued.

THE TWO SNAPSHOT DEFECTS FROM TEMPORAL_PROBE
    Filename collision at one-second granularity, and `verify_chain()` never
    re-hashing the snapshot file. **S.2b touches neither**, and the reason is
    structural rather than lucky: the governance path writes no snapshot file
    and names none, so it cannot collide and has nothing to re-hash. Recorded
    with a test that pins the reason, per the packet's instruction to fix only
    if the change makes them reachable.
"""
from __future__ import annotations

import dataclasses

import asyncio
import json
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.recalibration import Envelope, apply_outcome
from backend.memory.chain import ArchiveBlock, NexusArchiveChain
from backend.memory.kairos import (
    DEFAULT_GOVERNANCE_SEAL_MODE,
    GOVERNANCE_SEAL_MODES,
    governance_seal_mode,
)
from backend.memory.lineage import verify_sealed_lineage
from backend.server import create_services


def _services(tmp_path):
    return create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots",
    )


def _a_recalibration(services) -> int:
    """Move one weight, on record. Returns the belief id."""
    bid = services.store.add(subject="QTc", relation="stands_for",
                             object="corrected QT", weight=0.55, tier=2,
                             granularity=Granularity.TOPIC, source="extraction")
    env = Envelope(version="v1", max_delta=0.05, movable_classes=("extraction",))
    services.recalibration.record_envelope(env, actor="operator",
                                           reason="sentinel policy")
    apply_outcome(services.store, services.recalibration, belief_id=bid,
                  delta=-0.05, envelope=env, actor="operator", outcome_ref="o1")
    return bid


# ---------------------------------------------------------------------------
# The knob
# ---------------------------------------------------------------------------

def test_the_default_mode_is_prompt(monkeypatch):
    monkeypatch.delenv("NEXUS_GOVERNANCE_SEAL", raising=False)
    assert DEFAULT_GOVERNANCE_SEAL_MODE == "prompt"
    assert governance_seal_mode() == "prompt"


@pytest.mark.parametrize("mode", GOVERNANCE_SEAL_MODES)
def test_every_declared_mode_is_accepted(mode, monkeypatch):
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", mode.upper())
    assert governance_seal_mode() == mode


def test_an_unrecognised_mode_warns_and_falls_back(monkeypatch, caplog):
    import logging
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "promt")
    with caplog.at_level(logging.WARNING, logger="nexus.kairos"):
        assert governance_seal_mode() == DEFAULT_GOVERNANCE_SEAL_MODE
    assert any("promt" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# THE SENTINEL THE PACKET ASKED FOR
# ---------------------------------------------------------------------------

def test_a_recalibration_is_sealed_on_the_governance_cadence_not_the_snapshot(tmp_path, monkeypatch, recalibration_on):
    """The whole packet, in one test.

    NON-VACUITY IS THE FIRST HALF: the record has to be genuinely unsealed
    before the cycle runs, and the snapshot has to be genuinely not due. Skip
    either and this passes on a store where the seal was already there, or
    where a snapshot happened to fire and did the work.
    """
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "prompt")
    services = _services(tmp_path)
    bid = _a_recalibration(services)

    # --- non-vacuity, both halves -----------------------------------------
    pending = services.recalibration.unsealed()
    assert pending, "nothing was awaiting a seal; the fixture proves nothing"
    events = services.recalibration.events_for(bid)
    assert events and events[0].sealed_block is None, (
        "the recalibration was already sealed before the cycle ran")
    # The snapshot is NOT due — thirty days have not passed and the graph is
    # empty, so anything that gets sealed was sealed by the governance path.
    assert services.kairos._snapshot_gate(False) != "", (
        "the snapshot is due; this test cannot tell the two cadences apart")

    result = asyncio.run(services.kairos.run_one_cycle())

    assert result.governance_block_index is not None, (
        f"no governance block was appended: {result.governance_skipped_reason}")
    assert result.snapshot_taken is False, "a snapshot fired; wrong cadence"
    assert result.governance_sealed_records == len(pending)

    sealed = services.recalibration.events_for(bid)[0]
    assert sealed.sealed_block == result.governance_block_index
    assert services.recalibration.unsealed() == []


def test_the_snapshot_cadence_alone_would_have_left_it_unsealed(tmp_path, monkeypatch, recalibration_on):
    """THE COUNTERFACTUAL, and the measurement behind the packet's claim.

    Under `snapshot` mode — pre-S.2b behaviour exactly — the same store runs
    the same cycles and the recalibration is still unsealed at the end. This
    is the before half of the before/after; without it "prompt seals it" is a
    statement with nothing to compare against.
    """
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "snapshot")
    services = _services(tmp_path)
    bid = _a_recalibration(services)

    for _ in range(12):                       # an hour of 5-minute polls
        asyncio.run(services.kairos.run_one_cycle())

    assert services.recalibration.events_for(bid)[0].sealed_block is None, (
        "the rollback mode sealed it anyway — it is not reproducing pre-S.2b "
        "behaviour")
    assert services.recalibration.unsealed(), "nothing left pending"
    assert services.kairos.last_governance_skip_reason == "mode_snapshot"

    # ...and the snapshot path still seals it when it eventually fires, which
    # is what made the window thirty days rather than infinite.
    services.hippocampus.ingest_triple("QTc", "stands_for", "corrected QT")
    block = asyncio.run(
        services.kairos.maybe_snapshot_and_fingerprint(force=True))
    assert block is not None
    assert services.recalibration.events_for(bid)[0].sealed_block == block.index


def test_off_mode_never_seals_and_says_so(tmp_path, monkeypatch, recalibration_on):
    """I-6. A seal that quietly never runs is worse than a slow one."""
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "off")
    services = _services(tmp_path)
    _a_recalibration(services)
    result = asyncio.run(services.kairos.run_one_cycle())
    assert result.governance_block_index is None
    assert result.governance_skipped_reason == "mode_off"


def test_a_cycle_with_nothing_pending_mints_no_block(tmp_path, monkeypatch):
    """Otherwise every idle poll appends a block covering nothing, and the
    chain fills with rows that assert the absence of events."""
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "prompt")
    services = _services(tmp_path)
    result = asyncio.run(services.kairos.run_one_cycle())
    assert result.governance_block_index is None
    assert result.governance_skipped_reason == "nothing_pending"
    assert services.archive_chain.count() == 0


def test_the_interval_floor_delays_but_does_not_lose(tmp_path, monkeypatch, recalibration_on):
    """A deployment that cares about block volume can slow the cadence. The
    record must still be sealed once the floor elapses, not dropped."""
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "prompt")
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL_INTERVAL_MINUTES", "60")
    services = _services(tmp_path)
    bid = _a_recalibration(services)
    # A seal happened "recently", so the floor is in force.
    import time as _t
    services.kairos.last_governance_seal_ts = _t.time()

    result = asyncio.run(services.kairos.run_one_cycle())
    assert result.governance_block_index is None
    assert result.governance_skipped_reason == "not_due"

    services.kairos.last_governance_seal_ts = _t.time() - 3601
    result = asyncio.run(services.kairos.run_one_cycle())
    assert result.governance_block_index is not None, "the record was dropped"
    assert services.recalibration.events_for(bid)[0].sealed_block is not None


# ---------------------------------------------------------------------------
# What a governance block carries, and that both verifiers still hold
# ---------------------------------------------------------------------------

def test_a_governance_block_carries_seals_and_no_snapshot(tmp_path, monkeypatch, recalibration_on):
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "prompt")
    services = _services(tmp_path)
    _a_recalibration(services)
    block = asyncio.run(services.kairos.maybe_seal_governance(force=True))

    assert block is not None
    assert block.kind == "governance"
    assert block.snapshot_path == ""
    assert "recalibration_seal" in block.fingerprint
    # No fingerprint fields — this block makes no claim about the graph.
    assert "signature" not in block.fingerprint
    assert "n_nodes" not in block.fingerprint


def test_the_chain_still_verifies_across_mixed_block_kinds(tmp_path, monkeypatch, recalibration_on):
    """`verify_chain()` recomputes stored fields and has never re-read a
    snapshot file, so a block with no snapshot verifies as any other does.
    Asserted across an interleaving, because a single governance block at the
    end would not exercise the links either side of one.
    """
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "prompt")
    services = _services(tmp_path)
    _a_recalibration(services)
    services.hippocampus.ingest_triple("QTc", "stands_for", "corrected QT")

    asyncio.run(services.kairos.maybe_seal_governance(force=True))
    asyncio.run(services.kairos.maybe_snapshot_and_fingerprint(force=True))
    services.store.add(subject="x", relation="is_a", object="y", weight=0.5,
                       tier=2, granularity=Granularity.TOPIC, source="wikidata")
    apply_outcome(services.store, services.recalibration,
                  belief_id=services.store.all()[0].id, delta=-0.01,
                  envelope=Envelope(version="v2", max_delta=0.05,
                                    movable_classes=("extraction",)),
                  actor="operator")
    asyncio.run(services.kairos.maybe_seal_governance(force=True))

    kinds = [b.kind for b in services.archive_chain.all_blocks()]
    assert kinds == ["governance", "snapshot", "governance"], kinds
    result = services.archive_chain.verify_chain()
    assert result.valid, f"chain broke across mixed kinds: {result.to_dict()}"


def test_governance_blocks_do_not_pollute_the_missing_snapshot_signal(tmp_path, monkeypatch, recalibration_on):
    """F-16's `missing_snapshots` names snapshot blocks whose file was absent
    at seal time — a defect signal. A governance block has no snapshot BY
    DESIGN, so counting it there would flood the list with intended absences
    and destroy the one thing it is for."""
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "prompt")
    services = _services(tmp_path)
    _a_recalibration(services)
    block = asyncio.run(services.kairos.maybe_seal_governance(force=True))

    assert block.snapshot_missing is True, (
        "the fixture no longer reproduces the condition being excluded")
    assert services.archive_chain.verify_chain().missing_snapshots == (), (
        "a governance block was reported as a missing snapshot")


def test_lineage_verification_holds_through_a_governance_block(
        tmp_path, monkeypatch):
    """The seal has to be usable, not merely present. `verify_sealed_lineage`
    resolves `sealed_block -> get_block(index)` and checks the record hash
    against the block's `record_hashes` — it never asked what kind of block
    that was, and this asserts it still does not need to."""
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "prompt")
    services = _services(tmp_path)
    bid = services.store.add(subject="QTc", relation="stands_for",
                             object="corrected QT", weight=0.55, tier=2,
                             granularity=Granularity.TOPIC, source="extraction")
    rec = services.lineage.record_turn(
        query="what is QTc?",
        injected=[{"belief_id": bid, "weight_at_injection": 0.55,
                   "tier_at_injection": 2, "cited": True}],
        contradictions=[], model_id="test", response_text="QTc.",
        prompt_prefix="STABLE")

    block = asyncio.run(services.kairos.maybe_seal_governance(force=True))
    assert block is not None and block.kind == "governance"

    report = verify_sealed_lineage(services.lineage, services.archive_chain,
                                   lineage_id=rec.lineage_id)
    # All three layers, separately — collapsing them into one boolean is what
    # `verify_sealed_lineage`'s own docstring warns against, and `seal` is the
    # layer this packet moved.
    assert report["verdict"] == "intact", report
    assert report["layers"]["seal"]["ok"] == 1, report["layers"]["seal"]
    assert report["layers"]["seal"]["unsealed"] == 0, (
        f"the record is still awaiting a seal: {report['layers']['seal']}")
    assert report["layers"]["seal"]["missing_block"] == [], (
        "the governance block could not be resolved from sealed_block")
    assert report["layers"]["chain"]["ok"] is True, report["layers"]["chain"]


# ---------------------------------------------------------------------------
# The historical chain is not invalidated by the new field
# ---------------------------------------------------------------------------

def test_a_pre_s2b_block_recomputes_to_its_stored_hash(tmp_path):
    """`kind` is sealed into the hash only when it is not the default —
    deliberately the same rule F-16 used for `snapshot_missing`. A new field
    that silently invalidated the historical chain would be the one change a
    tamper-evident log must never make to itself.
    """
    chain = NexusArchiveChain(tmp_path / "archive.db")
    block = chain.append({"n_nodes": 3}, "")
    # W8 — a block appended now is layout 2, whose header leaves a default kind
    # out by the same rule; a block written before S.2b is layout 1, so the
    # stored hash this pins is the layout-1 hash of the same block.
    assert "kind" not in block.header()
    stored = dataclasses.replace(block, layout=1, roots=None, fingerprint_digest=None).compute_hash()

    # A block read back with no `kind` at all — an archive.db written before
    # S.2b, after the additive migration filled the column with its default.
    legacy = ArchiveBlock(
        index=block.index, timestamp=block.timestamp,
        fingerprint=block.fingerprint, snapshot_path=block.snapshot_path,
        snapshot_hash=block.snapshot_hash, previous_hash=block.previous_hash,
        snapshot_missing=block.snapshot_missing)
    assert legacy.compute_hash() == stored
    assert chain.verify_chain().valid
    chain.close()


def test_a_governance_block_hashes_differently_from_a_snapshot_block(tmp_path):
    """The other direction: `kind` is genuinely covered when it is set. If it
    were omitted from the hash entirely, a governance block could be relabelled
    a snapshot without breaking the chain."""
    chain = NexusArchiveChain(tmp_path / "archive.db")
    gov = chain.append({"lineage_seal": {"count": 0}}, "", kind="governance")
    relabelled = ArchiveBlock(
        index=gov.index, timestamp=gov.timestamp, fingerprint=gov.fingerprint,
        snapshot_path=gov.snapshot_path, snapshot_hash=gov.snapshot_hash,
        previous_hash=gov.previous_hash,
        snapshot_missing=gov.snapshot_missing, kind="snapshot")
    assert relabelled.compute_hash() != gov.block_hash
    chain.close()


# ---------------------------------------------------------------------------
# Drift compares fingerprints, and governance blocks have none
# ---------------------------------------------------------------------------

def test_drift_compares_against_the_last_snapshot_not_the_last_block(tmp_path, monkeypatch, recalibration_on):
    """MEASURED CONSEQUENCE, not a hypothetical.

    `assess_drift` loaded `load_recent_blocks(n=1)`. Once governance blocks
    interleave, that returns whichever block was appended last — and a
    governance payload has no fingerprint fields, while
    `CortexFingerprint.from_dict` is key-tolerant. The comparison would have
    run against zeros and alerted on every cycle after a recalibration.
    """
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "prompt")
    services = _services(tmp_path)
    services.hippocampus.ingest_triple("a", "is_a", "b")
    snap = asyncio.run(services.kairos.maybe_snapshot_and_fingerprint(force=True))
    assert snap is not None

    _a_recalibration(services)
    gov = asyncio.run(services.kairos.maybe_seal_governance(force=True))
    assert gov is not None and gov.index > snap.index

    latest = services.archive_chain.load_recent_blocks(n=1)[0]
    assert latest.kind == "governance", "the fixture did not interleave"
    assert services.archive_chain.latest_snapshot_block().index == snap.index

    alerts_before = len(services.pending_alerts)
    asyncio.run(services.kairos.maybe_snapshot_and_fingerprint(force=True))
    drift_alerts = [a for a in services.pending_alerts[alerts_before:]
                    if a.get("type") == "cortex_drift"]
    assert drift_alerts == [], (
        f"drift was assessed against a governance block: {drift_alerts}")


# ---------------------------------------------------------------------------
# TEMPORAL_PROBE's two open defects — recorded, and shown untouched
# ---------------------------------------------------------------------------

def test_the_governance_path_writes_no_snapshot_file(tmp_path, monkeypatch, recalibration_on):
    """Why S.2b does not touch either open snapshot defect.

    F1, filename collision at one-second granularity: the governance path
    names no file and writes none, so it cannot collide with anything.
    F2, `verify_chain()` never re-hashing the snapshot: unchanged, and a
    governance block has no snapshot to re-hash either way.

    Both remain open and are recorded here rather than fixed, per the packet:
    fix only if the change makes them reachable, otherwise record.
    """
    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "prompt")
    services = _services(tmp_path)
    _a_recalibration(services)
    snaps_before = sorted((tmp_path / "snapshots").glob("*")) \
        if (tmp_path / "snapshots").is_dir() else []

    block = asyncio.run(services.kairos.maybe_seal_governance(force=True))
    snaps_after = sorted((tmp_path / "snapshots").glob("*")) \
        if (tmp_path / "snapshots").is_dir() else []

    assert block is not None
    assert snaps_after == snaps_before, (
        f"the governance path wrote a snapshot file: "
        f"{set(snaps_after) - set(snaps_before)}")
