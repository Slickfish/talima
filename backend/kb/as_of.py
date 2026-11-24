"""AS-1 — as-of on transaction time: what the store held at an instant.

THE PREDICATE (S1.2), verbatim on the surface and in the tests:
    A claim is held at D if it was first admitted on or before D, and, reading
    its archival events up to D in order, its last event is not an archival;
    a claim whose supersede act is dated on or before D is held and
    superseded.

THE TIMESTAMPS IT READS (S1.1). Every one is a POSIX epoch second written
by `time.time()` — a REAL column with no zone stored, UTC by definition:
  * `triples.created_at` (schema.py:52) — the FIRST admission. The monotone
    upsert on re-assertion (tiered_store.py:604-608) does not name it in its
    SET list, so it never moves; `last_fired` and `weight`/`tier` do.
  * `archival_events.ts` (schema.py:102), one row per belief per act
    (tiered_store.py:814-823), direction `archived` | `unarchived`, read in
    `(ts, rowid)` order. The `triples.archived_at` column is cleared on
    unarchival (tiered_store.py:792-793) and is a terminal fallback only
    for a row with no event at all — a pre-ledger archival, or one made
    with `NEXUS_ARCHIVAL_LEDGER=off`.
  * `outcome_events.ts` (recalibration.py:314) on the accepted rows whose
    `recall_count IS NOT NULL` — the sealed supersede selector (A-3), never
    `notes`.

WHAT IT DOES NOT SHOW (A-1): no standing. `weight` (high-water) is a
monotone MAX with no history, `tier` is overwritten by `rebalance` with no
event, `fire_count` is a counter with no event row; the two tables that
carry a weight or a tier beside a timestamp (`recalibration_events.
stored_weight`, the lineage record's `weight_at_injection` /
`tier_at_injection`) are sampled observations, not change histories.

D IS AN INSTANT WITH AN OFFSET (AS-1b, ruled by Fable, 3 October, option
b). A bare date has no zone, so the record cannot know whose end of day is
meant — for an operator in UTC+8 a UTC reading is eight hours off, and a zone
setting breaks the moment two sites share an instance. A naive instant
read as UTC is the same convention in another spelling. Both are REFUSED
by name (422, `refused: bare_date | naive_instant`, with an example); the
conversion belongs in the client, where the zone is known.

THE LEDGER'S START IS STATED, NOT ITS BEGINNING. `archival_ledger_from` is
the earliest `archival_events.ts`; the table may have existed, empty,
before it, and the record does not say when the table was created. A row
archived before that by the column path and unarchived after it leaves no
event and is not visible to as-of; the note says so on every response.

Read-only: nothing here writes. The predicate is two SQL statements over
one CTE (the count and the page) with an indexed correlated lookup into
`archival_events` (idx_archival_belief), plus one statement over the
supersede selector; measured at 10,000 claims in tests/test_as_of.py.
"""
from __future__ import annotations

import json
import re
from backend.store import Database, OperationalError, is_row
from datetime import datetime, timezone

from backend.kb.tiered_store import SOURCE_CLASS, UNKNOWN_CLASS

PREDICATE = ("a claim is held at D if it was first admitted on or before D, and, reading its "
             "archival events up to D in order, its last event is not an archival; a claim whose "
             "supersede act is dated on or before D is held and superseded")

FIXED_SENTENCE = ("Transaction time only: when NEXUS admitted, archived or superseded a claim. "
                  "Valid time is not modelled. No standing at the date is shown, because standing "
                  "has no timed history yet. D must be an instant with an offset; timestamps are "
                  "stored as UTC epoch seconds.")

READ_FROM = ("triples.created_at (first admission); archival_events in (ts, rowid) order, with "
             "triples.archived_at as the fallback for a row that has no event; outcome_events "
             "where recall_count IS NOT NULL (the sealed supersede selector)")

CONTRACT_VERSION = 2          # 1: AS-1; 2: bare dates and naive instants refused, archival_ledger_from (AS-1b)
ROUTE = "/kb/as-of"
REFUSAL_EXAMPLE = "2026-03-12T23:59:59+08:00"
NO_EVENTS_REASON = "no archival events recorded"

