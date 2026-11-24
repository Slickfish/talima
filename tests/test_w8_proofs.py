"""W8 part 4 — a record's inclusion proof, and the verification that reads it (run/specs/W8.md, D7, D8; exit 2, 5).

WHAT IS HELD HERE
    P1  A proof for a record sealed now (a layout-2 block) verifies with `verify_proof` from the proof alone: the
        header's hash is the block's, the root and the size are the header's, the leaf is the record's hash and
        commitment root, and the path leads to the root.
    P2  Each part of such a proof altered is refused by name: a path element, the index, the size, the leaf, the
        record hash, a header field, the block hash.
    P3  A proof for a record sealed before W8 (a layout-1 block) carries that block's whole body, because its hash
        covers the list and not a root; it verifies, and its list altered is named.
    P4  The proof route is gated, and returns the proof `build_proof` builds; an unsealed record says so.
    P5  `verify_sealed_lineage` reads every sealed record's proof (a fourth layer), and reads block to row: a sealed
        record rewritten with a recomputed hash and its seal cleared is named (BACKLOG W6-B6).
    P6  A backup taken across a seal of a record is copied again (W6's consistent pair), so R4 passes and the
        restored store's own lineage check, the fourth layer's block-to-row read included, is intact.
"""
from __future__ import annotations

import asyncio
import hashlib
import copy
import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.memory import merkle, proofs
from backend.memory.chain import NexusArchiveChain
from backend.memory.lineage import LineageStore, _hash_payload_from_row, verify_sealed_lineage
from backend.server import create_app, create_services
from tests._tracked import REPO
from tests.fixtures.ce_rig import RigClock

import sys
sys.path.insert(0, str(REPO / "scripts"))
import history_shape_harness as hsh  # noqa: E402

PRE_RUN = REPO / "tests" / "fixtures" / "pre_run_data"
QUESTIONS = ("what does interval training improve?", "and what does sleep improve?", "does caffeine help?")


def _console(root: Path):
    hsh._build_store(root / "kb.db").close()
    svc = create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma", eval_log_path=root / "metrics.jsonl",
                          archive_db_path=root / "archive.db", snapshots_dir=root / "snapshots",
                          provider=hsh._Stub(plant=None))
    return svc, TestClient(create_app(svc)), {"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"}


def _turn(client, bearer, message: str) -> str:
    resp = client.post("/chat", headers=bearer, json={"message": message})
    done = [f for f in resp.text.split("\n\n") if f.startswith("event: done")]
    return json.loads(done[-1].split("\ndata: ", 1)[1])["lineage_id"]


def _sealed(tmp_path):
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        ids = [_turn(client, bearer, q) for q in QUESTIONS]
        asyncio.run(svc.kairos.maybe_seal_governance(force=True))
    return svc, client, bearer, ids


def test_p1_a_proof_for_a_record_sealed_now_verifies_from_itself_alone(tmp_path):
    svc, _client, _bearer, ids = _sealed(tmp_path)
    for lineage_id in ids:
        proof = proofs.build_proof(svc.lineage, svc.archive_chain, lineage_id)
        assert proof["state"] == "sealed" and proof["block"]["layout"] == 2
        assert proof["leaf"] == proof["record_hash"] + proof["commitment_root"]
        assert proofs.verify_proof(json.loads(json.dumps(proof))) is None, lineage_id


