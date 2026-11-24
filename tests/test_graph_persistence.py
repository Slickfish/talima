"""Packet E.3 — persistence, demotion-not-deletion, lazy wall-clock decay.

The payoff test is `test_recurrence_survives_restart_and_reaches_the_gate`:
before E.3, `recurrence_count` reset to 0 on every process start, so the
two-stage gate could never be satisfied by genuine cross-session
reinforcement. That is the inversion this packet exists to fix.
"""
from pathlib import Path

import pytest

from backend.cortex.edges import ActivationType, SynapticEdge
from backend.cortex.hebbian import HebbianUpdater
from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore
from backend.memory.graph_store import HebbianGraphStore, cited_pairs
from backend.memory.hippocampus import Hippocampus
from backend.memory.neocortex import CONSOLIDATION_THRESHOLD, Neocortex

HOUR = 3600.0
DAY = 24 * HOUR
T0 = 1_700_000_000.0
WINDOW = 12 * HOUR


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

def test_edge_round_trip_preserves_every_field(tmp_path: Path):
    h = Hippocampus()
    e = SynapticEdge(source="alpha", target="beta", weight=0.42,
                     activation_type=ActivationType.INHIBITORY,
                     last_fired=T0, fire_count=9,
                     last_counted_at=T0, recurrence_count=4, demoted=True)
    h.add_edge(e)

    gs = HebbianGraphStore(tmp_path / "kb.db")
    assert gs.save(h) == 1

    restored = Hippocampus()
    assert HebbianGraphStore(tmp_path / "kb.db").load_into(restored) == 1
    r = restored.get_edge("alpha", "beta")

    assert (r.weight, r.fire_count, r.recurrence_count) == (0.42, 9, 4)
    assert r.last_counted_at == T0
    assert r.last_fired == T0                       # persisted as last_seen
    assert r.demoted is True
    assert r.activation_type is ActivationType.INHIBITORY
    gs.close()


def test_save_is_idempotent_and_monotone_on_counters(tmp_path: Path):
    """Re-saving never lowers a counter — I-2 recoverability under repeated
    writes from two writers."""
    gs = HebbianGraphStore(tmp_path / "kb.db")
    h = Hippocampus()
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.5,
                            fire_count=9, recurrence_count=5,
                            last_counted_at=T0, last_fired=T0))
    gs.save(h)

    # A stale in-memory view writes back lower counters; they must not regress.
    stale = Hippocampus()
    stale.add_edge(SynapticEdge(source="a", target="b", weight=0.5,
                                fire_count=1, recurrence_count=1))
    gs.save(stale)

    restored = Hippocampus()
    gs.load_into(restored)
    e = restored.get_edge("a", "b")
    assert e.recurrence_count == 5
    assert e.fire_count == 9
    gs.close()


def test_migration_is_rerunnable(tmp_path: Path):
    db = tmp_path / "kb.db"
    HebbianGraphStore(db).close()
    gs = HebbianGraphStore(db)          # second open must not raise
    assert gs.count() == 0
    gs.close()


# ---------------------------------------------------------------------------
# The payoff: recurrence across process lifetimes
# ---------------------------------------------------------------------------

def test_recurrence_survives_restart_and_reaches_the_gate(tmp_path: Path):
    """Three refractory-separated episodes, each in its OWN process, now
    consolidate. Before E.3 recurrence_count reset on every start, so this
    was unreachable — the accumulation path was inverted."""
    db = tmp_path / "kb.db"

    for day in range(3):                       # three separate "processes"
        h = Hippocampus()
        HebbianGraphStore(db).load_into(h)
        h.ingest_triple("interval_training", "improves", "vo2_max", weight=0.9)
        edge = h.get_edge("interval_training", "vo2_max")
        # a burst of intra-session firing: intensity climbs, recurrence +1
        for i in range(4):
            edge.mark_fired(T0 + day * DAY + i * 60.0, refractory_seconds=WINDOW)
        HebbianGraphStore(db).save(h)

    final = Hippocampus()
    HebbianGraphStore(db).load_into(final)
    e = final.get_edge("interval_training", "vo2_max")

    assert e.recurrence_count == 3             # one per episode, not per firing
    assert e.fire_count >= CONSOLIDATION_THRESHOLD

    nc = Neocortex(tmp_path / "neo.db")
    assert nc.consolidate_from_hippocampus(final) == 1
    nc.close()


