"""Package A — bearer-token auth on state-changing endpoints.

conftest.py gives every TestClient a valid default Authorization header;
these tests strip or override it to exercise the 401 paths directly.
"""
import pytest
from fastapi.testclient import TestClient

from backend.server import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


def test_post_without_token_is_401(client):
    client.headers.pop("Authorization", None)
    r = client.post("/model/override", json={"model": "primary", "lock": False})
    assert r.status_code == 401


def test_post_with_wrong_token_is_401(client):
    r = client.post(
        "/model/override",
        json={"model": "primary", "lock": False},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401


def test_post_with_valid_token_succeeds(client):
    # conftest default header carries the valid token. Not-401 is the
    # contract; the endpoint's own semantics decide 2xx/4xx.
    r = client.post("/model/override", json={"model": "primary", "lock": False})
    assert r.status_code != 401
    assert r.status_code == 200


def test_get_endpoints_remain_open(client):
    client.headers.pop("Authorization", None)
    r = client.get("/kb/stats")
    assert r.status_code == 200


def test_default_bind_is_localhost(monkeypatch):
    monkeypatch.delenv("NEXUS_HOST", raising=False)
    monkeypatch.delenv("NEXUS_PORT", raising=False)
    import start
    host, port = start.resolve_bind()
    assert host == "127.0.0.1"
    assert port == 8000
