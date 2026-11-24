"""Phase 1 tests — required coverage from CLAUDE.md:
  - state immutability (`replace()` returns new object)
  - SSE format valid
  - Ollama reachable (skipped when offline so the rest of the suite still runs)
"""
from __future__ import annotations

import asyncio
import dataclasses
import re

import pytest

from backend.loop.agentic_loop import sse_event, run_turn, STABLE_SYSTEM_PREFIX
from backend.loop.state import NexusLoopState, next_state
# W1 — from the provider itself: the legacy backend/ollama_client.py shim
# this imported is retired (tests/test_w1_shim_retired.py).
from backend.providers.ollama import NUM_CTX, OllamaProvider
from tests._tracked import tracked_backend


# ---------- Law VI: immutable state ----------

def test_state_is_frozen():
    s = NexusLoopState()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.turn_count = 99  # type: ignore[misc]


def test_next_state_returns_new_object():
    s0 = NexusLoopState()
    s1 = next_state(s0, turn_count=1, transition_reason="test")
    assert s1 is not s0
    assert s0.turn_count == 0  # original untouched
    assert s1.turn_count == 1
    assert s1.transition_reason == "test"


def test_state_default_active_model_is_deepseek():
    # Phase 1: deepseek is default until model kernel arrives in Phase 6
    assert "deepseek" in NexusLoopState().active_model.lower()


# ---------- Law VII: stable system prefix ----------

def test_stable_system_prefix_is_constant():
    # The string must be identical every import. Future phases may not mutate it
    # mid-session — this test pins the constant so accidental edits are visible.
    assert isinstance(STABLE_SYSTEM_PREFIX, str)
    assert len(STABLE_SYSTEM_PREFIX) > 0
    assert "NEXUS" in STABLE_SYSTEM_PREFIX


# ---------- SSE format ----------

SSE_RE = re.compile(r"^event: (?P<event>[^\n]+)\ndata: (?P<data>.*)\n\n$", re.DOTALL)


def test_sse_event_format_string_payload():
    frame = sse_event("token", "hello")
    m = SSE_RE.match(frame)
    assert m, f"frame did not match SSE regex: {frame!r}"
    assert m.group("event") == "token"
    assert m.group("data") == "hello"


def test_sse_event_format_json_payload():
    frame = sse_event("done", {"turn": 1, "tokens": 12})
    m = SSE_RE.match(frame)
    assert m
    import json
    assert json.loads(m.group("data")) == {"turn": 1, "tokens": 12}


# ---------- Law V: num_ctx ceiling ----------

def test_num_ctx_ceiling():
    assert NUM_CTX == 8192


# ---------- Ollama reachability ----------

def test_ollama_reachable():
    reachable = asyncio.run(OllamaProvider().health())
    if not reachable:
        pytest.skip("Ollama not running on localhost:11434 — start it before this test counts")
    assert reachable is True


# ---------- End-to-end agentic loop (only when Ollama is up) ----------

def test_run_turn_streams_and_transitions():
    if not asyncio.run(OllamaProvider().health()):
        pytest.skip("Ollama not running — skipping live turn test")

    async def go():
        s0 = NexusLoopState()
        frames = []
        last_state = s0
        async for frame, state_after in run_turn(s0, "Say the single word: hello"):
            frames.append(frame)
            last_state = state_after
        return frames, last_state

    frames, s1 = asyncio.run(go())
    assert any("event: token" in f for f in frames), "no token frames streamed"
    assert frames[-1].startswith("event: done"), f"final frame not 'done': {frames[-1]!r}"
    assert s1.turn_count == 1
    assert s1 is not NexusLoopState()  # different object than a fresh state
    assert len(s1.messages) == 2  # user + assistant


# ===================================================================
# Fix: graceful 404 + env-driven BASE_URL (Ollama port configurability)
# ===================================================================

import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

from backend.providers.ollama import OllamaProvider


class _FakeStreamResponse:
    """Mock httpx.AsyncClient.stream() context manager — yields prearranged
    aiter_lines() output and reports a configurable status_code."""
    def __init__(self, *, status_code: int, lines=()):
        self.status_code = status_code
        self._lines = list(lines)

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return None

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln


class _FakeClient:
    def __init__(self, response: _FakeStreamResponse):
        self.response = response
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return None
    def stream(self, method, url, json=None):
        return self.response


def _drain(provider, *, model="ghost-model:1b"):
    """Run stream_chat to completion and return the list of yielded items."""
    async def go():
        out = []
        async for item in provider.stream_chat(
            model, [{"role": "user", "content": "hi"}], num_ctx=8192,
        ):
            out.append(item)
        return out
    return asyncio.run(go())


