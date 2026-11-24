"""Packet AM — co-fired, not co-traversed.

WHAT WAS MEASURED, on the operator's real store
    One turn's `hebbian_update` ran 6m52s. `strengthened: 987,319,778` on a
    44,547-edge graph — which solves E(E-1)/2 at E = 44,437, the edge count
    to 0.2%: the pairing loop iterated the SQUARE of the fired_sequence,
    and fired_sequence holds one entry per DELIVERY (propagation.py appends
    per transmit), so it is quadratic in edges once the graph is densely
    reachable. Per-op cost is flat (~417ns measured at four sizes), so the
    turn path contained a component at E²/2 x 417ns: ~35 minutes at 100k
    edges. Re-measured before this fix on a dense sandbox graph: one turn
    over 11,970 edges made 3,589,970 pairing probes, FORMED 1,097,065 new
    edges (a 92x graph explosion of pair debris), and strengthened only
    6,486 distinct pre-existing edges — the payload number overstated the
    distinct edges by ~550x (I-7: declared `edges_modified` until W1, which
    aligned the declaration to the keys emitted).

THE FIX, and what it must preserve
    Pairing draws from `activation.top_k(K)` — unique nodes, selected by
    THIS TURN'S activation, the same surface every other consumer of the
    map uses — with K configurable and provisional. LTD is untouched: its
    fired_set still comes from the full traversal, because sparing
    traversed edges from LTD is today's behaviour and narrowing it is AJ's
    question, not this packet's. `full` mode restores the pre-AM loop
    exactly, duplicate multiplication, operation-counting payload and all.

THE SENTINELS, per the packet
    - growth ORDER across three sizes, not a fixed threshold — a fixed
      number passes on a graph too small to expose the problem, which is
      how this survived three misattributions;
    - the counter equals DISTINCT edges modified;
    - the selection input is activation and nothing else — same
      activations, different delivery multiplicity => same pairs;
    - reducibility both ways, the old path byte-exact;
    - non-vacuity: the fixture is dense enough that the unbounded path
      pairs orders of magnitude more than the bounded one.
"""
from __future__ import annotations

import random

import pytest

from backend.cortex.edges import SynapticEdge
from backend.cortex.hebbian import HebbianUpdater
from backend.cortex.propagation import ActivationMap
from backend.memory.hippocampus import Hippocampus


class CountingHippo(Hippocampus):
    """Counts get_edge probes — one per pairing-loop iteration."""

    def __init__(self):
        super().__init__()
        self.get_edge_calls = 0

    def get_edge(self, s, t):
        self.get_edge_calls += 1
        return super().get_edge(s, t)


def dense(n_nodes: int, seed: int = 3) -> tuple[CountingHippo, list[str]]:
    rng = random.Random(seed)
    h = CountingHippo()
    nodes = [f"c{i}" for i in range(n_nodes)]
    for i in range(n_nodes):
        for d in (1, 2, 3):
            h.add_edge(SynapticEdge(source=nodes[i],
                                    target=nodes[(i + d) % n_nodes],
                                    weight=rng.uniform(0.3, 0.8)))
    return h, nodes


