"""Snapshot tamper-evidence: the collision guard and the audit beside the seal.

TWO DEFECTS, BOTH EVIDENCED ON A REAL STORE (TEMPORAL_PROBE.md)

    1. `_snapshot_and_fingerprint` named files `cortex-{int(time.time())}.json`
       and `write_text` overwrites, so two snapshots in the same second wrote
       one file. The earlier block's sealed content was not merely
       unverifiable — it was DESTROYED, and `verify_chain()` stayed valid,
       because nothing re-reads the file after `append()`. The trigger is
       `POST /kairos/cycle` (force_snapshot=True, wired to a UI button): two
       clicks in one second, one damaged block. Block 0 of the operator's
       chain is exactly this signature.

    2. `verify_chain()` never re-reads snapshot files at all, while the
       module docstring read as though it did. The fix is NOT to fold a file
       check into the verdict — a filesystem observation is present-tense and
       changes when a file is restored, while the chain verdict is a claim
       about the immutable log. `audit_snapshots()` is the file check, as an
       additive sibling: `verify_chain()`'s verdict and signature are pinned
       untouched here.

THE SIX STATES the audit must keep distinct, because collapsing any two
destroys a signal (the same argument verify_sealed_lineage makes for
row/seal/chain):

    absent_by_design      governance blocks carry no snapshot (S.2b)
    missing               sealed over a real file that is gone NOW —
                          unexpected, the file existed at seal time
    missing_at_seal       F-16's state: absent when sealed, still absent.
                          Consistent with what the chain recorded, so it is
                          not an alarm — but calling it "intact" would claim
                          a file was checked when there is no file
    altered               file present, hash differs from the sealed hash
    unexpected_file       sealed as absent, but a file exists there NOW —
                          someone planted a file where the chain recorded
                          absence; suspicious, and it is not "altered"
    intact                file present and matching
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

from backend.memory.chain import NexusArchiveChain

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# the collision guard (defect 1)
# ---------------------------------------------------------------------------

def _kairos(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", "snapshot-tamper-token")
    from backend.server import create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    return svc.kairos


def test_two_snapshots_in_one_second_produce_two_files_and_two_intact_blocks(
        tmp_path, monkeypatch):
    """THE SENTINEL for defect 1. Before the guard, the second write
    overwrote the first file and block 0's sealed content was gone."""
    k = _kairos(tmp_path, monkeypatch)
    # Freeze the clock so both snapshots land in the SAME second by
    # construction, not by racing the wall clock.
    frozen = time.time()
    monkeypatch.setattr("backend.memory.kairos.time",
                        type("T", (), {"time": staticmethod(lambda: frozen)}))

    _fp1, p1 = k._snapshot_and_fingerprint()
    _fp2, p2 = k._snapshot_and_fingerprint()

    assert p1 != p2, "same-second snapshots share a path — the overwrite is back"
    assert Path(p1).is_file() and Path(p2).is_file()

    b1 = k.archive_chain.append({"probe": 1}, p1)
    b2 = k.archive_chain.append({"probe": 2}, p2)
    from backend.memory.chain import NexusArchiveChain as C
    assert C._hash_file(p1) == b1.snapshot_hash
    assert C._hash_file(p2) == b2.snapshot_hash
    assert k.archive_chain.verify_chain().valid is True


def test_the_first_snapshot_of_a_second_keeps_the_historical_name(
        tmp_path, monkeypatch):
    """FORWARD-ONLY (I-9). `snapshot_path` is inside `compute_hash`'s body,
    so existing blocks' paths can never be rewritten — the fix must not move
    the common-case name, or every future audit of an old block would look at
    a path scheme that no longer exists. Only COLLIDING names change."""
    k = _kairos(tmp_path, monkeypatch)
    frozen = time.time()
    monkeypatch.setattr("backend.memory.kairos.time",
                        type("T", (), {"time": staticmethod(lambda: frozen)}))
    _fp, p1 = k._snapshot_and_fingerprint()
    assert Path(p1).name == f"cortex-{int(frozen)}.json", (
        "the non-colliding name changed — that orphans nothing but breaks "
        "the property that old and new snapshots are named alike")
    _fp, p2 = k._snapshot_and_fingerprint()
    assert Path(p2).name == f"cortex-{int(frozen)}-1.json"
    _fp, p3 = k._snapshot_and_fingerprint()
    assert Path(p3).name == f"cortex-{int(frozen)}-2.json"


