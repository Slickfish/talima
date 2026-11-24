"""W8 — the first delta check's must-fix and witnesses (run/specs/W8.md, D6, D7, D9).

WHAT IS HELD HERE
    R1  A tombstoned record sealed as its bare hash, its kept field and root rewritten consistently and its binding
        pointer cleared (or kept), is named, and the next seal does not bind it again.
    R2  The same rewrite on a record sealed before W8 (layout 1) is named.
    R3  The committed pre-run fixture, migrated to Postgres through the migration script, is bound by one seal;
        every proof holds with its binding and its block's body; a tombstone is made and the store verifies.
    R4  `verify_proof`'s two halves: a record hash that is not 64 hex characters, and a commitment root that is not
        the leaf's rest, are each refused.
    R5  The open-time backfill skips a record whose values cannot be committed (a NaN), counts it, never raises, and
        counts only what it made; such a record's tombstone is refused by name.
    R6  A record whose seal carried another root (the root rewritten with its salts) is neither bound nor tombstoned:
        refused `does_not_verify`, never "unbound".

The second delta check's must-fix and witnesses:
    R7  A root re-made over other values under the record's own salts before its binding (the row untouched) is
        never bound, and is named; the tombstone a store writer then completes by hand is named too.
    R8  A binding block appended whose kb.db marks then failed is bound again by the next seal, and the store
        reads intact: two blocks holding one honest leaf are not a broken seal.
    R9  A covered tombstone rewritten (its kept field, its commitment and its root) with its seal cleared is
        named at once, and still after the next seal re-seals it under the rewritten root.

The third delta check's must-fix, the fixes taken with them, and witnesses:
    R10 A salt that is not 32 bytes never rebuilds a root (a 33-byte `S||"-"` rebuilt one that opens to `-ts`
        under `S`), so the root is never bound and the tombstone completed by hand is named.
    R11 `unbound()` offers the root it checked: a root changed between its read and its check is never bound.
    R12 One record whose salt cannot be read stops neither the other records' bindings nor the verification.
    R13 A row edited while it awaits its binding reads `record_tampered`, not a broken seal.
    R14 The per-record read names what the whole-store read names (R9's forgery, one record).
    R15 A turn whose values the encoding refuses (a NaN) is still recorded, without commitments, and verifies.
    W1-W4 Witnesses for mechanisms no test held alone: the record-hash check of a rebuild, its refused-encoding
        arm with a record that reads intact, a deleted root named by its leaf, and a malformed salt refused.
"""
from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import shutil
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.schema import record_tombstone_body, record_tombstone_hash
from backend.memory import commitments as cm
from backend.memory import merkle, proofs
from backend.memory.lineage import (TOMBSTONE_KEPT_FIELDS, _TOMBSTONE_DROP, LineageStore, TombstoneRefused,
                                    _hash_payload_from_row, verify_sealed_lineage)
from backend.server import create_app, create_services
from tests.fixtures.ce_rig import RigClock
from tests.test_w3b_migration import _migrate, _source, pg  # noqa: F401  (pg: the fixture)
from tests.test_w8_bindings import _layout_1_sealed, _pre_run_services, _seal, hsh
from tests.test_w8_proofs import _sealed

WHO = {"id": "steward-1"}


def _bare_hash_record(tmp_path):
    """A record sealed as its bare hash (its commitments removed before its seal), given a root at the next open."""
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
        for table in ("record_salts", "record_commitments"):
            svc.lineage.conn.execute(f"DELETE FROM {table} WHERE lineage_id = ?", (lid,))
        svc.lineage.conn.commit()
        _seal(svc)
    LineageStore(svc.lineage.target)
    return svc, lid


def _rewrite_kept_ts(svc, lid, *, clear_bound: bool, clear_sealed: bool = False) -> None:
    """The kept field `ts` moved a day, its commitment and the record's root recomputed under its own salt."""
    conn = svc.lineage.conn
    new_ts = conn.execute("SELECT ts FROM lineage_records WHERE lineage_id = ?", (lid,)).fetchone()["ts"] + 86400.0
    salt = bytes.fromhex(conn.execute("SELECT salt FROM record_salts WHERE lineage_id = ? AND field = 'ts'",
                                      (lid,)).fetchone()["salt"])
    stored = {r["field"]: bytes.fromhex(r["commitment"]) for r in conn.execute(
        "SELECT field, commitment FROM record_field_commitments WHERE lineage_id = ?", (lid,))}
    stored["ts"] = cm.commitment(salt, new_ts)
    conn.execute("UPDATE lineage_records SET ts = ? WHERE lineage_id = ?", (new_ts, lid))
    conn.execute("UPDATE record_field_commitments SET commitment = ? WHERE lineage_id = ? AND field = 'ts'",
                 (stored["ts"].hex(), lid))
    conn.execute("UPDATE record_commitments SET commitment_root = ? WHERE lineage_id = ?",
                 (cm.commitment_root(stored).hex(), lid))
    if clear_bound:
        conn.execute("UPDATE record_commitments SET bound_block = NULL WHERE lineage_id = ?", (lid,))
    if clear_sealed:
        conn.execute("UPDATE lineage_records SET sealed_block = NULL WHERE lineage_id = ?", (lid,))
    conn.commit()


