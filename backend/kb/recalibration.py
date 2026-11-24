"""Packet S.2 — governed adaptation. A weight moves only as a recorded event.

THE CLAIM
    Every ML system moves its weights silently; that is what training is. Here
    a change to what the system believes is an EVENT: author, reason,
    timestamp, a bound it cannot exceed, and a digest sealed into the same
    chain as everything else. Learning happens inside the audit trail rather
    than beside it.

WHERE THE MOVE LANDS, AND WHY IT IS NOT THE CRUX S.0 EXPECTED
    S.0.4 asked how a recalibration lowers a weight without breaking I-2/I-8,
    and treated it as the hard part. F.3 and Q.2 had already answered it.
    `current_weight()` is a read-time projection over a stored value nothing
    writes; Q.2 added `arrival_scale` as a second factor and stated the reason
    in the source — *"Scaling the stored weight would write a number nobody
    claimed, which is a record-layer change."*

    Recalibration is the **third factor in that same product**:

        effective = clamp(current_weight(now) * arrival_scale(source)
                          + adjustment(belief_id), floor, ceiling)

    Additive, not multiplicative, because the envelope's unit is a delta and a
    board approves "±0.05", not "×0.93". The stored `weight` column is not
    touched by any path in this module — enforced, not promised, by
    `tests/test_governed_adaptation.py::test_stored_weight_has_exactly_one_writer`.

    `adjustment()` is a pure sum over stored events, so `rebalance()` stays
    idempotent (F.3's requirement) and re-running a projection ten times still
    leaves the store where running it once did.

WHAT IS RECORDED, INCLUDING THE REFUSALS
    I-6. An outcome that was refused and a recalibration that was refused are
    both written, with the rule that refused them. A governance record that
    holds only what succeeded describes a system in which nothing is ever
    declined, which is not this one and would not survive an auditor.

    `events_for()` returns APPLIED events — what actually moved a number.
    `all_events_for()` returns every attempt. Two names because they answer
    two different questions and collapsing them is how "we never refuse
    anything" becomes true by accident.

REVERSIBILITY (E.2, Q.2 precedent)
    Two, at different altitudes:

    - **The default envelope moves nothing.** `DEFAULT_ENVELOPE` has
      `max_delta = 0.0` and no movable classes, so a deployment that installs
      this module and sets no policy behaves exactly as it did before S.2.
      This is the *right* control, because it is auditable: "no weight may
      move" is a policy someone signed, not a constant someone compiled.
    - **`NEXUS_RECALIBRATION=off`** is the kill switch, for when the question
      is not "what is permitted" but "stop". The read path returns a zero
      adjustment and `apply_outcome` refuses with a declared reason.

ACTOR (O.2a, seam spec §5)
    There is no actor column anywhere in NEXUS and exactly one operator. The
    field exists anyway, and carries `actor_source` alongside id and role, so
    that when an enterprise IdP does propagate identity the change is a VALUE
    change rather than a schema change. Retrofitting attribution is what
    `MAX()` proved impossible.

WHAT THIS CANNOT DO
    It answers only forward. `TEMPORAL_PROBE.md` established that the stored
    weight is a monotone high-water mark with no recoverable history, so no
    recalibration record exists for any movement before this ships, and the
    provenance surface must say so rather than rendering an empty list.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
import os
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from backend.kb.tiered_store import classify
from backend.store import Database, IntegrityError, Row, StoreTarget, connect as store_connect

logger = logging.getLogger("nexus.recalibration")

DEFAULT_KB_DB = Path("data/kb.db")

# Hard bounds on prominence, independent of any envelope. An envelope may
# narrow these; nothing may widen them.
HARD_FLOOR   = 0.0
HARD_CEILING = 1.0

DIRECTIONS   = ("confirming", "contradicting", "neutral")


def recalibration_enabled() -> bool:
    """`NEXUS_RECALIBRATION=off` stops every weight from moving.

    Read at call time so a `.env` loaded after import is honoured — the same
    reason `gate_enabled()` and `OllamaProvider.BASE_URL` resolve late.
    """
    return os.getenv("NEXUS_RECALIBRATION", "on").strip().lower() != "off"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_hash(body: dict) -> str:
    return _sha256(json.dumps(body, sort_keys=True, separators=(",", ":")))


# W0b — A ZERO'S SIGN IS NOT STORED. SQLite keeps a REAL whose value is
# integral as an integer and reads it back as +0.0, so a -0.0 hashed at write
# ("-0.0" in the canonical JSON) can never be recomputed from its row. Every
# refused recalibration of a contradicting outcome with the default zero
# magnitude reached it: `DIRECTION_SIGN[direction] * abs(magnitude)` is
# -1 * 0.0 = -0.0, and `verify_governance_records` has reported each such row
# as a content edit since Z.1 (found by the pre-run data fixture, W0b).
# Forward, the event's four REAL fields are written with an unsigned zero.
# Backward, the verifier accepts a STORED ZERO under either sign — the one bit
# the row cannot carry — and counts every row it resolved that way. It is not
# a wider tolerance: a nonzero value, or a zero with any other field changed,
# still fails. Hashes stay as they were sealed (law 3); `_canonical_hash` is
# unchanged, because JSON text columns DO keep a zero's sign.
RECAL_REAL_FIELDS = ("delta", "prominence_before", "prominence_after", "stored_weight")


def unsigned_zero(x):
    """-0.0 becomes +0.0; every other value is returned unchanged."""
    return x + 0.0 if isinstance(x, float) else x


class EnvelopeViolation(Exception):
    """A recalibration was refused by policy.

    Carries `rule` so a caller can act on the reason rather than parse prose,
    and the message names the limit that refused it (I-6).
    """

    def __init__(self, rule: str, detail: str):
        self.rule = rule
        self.detail = detail
        super().__init__(f"{rule}: {detail}")


# ---------------------------------------------------------------------------
# Actor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)                      # Law 1
class Actor:
    id: str
    role: str = "local_operator"
    # `local` today; `idp` when identity is propagated from an enterprise
    # directory. A value, not a schema change — that is the whole point.
    source: str = "local"


# Z.1 — what kind of agent supplied an outcome. A CLOSED vocabulary, because
# `event_type` demonstrates the alternative: intended values in a comment
# constrain nothing. `person` is a human judgement; `source_system` is a fact
# a system observed and reported (a QC result, a test run, an agency
# response). The default `local_operator` is neither stated nor validated —
# it is what silence has always meant, "the local human operator", and every
# pre-Z.1 row carries it; constraining silence would rewrite what those rows
# mean. The role rides the hashed record body, so unlike a flag it cannot be
# edited without breaking the seal — tests/test_outcome_actor.py proves it.
ACTOR_ROLES = ("person", "source_system")

# Z.1 — validated, NOT extended. `actor_source` answers "how was this
# identity established", never "what kind of agent is this" — the axes are
# orthogonal (a source system's identity can itself be IdP-attested), and
# adding "system" here would destroy the axis the field carries.
ACTOR_SOURCES = ("local", "idp")


DEFAULT_ACTOR = Actor(id="operator")


# AT — the line, carried by the read surface so the ledger's scope is
# statable rather than assumed complete.
REFUSAL_RULE = (
    "recorded: a well-formed, in-vocabulary request for a governed act that "
    "the system declined (Law 3 unconfirmed) — would not is a governance "
    "act. Declared only: a request that was never valid (schema, closed "
    "vocabulary) is a client error; a backend or index that could not is "
    "system state. Ingest-gate refusals are counted per pass, never "
    "ledgered (OQ-4).")


def refusal_ledger_enabled() -> bool:
    """AT's rollback. `off` writes no refusal rows; every refusal is still
    declared on its response, so `off` is exactly the pre-AT store."""
    return os.getenv("NEXUS_REFUSAL_LEDGER", "on").strip().lower() != "off"


def _as_actor(actor: "Actor | str | None") -> Actor:
    if actor is None:
        return DEFAULT_ACTOR
    if isinstance(actor, Actor):
        return actor
    return Actor(id=str(actor))


# ---------------------------------------------------------------------------
# The envelope — a policy object, not a constant
# ---------------------------------------------------------------------------

@dataclass(frozen=True)                      # Law 1
class Envelope:
    """What may move, by how much, for whom.

    A bare max-delta cannot be approved by a change-control board, and
    "bounded by limits you set" is only true if the limits are a thing that
    can be set, named and audited. So this is versioned, recorded, and cited
    by every recalibration it permits.
    """
    version: str
    # PROVISIONAL defaults, and they are deliberately the immovable ones:
    # installing this module changes no behaviour until a policy says so.
    max_delta: float = 0.0
    movable_classes: tuple[str, ...] = ()
    frozen_belief_ids: tuple[int, ...] = ()
    # Total absolute movement permitted for one belief across all events.
    # None = uncapped, which is a real choice and must be made explicitly.
    cumulative_max: float | None = None
    floor: float = HARD_FLOOR
    ceiling: float = HARD_CEILING

    def to_dict(self) -> dict:
        d = asdict(self)
        d["movable_classes"] = list(self.movable_classes)
        d["frozen_belief_ids"] = list(self.frozen_belief_ids)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Envelope":
        return cls(
            version=d["version"],
            max_delta=float(d.get("max_delta", 0.0)),
            movable_classes=tuple(d.get("movable_classes") or ()),
            frozen_belief_ids=tuple(int(i) for i in d.get("frozen_belief_ids") or ()),
            cumulative_max=(None if d.get("cumulative_max") is None
                            else float(d["cumulative_max"])),
            floor=float(d.get("floor", HARD_FLOOR)),
            ceiling=float(d.get("ceiling", HARD_CEILING)),
        )


# The policy in force when nobody has set one. Reproduces pre-S.2 behaviour
# exactly: no class may move and the permitted delta is zero.
DEFAULT_ENVELOPE = Envelope(version="v0-immovable")


@dataclass(frozen=True)
class EnvelopeRecord:
    version: str
    ts: float
    actor: Actor
    reason: str
    envelope: Envelope
    record_hash: str
    sealed_block: int | None = None
    scope: str = "default"


@dataclass(frozen=True)
class OutcomeEvent:
    event_id: str
    ts: float
    event_type: str
    decision: str
    direction: str
    actor: Actor
    references: dict
    workflow_ref: str = ""
    notes: str = ""
    accepted: bool = True
    refusal_reason: str = ""
    record_hash: str = ""
    sealed_block: int | None = None
    scope: str = "default"
    # AR (advice recall) — present only when the outcome's `notes` began
    # `superseded_by:`: how many sealed records rested on the referenced
    # claims, and the route to the list. Hashed into the row only when
    # present, so every earlier row recomputes unchanged.
    recall_count: int | None = None
    recall_route: str | None = None


# AR — the reason prefix that makes an outcome a supersede. A reason on the
# existing act, not a new act kind: nothing else about the outcome changes.
SUPERSEDE_PREFIX = "superseded_by:"


def is_supersede_reason(notes: str | None) -> bool:
    return (notes or "").strip().lower().startswith(SUPERSEDE_PREFIX)


@dataclass(frozen=True)
class RecalibrationEvent:
    event_id: str
    ts: float
    belief_id: int
    delta: float
    prominence_before: float
    prominence_after: float
    stored_weight: float          # the untouched record value, kept for audit
    envelope_version: str
    actor: Actor
    outcome_ref: str = ""
    applied: bool = True
    refusal_rule: str = ""
    refusal_detail: str = ""
    record_hash: str = ""
    sealed_block: int | None = None
    scope: str = "default"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS envelope_versions (
    version       TEXT PRIMARY KEY,
    ts            REAL NOT NULL,
    actor_id      TEXT NOT NULL,
    actor_role    TEXT NOT NULL,
    actor_source  TEXT NOT NULL,
    reason        TEXT NOT NULL,
    policy_json   TEXT NOT NULL,
    record_hash   TEXT NOT NULL,
    sealed_block  INTEGER,
    scope         TEXT NOT NULL DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS outcome_events (
    event_id        TEXT PRIMARY KEY,
    ts              REAL NOT NULL,
    event_type      TEXT NOT NULL,
    decision        TEXT NOT NULL,
    direction       TEXT NOT NULL,
    actor_id        TEXT NOT NULL,
    actor_role      TEXT NOT NULL,
    actor_source    TEXT NOT NULL,
    references_json TEXT NOT NULL,
    workflow_ref    TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    accepted        INTEGER NOT NULL,
    refusal_reason  TEXT NOT NULL DEFAULT '',
    record_hash     TEXT NOT NULL,
    sealed_block    INTEGER,
    scope           TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_outcome_ts ON outcome_events(ts);

CREATE TABLE IF NOT EXISTS recalibration_events (
    event_id          TEXT PRIMARY KEY,
    ts                REAL NOT NULL,
    belief_id         INTEGER NOT NULL,
    delta             REAL NOT NULL,
    prominence_before REAL NOT NULL,
    prominence_after  REAL NOT NULL,
    stored_weight     REAL NOT NULL,
    envelope_version  TEXT NOT NULL,
    actor_id          TEXT NOT NULL,
    actor_role        TEXT NOT NULL,
    actor_source      TEXT NOT NULL,
    outcome_ref       TEXT NOT NULL DEFAULT '',
    applied           INTEGER NOT NULL,
    refusal_rule      TEXT NOT NULL DEFAULT '',
    refusal_detail    TEXT NOT NULL DEFAULT '',
    record_hash       TEXT NOT NULL,
    sealed_block      INTEGER,
    scope             TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_recal_belief ON recalibration_events(belief_id);
CREATE INDEX IF NOT EXISTS idx_recal_ts ON recalibration_events(ts);
"""


