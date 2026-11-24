"""W9 part 2a — every block appended is signed, and each key is announced in the chain (run/specs/W9.md D4 and the
operator's decision 1).

WHAT IS HELD HERE
    S1  A block appended now carries the instance's signature over its statement and the key's id, beside its hash;
        the first block announces the key (`first`), later blocks do not; `/identity` lists the key with the block
        that announced it and the period it opened.
    S2  Every block appended before W9 (the committed pre-run fixture) verifies unchanged and unsigned beside the
        signed ones, and the chain verifies end to end.
    S3  A restore that carries no private key makes a new one, and the next block announces it as a rotation from
        the old one; `/identity` lists both, the old one's period closed where the new one's opens.
    S4  A key that cannot be read leaves the block appended unsigned, never refused, and counted where `/identity`
        reports it.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient

from backend.memory import instance_key as ik
from backend.server import create_app, create_services
from tests.test_w8_bindings import _layout_1_sealed, _pre_run_services, _seal
from tests.test_w8_proofs import _sealed


def _append(svc):
    """A block appended through the services' chain (a seal appends one only when it has something to seal)."""
    return svc.archive_chain.append({"note": "w9"}, svc.instance_key_path.parent / "no-snapshot")


def _verifies(public_key_hex: str, block) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex)).verify(
            bytes.fromhex(block.instance_signature), ik.block_statement(block.index, block.block_hash))
        return True
    except Exception:
        return False


def test_s1_a_block_appended_now_is_signed_and_its_key_announced_once(tmp_path):
    svc, client, _bearer, _ids = _sealed(tmp_path)
    blocks = svc.archive_chain.all_blocks()
    signed = [b for b in blocks if b.instance_signature]
    assert signed and all(b.instance_signature for b in blocks), [b.instance_signature for b in blocks]
    identity = client.get("/identity").json()
    assert all(b.instance_key_id == identity["key_id"] and _verifies(identity["public_key"], b) for b in signed)
    announcing = [b for b in blocks if "identity_seal" in b.fingerprint]
    assert len(announcing) == 1 and announcing[0].index == blocks[0].index, [b.index for b in announcing]
    assert announcing[0].fingerprint["identity_seal"]["reason"] == "first"
    later = _append(svc)
    assert "identity_seal" not in later.fingerprint and _verifies(identity["public_key"], later), \
        "a key announced twice in a row, or a later block unsigned"
    keys = client.get("/identity").json()["keys"]
    assert keys == [{"key_id": identity["key_id"], "public_key": identity["public_key"], "algorithm": "ed25519",
                     "reason": "first", "previous_key_id": None, "announced_in": announcing[0].index,
                     "until_block": None, "active_from": announcing[0].timestamp, "active_to": None}], keys
    assert svc.archive_chain.verify_chain().valid


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; W8's R3 runs a pre-W8 store on Postgres")
def test_s2_blocks_before_w9_verify_unchanged_beside_signed_ones(tmp_path):
    svc = _pre_run_services(tmp_path)
    before = svc.archive_chain.all_blocks()
    assert before and not any(b.instance_signature for b in before)
    assert _layout_1_sealed(svc)
    _seal(svc)
    after = svc.archive_chain.all_blocks()
    assert [b.block_hash for b in after[:len(before)]] == [b.block_hash for b in before]
    assert after[-1].instance_signature and "identity_seal" in after[-1].fingerprint
    assert svc.archive_chain.verify_chain().valid


@pytest.mark.sqlite_only("a restore here is the stores' files copied; on Postgres it is the hosted backup's dump")
def test_s3_a_restore_without_the_private_key_records_a_rotation(tmp_path):
    svc, client, _bearer, _ids = _sealed(tmp_path / "before")
    old = client.get("/identity").json()
    restored = tmp_path / "after"
    restored.mkdir()
    for name in ("kb.db", "archive.db"):                       # a restore: the stores, never the private key
        shutil.copy(tmp_path / "before" / name, restored / name)
    assert not (restored / ik.KEY_FILE).exists()
    svc2 = create_services(kb_db_path=restored / "kb.db", chroma_dir=restored / "chroma",
                           eval_log_path=restored / "m.jsonl", archive_db_path=restored / "archive.db",
                           snapshots_dir=restored / "snapshots", provider=svc.provider)
    client2 = TestClient(create_app(svc2))
    pending = client2.get("/identity").json()
    assert pending["key_id"] != old["key_id"]
    assert [k["announced_in"] is None for k in pending["keys"]] == [False, True], pending["keys"]
    last = _append(svc2)
    assert last.fingerprint["identity_seal"] == {"key_id": pending["key_id"], "public_key": pending["public_key"],
                                                 "algorithm": "ed25519", "reason": "rotation",
                                                 "previous_key_id": old["key_id"], "made_at_block": last.index}
    assert _verifies(pending["public_key"], last)
    keys = client2.get("/identity").json()["keys"]
    assert [k["key_id"] for k in keys] == [old["key_id"], pending["key_id"]]
    assert keys[0]["active_to"] == keys[1]["active_from"] == last.timestamp and keys[1]["active_to"] is None
    assert keys[0]["until_block"] == keys[1]["announced_in"] == last.index and keys[1]["until_block"] is None
    assert svc2.archive_chain.verify_chain().valid


def test_s4_a_key_that_cannot_be_read_leaves_the_block_unsigned_and_counted(tmp_path):
    svc, client, _bearer, _ids = _sealed(tmp_path)
    path = svc.instance_key_path
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "public_key": "00" * 32}), encoding="utf-8")
    ik._cache.clear()
    count = svc.archive_chain.count()
    _append(svc)
    assert svc.archive_chain.count() == count + 1, "the block was refused"
    assert svc.archive_chain.all_blocks()[-1].instance_signature is None
    resp = client.get("/identity")
    assert resp.status_code == 503 and resp.json()["signing"]["unsigned_appends_since_start"] == 1, resp.text
    assert svc.archive_chain.verify_chain().valid
