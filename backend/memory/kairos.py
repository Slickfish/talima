"""KAIROS — background asyncio consolidation daemon (arch §7.2).

Idle-only operations: consolidation (hippocampus -> neocortex), tier
rebalancer, decay pass, procedural promotion. Phase 9 adds snapshot +
fingerprint + chain append. The daemon is always idle until session_active
is flipped to True by the agentic loop entering a turn.

Default poll interval is 5 minutes; tests bypass with run_one_cycle().
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, TYPE_CHECKING

from backend.memory import seal as _seal

if TYPE_CHECKING:
    from backend.cortex.hebbian import HebbianUpdater
    from backend.memory.chain import ArchiveBlock, NexusArchiveChain
    from backend.memory.fingerprint import CortexFingerprint, CortexFingerprinter
    from backend.memory.hippocampus import Hippocampus
    from backend.memory.lineage import LineageStore
    from backend.memory.neocortex import Neocortex
    from backend.memory.procedural import ProceduralMemory


POLL_INTERVAL_SECONDS  = 300   # arch §7.2 — 5 minutes
# arch §7.2 chose 30 days as a point-in-time *structural report* cadence — a
# process decision, not a system requirement. The same mechanism now serves a
# second purpose: it carries the P3 governance seal for lineage records
# (maybe_snapshot_and_fingerprint), which wants to be frequent — a 30-day
# unsealed window is long for a substrate whose claim is tamper-evident
# accountability. Those two needs are NOT yet separated; this is a known
# design question, not a magic number. Configurable so a deployment can
# choose without a code change (D.5).
SNAPSHOT_INTERVAL_DAYS = 30
DAY_SECONDS            = 86400.0

# ---------------------------------------------------------------------------
# S.2b — the governance seal is NOT the snapshot cadence
# ---------------------------------------------------------------------------
# The comment above used to end "those two needs are NOT yet separated; this is
# a known design question, not a magic number." S.2 made it load-bearing: a
# recalibration could move a weight and sit unsealed for thirty days, which
# undercuts the one claim S.2 exists to make — that no weight moved outside a
# limit, PROVABLY. An unsealed record proves nothing.
#
# Two purposes, now two mechanisms:
#
#   snapshot    periodic structural report of the hippocampus. The operator's
#               original intent; monthly is fine and unchanged.
#   governance  digest seal over lineage and recalibration records. Wants to
#               be prompt, carries no snapshot, and rides its own block.
#
# The cadence is "every idle cycle that has something to seal", bounded below
# by GOVERNANCE_SEAL_INTERVAL_MINUTES so a deployment that cares about block
# volume can slow it without touching code. With the default 5-minute poll
# that makes the worst-case unsealed window one poll interval instead of
# thirty days — a ~8,600x reduction, and the number that matters is that it is
# bounded by something an operator chose.
#
# MODE `snapshot` reproduces pre-S.2b behaviour EXACTLY: governance records
# seal only when a snapshot does. That is the rollback (E.2, Q.2 precedent).
GOVERNANCE_SEAL_MODES            = ("prompt", "snapshot", "off")
DEFAULT_GOVERNANCE_SEAL_MODE     = "prompt"      # PROVISIONAL
GOVERNANCE_SEAL_INTERVAL_MINUTES = 0.0           # PROVISIONAL: no floor
# W8 (D6) — records whose seal claims no root (sealed before W8, or a layout-2
# seal of the bare hash) bound per seal, at most: the binding of a
# store's whole history rides as many seals as it needs, each block bounded.
BINDING_BATCH = _seal.BINDING_BATCH               # PROVISIONAL: a size, fitted to nothing (the seal module's)

logger = logging.getLogger("nexus.kairos")


def _default_snapshot_interval_days() -> float:
    """Read at construction time so a deployment/test can set the env var
    after import. Falls back to the arch §7.2 monthly cadence."""
    raw = os.environ.get("NEXUS_SNAPSHOT_INTERVAL_DAYS")
    if raw is None or raw.strip() == "":
        return float(SNAPSHOT_INTERVAL_DAYS)
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "NEXUS_SNAPSHOT_INTERVAL_DAYS=%r is not a number — falling back to %s",
            raw, SNAPSHOT_INTERVAL_DAYS)
        return float(SNAPSHOT_INTERVAL_DAYS)


def governance_seal_mode() -> str:
    """Read NEXUS_GOVERNANCE_SEAL at call time. Unrecognised values warn and
    fall back — a typo in a governance knob must not stop the daemon, and must
    not pass unremarked either (I-6)."""
    raw = os.environ.get("NEXUS_GOVERNANCE_SEAL", "").strip().lower()
    if not raw:
        return DEFAULT_GOVERNANCE_SEAL_MODE
    if raw not in GOVERNANCE_SEAL_MODES:
        logger.warning("NEXUS_GOVERNANCE_SEAL=%r is not one of %s; using %r",
                       raw, GOVERNANCE_SEAL_MODES, DEFAULT_GOVERNANCE_SEAL_MODE)
        return DEFAULT_GOVERNANCE_SEAL_MODE
    return raw


def _governance_interval_seconds() -> float:
    raw = os.environ.get("NEXUS_GOVERNANCE_SEAL_INTERVAL_MINUTES", "").strip()
    if not raw:
        return GOVERNANCE_SEAL_INTERVAL_MINUTES * 60.0
    try:
        return max(0.0, float(raw)) * 60.0
    except ValueError:
        logger.warning(
            "NEXUS_GOVERNANCE_SEAL_INTERVAL_MINUTES=%r is not a number — "
            "falling back to %s", raw, GOVERNANCE_SEAL_INTERVAL_MINUTES)
        return GOVERNANCE_SEAL_INTERVAL_MINUTES * 60.0


def _merge_counts(*ds: dict) -> dict:
    out: dict = {}
    for d in ds:
        for k, n in d.items():
            out[k] = out.get(k, 0) + n
    return out


def _default_engine():
    """AX.1 — a KAIROS constructed without services (tests, scripts) still
    runs the pass; with services bound it uses the app's engine."""
    from backend.cortex.curiosity import CuriosityEngine
    return CuriosityEngine()