class RecalibrationStore:
    """kb.db-backed governance record: envelopes, outcomes, recalibrations.

    kb.db and not archive.db, per the write-topology rule — `archive.db` keeps
    exactly one writer (KAIROS), and these are written on the request path.
    The seal still rides KAIROS, exactly as lineage records do.
    """

    def __init__(self, db_path: "Path | str | StoreTarget" = DEFAULT_KB_DB):
        # W3b — a path is SQLite exactly as before; a StoreTarget names the
        # backend (`backend.store`).
        self.target = StoreTarget.of(db_path)
        self.db_path = self.target.path
        self.conn: Database = store_connect(self.target)
        self.conn.executescript(SCHEMA_SQL)      # additive, re-runnable
        # AD — additive scope column on existing stores (F.2's `archived`
        # shape): old rows read 'default', nothing stored is altered. The
        # 'default'-before-vs-after ambiguity is decided by scope_meta's
        # recordable_since (shared with LineageStore — one instant for the
        # whole kb.db, whichever store migrates first writes it).
        for table in ("envelope_versions", "outcome_events",
                      "recalibration_events"):
            cols = {c.name for c in self.conn.columns(table)}
            if "scope" not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN "
                                  f"scope TEXT NOT NULL DEFAULT 'default'")
        # AR — additive, nullable, hashed only when present: the supersede
        # outcome's recall count and route. Old rows read NULL and recompute.
        outcome_cols = {c.name for c in self.conn.columns("outcome_events")}
        for col, typ in (("recall_count", "INTEGER"), ("recall_route", "TEXT")):
            if col not in outcome_cols:
                self.conn.execute(f"ALTER TABLE outcome_events ADD COLUMN {col} {typ}")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS scope_meta ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "recordable_since REAL NOT NULL)")
        # AL — the archival act ledger shares kb.db and rides this store's
        # seal enumeration. Its writer is TripleStore (the archive/unarchive
        # chokepoint); ensured here too so a RecalibrationStore opened on a
        # fresh path can enumerate and verify without a TripleStore having
        # run first. Additive, re-runnable.
        from backend.kb.schema import ARCHIVAL_EVENTS_DDL, REFUSED_REQUESTS_DDL, WORK_UNIT_EVENTS_DDL
        self.conn.executescript(ARCHIVAL_EVENTS_DDL)
        self.conn.executescript(REFUSED_REQUESTS_DDL)   # AT — additive
        self.conn.executescript(WORK_UNIT_EVENTS_DDL)   # W6 — additive; WorkUnitStore writes it, this store seals it
        from backend.kb.schema import RECORD_TOMBSTONES_DDL
        self.conn.executescript(RECORD_TOMBSTONES_DDL)  # W8 (D9) — LineageStore writes it, this store seals it
        self.conn.execute(
            "INSERT OR IGNORE INTO scope_meta (id, recordable_since) "
            "VALUES (1, ?)", (time.time(),))
        self.conn.commit()

    def scope_recordable_since(self) -> "float | None":
        row = self.conn.execute(
            "SELECT recordable_since FROM scope_meta").fetchone()
        return row["recordable_since"] if row else None

    def close(self) -> None:
        self.conn.close()

    # ---------- envelope ----------

    def record_envelope(self, envelope: Envelope, *,
                        actor: "Actor | str | None" = None,
                        reason: str, scope: str = "default") -> EnvelopeRecord:
        """Install a policy version. Changing the bound is itself an event.

        `reason` is required and not defaulted: an envelope change with no
        stated reason is the thing a change-control board exists to prevent,
        and a default would make forgetting it silent.
        """
        who = _as_actor(actor)
        ts = time.time()
        scope = (scope or "default").strip() or "default"
        rec_hash = _canonical_hash(
            self._envelope_body(envelope, ts, who, reason, scope))
        try:
            self.conn.execute(
                "INSERT INTO envelope_versions (version, ts, actor_id, "
                "actor_role, actor_source, reason, policy_json, record_hash, "
                "scope) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (envelope.version, ts, who.id, who.role, who.source, reason,
                 json.dumps(envelope.to_dict(), sort_keys=True), rec_hash,
                 scope),
            )
        except IntegrityError as e:
            # A version is a name for a decision. Reusing one to mean something
            # else makes every recalibration citing it ambiguous, which is the
            # `MAX()` defect wearing a different hat.
            raise EnvelopeViolation(
                "version_reused",
                f"envelope version {envelope.version!r} already exists; "
                f"policy versions are immutable") from e
        self.conn.commit()
        logger.info("envelope %s installed by %s: %s",
                    envelope.version, who.id, reason)
        return EnvelopeRecord(version=envelope.version, ts=ts, actor=who,
                              reason=reason, envelope=envelope,
                              record_hash=rec_hash, scope=scope)

    def envelope_history(self) -> list[EnvelopeRecord]:
        return [self._envelope_row(r) for r in self.conn.execute(
            "SELECT * FROM envelope_versions ORDER BY ts ASC")]

    def active_envelope(self) -> Envelope:
        """The most recently installed policy, or the immovable default.

        The default is not "no policy" — it is the policy that nothing moves,
        which is the honest reading of an operator who has not decided yet.
        """
        row = self._envelope_row_in_force()
        return Envelope.from_dict(json.loads(row["policy_json"])) if row \
            else DEFAULT_ENVELOPE

    def _envelope_row_in_force(self):
        return self.conn.execute(
            "SELECT * FROM envelope_versions ORDER BY ts DESC LIMIT 1").fetchone()

    def installed_envelope_version(self) -> str | None:
        """W6 — the version of the installed policy in force, or None when no
        policy is installed and the compiled default is.

        Read from whether a row exists, never from the name. The compiled
        default is no row here, so its name is free: a policy with other
        limits can be installed as `v0-immovable`, and a reader that compared
        the name would report "the default" while that policy was in force.
        """
        row = self._envelope_row_in_force()
        return Envelope.from_dict(json.loads(row["policy_json"])).version if row else None

    @staticmethod
    def _envelope_row(r: Row) -> EnvelopeRecord:
        return EnvelopeRecord(
            version=r["version"], ts=r["ts"],
            actor=Actor(r["actor_id"], r["actor_role"], r["actor_source"]),
            reason=r["reason"],
            envelope=Envelope.from_dict(json.loads(r["policy_json"])),
            record_hash=r["record_hash"], sealed_block=r["sealed_block"],
            scope=r["scope"])

    # ---------- outcomes ----------

    def record_outcome(
        self, *, event_type: str, decision: str, direction: str,
        references: dict, actor: "Actor | str | None" = None,
        workflow_ref: str = "", notes: str = "",
        accepted: bool = True, refusal_reason: str = "",
        event_id: str | None = None, scope: str = "default",
        recall_count: int | None = None, recall_route: str | None = None,
    ) -> OutcomeEvent:
        """Record one outcome signal — accepted or refused (I-6).

        A refused outcome is written too. A record that holds only what
        succeeded describes a system that never declines anything.

        AR — `recall_count` / `recall_route` are supplied by the caller only
        for a supersede (`notes` beginning `superseded_by:`); both ride the
        row and the hash when present and are absent otherwise.
        """
        who = _as_actor(actor)
        eid = event_id or str(uuid.uuid4())
        ts = time.time()
        scope = (scope or "default").strip() or "default"
        rec_hash = _canonical_hash(self._outcome_body(
            eid, ts, event_type, decision, direction, who, references,
            workflow_ref, bool(accepted), refusal_reason, scope,
            recall_count=recall_count, recall_route=recall_route))
        self.conn.execute(
            "INSERT INTO outcome_events (event_id, ts, event_type, decision, "
            "direction, actor_id, actor_role, actor_source, references_json, "
            "workflow_ref, notes, accepted, refusal_reason, record_hash, "
            "scope, recall_count, recall_route) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, ts, event_type, decision, direction, who.id, who.role,
             who.source, json.dumps(references, sort_keys=True), workflow_ref,
             notes, int(bool(accepted)), refusal_reason, rec_hash, scope,
             recall_count, recall_route),
        )
        self.conn.commit()
        return OutcomeEvent(
            event_id=eid, ts=ts, event_type=event_type, decision=decision,
            direction=direction, actor=who, references=references,
            workflow_ref=workflow_ref, notes=notes, accepted=bool(accepted),
            refusal_reason=refusal_reason, record_hash=rec_hash, scope=scope,
            recall_count=recall_count, recall_route=recall_route)

    def superseded(self) -> dict:
        """AR coverage: the accepted supersede outcomes, read from the record
        each time — a count, never a stored number. `claims` are the distinct
        belief ids those outcomes referenced; `since` is the first such act."""
        claims: set[int] = set()
        since: float | None = None
        n = 0
        # SELECTS ON THE HASHED FIELD. `notes` is outside the outcome hash
        # (OQ-5), so a row's notes can be rewritten under an intact seal;
        # `recall_count` is written only for a supersede and is sealed into
        # the row, so this count cannot be moved by editing prose.
        for r in self.conn.execute(
                "SELECT ts, references_json FROM outcome_events "
                "WHERE accepted = 1 AND recall_count IS NOT NULL ORDER BY ts ASC"):
            n += 1
            since = r["ts"] if since is None else since
            refs = json.loads(r["references_json"] or "{}")
            for b in (refs.get("belief_ids") or []):
                try:
                    claims.add(int(b))
                except (TypeError, ValueError):
                    pass
        return {"events": n, "claims": sorted(claims), "since": since}

    def outcomes(self, limit: int = 50) -> list[OutcomeEvent]:
        return [self._outcome_row(r) for r in self.conn.execute(
            "SELECT * FROM outcome_events ORDER BY ts DESC LIMIT ?", (limit,))]

    def get_outcome(self, event_id: str) -> OutcomeEvent | None:
        r = self.conn.execute(
            "SELECT * FROM outcome_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return self._outcome_row(r) if r else None

    @staticmethod
    def _outcome_row(r: Row) -> OutcomeEvent:
        return OutcomeEvent(
            event_id=r["event_id"], ts=r["ts"], event_type=r["event_type"],
            decision=r["decision"], direction=r["direction"],
            actor=Actor(r["actor_id"], r["actor_role"], r["actor_source"]),
            references=json.loads(r["references_json"]),
            workflow_ref=r["workflow_ref"], notes=r["notes"],
            accepted=bool(r["accepted"]), refusal_reason=r["refusal_reason"],
            record_hash=r["record_hash"], sealed_block=r["sealed_block"],
            scope=r["scope"],
            recall_count=(r["recall_count"] if "recall_count" in r.keys() else None),
            recall_route=(r["recall_route"] if "recall_route" in r.keys() else None))

    # ---------- recalibrations ----------

    def _insert_recalibration(self, ev: RecalibrationEvent) -> RecalibrationEvent:
        # W0b — an unsigned zero in every REAL field, so the hash is the one
        # the row will read back (see RECAL_REAL_FIELDS).
        ev = replace(ev, **{f: unsigned_zero(getattr(ev, f)) for f in RECAL_REAL_FIELDS})
        rec_hash = _canonical_hash(self._recal_body(
            ev.event_id, ev.ts, ev.belief_id, ev.delta, ev.prominence_before,
            ev.prominence_after, ev.stored_weight, ev.envelope_version,
            ev.actor, ev.outcome_ref, ev.applied, ev.refusal_rule, ev.scope))
        self.conn.execute(
            "INSERT INTO recalibration_events (event_id, ts, belief_id, delta, "
            "prominence_before, prominence_after, stored_weight, "
            "envelope_version, actor_id, actor_role, actor_source, "
            "outcome_ref, applied, refusal_rule, refusal_detail, record_hash, "
            "scope) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ev.event_id, ev.ts, ev.belief_id, ev.delta, ev.prominence_before,
             ev.prominence_after, ev.stored_weight, ev.envelope_version,
             ev.actor.id, ev.actor.role, ev.actor.source, ev.outcome_ref,
             int(ev.applied), ev.refusal_rule, ev.refusal_detail, rec_hash,
             ev.scope),
        )
        self.conn.commit()
        return RecalibrationEvent(**{**asdict_shallow(ev), "record_hash": rec_hash})

    def events_for(self, belief_id: int) -> list[RecalibrationEvent]:
        """APPLIED recalibrations for one belief, oldest first.

        Distinct from `all_events_for()` on purpose: "what moved this number"
        and "what was attempted against it" are different questions, and one
        method answering both is how "we never refuse anything" becomes true
        by accident.
        """
        return [self._recal_row(r) for r in self.conn.execute(
            "SELECT * FROM recalibration_events WHERE belief_id = ? AND "
            "applied = 1 ORDER BY ts ASC", (belief_id,))]

    def all_events_for(self, belief_id: int) -> list[RecalibrationEvent]:
        return [self._recal_row(r) for r in self.conn.execute(
            "SELECT * FROM recalibration_events WHERE belief_id = ? "
            "ORDER BY ts ASC", (belief_id,))]

    def all_events_by_belief(self) -> dict[int, list[RecalibrationEvent]]:
        """{belief_id: applied events} — what S.1's invariant consults to
        decide whether an observed move was accounted for."""
        out: dict[int, list[RecalibrationEvent]] = {}
        for r in self.conn.execute(
                "SELECT * FROM recalibration_events WHERE applied = 1 "
                "ORDER BY ts ASC"):
            out.setdefault(int(r["belief_id"]), []).append(self._recal_row(r))
        return out

    def adjustments(self) -> dict[int, float]:
        """{belief_id: summed applied delta}. The read path's third factor.

        A pure function of stored rows, which is what keeps `rebalance()`
        idempotent — the same projection run twice reads the same sum.
        """
        if not recalibration_enabled():
            return {}
        # W3b D6 — summed in Python with `math.fsum`, never in SQL. SQLite's
        # SUM over REAL is exactly rounded (measured equal to fsum on random
        # deltas; `tests/test_w3b_store_interface.py`), so the SQLite path is
        # unchanged to the bit; Postgres's `sum(double precision)` is a naive
        # accumulation in scan order and must never reach `ranking_prominence`
        # or the hashed `prominence_before`/`prominence_after`.
        by_belief: dict[int, list[float]] = {}
        for r in self.conn.execute(
                "SELECT belief_id, delta FROM recalibration_events WHERE applied = 1"):
            by_belief.setdefault(int(r["belief_id"]), []).append(float(r["delta"]))
        return {bid: math.fsum(deltas) for bid, deltas in by_belief.items()}

    def _applied_deltas(self, belief_id: int) -> list[float]:
        return [float(r["delta"]) for r in self.conn.execute(
            "SELECT delta FROM recalibration_events WHERE belief_id = ? AND applied = 1",
            (belief_id,))]

    def adjustment_for(self, belief_id: int) -> float:
        if not recalibration_enabled():
            return 0.0
        deltas = self._applied_deltas(belief_id)
        return math.fsum(deltas) if deltas else 0.0          # D6: Python's sum, both backends

    def cumulative_movement(self, belief_id: int) -> float:
        """Total ABSOLUTE movement, for the envelope's cumulative cap. Summed
        magnitudes, not the net — a belief walked up and down ten times has
        moved, whatever the net says."""
        deltas = self._applied_deltas(belief_id)
        return math.fsum(abs(d) for d in deltas) if deltas else 0.0

    @staticmethod
    def _recal_row(r: Row) -> RecalibrationEvent:
        return RecalibrationEvent(
            event_id=r["event_id"], ts=r["ts"], belief_id=int(r["belief_id"]),
            delta=r["delta"], prominence_before=r["prominence_before"],
            prominence_after=r["prominence_after"],
            stored_weight=r["stored_weight"],
            envelope_version=r["envelope_version"],
            actor=Actor(r["actor_id"], r["actor_role"], r["actor_source"]),
            outcome_ref=r["outcome_ref"], applied=bool(r["applied"]),
            scope=r["scope"],
            refusal_rule=r["refusal_rule"], refusal_detail=r["refusal_detail"],
            record_hash=r["record_hash"], sealed_block=r["sealed_block"])

    # ---------- AT — the refusal ledger ----------

    def record_refusal(self, *, route: str, rule: str, detail: str,
                       actor: "Actor | str | None" = None,
                       scope: str = "default",
                       request: dict | None = None) -> "dict | None":
        """Record that a well-formed request for a governed act was
        declined. Returns the row as a dict, or None when the ledger is off
        (`NEXUS_REFUSAL_LEDGER=off` — the pre-AT store, declared only).

        The request is stored as a sha256 over its canonical JSON: enough to
        recognise the attempt again, not enough to make the ledger a store
        of arbitrary client text."""
        if not refusal_ledger_enabled():
            return None
        from backend.kb.schema import refusal_body, refusal_hash
        who = _as_actor(actor)
        eid = str(uuid.uuid4())
        ts = time.time()
        scope = (scope or "default").strip() or "default"
        digest = _canonical_hash(request or {})
        body = refusal_body(eid, ts, route, rule, detail, who.id, who.role,
                            who.source, scope, digest)
        rec_hash = refusal_hash(body)
        self.conn.execute(
            "INSERT INTO refused_requests (event_id, ts, route, rule, detail, "
            "actor_id, actor_role, actor_source, scope, request_sha256, "
            "record_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, ts, route, rule, detail, who.id, who.role, who.source,
             scope, digest, rec_hash))
        self.conn.commit()
        return {**body, "record_hash": rec_hash, "sealed_block": None}

    def refusals(self, limit: int = 50) -> list[dict]:
        """Newest first, both ledgers: the generic table and the outcome
        route's own refused rows, each tagged with the ledger it lives in,
        so the read surface is one list rather than two."""
        out = [{
            "ledger": "refused_requests", "event_id": r["event_id"],
            "ts": r["ts"], "route": r["route"], "rule": r["rule"],
            "detail": r["detail"],
            "actor": {"id": r["actor_id"], "role": r["actor_role"],
                      "source": r["actor_source"]},
            "scope": r["scope"], "request_sha256": r["request_sha256"],
            "record_hash": r["record_hash"], "sealed_block": r["sealed_block"],
        } for r in self.conn.execute(
            "SELECT * FROM refused_requests ORDER BY ts DESC LIMIT ?", (limit,))]
        out += [{
            "ledger": "outcome_events", "event_id": r["event_id"],
            "ts": r["ts"], "route": "/outcome/confirm",
            "rule": (r["refusal_reason"] or "").split(":")[0],
            "detail": r["refusal_reason"],
            "actor": {"id": r["actor_id"], "role": r["actor_role"],
                      "source": r["actor_source"]},
            "scope": r["scope"], "request_sha256": None,
            "record_hash": r["record_hash"], "sealed_block": r["sealed_block"],
        } for r in self.conn.execute(
            "SELECT * FROM outcome_events WHERE accepted = 0 "
            "ORDER BY ts DESC LIMIT ?", (limit,))]
        out.sort(key=lambda r: -r["ts"])
        return out[:limit]

    def refusal_coverage(self) -> dict:
        g = self.conn.execute(
            "SELECT COUNT(*) AS n, MIN(ts) AS begin FROM refused_requests").fetchone()
        o = self.conn.execute(
            "SELECT COUNT(*) AS n, MIN(ts) AS begin FROM outcome_events "
            "WHERE accepted = 0").fetchone()
        begins = [b for b in (g["begin"], o["begin"]) if b is not None]
        return {
            "enabled": refusal_ledger_enabled(),
            "events": int(g["n"]) + int(o["n"]),
            "by_ledger": {"refused_requests": int(g["n"]),
                          "outcome_events": int(o["n"])},
            "records_begin_at": min(begins) if begins else None,
            "rule": REFUSAL_RULE,
            "note": ("an empty ledger means nothing recorded yet, not nothing "
                     "ever refused — refusals before the ledger shipped were "
                     "never recorded (forward-only, the records_begin_at "
                     "class); never-valid requests and could-not conditions "
                     "are declared on the response and recorded nowhere"),
        }

    def records_begin_at(self) -> float | None:
        """Earliest governance row in this store, or None if there are none.

        S.3 — the honest edge of the record. `TEMPORAL_PROBE.md` established
        that stored weight is a monotone high-water mark with no recoverable
        history, so nothing before this timestamp can ever be reconstructed.
        A provenance surface that omitted this would let an empty history read
        as "this belief never moved" when it means "nothing was recorded".
        """
        rows = [self.conn.execute(f"SELECT MIN(ts) AS t FROM {table}").fetchone()["t"]
                for table in ("envelope_versions", "outcome_events",
                              "recalibration_events")]
        present = [r for r in rows if r is not None]
        return min(present) if present else None

    # ---------- AG — the budget report ----------

    def budget_report(self) -> dict:
        """Budget used against budget permitted — a read-only aggregate.

        AG. This is a report about GOVERNED MOVEMENT and nothing else (I-7):
        it aggregates recalibration_events, the layer where an actor must
        use the governed door. It is not a contamination detector — the
        belief layer's door is ungoverned by identity — and the payload says
        so on its surface (`note`, `declarations`), because a governance
        report that looks complete is worse than one that names what it
        omits (I-6).

        VERSION ANCHORING: consumption is grouped by the envelope_version
        each event itself recorded — the signed fact, not a time join. A
        belief whose movement spans versions is reported as spanning, with
        per-version figures kept apart; the one summed figure per belief is
        `consumed_lifetime`, present because it is exactly the quantity
        `apply_outcome`'s cumulative_max check compares against the ACTIVE
        cap — the policy's own construction, not this report's invention.

        ROLES ARE NOT SUMMED: actors are keyed (id, role, source), so a
        person and a source system sharing an id remain separate rows.

        READ-ONLY, pinned: the store's bytes are md5-identical across a
        call, and an AST guard holds this method to reads
        (tests/test_budget_report.py). NOTHING MAY ACT ON THIS REPORT — the
        companion pin holds `_insert_recalibration` to exactly its current
        callers, because that insert performs no envelope checks and an
        automated responder calling it would be an ungoverned write path
        producing events that verify clean.
        """
        active = self.active_envelope()
        begin = self.records_begin_at()

        per_version: dict[str, dict] = {}
        per_belief: dict[int, dict] = {}
        per_actor: dict[tuple, dict] = {}
        applied_n = refused_n = movements_n = zero_n = 0
        total_movement = 0.0
        refusal_reasons: dict[str, int] = {}

        # AR.1 — EVENTS AND MOVEMENT ARE COUNTED APART (I-7). A `neutral`
        # outcome under a permitting envelope writes an applied event with
        # delta 0.0: a real adjudication, on the record, shown as a step in
        # provenance — and NOT a movement. `applied` counts recorded
        # events, `movements` counts the non-zero ones, `zero_delta` names
        # the rest, at every level; consumed figures sum |delta| and never
        # counted a zero as anything.
        def _version(v: str) -> dict:
            return per_version.setdefault(v, {
                "applied": 0, "refused": 0, "consumed": 0.0,
                "movements": 0, "zero_delta": 0})

        for r in self.conn.execute(
                "SELECT belief_id, delta, envelope_version, actor_id, "
                "actor_role, actor_source, ts, applied, refusal_rule "
                "FROM recalibration_events ORDER BY ts ASC"):
            akey = (r["actor_id"], r["actor_role"], r["actor_source"])
            actor = per_actor.setdefault(akey, {
                "actor_id": r["actor_id"], "actor_role": r["actor_role"],
                "actor_source": r["actor_source"], "consumed": 0.0,
                "events": 0, "movements": 0, "zero_delta": 0, "refused": 0,
                "first_ts": r["ts"], "last_ts": r["ts"],
                "versions": set()})
            actor["last_ts"] = r["ts"]
            # `|=` rather than `.add`: the read-only AST guard forbids any
            # call named `add` in this method so `store.add` can never creep
            # in, and a set method sharing that name would be a false hit.
            actor["versions"] |= {r["envelope_version"]}
            if not r["applied"]:
                refused_n += 1
                actor["refused"] += 1
                rule = r["refusal_rule"] or "unrecorded"
                refusal_reasons[rule] = refusal_reasons.get(rule, 0) + 1
                _version(r["envelope_version"])["refused"] += 1
                continue
            magnitude = abs(float(r["delta"]))
            moved = magnitude > 0.0
            applied_n += 1
            movements_n += int(moved)
            zero_n += int(not moved)
            total_movement += magnitude
            actor["consumed"] += magnitude
            actor["events"] += 1
            actor["movements"] += int(moved)
            actor["zero_delta"] += int(not moved)
            v = _version(r["envelope_version"])
            v["applied"] += 1
            v["movements"] += int(moved)
            v["zero_delta"] += int(not moved)
            v["consumed"] += magnitude
            belief = per_belief.setdefault(int(r["belief_id"]), {
                "belief_id": int(r["belief_id"]),
                "by_version": {}, "consumed_lifetime": 0.0, "events": 0,
                "movements": 0, "zero_delta": 0})
            belief["events"] += 1
            belief["movements"] += int(moved)
            belief["zero_delta"] += int(not moved)
            belief["consumed_lifetime"] += magnitude
            bv = belief["by_version"]
            bv[r["envelope_version"]] = (
                bv.get(r["envelope_version"], 0.0) + magnitude)

        cap = active.cumulative_max
        belief_rows = []
        for row in sorted(per_belief.values(),
                          key=lambda x: -x["consumed_lifetime"]):
            row["spans_versions"] = len(row["by_version"]) > 1
            row["active_cumulative_max"] = cap
            row["remaining_under_active_cap"] = (
                None if cap is None
                else max(0.0, cap - row["consumed_lifetime"]))
            belief_rows.append(row)

        actor_rows = []
        for row in sorted(per_actor.values(), key=lambda x: -x["consumed"]):
            row["versions"] = sorted(row["versions"])
            actor_rows.append(row)

        return {
            "note": BUDGET_NOTE,
            "declarations": {
                "cap_scope": (
                    "cumulative_max caps movement per belief only. There is "
                    "no per-actor or store-wide budget in the envelope, so "
                    "an actor spreading small deltas across many beliefs "
                    "consumes nothing any cap watches — this report can "
                    "show that concentration; the policy cannot currently "
                    "refuse it."),
                "governed_movement_only": (
                    "only recalibration events appear here. Claims entering "
                    "the store are not recalibrations and are invisible to "
                    "this report."),
                "attribution_limit": (
                    "beliefs carry an evidence class, not an identity, so "
                    "contribution cannot be attributed to a person at all — "
                    "only governed movement can."),
                "coverage": {
                    "records_begin_at": begin,
                    "note": ("movement before records_begin_at was never "
                             "recorded and is invisible here; stored weight "
                             "is a monotone high-water mark with no "
                             "recoverable history" if begin is not None else
                             "no governance rows exist; nothing has been "
                             "recorded yet, and nothing earlier can be"),
                },
            },
            "active_envelope": active.to_dict(),
            "envelope_versions": [{
                "version": e.version, "ts": e.ts,
                "actor": asdict(e.actor), "reason": e.reason,
                "cumulative_max": e.envelope.cumulative_max,
                "max_delta": e.envelope.max_delta,
            } for e in self.envelope_history()],
            "store_wide": {
                "applied": applied_n,
                "movements": movements_n,
                "zero_delta": zero_n,
                "refused": refused_n,
                "total_movement": round(total_movement, 6),
                "counting": (
                    "applied counts recorded events; movements counts the "
                    "events with a non-zero delta; zero_delta counts applied "
                    "events that moved nothing (a neutral adjudication under "
                    "a permitting envelope, kept on the record). "
                    "applied == movements + zero_delta at every level; "
                    "consumed figures sum |delta| and are unaffected."),
                "refusal_reasons": refusal_reasons,
                "by_version": {k: {**v, "consumed": round(v["consumed"], 6)}
                               for k, v in per_version.items()},
            },
            "per_belief": belief_rows,
            "per_actor": actor_rows,
        }

    # ---------- sealing (S.0.7 — the lineage path, reused) ----------

    def unsealed(self) -> list[tuple[str, str]]:
        """(event_id, record_hash) for every governance row not yet sealed.

        Every governance table (six since W6), because an envelope change
        that is not chain-covered is exactly as forgeable as a recalibration
        that is not — and the envelope is the thing that says the
        recalibration was in bounds. AL added archival_events for the same
        reason: an act that sets a claim aside is a governance act, and an
        unsealed one is rewritable. AT added refused_requests and W6
        work_unit_events.
        """
        out: list[tuple[str, str]] = []
        for sql, key in (
            ("SELECT version AS k, record_hash FROM envelope_versions "
             "WHERE sealed_block IS NULL", "version"),
            ("SELECT event_id AS k, record_hash FROM outcome_events "
             "WHERE sealed_block IS NULL", "event_id"),
            ("SELECT event_id AS k, record_hash FROM recalibration_events "
             "WHERE sealed_block IS NULL", "event_id"),
            ("SELECT event_id AS k, record_hash FROM archival_events "
             "WHERE sealed_block IS NULL", "event_id"),
            # AT — a declined governed act is a governance record too.
            ("SELECT event_id AS k, record_hash FROM refused_requests "
             "WHERE sealed_block IS NULL", "event_id"),
            # W6 — a unit's close, and its reversal, are governance records.
            ("SELECT event_id AS k, record_hash FROM work_unit_events "
             "WHERE sealed_block IS NULL", "event_id"),
            # W8 (D9) — so is a record's content disposed of.
            ("SELECT event_id AS k, record_hash FROM record_tombstones "
             "WHERE sealed_block IS NULL", "event_id"),
        ):
            out.extend((r["k"], r["record_hash"]) for r in self.conn.execute(sql))
        return out

    def mark_sealed(self, keys: list[str], block_index: int) -> int:
        if not keys:
            return 0
        n = 0
        for table, col in (("envelope_versions", "version"),
                           ("outcome_events", "event_id"),
                           ("recalibration_events", "event_id"),
                           ("archival_events", "event_id"),
                           ("refused_requests", "event_id"),
                           ("work_unit_events", "event_id"),
                           ("record_tombstones", "event_id")):
            cur = self.conn.executemany(
                f"UPDATE {table} SET sealed_block = ? "
                f"WHERE {col} = ? AND sealed_block IS NULL",
                [(block_index, k) for k in keys])
            n += cur.rowcount or 0
        self.conn.commit()
        return n

    @staticmethod
    def seal_digest(record_hashes: list[str]) -> str:
        """Same construction as `LineageStore.seal_digest` — deliberately, so
        a verifier reads one rule rather than two that happen to agree."""
        return _sha256("\n".join(sorted(record_hashes)))

    # ---------- hash bodies (Z.1 — one rule, shared by writer and verifier) --
    #
    # Extracted so `verify_governance_records` recomputes by the SAME
    # construction the writers hashed, instead of a second copy that drifts.
    # These define what the record hash covers; `notes` and `refusal_detail`
    # are deliberately outside (recorded as an open question with proof —
    # changing coverage would orphan every existing hash, an I-9 decision).

    @staticmethod
    def _with_scope(body: dict, scope: str) -> dict:
        """AD — sealed into the hash ONLY when not the default, the exact
        rule ArchiveBlock.compute_hash uses for `kind`: every record hashed
        before the field existed recomputes unchanged, and a STATED scope is
        tamper-evident rather than rewritable under an intact seal (the
        notes defect, OQ-5)."""
        if scope != "default":
            body["scope"] = scope
        return body

    @staticmethod
    def _envelope_body(envelope: Envelope, ts: float, who: Actor,
                       reason: str, scope: str = "default") -> dict:
        return RecalibrationStore._with_scope(
            {"version": envelope.version, "ts": ts, "actor": asdict(who),
             "reason": reason, "policy": envelope.to_dict()}, scope)

    @staticmethod
    def _outcome_body(eid: str, ts: float, event_type: str, decision: str,
                      direction: str, who: Actor, references: dict,
                      workflow_ref: str, accepted: bool,
                      refusal_reason: str, scope: str = "default",
                      recall_count: int | None = None,
                      recall_route: str | None = None) -> dict:
        body = RecalibrationStore._with_scope(
            {"event_id": eid, "ts": ts, "event_type": event_type,
             "decision": decision, "direction": direction,
             "actor": asdict(who), "references": references,
             "workflow_ref": workflow_ref, "accepted": bool(accepted),
             "refusal_reason": refusal_reason}, scope)
        # AR — the same rule as `scope`: in the hash only when present, so a
        # row written before the fields existed recomputes unchanged and a
        # stated count is tamper-evident.
        if recall_count is not None:
            body["recall_count"] = int(recall_count)
            body["recall_route"] = recall_route
        return body

    @staticmethod
    def _recal_body(eid: str, ts: float, belief_id: int, delta: float,
                    before: float, after: float, stored: float,
                    envelope_version: str, who: Actor, outcome_ref: str,
                    applied: bool, refusal_rule: str,
                    scope: str = "default") -> dict:
        return RecalibrationStore._with_scope(
            {"event_id": eid, "ts": ts, "belief_id": belief_id,
             "delta": delta, "prominence_before": before,
             "prominence_after": after, "stored_weight": stored,
             "envelope_version": envelope_version, "actor": asdict(who),
             "outcome_ref": outcome_ref, "applied": applied,
             "refusal_rule": refusal_rule}, scope)

    # ---------- verification (Z.1) ----------

    def verify_governance_records(self, chain=None) -> dict:
        """Recompute every governance record's hash; check sealed ones
        against their blocks.

        EXISTS BECAUSE NOTHING DID. These records were sealed into governance
        blocks and no code path ever recomputed them — the guarantee was
        reachable only by reading pytest, which is the E.1 defect
        `verify_sealed_lineage` was built to end, one table over. Same three
        layers, same reason they stay separate: a row failure names a content
        edit, a seal failure names a consistent forgery, and the chain check
        is what makes the seal worth consulting.

        Read-only. Hashes are recomputed to COMPARE, never to replace.

        W6 — and the other direction, block to row. Every hash a block's
        `recalibration_seal` holds must still be some governance row's
        stored `record_hash`. A row checked only from its own side can be
        rewritten with a recomputed hash and its `sealed_block` set back to
        NULL: it then reads as an unsealed row with a consistent hash, and
        the next seal seals the forgery. A deleted sealed row reads as
        nothing at all. Both leave a sealed hash that no row holds, and that
        is named (`sealed_hash_without_row`), for every one of the six
        tables. NOT SEEN by this: an act that was never sealed, rewritten or
        deleted before its first seal.
        """
        row_checked = 0
        row_failures: list[dict] = []
        sealed = seal_ok = 0
        seal_failures: list[dict] = []
        block_cache: dict[int, object] = {}
        stored_hashes: set[str] = set()     # W6 — every governance row's stored hash, for block -> row

        def _sealed_hashes(idx: int) -> list[str]:
            if idx not in block_cache:
                block_cache[idx] = chain.get_block(idx) if chain else None
            block = block_cache[idx]
            if block is None:
                return []
            fp = block.fingerprint or {}
            return (fp.get("recalibration_seal", {}).get("record_hashes", [])
                    + fp.get("lineage_seal", {}).get("record_hashes", []))

        def _check(table: str, key: str, body: dict, stored_hash: str,
                   sealed_block) -> None:
            nonlocal row_checked, sealed, seal_ok
            row_checked += 1
            stored_hashes.add(stored_hash)
            if _canonical_hash(body) != stored_hash:
                row_failures.append({"table": table, "key": key,
                                     "layer": "row"})
            if sealed_block is None:
                return
            sealed += 1
            if chain is None:
                return
            if stored_hash in _sealed_hashes(int(sealed_block)):
                seal_ok += 1
            else:
                seal_failures.append({"table": table, "key": key,
                                      "sealed_block": int(sealed_block)})

        for r in self.conn.execute("SELECT * FROM envelope_versions"):
            _check("envelope_versions", r["version"],
                   self._envelope_body(
                       Envelope.from_dict(json.loads(r["policy_json"])),
                       r["ts"],
                       Actor(r["actor_id"], r["actor_role"], r["actor_source"]),
                       r["reason"], r["scope"]),
                   r["record_hash"], r["sealed_block"])
        for r in self.conn.execute("SELECT * FROM outcome_events"):
            _check("outcome_events", r["event_id"],
                   self._outcome_body(
                       r["event_id"], r["ts"], r["event_type"], r["decision"],
                       r["direction"],
                       Actor(r["actor_id"], r["actor_role"], r["actor_source"]),
                       json.loads(r["references_json"]), r["workflow_ref"],
                       bool(r["accepted"]), r["refusal_reason"], r["scope"],
                       recall_count=(r["recall_count"] if "recall_count" in r.keys() else None),
                       recall_route=(r["recall_route"] if "recall_route" in r.keys() else None)),
                   r["record_hash"], r["sealed_block"])
        zero_sign_legacy = 0
        for r in self.conn.execute("SELECT * FROM recalibration_events"):
            def _body(signed: dict, r=r) -> dict:
                vals = {f: signed.get(f, r[f]) for f in RECAL_REAL_FIELDS}
                return self._recal_body(
                    r["event_id"], r["ts"], int(r["belief_id"]),
                    vals["delta"], vals["prominence_before"],
                    vals["prominence_after"], vals["stored_weight"],
                    r["envelope_version"],
                    Actor(r["actor_id"], r["actor_role"], r["actor_source"]),
                    r["outcome_ref"], bool(r["applied"]),
                    r["refusal_rule"], r["scope"])
            body = _body({})
            if _canonical_hash(body) != r["record_hash"]:
                # W0b — a stored zero may have been hashed as -0.0; try the
                # sign the row could not keep, on zero fields only.
                zeros = [f for f in RECAL_REAL_FIELDS if r[f] == 0.0]
                for combo in (c for k in range(1, len(zeros) + 1)
                              for c in itertools.combinations(zeros, k)):
                    alt = _body({f: -0.0 for f in combo})
                    if _canonical_hash(alt) == r["record_hash"]:
                        body = alt
                        zero_sign_legacy += 1
                        break
            _check("recalibration_events", r["event_id"], body,
                   r["record_hash"], r["sealed_block"])
        # AL — the archival ledger, recomputed by the writer's own rule
        # (backend/kb/schema.py) so the two cannot drift.
        from backend.kb.schema import archival_event_body, archival_event_hash
        for r in self.conn.execute("SELECT * FROM archival_events"):
            body = archival_event_body(
                r["event_id"], r["ts"], int(r["belief_id"]), r["direction"],
                r["reason"], r["actor_id"], r["actor_role"], r["actor_source"])
            nonlocal_hash = archival_event_hash(body)
            row_checked += 1
            stored_hashes.add(r["record_hash"])
            if nonlocal_hash != r["record_hash"]:
                row_failures.append({"table": "archival_events",
                                     "key": r["event_id"], "layer": "row"})
            if r["sealed_block"] is not None:
                sealed += 1
                if chain is not None:
                    if r["record_hash"] in _sealed_hashes(int(r["sealed_block"])):
                        seal_ok += 1
                    else:
                        seal_failures.append(
                            {"table": "archival_events", "key": r["event_id"],
                             "sealed_block": int(r["sealed_block"])})
        # AT — the refusal ledger, recomputed by the writer's own rule.
        from backend.kb.schema import refusal_body, refusal_hash
        for r in self.conn.execute("SELECT * FROM refused_requests"):
            body = refusal_body(
                r["event_id"], r["ts"], r["route"], r["rule"], r["detail"],
                r["actor_id"], r["actor_role"], r["actor_source"], r["scope"],
                r["request_sha256"])
            row_checked += 1
            stored_hashes.add(r["record_hash"])
            if refusal_hash(body) != r["record_hash"]:
                row_failures.append({"table": "refused_requests",
                                     "key": r["event_id"], "layer": "row"})
            if r["sealed_block"] is not None:
                sealed += 1
                if chain is not None:
                    if r["record_hash"] in _sealed_hashes(int(r["sealed_block"])):
                        seal_ok += 1
                    else:
                        seal_failures.append(
                            {"table": "refused_requests", "key": r["event_id"],
                             "sealed_block": int(r["sealed_block"])})

        # W6 — the work-unit boundary ledger, by the same three layers. The
        # id list is read only in the writer's own form (a JSON array of
        # strings, as written): any other text is named, never coerced into
        # a list that would hash as the original did.
        from backend.kb.schema import parse_lineage_ids, work_unit_event_body, work_unit_event_hash
        for r in self.conn.execute("SELECT * FROM work_unit_events"):
            ids = parse_lineage_ids(r["lineage_ids"])
            row_checked += 1
            stored_hashes.add(r["record_hash"])
            body = None if ids is None else work_unit_event_body(
                r["event_id"], r["ts"], r["work_unit_id"], r["kind"],
                r["actor_id"], r["actor_role"], r["actor_source"], r["accountable"],
                ids, r["reverses"], asserted_on=r["asserted_on"],
                identity_mechanism=r["identity_mechanism"])
            if body is None:
                row_failures.append({"table": "work_unit_events", "key": r["event_id"],
                                     "layer": "row", "problem": "lineage_ids_not_an_id_list"})
            elif work_unit_event_hash(body) != r["record_hash"]:
                row_failures.append({"table": "work_unit_events",
                                     "key": r["event_id"], "layer": "row"})
            if r["sealed_block"] is not None:
                sealed += 1
                if chain is not None:
                    if r["record_hash"] in _sealed_hashes(int(r["sealed_block"])):
                        seal_ok += 1
                    else:
                        seal_failures.append(
                            {"table": "work_unit_events", "key": r["event_id"],
                             "sealed_block": int(r["sealed_block"])})

        # W8 (D9) — the tombstone acts, by the same three layers.
        from backend.kb.schema import record_tombstone_body, record_tombstone_hash
        for r in self.conn.execute("SELECT * FROM record_tombstones"):
            row_checked += 1
            stored_hashes.add(r["record_hash"])
            try:
                kept = json.loads(r["kept_fields"])
            except (TypeError, ValueError):
                kept = None
            body = None if not isinstance(kept, list) else record_tombstone_body(
                r["event_id"], r["ts"], r["lineage_id"], r["actor_id"], r["actor_role"], r["actor_source"],
                r["reason"], kept)
            if body is None or json.dumps(kept, separators=(",", ":")) != r["kept_fields"] \
                    or record_tombstone_hash(body) != r["record_hash"]:
                row_failures.append({"table": "record_tombstones", "key": r["event_id"], "layer": "row"})
            if r["sealed_block"] is not None:
                sealed += 1
                if chain is not None:
                    if r["record_hash"] in _sealed_hashes(int(r["sealed_block"])):
                        seal_ok += 1
                    else:
                        seal_failures.append({"table": "record_tombstones", "key": r["event_id"],
                                              "sealed_block": int(r["sealed_block"])})

        # W6 — block to row: every sealed governance hash must still be held.
        orphaned: list[dict] = []
        if chain is not None:
            for block in chain.all_blocks():
                for h in (block.fingerprint or {}).get("recalibration_seal", {}).get("record_hashes", []):
                    if h not in stored_hashes:
                        orphaned.append({"sealed_block": block.index, "record_hash": h,
                                         "problem": "sealed_hash_without_row"})
        seal_failures.extend(orphaned)

        if row_failures:
            verdict = "row_hash_mismatch"
        elif seal_failures:
            verdict = "seal_mismatch"
        else:
            verdict = "intact"
        return {
            "verdict": verdict,
            "row": {"checked": row_checked, "failed": len(row_failures),
                    "failures": row_failures,
                    # W0b — recalibration rows whose sealed hash carries a
                    # -0.0 the stored zero cannot show; verified, and counted.
                    "zero_sign_legacy": zero_sign_legacy},
            "seal": {"sealed": sealed,
                     "ok": seal_ok if chain is not None else None,
                     "failed": len(seal_failures),
                     "failures": seal_failures,
                     "unsealed": row_checked - sealed,
                     # W6 — sealed hashes no row holds (a sealed row deleted, or
                     # rewritten and unsealed); None when no chain was given.
                     "sealed_without_row": len(orphaned) if chain is not None else None},
        }


