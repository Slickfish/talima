"""CuriosityEngine (arch §6.4) — questions derived from graph topology, not LLM.

The formula, stated plainly (P2/F-20 — no anthropomorphic overclaim):

    score = in_degree × (1 − evidential weight)

A node many edges point at, whose evidential weight is low (or whose
epistemic state is known_unknown / partial / inferred), is a load-bearing
concept the system doesn't understand. The engine ranks those and phrases
each as one of four question templates. That is the entire mechanism —
"curiosity" here means gap detection over graph structure, nothing more.

The graph asks. Not the chatbot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.cortex.nodes import EpistemicState

if TYPE_CHECKING:
    from backend.memory.hippocampus import Hippocampus


CURIOSITY_STATES: frozenset[EpistemicState] = frozenset({
    EpistemicState.KNOWN_UNKNOWN,
    EpistemicState.PARTIAL,
    EpistemicState.INFERRED,
})


@dataclass(frozen=True)
class CuriousNode:
    node_id: str
    score: float
    question: str
    epistemic_state: str
    in_degree: int
    confidence: float

    def to_dict(self) -> dict:
        # P2/F-02 — external vocabulary is "evidential weight". The old
        # "confidence" key is kept for one release as a deprecated alias.
        return {
            "node_id":           self.node_id,
            "score":             round(self.score, 4),
            "question":          self.question,
            "epistemic_state":   self.epistemic_state,
            "in_degree":         self.in_degree,
            "evidential_weight": round(self.confidence, 3),
            "confidence":        round(self.confidence, 3),
            "confidence_deprecated": True,
        }


def _format_question(
    node_id: str,
    predecessors: list[str],
    successors: list[str],
) -> str:
    if predecessors and successors:
        return (
            f"How does {node_id} connect "
            f"{', '.join(predecessors)} to {', '.join(successors)}?"
        )
    if predecessors:
        return f"Why do {', '.join(predecessors)} depend on {node_id}?"
    if successors:
        return f"What does {node_id} reach in {', '.join(successors)}?"
    return f"What is {node_id}, and what does it touch?"


class CuriosityEngine:
    """Reads the LIVE Hippocampus on every call — never a snapshot."""

    def __init__(
        self,
        *,
        states: frozenset[EpistemicState] = CURIOSITY_STATES,
        min_in_degree: int = 1,
    ):
        self.states = states
        self.min_in_degree = min_in_degree

    def find_curious_nodes(
        self,
        cortex: "Hippocampus",
        *,
        top_k: int = 5,
    ) -> list[CuriousNode]:
        candidates: list[CuriousNode] = []
        for node_id in list(cortex.nodes):
            node = cortex.get_node(node_id)
            in_deg = cortex.in_degree(node_id)
            if in_deg < self.min_in_degree:
                continue
            if node.epistemic_state not in self.states:
                continue
            score = in_deg * (1.0 - float(node.confidence))
            preds = list(cortex.predecessors(node_id))[:2]
            succs = list(cortex.successors(node_id))[:2]
            candidates.append(CuriousNode(
                node_id=node_id,
                score=score,
                question=_format_question(node_id, preds, succs),
                epistemic_state=node.epistemic_state.value,
                in_degree=in_deg,
                confidence=float(node.confidence),
            ))
        candidates.sort(key=lambda c: -c.score)
        return candidates[:top_k]
