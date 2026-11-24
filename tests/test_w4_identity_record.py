"""W4 part 2 — `identity_mechanism` on the lineage record (tier 1: a sealed field).

How the record's `accountable_identity` was established, sealed by the LS
mechanism (a key enters the hashed body only when it is not the default):
`asserted`, the default, stored NULL and never hashed — the identity as the
request (or, under MCP, the configuration) stated it, recorded and never
authenticated; `proxy` — this process took the login from the identity header
of a loopback peer and answers only for its configured owner: the proxy
authenticated the person, NEXUS did not (run/specs/W4.md D10). Every record
written before the field, and every asserted one, recomputes byte for byte.

The pre-registered tests (run/specs/W4.md S3), each run on the base 9b200e9
before the product change (law 16; the output is the part's evidence,
`prefix.txt`). A failure there that is only "the new name does not exist yet"
is declared in the test's docstring.

R1  the default hashes as the base wrote it: a W4 literal computed on 9b200e9
    equals today's hash with the field absent and with `asserted` spelled out;
    the LS literal the same way
R2  a store made by the base's DDL gains the column on open, last; its rows
    read NULL and verify, at the row and the seal layer
R2b a store from before W0b (the committed pre-run fixture) migrates to the
    fresh store's column order exactly, so the store copy still takes it; a
    column added ahead of W0b's is refused by the copy
R3  both tamper directions are named by `verify_record` and `/lineage/verify`:
    a sealed `proxy` rewritten to NULL, and `proxy` written into a sealed
    asserted record; R3c: a check over the sealed non-NULL values only misses
    the second, and R3's assertion fails on it
R4  a value outside the closed vocabulary raises at `record_turn` before
    anything is written (the record, its index rows, its companions)
R5  an MCP record carries no mechanism; a mechanism on an `mcp_block` record
    is named by `verify_scope` and on `/lineage/{id}`
R6  the column is cid 31 in the answered-for set with its W4 reason;
    `RECORD_CLAIMS` and the three scope tables classify it undefined under MCP
    and agree with each other on every field; `/lineage/{id}` carries it,
    unversioned
R7  recall, contract version 2: a `proxy` record's row is labelled `proxy`,
    an asserted record's `asserted`, on the HTTP route and MCP's
    `nexus_recall`, with one declaration that says what each label means; the
    documents state version 2

Beyond S3's list, for the brief's step 2: the loop carries the mechanism to
the record as a parameter and never on the handle (L1), and every
`record_turn` call site in the tracked backend states it explicitly (G1, a
guard of the AW `context` guard's shape, with its planted companion G1c).

PLATFORM is stamped on every printed line (sys.platform).
"""
from __future__ import annotations

import ast
import asyncio
import dataclasses
import hashlib
import json
import os
import re
import sqlite3
import sys
import textwrap
import types
import uuid
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import TurnHandle, run_turn
from backend.loop.state import NexusLoopState
from backend.mcp import scope as scope_mod
from backend.mcp.scope import verify_scope
from backend.memory.chain import NexusArchiveChain
from backend.memory.lineage import LineageStore, _hash_payload_from_row, verify_sealed_lineage
from backend.server import create_app, create_services
from backend.store import connect as store_connect
from tests._tracked import tracked_backend
from tests.conftest import store_target_for
from tests.test_ls_learning_state import FIXED_ID as LS_FIXED_ID
from tests.test_ls_learning_state import FIXED_TS as LS_FIXED_TS
from tests.test_ls_learning_state import FIXTURE_TURN as LS_FIXTURE_TURN
from tests.test_ls_learning_state import PRE_LS_HASH
from tests.test_mcp1_split import (CHANGED, RECORD_CLAIMS, UNCHANGED, UNDEFINED, hashed_when_present,
                                   lineage_record_fields)
from tests.test_scope_recorded import ANSWERED_COLUMNS, check_table_layer2

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "w4-identity-record"
H = {"Authorization": f"Bearer {TOKEN}"}

# ---------------------------------------------------------------------------
# The literals, computed on the base before anything was built (step 0)
# ---------------------------------------------------------------------------
#
# BASE is the commit the literals below were computed at: the W4 spec's own
# commit, whose backend/ is W3b's close. The computation is
# SP/w4/p2/step0_compute.py run with that tree's code on a scratch store; its
# output is SP/w4/p2/step0_base.txt (the part's evidence, beside prefix.txt).
BASE = "9b200e9823e315f1b8ab7055bb4a7d1eb8ce2068"

# R1 — the hash the base wrote for this exact fixture turn: a console turn's
# shape with `accountable_identity` set, a work unit, the answer and the W0b
# capture, at a fixed instant and id.
W4_PRE_HASH = "ac94d8263c4f015c2c4cad3b9b239536b1d456dc7997aa4727dfddaa208d6dfd"
W4_FIXED_TS = 1_762_000_000.0
W4_FIXED_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a4")
W4_FIXTURE_TURN = dict(
    query="who was answerable for this answer?",
    injected=[{"belief_id": 3, "weight_at_injection": 0.64, "tier_at_injection": 1, "cited": True},
              {"belief_id": 5, "weight_at_injection": 0.41, "tier_at_injection": 2, "cited": False}],
    contradictions=[], model_id="fixture-model", response_text="fixture answer", prompt_prefix="P",
    scope="default", citation_test="f5.2-informative-no_substrate", request_hash="b" * 64,
    work_unit_id="WU-W4", accountable_identity="tester-1@example.com",
    answer_text="fixture answer",
    inference_seed=424242, model_digest="sha256:" + "d" * 64,
    inference_config={"model": "fixture-model", "options": {"num_ctx": 8192}},
    environment={"os": "fixture", "python": "fixture"},
)

