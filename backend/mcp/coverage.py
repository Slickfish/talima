"""MCP-2 — coverage counters, persisted beside the record, never in it.

The record can no longer claim "every turn" under MCP (MCP-1 item 3): the
client decides when to call tools. What NEXUS can observe it counts here,
one row per event, in `kb.db` next to `lineage_records`:

    recorded                    retrieve, then record — a lineage row exists
    retrieved_without_record    a handle expired unrecorded (detectable)
    recorded_without_retrieve   a record named a retrieval_id NEXUS did not
                                hold (detectable; the record is refused)

A turn with neither is not visible to NEXUS, so no row can say so; the
coverage sentence names that. Counts are derived from the rows on every
read, never stored as numbers (the AR rule). `first_run` is the MCP
process's first start on this store, the from-date a frozen-by-default
learning state is declared against (MCP-2 decision 3).
"""
from __future__ import annotations

from backend.store import Database
import time

RECORDED = "recorded"
RETRIEVED_WITHOUT_RECORD = "retrieved_without_record"
RECORDED_WITHOUT_RETRIEVE = "recorded_without_retrieve"
RECORD_FAILED = "record_failed"        # W6 — a record that could not be written: refused by name, never RECORDED
EVENTS = (RECORDED, RETRIEVED_WITHOUT_RECORD, RECORDED_WITHOUT_RETRIEVE, RECORD_FAILED)

COVERAGE_TABLE = "mcp_coverage"
META_TABLE = "mcp_meta"

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {COVERAGE_TABLE} (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL NOT NULL,
    event        TEXT NOT NULL,
    retrieval_id TEXT,
    lineage_id   TEXT,
    reason       TEXT
);
CREATE TABLE IF NOT EXISTS {META_TABLE} (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    first_run REAL NOT NULL
);
"""

COVERAGE_SENTENCE = ("complete for every turn the client recorded; retrievals without a record "
                     "are counted; a record NEXUS could not write is refused by name, is never among "
                     "the recorded, and is counted here, or, when the store refused that count too, "
                     "in this process only (record_failed_uncounted); turns with neither are not "
                     "visible to NEXUS")


# W10b (D9) — what NEXUS can say about the actions a client takes: only what it is told.
ACTIONS_SENTENCE = ("actions are recorded only as a client reports them (nexus_record_action, client_reported): "
                    "complete only for clients that report them, and NEXUS sees none of them happen")


class CoverageStore:
    """Writes the events; reads are module functions over any connection so
    `/cortex/stats` can read a store the HTTP server opened without
    constructing anything MCP."""

    def __init__(self, conn: Database):
        self.conn = conn
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute(f"INSERT OR IGNORE INTO {META_TABLE} (id, first_run) VALUES (1, ?)",
                          (time.time(),))
        self.conn.commit()

    def record_event(self, event: str, *, retrieval_id: str | None = None,
                     lineage_id: str | None = None, reason: str | None = None) -> None:
        if event not in EVENTS:
            raise ValueError(f"unknown coverage event {event!r}")
        self.conn.execute(
            f"INSERT INTO {COVERAGE_TABLE} (ts, event, retrieval_id, lineage_id, reason) VALUES (?, ?, ?, ?, ?)",
            (time.time(), event, retrieval_id, lineage_id, reason))
        self.conn.commit()

    def first_run(self) -> float:
        row = self.conn.execute(f"SELECT first_run FROM {META_TABLE} WHERE id = 1").fetchone()
        return float(row[0])

    def counters(self) -> dict:
        return counters(self.conn)


def _has_table(conn: Database, name: str) -> bool:
    return conn.has_table(name)


def counters(conn: Database) -> dict:
    """The three counts plus the sentence, derived on every read. On a store
    the MCP server has never opened, every count is 0 and `present` is
    False — zero and absent are different facts."""
    if not _has_table(conn, COVERAGE_TABLE):
        return {"present": False, **{e: 0 for e in EVENTS}, "sentence": COVERAGE_SENTENCE,
                "actions_sentence": ACTIONS_SENTENCE}
    out = {"present": True, "sentence": COVERAGE_SENTENCE, "actions_sentence": ACTIONS_SENTENCE}
    for e in EVENTS:
        out[e] = int(conn.execute(f"SELECT COUNT(*) FROM {COVERAGE_TABLE} WHERE event = ?", (e,)).fetchone()[0])
    return out


def first_run(conn: Database) -> float | None:
    if not _has_table(conn, META_TABLE):
        return None
    row = conn.execute(f"SELECT first_run FROM {META_TABLE} WHERE id = 1").fetchone()
    return float(row[0]) if row else None
