"""W8 part 6 — a tombstone disposes of a record's content and keeps what lets it verify (run/specs/W8.md, D9).

WHAT IS HELD HERE
    T1  A sealed record tombstoned keeps its id, time, injected set, hash, commitment root, every field's commitment
        and the kept fields' salts; its other hashed fields read the mark (NOT NULL) or NULL, their salts and the
        kept request body are gone; it verifies by its kept openings and root, and its proof still holds.
    T2  The act is a governance record: sealed with the governance tables, its row verified, and a rewrite named.
    T3  A tombstoned record rewritten afterwards is named: a kept field, a stored commitment.
    T4  Refused by name, writing nothing: no record, already tombstoned, no reason, unsealed, and a record sealed
        before W8 that no seal has bound yet; once bound, it is tombstoned and verifies.
    T5  A request body another live record shares is kept.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.memory import commitments as cm
from backend.memory import proofs
from backend.memory.lineage import (TOMBSTONE_KEPT_FIELDS, TOMBSTONE_MARK, LineageStore, TombstoneRefused,
                                    _hash_payload_from_row, verify_sealed_lineage)
from backend.server import create_app, create_services
from tests._tracked import REPO
from tests.fixtures.ce_rig import RigClock

import sys
sys.path.insert(0, str(REPO / "scripts"))
import history_shape_harness as hsh  # noqa: E402

PRE_RUN = REPO / "tests" / "fixtures" / "pre_run_data"
WHO = {"id": "steward-1", "role": "steward", "source": "test"}


def _console(root: Path, prepare=None):
    if prepare is None:
        hsh._build_store(root / "kb.db").close()
    else:
        prepare(root)
    svc = create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma", eval_log_path=root / "metrics.jsonl",
                          archive_db_path=root / "archive.db", snapshots_dir=root / "snapshots",
                          provider=hsh._Stub(plant=None))
    return svc, TestClient(create_app(svc)), {"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"}


def _turn(client, bearer, message: str) -> str:
    resp = client.post("/chat", headers=bearer, json={"message": message})
    done = [f for f in resp.text.split("\n\n") if f.startswith("event: done")]
    return json.loads(done[-1].split("\ndata: ", 1)[1])["lineage_id"]


def _seal(svc):
    return asyncio.run(svc.kairos.maybe_seal_governance(force=True))


def _row(svc, lineage_id: str):
    return svc.lineage.conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (lineage_id,)).fetchone()


def _sealed_record(tmp_path):
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        ids = [_turn(client, bearer, q) for q in ("what does interval training improve?",
                                                   "and what does sleep improve?")]
        _seal(svc)
    return svc, ids


def test_t1_a_tombstoned_record_keeps_what_verifies_and_drops_the_rest(tmp_path):
    svc, ids = _sealed_record(tmp_path)
    lid = ids[0]
    before = dict(_row(svc, lid))
    body = LineageStore.hashed_body(_hash_payload_from_row(_row(svc, lid)))
    assert before["request_hash"] and svc.lineage.conn.execute(
        "SELECT 1 FROM request_bodies WHERE request_hash = ?", (before["request_hash"],)).fetchone()
    out = svc.lineage.tombstone(lid, chain=svc.archive_chain, actor=WHO, reason="the subject asked for erasure")
    kept = sorted(f for f in body if f in TOMBSTONE_KEPT_FIELDS)
    assert out["kept_fields"] == kept and set(out["dropped_fields"]) == set(body) - set(kept)
    after = _row(svc, lid)
    for column in ("lineage_id", "ts", "injected_json", "record_hash", "sealed_block"):
        assert after[column] == before[column], column
    for column in ("query_hash", "model_id", "prompt_prefix_hash", "response_hash"):
        assert after[column] == TOMBSTONE_MARK, column
    # a default scope is outside the hashed body (the LS rule), so it is neither kept nor dropped
    assert "scope" not in body and after["scope"] == before["scope"] == "default"
    assert after["contradictions_json"] == json.dumps(TOMBSTONE_MARK) and after["context_json"] is None
    assert after["answer_hash"] is None and after["request_hash"] is None
    assert set(svc.lineage.salts(lid)) == set(kept)
    assert svc.lineage.conn.execute("SELECT 1 FROM request_bodies WHERE request_hash = ?",
                                    (before["request_hash"],)).fetchone() is None
    assert svc.lineage.verify_record(lid)["record_intact"]
    verdict = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    assert verdict["verdict"] == "intact" and verdict["tombstoned"] == 1, verdict
    proof = json.loads(json.dumps(proofs.build_proof(svc.lineage, svc.archive_chain, lid)))
    assert proofs.verify_proof(proof) is None
    stored = {r["field"]: bytes.fromhex(r["commitment"]) for r in svc.lineage.conn.execute(
        "SELECT field, commitment FROM record_field_commitments WHERE lineage_id = ?", (lid,))}
    assert set(stored) == set(body) and cm.commitment_root(stored).hex() == svc.lineage.commitment_root(lid)


def test_t2_the_act_is_a_governance_record_sealed_and_verified(tmp_path):
    svc, ids = _sealed_record(tmp_path)
    out = svc.lineage.tombstone(ids[0], chain=svc.archive_chain, actor=WHO, reason="retention period ended")
    assert any(k == out["event_id"] for k, _h in svc.recalibration.unsealed())
    block = _seal(svc)
    assert block is not None
    sealed = svc.lineage.conn.execute("SELECT sealed_block FROM record_tombstones WHERE event_id = ?",
                                      (out["event_id"],)).fetchone()["sealed_block"]
    assert sealed == block.index, "the act is marked sealed in the block that sealed it"
    gov = svc.recalibration.verify_governance_records(svc.archive_chain)
    assert gov["verdict"] == "intact", gov
    svc.lineage.conn.execute("UPDATE record_tombstones SET reason = ? WHERE event_id = ?",
                             ("another reason", out["event_id"]))
    svc.lineage.conn.commit()
    gov = svc.recalibration.verify_governance_records(svc.archive_chain)
    assert gov["verdict"] == "row_hash_mismatch" and gov["row"]["failures"][0]["table"] == "record_tombstones"


def test_t3_a_tombstoned_record_rewritten_afterwards_is_named(tmp_path):
    svc, ids = _sealed_record(tmp_path)
    lid = ids[0]
    svc.lineage.tombstone(lid, chain=svc.archive_chain, actor=WHO, reason="the subject asked for erasure")
    conn = svc.lineage.conn
    original = _row(svc, lid)["injected_json"]
    conn.execute("UPDATE lineage_records SET injected_json = ? WHERE lineage_id = ?", ("[]", lid))
    conn.commit()
    assert not svc.lineage.verify_record(lid)["record_intact"], "a kept field rewritten"
    conn.execute("UPDATE lineage_records SET injected_json = ? WHERE lineage_id = ?", (original, lid))
    conn.execute("UPDATE record_field_commitments SET commitment = ? WHERE lineage_id = ? AND field = ?",
                 ("00" * 32, lid, "query_hash"))
    conn.commit()
    assert not svc.lineage.verify_record(lid)["record_intact"], "a dropped field's commitment rewritten"
    assert verify_sealed_lineage(svc.lineage, svc.archive_chain)["verdict"] != "intact"


def _snapshot(svc) -> dict:
    conn = svc.lineage.conn
    return {t: sorted(tuple(r) for r in conn.execute(f"SELECT * FROM {t}"))
            for t in ("lineage_records", "record_salts", "record_tombstones", "record_field_commitments")}


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; on Postgres its bind, prove and tombstone path runs through the migration script in tests/test_w8_rebinding.py R3")
def test_t4_refusals_by_name_write_nothing_and_a_bound_pre_w8_record_is_tombstoned(tmp_path):
    def pre_run(root):
        for name in ("kb.db", "archive.db"):
            shutil.copy(PRE_RUN / name, root / name)
    svc, client, bearer = _console(tmp_path, prepare=pre_run)
    old = [r["lineage_id"] for r in svc.lineage.conn.execute(
        "SELECT lineage_id FROM lineage_records WHERE sealed_block IS NOT NULL")]
    assert old, "the population: the pre-run fixture holds records sealed before W8"
    with RigClock().installed():
        fresh = _turn(client, bearer, "what does interval training improve?")
    cases = [("no_record", "no-such-record", "a reason"), ("unbound", old[0], "a reason"),
             ("unsealed", fresh, "a reason"), ("no_reason", old[0], "  ")]
    for name, lid, reason in cases:
        before = _snapshot(svc)
        with pytest.raises(TombstoneRefused) as e:
            svc.lineage.tombstone(lid, chain=svc.archive_chain, actor=WHO, reason=reason)
        assert str(e.value).split(":")[0] == name, (name, str(e.value))
        assert _snapshot(svc) == before, f"{name} wrote something"
    _seal(svc)                                   # binds the pre-W8 records and seals the fresh one
    svc.lineage.tombstone(old[0], chain=svc.archive_chain, actor=WHO, reason="the subject asked for erasure")
    assert svc.lineage.verify_record(old[0])["record_intact"]
    proof = json.loads(json.dumps(proofs.build_proof(svc.lineage, svc.archive_chain, old[0])))
    assert "binding" in proof and proofs.verify_proof(proof) is None
    before = _snapshot(svc)
    with pytest.raises(TombstoneRefused) as e:
        svc.lineage.tombstone(old[0], chain=svc.archive_chain, actor=WHO, reason="again")
    assert str(e.value).split(":")[0] == "already_tombstoned" and _snapshot(svc) == before


def test_t5_a_request_body_another_live_record_shares_is_kept(tmp_path):
    svc, ids = _sealed_record(tmp_path)
    request_hash = _row(svc, ids[0])["request_hash"]
    svc.lineage.conn.execute("UPDATE lineage_records SET request_hash = ? WHERE lineage_id = ?",
                             (request_hash, ids[1]))    # a plant: the second record shares the first's request
    svc.lineage.conn.commit()
    svc.lineage.tombstone(ids[0], chain=svc.archive_chain, actor=WHO, reason="the subject asked for erasure")
    assert svc.lineage.conn.execute("SELECT 1 FROM request_bodies WHERE request_hash = ?",
                                    (request_hash,)).fetchone() is not None


def test_t6_every_hashed_field_is_kept_or_has_its_drop_statement():
    """Derived from the one row-to-payload map, by AST: a hashed field a later change adds must say how a tombstone
    drops it, or `tombstone()` refuses it by name."""
    import ast
    import inspect
    from backend.memory.lineage import _TOMBSTONE_DROP
    tree = ast.parse(inspect.getsource(_hash_payload_from_row))
    returned = [n.value for n in ast.walk(tree) if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)]
    assert len(returned) == 1, "the population: the map returns one dict literal"
    fields = {k.value for k in returned[0].keys if isinstance(k, ast.Constant)}
    assert {"query_hash", "injected", "forecast"} <= fields, fields
    assert fields - set(TOMBSTONE_KEPT_FIELDS) == set(_TOMBSTONE_DROP), \
        fields ^ (set(_TOMBSTONE_DROP) | set(TOMBSTONE_KEPT_FIELDS))


def test_t7_a_forged_tombstone_beside_a_rewritten_field_is_named(tmp_path):
    """A tombstone reads its dropped fields too: a dropped field rewritten to a value other than what the tombstone
    leaves, or a value written into a field that had no commitment, is named, never read as a disposal."""
    svc, ids = _sealed_record(tmp_path)
    lid = ids[0]
    svc.lineage.tombstone(lid, chain=svc.archive_chain, actor=WHO, reason="the subject asked for erasure")
    conn = svc.lineage.conn
    assert svc.lineage.verify_record(lid)["record_intact"]
    conn.execute("UPDATE lineage_records SET model_id = ? WHERE lineage_id = ?", ("forged:model", lid))
    conn.commit()
    assert not svc.lineage.verify_record(lid)["record_intact"], "a dropped field rewritten"
    conn.execute("UPDATE lineage_records SET model_id = ? WHERE lineage_id = ?", (TOMBSTONE_MARK, lid))
    conn.commit()
    assert svc.lineage.verify_record(lid)["record_intact"]
    fields = {r["field"] for r in conn.execute("SELECT field FROM record_field_commitments WHERE lineage_id = ?",
                                              (lid,))}
    assert "boundary_asserted" not in fields, "the population: a field the record never carried"
    conn.execute("UPDATE lineage_records SET boundary_asserted = ? WHERE lineage_id = ?", ("close", lid))
    conn.commit()
    assert not svc.lineage.verify_record(lid)["record_intact"], "a value written into an uncommitted field"


def test_t8_a_salt_rewritten_and_a_field_with_no_drop_statement_are_refused_writing_nothing(tmp_path, monkeypatch):
    """W8's verifier, L16 and L17: a stored salt that no longer rebuilds the record's root refuses the tombstone
    (commitments_mismatch); a hashed field with no drop statement refuses it (unknown_field); neither writes."""
    from backend.memory import lineage as lineage_mod
    svc, ids = _sealed_record(tmp_path)
    conn = svc.lineage.conn
    conn.execute("UPDATE record_salts SET salt = ? WHERE lineage_id = ? AND field = ?", ("00" * 32, ids[0], "ts"))
    conn.commit()
    before = _snapshot(svc)
    with pytest.raises(TombstoneRefused) as e:
        svc.lineage.tombstone(ids[0], chain=svc.archive_chain, actor=WHO, reason="erase")
    assert str(e.value).split(":")[0] == "commitments_mismatch" and _snapshot(svc) == before, str(e.value)
    monkeypatch.setattr(lineage_mod, "_TOMBSTONE_DROP",
                        {k: v for k, v in lineage_mod._TOMBSTONE_DROP.items() if k != "query_hash"})
    before = _snapshot(svc)
    with pytest.raises(TombstoneRefused) as e:
        svc.lineage.tombstone(ids[1], chain=svc.archive_chain, actor=WHO, reason="erase")
    assert str(e.value).split(":")[0] == "unknown_field" and _snapshot(svc) == before, str(e.value)
