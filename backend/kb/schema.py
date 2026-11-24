"""SQLite schema for the tiered triple store + LlamaIndex chunk metadata.

Tier semantics per arch §10.2:
  0 core | 1 active | 2 warm | 3 cold

current_weight() decays based on (now - last_fired) but never deletes.

Packet F.2 — **tier 4 is no longer a tier.** It was used as an archive marker
by `POST /kb/dedup/confirm`, written by direct SQL that bypassed `add()`'s own
validation (which correctly rejects tier 4 as out of range). All three tier
tables are keyed 0-3, so a single tier-4 row raised `KeyError: 4` out of both
`rebalance()` and `current_weight()` — killing the KAIROS daemon and every
chat turn whose retrieval touched the row (both measured).

The deeper error was semantic: "deduplicated" is a judgement about the
**record** (this claim is the same as that one); a tier is a statement about
**prominence** (how salient this is now). Conflating them made a dedup loser
indistinguishable from a faded belief, which is exactly the distinction I-2's
audit story rests on. Archive status is therefore its own column.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

# W10c — the record's own tables (held commitments, governed actions, record tombstones), the tombstone's
# hashed body and the one canonical hash live in `backend/memory/record_schema.py`, so that a recorder with
# no memory engine opens a lineage store without loading this module; the names here are the same objects.
from backend.memory.record_schema import (ACTION_RECORDS_DDL, HELD_COMMITMENTS_DDL, RECORD_TOMBSTONES_DDL,
                                          canonical_hash, record_tombstone_body, record_tombstone_hash)
from backend.store import Database, StoreTarget, as_database, connect as store_connect

logger = logging.getLogger("nexus.kb.schema")

DEFAULT_DB = Path("data/kb.db")

# The substrate's own relations — rows the mechanism mints about its own
# activity, as opposed to claims anyone asserted. ONE spelling for the
# partition four sites draw: the retriever's share cap, the semantic index's
# default exclusion (which re-exports this object as
# DEFAULT_EXCLUDED_RELATIONS, env-overridable there), AK's ingest guard, and
# AN.2's decay-clock rule in add(). Homed here, the lowest layer, because
# tiered_store cannot import semantic_index without a cycle (AN.2 moved it;
# the semantic_index name and its override semantics are unchanged).
SUBSTRATE_RELATIONS = ("co_activated", "recurring_query_term")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS triples (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject       TEXT    NOT NULL,
    relation      TEXT    NOT NULL,
    object        TEXT    NOT NULL,
    weight        REAL    NOT NULL DEFAULT 0.5,
    tier          INTEGER NOT NULL DEFAULT 2,
    granularity   TEXT    NOT NULL,
    source        TEXT    NOT NULL,
    fire_count    INTEGER NOT NULL DEFAULT 0,
    created_at    REAL    NOT NULL,
    last_fired    REAL    NOT NULL,
    -- F.2 — record-layer status, not prominence. Set by dedup confirmation.
    -- An archived belief is superseded, not forgotten: it keeps its tier, its
    -- weight, its source and its id, and stays queryable by id and by export.
    -- It is excluded from active retrieval and from tier movement.
    archived      INTEGER NOT NULL DEFAULT 0,
    -- L.2a — WHY a row was archived, and WHEN. `archived` alone is an I-7
    -- defect the moment a second archival reason exists: "superseded by a
    -- duplicate" (dedup) and "the gate would refuse this" (Packet L) are
    -- different judgements and must not share one boolean. Free text by
    -- convention `dedup` or `gate:<rule>`; NULL means the row was archived
    -- before reasons were recorded, which is a fact about the record and is
    -- never backfilled with a guess.
    archive_reason TEXT,
    archived_at    REAL,
    UNIQUE(subject, relation, object, source)
);

CREATE INDEX IF NOT EXISTS idx_triples_subject  ON triples(subject);
CREATE INDEX IF NOT EXISTS idx_triples_object   ON triples(object);
CREATE INDEX IF NOT EXISTS idx_triples_tier     ON triples(tier);
CREATE INDEX IF NOT EXISTS idx_triples_granularity ON triples(granularity);
CREATE INDEX IF NOT EXISTS idx_triples_weight   ON triples(weight DESC);

CREATE TABLE IF NOT EXISTS chunks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    text          TEXT    NOT NULL,
    source        TEXT    NOT NULL,
    granularity   TEXT    NOT NULL,
    metadata_json TEXT    NOT NULL,
    created_at    REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);
"""