_BARE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AsOfRefused(ValueError):
    """A named refusal of the `date` parameter: `refused` is the rule
    (`bare_date` | `naive_instant` | `not_an_instant`), `reason` says why,
    `example` is an instant that would be accepted."""

    def __init__(self, refused: str, reason: str):
        super().__init__(reason)
        self.refused, self.reason, self.example = refused, reason, REFUSAL_EXAMPLE

    def payload(self) -> dict:
        return {"error": self.refused, "refused": self.refused, "reason": self.reason,
                "example": self.example}


def resolve_instant(text: str) -> dict:
    """The instant a request names, as an epoch second, with how it was read.

    `{"ts", "iso", "requested", "interpretation"}`. Refuses, by name, a bare
    date (`YYYY-MM-DD`), a naive instant (no offset and no `Z`), and a
    string that is not an ISO 8601 instant at all."""
    raw = (text or "").strip()
    if _BARE_DATE.match(raw):
        raise AsOfRefused("bare_date", "a date has no zone, so the record cannot know whose end of "
                                       "day is meant; send an instant with an offset")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise AsOfRefused("not_an_instant", f"not an ISO 8601 instant: {raw!r}") from None
    if dt.tzinfo is None:
        raise AsOfRefused("naive_instant", "an instant without an offset has no zone either; a naive "
                                           "instant read as UTC would be the bare-date convention in "
                                           "another spelling; send an instant with an offset")
    return {"ts": dt.timestamp(), "iso": dt.astimezone(timezone.utc).isoformat(),
            "requested": raw, "interpretation": "instant with offset: read as given"}


def archival_ledger_from(conn: Database) -> dict:
    """The earliest archival event's time — the ledger's first RECORDED act,
    not its beginning. `{"ts", "iso", "reason"}`; on a store with no event
    both are None and `reason` says so, never blank."""
    row = conn.execute("SELECT MIN(ts) AS t FROM archival_events").fetchone()
    ts = None if row is None or row[0] is None else float(row[0])
    return {"ts": ts, "iso": iso_utc(ts), "reason": None if ts is not None else NO_EVENTS_REASON}


def ledger_sentence(from_iso: str | None) -> str:
    """The one sentence the fixed note carries about the ledger, verbatim when
    there is a from-date; the declared empty case otherwise."""
    if from_iso:
        return (f"No archival event is recorded before `{from_iso}`; a row archived before that by "
                f"the column path and unarchived after it leaves no event and is not visible to as-of.")
    return f"No archival event is recorded ({NO_EVENTS_REASON}); `archival_ledger_from` is null."


def iso_utc(ts: float | None) -> str | None:
    """An epoch second rendered as an ISO 8601 instant in UTC; None stays None."""
    return None if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


_iso = iso_utc


def _supersede_times(conn: Database, ts: float) -> dict[int, float]:
    """belief id -> the earliest accepted supersede act dated on or before `ts`,
    through the sealed selector. A store with no governance tables has none."""
    out: dict[int, float] = {}
    try:
        rows = conn.execute(
            "SELECT ts, references_json FROM outcome_events "
            "WHERE accepted = 1 AND recall_count IS NOT NULL AND ts <= ? ORDER BY ts ASC",
            (ts,)).fetchall()
    except OperationalError:          # no outcome_events table in this store
        return out
    for r in rows:
        refs = json.loads(r["references_json"] or "{}") if is_row(r) else json.loads(r[1] or "{}")
        t = r["ts"] if is_row(r) else r[0]
        for b in (refs.get("belief_ids") or []):
            out.setdefault(int(b), float(t))
    return out


def earliest_admission(conn: Database) -> float | None:
    row = conn.execute("SELECT MIN(created_at) AS t FROM triples").fetchone()
    return None if row is None or row[0] is None else float(row[0])


