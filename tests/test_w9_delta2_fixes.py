"""W9's delta checks 2 and 3 (FAIL, 18 November): their must-fix, and the should-fix taken in delta check 2's round.

WHAT IS HELD HERE
    J1  (MF-D2-1) Blocks appended while the new key cannot be read, after a rotation, never widen the old key's
        range: a copy of the old key signing at those indices fails against every identity served from the window
        on, and the old key's range reads the same in each.
    J1b (MF-D2-1) The same for a block appended while the chain cannot read its key history.
    J2  A record sealed in a block appended while the key could not be read is credentialed at once by the current
        key, and the credential still verifies after a rotation.
    J2b (MF-D3-1) After a restore, an at-issue credential for a block the backup holds still verifies: the old
        key's range ends where the new key was made, past every block the restored archive holds.
    J3  On a fresh store the identity served before the first block admits that block's seal (`from_block` 0).
    J4  A block whose announcement cannot be made because the key cannot be read is appended unsigned, even when
        the signer itself would work.
    J5  A signature made at issue past its range is named with the range it was held to, offers no anchor (an
        anchor dates a block, not a signature made at issue), and names no "pinned" identity.
    J6  A scratch key file the platform will not remove is named as a copy of the private key.
    J7  A certificate saved as UTF-16 big-endian with no BOM reads.
    J8  A certificate that cannot be read is named as the certificate, not the receipt.
    J9  A store error on /identity, the credential route or the receipt route is never reported as the key's.
    J10 A key rotated away and back keeps each of its ranges inside its own period (the put-back key's own
        creation point falls before the previous period, so its announcement starts its range).
    J11 A key file that cannot be made is declared so, over HTTP and MCP; a store error in the MCP status names no
        key.
    J12 A key announced in a block it could not sign keeps that block in its own range, and a copy of it signing
        past where the next key was made fails.
    J14 Every path that can make the key records where the archive stood (`made_at_block`): /identity, the
        credential route, MCP's status, and the signer at append.
    J15 Every call that can make the key passes its archive (a derived AST guard over the tracked backend), with a
        planted companion.
    J16 A key file made beside another archive (a take-over point past this archive's next block) closes the old
        range no later than the next block.
    J17 A key file whose take-over point is not a block index is refused by name.

LAW 16. Delta check 2's round, run on 04643ef before its fixes: J1, J1b, J5, J7, J8, J9 and J11 failed there on
their consequence; J3, J4, J6 and J10 passed there (mutants' tests: IK-d2, CH-b, IK-b and the range bound). Delta
check 3's round, run on 655b577 before its fixes: J2 (on the refusal it removes), J2b, J12, J14 and J15 failed
there on their consequence, and J5 on the wording it changes; J16 and J17 are this round's mutants' tests.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.memory import anchor as an
from backend.memory import instance_key as ik
from backend.server import create_app
from scripts import backup as backup_script
from scripts import nexus_verify as nv
from tests import _tsa
from tests.fixtures.ce_rig import RigClock
from tests.test_w8_proofs import _console, _sealed, _turn
from tests.test_w9_credentials import _issue
from tests.test_w9_delta_fixes import _forge_at, _moved_aside, _ranges
from tests.test_w9_verifier_fixes import _held, _names, _services

REPO = Path(__file__).resolve().parent.parent


def _unreadable_key(mp, path: Path) -> None:
    """The key file is there and the process cannot read it (another owner's 0600 file, a Windows lock)."""
    real = Path.read_text

    def denied(self, *a, **k):
        if self.name == ik.KEY_FILE:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *a, **k)
    ik._cache.pop(str(Path(path).resolve()), None)
    mp.setattr(Path, "read_text", denied)


def _refuted_everywhere(forged: dict, served: list) -> None:
    for keys in served:
        got = nv.verify_credential(forged, keys)
        assert not got["verified"] and "signature_outside_key_period" in _names(got), (keys, got)


# ---------- J1, J1b (MF-D2-1) ----------

