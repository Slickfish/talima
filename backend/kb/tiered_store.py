"""Tiered SQLite triple store. No triple is ever deleted (Law 4).

TWO LAYERS (Packet F.3)
=======================
**The RECORD is monotone and absolute.** `subject`, `relation`, `object`,
stored `weight`, `source`, `created_at`, and the lineage records that cite
them. Stored `weight` is never written downward anywhere in the codebase —
`add()`'s upsert takes `MAX`, and no other statement touches it — so it is a
high-water mark of evidence, protected by the absence of a code path.

**PROMINENCE is lossy.** `current_weight()` and `tier`. An unreinforced
belief sinks out of retrieval and rests. Sinking is not destruction: the
belief stays queryable by id, exportable, and citable, with every record
field intact.

DECAY IS A PROPERTY OF THE EVIDENCE, NOT OF THE TIER
====================================================
Before F.3 the decay rate was read from the belief's *current tier*, and
`current_weight()` applied that rate to the belief's *entire* elapsed idle
time — as if it had always been in the tier it had just entered. Because
every lower tier decayed faster, the result was a downward discontinuity at
every tier change: measured `cw 0.390 -> 0.000` in a single step at a frozen
instant. The algebra generalises — demoting out of tier 1 required
`d > (w-0.4)/0.01`, giving a post-demotion value `< 1.2 - 2w`, i.e. zero for
any stored weight above 0.6, which is essentially the whole store. That
cliff, not the zero promote-threshold, is what drove the `3->2->3->2`
oscillation: it slammed prominence to zero, tripping every lower band at once.

Now the rate comes from the belief's **evidence class** (`classify(source)`),
which does not change over time, so `current_weight()` is continuous. Tier
becomes a **band over `current_weight` with hysteresis** — a pure projection,
recomputed rather than stepped. A function cannot oscillate, so the floor
absorbs by construction rather than by threshold engineering, and re-entry is
by reinforcement only (`fire()` resets `last_fired`).

No tier is immune to time. Nothing needs immunity, because nothing is at
risk: the record is untouchable either way.
"""
from __future__ import annotations

import logging
import os
from backend.store import ConnHandle, Database, Row, StoreTarget
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from backend.kb.gate import (  # noqa: F401 (BeliefRejected re-exported)
    BeliefRejected, check_triple, gate_enabled, normalise_relation,
    normalise_term,
)
from backend.kb.source_policy import store_refusal
from backend.kb.granularity import Granularity
from backend.kb.schema import DEFAULT_DB, SUBSTRATE_RELATIONS, connect

logger = logging.getLogger("nexus.kb.store")

DAY_SECONDS = 86400.0

# --------------------------------------------------------------------------
# Evidence classes — the decay rate is a property of how a belief was learned
# --------------------------------------------------------------------------
# **PROVISIONAL.** These numbers are a first calibration, not a result. What
# is load-bearing — and what the sentinels pin — is the *ordering*, which is
# an argument about evidence quality. The magnitudes are guesses about a
# personal assistant's usage and should be revisited once the store has real
# retrieval history behind it (Packet E precedent: ship configurable, treat
# the defaults as provisional, let the sentinel test the ordering).
#
# Slowest to fastest, and why:
#   external      an outside referent exists and can be re-checked
#   fixture       deliberately seeded, but by us, and synthetic
#   procedural    a pattern the system observed repeatedly in its own use
#   consolidation co-activation: "these fired together", weakest real signal
#   extraction    the model extracting triples from its OWN answer. No
#                 external referent at all — it is the model marking its own
#                 homework, so it earns the least durability, not the most.
CLASS_DECAY_PER_DAY = {
    # O.2b — human assertion, and it goes FIRST because F.3's own criterion
    # puts it there. The ordering is an argument about external referent, and
    # a named person stating a fact about their own trial has the strongest
    # referent the system holds: stronger than Wikidata, which is a third
    # party that can be wrong and that nobody in this room can be asked about.
    # Nothing should out-persist what the operator told it.
    # ~2.5 years from 0.9 to zero prominence.
    "asserted":      0.001,
    "external":      0.005,   # ~180 days from 0.9 to zero prominence
    "fixture":       0.010,
    "procedural":    0.020,
    "consolidation": 0.035,   # the 555: ~9 days to leave tier 0, ~28 to floor
    "extraction":    0.050,   # ~18 days
}

# Slowest -> fastest. The sentinel asserts this ordering holds; it does not
# assert the numbers above.
CLASS_ORDER = ("asserted", "external", "fixture", "procedural",
               "consolidation", "extraction")

SOURCE_CLASS = {
    # O.2b — the only source value the human-assertion path writes. Registered
    # here so it can never fall through to UNKNOWN_CLASS and decay fastest,
    # which is the inverse of what it deserves.
    "human_assertion":          "asserted",
    "wikidata":                 "external",
    "conceptnet":               "external",
    "web_search":               "external",
    "offline_fixture":          "fixture",
    "procedural_promotion":     "procedural",
    "hippocampus_consolidation": "consolidation",
    "extraction":               "extraction",
}

# An unregistered source is a defect (Law X: sources register in one place),
# and it must not be rewarded with durability. Same stance as F.2 took on an
# unreadable tier: a value we cannot interpret is never read as "immune".
UNKNOWN_CLASS = "extraction"

_warned_sources: set[str] = set()


def substrate_clock_refresh_enabled() -> bool:
    """AN.2's rollback. `on` restores the pre-AN.2 upsert exactly: a
    substrate re-promotion refreshes the row's decay clock. Off (default),
    re-promotion is not evidence — `last_fired` moves only for claim
    re-assertion and for citation-driven fire(). Read at call time."""
    return os.getenv(
        "NEXUS_SUBSTRATE_CLOCK_REFRESH", "off").strip().lower() == "on"


def archival_ledger_enabled() -> bool:
    """AL's rollback. `off` writes no archival events; the archive/unarchive
    UPDATEs themselves are untouched in both positions, so `off` is exactly
    the pre-AL store. Read at call time (the E.2 pattern)."""
    return os.getenv(
        "NEXUS_ARCHIVAL_LEDGER", "on").strip().lower() != "off"


def classify(source: str) -> str:
    """Evidence class for a `source` value. Unknown sources decay fastest."""
    cls = SOURCE_CLASS.get(source)
    if cls is None:
        if source not in _warned_sources:
            _warned_sources.add(source)
            logger.warning(
                "unregistered source %r — decaying it as %r. Law X: register "
                "new sources in backend/kb/source_registry.py and add them to "
                "SOURCE_CLASS so their durability is a decision, not a default.",
                source, UNKNOWN_CLASS)
        return UNKNOWN_CLASS
    return cls


