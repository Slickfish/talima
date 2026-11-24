"""Embeddings via Ollama nomic-embed-text (Capability.EMBEDDING).

When Ollama is reachable, embed_text() returns the live 768-dim vector via
POST /api/embeddings. When it is not (CI, offline dev), embed_text() falls
back to a deterministic hash-based 768-dim vector so that the rest of the
RAG pipeline + tests still exercise the right code paths.

Law IX: this module references the embedding model only via the Capability
enum. The exact name is resolved through ModelKernel.embedding_model().
"""
from __future__ import annotations

import hashlib
import logging
import os
import time

import httpx
import numpy as np

from backend.router.capabilities import Capability
from backend.router.model_kernel import ModelKernel
from backend.router.model_registry import default_registry

logger = logging.getLogger("nexus.embeddings")

FULL_DIM   = 768
COARSE_DIM = 256


class EmbeddingUnavailable(RuntimeError):
    """The provider could not embed, and the caller refuses the fallback.

    W.3 — raised only by `embed_via_provider(..., strict=True)`. Production
    never asks for this: a turn whose query embedding fails should still be
    answered from the lexical channel, and `/healthz.embedding_degraded` is
    how that is declared. **A MEASUREMENT IS THE OPPOSITE CASE.** The hash
    fallback carries no semantic signal at all — W.0 measured its cosine at
    0.766-0.995 over 400 unrelated text pairs — so a recall figure or a
    similarity rank computed from it is noise in the shape of a number, and
    the one thing it must not do is get printed next to the real ones.
    """


# P1/F-10 — degradation is never silent: the last fallback is recorded here
# and surfaced on the status path (GET /healthz → embedding_degraded; H.3b
# added /health as an alias because the UI had been polling that, and 404ing,
# since it shipped).
#
# W.3 — `fallbacks` is MONOTONE and `_mark_healthy` does not reset it.
# `degraded` alone answers "is it broken right now", and that was the wrong
# question for anything long-running: one `ReadTimeout` in the middle of
# indexing 800 beliefs sets the flag, the next success clears it, and the
# process ends reporting healthy while a scattering of the vectors it wrote
# are hash noise. "Is it broken now" and "did it break at any point during
# this work" are different facts and only the second one can invalidate a
# result already computed.
_DEGRADED: dict = {"degraded": False, "reason": "", "ts": 0.0,
                   "fallbacks": 0, "first_reason": ""}


def embedding_status() -> dict:
    """Read-only copy of the current embedding-degradation state."""
    return dict(_DEGRADED)


def _mark_degraded(reason: str, *, fell_back: bool = True) -> None:
    """Record a provider failure. `fell_back` says whether a hash vector was
    actually returned.

    W.3 — the two are not the same event and the log used to conflate them.
    Under `strict=True` nothing falls back: the call raises, so a line reading
    "using deterministic hash pseudo-embeddings" would be describing something
    that did not happen — the exact class of untrue status message this packet
    is about. `fallbacks` counts only real substitutions, because its job is
    to answer "is anything I computed built on a hash vector".
    """
    if not _DEGRADED["degraded"]:
        if fell_back:
            logger.warning(
                "embedding fallback engaged (%s) — using deterministic hash "
                "pseudo-embeddings until the provider recovers", reason)
        else:
            logger.warning(
                "embedding refused (%s) — the caller asked for strict "
                "embedding, so nothing was substituted and nothing was "
                "computed", reason)
    _DEGRADED.update(degraded=True, reason=reason, ts=time.time())
    if fell_back:
        _DEGRADED["fallbacks"] += 1
        if not _DEGRADED["first_reason"]:
            _DEGRADED["first_reason"] = reason


def _mark_healthy() -> None:
    """Recovery clears `degraded`. It deliberately does NOT clear `fallbacks`.

    A counter a recovery can reset cannot answer "was anything I computed
    built on a fallback vector", which is the only question a completed
    measurement can still act on.
    """
    if _DEGRADED["degraded"]:
        logger.info("embedding provider recovered — live embeddings restored")
    _DEGRADED.update(degraded=False, reason="", ts=time.time())


def _ollama_url() -> str:
    """Read OLLAMA_URL at call time so .env loaded after module import is
    honoured. 11434 default is a fallback only — env is the source of truth."""
    return os.environ.get("OLLAMA_URL", "http://localhost:11434")


def _resolve_embedding_model() -> str | None:
    profile = ModelKernel(default_registry).embedding_model()
    return profile.name if profile else None


async def embed_text(text: str, *, dim: int = FULL_DIM) -> list[float]:
    """Return a `dim`-dim embedding. Tries Ollama first; falls back offline.

    P1/F-10 — the fallback is flagged (WARNING log + embedding_status()),
    never silent."""
    model = _resolve_embedding_model()
    if model:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"{_ollama_url()}/api/embeddings",
                    json={"model": model, "prompt": text},
                )
                if r.status_code == 200:
                    vec = r.json().get("embedding") or []
                    if len(vec) >= dim:
                        _mark_healthy()
                        return list(vec[:dim])
                _mark_degraded(f"provider_status_{r.status_code}")
        except (httpx.HTTPError, OSError) as e:
            _mark_degraded(f"provider_unreachable:{type(e).__name__}")
    else:
        _mark_degraded("no_embedding_model_registered")
    return _offline_embed(text, dim=dim)


def embed_text_sync(text: str, *, dim: int = FULL_DIM) -> list[float]:
    """Synchronous wrapper. Always uses the offline fallback for determinism;
    callers that want live Ollama embeddings must use the async variant."""
    return _offline_embed(text, dim=dim)


