"""Packet AR.4 — a role that cannot answer is refused by name.

THE DEFECT, measured while re-deriving the integration reference:
`POST /model/override` accepted every one of the seven roles, including
`embed`. Pinning it made the embedding model the answering model; a raw
`nomic-embed-text` passed the installed-list check the same way.

DERIVED, NOT LISTED (the packet's standing question). Whether a model can
answer is read from the REGISTRY's capability declarations — the single
source of truth for what a model can do — not from a second hand-kept
list of roles. The one fact that is stated rather than derived is which
capabilities are NOT answering capabilities (`NON_ANSWERING` in
capabilities.py: FAST_EXTRACTION, EMBEDDING), and that fact is itself
checked against the kernel by AST: they are exactly the capabilities the
kernel reaches through a dedicated non-chat accessor rather than through
`route()`, and `infer_capability` never returns one.

Consequence stated on purpose: `extraction` resolves to a profile that
declares GENERAL_REASONING, so it remains pinnable — a small chat model
answering is a choice, not a category error. `embed` resolves to a profile
declaring EMBEDDING only, and is refused.

SCALE: a role set would not survive a tenth model; a derivation from the
registry does — a new embed-only profile is refused the moment it is
registered, and a new answering role needs no list edit. Pinned by the
synthetic-registry test below.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.providers.model_map import ROLES, answering_roles, resolve
from backend.router.capabilities import NON_ANSWERING, Capability
from backend.router.model_registry import (
    ModelProfile, ModelRegistry, default_registry)
from backend.server import create_app, create_services

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "ar4-token"
H = {"Authorization": f"Bearer {TOKEN}"}


class _Provider:
    """Lists the embedder as installed, so the raw-name path is exercised."""
    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "ok"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return ["nomic-embed-text:latest",
                                         "qwen2.5-coder:7b-q4_K_M"]
    async def health(self): return True


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap",
                          provider=_Provider())
    return TestClient(create_app(svc))


# ---------------------------------------------------------------------------
# The derivation
# ---------------------------------------------------------------------------

def test_embed_is_not_an_answering_role_and_the_rest_are():
    roles = answering_roles(default_registry)
    assert "embed" not in roles
    assert roles == ROLES - {"embed"}, roles


def test_non_answering_is_exactly_what_the_kernel_reaches_off_route():
    """The stated fact, checked against the code it describes. The kernel
    has one chat entry (`route`, fed by `infer_capability`) and dedicated
    accessors for the rest; the capabilities named only in those accessors
    are the non-answering ones, and `infer_capability` returns none of
    them."""
    src = (ROOT / "backend" / "router" / "model_kernel.py").read_text(encoding="utf-8")
    cls = next(n for n in ast.parse(src).body
               if isinstance(n, ast.ClassDef) and n.name == "ModelKernel")

    def caps_in(fn) -> set[str]:
        return {n.attr for n in ast.walk(fn)
                if isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name) and n.value.id == "Capability"}

    fns = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    chat = caps_in(fns["route"]) | caps_in(fns["infer_capability"])
    off_route = set()
    for name, fn in fns.items():
        if name not in ("route", "infer_capability", "__init__"):
            off_route |= caps_in(fn)
    assert {c.name for c in NON_ANSWERING} == off_route - chat, (
        f"NON_ANSWERING {sorted(c.name for c in NON_ANSWERING)} != kernel's "
        f"off-route capabilities {sorted(off_route - chat)}")
    assert not ({c.name for c in NON_ANSWERING} & caps_in(fns["infer_capability"]))


def test_the_derivation_follows_the_registry_not_the_role_name():
    """SCALE. Re-register `general`'s tag as an embed-only profile: the role
    is refused with no list edited. Register an answering profile under a
    tag a role resolves to: accepted, likewise."""
    reg = ModelRegistry()
    reg.register(ModelProfile(
        name=resolve("general", backend="ollama"), display_name="x",
        capabilities=(Capability.EMBEDDING,), vram_gb=1, tokens_per_sec=1,
        context_window=1, pull_size_gb=1))
    reg.register(ModelProfile(
        name=resolve("embed", backend="ollama"), display_name="y",
        capabilities=(Capability.GENERAL_REASONING,), vram_gb=1,
        tokens_per_sec=1, context_window=1, pull_size_gb=1))
    roles = answering_roles(reg, backend="ollama")
    assert "general" not in roles
    assert "embed" in roles, "the registry declared it can answer; the name is not the fact"


def test_a_role_the_registry_cannot_vouch_for_falls_back_to_its_capability():
    """No profile for the resolved name (the lmstudio table has none in the
    default registry): the only fact left is which capability the role is
    the image of, so the roles for NON_ANSWERING capabilities are refused
    and the rest accepted. Declared as a fallback, not hidden as a rule."""
    roles = answering_roles(ModelRegistry(), backend="lmstudio")
    assert "embed" not in roles and "extraction" not in roles
    assert {"primary", "code", "multimodal"} <= roles


# ---------------------------------------------------------------------------
# The route, both directions
# ---------------------------------------------------------------------------

def test_overriding_to_embed_is_refused_by_name(client):
    r = client.post("/model/override", json={"model": "embed"}, headers=H)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"] == "model_cannot_answer"
    assert body["model"] == "embed"
    assert body["resolved_model"] == resolve("embed")
    assert "embedding" in body["capabilities"]
    assert "embed" not in body["valid"] and "general" in body["valid"]
    assert client.get("/model/status").json()["override"] is None


@pytest.mark.parametrize("role", sorted(ROLES - {"embed"}))
def test_every_answering_role_still_pins(client, role):
    r = client.post("/model/override", json={"model": role, "lock": False},
                    headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["resolved_model"] == resolve(role)
    client.delete("/model/override", headers=H)


def test_a_raw_embedder_name_is_refused_the_same_way(client):
    """The raw-name path passes `matches_available`; the registry knows the
    tag declares EMBEDDING only, and the refusal is the same named one."""
    r = client.post("/model/override", json={"model": "nomic-embed-text"},
                    headers=H)
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "model_cannot_answer"
    r = client.post("/model/override", json={"model": "qwen2.5-coder:7b"},
                    headers=H)
    assert r.status_code == 200, r.text
