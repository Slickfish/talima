"""Packet E.2 — refractory-gated recurrence and the two-stage consolidation gate.

The premise: biological consolidation is gated by repetition DISTRIBUTED over
time, not raw count. Massed repetition (one debugging afternoon) yields fast,
fragile learning; distributed repetition yields durable learning. Measurement
showed 25 query-scaffolding terms co-firing in one session produced 555 of the
store's 787 triples — on a volatile graph with a count-only trigger, that is
the only output the mechanism can produce.

`fire_count` (intensity, early-phase LTP) keeps its meaning and its threshold
of 7 — I-1. `recurrence_count` (episodes, late-phase LTP) is added beside it.

Every clock here is injected. No wall-clock, no calendar, no timezone logic —
calendar days were explicitly rejected as an artifact rather than an
epistemic unit.
"""
from pathlib import Path

import pytest

from backend.cortex.edges import (
    DEFAULT_REFRACTORY_HOURS,
    SynapticEdge,
    _default_refractory_seconds,
)
from backend.cortex.propagation import propagate
from backend.memory.hippocampus import Hippocampus
from backend.memory.neocortex import (
    CONSOLIDATION_THRESHOLD,
    DEFAULT_RECURRENCE_THRESHOLD,
    Neocortex,
    _default_recurrence_threshold,
)

HOUR = 3600.0
T0 = 1_700_000_000.0          # fixed epoch; never time.time()
WINDOW = 12 * HOUR


def _edge() -> SynapticEdge:
    return SynapticEdge(source="alpha", target="beta", weight=0.6)


# ---------------------------------------------------------------------------
# The debounce itself
# ---------------------------------------------------------------------------

def test_first_firing_opens_an_episode():
    e = _edge()
    assert e.mark_fired(T0, refractory_seconds=WINDOW) is True
    assert e.recurrence_count == 1
    assert e.fire_count == 1


def test_burst_inside_the_window_is_one_episode():
    """Three days of intense work then two weeks idle is ONE episode of
    learning. Intensity still accrues; recurrence does not."""
    e = _edge()
    e.mark_fired(T0, refractory_seconds=WINDOW)
    for i in range(1, 20):
        advanced = e.mark_fired(T0 + i * 60.0, refractory_seconds=WINDOW)
        assert advanced is False

    assert e.recurrence_count == 1          # one episode
    assert e.fire_count == 20               # intensity unaffected


def test_firing_past_the_window_opens_a_new_episode():
    e = _edge()
    e.mark_fired(T0, refractory_seconds=WINDOW)
    assert e.mark_fired(T0 + WINDOW, refractory_seconds=WINDOW) is True
    assert e.recurrence_count == 2
    # exactly at the boundary counts (>=), one second short does not
    assert e.mark_fired(T0 + 2 * WINDOW - 1, refractory_seconds=WINDOW) is False
    assert e.recurrence_count == 2


def test_recurrence_is_monotone_under_every_weight_operation():
    """recurrence_count never decays and never resets — I-2 recoverability."""
    e = _edge()
    for day in range(4):
        e.mark_fired(T0 + day * 24 * HOUR, refractory_seconds=WINDOW)
    assert e.recurrence_count == 4

    e.weaken(0.5)
    e.decay_step(days=365.0)
    e.strengthen(2.0)
    assert e.recurrence_count == 4
    assert e.weight >= 0.0


# ---------------------------------------------------------------------------
# The two-stage gate
# ---------------------------------------------------------------------------

def test_intensity_without_recurrence_does_not_promote(tmp_path: Path):
    """The 555-row failure mode: fires hard in one session, never recurs."""
    h = Hippocampus()
    h.add_edge(SynapticEdge(source="aa", target="bb", weight=0.9,
                            fire_count=50, recurrence_count=1))
    nc = Neocortex(tmp_path / "kb.db")
    assert nc.consolidate_from_hippocampus(h) == 0
    assert nc.count() == 0
    nc.close()


def test_recurrence_without_intensity_does_not_promote(tmp_path: Path):
    h = Hippocampus()
    h.add_edge(SynapticEdge(source="aa", target="bb", weight=0.9,
                            fire_count=2, recurrence_count=10))
    nc = Neocortex(tmp_path / "kb.db")
    assert nc.consolidate_from_hippocampus(h) == 0
    nc.close()


