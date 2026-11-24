"""W3 part 2 — what the two `api` providers share (run/specs/W3.md D3).

- `ApiRefused`: an endpoint's non-2xx answer, raised by name with the status
  and the start of the body (an API's error body is where the reason is: a
  refused key, an unknown model, a rate limit), never delivered as the
  model's answer and never a bare status.
- `OpenAICompatEmbedder`: the one embedder an api backend may have, an
  OpenAI-compatible `/embeddings` endpoint named apart from the chat
  endpoint (`NEXUS_EMBED_URL`, `NEXUS_EMBED_MODEL`, `NEXUS_EMBED_API_KEY`):
  Anthropic offers no embedding model, and an OpenAI-compatible chat
  endpoint need not serve one.
- `api_model_registry`: the roster the kernel routes on under an api
  backend. One answering model carrying every answering capability (the
  operator named one model; NEXUS claims nothing finer about it), the
  extraction model (the same profile, or its own when named apart) and the
  embedder's model when there is one. Every profile is installed: an API
  serves what it is asked for or refuses by name.
"""
from __future__ import annotations

import httpx

from backend.providers.api_settings import ApiSettings, EmbedSettings
from backend.providers.ollama import NUM_CTX

BODY_EXCERPT = 300


class ApiRefused(RuntimeError):
    """The endpoint answered the request with an error status."""

    def __init__(self, provider_label: str, status: int, body: str, *, model: str):
        self.status, self.body, self.model = status, body, model
        super().__init__(f"{provider_label} answered HTTP {status} for model {model!r}: "
                         f"{body or '(empty body)'}")


async def refusal(r: httpx.Response, *, provider_label: str, model: str) -> ApiRefused:
    """The named error for a streaming response that is not 2xx: its body is
    read (the stream was never iterated) and the first BODY_EXCERPT
    characters are the reason."""
    try:
        raw = await r.aread()
    except Exception:                                  # noqa: BLE001 — the status is still the fact
        raw = b""
    text = " ".join(raw.decode("utf-8", errors="replace").split())[:BODY_EXCERPT]
    return ApiRefused(provider_label, r.status_code, text, model=model)


def law_v(num_ctx: int) -> None:
    """Law 5's ceiling, the refusal Ollama's `_payload` has always made: an
    API takes no context-size parameter, so nothing is sent for it, but a
    caller asking above the ceiling is refused before any request leaves."""
    if num_ctx > NUM_CTX:
        raise ValueError(f"num_ctx {num_ctx} exceeds Law V ceiling {NUM_CTX}")


class OpenAICompatEmbedder:
    """POST `{url}/embeddings` with `{"model", "input"}`; the first row's
    vector. Raises on any failure (the chokepoint,
    `backend/vector/embeddings.py`, declares the fallback)."""

    def __init__(self, settings: EmbedSettings):
        self.settings = settings

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.settings.api_key}"} if self.settings.api_key else {}

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{self.settings.url}/embeddings",
                                  json={"model": self.settings.model, "input": text},
                                  headers=self.headers())
            r.raise_for_status()
            data = r.json().get("data") or []
            if not data:
                return []
            return list(data[0].get("embedding") or [])


def api_model_registry(settings: ApiSettings, embed: EmbedSettings | None, *, label: str):
    from backend.router.capabilities import Capability as C
    from backend.router.model_registry import ModelProfile, ModelRegistry
    answering = [C.GENERAL_REASONING, C.ADVANCED_REASONING, C.GRAPH_SYNTHESIS, C.CODE_GENERATION,
                 C.CODE_REVIEW, C.MULTIMODAL]
    registry = ModelRegistry()
    same_extractor = settings.extraction_model == settings.model
    registry.register(ModelProfile(
        name=settings.model, display_name=settings.model,
        capabilities=tuple(answering + ([C.FAST_EXTRACTION] if same_extractor else [])),
        vram_gb=0.0, tokens_per_sec=0.0, context_window=NUM_CTX, pull_size_gb=0.0, is_pulled=True,
        notes=f"{label}: the model the operator named ({settings.model_setting}); every answering "
              f"capability is claimed because nothing finer is known about it"))
    if not same_extractor:
        registry.register(ModelProfile(
            name=settings.extraction_model, display_name=settings.extraction_model,
            capabilities=(C.FAST_EXTRACTION,), vram_gb=0.0, tokens_per_sec=0.0, context_window=NUM_CTX,
            pull_size_gb=0.0, is_pulled=True, notes=f"{label}: the extraction model named apart"))
    if embed is not None and embed.model not in registry._models:
        registry.register(ModelProfile(
            name=embed.model, display_name=embed.model, capabilities=(C.EMBEDDING,), vram_gb=0.0,
            tokens_per_sec=0.0, context_window=NUM_CTX, pull_size_gb=0.0, is_pulled=True,
            notes=f"the embedder named by NEXUS_EMBED_URL and NEXUS_EMBED_MODEL ({embed.host})"))
    return registry


__all__ = ["ApiRefused", "BODY_EXCERPT", "OpenAICompatEmbedder", "api_model_registry", "law_v", "refusal"]
