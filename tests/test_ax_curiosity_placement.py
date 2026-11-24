"""AX.1 — curiosity runs on the idle cycle, and the turn path does not call it.

THE DEFECT. `find_curious_nodes` ran on every turn (agentic_loop's Stage
7-aux) while NEXUS_KNOWLEDGE_PACK.md §5 had recorded curiosity as a
*proactive (idle/reflective) pass*. The record was checked for a purpose
the pack did not anticipate and holds none: Phase 9 added the call as one
of four per-turn stages with no packet naming the turn as its host, its
only consumer is a side panel that appends cards, and the call sat inside
the unguarded token-to-done window `test_stream_truncation.py` recorded.

WHAT SHIPS. The KAIROS cycle runs the pass after decay and demotion and
before the persist, enqueues each question through `notify_ui`, and the
next /chat bridges it as `curiosity_surfaced` — the same frame and payload,
one turn later. The cycle result carries `curiosity_surfaced`,
`curiosity_top` and `curiosity_placement`; `None` on an aborted cycle.

`NEXUS_CURIOSITY_PLACEMENT=turn` is the rollback: the per-turn call and
frame exactly as before, and the idle host is a no-op.

THE GUARD is derived: no tracked file under `backend/loop/` may call
`find_curious_nodes`. Non-vacuity: the fixture graph is proven to surface
a question BEFORE any host is asked to, so "the idle cycle produced one"
cannot pass on a graph with nothing partial in it.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from backend.cortex.curiosity import CuriosityEngine
from backend.cortex.curiosity_placement import (
    curiosity_placement, idle_curiosity, turn_curiosity)
from backend.cortex.edges import SynapticEdge
from backend.loop.agentic_loop import BRIDGE_EVENTS, run_turn
from backend.loop.state import NexusLoopState
from backend.memory.hippocampus import Hippocampus
from tests._tracked import tracked_backend
from tests.test_restart_with_state import _services, _used_store

ROOT = Path(__file__).resolve().parent.parent
FRAME_KEYS = {"node_id", "score", "question", "epistemic_state", "in_degree",
              "evidential_weight", "confidence", "confidence_deprecated"}


class _Provider:
    def __init__(self):
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx):
        self.calls += 1
        yield "An answer. " if self.calls == 1 else "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _graph() -> Hippocampus:
    """A hub three nodes point at: in_degree 3, PARTIAL at 0.5 — the shape
    curiosity scores. Everything else has in_degree 0 and is ineligible."""
    h = Hippocampus()
    for s in ("a", "b", "c"):
        h.add_edge(SynapticEdge(source=s, target="hub", weight=0.5))
    return h


def _frames(hippo: Hippocampus, **kw) -> list[tuple[str, dict]]:
    out = []

    async def drive():
        async for frame, _s in run_turn(NexusLoopState(), "tell me about hub",
                                        hippocampus=hippo, provider=_Provider(),
                                        curiosity=CuriosityEngine(), **kw):
            name, _, rest = frame.partition("\n")
            data = rest.split("data: ", 1)[1].strip() if "data: " in rest else ""
            try:
                payload = json.loads(data)
            except ValueError:
                payload = {}
            out.append((name.replace("event: ", ""), payload))

    asyncio.run(drive())
    return out


# ---------------------------------------------------------------------------
# Non-vacuity FIRST
# ---------------------------------------------------------------------------

def test_the_fixture_graph_surfaces_a_question_at_all():
    nodes = CuriosityEngine().find_curious_nodes(_graph(), top_k=3)
    assert nodes and nodes[0].node_id == "hub", "fixture has nothing to surface"


def test_the_used_store_graph_surfaces_a_question_at_all(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXUS_CURIOSITY_PLACEMENT", raising=False)
    _used_store(tmp_path)
    services = _services(tmp_path)
    assert CuriosityEngine().find_curious_nodes(services.hippocampus), (
        "the rehydrated used-store graph surfaces nothing — the cycle test "
        "below would pass on an empty pass")


# ---------------------------------------------------------------------------
# The turn path
# ---------------------------------------------------------------------------

def test_a_turn_emits_no_curiosity_frame_by_default(monkeypatch):
    monkeypatch.delenv("NEXUS_CURIOSITY_PLACEMENT", raising=False)
    names = [n for n, _ in _frames(_graph())]
    assert "done" in names, "the turn did not complete"
    assert "curiosity_surfaced" not in names


def test_turn_placement_restores_the_per_turn_frame_exactly(monkeypatch):
    monkeypatch.setenv("NEXUS_CURIOSITY_PLACEMENT", "turn")
    frames = [(n, d) for n, d in _frames(_graph()) if n == "curiosity_surfaced"]
    assert frames, "the rollback emitted no frame"
    assert set(frames[0][1]) == FRAME_KEYS
    assert frames[0][1]["node_id"] == "hub" and frames[0][1]["in_degree"] == 3


def test_the_two_hosts_are_exclusive(monkeypatch):
    h = _graph()
    monkeypatch.delenv("NEXUS_CURIOSITY_PLACEMENT", raising=False)
    assert idle_curiosity(CuriosityEngine(), h) and not turn_curiosity(CuriosityEngine(), h)
    monkeypatch.setenv("NEXUS_CURIOSITY_PLACEMENT", "turn")
    assert turn_curiosity(CuriosityEngine(), h) and not idle_curiosity(CuriosityEngine(), h)
    assert turn_curiosity(None, h) == [] and idle_curiosity(CuriosityEngine(), None) == []


def test_placement_default_and_unknown_values():
    import os
    os.environ.pop("NEXUS_CURIOSITY_PLACEMENT", None)
    assert curiosity_placement() == "idle"
    os.environ["NEXUS_CURIOSITY_PLACEMENT"] = " TURN "
    assert curiosity_placement() == "turn"
    os.environ["NEXUS_CURIOSITY_PLACEMENT"] = "both"
    assert curiosity_placement() == "idle"
    os.environ.pop("NEXUS_CURIOSITY_PLACEMENT", None)


# ---------------------------------------------------------------------------
# The idle cycle — on `_used_store()`, reported on the cycle surface
# ---------------------------------------------------------------------------

def _cycle(tmp_path, monkeypatch, placement: str | None):
    if placement is None:
        monkeypatch.delenv("NEXUS_CURIOSITY_PLACEMENT", raising=False)
    else:
        monkeypatch.setenv("NEXUS_CURIOSITY_PLACEMENT", placement)
    _used_store(tmp_path)
    services = _services(tmp_path)
    captured: list[dict] = []

    async def capture(payload: dict):
        captured.append(payload)

    services.kairos.notify_ui = capture
    res = asyncio.run(services.kairos.run_one_cycle())
    return services, res, captured


def test_the_idle_cycle_surfaces_reports_and_enqueues(tmp_path, monkeypatch):
    services, res, captured = _cycle(tmp_path, monkeypatch, None)
    assert res.skipped is False, res.cycle_error
    assert res.curiosity_placement == "idle"
    assert res.curiosity_surfaced >= 1
    assert res.curiosity_top and len(res.curiosity_top) == res.curiosity_surfaced
    queued = [p for p in captured if p.get("event") == "curiosity_surfaced"]
    assert len(queued) == res.curiosity_surfaced
    assert {p["node_id"] for p in queued} == set(res.curiosity_top)
    assert all(set(p) - {"event"} == FRAME_KEYS for p in queued)
    # The status surface reports the pass (I-6).
    from fastapi.testclient import TestClient
    from backend.server import create_app
    with TestClient(create_app(services)) as c:
        last = c.get("/kairos/status").json()["last_cycle"]
    assert last["curiosity_surfaced"] == res.curiosity_surfaced
    assert last["curiosity_top"] == res.curiosity_top


def test_the_idle_host_is_a_noop_under_turn_placement(tmp_path, monkeypatch):
    _services_, res, captured = _cycle(tmp_path, monkeypatch, "turn")
    assert res.skipped is False, res.cycle_error
    assert res.curiosity_placement == "turn"
    assert res.curiosity_surfaced == 0 and res.curiosity_top == []
    assert not [p for p in captured if p.get("event") == "curiosity_surfaced"]


def test_an_aborted_cycle_reports_none_not_zero(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXUS_CURIOSITY_PLACEMENT", raising=False)
    _used_store(tmp_path)
    services = _services(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("synthetic abort")

    monkeypatch.setattr(services.kairos.neocortex, "consolidate_from_hippocampus", boom)
    res = asyncio.run(services.kairos.run_one_cycle())
    assert res.skipped is True and res.skipped_reason == "error"
    assert res.curiosity_surfaced is None
    assert res.curiosity_top == []


# ---------------------------------------------------------------------------
# The bridge — the queued question reaches the client on the next turn
# ---------------------------------------------------------------------------

def test_the_bridge_delivers_the_queued_question_on_the_next_turn(monkeypatch):
    monkeypatch.delenv("NEXUS_CURIOSITY_PLACEMENT", raising=False)
    assert "curiosity_surfaced" in BRIDGE_EVENTS
    cn = CuriosityEngine().find_curious_nodes(_graph(), top_k=1)[0]
    queued = [{"event": "curiosity_surfaced", **cn.to_dict()}]
    frames = _frames(Hippocampus(), pending_alerts=queued)
    hits = [d for n, d in frames if n == "curiosity_surfaced"]
    assert len(hits) == 1
    assert "event" not in hits[0] and hits[0]["node_id"] == "hub"
    assert set(hits[0]) == FRAME_KEYS
    assert queued == [], "the bridge did not drain the queue"


# ---------------------------------------------------------------------------
# The guard — derived over tracked backend files, AST
# ---------------------------------------------------------------------------

def _find_curious_calls() -> list[tuple[str, int]]:
    out = []
    for path in tracked_backend():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "find_curious_nodes":
                out.append((path.relative_to(ROOT).as_posix(), node.lineno))
    return out


def test_the_turn_path_does_not_call_curiosity():
    calls = _find_curious_calls()
    in_loop = [(f, ln) for f, ln in calls if f.startswith("backend/loop/")]
    assert not in_loop, f"the turn path calls curiosity at {in_loop}"


def test_the_guard_population_is_not_empty():
    files = {f for f, _ln in _find_curious_calls()}
    assert "backend/cortex/curiosity_placement.py" in files
    assert "backend/server.py" in files, "the on-demand GET no longer calls it"
