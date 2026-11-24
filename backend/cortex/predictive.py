"""PredictiveCortex (Law XI, arch §6.2) — Friston's Free Energy applied to a graph.

Before each inference call: propagate query nodes through current edge weights
to GENERATE a prediction (the graph's own forward model). After the response:
extract triples and compare against the prediction.

  - actual ∖ predicted = HIGH SURPRISE        (multiplier 2.5 in Hebbian)
  - actual ∩ predicted = CONFIRMED            (multiplier 0.4)
  - predicted ∖ actual = MISSED               (factor 0.95 weakening)

The system learns fastest at the edges of its own knowledge, where surprise
is highest. Confirming what you already know teaches you nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.cortex.propagation import ActivationMap, propagate

if TYPE_CHECKING:
    from backend.memory.hippocampus import Hippocampus


# Multipliers — arch §6.2
SURPRISE_MULTIPLIER  = 2.5
CONFIRMED_MULTIPLIER = 0.4
MISSED_FACTOR        = 0.95


@dataclass(frozen=True)
class PredictionError:
    high_surprise: frozenset
    confirmed:     frozenset
    missed:        frozenset
    surprise_magnitude: float

    @property
    def total_observed(self) -> int:
        return len(self.high_surprise) + len(self.confirmed)


class PredictiveCortex:
    """Standard propagation IS the prediction. Current edge weights ARE the
    generative model. compute_error() takes the post-hoc activation map and
    diffs it against the pre-hoc prediction map.
    """

    def __init__(self, hippocampus: "Hippocampus", *, threshold: float = 0.0):
        self.hippocampus = hippocampus
        self.threshold = threshold

    # ---------- prediction ----------

    def predict(
        self,
        # ------------------------------------------------------------------
        # PACKET P.4 — QUARANTINED. Kept, not deleted, and not wired.
        #
        # Wiring this would recompute an activation map the loop has already
        # produced, so calling it costs a second propagation for a result the
        # turn already holds. It stays because the REPLACEMENT is the thing
        # worth having: a predictor that guesses what the graph will activate,
        # whose guesses can then be scored against what actually did.
        #
        # The `prediction` mechanism in backend/eval/calibration_log.py was
        # RESERVED for that replacement, and AF.1 built it: the citation
        # forecast (backend/cortex/forecast.py) — seeded from query terms
        # alone, persisted before retrieval, scored against the F.5 citation
        # flags beside the prominence null. It is the mechanism's ONLY
        # permitted writer — the P.4 sentinel in test_gate_clause_rules.py
        # now enforces the exclusive grant. This method STAYS
        # quarantined: its map is the retrieval-seeded one, and filling the
        # channel from it would still make the forecast uncalibratable
        # against its own history.
        #
        # Deleting instead of quarantining would lose the seam. E.4's declined
        # write and Packet B's purge are the precedents: the record of a
        # decision not taken is worth as much as the decision.
        # ------------------------------------------------------------------
        query_nodes: list[str],
        *,
        max_hops: int = 4,
        seed_activation: float = 1.0,
    ) -> ActivationMap:
        return propagate(
            self.hippocampus, query_nodes,
            max_hops=max_hops, seed_activation=seed_activation,
        )

    # ---------- error ----------

    def compute_error(
        self,
        predicted: ActivationMap,
        actual: ActivationMap,
    ) -> PredictionError:
        """Diff two activation maps. Both must come from THE SAME hippocampus
        snapshot — the caller is responsible for ordering predict→actual."""
        p_ids = {n for n, a in predicted.activations.items() if a > self.threshold}
        a_ids = {n for n, a in actual.activations.items()    if a > self.threshold}
        surprise  = a_ids - p_ids
        confirmed = a_ids & p_ids
        missed    = p_ids - a_ids
        magnitude = len(surprise) / max(len(a_ids), 1)
        return PredictionError(
            high_surprise=frozenset(surprise),
            confirmed=frozenset(confirmed),
            missed=frozenset(missed),
            surprise_magnitude=magnitude,
        )