# R2 — the base's lineage DDL (`backend/memory/lineage.py` SCHEMA_SQL at BASE,
# its comment lines dropped; nothing else changed) and its sha256, and the
# rows the base stored for the W4 fixture turn and the LS fixture turn, every
# column as the base wrote it.
BASE_LINEAGE_DDL_SHA256 = "4bc6123e32b5f3d17557d92cd8b721aba232ff93b4d7614f6e569cbeeeeab2c9"
BASE_LINEAGE_DDL = """
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
    scope               TEXT NOT NULL DEFAULT 'default',
    citation_test       TEXT,
    context_json        TEXT,
    request_hash        TEXT,
    work_unit_id        TEXT,
    accountable_identity TEXT,
    boundary_asserted   TEXT,
    boundary_asserted_by TEXT,
    attribution_flags   TEXT,
    learning_mode       TEXT,
    answer_hash         TEXT,
    reasoning_hash      TEXT,
    answer_chars        INTEGER,
    reasoning_chars     INTEGER,
    answer_empty_reason TEXT,
    record_scope        TEXT,
    inference_seed      INTEGER,
    model_digest        TEXT,
    inference_config_hash TEXT,
    environment_hash    TEXT,
    capture_notes       TEXT
);
CREATE INDEX IF NOT EXISTS idx_lineage_ts ON lineage_records(ts);

CREATE TABLE IF NOT EXISTS request_bodies (
    request_hash  TEXT PRIMARY KEY,
    encoding      TEXT NOT NULL,
    body          BLOB NOT NULL,
    raw_bytes     INTEGER NOT NULL,
    first_seen_ts REAL NOT NULL
);
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

CREATE TABLE IF NOT EXISTS lineage_injections (
    belief_id   INTEGER NOT NULL,
    lineage_id  TEXT NOT NULL REFERENCES lineage_records(lineage_id),
    PRIMARY KEY (belief_id, lineage_id)
);
CREATE INDEX IF NOT EXISTS idx_injections_lineage ON lineage_injections(lineage_id);"""

BASE_ROW_W4 = {'accountable_identity': 'tester-1@example.com',
 'answer_chars': 14,
 'answer_empty_reason': None,
 'answer_hash': 'b7ee2726de6ca82314c48a0190296560be0f3954487beb645468cdb16c09e23a',
 'attribution_flags': None,
 'boundary_asserted': None,
 'boundary_asserted_by': None,
 'capture_notes': None,
 'citation_test': 'f5.2-informative-no_substrate',
 'context_json': None,
 'contradictions_json': '[]',
 'environment_hash': 'e2621461073353de902d3d304eca920b7e93ca05214bc3818263c4c61b3807b5',
 'inference_config_hash': 'c7e9a6bdd0f57019505e6c729b1e63aa0a6405454d5de518adc1771795ef1409',
 'inference_seed': 424242,
 'injected_json': '[{"belief_id": 3, "cited": true, "tier_at_injection": 1, "weight_at_injection": 0.64}, '
                  '{"belief_id": 5, "cited": false, "tier_at_injection": 2, "weight_at_injection": 0.41}]',
 'learning_mode': None,
 'lineage_id': '00000000-0000-4000-8000-0000000000a4',
 'model_digest': 'sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
 'model_id': 'fixture-model',
 'prompt_prefix_hash': '5c62e091b8c0565f1bafad0dad5934276143ae2ccef7a5381e8ada5b1a8d26d2',
 'query_hash': '13f99e151b1b089b7a3be1235d2a2ba5699d4045582721c7d2a02d92bf1ca155',
 'reasoning_chars': None,
 'reasoning_hash': None,
 'record_hash': 'ac94d8263c4f015c2c4cad3b9b239536b1d456dc7997aa4727dfddaa208d6dfd',
 'record_scope': None,
 'request_hash': 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
 'response_hash': 'b7ee2726de6ca82314c48a0190296560be0f3954487beb645468cdb16c09e23a',
 'scope': 'default',
 'sealed_block': None,
 'ts': 1762000000.0,
 'work_unit_id': 'WU-W4'}

BASE_ROW_LS = {'accountable_identity': 'alice',
 'answer_chars': None,
 'answer_empty_reason': None,
 'answer_hash': None,
 'attribution_flags': None,
 'boundary_asserted': None,
 'boundary_asserted_by': None,
 'capture_notes': None,
 'citation_test': 'f5.2-informative-no_substrate',
 'context_json': None,
 'contradictions_json': '[]',
 'environment_hash': None,
 'inference_config_hash': None,
 'inference_seed': None,
 'injected_json': '[{"belief_id": 1, "cited": true, "tier_at_injection": 1, "weight_at_injection": 0.7}]',
 'learning_mode': None,
 'lineage_id': '00000000-0000-4000-8000-000000000001',
 'model_digest': None,
 'model_id': 'fixture-model',
 'prompt_prefix_hash': '5c62e091b8c0565f1bafad0dad5934276143ae2ccef7a5381e8ada5b1a8d26d2',
 'query_hash': '310c80b51344508ef8ff7ea3bf800d588e7a496cbf699de3d0d88960eebb8560',
 'reasoning_chars': None,
 'reasoning_hash': None,
 'record_hash': '811059ffe984a866e753387e9d9fc9589734a4ef3bcee45dadb883f75a777519',
 'record_scope': None,
 'request_hash': 'rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr',
 'response_hash': 'b7ee2726de6ca82314c48a0190296560be0f3954487beb645468cdb16c09e23a',
 'scope': 'default',
 'sealed_block': None,
 'ts': 1760000000.0,
 'work_unit_id': 'WU-1'}

BASE_COMPANIONS = {'environments': [{'descriptor_json': '{"os":"fixture","python":"fixture"}',
                   'environment_hash': 'e2621461073353de902d3d304eca920b7e93ca05214bc3818263c4c61b3807b5',
                   'first_seen_ts': 1762000000.0}],
 'inference_configs': [{'config_hash': 'c7e9a6bdd0f57019505e6c729b1e63aa0a6405454d5de518adc1771795ef1409',
                        'config_json': '{"model":"fixture-model","options":{"num_ctx":8192}}',
                        'first_seen_ts': 1762000000.0}]}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write(lineage: LineageStore, turn: dict, ts: float, lid: uuid.UUID, **extra):
    """`record_turn` at a fixed instant and id — the shape of LS's T2."""
    with mock.patch("backend.memory.lineage.time.time", return_value=ts), \
            mock.patch("backend.memory.lineage.uuid.uuid4", return_value=lid):
        return lineage.record_turn(**turn, **extra)


def _store(tmp_path: Path, name: str) -> LineageStore:
    (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return LineageStore(tmp_path / name / "kb.db")


def _row(lineage: LineageStore, lineage_id: str):
    return lineage.conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (lineage_id,)).fetchone()


def _seal(lineage: LineageStore, chain: NexusArchiveChain) -> int:
    """LS's seal: every recent record into one governance block."""
    recs = lineage.recent(limit=100)
    hashes = [r.record_hash for r in recs]
    block = chain.append({"kind": "governance", "lineage_seal": {
        "count": len(hashes), "digest": LineageStore.seal_digest(hashes), "record_hashes": hashes}},
        "", kind="governance")
    lineage.mark_sealed([r.lineage_id for r in recs], block.index)
    return block.index


ANSWER = "Creatine improves strength and caffeine improves endurance."


