"""Packet F.3 — decay by evidence class; tier as an idempotent projection.

The F.1 sentinels (test_prominence_record.py) state the *properties* and were
written before this change so they could constrain it. This file tests the
mechanism that delivers them, and carries the assertions those sentinels
cannot make now that they pass — chiefly a non-vacuous continuity check.

What is pinned here is the ORDERING of the evidence classes, not the
magnitudes. The magnitudes are a provisional calibration (Packet E precedent:
ship configurable, treat defaults as provisional, let the sentinel test the
shape). The ordering is an argument about evidence quality and is the part
that must not silently invert.
"""
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import (
    CLASS_DECAY_PER_DAY, CLASS_ORDER, LOWEST_TIER, SOURCE_CLASS, TIER_ENTRY,
    TIER_RETAIN, TripleStore, band_for, classify, decay_rate_for_source,
)

DAY = 86400.0
T0 = 1_700_000_000.0


def _store(tmp_path: Path) -> TripleStore:
    return TripleStore(tmp_path / "kb.db")


def _add(store, subject, *, tier, weight, idle_days=0.0, source="extraction"):
    tid = store.add(subject=subject, relation="r", object="oo", weight=weight,
                    tier=tier, granularity=Granularity.TOPIC, source=source)
    store.conn.execute("UPDATE triples SET last_fired = ? WHERE id = ?",
                       (T0 - idle_days * DAY, tid))
    store.conn.commit()
    return tid


# ---------------------------------------------------------------------------
# Evidence classes
# ---------------------------------------------------------------------------

def test_class_ordering_is_strict_slowest_to_fastest():
    """The ordering is the claim; the numbers are a calibration.

    `extraction` last is the F.0.4 correction: `source='extraction'` is the
    model extracting triples from its OWN answer (agentic_loop.py:465), with
    no external referent. It earns the least durability, not the most.
    """
    rates = [CLASS_DECAY_PER_DAY[c] for c in CLASS_ORDER]
    assert rates == sorted(rates), f"class ordering inverted: {list(zip(CLASS_ORDER, rates))}"
    assert len(set(rates)) == len(rates), "two classes decay identically"
    # O.2b — `asserted` displaced `external` at the head, and that is an
    # ARGUMENT, not a tuning. F.3 ordered these classes by external referent:
    # a named person stating a fact about their own trial has a stronger
    # referent than Wikidata, which is a third party that can be wrong and
    # that nobody in the room can be asked about. Nothing in the system should
    # out-persist what the operator told it.
    #
    # This line is a ratchet and it broke by design. Updated in the same
    # commit that changed the thing it pins, with the reasoning above, per
    # E.2/F.3: fixtures adapt to invariants — but an invariant ABOUT the
    # ordering is changed deliberately or not at all.
    assert CLASS_ORDER[0] == "asserted" and CLASS_ORDER[-1] == "extraction"
    assert CLASS_ORDER[1] == "external", (
        "human assertion must sit above external, not replace it")
    assert all(r > 0 for r in rates), "a zero rate is immunity by another name"
    assert set(CLASS_ORDER) == set(CLASS_DECAY_PER_DAY)


def test_every_written_source_has_a_class():
    """Every `source` the codebase writes must be classified deliberately."""
    for source in ("extraction", "hippocampus_consolidation", "wikidata",
                   "conceptnet", "offline_fixture", "procedural_promotion",
                   "human_assertion"):
        assert source in SOURCE_CLASS, f"{source!r} would decay as a default"


def test_unregistered_source_decays_fastest_and_warns(caplog):
    """A value we cannot interpret is never read as durable — the same stance
    F.2 took on an unreadable tier."""
    import logging
    with caplog.at_level(logging.WARNING, logger="nexus.kb.store"):
        assert classify("some_new_source_nobody_registered") == "extraction"
    assert decay_rate_for_source("some_new_source_nobody_registered") == \
        max(CLASS_DECAY_PER_DAY.values())


def test_ordering_survives_env_override(monkeypatch):
    """Rates are env-configurable, read at call time (the E.2 pattern)."""
    monkeypatch.setenv("NEXUS_DECAY_EXTERNAL", "0.123")
    assert decay_rate_for_source("wikidata") == pytest.approx(0.123)
    monkeypatch.setenv("NEXUS_DECAY_EXTERNAL", "not-a-number")
    assert decay_rate_for_source("wikidata") == CLASS_DECAY_PER_DAY["external"]