async def embed_via_provider(provider, text: str, *,
                             dim: int = FULL_DIM,
                             kind: str = "document",
                             strict: bool = False) -> list[float]:
    """Embed through the APP'S provider, and flag it when that fails.

    W.0 measured that `embedding_degraded` on `/healthz` could never become
    true. `_mark_degraded` is only reachable from `embed_text()` above, and
    `embed_text()` has no callers — the live path in production is
    `provider.get_embedding`, called from `chroma.py`, which caught the
    exception and fell back to `_offline_embed` directly. So every embedding
    in the system could be a hash while the status surface stayed clean.

    A degradation flag that cannot fire is worse than no flag (I-6): it is a
    green light wired to nothing. This is the one function both live callers
    now go through, and it returns exactly what they returned before — the
    same fallback vector, on the same conditions — so nothing downstream sees
    a different number. Only the flag is new.

    W.2 — `kind` is "query" or "document", and the model's task prefix is
    applied HERE rather than inside each provider. Two reasons, both about
    keeping one place where a thing is true:

      - two providers means two implementations to drift apart, and the
        prefix contract is per-MODEL, not per-backend — `model_map` is where
        model names are allowed to live (Law 2);
      - adding a keyword to `LLMProvider.get_embedding` would break every
        stub in the suite that defines `async def get_embedding(self, text)`,
        and a signature change with that blast radius to prepend a string is
        the wrong trade.

    This is the single chokepoint for live embedding — the `add()` argument
    one layer down — so applying it here reaches every caller by
    construction. `tests/test_embed_prefix.py` asserts there is no second
    route to `provider.get_embedding`.

    W.3 — `strict=True` raises `EmbeddingUnavailable` instead of returning the
    fallback vector. **A MEASUREMENT MAY NOT FALL BACK.** Production is the
    other case and keeps the default: a turn whose query embedding times out
    should still be answered from the lexical channel, with the failure
    declared on `/healthz` rather than thrown at the user. But a script that
    computes a recall figure or a similarity rank has no such excuse — the
    hash fallback scores 0.766-0.995 between unrelated texts (W.0), so a
    number built on even a few of those vectors is noise wearing the shape of
    a measurement, and it will be quoted.

    Failing on the FIRST bad call also stops the wasted work: the alternative
    that shipped in W.2 embedded the whole store, checked a flag at the end,
    and could not detect a transient timeout at all, because the next
    successful call had already cleared it.
    """
    prefix = ""
    try:
        from backend.providers.model_map import embed_prefix, resolve
        prefix = embed_prefix(resolve("embed"), kind)
    except Exception:                                 # noqa: BLE001
        prefix = ""
    try:
        vec = await provider.get_embedding(prefix + text)
    except Exception as e:
        reason = f"provider_unreachable:{type(e).__name__}"
        _mark_degraded(reason, fell_back=not strict)
        if strict:
            raise EmbeddingUnavailable(
                f"{reason}: {e}. Refusing the hash fallback — it carries no "
                f"semantic signal (W.0: unrelated pairs score 0.766-0.995), "
                f"so anything computed from it would not be a measurement."
            ) from e
        return _offline_embed(text, dim=dim)
    if not vec:
        _mark_degraded("provider_returned_empty", fell_back=not strict)
        if strict:
            raise EmbeddingUnavailable(
                "provider_returned_empty. Refusing the hash fallback — see "
                "EmbeddingUnavailable.")
        return _offline_embed(text, dim=dim)
    _mark_healthy()
    # Length is NOT coerced here. `chroma.py` has always rejected a wrong-sized
    # vector with a ValueError and that is its call to make; silently
    # truncating would turn a provider misconfiguration into a subtly worse
    # index. This function reports health; it does not reshape data.
    return list(vec)


def embedder_declaration(provider) -> dict:
    """W3 (H5) — what the provider DECLARES about its embedder, read by the
    idle scans before they ask it to embed, and shown on `/cortex/stats`.

    `has_embedder` False (the stub; an api backend with `NEXUS_EMBED_URL` and
    `NEXUS_EMBED_MODEL` unset) with the reason the provider gives
    (`no_embedder_reason`); True for a provider that declares nothing, as
    every provider before W3 did. A declaration, never a probe: whether an
    embedder that is declared answers is `embed_via_provider`'s to find out
    and `/healthz` to say."""
    has = getattr(provider, "has_embedder", True) if provider is not None else True
    if has:
        return {"has_embedder": True, "reason": ""}
    reason = getattr(provider, "no_embedder_reason", None) or "the provider declares no embedder (has_embedder False)"
    return {"has_embedder": False, "reason": str(reason)}


def _offline_embed(text: str, *, dim: int = FULL_DIM) -> list[float]:
    """Deterministic, dimension-flexible pseudo-embedding.

    SHA-256 the text, expand the digest into a `dim`-length float vector, then
    L2-normalise. Same text → same vector → cosine similarity is meaningful
    enough to exercise the matryoshka pipeline without an LLM in the loop.
    """
    h = hashlib.sha256(text.encode("utf-8")).digest()
    needed = dim * 4
    repeats = (needed + len(h) - 1) // len(h)
    blob = (h * repeats)[:needed]
    arr = np.frombuffer(blob, dtype=np.uint8).astype(np.float32)
    arr = arr.reshape(dim, 4).sum(axis=1)
    norm = float(np.linalg.norm(arr)) or 1.0
    return (arr / norm).tolist()