class _Provider:
    """The answer, then an empty extraction (LS's stub)."""

    def __init__(self):
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        if self.calls % 2 == 1:
            for tok in ANSWER.split(" "):
                yield tok + " "
        else:
            yield "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _plant(path: Path) -> tuple[TripleStore, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    store = TripleStore(path)
    ids = [store.add(subject=s, relation=r, object=o, weight=0.9, tier=1,
                     granularity=Granularity.TOPIC, source="wikidata")
           for s, r, o in (("creatine", "improves", "strength"), ("caffeine", "improves", "endurance"))]
    return store, ids[0]


def _services(tmp_path: Path, monkeypatch, name: str = "app"):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    svc = create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma",
                          eval_log_path=root / "m.jsonl", archive_db_path=root / "archive.db",
                          snapshots_dir=root / "snap", provider=_Provider())
    return svc, TestClient(create_app(svc))


def _done_of(sse_text: str) -> dict:
    return next(json.loads(f.split("data: ", 1)[1]) for f in sse_text.split("\n\n")
                if f.startswith("event: done"))


LEARNING_KEYS = ("NEXUS_LEARNING", "NEXUS_LEARNING_FROZEN_SINCE", "NEXUS_MCP_OWNER",
                 "NEXUS_MCP_DATA_ROOT", "NEXUS_MCP_HANDLE_TIMEOUT")


@pytest.fixture
def mcp_rt(tmp_path, monkeypatch):
    """An MCP runtime and its server, owner `alice`. The runtime writes
    NEXUS_LEARNING into the process environment on purpose (MCP mode's
    default; tests/test_mcp2_build.py's note), so the keys are restored by
    hand. Yields (runtime, server, root, the planted claim's id)."""
    from backend.mcp.mcp_server import NexusRuntime, build_server
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    before = {k: os.environ.get(k) for k in LEARNING_KEYS}
    for k in before:
        os.environ.pop(k, None)
    root = tmp_path / "mcp"
    rt = NexusRuntime.open(root=root, owner="alice")
    bid = rt.services.store.add(subject="python", relation="is_a", object="programming_language", weight=0.8,
                                tier=1, granularity=Granularity.TOPIC, source="human_assertion")
    try:
        yield rt, build_server(rt, with_lifespan=False), root, bid
    finally:
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _call(server, name, **args):
    return asyncio.run(server.call_tool(name, args)).model_dump()["structured_content"]


def _mcp_turn(srv) -> str:
    r = _call(srv, "nexus_retrieve", question="tell me about python")
    return _call(srv, "nexus_record", retrieval_id=r["retrieval_id"], answer="Python is a programming_language.")["lineage_id"]


# ---------------------------------------------------------------------------
# R1 — every earlier record recomputes unchanged
# ---------------------------------------------------------------------------

def test_r1_the_default_hashes_as_the_base_wrote_it_absent_and_spelled_out(tmp_path):
    """R1. The W4 fixture turn written by today's code hashes to the literal the base computed
    (9b200e9), with the field absent and with the default spelled out (`asserted`, with
    surrounding whitespace, and empty); it is stored NULL and recomputes from its row and from
    `to_dict()`, which names the default. The LS literal holds the same way with `asserted`
    spelled out. Not vacuous: the same turn written `proxy` hashes otherwise, and only by its
    own key (without it the body is the literal's).

    Law 16: on the base the absent arm's hash passes by construction (the literal was computed
    there); the test fails there only on the new names, declared: the stored row has no
    `identity_mechanism` column (IndexError), and `record_turn` takes no such parameter."""
    spellings = [("absent", {}), ("asserted", {"identity_mechanism": "asserted"}),
                 ("' asserted '", {"identity_mechanism": " asserted "}), ("''", {"identity_mechanism": ""})]
    for i, (label, extra) in enumerate(spellings):
        lineage = _store(tmp_path, f"s{i}")
        rec = _write(lineage, W4_FIXTURE_TURN, W4_FIXED_TS, W4_FIXED_ID, **extra)
        row = _row(lineage, rec.lineage_id)
        print(f"\nR1 ({sys.platform}) {label}: {rec.record_hash[:16]}… (base literal {W4_PRE_HASH[:16]}…, at {BASE[:7]})")
        assert rec.record_hash == W4_PRE_HASH, label
        assert row["identity_mechanism"] is None and rec.identity_mechanism is None, label
        assert rec.to_dict()["identity_mechanism"] == "asserted", "the dict names the default"
        assert LineageStore.compute_record_hash(rec.to_dict()) == W4_PRE_HASH, "recomputed from to_dict"
        assert LineageStore.compute_record_hash(_hash_payload_from_row(row)) == W4_PRE_HASH, "from the row"
        assert lineage.verify_record(rec.lineage_id)["record_intact"]
        lineage.close()
    ls = _store(tmp_path, "ls")
    pre = _write(ls, LS_FIXTURE_TURN, LS_FIXED_TS, LS_FIXED_ID, identity_mechanism="asserted")
    assert pre.record_hash == PRE_LS_HASH and LineageStore.compute_record_hash(pre.to_dict()) == PRE_LS_HASH
    ls.close()
    lineage = _store(tmp_path, "proxy")
    proxied = _write(lineage, W4_FIXTURE_TURN, W4_FIXED_TS, W4_FIXED_ID, identity_mechanism="proxy")
    payload = _hash_payload_from_row(_row(lineage, proxied.lineage_id))
    print(f"R1 ({sys.platform}) proxy: {proxied.record_hash[:16]}…; without its key "
          f"{LineageStore.compute_record_hash({**payload, 'identity_mechanism': None})[:16]}…")
    assert proxied.record_hash != W4_PRE_HASH and payload["identity_mechanism"] == "proxy"
    assert LineageStore.compute_record_hash({**payload, "identity_mechanism": None}) == W4_PRE_HASH
    assert LineageStore.compute_record_hash(proxied.to_dict()) == proxied.record_hash
    lineage.close()


# ---------------------------------------------------------------------------
# R2 — the migration
# ---------------------------------------------------------------------------

def _base_store(kb: Path) -> list[str]:
    """A store made by the base's DDL, holding the rows the base wrote (every
    column as stored) and their companions; returns its lineage columns."""
    assert hashlib.sha256(BASE_LINEAGE_DDL.encode("utf-8")).hexdigest() == BASE_LINEAGE_DDL_SHA256, \
        "the base's DDL literal was edited"
    raw = store_connect(store_target_for(kb))
    try:
        raw.executescript(BASE_LINEAGE_DDL)
        for row in (BASE_ROW_W4, BASE_ROW_LS):
            cols = list(row)
            raw.execute(f"INSERT INTO lineage_records ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                        [row[c] for c in cols])
        for table, rows in BASE_COMPANIONS.items():
            for row in rows:
                cols = list(row)
                raw.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                            [row[c] for c in cols])
        raw.commit()
        return [c.name for c in raw.columns("lineage_records")]
    finally:
        raw.close()


