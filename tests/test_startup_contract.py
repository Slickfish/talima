"""Packet R.1 — what startup actually guarantees, pinned before R.2 changes it.

WHY THIS FILE EXISTS
====================
`server.py:415` blocks the FastAPI lifespan on an HTTP GET to the local Ollama
daemon, inside `try/except Exception: pass`. R.0 measured what that costs: 56
refused connections across the suite, ~2.45s each on the operator's Windows
box (~2ms here), and the app is *designed* to start without the result.

R.2 will stop awaiting it. Before that, this file records what the startup
contract IS — and the finding from R.0.2 is that the contract is much weaker
than "the probe has run". It is:

    Nothing reads `is_pulled` before the first turn, and a turn routes
    correctly when nothing is flagged at all.

That is the whole of it. Asserting a stronger contract here would be
asserting something the code never promised, and the deferral in R.2 is safe
precisely because the weak contract is the true one.

WHAT R.0.3 FOUND, AND WHY IT MAKES DEFERRAL CHEAP
    All 56 probes fail in this sandbox and 944 tests pass. The degraded path
    is not merely *handled* — it is the ONLY startup this suite has ever run.
    Deferring the probe therefore puts the app into a state it already
    occupies under test, rather than into a new one. `test_phase_9.py:904`
    already pins the no-fallback routing branch; what was missing was any
    statement that STARTUP tolerates it, which is what this file adds.

THE RATCHETS
    Two facts about the probe's HTTP call are frozen here — its path and its
    timeout — not because either is right, but so that changing them is a
    deliberate act with a diff rather than a silent one. R.3 changes the path
    (`/api/ps` lists RUNNING models, not installed ones — the probe cannot
    flag anything on a freshly-booted box) and R.2 changes the timeout. Both
    ratchets fail in either direction, so the edit has to be made on purpose.

    They are measured by intercepting the client, not by reading the source.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.server import create_app, create_services
from tests._tracked import tracked_backend

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

TOKEN = "packet-r-token"


# ---------------------------------------------------------------------------
# Providers. Each one is a different way for the probe to come up empty.
# ---------------------------------------------------------------------------

class _CountingProvider:
    """Reachable, answers instantly, and counts. The non-vacuity instrument."""

    def __init__(self, models: list[str] | None = None):
        self.models = models or []
        self.list_models_calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "ok"

    async def get_embedding(self, text): return [0.0] * 768
    async def health(self): return True

    async def list_models(self):
        self.list_models_calls += 1
        return list(self.models)


class _UnreachableProvider(_CountingProvider):
    """No daemon. This is what every test in this suite actually runs against
    once the real provider is swapped out — and what the operator's box looks
    like whenever Ollama is not up."""

    async def list_models(self):
        self.list_models_calls += 1
        raise ConnectionRefusedError("nothing is listening on 11434")


def _services(tmp_path):
    return create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots",
    )


def _app(tmp_path, provider):
    services = _services(tmp_path)
    services.provider = provider
    return create_app(services), services


# ---------------------------------------------------------------------------
# NON-VACUITY FIRST — prove the fixture reaches the probe path at all
# ---------------------------------------------------------------------------

def test_the_lifespan_actually_probes_the_provider(tmp_path, monkeypatch):
    """Everything below asserts something about the probe. If entering the
    TestClient never reached it, they would all pass on a startup that never
    happened — the vacuous-fixture failure mode this packet series keeps
    finding. Measured on the injected provider, not inferred from source.
    """
    monkeypatch.setenv("NEXUS_STARTUP_PROBE", "sync")
    provider = _CountingProvider()
    app, _ = _app(tmp_path, provider)

    assert provider.list_models_calls == 0, "probed before startup"
    with TestClient(app):
        pass
    assert provider.list_models_calls >= 1, (
        "entering the TestClient did not reach the probe; every assertion in "
        "this file would be vacuous")


def test_startup_probes_once_per_lifespan(tmp_path, monkeypatch):
    """One probe per app start. R.0.4 measured 116 lifespan entries across the
    suite of which 56 reach a real socket; if this number ever becomes 2, the
    Windows cost doubles for a reason nobody chose."""
    monkeypatch.setenv("NEXUS_STARTUP_PROBE", "sync")
    provider = _CountingProvider()
    app, _ = _app(tmp_path, provider)
    with TestClient(app):
        pass
    assert provider.list_models_calls == 1, (
        f"startup probed {provider.list_models_calls} times, expected 1")


# ---------------------------------------------------------------------------
# The contract as it is: nothing depends on is_pulled before the first turn
# ---------------------------------------------------------------------------

def _is_pulled_readers() -> set[tuple[str, str]]:
    """(module, enclosing function) for every read of `is_pulled` in backend/.

    AST, not a text search — the fifth packet running where a grep-based guard
    fired on its own documentation. `getattr(x, "is_pulled", ...)` counts as a
    read; a docstring mentioning the field does not.
    """
    out: set[tuple[str, str]] = set()
    for path in tracked_backend():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scopes: list[tuple[ast.AST, str]] = []
        rel = path.relative_to(ROOT).as_posix()

        class V(ast.NodeVisitor):
            def _fn(self, node):
                scopes.append((node, node.name))
                self.generic_visit(node)
                scopes.pop()

            visit_FunctionDef = _fn
            visit_AsyncFunctionDef = _fn

            def _hit(self):
                out.add((rel, scopes[-1][1] if scopes else "<module>"))

            def visit_Attribute(self, node):
                if node.attr == "is_pulled" and isinstance(node.ctx, ast.Load):
                    self._hit()
                self.generic_visit(node)

            def visit_Call(self, node):
                if (isinstance(node.func, ast.Name) and node.func.id == "getattr"
                        and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and node.args[1].value == "is_pulled"):
                    self._hit()
                self.generic_visit(node)

        V().visit(tree)
    return out


# Frozen. Fails if a reader is added AND if one is removed — R.2 is safe
# because this set is this short, so the set is the safety argument and it
# must not drift silently.
EXPECTED_IS_PULLED_READERS = frozenset({
    # the writer's own idempotence check — reads to decide whether to skip
    ("backend/router/model_registry.py", "sync_pulled_state"),
    # THE ONE PRODUCTION CONSUMER, and it runs on a chat turn, not at startup.
    # MCP-2 moved it from `run_turn` into `prepare_turn`, the turn's first half,
    # which the console's `run_turn` composes — still a turn. It also runs on
    # MCP's `nexus_retrieve`, a turn too; the MCP process never probes, so
    # every profile reads not-pulled there, which is the daemon-is-down state
    # this deferral was argued safe against.
    ("backend/loop/agentic_loop.py", "prepare_turn"),
})


def test_is_pulled_has_exactly_one_production_consumer():
    """R.0.2's finding, pinned. If a second consumer appears — especially one
    on a startup or request path that assumes the flags are populated — the
    weak contract this file rests on stops being true and R.2's deferral stops
    being safe. That is a decision, so it fails here first.
    """
    found = _is_pulled_readers()
    assert found == set(EXPECTED_IS_PULLED_READERS), (
        "the set of is_pulled readers changed.\n"
        f"  added:   {sorted(found - set(EXPECTED_IS_PULLED_READERS))}\n"
        f"  removed: {sorted(set(EXPECTED_IS_PULLED_READERS) - found)}\n"
        "Deferring the startup probe (R.2) is safe only while the single "
        "consumer runs on a turn rather than at startup. Re-argue it.")


def test_a_turn_routes_with_nothing_flagged_as_pulled(tmp_path, monkeypatch):
    """THE CONTRACT, behaviourally, over HTTP.

    The probe returned nothing, so no profile is flagged. A turn must still
    route. This is the property that makes deferral a no-op from the client's
    point of view: the worst the race can do is reproduce this state for a
    few milliseconds, and this state works.
    """
    # monkeypatch, not os.environ — a token left set for the rest of the
    # process is how a suite develops an order dependency.
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    provider = _UnreachableProvider()
    app, services = _app(tmp_path, provider)

    with TestClient(app) as client:
        # precondition: the probe flagged nothing
        assert not any(p.is_pulled for p in services.kernel.registry.all()), (
            "a profile was flagged despite the probe failing")
        r = client.post("/chat", json={"message": "what is the QT interval?"},
                        headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200, r.text
        body = r.text

    events = [l[7:] for l in body.split("\n") if l.startswith("event: ")]
    assert "model_routed" in events, (
        f"no model was routed with nothing flagged as pulled; saw {events}")
    assert "done" in events, "the turn did not complete"

    # And the declared consequence of an unflagged profile, so that this test
    # notices if the degraded path stops being the one exercised.
    assert "model_pull_proposal" in events, (
        "the unflagged-profile branch was not taken — this test is no longer "
        "exercising the degraded startup it claims to")


# ---------------------------------------------------------------------------
# Degraded startup — the property that makes deferral safe
# ---------------------------------------------------------------------------

def test_the_app_starts_and_serves_with_no_provider_reachable(tmp_path,
                                                              monkeypatch):
    """Must pass today AND after R.2. If it ever fails, the deferral argument
    collapses, because the deferred state IS this state."""
    monkeypatch.setenv("NEXUS_STARTUP_PROBE", "sync")
    provider = _UnreachableProvider()
    app, _ = _app(tmp_path, provider)

    with TestClient(app) as client:
        assert provider.list_models_calls >= 1, "the failing probe never ran"
        r = client.get("/healthz")
        assert r.status_code == 200, r.text
        payload = r.json()
    assert "ollama" in payload, f"/healthz lost its liveness field: {payload}"


def test_a_failing_probe_leaves_every_profile_unflagged(tmp_path):
    """`try/except Exception: pass` at server.py:414 and a second swallow at
    model_registry.py:135. Neither raises; both leave the registry untouched.
    Pinned so a future 'improvement' cannot start half-flagging on failure."""
    provider = _UnreachableProvider()
    app, services = _app(tmp_path, provider)
    with TestClient(app):
        pass
    flagged = [p.name for p in services.kernel.registry.all() if p.is_pulled]
    assert flagged == [], f"a failed probe flagged {flagged}"


def test_a_successful_probe_flags_the_matching_profile(tmp_path, monkeypatch):
    """The other half — the probe still WORKS. Without this, every assertion
    above is satisfiable by a probe that does nothing at all, and R.2 could
    'succeed' by breaking it."""
    monkeypatch.setenv("NEXUS_STARTUP_PROBE", "sync")
    provider = _CountingProvider(models=["nomic-embed-text:latest"])
    app, services = _app(tmp_path, provider)
    with TestClient(app):
        pass
    flagged = [p.name for p in services.kernel.registry.all() if p.is_pulled]
    assert "nomic-embed-text" in flagged, (
        f"a successful probe flagged nothing useful: {flagged}")


# ---------------------------------------------------------------------------
# Where the probe sits in startup — today, and the ratchets R.2/R.3 move
# ---------------------------------------------------------------------------

def test_the_probe_completes_before_startup_finishes(tmp_path, monkeypatch):
    """REDUCIBILITY, half two. Written at R.1 against the pre-change default;
    R.2 added the one line that names the mode and changed nothing else about
    the assertion, which is what "provably reducible to today's behaviour"
    means. Deterministic — a count at a moment, not a duration.
    """
    monkeypatch.setenv("NEXUS_STARTUP_PROBE", "sync")
    provider = _CountingProvider()
    app, _ = _app(tmp_path, provider)
    with TestClient(app):
        at_startup_exit = provider.list_models_calls
    assert at_startup_exit == 1, (
        f"the probe had not completed when startup finished "
        f"({at_startup_exit} calls); today the lifespan awaits it")


class _RecordingAsyncClient:
    """Enough httpx.AsyncClient to capture what the probe asks for."""

    calls: list[dict] = []

    def __init__(self, *a, **kw):
        self._timeout = kw.get("timeout")

    async def __aenter__(self): return self

    async def __aexit__(self, *a): return False

    async def get(self, url, **kw):
        type(self).calls.append({"method": "GET", "url": url,
                                 "timeout": self._timeout})
        raise ConnectionRefusedError("recorded, not sent")


def _record_probe_request(monkeypatch) -> dict:
    import backend.providers.ollama as ollama_mod
    _RecordingAsyncClient.calls = []
    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", _RecordingAsyncClient)
    provider = ollama_mod.OllamaProvider()
    import asyncio
    try:
        asyncio.run(provider.list_models())
    except ConnectionRefusedError:
        pass
    assert _RecordingAsyncClient.calls, "the probe issued no request"
    return _RecordingAsyncClient.calls[0]


def test_the_probe_targets_the_endpoint_it_targets_today(monkeypatch):
    """RATCHET. Moved by R.3 — the one-line diff this was pinned for.

    `/api/ps` lists models currently LOADED IN VRAM; `/api/tags` lists models
    INSTALLED. `sync_pulled_state`'s docstring described `ollama list` (i.e.
    `/api/tags`) against an implementation that called `/api/ps`, and
    `health()` in the same class already used `/api/tags` correctly. On a
    freshly-booted box nothing is loaded, so the probe blocked startup to
    fetch an empty list and flagged nothing.
    """
    call = _record_probe_request(monkeypatch)
    assert call["url"].endswith("/api/tags"), (
        f"the probe endpoint moved to {call['url']!r} without moving this "
        f"ratchet")


def test_the_probe_timeout_is_the_one_it_is_today(monkeypatch):
    """RATCHET, moved by R.2. A flat 5.0s covered connect and read with one
    number; the Windows cost R.0 measured is spent entirely in connect."""
    import httpx

    call = _record_probe_request(monkeypatch)
    timeout = call["timeout"]
    assert isinstance(timeout, httpx.Timeout), (
        f"the probe went back to a flat timeout: {timeout!r}")
    assert timeout.connect == 0.25, f"connect budget is now {timeout.connect!r}"
    assert timeout.read == 5.0, (
        f"the READ budget changed to {timeout.read!r} — R.2 shortened connect "
        f"only; a daemon paging a 9GB model into VRAM still needs seconds")


def test_the_connect_budget_is_env_tunable_and_reduces_to_the_old_flat_value(
        monkeypatch):
    """REDUCIBILITY, half one. `NEXUS_PROBE_CONNECT_TIMEOUT=5.0` restores the
    pre-R.2 timeout exactly, so the change has a rollback that is a config
    value rather than a revert."""
    monkeypatch.setenv("NEXUS_PROBE_CONNECT_TIMEOUT", "5.0")
    timeout = _record_probe_request(monkeypatch)["timeout"]
    assert (timeout.connect, timeout.read) == (5.0, 5.0), (
        f"the env override did not restore the flat timeout: {timeout!r}")


def test_an_unparseable_connect_budget_falls_back_rather_than_raising(
        monkeypatch):
    """A malformed tuning knob must not stop the app from starting."""
    monkeypatch.setenv("NEXUS_PROBE_CONNECT_TIMEOUT", "soon")
    assert _record_probe_request(monkeypatch)["timeout"].connect == 0.25


# ---------------------------------------------------------------------------
# The change R.2 makes, asserted before it is made
# ---------------------------------------------------------------------------

def _lifespan_awaits_the_probe_directly() -> bool:
    """True iff the lifespan body contains `await ... sync_pulled_state(...)`.

    AST with real line numbers, because P.4's first draft of this same shape
    found a function DEFINITION where it wanted a call site.
    """
    tree = ast.parse((BACKEND / "server.py").read_text(encoding="utf-8"))
    lifespans = [n for n in ast.walk(tree)
                 if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"]
    assert lifespans, "no lifespan function found in server.py"
    for fn in lifespans:
        for node in ast.walk(fn):
            if not isinstance(node, ast.Await):
                continue
            call = node.value
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "sync_pulled_state"):
                return True
    return False


def test_startup_does_not_block_on_the_probe():
    """The change, made. Was `xfail(strict)` at R.1.

    Structural rather than timed: N.0.8 established that wall-clock thresholds
    are flaky and get muted, and "did startup await this call" is a fact about
    the code that is true or false with no clock involved. `sync` mode reaches
    the probe through `_run_startup_probe`, so the lifespan never names it
    directly in any mode.
    """
    assert not _lifespan_awaits_the_probe_directly(), (
        "the lifespan still awaits sync_pulled_state directly")


def test_healthz_still_carries_the_degradation_surface():
    """I-6: a probe that quietly never runs is worse than one that costs 5s.

    R.2 must declare the outcome somewhere an operator reads. `/healthz`
    already carries `embedding_degraded` from P1/F-10 and is the obvious home,
    so this pins that the home still exists. It carries no probe field today —
    tolerable only while the probe is synchronous and its failure has exactly
    one visible consequence, which R.2 ends.
    """
    src = (BACKEND / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "healthz")
    keys = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            keys |= {k.value for k in node.keys
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    assert "embedding_degraded" in keys, (
        "/healthz lost the degradation surface P1/F-10 added; R.2's probe "
        "state has nowhere declared to live")