@pytest.mark.parametrize("clear_bound", [True, False])
def test_r1_a_bare_hash_tombstone_rewritten_and_rebound_is_named(tmp_path, clear_bound):
    svc, lid = _bare_hash_record(tmp_path)
    _seal(svc)                                                   # binds the root
    svc.lineage.tombstone(lid, chain=svc.archive_chain, actor=WHO, reason="erase")
    _seal(svc)
    assert verify_sealed_lineage(svc.lineage, svc.archive_chain)["verdict"] == "intact"
    _rewrite_kept_ts(svc, lid, clear_bound=clear_bound)
    assert verify_sealed_lineage(svc.lineage, svc.archive_chain)["verdict"] != "intact"
    assert lid not in [u[0] for u in svc.lineage.unbound(svc.archive_chain, limit=10)], "a tombstone is never re-bound"
    _seal(svc)
    assert verify_sealed_lineage(svc.lineage, svc.archive_chain)["verdict"] != "intact"


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; R3 runs the same path on Postgres")
def test_r2_a_tombstone_sealed_before_w8_rewritten_is_named(tmp_path):
    svc = _pre_run_services(tmp_path)
    lid = sorted(_layout_1_sealed(svc))[0]
    _seal(svc)
    svc.lineage.tombstone(lid, chain=svc.archive_chain, actor=WHO, reason="erase")
    _seal(svc)
    assert verify_sealed_lineage(svc.lineage, svc.archive_chain)["verdict"] == "intact"
    _rewrite_kept_ts(svc, lid, clear_bound=True)
    assert verify_sealed_lineage(svc.lineage, svc.archive_chain)["verdict"] != "intact"
    _seal(svc)
    assert verify_sealed_lineage(svc.lineage, svc.archive_chain)["verdict"] != "intact"


def test_r3_the_pre_run_fixture_on_postgres_is_bound_proved_and_tombstoned(tmp_path, pg, monkeypatch):
    _source(tmp_path)
    migrated = _migrate(tmp_path, pg, "--confirm")
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    monkeypatch.setenv("NEXUS_STORE", "postgres")
    monkeypatch.setenv("NEXUS_DATABASE_URL", pg["url"])
    svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "metrics.jsonl", archive_db_path=tmp_path / "archive.db",
                          snapshots_dir=tmp_path / "snapshots", provider=hsh._Stub(plant=None))
    old = _layout_1_sealed(svc)
    assert old, "the population: the migrated fixture holds records sealed before W8"
    block = _seal(svc)
    assert block.fingerprint["binding_seal"]["count"] == len(old)
    for lid in old:
        proof = json.loads(json.dumps(proofs.build_proof(svc.lineage, svc.archive_chain, lid)))
        assert proofs.verify_proof(proof) is None and "binding" in proof and "body" in proof["block"], lid
    svc.lineage.tombstone(sorted(old)[0], chain=svc.archive_chain, actor=WHO, reason="erase")
    _seal(svc)
    assert verify_sealed_lineage(svc.lineage, svc.archive_chain)["verdict"] == "intact"
    assert svc.recalibration.verify_governance_records(svc.archive_chain)["verdict"] == "intact"


def test_r4_each_half_of_the_leaf_check_refuses_on_its_own(tmp_path):
    svc, _client, _bearer, ids = _sealed(tmp_path)
    proof = json.loads(json.dumps(proofs.build_proof(svc.lineage, svc.archive_chain, ids[0])))
    assert proofs.verify_proof(proof) is None
    split = copy.deepcopy(proof)                                 # a 10-character "hash", the rest as the root
    split["record_hash"], split["commitment_root"] = proof["leaf"][:10], proof["leaf"][10:]
    assert proofs.verify_proof(split).split(":")[0] == "leaf_mismatch"
    forged = copy.deepcopy(proof)                                # the leaf untouched, another root claimed
    forged["commitment_root"] = ("0" if proof["commitment_root"][0] != "0" else "1") + proof["commitment_root"][1:]
    assert proofs.verify_proof(forged).split(":")[0] == "leaf_mismatch"


