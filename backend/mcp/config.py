"""MCP-2 — configuration from the environment, every setting read at call
time and probed by the dotenv sentinel (tests/test_dotenv_settings_take_effect.py).

    NEXUS_MCP_OWNER            the accountable identity every MCP record
                               carries — asserted, never authenticated
                               (MCP-1 decision 9); unset = unattributed
    NEXUS_MCP_DATA_ROOT        the directory the store and its three logs
                               live in, an absolute path; unset, empty,
                               relative or an unexpanded `${...}`: the
                               per-user default (W2), never the working
                               directory (`data_root_choice`)
    NEXUS_MCP_HANDLE_TIMEOUT   seconds a retrieval may wait for its record
                               (backend/mcp/handles.py)

The learning default is MCP mode's own: `frozen` unless NEXUS_LEARNING is
set (`live` is the opt-in), applied by `apply_learning_default()` before
anything reads the switch — a bundle ships no `.env`, and B-17 is unfixed
(MCP-1 item 6; MCP-2 decision 3). The console's default is unchanged.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path

from backend.learning import FROZEN, LEARNING_SETTING, SINCE_SETTING

OWNER_SETTING = "NEXUS_MCP_OWNER"
DATA_ROOT_SETTING = "NEXUS_MCP_DATA_ROOT"
MCP_SESSION_ID = "mcp"
# W2 — a value a bundle host left unexpanded (`${HOME}/NEXUS`,
# `${user_config.owner}`): the reference substitution passes an untouched
# default and a blank optional field through as these literals, and before W2
# the owner was sealed as `${user_config.owner}` and the store made under
# `<cwd>/${HOME}/NEXUS`. Never a value; always said so.
PLACEHOLDER_MARK = "${"
# The three function writers' own variables; MCP points each at its own root
# when the environment does not (W2: they followed the console's
# NEXUS_DATA_ROOT, else the working directory's data/).
LOG_SETTINGS = {"NEXUS_CALIBRATION_LOG": "calibration_log.jsonl",
                "NEXUS_CITATION_CAPTURE_PATH": "citation_capture.jsonl",
                "NEXUS_FIRE_CAPTURE_PATH": "fire_capture.jsonl"}


def is_placeholder(value: "str | None") -> bool:
    return PLACEHOLDER_MARK in (value or "")


def owner() -> str | None:
    """The accountable identity, or None (unattributed): unset, empty, or a
    placeholder the host did not expand, which is never recorded."""
    raw = (os.environ.get(OWNER_SETTING) or "").strip()
    return None if not raw or is_placeholder(raw) else raw


def default_data_root() -> Path:
    """MCP mode's data folder when none is configured: a per-user
    application-data folder, never the working directory and never a
    documents folder (often synced). Windows: %LOCALAPPDATA%\\NEXUS;
    macOS: ~/Library/Application Support/NEXUS; elsewhere:
    $XDG_DATA_HOME/nexus or ~/.local/share/nexus."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "NEXUS"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "NEXUS"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "nexus"


def data_root_choice() -> tuple[Path, str]:
    """(the data root, why): the setting when it names an absolute folder;
    the per-user default when it is unset, empty, an unexpanded placeholder
    or relative (run/specs/W2.md D5), with the reason. A relative folder is
    read against the working directory, and in MCP mode that is the host's:
    the defect G4 named, in another spelling. `~` is not expanded, here or
    in the console."""
    raw = (os.environ.get(DATA_ROOT_SETTING) or "").strip()
    if not raw:
        return default_data_root(), f"{DATA_ROOT_SETTING} unset: the per-user default"
    if is_placeholder(raw):
        return default_data_root(), (f"{DATA_ROOT_SETTING}={raw!r} is an unexpanded placeholder, "
                                     f"not a folder: the per-user default")
    if not Path(raw).is_absolute():
        return default_data_root(), (f"{DATA_ROOT_SETTING}={raw!r} is relative, and MCP mode's working "
                                     f"directory is the host's: the per-user default")
    return Path(raw), DATA_ROOT_SETTING


def data_root() -> Path:
    return data_root_choice()[0]


def point_logs_at(root: Path) -> dict:
    """The three logs under MCP's own root, for each writer whose own
    variable the environment does not set (it wins when present, as it does
    for the console). Returns what was set."""
    applied = {}
    for setting, name in LOG_SETTINGS.items():
        if setting not in os.environ:
            os.environ[setting] = str(Path(root) / name)
            applied[setting] = os.environ[setting]
    return applied


def apply_learning_default(*, first_run_ts: float | None = None) -> dict:
    """MCP mode's learning default: frozen unless the environment says
    otherwise. Returns what was applied, so the caller can print it.

    `setdefault`, so an operator's NEXUS_LEARNING=live in the environment or
    `.env` (loaded before this runs) is the opt-in and wins. The from-date,
    when frozen and not asserted, is the MCP process's first run on this
    store — the coverage table's `first_run` — never an invented date."""
    applied = {"learning_setting": LEARNING_SETTING, "set_by": None, "since_set_by": None}
    if not (os.environ.get(LEARNING_SETTING) or "").strip():
        os.environ[LEARNING_SETTING] = FROZEN
        applied["set_by"] = "mcp_default"
    else:
        applied["set_by"] = "environment"
    if (os.environ.get(LEARNING_SETTING) or "").strip().lower() == FROZEN \
            and not (os.environ.get(SINCE_SETTING) or "").strip() and first_run_ts is not None:
        os.environ[SINCE_SETTING] = _dt.datetime.fromtimestamp(
            float(first_run_ts), tz=_dt.timezone.utc).date().isoformat()
        applied["since_set_by"] = "first_run"
    applied["value"] = os.environ.get(LEARNING_SETTING)
    applied["since"] = os.environ.get(SINCE_SETTING) or None
    return applied