def decay_rate_for_source(source: str) -> float:
    """Decay per day for this belief's evidence class.

    Env-overridable per class (`NEXUS_DECAY_EXTERNAL`, `NEXUS_DECAY_FIXTURE`,
    `NEXUS_DECAY_PROCEDURAL`, `NEXUS_DECAY_CONSOLIDATION`,
    `NEXUS_DECAY_EXTRACTION`), read at call time so a test or an operator can
    change it without a restart — the pattern E.2 established for the
    refractory window.
    """
    cls = classify(source)
    raw = os.environ.get(f"NEXUS_DECAY_{cls.upper()}")
    if raw is not None:
        try:
            return max(0.0, float(raw))
        except ValueError:
            logger.warning("NEXUS_DECAY_%s=%r is not a number; using default",
                           cls.upper(), raw)
    return CLASS_DECAY_PER_DAY[cls]


# --------------------------------------------------------------------------
# Packet Q — provenance governs ARRIVAL, not only decay
# --------------------------------------------------------------------------
# THE GAP F.3 LEFT, in one line: F.3 governs how fast evidence FADES; nothing
# governed how prominent it ARRIVES. Measured in P.3 — `agentic_loop.py:542`
# writes tier 2, `rebalance()` projects weight 1.0 into tier 0 because
# TIER_ENTRY[0] is 0.75, and retrieval orders tier ASC. So a model marking its
# own homework at confidence 1.0 outranked Wikidata within one KAIROS cycle,
# and every turn wrote fresh rows at 1.0, so the window never closed.
#
# THE DEFENCE, one sentence, and it is the same sentence as F.3's:
#   Provenance governs both how prominent evidence arrives and how long it
#   stays, because both questions ask how far a claim can be trusted, and the
#   answer to both is whether an outside referent exists that someone could
#   check.
# So the arrival order IS the decay order. That was checked rather than
# assumed — see the counter-argument recorded below.
#
# THE SHAPE: a per-class DISCOUNT on EFFECTIVE PROMINENCE.
#   effective = current_weight(now) * CLASS_ARRIVAL_SCALE[class]
# used for the band AND for the retrieval sort, so a class cannot be held out
# of a tier and then win the tie inside the one below it. Not a per-class
# entry table (24 constants, and the ordering claim disappears into it), and
# not a per-class ceiling on the band: a hard ceiling means reinforcement can
# never lift an extraction belief, which is F.3's "no tier is immune" broken
# in the mirror.
#
# EVERY SCALE IS <= 1.0, DELIBERATELY. The factor is a discount, never a
# boost: provenance may lower how prominent a claim arrives and may never
# inflate it above what its own evidence supports.
#
# I-8 HOLDS. Nothing here is written. `effective_prominence` is computed at
# read time from a stored weight that is never touched, so this is a change to
# the LOSSY layer only — which is the whole reason Q chose the band over the
# weight. Scaling the stored weight would write a number nobody claimed, which
# is sanitising, which K.5 ruled against by name.
#
# **PROVISIONAL**, exactly as F.3's rates are. What is load-bearing is the
# ORDERING, which the sentinels pin; the magnitudes are a first calibration.
# Env-overridable per class (`NEXUS_ARRIVAL_ASSERTED`, `_EXTERNAL`, `_FIXTURE`,
# `_PROCEDURAL`, `_CONSOLIDATION`, `_EXTRACTION`), read at call time.
#
# ROLLBACK IS EXACT. Set every scale to 1.0 and effective == current_weight,
# so the band and the sort are bit-identical to pre-Q behaviour. The sentinel
# asserts the reducing configuration reproduces the MEASURED pre-Q ordering
# ['extraction','human_assertion','offline_fixture','wikidata'] — a stronger
# claim than "behaviour unchanged", and E.2's precedent for shipping a
# provisional default with a way back.
#
# THE COUNTER-ARGUMENT, checked and rejected on evidence. Extraction rows
# concern the CURRENT conversation, so one could argue they should arrive
# prominent and fade fast — which is what today's behaviour implicitly
# assumes. It fails empirically: the replay found six of twenty injected slots
# going to PRIOR-SESSION extraction noise still resting at tier 0. Recency is
# a real ranking signal and it is not provenance; conflating the two is what
# produced the defect. If topical recency is wanted it belongs in retrieval
# scoring, not in the arrival band.
CLASS_ARRIVAL_SCALE = {
    "asserted":      1.00,   # a person's own statement; strongest referent
    "external":      1.00,   # an outside referent exists and can be re-checked
    "fixture":       0.95,   # deliberately seeded, but by us, and synthetic
    "procedural":    0.85,   # a pattern observed in our own use
    "consolidation": 0.75,   # "these fired together" — weakest real signal
    "extraction":    0.65,   # the model marking its own homework
}


def arrival_scale(source: str) -> float:
    """Prominence discount for this belief's evidence class. Never above 1.0."""
    cls = classify(source)
    raw = os.environ.get(f"NEXUS_ARRIVAL_{cls.upper()}")
    if raw is not None:
        try:
            return max(0.0, min(1.0, float(raw)))
        except ValueError:
            logger.warning("NEXUS_ARRIVAL_%s=%r is not a number; using default",
                           cls.upper(), raw)
    return CLASS_ARRIVAL_SCALE.get(cls, 1.0)


def effective_prominence(triple: "Triple", now: float | None = None,
                         adjustment: float = 0.0) -> float:
    """What this belief is worth to RANKING right now.

    Distinct from `current_weight()`, which is what it is worth as EVIDENCE
    and is what the lineage record must carry. Keeping them separate is why
    `weight_at_injection` stays honest while the ordering changes.

    S.2 — `adjustment` is the summed, recorded recalibration for this belief:
    the third factor, ADDITIVE because an envelope's unit is a delta and a
    board approves "±0.05", not "×0.93". Passed in rather than looked up so
    this stays a pure function of its arguments — the property that keeps
    `rebalance()` idempotent. Zero means "nothing has been recorded", which is
    also what a store with no recalibration wired reports.
    """
    base = triple.current_weight(now) * arrival_scale(triple.source)
    return min(max(base + adjustment, 0.0), 1.0)


