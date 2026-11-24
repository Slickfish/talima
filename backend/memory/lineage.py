"""Answer-lineage records (P3 / L2 — closes REVIEW_REPORT F-22's scope gap).

Every grounded turn writes one lineage record: which beliefs, at what
evidential weight and tier, with which surfaced contradictions, produced
which response. The record is hashed canonically (`record_hash`) and later
digest-sealed into the archive chain by KAIROS.

Write topology (READINESS_REPORT §4 rule, binding): lineage rows live in
kb.db and are written by the chat/API path; archive.db keeps exactly one
writer (KAIROS), which seals digests into the block it already appends and
backfills `sealed_block` here.

Vocabulary discipline (seam spec §0): this is **evidence lineage** — which
beliefs produced which output. It is never "chain of thought"; model
internals are not captured and no claim implies they are.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from backend.memory import commitments as _commitments
from backend.store import Database, Error, Row, StoreTarget, connect as store_connect
import time
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_KB_DB = Path("data/kb.db")

# W0b — how a kept request body is stored: zlib over the exact bytes
# `request_hash` covers. A console turn's bytes are the canonical messages
# (`prompt_render.request_bytes_of`); an `mcp_block` turn's are the block
# as UTF-8 (its `request_hash` is `context_hash(block)`).
REQUEST_ENCODING_MESSAGES = "zlib:canonical-messages-json:v1"
REQUEST_ENCODING_BLOCK = "zlib:utf8-block:v1"
# W10c — an `intercepted` exchange's are the HTTP body exactly as the harness sent it.
REQUEST_ENCODING_HTTP_BODY = "zlib:http-body:v1"
REQUEST_ENCODINGS = (REQUEST_ENCODING_MESSAGES, REQUEST_ENCODING_BLOCK, REQUEST_ENCODING_HTTP_BODY)


def canonical_json(obj) -> str:
    """W0b — the one serialisation the capture's companions are keyed and
    sealed by: sorted keys, no whitespace, ASCII (every other character
    escaped), so the text always encodes and a lone surrogate from a daemon's
    answer cannot raise inside the record write. Raises TypeError for a value
    JSON cannot carry; `_capture_json` turns that into a declared absence."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _scrub(obj, changed: list):
    """W0b — a copy with the values neither strict JSON nor the HTTP surfaces
    can carry made text: a lone surrogate backslash-escaped, a NaN or an
    infinity spelled. `changed` collects that it happened. A record that kept
    such a value used to break `GET /lineage/{id}` (a 500)."""
    if isinstance(obj, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in obj):
            changed.append(True)
            return obj.encode("utf-8", "backslashreplace").decode("utf-8")
        return obj
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        changed.append(True)
        return repr(obj)
    if isinstance(obj, dict):
        return {(_scrub(k, changed) if isinstance(k, str) else k): _scrub(v, changed) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v, changed) for v in obj]
    return obj


def _capture_json(obj, field: str, notes: dict) -> str | None:
    """W0b — a capture companion serialised, or None with the reason noted.
    The capture describes the record and must never cost it: before W0b a
    provider's options or runtime facts could not stop a record being
    written, and they still cannot."""
    if obj is None:
        return None
    changed: list = []
    try:
        text = canonical_json(_scrub(obj, changed))
        if changed:
            notes[field + "_escaped"] = "values JSON cannot carry were escaped (a lone surrogate, NaN or infinity)"
        return text
    except (TypeError, ValueError) as e:
        notes[field] = f"not recorded: {type(e).__name__} while serialising it"
        logger.warning("replay capture: %s not recorded: %s: %s", field, type(e).__name__, e)
        return None


# ---------------------------------------------------------------------------
# The sealed-cited exemption — ONE definition, deliberately
# ---------------------------------------------------------------------------

def sealed_cited_belief_ids(
    conn: Database,
) -> tuple[set[int], dict]:
    """Belief ids cited by a SEALED lineage record, plus the counts behind it.

    **This lives here, once, because two implementations of "cited" is exactly
    the drift H.4 was written about.** Packet L's archival pass uses it to
    decide what is exempt, and `verify_packet_k.py` uses it to report which
    gate-rejected rows participated in recorded decisions. If those two ever
    disagreed, an operator would read one number and act on another —
    `tests/test_l2b_consolidation_pass.py` asserts they are the same object.

    Three states of the `cited` flag, and they do not mean the same thing:

      ``true``            cited — exempt.
      ``null`` / absent   **counts as CITED.** F.5 introduced the flag; every
                          record written before it lacks the key entirely.
                          Reading a missing key as "not cited" would strip the
                          exemption from exactly the oldest records — the ones
                          most likely to matter and least able to defend
                          themselves.
      ``false``           an explicit negative is evidence, not silence. Not
                          exempt.

    An UNSEALED record confers nothing, matching `cited_pairs()`
    (graph_store.py:176-183): an unsealed record is not yet part of the
    tamper-evident history. Following that rather than inventing a second
    standard for the same word.

    Takes a bare connection (`row_factory = sqlite3.Row`) rather than a
    `LineageStore` so it runs unchanged against a `mode=ro` handle — a
    read-only caller must never be forced through a constructor that migrates.

    Returns `(ids, stats)`. A missing `lineage_records` table is reported in
    `stats["error"]` rather than returning an empty set silently: "no
    exemptions found" and "could not look" are different answers (Law 8).
    """
    stats = {"records_total": 0, "records_sealed": 0, "items": 0,
             "cited_true": 0, "cited_false": 0, "cited_null": 0}
    ids: set[int] = set()
    try:
        rows = conn.execute(
            "SELECT injected_json, sealed_block FROM lineage_records").fetchall()
    except Error as e:
        stats["error"] = f"lineage_records unreadable: {type(e).__name__}: {e}"
        return ids, stats

    for row in rows:
        stats["records_total"] += 1
        if row["sealed_block"] is None:
            continue
        stats["records_sealed"] += 1
        try:
            injected = json.loads(row["injected_json"] or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(injected, list):
            continue
        for item in injected:
            if not isinstance(item, dict):
                continue
            stats["items"] += 1
            cited = item.get("cited", None)
            if cited is None:
                stats["cited_null"] += 1
            elif cited:
                stats["cited_true"] += 1
            else:
                stats["cited_false"] += 1
                continue
            bid = item.get("belief_id")
            if bid is not None:
                try:
                    ids.add(int(bid))
                except (TypeError, ValueError):
                    pass
    return ids, stats


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lineage_records (
    lineage_id          TEXT PRIMARY KEY,
    ts                  REAL NOT NULL,
    query_hash          TEXT NOT NULL,
    injected_json       TEXT NOT NULL,
    contradictions_json TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    prompt_prefix_hash  TEXT NOT NULL,
    response_hash       TEXT NOT NULL,
    record_hash         TEXT NOT NULL,
    sealed_block        INTEGER,
    -- AD: which process step / programme the consuming workflow said this
    -- turn belonged to. 'default' = none stated, NEVER NULL — a null is
    -- ambiguous between "not scoped" and "scope unknown", and that ambiguity
    -- is what Packet AD exists to prevent. Supplied by the request, never
    -- inferred.
    scope               TEXT NOT NULL DEFAULT 'default',
    -- AU: which citation test produced this record's `cited` flags. NULL =
    -- written before the instrument was versioned (the F.5 test). NOT in
    -- the hashed body: provenance about the flag, not part of the claim.
    citation_test       TEXT,
    -- AW: what the model was told, outside the hashed body like
    -- citation_test: {render, hash, activation} — which render produced
    -- the dynamic block, sha256 of the block that went (system message
    -- minus the stable prefix), and the (concept, activation) pairs the
    -- cortex section rendered. NULL = written before AW recorded it.
    context_json        TEXT,
    -- BA fix 2: sha256 over the canonical serialisation of the FULL request
    -- sent to the provider (roles, content, order). Sealed INTO the record
    -- hash when present (the non-default-only rule below), so a record
    -- written before this column recomputes unchanged. The history is
    -- hashed, not retained: this makes the request auditable, not
    -- re-derivable — the asymmetry the audit's Part A named.
    request_hash        TEXT,
    -- v3.1 Phase 1: which work unit the turn belonged to, and who was
    -- answerable — both RECORDED, never authenticated, supplied by the
    -- request or resolved to the session pseudo-unit. NULL on every record
    -- written before the fields existed: history is unattributed by
    -- absence, deliberately (no migration, no historical pseudo-unit).
    -- Hashed into the record only when non-null (the non-default-only
    -- rule below), so every earlier record recomputes unchanged.
    work_unit_id        TEXT,
    accountable_identity TEXT,
    -- v3.1 Phase 1b: the boundary the request asserted (`close` is the only
    -- value honoured; any other is recorded as asserted and has no effect
    -- until Phase 2), and who asserted it (actor fields, canonical JSON).
    -- Both hashed when non-null, like the two above. `attribution_flags`
    -- (JSON list; `accountability.missing` today) is UNHASHED like
    -- citation_test: it is re-derivable from the hashed fields
    -- (accountable_identity null beside boundary_asserted = close), so an
    -- edit to it is detectable by re-derivation and it carries no claim
    -- of its own. NULL on every record written before 1b.
    boundary_asserted   TEXT,
    boundary_asserted_by TEXT,
    attribution_flags   TEXT,
    -- LS: the learning state at the moment the record was written —
    -- `frozen` when FZ-2's switch was on, NULL when learning was live (the
    -- default). Hashed into the record only when non-default, the same rule
    -- as the Phase 1 fields above, so every live record and every record
    -- written before the column recomputes unchanged. Records written while
    -- frozen but before this column exist are frozen by the operator's
    -- declaration, not by the record; no backfill.
    learning_mode       TEXT,
    -- C-38: the reply, separated at the provider boundary. `response_hash`
    -- above stays the hash of the content stream as delivered — continuity.
    -- `answer_hash` is sha256 of the stated answer and `reasoning_hash` of
    -- the reasoning, both hashed into the record only when present, the LS
    -- rule, so every earlier record recomputes unchanged. `answer_chars`
    -- and `reasoning_chars` are the lengths, UNHASHED: re-derivable from
    -- the texts the hashes bind, and the data A's cap review waits for.
    -- `reasoning_chars` is NULL when the reply carried no reasoning.
    -- `answer_empty_reason` is the declared empty-answer state, unhashed
    -- provenance like `citation_test`, NULL unless the answer was empty.
    -- No reasoning text enters the record.
    answer_hash         TEXT,
    reasoning_hash      TEXT,
    answer_chars        INTEGER,
    reasoning_chars     INTEGER,
    answer_empty_reason TEXT,
    -- MCP-2: which shape of turn the record describes. NULL is the console's
    -- full turn, the default and every pre-MCP record. `mcp_block` is a turn
    -- split at the model call across a client's model: NEXUS returned the
    -- block and recorded the answer the client sent, so `model_id`,
    -- `prompt_prefix_hash`, `response_hash`, `answer_hash`, `answer_chars`
    -- and `request_hash` carry the client's claims and the block's bytes,
    -- and `reasoning_hash`, `reasoning_chars`, `answer_empty_reason` and
    -- `contradictions` are undefined and NULL or empty. Hashed into the
    -- record only when non-default, the LS rule, so every earlier record
    -- recomputes unchanged.
    record_scope        TEXT,
    -- W0b (replay capture): what a later re-run of this turn will need.
    -- `inference_seed` the seed sent with the answering request;
    -- `model_digest` the content digest of the model as served;
    -- `inference_config_hash` keys the exact options sent (the request body
    -- minus messages and seed) in `inference_configs`; `environment_hash`
    -- keys the observable runtime facts in `environments`; `capture_notes`
    -- (canonical JSON) names the reason for every one of them that is
    -- absent. Each is hashed into the record only when present, the LS
    -- rule, so every earlier record recomputes unchanged. The request bytes
    -- themselves are kept beside the record in `request_bodies`, keyed by
    -- `request_hash`, and are NOT in the record or its hash.
    inference_seed      INTEGER,
    model_digest        TEXT,
    inference_config_hash TEXT,
    environment_hash    TEXT,
    capture_notes       TEXT,
    -- W4: how `accountable_identity` was established (IDENTITY_MECHANISMS
    -- below). NULL is `asserted`, the default and every earlier record: the
    -- identity as the request (or, under MCP, the configuration) stated it,
    -- never authenticated. `proxy`: taken from the identity header of a
    -- loopback peer by a process that answers only for its configured owner.
    -- Hashed into the record only when non-default, the LS rule, so every
    -- earlier record recomputes unchanged.
    identity_mechanism  TEXT,
    -- W6 — two decision fields, each hashed into the record only when
    -- present (the LS rule), so every earlier record recomputes unchanged.
    -- envelope_version: the installed recalibration policy in force when the
    -- record was written, whatever it is called. NULL when no policy was
    -- installed (the compiled default was in force), and on every record
    -- written before W6, which says nothing about the envelope then.
    -- forecast: the citation forecast's turn-level score, as
    -- canonical JSON. NULL when no forecast ran for the turn.
    envelope_version    TEXT,
    forecast            TEXT
);
CREATE INDEX IF NOT EXISTS idx_lineage_ts ON lineage_records(ts);