def test_both_stages_promote(tmp_path: Path):
    h = Hippocampus()
    h.add_edge(SynapticEdge(source="aa", target="bb", weight=0.9,
                            fire_count=CONSOLIDATION_THRESHOLD,
                            recurrence_count=DEFAULT_RECURRENCE_THRESHOLD))
    nc = Neocortex(tmp_path / "kb.db")
    assert nc.consolidate_from_hippocampus(h) == 1
    nc.close()


def test_intensity_threshold_unchanged():
    """I-1: stage 1 keeps its value and its meaning."""
    assert CONSOLIDATION_THRESHOLD == 7


# ---------------------------------------------------------------------------
# Conservative degradation — the rollback proof
# ---------------------------------------------------------------------------

def test_zero_refractory_reduces_to_previous_behaviour(tmp_path: Path):
    """With REFRACTORY_SECONDS = 0 every firing opens an episode, so
    recurrence_count == fire_count. Since the intensity threshold (7) exceeds
    the recurrence threshold (3), stage 2 is then implied by stage 1 and the
    gate is exactly the pre-E.2 gate. That makes this change provably
    conservative and gives a clean rollback path."""
    e = SynapticEdge(source="aa", target="bb", weight=0.9)
    for i in range(CONSOLIDATION_THRESHOLD):
        e.mark_fired(T0 + i, refractory_seconds=0.0)

    assert e.recurrence_count == e.fire_count == CONSOLIDATION_THRESHOLD

    h = Hippocampus()
    h.add_edge(e)
    nc = Neocortex(tmp_path / "kb.db")
    assert nc.consolidate_from_hippocampus(h) == 1      # promotes, as before
    nc.close()


# ---------------------------------------------------------------------------
# Config: read at call time, not import
# ---------------------------------------------------------------------------

def test_refractory_window_configurable(monkeypatch):
    monkeypatch.delenv("NEXUS_REFRACTORY_HOURS", raising=False)
    assert _default_refractory_seconds() == DEFAULT_REFRACTORY_HOURS * HOUR

    monkeypatch.setenv("NEXUS_REFRACTORY_HOURS", "0.5")
    assert _default_refractory_seconds() == 0.5 * HOUR

    monkeypatch.setenv("NEXUS_REFRACTORY_HOURS", "not-a-number")
    assert _default_refractory_seconds() == DEFAULT_REFRACTORY_HOURS * HOUR


def test_recurrence_threshold_configurable(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("NEXUS_RECURRENCE_THRESHOLD", raising=False)
    assert _default_recurrence_threshold() == DEFAULT_RECURRENCE_THRESHOLD

    monkeypatch.setenv("NEXUS_RECURRENCE_THRESHOLD", "1")
    assert _default_recurrence_threshold() == 1

    # and it is honoured by the gate
    h = Hippocampus()
    h.add_edge(SynapticEdge(source="aa", target="bb", weight=0.9,
                            fire_count=9, recurrence_count=1))
    nc = Neocortex(tmp_path / "kb.db")
    assert nc.consolidate_from_hippocampus(h) == 1
    nc.close()

    monkeypatch.setenv("NEXUS_RECURRENCE_THRESHOLD", "not-an-int")
    assert _default_recurrence_threshold() == DEFAULT_RECURRENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Integration through the real firing path
# ---------------------------------------------------------------------------

def test_propagation_advances_recurrence_once_per_window():
    """propagate() resolves the window once and injects it, so the quadratic
    edge set does not re-read the environment per firing."""
    h = Hippocampus()
    h.ingest_triple("alpha", "is_a", "beta", weight=0.9)

    propagate(h, ["alpha"], now=T0, refractory_seconds=WINDOW)
    e = h.get_edge("alpha", "beta")
    first = e.recurrence_count
    assert first == 1

    propagate(h, ["alpha"], now=T0 + 60.0, refractory_seconds=WINDOW)
    assert e.recurrence_count == first          # same episode

    propagate(h, ["alpha"], now=T0 + WINDOW + 1, refractory_seconds=WINDOW)
    assert e.recurrence_count == first + 1      # new episode