def test_single_session_burst_still_does_not_consolidate(tmp_path: Path):
    """The 555-row failure mode, now persisted: firing hard all day is one
    episode however many processes it spans."""
    db = tmp_path / "kb.db"
    h = Hippocampus()
    h.ingest_triple("sparse", "co_occurs", "about", weight=0.9)
    e = h.get_edge("sparse", "about")
    for i in range(50):
        e.mark_fired(T0 + i * 60.0, refractory_seconds=WINDOW)
    HebbianGraphStore(db).save(h)

    restored = Hippocampus()
    HebbianGraphStore(db).load_into(restored)
    assert restored.get_edge("sparse", "about").recurrence_count == 1

    nc = Neocortex(tmp_path / "neo.db")
    assert nc.consolidate_from_hippocampus(restored) == 0
    nc.close()


# ---------------------------------------------------------------------------
# Lazy wall-clock decay — no sweep, no third mechanism
# ---------------------------------------------------------------------------

def test_decay_is_lazy_and_wall_clock(tmp_path: Path):
    """Downtime is forgotten at READ time via the existing current_weight()
    path. Loading does not sweep: the stored weight is untouched, and the
    elapsed-time discount appears only when the weight is read."""
    db = tmp_path / "kb.db"
    h = Hippocampus()
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.9,
                            decay_per_day=0.05, last_fired=T0))
    HebbianGraphStore(db).save(h)

    restored = Hippocampus()
    HebbianGraphStore(db).load_into(restored)
    e = restored.get_edge("a", "b")

    assert e.weight == 0.9                                  # no startup sweep
    assert e.current_weight(T0) == pytest.approx(0.9)
    assert e.current_weight(T0 + 10 * DAY) == pytest.approx(0.4)   # 10 days idle
    assert e.weight == 0.9                                  # read did not mutate


# ---------------------------------------------------------------------------
# I-2 / I-4 under persistence
# ---------------------------------------------------------------------------

def test_demotion_persists_and_is_recoverable(tmp_path: Path):
    db = tmp_path / "kb.db"
    h = Hippocampus()
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.001))
    HebbianUpdater().decay_pass(h, days=1.0)
    assert h.get_edge("a", "b").demoted is True
    HebbianGraphStore(db).save(h)

    restored = Hippocampus()
    HebbianGraphStore(db).load_into(restored)
    e = restored.get_edge("a", "b")
    assert e.demoted is True
    assert e.transmit(1.0) == 0.0                # no prominence
    # ...but fully recoverable: firing restores it
    e.mark_fired(T0, refractory_seconds=WINDOW)
    assert e.demoted is False


def test_cited_pairs_resolves_only_sealed_records(tmp_path: Path):
    store = TripleStore(tmp_path / "kb.db")
    bid = store.add(subject="alpha", relation="is_a", object="beta",
                    weight=0.6, tier=2, granularity=Granularity.TOPIC,
                    source="extraction")
    from backend.memory.lineage import LineageStore
    lineage = LineageStore(tmp_path / "kb.db")
    rec = lineage.record_turn(
        query="q", injected=[{"belief_id": bid, "weight_at_injection": 0.6,
                              "tier_at_injection": 2}],
        contradictions=[], model_id="m", response_text="r", prompt_prefix="p")

    assert cited_pairs(lineage, store) == set()      # unsealed confers nothing
    lineage.mark_sealed([rec.lineage_id], 7)
    assert cited_pairs(lineage, store) == {("alpha", "beta")}
    store.close(); lineage.close()


def test_cited_edge_is_exempt_from_demotion(tmp_path: Path):
    """I-4 is stronger than I-2: a cited association is not merely undeleted,
    it is not demoted at all — it stays load-bearing for the claim it fed."""
    h = Hippocampus()
    h.add_edge(SynapticEdge(source="alpha", target="beta", weight=0.001))
    h.add_edge(SynapticEdge(source="gamma", target="delta", weight=0.001))

    upd = HebbianUpdater(protected_pairs=lambda: {("alpha", "beta")})
    upd.decay_pass(h, days=1.0)

    assert h.get_edge("alpha", "beta").demoted is False    # cited: exempt
    assert h.get_edge("gamma", "delta").demoted is True    # uncited: demoted
