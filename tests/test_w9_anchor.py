"""W9 part 3b — anchoring a block's hash with an RFC 3161 authority (run/specs/W9.md D7; the operator's decision 3).

WHAT IS HELD HERE
    A1  `nexus-verify` checks an RFC 3161 receipt offline against the authority's certificate: a granted stamp over
        the block's hash verifies (SHA-256 and SHA-512 signers); without the certificate it says the stamp is
        unchecked; a refused stamp, another imprint, an altered TSTInfo, an altered signature and another
        authority's certificate are each named `anchor_invalid`.
    A2  Off by default: a cycle sends the authority nothing, and `/cortex/stats.anchoring` says off.
    A3  On, each signed block is stamped once, and what each request carries is the block's 32-byte hash as its
        imprint, a nonce and the flags: nothing of any record.
    A4  A credential carries its block's receipt, and `nexus-verify --tsa-cert` checks it offline, isolated, from an
        empty directory; a receipt moved to another block, and another authority's certificate, are named.
    A5  An authority that is down is counted, the block stays unanchored and the cycle goes on; the next attempt,
        with the authority up, stamps it.
    A7  Its ECDSA check is SEC 1's over the NIST curves: each curve's generator lies on it with order n, signatures
        `cryptography` makes on P-256, P-384 and P-521 verify and altered ones do not, a stamp from an ECDSA
        authority shaped as FreeTSA's (P-384, SHA-512) verifies, a key of the other kind is named, and a
        certificate carrying its point compressed reads the same point.
    A6  The live test (the operator's decision 3): FreeTSA stamps a block and `nexus-verify` checks the stamp
        against FreeTSA's certificate. Skipped unless NEXUS_ANCHOR_LIVE_TEST=freetsa, because it leaves the machine.
"""
from __future__ import annotations

import base64
import datetime

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from backend.memory import anchor as anchor_mod
from scripts import nexus_verify as nv
from tests import _proc
from tests._tsa import StampAuthority, parse_request
from tests.test_w8_proofs import _sealed

REPO = Path(__file__).resolve().parent.parent

BLOCK = "ab" * 32


def _receipt(token: bytes) -> dict:
    return {"method": "rfc3161", "authority": "test", "block_index": 3, "token": base64.b64encode(token).decode()}


def test_a1_an_rfc_3161_stamp_is_checked_offline_against_the_authoritys_certificate():
    for digest in ("sha256", "sha512"):
        tsa = StampAuthority(digest=digest)
        at = datetime.datetime(2026, 11, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)
        receipt = _receipt(tsa.token(bytes.fromhex(BLOCK), nonce=7, at=at))
        failures, notes, when = nv.verify_anchor(receipt, BLOCK, tsa.cert_pem)
        assert failures == [] and when == "20261117120000Z" and notes[0].startswith("anchored"), (digest, notes)
        failures, notes, _ = nv.verify_anchor(receipt, BLOCK, None)
        assert failures == [] and notes[0].startswith("anchor_unpinned"), notes
    tsa, other = StampAuthority(), StampAuthority()
    good = tsa.token(bytes.fromhex(BLOCK))
    cases = {
        "refused": tsa.token(bytes.fromhex(BLOCK), status=2),
        "another imprint": tsa.token(bytes.fromhex("cd" * 32)),
        "an altered TSTInfo": _flip_in_tst(good),
        "an altered signature": good[:-5] + bytes([good[-5] ^ 1]) + good[-4:],
    }
    for label, token in cases.items():
        failures, _notes, _ = nv.verify_anchor(_receipt(token), BLOCK, tsa.cert_pem)
        assert failures and failures[0].startswith("anchor_invalid"), (label, failures)
    failures, _notes, _ = nv.verify_anchor(_receipt(good), BLOCK, other.cert_pem)
    assert failures and "not the authority's" in failures[0], failures
    failures, _notes, _ = nv.verify_anchor({"token": "not base64 at all!"}, BLOCK, tsa.cert_pem)
    assert failures and failures[0].startswith("anchor_invalid"), failures


def _flip_in_tst(token: bytes) -> bytes:
    """One byte of the TSTInfo's generation time changed: its message digest no longer holds."""
    i = token.index(b"\x18\x0f") + 2 + 3                       # inside the GeneralizedTime's digits
    return token[:i] + bytes([token[i] ^ 1]) + token[i + 1:]


@pytest.fixture
def authority():
    tsa = StampAuthority()
    url, stop = tsa.serve()
    yield tsa, url
    stop()


