"""OpenAICompatProvider — `LLM_BACKEND=openai`: an OpenAI-compatible
chat-completions endpoint (OpenAI itself, or any server speaking its API:
vLLM, llama.cpp's server, a gateway). W3 part 2 (run/specs/W3.md D3).

Endpoints used, under `OPENAI_BASE_URL` (default `https://api.openai.com/v1`):
  POST /chat/completions   (streaming chat — SSE "data: …" lines)
  GET  /models             (installed models + the health probe)
The embedder, when one is named, is `api_common.OpenAICompatEmbedder` at
`NEXUS_EMBED_URL` (`/embeddings`), never assumed to be this endpoint.

What the record says under it (W0b's capture, declared where it cannot be
observed):
- `model_id` is the model the endpoint REPORTS in its stream (`model` on
  every chunk: `gpt-4o-2024-08-06` for a request naming `gpt-4o`), prefixed
  `openai:` (`model_id_prefix`), so a record made against an API can never
  be read as a local model's. The stream reports it as `("model", name)`
  through `stream_pieces`; `ReplyStream` keeps it and the loop names the
  record with it.
- the seed: `takes_seed`, sent as the top-level `seed` (deprecated and
  best-effort in OpenAI's own API; the OpenAI-compatible servers honour it
  as LM Studio does).
- the digest: `system_fingerprint`, OpenAI's fingerprint of the backend
  configuration the model ran with ("can be used with seed to understand
  when backend changes have been made"), read from the stream that answered;
  absent with its reason when the endpoint reports none.
- the options: the body minus messages and seed (`request_config`), as sent.
- the environment: the endpoint's HOST (never the URL as set: it could carry
  credentials) and that no server version is reported.
- the key: read by `api_settings` alone, sent as `Authorization: Bearer`,
  never recorded (headers are not the body).

Law 5: an OpenAI-compatible endpoint takes no context-size parameter, so
nothing is sent for it; a `num_ctx` above the ceiling is refused before any
request leaves (`api_common.law_v`). A thinking model's reasoning arrives
under the vendor extension's two names (`reasoning_content`, `reasoning`),
read by LM Studio's own delta parser (`lmstudio_delta_pieces`: one
implementation of the OpenAI-compatible delta).
"""
from __future__ import annotations

import json
from typing import AsyncGenerator

import httpx

from backend.providers.api_common import ApiRefused, OpenAICompatEmbedder, api_model_registry, law_v, refusal
from backend.providers.api_settings import ApiSettings, EmbedSettings, embed_settings, openai_settings
from backend.providers.backends import OPENAI
from backend.providers.base import LLMProvider, health_timeout, list_models_timeout, reply_separation_enabled
from backend.providers.lmstudio import lmstudio_delta_pieces

LABEL = "the OpenAI-compatible endpoint"
CHAT_ENDPOINT, MODELS_ENDPOINT = "/chat/completions", "/models"
NO_FINGERPRINT = ("the endpoint reported no system_fingerprint (OpenAI's fingerprint of the backend "
                  "configuration; the one identity beyond the model's name an OpenAI-compatible endpoint "
                  "reports)")

# The OpenAI-compatible delta, under both names of the reasoning extension.
openai_delta_pieces = lmstudio_delta_pieces


def openai_chunk_pieces(chunk: dict, *, separate: bool) -> list[tuple[str, str]]:
    """C-38's shape for one chat-completions chunk: `("model", name)` when
    the chunk names the model that answered, then the first choice's delta
    as `("thinking", …)` and `("content", …)` pieces. Pure, so the parse is
    testable without an endpoint."""
    out: list[tuple[str, str]] = []
    model = chunk.get("model")
    if isinstance(model, str) and model:
        out.append(("model", model))
    choices = chunk.get("choices") or []
    if choices:
        delta = (choices[0] or {}).get("delta") or {}
        out += openai_delta_pieces(delta, separate=separate)
    return out