def test_j1_blocks_appended_while_the_new_key_cannot_be_read_never_widen_the_old_range(tmp_path, monkeypatch):
    m = _moved_aside(tmp_path)                    # the window's /identity made the new key
    chain, path, head = m["svc"].archive_chain, Path(m["svc"].instance_key_path), m["head"]
    served = [m["window"]]
    with monkeypatch.context() as mp:
        _unreadable_key(mp, path)
        for note in ("a", "b"):
            assert chain.append({"note": note}, tmp_path / "x").instance_signature is None, "non-vacuity: unsigned"
    served.append(m["client"].get("/identity").json()["keys"])
    assert chain.append({"note": "announces the new key"}, tmp_path / "x").fingerprint.get("identity_seal")
    served.append(m["client"].get("/identity").json()["keys"])
    for at in (head + 1, head + 2):
        _refuted_everywhere(_forge_at(m["honest"], m["old"], at), served)
    assert [_ranges(k)[0] for k in served] == [_ranges(served[0])[0]] * 3, served
    assert served[-1][0]["active_to"] == chain.get_block(head + 1).timestamp, "the first block it did not sign"


def test_j1b_a_block_appended_while_the_history_cannot_be_read_never_widens_the_old_range(tmp_path, monkeypatch):
    m = _moved_aside(tmp_path)
    chain, head = m["svc"].archive_chain, m["head"]
    chain._announced = None                       # the history is read from the store again, and that read fails once
    real, calls = chain.announced_keys, []

    def once():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("the store could not be read")
        return real()
    monkeypatch.setattr(chain, "announced_keys", once)
    assert chain.append({"note": "a"}, tmp_path / "x").instance_signature is None, "non-vacuity: unsigned"
    monkeypatch.undo()
    served = [m["window"], m["client"].get("/identity").json()["keys"]]
    chain.append({"note": "announces the new key"}, tmp_path / "x")
    served.append(m["client"].get("/identity").json()["keys"])
    _refuted_everywhere(_forge_at(m["honest"], m["old"], head + 1), served)
    assert [_ranges(k)[0] for k in served] == [_ranges(served[0])[0]] * 3, served


# ---------- J2 ----------

def _seal_turn(svc, client, bearer, question: str) -> str:
    with RigClock().installed():
        lid = _turn(client, bearer, question)
        asyncio.run(svc.kairos.maybe_seal_governance(force=True))
    return lid


def test_j2_a_record_sealed_while_the_key_could_not_be_read_is_credentialed_at_once(tmp_path, monkeypatch):
    svc, client, bearer, _ids = _sealed(tmp_path)
    path = Path(svc.instance_key_path)
    with monkeypatch.context() as mp:
        _unreadable_key(mp, path)
        lid = _seal_turn(svc, client, bearer, "and what does rest improve?")
    assert svc.archive_chain.latest().instance_signature is None, "non-vacuity: the record's block is unsigned"
    credential = _issue(client, bearer, lid, "model_id")
    assert [s["signed"] for s in credential["signatures"]] == ["issue"], credential["signatures"]
    assert nv.verify_credential(credential, client.get("/identity").json()["keys"])["verified"]
    path.rename(path.with_name("moved-aside.json"))
    ik._cache.pop(str(path.resolve()), None)
    window = client.get("/identity").json()["keys"]
    _seal_turn(svc, client, bearer, "and what does a walk improve?")
    for keys in (window, client.get("/identity").json()["keys"]):
        assert nv.verify_credential(credential, keys)["verified"], keys


@pytest.mark.sqlite_only("a restore here copies the backup folder's SQLite files (kb.db, archive.db); on Postgres "
                         "it is the hosted dump")
def test_j2b_after_a_restore_an_at_issue_credential_for_a_block_the_backup_holds_verifies(tmp_path, monkeypatch):
    svc, client, bearer, _ids = _sealed(tmp_path / "before")
    with monkeypatch.context() as mp:
        _unreadable_key(mp, Path(svc.instance_key_path))
        lid = _seal_turn(svc, client, bearer, "and what does rest improve?")
    held = svc.archive_chain.latest()
    assert held.instance_signature is None, "non-vacuity: the record's block is unsigned"
    folder, _manifest, failures = backup_script.take_backup(tmp_path / "before", tmp_path / "backups")
    assert failures == []
    _seal_turn(svc, client, bearer, "and what does sleep improve?")      # the old key signs a later block
    credential = _issue(client, bearer, lid, "model_id")
    assert credential["signatures"][0]["signed"] == "issue", credential["signatures"]
    assert nv.verify_credential(credential, client.get("/identity").json()["keys"])["verified"]
    shutil.copytree(folder, tmp_path / "after")
    with RigClock().installed():
        svc2 = _services(tmp_path / "after", provider=svc.provider)
        client2 = TestClient(create_app(svc2))
        assert svc2.archive_chain.get_block(held.index) is not None, "non-vacuity: the backup holds the block"
        window = client2.get("/identity").json()["keys"]
        _turn(client2, bearer, "and what does a walk improve?")
        asyncio.run(svc2.kairos.maybe_seal_governance(force=True))
    for keys in (window, client2.get("/identity").json()["keys"]):
        assert nv.verify_credential(credential, keys)["verified"], keys


