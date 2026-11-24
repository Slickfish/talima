"""Packet AT.0 — no confirm gate defaults to consent.

THE DEFECT, found by enumerating every refusal path from the router (AT
establish 1): `SearchConfirmReq.confirmed` defaulted to `True` since Phase
10b, so `POST /search/confirm {"query": "x"}` GRANTED consent. A Law 3 gate
defeated by a default value — and three tests in the suite relied on it.

THE GUARD, derived from the module by AST rather than listed: every
`confirmed` field on a request model, and every `confirmed` parameter on a
route function, must either have no default or default to False. A new
model that defaults to True fails here by name.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.server import create_app, create_services

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "at0-token"
H = {"Authorization": f"Bearer {TOKEN}"}


def _confirmed_defaults() -> list[tuple[str, object]]:
    """(site, default) for every `confirmed` declaration in server.py."""
    tree = ast.parse((ROOT / "backend" / "server.py").read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if (isinstance(stmt, ast.AnnAssign)
                        and isinstance(stmt.target, ast.Name)
                        and stmt.target.id == "confirmed"):
                    default = (stmt.value.value if isinstance(stmt.value, ast.Constant)
                               else ("<expr>" if stmt.value is not None else None))
                    out.append((f"{node.name}.confirmed", default))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            positional = args.args + args.kwonlyargs
            defaults = ([None] * (len(args.args) - len(args.defaults))
                        + list(args.defaults) + list(args.kw_defaults))
            for arg, d in zip(positional, defaults):
                if arg.arg == "confirmed":
                    default = (d.value if isinstance(d, ast.Constant)
                               else ("<expr>" if d is not None else None))
                    out.append((f"{node.name}(confirmed)", default))
    return out


def test_no_confirmed_field_or_parameter_defaults_to_true():
    sites = _confirmed_defaults()
    assert len(sites) >= 9, f"the derivation found too few gates: {sites}"
    offenders = [s for s, d in sites if d is True or d == "<expr>"]
    assert not offenders, f"a Law 3 gate defaults to consent: {offenders}"


def test_the_search_confirm_model_has_no_default_at_all():
    """The specific site, pinned: the body models are explicit booleans
    with no default (the shape every other gate documents)."""
    assert ("SearchConfirmReq.confirmed", None) in _confirmed_defaults()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    return TestClient(create_app(svc)), svc


def test_omitting_confirmed_grants_nothing(client):
    """THE SENTINEL. A body without `confirmed` is a schema 422 and records
    no consent — before AT.0 it returned 200 consent_granted."""
    c, svc = client
    r = c.post("/search/confirm", json={"query": "latest release"}, headers=H)
    assert r.status_code == 422, r.text
    assert "detail" in r.json() and "error" not in r.json()
    assert svc.search_consents == {}
    assert c.get("/search/pending").json()["pending"] == []


def test_explicit_consent_and_revocation_still_work(client):
    c, svc = client
    assert c.post("/search/confirm", json={"query": "q", "confirmed": True},
                  headers=H).json()["status"] == "consent_granted"
    assert svc.search_consents == {"q": True}
    assert c.post("/search/confirm", json={"query": "q", "confirmed": False},
                  headers=H).json()["status"] == "consent_revoked"
    assert svc.search_consents == {}
