"""SynapticEdge — Law I: edges are potentials, not facts.

weight 0.9 does not mean "this is true". It means "when source fires, this
much signal reaches the target". Edges decay over time, fall into a refractory
period after firing, and have a plasticity coefficient that modulates how fast
Hebbian updates strengthen them.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("nexus.cortex")

# --- E.2 refractory-gated recurrence --------------------------------------
# Biological consolidation is gated by repetition DISTRIBUTED over time, not
# by raw count: massed repetition (one session) yields fast, fragile
# learning; distributed repetition yields durable learning. A burst of
# co-firing inside one work session is ONE episode of learning, so the
# recurrence counter debounces it. Distinct from `refractory_seconds`, which
# is the sub-second transmission refractory used by in_refractory().
DEFAULT_REFRACTORY_HOURS = 12.0


def _default_refractory_seconds() -> float:
    """Read at call time, not import, so a deployment or test can set the env
    var after the module is loaded (the pattern Packet D established)."""
    raw = os.environ.get("NEXUS_REFRACTORY_HOURS")
    if raw is None or raw.strip() == "":
        return DEFAULT_REFRACTORY_HOURS * 3600.0
    try:
        return float(raw) * 3600.0
    except ValueError:
        logger.warning(
            "NEXUS_REFRACTORY_HOURS=%r is not a number — falling back to %s h",
            raw, DEFAULT_REFRACTORY_HOURS)
        return DEFAULT_REFRACTORY_HOURS * 3600.0


class ActivationType(str, Enum):
    EXCITATORY  = "excitatory"
    INHIBITORY  = "inhibitory"
    MODULATORY  = "modulatory"


PRUNE_THRESHOLD = 0.05  # Phase 3 test: prune below 0.05
DAY_SECONDS = 86400.0


@dataclass
class SynapticEdge:
    source: str
    target: str
    weight: float = 0.5
    decay_per_day: float = 0.02
    refractory_seconds: float = 0.5
    plasticity: float = 0.1            # how much Hebbian moves the weight
    activation_type: ActivationType = ActivationType.EXCITATORY
    last_fired: float = 0.0
    fire_count: int = 0
    # E.2 — episode counter. MONOTONE: never decays, never resets. fire_count
    # measures intensity within a session; recurrence_count measures how many
    # refractory-separated episodes this association has survived. The two
    # together are the early-/late-phase LTP distinction.
    last_counted_at: float = 0.0
    recurrence_count: int = 0
    # E.3 / I-2 — prune reduces PROMINENCE, never RECOVERABILITY. A demoted
    # edge stops transmitting (it is invisible to propagation) but stays in
    # the graph with its weight, fire_count and recurrence_count intact, so
    # n_edges is stable and nothing is destroyed. Firing restores it: an
    # association re-earns its place by being used again.
    demoted: bool = False

    def current_weight(self, now: float | None = None) -> float:
        """Time-decayed weight. Stored weight is unchanged (Law I)."""
        now = now or time.time()
        if self.last_fired == 0.0:
            return self.weight
        days = max(0.0, (now - self.last_fired) / DAY_SECONDS)
        return max(0.0, self.weight - self.decay_per_day * days)

    def in_refractory(self, now: float | None = None) -> bool:
        now = now or time.time()
        return (now - self.last_fired) < self.refractory_seconds

    def transmit(self, source_activation: float, *, now: float | None = None) -> float:
        """Signal delivered to target. Inhibitory edges return negative values."""
        if self.demoted:
            return 0.0          # E.3/I-2 — demoted: no prominence, still recoverable
        if self.in_refractory(now):
            return 0.0
        signal = source_activation * self.current_weight(now)
        if self.activation_type is ActivationType.INHIBITORY:
            return -signal
        if self.activation_type is ActivationType.MODULATORY:
            return signal * 0.5  # weaker but always-on
        return signal

    def mark_fired(
        self,
        now: float | None = None,
        *,
        refractory_seconds: float | None = None,
    ) -> bool:
        """Register a firing. Returns True if it opened a new episode.

        E.2 — intensity (`fire_count`) advances on every firing, as before.
        Recurrence advances only when this firing is separated from the last
        counted one by at least the refractory window, collapsing a burst
        into a single counted episode.

        The clock is injectable: `now` is resolved once, up front, and the
        gate compares against that value — no `time.time()` inside the gate,
        so tests are deterministic.
        """
        now = time.time() if now is None else now
        self.last_fired = now
        self.fire_count += 1
        # Reactivation restores prominence — the association re-earned it.
        self.demoted = False

        window = (_default_refractory_seconds() if refractory_seconds is None
                  else refractory_seconds)
        if now - self.last_counted_at >= window:
            self.recurrence_count += 1
            self.last_counted_at = now
            return True
        return False

    def strengthen(self, multiplier: float = 1.0) -> None:
        delta = self.plasticity * multiplier
        self.weight = min(1.0, self.weight + delta)

    def weaken(self, factor: float = 0.95) -> None:
        self.weight = max(0.0, self.weight * factor)

    def decay_step(self, days: float) -> None:
        """Apply LTD (long-term depression) — used by KAIROS decay pass."""
        self.weight = max(0.0, self.weight - self.decay_per_day * days)