def test_r5_a_record_the_encoding_cannot_carry_is_skipped_counted_and_refused_by_name(tmp_path):
    svc, _client, _bearer, ids = _sealed(tmp_path)
    lid, conn = ids[0], svc.lineage.conn
    row = conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (lid,)).fetchone()
    injected = json.loads(row["injected_json"])
    injected[0]["weight_at_injection"] = math.nan
    conn.execute("UPDATE lineage_records SET injected_json = ?, sealed_block = NULL WHERE lineage_id = ?",
                 (json.dumps(injected, sort_keys=True), lid))
    row = conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (lid,)).fetchone()
    conn.execute("UPDATE lineage_records SET record_hash = ? WHERE lineage_id = ?",
                 (LineageStore.compute_record_hash(_hash_payload_from_row(row)), lid))
    for table in ("record_salts", "record_commitments"):
        conn.execute(f"DELETE FROM {table} WHERE lineage_id = ?", (lid,))
    conn.commit()
    reopened = LineageStore(svc.lineage.target)                  # the open does not raise
    assert (reopened.commitments_backfilled, reopened.commitments_skipped) == (0, 1)
    _seal(svc)
    with pytest.raises(TombstoneRefused) as e:
        svc.lineage.tombstone(lid, chain=svc.archive_chain, actor=WHO, reason="erase")
    assert str(e.value).split(":")[0] == "no_commitments", str(e.value)


def test_r6_a_root_its_seal_contradicts_is_never_bound_or_tombstoned(tmp_path):
    svc, _client, _bearer, ids = _sealed(tmp_path)
    lid, conn = ids[0], svc.lineage.conn
    body = LineageStore.hashed_body(_hash_payload_from_row(conn.execute(
        "SELECT * FROM lineage_records WHERE lineage_id = ?", (lid,)).fetchone()))
    made = cm.commit(body)                                       # fresh salts over the same values: another root
    conn.execute("DELETE FROM record_salts WHERE lineage_id = ?", (lid,))
    conn.executemany("INSERT INTO record_salts (lineage_id, field, salt) VALUES (?, ?, ?)",
                     [(lid, f, made.salts[f].hex()) for f in sorted(made.salts)])
    conn.execute("UPDATE record_commitments SET commitment_root = ? WHERE lineage_id = ?", (made.root.hex(), lid))
    conn.commit()
    assert lid not in [u[0] for u in svc.lineage.unbound(svc.archive_chain, limit=10)]
    with pytest.raises(TombstoneRefused) as e:
        svc.lineage.tombstone(lid, chain=svc.archive_chain, actor=WHO, reason="erase")
    assert str(e.value).split(":")[0] == "does_not_verify", str(e.value)
    _seal(svc)
    assert svc.lineage.bound_block(lid) is None


# --- the second delta check ---------------------------------------------------------------------------------

def _record(tmp_path, shape):
    if shape == "bare":
        return _bare_hash_record(tmp_path)
    svc = _pre_run_services(tmp_path)
    return svc, sorted(_layout_1_sealed(svc))[0]


def _forge_root_before_binding(svc, lid):
    """A store writer: the record's root re-made over a `ts` moved a day, under its own salts; the row untouched."""
    conn = svc.lineage.conn
    row = conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (lid,)).fetchone()
    fake = dict(LineageStore.hashed_body(_hash_payload_from_row(row)))
    fake["ts"] = fake["ts"] + 86400.0
    made = cm.commit(fake, salts=svc.lineage.salts(lid))
    conn.execute("UPDATE record_commitments SET commitment_root = ? WHERE lineage_id = ?", (made.root.hex(), lid))
    conn.commit()
    return made, fake