# Packet AL — the archival act ledger. Append-only: one row per belief per
# act, surviving the reversal that clears the row's own archive columns.
# L.2's argument against a per-change history COLUMN (tiered_store.py,
# unarchive_ids) stands untouched — the column keeps describing current
# status; this ledger records ACTS, the substrate's standard append-only
# shape (outcome events, refusal ledger, calibration log). Written at the
# chokepoint inside archive_ids/unarchive_ids so every caller — the dedup
# endpoint, the L.2b script, anything future — is recorded; recording at
# call sites is how one gets forgotten. Sealed by riding RecalibrationStore's
# enumeration (unsealed/mark_sealed/verify), so KAIROS needs no change.
ARCHIVAL_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS archival_events (
    event_id     TEXT PRIMARY KEY,
    ts           REAL    NOT NULL,
    belief_id    INTEGER NOT NULL,
    direction    TEXT    NOT NULL,              -- archived | unarchived
    reason       TEXT    NOT NULL DEFAULT '',
    actor_id     TEXT    NOT NULL DEFAULT 'operator',
    actor_role   TEXT    NOT NULL DEFAULT 'local_operator',
    actor_source TEXT    NOT NULL DEFAULT 'local',
    record_hash  TEXT    NOT NULL,
    sealed_block INTEGER
);
CREATE INDEX IF NOT EXISTS idx_archival_belief ON archival_events(belief_id);
"""


def archival_event_body(event_id: str, ts: float, belief_id: int,
                        direction: str, reason: str, actor_id: str,
                        actor_role: str, actor_source: str) -> dict:
    """The hashed body of one archival event — ONE rule, used by the writer
    (tiered_store) and the verifier (recalibration.verify_governance_records)
    so the two cannot drift. Same canonical construction as the other
    governance records."""
    return {"event_id": event_id, "ts": ts, "belief_id": int(belief_id),
            "direction": direction, "reason": reason,
            "actor": {"id": actor_id, "role": actor_role,
                      "source": actor_source}}


def archival_event_hash(body: dict) -> str:
    return canonical_hash(body)      # W10c: the one canonical construction (the tombstone's hash is it too)


# Packet AT — the refusal ledger. ONE table for every confirm route's Law 3
# refusal (the outcome route keeps its richer outcome_events rows and does
# not double-write). The line it records (AT, adopted): a refusal is
# RECORDED when a well-formed, in-vocabulary request asked for a governed
# act and the system declined — "would not" is a governance act. A request
# that was never valid (schema, vocabulary) and a system that could not
# (backend down, index absent) are DECLARED on the response and recorded
# nowhere; ingest-gate refusals are counted per pass, never ledgered
# (OQ-4). `request_sha256` identifies the attempt without storing arbitrary
# client text. Sealed by riding RecalibrationStore's enumeration, as AL's
# ledger is.
# v3.1 Phase 1 — work-unit attribution. A `work_unit` is the application's
# unit of value (a claim, a contract, a ticket) that cognition was applied
# to; NEXUS records which unit a turn belonged to and which units admitted a
# claim. It never sequences, queues or moves one (Law XII). Attribution is a
# JUNCTION, not a column on `triples`: `add()` upserts on the claim key, so a
# column would record the first writer and be silent on re-assertion (BC
# Phase 0, item 2). The junction records ADMISSION — the gate let the claim
# in during this unit — never use; use is derivable from the lineage record,
# which carries the unit id, joined to its cited flags. Append-only: no code
# path updates or deletes a junction row (guarded by AST). `session_id` is
# not in the spec's DDL; it is what lets a restarted process re-read the
# session's open unit from the table rather than from the lost loop state.
WORK_UNIT_DDL = """
CREATE TABLE IF NOT EXISTS work_unit (
    work_unit_id          TEXT PRIMARY KEY,
    work_unit_type        TEXT NOT NULL,
    accountable_identity  TEXT,
    opened_at             REAL NOT NULL,
    closed_at             REAL,
    opened_by             TEXT NOT NULL,
    closed_by             TEXT,
    parent_work_unit_id   TEXT REFERENCES work_unit(work_unit_id),
    attributes_json       TEXT NOT NULL DEFAULT '{}',
    attribution_status    TEXT NOT NULL CHECK (attribution_status IN ('attributed','unattributed')),
    session_id            TEXT
);
CREATE TABLE IF NOT EXISTS triple_work_unit (
    triple_id        INTEGER NOT NULL REFERENCES triples(id),
    work_unit_id     TEXT NOT NULL REFERENCES work_unit(work_unit_id),
    first_seen_turn  INTEGER NOT NULL,
    lineage_id       TEXT,
    PRIMARY KEY (triple_id, work_unit_id)
);
CREATE INDEX IF NOT EXISTS idx_twu_unit ON triple_work_unit(work_unit_id);
CREATE INDEX IF NOT EXISTS idx_work_unit_session ON work_unit(session_id);
"""

REFUSED_REQUESTS_DDL = """
CREATE TABLE IF NOT EXISTS refused_requests (
    event_id       TEXT PRIMARY KEY,
    ts             REAL NOT NULL,
    route          TEXT NOT NULL,
    rule           TEXT NOT NULL,
    detail         TEXT NOT NULL DEFAULT '',
    actor_id       TEXT NOT NULL DEFAULT 'operator',
    actor_role     TEXT NOT NULL DEFAULT 'local_operator',
    actor_source   TEXT NOT NULL DEFAULT 'local',
    scope          TEXT NOT NULL DEFAULT 'default',
    request_sha256 TEXT NOT NULL,
    record_hash    TEXT NOT NULL,
    sealed_block   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_refused_route ON refused_requests(route);
"""


def refusal_body(event_id: str, ts: float, route: str, rule: str,
                 detail: str, actor_id: str, actor_role: str,
                 actor_source: str, scope: str, request_sha256: str) -> dict:
    """The hashed body of one refusal — ONE rule for writer and verifier."""
    return {"event_id": event_id, "ts": ts, "route": route, "rule": rule,
            "detail": detail,
            "actor": {"id": actor_id, "role": actor_role,
                      "source": actor_source},
            "scope": scope, "request_sha256": request_sha256}


def refusal_hash(body: dict) -> str:
    return archival_event_hash(body)      # the same canonical construction


# W6 — the work-unit boundary ledger. Append-only: one row per act (`close`,
# `reopen`), written by WorkUnitStore in the same transaction as the unit
# row's own change, and sealed by riding RecalibrationStore's enumeration
# (unsealed / mark_sealed / verify), as the archival ledger does. The unit
# row's `closed_at` keeps describing current status; this ledger records the
# acts, and a reopen never amends the close it reverses. A close lists the
# ids of the unit's lineage records at the act, oldest first: ids, never
# copies. The actor fields and `accountable` are what the request asserted.
WORK_UNIT_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS work_unit_events (
    event_id      TEXT PRIMARY KEY,
    ts            REAL NOT NULL,
    work_unit_id  TEXT NOT NULL,
    kind          TEXT NOT NULL,               -- close | reopen
    actor_id      TEXT NOT NULL,
    actor_role    TEXT NOT NULL,
    actor_source  TEXT NOT NULL,
    accountable   TEXT,
    lineage_ids   TEXT NOT NULL DEFAULT '[]',
    reverses      TEXT,                        -- reopen: the close event it reverses
    asserted_on   TEXT,                        -- the lineage record of the turn that asserted the boundary
    identity_mechanism TEXT,                   -- how that turn's `accountable` was established; NULL = asserted
    record_hash   TEXT NOT NULL,
    sealed_block  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_wue_unit ON work_unit_events(work_unit_id);
"""


def lineage_ids_text(ids) -> str:
    """The one stored form of a boundary event's id list: a JSON array of
    strings, as `json.dumps` writes it."""
    return json.dumps([str(i) for i in ids])


def parse_lineage_ids(text) -> "list[str] | None":
    """The id list a stored `lineage_ids` text holds, or None when the text
    is anything but the writer's own form. A JSON object keyed by the ids
    reads as the same list under `list()`, and its values are then free text
    that no hash covers; a duplicate key or a stray space parses the same
    and reads differently. So the text must BE the canonical dump of what it
    parses to, or it is not an id list."""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list) or not all(isinstance(i, str) for i in parsed):
        return None
    return parsed if lineage_ids_text(parsed) == text else None


