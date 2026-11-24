"""W9's delta check 1 (FAIL, 18 November): its three must-fix, and the should-fix taken now.

WHAT IS HELD HERE
    D1  (MF-D1-1) A rotation closes the old key's range at the next block the moment the new key exists, not when
        that block is appended: after a restore (D1) and after a key file moved aside (D1b), the identity served
        in the window closes the old key and declares the pending key from the same block, a copy of the old key
        signing a block past it fails against that identity, and the ranges read the same once the block is
        appended.
    D2  (MF-D1-2) The out-of-period failure names the key's range and the only evidence that would date the block
        (an anchor, or a copy received before the range closed); it names no restore and never sends the verifier
        to another identity.
    D3  (MF-D1-3) An anchored credential verifies with the authority's certificate saved as UTF-16 (with and without
        a BOM: Windows PowerShell 5.1's `>`), with a UTF-8 BOM, with CRLF line ends, and as DER.
    E1-E8  Should-fix: the reverse of F2 (a made-up binding sharing the sealing block's index); a reveal receipt
        saved with a BOM or as UTF-16; the receipt route's unreadable key; the MCP commit refusal holds the note's
        length only; an uncommitted decision field and a reveal's malformed number are named; a key file that
        exists and cannot be read is `key_unreadable` (HTTP and MCP); a block whose key history cannot be read is
        appended unsigned; `no_anchor` names each undated block.

LAW 16. Run on ef81619 (34bbe93 with the run branch merged) before the fixes: D1, D1b, D2, D3's two UTF-16 cases,
E6, E7 and E8 failed there on their consequence. D3's other three cases, E1 to E5 passed there: they are the
surviving mutants' tests (NV-c, NV-v, SV-c, MC-a, NV-o, NV-r), each run against its mutant (RUN_LOG).
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.memory import anchor as an
from backend.memory import instance_key as ik
from backend.server import create_app
from scripts import backup as backup_script
from scripts import nexus_verify as nv
from tests import _proc
from tests import _tsa
from tests.fixtures.ce_rig import RigClock
from tests.test_w8_bindings import _layout_1_sealed, _pre_run_services, _seal
from tests.test_w8_proofs import _sealed, _turn
from tests.test_w9_credentials import _issue
from tests.test_w9_verifier_fixes import _held, _names, _services

REPO = Path(__file__).resolve().parent.parent
VERIFY = str(REPO / "scripts" / "nexus_verify.py")


def _forge_at(credential: dict, key, index: int) -> dict:
    """A made-up layout-2 block at `index` whose lineage tree is the record's leaf alone, signed `seal` by `key`:
    what a copy of a closed key can make."""
    forged = copy.deepcopy(credential)
    p = forged["proof"]
    assert p["block"]["layout"] == 2, "non-vacuity: the record is in a layout-2 block"
    header = copy.deepcopy(p["block"]["header"])
    header["index"] = index
    header["roots"] = {"lineage": {"root": nv._leaf_hash(bytes.fromhex(p["leaf"])).hex(), "size": 1}}
    block_hash = nv._canonical_hash(header)
    p["block"] = {"layout": 2, "index": index, "block_hash": block_hash, "header": header}
    p.update(leaf_index=0, tree_size=1, path=[])
    p.pop("binding", None)
    forged["signatures"] = [{"block_index": index, "block_hash": block_hash, "key_id": key.key_id, "signed": "seal",
                             "value": key.sign(ik.block_statement(index, block_hash)).hex()}]
    forged["anchors"] = []
    return forged


def _ranges(keys: list) -> list:
    return [(k["key_id"], k["announced_in"] if k["announced_in"] is not None else k.get("from_block"),
             k["until_block"]) for k in keys]


def _the_window_is_closed(window: list, old_id: str, head: int, honest: dict, forged: dict) -> None:
    assert window[0]["key_id"] == old_id and window[0]["until_block"] == head + 1, window
    assert window[-1].get("pending") is True and window[-1].get("from_block") == head + 1, window
    assert nv.verify_credential(honest, window)["verified"], "an honest credential sealed before the rotation"
    got = nv.verify_credential(forged, window)
    assert not got["verified"] and "signature_outside_key_period" in _names(got), got


def _moved_aside(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path)
    honest = _issue(client, bearer, ids[0], "model_id")
    path = Path(svc.instance_key_path)
    old = ik.load_or_create(path)
    forged = _forge_at(honest, old, 1000)
    assert nv.verify_credential(forged, client.get("/identity").json()["keys"])["verified"], \
        "non-vacuity: where the old key's range is open, the forgery verifies"
    path.rename(path.with_name("moved-aside.json"))
    ik._cache.pop(str(path.resolve()), None)         # the process that finds no key file is a new one
    head = svc.archive_chain.latest().index
    return {"svc": svc, "client": client, "honest": honest, "old": old, "forged": forged, "head": head,
            "window": client.get("/identity").json()["keys"]}


# ---------- D1 (MF-D1-1) ----------

@pytest.mark.sqlite_only("a restore here is the backup folder's files copied; on Postgres it is the hosted dump")
def test_d1_a_restore_closes_the_old_keys_range_before_any_block_is_appended(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path / "before")
    honest = _issue(client, bearer, ids[0], "model_id")
    old = ik.load_or_create(tmp_path / "before" / ik.KEY_FILE)
    forged = _forge_at(honest, old, 1000)
    assert nv.verify_credential(forged, client.get("/identity").json()["keys"])["verified"], "non-vacuity"
    folder, _manifest, failures = backup_script.take_backup(tmp_path / "before", tmp_path / "backups")
    assert failures == []
    restored = tmp_path / "after"
    shutil.copytree(folder, restored)
    with RigClock().installed():
        svc2 = _services(restored, provider=svc.provider)
        client2 = TestClient(create_app(svc2))
        head = svc2.archive_chain.latest().index
        window = client2.get("/identity").json()["keys"]
        _the_window_is_closed(window, old.key_id, head, honest, forged)
        _turn(client2, bearer, "and what does rest improve?")
        asyncio.run(svc2.kairos.maybe_seal_governance(force=True))
    after = client2.get("/identity").json()["keys"]
    assert after[-1]["announced_in"] == head + 1 and _ranges(after) == _ranges(window), (window, after)


def test_d1b_a_key_moved_aside_closes_its_range_before_any_block_is_appended(tmp_path):
    m = _moved_aside(tmp_path)
    _the_window_is_closed(m["window"], m["old"].key_id, m["head"], m["honest"], m["forged"])
    new = ik.load_or_create(m["svc"].instance_key_path)
    assert new.key_id == m["window"][-1]["key_id"], "non-vacuity: the pending key is the new key file's"
    backdated = nv.verify_credential(_forge_at(m["honest"], new, m["head"]), m["window"])
    assert not backdated["verified"] and "before the block that announced it" in " ".join(backdated["failures"])
    m["svc"].archive_chain.append({"note": "w9 delta 1"}, tmp_path / "no-snapshot")
    after = m["client"].get("/identity").json()["keys"]
    assert after[-1]["announced_in"] == m["head"] + 1 and _ranges(after) == _ranges(m["window"]), after


# ---------- D2 (MF-D1-2) ----------

def test_d2_the_failure_names_what_would_date_the_block_and_no_restore(tmp_path):
    m = _moved_aside(tmp_path)
    said = " ".join(nv.verify_credential(m["forged"], m["window"])["failures"])
    assert "anchor" in said and "received and checked before" in said, said
    assert "restore" not in said and "identity served" not in said, said
    assert f"blocks {m['window'][0]['announced_in']} to {m['head']}" in said, said


# ---------- D3 (MF-D1-3) ----------

@pytest.mark.parametrize("save", ["utf-16", "utf-16-le", "utf-8-sig", "crlf", "der"])
def test_d3_an_anchored_credential_verifies_whatever_the_certificate_was_saved_as(tmp_path, save):
    authority = _tsa.StampAuthority()
    url, stop = authority.serve()
    try:
        svc, client, bearer, ids = _sealed(tmp_path / "store")
        stamped = asyncio.run(an.anchor_pending(svc.archive_chain, url=url))
    finally:
        stop()
    credential = _issue(client, bearer, ids[0], "model_id")
    assert stamped["anchored"] >= 1 and credential["anchors"], ("non-vacuity: the block is anchored", stamped)
    pem = authority.cert_pem
    der = base64.b64decode("".join(l for l in pem.decode("ascii").splitlines() if l and not l.startswith("-----")))
    saved = {"utf-16": pem.decode("ascii").encode("utf-16"), "utf-16-le": pem.decode("ascii").encode("utf-16-le"),
             "utf-8-sig": b"\xef\xbb\xbf" + pem, "crlf": pem.replace(b"\n", b"\r\n"), "der": der}[save]
    where = tmp_path / "stranger"
    where.mkdir()
    (where / "credential.json").write_text(json.dumps(credential), encoding="utf-8")
    (where / "identity.json").write_text(json.dumps(client.get("/identity").json()), encoding="utf-8")
    (where / "authority.pem").write_bytes(saved)
    proc = _proc.run([sys.executable, "-I", VERIFY, "credential.json", "--identity", "identity.json", "--tsa-cert",
                      "authority.pem"], cwd=where, timeout=120, capture_output=True, text=True, encoding="utf-8",
                     errors="replace")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0 and "nexus-verify: VERIFIED" in proc.stdout and "anchored:" in out, out


# ---------- E1-E8 (should-fix) ----------

@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture")
def test_e1_a_made_up_binding_sharing_the_sealing_blocks_index_is_named(tmp_path):
    svc = _pre_run_services(tmp_path)
    lid = sorted(_layout_1_sealed(svc))[0]
    _seal(svc)
    client = TestClient(create_app(svc))
    bearer = {"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"}
    good = _issue(client, bearer, lid, "ts")
    keys = client.get("/identity").json()["keys"]
    assert nv.verify_credential(good, keys)["verified"], "non-vacuity"
    forged = copy.deepcopy(good)
    p, salt, value = forged["proof"], bytes(range(32)), 0.5
    commitments = {n: bytes.fromhex(c) for n, c in forged["commitments"].items()}
    commitments["ts"] = nv.commitment(salt, value)
    root = nv.commitment_root(commitments).hex()
    forged["commitments"] = {n: c.hex() for n, c in commitments.items()}
    forged["disclosed"] = {"ts": {"value": value, "salt": salt.hex()}}
    index, leaf = p["block"]["index"], p["record_hash"] + root
    header = copy.deepcopy(p["binding"]["block"]["header"])
    header["index"] = index
    header["roots"] = {"binding": {"root": nv._leaf_hash(bytes.fromhex(leaf)).hex(), "size": 1}}
    p["binding"].update(commitment_root=root, leaf=leaf, leaf_index=0, tree_size=1, path=[],
                        block={"layout": 2, "index": index, "block_hash": nv._canonical_hash(header),
                               "header": header})
    forged["signatures"] = [s for s in forged["signatures"] if s["block_index"] == index]
    got = nv.verify_credential(forged, keys)
    assert not got["verified"] and {"malformed", "unsigned"} & set(_names(got)), got


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_e2_a_reveal_receipt_saved_with_a_bom_or_as_utf16_is_read(tmp_path, encoding):
    svc, client, bearer, made = _held(tmp_path / "store", note="the held note")
    receipt = client.get(f"/commitments/{made['commitment_id']}/receipt", headers=bearer).json()
    where = tmp_path / "later"
    where.mkdir()
    (where / "receipt.json").write_text(json.dumps(receipt), encoding=encoding)
    (where / "identity.json").write_text(json.dumps(client.get("/identity").json()), encoding=encoding)
    with open(where / "note.txt", "w", encoding="utf-8", newline="") as f:
        f.write("the held note")
    proc = _proc.run([sys.executable, "-I", VERIFY, "--reveal", "receipt.json", "--content", "note.txt", "--salt",
                      made["salt"], "--identity", "identity.json"], cwd=where, timeout=120, capture_output=True,
                     text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "VERIFIED" in proc.stdout, proc.stdout + proc.stderr


def test_e3_the_receipt_route_declares_an_unreadable_key(tmp_path, monkeypatch):
    svc, client, bearer, made = _held(tmp_path)

    def unreadable(path, **_):
        raise PermissionError(13, "Permission denied", "/a/private/place/" + ik.KEY_FILE)
    monkeypatch.setattr(ik, "load_or_create", unreadable)
    resp = client.get(f"/commitments/{made['commitment_id']}/receipt", headers=bearer)
    assert resp.status_code == 503 and resp.json()["refused"] == "key_unreadable", resp.text
    assert "/a/private/place" not in resp.text, resp.text


def test_e4_the_mcp_commit_refusal_row_holds_the_notes_length_only(tmp_path):
    from backend.mcp.mcp_server import build_server
    from tests.test_mcp2_build import _call, _runtime
    rt = _runtime(tmp_path / "d")
    srv = build_server(rt, with_lifespan=False)
    for note in ("meet at noon", "pay the bill"):
        with pytest.raises(Exception, match="Law 3"):
            _call(srv, "nexus_commit", content=note, confirmed=False)
    rows = [tuple(r) for r in rt.services.recalibration.conn.execute(
        "SELECT route, request_sha256 FROM refused_requests ORDER BY ts, event_id")]
    assert len(rows) == 2 and rows[0] == rows[1], rows


def test_e5_an_uncommitted_decision_field_and_a_reveals_malformed_number_are_named(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path / "a")
    keys = client.get("/identity").json()["keys"]
    credential = _issue(client, bearer, ids[0], "model_id")
    credential["decision_fields"] = ["no_such_field"]
    got = nv.verify_credential(credential, keys)
    assert not got["verified"] and "malformed" in _names(got), got
    svc, client, bearer, made = _held(tmp_path / "b", note="the held note")
    receipt = client.get(f"/commitments/{made['commitment_id']}/receipt", headers=bearer).json()
    receipt["signatures"][0]["block_index"] = receipt["block"]["index"] = 2 ** 70
    got = nv.verify_reveal(receipt, "the held note", made["salt"], client.get("/identity").json()["keys"])
    assert not got["verified"] and any(f.startswith("malformed") for f in got["failures"]), got


def test_e6_a_key_file_that_exists_and_cannot_be_read_is_unreadable_not_a_mismatch(tmp_path, monkeypatch):
    svc, client, bearer, ids = _sealed(tmp_path)
    path = Path(svc.instance_key_path)
    assert path.is_file(), "non-vacuity: the key file exists"
    real = Path.read_text

    def denied(self, *a, **k):
        if self.name == ik.KEY_FILE:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *a, **k)
    ik._cache.pop(str(path.resolve()), None)
    monkeypatch.setattr(Path, "read_text", denied)
    resp = client.get("/identity")
    assert resp.status_code == 503 and resp.json()["refused"] == "key_unreadable", resp.text
    from backend.mcp.mcp_server import build_server
    from tests.test_mcp2_build import _call, _runtime
    rt = _runtime(tmp_path / "mcp")
    ik._cache.clear()
    status = _call(build_server(rt, with_lifespan=False), "nexus_status")
    assert status["identity"]["state"] == "key_unreadable" and str(tmp_path) not in json.dumps(status["identity"])


def test_e7_a_block_whose_key_history_cannot_be_read_is_appended_unsigned(tmp_path, monkeypatch):
    svc, client, bearer, ids = _sealed(tmp_path)
    chain = svc.archive_chain
    before = chain.unsigned_appends

    def unreadable():
        raise OSError("the store could not be read")
    monkeypatch.setattr(chain, "announced_keys", unreadable)
    block = chain.append({"note": "w9 delta 1"}, tmp_path / "no-snapshot")
    assert block.instance_signature is None and chain.unsigned_appends == before + 1, block
    monkeypatch.undo()
    assert chain.append({"note": "w9 delta 1, after"}, tmp_path / "no-snapshot").instance_signature is not None


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture")
def test_e8_no_anchor_names_each_block_nothing_dates(tmp_path):
    authority = _tsa.StampAuthority()
    url, stop = authority.serve()
    try:
        svc = _pre_run_services(tmp_path)
        lid = sorted(_layout_1_sealed(svc))[0]
        _seal(svc)
        stamped = asyncio.run(an.anchor_pending(svc.archive_chain, url=url))
    finally:
        stop()
    client = TestClient(create_app(svc))
    bearer = {"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"}
    credential = _issue(client, bearer, lid, "ts")
    seal_index = credential["proof"]["block"]["index"]
    assert stamped["anchored"] >= 1 and credential["anchors"], ("non-vacuity: the binding block is anchored", stamped)
    got = nv.verify_credential(credential, client.get("/identity").json()["keys"], authority.cert_pem)
    assert got["verified"], got
    assert any(n.startswith("no_anchor") and f"block {seal_index} " in n for n in got["notes"]), got["notes"]
    dated = credential["anchors"][0]["block_index"]
    assert dated != seal_index and not any(n.startswith("no_anchor") and f"block {dated} " in n
                                           for n in got["notes"]), got["notes"]