def _forge_tombstone(svc, lid, made, fake):
    """A store writer: a complete tombstone by hand (W8-B7's capability), its kept `ts` the moved one."""
    conn = svc.lineage.conn
    kept = sorted(f for f in fake if f in TOMBSTONE_KEPT_FIELDS)
    conn.executemany("INSERT INTO record_field_commitments (lineage_id, field, commitment) VALUES (?, ?, ?)",
                     [(lid, f, made.commitments[f].hex()) for f in sorted(made.commitments)])
    for f in sorted(f for f in fake if f not in TOMBSTONE_KEPT_FIELDS):
        conn.execute("DELETE FROM record_salts WHERE lineage_id = ? AND field = ?", (lid, f))
        statement, value = _TOMBSTONE_DROP[f]
        conn.execute(statement, (value, lid))
    conn.execute("UPDATE lineage_records SET ts = ?, context_json = NULL WHERE lineage_id = ?", (fake["ts"], lid))
    event_id, ts = str(uuid.uuid4()), time.time()
    act = record_tombstone_body(event_id, ts, lid, WHO["id"], None, None, "erase", kept)
    conn.execute("INSERT INTO record_tombstones (event_id, ts, lineage_id, actor_id, actor_role, actor_source, "
                 "reason, kept_fields, record_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (event_id, ts, lid, act["actor_id"], act["actor_role"], act["actor_source"], "erase",
                  json.dumps(kept, separators=(",", ":")), record_tombstone_hash(act)))
    conn.commit()


def _refusals(svc, lid) -> list[str]:
    v = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    return [v["verdict"], *(f["refusal"] for f in v["proofs"]["failures"] if f["lineage_id"] == lid)]


def _r7(tmp_path, shape):
    svc, lid = _record(tmp_path, shape)
    made, fake = _forge_root_before_binding(svc, lid)
    assert lid not in [u[0] for u in svc.lineage.unbound(svc.archive_chain, limit=10_000)], \
        "a root the record's values do not rebuild is offered to the binding"
    _seal(svc)
    assert svc.lineage.bound_block(lid) is None, "the seal bound a root the sealed hash never covered"
    got = _refusals(svc, lid)
    assert got[0] != "intact" and any("does not rebuild" in r for r in got[1:]), got
    _forge_tombstone(svc, lid, made, fake)
    _seal(svc)
    assert _refusals(svc, lid)[0] != "intact"


def test_r7_a_root_rewritten_before_its_binding_is_never_bound_and_is_named(tmp_path):
    _r7(tmp_path, "bare")


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; R3 runs the same path on Postgres")
def test_r7_the_same_on_a_record_sealed_before_w8(tmp_path):
    _r7(tmp_path, "layout1")


def _r8(tmp_path, monkeypatch, shape):
    svc, lid = _record(tmp_path, shape)
    real, calls = LineageStore.mark_bound, {"n": 0}

    def locked_once(self, ids, index):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")         # the kb.db write after the archive's commit
        return real(self, ids, index)
    monkeypatch.setattr(LineageStore, "mark_bound", locked_once)
    blocks = svc.archive_chain.count()
    _seal(svc)
    assert svc.archive_chain.count() == blocks + 1 and svc.lineage.bound_block(lid) is None, "the plant did not hold"
    _seal(svc)
    assert svc.lineage.bound_block(lid) is not None and calls["n"] == 2
    assert svc.archive_chain.verify_chain().valid
    got = _refusals(svc, lid)
    assert got == ["intact"], got
    if shape == "bare":                                       # and the honest store can still dispose of it
        svc.lineage.tombstone(lid, chain=svc.archive_chain, actor=WHO, reason="erase")
        _seal(svc)
        assert _refusals(svc, lid) == ["intact"]


def test_r8_a_binding_whose_marks_failed_is_bound_again_and_reads_intact(tmp_path, monkeypatch):
    _r8(tmp_path, monkeypatch, "bare")


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; R3 runs the same path on Postgres")
def test_r8_the_same_on_records_sealed_before_w8(tmp_path, monkeypatch):
    _r8(tmp_path, monkeypatch, "layout1")


def test_r9_a_covered_tombstone_rewritten_and_its_seal_cleared_is_named_before_and_after_the_next_seal(tmp_path):
    svc, _client, _bearer, ids = _sealed(tmp_path)
    lid = ids[0]
    svc.lineage.tombstone(lid, chain=svc.archive_chain, actor=WHO, reason="erase")
    _seal(svc)
    assert _refusals(svc, lid) == ["intact"]
    _rewrite_kept_ts(svc, lid, clear_bound=False, clear_sealed=True)
    got = _refusals(svc, lid)
    assert got[0] != "intact" and any(r.startswith("leaf_mismatch") for r in got[1:]), got
    _seal(svc)                                                # the system seals it again, under the rewritten root
    got = _refusals(svc, lid)
    assert got[0] != "intact" and any(r.startswith("leaf_mismatch") for r in got[1:]), got


# --- the third delta check ---------------------------------------------------------------------------------

