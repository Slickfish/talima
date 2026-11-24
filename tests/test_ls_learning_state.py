"""LS — the learning state goes into the lineage record.

Since 2026-10-01 the instance has been frozen (FZ-2): the record still
marks claims cited while `fire` writes nothing, and nothing in the record
said whether learning was on. One field, `learning_mode`, written only when
it is not `live` and sealed into the hash by the mechanism the Phase 1
attribution fields use (`compute_record_hash`: a key enters the body only
when non-default; `_hash_payload_from_row` reads it row-tolerantly so every
verifier sees the same body). Live records are byte-identical to what the
pre-LS code wrote.

T1   a frozen turn's record says so, inside its seal
T2   a live turn's record hashes exactly as the pre-LS tree hashed it
T3   a store on the pre-LS schema migrates and every record verifies
T4   rewriting the mode on a sealed frozen record is named
T4c  writing a mode INTO a sealed live record's NULL column is named
T5   the mode recorded is the mode at write time, not at turn start
T6   the schema guard answers for the column, with its reason
T7   /lineage/{id} carries the field (unversioned surface; S1.4)
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import run_turn
from backend.loop.state import NexusLoopState
from backend.memory import lineage as lineage_mod
from backend.memory.chain import NexusArchiveChain
from backend.memory.lineage import LineageStore, _hash_payload_from_row, verify_sealed_lineage
from backend.server import create_app, create_services
from tests.test_scope_recorded import ANSWERED_COLUMNS, LS_REASON, check_table_layer2

TOKEN = "ls-token"
H = {"Authorization": f"Bearer {TOKEN}"}

# T2 — the hash the PRE-LS tree (main at c2837c1, the AS-1 merge) wrote for
# this exact fixture turn, computed there and committed here as a literal.
PRE_LS_HASH = "811059ffe984a866e753387e9d9fc9589734a4ef3bcee45dadb883f75a777519"
FIXED_TS = 1_760_000_000.0
FIXED_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
FIXTURE_TURN = dict(
    query="fixture question",
    injected=[{"belief_id": 1, "weight_at_injection": 0.7, "tier_at_injection": 1, "cited": True}],
    contradictions=[], model_id="fixture-model", response_text="fixture answer", prompt_prefix="P",
    scope="default", citation_test="f5.2-informative-no_substrate", request_hash="r" * 64,
    work_unit_id="WU-1", accountable_identity="alice")


# ---------------------------------------------------------------------------
# a stub turn
# ---------------------------------------------------------------------------

ANSWER = "Creatine improves strength and caffeine improves endurance."


class _Provider:
    """The answer, then an empty extraction. `on_answer` runs between turn
    start and the lineage write (T5's seam)."""

    def __init__(self, on_answer=None):
        self.calls, self.on_answer = 0, on_answer

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        if self.calls % 2 == 1:
            if self.on_answer:
                self.on_answer()
            for tok in ANSWER.split(" "):
                yield tok + " "
        else:
            yield "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _plant(tmp_path) -> TripleStore:
    store = TripleStore(tmp_path / "kb.db")
    for s, r, o in (("creatine", "improves", "strength"), ("caffeine", "improves", "endurance")):
        store.add(subject=s, relation=r, object=o, weight=0.9, tier=1,
                  granularity=Granularity.TOPIC, source="wikidata")
    return store


def _turn(store, lineage, provider=None) -> dict:
    frames = []

    async def drive():
        async for frame, _st in run_turn(NexusLoopState(), "does creatine improve strength?",
                                         retriever=TripleRetriever(store), provider=provider or _Provider(),
                                         lineage=lineage):
            frames.append(frame)

    asyncio.run(drive())
    return next(json.loads(f.split("data: ", 1)[1].strip()) for f in reversed(frames)
                if f.startswith("event: done\n"))


def _seal(lineage: LineageStore, chain: NexusArchiveChain) -> int:
    recs = lineage.recent(limit=100)
    hashes = [r.record_hash for r in recs]
    block = chain.append({"kind": "governance", "lineage_seal": {
        "count": len(hashes), "digest": LineageStore.seal_digest(hashes), "record_hashes": hashes}},
        "", kind="governance")
    lineage.mark_sealed([r.lineage_id for r in recs], block.index)
    return block.index


def _row(lineage: LineageStore, lineage_id: str) -> sqlite3.Row:
    return lineage.conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (lineage_id,)).fetchone()


