"""Session-level graph (Layer 1 of arch §7.1).

NetworkX DiGraph in RAM. High plasticity. Phase 3 implements the storage and
mutation surface; Phase 5 KAIROS handles the consolidation pass that promotes
edges with fire_count >= 7 into the SQLite neocortex.
"""
from __future__ import annotations

from typing import Iterator

import networkx as nx

from backend.cortex.columns import Column, infer_column
from backend.cortex.edges import SynapticEdge
from backend.cortex.nodes import CorticalNode, EpistemicState, NodeType


class Hippocampus:
    """Thin wrapper over networkx.DiGraph that owns CorticalNode / SynapticEdge."""

    def __init__(self):
        self.graph = nx.DiGraph()
        # AK / I-6 — how many times a substrate row's stored weight was NOT
        # copied onto a live edge (the suppressed record->prominence
        # restorations). Session-scoped like the graph itself; surfaced on
        # /cortex/stats, because a silent behaviour change in the graph is
        # how the leak went unnoticed in the first place.
        self.restorations_suppressed = 0

    # ---------- node ops ----------

    def upsert_node(
        self,
        node_id: str,
        *,
        node_type: NodeType = NodeType.INTERNEURON,
        epistemic_state: EpistemicState = EpistemicState.PARTIAL,
        confidence: float = 0.5,
        column: Column | str | None = None,
    ) -> CorticalNode:
        if node_id in self.graph:
            return self.graph.nodes[node_id]["node"]
        col = column.value if isinstance(column, Column) else (
            column or infer_column(node_id).value
        )
        node = CorticalNode(
            id=node_id, node_type=node_type, epistemic_state=epistemic_state,
            confidence=confidence, column=col,
        )
        self.graph.add_node(node_id, node=node)
        return node

    def get_node(self, node_id: str) -> CorticalNode:
        return self.graph.nodes[node_id]["node"]

    @property
    def nodes(self) -> set[str]:
        return set(self.graph.nodes)

    def relax_all(self) -> None:
        for nid in self.graph.nodes:
            self.get_node(nid).relax()

    # ---------- edge ops ----------

    def add_edge(self, edge: SynapticEdge) -> None:
        # Ensure endpoints exist
        self.upsert_node(edge.source)
        self.upsert_node(edge.target)
        self.graph.add_edge(edge.source, edge.target, edge=edge)

    def get_edge(self, src: str, tgt: str) -> SynapticEdge | None:
        if not self.graph.has_edge(src, tgt):
            return None
        return self.graph.edges[src, tgt]["edge"]

    def edges(self) -> Iterator[SynapticEdge]:
        for s, t, data in self.graph.edges(data=True):
            yield data["edge"]

    def outgoing(self, node_id: str) -> list[tuple[str, SynapticEdge]]:
        if node_id not in self.graph:
            return []
        return [
            (tgt, self.graph.edges[node_id, tgt]["edge"])
            for tgt in self.graph.successors(node_id)
        ]

    def predecessors(self, node_id: str) -> list[str]:
        if node_id not in self.graph:
            return []
        return list(self.graph.predecessors(node_id))

    def successors(self, node_id: str) -> list[str]:
        if node_id not in self.graph:
            return []
        return list(self.graph.successors(node_id))

    # ---------- maintenance ----------

    def prune_below(
        self, threshold: float, *, protected: "set[tuple[str, str]] | None" = None,
    ) -> int:
        """Demote every edge below the visibility floor. Returns the count.

        E.3 / I-2 — this used to `remove_edge`, destroying the association and
        (after E.2) its monotone recurrence_count. Now it sets `demoted`, so
        the edge stops transmitting but keeps its weight and counters and
        stays in the graph: prominence falls, recoverability does not, and
        `n_edges` stays stable so the sealed fingerprint is unaffected.

        I-4 — `protected` pairs are exempt entirely: an association whose
        belief is cited by a sealed lineage record is load-bearing for a claim
        someone may have to defend, so it is not even demoted.

        Orphan nodes were never removed here and still are not (Law 4 spirit).
        """
        protected = protected or set()
        demoted = 0
        for s, t, data in self.graph.edges(data=True):
            edge = data["edge"]
            if edge.weight >= threshold or edge.demoted:
                continue
            if (s, t) in protected:
                continue
            edge.demoted = True
            demoted += 1
        return demoted

    def demoted_edges(self) -> "list[SynapticEdge]":
        return [d["edge"] for _s, _t, d in self.graph.edges(data=True)
                if d["edge"].demoted]

    def active_edges(self) -> "Iterator[SynapticEdge]":
        """Edges that still carry prominence — demoted ones excluded.

        `edges()` remains the full set so topology (and the sealed
        fingerprint's n_edges) is stable; anything that renders context or
        measures live association should use this instead.
        """
        for _s, _t, data in self.graph.edges(data=True):
            if not data["edge"].demoted:
                yield data["edge"]

    def in_degree(self, node_id: str) -> int:
        return self.graph.in_degree(node_id) if node_id in self.graph else 0

    def out_degree(self, node_id: str) -> int:
        return self.graph.out_degree(node_id) if node_id in self.graph else 0

    # ---------- ingestion from Phase 2 triples ----------

    def ingest_triple(
        self,
        subject: str,
        relation: str,
        object: str,
        weight: float = 0.5,
        *,
        epistemic_state: EpistemicState = EpistemicState.KNOWN,
        restore_weight: bool = True,
    ) -> SynapticEdge:
        """Fold a (s,r,o) row into the cortex as two nodes and one edge.

        The relation label rides as edge metadata; future phases can route on it.

        AK — `restore_weight=False` skips the `max()` on an EXISTING edge.
        The store's row weight is a monotone high-water mark (add()'s MAX
        upsert), the edge's weight is a current strength, and copying the
        first onto the second silently undid Hebbian weakening and the
        KAIROS LTD pass for any association whose row got retrieved — the
        record layer writing the prominence layer (I-8). The default stays
        True because for CLAIM rows the copy is the wanted warming path;
        the caller decides, since only it knows what kind of row this is.
        Edge CREATION is unchanged in both positions — the ingest-set
        question is loop 1's, deliberately not this packet's.
        """
        s_node = self.upsert_node(subject, epistemic_state=epistemic_state)
        o_node = self.upsert_node(object, epistemic_state=epistemic_state)
        edge = self.get_edge(subject, object)
        if edge is None:
            edge = SynapticEdge(source=subject, target=object, weight=weight)
            self.add_edge(edge)
        elif restore_weight:
            edge.weight = max(edge.weight, weight)
        # carry the predicate as metadata on the source node for later inspection
        s_node.metadata.setdefault("relations", []).append(relation)
        return edge