@pytest.mark.parametrize("setting", [None, "off"])
def test_a2_off_by_default_nothing_is_sent(tmp_path, authority, monkeypatch, setting):
    tsa, url = authority
    if setting is None:
        monkeypatch.delenv("NEXUS_ANCHOR", raising=False)
    else:
        monkeypatch.setenv("NEXUS_ANCHOR", "off")
    monkeypatch.setenv("NEXUS_ANCHOR_URL", url)
    svc, client, _bearer, _ids = _sealed(tmp_path)
    assert asyncio.run(svc.kairos.maybe_anchor()) == {"anchored": 0, "failed": 0, "error": None}
    assert tsa.requests == [] and client.get("/cortex/stats").json()["anchoring"]["mode"] == "off"


def test_a3_each_signed_block_is_stamped_once_and_only_its_hash_leaves(tmp_path, authority, monkeypatch):
    tsa, url = authority
    monkeypatch.setenv("NEXUS_ANCHOR", "rfc3161")
    monkeypatch.setenv("NEXUS_ANCHOR_URL", url)
    svc, client, _bearer, _ids = _sealed(tmp_path)
    blocks = [b for b in svc.archive_chain.all_blocks() if b.instance_signature]
    out = asyncio.run(svc.kairos.maybe_anchor())
    assert out == {"anchored": len(blocks), "failed": 0, "error": None} and len(tsa.requests) == len(blocks)
    assert asyncio.run(svc.kairos.maybe_anchor())["anchored"] == 0, "a block stamped twice"
    for block, request in zip(blocks, tsa.requests):
        imprint, nonce = parse_request(request)
        assert imprint == bytes.fromhex(block.block_hash) and nonce is not None
        assert len(request) < 70, f"{len(request)} bytes: more than the hash, a nonce and the flags"
    stats = client.get("/cortex/stats").json()["anchoring"]
    assert stats["mode"] == "rfc3161" and stats["anchored_blocks"] == stats["signed_blocks"] == len(blocks)


