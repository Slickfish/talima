"""Spreading-activation propagation. Up to 4 hops (arch §3.1).

Given a set of seed nodes, fire signals through SynapticEdges. Inhibitory and
modulatory edges modelled. Returns an ActivationMap so the caller can pick the
top-K activated nodes for context injection or Hebbian co-activation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.cortex.columns import Column, ColumnState

if TYPE_CHECKING:
    from backend.memory.hippocampus import Hippocampus


MAX_HOPS = 4
PROPAGATION_TAU = 0.7   # signal attenuation per hop
# SNIP-DET — the precision activation is ORDERED at. The record keeps six
# decimals; the edge decay's microsecond noise sits around 1e-10 and below.
ACTIVATION_RANK_PRECISION = 6


@dataclass
class ActivationMap:
    activations: dict[str, float] = field(default_factory=dict)
    fired_sequence: list[tuple[str, int]] = field(default_factory=list)  # (node_id, hop)
    column_state: ColumnState = field(default_factory=ColumnState)

    @property
    def density(self) -> float:
        """Mean activation across activated nodes — used by gap detector (Phase 8)."""
        if not self.activations:
            return 0.0
        return sum(self.activations.values()) / len(self.activations)

    @property
    def peak_activations(self) -> list[tuple[str, float]]:
        """A TOTAL order (SNIP-DET): activation at the precision anything
        reads it (the cortex line renders two decimals, the record keeps
        six), then the order the node first fired in this pass, then the
        node id. Before this the sort had no tie-break and the raw floats
        carried the edge decay's microsecond noise in their last bits, so
        which of the equally activated seeds made the Hebbian top-k could
        turn on the clock."""
        first = {}
        for i, (n, _hop) in enumerate(self.fired_sequence):
            first.setdefault(n, i)
        return sorted(self.activations.items(),
                      key=lambda kv: (-round(kv[1], ACTIVATION_RANK_PRECISION),
                                      first.get(kv[0], len(first)), kv[0]))

    def top_k(self, k: int = 20) -> list[tuple[str, float]]:
        return self.peak_activations[:k]


def propagate(
    hippocampus: "Hippocampus",
    seed_nodes: list[str],
    *,
    seed_activation: float = 1.0,
    max_hops: int = MAX_HOPS,
    now: float | None = None,
    refractory_seconds: float | None = None,
) -> ActivationMap:
    """BFS up to max_hops, accumulating activation, applying lateral inhibition."""
    now = now or time.time()
    # E.2 — resolve the recurrence window once here rather than per edge: the
    # co-firing edge set is quadratic, so a per-firing env read would be a hot
    # loop. Injectable for deterministic tests.
    if refractory_seconds is None:
        from backend.cortex.edges import _default_refractory_seconds
        refractory_seconds = _default_refractory_seconds()
    am = ActivationMap()
    if not seed_nodes:
        return am

    # Seed
    frontier: dict[str, float] = {}
    for sid in seed_nodes:
        if sid in hippocampus.nodes:
            am.activations[sid] = seed_activation
            am.fired_sequence.append((sid, 0))
            n = hippocampus.get_node(sid)
            n.excite(seed_activation)
            n.fire()
            if n.column:
                am.column_state.add(Column(n.column), seed_activation)
            frontier[sid] = seed_activation

    # Spread
    for hop in range(1, max_hops + 1):
        next_frontier: dict[str, float] = {}
        inhibition = am.column_state.lateral_inhibit()
        for src_id, src_act in frontier.items():
            for tgt_id, edge in hippocampus.outgoing(src_id):
                if tgt_id not in hippocampus.nodes:
                    continue
                signal = edge.transmit(src_act, now=now)
                if signal == 0.0:
                    continue
                edge.mark_fired(now, refractory_seconds=refractory_seconds)
                tgt = hippocampus.get_node(tgt_id)
                # Lateral inhibition from column dynamics
                col_factor = inhibition.get(Column(tgt.column), 1.0) if tgt.column else 1.0
                delivered = signal * (PROPAGATION_TAU ** (hop - 1)) * col_factor
                tgt.excite(delivered)
                tgt.fire()
                am.activations[tgt_id] = max(
                    am.activations.get(tgt_id, 0.0),
                    tgt.activation,
                )
                am.fired_sequence.append((tgt_id, hop))
                if tgt.column:
                    am.column_state.add(Column(tgt.column), abs(delivered))
                if tgt.is_firing():
                    next_frontier[tgt_id] = tgt.activation
        if not next_frontier:
            break
        frontier = next_frontier

    return am