def asdict_shallow(ev: RecalibrationEvent) -> dict:
    """`dataclasses.asdict` recurses and would turn `actor` into a dict, which
    `RecalibrationEvent(**...)` then cannot accept. One level only."""
    return {f: getattr(ev, f) for f in ev.__dataclass_fields__}


# ---------------------------------------------------------------------------
# The act
# ---------------------------------------------------------------------------

def apply_outcome(
    store, recal: RecalibrationStore, *,
    belief_id: int,
    delta: float,
    envelope: Envelope | None = None,
    actor: "Actor | str | None" = None,
    outcome_ref: str = "",
    scope: str = "default",
) -> RecalibrationEvent:
    """Move one belief's prominence by `delta`, inside `envelope`, on record.

    Raises `EnvelopeViolation` if policy refuses — and records the refusal
    before raising, so a caller cannot make a declined attempt disappear by
    catching the exception (I-6). The stored `weight` column is never touched.
    """
    who = envelope or recal.active_envelope()
    actor_obj = _as_actor(actor)
    triple = store.get_by_id(belief_id)

    def _refuse(rule: str, detail: str, before: float = 0.0,
                stored: float = 0.0) -> None:
        recal._insert_recalibration(RecalibrationEvent(
            event_id=str(uuid.uuid4()), ts=time.time(), belief_id=belief_id,
            delta=float(delta), prominence_before=before,
            prominence_after=before, stored_weight=stored,
            envelope_version=who.version, actor=actor_obj,
            outcome_ref=outcome_ref, applied=False,
            refusal_rule=rule, refusal_detail=detail,
            scope=(scope or "default").strip() or "default"))
        logger.info("recalibration refused (%s) belief=%s delta=%s: %s",
                    rule, belief_id, delta, detail)
        raise EnvelopeViolation(rule, detail)

    if triple is None:
        _refuse("unknown_belief", f"no belief with id {belief_id}")

    before = store.effective_weight(belief_id)
    stored = triple.weight

    if not recalibration_enabled():
        _refuse("recalibration_disabled",
                "NEXUS_RECALIBRATION=off — no weight may move", before, stored)
    if belief_id in who.frozen_belief_ids:
        _refuse("frozen_belief",
                f"belief {belief_id} is frozen by envelope {who.version}",
                before, stored)
    cls = classify(triple.source)
    if cls not in who.movable_classes:
        _refuse("class_not_movable",
                f"evidence class {cls!r} is not movable under envelope "
                f"{who.version} (movable: {list(who.movable_classes)})",
                before, stored)
    if abs(float(delta)) > who.max_delta + 1e-12:
        _refuse("max_delta",
                f"|delta| {abs(float(delta)):.4f} exceeds max_delta "
                f"{who.max_delta:.4f} of envelope {who.version}",
                before, stored)
    if who.cumulative_max is not None:
        already = recal.cumulative_movement(belief_id)
        if already + abs(float(delta)) > who.cumulative_max + 1e-12:
            _refuse("cumulative_max",
                    f"cumulative movement {already:.4f} + {abs(float(delta)):.4f} "
                    f"exceeds cumulative_max {who.cumulative_max:.4f} of "
                    f"envelope {who.version}", before, stored)

    # Clamped, not refused: the floor and ceiling are the shape of the scale,
    # not a policy judgement about the request. The clamp is visible because
    # `prominence_after` is recorded and will differ from before+delta.
    after = min(max(before + float(delta), who.floor), who.ceiling)

    ev = recal._insert_recalibration(RecalibrationEvent(
        event_id=str(uuid.uuid4()), ts=time.time(), belief_id=belief_id,
        delta=float(delta), prominence_before=before, prominence_after=after,
        stored_weight=stored, envelope_version=who.version, actor=actor_obj,
        outcome_ref=outcome_ref, applied=True,
        scope=(scope or "default").strip() or "default"))
    logger.info("recalibration applied belief=%s %.4f -> %.4f (envelope %s, "
                "actor %s, outcome %s)", belief_id, before, after,
                who.version, actor_obj.id, outcome_ref or "-")
    return ev


