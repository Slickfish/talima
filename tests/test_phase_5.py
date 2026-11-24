"""Phase 5 tests — required coverage from CLAUDE.md:

  - edges with fire_count >= 7 promote to neocortex
  - behavioural patterns with >= 5 instances promote to procedural
  - LangGraph checkpoint round-trips state
  - KAIROS marked idle when no session
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.cortex.edges import SynapticEdge
from backend.cortex.hebbian import HebbianUpdater
from backend.kb.granularity import Granularity
from backend.loop.state import NexusLoopState
from backend.memory.checkpoint import NexusCheckpointer
from backend.memory.hippocampus import Hippocampus
from backend.memory.kairos import KairosProcess
from backend.memory.neocortex import (
    CONSOLIDATION_THRESHOLD,
    NEOCORTEX_SOURCE,
    Neocortex,
)
from backend.memory.procedural import (
    PROCEDURAL_PROMOTION_THRESHOLD,
    ProceduralMemory,
)


# =================== consolidation: fire_count >= 7 ===================

def test_consolidation_threshold_constant():
    assert CONSOLIDATION_THRESHOLD == 7


def test_edges_at_or_above_threshold_promote(tmp_path: Path):
    h = Hippocampus()
    nc = Neocortex(tmp_path / "kb.db")
    e_hot = SynapticEdge(source="alpha", target="beta", weight=0.8, fire_count=7,
                          recurrence_count=3)
    e_cold = SynapticEdge(source="gamma", target="delta", weight=0.8, fire_count=3,
                           recurrence_count=3)
    h.add_edge(e_hot)
    h.add_edge(e_cold)

    promoted = nc.consolidate_from_hippocampus(h)
    assert promoted == 1

    rows = nc.all()
    assert any(t.subject == "alpha" and t.object == "beta" for t in rows)
    assert not any(t.subject == "gamma" for t in rows)
    nc.close()


def test_below_threshold_skips_promotion(tmp_path: Path):
    h = Hippocampus()
    nc = Neocortex(tmp_path / "kb.db")
    h.add_edge(SynapticEdge(source="x", target="y", weight=0.9, fire_count=6,
                             recurrence_count=3))
    promoted = nc.consolidate_from_hippocampus(h)
    assert promoted == 0
    assert nc.count() == 0
    nc.close()


def test_consolidated_triple_is_tagged_with_source_and_granularity(tmp_path: Path):
    h = Hippocampus()
    nc = Neocortex(tmp_path / "kb.db")
    h.add_edge(SynapticEdge(source="aa", target="bb", weight=0.5, fire_count=8,
                             recurrence_count=3))
    nc.consolidate_from_hippocampus(h)
    [row] = nc.all()
    assert row.source == NEOCORTEX_SOURCE
    assert row.granularity == Granularity.SESSION.value
    nc.close()


def test_consolidated_triples_persist_across_restart(tmp_path: Path):
    db = tmp_path / "kb.db"
    h = Hippocampus()
    h.add_edge(SynapticEdge(source="aa", target="bb", weight=0.7, fire_count=10,
                             recurrence_count=3))

    nc = Neocortex(db)
    nc.consolidate_from_hippocampus(h)
    nc.close()

    nc2 = Neocortex(db)
    assert nc2.count() == 1
    nc2.close()


# =================== procedural: >= 5 instances ===================

def test_procedural_threshold_constant():
    assert PROCEDURAL_PROMOTION_THRESHOLD == 5


def test_procedural_promotes_at_threshold(tmp_path: Path):
    pm = ProceduralMemory()
    nc = Neocortex(tmp_path / "kb.db")
    for _ in range(5):
        pm.observe("how do I deadlift safely")
    assert pm.candidates(), "expected pattern to be candidate at 5 observations"
    promoted = pm.promote(nc)
    assert promoted == 1
    rows = nc.all()
    assert rows, "expected procedural triples to be written"
    assert all(r.source == "procedural_promotion" for r in rows)
    nc.close()


def test_procedural_skips_below_threshold(tmp_path: Path):
    pm = ProceduralMemory()
    nc = Neocortex(tmp_path / "kb.db")
    for _ in range(4):
        pm.observe("what is matplotlib")
    assert not pm.candidates()
    assert pm.promote(nc) == 0
    nc.close()


def test_procedural_does_not_re_promote_already_promoted(tmp_path: Path):
    pm = ProceduralMemory()
    nc = Neocortex(tmp_path / "kb.db")
    for _ in range(5):
        pm.observe("explain transformers")
    pm.promote(nc)
    # Further observations of the same pattern should not re-promote.
    for _ in range(3):
        pm.observe("explain transformers")
    assert pm.promote(nc) == 0
    nc.close()


def test_procedural_ignores_empty_queries():
    pm = ProceduralMemory()
    pm.observe("")
    pm.observe("the and is or")  # all stopwords
    assert pm.counts == {}


# =================== KAIROS idle gating ===================

def test_kairos_starts_idle(tmp_path: Path):
    h = Hippocampus()
    nc = Neocortex(tmp_path / "kb.db")
    k = KairosProcess(h, nc, HebbianUpdater(), ProceduralMemory())
    assert k.is_idle is True
    assert k.session_active is False
    nc.close()


def test_kairos_skips_cycle_when_session_active(tmp_path: Path):
    h = Hippocampus()
    nc = Neocortex(tmp_path / "kb.db")
    h.add_edge(SynapticEdge(source="aa", target="bb", weight=0.5, fire_count=8,
                             recurrence_count=3))
    k = KairosProcess(h, nc, HebbianUpdater(), ProceduralMemory())
    k.session_started()
    res = asyncio.run(k.run_one_cycle())
    assert res.skipped is True
    assert res.skipped_reason == "session_active"
    assert nc.count() == 0   # no consolidation happened
    nc.close()


def test_kairos_runs_consolidation_when_idle(tmp_path: Path):
    h = Hippocampus()
    nc = Neocortex(tmp_path / "kb.db")
    h.add_edge(SynapticEdge(source="aa", target="bb", weight=0.5, fire_count=8,
                             recurrence_count=3))
    k = KairosProcess(h, nc, HebbianUpdater(), ProceduralMemory())
    res = asyncio.run(k.run_one_cycle())
    assert res.skipped is False
    assert res.consolidated == 1
    assert nc.count() == 1
    nc.close()


def test_kairos_session_lifecycle_toggles_idle_flag(tmp_path: Path):
    nc = Neocortex(tmp_path / "kb.db")
    k = KairosProcess(Hippocampus(), nc, HebbianUpdater(), ProceduralMemory())
    assert k.is_idle
    k.session_started()
    assert not k.is_idle
    k.session_ended()
    assert k.is_idle
    nc.close()


def test_kairos_runs_procedural_promotion_during_cycle(tmp_path: Path):
    nc = Neocortex(tmp_path / "kb.db")
    pm = ProceduralMemory()
    for _ in range(5):
        pm.observe("rebuild the cortex")
    k = KairosProcess(Hippocampus(), nc, HebbianUpdater(), pm)
    res = asyncio.run(k.run_one_cycle())
    assert res.promoted_patterns == 1
    nc.close()


# =================== LangGraph checkpoint round-trip ===================

def test_checkpoint_round_trip_preserves_state():
    s = NexusLoopState(
        messages=({"role": "user", "content": "hi"},
                  {"role": "assistant", "content": "hello"}),
        graph_context=(("python", "is_a", "language"),),
        session_memory=(("aa", "bb", 0.5),),
        predicted_nodes=("python",),
        prediction_error=0.42,
        active_model="deepseek-r1:14b-qwen-distill-q4_K_M",
        turn_count=3,
        token_count=128,
        compaction_state="snipped",
        last_extraction=(("python", "is_a", "language"),),
        transition_reason="turn_complete",
        # BD-3 removed `kairos_pending`, a field never written or read.
    )
    cp = NexusCheckpointer()
    cp.put("session-A", s)
    loaded = cp.get("session-A")
    assert loaded is not None
    assert loaded == s          # frozen-dataclass equality (tuples + scalars)
    assert loaded.messages == s.messages
    assert loaded.prediction_error == 0.42


def test_checkpoint_returns_none_for_unknown_thread():
    cp = NexusCheckpointer()
    assert cp.get("never-created") is None


def test_checkpoint_overwrite_replaces_state():
    cp = NexusCheckpointer()
    s0 = NexusLoopState(turn_count=1)
    s1 = NexusLoopState(turn_count=99, transition_reason="advanced")
    cp.put("session-X", s0)
    cp.put("session-X", s1)
    assert cp.get("session-X").turn_count == 99


def test_checkpoint_isolates_threads():
    cp = NexusCheckpointer()
    cp.put("alice", NexusLoopState(turn_count=1))
    cp.put("bob",   NexusLoopState(turn_count=99))
    assert cp.get("alice").turn_count == 1
    assert cp.get("bob").turn_count == 99