# ---------- J3, J4 ----------

def test_j3_on_a_fresh_store_the_identity_served_before_the_first_block_admits_it(tmp_path):
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        assert svc.archive_chain.latest() is None, "non-vacuity: no block yet"
        window = client.get("/identity").json()["keys"]
        assert [(k.get("pending"), k.get("from_block")) for k in window] == [(True, 0)], window
        lid = _turn(client, bearer, "what does sleep improve?")
        asyncio.run(svc.kairos.maybe_seal_governance(force=True))
    credential = _issue(client, bearer, lid, "model_id")
    assert credential["signatures"][0]["block_index"] == 0 and credential["signatures"][0]["signed"] == "seal"
    assert nv.verify_credential(credential, window)["verified"], window


def test_j4_a_block_whose_key_cannot_be_read_to_announce_is_appended_unsigned(tmp_path, monkeypatch):
    svc, _client, _bearer, _ids = _sealed(tmp_path)
    chain = svc.archive_chain
    before = chain.unsigned_appends

    def unreadable():
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(chain, "identity_source", unreadable)       # the signer itself still works
    block = chain.append({"note": "j4"}, tmp_path / "x")
    assert block.instance_signature is None and chain.unsigned_appends == before + 1, block


# ---------- J5 ----------

def test_j5_a_signature_made_at_issue_past_its_range_offers_no_anchor(tmp_path):
    m = _moved_aside(tmp_path)
    forged = _forge_at(m["honest"], m["old"], 1000)
    forged["signatures"][0]["signed"] = "issue"
    said = " ".join(nv.verify_credential(forged, m["window"])["failures"])
    assert f"blocks before {m['window'][0]['until_block']}" in said and "made at issue" in said, said
    assert "anchor" not in said and "pinned identity" not in said and "checked against" in said, said
    assert "where its instance moved to a new key" in said, said


# ---------- J6 ----------

def test_j6_a_scratch_file_left_behind_is_named_as_a_copy_of_the_key(tmp_path, monkeypatch, caplog):
    real = Path.unlink

    def held(self, *a, **k):
        if self.name.startswith("." + ik.KEY_FILE + "."):
            raise PermissionError(13, "held by another process", str(self))
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", held)
    with caplog.at_level(logging.WARNING, logger=ik.logger.name):
        ik.load_or_create(tmp_path / ik.KEY_FILE)
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "copy of the private key" in said and "by hand" in said, said


# ---------- J7, J8 ----------

def _anchored(tmp_path):
    authority = _tsa.StampAuthority()
    url, stop = authority.serve()
    try:
        svc, client, bearer, ids = _sealed(tmp_path)
        stamped = asyncio.run(an.anchor_pending(svc.archive_chain, url=url))
    finally:
        stop()
    credential = _issue(client, bearer, ids[0], "model_id")
    assert stamped["anchored"] >= 1 and credential["anchors"], ("non-vacuity: the block is anchored", stamped)
    return authority, credential, client.get("/identity").json()["keys"]


def test_j7_a_certificate_saved_as_utf16_big_endian_without_a_bom_reads(tmp_path):
    authority, credential, keys = _anchored(tmp_path)
    got = nv.verify_credential(credential, keys, authority.cert_pem.decode("ascii").encode("utf-16-be"))
    assert got["verified"] and any(n.startswith("anchored") for n in got["notes"]), got


def test_j8_a_certificate_that_cannot_be_read_is_named_as_the_certificate(tmp_path):
    _authority, credential, keys = _anchored(tmp_path)
    got = nv.verify_credential(credential, keys, b"not a certificate")
    said = " ".join(got["failures"])
    assert not got["verified"] and "certificate" in said and "cannot be read" in said, got


# ---------- J9 ----------

