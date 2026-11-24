"""Packet F.1 — the prominence/record separation, pinned before F.3 changes it.

Guards only. **No behaviour changes ship with this file.**

The design F.3 implements is one sentence: *the RECORD is monotone and
absolute; PROMINENCE is lossy.* Stored `weight` is never written downward
anywhere in the codebase (F.0.1: only three writes to `triples` exist, and
none of them lower it), and `current_weight()` is a pure function — so the
split largely exists already. What is broken is that `tier` is a *cached,
stateful* function of prominence, and every defect traces to that cache.

Written before the change rather than after, per §14 discipline and Packet E's
precedent: a sentinel written afterwards validates whatever was built, while
one written first constrains what may be built. `xfail(strict)` throughout, so
an accidental xpass fails the suite and forces the marker to be updated rather
than passing silently.

Each xfail records the measured value that violates it. Those numbers are the
before half of F.3's before/after evidence:

  continuity  cw 0.390 -> 0.000 across one tier step, at a FIXED instant
  floor       tier sequence [2,3,2,3,2,3,2,3,2,3] over ten rebalances
  immunity    tier 0, 3650 days unreinforced, cw 0.971 -> 0.971

Deliberately NOT written: `test_human_asserted_decays_slower_than_inferred`.
No human-assertion class exists — `source='extraction'` is the model
extracting from its *own answer* (`agentic_loop.py:465`), so approximating the
sentinel with it would assert the model's self-report is privileged evidence,
which is the inverse of true. F.4 is blocked on an ingest-path decision.
"""
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import (
    TIER_CONSTANTS_DEPRECATED, TIER_DECAY_PER_DAY, TIER_PROMOTE_WEIGHT,
    TripleStore, decay_rate_for_tier,
)
from backend.memory.lineage import LineageStore

DAY = 86400.0
T0 = 1_700_000_000.0

# Record columns: the fields that must survive any prominence operation
# untouched. `tier` and `last_fired` are deliberately absent — they ARE
# prominence, and moving them is the point of a rebalance.
RECORD_FIELDS = ("subject", "relation", "object", "weight", "source",
                 "created_at")


def _store(tmp_path: Path) -> TripleStore:
    return TripleStore(tmp_path / "kb.db")


def _add(store, subject, *, tier, weight, idle_days=0.0, source="x",
         relation="r", object="oo"):
    tid = store.add(subject=subject, relation=relation, object=object,
                    weight=weight, tier=tier, granularity=Granularity.TOPIC,
                    source=source)
    store.conn.execute("UPDATE triples SET last_fired = ? WHERE id = ?",
                       (T0 - idle_days * DAY, tid))
    store.conn.commit()
    return tid


def record_fingerprint(store) -> dict:
    """{triple_id: (record fields)} — Packet A's fingerprint pattern.

    Keyed by **id**, not by position or count. Per the E.1 trap: a count
    comparison is masked wherever a path both removes and creates, so every
    assertion here is identity-based. A belief that vanished and a different
    belief that appeared would net to zero under a count and is caught here.
    """
    return {t.id: tuple(getattr(t, f) for f in RECORD_FIELDS)
            for t in store.all()}


# ---------------------------------------------------------------------------
# 1. The record is untouchable — PASSES TODAY, written unmarked so it pins
#    F.3 rather than validating it afterwards.
# ---------------------------------------------------------------------------

def test_decay_never_alters_the_record(tmp_path):
    """An aggressive rebalance changes no record field, and no lineage.

    This is the general statement of I-2 (prominence may fall, recoverability
    may not) and the proof obligation for F.3: whatever F.3 does to decay
    rates, tier bands and the floor, this must still pass unchanged.
    """
    store = _store(tmp_path)
    lineage = LineageStore(tmp_path / "kb.db")

    ids = [
        _add(store, "fresh_core",   tier=0, weight=0.97, idle_days=0),
        _add(store, "stale_core",   tier=0, weight=0.97, idle_days=3650),
        _add(store, "active",       tier=1, weight=0.65, idle_days=26),
        _add(store, "warm",         tier=2, weight=0.40, idle_days=200),
        _add(store, "cold",         tier=3, weight=0.90, idle_days=400),
        _add(store, "archived_one", tier=2, weight=0.50, idle_days=10),
    ]
    store.archive("archived_one", "r", "oo")

    rec = lineage.record_turn(
        query="q",
        injected=[{"belief_id": ids[0], "weight_at_injection": 0.97,
                   "tier_at_injection": 0}],
        contradictions=[], model_id="m", response_text="r", prompt_prefix="p")
    lineage.mark_sealed([rec.lineage_id], 7)

    before = record_fingerprint(store)
    before_lineage = (lineage.count(), len(lineage.unsealed()))
    before_injected = lineage.get(rec.lineage_id).injected
    tiers_before = {t.id: t.tier for t in store.all()}

    for _ in range(25):                       # aggressive: far past convergence
        store.rebalance(T0)

    # Non-vacuity (Packet D's lesson): a guard that passes because nothing
    # happened proves nothing. The pass must have actually moved prominence
    # for "and the record was untouched" to carry any weight.
    tiers_after = {t.id: t.tier for t in store.all()}
    assert tiers_after != tiers_before, (
        "no belief changed tier — this run exercised nothing")

    after = record_fingerprint(store)
    assert after == before, "a rebalance pass altered the record layer"
    # Identity-based, not a count: every id present before is present after,
    # and no id appeared that was not there.
    assert set(after) == set(ids)
    assert (lineage.count(), len(lineage.unsealed())) == before_lineage
    # I-5 — the weight sealed at claim time is a different object from the
    # live estimate, and decay must not reach it.
    assert lineage.get(rec.lineage_id).injected == before_injected
    assert lineage.verify_record(rec.lineage_id)["sealed"] is True

    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# 2. Decay is continuous — THE sentinel that makes F.3 correct rather than
