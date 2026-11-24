"""W9 part 3a — commit now, reveal later (run/specs/W9.md D8; the operator's decision 2; K6).

WHAT IS HELD HERE
    CR1 `POST /commit` without `confirmed=true` holds nothing: it answers 422 with the offer, and the refusal is
        recorded by the request's digest, never its text.
    CR2 Confirmed, it holds a salted commitment, `SHA-256(salt || canonical(content))`, hands the salt back once, and
        the content enters no model request on the turns that follow.
    CR3 (K6) The next seal seals the commitment as a leaf of its block's `commit` root; the receipt and a later
        disclosure of the content and salt verify with `nexus-verify --reveal` in another process, isolated, from an
        empty directory; a wrong salt, other content, a moved leaf and another key each fail by name.
    CR4 The receipt route is gated, names an unknown commitment (404) and an unsealed one (409).
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from pathlib import Path

from backend.memory import commitments as cm
from backend.memory import instance_key as ik
from scripts import nexus_verify as nv
from tests import _proc
from tests.test_w8_proofs import _console, _turn

REPO = Path(__file__).resolve().parent.parent
NOTE = "The QTc change at 600 mg was 11.4 ms; we knew it on this date.\r\nSecond line, CRLF kept."


def _held(tmp_path):
    svc, client, bearer = _console(tmp_path)
    resp = client.post("/commit", headers=bearer, json={"content": NOTE, "confirmed": True})
    assert resp.status_code == 200, resp.text
    return svc, client, bearer, resp.json()


def test_cr1_unconfirmed_holds_nothing_and_is_recorded_by_digest(tmp_path):
    svc, client, bearer = _console(tmp_path)
    for body in ({"content": NOTE}, {"content": NOTE, "confirmed": False}):
        resp = client.post("/commit", headers=bearer, json=body)
        assert resp.status_code == 422 and resp.json()["recorded"] is True, resp.text
        assert resp.json()["offer"]["content_chars"] == len(NOTE)
    assert svc.lineage.conn.execute("SELECT COUNT(*) AS n FROM held_commitments").fetchone()["n"] == 0
    rows = svc.lineage.conn.execute("SELECT * FROM refused_requests").fetchall()
    assert rows and not any(NOTE[:20] in json.dumps(dict(r)) for r in rows), "the note's text is on the ledger"


def test_cr2_a_confirmed_commitment_is_salted_and_never_enters_a_prompt(tmp_path):
    svc, client, bearer, made = _held(tmp_path)
    assert set(made) >= {"commitment_id", "commitment", "salt", "ts"} and len(bytes.fromhex(made["salt"])) == 32
    assert made["commitment"] == cm.commitment(bytes.fromhex(made["salt"]), NOTE).hex()
    _turn(client, bearer, "what was the QTc change at 600 mg?")
    stub = svc.provider
    assert stub.requests, "non-vacuity: the turn reached the model"
    sent = json.dumps(stub.requests + stub.extraction_requests)
    assert "11.4 ms; we knew it" not in sent, "the held note reached a model request"


def _reveal(tmp_path, receipt, salt, identity, content=NOTE) -> tuple[int, str]:
    where = tmp_path / f"later-{len(list(tmp_path.glob('later-*')))}"
    where.mkdir()
    (where / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    (where / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    with open(where / "note.txt", "w", encoding="utf-8", newline="") as f:
        f.write(content)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    proc = _proc.run([sys.executable, "-I", str(REPO / "scripts" / "nexus_verify.py"), "--reveal", "receipt.json",
                      "--content", "note.txt", "--salt", salt, "--identity", "identity.json"], cwd=where, env=env,
                     timeout=120, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout + proc.stderr


def test_cr3_a_held_commitment_is_revealed_and_verified_offline_in_another_process(tmp_path):
    svc, client, bearer, made = _held(tmp_path / "store")
    asyncio.run(svc.kairos.maybe_seal_governance(force=True))
    receipt = client.get(f"/commitments/{made['commitment_id']}/receipt", headers=bearer)
    assert receipt.status_code == 200, receipt.text
    receipt = receipt.json()
    assert "salt" not in receipt and NOTE[:20] not in json.dumps(receipt)
    identity = client.get("/identity").json()
    rc, out = _reveal(tmp_path, receipt, made["salt"], identity)
    assert rc == 0 and "nexus-verify: VERIFIED" in out, out
    keys = identity["keys"]
    def names(result):
        return [f.split(":", 1)[0] for f in result["failures"]]
    assert "reveal_mismatch" in names(nv.verify_reveal(receipt, NOTE, "00" * 32, keys))
    assert "reveal_mismatch" in names(nv.verify_reveal(receipt, NOTE.replace("\r\n", "\n"), made["salt"], keys))
    assert "unsalted_hash" in names(nv.verify_reveal(receipt, NOTE, "", keys))
    moved = copy.deepcopy(receipt)
    moved["leaf_index"] = moved["leaf_index"] + 1
    assert names(nv.verify_reveal(moved, NOTE, made["salt"], keys))
    other = ik.load_or_create(tmp_path / "another_key.json")
    pinned_other = [{"key_id": other.key_id, "public_key": other.public_key.hex(), "announced_in": None,
                     "until_block": None}]
    assert "signature_invalid" in names(nv.verify_reveal(receipt, NOTE, made["salt"], pinned_other))
    rc, out = _reveal(tmp_path, receipt, made["salt"], identity, content=NOTE + " ")
    assert rc == 1 and "FAIL reveal_mismatch" in out, out


def test_cr4_the_receipt_route_is_gated_and_refuses_by_name(tmp_path):
    svc, client, bearer, made = _held(tmp_path)
    url = f"/commitments/{made['commitment_id']}/receipt"
    assert client.get(url, headers={"Authorization": ""}).status_code == 401
    resp = client.get(url, headers=bearer)
    assert resp.status_code == 409 and resp.json()["refused"] == "unsealed", resp.text
    resp = client.get("/commitments/no-such/receipt", headers=bearer)
    assert resp.status_code == 404 and resp.json()["refused"] == "no_commitment", resp.text


def test_cr5_nexus_commit_refuses_unconfirmed_and_status_carries_the_identity(tmp_path):
    """The same act under MCP: `nexus_commit` refuses without `confirmed=true`, recorded by digest; confirmed, it
    holds the note; `nexus_status` carries the instance's keys (contract v4)."""
    from tests.test_mcp2_build import _call, _runtime
    from backend.mcp.mcp_server import build_server
    import pytest
    rt = _runtime(tmp_path / "d")
    srv = build_server(rt, with_lifespan=False)
    conn = rt.services.lineage.conn
    for args in ({"content": NOTE}, {"content": NOTE, "confirmed": False}):
        with pytest.raises(Exception, match="Law 3"):
            _call(srv, "nexus_commit", **args)
    rows = [tuple(r) for r in rt.services.recalibration.conn.execute(
        "SELECT route, rule FROM refused_requests ORDER BY ts, event_id")]
    assert rows == [("mcp:nexus_commit", "law3_unconfirmed")] * 2, rows
    assert conn.execute("SELECT COUNT(*) AS n FROM held_commitments").fetchone()["n"] == 0
    made = _call(srv, "nexus_commit", content=NOTE, confirmed=True)
    assert made["commitment"] == cm.commitment(bytes.fromhex(made["salt"]), NOTE).hex()
    status = _call(srv, "nexus_status")
    assert status["contract_version"] == 5 and status["identity"]["keys"], status["identity"]   # W10b: 5
