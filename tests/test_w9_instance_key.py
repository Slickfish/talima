"""W9 part 1 — the instance's Ed25519 key and `GET /identity` (run/specs/W9.md, D1 to D3).

WHAT IS HELD HERE
    I1  The first use makes one key beside kb.db; a second use, and a second process's view of the same file,
        read the same key; on POSIX the file is owner-only.
    I2  `GET /identity` is open and serves the public half only: the algorithm, the public key, its key id (the
        SHA-256 of the public key), when it was made, every key the instance has used (part 2) and the signing
        count. The seed appears in no response.
    I3  A key file whose public half is not its seed's, or that cannot be read, is refused by name and never
        replaced: the route says so (503) and the file is left as it was.
    I4  A signature the instance makes verifies with the public key `/identity` serves (checked by `cryptography`
        here; by `nexus-verify`'s own RFC 8032 code in part 2).
    I5  The key sits in the services' data root, and the test makes and changes no key in the repository's
        `data/`, which on the operator's machine is the live instance's (its key is that instance's identity):
        absent before, absent after; present before, the same bytes after, compared by digest so a failure
        prints no key material. Planted: a key already there is left as it was (passes); a test that makes,
        changes or removes one is named (fails).

LAW 16 (I5, the operator's Windows gate on 001f940, whose checkout holds the live key): at 001f940 with a key
planted in the checkout's `data/`, I5 fails (`assert not True`); with this change it passes and the planted key's
bytes are unchanged. Under a check that compares nothing, I5c fails on each of its three plants.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient

from backend.memory import instance_key as ik
from backend.server import create_app, create_services

REPO = Path(__file__).resolve().parent.parent


def _services(root: Path):
    return create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma", eval_log_path=root / "m.jsonl",
                           archive_db_path=root / "archive.db", snapshots_dir=root / "snapshots")


def test_i1_the_first_use_makes_one_key_and_every_later_use_reads_it(tmp_path):
    path = tmp_path / ik.KEY_FILE
    assert not path.exists()
    first = ik.load_or_create(path)
    assert path.is_file() and len(first.public_key) == 32
    ik._cache.clear()                                         # a second process: nothing cached
    second = ik.load_or_create(path)
    assert second.public_key == first.public_key and second.created_at == first.created_at
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, oct(path.stat().st_mode)


def test_i2_identity_is_open_and_serves_the_public_half_only(tmp_path):
    svc = _services(tmp_path)
    resp = TestClient(create_app(svc)).get("/identity")       # no token
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"algorithm", "public_key", "key_id", "created_at", "keys", "signing"}, body
    assert body["algorithm"] == "ed25519" and len(body["public_key"]) == 64
    assert body["key_id"] == hashlib.sha256(bytes.fromhex(body["public_key"])).hexdigest()
    seed = json.loads((tmp_path / ik.KEY_FILE).read_text(encoding="utf-8"))["seed"]
    assert seed not in resp.text


def test_i3_a_key_file_that_is_not_its_seeds_is_refused_by_name_and_never_replaced(tmp_path):
    path = tmp_path / ik.KEY_FILE
    ik.load_or_create(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["public_key"] = "00" * 32
    path.write_text(json.dumps(data), encoding="utf-8")
    planted = path.read_text(encoding="utf-8")
    ik._cache.clear()
    with pytest.raises(ik.KeyMismatch, match="is not its seed's"):
        ik.load_or_create(path)
    svc = _services(tmp_path)
    resp = TestClient(create_app(svc)).get("/identity")
    assert resp.status_code == 503 and resp.json()["refused"] == "key_mismatch", resp.text
    assert path.read_text(encoding="utf-8") == planted, "the key file was replaced"
    path.write_text("not json", encoding="utf-8")
    ik._cache.clear()
    with pytest.raises(ik.KeyMismatch, match="cannot be read"):
        ik.load_or_create(path)


def test_i4_a_signature_verifies_with_the_served_public_key(tmp_path):
    svc = _services(tmp_path)
    body = TestClient(create_app(svc)).get("/identity").json()
    key = ik.load_or_create(svc.instance_key_path)
    message = b"block 7 " + bytes(32)
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(body["public_key"])).verify(key.sign(message), message)
    with pytest.raises(Exception):
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(body["public_key"])).verify(key.sign(message), b"another")


def _digest(path: Path) -> "str | None":
    """A file's SHA-256, or None when it is absent: all the comparison keeps, so a failure prints no key material."""
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _key_untouched(path: Path, act) -> None:
    """Run `act`, then hold that it neither made the key at `path` nor changed or removed it."""
    before = _digest(path)
    act()
    after = _digest(path)
    made, changed = before is None and after is not None, before is not None and after != before
    assert not made, f"this test made a key at {path}"
    assert not changed, f"this test changed or removed the key at {path}"


def _use_the_services_key(root: Path):
    def act():
        svc = _services(root)
        assert svc.instance_key_path == root / ik.KEY_FILE
        TestClient(create_app(svc)).get("/identity")
        assert (root / ik.KEY_FILE).is_file()
    return act


def test_i5_the_key_sits_in_the_services_data_root_never_in_the_repositorys(tmp_path):
    _key_untouched(REPO / "data" / ik.KEY_FILE, _use_the_services_key(tmp_path))


def test_i5b_a_key_already_in_the_checkout_is_left_as_it_was(tmp_path):
    """The operator's checkout: the live instance's key is in the repository's `data/` before the suite runs."""
    live = tmp_path / "checkout" / "data" / ik.KEY_FILE
    ik.load_or_create(live)
    ik._cache.clear()
    before = _digest(live)
    _key_untouched(live, _use_the_services_key(tmp_path / "root"))
    assert _digest(live) == before


def _make_one_there(watched: Path):
    return _use_the_services_key(watched.parent)


def _change_the_one_there(watched: Path):
    def act():
        watched.unlink()
        ik._cache.clear()
        ik.load_or_create(watched)
    return act


def _remove_the_one_there(watched: Path):
    return watched.unlink


KEY_PLANTS = {"a test that makes one where none was": (False, _make_one_there),
              "a test that changes the one there": (True, _change_the_one_there),
              "a test that removes the one there": (True, _remove_the_one_there)}


@pytest.mark.parametrize("plant", sorted(KEY_PLANTS))
def test_i5c_a_test_that_makes_changes_or_removes_the_checkouts_key_is_named(tmp_path, plant):
    present, act_for = KEY_PLANTS[plant]
    watched = tmp_path / "checkout" / "data" / ik.KEY_FILE
    if present:
        ik.load_or_create(watched)
        ik._cache.clear()
    with pytest.raises(AssertionError) as caught:
        _key_untouched(watched, act_for(watched))
    assert str(watched) in str(caught.value), caught.value
    ik._cache.clear()


def test_i6_a_filesystem_without_hard_links_still_makes_one_key(tmp_path, monkeypatch):
    """W9 3c (Windows-sensitive): `os.link` refused by the filesystem (FAT, some network shares) makes the key by
    an exclusive create of the file itself, once; a second start reads it and never overwrites it."""
    def no_links(*_a, **_k):
        raise OSError(1, "hard links are not supported here")
    monkeypatch.setattr(ik.os, "link", no_links)
    path = tmp_path / ik.KEY_FILE
    first = ik.load_or_create(path)
    ik._cache.clear()
    assert ik.load_or_create(path).public_key == first.public_key
    assert [p.name for p in tmp_path.iterdir()] == [ik.KEY_FILE], "a scratch file was left behind"