# ---------------------------------------------------------------------------
# S.3 — learning provenance: why a belief weighs what it weighs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)                      # Law 1
class ProvenanceStep:
    """One recorded movement, resolved to the decision behind it."""
    event_id: str
    ts: float
    delta: float
    prominence_before: float
    prominence_after: float
    envelope_version: str
    actor: Actor
    outcome_ref: str
    outcome: OutcomeEvent | None
    sealed_block: int | None
    applied: bool
    refusal_rule: str = ""
    refusal_detail: str = ""

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id, "ts": self.ts, "delta": self.delta,
            "prominence_before": round(self.prominence_before, 6),
            "prominence_after": round(self.prominence_after, 6),
            "envelope_version": self.envelope_version,
            "actor": asdict(self.actor),
            "outcome_ref": self.outcome_ref,
            "outcome": (
                {"event_id": self.outcome.event_id,
                 "event_type": self.outcome.event_type,
                 "decision": self.outcome.decision,
                 "direction": self.outcome.direction,
                 "workflow_ref": self.outcome.workflow_ref,
                 "notes": self.outcome.notes,
                 "actor": asdict(self.outcome.actor)}
                if self.outcome is not None else None),
            # I-6 — an outcome_ref that does not resolve is REPORTED, not
            # dropped. A step whose reason cannot be found is a finding.
            "outcome_resolved": self.outcome is not None if self.outcome_ref
                                else None,
            "sealed_block": self.sealed_block,
            "sealed": self.sealed_block is not None,
            "applied": self.applied,
            "refusal_rule": self.refusal_rule,
            "refusal_detail": self.refusal_detail,
        }


