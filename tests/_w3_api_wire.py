"""W3 part 2 — the network replaced at the transport, for the two api
providers (W0b's `tests/_w0b_wire.py` shape, extended).

`FakeApis` stands in for `httpx.AsyncClient` inside the provider modules,
so `OpenAICompatProvider` and `AnthropicProvider` run unchanged: their own
`_payload`, their own headers, their own stream parsing. Every request's
body AND headers are kept, which is what lets a test compare the record
with what went on the wire and assert the key travelled as a header and
nowhere else.

The fake model is W0b's: the reply is a pure function of the seed
(`answer_for`), so a seeded request reproduces and an unseeded one answers
a fixed text. `reasoning` plants a reasoning text before the answer, under
the OpenAI-compatible extension's name (`reasoning_content`) and as
Anthropic's `thinking_delta`.
"""
from __future__ import annotations

import copy
import json

from tests._w0b_wire import FakeDaemon, _Resp, _dumps, answer_for, answering

OPENAI_MODEL, OPENAI_SERVED, FINGERPRINT = "gpt-4o", "gpt-4o-2024-08-06", "fp_44709d6fcb"
OPENAI_EXTRACTOR = "gpt-4o-mini"
ANTHROPIC_MODEL, ANTHROPIC_SERVED = "claude-sonnet-5-5", "claude-sonnet-5-5-20260301"
API_KEY, EMBED_KEY = "sk-test-0123456789abcdef", "emb-test-fedcba9876543210"
EMBED_URL, EMBED_MODEL = "http://embed.invalid/v1", "text-embedding-3-small"


def answering_api(body: dict) -> bool:
    """The answering request, under either shape: the stable prefix leads the
    first message (OpenAI-compatible), or the top-level `system` (Anthropic)."""
    from backend.loop.compaction import STABLE_SYSTEM_PREFIX
    system = body.get("system")
    if isinstance(system, str) and system.startswith(STABLE_SYSTEM_PREFIX):
        return True
    return answering(body)


def embedding_for(text: str) -> list[float]:
    """A deterministic 768-vector a test can recognise (never a hash vector:
    the chokepoint's fallback must stay distinguishable from a real answer)."""
    base = (sum(text.encode("utf-8")) % 97) / 100.0
    return [base] + [0.01] * 767


class _ApiResp(_Resp):
    """W0b's response with `aread()`, which the api providers call on a
    refusal to read the error body, and the headers the request carried."""

    async def aread(self) -> bytes:
        return json.dumps(self._body).encode("utf-8") if self._body is not None else b""


class FakeApis(FakeDaemon):
    headers: list[tuple[str, dict]] = []        # (url, headers) per request
    embed_bodies: list[tuple[str, dict]] = []
    status = 200                                 # what the chat endpoints answer
    reasoning: str | None = None                 # a reasoning text before the answer
    report_model = True                          # chunks/events name the served model
    report_fingerprint = True                    # OpenAI chunks carry system_fingerprint
    served = {OPENAI_MODEL: OPENAI_SERVED, OPENAI_EXTRACTOR: OPENAI_EXTRACTOR + "-2026-01-01",
              ANTHROPIC_MODEL: ANTHROPIC_SERVED}

    @classmethod
    def reset(cls, *, tags=None, answer=None, status=200, reasoning=None, report_model=True,
              report_fingerprint=True):
        super().reset(tags=tags if tags is not None else {OPENAI_MODEL: "", ANTHROPIC_MODEL: ""}, answer=answer)
        cls.headers, cls.embed_bodies = [], []
        cls.status, cls.reasoning = status, reasoning
        cls.report_model, cls.report_fingerprint = report_model, report_fingerprint

    async def get(self, url, headers=None, **kw):
        type(self).headers.append((url, dict(headers or {})))
        if url.endswith("/models"):
            return _ApiResp(200, {"data": [{"id": n} for n in type(self).tags]})
        return await super().get(url, **kw)

    async def post(self, url, json=None, headers=None, **kw):
        type(self).headers.append((url, dict(headers or {})))
        if url.endswith("/embeddings"):
            type(self).embed_bodies.append((url, copy.deepcopy(json)))
            return _ApiResp(200, {"data": [{"embedding": embedding_for(json["input"])}]})
        return _ApiResp(404, {})

    def stream(self, method, url, json=None, headers=None, **kw):
        body = copy.deepcopy(json)
        cls = type(self)
        cls.bodies.append((url, body))
        cls.headers.append((url, dict(headers or {})))
        if cls.status >= 400:
            return _ApiResp(cls.status, {"error": {"type": "fake_refusal",
                                                   "message": f"the fake endpoint refused with {cls.status}"}})
        pieces = cls.answer(body) if answering_api(body) else ["[]"]
        model = body.get("model")
        served = cls.served.get(model, model) if cls.report_model else None
        if url.endswith("/v1/messages"):
            return _ApiResp(200, lines=_anthropic_lines(served, pieces, cls.reasoning))
        if url.endswith("/chat/completions"):
            return _ApiResp(200, lines=_openai_lines(served, pieces, cls.reasoning,
                                                     FINGERPRINT if cls.report_fingerprint else None))
        return super().stream(method, url, json=json, **kw)


def _openai_chunk(served, delta, fingerprint):
    chunk = {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta}]}
    if served:
        chunk["model"] = served
    if fingerprint:
        chunk["system_fingerprint"] = fingerprint
    return "data: " + _dumps(chunk)


def _openai_lines(served, pieces, reasoning, fingerprint) -> list[str]:
    lines = []
    if reasoning:
        lines.append(_openai_chunk(served, {"reasoning_content": reasoning}, fingerprint))
    lines += [_openai_chunk(served, {"content": p}, fingerprint) for p in pieces]
    lines.append("data: [DONE]")
    return lines


def _anthropic_lines(served, pieces, reasoning) -> list[str]:
    def ev(kind, payload):
        return [f"event: {kind}", "data: " + _dumps({"type": kind, **payload}), ""]
    message = {"id": "msg_fake", "type": "message", "role": "assistant", "content": []}
    if served:
        message["model"] = served
    lines = ev("message_start", {"message": message}) + ev("ping", {})
    index = 0
    if reasoning:
        lines += ev("content_block_start", {"index": index, "content_block": {"type": "thinking", "thinking": ""}})
        lines += ev("content_block_delta", {"index": index, "delta": {"type": "thinking_delta", "thinking": reasoning}})
        lines += ev("content_block_delta", {"index": index, "delta": {"type": "signature_delta", "signature": "sig"}})
        lines += ev("content_block_stop", {"index": index})
        index += 1
    lines += ev("content_block_start", {"index": index, "content_block": {"type": "text", "text": ""}})
    for p in pieces:
        lines += ev("content_block_delta", {"index": index, "delta": {"type": "text_delta", "text": p}})
    lines += ev("content_block_stop", {"index": index})
    lines += ev("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}})
    lines += ev("message_stop", {})
    return lines


def install(monkeypatch) -> type[FakeApis]:
    """Replace httpx.AsyncClient for every provider module (one httpx module
    object, so one attribute) for one test."""
    import backend.providers.api_common as common
    FakeApis.reset()
    monkeypatch.setattr(common.httpx, "AsyncClient", FakeApis)
    return FakeApis


__all__ = ["ANTHROPIC_MODEL", "ANTHROPIC_SERVED", "API_KEY", "EMBED_KEY", "EMBED_MODEL", "EMBED_URL", "FINGERPRINT",
           "FakeApis", "OPENAI_EXTRACTOR", "OPENAI_MODEL", "OPENAI_SERVED", "answer_for", "answering_api",
           "embedding_for", "install"]