# ---------------------------------------------------------------------------
# the audit (defect 2) — additive, beside the verdict
# ---------------------------------------------------------------------------

@pytest.fixture
def chain(tmp_path):
    c = NexusArchiveChain(tmp_path / "archive.db")
    yield c
    c.close()


def _snap(tmp_path, name, payload) -> str:
    p = tmp_path / name
    p.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return str(p)


def test_the_audit_names_all_six_states_distinctly(chain, tmp_path):
    """One chain holding every state at once, so no pair can be conflated by
    an implementation that happens to pass single-state tests."""
    p_intact  = _snap(tmp_path, "intact.json", {"v": 1})
    p_altered = _snap(tmp_path, "altered.json", {"v": 2})
    p_missing = _snap(tmp_path, "missing.json", {"v": 3})
    p_planted = str(tmp_path / "planted.json")          # absent at seal time
    p_never   = str(tmp_path / "never-existed.json")    # absent then AND now

    b0 = chain.append({"n": 0}, p_intact)
    b1 = chain.append({"n": 1}, p_altered)
    b2 = chain.append({"n": 2}, p_missing)
    b3 = chain.append({"lineage_seal": {}}, "", kind="governance")
    b4 = chain.append({"n": 4}, p_planted)              # sealed as missing
    b5 = chain.append({"n": 5}, p_never)                # sealed as missing

    Path(p_altered).write_text(json.dumps({"v": "rewritten"}), encoding="utf-8")
    Path(p_missing).unlink()
    Path(p_planted).write_text("appeared after sealing", encoding="utf-8")

    report = chain.audit_snapshots()
    by_idx = {r["index"]: r["state"] for r in report["blocks"]}
    assert by_idx == {
        b0.index: "intact",
        b1.index: "altered",
        b2.index: "missing",
        b3.index: "absent_by_design",
        b4.index: "unexpected_file",
        b5.index: "missing_at_seal",
    }, f"states conflated: {by_idx}"
    assert report["intact"] == 1
    assert report["altered"] == [b1.index]
    assert report["missing"] == [b2.index]
    assert report["unexpected_file"] == [b4.index]
    assert report["missing_at_seal"] == [b5.index]
    # An altered snapshot carries both hashes, so an operator can grep their
    # backups for the sealed one instead of learning only that it is gone.
    row = next(r for r in report["blocks"] if r["index"] == b1.index)
    assert row["sealed_hash"] and row["live_hash"] and \
        row["sealed_hash"] != row["live_hash"]


def test_the_audit_is_present_tense_a_restored_file_clears_it(chain, tmp_path):
    """The reason this is NOT part of the verdict: the observation must
    change when the filesystem does. A sealed field could never clear."""
    p = _snap(tmp_path, "s.json", {"v": 1})
    original = Path(p).read_bytes()
    b = chain.append({"n": 0}, p)
    Path(p).write_text("tampered", encoding="utf-8")
    assert chain.audit_snapshots()["altered"] == [b.index]
    Path(p).write_bytes(original)
    report = chain.audit_snapshots()
    assert report["altered"] == [] and report["intact"] == 1


def test_the_audit_finds_the_operator_shape(chain, tmp_path):
    """NON-VACUITY against the evidenced defect: an overwritten same-second
    snapshot — one file, two blocks — is reported as exactly one altered
    block (the earlier one) while verify_chain() stays valid. This is the
    real store's block 0, reproduced."""
    p = _snap(tmp_path, "cortex-100.json", {"v": 1})
    b0 = chain.append({"n": 0}, p)
    Path(p).write_text(json.dumps({"v": 2}, sort_keys=True), encoding="utf-8")   # the overwrite
    b1 = chain.append({"n": 1}, p)

    assert chain.verify_chain().valid is True, "precondition: chain verifies"
    report = chain.audit_snapshots()
    assert report["altered"] == [b0.index]
    assert next(r["state"] for r in report["blocks"]
                if r["index"] == b1.index) == "intact"


