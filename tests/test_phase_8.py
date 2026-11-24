"""Phase 8 tests — required coverage from CLAUDE.md:

  - graph snapshot endpoint returns valid JSON
  - SSE events parse client-side (server emits well-formed event/data frames)

Plus arch §15 invariants:
  - frontend HTML loads d3 from CDN (single file, no build step)
  - all SSE event types listed in arch §15.2 are emitted by the loop / server
  - /state/summary returns the right-panel inspector payload
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.cortex.edges import SynapticEdge
from backend.cortex.nodes import EpistemicState, NodeType
from backend.loop.agentic_loop import sse_event
from backend.memory.hippocampus import Hippocampus


@pytest.fixture
def app_and_client(tmp_path):
    """Per-test app via create_app() — each invocation builds a fresh
    NexusServices container so module-level state cannot leak (Fix 4)."""
    from backend.server import create_app, create_services
    services = create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "metrics.jsonl",
        snapshots_dir=tmp_path / "snapshots",      # W11c: never the repository's data/snapshots
    )
    app = create_app(services)
    with TestClient(app) as c:
        yield app, c


@pytest.fixture
def client(app_and_client):
    return app_and_client[1]


@pytest.fixture
def services(app_and_client):
    return app_and_client[0].state.services


# =============== /graph/snapshot ===============

def test_snapshot_returns_valid_json_with_required_keys(client):
    r = client.get("/graph/snapshot")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict)
    assert set(data.keys()) >= {"nodes", "edges", "metadata"}
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)
    assert isinstance(data["metadata"], dict)


def test_snapshot_metadata_includes_runtime_fields(client):
    r = client.get("/graph/snapshot")
    md = r.json()["metadata"]
    assert "n_nodes" in md and md["n_nodes"] == 0
    assert "n_edges" in md and md["n_edges"] == 0
    assert "active_model" in md
    assert "turn_count" in md
    assert "prediction_error" in md


def test_snapshot_reflects_added_nodes_and_edges(client, services):
    services.hippocampus.upsert_node(
        "python", epistemic_state=EpistemicState.KNOWN,
        confidence=0.9, node_type=NodeType.SENSORY,
    )
    services.hippocampus.upsert_node(
        "language", epistemic_state=EpistemicState.PARTIAL,
    )
    services.hippocampus.add_edge(SynapticEdge(
        source="python", target="language", weight=0.8, fire_count=3,
    ))
    snap = client.get("/graph/snapshot").json()
    ids = {n["id"] for n in snap["nodes"]}
    assert {"python", "language"} <= ids
    py = next(n for n in snap["nodes"] if n["id"] == "python")
    assert py["epistemic_state"] == "known"
    assert py["confidence"] == 0.9
    assert py["node_type"] == "sensory"
    [edge] = snap["edges"]
    assert edge["source"] == "python" and edge["target"] == "language"
    assert edge["weight"] == 0.8 and edge["fire_count"] == 3
    assert "current_weight" in edge
    assert edge["activation_type"] == "excitatory"


def test_snapshot_node_payload_is_d3_force_compatible(client, services):
    """d3.forceLink expects edges with `source` and `target` — the snapshot
    must use those exact field names."""
    services.hippocampus.upsert_node("a")
    services.hippocampus.upsert_node("b")
    services.hippocampus.add_edge(SynapticEdge(source="a", target="b", weight=0.5))
    snap = client.get("/graph/snapshot").json()
    edge = snap["edges"][0]
    assert "source" in edge and "target" in edge
    # d3 also expects an `id` on each node
    for n in snap["nodes"]:
        assert "id" in n


def test_create_app_isolation_between_calls(tmp_path):
    """Two sequential create_app() calls must yield independent state —
    turn_count starts at 0 in both even after the first one is mutated."""
    from backend.server import create_app, create_services
    s1 = create_services(kb_db_path=tmp_path / "a.db", chroma_dir=tmp_path / "ca", snapshots_dir=tmp_path / "sa")
    s2 = create_services(kb_db_path=tmp_path / "b.db", chroma_dir=tmp_path / "cb", snapshots_dir=tmp_path / "sb")
    a1 = create_app(s1)
    a2 = create_app(s2)
    assert a1.state.services is not a2.state.services
    assert a1.state.services.state.turn_count == 0
    assert a2.state.services.state.turn_count == 0
    # Mutating one must not affect the other
    from backend.loop.state import next_state
    s1.state = next_state(s1.state, turn_count=99)
    assert a1.state.services.state.turn_count == 99
    assert a2.state.services.state.turn_count == 0


def test_state_summary_endpoint_returns_inspector_payload(client):
    r = client.get("/state/summary")
    assert r.status_code == 200
    data = r.json()
    expected = {
        "turn_count", "token_count", "active_model", "compaction_state",
        "transition_reason", "n_messages", "n_graph_context",
        "n_session_memory", "n_predicted_nodes", "prediction_error",
    }
    assert expected <= set(data.keys())


# =============== Frontend HTML — single file, no build, d3 from CDN ===============

FRONTEND = Path("frontend/nexus_ui.html")


def test_frontend_html_exists():
    assert FRONTEND.exists() and FRONTEND.stat().st_size > 0


def test_frontend_loads_d3_vendored():
    # P1/F-07 inverted the original CDN expectation: d3 v7 is now vendored
    # locally so the UI loads air-gapped (sovereignty requirement).
    body = FRONTEND.read_text(encoding="utf-8")
    assert "/vendor/d3.v7.min.js" in body, "vendored d3 v7 script not found"
    assert "d3js.org" not in body, "external d3 CDN reference reintroduced"


def test_frontend_panels_present():
    body = FRONTEND.read_text(encoding="utf-8").lower()
    # Three named panels (left/right/centre) exist
    for marker in ('id="left"', 'id="centre"', 'id="right"'):
        assert marker in body, f"missing panel marker {marker}"


def test_frontend_handles_required_sse_events():
    """The client-side switch must dispatch every SSE event the loop emits."""
    body = FRONTEND.read_text(encoding="utf-8")
    required_events = {
        "token", "turn_start", "model_swap_starting", "triples_injected",
        "graph_activating", "prediction_generated", "prediction_error",
        # H.3b — the loop emitted `hebbian` while CLAUDE.md declared
        # `hebbian_update`. The emit was renamed to the declared name,
        # so both the handler and the emit-site assertions move with it.
        "compaction_fired", "hebbian_update", "eval_metric", "done",
    }
    for ev in required_events:
        # Look for `case 'EVENT':` in the JS switch
        assert f"case '{ev}'" in body, f"frontend missing handler for SSE event {ev!r}"


def test_frontend_serves_via_root_route(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "NEXUS" in r.text


# =============== SSE format is parseable ===============

# Same regex the Phase 1 test uses — the contract has not changed.
SSE_RE = re.compile(r"^event: (?P<event>[^\n]+)\ndata: (?P<data>.*)\n\n$",
                    re.DOTALL)


def test_sse_event_format_round_trips_json():
    payload = {"id": "alpha", "act": 0.42, "list": [1, 2, 3]}
    frame = sse_event("graph_activating", payload)
    m = SSE_RE.match(frame)
    assert m is not None
    assert m.group("event") == "graph_activating"
    assert json.loads(m.group("data")) == payload


def test_loop_emits_all_required_sse_event_types_in_source():
    """Source-grep the agentic loop to confirm every arch §15.2 event name
    has a corresponding sse_event(...) call."""
    src = Path("backend/loop/agentic_loop.py").read_text(encoding="utf-8")
    required = {
        "token", "turn_start", "model_swap_starting", "triples_injected",
        "graph_activating", "prediction_generated", "prediction_error",
        # H.3b — the loop emitted `hebbian` while CLAUDE.md declared
        # `hebbian_update`. The emit was renamed to the declared name,
        # so both the handler and the emit-site assertions move with it.
        "compaction_fired", "hebbian_update", "eval_metric", "done",
    }
    for ev in required:
        assert f'sse_event("{ev}"' in src, f"loop never emits {ev!r}"


# =============== Compaction tier emission ===============

def test_compaction_tier_names_emitted_match_compaction_module():
    """The frontend reads tier names from the SSE payload; ensure they match
    what the compaction module produces."""
    from backend.loop.compaction import Tier
    body = FRONTEND.read_text(encoding="utf-8")
    # The frontend doesn't hard-code tier names (it just renders data.tier),
    # but make sure the enum values are stable strings.
    for t in Tier:
        assert isinstance(t.value, str) and t.value
