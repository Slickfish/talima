"""OllamaProvider — wraps the local Ollama HTTP API.

Endpoints used:
  POST /api/chat       (streaming chat)
  POST /api/embeddings (single-text embedding)
  GET  /api/tags       (installed models — list_models + health probe)

NUM_CTX = 8192 (Law V — hard ceiling on RTX 3060). The backend.ollama_client
shim that re-exported it is retired (W1); import it from here.
"""
from __future__ import annotations

import json
import os
from typing import AsyncGenerator

import httpx

from backend.providers.base import LLMProvider, health_timeout, list_models_timeout, reply_separation_enabled

NUM_CTX = 8192   # Law V — hard ceiling

# W0b — Ollama's own name equality (types/model/name.go at f9323416: a name is
# merged with DefaultName() and compared field by field with EqualFold). A
# bare `qwen3` is `registry.ollama.ai/library/qwen3:latest`, and `Qwen3:Latest`
# is the same name. This is the daemon's rule, not a looser match: a
# different tag is a different name, and a model family never matches.
OLLAMA_DEFAULT_HOST, OLLAMA_DEFAULT_NAMESPACE, OLLAMA_DEFAULT_TAG = "registry.ollama.ai", "library", "latest"


def ollama_name_key(name: str | None) -> tuple[str, str, str, str] | None:
    """(host, namespace, model, tag), defaults filled and case folded, or
    None for an empty name. The tag is the text after the last `:` that
    follows the last `/`, so a host's port (`localhost:5000/...`) is never
    read as a tag."""
    s = (name or "").strip().split("@", 1)[0]
    if not s:
        return None
    tag = OLLAMA_DEFAULT_TAG
    colon, slash = s.rfind(":"), s.rfind("/")
    if colon > slash:
        s, tag = s[:colon], (s[colon + 1:] or OLLAMA_DEFAULT_TAG)
    parts = [p for p in s.split("/") if p]
    if not parts:
        return None
    if len(parts) == 1:
        host, ns, model = OLLAMA_DEFAULT_HOST, OLLAMA_DEFAULT_NAMESPACE, parts[0]
    elif len(parts) == 2:
        host, ns, model = OLLAMA_DEFAULT_HOST, parts[0], parts[1]
    else:
        host, ns, model = parts[0], "/".join(parts[1:-1]), parts[-1]
    return (host.casefold(), ns.casefold(), model.casefold(), tag.casefold())


def ollama_chunk_pieces(chunk: dict, *, separate: bool) -> list[tuple[str, str]]:
    """C-38 — one /api/chat NDJSON chunk as (source, text) pieces, in the
    order the daemon lays them out: `message.thinking` first, then
    `message.content` (a thinking chunk carries `content: ""`; the final
    `done` chunk may carry both). Pure, so the parse is testable without a
    daemon. With `separate` false (the pre-C-38 path and the rollback) only
    `message.content` is read — a thinking field the daemon sends anyway
    is dropped, exactly as it always was."""
    msg = chunk.get("message") or {}
    out: list[tuple[str, str]] = []
    if separate:
        thinking = msg.get("thinking") or ""
        if thinking:
            out.append(("thinking", thinking))
    content = msg.get("content") or ""
    if content:
        out.append(("content", content))
    return out