# AG — the sentence the contamination investigation ended on, carried by the
# budget report's own payload so the surface cannot be read as more than it
# is. This report watches the governed door; it is not a detector.
BUDGET_NOTE = (
    "Contamination is attributable and findable afterwards at the governance "
    "layer; at the belief layer it is attributable only to an evidence "
    "class; and nothing at either layer would notice it accumulating. This "
    "report aggregates governed movement only.")

# The sentence that must appear on any surface rendering a provenance trace.
# Not a UI decision: an empty history means "nothing was recorded", and only
# the record layer knows the difference between that and "nothing happened".
NO_RECORDS_NOTE = (
    "No governance records exist in this store. Nothing has been recorded "
    "yet, and nothing earlier can be: stored weight is a monotone high-water "
    "mark with no recoverable history (TEMPORAL_PROBE.md).")
COVERAGE_NOTE = (
    "Recalibration records begin at {when}. Weight movements before that are "
    "unrecorded and unrecoverable — stored weight is a monotone high-water "
    "mark (TEMPORAL_PROBE.md), so an empty history here means 'not recorded', "
    "never 'never moved'.")


@dataclass(frozen=True)                      # Law 1
class BeliefProvenance:
    """Why this belief weighs what it weighs — a query, not a reconstruction.

    Carries all three quantities under H.2's three names, because collapsing
    them is the defect H.2 was written about: the RECORD (`stored_weight`,
    monotone, never moved by anything here), the decayed prominence, and the
    effective value ranking actually uses.
    """
    belief_id: int
    stored_weight: float
    current_weight: float
    arrival_scale: float
    adjustment: float
    effective_weight: float
    steps: tuple
    refused: tuple
    records_begin_at: float | None
    coverage: str
    enabled: bool
    resolved: bool = True

    def to_dict(self) -> dict:
        return {
            "belief_id": self.belief_id,
            "resolved": self.resolved,
            "stored_weight": self.stored_weight,
            "current_weight": round(self.current_weight, 6),
            "arrival_scale": self.arrival_scale,
            "recalibration_adjustment": round(self.adjustment, 6),
            "effective_weight": round(self.effective_weight, 6),
            "steps": [s.to_dict() for s in self.steps],
            "refused": [s.to_dict() for s in self.refused],
            "records_begin_at": self.records_begin_at,
            # ALWAYS present, including when `steps` is empty. That is the
            # whole point of the field.
            "coverage": self.coverage,
            "recalibration_enabled": self.enabled,
            "weight_vocabulary": {
                "stored_weight":    "record layer; monotone; never moved by a "
                                    "recalibration",
                "current_weight":   "stored weight after class decay (F.3)",
                "arrival_scale":    "per-evidence-class discount (Q.2)",
                "recalibration_adjustment":
                                    "summed applied deltas (S.2); the only "
                                    "term an outcome can move",
                "effective_weight": "what ranking and the tier band use",
            },
        }


