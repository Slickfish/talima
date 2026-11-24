"""W10b part 5 — the simulated UAT's actions section (RUN_WORKLIST W10b's exit; run/specs/W10b.md, Exit test).

WHAT IS HELD HERE
    UA1 `--sections actions,mcp` on real `start.py --serve` and `python -m backend.mcp` children under the stub:
        exit 0 and every actions step PASS; the rows reach what the exit names (a consented search turn, the seal,
        the action's receipt, the open lookup by signature, nexus-verify with --action) and the failure it names
        (action_mismatch); the mcp section's reported action carries client_reported; the checkout's status and
        data/ as they were.

LAW 16. Run on 0e5402b (part 4, the simulator without the section): UA1 fails there on `--sections actions`
refused (exit 2, an unknown section).
"""
from __future__ import annotations

import re
import subprocess
import sys

import pytest

from tests import _proc
from tests._tracked import REPO
from tests._worktree import UNCHANGED, compare, describe, porcelain
from tests.test_w4_uat_simulate import _data_listing, _run_env, step_rows

SCRIPT = REPO / "scripts" / "uat_simulate.py"
REACHES = {"POST /search/confirm", "POST /chat", "POST /kairos/cycle", "GET /actions/{id}",
           "GET /identity/signed-action"}
REACH = re.compile(r"\b(?:GET|POST) /[A-Za-z0-9_{}./-]*[A-Za-z0-9_}]")


@pytest.fixture(scope="module")
def actions_run(tmp_path_factory):
    base = tmp_path_factory.mktemp("uat_actions")
    report, work = base / "report.md", base / "work"
    status_before, data_before = porcelain(), _data_listing()
    proc = _proc.run([sys.executable, str(SCRIPT), "--sections", "actions,mcp", "--report", str(report),
                      "--work", str(work)], cwd=REPO, env=_run_env(), stdin=subprocess.DEVNULL,
                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1500)
    return {"proc": proc, "report": report, "status_before": status_before, "status_after": porcelain(),
            "data_before": data_before, "data_after": _data_listing()}


def test_ua1_the_actions_section_runs_end_to_end(actions_run):
    proc, report = actions_run["proc"], actions_run["report"]
    print(f"\nUA1 ({sys.platform}) exit {proc.returncode}; "
          f"{[l for l in proc.stdout.splitlines() if l.startswith('uat-simulate')]}")
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-4000:]
    rows = step_rows(report.read_text(encoding="utf-8"))
    actions = [r for r in rows if r["id"].startswith("actions.")]
    print(f"UA1 {len(actions)} actions steps: {[(r['id'], r['result']) for r in actions]}")
    assert len(actions) >= 5 and all(r["result"] == "PASS" for r in actions), [r for r in actions
                                                                                if r["result"] != "PASS"]
    section = "\n".join(" | ".join(r[k] for k in ("what", "input", "expected", "seen")) for r in actions)
    missing = REACHES - set(REACH.findall(section))
    assert not missing, f"no actions step reaches {sorted(missing)}"
    for named in ("action_mismatch", "nexus_verify.py --action", "no token", "Signature"):
        assert named in section, f"no actions row names {named!r}"
    reported = [r for r in rows if r["id"].startswith("mcp.") and "nexus_record_action" in r["what"]]
    assert reported and reported[0]["result"] == "PASS" and "client_reported" in reported[0]["seen"], reported
    assert compare(actions_run["status_before"], actions_run["status_after"])["verdict"] == UNCHANGED, \
        describe(compare(actions_run["status_before"], actions_run["status_after"]))
    assert actions_run["data_after"] == actions_run["data_before"], "the run wrote under the checkout's data/"