def test_the_verdict_is_untouched_by_snapshot_damage(chain, tmp_path):
    """THE CONSTRAINT THE BUILD WAS CLEARED UNDER. verify_chain()'s verdict
    and signature do not move: snapshot damage is the audit's finding, never
    a changed chain verdict — and the result surface itself is pinned so an
    additive field cannot ride in unnoticed."""
    p = _snap(tmp_path, "s.json", {"v": 1})
    chain.append({"n": 0}, p)
    before = chain.verify_chain()
    Path(p).write_text("tampered", encoding="utf-8")
    after = chain.verify_chain()
    assert before == after
    assert after.valid is True and after.reason == ""
    from backend.memory.chain import ChainVerificationResult
    from dataclasses import fields
    assert [f.name for f in fields(ChainVerificationResult)] == [
        "valid", "n_blocks", "broken_at", "reason", "missing_snapshots"], (
        "ChainVerificationResult's shape moved — that is a change to what "
        "the product's verification claims, and it was explicitly out of "
        "scope for this packet")


# ---------------------------------------------------------------------------
# the endpoint — opt-in, and the default poll is unchanged
# ---------------------------------------------------------------------------

def test_chain_status_audit_is_opt_in(tmp_path, monkeypatch):
    """The audit re-reads every snapshot file, and the UI hits /chain/status
    on a timer — so the default response must be byte-for-byte what it was
    before the audit existed, and `?audit=true` must actually carry the
    finding. Both directions, or the field is either always-on I/O or
    unreachable."""
    monkeypatch.setenv("NEXUS_API_TOKEN", "snapshot-tamper-token")
    from fastapi.testclient import TestClient

    from backend.server import create_app, create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    p = _snap(tmp_path, "s.json", {"v": 1})
    b = svc.archive_chain.append({"n": 0}, p)
    Path(p).write_text("tampered", encoding="utf-8")

    client = TestClient(create_app(svc))
    plain = client.get("/chain/status").json()
    assert "snapshot_audit" not in plain, (
        "the audit ran on the default poll — that is the timer-path I/O the "
        "opt-in exists to avoid")
    assert plain["verification"]["valid"] is True

    audited = client.get("/chain/status?audit=true").json()
    assert audited["snapshot_audit"]["altered"] == [b.index]
    # The verdict beside it is UNCHANGED by the finding — the whole design.
    assert audited["verification"] == plain["verification"]


# ---------------------------------------------------------------------------
# THE AST GUARD — the audit stays additive, structurally
# ---------------------------------------------------------------------------

def test_verify_chain_never_reads_a_file():
    """`_hash_file` must acquire no caller inside `verify_chain` (nor the
    audit inside the verdict), so a later well-meaning change cannot quietly
    convert the additive report into a changed verdict. AST, not grep."""
    src = (ROOT / "backend" / "memory" / "chain.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    verify = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "verify_chain")
    called = {
        (n.func.attr if isinstance(n.func, ast.Attribute)
         else getattr(n.func, "id", ""))
        for n in ast.walk(verify) if isinstance(n, ast.Call)
    }
    forbidden = {"_hash_file", "audit_snapshots", "open", "read_bytes",
                 "read_text", "is_file", "exists"}
    assert not (called & forbidden), (
        f"verify_chain now touches the filesystem via {called & forbidden} — "
        f"the verdict has stopped being a pure function of the rows, which "
        f"changes what the product's verification claims")


def test_the_guard_is_not_vacuous():
    """The same detector must SEE `_hash_file`'s legitimate callers, or the
    empty intersection above proves only that the detector is blind."""
    src = (ROOT / "backend" / "memory" / "chain.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    callers = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "_hash_file"):
                callers.add(fn.name)
    assert "append" in callers, "detector no longer sees append's call"
    assert "audit_snapshots" in callers, (
        "audit_snapshots does not call _hash_file — either it stopped "
        "re-reading files (vacuous audit) or the detector is broken")
    assert "verify_chain" not in callers
