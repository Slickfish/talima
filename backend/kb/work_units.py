"""v3.1 Phase 1 — work-unit attribution, done the way scope is done.

WHAT THIS RECORDS, AND WHAT IT DOES NOT
    A work unit is the application's unit of value — a claim, a contract, a
    ticket — that cognition was applied to. NEXUS records, per answer, which
    unit the turn belonged to and who was answerable (the lineage record's
    fields, recorded not authenticated), and, per claim, which units
    ADMITTED it (the `triple_work_unit` junction). It never sequences,
    queues or moves a unit (Law XII).

ATTRIBUTION IS SCOPE WITH A ROW (1b)
    Scope is an opaque, caller-supplied label on every request: never
    inferred, recorded literally, `"default"` when absent, sealed into the
    record, never confirmed — attaching a label to a record changes no
    standing. A work unit is treated the same way. The client names the
    unit on each request as it names scope; nothing on the server
    remembers "the current unit" between turns, nothing refuses a second
    open unit, nothing re-reads an open unit after a restart, and no route
    opens or closes anything. A unit row is created on first mention with
    the request's actor fields; `decision_boundary: close` on a request
    naming a unit sets `closed_at`, and (W6) `reopen` clears it; each of
    the two is also one appended row of the boundary ledger. Those are the
    row's writes. On a turn the boundary act follows the turn's record
    (`attribute_for_turn` before the model answers, `apply_boundary_for_turn`
    after the record), so a turn whose record could not be written closes
    and reopens nothing; the row made on first mention is the first half's
    and stays. Accountability is a field in force, recorded per lineage
    record from the request; it may change mid-unit and the records are the
    history.

WHY A JUNCTION
    `add()` upserts on `(subject, relation, object, source)`, so a column on
    `triples` would record the first unit that admitted a claim and be
    silent when a second one re-asserted it (BC Phase 0, item 2). The
    junction gains a row per (claim, unit); the claim row is untouched.

ADMISSION, NOT USE
    A junction row is written beside every successful `add()` during a
    unit — extraction, human assertion, seeding — keyed on the id `add()`
    returns whether the add inserted or hit the upsert. It is NOT written at
    citation or injection: "used in unit" is derivable from the lineage
    record joined to its cited flags, and writing it here would bake the
    citation flag's measured under-report into attribution.

THE SESSION PSEUDO-UNIT
    When a turn names no unit, the turn is attributed to
    `session:<session_id>`, `attribution_status='unattributed'`, created
    lazily by the same rule as any unit (the request's actor fields, the
    vocabulary's defaults when absent). Never fails; counted apart wherever
    units are counted. E.6 (1b) records that this is one row per session
    id, unbounded in the number of sessions, and names the alternatives.

ARCHIVAL (E.7)
    A junction row pointing at an archived claim needs nothing: it records
    that the claim was admitted, and an archived claim was still admitted.
    `archive_ids` and `unarchive_ids` never touch this table; a read that
    lists a unit's claims carries the row's `archived` flag so the reader
    can tell, rather than hiding the row.

This store runs on the TripleStore's own connection (kb.db) and takes no
path: it is not a new writer for the derived-writer guard to see, and the
tables ride the same file as the claims they point at.
"""
from __future__ import annotations

import json
from backend.store import Database, Row
import time
import uuid

from backend.kb.recalibration import DEFAULT_ACTOR
from backend.kb.schema import (
    lineage_ids_text, parse_lineage_ids,
    WORK_UNIT_DDL, WORK_UNIT_EVENTS_DDL, work_unit_event_body, work_unit_event_hash,
)

PSEUDO_UNIT_PREFIX = "session:"
PSEUDO_UNIT_TYPE = "session"
STATUS_ATTRIBUTED = "attributed"
STATUS_UNATTRIBUTED = "unattributed"
# The boundary values honoured: `close` since 1b, and since Phase 2 (W6) its
# reversal, `reopen`. Any other value is recorded on the lineage record as
# asserted and has no effect.
BOUNDARY_CLOSE = "close"
BOUNDARY_REOPEN = "reopen"
# W6 — the kinds of act the boundary ledger records.
KIND_CLOSE = "close"
KIND_REOPEN = "reopen"
FLAG_ACCOUNTABILITY_MISSING = "accountability.missing"

ACTOR_KEYS = ("id", "role", "source")


class WorkUnitError(ValueError):
    """A named refusal: `rule` is the reason a caller reports."""

    def __init__(self, rule: str, detail: str, **extra):
        super().__init__(detail)
        self.rule, self.detail, self.extra = rule, detail, extra


