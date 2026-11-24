"""AnthropicProvider — `LLM_BACKEND=anthropic`: the Anthropic Messages API.
W3 part 2 (run/specs/W3.md D3).

Endpoints used, under `ANTHROPIC_BASE_URL` (default `https://api.anthropic.com`):
  POST /v1/messages   (streaming — SSE `event:`/`data:` pairs)
  GET  /v1/models     (the models the key may use + the health probe)
Headers: `x-api-key` and `anthropic-version` (ANTHROPIC_VERSION). The API
has no embeddings endpoint ("Anthropic does not offer its own embedding
model"), so the embedder is the one `NEXUS_EMBED_URL` and
`NEXUS_EMBED_MODEL` name, or none, declared (`has_embedder`).

The request's shape, and so `wire_body`'s (`replay_capture.SYSTEM_PLACEMENT`):
the API takes the system prompt as a top-level `system` string, never as a
message, so `_payload` moves NEXUS's one system message (the stable prefix
and the dynamic block; `assert_single_system_message` holds the request to
one) out of `messages` and into `system`. `max_tokens` is required and
NEXUS has no setting for an output cap, so the request's `max_tokens` is
the context size the caller stated (compaction's TOKEN_BUDGET, 8192, for an
answer; extraction's sized 4096): the one size NEXUS states, under Law 5's
ceiling, which refuses above it before any request leaves. A model whose
output limit is lower refuses the request by name (HTTP 400, `ApiRefused`).

What the record says under it:
- `model_id` is the model the API REPORTS in `message_start`
  (`message.model`), prefixed `anthropic:`.
- no seed (`takes_seed` False: the Messages API takes none): the record's
  `inference_seed` is NULL with W0b's reason.
- no digest: the API names no weights or fingerprint; the model id is the
  only identity it reports (absent with that reason).
- the options: the body minus messages and system (`request_config`).
- the environment: the endpoint's host and the `anthropic-version` sent.
- the reasoning: `thinking_delta` events, read when the API sends them
  (C-38's rule: read what is sent, ask for nothing; no `thinking` option is
  sent, so an answer carries reasoning only where the operator's gateway
  turns it on).
"""
from __future__ import annotations

import json
from typing import AsyncGenerator

import httpx

from backend.providers.api_common import ApiRefused, OpenAICompatEmbedder, api_model_registry, law_v, refusal
from backend.providers.api_settings import (ANTHROPIC_VERSION, ApiSettings, EmbedSettings, anthropic_settings,
                                            embed_settings)
from backend.providers.backends import ANTHROPIC
from backend.providers.base import LLMProvider, health_timeout, list_models_timeout, reply_separation_enabled

LABEL = "the Anthropic API"
MESSAGES_ENDPOINT, MODELS_ENDPOINT = "/v1/messages", "/v1/models"
NO_DIGEST = ("the Anthropic API names no weights or fingerprint; the model id it reports in message_start "
             "is the only identity it gives")
SYSTEM_JOIN = "\n\n"