def test_identical_beliefs_differing_only_in_source_diverge(tmp_path):
    """The property the ordering buys: provenance changes how long a belief
    stays prominent, and nothing else about it."""
    store = _store(tmp_path)
    ids = {src: _add(store, f"claim_{src}", tier=1, weight=0.9, idle_days=20,
                     source=src)
           for src in ("wikidata", "offline_fixture", "procedural_promotion",
                       "hippocampus_consolidation", "extraction")}

    cws = {src: store.get_by_id(i).current_weight(T0) for src, i in ids.items()}
    ordered = [cws[s] for s in ("wikidata", "offline_fixture",
                                "procedural_promotion",
                                "hippocampus_consolidation", "extraction")]
    assert ordered == sorted(ordered, reverse=True), (
        f"prominence did not follow evidence class: {cws}")
    # Non-vacuity: the strongest class must still be genuinely prominent, or
    # this is just five different flavours of zero.
    assert cws["wikidata"] > 0.5
    # ...and the record is identical across all five.
    assert len({store.get_by_id(i).weight for i in ids.values()}) == 1
    store.close()


# ---------------------------------------------------------------------------
# Continuity — the non-vacuous version
# ---------------------------------------------------------------------------

def test_decay_is_continuous_at_every_crossing(tmp_path):
    """Every real tier crossing, at non-zero prominence, for every class.

    The F.1 sentinel of the same name is now vacuous for its own fixture
    (its `source="x"` classifies as extraction, which has already decayed to
    zero by the day it crosses), so it would pass even if a discontinuity
    remained. This one asserts prominence is non-zero on both sides of each
    crossing it observes, and requires that crossings actually occurred.
    """
    store = _store(tmp_path)
    observed_crossings = 0

    for source in CLASS_ORDER:
        real_source = next(s for s, c in SOURCE_CLASS.items() if c == source)
        tid = _add(store, f"faller_{source}", tier=0, weight=0.95,
                   source=real_source)
        rate = decay_rate_for_source(real_source)
        step = 0.02 / rate          # ~0.02 prominence per step: fine-grained

        day = 0.0
        while True:
            store.conn.execute("UPDATE triples SET last_fired = ? WHERE id = ?",
                               (T0 - day * DAY, tid))
            store.conn.commit()
            before = store.get_by_id(tid)
            cw_before = before.current_weight(T0)
            if cw_before <= 0.0:
                break

            store.rebalance(T0)

            after = store.get_by_id(tid)
            cw_after = after.current_weight(T0)
            assert cw_after == pytest.approx(cw_before), (
                f"{real_source}: prominence moved {cw_before:.4f} -> "
                f"{cw_after:.4f} at a frozen instant when the tier went "
                f"{before.tier} -> {after.tier}")
            if after.tier != before.tier:
                observed_crossings += 1
                assert cw_after > 0.0        # non-vacuous: a REAL crossing
            day += step

    # 5 classes x 3 crossings (0->1, 1->2, 2->3) if each is reached.
    assert observed_crossings >= 10, (
        f"only {observed_crossings} tier crossings exercised — the test did "
        f"not reach the transitions it claims to check")
    store.close()


# ---------------------------------------------------------------------------
# rebalance() is an idempotent projection
# ---------------------------------------------------------------------------

def test_rebalance_is_idempotent_across_ten_passes(tmp_path):
    """The claim behind "the floor absorbs by construction".

    Identity-based, not a count: `{id: tier}` after each pass, because a pass
    that promoted one belief and demoted another nets to zero under a count
    while the store churns (the E.1 trap).
    """
    store = _store(tmp_path)
    fixtures = [
        (0.97, 0.0,   "hippocampus_consolidation", 0),   # fresh, top
        (0.90, 30.0,  "wikidata",                  1),   # slow class, idle
        (0.65, 26.0,  "extraction",                1),   # the old cliff case
        (0.50, 100.0, "offline_fixture",           2),
        (0.80, 5.0,   "procedural_promotion",      3),   # promotes
        (0.30, 400.0, "extraction",                0),   # long dead, demotes far
        (0.95, 9.0,   "hippocampus_consolidation", 2),
    ]
    ids = [_add(store, f"m{i}", tier=t, weight=w, idle_days=d, source=s)
           for i, (w, d, s, t) in enumerate(fixtures)]

    start = {i: store.get_by_id(i).tier for i in ids}
    store.rebalance(T0)
    settled = {i: store.get_by_id(i).tier for i in ids}

    # Non-vacuity: the first pass must actually have moved something, or
    # "stable across ten passes" is a statement about an inert store.
    assert settled != start, "the first pass moved nothing — nothing tested"

    for n in range(2, 12):
        store.rebalance(T0)
        assert {i: store.get_by_id(i).tier for i in ids} == settled, (
            f"pass {n} moved a belief; rebalance is not a projection")

    # And the second pass reports no movement at all, not merely no net change.
    result = store.rebalance(T0)
    assert result["promoted"] == 0 and result["demoted"] == 0
    store.close()