def _r10(tmp_path, shape):
    svc, lid = _record(tmp_path, shape)
    conn = svc.lineage.conn
    row = conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (lid,)).fetchone()
    body = LineageStore.hashed_body(_hash_payload_from_row(row))
    salts = svc.lineage.salts(lid)
    short = salts["ts"]
    forged = {**salts, "ts": short + b"-"}                      # S||"-"||canonical(ts) is S||canonical(-ts)
    made = {name: cm.commitment(forged[name], body[name]) for name in body}
    root = cm.commitment_root(made)
    conn.execute("UPDATE record_salts SET salt = ? WHERE lineage_id = ? AND field = 'ts'", (forged["ts"].hex(), lid))
    conn.execute("UPDATE record_commitments SET commitment_root = ? WHERE lineage_id = ?", (root.hex(), lid))
    conn.commit()
    assert lid not in [u[0] for u in svc.lineage.unbound(svc.archive_chain, limit=10_000)], \
        "a salt of 33 bytes rebuilt the root"
    _seal(svc)
    assert svc.lineage.bound_block(lid) is None
    conn.execute("UPDATE record_salts SET salt = ? WHERE lineage_id = ? AND field = 'ts'", (short.hex(), lid))
    conn.commit()
    _forge_tombstone(svc, lid, cm.Commitments(salts=forged, commitments=made, root=root), {**body, "ts": -body["ts"]})
    _seal(svc)
    assert _refusals(svc, lid)[0] != "intact"


def test_r10_a_salt_that_is_not_32_bytes_never_rebuilds_a_root(tmp_path):
    _r10(tmp_path, "bare")


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; R3 runs the same path on Postgres")
def test_r10_the_same_on_a_record_sealed_before_w8(tmp_path):
    _r10(tmp_path, "layout1")


def test_r11_a_root_changed_between_its_read_and_its_check_is_never_bound(tmp_path, monkeypatch):
    svc, lid = _bare_hash_record(tmp_path)
    honest = svc.lineage.commitment_root(lid)
    made, _fake = _forge_root_before_binding(svc, lid)
    real = LineageStore.root_rebuilds

    def racing(self, lineage_id, **given):                       # a second writer, around the check
        if lineage_id == lid:
            self.conn.execute("UPDATE record_commitments SET commitment_root = ? WHERE lineage_id = ?", (honest, lid))
            self.conn.commit()
        try:
            return real(self, lineage_id, **given)
        finally:
            if lineage_id == lid:
                self.conn.execute("UPDATE record_commitments SET commitment_root = ? WHERE lineage_id = ?",
                                  (made.root.hex(), lid))
                self.conn.commit()
    monkeypatch.setattr(LineageStore, "root_rebuilds", racing)
    offered = svc.lineage.unbound(svc.archive_chain, limit=10_000)
    assert made.root.hex() not in [root for _l, _h, root, _f in offered], "a root it did not check was offered"


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture (five records); R3 runs it on Postgres")
def test_r12_one_record_whose_salt_cannot_be_read_stops_no_binding_and_no_verification(tmp_path):
    svc = _pre_run_services(tmp_path)
    old = sorted(_layout_1_sealed(svc))
    svc.lineage.conn.execute("UPDATE record_salts SET salt = 'zz' WHERE lineage_id = ? AND field = 'ts'", (old[0],))
    svc.lineage.conn.commit()
    _seal(svc)
    assert all(svc.lineage.bound_block(lid) is not None for lid in old[1:]), "one unreadable salt stopped every binding"
    assert svc.lineage.bound_block(old[0]) is None
    got = _refusals(svc, old[0])                                  # and the verification names it, never raises
    assert got[0] != "intact" and any("does not rebuild" in r for r in got[1:]), got


def test_r13_a_row_edited_while_it_awaits_its_binding_reads_as_tampered_not_as_a_broken_seal(tmp_path):
    svc, lid = _bare_hash_record(tmp_path)
    svc.lineage.conn.execute("UPDATE lineage_records SET model_id = 'edited' WHERE lineage_id = ?", (lid,))
    svc.lineage.conn.commit()
    v = verify_sealed_lineage(svc.lineage, svc.archive_chain)
    assert v["verdict"] == "record_tampered", (v["verdict"], v["proofs"]["failures"])


