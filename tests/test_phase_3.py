"""Phase 3 tests — required coverage from CLAUDE.md:

  - Hebbian strengthens co-fired pairs
  - LTD decays unfired
  - prune below 0.05
  - new edges form
  - propagate returns activation map

Plus arch-doc invariants verified here:
  - epistemic states are renderable for prompt injection (Law II)
  - lateral inhibition reduces non-winning column activation (Law IV)
  - inhibitory edges deliver negative signal
  - refractory period blocks immediate retransmission
  - decay_pass weakens edges and re-prunes
"""
from __future__ import annotations

import time

import pytest

from backend.cortex.columns import Column, ColumnState, infer_column
from backend.cortex.edges import PRUNE_THRESHOLD, ActivationType, SynapticEdge
from backend.cortex.hebbian import HebbianUpdater
from backend.cortex.nodes import CorticalNode, EpistemicState
from backend.cortex.propagation import propagate
from backend.memory.hippocampus import Hippocampus


@pytest.fixture
def chain_hippo():
    """Three-node chain a -> b -> c with mid-strength edges."""
    h = Hippocampus()
    h.upsert_node("a"); h.upsert_node("b"); h.upsert_node("c")
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.7))
    h.add_edge(SynapticEdge(source="b", target="c", weight=0.6))
    return h


# ---------- propagation returns activation ----------

def test_propagation_returns_activation_map(chain_hippo):
    am = propagate(chain_hippo, ["a"])
    assert "a" in am.activations
    # b should fire from a (weight 0.7 over threshold 0.2)
    assert am.activations.get("b", 0.0) > 0.0
    # c should fire from b
    assert am.activations.get("c", 0.0) > 0.0
    # density is mean of activations
    assert 0.0 < am.density <= 1.0
    # peak ordering
    peaks = am.peak_activations
    assert peaks[0][1] >= peaks[-1][1]


def test_propagation_respects_max_hops():
    h = Hippocampus()
    nodes = list("abcdefg")
    for n in nodes:
        h.upsert_node(n)
    for s, t in zip(nodes, nodes[1:]):
        h.add_edge(SynapticEdge(source=s, target=t, weight=0.95))
    am = propagate(h, ["a"], max_hops=2)
    # Reachable: a (hop 0), b (hop 1), c (hop 2). d should NOT fire.
    assert "a" in am.activations and "b" in am.activations and "c" in am.activations
    assert "d" not in am.activations


def test_propagation_empty_seed():
    h = Hippocampus()
    h.upsert_node("a")
    am = propagate(h, [])
    assert am.activations == {}


# ---------- Hebbian: strengthen co-fired pairs ----------

def test_hebbian_strengthens_cofired_pairs(chain_hippo):
    upd = HebbianUpdater()
    edge_ab = chain_hippo.get_edge("a", "b")
    w0 = edge_ab.weight
    am = propagate(chain_hippo, ["a"])
    upd.update(chain_hippo, am)
    assert edge_ab.weight > w0, "co-fired edge should strengthen"


# ---------- Hebbian: LTD decays unfired ----------

def test_ltd_decays_unfired_edges():
    h = Hippocampus()
    for n in "abxy":
        h.upsert_node(n)
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.7))
    unfired = SynapticEdge(source="x", target="y", weight=0.6)
    h.add_edge(unfired)
    am = propagate(h, ["a"])
    upd = HebbianUpdater()
    upd.update(h, am)
    assert unfired.weight < 0.6, "unfired edge should weaken via LTD"


# ---------- Hebbian: prune below 0.05 ----------

def test_prune_below_threshold():
    h = Hippocampus()
    for n in "ab":
        h.upsert_node(n)
    weak = SynapticEdge(source="a", target="b", weight=0.04)
    h.add_edge(weak)
    assert h.graph.has_edge("a", "b")
    pruned = h.prune_below(PRUNE_THRESHOLD)
    assert pruned == 1
    # E.3/I-2: prune reduces PROMINENCE, not recoverability — the edge
    # stays in the graph (n_edges stable) but stops transmitting.
    assert h.graph.has_edge("a", "b")
    assert h.get_edge("a", "b").demoted is True
    assert h.get_edge("a", "b").transmit(1.0) == 0.0


def test_prune_threshold_constant():
    assert PRUNE_THRESHOLD == 0.05


def test_hebbian_prunes_at_end_of_update():
    h = Hippocampus()
    for n in "abc":
        h.upsert_node(n)
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.06))   # near floor
    h.add_edge(SynapticEdge(source="b", target="c", weight=0.5))     # safe
    upd = HebbianUpdater(ltd_factor=0.5)  # aggressive LTD
    am = propagate(h, ["b"])  # only b/c fire — a-b edge weakens
    upd.update(h, am)
    # a-b should now be demoted (0.06 * 0.5 = 0.03 < 0.05) — E.3/I-2
    assert h.get_edge("a", "b").demoted is True