def test_p2_each_part_of_a_proof_altered_is_refused_by_name(tmp_path):
    svc, _client, _bearer, ids = _sealed(tmp_path)
    proof = proofs.build_proof(svc.lineage, svc.archive_chain, ids[1])
    assert proof["path"], "the population: a tree of three has a path"

    def altered(change) -> dict:
        p = copy.deepcopy(proof)
        change(p)
        return p

    flip = lambda h: ("0" if h[0] != "0" else "1") + h[1:]
    cases = {
        "a path element": lambda p: p["path"].__setitem__(0, flip(p["path"][0])),
        "the index": lambda p: p.__setitem__("leaf_index", (p["leaf_index"] + 1) % p["tree_size"]),
        "the size": lambda p: p.__setitem__("tree_size", p["tree_size"] + 1),
        "the leaf": lambda p: p.__setitem__("leaf", flip(p["leaf"])),
        "the record hash": lambda p: p.__setitem__("record_hash", flip(p["record_hash"])),
        "a header field": lambda p: p["block"]["header"].__setitem__("timestamp", p["block"]["header"]["timestamp"] + 1),
        "the header's root": lambda p: p["block"]["header"]["roots"]["lineage"].__setitem__(
            "root", flip(p["block"]["header"]["roots"]["lineage"]["root"])),
        "the block hash": lambda p: p["block"].__setitem__("block_hash", flip(p["block"]["block_hash"])),
    }
    for label, change in cases.items():
        refusal = proofs.verify_proof(altered(change))
        assert isinstance(refusal, str) and refusal.split(":")[0] in proofs.REFUSALS, (label, refusal)


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; on Postgres its bind, prove and tombstone path runs through the migration script in tests/test_w8_rebinding.py R3")
def test_p3_a_proof_for_a_record_sealed_before_w8_carries_its_blocks_body(tmp_path):
    for name in ("kb.db", "archive.db"):
        shutil.copy(PRE_RUN / name, tmp_path / name)
    lineage = LineageStore(tmp_path / "kb.db")
    chain = NexusArchiveChain(tmp_path / "archive.db")
    sealed = [r["lineage_id"] for r in lineage.conn.execute(
        "SELECT lineage_id FROM lineage_records WHERE sealed_block IS NOT NULL")]
    assert sealed, "the population: the pre-run fixture holds records sealed in layout-1 blocks"
    proof = proofs.build_proof(lineage, chain, sealed[0])
    assert proof["block"]["layout"] == 1 and "body" in proof["block"]
    assert proof["leaf"] == proof["record_hash"], "a record sealed before W8 is its hash in its block's list"
    assert proofs.verify_proof(json.loads(json.dumps(proof))) is None
    forged = copy.deepcopy(proof)
    forged["block"]["body"]["fingerprint"]["lineage_seal"]["record_hashes"][0] = "0" * 64
    assert proofs.verify_proof(forged).split(":")[0] in proofs.REFUSALS


def test_p4_the_proof_route_is_gated_and_says_when_a_record_is_unsealed(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path)
    assert client.get(f"/lineage/{ids[0]}/proof", headers={"Authorization": ""}).status_code == 401
    body = client.get(f"/lineage/{ids[0]}/proof", headers=bearer).json()
    assert body == json.loads(json.dumps(proofs.build_proof(svc.lineage, svc.archive_chain, ids[0])))
    with RigClock().installed():
        later = _turn(client, bearer, "one more question")
    assert client.get(f"/lineage/{later}/proof", headers=bearer).json()["state"] == "unsealed"
    assert client.get("/lineage/no-such-record/proof", headers=bearer).status_code == 404


def test_p5_the_verifier_reads_every_proof_and_names_a_sealed_record_rewritten_and_unsealed(tmp_path):
    svc, _client, _bearer, ids = _sealed(tmp_path)
    verdict = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    assert verdict["proofs"] == {"checked": 3, "failed": 0, "failures": [], "bound": 0, "unbound": 0}
    assert verdict["verdict"] == "intact"
    row = svc.lineage.conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (ids[0],)).fetchone()
    payload = {**_hash_payload_from_row(row), "model_id": "forged:model"}
    svc.lineage.conn.execute("UPDATE lineage_records SET model_id = ?, record_hash = ?, sealed_block = NULL "
                             "WHERE lineage_id = ?", ("forged:model", LineageStore.compute_record_hash(payload), ids[0]))
    svc.lineage.conn.commit()
    verdict = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    assert verdict["layers"]["row"]["failed"] == 0 and verdict["sealed_without_row"] == 1
    assert verdict["verdict"] != "intact", verdict["verdict"]