def test_r2_a_store_made_by_the_base_ddl_gains_the_column_and_every_row_verifies(tmp_path):
    """R2. A store made by the base's DDL, holding the rows the base wrote, gains the column when
    today's code opens it — after every earlier column, as a fresh store has it — and nothing
    stored moves: both rows read NULL (`asserted` in the dict), keep their hashes and verify at
    the row layer and, once sealed, at the seal and chain layers, with their capture rows intact.

    Law 16: fails on the base on its consequence (the base's open adds no column)."""
    kb = tmp_path / "kb.db"
    before = _base_store(kb)
    assert len(before) == 31 and "identity_mechanism" not in before
    lineage = LineageStore(kb)                                  # migrates at open
    after = [c.name for c in lineage.conn.columns("lineage_records")]
    fresh = _store(tmp_path, "fresh")
    print(f"\nR2 ({sys.platform}) base store: {len(before)} columns; after today's open {len(after)}, last {after[-1]!r}")
    # W6 added two columns after W4's; a base store gains all three, in the DDL's order
    assert after == before + ["identity_mechanism", "envelope_version", "forecast"], \
        "the column is gained, after every earlier column"
    assert after == [c.name for c in fresh.conn.columns("lineage_records")], "the order a fresh store has"
    fresh.close()
    for base_row in (BASE_ROW_W4, BASE_ROW_LS):
        lid = base_row["lineage_id"]
        row = _row(lineage, lid)
        assert row["identity_mechanism"] is None and row["record_hash"] == base_row["record_hash"]
        rec = lineage.get(lid)
        assert rec.identity_mechanism is None and rec.to_dict()["identity_mechanism"] == "asserted"
        assert lineage.verify_record(lid)["record_intact"], lid
    chain = NexusArchiveChain(tmp_path / "archive.db")
    _seal(lineage, chain)
    v = verify_sealed_lineage(lineage, chain)
    print(f"R2 ({sys.platform}) sealed verdict {v['verdict']}; capture rows {v['capture_rows']['verdict']}")
    assert v["verdict"] == "intact" and v["records_checked"] == 2 and v["layers"]["seal"]["ok"] == 2
    assert v["capture_rows"]["verdict"] == "intact"
    lineage.close(); chain.close()


@pytest.mark.sqlite_only("opens a copy of the committed pre-run SQLite fixture (tests/fixtures/pre_run_data); its "
                         "Postgres twin is the migration round trip (tests/test_w3b_migration.py T1/T2), which "
                         "copies the same fixture into Postgres through the same column-order check")
def test_r2b_a_store_from_before_w0b_migrates_to_the_ddl_column_order(tmp_path):
    """R2b. The pre-run fixture (MCP-2's vintage, before W0b's five columns) migrates to exactly
    the column order a fresh store has, the new column last, so the store copy — which refuses a
    source whose column order is not its target's (scripts/_store_copy.py `copy_rows`) — still
    takes it, and every copied record recomputes in the target. That is why the column is added
    after W0b's loop and not in `_migrate_scope`'s TEXT loop, which runs before it: planted here,
    the column added ahead of W0b's columns, the copy refuses. W3b's T1/T2 migrate this fixture.

    Law 16: fails on the base on its consequence (the base's open adds no column)."""
    from scripts._store_copy import CopyRefused, copy_rows
    from tests.test_pre_run_fixture import MANIFEST, _copy
    fresh = _store(tmp_path, "fresh")
    fresh_cols = [c.name for c in fresh.conn.columns("lineage_records")]
    src = LineageStore(_copy(tmp_path / "good") / "kb.db")
    cols = [c.name for c in src.conn.columns("lineage_records")]
    print(f"\nR2b ({sys.platform}) pre-run fixture after today's open: {len(cols)} columns, last {cols[-1]!r}; "
          f"fresh: {len(fresh_cols)}")
    assert cols == fresh_cols and cols[-3:] == ["identity_mechanism", "envelope_version", "forecast"]   # W6's two follow W4's
    report = copy_rows({"lineage_records": src.conn}, fresh.conn, ["lineage_records"])
    assert report.tables["lineage_records"] == MANIFEST["lineage"]["count"] == 5
    ids = [r["lineage_id"] for r in fresh.conn.execute("SELECT lineage_id FROM lineage_records ORDER BY lineage_id")]
    assert len(ids) == 5 and all(fresh.verify_record(lid)["record_intact"] for lid in ids)
    assert all(fresh.get(lid).identity_mechanism is None for lid in ids)
    src.close(); fresh.close()
    # Planted: the column added ahead of W0b's (what a TEXT-loop ALTER does to this store).
    bad_dir = _copy(tmp_path / "bad")
    c = sqlite3.connect(bad_dir / "kb.db")
    c.execute("ALTER TABLE lineage_records ADD COLUMN identity_mechanism TEXT")
    c.commit(); c.close()
    bad = LineageStore(bad_dir / "kb.db")
    bad_cols = [col.name for col in bad.conn.columns("lineage_records")]
    target = _store(tmp_path, "fresh2")
    print(f"R2b ({sys.platform}) planted order: {bad_cols[25:]}")
    assert bad_cols != fresh_cols and sorted(bad_cols) == sorted(fresh_cols)
    with pytest.raises(CopyRefused, match="lineage_records"):
        copy_rows({"lineage_records": bad.conn}, target.conn, ["lineage_records"])
    assert target.conn.execute("SELECT COUNT(*) FROM lineage_records").fetchone()[0] == 0, "nothing written"
    bad.close(); target.close()


# ---------------------------------------------------------------------------
# R3 — tamper, both directions
# ---------------------------------------------------------------------------

def assert_both_tamper_directions_named(lineage: LineageStore, chain: NexusArchiveChain, intact) -> tuple[str, str]:
    """R3's assertion, over the check `intact(lineage_id) -> bool` under test: a
    sealed `proxy` record whose column is set to NULL, and a sealed asserted
    record whose column is set to `proxy`, are each no longer intact."""
    proxied = lineage.record_turn(**W4_FIXTURE_TURN, identity_mechanism="proxy")
    asserted = lineage.record_turn(**W4_FIXTURE_TURN)
    _seal(lineage, chain)
    assert intact(proxied.lineage_id) and intact(asserted.lineage_id), "both intact once sealed"
    lineage.conn.execute("UPDATE lineage_records SET identity_mechanism = NULL WHERE lineage_id = ?",
                         (proxied.lineage_id,))
    lineage.conn.commit()
    assert not intact(proxied.lineage_id), "a sealed proxy rewritten to NULL is not named"
    lineage.conn.execute("UPDATE lineage_records SET identity_mechanism = 'proxy' WHERE lineage_id = ?",
                         (asserted.lineage_id,))
    lineage.conn.commit()
    assert not intact(asserted.lineage_id), "proxy written into a sealed asserted record is not named"
    return proxied.lineage_id, asserted.lineage_id