def test_r14_the_per_record_read_names_what_the_whole_store_read_names(tmp_path):
    svc, _client, _bearer, ids = _sealed(tmp_path)
    lid = ids[0]
    svc.lineage.tombstone(lid, chain=svc.archive_chain, actor=WHO, reason="erase")
    _seal(svc)
    _rewrite_kept_ts(svc, lid, clear_bound=False, clear_sealed=True)
    for _ in range(2):                                            # before the next seal, and after it re-seals it
        one = verify_sealed_lineage(svc.lineage, svc.archive_chain, lineage_id=lid)
        assert one["verdict"] != "intact" and any(f["refusal"].startswith("leaf_mismatch")
                                                  for f in one["proofs"]["failures"]), one["proofs"]
        _seal(svc)


def test_r15_a_turn_whose_values_cannot_be_committed_is_still_recorded(tmp_path):
    svc, _client, _bearer, _ids = _sealed(tmp_path)
    before = svc.lineage.count()
    rec = svc.lineage.record_turn(query="q", injected=[{"belief_id": 1, "weight_at_injection": math.nan,
                                                        "tier_at_injection": 2}],
                                  contradictions=[], model_id="m", response_text="a", prompt_prefix="p")
    assert svc.lineage.count() == before + 1 and svc.lineage.commitment_root(rec.lineage_id) is None
    _seal(svc)
    assert _refusals(svc, rec.lineage_id) == ["intact"]


def test_w1_a_row_edited_and_its_root_rebuilt_over_the_edit_is_never_offered(tmp_path):
    svc, lid = _bare_hash_record(tmp_path)
    conn = svc.lineage.conn
    conn.execute("UPDATE lineage_records SET model_id = 'edited' WHERE lineage_id = ?", (lid,))
    row = conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (lid,)).fetchone()
    made = cm.commit(LineageStore.hashed_body(_hash_payload_from_row(row)), salts=svc.lineage.salts(lid))
    conn.execute("UPDATE record_commitments SET commitment_root = ? WHERE lineage_id = ?", (made.root.hex(), lid))
    conn.commit()
    assert lid not in [u[0] for u in svc.lineage.unbound(svc.archive_chain, limit=10_000)]


def test_w2_a_record_the_encoding_refuses_reads_intact_and_a_root_planted_on_it_is_never_offered(tmp_path):
    svc, _client, _bearer, _ids = _sealed(tmp_path)
    lid = svc.lineage.record_turn(query="q", injected=[{"belief_id": 1, "weight_at_injection": math.nan,
                                                        "tier_at_injection": 2}],
                                  contradictions=[], model_id="m", response_text="a", prompt_prefix="p").lineage_id
    _seal(svc)                                                   # sealed as its bare hash: it has no root
    assert _refusals(svc, lid) == ["intact"]                     # no false alarm on a record with no root
    conn = svc.lineage.conn
    body = LineageStore.hashed_body(_hash_payload_from_row(conn.execute(
        "SELECT * FROM lineage_records WHERE lineage_id = ?", (lid,)).fetchone()))
    conn.executemany("INSERT INTO record_salts (lineage_id, field, salt) VALUES (?, ?, ?)",
                     [(lid, f, "11" * 32) for f in sorted(body)])
    conn.execute("INSERT INTO record_commitments (lineage_id, commitment_root, made_at) VALUES (?, ?, ?)",
                 (lid, "ab" * 32, 0.0))
    conn.commit()
    assert lid not in [u[0] for u in svc.lineage.unbound(svc.archive_chain, limit=10_000)]


def test_w3_a_covered_record_whose_root_was_deleted_is_named_by_its_leaf(tmp_path):
    svc, _client, _bearer, ids = _sealed(tmp_path)
    svc.lineage.conn.execute("DELETE FROM record_commitments WHERE lineage_id = ?", (ids[0],))
    svc.lineage.conn.commit()
    got = _refusals(svc, ids[0])
    assert got[0] != "intact" and any(r.startswith("leaf_mismatch") for r in got[1:]), got



def test_w4_a_tombstone_over_a_salt_that_is_not_32_bytes_is_refused_by_name(tmp_path):
    svc, _client, _bearer, ids = _sealed(tmp_path)
    salt = svc.lineage.salts(ids[0])["ts"]
    svc.lineage.conn.execute("UPDATE record_salts SET salt = ? WHERE lineage_id = ? AND field = 'ts'",
                             ((salt + b"-").hex(), ids[0]))
    svc.lineage.conn.commit()
    with pytest.raises(TombstoneRefused) as e:
        svc.lineage.tombstone(ids[0], chain=svc.archive_chain, actor=WHO, reason="erase")
    assert str(e.value).split(":")[0] == "commitments_mismatch", str(e.value)