def work_unit_event_body(event_id: str, ts: float, work_unit_id: str, kind: str,
                         actor_id: str, actor_role: str, actor_source: str,
                         accountable: "str | None", lineage_ids, reverses: "str | None", *,
                         asserted_on: "str | None" = None,
                         identity_mechanism: "str | None" = None) -> dict:
    """The hashed body of one boundary event — ONE rule for writer
    (work_units) and verifier (recalibration.verify_governance_records).
    `lineage_ids` is a list of strings here: the verifier passes what
    `parse_lineage_ids` returned and names a row whose text it refused.
    `asserted_on` and `identity_mechanism` enter the body only when present."""
    body = {"event_id": event_id, "ts": ts, "work_unit_id": work_unit_id, "kind": kind,
            "actor": {"id": actor_id, "role": actor_role, "source": actor_source},
            "accountable": accountable, "lineage_ids": list(lineage_ids), "reverses": reverses}
    if asserted_on:
        body["asserted_on"] = asserted_on
    if identity_mechanism:
        body["identity_mechanism"] = identity_mechanism
    return body


def work_unit_event_hash(body: dict) -> str:
    return archival_event_hash(body)      # the same canonical construction


def connect(db_path: "Path | str | StoreTarget" = DEFAULT_DB) -> Database:
    # W3b — the connection comes from the store interface (`backend.store`):
    # a path is SQLite exactly as before (check_same_thread=False, because
    # the KAIROS daemon runs on a different thread to the request handler
    # that opened the connection; sqlite3.Row), a StoreTarget names the
    # backend. The DDL and the migration are the store's own and run here.
    conn = store_connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.executescript(ARCHIVAL_EVENTS_DDL)      # AL — additive, re-runnable
    migrate(conn)
    conn.commit()
    return conn


