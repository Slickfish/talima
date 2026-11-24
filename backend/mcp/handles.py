"""MCP-2 — the retrieval handle table: in-process, keyed by `retrieval_id`,
with a declared timeout.

A `TurnHandle` (backend/loop/agentic_loop.py) is exactly the state that
crosses the model call. Under MCP the model call is the client's, so the
handle waits here between `nexus_retrieve` and `nexus_record`. A handle not
recorded within the timeout expires: that is a retrieval-without-record, a
coverage event the caller counts (never a lineage record), and the forecast
row the first half already wrote stays as it is.
"""
from __future__ import annotations

import os
import time

DEFAULT_HANDLE_TIMEOUT_SECONDS = 600.0
HANDLE_TIMEOUT_SETTING = "NEXUS_MCP_HANDLE_TIMEOUT"


def handle_timeout_seconds() -> float:
    """Read at call time (the E.2 pattern). Seconds; the default is ten
    minutes, a wall clock because under MCP "the next turn" is the client's
    notion and NEXUS sees only calls (MCP-1 decision 7)."""
    raw = (os.environ.get(HANDLE_TIMEOUT_SETTING) or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_HANDLE_TIMEOUT_SECONDS
    except ValueError:
        value = DEFAULT_HANDLE_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_HANDLE_TIMEOUT_SECONDS


class HandleTable:
    """The open retrievals of one process. Not thread-safe by design: one
    stdio process is one person and the SDK serialises tool calls."""

    def __init__(self) -> None:
        self._open: dict = {}

    def put(self, handle) -> str:
        self._open[handle.retrieval_id] = handle
        return handle.retrieval_id

    def pop(self, retrieval_id: str):
        return self._open.pop(retrieval_id, None)

    def __len__(self) -> int:
        return len(self._open)

    def open_ids(self) -> list[str]:
        return sorted(self._open)

    def expire(self, now: float | None = None, timeout: float | None = None) -> list:
        """Remove and return every handle older than the timeout, oldest
        first. The caller records each as a retrieval-without-record."""
        now = time.time() if now is None else now
        limit = handle_timeout_seconds() if timeout is None else timeout
        expired = sorted((h for h in self._open.values() if now - h.created_at > limit),
                         key=lambda h: h.created_at)
        for h in expired:
            self._open.pop(h.retrieval_id, None)
        return expired
