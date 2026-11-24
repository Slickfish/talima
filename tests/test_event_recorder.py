"""Tests for the event recorder + /events/recent endpoint (Phase 2A)."""
import pytest
from fastapi.testclient import TestClient

from backend.server import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


def test_events_recent_empty_initially(client):
    r = client.get("/events/recent")
    assert r.status_code == 200
    data = r.json()
    assert "events" in data
    assert "now" in data
    assert "buffer_size" in data


def test_events_recent_filters_by_since(client):
    r1 = client.get("/events/recent")
    now1 = r1.json()["now"]
    r2 = client.get(f"/events/recent?since={now1 + 100}")
    # Asking for events from the far future should return empty.
    assert r2.json()["events"] == []


def test_event_recorder_records_loop_events(client):
    """When the loop emits events, they appear in /events/recent."""
    from backend.loop.agentic_loop import sse_event

    sse_event("test_event", {"foo": "bar"})
    r = client.get("/events/recent")
    events = r.json()["events"]
    test_events = [e for e in events if e["event"] == "test_event"]
    assert len(test_events) >= 1
    assert test_events[-1]["data"]["foo"] == "bar"