@dataclass
class KairosCycleResult:
    consolidated: int = 0
    promoted_patterns: int = 0
    rebalanced: dict = field(default_factory=dict)
    decayed: int = 0
    skipped: bool = False
    skipped_reason: str = ""
    timestamp: float = 0.0
    # Phase 9 — fingerprint + archive chain summary, when a snapshot ran.
    snapshot_taken: bool = False
    snapshot_path: str = ""
    archive_block_index: int | None = None
    archive_block_hash: str = ""
    # Fix A — count of near-duplicate candidates surfaced this cycle.
    dedup_candidates: int = 0
    # D.1 (Law 8) — why no snapshot was taken. "" when one was.
    # Vocabulary: "not_due" | "unwired" | "graph_empty" | "error".
    # Without this, all three skip paths emitted an identical
    # snapshot_taken=false / snapshot_path="" and were indistinguishable.
    snapshot_skipped_reason: str = ""
    # D.1.3 — a dedup scan that threw and returned [] must not look like a
    # scan that legitimately found nothing.
    dedup_scan_failed: bool = False
    dedup_error: str = ""
    # W3 (H5) — "" when the scan ran; else why it was not asked to: the
    # provider declares it has no embedder (`skipped: <its reason>`), so a
    # scan would be one declared fallback per triple and no information.
    dedup_skipped_reason: str = ""
    # W.1 — beliefs given a semantic embedding this cycle. Zero is the normal
    # steady state (everything indexed) AND the normal disabled state, so the
    # error field is what distinguishes "nothing to do" from "could not".
    semantic_indexed: int = 0
    semantic_index_error: str = ""
    # W3 (H5) — the same declaration, for the indexer: "" when it ran or had
    # nothing to do; else why nothing was indexed (hash vectors would be).
    semantic_index_skipped_reason: str = ""
    # AX.1 — the curiosity pass, on this cycle. A count of questions
    # surfaced and the node ids they concern; `None` on an aborted cycle,
    # because an aborted cycle did not look (the `edges_promoted` rule).
    # 0 with `curiosity_top == []` means the pass ran and found nothing —
    # or placement is `turn`, in which case the idle host is a no-op and
    # `curiosity_placement` says so.
    curiosity_surfaced: "int | None" = 0
    curiosity_top: list = field(default_factory=list)
    curiosity_placement: str = ""
    # Beliefs the gate refused during this cycle, DECLARED rather than logged
    # (I-6). A pass that silently promotes four of five is the same defect one
    # level down from the one that aborted the whole cycle. `None` on an
    # aborted cycle, for the reason `edges_promoted` is.
    consolidation_refused: "int | None" = 0
    procedural_refused: "int | None" = 0
    refusal_first_reason: str = ""
    # Counts by rule, on the SURFACE and not only in the log (I-6). "5,235
    # refused" does not distinguish one rule biting a whole class from a long
    # tail, and those need different responses; the operator should not have
    # to scrape a console to tell them apart.
    refusal_by_reason: "dict | None" = field(default_factory=dict)
    # S.2b — the governance seal, declared separately from the snapshot so an
    # operator can see which mechanism did or did not run (I-6).
    #
    # U.1 — THESE THREE ARE ABOUT THE CADENCE, NOT ABOUT THE SEAL, and reading
    # them as "was anything sealed" is what produced a contradiction on the
    # operator's store: `/kairos/cycle` forces a snapshot by default, the
    # snapshot path seals whatever is pending (S.2's design, deliberately),
    # and then `maybe_seal_governance()` correctly finds nothing left and
    # reports `None` / `0` / "nothing_pending". Three records had just been
    # sealed. A governance action reported as not taken is exactly the I-6
    # defect the reporting exists to prevent.
    #
    # `sealed_records` / `sealed_block_index` / `sealed_block_kind` answer the
    # question an operator is actually asking — WAS IT SEALED, AND WHERE —
    # whichever block carried it.
    governance_block_index: int | None = None
    governance_sealed_records: int = 0
    governance_skipped_reason: str = ""
    sealed_records: int = 0
    sealed_block_index: int | None = None
    sealed_block_kind: str = ""
    # F.2 — set when the cycle body raised. Empty on a healthy cycle; the
    # daemon keeps running either way, so this is the only place a caller can
    # see that a cycle was lost (I-6).
    cycle_error: str = ""
    # D.2 — honest counter names. `consolidated` counts hippocampus EDGES
    # promoted to store rows; `decayed` is a pre-prune CENSUS of in-memory
    # Hebbian edges visited, not a count of things that changed. Both old
    # keys are retained for one release as deprecated aliases (P2 pattern).
    # `None` means NOT MEASURED, and the distinction is the whole point.
    #
    # This was `int = 0`, and on an aborted cycle the handler built a result
    # from dataclass defaults — so `edges_promoted` read `0`, which is a
    # CLAIM. OQ-1 took that claim as evidence that consolidation had never
    # fired under the post-Packet-E mechanism and gated a decision on it. The
    # cycle had been aborting at its first statement for weeks; the gate was
    # unreachable, not unmet.
    #
    # A default presenting itself as a measurement — the same class as U.1's
    # seal reporting itself unset while three records had just been sealed.
    # The abort path has no right to say "none were promoted".
    edges_promoted: "int | None" = 0
    hebbian_edges_visited: int = 0
    counters_deprecated: bool = True


