"""W9 part 2c — the decision credential and its route (run/specs/W9.md D5; K1 to K4, and the operator's K8).

WHAT IS HELD HERE
    C1  (K1) A credential for a record sealed now verifies with `nexus-verify` run isolated from an empty directory,
        pinned to the instance's `/identity`, with no store and no NEXUS.
    C2  (K2) Each part altered fails by name: a disclosed value, its salt, another field's commitment, the root, the
        path, the header, the signature, a signature dropped, and a key other than the instance's pinned.
    C3  (K3) A tombstoned record's credential verifies on what it kept; disclosing a dropped field is refused.
    C4  (K4) Two disclosed fields verify, every other field appears only as its salted commitment, and a planted
        unsalted hash is named.
    C5  A record sealed before W8 and bound verifies; its block, sealed before W9, is signed at issue and says so.
    C6  The route is gated, names an unknown record (404) and refuses an unknown field by name (409).
    C7  (K8, the operator's) A restore without the private key: the backup holds no private key; a credential
        issued under the old key and one issued after the restore under the new key both verify against the
        restored instance's `/identity`.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.memory import instance_key as ik
from backend.server import create_app, create_services
from scripts import backup as backup_script
from scripts import nexus_verify as nv
from tests import _proc
from tests.fixtures.ce_rig import RigClock
from tests.test_w8_bindings import _layout_1_sealed, _pre_run_services, _seal
from tests.test_w8_proofs import _sealed, _turn

REPO = Path(__file__).resolve().parent.parent
WHO = {"id": "steward-1"}


def _issue(client, bearer, lid, disclose=""):
    resp = client.get(f"/lineage/{lid}/credential", headers=bearer, params={"disclose": disclose} if disclose else {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _offline(tmp_path, credential, identity) -> tuple[int, str]:
    """`nexus-verify` as a stranger runs it: isolated, from an empty directory, the two files beside it only."""
    where = tmp_path / f"stranger-{len(list(tmp_path.glob('stranger-*')))}"
    where.mkdir()
    (where / "credential.json").write_text(json.dumps(credential), encoding="utf-8")
    (where / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    proc = _proc.run([sys.executable, "-I", str(REPO / "scripts" / "nexus_verify.py"), "credential.json",
                      "--identity", "identity.json"], cwd=where, env=env, timeout=120, capture_output=True,
                     text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout + proc.stderr


def _failures(credential, keys) -> list[str]:
    return [f.split(":", 1)[0] for f in nv.verify_credential(credential, keys)["failures"]]


def test_c1_a_credential_verifies_offline_from_an_empty_directory(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path / "store")
    credential = _issue(client, bearer, ids[0], "model_id")
    identity = client.get("/identity").json()
    rc, out = _offline(tmp_path, credential, identity)
    assert rc == 0 and "nexus-verify: VERIFIED" in out and "DISCLOSED model_id" in out, out
    assert [s["signed"] for s in credential["signatures"]] == ["seal"]


def test_c2_each_part_altered_fails_by_name(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path)
    good = _issue(client, bearer, ids[1], "model_id,ts")
    keys = client.get("/identity").json()["keys"]
    assert _failures(good, keys) == []
    other = ik.load_or_create(tmp_path / "another_key.json")
    alterations = {
        "disclosure_mismatch": lambda c: c["disclosed"]["model_id"].__setitem__("value", "another-model"),
        "disclosure_mismatch ": lambda c: c["disclosed"]["ts"].__setitem__("salt", "00" * 32),
        "commitments_mismatch": lambda c: c["commitments"].__setitem__("response_hash", "00" * 32),
        "root_not_sealed": lambda c: c.__setitem__("commitment_root", "00" * 32),
        "root_mismatch": lambda c: c["proof"].__setitem__("path", ["11" * 32] + c["proof"]["path"][1:])
        if c["proof"]["path"] else c["proof"].__setitem__("leaf_index", 1),
        "block_hash_mismatch": lambda c: c["proof"]["block"]["header"].__setitem__("timestamp", 1.0),
        "signature_invalid": lambda c: c["signatures"][0].__setitem__("value", "00" * 64),
        "unsigned": lambda c: c.__setitem__("signatures", []),
    }
    for name, alter in alterations.items():
        bad = copy.deepcopy(good)
        alter(bad)
        got = _failures(bad, keys)
        assert name.strip() in got or (name.strip() == "root_mismatch" and got), (name, got)
    pinned_other = [{"key_id": other.key_id, "public_key": other.public_key.hex(), "announced_in": None,
                     "until_block": None}]
    assert "signature_invalid" in _failures(good, pinned_other)


def test_c3_a_tombstoned_records_credential_verifies_on_what_it_kept(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path / "store")
    svc.lineage.tombstone(ids[0], chain=svc.archive_chain, actor=WHO, reason="erase")
    asyncio.run(svc.kairos.maybe_seal_governance(force=True))
    credential = _issue(client, bearer, ids[0], "ts,lineage_id")
    rc, out = _offline(tmp_path, credential, client.get("/identity").json())
    assert rc == 0, out
    resp = client.get(f"/lineage/{ids[0]}/credential", headers=bearer, params={"disclose": "model_id"})
    assert resp.status_code == 409 and resp.json()["refused"] == "dropped", resp.text


def test_c4_two_fields_disclosed_the_rest_as_commitments_and_an_unsalted_hash_named(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path)
    credential = _issue(client, bearer, ids[2], "model_id,ts")
    keys = client.get("/identity").json()["keys"]
    assert set(credential["disclosed"]) == {"model_id", "ts"} and len(credential["commitments"]) > 2
    row = svc.lineage.conn.execute("SELECT query_hash FROM lineage_records WHERE lineage_id = ?", (ids[2],)).fetchone()
    assert row["query_hash"] not in json.dumps({k: v for k, v in credential.items() if k != "proof"}), \
        "an undisclosed field's value is in the credential"
    assert _failures(credential, keys) == []
    planted = copy.deepcopy(credential)
    value = planted["disclosed"]["model_id"]["value"]
    planted["commitments"]["model_id"] = hashlib.sha256(nv.canonical(value)).hexdigest()
    planted["disclosed"]["model_id"]["salt"] = ""
    assert "unsalted_hash" in _failures(planted, keys)


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; W8's R3 runs a pre-W8 store on Postgres")
def test_c5_a_record_sealed_before_w8_and_bound_verifies_signed_at_issue(tmp_path):
    svc = _pre_run_services(tmp_path)
    lid = sorted(_layout_1_sealed(svc))[0]
    _seal(svc)                                                     # binds it, in a block signed when appended
    client = TestClient(create_app(svc))
    bearer = {"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"}
    credential = _issue(client, bearer, lid, "ts")
    assert [s["signed"] for s in credential["signatures"]] == ["issue", "seal"], credential["signatures"]
    result = nv.verify_credential(credential, client.get("/identity").json()["keys"])
    assert result["verified"] and any(n.startswith("signed_at_issue") for n in result["notes"]), result


def test_c6_the_route_is_gated_and_refuses_by_name(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path)
    assert client.get(f"/lineage/{ids[0]}/credential", headers={"Authorization": ""}).status_code == 401
    resp = client.get("/lineage/no-such-record/credential", headers=bearer)
    assert resp.status_code == 404 and resp.json()["refused"] == "no_record", resp.text
    resp = client.get(f"/lineage/{ids[0]}/credential", headers=bearer, params={"disclose": "no_such_field"})
    assert resp.status_code == 409 and resp.json()["refused"] == "unknown_field", resp.text


@pytest.mark.sqlite_only("a restore here is the backup folder's files copied; on Postgres it is the hosted dump")
def test_c7_a_restore_without_the_private_key_old_and_new_credentials_both_verify(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path / "before")
    old = _issue(client, bearer, ids[0], "model_id")
    folder, _manifest, failures = backup_script.take_backup(tmp_path / "before", tmp_path / "backups")
    assert failures == [] and not list(folder.rglob(ik.KEY_FILE)), "the backup holds the private key"
    restored = tmp_path / "after"
    shutil.copytree(folder, restored)
    with RigClock().installed():
        svc2 = create_services(kb_db_path=restored / "kb.db", chroma_dir=restored / "chroma",
                               eval_log_path=restored / "metrics.jsonl", archive_db_path=restored / "archive.db",
                               snapshots_dir=restored / "snapshots", provider=svc.provider)
        client2 = TestClient(create_app(svc2))
        new_id = _turn(client2, bearer, "and what does rest improve?")
        asyncio.run(svc2.kairos.maybe_seal_governance(force=True))
    new = _issue(client2, bearer, new_id, "model_id")
    identity = client2.get("/identity").json()
    assert [k["reason"] for k in identity["keys"]] == ["first", "rotation"], identity["keys"]
    assert {s["key_id"] for s in old["signatures"]} == {identity["keys"][0]["key_id"]}
    assert {s["key_id"] for s in new["signatures"]} == {identity["keys"][1]["key_id"]}
    for credential in (old, new):
        rc, out = _offline(tmp_path, credential, identity)
        assert rc == 0 and "nexus-verify: VERIFIED" in out, out
