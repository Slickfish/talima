"""Hebbian plasticity (Law III + Law XI).

Phase 3: co-activation strengthening, LTD decay, edge formation, pruning <0.05.
Phase 7: prediction-error modulation (arch §6.2):
  - target node in error.high_surprise → strengthen with 2.5× multiplier
  - target node in error.confirmed     → strengthen with 0.4× multiplier
  - target node in error.missed        → weaken with 0.95× factor (extra LTD)

Without a PredictionError the updater behaves identically to Phase 3.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

from backend.cortex.edges import PRUNE_THRESHOLD, SynapticEdge

logger = logging.getLogger("nexus.hebbian")

if TYPE_CHECKING:
    from backend.cortex.predictive import PredictionError
    from backend.cortex.propagation import ActivationMap
    from backend.memory.hippocampus import Hippocampus


NEW_EDGE_INITIAL_WEIGHT  = 0.2
LTD_FACTOR               = 0.95
DEFAULT_STRENGTHEN_MULT  = 1.0

# AM — how many activation-ranked nodes the strengthening loop pairs.
# PROVISIONAL: fitted to nothing; it is the same top_k(20) surface every
# other consumer of the activation map reads. 20 nodes -> at most 190 pairs
# per turn, against the measured 987,319,778 pairing operations one real
# turn performed when the loop paired the full fired_sequence (one entry
# per DELIVERY, so quadratic in edges on a densely reachable graph:
# E²/2 x 417ns = 6m52s at 44,547 edges, ~35min at 100k).
DEFAULT_PAIRING_TOPK = 20


def pairing_mode() -> str:
    """`topk` (default) pairs the activation map's top-K unique nodes.
    `full` is AM's rollback: the pre-AM loop exactly — pairing the raw
    fired_sequence, duplicate deliveries multiplying both the weight
    movement and the operation-counting payload. Read at call time."""
    raw = os.environ.get("NEXUS_HEBBIAN_PAIRING", "topk").strip().lower()
    return raw if raw in ("topk", "full") else "topk"


def pairing_topk() -> int:
    raw = os.environ.get("NEXUS_HEBBIAN_TOPK")
    if raw is None or not raw.strip():
        return DEFAULT_PAIRING_TOPK
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("NEXUS_HEBBIAN_TOPK=%r is not an integer — using %s",
                       raw, DEFAULT_PAIRING_TOPK)
        return DEFAULT_PAIRING_TOPK


class ProtectionUnavailable(RuntimeError):
    """AN.1 — the I-4 exemption set could not be computed.

    Raised (never swallowed) so the DEMOTION step it governs can fail
    closed. The old shape returned an empty set with a console log line:
    a transient database error silently ran the prune with nothing
    protected, which is a guard that holds only when nothing goes wrong.
    """


@dataclass
class HebbianResult:
    strengthened: int = 0
    weakened: int = 0
    formed: int = 0
    # None = the prune did not run (protection unknown). The None
    # discipline: an aborted count must not read as a measured zero.
    pruned: "int | None" = 0
    surprise_strengthened: int = 0
    confirmed_strengthened: int = 0
    missed_weakened: int = 0
    # AM / I-6 — what the pairing considered and what it skipped. In `topk`
    # mode the counts above are DISTINCT EDGES whose weight actually moved;
    # in `full` mode they keep the pre-AM operation-counting semantics, so
    # the rollback is exact including its payload.
    pairing_mode: str = "topk"
    k: int = 0                    # 0 = unbounded (full mode)
    nodes_paired: int = 0
    fired_events: int = 0
    # AN.1 / I-6 — "ok" | "unavailable". Declared on the hebbian_update
    # frame, because the failure used to reach a console line and nothing
    # else.
    protection_state: str = "ok"


class HebbianUpdater:
    """Applies one Hebbian pass over a hippocampus given a fired sequence."""

    def __init__(
        self,
        *,
        new_edge_initial_weight: float = NEW_EDGE_INITIAL_WEIGHT,
        ltd_factor: float = LTD_FACTOR,
        prune_threshold: float = PRUNE_THRESHOLD,
        protected_pairs: "Callable[[], set[tuple[str, str]]] | None" = None,
    ):
        self.new_edge_initial_weight = new_edge_initial_weight
        self.ltd_factor = ltd_factor
        self.prune_threshold = prune_threshold
        # E.3 / I-4 — supplies the (source, target) pairs whose beliefs are
        # cited by a sealed lineage record. Injected once at construction so
        # neither update() nor decay_pass() needs a store reference. None means
        # "nothing protected", which is the correct default for a bare updater.
        self.protected_pairs = protected_pairs

    def _protected(self) -> "set[tuple[str, str]]":
        """The I-4 exemption set, or ProtectionUnavailable — never a guess.

        AN.1 — this used to catch and return `set()` ("never let protection
        lookup break a turn"), which meant a failed lookup ran the prune
        with nothing protected. The turn still never breaks: the CALLER of
        the prune fails closed (skips demotion, declares), and only the
        maintenance path lets the exception propagate into the cycle's
        existing abort-and-report-None machinery.
        """
        if self.protected_pairs is None:
            return set()
        try:
            return self.protected_pairs()
        except ProtectionUnavailable:
            raise
        except Exception as e:
            raise ProtectionUnavailable(
                f"protected-pair lookup failed: {type(e).__name__}: {e}"
            ) from e

    # ---------- main entry ----------

    def update(
        self,
        hippocampus: "Hippocampus",
        activation: "ActivationMap",
        *,
        strengthen_multiplier: float = DEFAULT_STRENGTHEN_MULT,
        error: "PredictionError | None" = None,
    ) -> HebbianResult:
        # Late import to avoid cycles
        from backend.cortex.predictive import (
            CONFIRMED_MULTIPLIER, MISSED_FACTOR, SURPRISE_MULTIPLIER,
        )

        result = HebbianResult()
        # LTD's spared set is the FULL traversal in both modes: sparing
        # traversed edges from decay is the pre-AM behaviour, and narrowing
        # it would change what survives, which is AJ's episode question,
        # not AM's pairing one.
        fired_set = {n for n, _ in activation.fired_sequence}
        result.fired_events = len(activation.fired_sequence)
        result.pairing_mode = mode = pairing_mode()

        if mode == "full":
            # AM's rollback: the raw event list, duplicates and all. One
            # real turn measured this at E(E-1)/2 = 987,319,778 operations
            # over 44,547 edges (6m52s) — quadratic in deliveries, and a
            # node reached over ten edges appears ten times.
            fired_ids = [n for n, _ in activation.fired_sequence]
            result.nodes_paired = len(fired_set)
        else:
            # AM — pair the activation map's top-K UNIQUE nodes: selection
            # by this turn's co-activation level (I-1 — the same surface
            # prediction and the context block read), never by delivery
            # count, order, or any stored frequency.
            result.k = pairing_topk()
            fired_ids = [n for n, _ in activation.top_k(result.k)]
            result.nodes_paired = len(fired_ids)

        # Strengthen co-fired pairs. In topk mode the counters hold DISTINCT
        # edges whose weight actually moved (I-7 — the payload was declared
        # `edges_modified` until W1; the old operation count overstated distinct
        # edges ~550x on a measured turn, most of it hammering edges already
        # pinned at the cap). In full mode the pre-AM operation-counting
        # semantics are preserved so the rollback is exact.
        s_edges: set = set()
        su_edges: set = set()
        co_edges: set = set()
        for i, src in enumerate(fired_ids):
            for tgt in fired_ids[i + 1:]:
                if src == tgt:
                    continue
                edge = hippocampus.get_edge(src, tgt)
                if edge is None:
                    edge = SynapticEdge(
                        source=src, target=tgt, weight=self.new_edge_initial_weight,
                    )
                    hippocampus.add_edge(edge)
                    result.formed += 1

                # Phase 7: prediction-error modulates per-edge multiplier
                mult = strengthen_multiplier
                surprise = confirmed = False
                if error is not None:
                    if tgt in error.high_surprise:
                        mult = SURPRISE_MULTIPLIER
                        surprise = True
                    elif tgt in error.confirmed:
                        mult = CONFIRMED_MULTIPLIER
                        confirmed = True
                w0 = edge.weight
                edge.strengthen(mult)
                if mode == "full":
                    result.strengthened += 1
                    if surprise:
                        result.surprise_strengthened += 1
                    elif confirmed:
                        result.confirmed_strengthened += 1
                elif edge.weight > w0:
                    key = (src, tgt)
                    s_edges.add(key)
                    if surprise:
                        su_edges.add(key)
                    elif confirmed:
                        co_edges.add(key)
        if mode != "full":
            result.strengthened = len(s_edges)
            result.surprise_strengthened = len(su_edges)
            result.confirmed_strengthened = len(co_edges)

        # LTD — edges with endpoints not both firing weaken; predicted-but-missed
        # nodes attract an additional 0.95 factor on every edge feeding them.
        missed = error.missed if error is not None else frozenset()
        for edge in list(hippocampus.edges()):
            applied = False
            if not (edge.source in fired_set and edge.target in fired_set):
                edge.weaken(self.ltd_factor)
                applied = True
            if edge.target in missed:
                edge.weaken(MISSED_FACTOR)
                result.missed_weakened += 1
                applied = True
            if applied:
                result.weakened += 1

        # Prune below floor.
        # E.3 / I-2 — demotes rather than deletes (path 2: runs every turn).
        # AN.1 — FAIL CLOSED: if the exemption set cannot be computed, the
        # demotion step does not run at all, and says so. Skipping the prune
        # for one turn costs a few edges a turn's grace; running it
        # unprotected strips I-4 from every sealed-cited belief silently.
        try:
            protected = self._protected()
        except ProtectionUnavailable as e:
            logger.warning("prune skipped, failing closed: %s", e)
            result.pruned = None
            result.protection_state = "unavailable"
            return result
        result.pruned = hippocampus.prune_below(
            self.prune_threshold, protected=protected)
        return result

    # ---------- helpers ----------

    def decay_pass(self, hippocampus: "Hippocampus", days: float) -> int:
        """Apply time-decay to every in-memory edge, then prune.

        The return value is a **pre-prune census of edges visited**, not a
        count of edges whose weight changed and not a count of triples: `n`
        increments once per edge regardless of outcome, and the prune runs
        after counting. KAIROS surfaces it as `hebbian_edges_visited`
        (`decayed` is the deprecated alias). Because edges are formed
        pairwise across co-fired nodes, this number is quadratic in session
        activity and routinely exceeds the store's triple count — that is
        expected, not a bug. Mislabelling it cost a full diagnostic session.
        """
        # AN.1 — resolve the exemption set BEFORE touching any edge, and let
        # ProtectionUnavailable propagate: on the maintenance path the cycle's
        # blanket handler already turns an abort into skipped=True with every
        # count None (OQ-1's machinery), which is exactly "report unknown".
        # Resolving first means a failed lookup decays nothing either — the
        # pass aborts whole, not half.
        protected = self._protected()
        n = 0
        for edge in hippocampus.edges():
            edge.decay_step(days)
            n += 1
        # E.3 / I-2 — demotes rather than deletes (path 1: per KAIROS cycle).
        hippocampus.prune_below(
            self.prune_threshold, protected=protected)
        return n