# ---------------------------------------------------------------------------
# T1 / T2 — the field, sealed when frozen, absent when live
# ---------------------------------------------------------------------------

def test_frozen_turn_record_carries_mode(tmp_path, monkeypatch):
    """A record written while learning is frozen says so, inside its seal."""
    monkeypatch.setenv("NEXUS_LEARNING", "frozen")
    store, lineage = _plant(tmp_path), LineageStore(tmp_path / "kb.db")
    done = _turn(store, lineage)
    rec = lineage.get(done["lineage_id"])
    assert rec.learning_mode == "frozen" and done["learning"]["state"] == "frozen"
    assert done["reinforcement"]["cited"] >= 1, "non-vacuity: the record marks a claim cited while fire wrote nothing"
    payload = _hash_payload_from_row(_row(lineage, rec.lineage_id))
    with_field = LineageStore.compute_record_hash(payload)
    without = LineageStore.compute_record_hash({**payload, "learning_mode": None})
    print(f"\nT1 ({sys.platform}) frozen record {rec.lineage_id[:8]}: stored {rec.record_hash[:16]}… "
          f"recomputed with the field {with_field[:16]}… without {without[:16]}…")
    assert with_field == rec.record_hash and without != rec.record_hash
    assert _row(lineage, rec.lineage_id)["learning_mode"] == "frozen"
    store.close(); lineage.close()


def test_live_turn_record_unchanged(tmp_path):
    """A live turn's record is byte-identical to what the pre-LS code wrote: the same fixture
    turn, written here, hashes to the literal computed on the pre-LS tree."""
    lineage = LineageStore(tmp_path / "kb.db")
    with mock.patch("backend.memory.lineage.time.time", return_value=FIXED_TS), \
            mock.patch("backend.memory.lineage.uuid.uuid4", return_value=FIXED_ID):
        rec = lineage.record_turn(**FIXTURE_TURN)
    print(f"\nT2 committed pre-LS hash {PRE_LS_HASH[:16]}… recomputed now {rec.record_hash[:16]}…")
    assert rec.record_hash == PRE_LS_HASH
    assert rec.learning_mode is None and _row(lineage, rec.lineage_id)["learning_mode"] is None
    # An explicit `live` is the default too: stored NULL, same hash.
    with mock.patch("backend.memory.lineage.time.time", return_value=FIXED_TS), \
            mock.patch("backend.memory.lineage.uuid.uuid4", return_value=uuid.UUID(int=2)):
        live = lineage.record_turn(**FIXTURE_TURN, learning_mode="live")
    assert _row(lineage, live.lineage_id)["learning_mode"] is None
    assert lineage.verify_record(live.lineage_id)["record_intact"]
    lineage.close()


# ---------------------------------------------------------------------------
# T3 — migration
# ---------------------------------------------------------------------------

