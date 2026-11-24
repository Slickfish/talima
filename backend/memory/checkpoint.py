"""LangGraph checkpoint round-trip for NexusLoopState.

Phase 5 test: "checkpoint resumes session". A session is identified by a
thread_id; the loop state is pickled into a single channel value so frozen
dataclasses + tuples survive byte-for-byte. The underlying storage is
LangGraph's InMemorySaver — the same checkpointer wired into a real
StateGraph in later phases — so the round-trip travels the production path.
"""
from __future__ import annotations

import pickle
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from backend.loop.state import NexusLoopState


class _Channel(TypedDict):
    nexus_blob: bytes


def _persist(state: _Channel) -> _Channel:
    return state


def _build_app() -> tuple[InMemorySaver, "object"]:
    sg = StateGraph(_Channel)
    sg.add_node("persist", _persist)
    sg.add_edge(START, "persist")
    sg.add_edge("persist", END)
    saver = InMemorySaver()
    return saver, sg.compile(checkpointer=saver)


class NexusCheckpointer:
    """LangGraph-backed checkpointer for NexusLoopState.

    Public API is intentionally tiny — put / get / list_threads — so callers
    don't depend on LangGraph's internals. Replace InMemorySaver with
    langgraph-checkpoint-sqlite for cross-process persistence.
    """

    def __init__(self):
        self._saver, self._app = _build_app()

    @staticmethod
    def _config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def put(self, thread_id: str, state: NexusLoopState) -> None:
        blob = pickle.dumps(state)
        self._app.invoke({"nexus_blob": blob}, config=self._config(thread_id))

    def get(self, thread_id: str) -> NexusLoopState | None:
        snap = self._app.get_state(self._config(thread_id))
        if not snap or not snap.values:
            return None
        blob = snap.values.get("nexus_blob")
        return pickle.loads(blob) if blob else None

    def list_threads(self) -> list[str]:
        seen = set()
        for ckpt in self._saver.list(None):
            tid = (ckpt.config or {}).get("configurable", {}).get("thread_id")
            if tid:
                seen.add(tid)
        return sorted(seen)