def test_j9_a_store_error_is_never_reported_as_the_keys(tmp_path, monkeypatch):
    svc, _client, bearer, made = _held(tmp_path)
    lid = svc.lineage.conn.execute("SELECT lineage_id FROM lineage_records LIMIT 1").fetchone()["lineage_id"]
    raw = TestClient(create_app(svc), raise_server_exceptions=False)
    assert raw.get(f"/lineage/{lid}/credential", headers=bearer).status_code == 200, "non-vacuity"

    def broken(*a, **k):
        raise OSError("the store could not be read")
    monkeypatch.setattr(svc.archive_chain, "announced_keys", broken)
    resp = raw.get("/identity")
    assert resp.status_code == 500 and "key_unreadable" not in resp.text, resp.text
    monkeypatch.undo()
    monkeypatch.setattr(svc.archive_chain, "get_block", broken)
    for url in (f"/lineage/{lid}/credential", f"/commitments/{made['commitment_id']}/receipt"):
        resp = raw.get(url, headers=bearer)
        assert resp.status_code == 500 and "key_unreadable" not in resp.text, (url, resp.text)


# ---------- J10, J11 ----------

def test_j10_a_key_rotated_away_and_back_keeps_each_range_inside_its_own_period(tmp_path):
    m = _moved_aside(tmp_path)
    svc, client, path = m["svc"], m["client"], Path(m["svc"].instance_key_path)
    svc.archive_chain.append({"note": "announces the second key"}, tmp_path / "x")
    path.rename(path.with_name("second.json"))
    path.with_name("moved-aside.json").rename(path)                 # the first key put back
    ik._cache.pop(str(path.resolve()), None)
    svc.archive_chain.append({"note": "announces the first key again"}, tmp_path / "x")
    keys = client.get("/identity").json()["keys"]
    assert [k["key_id"] for k in keys] == [m["old"].key_id, keys[1]["key_id"], m["old"].key_id], keys
    assert keys[0]["until_block"] <= keys[1]["announced_in"] and keys[1]["until_block"] == keys[2]["announced_in"]
    assert keys[2]["until_block"] is None and nv.verify_credential(m["honest"], keys)["verified"], keys


def test_j11_a_key_that_cannot_be_made_is_declared_so_and_a_store_error_in_mcp_status_names_no_key(tmp_path,
                                                                                                    monkeypatch):
    svc, client, _bearer, _ids = _sealed(tmp_path)

    def cannot(path, **_):
        raise FileNotFoundError(2, "No such file or directory")
    monkeypatch.setattr(ik, "load_or_create", cannot)
    detail = client.get("/identity").json()["detail"]
    assert "cannot be read or made" in detail and "left as it is" not in detail, detail
    monkeypatch.undo()
    from backend.mcp.mcp_server import build_server
    from tests.test_mcp2_build import _call, _runtime
    rt = _runtime(tmp_path / "mcp")
    srv = build_server(rt, with_lifespan=False)
    assert _call(srv, "nexus_status")["identity"]["keys"], "non-vacuity: the status carries the identity"
    with monkeypatch.context() as mp:
        mp.setattr(ik, "load_or_create", cannot)
        said = _call(srv, "nexus_status")["identity"]
    assert said["state"] == "key_unreadable" and "cannot be read or made" in said["detail"], said

    def broken(*a, **k):
        raise OSError("the store could not be read")
    monkeypatch.setattr(rt.services.archive_chain, "announced_keys", broken)
    try:
        status = _call(srv, "nexus_status")
    except Exception as e:                          # the tool fails, naming no key
        assert "key_unreadable" not in str(e), e
    else:
        assert status["identity"].get("state") != "key_unreadable", status["identity"]


# ---------- J12, J14, J15 ----------

def test_j12_a_key_announced_in_a_block_it_could_not_sign_keeps_that_block_and_no_more(tmp_path, monkeypatch):
    m = _moved_aside(tmp_path)
    svc, client, path = m["svc"], m["client"], Path(m["svc"].instance_key_path)
    second = ik.load_or_create(path, chain=svc.archive_chain)

    def fails(index, block_hash):
        raise OSError("the signer failed after the announcement was read")
    with monkeypatch.context() as mp:
        mp.setattr(svc.archive_chain, "signer", fails)              # planted: the announcement read, the signing not
        block = svc.archive_chain.append({"note": "announces the second key, unsigned"}, tmp_path / "x")
    assert block.fingerprint["identity_seal"]["key_id"] == second.key_id and block.instance_signature is None
    path.rename(path.with_name("second.json"))
    ik._cache.pop(str(path.resolve()), None)
    keys = client.get("/identity").json()["keys"]
    assert keys[1]["key_id"] == second.key_id and keys[1]["until_block"] == block.index + 1, keys
    _refuted_everywhere(_forge_at(m["honest"], second, block.index + 1), [keys])


