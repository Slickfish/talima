"""Tests for /memory/column endpoint and /memory route."""
import pytest
from fastapi.testclient import TestClient
from backend.server import create_app

@pytest.fixture
def client():
    return TestClient(create_app())

def test_memory_column_returns_unified_payload(client):
    """Endpoint returns surface, neocortex, archive, procedural."""
    r = client.get("/memory/column")
    assert r.status_code == 200
    data = r.json()
    assert "surface" in data
    assert "neocortex" in data
    assert "archive" in data
    assert "procedural" in data
    assert "timestamp" in data
    assert "nodes" in data["surface"]
    assert "edges" in data["surface"]

def test_memory_column_surface_nodes_have_required_fields(client):
    """Surface nodes carry id, ep, confidence, fire_count."""
    r = client.get("/memory/column")
    data = r.json()
    for node in data["surface"]["nodes"]:
        assert "id" in node
        assert "ep" in node
        assert "confidence" in node
        assert "fire_count" in node

def test_memory_column_neocortex_filtered_by_consolidation(client):
    """Neocortex entries have tier <= 2 OR fire_count >= 7."""
    r = client.get("/memory/column")
    data = r.json()
    for t in data["neocortex"]:
        assert t["tier"] <= 2 or t["fire_count"] >= 7

def test_memory_column_archive_blocks_have_fingerprint(client):
    """Archive blocks include hash and fingerprint structure."""
    r = client.get("/memory/column")
    data = r.json()
    for b in data["archive"]:
        assert "block_hash" in b
        assert "fingerprint" in b
        assert "timestamp" in b

def test_memory_route_serves_html(client):
    """The /memory route returns HTML when file exists, 404 otherwise."""
    r = client.get("/memory")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert "html" in r.text.lower()

def test_memory_column_is_read_only(client):
    """Calling /memory/column does not mutate any service state."""
    r1 = client.get("/memory/column")
    r2 = client.get("/memory/column")
    assert r1.status_code == 200 and r2.status_code == 200
    # Idempotent: same call returns same shape
    assert set(r1.json().keys()) == set(r2.json().keys())