def split_system(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """The system text the API takes at the top level, and the rest. Several
    system messages (none in NEXUS's requests) are joined, in order."""
    system = [str(m.get("content", "")) for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    return (SYSTEM_JOIN.join(system) if system else None), rest


def anthropic_event_pieces(event: dict, *, separate: bool) -> list[tuple[str, str]]:
    """C-38's shape for one streamed event: `message_start` names the model
    that answers (`("model", name)`); a `content_block_delta` carries a
    `text_delta` (content) or, when `separate`, a `thinking_delta`
    (reasoning). Every other event (`ping`, `content_block_start`,
    `signature_delta`, `message_delta`, `message_stop`) carries no text.
    Pure, so the parse is testable without the API."""
    kind = event.get("type")
    if kind == "message_start":
        model = (event.get("message") or {}).get("model")
        return [("model", model)] if isinstance(model, str) and model else []
    if kind != "content_block_delta":
        return []
    delta = event.get("delta") or {}
    if delta.get("type") == "text_delta":
        text = delta.get("text") or ""
        return [("content", text)] if text else []
    if separate and delta.get("type") == "thinking_delta":
        thinking = delta.get("thinking") or ""
        return [("thinking", thinking)] if thinking else []
    return []


class AnthropicProvider(LLMProvider):
    takes_seed = False
    model_id_prefix = ANTHROPIC

    def __init__(self, settings: ApiSettings | None = None, embed: EmbedSettings | None = None):
        self.settings = settings or anthropic_settings()
        self.BASE_URL = self.settings.base_url
        self.embed_settings = embed if embed is not None else embed_settings()
        self.embedder = OpenAICompatEmbedder(self.embed_settings) if self.embed_settings else None

    @property
    def has_embedder(self) -> bool:
        return self.embedder is not None

    @property
    def no_embedder_reason(self) -> str:
        return (f"LLM_BACKEND={ANTHROPIC} has no embedder: Anthropic offers no embedding model, and "
                f"NEXUS_EMBED_URL and NEXUS_EMBED_MODEL name none")

    def headers(self) -> dict:
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        if self.settings.api_key:
            headers["x-api-key"] = self.settings.api_key
        return headers

    @staticmethod
    def _payload(model: str, messages: list[dict], num_ctx: int) -> dict:
        """The /v1/messages body — the one builder, so `request_config`
        records exactly what `stream_pieces` sends, minus the request text."""
        law_v(num_ctx)
        system, rest = split_system(messages)
        body = {"model": model, "max_tokens": int(num_ctx), "messages": rest, "stream": True}
        if system is not None:
            body["system"] = system
        return body

    def request_config(self, model: str, num_ctx: int, *, separate: bool) -> dict:
        """W0b — the body minus the request's text: messages and the system
        string it was split from (`wire_body` splits it again from the kept
        messages, `SYSTEM_PLACEMENT`)."""
        body = self._payload(model, [], num_ctx)
        body.pop("messages")
        body.pop("system", None)
        return {"provider": ANTHROPIC, "endpoint": MESSAGES_ENDPOINT, **body}

    async def served_model_digest(self, model: str) -> tuple[str | None, str]:
        return None, NO_DIGEST

    async def inference_environment(self) -> dict:
        return {"anthropic_endpoint_host": self.settings.host, "anthropic_version": ANTHROPIC_VERSION,
                "model_server_version_absent": "the Anthropic API reports no server version"}

    async def stream_chat(
        self, model: str, messages: list[dict], num_ctx: int,
    ) -> AsyncGenerator[str, None]:
        async for source, text in self.stream_pieces(model, messages, num_ctx, separate=False):
            if source == "content":
                yield text

    async def stream_pieces(
        self, model: str, messages: list[dict], num_ctx: int, *, separate: bool | None = None,
    ) -> AsyncGenerator[tuple[str, str], None]:
        """Each event's pieces (`anthropic_event_pieces`); `message_stop`
        ends the stream. A status other than 2xx is `ApiRefused`."""
        if separate is None:
            separate = reply_separation_enabled()
        payload = self._payload(model, messages, num_ctx)
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{self.BASE_URL}{MESSAGES_ENDPOINT}", json=payload,
                                     headers=self.headers()) as r:
                if r.status_code >= 400:
                    raise await refusal(r, provider_label=LABEL, model=model)
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[len("data:"):].strip())
                    except json.JSONDecodeError:
                        continue
                    for source, text in anthropic_event_pieces(event, separate=separate):
                        yield source, text
                    if event.get("type") == "message_stop":
                        return

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


__all__ = ["ANTHROPIC_VERSION", "ApiRefused", "AnthropicProvider", "LABEL", "MESSAGES_ENDPOINT", "MODELS_ENDPOINT",
           "NO_DIGEST", "anthropic_event_pieces", "split_system"]