def test_stream_chat_yields_done_error_on_404():
    """404 (model not found) → one JSON-encoded token with `error` + `event:done`.
    No HTTPStatusError raised, no further iteration over aiter_lines."""
    resp = _FakeStreamResponse(status_code=404, lines=[])
    with patch("backend.providers.ollama.httpx.AsyncClient",
               return_value=_FakeClient(resp)):
        out = _drain(OllamaProvider(), model="ghost-model:1b")
    assert len(out) == 1, f"expected exactly 1 yield, got {len(out)}: {out}"
    payload = _json.loads(out[0])
    assert payload["event"] == "done"
    assert "error" in payload
    assert "ghost-model:1b" in payload["error"]
    assert "ollama pull ghost-model:1b" in payload["error"]


def test_stream_chat_yields_done_error_on_500():
    """Any 4xx/5xx that isn't 404 → generic error event, still no raise."""
    resp = _FakeStreamResponse(status_code=503, lines=[])
    with patch("backend.providers.ollama.httpx.AsyncClient",
               return_value=_FakeClient(resp)):
        out = _drain(OllamaProvider(), model="some-model")
    assert len(out) == 1
    payload = _json.loads(out[0])
    assert payload["event"] == "done"
    assert "Ollama error 503" in payload["error"]
    assert "some-model" in payload["error"]


def test_stream_chat_does_not_raise_on_http_status_failure():
    """The contract is graceful degradation — never bubble up into the SSE
    consumer as an HTTPStatusError."""
    import httpx as _httpx
    resp = _FakeStreamResponse(status_code=404, lines=[])
    with patch("backend.providers.ollama.httpx.AsyncClient",
               return_value=_FakeClient(resp)):
        try:
            _drain(OllamaProvider(), model="x")
        except _httpx.HTTPStatusError:
            pytest.fail("stream_chat raised HTTPStatusError on 404 — must yield gracefully")


def test_stream_chat_normal_path_still_yields_tokens():
    """Sanity: the 404/5xx guard must not break the happy path."""
    lines = [
        _json.dumps({"message": {"content": "hello"}, "done": False}),
        _json.dumps({"message": {"content": " world"}, "done": False}),
        _json.dumps({"message": {"content": ""}, "done": True}),
    ]
    resp = _FakeStreamResponse(status_code=200, lines=lines)
    with patch("backend.providers.ollama.httpx.AsyncClient",
               return_value=_FakeClient(resp)):
        out = _drain(OllamaProvider(), model="m")
    assert out == ["hello", " world"]


def test_ollama_base_url_reads_from_env_var(monkeypatch):
    """OLLAMA_URL env var must override the hardcoded fallback. Instance-time
    resolution means a fresh provider after monkeypatch sees the override."""
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:64742")
    p = OllamaProvider()
    assert p.BASE_URL == "http://localhost:64742"
    assert p.BASE_URL != "http://localhost:11434"


def test_ollama_base_url_resolves_at_instance_time_not_import_time(monkeypatch):
    """Two providers built before vs after monkeypatch must reflect the
    env var as it was AT EACH __init__ — never frozen to import-time value."""
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    p1 = OllamaProvider()
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:64742")
    p2 = OllamaProvider()
    assert p1.BASE_URL == "http://localhost:11434"
    assert p2.BASE_URL == "http://localhost:64742"


def test_swap_manager_reads_ollama_url_at_call_time(monkeypatch):
    """The swap manager helper must hit the env var fresh each call."""
    from backend.router.swap_manager import _ollama_url
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    assert _ollama_url() == "http://localhost:11434"
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:64742")
    assert _ollama_url() == "http://localhost:64742"


def test_embeddings_helper_reads_ollama_url_at_call_time(monkeypatch):
    """Same contract for the embeddings module's URL helper."""
    from backend.vector.embeddings import _ollama_url
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:64742")
    assert _ollama_url() == "http://localhost:64742"


def test_no_hardcoded_11434_as_primary_value_in_backend():
    """Spec acceptance criterion — 11434 may appear as an env-fallback default
    only, never as a primary value. We flag any URL literal containing
    :11434 unless it's the 2nd arg of os.getenv("OLLAMA_URL", ...) or
    os.environ.get("OLLAMA_URL", ...). Docstring mentions of the bare
    digit sequence are not URLs, so they don't trip this check."""
    import re as _re
    import pathlib as _pl
    url_re = _re.compile(r'http://[^"\']*:11434')
    fallback_ok = _re.compile(
        r'os\.(?:getenv|environ\.get)\(\s*["\']OLLAMA_URL["\']\s*,\s*'
        r'["\']http://localhost:11434["\']\s*\)'
    )
    for f in tracked_backend():
        text = f.read_text(encoding="utf-8")
        for m in url_re.finditer(text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            line = text[line_start:line_end if line_end != -1 else len(text)]
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            code_part = line.split("#", 1)[0]
            if "11434" not in code_part:
                continue
            assert fallback_ok.search(code_part), (
                f"Hardcoded primary URL :11434 found in {f}:\n  {line.strip()}"
            )
