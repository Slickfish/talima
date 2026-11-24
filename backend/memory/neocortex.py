"""Neocortex (Layer 2 of arch §7.1) — persistent graph in tiered SQLite.

Wraps the Phase 2 TripleStore so the hippocampus / neocortex split is
explicit. Promotion from hippocampus is gated by fire_count >= threshold
(arch §7.2 CONSOLIDATION_THRESHOLD = 7). No triple is ever deleted (Law 4).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from backend.kb.gate import BeliefRejected
from backend.kb.granularity import Granularity
from backend.kb.schema import DEFAULT_DB
from backend.kb.tiered_store import TripleStore

if TYPE_CHECKING:
    from backend.memory.hippocampus import Hippocampus


CONSOLIDATION_THRESHOLD = 7   # arch §7.2 — fire_count required for promotion

logger = logging.getLogger("nexus.neocortex")

# E.2 — second stage of the consolidation gate. CONSOLIDATION_THRESHOLD is
# intensity within a session (early-phase LTP); this is recurrence across
# refractory-separated episodes (late-phase LTP, which is what makes a
# memory durable). Both must hold. I-1: the intensity threshold keeps its
# value and its meaning — this stage is added beside it, never in place of it.
DEFAULT_RECURRENCE_THRESHOLD = 3


def consolidation_batch_enabled() -> bool:
    """Packet AA — one commit per consolidation pass.

    `on` (default, PROVISIONAL): the pass runs inside
    `TripleStore.deferred_pass()` — one fsync instead of one per promoted
    edge, and an infrastructure failure rolls the whole pass back (the
    hippocampus re-offers every edge next poll, so rollback defers, never
    loses). `off` restores pre-AA behaviour exactly: commit per row,
    including the old failure shape where rows before a mid-pass crash stay
    committed. Read at call time (the Packet D pattern).
    """
    return os.environ.get("NEXUS_CONSOLIDATION_BATCH",
                          "on").strip().lower() != "off"


def _default_recurrence_threshold() -> int:
    """Read at call time, not import (the pattern Packet D established)."""
    raw = os.environ.get("NEXUS_RECURRENCE_THRESHOLD")
    if raw is None or raw.strip() == "":
        return DEFAULT_RECURRENCE_THRESHOLD
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "NEXUS_RECURRENCE_THRESHOLD=%r is not an integer — falling back to %s",
            raw, DEFAULT_RECURRENCE_THRESHOLD)
        return DEFAULT_RECURRENCE_THRESHOLD
NEOCORTEX_SOURCE        = "hippocampus_consolidation"


class Neocortex:
    def __init__(self, db_path: Path | str = DEFAULT_DB):
        self.store = TripleStore(db_path)
        # DECLARED, NOT LOGGED (I-6). A pass that silently promotes four of
        # five is the same defect one level down from the one that aborted the
        # whole cycle — the operator needs the count, not a log line they were
        # not watching for. Reset per pass, so `count` means THIS pass rather
        # than a scar that never heals.
        self.begin_refusal_pass("init")

    # ---------- the refusal ledger ----------
    #
    # SCOPED BY AN EXPLICIT CALL, and that is the correction to how this
    # shipped. `_reset_refusals()` was called only by
    # `consolidate_from_hippocampus`, but `store_pattern_triple` writes into
    # the SAME ledger and `ProceduralMemory.promote()` never reset it. So
    # KAIROS read the ledger twice — once after consolidation, once after
    # promotion — and the second read was a RUNNING TOTAL, not the procedural
    # stage's own count.
    #
    # In steady state `promote()` writes nothing (`self.promoted` suppresses
    # re-promotion), so the second read equalled the first exactly and
    # `consolidation_refused` and `procedural_refused` both printed 5235 on
    # the operator's cycle. Two counters agreeing perfectly is not
    # corroboration; it was one counter shown twice — the same shape as the
    # `edges_promoted: 0` this packet fixed, one level down. Caught because
    # the operator did not believe the coincidence.
    #
    # `stage` is carried so the ledger says which pass it describes, and
    # KAIROS asserts the label it gets is the label it asked for.

    def begin_refusal_pass(self, stage: str) -> None:
        """Open a refusal-counting scope. Every writer of this ledger must be
        preceded by one of these, or its count belongs to someone else."""
        self.last_refusals: dict = {
            "stage": stage, "count": 0, "attempted": 0, "stored": 0,
            "first_reason": "", "first_term": "", "by_reason": {},
        }

    def _record_refusal(self, rej: "BeliefRejected") -> None:
        led = self.last_refusals
        led["count"] += 1
        key = f"{getattr(rej, 'field', '') or '?'}:{getattr(rej, 'reason', '') or '?'}"
        led["by_reason"][key] = led["by_reason"].get(key, 0) + 1
        if not led["first_reason"]:
            led["first_reason"] = str(rej)
            led["first_term"] = getattr(rej, "term", "")
        # DEBUG, NOT WARNING. The operator's hippocampus refuses 5,235 beliefs
        # per cycle on a 5-minute poll and the same nineteen surfaces recur,
        # because many edges share a target. At WARNING that is a repeating
        # wall of identical lines every five minutes, and a log nobody can
        # read is a log that hides the next finding — the KAIROS abort this
        # packet fixed was visible in exactly this stream. The per-cycle
        # summary below carries the same information in one line; this level
        # is for when the individual rows are what you actually want.
        logger.debug("belief refused during %s: %s", led["stage"], rej)

    def summarise_refusals(self) -> dict:
        """One WARNING line for the pass, or silence if nothing was refused.

        Counts by reason and the first example, because "5,235 refused" does
        not tell an operator whether they are looking at one rule biting a
        whole class or at a long tail — and those need different responses.
        Returns the ledger so the caller does not have to read it twice.
        """
        led = self.last_refusals
        if led["count"]:
            breakdown = ", ".join(
                f"{k}×{n}" for k, n in
                sorted(led["by_reason"].items(), key=lambda kv: -kv[1]))
            logger.warning(
                "%s refused %d of %d beliefs this pass [%s] — first: %s",
                led["stage"], led["count"], led["attempted"], breakdown,
                led["first_reason"])
        return led

    def close(self) -> None:
        self.store.close()

    # ---------- promotion from hippocampus ----------

    def consolidate_from_hippocampus(
        self,
        hippocampus: "Hippocampus",
        *,
        threshold: int = CONSOLIDATION_THRESHOLD,
        recurrence_threshold: int | None = None,
        relation: str = "co_activated",
    ) -> int:
        """Promote each hippocampus edge that clears BOTH stages of the gate.

        Stage 1 — intensity: `fire_count >= threshold` (7). Unchanged in
        value and meaning (I-1): this reads in-memory co-activation, never
        `TripleStore.fire()`'s retrieval-frequency counter.

        Stage 2 — recurrence (E.2): `recurrence_count >= recurrence_threshold`
        (3). An association must survive several refractory-separated
        episodes, not merely fire hard once. This is what distinguishes a
        genuine association reinforced across sessions from query scaffolding
        co-firing in a single afternoon.

        Returns the count of edges promoted. Promoted edges remain in the
        hippocampus until the next session-clear; the neocortex copy is
        permanent (subject to tier rebalancing).
        """
        if recurrence_threshold is None:
            recurrence_threshold = _default_recurrence_threshold()
        self.begin_refusal_pass("consolidation")
        promoted = 0
        # FZ-2 — the promotion entry checks the freeze. Under frozen the
        # edges that clear both gate stages are COUNTED as skipped writes
        # (the attempts the pass would have made) and none is offered to
        # `add()`; they stay in the hippocampus and are re-offered on the
        # next cycle after an unfreeze, exactly as a refused edge is.
        from backend.learning import learning_frozen, record_skip
        if learning_frozen():
            would = sum(1 for e in hippocampus.edges()
                        if e.fire_count >= threshold and e.recurrence_count >= recurrence_threshold)
            record_skip("Neocortex.consolidate_from_hippocampus", would)
            self.summarise_refusals()
            return 0
        # Packet AA — one commit per pass. OQ-4 measured the alternative:
        # 13,183 per-row fsyncs, 15.4s of held write lock per cycle, and the
        # operator's `database is locked`. The gate is untouched — a refusal
        # raises before the INSERT and the per-belief handler below counts it
        # and continues, exactly as Packet Z built it. On an infrastructure
        # failure the whole pass rolls back and is DECLARED with how much was
        # discarded (I-6); nothing is lost because every edge stays in the
        # hippocampus and is re-offered next poll.
        if consolidation_batch_enabled():
            try:
                with self.store.deferred_pass():
                    promoted = self._consolidation_loop(
                        hippocampus, threshold, recurrence_threshold, relation)
            except BaseException:
                # DECLARED (I-6): a cycle that discarded its promotions must
                # not look like one that made them. The count is what the
                # gate admitted before the failure — exactly what rolled back.
                logger.warning(
                    "consolidation pass rolled back after %d promotion(s) — "
                    "nothing was committed; the hippocampus re-offers every "
                    "edge on the next cycle", self.last_refusals["stored"])
                raise
        else:
            # Pre-AA behaviour exactly: per-row commits, and a mid-pass
            # failure leaves the rows already written committed.
            promoted = self._consolidation_loop(
                hippocampus, threshold, recurrence_threshold, relation)
        self.summarise_refusals()
        return promoted

    def _consolidation_loop(self, hippocampus, threshold,
                            recurrence_threshold, relation) -> int:
        promoted = 0
        for edge in list(hippocampus.edges()):
            if edge.fire_count < threshold:
                continue
            if edge.recurrence_count < recurrence_threshold:
                continue
            self.last_refusals["attempted"] += 1
            try:
                self.store.add(
                    subject=edge.source,
                    relation=relation,
                    object=edge.target,
                    weight=edge.weight,
                    tier=1,                          # promoted edges enter Tier 1 active
                    granularity=Granularity.SESSION, # survived a session of co-activation
                    source=NEOCORTEX_SOURCE,
                )
            except BeliefRejected as rej:
                # PER BELIEF, NOT PER PASS. `add()` raises on refusal (Packet
                # K), and this call is the FIRST statement of KAIROS's cycle
                # body — so an unguarded raise took consolidation, rebalance,
                # decay, procedural promotion, the dedup scan, semantic
                # indexing, the graph persist, the snapshot and the governance
                # seal with it. F.2's blanket handler then logged it and kept
                # the daemon alive, and the offending edge stays in the
                # hippocampus, so the next poll retried it. Forever.
                #
                # Measured live on the operator's machine: `too_many_tokens`
                # on a 12-token target against a cap of 8, every cycle, for
                # weeks, while the system reported itself healthy.
                #
                # The pattern is `agentic_loop.py:686`'s, which has been right
                # since O.2b. It was never applied here.
                self._record_refusal(rej)
                continue
            promoted += 1
            self.last_refusals["stored"] += 1
        return promoted

    def store_pattern_triple(
        self,
        subject: str, relation: str, object: str,
        *, weight: float = 0.7, source: str = "procedural_promotion",
        granularity: Granularity = Granularity.TOPIC,
    ) -> "int | None":
        """Returns the belief id, or None when the gate refused it.

        FIXED IN THE SAME COMMIT AS `consolidate_from_hippocampus`, and that
        is deliberate. `ProceduralMemory.promote()` calls this in a nested
        loop over patterns and their terms, and the terms come from user
        queries — so a long query term hits the same gate the same way. It had
        never been SEEN only because consolidation aborts first, three lines
        earlier in the same cycle body. Fixing one alone would have surfaced
        the other on the operator's next poll, reading as a new defect rather
        than the same one.

        Returning None rather than raising keeps `promote()`'s loop intact
        without asking it to know about the gate; the refusal is counted here,
        where the store call is.
        """
        # FZ-2 — the procedural half of the promotion entry: frozen counts
        # the write it would have made and returns None, the same value the
        # caller's loop already handles for a gate refusal.
        from backend.learning import learning_frozen, record_skip
        if learning_frozen():
            record_skip("Neocortex.store_pattern_triple", 1)
            return None
        self.last_refusals["attempted"] += 1
        try:
            return self.store.add(
                subject=subject, relation=relation, object=object,
                weight=weight, tier=1, granularity=granularity, source=source,
            )
        except BeliefRejected as rej:
            self._record_refusal(rej)
            return None

    # ---------- read-through to underlying store ----------

    def count(self, **kw) -> int:
        return self.store.count(**kw)

    def all(self):
        return self.store.all()
