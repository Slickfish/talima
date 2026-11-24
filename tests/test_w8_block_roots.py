"""W8 part 2 — Merkle roots in a block's header (run/specs/W8.md, D2 and D3; exit 1).

WHAT IS HELD HERE
    B1  Every block written before W8 recomputes and verifies unchanged: the pre-run fixture's archive, layout 1.
    B2  A block appended now is layout 2: its hash is over a header (the index, the time, the previous hash, the
        snapshot fields, the kind, `fingerprint_hash` and the two roots with their sizes), so it recomputes from
        the header with no fingerprint at all; the roots are RFC 6962 roots over each seal's leaves, in the seal's
        order, and each carries its size, which a Merkle path alone does not prove.
    B3  Each field of a layout-2 header, the fingerprint, and the stored roots, altered, is named by the chain's
        verification (`block_hash_mismatch`, `fingerprint_mismatch`, `roots_mismatch`).
    B4  A layout-1 block has a derived root, computed from its sealed list and never stored back; a chain of
        layout-1 blocks followed by layout-2 blocks verifies, and its links hold across the boundary.
"""
from __future__ import annotations

import pytest

import hashlib
import json
import shutil
from pathlib import Path

from backend.memory import merkle
from backend.memory.chain import NexusArchiveChain, derived_roots
from tests._tracked import REPO

PRE_RUN = REPO / "tests" / "fixtures" / "pre_run_data"


def _seal(hashes: list[str]) -> dict:
    hashes = sorted(hashes)
    return {"count": len(hashes), "digest": hashlib.sha256("\n".join(hashes).encode()).hexdigest(),
            "record_hashes": hashes}


def _h(n: int) -> list[str]:
    return [hashlib.sha256(f"record-{n}-{i}".encode()).hexdigest() for i in range(n)]


def _write(chain: NexusArchiveChain, sql: str, *args) -> None:
    chain.conn.execute(sql, args)
    chain.conn.commit()


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; on Postgres its bind, prove and tombstone path runs through the migration script in tests/test_w8_rebinding.py R3")
def test_b1_every_block_before_w8_verifies_unchanged(tmp_path):
    shutil.copy(PRE_RUN / "archive.db", tmp_path / "archive.db")
    chain = NexusArchiveChain(tmp_path / "archive.db")
    blocks = chain.all_blocks()
    assert blocks and all(b.layout == 1 for b in blocks)
    assert all(b.compute_hash() == b.block_hash for b in blocks)
    assert chain.verify_chain().valid


