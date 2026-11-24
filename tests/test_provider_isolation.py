"""Packet R.4 — one provider per app, and nothing reaches around it.

FIX 0 SAID IT ALREADY
    "NexusServices.provider per-app instance for test isolation." Two layers
    opted out:

        backend/vector/chroma.py   module-global PROVIDER, at :93 and :150
        backend/server.py /healthz is_reachable() -> OllamaProvider().health()

    Both are now given the app's own provider.

THE CORRECTION THIS FILE CARRIES
    R.0 reported that fixing `chroma.py` was "worth ~201s of the operator's
    360s" — the reasoning being that 31 tests injected a stub provider and the
    vector store bypassed it. **Measured at R.4, that was wrong.** Threading
    the provider through removed exactly ONE of the 82 embedding calls. The
    other 81 came from tests that inject no provider at all: there was no stub
    being bypassed, only a live default nobody had chosen.

    The bypass was real and is fixed here on its own merits — a per-app seam
    that one layer ignores is not a seam. But the seconds were somewhere else,
    and `tests/conftest.py` is where they were taken: `create_services()` now
    defaults to an offline provider under test, which is possible only because
    the seam exists and is honoured everywhere.

    Measured, one suite run: 145 requests to localhost:11434 -> 2. The two
    that remain are `test_phase_1`'s deliberate live-daemon probes, which skip
    when nothing is listening, and they should remain.

WHY THAT IS NOT THE TEST-ONLY BYPASS PACKET R REJECTED
    R.2 refused a fix that "makes the suite fast by making it test something
    else". Nothing here changes what the code under test computes: a refused
    `get_embedding` raises, `chroma.py` catches it and calls `_offline_embed`,
    and the offline provider returns `_offline_embed`. Same vector, same
    ranking, same assertions. `test_the_offline_provider_computes_what_the_
    live_failure_path_computes` is that claim, asserted rather than promised.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.server import create_app, create_services
from backend.vector.chroma import VectorStore
from backend.vector.embeddings import FULL_DIM, _offline_embed
from tests._tracked import tracked_backend

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"


class _RecordingProvider:
    """Counts what it is asked for. Never opens anything."""

    def __init__(self):
        self.embeddings: list[str] = []
        self.health_calls = 0
        self.list_calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "ok"

    async def get_embedding(self, text: str) -> list[float]:
        self.embeddings.append(text)
        return _offline_embed(text, dim=FULL_DIM)

    async def list_models(self) -> list[str]:
        self.list_calls += 1
        return []

    async def health(self) -> bool:
        self.health_calls += 1
        return True                      # distinguishable from the default


def _services(tmp_path, provider=None):
    return create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots",
        provider=provider,
    )


def _used_store(services) -> None:
    """A store with something in it, so KAIROS's dedup scan has work to do.

    Non-vacuity for every assertion about the scan below: on an empty store
    `get_relevant()` returns nothing and the scan makes zero embedding calls,
    which would satisfy "no live calls" without proving anything at all.
    """
    for s, r, o in [
        ("QT interval", "is_a_segment_of", "ECG"),
        ("QTc", "stands_for", "corrected QT"),
        ("ganymede", "orbits", "jupiter"),
    ]:
        services.store.add(subject=s, relation=r, object=o, weight=0.9,
                           tier=1, granularity=Granularity.TOPIC,
                           source="wikidata")


# ---------------------------------------------------------------------------
# NON-VACUITY — the offline default is actually in force
# ---------------------------------------------------------------------------

def test_the_suite_default_provider_is_the_offline_one(tmp_path):
    """conftest guards `create_services`. If that guard were removed, every
    test below would still pass while the suite quietly resumed opening
    sockets — so the guard itself is asserted, not assumed."""
    from tests.conftest import OfflineTestProvider

    services = _services(tmp_path)
    assert isinstance(services.provider, OfflineTestProvider), (
        f"create_services() defaulted to {type(services.provider).__name__}; "
        f"the conftest guard is not in force")


def test_an_explicit_provider_still_wins(tmp_path):
    """The escape hatch is one keyword wide and must stay open — a test that
    needs a real daemon says so."""
    mine = _RecordingProvider()
    assert _services(tmp_path, mine).provider is mine


# ---------------------------------------------------------------------------
# The seam: every collaborator holds the app's provider
# ---------------------------------------------------------------------------

def test_the_vector_store_gets_the_apps_provider_at_construction(tmp_path):
    services = _services(tmp_path, _RecordingProvider())
    assert services.vector.provider is services.provider, (
        "VectorStore is holding a different provider than the app")


def test_assigning_services_provider_rebinds_the_vector_store(tmp_path):
    """THE PATTERN THIRTY TEST FILES ALREADY WROTE.

        services = create_services(...)
        services.provider = _StubProvider()

    Before R.4 that second line was half true: the vector store had already
    been built and kept what it was given. Construction-time injection alone
    would not have fixed it, because this is the line that exists.
    """
    services = _services(tmp_path)
    replacement = _RecordingProvider()
    services.provider = replacement
    assert services.vector.provider is replacement, (
        "assigning services.provider left the vector store on the old one")


def test_the_vector_store_uses_it(tmp_path):
    """Behavioural, not structural — holding a reference is not using it."""
    provider = _RecordingProvider()
    services = _services(tmp_path, provider)
    asyncio.run(services.vector.add("c1", "the QT interval is on the ECG"))
    assert provider.embeddings, "add() did not go through the app's provider"


def test_healthz_asks_the_apps_provider(tmp_path):
    """`/healthz` called `is_reachable()` -> `OllamaProvider().health()`, so
    every status check in a test opened a real socket no matter what had been
    injected. Asserted through the endpoint, and on the VALUE, so a handler
    that called the provider and then ignored it would fail."""
    provider = _RecordingProvider()          # health() -> True
    app = create_app(_services(tmp_path, provider))
    with TestClient(app) as client:
        payload = client.get("/healthz").json()
    assert provider.health_calls >= 1, "/healthz did not ask the app's provider"
    assert payload["ollama"] is True, (
        f"/healthz reported {payload['ollama']!r} while the app's provider "
        f"said True — it is still asking something else")


def test_the_dedup_scan_goes_through_the_apps_provider(tmp_path):
    """END TO END, and the 82 calls' actual path:
    KAIROS cycle -> deduplicator.scan -> vector.query -> provider.
    """
    provider = _RecordingProvider()
    services = _services(tmp_path, provider)
    _used_store(services)

    result = asyncio.run(services.kairos.run_one_cycle())
    assert not result.skipped, f"the cycle did not run: {result}"
    assert provider.embeddings, (
        "the dedup scan embedded nothing through the app's provider — either "
        "the fixture is empty or the scan is still reaching around it")


# ---------------------------------------------------------------------------
# The class-level guard: no daemon traffic at all
# ---------------------------------------------------------------------------

def test_a_fully_exercised_app_opens_nothing_outbound(tmp_path, monkeypatch):
    """Start, serve, and run a governance cycle — and issue zero requests to
    anything but the in-process test transport.

    This is the assertion that covers the class rather than the two instances
    fixed above: a third layer that reaches around `services.provider` fails
    here even though nobody thought to write a test for it.
    """
    outbound: list[str] = []
    orig = httpx.AsyncClient.send

    async def recording_send(self, request, **kw):
        if request.url.host not in {"testserver", ""}:
            outbound.append(str(request.url))
        return await orig(self, request, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "send", recording_send)

    provider = _RecordingProvider()
    services = _services(tmp_path, provider)
    _used_store(services)
    app = create_app(services)

    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/kb/stats").status_code == 200
        asyncio.run(services.kairos.run_one_cycle())

    assert outbound == [], (
        f"the app reached a network host despite an injected provider: "
        f"{outbound}")
    # Non-vacuity: the run has to have DONE something, or "no traffic" is
    # trivially true.
    assert provider.embeddings and provider.health_calls >= 1, (
        "nothing exercised the provider; this test proves nothing")


# ---------------------------------------------------------------------------
# The correction, asserted
# ---------------------------------------------------------------------------

def test_the_offline_provider_computes_what_the_live_failure_path_computes():
    """WHY conftest's default is not a test-only bypass.

    `chroma.py` catches a failing `get_embedding` and calls `_offline_embed`.
    The offline provider returns `_offline_embed`. If these ever diverge, the
    suite would start asserting against vectors production never produces, and
    the conftest guard would have become the thing R.2 refused to ship.
    """
    from tests.conftest import OfflineTestProvider

    text = "the QT interval is a segment of the ECG"
    got = asyncio.run(OfflineTestProvider().get_embedding(text))
    assert got == _offline_embed(text, dim=FULL_DIM)
    assert len(got) == FULL_DIM


def test_the_offline_provider_reports_a_down_daemon_faithfully():
    """`list_models() -> []` and `health() -> False` are exactly what a refused
    daemon yields today. A provider that claimed otherwise would change which
    branch the routing and status paths take."""
    from tests.conftest import OfflineTestProvider

    p = OfflineTestProvider()
    assert asyncio.run(p.list_models()) == []
    assert asyncio.run(p.health()) is False


# ---------------------------------------------------------------------------
# Ratchet: who still reaches for the module global
# ---------------------------------------------------------------------------

# The two spellings of the module-global default: `PROVIDER`, and (W3)
# `default_provider`, the same default resolved on its first call so that an
# unknown LLM_BACKEND is a startup refusal rather than an import-time
# traceback. One reach, two names; the ratchet counts both.
MODULE_GLOBAL_PROVIDER_NAMES = frozenset({"PROVIDER", "default_provider"})


def _module_global_provider_importers() -> set[str]:
    """Modules importing `PROVIDER` (or W3's `default_provider`) from
    `backend.providers`.

    AST, not a text search — six packets running. A docstring naming PROVIDER
    is not an import of it, and this file's own prose says the word repeatedly.
    """
    out = set()
    for path in tracked_backend():
        if path.parts[-2] == "providers":
            continue                      # the definition and its package
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom)
                    and (node.module or "").startswith("backend.providers")
                    and any(a.name in MODULE_GLOBAL_PROVIDER_NAMES for a in node.names)):
                out.add(path.relative_to(ROOT).as_posix())
    return out


# Frozen both ways. Each entry is a DEFAULT, reached only when no provider was
# supplied, and `create_services()` always supplies one — so these are fallback
# paths rather than live bypasses. A new entry is a new way for a layer to
# opt out of Fix 0's per-app seam, and has to be argued for.
EXPECTED_PROVIDER_GLOBAL_IMPORTERS = frozenset({
    # `VectorStore(provider=None)` default. R.4 made it a default rather than
    # the only option.
    "backend/vector/chroma.py",
    # `run_turn(provider=None)` default for legacy callers; server.py has
    # always passed services.provider explicitly.
    "backend/loop/agentic_loop.py",
})


def test_no_new_layer_reaches_for_the_module_global_provider():
    found = _module_global_provider_importers()
    assert found == set(EXPECTED_PROVIDER_GLOBAL_IMPORTERS), (
        "the set of modules importing the module-global PROVIDER changed.\n"
        f"  added:   {sorted(found - set(EXPECTED_PROVIDER_GLOBAL_IMPORTERS))}\n"
        f"  removed: {sorted(set(EXPECTED_PROVIDER_GLOBAL_IMPORTERS) - found)}\n"
        "Fix 0's per-app provider is the seam that makes the suite offline "
        "and the tests isolated; a new importer is a new hole in it.")


@pytest.mark.parametrize("module", sorted(EXPECTED_PROVIDER_GLOBAL_IMPORTERS))
def test_each_module_global_use_is_only_a_default(module):
    """The ratchet above allows two importers. This asserts WHY they are
    allowed: each reaches the global only through an `x or PROVIDER` fallback,
    never as the sole path. A direct `PROVIDER.something(...)` call is the
    thing R.4 removed from chroma.py and must not come back."""
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))

    def _is_global(value) -> bool:
        # `PROVIDER.x` and, since W3, `default_provider().x`: the same direct
        # reach in the lazy spelling.
        if isinstance(value, ast.Name):
            return value.id == "PROVIDER"
        return (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id == "default_provider")

    direct = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and _is_global(node.value)
    ]
    assert direct == [], (
        f"{module} calls the module-global provider directly at lines "
        f"{direct}; it should fall back to it, not reach for it")