class OllamaProvider(LLMProvider):
    # W0b — the daemon takes a per-request `options.seed`; the answering path
    # sends one drawn by `replay_capture.draw_seed()` (see that module).
    takes_seed = True

    def __init__(self):
        # Resolve at instance time, not import time, so .env loaded after
        # Python startup is honoured. The 11434 default is a fallback only —
        # the env var is the single source of truth.
        self.BASE_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self._digests: dict[str, tuple[str | None, str]] = {}
        self._environment: dict | None = None

    def _payload(self, model: str, messages: list[dict], num_ctx: int, *,
                 separate: bool, seed: int | None = None) -> dict:
        """The /api/chat body — the one builder, so `request_config` records
        exactly what `stream_pieces` sends."""
        from backend.providers.model_map import think_option
        think = separate and think_option(model)
        # `num_ctx` has NO default. It defaulted to the ceiling, so every
        # caller that did not think about context asked for the maximum;
        # the answering path inherited 8192 by omission and extraction
        # wrote it as a literal. The ceiling refuses; it does not supply.
        if num_ctx > NUM_CTX:
            raise ValueError(f"num_ctx {num_ctx} exceeds Law V ceiling {NUM_CTX}")
        options: dict = {"num_ctx": num_ctx}
        if seed is not None:
            options["seed"] = int(seed)
        return {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": options,
            **({"think": True} if think else {}),
        }

    def request_config(self, model: str, num_ctx: int, *, separate: bool) -> dict:
        """W0b — the request body minus the messages and the seed, as sent."""
        body = self._payload(model, [], num_ctx, separate=separate)
        body.pop("messages")
        return {"provider": "ollama", "endpoint": "/api/chat", **body}

    async def served_model_digest(self, model: str) -> tuple[str | None, str]:
        """W0b — the content digest `/api/tags` reports for the tag, read the
        first time the tag serves in this process (cached per tag, so a model
        change reads it afresh; a re-pull or an `ollama cp` under a running
        process is not seen until then). The row is found by Ollama's own
        name equality (`ollama_name_key`): defaults filled, case folded, every
        field equal. A tag the daemon does not list is absent with that reason,
        never matched loosely."""
        if model in self._digests:
            return self._digests[model]
        try:
            async with httpx.AsyncClient(timeout=list_models_timeout()) as client:
                r = await client.get(f"{self.BASE_URL}/api/tags")
            if r.status_code != 200:
                return None, f"/api/tags answered {r.status_code}"
            rows = r.json().get("models", [])
        except (httpx.HTTPError, OSError, ValueError) as e:
            return None, f"/api/tags unavailable: {type(e).__name__}"
        want = ollama_name_key(model)
        for row in rows:
            names = {ollama_name_key(row.get("name")), ollama_name_key(row.get("model"))}
            if want is not None and want in names and row.get("digest"):
                self._digests[model] = (str(row["digest"]), "")
                return self._digests[model]
        # Not cached: an absent tag is the state `ollama pull` (which the
        # 404 path tells the operator to run) ends, and a cached negative
        # would carry a false reason on every record until restart.
        return None, "tag not listed by /api/tags"

    async def inference_environment(self) -> dict:
        """W0b — the daemon's version from `/api/version`, once per process."""
        if self._environment is None:
            try:
                async with httpx.AsyncClient(timeout=list_models_timeout()) as client:
                    r = await client.get(f"{self.BASE_URL}/api/version")
                version = r.json().get("version") if r.status_code == 200 else None
            except (httpx.HTTPError, OSError, ValueError) as e:
                return {"ollama_version_absent": f"/api/version unavailable: {type(e).__name__}"}
            if not version:
                # Not cached, as an absent tag is not (part 5): a daemon that
                # answered 503 while loading answers 200 a turn later.
                return {"ollama_version_absent": f"/api/version answered {r.status_code}"}
            self._environment = {"ollama_version": version}
        return dict(self._environment)

    async def stream_chat(
        self,
        model: str,
        messages: list[dict],
        num_ctx: int,
        *,
        seed: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """The content stream, exactly as before C-38: `message.content` per
        chunk and nothing else. `stream_pieces` is the separating path."""
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
        """C-38 — every chunk's two fields, each as its kind: `("thinking",
        text)` from `message.thinking`, `("content", text)` from
        `message.content`. The daemon separates a thinking model's reasoning
        into `message.thinking` by default since v0.12.4 (the operator's 4
        October check saw it without asking), and since v0.12.5 it answers a
        `think: true` for a manifest without the capability with a 400 — so
        the request carries the option only for the models
        `model_map.THINK_OPTION` names, which is none, and reads the field
        whenever it is sent. A daemon that still puts the tag pair inline in
        `content` is handled one layer up by `ReplySeparator`, keyed per
        model. `separate=False` (the pre-C-38 `stream_chat` path, and the
        rollback) reads `content` only and sends no option. `seed` (W0b) is
        sent as `options.seed` when given."""
        if separate is None:
            separate = reply_separation_enabled()
        payload = self._payload(model, messages, num_ctx, separate=separate, seed=seed)
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", f"{self.BASE_URL}/api/chat", json=payload,
            ) as r:
                # Graceful 404/5xx — yield a done+error JSON token instead of
                # raising into the SSE stream. The consumer (agentic_loop)
                # still receives a string, so the contract is preserved.
                if r.status_code == 404:
                    error_msg = (
                        f"Model not found in Ollama: '{model}'. "
                        f"Fix: ollama pull {model}"
                    )
                    yield "content", json.dumps({"event": "done", "error": error_msg,
                                                 "full_response": error_msg})
                    return
                if r.status_code >= 400:
                    error_msg = f"Ollama error {r.status_code} for model '{model}'"
                    yield "content", json.dumps({"event": "done", "error": error_msg,
                                                 "full_response": error_msg})
                    return
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for source, text in ollama_chunk_pieces(chunk, separate=separate):
                        yield source, text
                    if chunk.get("done"):
                        return

    async def get_embedding(self, text: str) -> list[float]:
        from backend.providers.model_map import resolve
        model = resolve("embed", backend="ollama")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{self.BASE_URL}/api/embeddings",
                json={"model": model, "prompt": text},
            )
            r.raise_for_status()
            vec = r.json().get("embedding") or []
            return list(vec)

    async def list_models(self) -> list[str]:
        """Installed models, per the ABC's contract (R.3).

        Was `/api/ps`, which lists models currently resident in VRAM. That is
        not what any caller wanted: `sync_pulled_state` sets `is_pulled`, whose
        own docstring describes `ollama list`, and `/model/override` validates
        a user-supplied name against the result. On a box that has just
        booted, nothing is resident, so both read an empty list and the probe
        cost five seconds of startup to learn nothing. `health()` two methods
        below has always used the right endpoint.
        """
        async with httpx.AsyncClient(timeout=list_models_timeout()) as client:
            r = await client.get(f"{self.BASE_URL}/api/tags")
            if r.status_code != 200:
                return []
            # /api/tags rows carry both `name` and `model` (same tag string in
            # every Ollama build to date); `name` is what /api/ps used, so the
            # shape the callers see is unchanged.
            return [m.get("name", "") for m in r.json().get("models", [])]

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=health_timeout()) as client:
                r = await client.get(f"{self.BASE_URL}/api/tags")
                return r.status_code == 200
        except (httpx.HTTPError, OSError):
            return False
