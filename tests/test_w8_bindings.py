"""W8 part 5 — records sealed before W8 are bound later, and the proof says so (run/specs/W8.md, D6).

WHAT IS HELD HERE
    N1  The next seal after a pre-W8 store is opened binds every record sealed in a layout-1 block: its block
        carries a `binding_seal` with one leaf per record, `record_hash || commitment_root`, by record hash, each
        with the block that first sealed it, under a `binding` root in the header with its size; the records are
        marked bound, a second seal binds none again, and the chain verifies.
    N2  A bound record's proof carries the binding and verifies from itself alone; each part of the binding altered
        is refused by name. Before its binding the same proof has none and verifies as it did (layout 1 alone).
    N3  The fourth layer counts the bound and the unbound, and names a binding that no longer holds (a commitment
        root rewritten after its binding).
    N4  A record sealed after W8 is never bound: its own seal carries its leaf.
    N5  Bindings ride as many seals as they need, at most BINDING_BATCH each.
"""
from __future__ import annotations

import pytest

import asyncio
import copy
import json
import os
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from backend.memory import kairos as kairos_mod
from backend.memory import merkle, proofs
from backend.memory.chain import seal_leaves
from backend.memory.lineage import verify_sealed_lineage
from backend.server import create_app, create_services
from tests._tracked import REPO
from tests.fixtures.ce_rig import RigClock

import sys
sys.path.insert(0, str(REPO / "scripts"))
import history_shape_harness as hsh  # noqa: E402

PRE_RUN = REPO / "tests" / "fixtures" / "pre_run_data"


def _pre_run_services(root: Path):
    for name in ("kb.db", "archive.db"):
        shutil.copy(PRE_RUN / name, root / name)
    return create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma", eval_log_path=root / "metrics.jsonl",
                           archive_db_path=root / "archive.db", snapshots_dir=root / "snapshots",
                           provider=hsh._Stub(plant=None))


def _layout_1_sealed(svc) -> dict[str, tuple[str, int]]:
    """lineage_id -> (record_hash, first sealed block) for every record sealed in a layout-1 block."""
    out = {}
    for r in svc.lineage.conn.execute("SELECT lineage_id, record_hash, sealed_block FROM lineage_records "
                                      "WHERE sealed_block IS NOT NULL"):
        if svc.archive_chain.get_block(int(r["sealed_block"])).layout == 1:
            out[r["lineage_id"]] = (r["record_hash"], int(r["sealed_block"]))
    return out