def test_r3_both_tamper_directions_are_named(tmp_path, monkeypatch):
    """R3. Both tamper directions are named — by `verify_record`, and on the surface by
    `/lineage/verify` (the row layer names exactly the two, the verdict is not intact) and
    `/lineage/{id}/verify`: a sealed `proxy` record rewritten to NULL, and `proxy` written into
    a sealed asserted record's NULL column (the direction a check over non-NULL values only
    misses: R3c).

    Law 16: fails on the base only because `record_turn` takes no `identity_mechanism` (a
    TypeError: the new name, declared)."""
    svc, client = _services(tmp_path, monkeypatch)
    proxied, asserted = assert_both_tamper_directions_named(
        svc.lineage, svc.archive_chain, lambda lid: svc.lineage.verify_record(lid)["record_intact"])
    v = client.get("/lineage/verify", headers=H).json()
    print(f"\nR3 ({sys.platform}) /lineage/verify: verdict {v['verdict']}, row failures {v['layers']['row']['failures']}")
    assert set(v["layers"]["row"]["failures"]) == {proxied, asserted} and v["verdict"] != "intact"
    for lid in (proxied, asserted):
        one = client.get(f"/lineage/{lid}/verify", headers=H).json()
        assert one["layers"]["row"]["failures"] == [lid] and one["verdict"] != "intact"


def _non_null_only(lineage: LineageStore):
    """The plausible wrong check — over the sealed non-NULL values only: a row
    passes if it recomputes with the mechanism it holds or with none
    ("row-tolerant across the migration"). It names a sealed `proxy` taken
    away and cannot see one written in."""
    def intact(lid: str) -> bool:
        row = _row(lineage, lid)
        payload = _hash_payload_from_row(row)
        return (LineageStore.compute_record_hash(payload) == row["record_hash"]
                or LineageStore.compute_record_hash({**payload, "identity_mechanism": None}) == row["record_hash"])
    return intact


def test_r3c_companion_a_check_over_non_null_values_only_fails_r3(tmp_path):
    """R3c — COMPANION (OQ-16 rules 1 and 3). R3's assertion run over a check that the product's
    verifier is not, and that its source does not name: one that tolerates a record recomputing
    without the mechanism. R3 fails on it, at the second direction; the same assertion over
    `verify_record` holds on the same shape of store.

    Law 16: fails on the base only because `record_turn` takes no `identity_mechanism` (a
    TypeError: the new name, declared)."""
    lineage = _store(tmp_path, "tolerant")
    chain = NexusArchiveChain(tmp_path / "tolerant" / "archive.db")
    with pytest.raises(AssertionError, match="written into a sealed asserted record"):
        assert_both_tamper_directions_named(lineage, chain, _non_null_only(lineage))
    lineage.close(); chain.close()
    lineage = _store(tmp_path, "verifier")
    chain = NexusArchiveChain(tmp_path / "verifier" / "archive.db")
    assert_both_tamper_directions_named(lineage, chain, lambda lid: lineage.verify_record(lid)["record_intact"])
    lineage.close(); chain.close()


# ---------------------------------------------------------------------------
# R4 — a value outside the closed vocabulary
# ---------------------------------------------------------------------------

def _counts(lineage: LineageStore) -> dict:
    return {t: lineage.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("lineage_records", "lineage_injections", "inference_configs", "environments",
                      "request_bodies")}


@pytest.mark.parametrize("value", ["oauth", "verified", "PROXY", "Asserted", "proxy\x00", 7],
                         ids=["oauth", "verified", "PROXY", "Asserted", "proxy-nul", "int"])
def test_r4_a_mechanism_outside_the_vocabulary_raises_and_nothing_is_written(tmp_path, value):
    """R4. A value outside the closed vocabulary (`asserted` | `proxy`) raises at `record_turn`
    and the store holds nothing new: no record, no index row, no options or environment row, no
    kept request body — the turn passes all four, so a check placed after any write would leave
    one. A future member (`oauth`) is refused until it is added to the vocabulary; a case or a
    byte off a member is not a member. The store still writes a valid record afterwards.

    Law 16: fails on the base only because `record_turn` takes no `identity_mechanism` (a
    TypeError, not the ValueError the vocabulary raises: the new name, declared)."""
    lineage = _store(tmp_path, "r4")
    body = b'{"messages": []}'
    turn = {**W4_FIXTURE_TURN, "request_hash": hashlib.sha256(body).hexdigest()}
    before = _counts(lineage)
    with pytest.raises(ValueError, match=r"identity_mechanism .*asserted \| proxy"):
        lineage.record_turn(**turn, request_body=body, identity_mechanism=value)
    after = _counts(lineage)
    print(f"\nR4 ({sys.platform}) {value!r} refused; counts before {before} after {after}")
    assert after == before == {k: 0 for k in before}
    ok = lineage.record_turn(**turn, request_body=body, identity_mechanism="proxy")
    assert lineage.verify_record(ok.lineage_id)["record_intact"] and _counts(lineage)["request_bodies"] == 1
    lineage.close()


# ---------------------------------------------------------------------------
# R5 — under mcp_block the field is NULL, and a value is named
# ---------------------------------------------------------------------------

def test_r5_an_mcp_record_carries_no_mechanism(mcp_rt):
    """R5 (the record MCP writes). An MCP turn's record stores NULL (`asserted` in the dict: its
    owner is configuration, asserted, never authenticated), is consistent with its scope and
    intact, and the scope's declaration lists the field as undefined there.

    Law 16: fails on the base only because the record has no `identity_mechanism` attribute
    (the new name, declared)."""
    rt, srv, _root, _bid = mcp_rt
    lid = _mcp_turn(srv)
    rec = rt.services.lineage.get(lid)
    print(f"\nR5 ({sys.platform}) mcp record {lid[:8]}: identity_mechanism={rec.identity_mechanism!r}, "
          f"accountable_identity={rec.accountable_identity!r}, scope violations {verify_scope(rec)}")
    assert rec.record_scope == "mcp_block" and rec.accountable_identity == "alice"
    assert rec.identity_mechanism is None and _row(rt.services.lineage, lid)["identity_mechanism"] is None
    assert rec.to_dict()["identity_mechanism"] == "asserted"
    assert verify_scope(rec) == [] and rt.services.lineage.verify_record(lid)["record_intact"]
    assert "identity_mechanism" in scope_mod.declaration("mcp_block")["undefined"]