class KairosProcess:
    def __init__(
        self,
        hippocampus: "Hippocampus",
        neocortex: "Neocortex",
        hebbian: "HebbianUpdater",
        procedural: "ProceduralMemory",
        *,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        # Phase 9 collaborators — optional so older callers / tests still work.
        fingerprinter: "CortexFingerprinter | None" = None,
        archive_chain: "NexusArchiveChain | None" = None,
        snapshots_dir: "Path | str | None" = None,
        # D.5 — None means "read NEXUS_SNAPSHOT_INTERVAL_DAYS at construction
        # time, else the arch §7.2 default". An explicit value always wins
        # (0 is honoured, so tests can force "always due").
        snapshot_interval_days: float | None = None,
        # P3 / L2 — lineage store to digest-seal per-turn answer records.
        lineage: "LineageStore | None" = None,
        # S.2 — governance record (envelopes, outcomes, recalibrations),
        # sealed on the same path and in the same block as lineage records.
        recalibration: "RecalibrationStore | None" = None,
        # E.3 — persistent association graph + the lock shared with the chat
        # path. Both are optional so bare KairosProcess callers still work.
        graph_store: "HebbianGraphStore | None" = None,
        graph_lock: "asyncio.Lock | None" = None,
    ):
        self.hippocampus = hippocampus
        self.neocortex   = neocortex
        self.hebbian     = hebbian
        self.procedural  = procedural
        self.poll_interval = poll_interval
        self.fingerprinter         = fingerprinter
        self.archive_chain         = archive_chain
        self.snapshots_dir         = (
            Path(snapshots_dir) if snapshots_dir else Path("data/snapshots")
        )
        self.snapshot_interval_days = (
            snapshot_interval_days if snapshot_interval_days is not None
            else _default_snapshot_interval_days()
        )
        self.lineage = lineage
        self.recalibration = recalibration
        self.graph_store = graph_store
        self.graph_lock = graph_lock
        # D.1 — set by maybe_snapshot_and_fingerprint at every return point so
        # run_one_cycle can declare *why* no block was appended.
        self.last_snapshot_skip_reason: str = ""
        # S.2b — separate cadence, separate bookkeeping.
        self.last_governance_seal_ts: float = 0.0
        self.last_governance_skip_reason: str = ""
        # U.1 — set by WHICHEVER path sealed, so the cycle can report the act
        # rather than the mechanism. Reset at the top of every cycle.
        self.last_seal: dict = {"records": 0, "block_index": None, "kind": ""}
        self.last_binding: dict = {"records": 0, "block_index": None}
        self.session_active: bool = False
        self.last_cycle: KairosCycleResult | None = None
        self.last_snapshot_block: "ArchiveBlock | None" = None
        self._task: asyncio.Task | None = None
        # Lazy: bound to whichever event loop calls .start(). Created here
        # eagerly would tie the Event to a loop that may not be the running one
        # (tests create one loop per TestClient).
        self._stop: asyncio.Event | None = None
        # Pre-Phase-10c governance: notify_ui hook + services back-ref.
        # KAIROS runs on a background asyncio task — there is no live SSE
        # channel to push to. The default hook is a no-op; create_services()
        # rebinds it to append onto NexusServices.pending_alerts. The
        # agentic loop drains that queue at turn-start and emits the SSEs
        # over the live /chat connection.
        self.notify_ui: Optional[Callable[[dict], Awaitable[None]]] = None
        self.consecutive_drift_warns: int = 0
        self._services_ref: "Any" = None

    # ---------- services back-ref (governance fixes A/B) ----------

    def bind_services(self, services: "Any") -> None:
        """Late-bind a NexusServices reference. Constructed-after-services to
        avoid the circular dep at NexusServices instantiation time. Fix A
        and Fix B's KAIROS-side hooks reach `self.services.deduplicator`
        and `self.services.last_dedup_candidates` via this binding."""
        self._services_ref = services

    @property
    def services(self) -> "Any":
        if self._services_ref is None:                # pragma: no cover
            raise RuntimeError(
                "KairosProcess.services accessed before bind_services()",
            )
        return self._services_ref

    # ---------- session gating ----------

    @property
    def is_idle(self) -> bool:
        """True iff no foreground turn is in flight — KAIROS only runs while idle."""
        return not self.session_active

    def session_started(self) -> None:
        self.session_active = True

    def session_ended(self) -> None:
        self.session_active = False

    # ---------- one cycle ----------

    async def run_one_cycle(self, *, force_snapshot: bool = False) -> KairosCycleResult:
        """Run one consolidation cycle. **Never raises.**

        F.2 — this is C10 one level up. `rebalance()` raising `KeyError: 4` on
        a single dedup-archived row propagated out of here and out of `run()`,
        which awaits it outside any handler: the daemon task died with an
        exception nobody retrieved, and consolidation, decay, procedural
        promotion, dedup scanning, snapshotting and lineage sealing all
        stopped silently for the life of the process. Measured.

        A background daemon that can be killed by one row is not a daemon.
        The cycle now survives, declares the failure in the result an operator
        reads (Law 8 / I-6), and logs at ERROR — a dead governance loop is
        more serious than a skipped snapshot, which is why this is not WARNING.
        """
        if not self.is_idle:
            self.last_cycle = KairosCycleResult(
                skipped=True, skipped_reason="session_active",
                timestamp=time.time(),
            )
            return self.last_cycle

        try:
            return await self._run_cycle_body(force_snapshot)
        except Exception as e:
            # NOT MEASURED, so not reported as a number. See the note on
            # `edges_promoted`.
            self.last_cycle = KairosCycleResult(
                skipped=True,
                skipped_reason="error",
                cycle_error=f"{type(e).__name__}: {e}",
                timestamp=time.time(),
                edges_promoted=None,
                consolidation_refused=None,
                procedural_refused=None,
                # `{}` would read as "nothing was refused", which is the same
                # claim `0` made. An aborted cycle did not count.
                refusal_by_reason=None,
                curiosity_surfaced=None,
            )
            logger.error("KAIROS cycle failed, daemon continues: %s: %s",
                         type(e).__name__, e, exc_info=True)
            return self.last_cycle

    async def _run_cycle_body(self, force_snapshot: bool) -> KairosCycleResult:
        """Body of one cycle, separated so F.2's handler can wrap it."""
        # U.1 — per-cycle, so `sealed_records` reports THIS cycle rather than
        # the last one that happened to seal something.
        self.last_seal = {"records": 0, "block_index": None, "kind": ""}
        consolidated   = self.neocortex.consolidate_from_hippocampus(self.hippocampus)
        # Read immediately: the ledger is re-scoped for the procedural pass a
        # few lines down, and both passes write through the same neocortex.
        _consol_ref    = dict(self.neocortex.last_refusals)
        rebalanced     = self.neocortex.store.rebalance()
        decayed        = self.hebbian.decay_pass(self.hippocampus, days=1.0 / 24.0)
        # OPEN THE SCOPE HERE, because `ProceduralMemory.promote()` does not
        # own the ledger and cannot. Without this the second read was
        # consolidation's count plus procedural's, and in steady state
        # `promote()` writes nothing — so the two counters printed the same
        # number and looked like corroboration. They are independent now, and
        # `stage` is asserted below so a future reordering cannot re-merge
        # them silently.
        self.neocortex.begin_refusal_pass("procedural")
        promoted       = self.procedural.promote(self.neocortex)
        _proc_ref      = dict(self.neocortex.summarise_refusals())
        # NOT an assert. An assert here would raise inside the cycle body, and
        # a raise inside the cycle body is the exact defect this packet
        # closed — the guard belongs in the suite, where failing is free.
        # `tests/test_ingest_refusal_class.py` pins the two stages independent
        # and pins the labels.
        _first_reason  = (_consol_ref["first_reason"]
                          or _proc_ref["first_reason"])

        # H.3b — `kairos_consolidation {promoted}` completes the same contract
        # gap. Emitted on EVERY completed cycle, including `promoted: 0`.
        #
        # The first draft fired only when something was promoted, reasoning
        # that a zero every cycle is noise. That was wrong, and wrong in the
        # specific way this packet exists to fix: after E.2/E.3, promotion
        # requires three refractory-separated recurrences, so on a real store
        # `edges_promoted` reads 0 for a long time — and a frame that only
        # fires on a non-zero would therefore never fire at all, leaving the
        # contract member exactly as dead as it was before. Zero is a
        # measurement, not an absence: "the cycle ran and promoted nothing"
        # is what a reviewer needs to see, and is different from silence.
        #
        # I-7: the field name matches the counter Packet D renamed
        # (`edges_promoted`), not the older, wider "consolidated".
        if self.notify_ui is not None:
            try:
                await self.notify_ui({
                    "event":    "kairos_consolidation",
                    "promoted": consolidated,
                })
            except Exception as e:
                logger.warning("kairos_consolidation notify failed: %s: %s",
                               type(e).__name__, e)

        # Fix A — semantic deduplication scan. Idle-only by virtue of being
        # inside this method (the is_idle guard above gates the whole cycle).
        # Flags candidates only — never auto-collapses (Law 3).
        dedup_count = 0
        dedup_failed = False
        dedup_error = ""
        # W3 (H5) — a provider that DECLARES it has no embedder (`has_embedder`
        # False: the stub; an api backend naming none) is not asked: the scan
        # embeds once per triple, up to 200 a cycle, so every call would be a
        # declared fallback and an `api` instance read degraded on every idle
        # cycle for no information (the establish measured 12 triples, 12
        # fallbacks). Skipped and said, on the result and on
        # `/cortex/stats.embedder`. A provider that declares nothing is asked
        # as before, and a failure is still the chokepoint's to declare.
        from backend.vector.embeddings import embedder_declaration
        embedder = embedder_declaration(getattr(self._services_ref, "provider", None))
        dedup_skipped_reason = "" if embedder["has_embedder"] else f"skipped: {embedder['reason']}"
        if self._services_ref is not None and getattr(
            self._services_ref, "deduplicator", None,
        ) is not None and not dedup_skipped_reason:
            try:
                candidates = await self._services_ref.deduplicator.scan()
            except Exception as e:
                # D.1 — the cycle must not die on a dedup scan failure, but
                # Law 8 forbids swallowing silently: declare what was lost,
                # in the result as well as the log.
                candidates = []
                dedup_failed = True
                dedup_error = f"{type(e).__name__}: {e}"
                logger.warning("KAIROS dedup scan failed: %s", dedup_error)
            if candidates:
                # Replace in-place so endpoint readers holding the same list
                # ref see the new candidates (Law 1 spirit — atomic swap).
                self._services_ref.last_dedup_candidates.clear()
                self._services_ref.last_dedup_candidates.extend(candidates)
                dedup_count = len(candidates)
                if self.notify_ui is not None:
                    await self.notify_ui({
                        "event":  "kb_pressure_alert",
                        "type":   "semantic_duplicates",
                        "domain": "kb",
                        "action": "review_required",
                        "count":  dedup_count,
                    })

        # W.1 — semantic indexing. Idle-only, batched, and OFF unless the
        # channel is on: with NEXUS_SEMANTIC_SLOTS=0 nothing is embedded, so
        # installing W.1 adds no provider traffic to a cycle at all.
        #
        # Deliberately NOT on the turn: embedding is one provider call per
        # belief, extraction produces several beliefs per turn, and the vector
        # is not needed until the NEXT question. KAIROS already owns a
        # per-cycle budget and only runs when the session is idle.
        semantic_indexed = 0
        semantic_index_error = ""
        semantic_index_skipped_reason = ""
        _svc = self._services_ref
        if (_svc is not None
                and getattr(_svc, "semantic", None) is not None
                and getattr(_svc, "provider", None) is not None):
            try:
                from backend.kb.semantic_index import (
                    index_pending, semantic_slots)
                if semantic_slots() > 0 and dedup_skipped_reason:
                    # W3 (H5) — the same declaration: a provider with no
                    # embedder would have every belief indexed on a hash
                    # vector, written to kb.db. Nothing is indexed, and said.
                    semantic_index_skipped_reason = dedup_skipped_reason
                elif semantic_slots() > 0:
                    result = await index_pending(_svc.semantic, _svc.provider)
                    semantic_indexed = int(result["indexed"])
                    semantic_index_error = result["error"]
            except Exception as e:
                # Same rule as the dedup scan (D.1): the cycle survives, and
                # the failure is declared rather than swallowed.
                semantic_index_error = f"{type(e).__name__}: {e}"
                logger.warning("KAIROS semantic indexing failed: %s",
                               semantic_index_error)

        # Packet AB — lay relation trails from lineage records newer than the
        # cursor. Idle-cycle for the same reason semantic indexing is: the
        # records already exist, nothing about a live turn depends on the
        # trail being laid now, and the scan is one read pass plus one
        # transaction (AA). Gated inside scan_new() by NEXUS_RELATION_TRAILS;
        # history older than the cursor is only entered via the Law 3
        # backfill. Failure is declared and never fatal to the cycle (D.1).
        if (_svc is not None and getattr(_svc, "trails", None) is not None):
            try:
                _svc.trails.scan_new()
            except Exception as e:
                logger.warning("KAIROS trail laying failed: %s: %s",
                               type(e).__name__, e)

        # AX.1 — the curiosity pass, AFTER decay and demotion have settled
        # the graph it reads and BEFORE the persist, so what is surfaced is
        # what this cycle leaves behind. Enqueued through notify_ui like
        # every other idle-time signal; the next /chat bridges it as
        # `curiosity_surfaced`. Read-only over the in-memory graph.
        from backend.cortex.curiosity_placement import (
            curiosity_placement, idle_curiosity)
        _engine = (getattr(self._services_ref, "curiosity", None)
                   if self._services_ref is not None else None) or _default_engine()
        curious = idle_curiosity(_engine, self.hippocampus)
        if self.notify_ui is not None:
            for cn in curious:
                try:
                    await self.notify_ui({"event": "curiosity_surfaced",
                                          **cn.to_dict()})
                except Exception as e:
                    logger.warning("curiosity_surfaced notify failed: %s: %s",
                                   type(e).__name__, e)

        # E.3 — persist the association graph this cycle just decayed and
        # demoted, under the lock shared with the chat path. Declared on
        # failure (Law 8), never fatal to the cycle.
        if self.graph_store is not None:
            try:
                if self.graph_lock is not None:
                    async with self.graph_lock:
                        self.graph_store.save(self.hippocampus)
                else:
                    self.graph_store.save(self.hippocampus)
            except Exception as e:
                logger.warning("KAIROS association-graph persist failed: %s: %s",
                               type(e).__name__, e)

        # Phase 9 — fingerprint + chain append on monthly cadence.
        snap_block = await self.maybe_snapshot_and_fingerprint(force=force_snapshot)
        snap_path  = snap_block.snapshot_path if snap_block else ""

        # S.2b — the governance seal, AFTER the snapshot and on its own
        # cadence. After, so that when both fire in one cycle the snapshot
        # picks up what is pending and the governance pass finds nothing left
        # rather than minting an empty block. Before would produce two blocks
        # for one set of records and a snapshot block with no seal, which is
        # the more confusing artefact to hand an auditor.
        gov_block = await self.maybe_seal_governance()
        # W9 (D7) — after the seal, so the block it just appended is stamped
        # on this cycle or the next; off by default, and never on a turn.
        await self.maybe_anchor()

        self.last_cycle = KairosCycleResult(
            # D.2 — new honest names first; the old keys carry identical
            # values as deprecated aliases for one release.
            edges_promoted=consolidated,
            consolidation_refused=_consol_ref["count"],
            procedural_refused=_proc_ref["count"],
            refusal_first_reason=_first_reason,
            # SUMMED, not merged. Both stages produce keys of the same shape
            # (`object:too_many_tokens`), so `{**a, **b}` would silently drop
            # consolidation's count whenever the two agreed on a rule — a
            # smaller number than actually happened, which is the one kind of
            # error a refusal report must not make.
            refusal_by_reason=_merge_counts(_consol_ref["by_reason"],
                                            _proc_ref["by_reason"]),
            hebbian_edges_visited=decayed,
            consolidated=consolidated,
            decayed=decayed,
            promoted_patterns=promoted,
            rebalanced=rebalanced,
            skipped=False,
            timestamp=time.time(),
            snapshot_taken=snap_block is not None,
            snapshot_path=snap_path,
            snapshot_skipped_reason=(
                "" if snap_block is not None else self.last_snapshot_skip_reason
            ),
            archive_block_index=snap_block.index if snap_block else None,
            archive_block_hash=snap_block.block_hash if snap_block else "",
            governance_block_index=gov_block.index if gov_block else None,
            governance_sealed_records=(
                (gov_block.fingerprint.get("lineage_seal", {}).get("count", 0)
                 + gov_block.fingerprint.get("recalibration_seal", {}).get("count", 0))
                if gov_block else 0),
            governance_skipped_reason=(
                "" if gov_block is not None else self.last_governance_skip_reason),
            # U.1 — the act, whichever block carried it.
            sealed_records=self.last_seal["records"],
            sealed_block_index=self.last_seal["block_index"],
            sealed_block_kind=self.last_seal["kind"],
            dedup_candidates=dedup_count,
            dedup_scan_failed=dedup_failed,
            dedup_error=dedup_error,
            dedup_skipped_reason=dedup_skipped_reason,
            semantic_indexed=semantic_indexed,
            semantic_index_error=semantic_index_error,
            semantic_index_skipped_reason=semantic_index_skipped_reason,
            curiosity_surfaced=len(curious),
            curiosity_top=[cn.node_id for cn in curious],
            curiosity_placement=curiosity_placement(),
        )
        return self.last_cycle

    # ---------- S.2b: the governance seal, on its own cadence ----------

    def _unsealed_lineage(self) -> list:
        return _seal.unsealed_lineage(self.lineage)

    def _unsealed_recalibrations(self) -> list:
        return _seal.unsealed_recalibrations(self.recalibration)

    @staticmethod
    def _seal_payload(lineage_unsealed: list, recal_unsealed: list, lineage_leaves: list | None = None,
                      bindings: list | None = None, held: list | None = None,
                      acts: list | None = None) -> dict:
        """The seal keys for one block. One builder, used by both paths, and since W10c homed in
        `backend/memory/seal.py` (`seal_payload`), where a recorder with no memory engine builds the same block."""
        return _seal.seal_payload(lineage_unsealed, recal_unsealed, lineage_leaves, bindings, held, acts)

    def _unsealed_commitments(self) -> list:
        """W9 (D8) — never raises: a seal that cannot read them seals the rest."""
        return _seal.unsealed_commitments(self.lineage)

    def _unsealed_actions(self) -> list:
        """W10b (D6) — never raises: a seal that cannot read them seals the rest."""
        return _seal.unsealed_actions(self.lineage)

    def _mark_actions_sealed(self, acts: list, block_index: int) -> int:
        return _seal.mark_actions_sealed(self.lineage, acts, block_index)

    def _mark_commitments_sealed(self, held: list, block_index: int) -> int:
        return _seal.mark_commitments_sealed(self.lineage, held, block_index)

    def _unbound_lineage(self) -> list:
        """W8 (D6) — the records whose seal claims no root (sealed before W8,
        or a layout-2 seal of the bare hash) and whose root, rebuilt from
        their values, no binding has sealed yet, at most BINDING_BATCH; [] with no store or no chain. Never raises:
        a seal that cannot read them still seals everything else."""
        return _seal.unbound_lineage(self.lineage, self.archive_chain, limit=BINDING_BATCH)

    def _mark_bound(self, bindings: list, block_index: int) -> int:
        n = _seal.mark_bound(self.lineage, bindings, block_index)
        if n:
            self.last_binding = {"records": n, "block_index": block_index}
            logger.info("bound %d record(s) whose seal claims no root in block %s", n, block_index)
        return n

    def _lineage_leaves(self, lineage_unsealed: list) -> list | None:
        """W8 — the seal's leaves, from the lineage store; None with no store."""
        return _seal.lineage_leaves(self.lineage, lineage_unsealed)

    def _mark_sealed(self, lineage_unsealed: list, recal_unsealed: list,
                     block_index: int, kind: str = "") -> int:
        n = _seal.mark_sealed(self.lineage, self.recalibration, lineage_unsealed, recal_unsealed, block_index)
        # U.1 — record the ACT. Both callers pass through here, so a seal
        # cannot happen without the cycle being able to report it.
        if n:
            self.last_seal = {"records": n, "block_index": block_index,
                              "kind": kind}
            logger.info("sealed %d governance record(s) into %s block %s",
                        n, kind or "?", block_index)
        return n

    def _governance_gate(self, mode: str, pending: int, now: float) -> str:
        """"" to proceed, else the declared skip reason (D.1 / Law 8).

        Vocabulary: "unwired" | "mode_snapshot" | "mode_off" | "nothing_pending"
        | "not_due".
        """
        if self.archive_chain is None:
            logger.warning(
                "KAIROS has no archive_chain: governance records can never be "
                "sealed. A weight that moves without a seal is exactly what "
                "S.2 exists to prevent.")
            return "unwired"
        if mode == "off":
            return "mode_off"
        if mode == "snapshot":
            # Pre-S.2b behaviour: seals ride snapshots and nothing else.
            return "mode_snapshot"
        if pending == 0:
            return "nothing_pending"
        interval = _governance_interval_seconds()
        if interval > 0 and (now - self.last_governance_seal_ts) < interval:
            return "not_due"
        return ""

    async def maybe_anchor(self, *, client=None) -> dict:
        """W9 (D7) — ask the configured RFC 3161 authority to stamp the signed
        blocks no receipt covers yet. Never raises; counted on
        `/cortex/stats.anchoring`."""
        from backend.memory import anchor as _anchor
        stats = self.__dict__.setdefault("anchor_stats", {"anchored_since_start": 0, "failures_since_start": 0,
                                                          "last_error": None, "last_attempt": None})
        if _anchor.anchor_mode() != "rfc3161" or self.archive_chain is None:
            return {"anchored": 0, "failed": 0, "error": None}
        try:
            out = await _anchor.anchor_pending(self.archive_chain, client=client)
        except Exception as e:
            out = {"anchored": 0, "failed": 1, "error": f"{type(e).__name__}: {e}"}
        stats["anchored_since_start"] += out["anchored"]
        stats["failures_since_start"] += out["failed"]
        stats["last_attempt"] = time.time()
        if out["error"]:
            stats["last_error"] = out["error"]
            logger.warning("KAIROS anchoring: %s", out["error"])
        return out

    async def maybe_seal_governance(self, *, force: bool = False):
        """Append a GOVERNANCE block covering everything currently unsealed.

        No snapshot, no fingerprint. The block carries only the seal keys and
        its chain link, which is the whole of what a governance seal needs:
        `verify_sealed_lineage` resolves `sealed_block -> get_block(index)` and
        checks the record hash against `record_hashes`, and `verify_chain()`
        recomputes stored fields and never re-reads a snapshot file — so a
        block with no snapshot verifies exactly as any other does.

        Never raises. A governance seal that kills the daemon would take the
        rest of consolidation with it (F.2).
        """
        mode = governance_seal_mode()
        lineage_unsealed = self._unsealed_lineage()
        recal_unsealed = self._unsealed_recalibrations()
        bindings = self._unbound_lineage()
        held = self._unsealed_commitments()
        acts = self._unsealed_actions()
        pending = len(lineage_unsealed) + len(recal_unsealed) + len(bindings) + len(held) + len(acts)
        now = time.time()

        reason = "" if force and self.archive_chain is not None else \
            self._governance_gate(mode, pending, now)
        if force and pending == 0:
            reason = "nothing_pending"
        if reason:
            self.last_governance_skip_reason = reason
            return None
        self.last_governance_skip_reason = ""

        try:
            payload = {"kind": "governance"}
            payload.update(self._seal_payload(lineage_unsealed, recal_unsealed,
                                              self._lineage_leaves(lineage_unsealed), bindings, held, acts))
            block = self.archive_chain.append(payload, "", kind="governance")
            self._mark_sealed(lineage_unsealed, recal_unsealed, block.index,
                              "governance")
            self._mark_bound(bindings, block.index)
            self._mark_commitments_sealed(held, block.index)
            self._mark_actions_sealed(acts, block.index)
            self.last_governance_seal_ts = now
            logger.info("governance seal: block %s covers %d record(s)",
                        block.index, pending)
            return block
        except Exception as e:
            self.last_governance_skip_reason = "error"
            logger.warning("KAIROS governance seal failed: %s: %s",
                           type(e).__name__, e)
            return None

    async def maybe_snapshot_and_fingerprint(
        self, *, force: bool = False,
    ) -> "ArchiveBlock | None":
        """Public snapshot hook (arch §7.2). Returns the new block if a
        snapshot fired, or None if not due / no fingerprinter wired.

        Fix B — between fingerprint compute and chain append, compares the
        new fingerprint against the previous chain block's fingerprint and
        emits a kb_pressure_alert (type=cortex_drift) when severity is
        warn or alert. Stable severity emits nothing (no false alarms).

        D.1 (Law 8) — every skip path sets `last_snapshot_skip_reason` and
        logs. A skipped seal is dropped governance evidence; it must be
        declared, not inferred from an empty field."""
        reason = self._snapshot_gate(force)
        if reason:
            self.last_snapshot_skip_reason = reason
            return None
        self.last_snapshot_skip_reason = ""
        try:
            return await self._snapshot_and_seal(force)
        except Exception as e:
            # C10 — pack §13 I-6 named "error" as a reachable skip reason, but
            # nothing emitted it: a throw here propagated uncaught out of
            # run_one_cycle. An invariant naming a state the code cannot reach
            # is worse than no invariant — it reads as satisfied on inspection
            # and fails only when the untested path fires.
            self.last_snapshot_skip_reason = "error"
            logger.warning("KAIROS snapshot failed: %s: %s", type(e).__name__, e)
            return None

    async def _snapshot_and_seal(self, force: bool) -> "ArchiveBlock | None":
        """Body of the snapshot path, separated so C10's handler can wrap it."""
        fp, snap_path = self._snapshot_and_fingerprint()

        # Fix B — compare with the previous chain block's fingerprint.
        # S.2b — the previous SNAPSHOT block, not simply the previous block.
        # Governance blocks carry no fingerprint, and `from_dict` is
        # key-tolerant, so comparing against one would silently assess drift
        # against zeros and alert on every cycle after a recalibration.
        prev_block = self.archive_chain.latest_snapshot_block()
        if prev_block is not None:
            from backend.memory.fingerprint import CortexFingerprint, assess_drift
            fp_prev = CortexFingerprint.from_dict(prev_block.fingerprint)
            assessment = assess_drift(fp_prev, fp, self.consecutive_drift_warns)
            self.consecutive_drift_warns = assessment.consecutive_warns
            if assessment.severity != "stable" and self.notify_ui is not None:
                await self.notify_ui({
                    "event":    "kb_pressure_alert",
                    "type":     "cortex_drift",
                    "domain":   "cortex",
                    "action":   assessment.recommended_action,
                    "distance": assessment.distance,
                    "severity": assessment.severity,
                })

        # P3 / L2 — digest-seal unsealed answer-lineage records into this
        # block. The seal rides the fingerprint dict (drift's from_dict is
        # key-tolerant), so it is covered by the block hash. KAIROS stays
        # archive.db's only writer; the sealed_block backfill below is a
        # kb.db write, which the write-topology rule permits.
        payload = fp.to_dict()
        unsealed = self._unsealed_lineage()

        # S.2 — the governance record rides the SAME block, under its own key.
        # An envelope change that is not chain-covered is exactly as forgeable
        # as a recalibration that is not — and the envelope is the thing that
        # says the recalibration was in bounds, so both are sealed or neither
        # means anything. `from_dict` is key-tolerant, and the block hash
        # covers the whole payload, so this needs no new mechanism.
        #
        # S.2b — a snapshot block STILL seals whatever is pending. It is no
        # longer the only thing that does, and under the default mode there is
        # usually nothing left for it to pick up.
        recal_unsealed = self._unsealed_recalibrations()
        bindings = self._unbound_lineage()
        held = self._unsealed_commitments()
        acts = self._unsealed_actions()
        payload.update(self._seal_payload(unsealed, recal_unsealed, self._lineage_leaves(unsealed), bindings, held,
                                          acts))

        block = self.archive_chain.append(payload, snap_path)
        self._mark_sealed(unsealed, recal_unsealed, block.index, "snapshot")
        self._mark_bound(bindings, block.index)
        self._mark_commitments_sealed(held, block.index)
        self._mark_actions_sealed(acts, block.index)
        self.last_snapshot_block = block

        # H.3b — `chain_appended` has been in CLAUDE.md's SSE contract since
        # Phase 9 with nothing emitting it, while both UI files carry a
        # handler waiting for it (nexus_ui.html:1307 refreshes chain status,
        # nexus_memory.html:1252 is marked "dormant"). Sealing is the event
        # this substrate exists to demonstrate; an operator watching a seal
        # happen saw nothing at all.
        #
        # Queued, not yielded: KAIROS is idle-time and has no live SSE
        # channel. It reaches a client only through the bridge H.3a fixed,
        # which is why that fix had to land first — before it, this frame
        # would have arrived relabelled as `kb_pressure_alert`.
        if self.notify_ui is not None:
            try:
                await self.notify_ui({
                    "event":        "chain_appended",
                    "index":        block.index,
                    "hash":         block.block_hash,
                    "sealed_count": len(unsealed),
                })
            except Exception as e:      # never let telemetry break the seal
                logger.warning("chain_appended notify failed: %s: %s",
                               type(e).__name__, e)
        return block

    # ---------- snapshot helpers (Phase 9) ----------

    def _graph_is_empty(self) -> bool:
        """True when the in-memory cortex has no nodes. The hippocampus is
        never persisted, so this is the state of a freshly started process."""
        h = getattr(self, "hippocampus", None)
        if h is None:
            return True
        try:
            return h.graph.number_of_nodes() == 0
        except AttributeError:          # pragma: no cover - defensive
            return not list(h.nodes)

    def _snapshot_gate(self, force: bool = False) -> str:
        """Return "" to proceed, else the declared skip reason (D.1/Law 8).

        Vocabulary: "unwired" | "graph_empty" | "not_due".
        """
        if self.fingerprinter is None or self.archive_chain is None:
            # WARNING: a misconfigured stack silently never sealing is exactly
            # the failure this packet exists to make visible.
            logger.warning(
                "KAIROS snapshot skipped: unwired "
                "(fingerprinter=%s, archive_chain=%s)",
                "set" if self.fingerprinter else "None",
                "set" if self.archive_chain else "None")
            return "unwired"

        latest = self.archive_chain.latest()

        if force:
            # D.4 — an operator forcing a cycle may seal whatever exists,
            # including an empty graph. Explicit and attributed, not accidental.
            if latest is None and self._graph_is_empty():
                logger.info(
                    "KAIROS snapshot forced over an empty cortex — the first "
                    "block will seal a zero fingerprint (explicit override)")
            return ""

        if latest is None:
            # D.4 — the first-ever block must not seal a vacuous graph. A fresh
            # process has an empty in-memory cortex, so an unconditional first
            # snapshot seals n_nodes=0 / signature=[0.0]*6 and then starts the
            # interval clock from that meaningless block.
            if self._graph_is_empty():
                logger.info(
                    "KAIROS snapshot skipped: graph_empty "
                    "(no chain blocks yet and the cortex has no nodes; "
                    "the first block must record a real topology)")
                return "graph_empty"
            return ""

        elapsed_days = (time.time() - latest.timestamp) / DAY_SECONDS
        if elapsed_days < self.snapshot_interval_days:
            logger.debug(
                "KAIROS snapshot skipped: not_due "
                "(%.2f days elapsed of %.2f configured)",
                elapsed_days, self.snapshot_interval_days)
            return "not_due"
        return ""

    def _snapshot_due(self) -> bool:
        """Retained for callers/tests predating the reason vocabulary.
        Subsequent-snapshot behaviour is unchanged: interval only."""
        return self._snapshot_gate(force=False) == ""

    def _snapshot_and_fingerprint(self) -> "tuple[CortexFingerprint, str]":
        """Persist a JSON snapshot of the live cortex + return fingerprint."""
        assert self.fingerprinter is not None
        fp = self.fingerprinter.compute(self.hippocampus)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        # THE COLLISION GUARD. `int(time.time())` names files to the second
        # and `write_text` overwrites, so two snapshots in one second wrote
        # ONE file — the earlier block's sealed content was destroyed, not
        # merely unverifiable, and `verify_chain()` stayed valid because
        # nothing re-reads the file after `append()` (TEMPORAL_PROBE.md;
        # block 0 of the operator's chain is this signature). The trigger is
        # real: POST /kairos/cycle forces a snapshot and is wired to a UI
        # button, so two clicks in one second sufficed.
        #
        # A counter, not a content-hash suffix, and FIRST-IN-SECOND KEEPS THE
        # HISTORICAL NAME: `snapshot_path` is sealed inside every existing
        # block's hash (chain.py compute_hash), so old paths can never be
        # rewritten — the fix must be forward-only and must not rename the
        # common case, or old and new snapshots stop being named alike. Only
        # colliding names change: cortex-{ts}-1.json, -2.json, …
        path = self.snapshots_dir / f"cortex-{ts}.json"
        n = 0
        while path.exists():
            n += 1
            path = self.snapshots_dir / f"cortex-{ts}-{n}.json"
        snap = {
            "timestamp":   ts,
            "fingerprint": fp.to_dict(),
            "nodes": [
                {"id": nid,
                 "epistemic_state": getattr(self.hippocampus.get_node(nid).epistemic_state, "value", ""),
                 # P2/F-02 — evidential_weight canonical; confidence = deprecated alias.
                 "evidential_weight": float(getattr(self.hippocampus.get_node(nid), "confidence", 0.0)),
                 "confidence": float(getattr(self.hippocampus.get_node(nid), "confidence", 0.0))}
                for nid in self.hippocampus.nodes
            ],
            "edges": [
                {"source": e.source, "target": e.target, "weight": float(e.weight)}
                for e in self.hippocampus.edges()
            ],
        }
        path.write_text(json.dumps(snap, sort_keys=True))
        return fp, str(path)

    # ---------- background loop ----------

    async def run(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            await self.run_one_cycle()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                continue

    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._stop = asyncio.Event()   # bind to the running loop
            self._task = asyncio.create_task(self.run())
        return self._task

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._stop = None
