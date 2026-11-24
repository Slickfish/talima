"""LLMProvider abstract base.

Every concrete provider (Ollama, LM Studio, …) implements the four async
methods below. The agentic loop, vector store, and KAIROS daemon depend
on this interface — never on a concrete provider class.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator

import httpx

# ---------- Packet R.2 — model-listing timeouts. PROVISIONAL. ----------
#
# `list_models()` used one number for connect and read. R.0 measured where the
# time actually goes when no daemon is listening: a refused connection costs
# ~2ms in a Linux sandbox and ~2.45s on the operator's Windows box, and every
# one of those seconds is spent in CONNECT. A flat 5.0s covers the wrong half.
#
# Connect and read are different clocks. A daemon that is up completes the TCP
# handshake immediately even while it is busy paging a 9GB model into VRAM —
# that is what the read timeout is for. So the connect budget can be small
# without making the probe fragile, and shrinking it is what turns a refusal
# from seconds into milliseconds.
#
# Both numbers are provisional and both are env-tunable. Setting
# NEXUS_PROBE_CONNECT_TIMEOUT=5.0 restores the flat behaviour exactly.
DEFAULT_PROBE_CONNECT_TIMEOUT = 0.25     # PROVISIONAL
DEFAULT_LIST_MODELS_TIMEOUT   = 5.0      # unchanged from before R.2


def probe_connect_timeout() -> float:
    """Connect budget for model-listing calls, from the env at call time.

    Read per call rather than at import so a `.env` loaded after Python
    startup is honoured — the same reason `OllamaProvider` resolves BASE_URL
    in `__init__`. A value that will not parse falls back to the default; it
    does not raise, because a malformed tuning knob must not stop the app
    from starting.
    """
    raw = os.getenv("NEXUS_PROBE_CONNECT_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_PROBE_CONNECT_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_PROBE_CONNECT_TIMEOUT
    return value if value > 0 else DEFAULT_PROBE_CONNECT_TIMEOUT


def list_models_timeout() -> httpx.Timeout:
    """Split timeout for `list_models()` — short connect, unchanged read."""
    return httpx.Timeout(DEFAULT_LIST_MODELS_TIMEOUT,
                         connect=probe_connect_timeout())


DEFAULT_HEALTH_TIMEOUT = 2.0                 # unchanged: the flat value every health() had


def health_timeout() -> httpx.Timeout:
    """Split timeout for every provider's `health()`, which `/healthz` awaits
    on every call: the same short connect as `list_models()`, the read
    unchanged. A flat 2.0 s let a dead model server cost `/healthz` the whole
    two seconds wherever a connect to a closed port is dropped rather than
    refused (Windows: R.2 measured 2.45 s per probe there against 2 ms on
    Linux), so a health poll with a two-second budget never saw an answer
    (W3's V3 on CI's Windows runner)."""
    return httpx.Timeout(DEFAULT_HEALTH_TIMEOUT, connect=probe_connect_timeout())


def num_ctx_for(prompt_chars: int, *, ceiling: int = 8192) -> int:
    """A SIZED context for a prompt of `prompt_chars`: the project's token
    estimate, doubled (the char/4 estimate undercounts dense text, and the
    output has to fit too — an unmeasured margin, stated as one), rounded
    up to a power of two so a runner's size is stable across calls, and
    never above the ceiling. Callers that must keep one runner resident
    size from their CAP, not their text (extraction does)."""
    from backend.loop.compaction import estimate_tokens_text
    need = 2 * estimate_tokens_text("x" * max(0, int(prompt_chars)))
    size = 1
    while size < need:
        size *= 2
    return min(size, ceiling)


class LLMProvider(ABC):
    @abstractmethod
    async def stream_chat(
        self, model: str, messages: list[dict], num_ctx: int,
    ) -> AsyncGenerator[str, None]:
        """Yield response tokens as they arrive. Stop when the backend signals
        completion. Caller never sees raw chunk dicts or transport details.

        `num_ctx` is REQUIRED (no default). Law 5's ceiling refuses what is
        too large; it does not supply a value. Every caller states its
        size — the answering path from compaction's TOKEN_BUDGET, extraction
        from its input cap — and a sentinel derived from the call sites
        fails on any site that omits it."""
        # Pragma: this is an abstract async generator; subclasses implement.
        if False:                                              # pragma: no cover
            yield ""

    @abstractmethod
    async def get_embedding(self, text: str) -> list[float]:
        """Return the embedding vector for `text` from the backend's
        embedding model. Raises on transport failure — callers handle fallback."""

    @abstractmethod
    async def list_models(self) -> list[str]:
        """Return the model names the backend has INSTALLED and can run.

        Packet R.3 — "loaded / available" is what this said, and the slash was
        load-bearing: `LMStudioProvider` returned installed models (`/v1/models`)
        while `OllamaProvider` returned models resident in VRAM (`/api/ps`).
        Two implementations of one method meaning two different things is a
        contract that isn't one, and the caller that suffered for it is the
        startup probe — on a freshly-booted box nothing is resident, so it
        blocked startup to fetch an empty list.

        Installed, not resident. A model that would have to be paged into VRAM
        to answer is still available; a backend that is down returns []."""

    @abstractmethod
    async def health(self) -> bool:
        """True iff the backend is reachable and responding. Never raises."""


# --------------------------------------------------------------------------
# C-38 — the structured reply at the provider boundary
# --------------------------------------------------------------------------
# A thinking model's output is two texts: the reasoning it produced for itself
# and the answer it stated. Before C-38 every consumer of the model's output —
# history, extraction, the citation detector, the record's hash, the console
# stream — received whatever the daemon put in `content`, so a reasoning span
# that arrived inline became claims, cited flags and history. The boundary now
# returns {answer, reasoning, raw}: `answer` is what every consumer receives,
# `reasoning` is read by the record and nothing else (an AST guard holds
# that), and `raw` is the content stream exactly as the daemon delivered it,
# which is what `response_hash` has always covered.
#
# Separation happens IN THE STREAM. Where the daemon already separates
# (Ollama's `think` option: `message.thinking` chunks before `message.content`
# chunks), the provider yields each as its kind. Where the model's tag pair
# arrives inline in `content`, `ReplySeparator` — a per-model state machine
# keyed on `model_map.think_tags` — holds back at most len(tag) - 1
# characters while a partial tag could be forming and emits everything else
# as it arrives, so the console still streams. A model with no tag pair has
# its content left untouched.
#
# NEXUS_REPLY_SEPARATION=off is the rollback: no `think` option is sent and
# nothing is parsed — the pre-C-38 path byte for byte (a separate thinking
# field the daemon sends anyway is dropped, as it always was).

ANSWER, REASONING = "answer", "reasoning"


def reply_separation_enabled() -> bool:
    return os.environ.get("NEXUS_REPLY_SEPARATION", "on").strip().lower() != "off"


@dataclass(frozen=True)
class ReplyChunk:
    kind: str        # ANSWER | REASONING
    text: str


@dataclass(frozen=True)
class Reply:
    """The structured reply, assembled by the loop from the chunks."""
    answer: str
    reasoning: str | None
    raw: str
    unclosed: bool = False       # the stream ended inside the reasoning

    @property
    def answer_empty(self) -> bool:
        """The declared state: the reply carried reasoning and no stated
        answer — never a reason to extract from the reasoning."""
        return not self.answer.strip() and bool(self.reasoning)

    @property
    def answer_empty_reason(self) -> str | None:
        if not self.answer_empty:
            return None
        return ("the reply was cut off inside its thinking" if self.unclosed
                else "the reply contained only reasoning")


class ReplySeparator:
    """A streaming state machine for one model's tag pair.

    `feed(text)` returns the chunks it can emit now; `flush()` returns what
    it held back at the end. Outside a span it holds back only a suffix that
    is a proper prefix of the open tag (so a tag split across two tokens is
    never emitted as answer text); inside a span, likewise for the close tag.
    Text after a close tag has its leading whitespace dropped — the newline
    the model puts between its reasoning and its answer is part of the tag's
    layout, not of either text. A separator with no tag pair passes every
    character through as answer, untouched."""

    def __init__(self, tags: tuple[str, str] | None):
        self.open, self.close = tags if tags else (None, None)
        self.buffer = ""
        self.in_reasoning = False
        self._skip_ws = False
        self.spans = 0

    @staticmethod
    def _held_suffix(buf: str, tag: str) -> int:
        """Length of the longest suffix of `buf` that is a proper prefix of `tag`."""
        for k in range(min(len(buf), len(tag) - 1), 0, -1):
            if tag.startswith(buf[-k:]):
                return k
        return 0

    def _emit_answer(self, text: str) -> list[ReplyChunk]:
        if self._skip_ws:
            stripped = text.lstrip()
            if not stripped:
                return []
            self._skip_ws = False
            text = stripped
        return [ReplyChunk(ANSWER, text)] if text else []

    def feed(self, text: str) -> list[ReplyChunk]:
        if self.open is None:
            return [ReplyChunk(ANSWER, text)] if text else []
        self.buffer += text
        out: list[ReplyChunk] = []
        while self.buffer:
            if not self.in_reasoning:
                i = self.buffer.find(self.open)
                if i >= 0:
                    out += self._emit_answer(self.buffer[:i])
                    self.buffer = self.buffer[i + len(self.open):]
                    self.in_reasoning = True
                    self.spans += 1
                    continue
                k = self._held_suffix(self.buffer, self.open)
                out += self._emit_answer(self.buffer[:len(self.buffer) - k] if k else self.buffer)
                self.buffer = self.buffer[len(self.buffer) - k:] if k else ""
                break
            j = self.buffer.find(self.close)
            if j >= 0:
                if j:
                    out.append(ReplyChunk(REASONING, self.buffer[:j]))
                self.buffer = self.buffer[j + len(self.close):]
                self.in_reasoning = False
                self._skip_ws = True
                continue
            k = self._held_suffix(self.buffer, self.close)
            head = self.buffer[:len(self.buffer) - k] if k else self.buffer
            if head:
                out.append(ReplyChunk(REASONING, head))
            self.buffer = self.buffer[len(self.buffer) - k:] if k else ""
            break
        return out

    def flush(self) -> list[ReplyChunk]:
        rest, self.buffer = self.buffer, ""
        if not rest:
            return []
        if self.in_reasoning:
            return [ReplyChunk(REASONING, rest)]
        return self._emit_answer(rest)


class ReplyStream:
    """The chokepoint every answering call goes through: iterate it for
    ReplyChunks; afterwards `raw` is the content stream as delivered and
    `unclosed` says whether it ended inside a reasoning span.

    A provider that separates natively implements `stream_pieces(model,
    messages, num_ctx)` yielding `(source, text)` with source `content` or
    `thinking`; any other provider (LM Studio, every stub) is wrapped through
    `stream_chat`, so one implementation of the separation remains."""

    def __init__(self, provider, model: str, messages: list[dict], *, num_ctx: int):
        self.provider, self.model, self.messages, self.num_ctx = provider, model, messages, num_ctx
        self.raw_parts: list[str] = []
        self.unclosed = False
        self.separated = reply_separation_enabled()
        # W0b — the replay capture: the seed sent (or why none was), then,
        # after the stream, the options, the digest and the environment.
        self.capture = None
        self._seed, self._notes = None, {}
        # W3 — the model the provider REPORTS answered (`("model", name)`
        # pieces from the api providers), for the record's `model_id`; None
        # for a provider that reports none (Ollama, LM Studio, the stub).
        self.served_model: str | None = None

    @property
    def raw(self) -> str:
        return "".join(self.raw_parts)

    def _draw_seed(self) -> None:
        from backend.providers.replay_capture import draw_seed, seed_enabled
        if not seed_enabled():
            self._notes["inference_seed"] = "seed off (NEXUS_INFERENCE_SEED=off)"
        elif not getattr(self.provider, "takes_seed", False):
            self._notes["inference_seed"] = "provider takes no seed"
        else:
            self._seed = draw_seed()

    async def _pieces(self):
        # W0b — the seed rides only to a provider that declares `takes_seed`;
        # every other provider (LM-Studio-less stubs, the rigs) is called
        # exactly as before.
        extra = {"seed": self._seed} if self._seed is not None else {}
        native = getattr(self.provider, "stream_pieces", None)
        if native is not None and self.separated:
            async for source, text in native(self.model, self.messages, self.num_ctx, **extra):
                yield source, text
            return
        async for tok in self.provider.stream_chat(self.model, self.messages, num_ctx=self.num_ctx, **extra):
            yield "content", tok

    async def __aiter__(self):
        from backend.providers.model_map import think_tags
        from backend.providers.replay_capture import build_capture
        self._draw_seed()
        sep = ReplySeparator(think_tags(self.model) if self.separated else None)
        async for source, text in self._pieces():
            if not text:
                continue
            if source == "model":
                # W3 — the served model's name: neither content nor reasoning,
                # so it reaches no consumer and no hash; the loop reads it.
                self.served_model = text
                continue
            if source == "thinking":
                yield ReplyChunk(REASONING, text)
                continue
            self.raw_parts.append(text)
            for chunk in sep.feed(text):
                yield chunk
        for chunk in sep.flush():
            yield chunk
        self.unclosed = sep.in_reasoning
        self.capture = await build_capture(self.provider, self.model, self.num_ctx,
                                           separate=self.separated, seed=self._seed,
                                           notes=self._notes)


def reply_stream(provider, model: str, messages: list[dict], *, num_ctx: int) -> ReplyStream:
    return ReplyStream(provider, model, messages, num_ctx=num_ctx)


async def collect_reply(provider, model: str, messages: list[dict], *,
                        num_ctx: int) -> "tuple[Reply, object]":
    """`reply_stream` driven to its end, for a caller that needs the whole
    reply and streams nothing (W1: the scripts that took a model's text from
    `stream_chat` directly, so an inline reasoning span became their answer).

    The assembly is the loop's own (`run_turn`): the answer chunks joined,
    the reasoning chunks joined (None when there were none), `raw` as
    delivered, and whether the stream ended inside a reasoning span. The
    loop keeps its inline assembly because it emits a `token` frame per
    chunk as it arrives. Returns the reply and the W0b capture (the seed, the
    options, the digest), which a caller may record so its run can be
    repeated. Everything `reply_stream` does happens here too: a seed is
    drawn and sent where the provider takes one (`NEXUS_INFERENCE_SEED=off`
    sends none), and the provider's digest and environment are read once."""
    rs = reply_stream(provider, model, messages, num_ctx=num_ctx)
    answer: list[str] = []
    reasoning: list[str] = []
    async for chunk in rs:
        (answer if chunk.kind == ANSWER else reasoning).append(chunk.text)
    return (Reply(answer="".join(answer), reasoning="".join(reasoning) or None,
                  raw=rs.raw, unclosed=rs.unclosed), rs.capture)