def test_r5_a_mechanism_on_an_mcp_block_record_is_named(mcp_rt):
    """R5. A mechanism on an `mcp_block` record is named by `verify_scope` — `proxy`, and members
    the rule's source does not name (`oauth`, `verified`): the rule is "not the default", not a
    list — while the default spelled out is not a violation. Written into the MCP record's NULL
    column, the consequence is on `/lineage/{id}`: the violation in `scope_declaration` and a
    broken row.

    Law 16: fails on the base on its consequence (`verify_scope` names nothing about identity)."""
    rt, srv, root, _bid = mcp_rt
    lid = _mcp_turn(srv)
    rec = rt.services.lineage.get(lid)
    attrs = {f.name: getattr(rec, f.name) for f in dataclasses.fields(rec)}
    for value in ("proxy", "oauth", "verified"):
        named = verify_scope(types.SimpleNamespace(**{**attrs, "identity_mechanism": value}))
        print(f"\nR5 ({sys.platform}) planted {value!r} on an mcp_block record: {named}")
        assert any(p.startswith("identity_mechanism is set on an mcp_block record") for p in named), (value, named)
    for default in ("asserted", None):
        assert not [p for p in verify_scope(types.SimpleNamespace(**{**attrs, "identity_mechanism": default}))
                    if "identity_mechanism" in p], default
    rt.services.lineage.conn.execute("UPDATE lineage_records SET identity_mechanism = 'proxy' WHERE lineage_id = ?",
                                     (lid,))
    rt.services.lineage.conn.commit()
    http = create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma-http", eval_log_path=root / "h.jsonl",
                           archive_db_path=root / "archive.db", snapshots_dir=root / "snapshots")
    page = TestClient(create_app(http)).get(f"/lineage/{lid}", headers=H).json()
    print(f"R5 ({sys.platform}) /lineage/{{id}} after the write: identity_mechanism={page['identity_mechanism']!r}, "
          f"violations {page['scope_declaration']['violations']}, intact {page['verify']['record_intact']}")
    assert page["identity_mechanism"] == "proxy"
    assert any(v.startswith("identity_mechanism is set") for v in page["scope_declaration"]["violations"])
    assert page["verify"]["record_intact"] is False


# ---------------------------------------------------------------------------
# R6 — the answered-for sets and the surface
# ---------------------------------------------------------------------------

def test_r6_the_column_is_answered_for_at_cid_31():
    """R6. The column arrives with a recorded reason: the lineage table's two-layer guard passes
    with exactly one `identity_mechanism` entry, cid 31 (the last), and its reason names W4.

    Law 16: fails on the base on its consequence — layer 2: the answered-for set names a
    column the base's DDL does not have."""
    entries = [e for e in ANSWERED_COLUMNS["lineage_records"] if e[1] == "identity_mechanism"]
    print(f"\nR6 ({sys.platform}) answered-for: {entries}")
    assert entries == [(31, "identity_mechanism", "TEXT", 0, None, 0)]
    assert max(e[0] for e in ANSWERED_COLUMNS["lineage_records"]) == 33      # 31 was the last at W4; W6 added 32 and 33
    check_table_layer2("lineage_records")
    from tests.test_scope_recorded import W4_REASON
    src = (ROOT / "tests" / "test_scope_recorded.py").read_text(encoding="utf-8").splitlines()
    (entry,) = [line for line in src if '(31, "identity_mechanism"' in line]
    assert W4_REASON.endswith("W4") and W4_REASON in entry, "the reason rides the entry"


def scope_table_disagreements(unchanged, changed, undefined) -> list[str]:
    """Where the scope module's three tables and `RECORD_CLAIMS` part: every
    `LineageRecord` field (derived by AST) must sit in exactly one table, the
    one `RECORD_CLAIMS` classifies it in, and neither may name a field the
    record does not have."""
    tables = {UNCHANGED: set(unchanged), CHANGED: set(changed), UNDEFINED: set(undefined)}
    fields = lineage_record_fields()
    problems = []
    for f in fields:
        homes = sorted(c for c, names in tables.items() if f in names)
        want = RECORD_CLAIMS.get(f, (None, ""))[0]
        if homes != [want]:
            problems.append(f"{f}: RECORD_CLAIMS says {want}, the scope tables say {homes or 'none'}")
    stale = set().union(*tables.values()) - set(fields)
    if stale:
        problems.append(f"the scope tables name fields the record does not have: {sorted(stale)}")
    stale_claims = set(RECORD_CLAIMS) - set(fields)
    if stale_claims:
        problems.append(f"RECORD_CLAIMS classifies fields the record does not have: {sorted(stale_claims)}")
    return problems


def assert_scope_tables_agree(unchanged, changed, undefined) -> None:
    problems = scope_table_disagreements(unchanged, changed, undefined)
    assert not problems, "the scope tables disagree with RECORD_CLAIMS:\n  " + "\n  ".join(problems)


def test_r6_the_field_is_classified_undefined_under_mcp_and_the_tables_agree():
    """R6. `RECORD_CLAIMS` classifies the field (undefined under MCP: no peer, an owner by
    configuration) and the field is sealed when non-default (the keys `compute_record_hash`
    seals only then, derived by AST). The scope module's three tables, which no test read
    before W4 (S1.A3), now agree with `RECORD_CLAIMS` on every field the record has.

    Law 16: fails on the base on its consequence (the record has no such field)."""
    fields = lineage_record_fields()
    print(f"\nR6 ({sys.platform}) {len(fields)} record fields; identity_mechanism "
          f"{RECORD_CLAIMS.get('identity_mechanism')}; hashed when non-default: {'identity_mechanism' in hashed_when_present()}")
    assert "identity_mechanism" in fields and RECORD_CLAIMS["identity_mechanism"][0] == UNDEFINED
    assert "identity_mechanism" in hashed_when_present()
    assert "identity_mechanism" in scope_mod.UNDEFINED_UNDER_MCP
    assert_scope_tables_agree(scope_mod.UNCHANGED_UNDER_MCP, scope_mod.CHANGED_UNDER_MCP,
                              scope_mod.UNDEFINED_UNDER_MCP)


