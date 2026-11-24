"""W8 part 3b — each record's salted commitments, and its leaf in its block's root (run/specs/W8.md, D4, D5).

WHAT IS HELD HERE
    R1  A record written now has one stored salt per field of its hashed body and a commitment root, written in the
        record's own transaction; the root recomputes from the body and the salts, and every field opens.
    R2  The salts are kept beside the record, never in it: the record's hash is the same function of the same body
        it always was, and the record verifies.
    R3  A record written before W8 gets its salts and its root when the store is opened (a derived write, counted),
        and its row is untouched.
    R4  A seal now carries each record's leaf, `record_hash || commitment_root`, in the seal's order, and the
        block's lineage root is RFC 6962's root over those leaves.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from backend.memory import commitments as cm
from backend.memory import merkle
from backend.memory.chain import seal_leaves
from backend.memory.lineage import LineageStore, _hash_payload_from_row
from backend.server import create_app, create_services
from tests._tracked import REPO
from tests.fixtures.ce_rig import RigClock

import sys
sys.path.insert(0, str(REPO / "scripts"))
import history_shape_harness as hsh  # noqa: E402

QUESTIONS = ("what does interval training improve?", "and what does sleep improve?")


def _console(root: Path):
    hsh._build_store(root / "kb.db").close()
    svc = create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma", eval_log_path=root / "metrics.jsonl",
                          archive_db_path=root / "archive.db", snapshots_dir=root / "snapshots",
                          provider=hsh._Stub(plant=None))
    return svc, TestClient(create_app(svc)), {"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"}


def _turn(client, bearer, message: str) -> str:
    resp = client.post("/chat", headers=bearer, json={"message": message})
    assert resp.status_code == 200, resp.text
    done = [f for f in resp.text.split("\n\n") if f.startswith("event: done")]
    return json.loads(done[-1].split("\ndata: ", 1)[1])["lineage_id"]


def _row(svc, lineage_id: str):
    return svc.lineage.conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (lineage_id,)).fetchone()


def test_r1_a_record_written_now_has_its_salts_and_its_root_and_every_field_opens(tmp_path):
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        lineage_id = _turn(client, bearer, QUESTIONS[0])
    body = LineageStore.hashed_body(_hash_payload_from_row(_row(svc, lineage_id)))
    salts = svc.lineage.salts(lineage_id)
    assert set(salts) == set(body) and all(len(s) == cm.SALT_BYTES for s in salts.values())
    made = cm.commit(body, salts=salts)
    assert svc.lineage.commitment_root(lineage_id) == made.root.hex()
    for name, value in body.items():
        assert cm.open_field(name, value, salts[name], made.commitments) is None, name


def test_r2_the_salts_are_beside_the_record_never_in_it(tmp_path):
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        lineage_id = _turn(client, bearer, QUESTIONS[0])
    row = _row(svc, lineage_id)
    assert LineageStore.compute_record_hash(_hash_payload_from_row(row)) == row["record_hash"]
    assert svc.lineage.verify_record(lineage_id)["record_intact"]
    assert not any("salt" in k or "commitment" in k for k in row.keys())


def test_r3_a_record_written_before_w8_gets_its_salts_and_root_when_the_store_opens(tmp_path):
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        lineage_ids = [_turn(client, bearer, q) for q in QUESTIONS]
    before = {i: dict(_row(svc, i)) for i in lineage_ids}
    svc.lineage.conn.execute("DELETE FROM record_salts WHERE lineage_id = ?", (lineage_ids[0],))
    svc.lineage.conn.execute("DELETE FROM record_commitments WHERE lineage_id = ?", (lineage_ids[0],))
    svc.lineage.conn.commit()
    reopened = LineageStore(tmp_path / "kb.db")
    assert reopened.commitments_backfilled == 1
    assert reopened.commitment_root(lineage_ids[0]) is not None
    assert {i: dict(reopened.conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (i,)).fetchone())
            for i in lineage_ids} == before, "the records' rows are untouched"
    assert LineageStore(tmp_path / "kb.db").commitments_backfilled == 0, "idempotent"


def test_r4_a_seal_carries_each_records_leaf_and_the_root_is_over_them(tmp_path):
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        lineage_ids = [_turn(client, bearer, q) for q in QUESTIONS]
        block = asyncio.run(svc.kairos.maybe_seal_governance(force=True))
    seal = block.fingerprint["lineage_seal"]
    expected = sorted((_row(svc, i)["record_hash"], svc.lineage.commitment_root(i)) for i in lineage_ids)
    assert seal["record_hashes"] == [h for h, _r in expected]
    assert seal["leaves"] == [h + r for h, r in expected]
    assert block.roots["lineage"] == {"root": merkle.root(seal_leaves(seal)).hex(), "size": len(lineage_ids)}
    assert svc.archive_chain.verify_chain().valid
