"""W9's verifier (FAIL, 18 November): the six must-fix, the mutants that survived, and the should-fix taken now.

WHAT IS HELD HERE
    F1  (MF1) An ended key signing past its period is named under every label: `seal`, `issue` and none.
    F2  (MF2) A made-up sealing block that shares the binding block's index is named, never verified through the
        binding's signature.
    F3  (MF3, Ruling 7) A block sealed after the backup a restore came from reads outside its key's period against
        the restored instance's identity, and the failure says what would date the block, never another identity
        to check against (delta check 1); its own instance's identity still vouches for it.
    F4  (MF4) A credential names no lineage id unless it is disclosed, so a guess of an undisclosed field cannot
        be tested against the record hash; with the id disclosed too, the residual the documents declare holds.
    F5  (MF5) The key file and its scratch file are ignored under data/ at any depth.
    F6  (MF6) An unpinned verification fails by name (`key_unpinned`, exit 1), a credential and a reveal alike.
    G1-G12  The surviving mutants' gaps: a bare hash under a full salt and a short salt that opens; a
        non-canonical signature; a receipt's tree size; a key never overwritten; unsigned blocks never anchored;
        a record that does not rebuild, an unbound root and a moved held commitment refused; the cycle anchors;
        an unknown anchor setting sends nothing; a stamp with another nonce, none, or another imprint is refused;
        a binding altered in a credential.
    H1-H4  Should-fix: malformed input is named (never a traceback); `--tsa-cert` with nothing to check says so;
        the /commit refusal row holds no hash of the note; the open /identity names no path.

LAW 16. Run on 60f5327 (the verifier's base plus the encoding fix) before the fixes: F1 to F6, G11's no-nonce
case, H1's two crashes, H2, H3, H4, H5 and H6 failed there on their consequence. G1 to G10, G11's other two cases,
G12 and H1's index cases passed there: they are the surviving mutants' tests, each run against its mutant (RUN_LOG).
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

from backend.memory import anchor as an
from backend.memory import instance_key as ik
from backend.server import create_app, create_services
from scripts import backup as backup_script
from scripts import nexus_verify as nv
from tests import _proc
from tests import _tsa
from tests.fixtures.ce_rig import RigClock
from tests.test_w8_bindings import _layout_1_sealed, _pre_run_services, _seal
from tests.test_w8_proofs import QUESTIONS, _sealed, _turn
from tests.test_w9_credentials import _issue

REPO = Path(__file__).resolve().parent.parent


def _names(result) -> list[str]:
    return [f.split(":", 1)[0] for f in result["failures"]]


def _services(root: Path, provider=None):
    return create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma", eval_log_path=root / "metrics.jsonl",
                           archive_db_path=root / "archive.db", snapshots_dir=root / "snapshots", provider=provider)


def _restored(tmp_path, *, sealed_after_backup: bool = False):
    """C7's flow: a store sealed under K1, backed up, restored without the key (a new key, K2, the rotation
    announced). With `sealed_after_backup`, the first store seals one more record after the backup."""
    svc, client, bearer, ids = _sealed(tmp_path / "before")
    folder, _manifest, failures = backup_script.take_backup(tmp_path / "before", tmp_path / "backups")
    assert failures == []
    later = None
    if sealed_after_backup:
        with RigClock().installed():
            lid = _turn(client, bearer, "and what does sleep improve?")
            asyncio.run(svc.kairos.maybe_seal_governance(force=True))
        later = (_issue(client, bearer, lid, "model_id"), client.get("/identity").json())
    restored = tmp_path / "after"
    shutil.copytree(folder, restored)
    with RigClock().installed():
        svc2 = _services(restored, provider=svc.provider)
        client2 = TestClient(create_app(svc2))
        new_id = _turn(client2, bearer, "and what does rest improve?")
        asyncio.run(svc2.kairos.maybe_seal_governance(force=True))
    return {"svc": svc, "client": client, "bearer": bearer, "ids": ids, "svc2": svc2, "client2": client2,
            "new": _issue(client2, bearer, new_id, "model_id"), "identity2": client2.get("/identity").json(),
            "later": later}


# ---------- F1 (MF1) ----------

@pytest.mark.sqlite_only("a restore here is the backup folder's files copied; on Postgres it is the hosted dump")
def test_f1_an_ended_key_signing_past_its_period_is_named_under_every_label(tmp_path):
    r = _restored(tmp_path)
    keys = r["identity2"]["keys"]
    assert [k["reason"] for k in keys] == ["first", "rotation"] and keys[0]["until_block"] is not None
    old = ik.load_or_create(tmp_path / "before" / ik.KEY_FILE)
    assert old.key_id == keys[0]["key_id"], "non-vacuity: the planted key is the first period's"
    assert nv.verify_credential(r["new"], keys)["verified"], "non-vacuity: the honest credential verifies"
    for label in ("seal", "issue", None, "anything"):
        forged = copy.deepcopy(r["new"])
        sig = forged["signatures"][0]
        sig["key_id"] = old.key_id
        sig["value"] = old.sign(ik.block_statement(int(sig["block_index"]), sig["block_hash"])).hex()
        if label is None:
            sig.pop("signed")
        else:
            sig["signed"] = label
        got = nv.verify_credential(forged, keys)
        assert not got["verified"] and "signature_outside_key_period" in _names(got), (label, got)


# ---------- F2 (MF2) ----------

@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture")
def test_f2_a_made_up_sealing_block_sharing_the_bindings_index_is_named(tmp_path):
    svc = _pre_run_services(tmp_path)
    lid = sorted(_layout_1_sealed(svc))[0]
    _seal(svc)
    client = TestClient(create_app(svc))
    bearer = {"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"}
    good = _issue(client, bearer, lid, "ts")
    keys = client.get("/identity").json()["keys"]
    assert nv.verify_credential(good, keys)["verified"], "non-vacuity"
    forged = copy.deepcopy(good)
    p = forged["proof"]
    index, record_hash = p["binding"]["block"]["index"], p["record_hash"]
    header = copy.deepcopy(p["binding"]["block"]["header"])
    header["timestamp"] = 946684800.0                                   # 1 January 2000
    header["roots"] = {"lineage": {"root": nv._leaf_hash(bytes.fromhex(record_hash)).hex(), "size": 1}}
    p["block"] = {"layout": 2, "index": index, "block_hash": nv._canonical_hash(header), "header": header}
    p.update(leaf=record_hash, leaf_index=0, tree_size=1, path=[])
    p.pop("commitment_root", None)
    forged["signatures"] = [s for s in forged["signatures"] if s["block_index"] == index]
    got = nv.verify_credential(forged, keys)
    assert not got["verified"], got
    assert {"malformed", "unsigned"} & set(_names(got)), got


# ---------- F3 (MF3; Ruling 7) ----------

@pytest.mark.sqlite_only("a restore here is the backup folder's files copied; on Postgres it is the hosted dump")
def test_f3_a_block_sealed_after_the_backup_reads_outside_the_period_and_says_why(tmp_path):
    r = _restored(tmp_path, sealed_after_backup=True)
    later, identity_then = r["later"]
    against_restored = nv.verify_credential(later, r["identity2"]["keys"])
    assert not against_restored["verified"] and "signature_outside_key_period" in _names(against_restored)
    said = " ".join(against_restored["failures"])
    assert "anchor" in said and "restore" not in said and "identity served" not in said, said   # delta check 1
    assert nv.verify_credential(later, identity_then["keys"])["verified"], "the identity served at issue vouches"


# ---------- F4 (MF4) ----------

def test_f4_no_lineage_id_unless_disclosed_so_an_undisclosed_field_cannot_be_guessed(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path)
    lid, question = ids[0], QUESTIONS[0]
    fields = sorted(_issue(client, bearer, lid)["commitments"])
    assert {"lineage_id", "query_hash"} <= set(fields)
    credential = _issue(client, bearer, lid, ",".join(f for f in fields if f not in ("query_hash", "lineage_id")))
    assert lid not in json.dumps(credential), "the record's id is in clear in a credential that does not disclose it"
    assert nv.verify_credential(credential, client.get("/identity").json()["keys"])["verified"]
    # The residual the documents declare: with the id disclosed too, every field but one opens a guess of it.
    everything_else = _issue(client, bearer, lid, ",".join(f for f in fields if f != "query_hash"))
    body = {n: d["value"] for n, d in everything_else["disclosed"].items()}

    def guess(q):
        body["query_hash"] = hashlib.sha256(q.encode("utf-8")).hexdigest()
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest() == everything_else["proof"]["record_hash"]
    assert guess(question) and not guess(question + "?"), "the declared residual no longer holds: correct the docs"


# ---------- F5 (MF5) ----------

def test_f5_the_key_and_its_scratch_file_are_ignored_under_data_at_any_depth():
    paths = ["data/" + ik.KEY_FILE, "data/tester1/" + ik.KEY_FILE, "data/a/b/" + ik.KEY_FILE,
             f"data/.{ik.KEY_FILE}.{'0' * 32}", f"data/tester1/.{ik.KEY_FILE}.{'f' * 32}"]
    shown = [p for p in paths if _proc.run(["git", "check-ignore", "-q", "--no-index", p], cwd=REPO,
                                           capture_output=True, timeout=120).returncode != 0]
    assert shown == [], f"git status would show the instance's private key: {shown}"
    assert ik.DEFAULT_KEY_PATH == "data/" + ik.KEY_FILE


# ---------- F6 (MF6) ----------

def test_f6_an_unpinned_verification_fails_by_name(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path / "store")
    credential = _issue(client, bearer, ids[0], "model_id")
    got = nv.verify_credential(credential)
    assert not got["verified"] and "key_unpinned" in _names(got), got
    where = tmp_path / "unpinned"
    where.mkdir()
    (where / "credential.json").write_text(json.dumps(credential), encoding="utf-8")
    proc = _proc.run([sys.executable, "-I", str(REPO / "scripts" / "nexus_verify.py"), "credential.json"], cwd=where,
                     timeout=120, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 1 and "FAIL key_unpinned" in proc.stdout, proc.stdout + proc.stderr
    made = client.post("/commit", headers=bearer, json={"content": "a note", "confirmed": True}).json()
    asyncio.run(svc.kairos.maybe_seal_governance(force=True))
    receipt = client.get(f"/commitments/{made['commitment_id']}/receipt", headers=bearer).json()
    got = nv.verify_reveal(receipt, "a note", made["salt"])
    assert not got["verified"] and "key_unpinned" in _names(got), got
    assert nv.verify_reveal(receipt, "a note", made["salt"], client.get("/identity").json()["keys"])["verified"]


# ---------- G: the surviving mutants' gaps ----------

def test_g1_a_bare_hash_under_a_full_salt_and_a_short_salt_that_opens_are_unsalted(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path)
    good = _issue(client, bearer, ids[0], "model_id")
    keys = client.get("/identity").json()["keys"]
    value = good["disclosed"]["model_id"]["value"]
    bare = copy.deepcopy(good)
    bare["commitments"]["model_id"] = hashlib.sha256(nv.canonical(value)).hexdigest()
    bare["disclosed"]["model_id"]["salt"] = "11" * 32                    # a full salt: only the bare check names it
    assert "unsalted_hash" in _names(nv.verify_credential(bare, keys))
    short = copy.deepcopy(good)
    salt = b"\x22" * 16
    short["commitments"]["model_id"] = nv.commitment(salt, value).hex()  # a short salt that opens its commitment
    short["disclosed"]["model_id"]["salt"] = salt.hex()
    assert "unsalted_hash" in _names(nv.verify_credential(short, keys))


def test_g2_a_signature_with_s_plus_the_group_order_is_invalid(tmp_path):
    """The code held this already (the verifier measured it); the suite did not (mutant NV6 survived)."""
    svc, client, bearer, ids = _sealed(tmp_path)
    good = _issue(client, bearer, ids[0], "model_id")
    keys = client.get("/identity").json()["keys"]
    raw = bytes.fromhex(good["signatures"][0]["value"])
    s = int.from_bytes(raw[32:], "little") + nv._L
    assert s < 2 ** 256
    bad = copy.deepcopy(good)
    bad["signatures"][0]["value"] = (raw[:32] + s.to_bytes(32, "little")).hex()
    assert "signature_invalid" in _names(nv.verify_credential(bad, keys))


def _held(tmp_path, note="a held note"):
    svc, client, bearer, _ids = _sealed(tmp_path)
    made = client.post("/commit", headers=bearer, json={"content": note, "confirmed": True}).json()
    asyncio.run(svc.kairos.maybe_seal_governance(force=True))
    return svc, client, bearer, made


def test_g3_a_receipts_tree_size_altered_is_named(tmp_path):
    """The code held this already; mutant NV14 survived the suite."""
    svc, client, bearer, made = _held(tmp_path)
    receipt = client.get(f"/commitments/{made['commitment_id']}/receipt", headers=bearer).json()
    keys = client.get("/identity").json()["keys"]
    assert nv.verify_reveal(receipt, "a held note", made["salt"], keys)["verified"]
    grown = copy.deepcopy(receipt)
    grown["tree_size"] += 1
    assert "size_mismatch" in _names(nv.verify_reveal(grown, "a held note", made["salt"], keys))


def test_g4_a_second_create_never_overwrites_a_key(tmp_path):
    path = tmp_path / ik.KEY_FILE
    first = ik.load_or_create(path)
    before = path.read_bytes()
    ik._create(path, 1.0)
    assert path.read_bytes() == before
    ik._cache.clear()
    assert ik.load_or_create(path).public_key == first.public_key
    assert sorted(p.name for p in tmp_path.iterdir()) == [ik.KEY_FILE]


def test_g5_an_unsigned_block_is_never_offered_for_anchoring(tmp_path):
    svc, client, _bearer, _ids = _sealed(tmp_path)
    path = svc.instance_key_path
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "public_key": "00" * 32}), encoding="utf-8")
    ik._cache.clear()
    block = svc.archive_chain.append({"note": "w9"}, tmp_path / "no-snapshot")
    assert svc.archive_chain.all_blocks()[-1].instance_signature is None
    offered = [b.index for b in svc.archive_chain.unanchored_signed(limit=100)]
    assert offered and svc.archive_chain.all_blocks()[-1].index not in offered, (offered, block)


def test_g6_a_record_whose_salts_do_not_rebuild_its_root_is_refused(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path)
    conn = svc.lineage.conn
    row = conn.execute("SELECT field FROM record_salts WHERE lineage_id = ? ORDER BY field LIMIT 1",
                       (ids[0],)).fetchone()
    conn.execute("UPDATE record_salts SET salt = ? WHERE lineage_id = ? AND field = ?",
                 ("33" * 32, ids[0], row["field"]))
    conn.commit()
    resp = client.get(f"/lineage/{ids[0]}/credential", headers=bearer)
    assert resp.status_code == 409 and resp.json()["refused"] == "does_not_verify", resp.text


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture")
def test_g7_a_record_whose_root_no_seal_binds_yet_is_refused(tmp_path):
    svc = _pre_run_services(tmp_path)
    lid = sorted(_layout_1_sealed(svc))[0]
    client = TestClient(create_app(svc))
    bearer = {"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"}
    resp = client.get(f"/lineage/{lid}/credential", headers=bearer)
    assert resp.status_code == 409 and resp.json()["refused"] == "unbound", resp.text


def test_g8_a_held_commitment_moved_after_its_seal_is_refused(tmp_path):
    svc, client, bearer, made = _held(tmp_path)
    svc.lineage.conn.execute("UPDATE held_commitments SET commitment = ? WHERE commitment_id = ?",
                             ("44" * 32, made["commitment_id"]))
    svc.lineage.conn.commit()
    resp = client.get(f"/commitments/{made['commitment_id']}/receipt", headers=bearer)
    assert resp.status_code == 409 and resp.json()["refused"] == "does_not_verify", resp.text


def test_g9_the_idle_cycle_anchors_under_rfc3161(tmp_path, monkeypatch):
    authority = _tsa.StampAuthority()
    url, stop = authority.serve()
    try:
        svc, client, _bearer, _ids = _sealed(tmp_path)
        monkeypatch.setenv(an.ANCHOR_SETTING, "rfc3161")
        monkeypatch.setenv(an.URL_SETTING, url)
        asyncio.run(svc.kairos.run_one_cycle(force_snapshot=True))
        counts = svc.archive_chain.anchor_counts()
    finally:
        stop()
    assert authority.requests and counts["anchored_blocks"] >= 1, (len(authority.requests), counts)


def test_g10_an_unknown_anchor_setting_sends_nothing(tmp_path, monkeypatch):
    authority = _tsa.StampAuthority()
    url, stop = authority.serve()
    try:
        svc, client, _bearer, _ids = _sealed(tmp_path)
        monkeypatch.setenv(an.ANCHOR_SETTING, "rfc-3161")
        monkeypatch.setenv(an.URL_SETTING, url)
        assert an.anchor_mode() == "off"
        asyncio.run(svc.kairos.run_one_cycle(force_snapshot=True))
    finally:
        stop()
    assert authority.requests == [], "a misspelt setting sent a block's hash"
    assert client.get("/cortex/stats").json()["anchoring"]["declared"] is not None


class _Odd(_tsa.StampAuthority):
    """An authority whose stamp answers another nonce, none, or another imprint."""

    def __init__(self, how: str):
        super().__init__()
        self.how = how

    def token(self, imprint, *, nonce=None, at=None, status=0):
        if self.how == "nonce":
            nonce = (nonce or 0) + 1
        elif self.how == "no-nonce":
            nonce = None
        elif self.how == "imprint":
            imprint = bytes(32)
        return super().token(imprint, nonce=nonce, at=at, status=status)


@pytest.mark.parametrize("how", ["nonce", "no-nonce", "imprint"])
def test_g11_a_stamp_with_another_nonce_none_or_another_imprint_is_refused(tmp_path, how):
    """`nonce` and `imprint` the code held already (mutants AN2, AN3 survived the suite); `no-nonce` it did not."""
    authority = _Odd(how)
    url, stop = authority.serve()
    try:
        svc, _client, _bearer, _ids = _sealed(tmp_path)
        out = asyncio.run(an.anchor_pending(svc.archive_chain, url=url))
    finally:
        stop()
    assert out["anchored"] == 0 and out["failed"] == 1 and out["error"], out
    assert ("imprint" if how == "imprint" else "nonce") in out["error"], out          # refused for what was wrong
    assert svc.archive_chain.anchor_counts()["anchored_blocks"] == 0


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture")
def test_g12_a_bound_records_binding_altered_in_its_credential_is_named(tmp_path):
    """Mutant NV20 (the binding left unchecked) survived the suite: no test altered a binding."""
    svc = _pre_run_services(tmp_path)
    lid = sorted(_layout_1_sealed(svc))[0]
    _seal(svc)
    client = TestClient(create_app(svc))
    bearer = {"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"}
    good = _issue(client, bearer, lid, "ts")
    keys = client.get("/identity").json()["keys"]
    assert nv.verify_credential(good, keys)["verified"]
    bad = copy.deepcopy(good)
    bad["proof"]["binding"]["leaf"] = bad["proof"]["record_hash"] + "00" * 32
    got = nv.verify_credential(bad, keys)
    assert not got["verified"] and any(n.startswith("binding_") for n in _names(got)), got


# ---------- H: should-fix taken now ----------

@pytest.mark.parametrize("alter", ["leaf_index", "block_index_negative", "block_index_huge"])
def test_h1_malformed_numbers_are_named_never_a_traceback(tmp_path, alter):
    svc, client, bearer, ids = _sealed(tmp_path / "store")
    credential = _issue(client, bearer, ids[0], "model_id")
    if alter == "leaf_index":
        text = json.dumps(credential).replace(f'"leaf_index": {credential["proof"]["leaf_index"]}', '"leaf_index": 1e400')
    else:
        credential["signatures"][0]["block_index"] = -1 if alter == "block_index_negative" else 2 ** 64
        text = json.dumps(credential)
    where = tmp_path / f"m-{alter}"
    where.mkdir()
    (where / "credential.json").write_text(text, encoding="utf-8")
    (where / "identity.json").write_text(json.dumps(client.get("/identity").json()), encoding="utf-8")
    proc = _proc.run([sys.executable, "-I", str(REPO / "scripts" / "nexus_verify.py"), "credential.json", "--identity",
                      "identity.json"], cwd=where, timeout=120, capture_output=True, text=True, encoding="utf-8",
                     errors="replace")
    assert proc.returncode in (1, 2) and "Traceback" not in proc.stderr and "nexus-verify:" in proc.stdout + proc.stderr, \
        proc.stdout + proc.stderr


def test_h1_deep_json_is_input_it_cannot_read(tmp_path):
    where = tmp_path / "deep"
    where.mkdir()
    (where / "credential.json").write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    proc = _proc.run([sys.executable, "-I", str(REPO / "scripts" / "nexus_verify.py"), "credential.json", "--key",
                      "00" * 32], cwd=where, timeout=120, capture_output=True, text=True, encoding="utf-8",
                     errors="replace")
    assert proc.returncode == 2 and "Traceback" not in proc.stderr, proc.stdout + proc.stderr


def test_h2_a_tsa_certificate_with_nothing_to_check_says_so(tmp_path):
    svc, client, bearer, ids = _sealed(tmp_path)
    credential = _issue(client, bearer, ids[0], "model_id")
    got = nv.verify_credential(credential, client.get("/identity").json()["keys"],
                               _tsa.StampAuthority().cert_pem)
    assert got["verified"] and any(n.startswith("no_anchor") for n in got["notes"]), got


def test_h3_a_commit_refusal_row_holds_nothing_computed_from_the_note(tmp_path):
    """Two notes of one length leave the same request digest: the row says how long the note was, never what it
    said, so a guess at a short note cannot be tested against GET /governance/refusals."""
    svc, client, bearer, _ids = _sealed(tmp_path)
    for note in ("meet at noon", "pay the bill"):
        resp = client.post("/commit", headers=bearer, json={"content": note, "confirmed": False})
        assert resp.status_code == 422 and resp.json()["recorded"] is True
    rows = [dict(r) for r in svc.lineage.conn.execute(
        "SELECT route, request_sha256 FROM refused_requests WHERE route = '/commit' ORDER BY ts")]
    assert len(rows) == 2 and rows[0]["request_sha256"] == rows[1]["request_sha256"], rows


def test_h4_the_open_identity_names_no_path_and_an_unreadable_key_is_declared(tmp_path, monkeypatch):
    svc, client, bearer, ids = _sealed(tmp_path)
    secret = "/a/private/place/" + ik.KEY_FILE

    def unreadable(path, **_):
        raise PermissionError(13, "Permission denied", secret)
    monkeypatch.setattr(ik, "load_or_create", unreadable)
    svc.archive_chain.append({"note": "w9"}, tmp_path / "no-snapshot")
    assert svc.archive_chain.all_blocks()[-1].instance_signature is None, "non-vacuity: signing failed"
    for url, headers in (("/identity", {}), (f"/lineage/{ids[0]}/credential", bearer)):
        resp = client.get(url, headers=headers)
        assert resp.status_code == 503 and resp.json()["refused"] == "key_unreadable", (url, resp.text)
        assert "/a/private/place" not in resp.text, (url, resp.text)
    assert client.get("/identity").json()["signing"]["last_error"] == "PermissionError"


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_h5_a_document_saved_with_a_bom_or_as_utf16_is_read(tmp_path, encoding):
    """Windows PowerShell 5.1's `>` writes UTF-16 with a BOM; Notepad may add a UTF-8 BOM. The documents are JSON,
    so either reads; a note stays exact (a BOM in it is content, and its reveal fails)."""
    svc, client, bearer, ids = _sealed(tmp_path / "store")
    credential = _issue(client, bearer, ids[0], "model_id")
    where = tmp_path / f"saved-{encoding}"
    where.mkdir()
    (where / "credential.json").write_text(json.dumps(credential), encoding=encoding)
    (where / "identity.json").write_text(json.dumps(client.get("/identity").json()), encoding=encoding)
    (where / "authority.pem").write_bytes(b"\xef\xbb\xbf" + _tsa.StampAuthority().cert_pem)
    proc = _proc.run([sys.executable, "-I", str(REPO / "scripts" / "nexus_verify.py"), "credential.json", "--identity",
                      "identity.json", "--tsa-cert", "authority.pem"], cwd=where, timeout=120, capture_output=True,
                     text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "nexus-verify: VERIFIED" in proc.stdout, proc.stdout + proc.stderr


def test_h6_a_scratch_file_the_platform_will_not_remove_leaves_the_key_made(tmp_path, monkeypatch):
    """An antivirus holding the scratch file (Windows) must not turn a made key into an error."""
    real = Path.unlink

    def held(self, *a, **k):
        if self.name.startswith("." + ik.KEY_FILE + "."):
            raise PermissionError(13, "held by another process", str(self))
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", held)
    path = tmp_path / ik.KEY_FILE
    key = ik.load_or_create(path)
    assert path.is_file() and len(key.public_key) == 32