def test_b2_a_new_block_is_layout_2_and_its_hash_is_over_its_header(tmp_path):
    chain = NexusArchiveChain(tmp_path / "archive.db")
    lineage, governance = _h(5), _h(3)
    block = chain.append({"lineage_seal": _seal(lineage), "recalibration_seal": _seal(governance)}, "",
                         kind="governance")
    assert block.layout == 2
    assert block.roots == {
        "lineage": {"root": merkle.root([bytes.fromhex(h) for h in sorted(lineage)]).hex(), "size": 5},
        "governance": {"root": merkle.root([bytes.fromhex(h) for h in sorted(governance)]).hex(), "size": 3}}
    header = block.header()
    assert "fingerprint" not in header and header["fingerprint_hash"] == hashlib.sha256(
        json.dumps(block.fingerprint, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert hashlib.sha256(json.dumps(header, sort_keys=True, separators=(",", ":")).encode()).hexdigest() \
        == block.block_hash
    reread = chain.get_block(block.index)
    assert reread.layout == 2 and reread.roots == block.roots and reread.compute_hash() == block.block_hash
    assert chain.verify_chain().valid
    # a block with no seal has no roots, and says so
    empty = chain.append({"snapshot": "s"}, "")
    assert empty.layout == 2 and empty.roots == {} and chain.verify_chain().valid


def test_b3_each_header_field_the_fingerprint_and_the_roots_altered_are_named(tmp_path):
    chain = NexusArchiveChain(tmp_path / "archive.db")
    chain.append({"lineage_seal": _seal(_h(4))}, "", kind="governance")
    block = chain.append({"lineage_seal": _seal(_h(6)), "recalibration_seal": _seal(_h(2))}, "", kind="governance")
    row = dict(chain.conn.execute("SELECT * FROM archive_blocks WHERE idx = ?", (block.index,)).fetchone())
    other_fp = json.dumps({"lineage_seal": _seal(_h(7))}, sort_keys=True)
    other_roots = json.dumps({"lineage": {"root": "00" * 32, "size": 6}}, sort_keys=True)
    cases = [("timestamp", row["timestamp"] + 1, "block_hash_mismatch"),
             ("previous_hash", "f" * 64, "block_hash_mismatch"),
             ("kind", "snapshot", "block_hash_mismatch"),
             ("snapshot_hash", "e" * 64, "block_hash_mismatch"),
             ("fingerprint_json", other_fp, "fingerprint_mismatch"),
             ("roots_json", other_roots, "roots_mismatch")]
    for column, value, reason in cases:
        _write(chain, f"UPDATE archive_blocks SET {column} = ? WHERE idx = ?", value, block.index)
        result = chain.verify_chain()
        assert (result.valid, result.broken_at, result.reason) == (False, block.index, reason), (column, result)
        _write(chain, f"UPDATE archive_blocks SET {column} = ? WHERE idx = ?", row[column], block.index)
        assert chain.verify_chain().valid, column


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; on Postgres its bind, prove and tombstone path runs through the migration script in tests/test_w8_rebinding.py R3")
def test_b4_a_layout_1_block_has_a_derived_root_and_the_chain_holds_across_the_boundary(tmp_path):
    shutil.copy(PRE_RUN / "archive.db", tmp_path / "archive.db")
    chain = NexusArchiveChain(tmp_path / "archive.db")
    old = chain.all_blocks()
    sealed = [b for b in old if (b.fingerprint or {}).get("lineage_seal", {}).get("record_hashes")]
    assert sealed, "the population: the pre-run archive seals lineage records in a layout-1 block"
    for b in sealed:
        hashes = b.fingerprint["lineage_seal"]["record_hashes"]
        assert derived_roots(b)["lineage"] == {"root": merkle.root([bytes.fromhex(h) for h in hashes]).hex(),
                                               "size": len(hashes)}
    before = [(b.index, b.block_hash) for b in old]
    new = chain.append({"lineage_seal": _seal(_h(3))}, "", kind="governance")
    assert new.previous_hash == old[-1].block_hash and new.layout == 2
    assert [(b.index, b.block_hash) for b in chain.all_blocks()[:-1]] == before, "no block before W8 was rewritten"
    assert chain.verify_chain().valid
    assert derived_roots(new) == new.roots, "a layout-2 block's roots are its stored ones"


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture; on Postgres its bind, prove and tombstone path runs through the migration script in tests/test_w8_rebinding.py R3")
def test_b5_a_fresh_archive_and_an_upgraded_one_have_one_column_order(tmp_path):
    """W8's verifier, MF3: the store copy compares column lists in order (scripts/_store_copy.py), so a store made
    before W8 and opened now must read its columns in the order a fresh one has, or migration and export refuse it."""
    import sqlite3
    shutil.copy(PRE_RUN / "archive.db", tmp_path / "old.db")
    NexusArchiveChain(tmp_path / "old.db").close()
    NexusArchiveChain(tmp_path / "new.db").close()

    def order(path):
        conn = sqlite3.connect(path)
        try:
            return [r[1] for r in conn.execute("PRAGMA table_info(archive_blocks)")]
        finally:
            conn.close()

    assert order(tmp_path / "new.db") == order(tmp_path / "old.db"), (order(tmp_path / "new.db"),
                                                                      order(tmp_path / "old.db"))
