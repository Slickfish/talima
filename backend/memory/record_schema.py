"""W10c (run/specs/W10c.md S1) — the record's own tables, outside the knowledge store's schema.

The record-side DDL the lineage store executes and the tombstone's hashed body, moved here verbatim from
`backend/kb/schema.py`, which re-exports the same objects, so every caller is unchanged. They moved because the
interceptor records with no memory engine: `backend.memory.lineage` imported them from `backend.kb` inside its
constructor, and an import graph that counts function bodies (tests/test_w10c_standalone.py) reached the knowledge
store through that one line. Nothing here imports anything but the standard library.

`canonical_hash` is the one canonical construction the governance records hash by (sorted keys, no whitespace,
UTF-8): `archival_event_hash` and `record_tombstone_hash` are both it.
"""
from __future__ import annotations

import hashlib
import json


def canonical_hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# W9 (run/specs/W9.md D8) — commit now, reveal later: a salted commitment to a
# note the person supplied. The content and its salt stay in this table and
# nowhere else (no prompt, no retrieval, no record); only the commitment is
# sealed, as a leaf of the next block's `commit` root.
HELD_COMMITMENTS_DDL = """
CREATE TABLE IF NOT EXISTS held_commitments (
    commitment_id   TEXT PRIMARY KEY,
    ts              REAL NOT NULL,
    commitment      TEXT NOT NULL,
    salt            TEXT NOT NULL,
    content         TEXT NOT NULL,
    sealed_block    INTEGER
);
"""

# W10b (run/specs/W10b.md D1, D2) — governed actions: one row per outbound
# request NEXUS's runtime makes (the web search, the seeder's fetches) or a
# client reports (`client_reported`), inserted before a runtime request leaves
# and given its outcome once, before the result is used; sealed with the
# governance tables. Never a header's value: a key is named and fingerprinted.
ACTION_RECORDS_DDL = """
CREATE TABLE IF NOT EXISTS action_records (
    action_id            TEXT PRIMARY KEY,
    kind                 TEXT NOT NULL,
    evidence_class       TEXT NOT NULL,
    method               TEXT NOT NULL,
    target               TEXT NOT NULL,
    request_hash         TEXT NOT NULL,
    credentials_json     TEXT NOT NULL,
    lineage_id           TEXT,
    trigger_route        TEXT,
    accountable_identity TEXT,
    identity_mechanism   TEXT,
    identity_salt        TEXT,
    identity_commitment  TEXT,
    signature_key_id     TEXT,
    signature            TEXT,
    requested_at         REAL NOT NULL,
    state                TEXT NOT NULL,
    status               TEXT,
    response_hash        TEXT,
    completed_at         REAL,
    record_hash          TEXT,
    sealed_block         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_action_records_lineage ON action_records(lineage_id);
CREATE INDEX IF NOT EXISTS idx_action_records_signature ON action_records(signature);
"""

# W8 (D9) — a record's content disposed of, as a governance act: one row per
# tombstoned record, sealed with the governance tables. The record keeps its
# id, time, learning mode, injected set, hash, commitment root, every field's
# commitment and the salts of the kept fields, which this row names.
RECORD_TOMBSTONES_DDL = """
CREATE TABLE IF NOT EXISTS record_tombstones (
    event_id      TEXT PRIMARY KEY,
    ts            REAL NOT NULL,
    lineage_id    TEXT NOT NULL UNIQUE,
    actor_id      TEXT,
    actor_role    TEXT,
    actor_source  TEXT,
    reason        TEXT NOT NULL,
    kept_fields   TEXT NOT NULL,
    record_hash   TEXT NOT NULL,
    sealed_block  INTEGER
);
"""


def record_tombstone_body(event_id: str, ts: float, lineage_id: str, actor_id, actor_role, actor_source,
                          reason: str, kept_fields: list) -> dict:
    """The hashed body of a tombstone act. `kept_fields` is the sorted list of
    the record's fields whose values and salts the tombstone keeps."""
    return {"event_id": event_id, "ts": ts, "lineage_id": lineage_id, "actor_id": actor_id,
            "actor_role": actor_role, "actor_source": actor_source, "reason": reason,
            "kept_fields": list(kept_fields)}


def record_tombstone_hash(body: dict) -> str:
    return canonical_hash(body)      # the same canonical construction as an archival event's