def _seal(svc):
    return asyncio.run(svc.kairos.maybe_seal_governance(force=True))


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; on Postgres its bind, prove and tombstone path runs through the migration script in tests/test_w8_rebinding.py R3")
def test_n1_the_next_seal_binds_every_record_sealed_before_w8(tmp_path):
    svc = _pre_run_services(tmp_path)
    old = _layout_1_sealed(svc)
    assert old, "the population: the pre-run fixture holds records sealed in layout-1 blocks"
    block = _seal(svc)
    assert block is not None and block.layout == 2
    seal = block.fingerprint["binding_seal"]
    expected = sorted((h, svc.lineage.commitment_root(lid), first) for lid, (h, first) in old.items())
    assert seal["record_hashes"] == [h for h, _r, _f in expected]
    assert seal["leaves"] == [h + r for h, r, _f in expected]
    assert seal["first_sealed"] == [f for _h, _r, f in expected]
    assert block.roots["binding"] == {"root": merkle.root(seal_leaves(seal)).hex(), "size": len(old)}
    assert all(svc.lineage.bound_block(lid) == block.index for lid in old)
    again = _seal(svc)
    assert again is None or "binding_seal" not in (again.fingerprint or {}), "a bound record is not bound again"
    assert svc.archive_chain.verify_chain().valid


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; on Postgres its bind, prove and tombstone path runs through the migration script in tests/test_w8_rebinding.py R3")
def test_n2_a_bound_records_proof_carries_the_binding_and_each_part_altered_is_named(tmp_path):
    svc = _pre_run_services(tmp_path)
    lineage_id = sorted(_layout_1_sealed(svc))[0]
    before = proofs.build_proof(svc.lineage, svc.archive_chain, lineage_id)
    assert "binding" not in before and proofs.verify_proof(json.loads(json.dumps(before))) is None
    _seal(svc)
    proof = json.loads(json.dumps(proofs.build_proof(svc.lineage, svc.archive_chain, lineage_id)))
    binding = proof["binding"]
    assert binding["state"] == "bound" and binding["leaf"] == proof["record_hash"] + binding["commitment_root"]
    assert proofs.verify_proof(proof) is None
    flip = lambda h: ("0" if h[0] != "0" else "1") + h[1:]
    cases = {
        "the binding's leaf": lambda b: b.__setitem__("leaf", flip(b["leaf"])),
        "the commitment root": lambda b: b.__setitem__("commitment_root", flip(b["commitment_root"])),
        "the binding's size": lambda b: b.__setitem__("tree_size", b["tree_size"] + 1),
        "the binding's index": lambda b: b.__setitem__("leaf_index", b["leaf_index"] + 1),
        "a header field": lambda b: b["block"]["header"].__setitem__("timestamp", b["block"]["header"]["timestamp"] + 1),
        "the header's binding root": lambda b: b["block"]["header"]["roots"]["binding"].__setitem__(
            "root", flip(b["block"]["header"]["roots"]["binding"]["root"])),
        "the binding block's hash": lambda b: b["block"].__setitem__("block_hash", flip(b["block"]["block_hash"])),
        "its state": lambda b: b.__setitem__("state", "not_in_block"),
    }
    for label, change in cases.items():
        forged = copy.deepcopy(proof)
        change(forged["binding"])
        refusal = proofs.verify_proof(forged)
        assert isinstance(refusal, str) and refusal.split(":")[0] in proofs.BINDING_REFUSALS, (label, refusal)


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; on Postgres its bind, prove and tombstone path runs through the migration script in tests/test_w8_rebinding.py R3")
def test_n3_the_fourth_layer_counts_bindings_and_names_one_that_no_longer_holds(tmp_path):
    svc = _pre_run_services(tmp_path)
    old = _layout_1_sealed(svc)
    first = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    assert (first["proofs"]["bound"], first["proofs"]["unbound"]) == (0, len(old))
    _seal(svc)
    bound = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    assert (bound["proofs"]["bound"], bound["proofs"]["unbound"]) == (len(old), 0)
    assert bound["proofs"]["failed"] == 0
    victim = sorted(old)[0]
    svc.lineage.conn.execute("UPDATE record_commitments SET commitment_root = ? WHERE lineage_id = ?",
                             ("ab" * 32, victim))
    svc.lineage.conn.commit()
    after = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    # named by its proof and by the binding's block-to-row read
    assert {f["lineage_id"] for f in after["proofs"]["failures"]} == {victim}, after["proofs"]
    assert all(f["refusal"].split(":")[0] in proofs.BINDING_REFUSALS for f in after["proofs"]["failures"])
    assert after["verdict"] != "intact"