@pytest.mark.sqlite_only("scripts/backup.py copies the SQLite files; a hosted backup is one pg_dump snapshot")
def test_p6_a_backup_taken_across_a_seal_of_a_record_restores_and_verifies(tmp_path, monkeypatch):
    from scripts import backup
    from tests import _proc
    root = tmp_path / "data"
    root.mkdir()
    svc, client, bearer, _ids = _sealed(root)
    real, fired = backup.copy_database, []

    def straddle(src, dst):
        took = real(src, dst)
        if Path(src).name == "kb.db" and not fired:     # once: a turn recorded and sealed between the two copies
            fired.append(1)
            _turn(client, bearer, "one more question")
            asyncio.run(svc.kairos.maybe_seal_governance(force=True))
        return took

    monkeypatch.setattr(backup, "copy_database", straddle)
    folder, manifest, failures = backup.take_backup(root, tmp_path / "backups")
    assert failures == [] and manifest["pair_attempts"] == 2, (failures, manifest.get("pair_attempts"))
    proc = _proc.run([sys.executable, str(REPO / "scripts" / "restore_verify.py"), str(folder), "--into",
                      str(tmp_path / "scratch")], cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                     errors="replace", timeout=300, env={**os.environ, "NEXUS_ENV_ROOT": str(REPO / "tests" / "fixtures")})
    assert proc.returncode == 0, proc.stdout[-2000:]
    restored = tmp_path / "restored"
    restored.mkdir()
    for name in ("kb.db", "archive.db"):
        shutil.copy(folder / name, restored / name)
    live = verify_sealed_lineage(LineageStore(restored / "kb.db"), NexusArchiveChain(restored / "archive.db"))
    assert live["verdict"] == "intact" and live["sealed_without_row"] == 0, live


def test_p7_a_records_root_rewritten_in_the_store_is_named_by_its_proof(tmp_path):
    """W8's verifier, MF1: the proof is built from the store's commitment root, never from the sealed leaf's
    suffix, so a root (and its field commitments) rewritten in the store no longer matches what the seal sealed."""
    svc, _client, _bearer, ids = _sealed(tmp_path)
    assert verify_sealed_lineage(svc.lineage, svc.archive_chain)["verdict"] == "intact"
    svc.lineage.conn.execute("UPDATE record_commitments SET commitment_root = ? WHERE lineage_id = ?",
                             ("ab" * 32, ids[0]))
    svc.lineage.conn.commit()
    proof = proofs.build_proof(svc.lineage, svc.archive_chain, ids[0])
    assert proof["state"] == "not_in_block", proof["state"]
    verdict = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    # Named by its proof and, since W8's second delta check, by its block's leaf too: the set, by design.
    named = {f["lineage_id"] for f in verdict["proofs"]["failures"]}
    assert named == {ids[0]} and verdict["verdict"] != "intact", verdict["proofs"]["failures"]
    assert {f["refusal"].split(":", 1)[0] for f in verdict["proofs"]["failures"]} >= {"leaf_mismatch"}


def test_p8_a_record_hash_that_is_a_prefix_of_the_leaf_is_refused():
    """W8's verifier, should-fix: a layout-2 proof's record hash is 64 hex characters and the leaf is exactly the
    record hash and its commitment root; an empty or shortened record hash proves nothing."""
    leaves = [bytes.fromhex(("%02x" % i) * 64) for i in range(3)]
    root = merkle.root(leaves)
    header = {"index": 0, "roots": {"lineage": {"root": root.hex(), "size": 3}}}
    block_hash = hashlib.sha256(json.dumps(header, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    leaf = leaves[1].hex()
    for record_hash in ("", leaf[:10], leaf[:100]):
        proof = {"state": "sealed", "record_hash": record_hash, "commitment_root": None, "leaf": leaf,
                 "leaf_index": 1, "tree_size": 3, "path": [p.hex() for p in merkle.audit_path(leaves, 1)],
                 "block": {"layout": 2, "index": 0, "block_hash": block_hash, "header": header}}
        refusal = proofs.verify_proof(proof)
        assert isinstance(refusal, str) and refusal.split(":")[0] in proofs.REFUSALS, (len(record_hash), refusal)


def test_p9_a_proof_whose_block_index_is_not_its_headers_is_refused(tmp_path):
    """W8's verifier, P1: the block's index is the header's, or the proof could name another block."""
    svc, _client, _bearer, ids = _sealed(tmp_path)
    proof = json.loads(json.dumps(proofs.build_proof(svc.lineage, svc.archive_chain, ids[0])))
    proof["block"]["index"] += 1
    assert proofs.verify_proof(proof).split(":")[0] == "block_hash_mismatch"