# --------------------------------------------------------------------------
# Tier bands — a projection of effective prominence, with hysteresis
# --------------------------------------------------------------------------
# TIER_ENTRY  — the prominence needed to be *assigned* a tier.
# TIER_RETAIN — the prominence needed to *stay* in it once there.
# ENTRY > RETAIN is the hysteresis: a belief hovering on a boundary does not
# flap between bands turn after turn. Both are read fresh from
# `current_weight` every pass, so `rebalance()` is idempotent — see
# `band_for()`.
TIER_ENTRY  = {0: 0.75, 1: 0.45, 2: 0.15, 3: 0.0}
TIER_RETAIN = {0: 0.65, 1: 0.35, 2: 0.08, 3: 0.0}
LOWEST_TIER = 3


def band_for(cw: float, current_tier: int | None = None) -> int:
    """The tier this prominence belongs in. A pure function — no state.

    Idempotent by construction: the value returned for `(cw, t)` where `t` is
    itself a previous return value is `t` again, because a belief that just
    entered tier `t` satisfies `cw >= TIER_ENTRY[t] >= TIER_RETAIN[t]`.
    That is the property that makes the floor absorbing: at `cw = 0` the band
    is `LOWEST_TIER`, and `TIER_RETAIN[LOWEST_TIER] = 0.0` holds it there
    forever. Nothing re-promotes it but reinforcement, which raises `cw`.
    """
    target = LOWEST_TIER
    for tier in sorted(TIER_ENTRY):                   # 0 (best) first
        if cw >= TIER_ENTRY[tier]:
            target = tier
            break
    if current_tier is None or current_tier not in TIER_RETAIN:
        return target
    # Hysteresis: hold a belief that is still above its retention floor and
    # is not owed a promotion.
    if current_tier <= target and cw >= TIER_RETAIN[current_tier]:
        return current_tier
    return target


class AmbiguousArchiveTarget(ValueError):
    """A (subject, relation, object) that names more than one stored belief.

    Raised instead of archiving every match. The uniqueness constraint is
    (subject, relation, object, source), so the same claim from two sources is
    two rows with two evidence classes and two decay rates — by design, and how
    a model's self-extraction stays distinguishable from an external referent.
    Collapsing them on a call that could not have named either one is a
    judgement nobody made.
    """

    def __init__(self, subject: str, relation: str, object: str, *,
                 sources: Sequence[str]):
        self.subject, self.relation, self.object = subject, relation, object
        self.sources = list(sources)
        super().__init__(
            f"({subject} {relation} {object}) exists under "
            f"{len(self.sources)} sources {sorted(self.sources)} — pass "
            f"source= to name one, or archive_ids() to name rows directly")


@dataclass(frozen=True)
class Triple:
    id: int | None
    subject: str
    relation: str
    object: str
    weight: float
    tier: int
    granularity: str
    source: str
    fire_count: int
    created_at: float
    last_fired: float
    # F.2 — record-layer status. Defaulted so existing positional/keyword
    # construction in tests and fixtures keeps working unchanged.
    archived: bool = False
    # L.2a — the judgement behind `archived`, and when it was made. NULL on a
    # live row, and also on a row archived before L.2a recorded reasons.
    archive_reason: str | None = None
    archived_at: float | None = None

    def current_weight(self, now: float | None = None) -> float:
        """Prominence now. Never modifies the stored weight.

        F.3 — the rate comes from the evidence class, not from `self.tier`,
        which is what makes this continuous: a tier change re-labels the band
        a belief sits in and is worth exactly zero prominence at a frozen
        instant.
        """
        now = now or time.time()
        days = max(0.0, (now - self.last_fired) / DAY_SECONDS)
        decayed = self.weight - decay_rate_for_source(self.source) * days
        return max(0.0, decayed)


# --------------------------------------------------------------------------
# Superseded by the above — retained one release (the P2 deprecated-alias
# pattern). NOTHING READS THESE. They are kept only so callers and tests that
# still import the names do not break on the same commit that changes the
# model, and so the old values remain legible next to the new ones.
#   TIER_DECAY_PER_DAY[0] = 0.0 was the immunity: zero decay, and
#   TIER_DEMOTE_WEIGHT[0] = 0.0 made `cw < 0.0` unsatisfiable so nothing could
#   leave. TIER_PROMOTE_WEIGHT[0] = 0.95 was already dead before F.3 — the
#   `and t.tier > 0` guard meant it was read and discarded — so the real
#   immunity bar was 0.70, reached from tier 1.
# --------------------------------------------------------------------------
TIER_PROMOTE_WEIGHT = {0: 0.95, 1: 0.7, 2: 0.4, 3: 0.0}   # DEPRECATED, unused
TIER_DEMOTE_WEIGHT  = {0: 0.0,  1: 0.4, 2: 0.15, 3: 0.05}  # DEPRECATED, unused
TIER_DECAY_PER_DAY  = {0: 0.0,  1: 0.01, 2: 0.03, 3: 0.08}  # DEPRECATED, unused
TIER_CONSTANTS_DEPRECATED = True


def decay_rate_for_tier(tier: int) -> float:
    """DEPRECATED (F.3) — decay no longer depends on tier. Use
    `decay_rate_for_source`. Retained one release; returns the legacy value."""
    if tier in TIER_DECAY_PER_DAY:
        return TIER_DECAY_PER_DAY[tier]
    return TIER_DECAY_PER_DAY[max(TIER_DECAY_PER_DAY)]


# The connection handle (`.execute(...)` AND `with handle() as c:`) is the
# store interface's `ConnHandle` since W3b (`backend/store/base.py`); it was
# `_SQLiteConnHandle` here, wrapping the raw sqlite3 connection.


