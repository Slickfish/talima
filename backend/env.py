"""`.env` loading — one implementation, reachable from both sides.

THE DEFECT THIS EXISTS BECAUSE OF
    `start.py` read `.env` twice — `dotenv_values` at :177 and `load_dotenv()`
    at :519 — and NOTHING under `backend/` did. Six modules carried comments
    saying "so a `.env` loaded after import is honoured"; each read its
    variable late and correctly, and each was describing a load that only the
    launcher ever performed. So every launch except `start.py`'s happy path
    got no `.env` at all, and an operator with `NEXUS_API_TOKEN=…` in the file
    watched the server announce "NEXUS_API_TOKEN not set" and mint its own.

WHY THIS IS ITS OWN MODULE (Packet U.2)
    Scripts need the same behaviour — `acceptance_governed_adaptation.py`
    wants a token, `measure_vocabulary_compliance.py` reads `LLM_BACKEND` and
    `OLLAMA_URL`, `propose_contradiction_pairs.py` reads
    `NEXUS_CONTRADICTION_PAIRS` — and **no script may import
    `backend.server`**: H.2 measured that doing so migrated the operator's
    live `data/kb.db`, which is why `tests/test_import_purity.py` exists.

    So the choice was a second implementation or a module both can import. A
    second implementation is exactly how `.env` came to be read by the
    launcher and ignored by the application. This module constructs nothing
    and opens nothing; importing it is safe from anywhere.

WHICH FILES, AND IN WHAT ORDER
    `load_dotenv()` with no argument calls `find_dotenv()`, which walks up
    from the CALLING MODULE's directory rather than from the cwd — surprising
    enough that relying on it is how a loader reads the wrong file, or none.
    Both locations are named, and the order is a decision rather than an
    accident:

      1. THE REPO ROOT — intended, documented, primary. CLAUDE.md says "Env
         (.env in repo root)"; that is where the operator's file lives, and it
         is deterministic regardless of where the process was launched from.
      2. THE CURRENT WORKING DIRECTORY, if different — a fallback for a
         deployment that runs elsewhere. It fills keys the repo root does not
         set; it does not override the ones it does.

    REPO ROOT BEATS CWD, deliberately. The alternative — nearer file wins, as
    most tools do — was considered and rejected: this project documents ONE
    location, and a cwd file silently overriding the documented one is how an
    operator ends up debugging a value they cannot find. The fallback exists
    so a deployment elsewhere still works, not so it can shadow the file the
    operator has been told to edit.

    A real environment variable beats both, per `override=False`.

    Both functions take their paths, so the order is testable without a
    subprocess and without a test-only hook. `tests/test_dotenv_loading.py`
    pins it.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("nexus.env")


def env_root(default: "Path | None" = None) -> Path:
    """Which directory counts as "the repo root" for `.env` discovery.

    `NEXUS_ENV_ROOT` overrides it. Two callers want that, and neither is a
    bypass of the precedence rule below — both relocate WHERE the root is,
    and nothing about "root beats cwd, a real variable beats both" changes:

      * a deployment running the package from site-packages, whose config
        does not live beside `backend/`;
      * the suite. Without this the root is derived from `__file__` and
        cannot be moved, so `tests/test_dotenv_settings_take_effect.py` could
        only isolate on a machine with no `.env` — which is every machine
        except a working one. A guard nobody can run clean is a guard that
        gets muted, and this project has that failure mode on record from the
        slow suite before Packet R.

    Deliberately NOT a way to name the file: it names the directory, so the
    documented "`.env` in repo root" stays the only filename anywhere.
    """
    raw = os.environ.get("NEXUS_ENV_ROOT", "").strip()
    if raw:
        return Path(raw)
    return default or Path(__file__).resolve().parent.parent


def env_file_candidates(repo_root: "Path | None" = None,
                        cwd: "Path | None" = None) -> list[Path]:
    """The `.env` files consulted, in precedence order. First wins per key.

    AN EXPLICIT `NEXUS_ENV_ROOT` SUPPRESSES THE CWD FALLBACK, and that is the
    whole of its usefulness. The cwd candidate exists for a deployment that
    did NOT say where its config lives; once something has said, consulting a
    second location would defeat the saying — and for the suite it would mean
    the operator's repo-root `.env` gets read anyway whenever pytest is run
    from the repo root, which is how everyone runs it.

    Without the override, unchanged: root first, cwd second, deduplicated.
    """
    if os.environ.get("NEXUS_ENV_ROOT", "").strip():
        return [env_root(repo_root) / ".env"]
    root_env = env_root(repo_root) / ".env"
    cwd_env = (cwd or Path.cwd()) / ".env"
    return [root_env] if cwd_env == root_env else [root_env, cwd_env]


def load_env_files(candidates: "list[Path] | None" = None) -> list[Path]:
    """Load each candidate that exists, non-overriding. Returns those read."""
    try:
        from dotenv import load_dotenv
    except ImportError:                                    # pragma: no cover
        # Declared rather than swallowed: the app runs fine on real
        # environment variables, and an operator who put their token in a
        # file needs to know why it is being ignored.
        logger.warning(
            "python-dotenv is not installed, so .env is not loaded. Set "
            "NEXUS_API_TOKEN and friends as real environment variables, or "
            "`pip install -r requirements.txt`.")
        return []
    read: list[Path] = []
    for path in (env_file_candidates() if candidates is None else candidates):
        if path.is_file():
            load_dotenv(path, override=False)
            read.append(path)
    return read


__all__ = ["env_file_candidates", "env_root", "load_env_files"]
