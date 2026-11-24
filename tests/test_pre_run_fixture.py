"""Law 4's fixture: every record the run started from still recomputes.

`tests/fixtures/pre_run_data/` is a data directory as NEXUS writes it —
`kb.db`, `archive.db`, `snapshots/` — captured ONCE, before W0b's first
record-layer change, by `tests/fixtures/make_pre_run_data.py` run from a
worktree at `f45a18f`, whose `backend/` tree (`ba6a1448`) is MCP-2's
`e9894ae` byte for byte. `MANIFEST.json` names every kept file with its
sha256 and the facts below. `RUN_CHARTER.md` law 4: "the LS fixture literal
and the pre-run data fixture prove it on every commit"; W4 upgrades from it.

THE FIXTURE IS NEVER REGENERATED. A later commit that changes how records
are written changes what NEW records look like; these were written by the
base, and the point is that today's code still reads them. Each test copies
the files first, because opening a store runs additive migrations.

What it holds, written through the product's own writers: four console
turns (an attributed unit, the session pseudo-unit, a close boundary, a
frozen turn) and one MCP turn (`mcp_block`); a confirmed assertion and a
refused one; an approval and a supersede with its recall count; one
archival and one archival reversed; a snapshot block and a governance
block. At capture, the base verifier reported one recalibration event as a
content edit: the zero-sign defect (`tests/test_w0b_zero_sign.py`), recorded
in the manifest as found and now verified under Ruling 1.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from backend.kb import as_of as as_of_mod
from backend.kb.recalibration import RecalibrationStore
from backend.memory.chain import NexusArchiveChain
from backend.memory.lineage import LineageStore, verify_sealed_lineage
import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pre_run_data"
MANIFEST = json.loads((FIXTURE / "MANIFEST.json").read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy(tmp_path: Path) -> Path:
    """The manifest's files, copied under `<tmp>/data` (names are posix
    relative paths; `Path` joins them on every platform)."""
    dst = tmp_path / "data"
    for name in MANIFEST["files"]:
        target = dst / Path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FIXTURE / Path(name), target)
    return dst


def test_the_fixture_is_the_one_captured():
    """Every committed file is byte-identical to its manifest digest, and the
    capture is the base: the manifest names `f45a18f` and `e9894ae`'s
    backend tree. (`.gitattributes` holds the directory `-text`, so a
    Windows checkout does not rewrite the snapshot's line endings.)"""
    changed = [n for n, d in MANIFEST["files"].items() if _sha256_file(FIXTURE / Path(n)) != d]
    print(f"\nPRF-1 ({sys.platform}) {len(MANIFEST['files'])} files; head {MANIFEST['head'][:7]}, "
          f"backend tree {MANIFEST['backend_tree'][:8]}; changed: {changed}")
    assert MANIFEST["files"] and changed == []
    assert MANIFEST["head"].startswith("f45a18f") and MANIFEST["backend_tree"].startswith("ba6a1448")


@pytest.mark.sqlite_only("opens a copy of the committed pre-run SQLite fixture (tests/fixtures/pre_run_data); its Postgres twin is the migration round trip (W3b part 4, T10)")
def test_every_record_and_seal_recomputes_on_a_copy(tmp_path):
    """Opened by today's code on a copy: the same records, every one intact,
    the chain valid, every seal resolving, the governance rows intact (one
    resolved by the zero-sign rule, which the base verifier reported as an
    edit), the recall index consistent, and as-of answering as it did."""
    d = _copy(tmp_path)
    lin = LineageStore(d / "kb.db")
    records = [lin._row_to_record(r) for r in lin._all_rows()]
    hashes = sorted(r.record_hash for r in records)
    bad = [r.lineage_id for r in records if not lin.verify_record(r.lineage_id)["record_intact"]]
    chain = NexusArchiveChain(d / "archive.db")
    cv = chain.verify_chain()
    sealed = verify_sealed_lineage(lin, chain)
    gov = RecalibrationStore(d / "kb.db").verify_governance_records(chain)
    recall = lin.verify_recall_index()
    held = [(i["held"], as_of_mod.held_at(lin.conn, i["ts"])[1]) for i in MANIFEST["as_of_instants"]]
    print(f"\nPRF-2 ({sys.platform}) records {len(records)} (intact {len(records) - len(bad)}); chain valid "
          f"{cv.valid} over {cv.n_blocks} blocks; seals {sealed['verdict']}; governance {gov['verdict']} "
          f"(checked {gov['row']['checked']}, zero_sign_legacy {gov['row'].get('zero_sign_legacy')}); recall index "
          f"consistent {recall['consistent']}; as-of held at capture/now {held}")
    assert len(records) == MANIFEST["lineage"]["count"] == 5
    assert hashes == MANIFEST["lineage"]["record_hashes"]
    assert hashlib.sha256("\n".join(hashes).encode()).hexdigest() == MANIFEST["lineage"]["record_hashes_sha256"]
    assert bad == []
    assert sorted({r.record_scope or "full_turn" for r in records}) == MANIFEST["lineage"]["scopes"]
    assert cv.valid and cv.n_blocks == MANIFEST["chain"]["blocks"]
    assert chain.latest().block_hash == MANIFEST["chain"]["head_hash"]
    assert sealed["verdict"] == "intact" and sealed["records_checked"] == len(records)
    assert gov["verdict"] == "intact", gov["row"]["failures"]
    assert gov["row"]["zero_sign_legacy"] == 1, "the base's one -0.0 row is the one the rule resolves"
    assert recall["consistent"]
    assert all(then == now for then, now in held)
    lin.close(); chain.close()


@pytest.mark.sqlite_only("opens a copy of the committed pre-run SQLite fixture (tests/fixtures/pre_run_data); its Postgres twin is the migration round trip (W3b part 4, T10)")
def test_companion_an_edited_record_in_a_copy_is_named(tmp_path):
    """The recompute is not vacuous: one console record's model id edited in
    the copy (its hash left behind) is named by the row check and by the
    sealed-lineage check."""
    d = _copy(tmp_path)
    lin = LineageStore(d / "kb.db")
    rid = MANIFEST["turns"]["console"][0]
    lin.conn.execute("UPDATE lineage_records SET model_id = model_id || '-edited' WHERE lineage_id = ?", (rid,))
    lin.conn.commit()
    assert lin.verify_record(rid)["record_intact"] is False
    chain = NexusArchiveChain(d / "archive.db")
    assert verify_sealed_lineage(lin, chain)["verdict"] != "intact"
    lin.close(); chain.close()


def test_companion_a_changed_fixture_byte_is_named(tmp_path):
    """The manifest check is not vacuous: a copy of the fixture with one byte
    of `kb.db` flipped no longer matches the digest the first test reads."""
    d = _copy(tmp_path)
    raw = bytearray((d / "kb.db").read_bytes())
    raw[len(raw) // 2] ^= 0x01
    (d / "kb.db").write_bytes(bytes(raw))
    assert _sha256_file(d / "kb.db") != MANIFEST["files"]["kb.db"]