def pseudo_unit_id(session_id: str) -> str:
    return f"{PSEUDO_UNIT_PREFIX}{session_id}"


def is_pseudo_unit(unit_id: str | None) -> bool:
    return bool(unit_id) and unit_id.startswith(PSEUDO_UNIT_PREFIX)


def actor_fields(actor: dict | None = None) -> dict:
    """The request's actor fields, the vocabulary's recorded defaults for
    whatever it left unsaid (`DEFAULT_ACTOR`: what silence has always
    meant). Never a fresh literal such as `request` or `system` (1b, B.2)."""
    actor = actor or {}
    return {"id": (actor.get("id") or "").strip() or DEFAULT_ACTOR.id,
            "role": (actor.get("role") or "").strip() or DEFAULT_ACTOR.role,
            "source": (actor.get("source") or "").strip() or DEFAULT_ACTOR.source}


def _actor_json(actor) -> str:
    """`opened_by` / `closed_by` are actor fields, stored as canonical JSON.
    A bare string is refused: that is exactly the literal 1a wrote."""
    if not isinstance(actor, dict) or set(actor) != set(ACTOR_KEYS) or not all(
            isinstance(actor[k], str) and actor[k] for k in ACTOR_KEYS):
        raise WorkUnitError("actor_not_fields",
                            f"opened_by/closed_by must be actor fields {ACTOR_KEYS}, got {actor!r}")
    return json.dumps(actor, sort_keys=True)


