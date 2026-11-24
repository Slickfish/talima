"""W0b — the network replaced at the transport, for the real providers.

`FakeDaemon` stands in for `httpx.AsyncClient` inside the provider modules,
so `OllamaProvider` and `LMStudioProvider` run unchanged: their own
`_payload`, their own seed placement, their own stream parsing. Every
request body they send is kept (`bodies`), which is what lets a test compare
the record's seed and options with what actually went on the wire (the
verifier's M2: a stub that builds a body and throws it away proves nothing
about the body).

The answering request is told apart from extraction by its first message
(the stable prefix leads every answering request). The reply is a pure
function of the body (`answer`), so a deterministic model reproduces from
the same seed and a test can plant any other.
"""
from __future__ import annotations

import copy
import json

from backend.loop.compaction import STABLE_SYSTEM_PREFIX

MODEL = "deepseek-r1:14b-qwen-distill-q4_K_M"     # state.active_model's default, the tag the console asks for
DIGEST = "sha256:" + "5e" * 32
STEM = "Interval training improves VO2 max (sample "


def answering(body: dict) -> bool:
    msgs = body.get("messages") or []
    return bool(msgs) and str(msgs[0].get("content", "")).startswith(STABLE_SYSTEM_PREFIX)


def wire_seed(body: dict):
    """Where each provider carries the seed: Ollama in options, LM Studio at the top."""
    if "options" in body:
        return (body.get("options") or {}).get("seed")
    return body.get("seed")


def answer_for(seed) -> list[str]:
    """A deterministic model: the reply is a pure function of the seed, in
    two pieces. No seed answers a fixed text, so a request that lost its seed
    does not reproduce a record that carries one."""
    return [STEM, f"{seed % 9973})." if isinstance(seed, int) else "no seed)."]


class _Resp:
    def __init__(self, status, body=None, lines=()):
        self.status_code, self._body, self._lines = status, body, list(lines)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"fake daemon answered {self.status_code}")

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeDaemon:
    """Class-level state, because each provider call constructs a new client.
    `reset()` before each test; `tags` and `version` are what /api/tags and
    /api/version answer; `answer(body) -> pieces` is the model."""
    bodies: list[tuple[str, dict]] = []
    tags: dict[str, str] = {MODEL: DIGEST}
    version = "0.12.6-stub"
    answer = staticmethod(lambda body: answer_for(wire_seed(body)))

    @classmethod
    def reset(cls, *, tags=None, answer=None):
        cls.bodies = []
        cls.tags = dict({MODEL: DIGEST} if tags is None else tags)
        cls.answer = staticmethod(answer or (lambda body: answer_for(wire_seed(body))))

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        if url.endswith("/api/tags"):
            return _Resp(200, {"models": [{"name": n, "model": n, "digest": d} for n, d in type(self).tags.items()]})
        if url.endswith("/api/version"):
            return _Resp(200, {"version": type(self).version})
        if url.endswith("/v1/models"):
            return _Resp(200, {"data": [{"id": n} for n in type(self).tags]})
        return _Resp(404, {})

    async def post(self, url, **kw):                                # embeddings: not served here
        return _Resp(404, {})

    def stream(self, method, url, json=None, **kw):
        body = copy.deepcopy(json)
        type(self).bodies.append((url, body))
        pieces = type(self).answer(body) if answering(body) else ["[]"]
        if url.endswith("/v1/chat/completions"):                   # LM Studio's SSE
            lines = [f"data: {_dumps({'choices': [{'delta': {'content': p}}]})}" for p in pieces] + ["data: [DONE]"]
        else:                                                      # Ollama's NDJSON
            lines = [_dumps({"message": {"role": "assistant", "content": p}, "done": False}) for p in pieces]
            lines.append(_dumps({"message": {"role": "assistant", "content": ""}, "done": True}))
        return _Resp(200, lines=lines)


def _dumps(obj) -> str:
    return json.dumps(obj)


def install(monkeypatch) -> type[FakeDaemon]:
    """Replace httpx.AsyncClient for the provider modules for one test."""
    import backend.providers.ollama as ollama_mod
    FakeDaemon.reset()
    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", FakeDaemon)
    return FakeDaemon