-- W0b: the exact request, kept so a decision can be re-run. VERIFIED BY THE
-- RECORD, NOT PART OF IT: the bytes are the canonical form `request_hash`
-- covers (the messages; under `mcp_block`, the block), zlib compressed, and
-- on write they must hash to the key or they are not kept;
-- `verify_request_bodies` decompresses and re-hashes every row, and
-- /lineage/verify reports it beside the record's three layers, never inside
-- them (a corrupted copy is not a tampered record). Not in the record or its
-- hash. Written in the record's own transaction when NEXUS_KEEP_REQUESTS is
-- on (the default); never expired here — disposal belongs to retention (W14)
-- under the tombstone rule.
CREATE TABLE IF NOT EXISTS request_bodies (
    request_hash  TEXT PRIMARY KEY,
    encoding      TEXT NOT NULL,
    body          BLOB NOT NULL,
    raw_bytes     INTEGER NOT NULL,
    first_seen_ts REAL NOT NULL
);
-- W0b: each distinct options object and environment descriptor once, keyed
-- by the sha256 of its canonical JSON (the record carries the key).
CREATE TABLE IF NOT EXISTS inference_configs (
    config_hash   TEXT PRIMARY KEY,
    config_json   TEXT NOT NULL,
    first_seen_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS environments (
    environment_hash TEXT PRIMARY KEY,
    descriptor_json  TEXT NOT NULL,
    first_seen_ts    REAL NOT NULL
);

-- AR (advice recall): the backward index, claim -> the records that
-- injected it. DERIVED, NOT RECORD: every row is re-derivable from
-- `injected_json`, which stays the sealed source; `verify_recall_index`
-- names any divergence in either direction. Written beside each record in
-- the same transaction, and backfilled once at open for records that
-- predate it (a derived write to kb.db on first open, declared on
-- /cortex/stats.recall.index). Built because the S1.5 measurement put the
-- scan's p95 at 201.5 ms on 100,000 records against a 200 ms rule fixed
-- before measuring.
-- W8: the record's salted field commitments (run/specs/W8.md, D4). One salt
-- per field of the record's hashed body, hex, kept BESIDE the record and
-- never in it or its hash; the commitment root over the fields. Written in
-- the record's own transaction; for a record written before W8, at the
-- store's first open after it (a derived write, counted).
CREATE TABLE IF NOT EXISTS record_salts (
    lineage_id  TEXT NOT NULL REFERENCES lineage_records(lineage_id),
    field       TEXT NOT NULL,
    salt        TEXT NOT NULL,
    PRIMARY KEY (lineage_id, field)
);
CREATE TABLE IF NOT EXISTS record_commitments (
    lineage_id       TEXT PRIMARY KEY REFERENCES lineage_records(lineage_id),
    commitment_root  TEXT NOT NULL,
    made_at          REAL NOT NULL,
    -- W8 (D6): for a record sealed before W8, the block whose binding seal
    -- bound this root to the record's hash; NULL until then, and for every
    -- record sealed after W8, whose own seal carries its leaf.
    bound_block      INTEGER
);
-- W8 (D9): every field's commitment C_f, kept when a record is tombstoned, so
-- the record still verifies by its root after its content and the dropped
-- fields' salts are gone. Written only by `tombstone()`.
CREATE TABLE IF NOT EXISTS record_field_commitments (
    lineage_id  TEXT NOT NULL REFERENCES lineage_records(lineage_id),
    field       TEXT NOT NULL,
    commitment  TEXT NOT NULL,
    PRIMARY KEY (lineage_id, field)
);
CREATE TABLE IF NOT EXISTS lineage_injections (
    belief_id   INTEGER NOT NULL,
    lineage_id  TEXT NOT NULL REFERENCES lineage_records(lineage_id),
    PRIMARY KEY (belief_id, lineage_id)
);
CREATE INDEX IF NOT EXISTS idx_injections_lineage ON lineage_injections(lineage_id);
"""

RECALL_INDEX_TABLE = "lineage_injections"

# MCP-2 — the record's scope vocabulary. `full_turn` is the console's turn,
# the default, stored as NULL and never hashed; `mcp_block` is a turn split
# at the model call across a client's model, stored and sealed.
FULL_TURN = "full_turn"
MCP_BLOCK = "mcp_block"
# W10c — `intercepted` is one model exchange relayed by the interceptor (`backend/intercept/`): the harness's
# request and the upstream's response, hashed as their bytes; stored and sealed like `mcp_block`.
INTERCEPTED = "intercepted"
RECORD_SCOPES = (FULL_TURN, MCP_BLOCK, INTERCEPTED)

# W4 — how a record's `accountable_identity` was established: the closed
# vocabulary, each member with what it means. `asserted` is the default,
# stored as NULL and never hashed. `proxy` means this process took the login
# from the identity header of a loopback peer and answers only for its
# configured owner: the proxy authenticated the person, NEXUS did not. A later
# mechanism (`oauth`, `signed`) is a new member here, not a schema change: the
# column is TEXT and the hash rule seals any member that is not the default.
IDENTITY_ASSERTED = "asserted"
IDENTITY_PROXY = "proxy"
IDENTITY_MECHANISMS = {
    IDENTITY_ASSERTED: "recorded as the request (or, under MCP, the configuration) stated it; never authenticated",
    IDENTITY_PROXY: ("taken from the identity header of a loopback peer by a process that answers only for its "
                     "configured owner: the proxy authenticated the person, NEXUS did not"),
}

# AR / W4 — the recall contract, one definition for its two surfaces
# (`GET /belief/{id}/recall` and MCP's `nexus_recall`), beside the rows it
# describes. 1 = AR; 2 = W4: each row's `identity_label` reads the record's
# `identity_mechanism` (NULL is `asserted`) where version 1 printed the
# literal `asserted` on every row, and the declaration says what each label
# means. The keys did not change.
RECALL_CONTRACT_VERSION = 2
RECALL_PER_PERSON = {
    "declared": True,
    "field": "accountable",
    "label": "per row: identity_label, read from the record's identity_mechanism",
    "labels": dict(IDENTITY_MECHANISMS),
    "note": ("per-person rows: each record names the identity that was answerable for that answer, and "
             "each row's identity_label says how it was established. asserted: recorded as stated, never "
             "authenticated. proxy: taken from a loopback identity proxy's header; the proxy authenticated "
             "the person and NEXUS did not. NEXUS itself authenticates no person. NEXUS never aggregates "
             "by person; a consumer that groups these rows does so on its own account."),
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def forecast_from_text(text):
    """W6 — the record's forecast as its column holds it: None for NULL; the
    dict when the text is exactly the writer's form of it (sorted keys,
    compact separators); otherwise the text itself. No record was hashed
    over a text, so a forecast column edited into anything but the writer's
    form (a duplicate key, which one reader takes first and another last; a
    stray space; something that is not JSON) is named by the record's own
    hash, never raised on and never read as a forecast it is not."""
    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text
    if isinstance(parsed, dict) and json.dumps(parsed, sort_keys=True, separators=(",", ":")) == text:
        return parsed
    return text


# W8 (D9) — what a tombstone keeps of a record's hashed fields, and how it
# marks the rest. A NOT NULL column holds the mark (self-describing, never a
# plausible value); a nullable one holds NULL.
TOMBSTONE_KEPT_FIELDS = ("lineage_id", "ts", "learning_mode", "injected")
TOMBSTONE_MARK = "tombstoned"
# Each dropped field's statement, spelled whole so every one is a statement the
# dialect guard prepares on Postgres; a hashed field with no entry here is
# refused by name, so a new hashed column must say how a tombstone drops it.
_TOMBSTONE_DROP = {   # field -> (the statement that drops it, the value it leaves)
    "query_hash": ("UPDATE lineage_records SET query_hash = ? WHERE lineage_id = ?", TOMBSTONE_MARK),
    "model_id": ("UPDATE lineage_records SET model_id = ? WHERE lineage_id = ?", TOMBSTONE_MARK),
    "prompt_prefix_hash": ("UPDATE lineage_records SET prompt_prefix_hash = ? WHERE lineage_id = ?", TOMBSTONE_MARK),
    "response_hash": ("UPDATE lineage_records SET response_hash = ? WHERE lineage_id = ?", TOMBSTONE_MARK),
    "scope": ("UPDATE lineage_records SET scope = ? WHERE lineage_id = ?", TOMBSTONE_MARK),
    "contradictions": ("UPDATE lineage_records SET contradictions_json = ? WHERE lineage_id = ?",
                       json.dumps(TOMBSTONE_MARK)),
    "request_hash": ("UPDATE lineage_records SET request_hash = ? WHERE lineage_id = ?", None),
    "work_unit_id": ("UPDATE lineage_records SET work_unit_id = ? WHERE lineage_id = ?", None),
    "accountable_identity": ("UPDATE lineage_records SET accountable_identity = ? WHERE lineage_id = ?", None),
    "boundary_asserted": ("UPDATE lineage_records SET boundary_asserted = ? WHERE lineage_id = ?", None),
    "boundary_asserted_by": ("UPDATE lineage_records SET boundary_asserted_by = ? WHERE lineage_id = ?", None),
    "answer_hash": ("UPDATE lineage_records SET answer_hash = ? WHERE lineage_id = ?", None),
    "reasoning_hash": ("UPDATE lineage_records SET reasoning_hash = ? WHERE lineage_id = ?", None),
    "record_scope": ("UPDATE lineage_records SET record_scope = ? WHERE lineage_id = ?", None),
    "inference_seed": ("UPDATE lineage_records SET inference_seed = ? WHERE lineage_id = ?", None),
    "model_digest": ("UPDATE lineage_records SET model_digest = ? WHERE lineage_id = ?", None),
    "inference_config_hash": ("UPDATE lineage_records SET inference_config_hash = ? WHERE lineage_id = ?", None),
    "environment_hash": ("UPDATE lineage_records SET environment_hash = ? WHERE lineage_id = ?", None),
    "capture_notes": ("UPDATE lineage_records SET capture_notes = ? WHERE lineage_id = ?", None),
    "identity_mechanism": ("UPDATE lineage_records SET identity_mechanism = ? WHERE lineage_id = ?", None),
    "envelope_version": ("UPDATE lineage_records SET envelope_version = ? WHERE lineage_id = ?", None),
    "forecast": ("UPDATE lineage_records SET forecast = ? WHERE lineage_id = ?", None),
}


def _tombstone_left(field: str):
    """What a dropped field reads after a tombstone, through the row-to-payload map."""
    value = _TOMBSTONE_DROP[field][1]
    return json.loads(value) if field == "contradictions" and value is not None else value


def _seal_state(block, record_hash: str, root: "str | None") -> str:
    """W8 (D6) — what the block that sealed a record says of its commitment
    root, read exactly: "covered" (a layout-2 leaf is the hash and this root),
    "bare" (the seal carried the hash alone: every block before W8, and a
    layout-2 seal of a record that had no root then; a root made later is
    anchored only by a binding), "contradicted" (a leaf carries this hash
    with another root: the root was rewritten after its seal), "absent" (the
    block does not hold the record), or "no_root"."""
    if not root:
        return "no_root"
    if block is None:
        return "absent"
    seal = (block.fingerprint or {}).get("lineage_seal") or {}
    if block.layout == 1:
        return "bare" if record_hash in (seal.get("record_hashes") or []) else "absent"
    leaves = seal.get("leaves")
    if leaves is None:
        return "bare" if record_hash in (seal.get("record_hashes") or []) else "absent"
    if record_hash + root in leaves:
        return "covered"
    if record_hash in leaves:
        return "bare"
    return "contradicted" if any(x.startswith(record_hash) for x in leaves) else "absent"


class TombstoneRefused(ValueError):
    """A tombstone refused by name (`<reason>: <why>`); nothing was written."""


def _hash_payload_from_row(row: Row) -> dict:
    """The stored fields `compute_record_hash` reads, row-tolerantly: a
    column a record predates reads None and stays out of the hash body.
    ONE definition, used by `verify_record` and `verify_sealed_lineage` —
    two copies of this list is how a new hashed column gets verified by
    one reader and not the other (1b: `boundary_asserted` and its asserter
    join `work_unit_id` and `accountable_identity` here, nowhere else)."""
    keys = row.keys()

    def opt(col):
        return row[col] if col in keys else None

    return {
        "lineage_id":         row["lineage_id"],
        "ts":                 row["ts"],
        "query_hash":         row["query_hash"],
        "injected":           json.loads(row["injected_json"]),
        "contradictions":     json.loads(row["contradictions_json"]),
        "model_id":           row["model_id"],
        "prompt_prefix_hash": row["prompt_prefix_hash"],
        "response_hash":      row["response_hash"],
        "scope":              row["scope"],
        "request_hash":       opt("request_hash"),
        "work_unit_id":       opt("work_unit_id"),
        "accountable_identity": opt("accountable_identity"),
        "boundary_asserted":  opt("boundary_asserted"),
        "boundary_asserted_by": opt("boundary_asserted_by"),
        # LS — read row-tolerantly like the rest, so a value written INTO a
        # sealed record's NULL column after the fact changes the recomputed
        # hash and is named (T4c), and a record that predates the column
        # reads None and stays out of the body.
        "learning_mode":      opt("learning_mode"),
        # C-38 — the two hashes, row-tolerantly like the rest: a record that
        # predates them reads None and stays out of the body; a value written
        # into a sealed record's NULL column after the fact changes the
        # recomputed hash and is named.
        "answer_hash":        opt("answer_hash"),
        "reasoning_hash":     opt("reasoning_hash"),
        # MCP-2 — row-tolerantly like the rest.
        "record_scope":       opt("record_scope"),
        # W0b — the replay capture, row-tolerantly like the rest: a record
        # that predates the columns reads None for each and stays out of the
        # body; a value written into a sealed record's NULL column after the
        # fact changes the recomputed hash and is named. `capture_notes` is
        # read as the canonical JSON text the column holds — the stored bytes
        # are what the seal covers.
        "inference_seed":     opt("inference_seed"),
        "model_digest":       opt("model_digest"),
        "inference_config_hash": opt("inference_config_hash"),
        "environment_hash":   opt("environment_hash"),
        "capture_notes":      opt("capture_notes"),
        # W4 — row-tolerantly like the rest: a record that predates the
        # column reads None and stays out of the body; a mechanism written
        # into a sealed record's NULL column after the fact changes the
        # recomputed hash and is named, and so is one taken away.
        "identity_mechanism": opt("identity_mechanism"),
        # W6 — the same shape: absent from the row (an older store) or NULL
        # reads None and stays out of the body; a value written into a
        # sealed record's NULL column, or taken away, changes the recomputed
        # hash and is named.
        "envelope_version":   opt("envelope_version"),
        "forecast":           forecast_from_text(opt("forecast")),
    }


@dataclass(frozen=True)
class LineageRecord:
    lineage_id:         str
    ts:                 float
    query_hash:         str
    injected:           tuple            # ({belief_id, weight_at_injection, tier_at_injection}, …)
    contradictions:     tuple            # surfaced-contradiction payloads
    model_id:           str
    prompt_prefix_hash: str
    response_hash:      str
    record_hash:        str
    sealed_block:       int | None = None
    # AD — 'default' means the request stated none; see SCHEMA_SQL.
    scope:              str = "default"
    # AU — the citation test that produced `injected[].cited`; None = F.5.
    citation_test:      str | None = None
    # AW — {render, hash, activation}: what the model was told; None = pre-AW.
    context:            dict | None = None
    # BA fix 2 — sha256 of the whole request as sent; None = pre-fix record.
    request_hash:       str | None = None
    # v3.1 Phase 1 — the work unit and the answerable identity; None = the
    # record predates the fields (unattributed by absence).
    work_unit_id:       str | None = None
    accountable_identity: str | None = None
    # v3.1 Phase 1b — the boundary asserted on the request and by whom
    # (hashed when stated); the declared flags (unhashed, re-derivable).
    boundary_asserted:  str | None = None
    boundary_asserted_by: dict | None = None
    attribution_flags:  tuple = ()
    # LS — the learning state at write time; None = live, or written before
    # the field existed (frozen by declaration, not by record, in that case).
    learning_mode:      str | None = None
    # C-38 — the reply separated: hashes (sealed when present) and lengths
    # (unhashed); None on every record written before the fields existed,
    # and `reasoning_*` None when the reply carried no reasoning.
    answer_hash:        str | None = None
    reasoning_hash:     str | None = None
    answer_chars:       int | None = None
    reasoning_chars:    int | None = None
    answer_empty_reason: str | None = None
    # MCP-2 — `mcp_block` when the turn was split at the model call across a
    # client's model (hashed when present); None = the console's full turn,
    # the default, and every record written before the field existed.
    record_scope:       str | None = None
    # W0b — the replay capture: the seed sent, the model's content digest as
    # served, the keys of the options and the environment kept beside the
    # record, and the reason for each that is absent (all hashed when
    # present). None on every record written before W0b, and on every
    # record whose turn captured nothing (a record written outside a turn).
    inference_seed:     int | None = None
    model_digest:       str | None = None
    inference_config_hash: str | None = None
    environment_hash:   str | None = None
    capture_notes:      dict | None = None
    # W4 — how `accountable_identity` was established: `proxy` (hashed when
    # present); None = `asserted`, the default, and every record written
    # before the field existed.
    identity_mechanism: str | None = None
    # W6 — the installed recalibration policy in force when the record was
    # written (hashed when present). None = no policy installed (the compiled
    # default in force), OR a record written before W6: the record alone
    # cannot tell the two apart, and the envelope history's own timestamps
    # are what date a policy.
    envelope_version:   str | None = None
    # W6 — the citation forecast's turn-level score for this turn (hashed
    # when present): k, predicted, coverage, scorable, reason, precision,
    # null_precision. None = no forecast ran for the turn.
    forecast:           dict | None = None

    def to_dict(self) -> dict:
        return {
            "lineage_id":         self.lineage_id,
            "ts":                 self.ts,
            "query_hash":         self.query_hash,
            "injected":           list(self.injected),
            "contradictions":     list(self.contradictions),
            "model_id":           self.model_id,
            "prompt_prefix_hash": self.prompt_prefix_hash,
            "response_hash":      self.response_hash,
            "record_hash":        self.record_hash,
            "sealed_block":       self.sealed_block,
            "scope":              self.scope,
            "citation_test":      self.citation_test,
            "context":            self.context,
            "request_hash":       self.request_hash,
            "work_unit_id":       self.work_unit_id,
            "accountable_identity": self.accountable_identity,
            # W4 — the stored value with the default NAMED, as `record_scope`
            # does: a reader sees `asserted` or `proxy`, never a blank.
            "identity_mechanism": self.identity_mechanism or IDENTITY_ASSERTED,
            "envelope_version":   self.envelope_version,        # W6 — None: no policy installed, or before W6
            "forecast":           self.forecast,                # W6 — None: no forecast ran
            "boundary_asserted":  self.boundary_asserted,
            "boundary_asserted_by": self.boundary_asserted_by,
            "attribution_flags":  list(self.attribution_flags),
            "learning_mode":      self.learning_mode,
            "answer_hash":        self.answer_hash,
            "reasoning_hash":     self.reasoning_hash,
            "answer_chars":       self.answer_chars,
            "reasoning_chars":    self.reasoning_chars,
            "answer_empty_reason": self.answer_empty_reason,
            # MCP-2 — the stored value, with the default NAMED rather than
            # read as a blank: a reader sees `full_turn` or `mcp_block`.
            "record_scope":       self.record_scope or FULL_TURN,
            "inference_seed":     self.inference_seed,
            "model_digest":       self.model_digest,
            "inference_config_hash": self.inference_config_hash,
            "environment_hash":   self.environment_hash,
            "capture_notes":      self.capture_notes,
        }


class LineageStore:
    """kb.db-backed store for per-turn answer-lineage records."""

    # W10b (D10) — signs a runtime action's request when NEXUS_SIGN_REQUESTS=on; set by `create_services`.
    request_signer = None

    def __init__(self, db_path: "Path | str | StoreTarget" = DEFAULT_KB_DB):
        # W3b — a path is SQLite exactly as before; a StoreTarget names the
        # backend (`backend.store`).
        self.target = StoreTarget.of(db_path)
        self.db_path = self.target.path
        self.conn = store_connect(self.target)
        self.conn.executescript(SCHEMA_SQL)   # additive, re-runnable
        from backend.memory.record_schema import RECORD_TOMBSTONES_DDL
        self.conn.executescript(RECORD_TOMBSTONES_DDL)   # W8 (D9): this store writes it, the governance store seals it
        from backend.memory.record_schema import HELD_COMMITMENTS_DDL
        self.conn.executescript(HELD_COMMITMENTS_DDL)    # W9 (D8): commit now, reveal later
        from backend.memory.record_schema import ACTION_RECORDS_DDL
        self.conn.executescript(ACTION_RECORDS_DDL)      # W10b (D1): governed actions
        self._migrate_scope()
        self.conn.commit()
        # AR — index rows for records written before the index existed. A
        # derived write, idempotent (INSERT OR IGNORE), declared on the
        # coverage block; the sealed record is not touched.
        self.recall_index_backfilled = self._index_backfill()
        # W8 — salts and a commitment root for every record that has none
        # (written before W8). A derived write; the records are not touched.
        self.commitments_backfilled = self._commitments_backfill()

    def _migrate_scope(self) -> None:
        """AD — additive column on an existing store, F.2's `archived` shape:
        old rows read 'default', nothing stored is altered. The moment scope
        BECAME recordable is persisted once (INSERT OR IGNORE), because a
        'default' before that instant means "could not be stated" and after
        it means "the request stated none" — two facts one value cannot
        carry; `scope_recordable_since` is what makes them decidable, the
        records_begin_at answer borrowed verbatim."""
        cols = {c.name for c in self.conn.columns("lineage_records")}
        if "scope" not in cols:
            self.conn.execute("ALTER TABLE lineage_records ADD COLUMN "
                              "scope TEXT NOT NULL DEFAULT 'default'")
        if "citation_test" not in cols:
            # AU — additive, nullable, unhashed: old rows read NULL, meaning
            # "flagged by the F.5 test before the instrument was versioned".
            self.conn.execute("ALTER TABLE lineage_records ADD COLUMN "
                              "citation_test TEXT")
        if "request_hash" not in cols:
            # BA fix 2 — additive, nullable; hashed into new records only.
            self.conn.execute("ALTER TABLE lineage_records ADD COLUMN "
                              "request_hash TEXT")
        if "context_json" not in cols:
            # AW — additive, nullable, unhashed: old rows read NULL, meaning
            # "the block that went to the model was not recorded".
            self.conn.execute("ALTER TABLE lineage_records ADD COLUMN "
                              "context_json TEXT")
        for col in ("work_unit_id", "accountable_identity",
                    "boundary_asserted", "boundary_asserted_by", "attribution_flags",
                    "learning_mode", "answer_hash", "reasoning_hash", "answer_empty_reason",
                    "record_scope"):                       # MCP-2 — the same way
            if col not in cols:
                # v3.1 Phase 1 / 1b — additive, nullable; the hashed ones enter
                # new records only when stated. Old rows read NULL:
                # unattributed by absence. LS — `learning_mode` the same way:
                # NULL on every earlier record, never backfilled. C-38 — the
                # two hashes and the empty-answer reason likewise.
                self.conn.execute(f"ALTER TABLE lineage_records ADD COLUMN {col} TEXT")
        for col in ("answer_chars", "reasoning_chars"):
            if col not in cols:
                # C-38 — additive, nullable, unhashed lengths; NULL on every
                # earlier record.
                self.conn.execute(f"ALTER TABLE lineage_records ADD COLUMN {col} INTEGER")
        for col, kind in (("inference_seed", "INTEGER"), ("model_digest", "TEXT"),
                          ("inference_config_hash", "TEXT"), ("environment_hash", "TEXT"),
                          ("capture_notes", "TEXT")):
            if col not in cols:
                # W0b — additive, nullable; hashed into new records only when
                # present. NULL on every earlier record: it was written
                # before anything was captured, and says so by absence.
                self.conn.execute(f"ALTER TABLE lineage_records ADD COLUMN {col} {kind}")
        if "identity_mechanism" not in cols:
            # W4 — additive, nullable; hashed into new records only when not
            # the default. NULL on every earlier record: `asserted` by
            # absence, never backfilled. Added here, after W0b's loop, and not
            # in the TEXT loop above: that loop runs first, so on a store
            # from before W0b (the committed pre-run fixture) it would put
            # this column ahead of W0b's five, and the store's column order
            # would no longer be a fresh store's, which the store copy
            # refuses (scripts/_store_copy.py `copy_rows`; the migration to
            # Postgres copies that fixture).
            self.conn.execute("ALTER TABLE lineage_records ADD COLUMN identity_mechanism TEXT")
        # W6 — two columns, additive and nullable; hashed into new records
        # only when present. NULL on every earlier record, never backfilled.
        # After W4's column and in the DDL's order, for the reason given
        # above: the store copy compares column order. Two literal statements
        # and no loop, so each is a statement the dialect guard reads whole.
        if "envelope_version" not in cols:
            self.conn.execute("ALTER TABLE lineage_records ADD COLUMN envelope_version TEXT")
        if "forecast" not in cols:
            self.conn.execute("ALTER TABLE lineage_records ADD COLUMN forecast TEXT")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS scope_meta ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "recordable_since REAL NOT NULL)")
        self.conn.execute(
            "INSERT OR IGNORE INTO scope_meta (id, recordable_since) "
            "VALUES (1, ?)", (time.time(),))

    def scope_recordable_since(self) -> "float | None":
        row = self.conn.execute(
            "SELECT recordable_since FROM scope_meta").fetchone()
        return row["recordable_since"] if row else None

    def close(self) -> None:
        self.conn.close()

    # ---------- hashing (canonical, order-independent) ----------

    @staticmethod
    def compute_record_hash(payload: dict) -> str:
        """sha256 over the canonical JSON of the record's core fields.
        Key order in the input dict never matters (sort_keys)."""
        body = LineageStore.hashed_body(payload)
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def hashed_body(payload: dict) -> dict:
        """W8 — the body the record's hash is over, as a dict: ONE builder for
        the hash and for the field commitments (`backend/memory/commitments.py`),
        so the two cover the same fields, present and absent alike."""
        body = {
            "lineage_id":         payload["lineage_id"],
            "ts":                 payload["ts"],
            "query_hash":         payload["query_hash"],
            "injected":           payload["injected"],
            "contradictions":     payload["contradictions"],
            "model_id":           payload["model_id"],
            "prompt_prefix_hash": payload["prompt_prefix_hash"],
            "response_hash":      payload["response_hash"],
        }
        # AD — sealed into the hash ONLY when not the default, the exact
        # rule ArchiveBlock.compute_hash uses for `kind` (chain.py): every
        # record hashed before the field existed recomputes to its stored
        # hash unchanged, and a STATED scope becomes tamper-evident rather
        # than rewritable-under-an-intact-seal (the notes defect, OQ-5).
        if payload.get("scope", "default") != "default":
            body["scope"] = payload["scope"]
        # BA fix 2 — the same rule: sealed into the hash only when present,
        # so every record hashed before the field existed still recomputes.
        if payload.get("request_hash"):
            body["request_hash"] = payload["request_hash"]
        # v3.1 Phase 1 — the same rule again: a stated unit or identity is
        # tamper-evident; a record with neither recomputes as it always did.
        for key in ("work_unit_id", "accountable_identity"):
            if payload.get(key):
                body[key] = payload[key]
        # v3.1 Phase 1b — the boundary and its asserter, the same rule.
        # `boundary_asserted_by` is hashed as the canonical JSON string the
        # column holds, so the stored bytes are what the seal covers.
        if payload.get("boundary_asserted"):
            body["boundary_asserted"] = payload["boundary_asserted"]
        if payload.get("boundary_asserted_by"):
            by = payload["boundary_asserted_by"]
            body["boundary_asserted_by"] = (by if isinstance(by, str)
                                            else json.dumps(by, sort_keys=True))
        # LS — the same rule once more: the learning state is sealed only
        # when it is not the default (`live`, stored as NULL), so a live
        # record's hash is byte-identical to what the pre-LS code wrote.
        if payload.get("learning_mode") and payload["learning_mode"] != "live":
            body["learning_mode"] = payload["learning_mode"]
        # C-38 — the same rule: the answer's hash and the reasoning's hash are
        # sealed when present. A record written before the fields existed, or
        # a reply with no reasoning, recomputes exactly as before.
        for key in ("answer_hash", "reasoning_hash"):
            if payload.get(key):
                body[key] = payload[key]
        # MCP-2 — the same rule once more: the record's scope is sealed only
        # when it is not the console's full turn (stored as NULL), so every
        # pre-MCP record recomputes byte for byte and a scope written into a
        # sealed record's NULL column after the fact is named. `full_turn` is
        # the default spelled out (`to_dict` names it; the writer stores it as
        # NULL), so it normalises to absent here too: a reader recomputing a
        # console record's hash from its dict gets the stored hash.
        if payload.get("record_scope") not in (None, "", FULL_TURN):
            body["record_scope"] = payload["record_scope"]
        # W0b — the replay capture, the same rule once more: each field is
        # sealed only when present, so every record written before W0b (and
        # every one that captured nothing) recomputes unchanged, and a value
        # written into a sealed record's NULL column is named. The notes are
        # sealed as their canonical JSON text.
        for key in ("inference_seed", "model_digest", "inference_config_hash",
                    "environment_hash", "capture_notes"):
            value = payload.get(key)
            if value is None or value == "":
                continue
            if key == "capture_notes" and not isinstance(value, str):
                # A reader recomputing from `to_dict` holds the notes as a
                # dict; the seal covers the canonical text the column holds.
                if not value:
                    continue
                value = canonical_json(value)
            body[key] = value
        # W4 — the same rule once more: how `accountable_identity` was
        # established is sealed only when it is not the default (`asserted`,
        # stored as NULL), so every record written before the field existed,
        # and every asserted one, recomputes byte for byte, and a mechanism
        # written into a sealed record's NULL column after the fact is named.
        # `asserted` is the default spelled out (`to_dict` names it; the
        # writer stores it as NULL), so it normalises to absent here too.
        if payload.get("identity_mechanism") not in (None, "", IDENTITY_ASSERTED):
            body["identity_mechanism"] = payload["identity_mechanism"]
        # W6 — the two decision fields, by the same rule, and absent means
        # NULL and nothing else. The envelope in force is sealed whenever a
        # policy was installed (no policy is stored NULL), and the forecast's
        # score whenever a forecast ran, so every record written before W6,
        # and every one under the compiled default with no forecast,
        # recomputes byte for byte. No value is read as absent: not the
        # default's name (a policy can be installed under it), not an empty
        # name, not `{}` or `0`, so a value written into a sealed record's
        # NULL column is named, whatever it is.
        if payload.get("envelope_version") is not None:
            body["envelope_version"] = payload["envelope_version"]
        if payload.get("forecast") is not None:
            body["forecast"] = payload["forecast"]
        return body

    @staticmethod
    def seal_digest(record_hashes: list[str]) -> str:
        """Digest over a sorted set of record hashes — what KAIROS seals into
        the chain block alongside the count and the hash list itself."""
        return _sha256("\n".join(sorted(record_hashes)))

    # ---------- write path (chat/API) ----------

    def record_turn(
        self,
        *,
        query: str,
        injected: list[dict],
        contradictions: list[dict],
        model_id: str,
        response_text: str,
        prompt_prefix: str,
        scope: str = "default",
        citation_test: str | None = None,
        context: dict | None = None,
        request_hash: str | None = None,
        work_unit_id: str | None = None,
        accountable_identity: str | None = None,
        boundary_asserted: str | None = None,
        boundary_asserted_by: dict | None = None,
        attribution_flags: list[str] | tuple[str, ...] | None = None,
        learning_mode: str | None = None,
        answer_text: str | None = None,
        reasoning_text: str | None = None,
        answer_empty_reason: str | None = None,
        record_scope: str | None = None,
        inference_seed: int | None = None,
        model_digest: str | None = None,
        inference_config: dict | None = None,
        environment: dict | None = None,
        capture_notes: dict | None = None,
        request_body: bytes | None = None,
        request_body_encoding: str | None = None,
        identity_mechanism: str | None = None,
        envelope_version: str | None = None,
        forecast: dict | None = None,
        lineage_id: str | None = None,
        response_bytes: bytes | None = None,
        in_transaction=None,
    ) -> LineageRecord:
        by_json = (json.dumps(boundary_asserted_by, sort_keys=True)
                   if boundary_asserted_by else None)
        flags = tuple(attribution_flags or ())
        # LS — the mode the caller read at write time; `live` (the default)
        # is stored as NULL and stays out of the hash.
        mode = (learning_mode or "").strip() or None
        mode = None if mode == "live" else mode
        # MCP-2 — the record's scope, the same rule: `full_turn` (the
        # console's default) is stored as NULL and stays out of the hash;
        # `mcp_block` is stored and sealed. Any other value is refused here
        # rather than recorded as a scope nothing can read.
        rs = (record_scope or "").strip() or None
        rs = None if rs == FULL_TURN else rs
        if rs is not None and rs not in RECORD_SCOPES:
            raise ValueError(f"unknown record_scope {record_scope!r}: {' | '.join(RECORD_SCOPES)}")
        # W4 — how `accountable_identity` was established, the same rule:
        # `asserted` (the default) is stored as NULL and stays out of the
        # hash; `proxy` is stored and sealed. A value outside the closed
        # vocabulary is refused here, before anything is written, rather than
        # recorded as a mechanism nothing can read.
        im = identity_mechanism.strip() if isinstance(identity_mechanism, str) else identity_mechanism
        im = None if im in (None, "", IDENTITY_ASSERTED) else im
        if im is not None and im not in IDENTITY_MECHANISMS:
            raise ValueError(f"unknown identity_mechanism {identity_mechanism!r}: "
                             f"{' | '.join(IDENTITY_MECHANISMS)}")
        # W6 — no installed policy is stored as NULL (so it stays out of the
        # hash), and an installed one exactly as it is named: a policy's
        # version is a name, and a name is not trimmed (` v1` and `v1` are two
        # policies). The forecast is reduced to plain JSON values once, here,
        # so what is hashed is exactly what a reader of the stored text
        # rebuilds.
        ev = envelope_version
        fc = json.loads(json.dumps(forecast, sort_keys=True)) if forecast is not None else None
        # C-38 — the reply separated. `response_text` is the content stream
        # as delivered (response_hash, continuity); `answer_text` is the
        # stated answer and `reasoning_text` the reasoning, each hashed and
        # counted apart; the texts are not stored. A caller that passes no
        # answer_text (a record written outside the turn) leaves the fields
        # NULL, exactly as a pre-C-38 record reads.
        answer_hash = _sha256(answer_text) if answer_text is not None else None
        reasoning_hash = _sha256(reasoning_text) if reasoning_text else None
        answer_chars = len(answer_text) if answer_text is not None else None
        reasoning_chars = len(reasoning_text) if reasoning_text else None
        # W0b — the replay capture. The options and the environment are kept
        # once each, keyed by the sha256 of their canonical JSON; the record
        # carries the keys. The request bytes are kept only if they hash to
        # `request_hash` — the hash the record already seals — so the kept
        # copy needs no trust of its own; otherwise they are refused and the
        # reason is noted. A caller that passes none of this (every record
        # written outside a turn, every record before W0b) writes NULLs and
        # its hash is exactly what it was.
        notes = dict(capture_notes or {})
        config_json = _capture_json(inference_config, "inference_config_hash", notes)
        env_json = _capture_json(environment, "environment_hash", notes)
        config_hash = _sha256(config_json) if config_json is not None else None
        env_hash = _sha256(env_json) if env_json is not None else None
        digest = model_digest or None
        if digest is not None and not (isinstance(digest, str) and digest.isascii() and digest.isprintable()):
            notes["model_digest"] = (f"not recorded: the provider's digest is not printable ASCII "
                                     f"({type(digest).__name__})")
            digest = None
        encoding = request_body_encoding or REQUEST_ENCODING_MESSAGES
        if encoding not in REQUEST_ENCODINGS:
            raise ValueError(f"unknown request body encoding {encoding!r}: {REQUEST_ENCODINGS}")
        kept_body = None
        if request_body is not None:
            kept_body, why_not = self._kept_body(request_body, request_hash, encoding)
            if why_not:
                notes["request_body"] = why_not
        # The reasons are text whatever a provider handed back, so the notes
        # always serialise (ASCII canonical JSON) and never cost the record.
        notes_json = (canonical_json(_scrub({str(k): str(v) for k, v in notes.items()}, []))
                      if notes else None)
        seed = int(inference_seed) if inference_seed is not None else None
        payload = {
            "scope":              (scope or "default").strip() or "default",
            "lineage_id":         lineage_id or str(uuid.uuid4()),   # W10b (D4): a turn may mint it first
            "ts":                 time.time(),
            "query_hash":         _sha256(query),
            "injected":           injected,
            "contradictions":     contradictions,
            "model_id":           model_id,
            "prompt_prefix_hash": _sha256(prompt_prefix),
            # W10c — an interceptor's response is bytes (a provider's stream, compressed or not), hashed as
            # they are; every other caller gives the text, hashed as its UTF-8, exactly as before.
            "response_hash":      (hashlib.sha256(response_bytes).hexdigest() if response_bytes is not None
                                   else _sha256(response_text)),
            "request_hash":       request_hash,
            "work_unit_id":       work_unit_id or None,
            "accountable_identity": accountable_identity or None,
            "boundary_asserted":  boundary_asserted or None,
            "boundary_asserted_by": by_json,
            "learning_mode":      mode,
            "answer_hash":        answer_hash,
            "reasoning_hash":     reasoning_hash,
            "record_scope":       rs,
            "inference_seed":     seed,
            "model_digest":       digest,
            "inference_config_hash": config_hash,
            "environment_hash":   env_hash,
            "capture_notes":      notes_json,
            "identity_mechanism": im,
            "envelope_version":   ev,
            "forecast":           fc,
        }
        record_hash = self.compute_record_hash(payload)
        # W0b part 5 — the record, its companions and its index rows commit
        # together or not at all. A write that fails is rolled back, so the
        # connection is never left inside an open transaction (which made
        # every later record on it fail with "database is locked"). Written
        # here, in record_turn itself, so the one writer stays the one writer.
        try:
            # W0b — the capture's companions, in the record's own transaction.
            if config_json is not None:
                self.conn.execute(
                    "INSERT OR IGNORE INTO inference_configs (config_hash, config_json, first_seen_ts) "
                    "VALUES (?, ?, ?)", (config_hash, config_json, payload["ts"]))
            if env_json is not None:
                self.conn.execute(
                    "INSERT OR IGNORE INTO environments (environment_hash, descriptor_json, first_seen_ts) "
                    "VALUES (?, ?, ?)", (env_hash, env_json, payload["ts"]))
            if kept_body is not None:
                self._insert_request_body(kept_body, payload["ts"])
            self.conn.execute(
                "INSERT INTO lineage_records "
                "(lineage_id, ts, query_hash, injected_json, contradictions_json, "
                " model_id, prompt_prefix_hash, response_hash, record_hash, "
                " sealed_block, scope, citation_test, context_json, request_hash, "
                " work_unit_id, accountable_identity, boundary_asserted, "
                " boundary_asserted_by, attribution_flags, learning_mode, "
                " answer_hash, reasoning_hash, answer_chars, reasoning_chars, answer_empty_reason, "
                " record_scope, inference_seed, model_digest, inference_config_hash, "
                " environment_hash, capture_notes, identity_mechanism, "
                " envelope_version, forecast) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?)",
                (payload["lineage_id"], payload["ts"], payload["query_hash"],
                 json.dumps(payload["injected"], sort_keys=True),
                 json.dumps(payload["contradictions"], sort_keys=True),
                 payload["model_id"], payload["prompt_prefix_hash"],
                 payload["response_hash"], record_hash, payload["scope"],
                 citation_test,
                 json.dumps(context, sort_keys=True) if context is not None else None,
                 request_hash, payload["work_unit_id"], payload["accountable_identity"],
                 payload["boundary_asserted"], payload["boundary_asserted_by"],
                 json.dumps(list(flags)) if flags else None,
                 payload["learning_mode"],
                 answer_hash, reasoning_hash, answer_chars, reasoning_chars,
                 (answer_empty_reason or None), rs,
                 seed, payload["model_digest"], config_hash, env_hash, notes_json,
                 payload["identity_mechanism"],
                 ev, (json.dumps(fc, sort_keys=True, separators=(",", ":")) if fc is not None else None)),
            )
            # AR — the backward index, written in the same transaction as the
            # record it derives from, so the two cannot disagree by a crash
            # between them.
            self.conn.executemany(
                f"INSERT OR IGNORE INTO {RECALL_INDEX_TABLE} (belief_id, lineage_id) VALUES (?, ?)",
                [(int(item["belief_id"]), payload["lineage_id"]) for item in injected
                 if isinstance(item, dict) and item.get("belief_id") is not None])
            # W8 — the record's salted field commitments, in its transaction. A
            # value the canonical encoding refuses (a NaN) leaves the record
            # without them, as the open-time backfill does: it is recorded and
            # seals bare (W8's third delta check: the record was lost, and the
            # fail-closed recorder withheld the answer).
            try:
                self._write_commitments(payload["lineage_id"], payload, payload["ts"])
            except ValueError as e:
                logger.warning("lineage record %s: its values cannot be committed (%s); it is recorded without "
                               "commitments and seals as its bare hash", payload["lineage_id"], e)
            # W10c — what must commit with this record or not at all (an intercepted exchange's tool-use rows
            # and its outcome): run here, inside the record's transaction; whatever it raises rolls all of it back.
            if in_transaction is not None:
                in_transaction()
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return self._record_from(payload, record_hash, citation_test, context, request_hash, by_json,
                                 flags, answer_hash, reasoning_hash, answer_chars, reasoning_chars,
                                 answer_empty_reason, rs, seed, config_hash, env_hash, notes_json)

    @staticmethod
    def _kept_body(request_body: bytes, request_hash: "str | None", encoding: str) -> "tuple[tuple | None, str | None]":
        """(what to keep, None) or (None, why not). The one place the keep switch is read (at write time, like
        LS's mode), so every writer of `request_bodies` is covered: the bytes are kept only if they hash to
        `request_hash`, the hash a record seals, so the kept copy needs no trust of its own."""
        from backend.memory.capture_settings import KEEP_SETTING, keep_requests_enabled
        if not keep_requests_enabled():
            return None, f"not kept: {KEEP_SETTING}=off"
        if request_hash is None:
            return None, "not kept: the record has no request_hash"
        if hashlib.sha256(request_body).hexdigest() != request_hash:
            logger.error("request bytes refused: sha256 differs from request_hash %s", request_hash)
            return None, "not kept: the bytes did not hash to request_hash"
        return (request_hash, encoding, zlib.compress(request_body, 9), len(request_body)), None

    def _insert_request_body(self, kept_body: tuple, ts: float) -> None:
        """The one statement that writes `request_bodies`; its caller commits."""
        self.conn.execute(
            "INSERT OR IGNORE INTO request_bodies (request_hash, encoding, body, raw_bytes, first_seen_ts) "
            "VALUES (?, ?, ?, ?, ?)", (*kept_body, ts))

    @staticmethod
    def _record_from(payload, record_hash, citation_test, context, request_hash, by_json, flags,
                     answer_hash, reasoning_hash, answer_chars, reasoning_chars, answer_empty_reason,
                     rs, seed, config_hash, env_hash, notes_json) -> "LineageRecord":
        return LineageRecord(
            lineage_id=payload["lineage_id"], ts=payload["ts"],
            query_hash=payload["query_hash"],
            injected=tuple(payload["injected"]),
            contradictions=tuple(payload["contradictions"]),
            model_id=payload["model_id"],
            prompt_prefix_hash=payload["prompt_prefix_hash"],
            response_hash=payload["response_hash"],
            record_hash=record_hash, sealed_block=None,
            scope=payload["scope"], citation_test=citation_test,
            context=context, request_hash=request_hash,
            work_unit_id=payload["work_unit_id"],
            accountable_identity=payload["accountable_identity"],
            boundary_asserted=payload["boundary_asserted"],
            boundary_asserted_by=(json.loads(by_json) if by_json else None),
            attribution_flags=flags,
            learning_mode=payload["learning_mode"],
            answer_hash=answer_hash, reasoning_hash=reasoning_hash,
            answer_chars=answer_chars, reasoning_chars=reasoning_chars,
            answer_empty_reason=(answer_empty_reason or None),
            record_scope=rs,
            inference_seed=seed, model_digest=payload["model_digest"],
            inference_config_hash=config_hash, environment_hash=env_hash,
            capture_notes=(json.loads(notes_json) if notes_json else None),
            identity_mechanism=payload["identity_mechanism"],
            envelope_version=payload["envelope_version"], forecast=payload["forecast"],
        )

    # ---------- W0b: the replay capture's read side ----------

    def request_body(self, request_hash: str) -> bytes | None:
        """The kept request bytes for a record's `request_hash`, or None."""
        row = self.conn.execute("SELECT body FROM request_bodies WHERE request_hash = ?",
                                (request_hash,)).fetchone()
        return zlib.decompress(row["body"]) if row else None

    def inference_config(self, config_hash: str) -> dict | None:
        row = self.conn.execute("SELECT config_json FROM inference_configs WHERE config_hash = ?",
                                (config_hash,)).fetchone()
        return json.loads(row["config_json"]) if row else None

    def environment(self, environment_hash: str) -> dict | None:
        row = self.conn.execute("SELECT descriptor_json FROM environments WHERE environment_hash = ?",
                                (environment_hash,)).fetchone()
        return json.loads(row["descriptor_json"]) if row else None

    def replay_capture_view(self, rec: "LineageRecord") -> dict:
        """W0b — one record's capture, resolved for a reader: the seed, the
        digest, the options and the environment its hashes key, the reason
        for each that is absent, and whether its exact request is kept (the
        size and encoding, never the text). A record written before W0b says
        so rather than reading as four blanks."""
        kept = None
        if rec.request_hash:
            kept = self.conn.execute(
                "SELECT encoding, raw_bytes, LENGTH(body) AS stored FROM request_bodies "
                "WHERE request_hash = ?", (rec.request_hash,)).fetchone()
        captured = any(getattr(rec, f) is not None for f in (
            "inference_seed", "model_digest", "inference_config_hash", "environment_hash",
            "capture_notes"))
        return {
            "captured": captured,
            "note": (None if captured else
                     "written before the replay capture existed (W0b): nothing a re-run needs was recorded"),
            "inference_seed": rec.inference_seed,
            "model_digest": rec.model_digest,
            "inference_config": (self.inference_config(rec.inference_config_hash)
                                 if rec.inference_config_hash else None),
            "environment": (self.environment(rec.environment_hash)
                            if rec.environment_hash else None),
            "absent": dict(rec.capture_notes or {}),
            "request_kept": kept is not None,
            "request_encoding": kept["encoding"] if kept else None,
            "request_bytes_raw": kept["raw_bytes"] if kept else None,
            "request_bytes_stored": kept["stored"] if kept else None,
        }

    def capture_stats(self) -> dict:
        """Counts derived on every read (never stored numbers): how many
        records carry each capture field, how many request bodies are kept and
        their sizes, and the switches in force."""
        from backend.memory.capture_settings import keep_requests_enabled, seed_enabled
        c = self.conn
        n = c.execute("SELECT COUNT(*) FROM lineage_records").fetchone()[0]
        with_seed = c.execute("SELECT COUNT(*) FROM lineage_records WHERE inference_seed IS NOT NULL").fetchone()[0]
        with_digest = c.execute("SELECT COUNT(*) FROM lineage_records WHERE model_digest IS NOT NULL").fetchone()[0]
        with_config = c.execute("SELECT COUNT(*) FROM lineage_records WHERE inference_config_hash IS NOT NULL").fetchone()[0]
        with_kept = c.execute("SELECT COUNT(*) FROM lineage_records r JOIN request_bodies b "
                              "ON b.request_hash = r.request_hash").fetchone()[0]
        # W3b — the sums cast: SQLite's SUM over an integer column is an
        # integer and Postgres's is numeric (a Decimal), and the dialect
        # refuses a SUM that does not say which it means. The same integers
        # on SQLite.
        bodies, raw, stored = c.execute(
            "SELECT COUNT(*), CAST(COALESCE(SUM(raw_bytes), 0) AS INTEGER), "
            "CAST(COALESCE(SUM(LENGTH(body)), 0) AS INTEGER) FROM request_bodies").fetchone()
        return {"keep_requests": "on" if keep_requests_enabled() else "off",
                "inference_seed": "on" if seed_enabled() else "off",
                "records": n, "with_seed": with_seed, "with_digest": with_digest,
                "with_config": with_config, "with_request_kept": with_kept,
                "request_bodies": bodies, "request_bytes_raw": raw, "request_bytes_stored": stored,
                "configs": c.execute("SELECT COUNT(*) FROM inference_configs").fetchone()[0],
                "environments": c.execute("SELECT COUNT(*) FROM environments").fetchone()[0],
                "note": ("NEXUS keeps the exact text it sent to the model so a decision can be re-run; "
                         "nothing here expires on its own")}

    def verify_request_bodies(self, *, lineage_id: str | None = None) -> dict:
        """Every kept body decompresses and hashes to its key — the
        `request_hash` its record seals — and is stored in a declared
        encoding. Read-only. With `lineage_id`, only that record's body.

        A kept copy is verified BY the record, it is not part of it: a body
        that fails here is a corrupted copy, and the record beside it may be
        intact. So this is reported beside the record's three layers, never
        folded into their verdict. `records_with_body` / `records_without_body`
        count the records (a record whose request was not kept says why in its
        `capture_notes`); `orphans` counts bodies no record names."""
        if lineage_id is not None:
            row = self.conn.execute(
                "SELECT * FROM lineage_records WHERE lineage_id = ?",
                (lineage_id,)).fetchone()
            keys = [row["request_hash"]] if row and row["request_hash"] else []
            rows = [self.conn.execute(
                "SELECT request_hash, encoding, body, raw_bytes FROM request_bodies "
                "WHERE request_hash = ?", (k,)).fetchone() for k in keys]
            rows = [r for r in rows if r is not None]
            if not rows:
                # Nothing kept is not "intact": say which absence it is (law 5).
                keys = row.keys() if row is not None else ()
                raw_notes = row["capture_notes"] if "capture_notes" in keys else None
                notes = json.loads(raw_notes) if raw_notes else {}
                # Captured is a fact of the record's own columns, never of its
                # notes: a fully captured record has no notes at all.
                captured = row is not None and any(
                    row[c] is not None for c in ("inference_seed", "model_digest", "inference_config_hash",
                                                 "environment_hash", "capture_notes") if c in keys)
                reason = (notes.get("request_body") if notes.get("request_body") else
                          "written before the replay capture (W0b)" if not captured else
                          "the kept copy is missing, and the record noted no reason "
                          "(removed after the record was written)")
                return {"checked": 0, "failed": 0, "failures": [], "verdict": "none kept", "reason": reason}
        else:
            rows = self.conn.execute(
                "SELECT request_hash, encoding, body, raw_bytes FROM request_bodies "
                "ORDER BY request_hash").fetchall()
        checked, failures = 0, []
        for row in rows:
            checked += 1
            try:
                raw = zlib.decompress(row["body"])
                ok = hashlib.sha256(raw).hexdigest() == row["request_hash"]
                why = "" if ok else "bytes do not hash to request_hash"
                if ok and len(raw) != row["raw_bytes"]:
                    ok, why = False, "raw_bytes differs from the decompressed length"
                if ok and row["encoding"] not in REQUEST_ENCODINGS:
                    ok, why = False, f"undeclared encoding {row['encoding']!r}"
            except zlib.error:
                ok, why = False, "body does not decompress"
            except TypeError:
                ok, why = False, "the body is not bytes"
            if not ok:
                failures.append({"request_hash": row["request_hash"], "reason": why,
                                 "lineage_ids": [r["lineage_id"] for r in self.conn.execute(
                                     "SELECT lineage_id FROM lineage_records WHERE request_hash = ? "
                                     "ORDER BY ts", (row["request_hash"],))]})
        out = {"checked": checked, "failed": len(failures), "failures": failures,
               "verdict": ("copy_corrupted" if failures else "intact" if checked else "none kept")}
        if lineage_id is None:
            c = self.conn
            out["records_with_body"] = c.execute(
                "SELECT COUNT(*) FROM lineage_records r WHERE EXISTS "
                "(SELECT 1 FROM request_bodies b WHERE b.request_hash = r.request_hash)").fetchone()[0]
            out["records_without_body"] = c.execute(
                "SELECT COUNT(*) FROM lineage_records r WHERE NOT EXISTS "
                "(SELECT 1 FROM request_bodies b WHERE b.request_hash = r.request_hash)").fetchone()[0]
            out["orphans"] = c.execute(
                "SELECT COUNT(*) FROM request_bodies b WHERE NOT EXISTS "
                "(SELECT 1 FROM lineage_records r WHERE r.request_hash = b.request_hash)").fetchone()[0]
        return out

    def verify_capture_rows(self, *, lineage_id: str | None = None) -> dict:
        """W0b — every kept options object and environment descriptor still
        hashes to the key the records seal, and every key a record seals has
        its row. Read-only. A row edited after the fact would otherwise be
        shown on /lineage/{id} and re-sent by the replay check as though it
        were what the turn sent. With `lineage_id`, only that record's keys."""
        specs = (("inference_configs", "config_hash", "config_json", "inference_config_hash"),
                 ("environments", "environment_hash", "descriptor_json", "environment_hash"))
        where = "WHERE lineage_id = ?" if lineage_id is not None else ""
        params = (lineage_id,) if lineage_id is not None else ()
        checked, failures = 0, []
        for table, key, text, col in specs:
            keys = sorted({r[0] for r in self.conn.execute(
                f"SELECT {col} FROM lineage_records {where}", params) if r[0]})
            for k in keys:
                checked += 1
                row = self.conn.execute(f"SELECT {text} AS t FROM {table} WHERE {key} = ?", (k,)).fetchone()
                why = ("the record's key has no row" if row is None else
                       "the row is not text" if not isinstance(row["t"], str) else
                       "" if _sha256(row["t"]) == k else "the row does not hash to its key")
                if why:
                    lids = [r[0] for r in self.conn.execute(
                        f"SELECT lineage_id FROM lineage_records WHERE {col} = ? ORDER BY ts", (k,))]
                    failures.append({"table": table, "key": k, "lineage_ids": lids, "reason": why})
        return {"checked": checked, "failed": len(failures), "failures": failures,
                "verdict": ("row_corrupted" if failures else "intact" if checked else "none kept")}

    # ---------- read path ----------

    def get(self, lineage_id: str) -> LineageRecord | None:
        row = self.conn.execute(
            "SELECT * FROM lineage_records WHERE lineage_id = ?", (lineage_id,),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def recent(self, limit: int = 20) -> list[LineageRecord]:
        rows = self.conn.execute(
            "SELECT * FROM lineage_records ORDER BY ts DESC LIMIT ?", (limit,),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) AS n FROM lineage_records").fetchone()["n"])

    # ---------- AR: the backward read (claim -> records) ----------
    #
    # The recall reads the INJECTED set, never the cited flag: the flag
    # under-reports by about a fifth (measured), and a recall that misses a
    # fifth of the affected answers is wrong in the unsafe direction.
    # Nothing here writes to a record; the index is the one derived table,
    # and `verify_recall_index` is what keeps it honest.

    RECALL_ROW_KEYS = ("lineage_id", "ts", "work_unit_id", "accountable", "identity_label",
                       "scope", "weight_at_injection", "cited", "tier_at_injection",
                       "response_hash", "sealed_block")

    def _write_commitments(self, lineage_id: str, payload: dict, now: float) -> None:
        """No commit: the caller's transaction holds it."""
        made = _commitments.commit(LineageStore.hashed_body(payload))
        self.conn.executemany("INSERT INTO record_salts (lineage_id, field, salt) VALUES (?, ?, ?)",
                              [(lineage_id, f, made.salts[f].hex()) for f in sorted(made.salts)])
        self.conn.execute("INSERT INTO record_commitments (lineage_id, commitment_root, made_at) VALUES (?, ?, ?)",
                          (lineage_id, made.root.hex(), now))

    def _commitments_backfill(self) -> int:
        """Salts and a root for each record without them. Returns how many.

        Only a record whose fields still hash to its stored `record_hash` is
        committed (W8's verifier, MF2): committing a rewritten record would
        let a later seal bind, and a tombstone dispose of, values the sealed
        hash never covered. A record that does not verify, or whose values
        cannot be committed (a NaN the canonical encoding refuses), is left
        without a root, counted in `commitments_skipped`, and the row layer
        keeps naming what it names."""
        done = {r["lineage_id"] for r in self.conn.execute("SELECT lineage_id FROM record_commitments")}
        missing = [r for r in self.conn.execute("SELECT * FROM lineage_records") if r["lineage_id"] not in done]
        self.commitments_skipped = 0
        if not missing:
            return 0
        now, made = time.time(), 0
        try:
            for r in missing:
                payload = _hash_payload_from_row(r)
                if LineageStore.compute_record_hash(payload) != r["record_hash"]:
                    self.commitments_skipped += 1
                    continue
                try:
                    _commitments.commit(LineageStore.hashed_body(payload))
                except ValueError:
                    self.commitments_skipped += 1
                    continue
                self.conn.execute("DELETE FROM record_salts WHERE lineage_id = ?", (r["lineage_id"],))
                self._write_commitments(r["lineage_id"], payload, now)
                made += 1
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return made

    def salts(self, lineage_id: str) -> dict:
        """field -> salt (bytes) for one record; {} when it has none."""
        return {r["field"]: bytes.fromhex(r["salt"]) for r in self.conn.execute(
            "SELECT field, salt FROM record_salts WHERE lineage_id = ?", (lineage_id,))}

    def commitment_root(self, lineage_id: str) -> str | None:
        row = self.conn.execute("SELECT commitment_root FROM record_commitments WHERE lineage_id = ?",
                                (lineage_id,)).fetchone()
        return row["commitment_root"] if row else None

    def tombstoned(self) -> dict[str, list[str]]:
        """W8 (D9) — lineage_id -> the kept fields' names, for every tombstoned record."""
        return {r["lineage_id"]: json.loads(r["kept_fields"]) for r in self.conn.execute(
            "SELECT lineage_id, kept_fields FROM record_tombstones")}

    def tombstone(self, lineage_id: str, *, chain, actor: dict, reason: str, now: float | None = None) -> dict:
        """W8 (D9) — dispose of a record's content, keeping what lets it verify.

        Kept: the record's `lineage_id`, `ts`, `learning_mode`, injected set
        (with its cited flags), `record_hash`, commitment root, every field's
        commitment and the kept fields' salts. Dropped: every other hashed
        field (a NOT NULL column holds TOMBSTONE_MARK, never a plausible value;
        a nullable one NULL), its salt, the unhashed `context_json`, and the
        kept request body when no other live record shares it. The act is a
        `record_tombstones` row, sealed with the governance tables. Refused by
        name, writing nothing: no record, already tombstoned, a record that
        does not verify (its fields, its seal's root, its binding or its
        salts), no commitments, no reason, not yet sealed, its block missing,
        or a seal that claims no root and no binding yet (its kept openings
        would then bind to no sealed root). No route reaches this: when a
        record may be disposed of, and who confirms it, is W14's."""
        from backend.memory.record_schema import record_tombstone_body, record_tombstone_hash
        row = self.conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (lineage_id,)).fetchone()
        if row is None:
            raise TombstoneRefused("no_record: no lineage record has this id")
        if lineage_id in self.tombstoned():
            raise TombstoneRefused("already_tombstoned: this record's content was disposed of already")
        if LineageStore.compute_record_hash(_hash_payload_from_row(row)) != row["record_hash"]:
            raise TombstoneRefused("does_not_verify: the record's fields do not hash to its sealed hash; "
                                   "a tombstone would hide that")
        if not isinstance(reason, str) or not reason.strip():
            raise TombstoneRefused("no_reason: a tombstone records why")
        if row["sealed_block"] is None:
            raise TombstoneRefused("unsealed: seal the record first, so its leaf is in a block's root")
        block = chain.get_block(int(row["sealed_block"]))
        if block is None:
            raise TombstoneRefused("block_missing: the block that sealed the record is not in the chain")
        root = self.commitment_root(lineage_id)
        state = _seal_state(block, row["record_hash"], root)
        if state == "no_root":
            raise TombstoneRefused("no_commitments: the record has no commitment root (its values could not be "
                                   "committed); nothing could verify what a tombstone keeps")
        if state == "contradicted":
            raise TombstoneRefused("does_not_verify: the record's commitment root is not the one its seal covered")
        if state == "absent":
            raise TombstoneRefused("does_not_verify: the block that sealed the record does not hold its hash")
        if state == "bare":
            bound = self.bound_block(lineage_id)
            bblock = chain.get_block(bound) if bound is not None else None
            leaves = (((bblock.fingerprint or {}).get("binding_seal") or {}).get("leaves") or []) if bblock else []
            if bound is None:
                raise TombstoneRefused("unbound: no seal covers the record's commitment root yet; the next seal "
                                       "binds it")
            if row["record_hash"] + root not in leaves:
                raise TombstoneRefused("does_not_verify: the record's binding does not hold its commitment root")
        body = LineageStore.hashed_body(_hash_payload_from_row(row))
        try:
            made = _commitments.commit(body, salts=self.salts(lineage_id))
        except ValueError:
            raise TombstoneRefused("commitments_mismatch: a stored salt is missing or not 32 bytes") from None
        if made.root.hex() != self.commitment_root(lineage_id):
            raise TombstoneRefused("commitments_mismatch: the stored salts do not rebuild the record's root")
        kept = sorted(f for f in body if f in TOMBSTONE_KEPT_FIELDS)
        dropped = sorted(f for f in body if f not in TOMBSTONE_KEPT_FIELDS)
        unknown = [f for f in dropped if f not in _TOMBSTONE_DROP]
        if unknown:
            raise TombstoneRefused(f"unknown_field: no statement drops {unknown}; nothing was written")
        who = actor or {}
        event_id, ts = str(uuid.uuid4()), (time.time() if now is None else now)
        act = record_tombstone_body(event_id, ts, lineage_id, who.get("id"), who.get("role"), who.get("source"),
                                    reason, kept)
        try:
            self.conn.executemany(
                "INSERT INTO record_field_commitments (lineage_id, field, commitment) VALUES (?, ?, ?)",
                [(lineage_id, f, made.commitments[f].hex()) for f in sorted(made.commitments)])
            for f in dropped:
                self.conn.execute("DELETE FROM record_salts WHERE lineage_id = ? AND field = ?", (lineage_id, f))
                statement, value = _TOMBSTONE_DROP[f]
                self.conn.execute(statement, (value, lineage_id))
            self.conn.execute("UPDATE lineage_records SET context_json = NULL WHERE lineage_id = ?", (lineage_id,))
            if row["request_hash"] is not None:
                shared = self.conn.execute(
                    "SELECT COUNT(*) AS n FROM lineage_records WHERE request_hash = ? AND lineage_id <> ?",
                    (row["request_hash"], lineage_id)).fetchone()["n"]
                if not shared:
                    self.conn.execute("DELETE FROM request_bodies WHERE request_hash = ?", (row["request_hash"],))
            self.conn.execute(
                "INSERT INTO record_tombstones (event_id, ts, lineage_id, actor_id, actor_role, actor_source, "
                "reason, kept_fields, record_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, ts, lineage_id, act["actor_id"], act["actor_role"], act["actor_source"], reason,
                 json.dumps(kept, separators=(",", ":")), record_tombstone_hash(act)))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return {"event_id": event_id, "lineage_id": lineage_id, "kept_fields": kept, "dropped_fields": dropped}

    def verify_tombstoned(self, row) -> bool:
        """W8 (D9) — a tombstoned record verifies by its kept openings and its
        root, never by recomputing its hash: each kept field's value, with its
        salt, opens its stored commitment, and the stored commitments rebuild
        the record's commitment root. (Its leaf in its block's root is the
        proof layer's.)"""
        lid = row["lineage_id"]
        stored = {r["field"]: bytes.fromhex(r["commitment"]) for r in self.conn.execute(
            "SELECT field, commitment FROM record_field_commitments WHERE lineage_id = ?", (lid,))}
        kept = self.tombstoned().get(lid) or []
        body = LineageStore.hashed_body(_hash_payload_from_row(row))
        salts = self.salts(lid)
        if set(salts) != set(kept) or not stored or not set(kept) <= set(stored):
            return False
        for f in kept:
            if f not in body or _commitments.open_field(f, body[f], salts[f], stored) is not None:
                return False
        # The dropped fields hold exactly what the tombstone leaves (a forged
        # act beside a rewritten field is named, not read as a disposal), and
        # no field reads a value that had no commitment when it was made.
        for f in set(stored) - set(kept):
            if f not in _TOMBSTONE_DROP or _hash_payload_from_row(row).get(f) != _tombstone_left(f):
                return False
        if set(body) - set(stored):
            return False
        return _commitments.commitment_root(stored).hex() == self.commitment_root(lid)

    def unbound(self, chain, *, limit: int) -> list[tuple[str, str, str, int]]:
        """W8 (D6) — records whose seal claims no root (sealed before W8, or a
        layout-2 seal of the bare hash) and whose commitment root no binding
        has sealed yet, never a tombstoned one, as `(lineage_id,
        record_hash, commitment_root, first_sealed)`, by record hash, at most
        `limit`. Ordered here, not in SQL (P11: a text order is a guarded
        construction)."""
        rows = self.conn.execute(
            "SELECT lr.lineage_id, lr.record_hash, lr.sealed_block, rc.commitment_root "
            "FROM lineage_records lr JOIN record_commitments rc ON rc.lineage_id = lr.lineage_id "
            "WHERE lr.sealed_block IS NOT NULL AND rc.bound_block IS NULL").fetchall()
        blocks: dict[int, object] = {}
        tombstoned = self.tombstoned()
        out: list[tuple[str, str, str, int]] = []
        for r in sorted(rows, key=lambda r: (r["record_hash"], r["lineage_id"])):
            index = int(r["sealed_block"])
            if index not in blocks:
                blocks[index] = chain.get_block(index)
            if r["lineage_id"] in tombstoned:
                continue                    # a tombstone's root was anchored when it was made; never bound again
            if _seal_state(blocks[index], r["record_hash"], r["commitment_root"]) == "bare":
                if not self.root_rebuilds(r["lineage_id"], record_hash=r["record_hash"], root=r["commitment_root"]):
                    continue                # never bind a root the record's values do not rebuild (MF-A)
                out.append((r["lineage_id"], r["record_hash"], r["commitment_root"], index))
                if len(out) >= limit:
                    break
        return out

    def root_rebuilds(self, lineage_id: str, *, record_hash: str | None = None, root: str | None = None) -> bool:
        """W8 (delta check 2, MF-A) — the record's fields hash to its record
        hash, and its stored salts rebuild its commitment root over those
        fields: the only root a binding may seal. Given `record_hash` and
        `root`, it checks exactly those, the values the caller will bind (the
        third delta check, MF-2: a root read once and checked as read again
        could be changed between the two). A salt that is not 32 bytes (MF-1),
        a record with no root, salts that do not name exactly its fields, or
        a row that cannot be parsed do not rebuild: this never raises, so one
        bad row stops neither a seal's bindings nor a verification."""
        try:
            row = self.conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (lineage_id,)).fetchone()
            if row is None:
                return False
            record_hash = row["record_hash"] if record_hash is None else record_hash
            root = self.commitment_root(lineage_id) if root is None else root
            if not root or row["record_hash"] != record_hash:
                return False
            payload = _hash_payload_from_row(row)
            if LineageStore.compute_record_hash(payload) != record_hash:
                return False
            salts, body = self.salts(lineage_id), LineageStore.hashed_body(payload)
            if set(salts) != set(body):
                return False
            return _commitments.commit(body, salts=salts).root.hex() == root
        except (ValueError, TypeError, KeyError, AttributeError):
            return False

    def mark_bound(self, lineage_ids: list[str], block_index: int) -> int:
        """W8 (D6) — the block whose binding seal bound these records. Once:
        a bound record is never bound again. Returns how many were marked."""
        n = 0
        try:
            for lid in lineage_ids:
                cur = self.conn.execute(
                    "UPDATE record_commitments SET bound_block = ? WHERE lineage_id = ? AND bound_block IS NULL",
                    (block_index, lid))
                n += cur.rowcount or 0
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return n

    def bound_block(self, lineage_id: str) -> "int | None":
        row = self.conn.execute("SELECT bound_block FROM record_commitments WHERE lineage_id = ?",
                                (lineage_id,)).fetchone()
        return None if row is None or row["bound_block"] is None else int(row["bound_block"])

    def seal_leaves(self, sealed: list) -> list[str]:
        """W8 (D5) — each record's leaf, `record_hash || commitment_root` (hex),
        in the seal's order (by record hash), for `(lineage_id, record_hash)`
        pairs. A record with no root (none can be after the open's backfill)
        is its hash alone (a bare-hash seal, bound later), and the seal still says which."""
        roots = {r["lineage_id"]: r["commitment_root"] for r in self.conn.execute(
            "SELECT lineage_id, commitment_root FROM record_commitments")}
        return [h + (roots.get(lid) or "") for lid, h in sorted(sealed, key=lambda p: p[1])]

    def _index_backfill(self) -> int:
        """Index rows for every record the index does not yet know. Returns
        the number of rows written (0 on a store already indexed)."""
        # W3b D4 — in Python, not SQL: `json_each`/`json_extract` have no
        # backend-neutral spelling. The records lacking index rows are few
        # (every record written since AR carries its rows), each parsed once;
        # the count is the statement's own `rowcount` (an ignored duplicate
        # counts 0), not a `total_changes` delta, which Postgres lacks.
        indexed = {r["lineage_id"] for r in self.conn.execute(
            f"SELECT DISTINCT lineage_id FROM {RECALL_INDEX_TABLE}")}
        pairs: list[tuple[int, str]] = []
        for r in self.conn.execute("SELECT lineage_id, injected_json FROM lineage_records"):
            if r["lineage_id"] in indexed:
                continue
            try:
                items = json.loads(r["injected_json"] or "[]")
            except (TypeError, ValueError):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("belief_id") is not None:
                    try:
                        pairs.append((int(item["belief_id"]), r["lineage_id"]))
                    except (TypeError, ValueError):
                        continue
        if not pairs:
            return 0
        cur = self.conn.executemany(
            f"INSERT OR IGNORE INTO {RECALL_INDEX_TABLE} (belief_id, lineage_id) VALUES (?, ?)", pairs)
        self.conn.commit()
        return max(0, int(cur.rowcount))

    def records_injecting(self, belief_id: int, *, limit: int = 100,
                          offset: int = 0) -> tuple[list[dict], int]:
        """Every record that INJECTED `belief_id`, oldest first, paginated.

        `accountable` is the identity recorded as answerable for that turn,
        and `identity_label` says how it was established: the record's own
        `identity_mechanism`, NULL read as `asserted` (recorded as stated,
        never authenticated), or `proxy` (taken from a loopback identity
        proxy's header; the proxy authenticated the person, NEXUS did not).
        Recall contract version 2 (W4); version 1 printed `asserted` on every
        row whatever the record said. `cited` rides beside it as
        information; it does not select.
        """
        bid = int(belief_id)
        total = int(self.conn.execute(
            f"SELECT COUNT(*) AS n FROM {RECALL_INDEX_TABLE} WHERE belief_id = ?",
            (bid,)).fetchone()["n"])
        # W3b D4 — the injected item is read from `injected_json` in Python
        # (a page of at most `limit` rows, each parsed once) where it was
        # `json_each`/`json_extract` in SQL, which has no backend-neutral
        # spelling. The page order is the index's join as before.
        rows = self.conn.execute(
            f"SELECT r.lineage_id, r.ts, r.work_unit_id, r.accountable_identity, r.identity_mechanism, "
            f"r.scope, r.response_hash, r.sealed_block, r.injected_json "
            f"FROM {RECALL_INDEX_TABLE} i "
            f"JOIN lineage_records r ON r.lineage_id = i.lineage_id "
            f"WHERE i.belief_id = ? "
            f"ORDER BY r.ts ASC, r.lineage_id ASC LIMIT ? OFFSET ?",
            (bid, max(1, int(limit)), max(0, int(offset)))).fetchall()
        out = []
        for r in rows:
            try:
                items = json.loads(r["injected_json"] or "[]")
            except (TypeError, ValueError):
                items = []
            item = next((it for it in items if isinstance(it, dict) and it.get("belief_id") == bid), {})
            cited = item.get("cited")
            out.append({
                "lineage_id":          r["lineage_id"],
                "ts":                  r["ts"],
                "work_unit_id":        r["work_unit_id"],
                "accountable":         r["accountable_identity"],
                # W4 (D12) — read from the record; a NULL is the default.
                "identity_label":      r["identity_mechanism"] or IDENTITY_ASSERTED,
                "scope":               r["scope"],
                "weight_at_injection": item.get("weight_at_injection"),
                "cited":               (None if cited is None else bool(cited)),
                "tier_at_injection":   item.get("tier_at_injection"),
                "response_hash":       r["response_hash"],
                "sealed_block":        r["sealed_block"],
            })
        return out, total

    def count_injecting(self, belief_id: int) -> int:
        return int(self.conn.execute(
            f"SELECT COUNT(*) AS n FROM {RECALL_INDEX_TABLE} WHERE belief_id = ?",
            (int(belief_id),)).fetchone()["n"])

    def records_resting_on(self, belief_ids) -> int:
        """Distinct records that injected at least one of `belief_ids`."""
        ids = sorted({int(b) for b in belief_ids})
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        return int(self.conn.execute(
            f"SELECT COUNT(DISTINCT lineage_id) AS n FROM {RECALL_INDEX_TABLE} "
            f"WHERE belief_id IN ({marks})", ids).fetchone()["n"])

    def recall_index_coverage(self) -> dict:
        n_records = self.count()
        indexed = int(self.conn.execute(
            f"SELECT COUNT(DISTINCT lineage_id) AS n FROM {RECALL_INDEX_TABLE}").fetchone()["n"])
        empty = int(self.conn.execute(
            "SELECT COUNT(*) AS n FROM lineage_records WHERE injected_json = '[]'").fetchone()["n"])
        return {"records": n_records, "indexed_records": indexed,
                "records_with_nothing_injected": empty,
                "backfilled_at_open": self.recall_index_backfilled}

    def verify_recall_index(self) -> dict:
        """The index against the record, both directions, by lineage id.

        `index_without_record`: an index row whose record did not inject
        that claim (a planted or stale row). `record_without_index`: a
        record that injected a claim the index does not know. Read-only.
        """
        index_without_record: list[dict] = []
        record_without_index: list[dict] = []
        pairs_from_records: set[tuple[int, str]] = set()
        for r in self.conn.execute("SELECT lineage_id, injected_json FROM lineage_records"):
            try:
                items = json.loads(r["injected_json"] or "[]")
            except (TypeError, ValueError):
                items = []
            for item in items:
                if isinstance(item, dict) and item.get("belief_id") is not None:
                    pairs_from_records.add((int(item["belief_id"]), r["lineage_id"]))
        pairs_from_index = {(int(r["belief_id"]), r["lineage_id"]) for r in self.conn.execute(
            f"SELECT belief_id, lineage_id FROM {RECALL_INDEX_TABLE}")}
        for bid, lid in sorted(pairs_from_index - pairs_from_records):
            index_without_record.append({"belief_id": bid, "lineage_id": lid})
        for bid, lid in sorted(pairs_from_records - pairs_from_index):
            record_without_index.append({"belief_id": bid, "lineage_id": lid})
        return {"consistent": not index_without_record and not record_without_index,
                "index_without_record": index_without_record,
                "record_without_index": record_without_index,
                "pairs": len(pairs_from_records)}

    # ---------- sealing (KAIROS only) ----------

    def unsealed(self) -> list[tuple[str, str]]:
        """(lineage_id, record_hash) for every record not yet chain-sealed."""
        rows = self.conn.execute(
            "SELECT lineage_id, record_hash FROM lineage_records "
            "WHERE sealed_block IS NULL ORDER BY ts ASC",
        ).fetchall()
        return [(r["lineage_id"], r["record_hash"]) for r in rows]

    # ---------- W9 (D8): commit now, reveal later ----------

    def hold_commitment(self, content: str, *, now: float | None = None) -> dict:
        """A salted commitment to `content`, kept here with its salt until the
        person reveals it; only the commitment is sealed (the next block's
        `commit` root). The content enters no prompt, retrieval or record."""
        salt = secrets.token_bytes(_commitments.SALT_BYTES)
        made = {"commitment_id": str(uuid.uuid4()), "ts": time.time() if now is None else now,
                "commitment": _commitments.commitment(salt, content).hex(), "salt": salt.hex()}
        self.conn.execute("INSERT INTO held_commitments (commitment_id, ts, commitment, salt, content) "
                          "VALUES (?, ?, ?, ?, ?)",
                          (made["commitment_id"], made["ts"], made["commitment"], made["salt"], content))
        self.conn.commit()
        return made

    def unsealed_commitments(self) -> list[tuple[str, str]]:
        """`(commitment_id, commitment)` for each held commitment no seal covers
        yet, by commitment (ordered here, not in SQL: P11)."""
        rows = self.conn.execute(
            "SELECT commitment_id, commitment FROM held_commitments WHERE sealed_block IS NULL").fetchall()
        return sorted(((r["commitment_id"], r["commitment"]) for r in rows), key=lambda r: (r[1], r[0]))

    def mark_commitments_sealed(self, commitment_ids: list[str], block_index: int) -> int:
        n = 0
        for cid in commitment_ids:
            n += self.conn.execute("UPDATE held_commitments SET sealed_block = ? WHERE commitment_id = ? "
                                   "AND sealed_block IS NULL", (block_index, cid)).rowcount
        self.conn.commit()
        return n

    # ---------- W10b (D1, D2): governed actions ----------

    def action_requested(self, row: dict, *, commit: bool = True) -> None:
        """The action's row before its request leaves: committed here, or the
        request is not sent (`backend/memory/actions.py`). With `commit=False`
        the caller's transaction holds it (W10c: an intercepted exchange's
        rows commit with its request, or with its record)."""
        self.conn.execute(
            "INSERT INTO action_records (action_id, kind, evidence_class, method, target, request_hash, "
            "credentials_json, lineage_id, trigger_route, accountable_identity, identity_mechanism, identity_salt, "
            "identity_commitment, signature_key_id, signature, requested_at, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["action_id"], row["kind"], row["evidence_class"], row["method"], row["target"], row["request_hash"],
             row["credentials_json"], row["lineage_id"], row["trigger_route"], row["accountable_identity"],
             row["identity_mechanism"], row["identity_salt"], row["identity_commitment"], row["signature_key_id"],
             row["signature"], row["requested_at"], row["state"]))
        if commit:
            self.conn.commit()

    def action_outcome(self, action_id: str, *, state: str, status: "str | None", response_hash: "str | None",
                       completed_at: float, record_hash: str, commit: bool = True) -> int:
        """The action's outcome, written once: the rows it changed (0 when the
        row is not `requested`, which the recorder treats as not written)."""
        n = self.conn.execute(
            "UPDATE action_records SET state = ?, status = ?, response_hash = ?, completed_at = ?, record_hash = ? "
            "WHERE action_id = ? AND state = 'requested'",
            (state, status, response_hash, completed_at, record_hash, action_id)).rowcount
        if commit:
            self.conn.commit()
        return n

    def exchange_requested(self, row: dict, *, request_hash: str, request_body: bytes, encoding: str,
                           outcomes: list) -> "str | None":
        """W10c — an intercepted exchange's request, before it is forwarded, in one transaction: the request's
        bytes (kept, W0b), the exchange's row, and the outcome of each earlier tool use this request reports
        (`outcomes`: `(action_id, {state, status, response_hash, completed_at}, record_hash)`). Returns why
        the bytes were not kept, or None when they were. Whatever fails, none of it is written."""
        if encoding not in REQUEST_ENCODINGS:
            raise ValueError(f"unknown request body encoding {encoding!r}: {REQUEST_ENCODINGS}")
        kept_body, why_not = self._kept_body(request_body, request_hash, encoding)
        try:
            if kept_body is not None:
                self._insert_request_body(kept_body, row["requested_at"])
            self.action_requested(row, commit=False)
            for action_id, outcome, record_hash in outcomes:
                if self.action_outcome(action_id, record_hash=record_hash, commit=False, **outcome) != 1:
                    raise RuntimeError(f"the tool use {action_id} was not `requested` when its result was linked")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return why_not

    def actions_in_state(self, *, evidence_class: str, kind: str, state: str) -> list[dict]:
        """W10c — the actions of one class and kind in one state, oldest first."""
        rows = self.conn.execute("SELECT * FROM action_records WHERE evidence_class = ? AND kind = ? AND state = ?",
                                 (evidence_class, kind, state)).fetchall()
        return sorted((dict(r) for r in rows), key=lambda r: (r["requested_at"], r["action_id"]))

    def actions_for(self, lineage_id: str) -> list[dict]:
        """The actions a turn triggered, oldest first."""
        rows = self.conn.execute("SELECT * FROM action_records WHERE lineage_id = ?", (lineage_id,)).fetchall()
        return sorted((dict(r) for r in rows), key=lambda r: (r["requested_at"], r["action_id"]))

    def unsealed_actions(self) -> list[tuple[str, str]]:
        """W10b (D6) — `(action_id, record_hash)` for each action whose outcome
        is written and no seal covers yet, by record hash (ordered here, not in
        SQL: P11). A row still `requested` has no hash and waits."""
        rows = self.conn.execute("SELECT action_id, record_hash FROM action_records "
                                 "WHERE sealed_block IS NULL AND record_hash IS NOT NULL").fetchall()
        return sorted(((r["action_id"], r["record_hash"]) for r in rows), key=lambda r: (r[1], r[0]))

    def mark_actions_sealed(self, action_ids: list[str], block_index: int) -> int:
        n = 0
        for aid in action_ids:
            n += self.conn.execute("UPDATE action_records SET sealed_block = ? WHERE action_id = ? "
                                   "AND sealed_block IS NULL", (block_index, aid)).rowcount
        self.conn.commit()
        return n

    def action_counts(self) -> dict:
        """W10b — `{evidence_class: {state: n}}` and the unsealed count, derived on every read."""
        out = {"runtime": {"requested": 0, "completed": 0, "failed": 0},
               "client_reported": {"requested": 0, "completed": 0, "failed": 0}}
        for r in self.conn.execute("SELECT evidence_class, state FROM action_records").fetchall():
            out.setdefault(r["evidence_class"], {}).setdefault(r["state"], 0)
            out[r["evidence_class"]][r["state"]] += 1
        out["unsealed"] = len(self.unsealed_actions())
        return out

    def action_by_signature(self, signature: str) -> "dict | None":
        """W10b (D10) — the action whose request carried this signature, or None."""
        row = self.conn.execute("SELECT * FROM action_records WHERE signature = ?", (signature,)).fetchone()
        return dict(row) if row is not None else None

    def action(self, action_id: str) -> "dict | None":
        row = self.conn.execute("SELECT * FROM action_records WHERE action_id = ?", (action_id,)).fetchone()
        return dict(row) if row is not None else None

    def held_commitment(self, commitment_id: str):
        return self.conn.execute("SELECT * FROM held_commitments WHERE commitment_id = ?",
                                 (commitment_id,)).fetchone()

    def mark_sealed(self, lineage_ids: list[str], block_index: int) -> int:
        """Backfill sealed_block after KAIROS appends the covering block."""
        cur = self.conn.executemany(
            "UPDATE lineage_records SET sealed_block = ? "
            "WHERE lineage_id = ? AND sealed_block IS NULL",
            [(block_index, lid) for lid in lineage_ids],
        )
        self.conn.commit()
        return cur.rowcount

    # ---------- verification ----------

    def verify_record(self, lineage_id: str) -> dict:
        """Recompute the record hash from stored fields and compare.
        A mutated row shows record_intact=False; a consistently-recomputed
        forgery still fails against the hash sealed in the chain block."""
        row = self.conn.execute(
            "SELECT * FROM lineage_records WHERE lineage_id = ?", (lineage_id,),
        ).fetchone()
        if row is None:
            return {"exists": False}
        # W8 (D9) — a tombstoned record verifies by its kept openings and its
        # root; its hash cannot be recomputed, by design.
        if lineage_id in self.tombstoned():
            intact = self.verify_tombstoned(row)
        else:
            intact = self.compute_record_hash(_hash_payload_from_row(row)) == row["record_hash"]
        return {
            "exists":        True,
            "record_intact": intact,
            "sealed":        row["sealed_block"] is not None,
            "sealed_block":  row["sealed_block"],
        }

    # ---------- helpers ----------

    def _all_rows(self, limit: int | None = None) -> list[Row]:
        sql = "SELECT * FROM lineage_records ORDER BY ts ASC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return self.conn.execute(sql).fetchall()

    @staticmethod
    def _row_to_record(row: Row) -> LineageRecord:
        return LineageRecord(
            lineage_id         = row["lineage_id"],
            ts                 = row["ts"],
            query_hash         = row["query_hash"],
            injected           = tuple(json.loads(row["injected_json"])),
            contradictions     = tuple(json.loads(row["contradictions_json"])),
            model_id           = row["model_id"],
            prompt_prefix_hash = row["prompt_prefix_hash"],
            response_hash      = row["response_hash"],
            record_hash        = row["record_hash"],
            sealed_block       = row["sealed_block"],
            scope              = row["scope"],
            citation_test      = (row["citation_test"]
                                  if "citation_test" in row.keys() else None),
            context            = (json.loads(row["context_json"])
                                  if "context_json" in row.keys() and row["context_json"]
                                  else None),
            request_hash       = (row["request_hash"]
                                  if "request_hash" in row.keys() else None),
            work_unit_id       = (row["work_unit_id"]
                                  if "work_unit_id" in row.keys() else None),
            accountable_identity = (row["accountable_identity"]
                                    if "accountable_identity" in row.keys() else None),
            boundary_asserted  = (row["boundary_asserted"]
                                  if "boundary_asserted" in row.keys() else None),
            boundary_asserted_by = (json.loads(row["boundary_asserted_by"])
                                    if "boundary_asserted_by" in row.keys()
                                    and row["boundary_asserted_by"] else None),
            attribution_flags  = (tuple(json.loads(row["attribution_flags"]))
                                  if "attribution_flags" in row.keys()
                                  and row["attribution_flags"] else ()),
            learning_mode      = (row["learning_mode"]
                                  if "learning_mode" in row.keys() else None),
            answer_hash        = (row["answer_hash"] if "answer_hash" in row.keys() else None),
            record_scope       = (row["record_scope"] if "record_scope" in row.keys() else None),
            reasoning_hash     = (row["reasoning_hash"] if "reasoning_hash" in row.keys() else None),
            answer_chars       = (row["answer_chars"] if "answer_chars" in row.keys() else None),
            reasoning_chars    = (row["reasoning_chars"] if "reasoning_chars" in row.keys() else None),
            answer_empty_reason = (row["answer_empty_reason"]
                                   if "answer_empty_reason" in row.keys() else None),
            inference_seed     = (row["inference_seed"] if "inference_seed" in row.keys() else None),
            model_digest       = (row["model_digest"] if "model_digest" in row.keys() else None),
            inference_config_hash = (row["inference_config_hash"]
                                     if "inference_config_hash" in row.keys() else None),
            environment_hash   = (row["environment_hash"]
                                  if "environment_hash" in row.keys() else None),
            capture_notes      = (json.loads(row["capture_notes"])
                                  if "capture_notes" in row.keys() and row["capture_notes"]
                                  else None),
            identity_mechanism = (row["identity_mechanism"]
                                  if "identity_mechanism" in row.keys() else None),
            envelope_version   = (row["envelope_version"]
                                  if "envelope_version" in row.keys() else None),
            forecast           = forecast_from_text(row["forecast"] if "forecast" in row.keys() else None),
        )


