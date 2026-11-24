"""W3 part 2 — the two `api` providers, H5's embedder guard, LM Studio's Law 5
refusal, and the replay check over stub and api records (run/specs/W3.md S2,
D3; S1's H5; the two register lines W0b left).

A1  The factory selects `OpenAICompatProvider` for `openai` and
    `AnthropicProvider` for `anthropic`; an api backend whose model is unset
    is refused by name (`MissingSetting`, naming the setting), and so is the
    embedder named by one of its two settings; an unknown value names all
    five backends.
A2  The console (uvicorn's factory call) and MCP refuse a missing model at
    startup with one line, exit 2, nothing opened.
A3  OpenAI-compatible: the wire body (`seed` at the top, Law 5 refused and
    not sent), the chunk parse (the served model, the reasoning under both
    names, the content), and a turn through the app: `model_id` is the
    SERVED model under the `openai:` prefix, the seed on the wire is the
    record's, `model_digest` is the endpoint's `system_fingerprint`, the key
    travels as a header and reaches no record, `wire_body` rebuilds the body
    sent, extraction names its own model and carries no seed; an endpoint
    reporting neither model nor fingerprint leaves both absent with reasons.
A4  Anthropic: the system message split to the top-level `system`,
    `max_tokens` the caller's context size, no seed; the event parse
    (`message_start`'s model, `text_delta`, `thinking_delta`); a turn through
    the app: `model_id` the served model under `anthropic:`, seed and digest
    absent with their reasons, `x-api-key` and `anthropic-version` sent,
    `wire_body` splits the system message as `_payload` did, a seed on such a
    record refused by name.
A5  Law 5: LM Studio, the OpenAI-compatible and the Anthropic providers each
    refuse `num_ctx` above the ceiling BEFORE any request leaves (LM Studio
    sent it before W3: the register line).
A6  H5: a provider declaring no embedder is not asked by the idle scans: the
    dedup scan is skipped and says why on the cycle result and on
    `/cortex/stats.embedder`; the embedder is never called. The companion: a
    provider that declares nothing and fails is still asked, and the
    fallback is declared as before (the guard keys on the declaration).
A7  The embedder the api backends share: named by `NEXUS_EMBED_URL` and
    `NEXUS_EMBED_MODEL`, posted to `/embeddings` with its own key, in the
    roster and the model table; with neither, `has_embedder` is False, the
    table's `embed` is a sentinel no endpoint serves, and the chokepoint
    declares the fallback.
A8  `scripts/replay_check.py` classifies a stub record (no model ran) and an
    api record (re-sent to Ollama only) by their provider, before the
    incidental reasons (no seed, no digest) it gave them before.
A9  `start.py`: `--check` under an api backend with no model exits 2 naming
    the setting; the model-server check reaches an OpenAI-compatible
    `/models` with the key as a header, and names a closed port.
A10 The documented settings are probed (tests/test_dotenv_settings_take_effect.py).
A11 The three keys are read in one module (`backend/providers/api_settings.py`)
    and nowhere else under `backend/`; a planted reader is named.
A12 A refused request is a named error with the status and the body's
    start, never delivered as the answer.
A13 Both providers speak HTTP to a real listener (a thread server on
    loopback): the SSE parse, the headers, the paths.
A14 The model table and the roster follow the environment, and every roster
    profile is some role's resolution.

The children run from the repo root with the .env search confined to
tmp_path and every model-server setting removed (W3 part 1's shape).
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
from tests import _proc
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests import _w3_api_wire as wire
from tests._tracked import REPO, tracked_backend
from tests.test_w3_serve_stub import _Child, _chat, _collect, _env, _free_port

START = REPO / "start.py"
EXIT_USAGE, EXIT_MODEL_SERVER = 2, 3
KNOWN = "ollama, lmstudio, stub, openai, anthropic"
API_SETTINGS = ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_EXTRACTION_MODEL", "ANTHROPIC_BASE_URL",
                "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_EXTRACTION_MODEL", "NEXUS_EMBED_URL",
                "NEXUS_EMBED_MODEL", "NEXUS_EMBED_API_KEY")
KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NEXUS_EMBED_API_KEY")
MSGS = [{"role": "system", "content": "You are NEXUS."}, {"role": "user", "content": "what does interval training improve?"}]


def _clear(monkeypatch):
    for key in ("LLM_BACKEND", *API_SETTINGS):
        monkeypatch.delenv(key, raising=False)


def _openai(**over):
    from backend.providers.api_settings import ApiSettings
    from backend.providers.openai_compat import OpenAICompatProvider
    fields = dict(backend="openai", base_url="http://openai.invalid/v1", api_key=wire.API_KEY,
                  model=wire.OPENAI_MODEL, extraction_model=wire.OPENAI_EXTRACTOR)
    fields.update(over)
    return OpenAICompatProvider(ApiSettings(**fields))


def _anthropic(**over):
    from backend.providers.anthropic import AnthropicProvider
    from backend.providers.api_settings import ApiSettings
    fields = dict(backend="anthropic", base_url="http://anthropic.invalid", api_key=wire.API_KEY,
                  model=wire.ANTHROPIC_MODEL, extraction_model=wire.ANTHROPIC_MODEL)
    fields.update(over)
    return AnthropicProvider(ApiSettings(**fields))


def _turn(root: Path, provider, question: str = "what does interval training improve?"):
    """One /chat turn through the app under `provider`: the done frame, the
    answer, the record, its options and environment rows, the kept request."""
    from fastapi.testclient import TestClient
    from backend.data_root import paths_under
    from backend.server import create_app, create_services
    services = create_services(**paths_under(root), provider=provider)
    try:
        done, answer = _chat(TestClient(create_app(services)), question)
        rec = services.lineage.get(done["lineage_id"])
        config = services.lineage.inference_config(rec.inference_config_hash) if rec.inference_config_hash else None
        environment = services.lineage.environment(rec.environment_hash) if rec.environment_hash else None
        kept = json.loads(services.lineage.request_body(rec.request_hash))
    finally:
        services.store_lock.release()
    return done, answer, rec, config, environment, kept


def _no_key(*blobs) -> None:
    text = json.dumps([b for b in blobs if b is not None])
    assert wire.API_KEY not in text and wire.EMBED_KEY not in text, "a key reached a record"


# --- A1, A2: the factory and the two startups ---------------------------------------

def test_a1_the_factory_selects_the_api_providers_and_refuses_a_missing_model(monkeypatch):
    """Asserted on what is SELECTED and on the refusal's text, so part 1's
    factory fails here on its consequence: `openai` and `anthropic` were
    names it did not know."""
    import backend.providers as providers
    from backend.providers.factory import get_provider
    _clear(monkeypatch)
    refusal = getattr(providers, "BackendConfigError", getattr(providers, "UnknownBackend", Exception))
    for value, setting, kind in (("openai", "OPENAI_MODEL", "OpenAICompatProvider"),
                                 (" Anthropic ", "ANTHROPIC_MODEL", "AnthropicProvider")):
        monkeypatch.setenv("LLM_BACKEND", value)
        monkeypatch.setenv(setting, "some-model")
        try:
            selected = get_provider()
        except Exception as refused:                                   # noqa: BLE001
            pytest.fail(f"LLM_BACKEND={value!r} with {setting} set was refused: {refused}")
        assert type(selected).__name__ == kind, (value, type(selected).__name__)
        assert selected.settings.model == "some-model" and selected.takes_seed is (kind == "OpenAICompatProvider")
        monkeypatch.delenv(setting)
        with pytest.raises(refusal) as missing:
            get_provider()
        assert setting in str(missing.value) and "NEXUS names no model" in str(missing.value), str(missing.value)
        assert type(missing.value).__name__ == "MissingSetting"
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    monkeypatch.setenv("NEXUS_EMBED_URL", "http://embed.invalid/v1")
    with pytest.raises(refusal, match="NEXUS_EMBED_URL is set without NEXUS_EMBED_MODEL"):
        get_provider()
    monkeypatch.setenv("NEXUS_EMBED_MODEL", "e")
    assert get_provider().has_embedder is True
    monkeypatch.setenv("LLM_BACKEND", "api")
    with pytest.raises(refusal) as unknown:
        get_provider()
    assert KNOWN in str(unknown.value) and type(unknown.value).__name__ == "UnknownBackend"
    from backend.providers.backends import KNOWN_BACKENDS, backend_problem
    assert ", ".join(KNOWN_BACKENDS) == KNOWN
    assert backend_problem({"LLM_BACKEND": "anthropic"}).startswith("LLM_BACKEND=anthropic needs ANTHROPIC_MODEL")
    assert backend_problem({"LLM_BACKEND": "anthropic", "ANTHROPIC_MODEL": "m"}) is None


def test_a2_the_console_and_mcp_refuse_a_missing_model_at_startup(tmp_path):
    root, port = tmp_path / "console", _free_port()
    child = _Child([sys.executable, "-m", "uvicorn", "backend.server:create_app", "--factory", "--host",
                    "127.0.0.1", "--port", str(port), "--workers", "1"], cwd=REPO,
                   env=_env(tmp_path, LLM_BACKEND="openai", NEXUS_DATA_ROOT=str(root)))
    state = child.wait_until(port, timeout=120)
    code = child.stop()
    out = child.output()
    print(f"A2 console: state {state}, exit {code}: {out.strip().splitlines()[-1:]}")
    assert state == "exited", f"the console served under LLM_BACKEND=openai with no model\n{out[-1500:]}"
    assert code == EXIT_USAGE and "NEXUS: LLM_BACKEND=openai needs OPENAI_MODEL" in out, out[-1500:]
    assert "Nothing was opened" in out and "Traceback" not in out and not root.exists()
    mcp_root = tmp_path / "mcp"
    proc = _proc.run([sys.executable, "-m", "backend.mcp"], cwd=REPO,
                          env=_env(tmp_path, LLM_BACKEND="anthropic", NEXUS_MCP_DATA_ROOT=str(mcp_root)),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=240)
    print(f"A2 mcp: exit {proc.returncode}: {proc.stderr.strip().splitlines()[-1:]}")
    assert proc.returncode == EXIT_USAGE and "NEXUS MCP: LLM_BACKEND=anthropic needs ANTHROPIC_MODEL" in proc.stderr
    assert "Traceback" not in proc.stderr and not (mcp_root / "kb.db").exists()


# --- A3, A4: the two providers, on the wire and in the record -----------------------

def test_a3_openai_compatible_wire_body_parse_and_record(tmp_path, monkeypatch, caplog):
    from backend.providers.openai_compat import CHAT_ENDPOINT, NO_FINGERPRINT, openai_chunk_pieces
    from backend.providers.replay_capture import SEED_PLACEMENT, SEPARATION_KEY, wire_body
    _clear(monkeypatch)
    p = _openai()
    body = p._payload(wire.OPENAI_MODEL, MSGS, 8192, seed=12345)
    assert body == {"model": wire.OPENAI_MODEL, "messages": MSGS, "stream": True, "seed": 12345}
    assert p.request_config(wire.OPENAI_MODEL, 8192, separate=True) == {
        "provider": "openai", "endpoint": CHAT_ENDPOINT, "model": wire.OPENAI_MODEL, "stream": True}
    assert SEED_PLACEMENT["openai"] == ("seed",)
    assert wire_body({**p.request_config(wire.OPENAI_MODEL, 8192, separate=True), SEPARATION_KEY: True},
                     MSGS, 12345) == body
    assert p.headers() == {"Authorization": f"Bearer {wire.API_KEY}"} and _openai(api_key="").headers() == {}
    # The parse, pure: the served model, the reasoning under both names, the content.
    chunk = {"model": "m-2026", "choices": [{"delta": {"reasoning_content": "t", "content": "c"}}]}
    assert openai_chunk_pieces(chunk, separate=True) == [("model", "m-2026"), ("thinking", "t"), ("content", "c")]
    assert openai_chunk_pieces({"choices": [{"delta": {"reasoning": "t"}}]}, separate=True) == [("thinking", "t")]
    assert openai_chunk_pieces(chunk, separate=False) == [("model", "m-2026"), ("content", "c")]
    assert openai_chunk_pieces({"choices": []}, separate=True) == []
    # A turn through the app, the network replaced at the transport. The
    # environment names the backend as a deployment does: extraction resolves
    # its model through the table (`resolve("extraction")`), not the roster.
    fake = wire.install(monkeypatch)
    fake.reasoning = "let me think about intervals"
    monkeypatch.setenv("NEXUS_REPLY_SEPARATION", "on")
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_MODEL", wire.OPENAI_MODEL)
    monkeypatch.setenv("OPENAI_EXTRACTION_MODEL", wire.OPENAI_EXTRACTOR)
    done, answer, rec, config, environment, kept = _turn(tmp_path / "openai", _openai())
    sent = [b for _u, b in fake.bodies if wire.answering_api(b)]
    others = [b for _u, b in fake.bodies if not wire.answering_api(b)]
    print(f"A3 model_id {rec.model_id}, seed {rec.inference_seed}, digest {rec.model_digest}, "
          f"reasoning_chars {done['reasoning_chars']}, extraction models {[b['model'] for b in others]}")
    (sent,) = sent
    assert rec.model_id == f"openai:{wire.OPENAI_SERVED}", rec.model_id
    assert isinstance(rec.inference_seed, int) and sent["seed"] == rec.inference_seed
    assert rec.model_digest == wire.FINGERPRINT and not (rec.capture_notes or {}).get("model_digest")
    assert config == {"provider": "openai", "endpoint": CHAT_ENDPOINT, "model": wire.OPENAI_MODEL, "stream": True,
                      SEPARATION_KEY: True}
    assert wire_body(config, kept, rec.inference_seed) == sent, "the record does not rebuild the body sent"
    assert environment["openai_endpoint_host"] == "openai.invalid" and "model_server_version_absent" in environment
    # The key reaches no record, and no log line of the turn either (the
    # runbook's claim, item 9; the W3 verifier's F9: tested by nothing before).
    _no_key(config, environment, rec.capture_notes, kept, caplog.text)
    assert others and all(b["model"] == wire.OPENAI_EXTRACTOR and "seed" not in b for b in others), others
    assert all(h.get("Authorization") == f"Bearer {wire.API_KEY}" for u, h in fake.headers if "openai.invalid" in u)
    assert done["reasoning_chars"] == len(fake.reasoning) and fake.reasoning not in answer
    assert answer.startswith(wire.STEM if hasattr(wire, "STEM") else "Interval training improves")
    # Nothing reported: absent with reasons, the request's name under the prefix.
    fake.reset(report_model=False, report_fingerprint=False)
    done2, _answer, rec2, _c, _e, _k = _turn(tmp_path / "openai-bare", _openai())
    print(f"A3 bare endpoint: model_id {rec2.model_id}, notes {rec2.capture_notes}")
    assert rec2.model_id == f"openai:{wire.OPENAI_MODEL}" and rec2.model_digest is None
    assert rec2.capture_notes["model_digest"] == NO_FINGERPRINT
    assert "reported no model" in rec2.capture_notes["model_id"]


def test_a4_anthropic_wire_body_parse_and_record(tmp_path, monkeypatch):
    from backend.loop.compaction import STABLE_SYSTEM_PREFIX, TOKEN_BUDGET
    from backend.providers.anthropic import (ANTHROPIC_VERSION, MESSAGES_ENDPOINT, NO_DIGEST, anthropic_event_pieces,
                                             split_system)
    from backend.providers.replay_capture import SEED_PLACEMENT, SEPARATION_KEY, SYSTEM_PLACEMENT, wire_body
    _clear(monkeypatch)
    p = _anthropic()
    body = p._payload(wire.ANTHROPIC_MODEL, MSGS, 4096)
    assert body == {"model": wire.ANTHROPIC_MODEL, "max_tokens": 4096, "messages": MSGS[1:], "stream": True,
                    "system": "You are NEXUS."}
    assert split_system(MSGS) == ("You are NEXUS.", MSGS[1:]) and split_system(MSGS[1:]) == (None, MSGS[1:])
    config = p.request_config(wire.ANTHROPIC_MODEL, 4096, separate=True)
    assert config == {"provider": "anthropic", "endpoint": MESSAGES_ENDPOINT, "model": wire.ANTHROPIC_MODEL,
                      "max_tokens": 4096, "stream": True}
    assert SEED_PLACEMENT["anthropic"] is None and SYSTEM_PLACEMENT["anthropic"] == "system"
    assert wire_body({**config, SEPARATION_KEY: True}, MSGS, None) == body, "wire_body does not split the system message"
    with pytest.raises(ValueError, match="takes no seed"):
        wire_body({**config, SEPARATION_KEY: True}, MSGS, 7)
    assert p.headers() == {"anthropic-version": ANTHROPIC_VERSION, "x-api-key": wire.API_KEY}
    assert p.takes_seed is False
    start = {"type": "message_start", "message": {"model": "claude-x-2026", "role": "assistant"}}
    assert anthropic_event_pieces(start, separate=True) == [("model", "claude-x-2026")]
    assert anthropic_event_pieces({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "c"}},
                                  separate=True) == [("content", "c")]
    thinking = {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "t"}}
    assert anthropic_event_pieces(thinking, separate=True) == [("thinking", "t")]
    assert anthropic_event_pieces(thinking, separate=False) == []
    assert anthropic_event_pieces({"type": "ping"}, separate=True) == []
    fake = wire.install(monkeypatch)
    fake.reasoning = "considering the question"
    monkeypatch.setenv("NEXUS_REPLY_SEPARATION", "on")
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", wire.ANTHROPIC_MODEL)
    done, answer, rec, config, environment, kept = _turn(tmp_path / "anthropic", _anthropic())
    assert all(b["model"] == wire.ANTHROPIC_MODEL for _u, b in fake.bodies), "extraction asked another model"
    (sent,) = [b for _u, b in fake.bodies if wire.answering_api(b)]
    others = [b for _u, b in fake.bodies if not wire.answering_api(b)]
    print(f"A4 model_id {rec.model_id}, notes {rec.capture_notes}, max_tokens {sent['max_tokens']}/"
          f"{[b['max_tokens'] for b in others]}, reasoning_chars {done['reasoning_chars']}")
    assert rec.model_id == f"anthropic:{wire.ANTHROPIC_SERVED}", rec.model_id
    assert rec.inference_seed is None and rec.capture_notes["inference_seed"] == "provider takes no seed"
    assert rec.model_digest is None and rec.capture_notes["model_digest"] == NO_DIGEST
    assert sent["system"].startswith(STABLE_SYSTEM_PREFIX) and all(m["role"] != "system" for m in sent["messages"])
    assert sent["max_tokens"] == TOKEN_BUDGET and "seed" not in sent and "system" not in config
    assert wire_body(config, kept, None) == sent, "the record does not rebuild the body sent"
    assert others and all(isinstance(b["max_tokens"], int) and b["max_tokens"] <= TOKEN_BUDGET for b in others)
    assert environment["anthropic_endpoint_host"] == "anthropic.invalid"
    assert environment["anthropic_version"] == ANTHROPIC_VERSION
    _no_key(config, environment, rec.capture_notes, kept)
    api_headers = [h for u, h in fake.headers if "anthropic.invalid" in u]
    assert api_headers and all(h.get("x-api-key") == wire.API_KEY and h.get("anthropic-version") == ANTHROPIC_VERSION
                               for h in api_headers)
    assert done["reasoning_chars"] == len(fake.reasoning) and fake.reasoning not in answer


# --- A5: Law 5 at three providers ----------------------------------------------------------

def test_a5_law_v_refuses_above_the_ceiling_before_any_request_leaves(monkeypatch):
    """The consequence, for each provider: no body reaches the transport. LM
    Studio sent `num_ctx=100000` before W3 (the establish ran it)."""
    from backend.providers.lmstudio import LMStudioProvider
    _clear(monkeypatch)
    fake = wire.install(monkeypatch)
    for provider, model in ((LMStudioProvider(), "m:1"), (_openai(), wire.OPENAI_MODEL),
                            (_anthropic(), wire.ANTHROPIC_MODEL)):
        fake.reset()
        refused = None
        try:
            _collect(provider.stream_pieces(model, MSGS, 8193))
        except ValueError as e:
            refused = e
        print(f"A5 {type(provider).__name__}: refused={refused is not None}, bodies sent {len(fake.bodies)}")
        assert fake.bodies == [], f"{type(provider).__name__} sent a request above Law V's ceiling: {fake.bodies}"
        assert refused is not None and "Law V" in str(refused), type(provider).__name__
        _collect(provider.stream_pieces(model, MSGS, 8192))
        assert len(fake.bodies) == 1, f"{type(provider).__name__} did not send at the ceiling"


# --- A6, A7: the embedder -------------------------------------------------------------------

class _Counting:
    """Answers, and counts what it is asked to embed."""
    has_embedder = False
    no_embedder_reason = "planted: this provider declares no embedder"

    def __init__(self):
        self.embed_calls = 0

    async def stream_chat(self, model, messages, num_ctx):
        yield "ok"

    async def get_embedding(self, text):
        self.embed_calls += 1
        raise NotImplementedError("planted: no embedder")

    async def list_models(self):
        return []

    async def health(self):
        return True


class _Failing(_Counting):
    """Declares nothing about an embedder (every pre-W3 provider's shape) and
    fails when asked: the chokepoint's fallback, declared as before."""
    has_embedder = True


def _used_services(root: Path, provider):
    from backend.data_root import paths_under
    from backend.kb.granularity import Granularity
    from backend.server import create_services
    services = create_services(**paths_under(root), provider=provider)
    for s, r, o in [("QT interval", "is_a_segment_of", "ECG"), ("QTc", "stands_for", "corrected QT"),
                    ("ganymede", "orbits", "jupiter")]:
        services.store.add(subject=s, relation=r, object=o, weight=0.9, tier=1, granularity=Granularity.TOPIC,
                           source="wikidata")
    return services


def test_a6_a_provider_without_an_embedder_is_not_asked_by_the_idle_scans(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.server import create_app
    from backend.vector.embeddings import embedding_status
    _clear(monkeypatch)
    provider = _Counting()
    services = _used_services(tmp_path / "none", provider)
    try:
        result = asyncio.run(services.kairos.run_one_cycle())
        stats = TestClient(create_app(services)).get("/cortex/stats").json()
    finally:
        services.store_lock.release()
    print(f"A6 declared: embed calls {provider.embed_calls}, dedup skipped {result.__dict__.get('dedup_skipped_reason')!r}, "
          f"stats.embedder {stats.get('embedder')}")
    assert not result.skipped
    assert provider.embed_calls == 0, "the dedup scan asked a provider that declared it has no embedder"
    assert "planted: this provider declares no embedder" in result.dedup_skipped_reason
    assert result.dedup_scan_failed is False and result.dedup_candidates == 0
    assert stats["embedder"]["has_embedder"] is False and stats["embedder"]["reason"] == provider.no_embedder_reason
    assert stats["embedder"]["dedup_scan"].startswith("skipped")
    # The companion: declaring nothing is still being asked, and the fallback declared.
    failing = _Failing()
    services = _used_services(tmp_path / "failing", failing)
    try:
        result = asyncio.run(services.kairos.run_one_cycle())
        stats = TestClient(create_app(services)).get("/cortex/stats").json()
    finally:
        services.store_lock.release()
    print(f"A6 undeclared: embed calls {failing.embed_calls}, degraded {embedding_status()['degraded']}")
    assert failing.embed_calls >= 3 and result.dedup_skipped_reason == "" and not result.dedup_scan_failed
    assert embedding_status()["degraded"] is True and stats["embedder"]["dedup_scan"] == "runs"


def test_a7_the_named_embedder_is_used_and_an_unnamed_one_is_declared_absent(monkeypatch):
    from backend.providers.api_settings import NO_EMBED_MODEL, api_model_table, embed_settings
    from backend.providers.model_map import resolve
    from backend.router.capabilities import Capability
    from backend.vector.embeddings import embed_via_provider, embedding_status
    _clear(monkeypatch)
    fake = wire.install(monkeypatch)
    # None named: declared absent, the table's sentinel, the chokepoint's fallback.
    bare = _anthropic()
    assert bare.has_embedder is False and embed_settings() is None
    assert resolve("embed", backend="anthropic") == NO_EMBED_MODEL and resolve("embed", backend="openai") == NO_EMBED_MODEL
    assert not [m for m in bare.model_registry().all() if Capability.EMBEDDING in m.capabilities]
    before = embedding_status()["fallbacks"]
    asyncio.run(embed_via_provider(bare, "cardiac safety"))
    assert embedding_status()["degraded"] is True and embedding_status()["fallbacks"] == before + 1
    assert fake.embed_bodies == []
    # Named: posted to its endpoint with its own key, in the roster and the table.
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", wire.ANTHROPIC_MODEL)
    monkeypatch.setenv("NEXUS_EMBED_URL", wire.EMBED_URL)
    monkeypatch.setenv("NEXUS_EMBED_MODEL", wire.EMBED_MODEL)
    monkeypatch.setenv("NEXUS_EMBED_API_KEY", wire.EMBED_KEY)
    named = _anthropic()
    assert named.has_embedder is True and resolve("embed") == wire.EMBED_MODEL
    assert api_model_table("anthropic")["embed"] == wire.EMBED_MODEL
    vec = asyncio.run(embed_via_provider(named, "cardiac safety"))
    print(f"A7 embed bodies {fake.embed_bodies}, headers {[h for u, h in fake.headers if 'embed.invalid' in u]}")
    assert vec == wire.embedding_for("cardiac safety") and embedding_status()["degraded"] is False
    assert fake.embed_bodies == [(f"{wire.EMBED_URL}/embeddings", {"model": wire.EMBED_MODEL, "input": "cardiac safety"})]
    assert [h for u, h in fake.headers if "embed.invalid" in u] == [{"Authorization": f"Bearer {wire.EMBED_KEY}"}]
    embed_profiles = [m for m in named.model_registry().all() if Capability.EMBEDDING in m.capabilities]
    assert [m.name for m in embed_profiles] == [wire.EMBED_MODEL] and embed_profiles[0].is_pulled
    assert _openai().has_embedder is True, "the embedder is the api backends' shared one"


# --- A8: the replay check over stub and api records -------------------------------------

def _record(ls, model_id: str, config: dict, *, seed=None, digest=None):
    import hashlib
    body = json.dumps([{"content": "q", "role": "user"}], sort_keys=True, separators=(",", ":")).encode()
    rec = ls.record_turn(query="q", injected=[], contradictions=[], model_id=model_id, response_text="a",
                         prompt_prefix="p", answer_text="a", request_hash=hashlib.sha256(body).hexdigest(),
                         request_body=body, inference_seed=seed, model_digest=digest, inference_config=config,
                         environment={"os": sys.platform},
                         capture_notes=({} if seed is not None else {"inference_seed": "provider takes no seed"}))
    return rec.lineage_id


@pytest.mark.sqlite_only("replay_check.py reads the SQLite file through the read-only open (law 15); the URL form is W3b part 3 (D9, T8)")
def test_a8_replay_check_classifies_stub_and_api_records_by_provider(tmp_path):
    from backend.memory.lineage import LineageStore
    from backend.providers.replay_capture import SEPARATION_KEY
    from scripts import replay_check
    from scripts.cp_b49_tally import open_read_only
    db = tmp_path / "kb.db"
    ls = LineageStore(db)
    stub = _record(ls, "stub:primary", {"provider": "stub", "endpoint": None, "model": "stub:primary", "num_ctx": 8192,
                                        SEPARATION_KEY: True})
    anthropic = _record(ls, "anthropic:claude-x", {"provider": "anthropic", "endpoint": "/v1/messages",
                                                   "model": "claude-x", "max_tokens": 8192, "stream": True,
                                                   SEPARATION_KEY: True})
    openai = _record(ls, "openai:gpt-x", {"provider": "openai", "endpoint": "/chat/completions", "model": "gpt-x",
                                          "stream": True, SEPARATION_KEY: True}, seed=7)
    ls.close()
    conn = open_read_only(db)
    try:
        report = replay_check.check(conn, last=5, daemon=None)
    finally:
        conn.close()
    detail = {o.candidate.lineage_id: (o.verdict, o.detail) for o in report.outcomes}
    for name, lid in (("stub", stub), ("anthropic", anthropic), ("openai", openai)):
        print(f"A8 {name}: {detail[lid]}")
    assert detail[stub][0] == "not replayable" and detail[stub][1].startswith("LLM_BACKEND=stub"), detail[stub]
    assert "no model" in detail[stub][1]
    assert detail[anthropic][1].startswith("provider 'anthropic'") and "Ollama only" in detail[anthropic][1]
    assert detail[openai][1].startswith("provider 'openai'") and "Ollama only" in detail[openai][1]
    assert report.counts()["not replayable"] == 3 and report.counts()["would_send"] == 0


# --- A9: start.py ------------------------------------------------------------------------------

class _ModelsHandler(BaseHTTPRequestHandler):
    seen: list = []
    served: list = []

    def log_message(self, *a):                                      # quiet
        pass

    def _send(self, status: int, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).seen.append((self.path, dict(self.headers)))
        if self.path.rstrip("/").endswith("models"):
            self._send(200, {"object": "list", "data": [{"id": m} for m in type(self).served]})
        else:
            self._send(404, {"error": "no such path"})


def _listener(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def test_a9_start_py_checks_an_api_backend(tmp_path, monkeypatch):
    import start
    _clear(monkeypatch)
    proc = _proc.run([sys.executable, str(START), "--check", "--data-root", str(tmp_path / "root")], cwd=REPO,
                          env=_env(tmp_path, LLM_BACKEND="openai"), stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=240)
    print(f"A9 --check exit {proc.returncode}: {[l for l in proc.stdout.splitlines() if l.startswith('  model')]}")
    assert proc.returncode == EXIT_USAGE and "OPENAI_MODEL" in proc.stdout, proc.stdout[-1500:]
    assert not (tmp_path / "root").exists()
    # The in-process check: the target as the factory resolves it, the key as a header, a closed port named.
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    monkeypatch.setenv("OPENAI_API_KEY", "k-check")
    _ModelsHandler.seen, _ModelsHandler.served = [], ["m", "m-mini"]
    server, port = _listener(_ModelsHandler)
    try:
        monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{port}/v1")
        assert start.model_server_target() == ("openai", f"http://127.0.0.1:{port}/v1")
        result = start.check_model_server("openai", f"http://127.0.0.1:{port}/v1")
    finally:
        server.shutdown()
    print(f"A9 check: {result}; seen {[(p, h.get('Authorization')) for p, h in _ModelsHandler.seen]}")
    assert result.reachable and result.models == ("m", "m-mini"), result
    assert _ModelsHandler.seen and all(h.get("Authorization") == "Bearer k-check" for _p, h in _ModelsHandler.seen)
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", "m")
    closed = _free_port()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{closed}")
    assert start.model_server_target() == ("anthropic", f"http://127.0.0.1:{closed}")
    result = start.check_model_server("anthropic", f"http://127.0.0.1:{closed}")
    assert not result.reachable and "connection refused" in result.reason and result.url.endswith("/v1/models"), result
    assert start.SERVER_NAMES["anthropic"] == "the Anthropic API"


# --- A11: one reader of the keys ------------------------------------------------------------

def key_readers(sources: dict[str, str]) -> dict[str, list[str]]:
    """{key: [files]} for every file that READS one of the three keys: the
    key name as a string constant in a reading position — an argument of a
    call (`os.environ.get(K)`, `os.getenv(K)`, `_read(environ, K)`), the
    slice of a subscript (`environ[K]`) or the value a name is bound to
    (`SETTING = K`, read through the name). A key named in a message or a
    docstring is not a read."""
    import ast
    out: dict[str, list[str]] = {k: [] for k in KEYS}
    for name, text in sources.items():
        tree = ast.parse(text)
        reading: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                    reading.add(id(arg))
            elif isinstance(node, ast.Subscript):
                reading.add(id(node.slice))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                reading.add(id(node.value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) in reading:
                for key in KEYS:
                    if node.value == key:
                        out[key].append(name)
    return {k: sorted(set(v)) for k, v in out.items()}


def test_a11_the_keys_are_read_in_one_module():
    sources = {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8") for p in tracked_backend()}
    sources["start.py"] = START.read_text(encoding="utf-8")
    readers = key_readers(sources)
    print(f"A11 {readers}")
    assert all(files == ["backend/providers/api_settings.py"] for files in readers.values()), readers


def test_a11c_a_planted_second_reader_is_named():
    planted = {"backend/providers/api_settings.py": 'KEY = "OPENAI_API_KEY"\n',
               "backend/loop/planted.py": '"""Mentions OPENAI_API_KEY in prose only."""\n'
                                          'import os\nkey = os.environ.get("ANTHROPIC_API_KEY")\n'
                                          'hint = f"set {\'NEXUS_EMBED_API_KEY\' if key else \'nothing\'} first"\n',
               "backend/loop/planted2.py": 'import os\nkey = os.environ["NEXUS_EMBED_API_KEY"]\n'}
    readers = key_readers(planted)
    assert readers["ANTHROPIC_API_KEY"] == ["backend/loop/planted.py"], readers
    assert readers["OPENAI_API_KEY"] == ["backend/providers/api_settings.py"], "a docstring mention was counted"
    assert readers["NEXUS_EMBED_API_KEY"] == ["backend/loop/planted2.py"], "a message naming the key was counted"


# --- A12: a refusal is named -------------------------------------------------------------

def test_a12_a_refused_request_is_a_named_error_not_an_answer(monkeypatch):
    from backend.providers.api_common import ApiRefused
    from backend.providers.base import collect_reply
    _clear(monkeypatch)
    fake = wire.install(monkeypatch)
    for provider, model in ((_openai(), wire.OPENAI_MODEL), (_anthropic(), wire.ANTHROPIC_MODEL)):
        fake.reset(status=429)
        with pytest.raises(ApiRefused) as refused:
            asyncio.run(collect_reply(provider, model, MSGS, num_ctx=8192))
        print(f"A12 {type(provider).__name__}: {refused.value}")
        assert refused.value.status == 429 and "HTTP 429" in str(refused.value)
        assert "fake endpoint refused with 429" in str(refused.value) and model in str(refused.value)
        assert len(fake.bodies) == 1


# --- A13: a real listener -------------------------------------------------------------------

class _ApiHandler(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def _sse(self, lines: list[str]):
        payload = ("\n".join(lines) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen.append((self.path, dict(self.headers), body))
        if self.path.endswith("/chat/completions"):
            self._sse(['data: ' + json.dumps({"model": "served-openai", "system_fingerprint": "fp_real",
                                              "choices": [{"delta": {"content": "Interval training "}}]}),
                       'data: ' + json.dumps({"model": "served-openai",
                                              "choices": [{"delta": {"content": "improves VO2 max."}}]}),
                       "data: [DONE]"])
        elif self.path.endswith("/v1/messages"):
            def ev(kind, payload):
                return [f"event: {kind}", "data: " + json.dumps({"type": kind, **payload}), ""]
            self._sse(ev("message_start", {"message": {"model": "served-claude"}})
                      + ev("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "Interval "}})
                      + ev("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "training."}})
                      + ev("message_stop", {}))
        else:
            self.send_response(404)
            self.end_headers()


def test_a13_both_providers_speak_http_to_a_real_listener(monkeypatch):
    from backend.providers.base import collect_reply
    _clear(monkeypatch)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    _ApiHandler.seen = []
    server, port = _listener(_ApiHandler)
    try:
        o_reply, o_capture = asyncio.run(collect_reply(_openai(base_url=f"http://127.0.0.1:{port}/v1"), "m",
                                                       MSGS, num_ctx=8192))
        a_reply, a_capture = asyncio.run(collect_reply(_anthropic(base_url=f"http://127.0.0.1:{port}"), "c",
                                                       MSGS, num_ctx=8192))
    finally:
        server.shutdown()
    print(f"A13 openai {o_reply.answer!r} digest {o_capture.digest}; anthropic {a_reply.answer!r}; "
          f"paths {[p for p, _h, _b in _ApiHandler.seen]}")
    assert o_reply.answer == "Interval training improves VO2 max." and o_capture.digest == "fp_real"
    assert a_reply.answer == "Interval training." and a_capture.digest is None
    (o_path, o_headers, o_body), (a_path, a_headers, a_body) = _ApiHandler.seen
    assert o_path == "/v1/chat/completions" and o_headers.get("Authorization") == f"Bearer {wire.API_KEY}"
    assert isinstance(o_body.get("seed"), int) and o_body["model"] == "m"
    assert a_path == "/v1/messages" and a_headers.get("x-api-key") == wire.API_KEY
    assert a_body["system"] == "You are NEXUS." and a_body["max_tokens"] == 8192 and "seed" not in a_body


# --- A14: the table and the roster follow the environment -------------------------------------

def test_a14_the_model_table_and_the_roster_follow_the_environment(monkeypatch):
    from backend.providers.api_settings import api_model_table
    from backend.providers.model_map import MODEL_MAP, ROLES, answering_roles, resolve
    _clear(monkeypatch)
    assert "openai" not in MODEL_MAP and "anthropic" not in MODEL_MAP, "an api backend's names are the environment's"
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-x")
    monkeypatch.setenv("OPENAI_EXTRACTION_MODEL", "gpt-x-mini")
    table = api_model_table()
    assert set(table) == set(ROLES) and table["primary"] == table["code"] == "gpt-x" and table["extraction"] == "gpt-x-mini"
    assert resolve("primary") == "gpt-x" and resolve("extraction") == "gpt-x-mini" and resolve("code", "openai") == "gpt-x"
    provider = _openai(model="gpt-x", extraction_model="gpt-x-mini")
    roster = provider.model_registry()
    assert sorted(m.name for m in roster.all()) == ["gpt-x", "gpt-x-mini"]
    assert all(m.is_pulled for m in roster.all())
    assert {resolve(role, "openai") for role in ROLES} >= {m.name for m in roster.all()}, "a roster profile no role reaches"
    assert "primary" in answering_roles(roster, "openai") and "embed" not in answering_roles(roster, "openai")
    assert resolve("primary", backend="weird") == MODEL_MAP["ollama"]["primary"], "the unknown-backend fallback moved"