class OpenAICompatProvider(LLMProvider):
    takes_seed = True
    model_id_prefix = OPENAI

    def __init__(self, settings: ApiSettings | None = None, embed: EmbedSettings | None = None):
        # Resolved at instance time, as every provider's settings are, so a
        # `.env` loaded after Python startup is honoured.
        self.settings = settings or openai_settings()
        self.BASE_URL = self.settings.base_url
        self.embed_settings = embed if embed is not None else embed_settings()
        self.embedder = OpenAICompatEmbedder(self.embed_settings) if self.embed_settings else None
        self._fingerprints: dict[str, str] = {}

    # ---------- declarations the chokepoints read ----------

    @property
    def has_embedder(self) -> bool:
        return self.embedder is not None

    @property
    def no_embedder_reason(self) -> str:
        return (f"LLM_BACKEND={OPENAI} names no embedder: NEXUS_EMBED_URL and NEXUS_EMBED_MODEL are unset "
                f"(an OpenAI-compatible chat endpoint need not serve one)")

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.settings.api_key}"} if self.settings.api_key else {}

    # ---------- the request ----------

    @staticmethod
    def _payload(model: str, messages: list[dict], num_ctx: int, *, seed: int | None = None) -> dict:
        """The /chat/completions body — the one builder, so `request_config`
        records exactly what `stream_pieces` sends. `num_ctx` is refused above
        Law 5's ceiling and otherwise not sent (the API has no such field)."""
        law_v(num_ctx)
        body = {"model": model, "messages": messages, "stream": True}
        if seed is not None:
            body["seed"] = int(seed)
        return body

    def request_config(self, model: str, num_ctx: int, *, separate: bool) -> dict:
        """W0b — the request body minus the messages and the seed, as sent."""
        body = self._payload(model, [], num_ctx)
        body.pop("messages")
        return {"provider": OPENAI, "endpoint": CHAT_ENDPOINT, **body}

    async def served_model_digest(self, model: str) -> tuple[str | None, str]:
        """W0b — the `system_fingerprint` the stream that answered reported
        for this model, else absent with its reason. Read per stream, so it is
        this answer's, never a cached one's."""
        fingerprint = self._fingerprints.get(model)
        return (fingerprint, "") if fingerprint else (None, NO_FINGERPRINT)

    async def inference_environment(self) -> dict:
        return {"openai_endpoint_host": self.settings.host,
                "model_server_version_absent": "an OpenAI-compatible endpoint reports no server version"}

    async def stream_chat(
        self, model: str, messages: list[dict], num_ctx: int, *, seed: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """The content stream only (the pre-C-38 shape); `stream_pieces` is
        the separating path."""
        async for source, text in self.stream_pieces(model, messages, num_ctx, separate=False, seed=seed):
            if source == "content":
                yield text

    async def stream_pieces(
        self, model: str, messages: list[dict], num_ctx: int, *,
        separate: bool | None = None, seed: int | None = None,
    ) -> AsyncGenerator[tuple[str, str], None]:
        """Each chunk's pieces (`openai_chunk_pieces`): the model that
        answered, the reasoning under either of the extension's names when
        `separate`, and the content. A status other than 2xx is `ApiRefused`,
        named, never delivered as the answer."""
        if separate is None:
            separate = reply_separation_enabled()
        payload = self._payload(model, messages, num_ctx, seed=seed)
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{self.BASE_URL}{CHAT_ENDPOINT}", json=payload,
                                     headers=self.headers()) as r:
                if r.status_code >= 400:
                    raise await refusal(r, provider_label=LABEL, model=model)
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
                    fingerprint = chunk.get("system_fingerprint")
                    if isinstance(fingerprint, str) and fingerprint:
                        self._fingerprints[model] = fingerprint
                    for source, text in openai_chunk_pieces(chunk, separate=separate):
                        yield source, text

    # ---------- the rest of the contract ----------

    async def get_embedding(self, text: str) -> list[float]:
        if self.embedder is None:
            raise NotImplementedError(f"{self.no_embedder_reason}; the embedding chokepoint declares the "
                                      f"fallback on /healthz")
        return await self.embedder.embed(text)

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=list_models_timeout()) as client:
            r = await client.get(f"{self.BASE_URL}{MODELS_ENDPOINT}", headers=self.headers())
            if r.status_code != 200:
                return []
            return [m.get("id", "") for m in r.json().get("data", [])]

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=health_timeout()) as client:
                r = await client.get(f"{self.BASE_URL}{MODELS_ENDPOINT}", headers=self.headers())
                return r.status_code == 200
        except (httpx.HTTPError, OSError):
            return False

    def model_registry(self):
        return api_model_registry(self.settings, self.embed_settings, label=LABEL)


__all__ = ["ApiRefused", "CHAT_ENDPOINT", "LABEL", "MODELS_ENDPOINT", "NO_FINGERPRINT", "OpenAICompatProvider",
           "openai_chunk_pieces", "openai_delta_pieces"]