class TripleStore:
    """Owns kb.db. Per-instance connection (single-process Phase 2)."""

    def __init__(self, db_path: "Path | str | StoreTarget" = DEFAULT_DB):
        # W3b — a path is SQLite exactly as before; a StoreTarget names the
        # backend (`backend.store`). `db_path` stays the file for a SQLite
        # store and is None for any other backend.
        self.target = StoreTarget.of(db_path)
        self.db_path = self.target.path
        raw: Database = connect(self.target)
        self.conn = ConnHandle(raw)
        # S.2 — the recalibration record, injected rather than imported.
        # `recalibration.py` imports `classify` from this module, so reaching
        # back the other way would be a cycle; and a hook keeps the store
        # usable with no governance wired at all, which is what every test
        # predating S.2 does. Same shape as `hebbian.protected_pairs`.
        # None means "no recalibration recorded", which reads as zero.
        self.recalibration = None
        # Packet AA — inside `deferred_pass()`, add() leaves its row pending.
        self._defer_commits = False
        # v3.1 Phase 1 — see `admissions()`.
        self._admission_sink: "list[int] | None" = None

    # ---------- recalibration adjustments (S.2, read-time) ----------

    def adjustment_for(self, belief_id: int) -> float:
        if self.recalibration is None:
            return 0.0
        return self.recalibration.adjustment_for(belief_id)

    def adjustments(self) -> dict[int, float]:
        if self.recalibration is None:
            return {}
        return self.recalibration.adjustments()

    def effective_weight(self, belief_id: int, now: float | None = None) -> float:
        """Prominence including every recorded recalibration.

        The number ranking uses and an operator reads. `Triple.weight` remains
        what was claimed; this is what it is currently worth.
        """
        t = self.get_by_id(belief_id)
        if t is None:
            raise KeyError(belief_id)
        return effective_prominence(t, now, self.adjustment_for(belief_id))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "TripleStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- one commit per pass (Packet AA) ----------

    @contextmanager
    def deferred_pass(self):
        """One transaction for a whole pass of `add()` calls.

        WHY. OQ-4 measured the steady-state consolidation pass at 13,183
        commits — one per no-op upsert — under `journal_mode=delete` /
        `synchronous=FULL`: 15.43s of held write lock, 97% of the cycle, and
        the `database is locked` the operator hit. The storage spike's Q3(a)
        added the visibility half: commit-per-row makes a half-done pass
        public, seventeen partial states observed by one reader. Both close
        here: inside this context `add()` defers its commit, and the pass
        becomes one fsync and one atomic publication.

        WHAT THIS DOES NOT CHANGE. `add()` is still the single chokepoint
        and the gate still runs per belief — a refusal raises BEFORE the
        INSERT, writes nothing, and does not disturb the pending
        transaction. This context changes WHEN work becomes durable, never
        what is admitted.

        FAILURE ROLLS BACK THE WHOLE PASS — rebalance() decided that
        question in F.2 ("a half-applied pass is a store state no policy
        produced") and the re-offer design is what makes it cheap: promoted
        edges stay in the hippocampus until session-clear, so a rolled-back
        pass loses nothing permanently. The CALLER declares the rollback
        (I-6) because only it knows how much was discarded.

        Does not nest: a nested pass would silently make the inner rollback
        take the outer pass's work with it, which is a semantics nobody
        asked for and this raises rather than guesses.
        """
        if self._defer_commits:
            raise RuntimeError("deferred_pass does not nest")
        self._defer_commits = True
        try:
            yield self
        except BaseException:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()
        finally:
            self._defer_commits = False

    # ---------- write ----------

    @staticmethod
    def stored_form(subject: str, relation: str,
                    object: str) -> tuple[str, str, str]:
        """What `add()` will store for these three strings — the ONE place
        that says so (AR.3).

        `/kb/assert/confirm` composed its response from the parsed triple
        while `add()` normalised the relation, so an integrator was handed
        `is` for a row that reads `is_a` and could never join on it. The
        S.4 offer had the same shape one step earlier. Both now ask this
        function, and `add()` itself derives from it (pinned by AST), so a
        second copy of the normalisation block cannot drift.

        Follows the gate switch exactly as `add()` does: with
        NEXUS_INGEST_GATE=off nothing is normalised and nothing is reported
        as normalised.
        """
        if gate_enabled():
            return (normalise_term(subject), normalise_relation(relation),
                    normalise_term(object))
        return (subject, relation, object)

    def add(
        self,
        subject: str,
        relation: str,
        object: str,
        *,
        weight: float = 0.5,
        tier: int = 2,
        granularity: str | Granularity,
        source: str,
    ) -> int:
        gran = granularity.value if isinstance(granularity, Granularity) else granularity
        if gran not in Granularity.all():
            raise ValueError(f"unknown granularity {gran!r}")
        if tier not in (0, 1, 2, 3):
            raise ValueError(f"tier {tier} out of range (Phase 2 supports 0-3)")
        # W5 — the reference policy, at the same chokepoint and before the
        # gate: with NEXUS_REFERENCE_GATEWAY on, a source the policy does not
        # declare, or does not permit to the store, is refused by name. Off
        # (the default) `store_refusal` answers None for every source and
        # this is the pre-W5 `add()` exactly.
        _not_permitted = store_refusal(source)
        if _not_permitted is not None:
            raise BeliefRejected(_not_permitted, field="source", term=str(source))
        # K.2 — the ingest gate. This is the ONLY chokepoint all six ingest
        # routes pass through, which is why the rule lives here rather than in
        # each caller: the endpoint audit's finding was not that the routes
        # were individually unguarded, it was that the shared floor validated
        # only granularity and tier. Strictness comes from the evidence class
        # (F.3's classify), so a Wikidata label and a term the model extracted
        # from its own answer are not held to the same rule.
        #
        # Rejects at the boundary and never reaches backwards: nothing already
        # stored is read, altered or removed by this call.
        #
        # K.3 — normalise BEFORE the gate, so the gate judges what will
        # actually be stored rather than what was typed. Relations are
        # slugged and aliased (five spellings of "is a" become one); terms are
        # whitespace-collapsed only — never slugged, because a term is a name
        # and `theory of relativity` must not become `theory_of_relativity`.
        # Unmappable relations are stored as their own slug, never rejected: a
        # relation vocabulary is open-ended and refusing an unknown predicate
        # would refuse legitimate new ones.
        #
        # NO RETROACTIVE NORMALISATION. Existing rows keep whatever spelling
        # they were written with. Rewriting them is a separate decision with
        # its own record, as Packet B's purge and E.4's declined write both
        # were.
        # Normalisation is behind the SAME switch as the gate. The switch is
        # documented as restoring prior behaviour, and prior behaviour had no
        # normalisation either — a switch that reverses half a packet is worse
        # than none, because the operator who flips it does not get what the
        # label promises. Caught while exercising the verification script,
        # whose fixture came out already-normalised with the gate off.
        subject, relation, object = self.stored_form(subject, relation, object)
        check_triple(subject, relation, object,
                     evidence_class=classify(source), weight=weight)
        now = time.time()
        # AN.2 — a substrate re-promotion is not evidence, so it may not
        # reset the decay clock. Confirmed live before the fix: one re-add
        # returned a 40-day-idle co_activated row (current weight 0.000) to
        # 0.900 by this line alone, and consolidation re-offers every
        # gate-passer every cycle — the store-side sibling of the graph
        # leak AK closed. A CLAIM re-asserted is fresh evidence and keeps
        # refreshing; the partition is schema.SUBSTRATE_RELATIONS, the same
        # object AK's ingest guard reads. Weight's monotone MAX and the
        # tier MIN are untouched — this is the clock, not the record.
        refresh_clock = (relation not in SUBSTRATE_RELATIONS
                         or substrate_clock_refresh_enabled())
        cur = self.conn.execute(
            """
            INSERT INTO triples
                (subject, relation, object, weight, tier, granularity, source,
                 fire_count, created_at, last_fired)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(subject, relation, object, source) DO UPDATE SET
                weight     = MAX(triples.weight, excluded.weight),
                tier       = MIN(triples.tier, excluded.tier),
                last_fired = CASE WHEN ? = 1 THEN excluded.last_fired
                                  ELSE triples.last_fired END
            RETURNING id
            """,
            (subject, relation, object, weight, tier, gran, source, now, now,
             int(refresh_clock)),
        )
        row = cur.fetchone()
        # Packet AA — one commit per PASS when a `deferred_pass()` is open.
        # Outside one, per-row commit is unchanged: every other caller keeps
        # durability-on-return, the contract they were written against.
        if not self._defer_commits:
            self.conn.commit()
        # v3.1 Phase 1 — an open `admissions()` scope collects the id of
        # every claim this chokepoint admitted, insert or upsert hit alike,
        # so the caller can write the work-unit junction beside the gate's
        # decision. `add()` stays the single chokepoint; the junction is not
        # a claim and does not pass through it.
        if self._admission_sink is not None:
            self._admission_sink.append(int(row["id"]))
        return int(row["id"])

    @contextmanager
    def admissions(self):
        """Collect the ids `add()` returns inside the block (v3.1 Phase 1).

        Scoped, not global: consolidation's `add()` runs on the KAIROS cycle
        with no unit open and must not be attributed to one, so the sink
        exists only where a caller opens it — the extraction stage of a turn,
        the assertion route, the seed routes."""
        sink: list[int] = []
        previous = self._admission_sink
        self._admission_sink = sink
        try:
            yield sink
        finally:
            self._admission_sink = previous

    def add_many(self, triples: Iterable[dict]) -> int:
        n = 0
        for t in triples:
            self.add(**t)
            n += 1
        return n

    def search_subject_relation(self, subject: str,
                                relation: str) -> "list[Triple]":
        """Live claims on one subject under one relation — the trail
        channel's admission lookup (Packet AB). Read-only; archived rows
        excluded for the same reason `search()` excludes them."""
        rows = self.conn.execute(
            # W3b D14 — the order spelled out: SQLite answers this through
            # the UNIQUE (subject, relation, object, source) index, so the
            # siblings came back in (object, source) order (measured); `id`
            # closes the order, and Postgres under COLLATE "C" gives the same.
            "SELECT * FROM triples WHERE subject = ? AND relation = ? "
            "AND archived = 0 ORDER BY object ASC, source ASC, id ASC", (subject, relation)).fetchall()
        return [self._row_to_triple(r) for r in rows]

    # ---------- archival (F.2 mechanism, L.2a precision) ----------
    #
    # L.2a — `archive()` used to match on (subject, relation, object) and
    # ignore `source`, while the store's uniqueness constraint is
    # (subject, relation, object, source). MEASURED: two rows holding the same
    # claim from `wikidata` and `extraction` -> one call reported **2 rows
    # affected**. That was harmless for F.2's only caller, a human confirming
    # one dedup pair. It is a hazard for a rule-driven pass, where the gate
    # refuses the extraction copy and permits the external one: a lenient-class
    # belief would leave retrieval on a strict-class judgement, silently, and
    # `unarchive` had the same blindness so the mistake could not be selectively
    # undone.
    #
    # Ambiguity is REFUSED, not resolved by guessing. A bare (s, r, o) that
    # matches two sources cannot know which copy was judged, and picking one is
    # a fabricated decision — the same stance F.2 took on an unreadable tier and
    # K took on an unparseable term.

    def _archive_targets(self, subject: str, relation: str, object: str,
                         source: str | None) -> list[Row]:
        sql = ("SELECT id, source FROM triples "
               "WHERE subject = ? AND relation = ? AND object = ?")
        args: list = [subject, relation, object]
        if source is not None:
            sql += " AND source = ?"
            args.append(source)
        return self.conn.execute(sql, args).fetchall()

    def archive(self, subject: str, relation: str, object: str, *,
                source: str | None = None, reason: str | None = None,
                actor_id: str = "operator",
                actor_role: str = "local_operator",
                actor_source: str = "local") -> int:
        """Mark a belief superseded. Record-layer status, not a tier.

        Law 4 holds exactly as before: the row keeps its id, text, weight,
        source, created_at and tier, and stays resolvable by `get_by_id` — it
        simply stops competing for retrieval slots. Returns rows affected.

        `source` scopes the call to one evidence class. Omitting it is only
        safe when the claim exists once; when it exists under several sources
        this raises `AmbiguousArchiveTarget` rather than archiving all of them.

        `reason` names the judgement (I-7). Convention: `dedup`, or
        `gate:<rule>` for a rule-driven pass. NULL means "archived before
        reasons were recorded" and is never written over with a guess.
        """
        rows = self._archive_targets(subject, relation, object, source)
        if len(rows) > 1:
            raise AmbiguousArchiveTarget(
                subject, relation, object,
                sources=[r["source"] for r in rows])
        return self.archive_ids([r["id"] for r in rows], reason=reason,
                                actor_id=actor_id, actor_role=actor_role,
                                actor_source=actor_source)

    def archive_ids(self, ids: Iterable[int], *,
                    reason: str | None = None,
                    actor_id: str = "operator",
                    actor_role: str = "local_operator",
                    actor_source: str = "local") -> int:
        """Archive exactly the rows named, by primary key.

        The precise primitive: a bulk pass selects its rows by a query it can
        show, then archives those ids and nothing else. No (s, r, o) predicate
        can be re-evaluated between selection and write, so what was reviewed
        is what is archived.

        AL — the act is RECORDED in archival_events, at this chokepoint so
        every caller is covered, in the same transaction as the status write.
        """
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        now = time.time()
        placeholders = ",".join("?" * len(ids))
        cur = self.conn.execute(
            f"UPDATE triples SET archived = 1, archive_reason = ?, "
            f"archived_at = ? WHERE id IN ({placeholders})",
            (reason, now, *ids),
        )
        self._record_archival(ids, "archived", reason or "",
                              actor_id, actor_role, actor_source)
        self.conn.commit()
        return cur.rowcount

    def unarchive(self, subject: str, relation: str, object: str, *,
                  source: str | None = None,
                  actor_id: str = "operator",
                  actor_role: str = "local_operator",
                  actor_source: str = "local") -> int:
        """Reverse `archive`. An archival judgement a human made is one a
        human can withdraw; without this the endpoint would be one-way, and a
        rule-driven pass over hundreds of rows makes reversal the only thing
        standing between a false positive and permanent invisibility.

        Source-scoped and ambiguity-refusing for the same reason as `archive`.
        """
        rows = self._archive_targets(subject, relation, object, source)
        if len(rows) > 1:
            raise AmbiguousArchiveTarget(
                subject, relation, object,
                sources=[r["source"] for r in rows])
        return self.unarchive_ids([r["id"] for r in rows],
                                  actor_id=actor_id, actor_role=actor_role,
                                  actor_source=actor_source)

    def unarchive_ids(self, ids: Iterable[int], *,
                      reason: str | None = None,
                      actor_id: str = "operator",
                      actor_role: str = "local_operator",
                      actor_source: str = "local") -> int:
        """Restore rows by primary key, clearing the archival judgement.

        `archive_reason` and `archived_at` are cleared rather than kept: the
        row is no longer archived, so a retained reason would state something
        false about its current status. Nothing in this substrate keeps a
        per-change history (see TEMPORAL_PROBE.md), and inventing one here for
        a single column would be a claim the rest of the store cannot support.

        AL — that argument covers the COLUMN and stands; the ACT is a
        different claim and is recorded in archival_events as its own event,
        never an amendment of the archive event it reverses. The column keeps
        saying what the row is; the ledger keeps saying what happened.
        """
        ids = [int(i) for i in ids]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        cur = self.conn.execute(
            f"UPDATE triples SET archived = 0, archive_reason = NULL, "
            f"archived_at = NULL WHERE id IN ({placeholders})",
            ids,
        )
        self._record_archival(ids, "unarchived", reason or "",
                              actor_id, actor_role, actor_source)
        self.conn.commit()
        return cur.rowcount

    def _record_archival(self, ids: list[int], direction: str, reason: str,
                         actor_id: str, actor_role: str,
                         actor_source: str) -> None:
        """AL — append one event per belief per act. Same transaction as the
        status write; the caller commits. `NEXUS_ARCHIVAL_LEDGER=off` writes
        nothing, which with the untouched UPDATEs above is exactly the
        pre-AL store."""
        if not archival_ledger_enabled():
            return
        import uuid as _uuid

        from backend.kb.schema import archival_event_body, archival_event_hash
        now = time.time()
        for bid in ids:
            eid = str(_uuid.uuid4())
            body = archival_event_body(eid, now, bid, direction, reason,
                                       actor_id, actor_role, actor_source)
            self.conn.execute(
                "INSERT INTO archival_events (event_id, ts, belief_id, "
                "direction, reason, actor_id, actor_role, actor_source, "
                "record_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (eid, now, int(bid), direction, reason, actor_id,
                 actor_role, actor_source, archival_event_hash(body)))

    def fire(self, triple_id: int) -> bool:
        """Mark a triple as activated this turn (drives Hebbian later).

        Returns True when the write was made and False when the freeze
        refused it — the writer says what it did, so the turn's
        `reinforcement.reinforced` counts writes made and `skipped` counts
        the attempts the freeze refused (B-43), rather than the loop
        counting its own calls."""
        # FZ-2 — reinforcement by citation is learning: frozen skips the
        # write and counts it. The lineage record's cited flags are the
        # record path and are untouched (tests/test_fz_freeze.py, T10).
        from backend.learning import learning_frozen, record_skip
        if learning_frozen():
            record_skip("TripleStore.fire", 1)
            return False
        self.conn.execute(
            "UPDATE triples SET fire_count = fire_count + 1, last_fired = ? WHERE id = ?",
            (time.time(), triple_id),
        )
        self.conn.commit()
        return True

    # ---------- read ----------

    def count(self, *, granularity: str | None = None, source: str | None = None,
              archived: bool | None = None) -> int:
        """Row count. `archived=None` counts the whole RECORD (the default,
        and what every existing caller means); `archived=False` counts what
        can still reach retrieval. L.2a exposes both, because archiving
        hundreds of rows while every counter in the UI holds steady is silent
        loss on the surface whose job is declaring it (I-6)."""
        sql = "SELECT COUNT(*) AS n FROM triples"
        clauses, args = [], []
        if granularity:
            clauses.append("granularity = ?"); args.append(granularity)
        if source:
            clauses.append("source = ?"); args.append(source)
        if archived is not None:
            clauses.append("archived = ?"); args.append(int(archived))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return int(self.conn.execute(sql, args).fetchone()["n"])

    def all(self) -> list[Triple]:
        rows = self.conn.execute("SELECT * FROM triples").fetchall()
        return [self._row_to_triple(r) for r in rows]

    def get_relevant(
        self,
        *,
        concepts: Sequence[str] | None = None,
        top_k: int = 200,
    ) -> list[dict]:
        """Top-K most-relevant triples as dicts.

        - When `concepts` is non-empty, narrows via search_terms().
        - When empty/None, returns the global top-K ranked by tier asc + weight desc.

        Returned dicts carry every column needed by downstream tooling
        (Fix A's deduplicator looks up `subject`, `relation`, `object`,
        `weight`).
        """
        terms = list(concepts) if concepts else []
        if terms:
            triples = self.search_terms(terms, limit=top_k)
        else:
            rows = self.conn.execute(
                "SELECT * FROM triples WHERE archived = 0 "
                # W3b D14 — `id ASC` makes the order total (SNIP-DET's rule,
                # extended to a candidate list): on SQLite it is the scan
                # order the query already returned on a (tier, weight) tie.
                "ORDER BY tier ASC, weight DESC, id ASC LIMIT ?",
                (top_k,),
            ).fetchall()
            triples = [self._row_to_triple(r) for r in rows]
        return [self._triple_to_dict(t) for t in triples]

    @staticmethod
    def _triple_to_dict(t: Triple) -> dict:
        return {
            "id":          t.id,
            "subject":     t.subject,
            "relation":    t.relation,
            "object":      t.object,
            "weight":      t.weight,
            "tier":        t.tier,
            "granularity": t.granularity,
            "source":      t.source,
            "fire_count":  t.fire_count,
            "created_at":  t.created_at,
            "last_fired":  t.last_fired,
        }

    def get_by_id(self, triple_id: int) -> Triple | None:
        """Fetch one triple by primary key (P3 — lineage belief resolution)."""
        row = self.conn.execute(
            "SELECT * FROM triples WHERE id = ?", (triple_id,),
        ).fetchone()
        return self._row_to_triple(row) if row else None

    def search_terms(self, terms: Sequence[str], *, limit: int = 20) -> list[Triple]:
        """Find triples whose subject or object loosely matches any term.
        Phase 2 uses LIKE; Phase 7 swaps to vector similarity over the same rows.

        Fetches with a PER-TERM quota so a high-frequency generic term cannot
        saturate the candidate pool before substantive terms contribute (the
        "previous chat session…" failure: one combined OR-of-LIKEs query let
        co-activation floods for a generic term fill the entire limit). Each
        term gets an equal slice of the limit (floor 5); results are
        deduplicated by triple id, then re-sorted tier ASC / weight DESC.
        `limit` remains the TOTAL cap.
        """
        if not terms:
            return []
        per_term = max(5, limit // len(terms))
        # F.2 — `archived = 0`: a superseded belief keeps every record field
        # and stays resolvable by id, but must not compete for retrieval slots.
        # Before F.2 the archive marker was tier 4, which did not exclude the
        # row here at all — it merely sorted it last and then raised KeyError
        # when its current_weight was computed.
        sql = (
            "SELECT * FROM triples "
            "WHERE archived = 0 AND (LOWER(subject) LIKE ? OR LOWER(object) LIKE ?) "
            # SNIP-DET — `id ASC` makes the order TOTAL: which tied rows survive
            # the per-term LIMIT was SQLite's unspecified tie order before.
            "ORDER BY tier ASC, weight DESC, id ASC LIMIT ?"
        )
        seen: dict[int, Triple] = {}
        for term in terms:
            wildcard = f"%{term.lower()}%"
            rows = self.conn.execute(sql, (wildcard, wildcard, per_term)).fetchall()
            for r in rows:
                t = self._row_to_triple(r)
                if t.id not in seen:
                    seen[t.id] = t
        # Deterministic union ordering: tier ASC, then stored weight DESC —
        # the same ranking surface the single-query version exposed — then
        # id ASC (SNIP-DET), so the union's order is total, not the dict's.
        out = sorted(seen.values(), key=lambda t: (t.tier, -t.weight, t.id if t.id is not None else -1))
        return out[:limit]

    def surfaces_with_claims(self, surfaces: Sequence[str], *,
                             exclude_relations: Sequence[str] = ()) -> set[str]:
        """Which of `surfaces` is the subject or object of a LIVE claim row.

        AW — the prompt's cortex section renders a node only when a claim
        stands behind it: a bare query token ("improve", "help") becomes a
        graph node and fires at 1.00, but nothing in the store says anything
        about it, so it must not be presented beside concepts that have
        claims. `exclude_relations` is the substrate line AU already draws
        (`SUBSTRATE_RELATIONS`): a co-activation row is an association, not
        a claim, and does not count as one here either.

        A presence query rather than `anchored_exact`: that one returns the
        rows and is capped by `limit`, so a hub with more claims than the
        cap could hide the surfaces after it. This returns the surfaces and
        nothing else. Read-only; archived rows excluded as everywhere.
        """
        surfaces = [s for s in surfaces if s]
        if not surfaces:
            return set()
        found: set[str] = set()
        for i in range(0, len(surfaces), 200):
            chunk = surfaces[i:i + 200]
            ph = ",".join("?" for _ in chunk)
            sql = (f"SELECT DISTINCT subject, object FROM triples WHERE archived = 0 "
                   f"AND (subject IN ({ph}) OR object IN ({ph}))")
            params: list = [*chunk, *chunk]
            if exclude_relations:
                xph = ",".join("?" for _ in exclude_relations)
                sql += f" AND relation NOT IN ({xph})"
                params.extend(exclude_relations)
            wanted = set(chunk)
            for r in self.conn.execute(sql, params).fetchall():
                for surface in (r["subject"], r["object"]):
                    if surface in wanted:
                        found.add(surface)
        return found

    def surface_classes(self, surfaces: Sequence[str], *,
                        exclude_relations: Sequence[str] = ()) -> dict[str, str]:
        """BA fix 4 — for each of `surfaces` that is the subject or object of
        a live claim row, the STRONGEST evidence class among those rows
        (earliest in CLASS_ORDER). The cortex section renders it beside the
        concept; a concept carried by a human assertion and by an
        extraction reads as asserted, because that is the best standing the
        store can vouch for. Read-only; the same query shape as
        `surfaces_with_claims`, returning classes instead of presence."""
        surfaces = [s for s in surfaces if s]
        if not surfaces:
            return {}
        rank = {c: i for i, c in enumerate(CLASS_ORDER)}
        best: dict[str, str] = {}
        for i in range(0, len(surfaces), 200):
            chunk = surfaces[i:i + 200]
            ph = ",".join("?" for _ in chunk)
            sql = (f"SELECT subject, object, source FROM triples WHERE archived = 0 "
                   f"AND (subject IN ({ph}) OR object IN ({ph}))")
            params: list = [*chunk, *chunk]
            if exclude_relations:
                xph = ",".join("?" for _ in exclude_relations)
                sql += f" AND relation NOT IN ({xph})"
                params.extend(exclude_relations)
            wanted = set(chunk)
            for r in self.conn.execute(sql, params).fetchall():
                cls = classify(r["source"])
                for surface in (r["subject"], r["object"]):
                    if surface in wanted and rank.get(cls, 99) < rank.get(best.get(surface), 99):
                        best[surface] = cls
        return best

    def anchored_exact(self, surfaces: Sequence[str], *,
                       exclude_relations: Sequence[str] = (),
                       limit: int = 200) -> "list[Triple]":
        """Live beliefs whose subject or object EXACTLY equals a surface.

        AF.1 — the citation forecast's anchor lookup. Deliberately not
        `search_terms`: the forecast must not touch retrieval machinery
        (substring LIKE with a per-term quota IS the lexical channel), and
        exact equality is the right relation between a graph node and the
        belief that minted it — `ingest_triple` uses the whole surface as
        the node id. Read-only; archived rows excluded as everywhere.

        `exclude_relations` keeps substrate-minted relations out for W.2's
        measured reason: co-activation surfaces are assembled from the
        query's own vocabulary and would flood every anchor set.
        """
        surfaces = [s for s in surfaces if s]
        if not surfaces:
            return []
        out: list[Triple] = []
        # Chunk the IN clause well under SQLite's parameter ceiling.
        for i in range(0, len(surfaces), 200):
            chunk = surfaces[i:i + 200]
            ph = ",".join("?" for _ in chunk)
            sql = (f"SELECT * FROM triples WHERE archived = 0 "
                   f"AND (subject IN ({ph}) OR object IN ({ph}))")
            params: list = [*chunk, *chunk]
            if exclude_relations:
                xph = ",".join("?" for _ in exclude_relations)
                sql += f" AND relation NOT IN ({xph})"
                params.extend(exclude_relations)
            sql += " ORDER BY id LIMIT ?"
            params.append(limit - len(out))
            for r in self.conn.execute(sql, params).fetchall():
                out.append(self._row_to_triple(r))
            if len(out) >= limit:
                break
        # One belief can anchor via both ends and via two chunks — dedup.
        seen: dict[int, Triple] = {}
        for t in out:
            if t.id not in seen:
                seen[t.id] = t
        return list(seen.values())

    @staticmethod
    def _row_to_triple(r: Row) -> Triple:
        return Triple(
            id=r["id"], subject=r["subject"], relation=r["relation"], object=r["object"],
            weight=r["weight"], tier=r["tier"], granularity=r["granularity"],
            source=r["source"], fire_count=r["fire_count"],
            created_at=r["created_at"], last_fired=r["last_fired"],
            archived=bool(r["archived"]) if "archived" in r.keys() else False,
            archive_reason=(r["archive_reason"]
                            if "archive_reason" in r.keys() else None),
            archived_at=(r["archived_at"]
                         if "archived_at" in r.keys() else None),
        )

    # ---------- rebalancer (Phase 5 KAIROS calls this) ----------

    def rebalance(self, now: float | None = None) -> dict:
        """Project every belief onto the tier its prominence belongs in.

        F.3 — this is now an **idempotent projection**, not a state machine
        that steps one tier per pass. `band_for(cw, tier)` is a pure function
        of prominence, so running it ten times leaves the store exactly where
        running it once did. That is the whole claim behind "the floor
        absorbs by construction": before F.3 a fully-decayed belief cycled
        `[2,3,2,3,2,3,2,3,2,3]` across ten passes, because
        `TIER_PROMOTE_WEIGHT[3] = 0.0` made `cw >= 0.0` trivially true while
        tier 2 demoted it straight back.

        Never deletes (Law 4). Never alters a record field: it writes the
        `tier` column and nothing else.

        F.2 — three things this used to get wrong, all measured:

        1. **It raised on an unreadable tier.** `TIER_PROMOTE_WEIGHT[t.tier]`
           on a tier-4 row raised `KeyError: 4` out of `run_one_cycle`, which
           was not wrapped, which killed the KAIROS daemon task for the life
           of the process. Unknown tiers are now skipped.
        2. **A skip was indistinguishable from "nothing to do".** Law 8: the
           result declares `skipped` and `skipped_reasons` so an operator
           reads what did not happen, rather than inferring it.
        3. **A failure mid-loop left partial UPDATEs in an open transaction**
           on the long-lived shared connection, where the next unrelated
           `add()` committed them under its own name. Measured: five rows
           moved 1 -> 0 and landed silently on an unrelated write.
           The pass is now atomic — on failure it rolls back and declares.
           Atomic rather than commit-per-row because "a rebalance pass" is the
           unit that means something: a half-applied pass is a store state no
           policy produced, and committing it deliberately is the same corrupt
           state as committing it accidentally, just on purpose.
        """
        now = now or time.time()
        adjustments = self.adjustments()
        promoted = demoted = skipped = 0
        reasons: dict[str, int] = {}

        def _skip(reason: str) -> None:
            nonlocal skipped
            skipped += 1
            reasons[reason] = reasons.get(reason, 0) + 1

        # FZ-2 — the pass is PLANNED first (pure: the band is a function of
        # prominence) and APPLIED second, so that under frozen the moves it
        # would have made are counted exactly and none is written. The only
        # column this pass ever writes is `tier` (FZ-2 S1.5, measured), and
        # that is the whole of what the freeze removes here.
        moves: list[tuple[int, int]] = []
        for t in self.all():
            if t.archived:
                # Record-layer status: a superseded belief has no
                # prominence to rebalance. Not an error — declared anyway.
                _skip("archived")
                continue
            if t.tier not in TIER_ENTRY:
                _skip(f"unknown_tier_{t.tier}")
                logger.warning(
                    "rebalance: triple %s has tier %s, outside the "
                    "0-%d bands; left where it is",
                    t.id, t.tier, LOWEST_TIER)
                continue

            # Q.2 — the band projects EFFECTIVE prominence, so provenance
            # governs arrival as well as decay. `band_for` is unchanged
            # and still a pure function, so rebalance() stays idempotent.
            # S.2 — recalibration is the third factor and it governs the
            # band too. Loaded once per pass rather than per row: a pure
            # sum over stored events, so the projection stays idempotent.
            cw = effective_prominence(t, now, adjustments.get(t.id, 0.0))
            new_tier = band_for(cw, t.tier)
            if new_tier < t.tier:
                promoted += 1
            elif new_tier > t.tier:
                demoted += 1
            if new_tier != t.tier:
                moves.append((new_tier, t.id))

        from backend.learning import learning_frozen, record_skip
        if learning_frozen():
            record_skip("TripleStore.rebalance", len(moves))
            return {"promoted": 0, "demoted": 0, "skipped": skipped,
                    "skipped_reasons": reasons, "frozen": True,
                    "moves_not_made": len(moves)}

        try:
            for new_tier, tid in moves:
                self.conn.execute(
                    "UPDATE triples SET tier = ? WHERE id = ?", (new_tier, tid)
                )
        except Exception:
            self.conn.rollback()
            raise

        self.conn.commit()
        return {"promoted": promoted, "demoted": demoted,
                "skipped": skipped, "skipped_reasons": reasons}
