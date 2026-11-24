"""An unreachable provider is an expected condition, not an exception.

THE DEFECT (U.3's class, one layer up)
    `POST /model/override` validates a raw model name against
    `provider.list_models()`, and that call was unguarded — a daemon that is
    simply not running surfaced as a raw httpx.ConnectError traceback and a
    500. U.3 was the same shape in a script: an empty token producing an
    httpx.LocalProtocolError instead of a named refusal. A provider being
    down is a state this system explicitly supports everywhere else
    (`/healthz` names it, `/model/status` degrades, the startup probe
    declares it), so the one endpoint that crashed on it was the anomaly.

THE SHAPE OF THE FIX
    A named 503 — {"error": "provider_unreachable", ...} with the backend
    and a hint — consistent with the named-refusal house style
    (scope_too_long, unknown_model). Role-name overrides never touched the
    provider and must keep working with the daemon down: pinning a ROLE is
    a statement about the roster, not about what is currently answering.

PROVEN WITH THE PROVIDER ACTUALLY UNAVAILABLE, NOT MOCKED TO RAISE
    The fixture builds the app with the REAL OllamaProvider pointed at a
    loopback port nothing listens on, so the failure is a genuine socket
    refusal travelling the genuine path. A mock that raises would prove the
    handler catches what the mock throws, not what httpx throws.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

TOKEN = "unreachable-token"
H = {"Authorization": f"Bearer {TOKEN}"}

# Loopback, port 9 (discard) — nothing listens there in any test
# environment, so a real connect fails immediately with ECONNREFUSED.
DEAD_URL = "http://127.0.0.1:9"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("OLLAMA_URL", DEAD_URL)
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    from backend.providers.ollama import OllamaProvider
    from backend.server import create_app, create_services
    # The REAL provider, explicitly — conftest injects OfflineTestProvider
    # when none is passed (R.4), and its list_models() returns [] instead of
    # failing, which would make every assertion here vacuous. Constructed
    # after OLLAMA_URL is set: BASE_URL resolves at instance time.
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap",
                          provider=OllamaProvider())
    # NON-VACUITY: really the live class, really aimed at the dead port.
    assert isinstance(svc.provider, OllamaProvider)
    assert svc.provider.BASE_URL == DEAD_URL
    return TestClient(create_app(svc))


def test_raw_name_override_returns_a_named_error_not_a_traceback(client):
    """THE SENTINEL. A raw model name needs the roster to validate against;
    with the daemon down the answer is a named 503, never a 500 with an
    httpx traceback."""
    r = client.post("/model/override",
                    json={"model": "qwen3.5:9b-q4_K_M", "lock": False},
                    headers=H)
    assert r.status_code == 503, (
        f"expected a named 503, got {r.status_code}: {r.text[:200]}")
    body = r.json()
    assert body["error"] == "provider_unreachable"
    assert body["backend"] == "ollama"
    assert "hint" in body
    assert "Traceback" not in r.text and "httpx" not in body["error"]


def test_a_role_override_still_works_with_the_daemon_down(client):
    """Pinning a canonical role consults the roster, not the daemon — it
    must keep working, or a dead provider locks the operator out of the
    very control they need while bringing it back."""
    r = client.post("/model/override",
                    json={"model": "general", "lock": True}, headers=H)
    assert r.status_code == 200
    assert r.json()["resolved_model"] == "qwen3.5:9b-q4_K_M"
    assert client.delete("/model/override", headers=H).status_code == 200


def test_the_named_error_is_declared_not_silent(client):
    """The refusal names the condition and the remedy — the operator asked
    for a message saying the provider is down, not a validation error that
    implies their model name was wrong."""
    r = client.post("/model/override",
                    json={"model": "some-model:7b", "lock": False}, headers=H)
    body = r.json()
    assert body["error"] == "provider_unreachable"
    assert "unknown_model" not in r.text, (
        "an unreachable backend must not be reported as a bad model name")
