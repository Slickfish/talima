"""SwapManager — Ollama unload/preload + UI notification.

Phase 6 ships the API only. The actual Ollama keep_alive flips are exercised
when the server runs against a live Ollama instance; tests do not require it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


def _ollama_url() -> str:
    """Read OLLAMA_URL at call time so .env loaded after import is honoured.
    The 11434 default is a fallback only — env is the single source of truth."""
    return os.environ.get("OLLAMA_URL", "http://localhost:11434")


@dataclass(frozen=True)
class SwapEvent:
    from_model: str
    to_model: str
    estimated_seconds: float
    reason: str


class SwapManager:
    """Tracks the currently-loaded large model and orchestrates unload/preload.

    Ollama's load behaviour is implicit: setting `keep_alive: 0` on a request
    unloads the named model; sending a small generate to a model loads it.
    OLLAMA_MAX_LOADED_MODELS=1 (arch §14.3) ensures only one large model
    occupies VRAM at a time.
    """

    def __init__(self, *, current_model: str | None = None):
        self.current_model = current_model

    def needs_swap(self, target: str) -> bool:
        return self.current_model is not None and self.current_model != target

    async def swap(
        self, target: str, *, estimated_seconds: float = 3.0, reason: str = ""
    ) -> SwapEvent:
        previous = self.current_model or ""
        if self.needs_swap(target):
            await self._unload(previous)
            await self._preload(target)
        self.current_model = target
        return SwapEvent(
            from_model=previous, to_model=target,
            estimated_seconds=estimated_seconds, reason=reason,
        )

    async def _unload(self, model: str) -> None:                # pragma: no cover
        if not model:
            return
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                await client.post(
                    f"{_ollama_url()}/api/generate",
                    json={"model": model, "keep_alive": 0, "prompt": ""},
                )
            except (httpx.HTTPError, OSError):
                pass

    async def _preload(self, model: str) -> None:               # pragma: no cover
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                await client.post(
                    f"{_ollama_url()}/api/generate",
                    json={"model": model, "keep_alive": "30m", "prompt": ""},
                )
            except (httpx.HTTPError, OSError):
                pass
