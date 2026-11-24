"""W9 part 3c — the simulated UAT's verification section (RUN_WORKLIST W9's exit; run/specs/W9.md, Exit test).

WHAT IS HELD HERE
    VU1 `--sections verification` (the console section added, because it needs it) on real `start.py --serve`
        children under the stub: exit 0 and every verification step PASS; the rows reach what the exit names
        (POST /commit both ways, POST /kairos/cycle, GET /lineage/{id}/credential, GET /identity,
        GET /commitments/{id}/receipt, nexus-verify on a credential and with --reveal) and the failures it names
        (disclosure_mismatch, unsalted_hash, reveal_mismatch, 409 dropped); the other session lists two keys; the
        checkout's status and data/ as they were.
    VU2 The tombstone is the one act not reached through an entry point and its row says so; the helper refuses a
        store another process holds with the lock's own exit code, writing nothing.
    VU3 The simulator's copy of the key file's name is the store's (it imports no store module to read it).

LAW 16. Run on the base (067a2bf, the simulator without the section, the helper absent): VU1 fails on
`--sections verification` refused (exit 2, unknown section), VU2 on the same absence (the refused run writes no
report), VU3 on no KEY_FILE in the simulator; declared here.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from backend.memory import instance_key as ik
from tests import _proc
from tests._tracked import REPO
from tests._worktree import UNCHANGED, compare, describe, porcelain
from tests.test_w4_uat_simulate import _data_listing, _run_env, step_rows

SCRIPT = REPO / "scripts" / "uat_simulate.py"
HELPER = REPO / "tests" / "_uat_tombstone.py"
REACHES = {"POST /commit", "POST /kairos/cycle", "GET /lineage/{id}/credential", "GET /identity",
           "GET /commitments/{id}/receipt"}
REACH = re.compile(r"\b(?:GET|POST) /[A-Za-z0-9_{}./-]*[A-Za-z0-9_}]")


@pytest.fixture(scope="module")
def verification_run(tmp_path_factory):
    base = tmp_path_factory.mktemp("uat_verification")
    report, work = base / "report.md", base / "work"
    status_before, data_before = porcelain(), _data_listing()
    proc = _proc.run([sys.executable, str(SCRIPT), "--sections", "verification", "--report", str(report),
                      "--work", str(work), "--keep-work"], cwd=REPO, env=_run_env(), stdin=subprocess.DEVNULL,
                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1500)
    return {"proc": proc, "report": report, "work": work, "status_before": status_before,
            "status_after": porcelain(), "data_before": data_before, "data_after": _data_listing()}


def test_vu1_the_verification_section_runs_end_to_end(verification_run):
    proc, report = verification_run["proc"], verification_run["report"]
    print(f"\nVU1 ({sys.platform}) exit {proc.returncode}; "
          f"{[l for l in proc.stdout.splitlines() if l.startswith('uat-simulate')]}")
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-4000:]
    text = report.read_text(encoding="utf-8")
    rows = [r for r in step_rows(text) if r["id"].startswith("verification.")]
    print(f"VU1 {len(rows)} verification steps: {[(r['id'], r['result']) for r in rows]}")
    assert len(rows) >= 14 and all(r["result"] == "PASS" for r in rows), [r for r in rows if r["result"] != "PASS"]
    section = "\n".join(" | ".join(r[k] for k in ("what", "input", "expected", "seen")) for r in rows)
    missing = REACHES - set(REACH.findall(section))
    assert not missing, f"no verification step reaches {sorted(missing)}"
    for named in ("disclosure_mismatch", "unsalted_hash", "reveal_mismatch", "409 dropped", "nexus_verify.py --reveal",
                  "nexus_verify.py credential.json", "first and rotation"):
        assert named in section, f"no verification row names {named!r}"
    assert compare(verification_run["status_before"], verification_run["status_after"])["verdict"] == UNCHANGED, \
        describe(compare(verification_run["status_before"], verification_run["status_after"]))
    assert verification_run["data_after"] == verification_run["data_before"], "the run wrote under the checkout's data/"
    assert (verification_run["work"] / "verification" / "session-2" / "kb.db").is_file(), "no other session's store kept"


def test_vu2_the_tombstone_is_the_one_act_off_the_entry_points_and_refuses_a_held_store(verification_run, tmp_path):
    rows = [r for r in step_rows(verification_run["report"].read_text(encoding="utf-8"))
            if "tests/_uat_tombstone.py" in r["what"] + r["input"]]
    assert len(rows) == 1 and "W8-B3" in rows[0]["what"] and "not reached through" in rows[0]["what"], rows
    from backend.store_lock import EXIT_STORE_LOCKED, acquire
    lock = acquire(tmp_path / "kb.db", mode="services")
    try:
        before = sorted(p.name for p in tmp_path.iterdir())
        proc = _proc.run([sys.executable, str(HELPER), "--data-root", str(tmp_path), "--lineage-id", "x", "--actor",
                          "a", "--reason", "r"], cwd=REPO, env=_run_env(), capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=300)
        after = sorted(p.name for p in tmp_path.iterdir())
    finally:
        lock.release()
    assert proc.returncode == EXIT_STORE_LOCKED and "REFUSED" in proc.stdout, proc.stdout + proc.stderr
    assert after == before and "kb.db" not in after, (before, after)


def test_vu3_the_simulators_key_file_name_is_the_stores():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    names = [n.value.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
             and any(isinstance(t, ast.Name) and t.id == "KEY_FILE" for t in n.targets)]
    assert names == [ik.KEY_FILE], names