def test_r6_companion_a_scope_table_that_disagrees_is_named():
    """COMPANION (OQ-16 rules 1 and 3). Planted, with fields the agreement rule's source does not
    name: `accountable_identity` placed in the undefined table as well as the unchanged one, and
    `request_hash` dropped from the changed one. The rule fails, and it names exactly those two:
    the tables as shipped add nothing to the list.

    Law 16: fails on the base on its consequence — a third problem is named there, because
    `RECORD_CLAIMS` classifies `identity_mechanism` and the base's record has no such field."""
    undefined = {**scope_mod.UNDEFINED_UNDER_MCP, "accountable_identity": "planted"}
    changed = {k: v for k, v in scope_mod.CHANGED_UNDER_MCP.items() if k != "request_hash"}
    with pytest.raises(AssertionError) as e:
        assert_scope_tables_agree(scope_mod.UNCHANGED_UNDER_MCP, changed, undefined)
    print(f"\nR6c ({sys.platform}) {e.value}")
    assert scope_table_disagreements(scope_mod.UNCHANGED_UNDER_MCP, changed, undefined) == [   # record order
        "request_hash: RECORD_CLAIMS says changed_in_meaning, the scope tables say none",
        "accountable_identity: RECORD_CLAIMS says unchanged, the scope tables say ['unchanged', 'undefined']"]


def test_r6_lineage_route_carries_the_field_unversioned(tmp_path, monkeypatch):
    """R6. `/lineage/{id}` (and `/lineage/recent`) carry the field through `to_dict()`, which
    names the default: a token-mode turn through `/chat` reads `asserted`, a record written
    `proxy` reads `proxy`, both intact; the surface stays unversioned (no `contract_version`),
    and no route code changed for it.

    Law 16: fails on the base on its consequence (the payload has no `identity_mechanism`)."""
    store, _bid = _plant(tmp_path / "app" / "kb.db")
    store.close()
    svc, client = _services(tmp_path, monkeypatch)
    done = _done_of(client.post("/chat", json={"message": "does creatine improve strength?"}, headers=H).text)
    page = client.get(f"/lineage/{done['lineage_id']}", headers=H).json()
    print(f"\nR6 ({sys.platform}) /lineage/{{id}} of a /chat turn: identity_mechanism={page.get('identity_mechanism')!r}; "
          f"versioned: {'contract_version' in page}")
    assert page["identity_mechanism"] == "asserted" and page["verify"]["record_intact"]
    assert "contract_version" not in page
    proxied = svc.lineage.record_turn(**W4_FIXTURE_TURN, identity_mechanism="proxy")
    page = client.get(f"/lineage/{proxied.lineage_id}", headers=H).json()
    assert page["identity_mechanism"] == "proxy" and page["verify"]["record_intact"]
    recent = {r["lineage_id"]: r["identity_mechanism"]
              for r in client.get("/lineage/recent", headers=H).json()["records"]}
    assert recent == {done["lineage_id"]: "asserted", proxied.lineage_id: "proxy"}


# ---------------------------------------------------------------------------
# R7 — recall, contract version 2
# ---------------------------------------------------------------------------

def test_r7_recall_rows_read_the_mechanism_at_contract_version_2(tmp_path, monkeypatch):
    """R7. `GET /belief/{id}/recall` is contract version 2 and its per-person declaration says
    what each label means; a record written `proxy` gives a row labelled `proxy`, one written
    without gives `asserted` — the label reads the record (version 1 printed `asserted` on every
    row).

    Law 16: fails on the base on its consequence (the route says contract version 1)."""
    svc, client = _services(tmp_path, monkeypatch)
    bid = svc.store.add(subject="creatine", relation="improves", object="strength", weight=0.9, tier=1,
                        granularity=Granularity.TOPIC, source="wikidata")
    empty = client.get(f"/belief/{bid}/recall", headers=H).json()
    print(f"\nR7 ({sys.platform}) route contract v{empty['contract_version']}; per_person {empty['per_person']}")
    assert empty["contract_version"] == 2
    decl = empty["per_person"]
    assert decl["declared"] is True and decl["field"] == "accountable"
    assert set(decl["labels"]) == {"asserted", "proxy"}
    assert "never authenticated" in decl["labels"]["asserted"]
    assert "NEXUS did not" in decl["labels"]["proxy"] and "proxy authenticated" in decl["labels"]["proxy"]
    injected = [{"belief_id": bid, "weight_at_injection": 0.5, "tier_at_injection": 1, "cited": True}]
    asserted = svc.lineage.record_turn(**{**W4_FIXTURE_TURN, "injected": injected})
    proxied = svc.lineage.record_turn(**{**W4_FIXTURE_TURN, "injected": injected,
                                         "accountable_identity": "tester-2@example.com"}, identity_mechanism="proxy")
    got = client.get(f"/belief/{bid}/recall", headers=H).json()
    labels = {r["lineage_id"]: (r["accountable"], r["identity_label"]) for r in got["records"]}
    print(f"R7 ({sys.platform}) rows: {labels}")
    assert labels == {asserted.lineage_id: ("tester-1@example.com", "asserted"),
                      proxied.lineage_id: ("tester-2@example.com", "proxy")}
    rows, total = svc.lineage.records_injecting(bid)
    assert total == 2 and {r["identity_label"] for r in rows} == {"asserted", "proxy"}


def test_r7_mcp_nexus_recall_is_the_same_contract(mcp_rt):
    """R7 (MCP). `nexus_recall` returns the recall contract too: version 2 and the declaration
    the HTTP route gives, over the same store, row for row; its own record is labelled
    `asserted` (an MCP record carries no mechanism: R5).

    Law 16: fails on the base on its consequence (the tool says contract version 1)."""
    rt, srv, root, bid = mcp_rt
    _mcp_turn(srv)
    res = json.loads(asyncio.run(srv.call_tool("nexus_recall", {"belief_id": bid})).model_dump()["content"][0]["text"])
    http = create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma-http", eval_log_path=root / "h.jsonl",
                           archive_db_path=root / "archive.db", snapshots_dir=root / "snapshots")
    route = TestClient(create_app(http)).get(f"/belief/{bid}/recall", headers=H).json()
    print(f"\nR7 ({sys.platform}) nexus_recall contract v{res['contract_version']}, route v{route['contract_version']}; "
          f"labels {[r['identity_label'] for r in res['records']]}")
    assert res["contract_version"] == route["contract_version"] == 2
    assert res["per_person"] == route["per_person"] and res["records"] == route["records"]
    assert [r["identity_label"] for r in res["records"]] == ["asserted"] and res["total"] == 1


def _section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    j = text.find(end, i + len(start))
    return text[i:j if j != -1 else len(text)]


