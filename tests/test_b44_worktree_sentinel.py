"""B-44 — the worktree sentinel's companions.

The sentinel lives in tests/conftest.py (snapshot at session start, comparison
at teardown) over tests/_worktree.py. These exercise the comparison DIRECTLY
against a snapshot and clean up after themselves, so the companion's own file
is never what the real teardown finds — a companion that relied on teardown
would make the suite flaky by design.
"""
from __future__ import annotations

import os
import contextlib
import subprocess
from tests import _proc
import sys

from tests._worktree import CHANGED, NOT_A_CHECKOUT, REPO, UNCHANGED, compare, describe, porcelain


@contextlib.contextmanager
def _stray(suffix: str):
    """A stray file's path under `reports/`, removed after. W11c (the operator's answer): the folder is made when it
    is absent, as in the public tree, which has no `reports/`, and removed after if this made it, so the checkout
    is left as it was found."""
    folder = REPO / "reports"
    made = not folder.is_dir()
    folder.mkdir(exist_ok=True)
    path = folder / f"stray_b44_{os.getpid()}{suffix}"
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)
        if made:
            folder.rmdir()


def test_a_stray_file_is_named_by_the_comparison():
    """Plant a file the sentinel's source does not name, under `reports/`, and read it back
    out of the comparison by name — then remove it and show the comparison returns to
    unchanged."""
    snapshot = porcelain()
    assert snapshot["state"] == "ok", snapshot
    with _stray(".txt") as path:
        rel = path.relative_to(REPO).as_posix()
        path.write_text("stray\n", encoding="utf-8")
        cmp = compare(snapshot, porcelain())
        print(f"\nB-44 ({sys.platform}): {cmp}")
        assert cmp["verdict"] == CHANGED
        assert f"?? {rel}" in cmp["added"], cmp
        assert rel in describe(cmp), describe(cmp)
    assert compare(snapshot, porcelain())["verdict"] == UNCHANGED


def test_the_ignored_class_is_invisible_declared():
    """THE DECLARED GAP. `.gitignore` (2e5c7bd) ignores `reports/*.json` — exactly the class
    FZ-2's traced script runs wrote into the repository, the defect B-44 exists because of —
    so a stray of that shape is invisible to this sentinel by the operator's decision. Pinned
    as an exception that FAILS WHEN IT CLOSES: un-ignoring the class must be a deliberate act
    that edits this test, not a side effect of a `.gitignore` edit."""
    snapshot = porcelain()
    with _stray(".json") as path:
        path.write_text("{}\n", encoding="utf-8")
        ignored = _proc.run(["git", "check-ignore", "-q", str(path)], cwd=REPO, timeout=120).returncode == 0
        cmp = compare(snapshot, porcelain())
    assert ignored, "reports/*.json is no longer ignored: the gap closed — edit this exception deliberately"
    assert cmp["verdict"] == UNCHANGED, "an ignored stray became visible: the gap closed — edit this exception deliberately"


def test_not_a_checkout_is_never_unchanged(tmp_path, monkeypatch):
    """A snapshot that could not be taken says so, and a comparison resting on one never
    reads unchanged — whichever side it is on, and even when both sides are the same
    failure. Two causes: the directory is not a checkout; git is missing."""
    ok = porcelain()
    assert ok["state"] == "ok"
    nc = porcelain(tmp_path)                        # a directory with no .git
    assert nc["state"] == NOT_A_CHECKOUT and nc["error"], nc
    for before, after in ((nc, ok), (ok, nc), (nc, nc)):
        cmp = compare(before, after)
        assert cmp["verdict"] == NOT_A_CHECKOUT and cmp["verdict"] != UNCHANGED
        msg = describe(cmp)
        assert NOT_A_CHECKOUT in msg and "unchanged" not in msg.split("not `unchanged`")[0]

    def no_git(*a, **kw):
        raise FileNotFoundError("git")
    monkeypatch.setattr(_proc, "run", no_git)            # W4b: the helper starts git through tests/_proc.py
    missing = porcelain()
    assert missing["state"] == NOT_A_CHECKOUT and "FileNotFoundError" in missing["error"]
    assert compare(ok, missing)["verdict"] == NOT_A_CHECKOUT


def test_the_sentinel_is_armed():
    """The session snapshot exists, was taken, and the data/ fingerprint is still beside it."""
    conftest = next(m for m in sys.modules.values()
                    if getattr(m, "__file__", "") and m.__file__.replace("\\", "/").endswith("tests/conftest.py"))
    assert conftest.WORKTREE_BASELINE["state"] == "ok", conftest.WORKTREE_BASELINE
    assert hasattr(conftest, "_assert_worktree_unchanged") and hasattr(conftest, "_assert_real_data_untouched")
    assert "data/kb.db" in conftest.GUARDED_REAL_PATHS
