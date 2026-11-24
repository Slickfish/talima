"""Packet R.3 — the probe asks which models are INSTALLED, not which are hot.

THE DEFECT
    `LLMProvider.list_models()` documented itself as "loaded / available", and
    the slash hid a genuine disagreement between the two implementations:

        LMStudioProvider  GET /v1/models   -> installed models
        OllamaProvider    GET /api/ps      -> models resident in VRAM

    Every caller wanted the first. `sync_pulled_state` sets `is_pulled`, and
    its own docstring described `ollama list`. `/model/override` validates a
    user-supplied model name against the result. `health()`, two methods below
    `list_models` in the same class, already called `/api/tags`.

    The consequence on a freshly-booted box: nothing is resident, `/api/ps`
    returns `{"models": []}`, `sync_pulled_state` hits its `if not available:
    return []` and flags NOTHING while every model sits installed on disk.
    R.0 blocked startup for five seconds on this call; what it fetched was an
    empty list, reliably, at exactly the moment it mattered.

WHAT THIS FILE PINS
    That the probe reads installed models, that the shipped roster is
    recognised through its quantisation suffixes, and — the part that is a
    behaviour change rather than a fix — that `/model/override` did not become
    stricter as a side effect of the probe becoming truthful.

WHY THE OVERRIDE VALIDATOR HAD TO MOVE WITH IT
    `if available and req.model not in available` was an exact-match check
    guarded by a condition that was almost always false. With `/api/ps` the
    list was empty on a booted box, the guard short-circuited, and every raw
    model name was accepted. With `/api/tags` the list is real, the guard
    stops short-circuiting, and an exact match refuses `qwen2.5-coder:7b` on a
    box whose installed tag is `qwen2.5-coder:7b-q4_K_M` — the roster NEXUS
    ships with. The registry has solved this since Fix 4 with base-family
    matching; the endpoint fix is what made the two rules' disagreement
    reachable, so R.3 gives them one rule (`matches_available`).

    This is a forced consequence, not a choice. Reported as such.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.providers.model_map import ROLES
from backend.router.model_registry import ModelRegistry, matches_available
from backend.server import create_app, create_services

TOKEN = "packet-r3-token"


# The operator's actual box, from CLAUDE.md's roster. Tags carry the
# quantisation suffix; the registry entries mostly do not. That mismatch is
# the whole reason a matching rule exists.
INSTALLED_ON_THE_OPERATORS_BOX = [
    "deepseek-r1:14b-qwen-distill-q4_K_M",
    "qwen2.5-coder:7b-q4_K_M",
    "phi4-mini:3.8b-q4_K_M",
    "nomic-embed-text:latest",
]


class _TagsProvider:
    """A backend that is up with models installed and NONE resident — the
    state a box is in for the first minutes after boot, and the state in which
    the old probe returned nothing."""

    def __init__(self, installed=None):
        self.installed = list(
            INSTALLED_ON_THE_OPERATORS_BOX if installed is None else installed)
        self.list_models_calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "ok"

    async def get_embedding(self, text): return [0.0] * 768
    async def health(self): return True

    async def list_models(self):
        self.list_models_calls += 1
        return list(self.installed)


def _services(tmp_path):
    return create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots",
    )


# ---------------------------------------------------------------------------
# NON-VACUITY — the roster fixture must actually differ from the registry
# ---------------------------------------------------------------------------

def test_the_installed_tags_are_not_the_registry_names():
    """If the fixture's tags matched the registry names exactly, every test
    below would pass under a naive `in` check and prove nothing about the
    matching rule — the vacuous-fixture failure mode.
    """
    registry_names = {p.name for p in _fresh_roster()}
    exact = registry_names & set(INSTALLED_ON_THE_OPERATORS_BOX)
    assert len(exact) < len(INSTALLED_ON_THE_OPERATORS_BOX), (
        f"every installed tag matches a registry name exactly ({exact}); this "
        f"fixture cannot exercise base-family matching")


# ---------------------------------------------------------------------------
# The probe now sees what is installed
# ---------------------------------------------------------------------------

def test_the_shipped_roster_is_recognised_through_its_quantisation_suffixes():
    """The behaviour the old endpoint could not deliver: a box with models on
    disk and nothing hot flags its profiles."""
    reg = ModelRegistry()
    for profile in _fresh_roster():
        reg.register(profile)

    flipped = asyncio.run(reg.sync_pulled_state(_TagsProvider()))

    for expected in ("deepseek-r1:14b-qwen-distill-q4_K_M",
                     "qwen2.5-coder:7b", "phi4-mini:3.8b-q4_K_M",
                     "nomic-embed-text"):
        assert expected in flipped, (
            f"{expected} is installed on the box and was not flagged; "
            f"flagged {flipped}")


def test_a_model_that_is_not_installed_is_not_flagged():
    """The other direction. Without this, flagging everything would pass."""
    reg = ModelRegistry()
    for profile in _fresh_roster():
        reg.register(profile)
    flipped = asyncio.run(reg.sync_pulled_state(_TagsProvider()))
    assert "qwen3.5:9b-q4_K_M" not in flipped, (
        "the multimodal model is not on the box and was flagged anyway")


def test_the_sibling_tag_caveat_is_still_true_and_still_harmless():
    """DECLARED, not fixed. Base-family matching accepts a sibling tag:
    `qwen3:8b` matches an installed `qwen3.5:9b` because "qwen3" is a
    substring. With `/api/ps` this almost never fired because the list was
    almost always empty; with `/api/tags` it fires whenever qwen3.5 is
    installed.

    It stays because the only profile it touches is `manual_only=True` and so
    never auto-routed — asserted here rather than assumed, because if that
    ever changes the caveat stops being harmless.
    """
    reg = ModelRegistry()
    for profile in _fresh_roster():
        reg.register(profile)
    flipped = asyncio.run(reg.sync_pulled_state(
        _TagsProvider(["qwen3.5:9b-q4_K_M"])))
    assert "qwen3:8b" in flipped, (
        "the caveat no longer reproduces — if the rule was tightened, say so "
        "here rather than leaving a docstring describing behaviour that ended")
    assert reg.by_name("qwen3:8b").manual_only is True, (
        "qwen3:8b became auto-routable while a sibling tag can still flag it "
        "as installed — the false positive is now reachable by the router")


def _fresh_roster():
    """A clean copy of the shipped profiles. The module-level
    `default_registry` is process-global and other tests flip is_pulled on it;
    starting from a copy keeps this file order-independent."""
    import dataclasses

    from backend.router.model_registry import default_registry
    return [dataclasses.replace(p, is_pulled=False)
            for p in default_registry.all(include_deprecated=True)]


# ---------------------------------------------------------------------------
# The forced consequence: /model/override must not have become stricter
# ---------------------------------------------------------------------------

def _client(tmp_path, monkeypatch, provider):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    services = _services(tmp_path)
    services.provider = provider
    return TestClient(create_app(services))


def test_override_accepts_an_installed_model_named_without_its_suffix(
        tmp_path, monkeypatch):
    """THE REGRESSION R.3 WOULD OTHERWISE HAVE SHIPPED.

    Before R.3 this returned 200 for the wrong reason — `available` was empty
    so the check was skipped. After R.3 `available` is the real roster, and an
    exact-match check would 422 a model the operator has installed, because
    they typed the registry's name rather than the tag's suffix.
    """
    with _client(tmp_path, monkeypatch, _TagsProvider()) as client:
        r = client.post("/model/override",
                        json={"model": "qwen2.5-coder:7b", "lock": False},
                        headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200, (
        f"an installed model was refused: {r.status_code} {r.text}")
    assert r.json()["override_active"] is True


def test_override_still_refuses_a_model_that_is_not_installed(
        tmp_path, monkeypatch):
    """The check has to still be a check. If R.3's relaxation went too far,
    this is where it shows — the endpoint would accept anything and the 422
    branch would be dead."""
    with _client(tmp_path, monkeypatch, _TagsProvider()) as client:
        r = client.post("/model/override",
                        json={"model": "llama4:70b-instruct", "lock": False},
                        headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 422, (
        f"a model nobody has installed was accepted: {r.status_code} {r.text}")
    assert r.json()["error"] == "unknown_model"


def test_override_still_accepts_a_canonical_role(tmp_path, monkeypatch):
    """Roles never go near the installed list. Pinned because R.3 edited the
    branch immediately below the role check."""
    role = sorted(ROLES)[0]
    with _client(tmp_path, monkeypatch, _TagsProvider()) as client:
        r = client.post("/model/override", json={"model": role, "lock": False},
                        headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200, f"role {role!r} refused: {r.text}"


def test_override_still_accepts_anything_when_the_backend_is_down(
        tmp_path, monkeypatch):
    """`if available and ...` — an unreachable backend must not lock the
    operator out of their own override. Unchanged by R.3, and worth pinning
    because R.3 is what made the guard's other branch reachable."""

    class _Down(_TagsProvider):
        async def list_models(self):
            self.list_models_calls += 1
            return []

    with _client(tmp_path, monkeypatch, _Down()) as client:
        r = client.post("/model/override",
                        json={"model": "anything:at-all", "lock": False},
                        headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200, (
        f"an unreachable backend now blocks overrides: {r.text}")


# ---------------------------------------------------------------------------
# The defect R.3 made reachable: a process-global registry that gets written to
# ---------------------------------------------------------------------------

def test_one_apps_probe_does_not_flag_models_for_another_app(
        tmp_path, monkeypatch):
    """FOUND BY R.1's OWN SENTINELS FAILING, which is what they are for.

    `create_services()` handed every app the process-global
    `default_registry`, and `sync_pulled_state()` writes to whatever registry
    it is given. While the probe read `/api/ps` it got [] on every box under
    test, so it never wrote and the sharing was invisible. The endpoint fix
    made it write — and R.1's `test_a_failing_probe_leaves_every_profile_
    unflagged` immediately failed, because THIS file had started an app with
    an installed roster earlier in the session and the flags outlived it.

    In production one process runs one app, so this was benign there. It was
    never benign under pytest, where a global written by any test leaks into
    every test that follows.
    """
    from fastapi.testclient import TestClient

    from backend.router.model_registry import default_registry

    # sync: this test asserts the first app's probe COMPLETED, which is what
    # sync means. Under R.2's background default that would be a race.
    monkeypatch.setenv("NEXUS_STARTUP_PROBE", "sync")
    first = _services(tmp_path / "a")
    first.provider = _TagsProvider()
    with TestClient(create_app(first)):
        pass
    assert any(p.is_pulled for p in first.kernel.registry.all()), (
        "the first app flagged nothing — this test cannot detect a leak")

    # The global itself must be untouched...
    assert not any(p.is_pulled for p in default_registry.all()), (
        "startup wrote is_pulled onto the process-global default_registry")

    # ...and so must a second app built afterwards.
    second = _services(tmp_path / "b")
    second.provider = _TagsProvider(installed=[])
    with TestClient(create_app(second)):
        pass
    leaked = [p.name for p in second.kernel.registry.all() if p.is_pulled]
    assert leaked == [], (
        f"a second app inherited another app's pull state: {leaked}")


# ---------------------------------------------------------------------------
# One rule, one place
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,available,expected", [
    ("qwen2.5-coder:7b", ["qwen2.5-coder:7b-q4_K_M"], True),
    ("nomic-embed-text", ["nomic-embed-text:latest"], True),
    ("deepseek-r1:14b-qwen-distill-q4_K_M",
     ["deepseek-r1:14b-qwen-distill-q4_K_M"], True),
    ("llama4:70b", ["qwen2.5-coder:7b-q4_K_M"], False),
    ("qwen2.5-coder:7b", [], False),
    ("", ["anything"], False),
])
def test_the_matching_rule(name, available, expected):
    assert matches_available(name, available) is expected


def test_the_router_and_the_override_endpoint_share_the_rule():
    """R.3's actual structural change. Two copies of a matching rule is how
    they came to disagree; asserting the import keeps it one copy."""
    import ast

    src = Path(__file__).resolve().parent.parent / "backend" / "server.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imported = any(
        isinstance(n, ast.ImportFrom)
        and n.module == "backend.router.model_registry"
        and any(a.name == "matches_available" for a in n.names)
        for n in ast.walk(tree))
    assert imported, (
        "server.py no longer imports the shared matching rule — if the "
        "override validator grew its own again, that is the defect R.3 fixed")