#    merely different.
# ---------------------------------------------------------------------------

# FLIPPED BY F.3 — was xfail(strict): "cw 0.390 -> 0.000 across one tier step
# at a fixed instant". Now 0.000 -> 0.000 (drop 0.000) because the decay rate
# no longer depends on the tier.
#
# CAVEAT, recorded rather than papered over: under F.3's class rates this
# fixture's belief (`source="x"` -> unregistered -> extraction, 0.05/day) has
# already decayed to zero by day 26, so it now crosses tiers at zero
# prominence and this test would pass even if a discontinuity remained. It is
# left exactly as written because its value is the historical before/after
# measurement. The non-vacuous version — real tier crossings at non-zero
# prominence, across every evidence class — is
# `test_decay_is_continuous_at_every_crossing` in test_evidence_class_decay.py.
def test_decay_is_continuous_across_a_tier_change(tmp_path):
    """Rebalancing at a fixed instant must not change prominence at all.

    A rebalance re-labels which band a belief sits in. Prominence is a
    property of the belief and of elapsed time — not of the label — so at a
    frozen `now` the label change must be worth exactly zero. Stated as the
    packet does (`no more than rate x dt`), this is the dt=0 case, which is
    the sharpest form available and needs no tolerance argument.

    Why it matters beyond tidiness: because every lower tier decays faster,
    the discontinuity is always downward, and the algebra makes it fatal.
    Demoting out of tier 1 requires `d > (w-0.4)/0.01`; the post-demotion
    value is `w - 0.03d < 1.2 - 2w`, which is <= 0 for any stored weight
    above 0.6. The fixture seeds at 0.7-0.95 and consolidation lands at
    ~0.97 — so essentially every belief in the store free-falls from ~0.4
    prominence to exactly zero the first time it demotes. That is a trapdoor,
    not decay, and it is the upstream cause of the oscillation in the next
    sentinel.
    """
    store = _store(tmp_path)
    tid = _add(store, "faller", tier=1, weight=0.65, idle_days=26)

    for _ in range(10):
        before = store.get_by_id(tid)
        allowed = decay_rate_for_tier(before.tier) * 0.0   # dt = 0
        cw_before = before.current_weight(T0)

        store.rebalance(T0)

        cw_after = store.get_by_id(tid).current_weight(T0)
        assert cw_before - cw_after <= allowed + 1e-9, (
            f"prominence fell {cw_before - cw_after:.3f} at a frozen instant "
            f"(tier {before.tier} -> {store.get_by_id(tid).tier}); "
            f"a tier relabel must cost nothing")
    store.close()


# ---------------------------------------------------------------------------
# 3. The floor absorbs
# ---------------------------------------------------------------------------

# FLIPPED BY F.3 — was xfail(strict): "tier sequence [2,3,2,3,2,3,2,3,2,3]".
# Now [3,3,3,3,3,3,3,3,3,3]. `band_for()` is a pure function of prominence, so
# a forgotten belief has a fixed point to rest at.
def test_forgotten_belief_rests(tmp_path):
    """A belief at zero prominence rests; it does not oscillate.

    The claim F.3 has to earn is that `rebalance()` is an idempotent
    projection — apply it once or ten times and the store is in the same
    state. A function cannot oscillate; a state machine with a trivially-true
    threshold can, and does.

    Asserted on the belief's own tier by id, not on a count of movers: a pass
    that promoted one belief and demoted another nets to zero under a count
    (the E.1 trap) while the store is churning.
    """
    store = _store(tmp_path)
    tid = _add(store, "forgotten", tier=3, weight=0.90, idle_days=400)
    assert store.get_by_id(tid).current_weight(T0) == 0.0    # premise

    store.rebalance(T0)
    settled = store.get_by_id(tid).tier

    tiers = []
    for _ in range(10):
        store.rebalance(T0)
        tiers.append(store.get_by_id(tid).tier)

    assert tiers == [settled] * 10, (
        f"a forgotten belief oscillated instead of resting: {tiers}")
    store.close()


