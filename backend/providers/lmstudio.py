"""LMStudioProvider — wraps LM Studio's OpenAI-compatible HTTP API.

Endpoints used:
  POST /v1/chat/completions   (streaming chat — SSE-style "data: …" lines)
  POST /v1/embeddings         (single-text embedding)
  GET  /v1/models             (model list + health probe)
"""
from __future__ import annotations

import json
import os
from typing import AsyncGenerator

import httpx

from backend.providers.base import LLMProvider, health_timeout, list_models_timeout, reply_separation_enabled
from backend.providers.ollama import NUM_CTX


def lmstudio_delta_pieces(delta: dict, *, separate: bool) -> list[tuple[str, str]]:
    """C-38 — one streamed delta as (source, text) pieces: the reasoning
    field first (`reasoning_content`, else `reasoning`), then `content`.
    Pure, so the parse is testable without the app. With `separate` false
    only `content` is read, exactly as before C-38."""
    out: list[tuple[str, str]] = []
    if separate:
        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
        if reasoning:
            out.append(("thinking", reasoning))
    content = delta.get("content") or ""
    if content:
        out.append(("content", content))
    return out


class LMStudioProvider(LLMProvider):
    # The compiled fallback, and only that. The env var is the source of truth.
    DEFAULT_BASE_URL = "http://localhost:1234"

    def __init__(self):
        # Resolve at instance time, not import time, so a `.env` loaded after
        # Python startup is honoured — the same shape `OllamaProvider` has
        # used since Fix 0, and the reason this one was broken while that one
        # worked. `backend/server.py` imports its dependency graph at lines
        # 49-91 and calls `load_env_files()` at 109, so a class-body
        # `os.getenv` had already taken the default before the file was read.
        self.BASE_URL = os.getenv("LM_STUDIO_URL", self.DEFAULT_BASE_URL)

    # W0b — the OpenAI-compatible endpoint takes a top-level `seed`; the
    # answering path sends one drawn by `replay_capture.draw_seed()`. LM Studio
    # exposes no content digest, so the record's `model_digest` is absent with
    # that reason.
    takes_seed = True

    @staticmethod
    def _payload(model: str, messages: list[dict], num_ctx: int, *, seed: int | None = None) -> dict:
        """The /v1/chat/completions body — the one builder, so
        `request_config` records exactly what `stream_pieces` sends.

        W3 part 2 — Law 5's ceiling refuses here, as Ollama's `_payload` has
        always refused: the OpenAI-compatible API has no context-size field,
        so nothing is sent for `num_ctx`, but a caller asking above the
        ceiling is refused before any request leaves. Before W3 LM Studio
        accepted `num_ctx=100000` and sent nothing (the register line W0b's
        refutation readers left; W3's establish ran it)."""
        if num_ctx > NUM_CTX:
            raise ValueError(f"num_ctx {num_ctx} exceeds Law V ceiling {NUM_CTX}")
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if seed is not None:
            body["seed"] = int(seed)
        return body

    def request_config(self, model: str, num_ctx: int, *, separate: bool) -> dict:
        """W0b — the request body minus the messages and the seed, as sent.
        `num_ctx` is not sent to LM Studio (the app's setting governs it);
        above Law 5's ceiling it is refused (W3)."""
        body = self._payload(model, [], num_ctx)
        body.pop("messages")
        return {"provider": "lmstudio", "endpoint": "/v1/chat/completions", **body}

    async def stream_chat(
        self,
        model: str,
        messages: list[dict],
        num_ctx: int,
        *,
        seed: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """The content stream, exactly as before C-38: `delta.content` per
        SSE line and nothing else. `stream_pieces` is the separating path."""
        async for source, text in self.stream_pieces(model, messages, num_ctx, separate=False, seed=seed):
            if source == "content":
                yield text

    async def stream_pieces(
        self,
        model: str,
        messages: list[dict],
        num_ctx: int,
        *,
        separate: bool | None = None,
        seed: int | None = None,
    ) -> AsyncGenerator[tuple[str, str], None]:
        """C-38 — each delta's reasoning field, when LM Studio's own
        separation setting puts one there (`reasoning_content`, or
        `reasoning` for the models that use it), as `("thinking", text)`,
        and `delta.content` as `("content", text)`. Nothing is requested:
        the setting is the app's, not the request's. A tag pair arriving
        inline in `content` is handled one layer up by `ReplySeparator`,
        keyed per model. `separate=False` reads `content` only."""
        if separate is None:
            separate = reply_separation_enabled()
        # `num_ctx` is not sent (the API has no field for it; LM Studio's own
        # setting governs the context); Law 5's ceiling refuses it in
        # `_payload` before the request leaves (W3).
        payload = self._payload(model, messages, num_ctx, seed=seed)
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", f"{self.BASE_URL}/v1/chat/completions", json=payload,
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0] or {}).get("delta") or {}
                    for source, text in lmstudio_delta_pieces(delta, separate=separate):
                        yield source, text

    async def get_embedding(self, text: str) -> list[float]:
        from backend.providers.model_map import resolve
        model = resolve("embed", backend="lmstudio")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{self.BASE_URL}/v1/embeddings",
                json={"model": model, "input": text},
            )
            r.raise_for_status()
            data = r.json().get("data") or []
            if not data:
                return []
            return list(data[0].get("embedding") or [])

    async def list_models(self) -> list[str]:
        # R.2 — same split timeout as Ollama. LM Studio is not where the
        # measured cost was, but a refused connection costs the same
        # regardless of which port refused it.
        async with httpx.AsyncClient(timeout=list_models_timeout()) as client:
            r = await client.get(f"{self.BASE_URL}/v1/models")
            if r.status_code != 200:
                return []
            return [m.get("id", "") for m in r.json().get("data", [])]

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=health_timeout()) as client:
                r = await client.get(f"{self.BASE_URL}/v1/models")
                return r.status_code == 200
        except (httpx.HTTPError, OSError):
            return False