def held_at(conn: Database, ts: float, *, subject: str | None = None,
            relation: str | None = None, source: str | None = None,
            limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    """Every claim held at `ts` under PREDICATE, ordered by admitted-at then
    claim key (subject, relation, object, source) — a total order, so pages
    partition the result (B.1 amended). Returns (page, total)."""
    where = ["t.created_at <= ?"]
    args = [ts]
    for col, val in (("subject", subject), ("relation", relation), ("source", source)):
        if val is not None:
            where.append(f"t.{col} = ?")
            args.append(val)
    # The predicate in SQL: candidates admitted by D, each with its last
    # archival event by D (indexed on belief_id) and whether it has any event
    # at all; held = the last event by D is not an archival, or, for a row
    # the ledger never saw, the column does not say archived by D.
    base = (
        "WITH cand AS ("
        "  SELECT t.id, t.subject, t.relation, t.object, t.source, t.created_at, "
        "         t.archived, t.archived_at, "
        "    (SELECT e.direction FROM archival_events e WHERE e.belief_id = t.id AND e.ts <= ? "
        "     ORDER BY e.ts DESC, e.rowid DESC LIMIT 1) AS last_dir, "
        "    (SELECT e.ts FROM archival_events e WHERE e.belief_id = t.id AND e.ts <= ? "
        "     ORDER BY e.ts DESC, e.rowid DESC LIMIT 1) AS last_ts, "
        "    EXISTS (SELECT 1 FROM archival_events e WHERE e.belief_id = t.id) AS has_events "
        "  FROM triples t WHERE " + " AND ".join(where) + "), "
        "held AS ("
        "  SELECT * FROM cand WHERE "
        "    (has_events AND (last_dir IS NULL OR last_dir != 'archived')) "
        "    OR (NOT has_events AND NOT (archived = 1 AND archived_at IS NOT NULL AND archived_at <= ?)))"
    )
    params = [ts, ts, *args, ts]
    total = int(conn.execute(base + " SELECT COUNT(*) FROM held", params).fetchone()[0])
    rows = conn.execute(
        base + " SELECT * FROM held ORDER BY created_at ASC, subject ASC, relation ASC, object ASC, "
        "source ASC LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
    superseded = _supersede_times(conn, ts)
    page = []
    for r in rows:
        sup = superseded.get(int(r["id"]))
        read_from = "ledger" if r["has_events"] else ("column" if r["archived"] else "none")
        page.append({
            "belief_id": int(r["id"]),
            "subject": r["subject"], "relation": r["relation"], "object": r["object"],
            "source": r["source"],
            "source_class": SOURCE_CLASS.get(r["source"], UNKNOWN_CLASS),
            "admitted_at": float(r["created_at"]), "admitted_at_iso": _iso(float(r["created_at"])),
            "status_at": "held_superseded" if sup is not None else "held",
            "superseded_at": sup, "superseded_at_iso": _iso(sup),
            "archival_at_d": {"last_event": r["last_dir"], "ts": r["last_ts"], "read_from": read_from},
        })
    return page, total


def coverage(conn: Database) -> dict:
    """The /cortex/stats block — derived on every read, never stored."""
    earliest = earliest_admission(conn)
    events = int(conn.execute("SELECT COUNT(*) FROM archival_events").fetchone()[0])
    try:
        acts = int(conn.execute(
            "SELECT COUNT(*) FROM outcome_events WHERE accepted = 1 AND recall_count IS NOT NULL"
        ).fetchone()[0])
    except OperationalError:
        acts = 0
    ledger = archival_ledger_from(conn)
    return {
        "earliest_admitted_at": earliest, "earliest_admitted_at_iso": _iso(earliest),
        "archival_events": events, "supersede_acts": acts,
        "archival_ledger_from": ledger["iso"], "archival_ledger_from_ts": ledger["ts"],
        "archival_ledger_reason": ledger["reason"],
        "read": ROUTE,
        "note": ("derived from the record on every read; before `earliest_admitted_at` an "
                 "as-of is empty by construction. " + FIXED_SENTENCE + " " + ledger_sentence(ledger["iso"])),
    }


__all__ = ["PREDICATE", "FIXED_SENTENCE", "READ_FROM", "CONTRACT_VERSION", "ROUTE", "REFUSAL_EXAMPLE",
           "NO_EVENTS_REASON", "AsOfRefused", "resolve_instant", "archival_ledger_from",
           "ledger_sentence", "held_at", "coverage", "earliest_admission", "iso_utc"]