def test_j14_every_path_that_can_make_the_key_records_where_the_archive_stood(tmp_path):
    from backend.mcp.mcp_server import build_server
    from tests.test_mcp2_build import _call, _runtime
    for path_name in ("identity", "credential", "signer", "mcp"):
        root = tmp_path / path_name
        if path_name == "mcp":
            rt = _runtime(root)
            svc = rt.services
        else:
            svc, client, bearer, _ids = _sealed(root)
        key_path = Path(svc.instance_key_path)
        ik._cache.pop(str(key_path.resolve()), None)
        if key_path.exists():
            key_path.unlink()                                       # the next use makes a new key, as a restore does
        expected = svc.archive_chain.latest().index + 1 if svc.archive_chain.latest() is not None else 0
        if path_name == "identity":
            client.get("/identity")
        elif path_name == "credential":
            client.get("/lineage/no-such-record/credential", headers=bearer)
        elif path_name == "signer":
            svc.archive_chain.append({"note": "j14"}, root / "x")
        else:
            _call(build_server(rt, with_lifespan=False), "nexus_status")
        made = json.loads(key_path.read_text(encoding="utf-8")).get("made_at_block")
        assert made == expected, (path_name, made, expected)


def _calls_without_archive(source: str) -> list:
    """Each call to `load_or_create` in `source` that does not pass `chain=`: a key such a call makes records
    no take-over point, and the previous key's range would end at the next announcement instead."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
            if name == "load_or_create" and not any(k.arg == "chain" for k in node.keywords):
                out.append(node.lineno)
    return out


def test_j15_every_call_that_can_make_the_key_passes_its_archive():
    from tests._tracked import tracked_backend
    found = {}
    population = [Path(p) for p in tracked_backend() if Path(p).suffix == ".py"]
    assert len(population) > 50, "non-vacuity: the tracked backend is the population"
    for path in population:
        lines = _calls_without_archive((path if path.is_absolute() else REPO / path).read_text(encoding="utf-8"))
        if lines:
            found[str(path)] = lines
    assert not found, found
    planted = "from backend.memory import instance_key as k\nkey = k.load_or_create(p, now=1.0)\n"
    assert _calls_without_archive(planted) == [2], "the guard names a call that passes no archive"


def test_j16_a_key_file_made_beside_another_archive_closes_the_old_range_no_later_than_the_next_block(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path)
    honest = _issue(client, bearer, ids[0], "model_id")
    path = Path(svc.instance_key_path)
    old = ik.load_or_create(path, chain=svc.archive_chain)
    head = svc.archive_chain.latest().index
    path.rename(path.with_name("moved-aside.json"))
    ik._cache.pop(str(path.resolve()), None)
    ik.load_or_create(path)                                         # made with no archive at hand ...
    body = json.loads(path.read_text(encoding="utf-8"))
    body["made_at_block"] = head + 50                               # ... and planted as if beside a longer one
    path.write_text(json.dumps(body), encoding="utf-8")
    ik._cache.pop(str(path.resolve()), None)
    window = client.get("/identity").json()["keys"]
    assert window[0]["key_id"] == old.key_id and window[0]["until_block"] == head + 1, window
    _refuted_everywhere(_forge_at(honest, old, head + 2), [window])


def test_j17_a_key_file_whose_take_over_point_is_not_a_block_index_is_refused_by_name(tmp_path):
    for bad in (True, -1, "3", 2.5):
        path = tmp_path / str(bad) / ik.KEY_FILE
        ik.load_or_create(path)
        body = json.loads(path.read_text(encoding="utf-8"))
        body["made_at_block"] = bad
        path.write_text(json.dumps(body), encoding="utf-8")
        ik._cache.pop(str(path.resolve()), None)
        try:
            ik.load_or_create(path)
        except ik.KeyMismatch as e:
            assert "cannot be read as an Ed25519 key" in str(e), e
        else:
            raise AssertionError(f"made_at_block={bad!r} was read as a block index")
