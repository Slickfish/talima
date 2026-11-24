"""Packet R.2 — the startup probe is scheduled, not awaited.

WHAT CHANGED
    `server.py`'s lifespan awaited `sync_pulled_state()` — one HTTP GET to the
    local daemon, inside `try/except Exception: pass`. It now runs in one of
    three modes, `NEXUS_STARTUP_PROBE` ∈ {sync, background, off}, defaulting
    to `background`, with the task cancelled explicitly on shutdown.

WHY THIS FILE IS NOT TIMED
    N.0.8 established that wall-clock thresholds are flaky and get muted, and
    a muted sentinel is worse than none. Every assertion here is a count, a
    state string, or a task's `done()`/`cancelled()` — facts with no clock in
    them. The seconds are reported in the packet, not asserted in the suite.

    "Background" is made deterministic with a gate: the provider's
    `list_models()` waits on an `asyncio.Event` the test controls. Without it,
    whether the scheduled task got a slice before startup returned would
    depend on how many awaits the ASGI portal happened to perform — true most
    of the time, which is the worst kind of test.

WHAT THIS FILE CANNOT ASSERT
    That a background probe wins the race against a first turn. It cannot,
    and that is the point: `tests/test_startup_contract.py` asserts instead
    that LOSING the race is harmless, because the registry then looks exactly
    as it does when the daemon is down, and that is the only startup this
    suite has ever run.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.server import (
    DEFAULT_STARTUP_PROBE,
    STARTUP_PROBE_MODES,
    create_app,
    create_services,
    startup_probe_mode,
)


class _Provider:
    def __init__(self, models=None):
        self.models = list(models or [])
        self.calls = 0
        self.completed = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "ok"

    async def get_embedding(self, text): return [0.0] * 768
    async def health(self): return True

    async def list_models(self):
        self.calls += 1
        self.completed += 1
        return list(self.models)


class _GatedProvider(_Provider):
    """Blocks inside `list_models()` until the test opens the gate.

    The gate is created lazily on first use so it binds to the loop the
    lifespan is actually running on — the portal thread's, not the test's.
    """

    def __init__(self, models=None):
        super().__init__(models)
        self.gate: asyncio.Event | None = None
        self.entered = asyncio.Event()

    async def list_models(self):
        self.calls += 1
        if self.gate is None:
            self.gate = asyncio.Event()
        # W4b: bounded like every wait in a test; no test holds the gate
        # for more than seconds, so the bound changes no outcome.
        await asyncio.wait_for(self.gate.wait(), timeout=300)
        self.completed += 1
        return list(self.models)


def _app(tmp_path, provider):
    services = create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots",
    )
    services.provider = provider
    return create_app(services), services


# ---------------------------------------------------------------------------
# The knob
# ---------------------------------------------------------------------------

def test_the_default_is_background(monkeypatch):
    monkeypatch.delenv("NEXUS_STARTUP_PROBE", raising=False)
    assert DEFAULT_STARTUP_PROBE == "background"
    assert startup_probe_mode() == "background"


@pytest.mark.parametrize("mode", STARTUP_PROBE_MODES)
def test_every_declared_mode_is_accepted(mode, monkeypatch):
    monkeypatch.setenv("NEXUS_STARTUP_PROBE", mode.upper())
    assert startup_probe_mode() == mode, "mode parsing is case-sensitive"


def test_an_unrecognised_mode_warns_and_falls_back(monkeypatch, caplog):
    """I-6: falling back is fine, falling back silently is not. A typo in a
    tuning knob must not stop the app from starting, and must not pass
    unremarked either."""
    import logging

    monkeypatch.setenv("NEXUS_STARTUP_PROBE", "backgrond")
    with caplog.at_level(logging.WARNING, logger="nexus.server"):
        assert startup_probe_mode() == DEFAULT_STARTUP_PROBE
    assert any("backgrond" in r.getMessage() for r in caplog.records), (
        f"the bad value was swallowed silently; saw "
        f"{[r.getMessage() for r in caplog.records]}")


# ---------------------------------------------------------------------------
# sync — the rollback, and the proof the change is conservative
# ---------------------------------------------------------------------------

def test_sync_mode_completes_the_probe_before_serving(tmp_path, monkeypatch):
    """Pre-R.2 behaviour, exactly. Deterministic because the gated provider
    would deadlock startup if the mode were anything else — so this test
    passing at all is itself the assertion."""
    monkeypatch.setenv("NEXUS_STARTUP_PROBE", "sync")
    provider = _Provider(models=["nomic-embed-text:latest"])
    app, services = _app(tmp_path, provider)

    with TestClient(app):
        assert provider.completed == 1, "startup did not await the probe"
        assert services.model_probe["state"] == "ok"
        assert services.model_probe["mode"] == "sync"
        assert services.model_probe["flagged"] >= 1


# ---------------------------------------------------------------------------
# background — the default
# ---------------------------------------------------------------------------

def test_background_mode_serves_before_the_probe_finishes(tmp_path, monkeypatch):
    """THE CHANGE. Startup returns with the probe still in flight, and the app
    answers while it is pending.

    Deterministic by construction: the provider is blocked inside
    `list_models()`, so `completed == 0` is a fact rather than a race the test
    happens to win.
    """
    monkeypatch.setenv("NEXUS_STARTUP_PROBE", "background")
    provider = _GatedProvider(models=["nomic-embed-text:latest"])
    app, services = _app(tmp_path, provider)

    with TestClient(app) as client:
        assert provider.completed == 0, (
            "startup waited for the probe despite background mode")
        assert services.model_probe["state"] == "pending"

        # ...and the app is serving while it is pending. This is the whole
        # point of the packet.
        #
        # Deliberately NOT /healthz when this was written: its liveness
        # check went through the module-global ollama_client, so every call
        # was a real refused connection (~2ms here, ~2.45s on the operator's
        # box). R.4 moved /healthz to `services.provider` and W1 retired the
        # shim; /kb/stats is kept so this test's probe count is unchanged.
        # The declared surface is asserted once, at the bottom.
        assert client.get("/kb/stats").status_code == 200


def test_a_pending_probe_is_cancelled_at_shutdown(tmp_path, monkeypatch):
    """Cancellation is not tidiness — it is where the seconds are.

    `kairos.stop()` is awaited in the same `finally`, and an await is exactly
    where a pending probe gets its slice. Cancelling second would hand the
    Windows seconds straight back and leave the loop closing over a live task.
    Asserted by the fact that exiting the block returns at all with the
    provider still blocked, plus the declared state.
    """
    monkeypatch.setenv("NEXUS_STARTUP_PROBE", "background")
    provider = _GatedProvider()
    app, services = _app(tmp_path, provider)

    with TestClient(app):
        pass                       # exiting must not hang on the blocked probe

    assert provider.completed == 0, "the probe ran to completion after all"
    assert services.model_probe["state"] == "cancelled", (
        f"shutdown left the probe reported as "
        f"{services.model_probe['state']!r}")


def test_a_background_probe_that_finishes_flags_and_declares(tmp_path,
                                                             monkeypatch):
    """The other direction: deferred does not mean abandoned. Without this,
    a probe that was cancelled every single time would pass everything above.
    """
    monkeypatch.setenv("NEXUS_STARTUP_PROBE", "background")
    provider = _Provider(models=["nomic-embed-text:latest"])
    app, services = _app(tmp_path, provider)

    with TestClient(app) as client:
        # Not a sleep: poll the declared state through the app itself, which
        # only advances when the scheduled task actually runs.
        for _ in range(200):
            if services.model_probe["state"] == "ok":
                break
            client.get("/kb/stats")       # in-process; see the note above
        assert services.model_probe["state"] == "ok", (
            "the background probe never completed while the app was serving")
        assert provider.completed == 1
        flagged = [p.name for p in services.kernel.registry.all() if p.is_pulled]
        assert "nomic-embed-text" in flagged, (
            f"the background probe completed but flagged nothing: {flagged}")


# ---------------------------------------------------------------------------
# off — declared, not silent
# ---------------------------------------------------------------------------

def test_off_mode_never_probes_and_says_so(tmp_path, monkeypatch):
    """I-6. A probe that quietly never runs is worse than one that costs 5s,
    so `off` has to be visible on the status path rather than inferable from
    the router proposing a pull for an installed model."""
    monkeypatch.setenv("NEXUS_STARTUP_PROBE", "off")
    provider = _Provider(models=["nomic-embed-text:latest"])
    app, services = _app(tmp_path, provider)

    with TestClient(app) as client:
        assert provider.calls == 0, "off mode probed anyway"
        assert client.get("/kb/stats").status_code == 200
        payload = dict(services.model_probe)

    assert payload["state"] == "disabled", payload
    assert payload["mode"] == "off", payload
    assert not any(p.is_pulled for p in services.kernel.registry.all())


def test_off_mode_still_serves_a_turn(tmp_path, monkeypatch):
    """With nothing flagged, routing takes the same branch it takes when the
    daemon is down — the state R.0.3 measured as the only one this suite has
    ever started in."""
    monkeypatch.setenv("NEXUS_STARTUP_PROBE", "off")
    monkeypatch.setenv("NEXUS_API_TOKEN", "packet-r2-token")
    app, _ = _app(tmp_path, _Provider())

    with TestClient(app) as client:
        r = client.post("/chat", json={"message": "what is the QT interval?"},
                        headers={"Authorization": "Bearer packet-r2-token"})
        assert r.status_code == 200, r.text
        events = [l[7:] for l in r.text.split("\n") if l.startswith("event: ")]

    assert "model_routed" in events, f"no model routed; saw {events}"
    assert "done" in events


# ---------------------------------------------------------------------------
# The declared surface
# ---------------------------------------------------------------------------

def test_healthz_reports_the_probe_in_every_mode(tmp_path, monkeypatch):
    """One field, all modes, no mode omitted. A status surface that reports
    two of three states is how a deferred probe becomes an undetectable one.

    The only test here that calls /healthz, and it calls it three times —
    because /healthz is what it is about. Every other assertion reads
    `services.model_probe` directly to keep real sockets out of the suite.
    """
    seen = {}
    for mode in STARTUP_PROBE_MODES:
        monkeypatch.setenv("NEXUS_STARTUP_PROBE", mode)
        app, _ = _app(tmp_path / mode, _Provider(models=["nomic-embed-text"]))
        with TestClient(app) as client:
            payload = client.get("/healthz").json()
        assert "model_probe" in payload, f"{mode}: /healthz lost the field"
        seen[mode] = payload["model_probe"]["state"]
        assert payload["model_probe"]["mode"] == mode

    assert seen["sync"] == "ok"
    assert seen["off"] == "disabled"
    assert seen["background"] in {"pending", "ok"}, seen