# ---------------------------------------------------------------------------
# 4. ...but the floor is not a grave — PASSES TODAY
# ---------------------------------------------------------------------------

def test_reinforcement_lifts_from_the_floor(tmp_path):
    """Re-entry is by reinforcement, and only by reinforcement.

    Two beliefs identical in every respect and both at the floor; one is
    fired. The fired one must end up strictly more prominent. Comparing the
    two rather than checking the fired one in isolation is deliberate: today
    an unfired belief at tier 3 also "lifts" to tier 2, because of the
    zero-threshold defect the previous sentinel covers. A single-belief
    assertion would therefore pass for entirely the wrong reason — the E.1
    trap in its other form.

    Prominence is ordered by (tier ascending, current_weight descending),
    which is the retriever's own ranking key.
    """
    store = _store(tmp_path)
    fired   = _add(store, "recalled",  tier=3, weight=0.90, idle_days=400)
    unfired = _add(store, "forgotten", tier=3, weight=0.90, idle_days=400)

    store.fire(fired)
    store.rebalance()

    f, u = store.get_by_id(fired), store.get_by_id(unfired)
    prominence = lambda t: (t.tier, -t.current_weight())
    assert prominence(f) < prominence(u), (
        f"reinforcement bought nothing: fired={prominence(f)} "
        f"unfired={prominence(u)}")
    assert f.current_weight() > u.current_weight()
    # Law 4 / I-2 — the unfired one is still there, in full.
    assert u.weight == 0.90 and u.source == "x"
    store.close()


# ---------------------------------------------------------------------------
# 5. No tier is immune to time
# ---------------------------------------------------------------------------

# FLIPPED BY F.3 — was xfail(strict): "tier 0, 3650 days unreinforced,
# cw 0.971 -> 0.971, still tier 0 after ten rebalances". Now cw 0.971 -> 0.000
# and tier 0 -> 3. No tier carries a decay rate any more, so none can be immune.
def test_no_tier_is_immune_to_decay(tmp_path):
    """A belief in the top tier, left unreinforced, loses prominence.

    Phrased behaviourally rather than as `all(rate > 0)` on the constants: a
    constants assertion pins a constant, and a future refactor that moves the
    rate elsewhere would satisfy it while immunity quietly survives. This
    asserts the property the design actually wants, and it survives F.3
    moving the rate from the tier to the evidence class.

    The real entry bar for that immunity is `cw >= 0.70` while in tier 1, not
    the 0.95 the pack recorded: `TIER_PROMOTE_WEIGHT[0] = 0.95` is dead code,
    guarded out by `and t.tier > 0` in `rebalance()`. The fixture seeds tier 1
    at up to 0.95 and consolidation lands at ~0.97, so both go immortal on
    their first rebalance.
    """
    store = _store(tmp_path)
    top = _add(store, "immortal", tier=0, weight=0.971, idle_days=3650)

    t = store.get_by_id(top)
    assert t.tier == 0                                      # premise
    assert t.current_weight(T0) < t.weight, (
        f"ten years unreinforced cost this belief nothing: stored "
        f"{t.weight:.3f}, current {t.current_weight(T0):.3f}")

    for _ in range(10):
        store.rebalance(T0)
    assert store.get_by_id(top).tier > 0, (
        "a belief can never leave the top tier, whatever happens to it")
    store.close()


# ---------------------------------------------------------------------------
# Premise checks — these keep the xfail reasons honest. If a constant moves,
# these fail loudly rather than letting a sentinel xfail for a new reason.
# ---------------------------------------------------------------------------

def test_legacy_tier_constants_are_deprecated_and_unread(tmp_path):
    """F.3 — this test's F.1 form pinned the OLD constants so the xfail
    reasons above could not go stale. Those constants no longer drive
    anything, so it now pins the deprecation instead: the values are retained
    one release under the codebase's P2 alias pattern, and nothing reads them.

    The proof that they are unread is behavioural, not a grep: a belief whose
    tier says "immune" (`TIER_DECAY_PER_DAY[0] == 0.0`) decays anyway.
    """
    assert TIER_CONSTANTS_DEPRECATED is True
    # The old values, kept legible next to the new model.
    assert TIER_DECAY_PER_DAY[0] == 0.0          # was: immunity
    assert TIER_PROMOTE_WEIGHT[3] == 0.0         # was: the oscillation trigger
    assert TIER_PROMOTE_WEIGHT[0] == 0.95        # was: already dead code

    store = _store(tmp_path)
    tid = _add(store, "was_immune", tier=0, weight=0.9, idle_days=30,
               source="wikidata")
    t = store.get_by_id(tid)
    # Tier 0 still claims zero decay in the legacy table...
    assert TIER_DECAY_PER_DAY[t.tier] == 0.0
    # ...and the belief decays regardless, because nothing reads it.
    assert t.current_weight(T0) < t.weight
    store.close()