def test_r7_the_documents_state_version_2_and_both_labels():
    """R7. Where version 1 was stated, version 2 is: CLAUDE.md's advice-recall paragraph, the
    integration reference's §4.3b (prose and example payload) in both its forms; each names
    `proxy` beside `asserted`, and none still says the label is always `asserted`. The ratchet's
    per-person classification of the route names both labels too.

    Law 16: fails on the base on its consequence (every document says version 1)."""
    from tests.test_bind_conditional_routes import PER_PERSON_ROWS
    claude = " ".join((ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8").split())
    m = re.search(r"GET /belief/\{belief_id\}/recall` \(paginated, oldest first, contract version (\d+)", claude)
    md = (ROOT / "docs" / "INTEGRATION_REFERENCE.md").read_text(encoding="utf-8")
    md_recall = _section(md, "### 4.3b", "\n### ")
    html = (ROOT / "docs" / "INTEGRATION_REFERENCE.html").read_text(encoding="utf-8")
    html_recall = _section(html, '<span class="path">/belief/{belief_id}/recall</span>', '<div class="endpoint">')
    print(f"\nR7 ({sys.platform}) docs/CONTRACTS.md states version {m.group(1) if m else None}")
    assert m and m.group(1) == "2"
    advice = _section(claude, "**Advice recall", "**Classification and the just-culture line.**")
    culture = _section(claude, "**Classification and the just-culture line.**", "Two derived guards hold the line")
    assert "`proxy`" in culture and "`asserted`" in culture
    assert "contract version 1" not in advice
    for name, text in (("INTEGRATION_REFERENCE.md", md_recall), ("INTEGRATION_REFERENCE.html", html_recall)):
        assert "contract_version: 2" in text and '"contract_version": 2' in text, name
        assert "contract_version: 1" not in text and '"contract_version": 1' not in text, name
        assert "proxy" in text and "always <code>asserted</code>" not in text and "always `asserted`" not in text, name
    assert "proxy" in PER_PERSON_ROWS["/belief/{belief_id}/recall"]


# ---------------------------------------------------------------------------
# L1 — the loop: a parameter, never the handle
# ---------------------------------------------------------------------------

def _turn(store: TripleStore, lineage: LineageStore, **kw) -> dict:
    frames = []

    async def drive():
        async for frame, _st in run_turn(NexusLoopState(), "does creatine improve strength?",
                                         retriever=TripleRetriever(store), provider=_Provider(),
                                         lineage=lineage, **kw):
            frames.append(frame)

    asyncio.run(drive())
    return next(json.loads(f.split("data: ", 1)[1].strip()) for f in reversed(frames)
                if f.startswith("event: done\n"))


def test_l1_the_loop_carries_the_mechanism_to_the_record_never_on_the_handle(tmp_path, monkeypatch):
    """L1 (the brief's step 2). `run_turn(identity_mechanism="proxy")` reaches the record through
    `complete_turn` as a parameter, as `record_scope` does: the record says `proxy` and verifies;
    a turn that names none records NULL. No `TurnHandle` field carries it (MCP-1's crossing pin
    in tests/test_mcp1_split.py holds unedited).

    Law 16: fails on the base only because `run_turn` takes no `identity_mechanism` (a
    TypeError: the new name, declared)."""
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    store, _bid = _plant(tmp_path / "kb.db")
    lineage = LineageStore(tmp_path / "kb.db")
    plain = lineage.get(_turn(store, lineage)["lineage_id"])
    proxied = lineage.get(_turn(store, lineage, identity_mechanism="proxy")["lineage_id"])
    handle_fields = [f.name for f in dataclasses.fields(TurnHandle)]
    print(f"\nL1 ({sys.platform}) records: default {plain.identity_mechanism!r}, proxy {proxied.identity_mechanism!r}; "
          f"handle fields naming identity: {[f for f in handle_fields if 'identity' in f or 'mechanism' in f]}")
    assert plain.identity_mechanism is None and proxied.identity_mechanism == "proxy"
    assert lineage.verify_record(plain.lineage_id)["record_intact"]
    assert lineage.verify_record(proxied.lineage_id)["record_intact"]
    assert not [f for f in handle_fields if "identity" in f or "mechanism" in f]
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# G1 — every record_turn call site states the mechanism (the AW guard's shape)
# ---------------------------------------------------------------------------

def record_turn_calls(sources: list[tuple[str, str]]) -> list[tuple[str, int, bool]]:
    """Every `<x>.record_turn(...)` call in `sources` ((relpath, text) pairs),
    and whether it passes `identity_mechanism=` by name."""
    out = []
    for rel, text in sources:
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "record_turn":
                out.append((rel, node.lineno, any(kw.arg == "identity_mechanism" for kw in node.keywords)))
    return out


def assert_every_record_turn_call_states_the_mechanism(sources: list[tuple[str, str]]) -> None:
    calls = record_turn_calls(sources)
    assert calls, "derivation found no record_turn call site"
    missing = [(f, ln) for f, ln, ok in calls if not ok]
    assert not missing, f"record_turn called without identity_mechanism= at {missing}"


def _tracked_sources() -> list[tuple[str, str]]:
    return [(p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8")) for p in tracked_backend()]


def test_g1_every_record_turn_call_site_states_the_identity_mechanism():
    """G1. Every `record_turn` call site in the tracked backend states the mechanism by name, so
    a writer added later decides how its record's identity was established rather than taking
    the default by omission (the AW `context` guard's and BA fix 2's `request_hash` guard's
    shape, derived by AST over `tracked_backend()`). The population is not empty: the loop's
    one call site is in it.

    Law 16: fails on the base on its consequence (the loop's call names no mechanism)."""
    calls = record_turn_calls(_tracked_sources())
    print(f"\nG1 ({sys.platform}) record_turn call sites: {calls}")
    assert any(f == "backend/loop/agentic_loop.py" for f, _ln, _ok in calls)
    assert_every_record_turn_call_states_the_mechanism(_tracked_sources())


def test_g1c_companion_a_writer_that_takes_the_default_by_omission_is_named():
    """G1c — COMPANION (OQ-16 rules 1 and 3). Planted beside the tracked backend, a module the
    guard's source does not name, with two writers: one that leaves the mechanism out on a
    receiver the guard never mentions, and one that passes it only inside a `**` splat, where
    nothing states it. The guard fails, naming both lines.

    Law 16: fails on the base on its consequence — the guard names a third line there, the
    loop's own call, which states no mechanism."""
    planted = textwrap.dedent('''\
        def write_audit(ledger, q):
            return ledger.records.record_turn(query=q, injected=[], contradictions=[], model_id="m",
                                              response_text="", prompt_prefix="", context={},
                                              request_hash=None)


        def write_through(ledger, **fields):
            return ledger.record_turn(**fields)
        ''')
    sources = _tracked_sources() + [("backend/kb/planted_writer.py", planted)]
    with pytest.raises(AssertionError) as e:
        assert_every_record_turn_call_states_the_mechanism(sources)
    print(f"\nG1c ({sys.platform}) {e.value}")
    assert [(f, ln) for f, ln, ok in record_turn_calls(sources) if not ok] == [
        ("backend/kb/planted_writer.py", 2), ("backend/kb/planted_writer.py", 8)]