def test_n4_a_record_sealed_after_w8_is_never_bound(tmp_path):
    hsh._build_store(tmp_path / "kb.db").close()
    with RigClock().installed():
        svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                              eval_log_path=tmp_path / "metrics.jsonl", archive_db_path=tmp_path / "archive.db",
                              snapshots_dir=tmp_path / "snapshots", provider=hsh._Stub(plant=None))
        client = TestClient(create_app(svc))
        resp = client.post("/chat", headers={"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"},
                           json={"message": "what does interval training improve?"})
        done = [f for f in resp.text.split("\n\n") if f.startswith("event: done")]
        lineage_id = json.loads(done[-1].split("\ndata: ", 1)[1])["lineage_id"]
        block = _seal(svc)
        _seal(svc)
    assert block.layout == 2 and "binding_seal" not in block.fingerprint
    assert svc.lineage.bound_block(lineage_id) is None
    assert "binding" not in proofs.build_proof(svc.lineage, svc.archive_chain, lineage_id)


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; on Postgres its bind, prove and tombstone path runs through the migration script in tests/test_w8_rebinding.py R3")
def test_n5_bindings_ride_as_many_seals_as_they_need(tmp_path, monkeypatch):
    monkeypatch.setattr(kairos_mod, "BINDING_BATCH", 2)
    svc = _pre_run_services(tmp_path)
    old = _layout_1_sealed(svc)
    sizes = []
    for _ in range(len(old) + 2):
        block = _seal(svc)
        if block is None or "binding_seal" not in block.fingerprint:
            break
        sizes.append(block.fingerprint["binding_seal"]["count"])
    assert sum(sizes) == len(old) and all(n <= 2 for n in sizes), sizes
    assert all(svc.lineage.bound_block(lid) is not None for lid in old)


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; on Postgres its bind, prove and tombstone path runs through the migration script in tests/test_w8_rebinding.py R3")
def test_n6_a_record_tampered_before_w8_is_never_committed_bound_or_tombstoned(tmp_path):
    """W8's verifier, MF2: the open-time backfill commits only a record whose fields still hash to its sealed hash,
    so a record rewritten before W8 opened the store gets no root, is never bound, and cannot be tombstoned into a
    verifying disposal; the row layer keeps naming it."""
    import sqlite3
    import pytest
    from backend.memory.lineage import TombstoneRefused
    for name in ("kb.db", "archive.db"):
        shutil.copy(PRE_RUN / name, tmp_path / name)
    conn = sqlite3.connect(tmp_path / "kb.db")
    victim = conn.execute("SELECT lineage_id FROM lineage_records WHERE sealed_block IS NOT NULL "
                          "ORDER BY lineage_id LIMIT 1").fetchone()[0]
    conn.execute("UPDATE lineage_records SET model_id = ? WHERE lineage_id = ?", ("forged:model", victim))
    conn.commit()
    conn.close()
    svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "metrics.jsonl", archive_db_path=tmp_path / "archive.db",
                          snapshots_dir=tmp_path / "snapshots", provider=hsh._Stub(plant=None))
    assert svc.lineage.commitment_root(victim) is None, "a record that does not verify gets no commitments"
    _seal(svc)
    assert svc.lineage.bound_block(victim) is None
    verdict = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    assert verdict["verdict"] != "intact" and victim in verdict["layers"]["row"]["failures"], verdict["layers"]["row"]
    with pytest.raises(TombstoneRefused) as e:
        svc.lineage.tombstone(victim, chain=svc.archive_chain, actor={"id": "s"}, reason="erase")
    assert str(e.value).split(":")[0] == "does_not_verify", str(e.value)


def test_n7_a_record_sealed_as_its_bare_hash_is_bound_before_it_can_be_tombstoned(tmp_path):
    """A layout-2 seal may carry a record's bare hash (the record had no root then); a root made later by the
    open-time backfill is covered by no seal: the proof claims none, a tombstone is refused until the next seal
    binds it, and then the proof carries the binding and the tombstone is made."""
    import pytest
    from backend.memory.lineage import LineageStore, TombstoneRefused
    hsh._build_store(tmp_path / "kb.db").close()
    with RigClock().installed():
        svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                              eval_log_path=tmp_path / "metrics.jsonl", archive_db_path=tmp_path / "archive.db",
                              snapshots_dir=tmp_path / "snapshots", provider=hsh._Stub(plant=None))
        client = TestClient(create_app(svc))
        resp = client.post("/chat", headers={"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"},
                           json={"message": "what does interval training improve?"})
        lid = json.loads([f for f in resp.text.split("\n\n") if f.startswith("event: done")][-1]
                         .split("\ndata: ", 1)[1])["lineage_id"]
        for table in ("record_salts", "record_commitments"):      # the record as if it had no root at its seal
            svc.lineage.conn.execute(f"DELETE FROM {table} WHERE lineage_id = ?", (lid,))
        svc.lineage.conn.commit()
        _seal(svc)
        assert LineageStore(tmp_path / "kb.db").commitments_backfilled == 1, "the backfill gives it a root"
        proof = proofs.build_proof(svc.lineage, svc.archive_chain, lid)
        assert proof["leaf"] == proof["record_hash"] and proof["commitment_root"] is None and "binding" not in proof
        assert proofs.verify_proof(json.loads(json.dumps(proof))) is None
        with pytest.raises(TombstoneRefused) as e:
            svc.lineage.tombstone(lid, chain=svc.archive_chain, actor={"id": "s"}, reason="erase")
        assert str(e.value).split(":")[0] == "unbound", str(e.value)
        _seal(svc)
    proof = json.loads(json.dumps(proofs.build_proof(svc.lineage, svc.archive_chain, lid)))
    assert "binding" in proof and proofs.verify_proof(proof) is None
    svc.lineage.tombstone(lid, chain=svc.archive_chain, actor={"id": "s"}, reason="erase")
    assert verify_sealed_lineage(svc.lineage, svc.archive_chain)["verdict"] == "intact"
