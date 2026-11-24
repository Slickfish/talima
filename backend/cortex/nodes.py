"""CorticalNode — Law II: nodes are activation states, not concepts.

Every node carries a continuous activation (0.0-1.0), a resting potential, a
firing threshold, a membrane time constant, a node type, and an epistemic
state. The epistemic state is what gets injected into the LLM prompt so the
model is honest about what it knows vs infers vs knows-it-doesn't-know.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class NodeType(str, Enum):
    SENSORY      = "sensory"      # external input (user query terms)
    INTERNEURON  = "interneuron"  # local relay
    ASSOCIATION  = "association"  # cross-column hub (Law IV)
    MOTOR        = "motor"        # output / response-shaping


class EpistemicState(str, Enum):
    KNOWN          = "known"
    PARTIAL        = "partial"
    CONTRADICTED   = "contradicted"
    KNOWN_UNKNOWN  = "known_unknown"
    INFERRED       = "inferred"

    def render(self, *, confidence: float | None = None) -> str:
        """Format the bracketed tag injected after the concept name in prompts.

        P2/F-02 — the prompt-visible label is "evidential weight" (the number
        is an uncalibrated relative weight, not a probability of truth)."""
        if self is EpistemicState.KNOWN:
            c = f", evidential weight: {confidence:.2f}" if confidence is not None else ""
            return f"[known{c}]"
        if self is EpistemicState.PARTIAL:
            return "[partial knowledge — use with caution]"
        if self is EpistemicState.CONTRADICTED:
            return "[contradictory evidence — treat carefully]"
        if self is EpistemicState.KNOWN_UNKNOWN:
            return "[referenced but uncharted — knowledge gap]"
        return "[inferred by association — unverified]"


@dataclass
class CorticalNode:
    id: str
    activation: float = 0.0          # current firing level (0.0-1.0)
    resting_potential: float = 0.0
    firing_threshold: float = 0.2
    membrane_tau: float = 0.6        # decay-per-step toward resting
    node_type: NodeType = NodeType.INTERNEURON
    epistemic_state: EpistemicState = EpistemicState.PARTIAL
    confidence: float = 0.5
    column: str | None = None        # cortical column membership (Phase 3)
    fire_count: int = 0
    metadata: dict = field(default_factory=dict)

    def is_firing(self) -> bool:
        return self.activation >= self.firing_threshold

    def relax(self) -> None:
        """Membrane decay one step toward resting_potential."""
        self.activation = (
            self.resting_potential
            + (self.activation - self.resting_potential) * self.membrane_tau
        )
        if self.activation < 1e-6:
            self.activation = self.resting_potential

    def excite(self, delta: float) -> None:
        self.activation = max(0.0, min(1.0, self.activation + delta))

    def fire(self) -> None:
        if self.is_firing():
            self.fire_count += 1