def _actor_from_column(raw: str | None) -> dict | None:
    """Rows created before 1b hold a bare literal (`request` for a unit named
    on a request, `system` for a pseudo-unit). Read back as `{"literal": …}`
    so a reader can tell a pre-1b row from an actor; nothing is rewritten."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {"literal": raw}
    if isinstance(parsed, dict) and set(parsed) == set(ACTOR_KEYS):
        return parsed
    return {"literal": raw}


class WorkUnitStore:
    def __init__(self, conn: Database):
        self.conn = conn
        self.conn.executescript(WORK_UNIT_DDL)      # additive, re-runnable
        self.conn.executescript(WORK_UNIT_EVENTS_DDL)   # W6 — the boundary ledger; additive
        self.conn.commit()

    # ---------- read ----------

    def get(self, unit_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM work_unit WHERE work_unit_id = ?", (unit_id,)).fetchone()
        return self._unit(row) if row else None

    def units_for_triple(self, triple_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT j.work_unit_id, j.first_seen_turn, j.lineage_id, u.attribution_status "
            "FROM triple_work_unit j JOIN work_unit u USING (work_unit_id) "
            "WHERE j.triple_id = ? ORDER BY j.first_seen_turn, j.work_unit_id", (triple_id,)).fetchall()
        return [dict(r) for r in rows]

    def claims_for_unit(self, unit_id: str) -> list[dict]:
        """Every claim the unit admitted, with the claim row's `archived` flag
        carried rather than filtered (E.7)."""
        rows = self.conn.execute(
            "SELECT j.triple_id, j.first_seen_turn, j.lineage_id, t.subject, t.relation, "
            "t.object, t.source, t.archived FROM triple_work_unit j "
            "JOIN triples t ON t.id = j.triple_id WHERE j.work_unit_id = ? "
            "ORDER BY j.first_seen_turn, j.triple_id", (unit_id,)).fetchall()
        return [dict(r) for r in rows]

    def counts(self) -> dict:
        """Units by status and open/closed, the pseudo-units counted apart."""
        out = {"attributed": {"open": 0, "closed": 0}, "unattributed": {"open": 0, "closed": 0},
               "junction_rows": 0}
        for r in self.conn.execute(
                "SELECT attribution_status AS s, (closed_at IS NULL) AS o, COUNT(*) AS n "
                "FROM work_unit GROUP BY s, o"):
            out[r["s"]]["open" if r["o"] else "closed"] = r["n"]
        out["junction_rows"] = self.conn.execute(
            "SELECT COUNT(*) AS n FROM triple_work_unit").fetchone()["n"]
        return out

    # ---------- write: the row's writes ----------

    def open(self, unit_id: str, *, work_unit_type: str, opened_by: dict,
             accountable_identity: str | None = None, parent_work_unit_id: str | None = None,
             attributes: dict | None = None, session_id: str | None = None,
             attribution_status: str = STATUS_ATTRIBUTED, now: float | None = None) -> dict:
        """Write one: creation. Refuses a duplicate id (`unit_exists`) and an
        `opened_by` that is not actor fields. No rule about other units."""
        if attribution_status not in (STATUS_ATTRIBUTED, STATUS_UNATTRIBUTED):
            raise WorkUnitError("bad_attribution_status", attribution_status)
        opened = _actor_json(opened_by)
        if self.get(unit_id) is not None:
            raise WorkUnitError("unit_exists", f"work unit {unit_id!r} already exists")
        self.conn.execute(
            "INSERT INTO work_unit (work_unit_id, work_unit_type, accountable_identity, opened_at, "
            "closed_at, opened_by, closed_by, parent_work_unit_id, attributes_json, "
            "attribution_status, session_id) VALUES (?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?, ?)",
            (unit_id, work_unit_type, accountable_identity, now if now is not None else time.time(),
             opened, parent_work_unit_id, json.dumps(attributes or {}, sort_keys=True),
             attribution_status, session_id))
        self.conn.commit()
        return self.get(unit_id)

    def close(self, unit_id: str, *, closed_by: dict, accountable: str | None = None,
              asserted_on: str | None = None, identity_mechanism: str | None = None,
              now: float | None = None) -> dict:
        """The boundary. `closed_at` and `closed_by` are set, and (W6) the
        act is appended to the boundary ledger in the same transaction, with
        the ids of the unit's lineage records at the act: both are committed
        or, on any error, neither is (rolled back, then raised). On a turn
        the act follows the turn's record, so the list includes `asserted_on`
        (that record's id); another turn's record on the unit, written while
        this one ran, can follow it. Refuses a missing unit and a close of
        a closed one; the turn path checks first and declares rather than
        refuses (B.3)."""
        closed = _actor_json(closed_by)
        unit = self.get(unit_id)
        if unit is None:
            raise WorkUnitError("unit_not_found", f"work unit {unit_id!r} does not exist")
        if unit["closed_at"] is not None:
            raise WorkUnitError("unit_already_closed", f"work unit {unit_id!r} closed at {unit['closed_at']}")
        ts = now if now is not None else time.time()
        try:
            self._append_event(unit_id, KIND_CLOSE, closed_by, accountable, ts,
                               lineage_ids=self.lineage_ids(unit_id), reverses=None,
                               asserted_on=asserted_on, identity_mechanism=identity_mechanism)
            self.conn.execute(
                "UPDATE work_unit SET closed_at = ?, closed_by = ? WHERE work_unit_id = ?",
                (ts, closed, unit_id))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get(unit_id)

    def reopen(self, unit_id: str, *, reopened_by: dict, accountable: str | None = None,
               asserted_on: str | None = None, identity_mechanism: str | None = None,
               now: float | None = None) -> dict:
        """W6 — the reversal of a close. The row's `closed_at` and
        `closed_by` are cleared and a `reopen` act is appended that names
        the close it reverses (None when that close predates the ledger), in
        one transaction: both or, on any error, neither. The close event is
        never amended. Refuses a missing unit and a unit that is not closed;
        the turn path declares rather than refuses."""
        _actor_json(reopened_by)
        unit = self.get(unit_id)
        if unit is None:
            raise WorkUnitError("unit_not_found", f"work unit {unit_id!r} does not exist")
        if unit["closed_at"] is None:
            raise WorkUnitError("unit_not_closed", f"work unit {unit_id!r} is not closed")
        closes = [e for e in self.events(unit_id) if e["kind"] == KIND_CLOSE]
        ts = now if now is not None else time.time()
        try:
            self._append_event(unit_id, KIND_REOPEN, reopened_by, accountable, ts, lineage_ids=(),
                               reverses=(closes[-1]["event_id"] if closes else None),
                               asserted_on=asserted_on, identity_mechanism=identity_mechanism)
            self.conn.execute(
                "UPDATE work_unit SET closed_at = NULL, closed_by = NULL WHERE work_unit_id = ?", (unit_id,))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self.get(unit_id)

    # ---------- W6: the boundary ledger ----------

    def _append_event(self, unit_id: str, kind: str, actor: dict, accountable: str | None, ts: float, *,
                      lineage_ids, reverses: str | None, asserted_on: str | None = None,
                      identity_mechanism: str | None = None) -> str:
        """One row, no commit: the caller commits it with the unit row's own
        change. The only writer of `work_unit_events`. `asserted_on` is the
        lineage record of the turn that asserted the boundary (None for an
        act made off a turn); `identity_mechanism` is how that turn's
        `accountable` was established, stored only when it is not the
        default (`asserted`), exactly as the lineage record stores it."""
        who = json.loads(_actor_json(actor))
        event_id = str(uuid.uuid4())
        ids = [str(i) for i in lineage_ids]
        mechanism = identity_mechanism if identity_mechanism not in (None, "", "asserted") else None
        body = work_unit_event_body(event_id, ts, unit_id, kind, who["id"], who["role"], who["source"],
                                    accountable or None, ids, reverses,
                                    asserted_on=asserted_on or None, identity_mechanism=mechanism)
        self.conn.execute(
            "INSERT INTO work_unit_events (event_id, ts, work_unit_id, kind, actor_id, actor_role, "
            "actor_source, accountable, lineage_ids, reverses, asserted_on, identity_mechanism, record_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, ts, unit_id, kind, who["id"], who["role"], who["source"], accountable or None,
             lineage_ids_text(ids), reverses, asserted_on or None, mechanism, work_unit_event_hash(body)))
        return event_id

    def lineage_ids(self, unit_id: str) -> list[str]:
        """The ids of the unit's lineage records, oldest first (by the
        record's time, then its id). Ids, never copies. Empty when this
        database holds no lineage table."""
        if not self.conn.has_table("lineage_records"):
            return []
        rows = self.conn.execute(
            "SELECT lineage_id, ts FROM lineage_records WHERE work_unit_id = ?", (unit_id,)).fetchall()
        return [r["lineage_id"] for r in sorted(rows, key=lambda r: (r["ts"], r["lineage_id"]))]

    def events(self, unit_id: str | None = None) -> list[dict]:
        """The boundary ledger in the order its acts were appended: one
        unit's, or every unit's. The order is the table's insertion ordinal
        (`rowid`, an explicit column on Postgres), never the act's time: two
        acts in one clock tick, or a clock set back, would otherwise read in
        an order that never happened."""
        if unit_id is None:
            rows = self.conn.execute("SELECT *, rowid AS ord FROM work_unit_events ORDER BY rowid").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT *, rowid AS ord FROM work_unit_events WHERE work_unit_id = ? ORDER BY rowid",
                (unit_id,)).fetchall()
        return [self._event(r) for r in rows]

    @staticmethod
    def _event(row: Row) -> dict:
        ids = parse_lineage_ids(row["lineage_ids"])
        return {"event_id": row["event_id"], "ts": row["ts"], "work_unit_id": row["work_unit_id"],
                "kind": row["kind"],
                "actor": {"id": row["actor_id"], "role": row["actor_role"], "source": row["actor_source"]},
                "accountable": row["accountable"],
                # The stored text when it is not a list of ids in the writer's own form: shown as it is,
                # never coerced into one (the verifier names such a row).
                "lineage_ids": ids if ids is not None else row["lineage_ids"],
                "reverses": row["reverses"], "asserted_on": row["asserted_on"],
                "identity_mechanism": row["identity_mechanism"] or "asserted",
                "record_hash": row["record_hash"], "sealed_block": row["sealed_block"]}

    def verify_events(self) -> dict:
        """Does the ledger agree with the unit rows and with itself? Three
        checks, read-only: a reopen names a close event of its own unit (or
        None, for a close that predates the ledger); a unit whose last act
        is a close is closed and one whose last act is a reopen is open; and
        a unit closed with no act at all was closed before the ledger's
        first act (`closed_before_ledger`, counted, never a failure), or it
        is named (`closed_with_no_act`). NOT SEEN by this: an unsealed act
        deleted in a way that leaves the row and the references consistent,
        and the ledger's first act itself deleted. A sealed act that is
        deleted or rewritten is `verify_governance_records`' to name, with
        each act's hash and seal."""
        events = self.events()
        first_act_ts = min((e["ts"] for e in events), default=None)
        by_unit: dict[str, list[dict]] = {}
        for e in events:
            by_unit.setdefault(e["work_unit_id"], []).append(e)
        closes = {e["event_id"]: e for e in events if e["kind"] == KIND_CLOSE}
        problems: list[dict] = []
        for e in events:
            if e["kind"] == KIND_REOPEN and e["reverses"] is not None:
                target = closes.get(e["reverses"])
                if target is None or target["work_unit_id"] != e["work_unit_id"]:
                    problems.append({"event_id": e["event_id"], "problem": "reverses_no_close_of_this_unit"})
        before_ledger = 0
        for row in self.conn.execute("SELECT work_unit_id, closed_at FROM work_unit").fetchall():
            acts = by_unit.get(row["work_unit_id"], [])
            if not acts:
                if row["closed_at"] is None:
                    continue
                if first_act_ts is None or row["closed_at"] < first_act_ts:
                    before_ledger += 1
                else:
                    problems.append({"work_unit_id": row["work_unit_id"], "problem": "closed_with_no_act"})
                continue
            closed_by_ledger = acts[-1]["kind"] == KIND_CLOSE
            if closed_by_ledger != (row["closed_at"] is not None):
                problems.append({"work_unit_id": row["work_unit_id"],
                                 "problem": "row_disagrees_with_last_act", "last_act": acts[-1]["kind"]})
        return {"verdict": "consistent" if not problems else "inconsistent", "events": len(events),
                "closed_before_ledger": before_ledger, "problems": problems}

    def pseudo_unit_for_session(self, session_id: str, *, opened_by: dict | None = None,
                                now: float | None = None) -> dict:
        uid = pseudo_unit_id(session_id)
        unit = self.get(uid)
        if unit is None:
            unit = self.open(uid, work_unit_type=PSEUDO_UNIT_TYPE, opened_by=actor_fields(opened_by),
                             session_id=session_id, attribution_status=STATUS_UNATTRIBUTED, now=now)
        return unit

    # ---------- the turn's resolution ----------

    @staticmethod
    def _decide(unit: dict | None, *, named: bool, boundary: str | None, accountable: str | None) -> dict:
        """What a request does to its unit, from the unit's row as it stands
        (None: no row yet). ONE rule, read by the plan (before the turn's
        record) and by the writes (after it), so the two cannot drift."""
        declaration: dict = {"unit_first_seen": False, "unit_closed": False, "flags": []}
        if not named:
            if boundary in (BOUNDARY_CLOSE, BOUNDARY_REOPEN):
                # Absence cannot be closed; the assertion is still recorded on
                # the lineage record, and this says why it did nothing.
                declaration["boundary_not_applied"] = "no_unit_named"
            return declaration
        closed = unit is not None and unit["closed_at"] is not None
        if unit is None:
            declaration["unit_first_seen"] = True
        elif closed:
            declaration["unit_closed_at_time_of_turn"] = True
        if boundary == BOUNDARY_CLOSE:
            if closed:
                declaration["unit_already_closed"] = True
            else:
                declaration["unit_closed"] = True
        elif boundary == BOUNDARY_REOPEN:
            # W6 — the reversal, asserted as `close` is: applied to a closed
            # unit, declared (never refused) on one that is not.
            if closed:
                declaration["unit_reopened"] = True
            else:
                declaration["unit_not_closed"] = True
        if (declaration.get("unit_closed") or declaration.get("unit_reopened")) and not accountable:
            declaration["flags"].append(FLAG_ACCOUNTABILITY_MISSING)
        return declaration

    def attribute_for_turn(self, *, session_id: str, requested_id: str | None = None,
                           requested_type: str | None = None, accountable: str | None = None,
                           actor: dict | None = None, boundary: str | None = None,
                           now: float | None = None) -> tuple[dict, dict]:
        """The turn's FIRST half (before the model answers): which unit this
        request belongs to, and what its boundary will do. The request's
        unit, created on first mention with the request's actor fields
        (`unit_first_seen`), else the session pseudo-unit; nothing is
        inherited from an earlier request. A closed unit named again is
        attributed and declared (`unit_closed_at_time_of_turn`).

        The boundary is DECIDED here and NOT APPLIED (W6): a close or a
        reopen is a governance act, and it is made by
        `apply_boundary_for_turn` after the turn's record exists, so a turn
        whose record cannot be written closes and reopens nothing. The
        declaration says what the act will be (`unit_closed`,
        `unit_reopened`, `unit_already_closed`, `unit_not_closed`,
        `boundary_not_applied`, and `accountability.missing` in `flags`),
        and the record carries the flags this saw. The unit row itself is
        made here, as an MCP retrieval makes it: a turn whose record then
        fails leaves the row (declared). Raises only on a write the store
        refuses (BACKLOG W6-B8: a NUL in `actor_id` on Postgres)."""
        who = actor_fields(actor)
        if not requested_id:
            unit = self.pseudo_unit_for_session(session_id, opened_by=who, now=now)
            return unit, self._decide(None, named=False, boundary=boundary, accountable=accountable)
        unit = self.get(requested_id)
        declaration = self._decide(unit, named=True, boundary=boundary, accountable=accountable)
        if unit is None:
            unit = self.open(requested_id, work_unit_type=requested_type or "unspecified",
                             opened_by=who, accountable_identity=accountable or None,
                             session_id=session_id, now=now)
        return unit, declaration

    def apply_boundary_for_turn(self, unit_id: str, declaration: dict, *, boundary: str | None,
                                actor: dict | None = None, accountable: str | None = None,
                                asserted_on: str | None = None, identity_mechanism: str | None = None,
                                now: float | None = None) -> tuple[dict, dict]:
        """The turn's boundary act, made AFTER its record (`asserted_on` is
        that record's id, so the act names the turn that asserted it, and
        the close's id list includes it). The decision is `_decide`'s again,
        on the row as it stands now, so the outcome can differ from the
        first half's declaration only if another request moved the unit in
        between; the returned declaration is the outcome, and `done` carries
        it. `unit_first_seen` and `unit_closed_at_time_of_turn` are the
        first half's and are kept. With no boundary asserted, or no unit
        named, nothing is written."""
        unit = self.get(unit_id)
        if boundary not in (BOUNDARY_CLOSE, BOUNDARY_REOPEN) or unit is None or is_pseudo_unit(unit_id):
            return unit, declaration
        outcome = self._decide(unit, named=True, boundary=boundary, accountable=accountable)
        for kept in ("unit_first_seen", "unit_closed_at_time_of_turn"):
            outcome.pop(kept, None)
            if declaration.get(kept):
                outcome[kept] = declaration[kept]
        outcome.setdefault("unit_first_seen", False)
        who = actor_fields(actor)
        if outcome.get("unit_closed"):
            unit = self.close(unit_id, closed_by=who, accountable=accountable, asserted_on=asserted_on,
                              identity_mechanism=identity_mechanism, now=now)
        elif outcome.get("unit_reopened"):
            unit = self.reopen(unit_id, reopened_by=who, accountable=accountable, asserted_on=asserted_on,
                               identity_mechanism=identity_mechanism, now=now)
        return unit, outcome

    def resolve_for_turn(self, *, session_id: str, requested_id: str | None = None,
                         requested_type: str | None = None, accountable: str | None = None,
                         actor: dict | None = None, boundary: str | None = None,
                         now: float | None = None) -> tuple[dict, dict]:
        """Attribution and the boundary in one call, for an act that is not
        a turn (the assertion and seed routes, which name a unit on a
        confirmed act and have no lineage record to wait for). A turn never
        calls this: it attributes in its first half and applies the boundary
        after its record."""
        unit, declaration = self.attribute_for_turn(
            session_id=session_id, requested_id=requested_id, requested_type=requested_type,
            accountable=accountable, actor=actor, boundary=boundary, now=now)
        return self.apply_boundary_for_turn(unit["work_unit_id"], declaration, boundary=boundary, actor=actor,
                                            accountable=accountable, now=now)

    # ---------- write: admission ----------

    def admit(self, triple_ids, unit_id: str, *, turn: int, lineage_id: str | None = None) -> int:
        """One junction row per (claim, unit), first admission wins; a repeat
        admission of the same claim in the same unit is a no-op. Append-only."""
        rows = [(int(t), unit_id, int(turn), lineage_id) for t in triple_ids if t is not None]
        if not rows:
            return 0
        # W3b — the statement's own count (sqlite3 sums an executemany's
        # modified rows into `rowcount`; an ignored duplicate counts 0), in
        # place of the connection-wide `total_changes` delta, which Postgres
        # has no twin for.
        cur = self.conn.executemany(
            "INSERT OR IGNORE INTO triple_work_unit (triple_id, work_unit_id, first_seen_turn, lineage_id) "
            "VALUES (?, ?, ?, ?)", rows)
        self.conn.commit()
        return max(0, int(cur.rowcount))

    # ---------- helpers ----------

    @staticmethod
    def _unit(row: Row) -> dict:
        d = dict(row)
        d["attributes"] = json.loads(d.pop("attributes_json") or "{}")
        d["opened_by"] = _actor_from_column(d["opened_by"])
        d["closed_by"] = _actor_from_column(d["closed_by"])
        d["pseudo"] = is_pseudo_unit(d["work_unit_id"])
        return d
