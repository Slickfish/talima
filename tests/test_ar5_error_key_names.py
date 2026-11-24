"""Packet AR.5 — one error, one key name.

`/outcome/confirm` refused an unknown actor role with `{"error":
"unknown_actor_role", "valid": [...]}`; `/kb/dedup/confirm` refused the
same error with `"allowed"`. Two names for the permitted-values list of
one error is how a third route invents a fourth.

THE CHOICE: `valid` — it came first (Z.1), `unknown_direction` already used
it, and no frontend reads either key (grep over tracked frontends, AR.5
establish 10). Pinned by AST over the router: a named-error literal may
carry its permitted values only under `valid`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.server import create_app, create_services
from tests._tracked import tracked

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "ar5-token"
H = {"Authorization": f"Bearer {TOKEN}"}

FORBIDDEN = {"allowed", "permitted", "options", "choices", "accepted_values"}


def _error_literals():
    """Every dict literal in backend/server.py carrying an `error` key, as
    {key names} with the error value where it is a constant."""
    out = []
    src = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        if "error" not in keys:
            continue
        idx = [i for i, k in enumerate(node.keys)
               if isinstance(k, ast.Constant) and k.value == "error"][0]
        val = node.values[idx]
        out.append((val.value if isinstance(val, ast.Constant) else None, keys))
    return out


def test_no_named_error_uses_a_second_name_for_its_permitted_values():
    offenders = [(err, sorted(keys & FORBIDDEN))
                 for err, keys in _error_literals() if keys & FORBIDDEN]
    assert not offenders, (
        f"permitted values must be under 'valid', not {offenders}")


def test_the_closed_vocabulary_refusals_all_carry_valid():
    """Non-vacuity for the guard above: the literals it protects exist, on
    more than one route, and every one names its vocabulary under `valid`."""
    vocab = [(err, keys) for err, keys in _error_literals()
             if err in ("unknown_actor_role", "unknown_actor_source",
                        "unknown_direction")]
    assert len(vocab) >= 5, f"expected the outcome and dedup sites, saw {vocab}"
    assert all("valid" in keys for _err, keys in vocab), vocab


def test_no_frontend_reads_the_old_key():
    """Establish 10, kept true: changing a key breaks a consumer, so the
    frontends must not have grown one."""
    for f in tracked("frontend/*.html"):
        assert ".allowed" not in f.read_text(encoding="utf-8"), f.name


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    return TestClient(create_app(svc))


def test_the_dedup_route_names_its_vocabulary_under_valid(client):
    r = client.post("/kb/dedup/confirm", json={
        "subject_a": "a", "relation_a": "r", "object_a": "b",
        "subject_b": "c", "relation_b": "r", "object_b": "d",
        "keep": "a", "confirmed": True, "actor_role": "robot"}, headers=H)
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "unknown_actor_role"
    assert sorted(body["valid"]) == ["person", "source_system"]
    assert "allowed" not in body
