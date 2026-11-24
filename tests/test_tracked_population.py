"""A guard's population comes from git, not from the filesystem.

THREE OCCURRENCES, WHICH IS WHAT MAKES IT A CLASS
    M.5  the operator had three untracked scratch scripts under `scripts/`,
         and `test_every_script_calls_force_utf8_stdio` invented three
         failures that did not exist in the repository. Fixed there, once,
         with `git ls-files`.
    U.2  `test_every_script_loads_dotenv_in_main` did the same thing six
         weeks later — six scratch files — because it globbed the directory
         again instead of reusing M.5's answer.
    …and when this was written, SIXTEEN of the suite's twenty enumerators
    were listing a repository directory from the filesystem. Only M.5's used
    git.

    Fixing it a third time by hand would guarantee a fourth. So the answer
    is a shared helper (`tests/_tracked.py`) plus the guard below, which
    fails on the next test that enumerates a repo directory without it.

WHY IT MATTERS BEYOND SCRIPTS
    The `backend/` sweeps are worse than the `scripts/` ones, and nobody had
    hit them yet. `test_governed_adaptation`'s frozen UPDATE-column set,
    `test_provider_isolation`'s PROVIDER-importer ratchet,
    `test_startup_contract`'s `is_pulled`-reader set and
    `test_sse_contract`'s three-way reconciliation all derive a FROZEN SET
    from a directory listing. One scratch file under `backend/` — a probe, a
    half-finished module — and each of them reports a change to the
    codebase that is not in the codebase.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from tests._tracked import REPO, tracked, tracked_backend, tracked_scripts

TEST_FILES = tracked("tests/*.py")

# Directories whose contents are a property of the REPOSITORY. Enumerating one
# of these from the filesystem is the defect; enumerating a `tmp_path` or the
# real `data/` tree is not, and the guard distinguishes them.
REPO_DIRS = ("backend", "frontend", "scripts", "tests")

# Enumerators that legitimately read the filesystem, each with its reason.
# Frozen: a new entry is a claim that something is not a repository listing,
# and it has to be made deliberately.
ALLOWED = {
    # fingerprints the operator's real data/ tree to prove the suite did not
    # write to it — the files there are precisely NOT tracked
    ("tests/conftest.py", "rglob"),
    # (test_import_purity's rglob lives inside a subprocess SOURCE STRING, so
    # the AST walk below cannot see it — and it walks a temp dir anyway.)
    # temp snapshot dirs written during the test itself
    ("tests/test_governance_cadence.py", "glob"),
    # the shared helper's own declared fallback for a tree with no git
    ("tests/_tracked.py", "rglob"),
    # walks whatever directory the caller passed, including tmp_path
    ("tests/test_encoding_parity.py", "rglob"),
    # `test_tracked_returns_only_tracked_files` globs `scripts/` ON PURPOSE —
    # it is the control half of the reproduction, proving the filesystem DOES
    # see the scratch file that `tracked_scripts()` must not.
    ("tests/test_tracked_population.py", "glob"),
}


def _enumerator_sites() -> list[tuple[str, int, str, str]]:
    """(file, line, method, source line) for every glob/walk in tests/."""
    out = []
    for path in TEST_FILES:
        src = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO).as_posix()
        tree = ast.parse(src, filename=str(path))
        lines = src.splitlines()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"glob", "rglob", "iterdir",
                                           "listdir", "walk"}):
                out.append((rel, node.lineno, node.func.attr,
                            lines[node.lineno - 1].strip()))
    return out


def test_no_test_enumerates_a_repo_directory_from_the_filesystem():
    """THE GUARD ON THE CLASS.

    AST rather than a text search — this file names `rglob` in prose a dozen
    times, and the sixth grep-based guard in this project to fire on its own
    documentation would be one too many.

    A site is a violation when it globs a path expression that mentions a
    repository directory AND is not on the allow-list. The allow-list carries
    a reason per entry, because "it looked fine" is how the first sixteen got
    written.
    """
    violations = []
    for rel, lineno, method, text in _enumerator_sites():
        if (rel, method) in ALLOWED:
            continue
        if any(f'"{d}"' in text or f"'{d}'" in text or f"/ {d}" in text
               for d in REPO_DIRS):
            violations.append(f"  {rel}:{lineno}  {text}")
    assert not violations, (
        "these enumerate a repository directory from the filesystem, so an "
        "untracked scratch file changes the suite's population:\n"
        + "\n".join(violations)
        + "\n\nUse tests._tracked.tracked(...) / tracked_backend() / "
          "tracked_scripts(). If the path is genuinely not a repository "
          "listing (a tmp_path, the real data/ tree), add it to ALLOWED with "
          "the reason.")


def test_the_allow_list_has_no_stale_entries():
    """A frozen list that names files which no longer enumerate anything is a
    list nobody is maintaining."""
    seen = {(rel, method) for rel, _l, method, _t in _enumerator_sites()}
    stale = sorted(ALLOWED - seen)
    assert not stale, f"ALLOWED names sites that no longer exist: {stale}"


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------

def test_tracked_returns_only_tracked_files(tmp_path):
    """THE REPRODUCTION, and the whole point.

    An untracked file is created under `scripts/` — the operator's exact
    condition — and `tracked_scripts()` must not see it while a filesystem
    glob does. Cleaned up in a `finally`, because a test that leaves a
    scratch file behind would be causing the very defect it guards.
    """
    scratch = REPO / "scripts" / "_u2_scratch_probe.py"
    assert not scratch.exists(), "the probe name is already taken"
    try:
        scratch.write_text("# untracked scratch file\n", encoding="utf-8")

        globbed = {p.name for p in (REPO / "scripts").glob("*.py")}
        assert scratch.name in globbed, (
            "the filesystem does not see the file this test just wrote")

        assert scratch.name not in {p.name for p in tracked_scripts()}, (
            "tracked_scripts() returned an untracked file — the helper is "
            "globbing, and M.5's fix has been undone")
    finally:
        scratch.unlink(missing_ok=True)
    assert not scratch.exists()


def test_tracked_scripts_excludes_underscore_helpers():
    """`_console.py` and `_env.py` are imported, not run. They have no
    `main()`, and the ratchets that parametrise over scripts would fail on
    them for a reason that is not a defect."""
    names = {p.name for p in tracked_scripts()}
    assert names, "no scripts found at all"
    assert not any(n.startswith("_") for n in names), sorted(names)
    assert "acceptance_governed_adaptation.py" in names


def test_the_backend_sweep_includes_the_top_level_modules():
    """MEASURED, and it caught my own draft.

    `git ls-files backend/**/*.py` looks recursive and is not — git pathspec
    `*` already crosses `/`, so `**/` demands at least one directory level.
    The first version of `tracked_backend()` used it and silently dropped
    `server.py`, `env.py` and `ollama_client.py`.

    The whole suite still passed, because every frozen set in every backend
    sweep happens to name nested files. That is luck. A guard that stops
    looking at `backend/server.py` does not fail; it just stops guarding, and
    THAT is the failure mode this module exists to prevent, one level up.
    """
    names = {p.name for p in tracked_backend()}
    # W1 — learning.py stands where ollama_client.py stood: the retired shim
    # was only a witness that a top-level module is swept, never the point.
    for top_level in ("server.py", "env.py", "learning.py"):
        assert top_level in names, (
            f"backend/{top_level} is missing from the sweep — the pathspec "
            f"is dropping top-level modules again")
    assert any(p.parent.name == "kb" for p in tracked_backend()), (
        "backend/kb/*.py is missing; the sweep stopped recursing")
    assert all(p.suffix == ".py" for p in tracked_backend())


def test_the_fallback_is_reachable_and_declared():
    """No git binary, no index — a source tarball. The helper falls back to
    the filesystem rather than returning nothing, because a guard that
    silently covers zero files is worse than one that covers a scratch file.
    """
    import tests._tracked as mod

    real = mod._proc.run                     # W4b: the helper starts git through tests/_proc.py
    try:
        mod._proc.run = lambda *a, **k: (_ for _ in ()).throw(
            FileNotFoundError("git"))
        out = mod.tracked("scripts/*.py")
    finally:
        mod._proc.run = real
    assert out, "the fallback returned nothing"
    assert all(p.suffix == ".py" for p in out)
