"""B-44 — the worktree sentinel, generalised from the `data/` fingerprint.

THE DEFECT. FZ-2's traced script runs wrote sixteen `reports/*.json` files
into the repository and nothing in the suite noticed; the `data/`
fingerprint (Packet A) watches six named paths and nothing else. The
generalised sentinel asks git: `git status --porcelain` at session start,
the same at teardown, and the two must be equal.

AGAINST THE SNAPSHOT, NOT AGAINST A CLEAN TREE. The operator keeps two
untracked scripts deliberately; a "must be clean" rule would fail every run
on that machine and be muted within the week. What the suite may not do is
CHANGE the status — add, remove, modify — and that is what the comparison
reads.

NEVER `unchanged` BY DEFAULT. If git is missing or the directory is not a
checkout, the snapshot is `not a checkout` and the comparison says so; a
sentinel that could not look must not report that it saw nothing
(B-39's shape, the empty data/ reading as "no mutation").

DECLARED GAP. `.gitignore` (2e5c7bd) ignores `reports/*.json` — the class
FZ-2's scripts wrote — so a stray file of that shape is invisible to this
sentinel by the operator's own decision. tests/test_b44_worktree_sentinel.py
pins the gap as an exception that fails when it closes, so closing it is a
deliberate act rather than a side effect of a `.gitignore` edit.
"""
from __future__ import annotations

import subprocess
from tests import _proc
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

NOT_A_CHECKOUT = "not a checkout"
UNCHANGED = "unchanged"
CHANGED = "changed"


def porcelain(repo: Path = REPO) -> dict:
    """The worktree status as git reports it, one sorted line per entry.

    `{"state": "ok", "lines": [...]}`, or `{"state": "not a checkout",
    "error": ...}` when git is missing, fails, or `repo` is not a checkout.
    `--untracked-files=all` names a new file inside an untracked directory
    rather than the directory, so the sentinel's message names the file."""
    try:
        proc = _proc.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return {"state": NOT_A_CHECKOUT, "error": f"{type(e).__name__}: {e}"}
    if proc.returncode != 0:
        return {"state": NOT_A_CHECKOUT,
                "error": proc.stderr.strip() or f"git exited {proc.returncode}"}
    return {"state": "ok",
            "lines": sorted(line for line in proc.stdout.splitlines() if line.strip())}


def compare(before: dict, after: dict) -> dict:
    """`verdict`: unchanged | changed | not a checkout, with every line that
    differs. `unchanged` only when BOTH snapshots were taken and agree."""
    if before.get("state") != "ok" or after.get("state") != "ok":
        return {"verdict": NOT_A_CHECKOUT, "added": [], "removed": [],
                "error": before.get("error") or after.get("error") or "no snapshot"}
    b, a = set(before["lines"]), set(after["lines"])
    added, removed = sorted(a - b), sorted(b - a)
    return {"verdict": CHANGED if (added or removed) else UNCHANGED,
            "added": added, "removed": removed, "error": None}


def describe(cmp: dict) -> str:
    """The sentinel's message: the verdict and every line it rests on."""
    if cmp["verdict"] == NOT_A_CHECKOUT:
        return (f"WORKTREE SENTINEL: {NOT_A_CHECKOUT} ({cmp['error']}) — the comparison "
                f"could not run, which is not `unchanged`")
    if cmp["verdict"] == UNCHANGED:
        return "WORKTREE SENTINEL: unchanged (git status equals its session-start snapshot)"
    lines = [f"  + {x}" for x in cmp["added"]] + [f"  - {x}" for x in cmp["removed"]]
    return ("TEST SUITE CHANGED THE WORKTREE (B-44): git status differs from its "
            "session-start snapshot:\n" + "\n".join(lines))


__all__ = ["REPO", "NOT_A_CHECKOUT", "UNCHANGED", "CHANGED", "porcelain", "compare", "describe"]