# ---------- Hebbian: new edges form ----------

def test_new_edges_form_between_cofired_nodes():
    h = Hippocampus()
    h.upsert_node("p"); h.upsert_node("q")
    h.add_edge(SynapticEdge(source="p", target="q", weight=0.9))
    # Add a third orphan node r and force it to "fire" by manually injecting
    # into the activation map — simulates two unrelated concepts co-activating.
    am = propagate(h, ["p"])
    am.activations["r"] = 0.8
    am.fired_sequence.append(("r", 1))
    h.upsert_node("r")
    upd = HebbianUpdater()
    before = h.graph.number_of_edges()
    result = upd.update(h, am)
    after = h.graph.number_of_edges()
    assert result.formed >= 1, "expected new edge to form between r and others"
    assert after > before


# ---------- Epistemic states inject renderable strings ----------

def test_epistemic_state_renders_for_prompt():
    n = CorticalNode(id="diabetes", epistemic_state=EpistemicState.PARTIAL)
    rendered = n.epistemic_state.render(confidence=n.confidence)
    assert "partial" in rendered.lower()


def test_known_state_includes_confidence():
    rendered = EpistemicState.KNOWN.render(confidence=0.91)
    assert "0.91" in rendered


def test_all_epistemic_states_have_renderings():
    for s in EpistemicState:
        out = s.render(confidence=0.5)
        assert isinstance(out, str) and len(out) > 0


# ---------- Lateral inhibition (Law IV) ----------

def test_lateral_inhibition_suppresses_non_winners():
    cs = ColumnState()
    cs.add(Column.CODING, 0.9)
    cs.add(Column.HEALTH, 0.1)
    factors = cs.lateral_inhibit()
    assert factors[Column.CODING] == 1.0
    assert factors[Column.HEALTH] < 1.0
    # ASSOCIATION is exempt from suppression
    assert factors[Column.ASSOCIATION] == 1.0


def test_infer_column_from_keywords():
    assert infer_column("python", "framework") is Column.CODING
    assert infer_column("deadlift", "rep") is Column.FITNESS
    assert infer_column("blood", "pressure") is Column.HEALTH
    assert infer_column("philosophy") is Column.GENERAL


# ---------- Inhibitory + refractory edge dynamics ----------

def test_inhibitory_edge_returns_negative_signal():
    e = SynapticEdge(source="a", target="b", weight=0.6,
                     activation_type=ActivationType.INHIBITORY)
    assert e.transmit(1.0) < 0


def test_modulatory_edge_returns_half_strength():
    exc = SynapticEdge(source="a", target="b", weight=0.6)
    mod = SynapticEdge(source="a", target="b", weight=0.6,
                       activation_type=ActivationType.MODULATORY)
    assert abs(mod.transmit(1.0) - exc.transmit(1.0) * 0.5) < 1e-9


def test_refractory_blocks_immediate_retransmission():
    e = SynapticEdge(source="a", target="b", weight=0.6, refractory_seconds=10)
    e.mark_fired()
    assert e.transmit(1.0) == 0.0  # in refractory window


# ---------- Decay pass (KAIROS preview) ----------

def test_decay_pass_weakens_and_prunes():
    h = Hippocampus()
    for n in "ab":
        h.upsert_node(n)
    e = SynapticEdge(source="a", target="b", weight=0.1, decay_per_day=0.05)
    h.add_edge(e)
    upd = HebbianUpdater()
    upd.decay_pass(h, days=2.0)  # 0.1 - 2*0.05 = 0.0 → demoted (E.3/I-2)
    assert h.get_edge("a", "b").demoted is True


# ---------- Triple ingestion creates nodes + edge ----------

def test_ingest_triple_creates_graph_structure():
    h = Hippocampus()
    h.ingest_triple("python", "is_a", "language", weight=0.8)
    assert "python" in h.nodes and "language" in h.nodes
    e = h.get_edge("python", "language")
    assert e is not None and e.weight == 0.8


# ---------- Stage 2 cortex assembly integration ----------

def test_assemble_context_runs_propagation_when_hippocampus_present(tmp_path):
    from backend.kb.granularity import Granularity
    from backend.kb.retriever import TripleRetriever
    from backend.kb.tiered_store import TripleStore
    from backend.loop.agentic_loop import assemble_context

    store = TripleStore(tmp_path / "kb.db")
    store.add(subject="python", relation="is_a", object="language",
              weight=0.9, tier=1, granularity=Granularity.TOPIC, source="t")
    retriever = TripleRetriever(store)
    h = Hippocampus()

    ctx = assemble_context("tell me about python", retriever, h)
    assert ctx.hits, "expected KB hits"
    assert ctx.activation is not None
    assert ctx.activation.activations
    assert "CORTEX ACTIVATION" in ctx.block
    assert "KB CONTEXT" in ctx.block
    store.close()
