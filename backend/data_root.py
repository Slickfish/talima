"""The console's data root (W1): one directory for every file the console
writes.

`NEXUS_DATA_ROOT` is read at call time. Unset or empty, nothing changes:
`create_app()` builds `create_services()` with no paths, and every store
keeps its own CWD-relative default under `data/`, exactly as before W1.
Set to a directory, every console file lands under it:

    kb.db  archive.db  chroma/  snapshots/  turn_metrics.jsonl
    calibration_log.jsonl  citation_capture.jsonl  fire_capture.jsonl

The first five are `create_services`' path parameters, in MCP-2's layout
(`backend/mcp/mcp_server.py` builds the same five from its own root through
`paths_under`). The last three are the function writers
(`backend/eval/calibration_log.py`, `citation_capture.py`,
`fire_capture.py`). Each keeps its own variable, which wins whenever it is
present, so an operator's explicit path and the suite's redirection
(`tests/conftest.py`) are untouched.

`start.py --data-root D` sets this variable for the server it launches.
W1's establish measured the alternative (CWD plus `--app-dir`): it moved the
files to `D/data/`, moved the `.env` fallback with them, and needed a
`--reload-dir` under `--dev`.

Under MCP the stores and, since W2, the three logs follow
`NEXUS_MCP_DATA_ROOT` (`backend/mcp/config.py`): the runtime sets each
log's own variable to its root when the environment does not, so this
variable never reaches them there. Before W2 they followed this variable
when it was set and the working directory's `data/` when it was not.

A relative root is read against the working directory every time a file is
opened. The snapshot path sealed into each archive block is the path as
given, so a block sealed under a relative root re-reads only from the same
working directory, and one sealed under an absolute root only while the
directory stays where it is.
"""
from __future__ import annotations

import os
from pathlib import Path

DATA_ROOT_SETTING = "NEXUS_DATA_ROOT"


def data_root() -> "Path | None":
    """The configured root, or None when unset or empty (today's defaults)."""
    raw = os.environ.get(DATA_ROOT_SETTING, "").strip()
    return Path(raw) if raw else None


def paths_under(root: "Path | str") -> dict[str, Path]:
    """`create_services`' five path arguments for a store rooted at `root`."""
    root = Path(root)
    return {
        "kb_db_path": root / "kb.db",
        "chroma_dir": root / "chroma",
        "eval_log_path": root / "turn_metrics.jsonl",
        "archive_db_path": root / "archive.db",
        "snapshots_dir": root / "snapshots",
    }


def console_kwargs() -> dict[str, Path]:
    """What `create_app()` passes to `create_services` when it builds its own
    services: nothing when no root is set, so the call is today's exactly."""
    root = data_root()
    return {} if root is None else paths_under(root)


def default_file(own_setting: str, default: "Path | str") -> Path:
    """A function writer's file: its own variable whenever it is present
    (even empty, as before W1), else the data root's copy of the default's
    name, else the default itself."""
    if own_setting in os.environ:
        return Path(os.environ[own_setting])
    root = data_root()
    return root / Path(default).name if root is not None else Path(default)


__all__ = ["DATA_ROOT_SETTING", "console_kwargs", "data_root",
           "default_file", "paths_under"]