def traversal_map(nodes, n_events: int, dup: int = 1) -> ActivationMap:
    """A fired_sequence with `dup` delivery events per node — the shape
    propagation produces on a dense graph (one append per transmit)."""
    uniq = nodes[:n_events // dup]
    seq = [(n, 1) for n in uniq for _ in range(dup)]
    return ActivationMap(
        activations={n: 1.0 - i / (2 * len(uniq)) for i, n in enumerate(uniq)},
        fired_sequence=seq)


# ---------------------------------------------------------------------------
# Growth order
# ---------------------------------------------------------------------------

def test_pairing_work_grows_sub_quadratically():
    """THE SENTINEL. Assert the ORDER: quadratic doubling of events must not
    quadruple the pairing probes. Under top-k pairing the work is constant
    in E; the assertion allows sub-quadratic slack but fails the old loop
    by orders of magnitude."""
    probes = []
    for n_events in (1000, 2000, 4000):
        h, nodes = dense(4000)
        am = traversal_map(nodes, n_events, dup=2)
        h.get_edge_calls = 0
        HebbianUpdater().update(h, am)
        probes.append(h.get_edge_calls)
    assert probes[0] > 0
    # Quadratic would give ratios ~4 and ~16 vs the first point. Demand
    # far better than quadratic — under top-k these ratios are ~1.
    assert probes[2] <= probes[0] * 4, (
        f"pairing work grew {probes[2] / probes[0]:.1f}x across a 4x event "
        f"increase — the quadratic is back: {probes}")


def test_non_vacuity_the_unbounded_path_pairs_orders_of_magnitude_more(
        monkeypatch):
    """A sparse fixture makes every assertion here pass trivially — which is
    precisely how a 67ms sandbox measurement and a 412s reality coexisted.
    Prove the fixture bites: the rollback path on the SAME map does >100x
    the bounded path's pairing work."""
    h1, nodes = dense(3000)
    am = traversal_map(nodes, 3000, dup=2)
    h1.get_edge_calls = 0
    HebbianUpdater().update(h1, am)
    bounded = h1.get_edge_calls

    monkeypatch.setenv("NEXUS_HEBBIAN_PAIRING", "full")
    h2, nodes2 = dense(3000)
    am2 = traversal_map(nodes2, 3000, dup=2)
    h2.get_edge_calls = 0
    HebbianUpdater().update(h2, am2)
    unbounded = h2.get_edge_calls

    assert unbounded > 100 * max(1, bounded), (
        f"fixture too sparse to expose the problem: full={unbounded} "
        f"bounded={bounded}")


# ---------------------------------------------------------------------------
# The counter counts edges, not operations (I-7)
# ---------------------------------------------------------------------------

def test_the_counter_equals_distinct_edges_modified():
    h, nodes = dense(500)
    am = traversal_map(nodes, 40, dup=4)          # heavy duplication
    before = {(e.source, e.target): e.weight for e in h.edges()}
    r = HebbianUpdater().update(h, am)
    touched = {k for k, e in
               {(e.source, e.target): e for e in h.edges()}.items()
               if k not in before or e.weight > before[k]}
    assert r.strengthened + 0 == len(
        {k for k in touched}), (
        f"payload says {r.strengthened}, graph shows {len(touched)} distinct "
        f"edges raised — the counter is counting operations again")
    assert r.strengthened <= 20 * 19 / 2 + r.formed


def test_duplicated_delivery_does_not_multiply_the_count():
    """One pair, delivered many times: the count must be 1, the weight must
    move once per pass, not once per duplicate."""
    h = CountingHippo()
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.5))
    am = ActivationMap(activations={"a": 1.0, "b": 0.9},
                       fired_sequence=[("a", 0), ("b", 1)] * 10)
    r = HebbianUpdater().update(h, am)
    assert r.strengthened == 1, (
        f"one edge, ten deliveries, counted {r.strengthened} times")
    # Weight moved by exactly one strengthen step (plasticity 0.1 x mult 1).
    assert h.get_edge("a", "b").weight == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Co-activation is still co-activation (I-1)
# ---------------------------------------------------------------------------

def test_selection_comes_from_activation_not_delivery_count():
    """Same activations, wildly different delivery multiplicity: the pairs
    strengthened must be IDENTICAL. A popularity signal (delivery count,
    order, frequency) sneaking into selection fails here."""
    results = []
    for dup in (1, 5):
        h, nodes = dense(600)
        am = traversal_map(nodes, 100 * dup, dup=dup)   # same 100 uniques
        before = {(e.source, e.target): e.weight for e in h.edges()}
        HebbianUpdater().update(h, am)
        results.append(frozenset(
            k for k, e in {(e.source, e.target): e for e in h.edges()}.items()
            if k not in before or e.weight > before[k]))
    assert results[0] == results[1], (
        "delivery multiplicity changed WHICH pairs strengthened — the "
        "selection has acquired a popularity input (I-1)")