def test_band_for_is_a_fixed_point(tmp_path):
    """The pure-function core, independent of the store."""
    for cw in [i / 100 for i in range(0, 101)]:
        first = band_for(cw)
        assert band_for(cw, first) == first, f"band_for not idempotent at {cw}"
    assert band_for(0.0) == LOWEST_TIER
    assert band_for(0.0, LOWEST_TIER) == LOWEST_TIER      # the floor absorbs
    assert band_for(1.0) == 0


def test_hysteresis_holds_a_belief_hovering_on_a_boundary():
    """ENTRY > RETAIN, so a belief on a band edge does not flap.

    Without hysteresis a belief oscillating microscopically around a
    threshold would change tier — and therefore its retrieval rank — every
    cycle, which is churn the operator would see and could not explain.
    """
    for tier in (0, 1, 2):
        assert TIER_ENTRY[tier] > TIER_RETAIN[tier]
        edge = (TIER_ENTRY[tier] + TIER_RETAIN[tier]) / 2
        # Sitting in the dead zone: held if already there...
        assert band_for(edge, tier) == tier
        # ...but not *entered* from below on the same value.
        assert band_for(edge, tier + 1) == tier + 1
    # The floor has no dead zone to escape upward through by accident.
    assert TIER_RETAIN[LOWEST_TIER] == 0.0


def test_reinforcement_is_the_only_way_up(tmp_path):
    """A rested belief is lifted by firing, and by nothing else."""
    store = _store(tmp_path)
    tid = _add(store, "dormant", tier=0, weight=0.9, idle_days=400,
               source="wikidata")

    store.rebalance(T0)
    assert store.get_by_id(tid).tier == LOWEST_TIER
    for _ in range(20):                       # time alone never lifts it
        store.rebalance(T0)
    assert store.get_by_id(tid).tier == LOWEST_TIER

    store.fire(tid)                           # a cue surfaces it
    store.rebalance()
    lifted = store.get_by_id(tid)
    assert lifted.tier == 0
    assert lifted.current_weight() == pytest.approx(0.9)
    # I-2 — nothing about the record changed on the way down or back up.
    assert lifted.weight == 0.9 and lifted.source == "wikidata"
    store.close()


# ---------------------------------------------------------------------------
# The 555, under the new semantics (F.0.7 / E.4)
# ---------------------------------------------------------------------------

def test_coactivation_rows_sink_and_rest_without_any_tier_write(tmp_path):
    """E.4's scoped `UPDATE triples SET tier=?` is now unnecessary.

    The 555 co-activation rows sat permanently at tier 0 with weight ~0.971.
    They were only immortal because tier 0 was immune; with the immunity gone
    they decay where they are, from their own evidence class, with no write.
    """
    store = _store(tmp_path)
    tid = _add(store, "clique_term", tier=0, weight=0.971, idle_days=0,
               source="hippocampus_consolidation")

    trajectory = []
    for day in (0, 5, 9, 20, 30, 60):
        store.conn.execute("UPDATE triples SET last_fired = ? WHERE id = ?",
                           (T0 - day * DAY, tid))
        store.conn.commit()
        store.rebalance(T0)
        trajectory.append(store.get_by_id(tid).tier)

    assert trajectory[0] == 0                  # fresh: still prominent
    assert trajectory[-1] == LOWEST_TIER       # forgotten: rested
    assert trajectory == sorted(trajectory), f"sank non-monotonically: {trajectory}"
    # Law 4 / I-2 — every record field intact at the floor.
    final = store.get_by_id(tid)
    assert final.weight == 0.971
    assert final.source == "hippocampus_consolidation"
    assert final.archived is False
    store.close()