# Rows written before F.2 used tier 4 to mean "archived". Anything at or above
# this is a legacy archive marker, not a prominence level.
LEGACY_ARCHIVE_TIER = 4
# Where a migrated legacy row lands. Cold, not core: it was archived because a
# human judged it a duplicate, so it should not re-enter retrieval at strength
# if it is ever un-archived.
MIGRATED_ARCHIVE_TIER = 3


def migrate(conn: Database) -> dict:
    """Additive and re-runnable — the `chain.py` / `graph_store.py` pattern.

    Returns a summary so callers can *declare* what a migration did (I-6)
    rather than leaving it to be inferred. Never deletes a row (Law 4); the
    legacy tier-4 rows are re-tiered and flagged, not removed.

    W3b: takes a `Database`, or the raw SQLite connection of an answered-for
    raw open (a script's temp copy; a test's scratch store), wrapped here.
    """
    conn = as_database(conn)
    cols = {c.name for c in conn.columns("triples")}
    added = []
    if "archived" not in cols:
        conn.execute(
            "ALTER TABLE triples ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        added.append("archived")

    # L.2a — the archival judgement gets a name and a time. Deliberately
    # NULLable and deliberately NOT backfilled: a row archived before this
    # migration was archived for a reason nobody recorded, and writing
    # "dedup" over it would fabricate a record in the layer whose only job is
    # faithfulness. NULL is the true value and reads as "pre-L.2a".
    for col, decl in (("archive_reason", "TEXT"), ("archived_at", "REAL")):
        if col not in cols:
            conn.execute(f"ALTER TABLE triples ADD COLUMN {col} {decl}")
            added.append(col)

    # Read first: `connect()` runs on every store open, and an unconditional
    # UPDATE would issue a write (and a file mtime change) on every one of
    # them. A no-op migration should be genuinely no-op.
    pending = int(conn.execute(
        "SELECT COUNT(*) AS n FROM triples WHERE tier >= ?",
        (LEGACY_ARCHIVE_TIER,),
    ).fetchone()["n"])
    migrated = 0
    if pending:
        migrated = conn.execute(
            "UPDATE triples SET archived = 1, tier = ? WHERE tier >= ?",
            (MIGRATED_ARCHIVE_TIER, LEGACY_ARCHIVE_TIER),
        ).rowcount
        logger.info("F.2 migration: %d legacy tier-%d row(s) -> archived=1, "
                    "tier=%d (none deleted)",
                    migrated, LEGACY_ARCHIVE_TIER, MIGRATED_ARCHIVE_TIER)
    if added or migrated:
        conn.commit()
    return {"columns_added": added, "legacy_tier4_migrated": max(0, migrated)}
