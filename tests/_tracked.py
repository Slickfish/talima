"""A guard's population is a property of the codebase, not of the machine.

THE DEFECT, THREE TIMES NOW
    M.5   the operator had three untracked scratch scripts under `scripts/`,
          and `test_every_script_calls_force_utf8_stdio` invented three
          failures that did not exist in the repository and could not be
          fixed by changing it. Fixed there with `git ls-files`.
    U.2   `test_every_script_loads_dotenv_in_main` did exactly the same thing
          six weeks later — six scratch files this time — because it enumerated
          the directory again instead of reusing M.5's answer.
    …and sixteen of the suite's twenty enumerators were listing a repository
    directory from the filesystem when this was written.

    Writing the fix twice is what makes it a class rather than two bugs. So
    the third fix is a shared helper, and the guard below asserts that no
    test enumerates a repo directory without it.

WHY NOT `.gitignore` THE SCRATCH FILES
    That fixes one operator's tree, once, for the files they happen to have
    today. The property a guard needs is "the files that ship", and git is
    the only thing that knows which those are.

WHY A FALLBACK RATHER THAN A SKIP
    A source tarball or a container without the git binary has no index. A
    guard that silently covers nothing is worse than one that occasionally
    covers a scratch file, so the fallback is the filesystem — declared here
    rather than inferred at the call site.
"""
from __future__ import annotations

import subprocess
from tests import _proc
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def tracked(*patterns: str) -> list[Path]:
    """Absolute paths of TRACKED files matching git pathspecs.

    `tracked("scripts/*.py")`, `tracked("backend/**/*.py")`,
    `tracked("frontend/*.html")`.

    Note the git semantics: `git ls-files` pathspecs are not `Path.glob`
    patterns — `scripts/*.py` matches only that directory, and a recursive
    sweep wants `backend/**/*.py`. Getting this wrong shrinks a guard's
    population silently, so the callers below are explicit about which they
    want and `test_tracked_helper.py` pins both.
    """
    try:
        proc = _proc.run(
            ["git", "ls-files", "--", *patterns],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=REPO, timeout=30)
        if proc.returncode == 0 and proc.stdout.strip():
            return sorted(REPO / line.strip()
                          for line in proc.stdout.splitlines() if line.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    # Fallback: the filesystem. Declared, and the reason is in the docstring.
    out: list[Path] = []
    for pattern in patterns:
        out.extend((REPO / pattern.split("/", 1)[0]).rglob(
            pattern.rsplit("/", 1)[-1]))
    return sorted(set(out))


def tracked_scripts() -> list[Path]:
    """Every shipped entry point under `scripts/`. Not `_`-prefixed helpers —
    those have no `main()` and are imported, not run."""
    return [p for p in tracked("scripts/*.py") if not p.name.startswith("_")]


def tracked_backend() -> list[Path]:
    """Every shipped module under `backend/`, recursively.

    THE PATHSPEC IS A DIRECTORY, NOT A GLOB, and that is measured rather than
    stylistic. `git ls-files backend/**/*.py` looks recursive and is not: git
    pathspec `*` already crosses `/`, so `**/` demands at least one directory
    level and DROPPED the three top-level modules — `server.py`, `env.py`,
    `ollama_client.py`.

    Every backend sweep in the suite still passed, because each frozen set
    happens to name only nested files. That is luck, not coverage: a guard
    that quietly stops looking at `backend/server.py` does not fail, it just
    stops guarding. Naming the directory and filtering the suffix cannot go
    wrong the same way.
    """
    return [p for p in tracked("backend") if p.suffix == ".py"]


def tracked_frontends() -> list[Path]:
    return tracked("frontend/*.html")


__all__ = ["REPO", "tracked", "tracked_backend", "tracked_frontends",
           "tracked_scripts"]