PRE_LS_INSERT = (
    "INSERT INTO lineage_records (lineage_id, ts, query_hash, injected_json, contradictions_json, "
    "model_id, prompt_prefix_hash, response_hash, record_hash, sealed_block, scope, citation_test, "
    "context_json, request_hash, work_unit_id, accountable_identity, boundary_asserted, "
    "boundary_asserted_by, attribution_flags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


def _pre_ls_ddl() -> str:
    """The lineage DDL as it was before LS: the current one minus the column."""
    ddl = lineage_mod.SCHEMA_SQL.replace("    attribution_flags   TEXT,\n", "    attribution_flags   TEXT\n")
    head, _sep, tail = ddl.partition("    -- LS: the learning state")
    tail = tail[tail.index(");"):]
    out = head + tail
    assert "learning_mode" not in out
    return out


@pytest.mark.sqlite_only("builds a pre-LS store by hand with sqlite3 and migrates it")
def test_existing_records_verify_after_migration(tmp_path):
    """Every record written before LS recomputes and verifies unchanged: a store built on the
    pre-LS schema, with rows written by the pre-LS INSERT and hash, is migrated at open and every
    record reads intact at the row layer and the seal layer."""
    kb = tmp_path / "kb.db"
    conn = sqlite3.connect(kb)
    conn.executescript(_pre_ls_ddl())
    before = {}
    for i in range(6):
        payload = {"lineage_id": str(uuid.UUID(int=100 + i)), "ts": FIXED_TS + i, "query_hash": "q" * 64,
                   "injected": [{"belief_id": i, "weight_at_injection": 0.5, "tier_at_injection": 1, "cited": i % 2 == 0}],
                   "contradictions": [], "model_id": "m", "prompt_prefix_hash": "p" * 64,
                   "response_hash": "r" * 64, "scope": "default" if i % 3 else "trial",
                   "request_hash": "h" * 64 if i % 2 else None,
                   "work_unit_id": "WU-A" if i > 2 else None, "accountable_identity": None,
                   "boundary_asserted": None, "boundary_asserted_by": None}
        h = LineageStore.compute_record_hash(payload)
        conn.execute(PRE_LS_INSERT, (payload["lineage_id"], payload["ts"], payload["query_hash"],
                                     json.dumps(payload["injected"], sort_keys=True), "[]", "m", "p" * 64, "r" * 64,
                                     h, payload["scope"], None, None, payload["request_hash"],
                                     payload["work_unit_id"], None, None, None, None))
        before[payload["lineage_id"]] = h
    conn.commit()
    assert "learning_mode" not in {r[1] for r in conn.execute("PRAGMA table_info(lineage_records)")}
    conn.close()
    lineage = LineageStore(kb)                       # migrates at open
    cols = {r["name"] for r in lineage.conn.execute("PRAGMA table_info(lineage_records)")}
    assert "learning_mode" in cols
    after = {r["lineage_id"]: r["record_hash"] for r in lineage.conn.execute("SELECT lineage_id, record_hash FROM lineage_records")}
    assert after == before, "migration rewrote a stored hash"
    intact = [lid for lid in before if lineage.verify_record(lid)["record_intact"]]
    assert all(lineage.get(lid).learning_mode is None for lid in before)
    chain = NexusArchiveChain(tmp_path / "archive.db")
    _seal(lineage, chain)
    v = verify_sealed_lineage(lineage, chain)
    print(f"\nT3 ({sys.platform}) migrated store: {len(intact)} of {len(before)} records intact; sealed verdict {v['verdict']}")
    assert len(intact) == len(before) == 6 and v["verdict"] == "intact"
    lineage.close(); chain.close()


# ---------------------------------------------------------------------------
# T4 / T4c — tamper in both directions
# ---------------------------------------------------------------------------

def _sealed_pair(tmp_path):
    lineage = LineageStore(tmp_path / "kb.db")
    frozen = lineage.record_turn(**FIXTURE_TURN, learning_mode="frozen")
    live = lineage.record_turn(**FIXTURE_TURN)
    chain = NexusArchiveChain(tmp_path / "archive.db")
    _seal(lineage, chain)
    assert verify_sealed_lineage(lineage, chain)["verdict"] == "intact"
    return lineage, chain, frozen, live


def test_mode_tamper_breaks_seal(tmp_path):
    """Editing the learning mode on a sealed record is detected."""
    lineage, chain, frozen, _live = _sealed_pair(tmp_path)
    lineage.conn.execute("UPDATE lineage_records SET learning_mode = 'live' WHERE lineage_id = ?", (frozen.lineage_id,))
    lineage.conn.commit()
    v = verify_sealed_lineage(lineage, chain)
    print(f"\nT4 frozen record rewritten to live: verdict {v['verdict']}, row failures {v['layers']['row']['failures']}")
    assert v["verdict"] != "intact" and v["layers"]["row"]["failures"] == [frozen.lineage_id]
    assert lineage.verify_record(frozen.lineage_id)["record_intact"] is False
    lineage.close(); chain.close()


def test_mode_insert_on_live_record_breaks_seal(tmp_path):
    """Adding a mode to a live record after the seal is detected too — the direction a check
    over non-NULL values only would miss: the sealed body had no field, the row now has one."""
    lineage, chain, _frozen, live = _sealed_pair(tmp_path)
    assert _row(lineage, live.lineage_id)["learning_mode"] is None
    lineage.conn.execute("UPDATE lineage_records SET learning_mode = 'frozen' WHERE lineage_id = ?", (live.lineage_id,))
    lineage.conn.commit()
    v = verify_sealed_lineage(lineage, chain)
    print(f"\nT4c frozen written into a live record's NULL column: verdict {v['verdict']}, row failures {v['layers']['row']['failures']}")
    assert v["verdict"] != "intact" and v["layers"]["row"]["failures"] == [live.lineage_id]
    assert lineage.verify_record(live.lineage_id)["record_intact"] is False
    lineage.close(); chain.close()


# ---------------------------------------------------------------------------
# T5 — read at write time
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("start, flip_to, expected", [("live", "frozen", "frozen"), ("frozen", "live", None)])
def test_mode_read_at_write_time(tmp_path, monkeypatch, start, flip_to, expected):
    """The mode recorded is the mode when the record was written: the switch is flipped between
    turn start and the lineage write (inside the model call), and the record carries the
    write-time value. `learning_declaration()` reads the environment at each call (S1.2)."""
    monkeypatch.setenv("NEXUS_LEARNING", start)
    store, lineage = _plant(tmp_path), LineageStore(tmp_path / "kb.db")
    done = _turn(store, lineage, _Provider(on_answer=lambda: os.environ.__setitem__("NEXUS_LEARNING", flip_to)))
    rec = lineage.get(done["lineage_id"])
    print(f"\nT5 started {start}, flipped to {flip_to} during the model call: record learning_mode={rec.learning_mode!r}, "
          f"done.learning.state={done['learning']['state']}")
    assert rec.learning_mode == expected and done["learning"]["state"] == flip_to
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# T6 / T7 — the schema guard; the surface
# ---------------------------------------------------------------------------

def test_schema_guard_answers_for_learning_mode():
    """The new column arrives with a recorded reason: the two-layer lineage guard passes with
    exactly one `learning_mode` entry, and the reason is the LS text."""
    check_table_layer2("lineage_records")
    entries = [e for e in ANSWERED_COLUMNS["lineage_records"] if e[1] == "learning_mode"]
    assert entries == [(19, "learning_mode", "TEXT", 0, None, 0)]
    assert LS_REASON == "learning state at write; sealed when non-default; LS"
    src = (Path(__file__).resolve().parent / "test_scope_recorded.py").read_text(encoding="utf-8")
    assert LS_REASON in src


def test_lineage_route_shape(tmp_path, monkeypatch):
    """S1.4 decided yes: `/lineage/{id}` returns the record's fields through `to_dict()` and is
    not a pinned, versioned shape (no `contract_version`), so the field rides it with no bump;
    AR's recall rows (pinned at version 1) and the AW re-derivation path do not carry it."""
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    store = _plant(tmp_path); store.close()
    svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl", archive_db_path=tmp_path / "archive.db",
                          snapshots_dir=tmp_path / "snap", provider=_Provider())
    client = TestClient(create_app(svc))
    monkeypatch.setenv("NEXUS_LEARNING", "frozen")
    frozen = client.post("/chat", json={"message": "does creatine improve strength?"}, headers=H).text
    monkeypatch.setenv("NEXUS_LEARNING", "live")
    live = client.post("/chat", json={"message": "does caffeine improve endurance?"}, headers=H).text
    ids = [json.loads(f.split("data: ", 1)[1])["lineage_id"] for text in (frozen, live)
           for f in text.split("\n\n") if f.startswith("event: done")]
    got_frozen = client.get(f"/lineage/{ids[0]}", headers=H).json()
    got_live = client.get(f"/lineage/{ids[1]}", headers=H).json()
    print(f"\nT7 /lineage/{{id}}: frozen record learning_mode={got_frozen['learning_mode']!r}, live {got_live['learning_mode']!r}; "
          f"versioned: {'contract_version' in got_frozen}")
    assert got_frozen["learning_mode"] == "frozen" and got_live["learning_mode"] is None
    assert "contract_version" not in got_frozen
    assert got_frozen["verify"]["record_intact"] and got_live["verify"]["record_intact"]
    from tests.test_advice_recall import RECALL_ROW_KEYS_V1
    assert "learning_mode" not in RECALL_ROW_KEYS_V1