def test_changing_activations_changes_selection():
    """Non-vacuity for the pin above: the selection genuinely reads the
    activation levels — inverting them selects different pairs."""
    sets = []
    for invert in (False, True):
        h, nodes = dense(600)
        uniq = nodes[:100]
        acts = {n: (i if invert else (100 - i)) / 100.0
                for i, n in enumerate(uniq)}
        am = ActivationMap(activations=acts,
                           fired_sequence=[(n, 1) for n in uniq])
        before = {(e.source, e.target): e.weight for e in h.edges()}
        HebbianUpdater().update(h, am)
        sets.append(frozenset(
            k for k, e in {(e.source, e.target): e for e in h.edges()}.items()
            if k not in before or e.weight > before[k]))
    assert sets[0] != sets[1]


# ---------------------------------------------------------------------------
# Declared, configurable, reducible
# ---------------------------------------------------------------------------

def test_the_payload_declares_what_was_considered_and_skipped():
    h, nodes = dense(600)
    am = traversal_map(nodes, 400, dup=2)
    r = HebbianUpdater().update(h, am)
    assert r.pairing_mode == "topk"
    assert r.nodes_paired <= r.k <= 200
    assert r.fired_events == 400, "the skipped scale must be visible (I-6)"


def test_k_is_configurable_and_the_default_is_20(monkeypatch):
    from backend.cortex.hebbian import pairing_topk
    monkeypatch.delenv("NEXUS_HEBBIAN_TOPK", raising=False)
    assert pairing_topk() == 20
    monkeypatch.setenv("NEXUS_HEBBIAN_TOPK", "7")
    assert pairing_topk() == 7
    h, nodes = dense(600)
    am = traversal_map(nodes, 100)
    r = HebbianUpdater().update(h, am)
    assert r.k == 7 and r.nodes_paired <= 7


def test_full_mode_is_the_pre_am_loop_exactly(monkeypatch):
    """Reducibility. Duplicate deliveries multiply both the weight movement
    and the operation-counting payload, exactly as before AM."""
    monkeypatch.setenv("NEXUS_HEBBIAN_PAIRING", "full")
    h = CountingHippo()
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.0))
    am = ActivationMap(activations={"a": 1.0, "b": 0.9},
                       fired_sequence=[("a", 0), ("b", 1)] * 3)
    r = HebbianUpdater().update(h, am)
    # [a,b,a,b,a,b]: directed (a->b) pairs 6, (b->a) pairs 3, self-pairs
    # skipped. Old counter counted OPERATIONS (9), the a->b edge is
    # strengthened SIX times (0.0 -> 0.6), and a reverse b->a edge is
    # FORMED at 0.2 and strengthened three times (-> 0.5) — the duplicate
    # multiplication and mirror-edge formation that ARE the pre-AM loop.
    assert r.strengthened == 9
    assert r.pairing_mode == "full"
    assert r.formed == 1
    assert h.get_edge("a", "b").weight == pytest.approx(0.6)  # 6 x 0.1
    assert h.get_edge("b", "a").weight == pytest.approx(0.5)  # 0.2 + 3 x 0.1


def test_ltd_still_uses_the_full_traversal_not_the_topk(monkeypatch):
    """Scope pin: narrowing LTD's spared set is AJ's question, not AM's. A
    traversed-but-not-top-k edge must NOT weaken."""
    monkeypatch.setenv("NEXUS_HEBBIAN_TOPK", "2")
    h = CountingHippo()
    h.add_edge(SynapticEdge(source="x", target="y", weight=0.5))
    h.add_edge(SynapticEdge(source="p", target="q", weight=0.5))
    am = ActivationMap(
        activations={"a": 1.0, "b": 0.9, "x": 0.3, "y": 0.2},
        fired_sequence=[("a", 0), ("b", 0), ("x", 1), ("y", 1)])
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.5))
    HebbianUpdater().update(h, am)
    assert h.get_edge("x", "y").weight == pytest.approx(0.5), (
        "a traversed edge outside the top-k was weakened — LTD's spared set "
        "silently narrowed, which is AJ's decision, not AM's")
    assert h.get_edge("p", "q").weight < 0.5   # untraversed still weakens