# ---------------------------------------------------------------------------
# H.1 — the two-layer tamper result, lifted out of the test that proved it
# ---------------------------------------------------------------------------

def verify_sealed_lineage(
    lineage: "LineageStore",
    chain,
    *,
    lineage_id: str | None = None,
    limit: int | None = None,
) -> dict:
    """Verify answer-lineage records at every layer, reported separately.

    WHY THIS EXISTS
        Packet E.1 established the guarantee and left it inside a test
        (`test_sealed_lineage_tamper_detected`). The system's strongest
        verified property was reachable only by reading pytest. This is that
        check as a callable, with identical semantics.

    THE THREE LAYERS, AND WHY THEY ARE NOT ONE BOOLEAN

        row    Does each record's content still hash to its stored
               `record_hash`? Catches a naive tamper — someone editing a
               field and leaving the hash behind.

        seal   Does each `record_hash` appear in the `record_hashes` sealed
               into the block named by `sealed_block`? This is the layer that
               matters. A **consistent forgery** — content and `record_hash`
               rewritten together — passes `row` cleanly and fails here,
               because the forger cannot reach into a chain block whose own
               hash is covered by the chain.

        chain  Does that block still verify within the chain? Without this,
               `seal` would be checking against evidence an attacker could
               have rewritten too.

        **Collapsing these into one boolean would discard the finding.**
        "Invalid" tells you nothing; "row-intact but seal-broken" names a
        forgery, and "row-broken" names a clumsy edit. Different attacks,
        different responses, and the distinction is the guarantee.

    UNSEALED IS NOT A FAILURE (I-6)
        A record written since the last KAIROS cycle has `sealed_block IS
        NULL` and is simply *awaiting* its seal. Reporting that as invalid
        would be a false alarm on every healthy store, and an operator who
        learns to ignore one alarm ignores the next. It is counted and named
        as its own state.

    Nothing here writes. Nothing is recomputed for display: the layers
    recompute hashes to *compare*, never to replace a stored value.
    """
    rows = ([lineage.conn.execute(
                "SELECT * FROM lineage_records WHERE lineage_id = ?",
                (lineage_id,)).fetchone()]
            if lineage_id is not None else lineage._all_rows(limit))
    rows = [r for r in rows if r is not None]

    if lineage_id is not None and not rows:
        return {"exists": False, "lineage_id": lineage_id}

    row_ok, row_failed = [], []
    seal_ok, seal_failed, unsealed = [], [], []
    missing_block: list[dict] = []

    # Cache blocks by index: many records typically share one seal.
    block_cache: dict[int, object] = {}

    tombstoned = lineage.tombstoned()      # W8 (D9)
    for row in rows:
        lid = row["lineage_id"]

        # ---- layer 1: row-level -------------------------------------------
        # W8 (D9) — a tombstoned record by its kept openings and its root.
        if lid in tombstoned:
            intact = lineage.verify_tombstoned(row)
        else:
            intact = LineageStore.compute_record_hash(_hash_payload_from_row(row)) == row["record_hash"]
        (row_ok if intact else row_failed).append(lid)

        # ---- layer 2: seal-level ------------------------------------------
        idx = row["sealed_block"]
        if idx is None:
            unsealed.append(lid)
            continue

        if idx not in block_cache:
            block_cache[idx] = (chain.get_block(idx) if chain is not None
                                else None)
        block = block_cache[idx]
        if block is None:
            # The record names a block that is not in the chain. That is not
            # "unsealed" and not "tampered content" — it is a dangling seal,
            # and conflating it with either would misdescribe it.
            missing_block.append({"lineage_id": lid, "sealed_block": idx})
            seal_failed.append(lid)
            continue

        sealed_hashes = (block.fingerprint or {}).get(
            "lineage_seal", {}).get("record_hashes", [])
        if row["record_hash"] in sealed_hashes:
            seal_ok.append(lid)
        else:
            seal_failed.append(lid)

    # ---- layer 3: chain ---------------------------------------------------
    if chain is None:
        chain_layer = {"ok": None, "reason": "no_chain_available",
                       "n_blocks": 0, "broken_at": None}
    else:
        result = chain.verify_chain()
        chain_layer = {
            "ok":        result.valid,
            "n_blocks":  result.n_blocks,
            "broken_at": result.broken_at,
            "reason":    result.reason,
        }

    # ---- layer 4 (W8, D8): each sealed record's proof, and block to row -----
    # A proof is built from the store and verified from itself alone, as a
    # stranger would; the block-to-row read is the one W6 gave the governance
    # tables: every hash a block's lineage seal holds must still be some
    # record's, so a record rewritten with a recomputed hash and its seal
    # cleared is named (BACKLOG W6-B6), and so is a sealed record deleted.
    from backend.memory import proofs as _proofs
    proof_failures: list[dict] = []
    proof_checked = 0
    bound = unbound = 0            # D6: records sealed before W8, bound or awaiting their binding
    orphaned: list[dict] | None = None
    tampered = set(row_failed)     # the row layer names these; a root that cannot rebuild is theirs, not a seal's
    if chain is not None:
        for row in rows:
            if row["sealed_block"] is None:
                continue
            proof_checked += 1
            proof = _proofs.build_proof(lineage, chain, row["lineage_id"])
            why = (_proofs.verify_proof(proof) if proof and proof.get("state") == "sealed"
                   else f"malformed: the record's proof is {proof and proof.get('state')}")
            if why:
                proof_failures.append({"lineage_id": row["lineage_id"], "refusal": why})
            elif proof.get("commitment_root") is None:
                # Its seal claims no root (a block before W8, or a bare-hash
                # seal): bound, or awaiting the next seal's binding. A
                # tombstoned record's root must be anchored, since what it
                # keeps verifies against that root and nothing else.
                if "binding" in proof:
                    bound += 1
                elif row["lineage_id"] in tombstoned:
                    proof_failures.append({"lineage_id": row["lineage_id"], "refusal":
                                           "binding_malformed: a tombstoned record's root is anchored by no seal"})
                elif (row["lineage_id"] not in tampered and lineage.commitment_root(row["lineage_id"])
                      and not lineage.root_rebuilds(row["lineage_id"])):
                    proof_failures.append({"lineage_id": row["lineage_id"], "refusal":
                                           "binding_malformed: the record's root does not rebuild from its values "
                                           "under its salts, so no seal may bind it"})
                else:
                    unbound += 1
        if lineage_id is None:
            stored = {r["record_hash"] for r in lineage.conn.execute("SELECT record_hash FROM lineage_records")}
            orphaned = [{"sealed_block": b.index, "record_hash": h}
                        for b in chain.all_blocks()
                        for h in (((b.fingerprint or {}).get("lineage_seal") or {}).get("record_hashes") or [])
                        if h not in stored]
        # W8 (D6) — and block to row, for the records read here (every one, or
        # the one asked for: W8's third delta check found the per-record read
        # never ran this). Every binding leaf must be its record's current hash
        # and root (W8's delta check: a binding cleared and re-made over a
        # rewritten root read as intact), the leaf alone: a binding block
        # appended whose kb.db marks then failed is bound again by the next
        # seal, so two blocks may hold one honest leaf (delta check 2, MF-B).
        # And every layout-2 lineage leaf, in whichever block it is: one that
        # carries a record's hash with another root than the store's, or a root
        # the store no longer holds, is named, so a covered tombstone
        # rewritten, its seal cleared and re-made under the new root, is named
        # by its first block (delta check 2).
        query = ("SELECT lr.lineage_id, lr.record_hash, rc.commitment_root FROM lineage_records lr "
                 "LEFT JOIN record_commitments rc ON rc.lineage_id = lr.lineage_id")
        by_hash = {r["record_hash"]: (r["lineage_id"], r["commitment_root"]) for r in (
            lineage.conn.execute(query) if lineage_id is None
            else lineage.conn.execute(query + " WHERE lr.lineage_id = ?", (lineage_id,)))}
        for b in chain.all_blocks():
            seal = (b.fingerprint or {}).get("binding_seal") or {}
            for h, leaf in zip(seal.get("record_hashes") or [], seal.get("leaves") or []):
                held = by_hash.get(h)
                if held is None:
                    if orphaned is not None:                       # the whole store: a hash no record holds
                        orphaned.append({"sealed_block": b.index, "record_hash": h, "seal": "binding"})
                elif leaf != h + (held[1] or ""):
                    proof_failures.append({"lineage_id": held[0], "refusal":
                                           f"binding_malformed: block {b.index}'s binding no longer matches "
                                           "the record's root"})
            seal = (b.fingerprint or {}).get("lineage_seal") or {}
            leaves = seal.get("leaves")
            if b.layout == 1 or leaves is None:
                continue
            for h in seal.get("record_hashes") or []:
                held = by_hash.get(h)
                if held is None:
                    continue                                        # named above, as sealed without a row
                state = _seal_state(b, h, held[1])
                if state == "contradicted" or (state == "no_root" and any(
                        x.startswith(h) and x != h for x in leaves)):
                    proof_failures.append({"lineage_id": held[0], "refusal":
                                           f"leaf_mismatch: block {b.index}'s leaf for the record carries "
                                           "another root than the store's"})
    verdict = _lineage_verdict(
        row_failed, seal_failed + [f["lineage_id"] for f in proof_failures]
        + [o["record_hash"] for o in (orphaned or [])], chain_layer)
    return {
        "records_checked": len(rows),
        # W8 (D9) — tombstoned records among those checked: verified by their
        # kept openings and root (row layer) and their leaf (proofs).
        "tombstoned": sum(1 for r in rows if r["lineage_id"] in tombstoned),
        "proofs": {"checked": proof_checked, "failed": len(proof_failures), "failures": proof_failures,
                   # D6 — of the records whose seal claims no root (before W8,
                   # or sealed as a bare hash): bound, and awaiting a binding.
                   "bound": bound, "unbound": unbound},
        "sealed_without_row": len(orphaned) if orphaned is not None else None,
        "sealed_without_row_failures": orphaned or [],
        # W0b — the kept request copies, verified BY the record (each must
        # hash to the `request_hash` its record seals) and reported BESIDE
        # the three layers, never inside them or their verdict: a corrupted
        # copy is not a tampered record; it is a record that can no longer
        # be re-run, and the failure names it.
        "request_bodies": lineage.verify_request_bodies(lineage_id=lineage_id),
        # W0b — the options and environment rows the records key, the same
        # way: beside the layers, never in their verdict.
        "capture_rows": lineage.verify_capture_rows(lineage_id=lineage_id),
        "layers": {
            "row": {
                "ok":       len(row_ok),
                "failed":   len(row_failed),
                "failures": row_failed,
            },
            "seal": {
                "ok":            len(seal_ok),
                "failed":        len(seal_failed),
                "failures":      seal_failed,
                "unsealed":      len(unsealed),
                "unsealed_ids":  unsealed,
                "missing_block": missing_block,
            },
            "chain": chain_layer,
        },
        "verdict": verdict,
    }


def _lineage_verdict(row_failed, seal_failed, chain_layer) -> str:
    """A one-word summary that never replaces the layers above it.

    Ordered by severity, and named so the name carries the diagnosis: a
    caller reading `seal_broken` learns "the content hashes to itself but is
    not the content that was sealed", which is a forgery, not a typo.
    """
    if chain_layer.get("ok") is False:
        return "chain_broken"
    if seal_failed and not row_failed:
        return "seal_broken"           # consistent forgery — the E.1 result
    if row_failed and seal_failed:
        return "record_and_seal_broken"
    if row_failed:
        return "record_tampered"
    return "intact"