def provenance_for(store, recal: RecalibrationStore, *,
                   belief_id: int, now: float | None = None
                   ) -> BeliefProvenance:
    """Trace one belief's current weight back through what moved it.

    Two indexed reads and one lookup per step — a query, which is what the
    packet asked for. `MAX()` made the alternative impossible anyway: there is
    nothing to reconstruct from.

    Refused attempts are returned alongside applied ones. A trace that showed
    only what succeeded would describe a system that never declines anything,
    which is the same objection S.2 answered inside the store.
    """
    from backend.kb.tiered_store import arrival_scale, effective_prominence

    triple = store.get_by_id(belief_id)
    begin = recal.records_begin_at()
    coverage = NO_RECORDS_NOTE if begin is None else COVERAGE_NOTE.format(
        when=time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(begin)))

    if triple is None:
        # H.2's discipline: an unresolved belief is REPORTED, not omitted.
        return BeliefProvenance(
            belief_id=belief_id, stored_weight=0.0, current_weight=0.0,
            arrival_scale=0.0, adjustment=0.0, effective_weight=0.0,
            steps=(), refused=(), records_begin_at=begin, coverage=coverage,
            enabled=recalibration_enabled(), resolved=False)

    outcomes: dict[str, OutcomeEvent] = {}

    def _step(ev: RecalibrationEvent) -> ProvenanceStep:
        ref = ev.outcome_ref
        if ref and ref not in outcomes:
            found = recal.get_outcome(ref)
            if found is not None:
                outcomes[ref] = found
        return ProvenanceStep(
            event_id=ev.event_id, ts=ev.ts, delta=ev.delta,
            prominence_before=ev.prominence_before,
            prominence_after=ev.prominence_after,
            envelope_version=ev.envelope_version, actor=ev.actor,
            outcome_ref=ref, outcome=outcomes.get(ref),
            sealed_block=ev.sealed_block, applied=ev.applied,
            refusal_rule=ev.refusal_rule, refusal_detail=ev.refusal_detail)

    every = recal.all_events_for(belief_id)
    steps = tuple(_step(e) for e in every if e.applied)
    refused = tuple(_step(e) for e in every if not e.applied)
    adjustment = recal.adjustment_for(belief_id)

    return BeliefProvenance(
        belief_id=belief_id,
        stored_weight=triple.weight,
        current_weight=triple.current_weight(now),
        arrival_scale=arrival_scale(triple.source),
        adjustment=adjustment,
        effective_weight=effective_prominence(triple, now, adjustment),
        steps=steps, refused=refused, records_begin_at=begin,
        coverage=coverage, enabled=recalibration_enabled())


# `direction` -> the sign of the delta. Named rather than inlined because the
# seam spec's vocabulary and the arithmetic must not drift apart.
DIRECTION_SIGN = {"confirming": 1.0, "contradicting": -1.0, "neutral": 0.0}