def _offline(tmp_path, credential, identity, cert) -> tuple[int, str]:
    where = tmp_path / f"stranger-{len(list(tmp_path.glob('stranger-*')))}"
    where.mkdir()
    (where / "credential.json").write_text(json.dumps(credential), encoding="utf-8")
    (where / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    (where / "tsa.pem").write_bytes(cert)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    proc = _proc.run([sys.executable, "-I", str(REPO / "scripts" / "nexus_verify.py"), "credential.json",
                      "--identity", "identity.json", "--tsa-cert", "tsa.pem"], cwd=where, env=env, timeout=120,
                     capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout + proc.stderr


def test_a4_a_credential_carries_its_stamp_and_nexus_verify_checks_it_offline(tmp_path, authority, monkeypatch):
    tsa, url = authority
    monkeypatch.setenv("NEXUS_ANCHOR", "rfc3161")
    monkeypatch.setenv("NEXUS_ANCHOR_URL", url)
    svc, client, bearer, ids = _sealed(tmp_path / "store")
    asyncio.run(svc.kairos.maybe_anchor())
    credential = client.get(f"/lineage/{ids[0]}/credential", headers=bearer).json()
    identity = client.get("/identity").json()
    assert [a["block_index"] for a in credential["anchors"]] == [credential["proof"]["block"]["index"]]
    rc, out = _offline(tmp_path, credential, identity, tsa.cert_pem)
    assert rc == 0 and "NOTE anchored:" in out and "nexus-verify: VERIFIED" in out, out
    moved = json.loads(json.dumps(credential))
    moved["anchors"][0]["block_index"] = moved["anchors"][0]["block_index"] + 99
    assert any(f.startswith("anchor_invalid") for f in nv.verify_credential(moved, identity["keys"], tsa.cert_pem)
               ["failures"])
    other = StampAuthority()
    rc, out = _offline(tmp_path, credential, identity, other.cert_pem)
    assert rc == 1 and "FAIL anchor_invalid" in out, out


def test_a5_an_authority_that_is_down_is_counted_and_tried_again(tmp_path, authority, monkeypatch):
    tsa, url = authority
    monkeypatch.setenv("NEXUS_ANCHOR", "rfc3161")
    monkeypatch.setenv("NEXUS_ANCHOR_URL", "http://127.0.0.1:9/tsr")          # nothing listens on the discard port
    svc, client, _bearer, _ids = _sealed(tmp_path)
    out = asyncio.run(svc.kairos.maybe_anchor())
    assert out["anchored"] == 0 and out["failed"] == 1 and out["error"], out
    stats = client.get("/cortex/stats").json()["anchoring"]
    assert stats["failures_since_start"] == 1 and stats["last_error"] and stats["anchored_blocks"] == 0
    monkeypatch.setenv("NEXUS_ANCHOR_URL", url)
    assert asyncio.run(svc.kairos.maybe_anchor())["anchored"] >= 1


@pytest.mark.skipif(os.environ.get("NEXUS_ANCHOR_LIVE_TEST") != "freetsa",
                    reason="leaves the machine: set NEXUS_ANCHOR_LIVE_TEST=freetsa (freetsa.org must be reachable)")
def test_a6_live_freetsa_stamps_a_block_and_the_stamp_verifies(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setenv("NEXUS_ANCHOR", "rfc3161")
    monkeypatch.setenv("NEXUS_ANCHOR_URL", "https://freetsa.org/tsr")
    svc, client, bearer, ids = _sealed(tmp_path)
    out = asyncio.run(svc.kairos.maybe_anchor())
    assert out["anchored"] >= 1 and out["failed"] == 0, out
    cert = httpx.get("https://freetsa.org/files/tsa.crt", timeout=30).content
    credential = client.get(f"/lineage/{ids[0]}/credential", headers=bearer).json()
    result = nv.verify_credential(credential, client.get("/identity").json()["keys"], cert)
    print(f"\nA6 FreeTSA: {result['notes']}")
    assert result["verified"] and any(n.startswith("anchored") for n in result["notes"]), result


def test_a7_the_ecdsa_check_over_the_nist_curves():
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    for curve_oid, (p, a, b, gx, gy, n) in nv._CURVES.items():
        assert (gy * gy - (gx ** 3 + a * gx + b)) % p == 0, f"{curve_oid}: the generator is not on the curve"
        assert nv._ec_mul(n, (gx, gy, 1), p, a)[2] == 0, f"{curve_oid}: the generator's order is not n"
    import hashlib as _h
    for curve, algorithm, name, curve_oid in ((ec.SECP256R1(), hashes.SHA256(), "sha256", "1.2.840.10045.3.1.7"),
                                              (ec.SECP384R1(), hashes.SHA384(), "sha384", "1.3.132.0.34"),
                                              (ec.SECP384R1(), hashes.SHA512(), "sha512", "1.3.132.0.34"),
                                              (ec.SECP521R1(), hashes.SHA512(), "sha512", "1.3.132.0.35")):
        for n_ in range(3):
            key = ec.generate_private_key(curve)
            nums = key.public_key().public_numbers()
            message = bytes([n_]) * (n_ * 17 + 1)
            signature = key.sign(message, ec.ECDSA(algorithm))
            digest = _h.new(name, message).digest()
            assert nv.ecdsa_verify(curve_oid, nums.x, nums.y, signature, digest), (curve.name, name)
            assert not nv.ecdsa_verify(curve_oid, nums.x, nums.y, signature, _h.new(name, message + b"x").digest())
            r, s_ = decode_dss_signature(signature)
            from tests._tsa import integer, seq
            assert not nv.ecdsa_verify(curve_oid, nums.x, nums.y, seq(integer(r), integer(s_ ^ 1)), digest)
    tsa, rsa_tsa = StampAuthority(kind="ec"), StampAuthority()
    receipt = _receipt(tsa.token(bytes.fromhex(BLOCK)))
    failures, notes, _ = nv.verify_anchor(receipt, BLOCK, tsa.cert_pem)
    assert failures == [] and notes[0].startswith("anchored"), (failures, notes)
    failures, _notes, _ = nv.verify_anchor(receipt, BLOCK, StampAuthority(kind="ec").cert_pem)
    assert failures and "not the authority's" in failures[0], failures
    failures, _notes, _ = nv.verify_anchor(receipt, BLOCK, rsa_tsa.cert_pem)
    assert failures and "does not match the certificate's key" in failures[0], failures
    # the same point, carried compressed in a certificate's key
    from tests._tsa import oid, seq, tlv, integer
    nums = tsa.key.public_key().public_numbers()
    compressed = tsa.key.public_key().public_bytes(serialization.Encoding.X962,
                                                   serialization.PublicFormat.CompressedPoint)
    spki = seq(seq(oid("1.2.840.10045.2.1"), oid("1.3.132.0.34")), tlv(0x03, b"\x00" + compressed))
    tbs = seq(tlv(0xA0, integer(2)), integer(1), seq(oid("1.2.840.10045.4.3.3")), seq(), seq(), seq(), spki)
    cert = seq(tbs, seq(oid("1.2.840.10045.4.3.3")), tlv(0x03, b"\x00"))
    assert nv._public_key(cert) == ("ec", "1.3.132.0.34", nums.x, nums.y)

