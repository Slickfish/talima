"""W10c (run/specs/W10c.md S8) — the one optional link: NEXUS's retrieval block added to a harness's request.

BUILT, AND OFF. `NEXUS_INTERCEPT_INJECT=off` is the default and the interceptor is then a pure recorder: it never
loads this module, and the bytes it forwards are the harness's (tests/test_w10c_interceptor.py T1 pins that byte
for byte). With `NEXUS_INTERCEPT_INJECT=on` the interceptor loads this module by name, once, at start
(`backend/intercept/app.py`), and this module is where the memory engine comes in: it is the only module of
`backend/intercept/` that may reach it, and the standalone guard reads the interceptor's reach without it
(tests/test_w10c_standalone.py).

WHAT ON DOES. For each request, NEXUS's first half of a turn (`prepare_turn`: retrieval over the knowledge store in
the interceptor's own data root, exactly as MCP's `nexus_retrieve` runs it) is run on the request's last user
message. When it retrieves something, its block is added to the request as one more system block, delimited
(`<nexus-block source="retrieval">`): Anthropic's `system` gains a text block at its end; an OpenAI-compatible
request gains a system message after its leading ones. When it retrieves nothing, nothing is added and the bytes
forwarded are the harness's. The record then says both things: `request_hash` is the harness's bytes, as always;
the exchange row's `request_hash` is the bytes sent; `context` holds the block's hash and `injected` the claims.
Nothing here learns: the second half of the turn never runs.

An injection that fails (a request this module cannot read, a store it cannot search) adds nothing: the harness's
bytes are forwarded and the record's notes say why. The optional link never costs an exchange.
"""
from __future__ import annotations

import json
import logging
import os

from backend.data_root import paths_under
from backend.intercept import protocols
from backend.intercept.config import ConfigRefused, INJECT_SETTING
from backend.loop.agentic_loop import prepare_turn
from backend.loop.prompt_render import context_hash, render_version
from backend.server import create_services

logger = logging.getLogger(__name__)

OPEN = '<nexus-block source="retrieval">'
CLOSE = "</nexus-block>"
SESSION = "intercept"


def _text_of(content) -> str:
    """A message's text: the string, or its text blocks joined."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


def question_of(doc: dict) -> str:
    """The last user message's text, in either protocol; empty when there is none."""
    messages = doc.get("messages") if isinstance(doc.get("messages"), list) else []
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            text = _text_of(message.get("content")).strip()
            if text:
                return text
    return ""


def with_block(protocol: str, doc: dict, block: str) -> dict:
    """`doc` with `block` added as one more system block; nothing else of it changes."""
    out = dict(doc)
    if protocol == protocols.OPENAI:
        messages = list(doc.get("messages") or [])
        at = 0
        while at < len(messages) and isinstance(messages[at], dict) and messages[at].get("role") in ("system",
                                                                                                    "developer"):
            at += 1
        out["messages"] = messages[:at] + [{"role": "system", "content": block}] + messages[at:]
        return out
    system = doc.get("system")
    added = {"type": "text", "text": block}
    if isinstance(system, str) and system:
        out["system"] = [{"type": "text", "text": system}, added]
    elif isinstance(system, list):
        out["system"] = list(system) + [added]
    else:
        out["system"] = [added]
    return out


class Injector:
    def __init__(self, config):
        # NEXUS's services over the interceptor's own root. The lock is this process's already (W2: one process may
        # take its own lock again); no idle cycle is started and no learning pass runs. The services read
        # NEXUS_STORE themselves, and a Postgres store is the console's: refused, never searched.
        if (os.environ.get("NEXUS_STORE") or "").strip().lower() == "postgres":
            raise ConfigRefused(f"inject_refused: {INJECT_SETTING}=on searches the knowledge store in the "
                                "interceptor's own folder, and NEXUS_STORE=postgres names the console's")
        self.services = create_services(**paths_under(config.data_root), lock_mode="intercept")
        self.owner = config.owner

    async def apply(self, protocol: str, body: bytes) -> "tuple[bytes, dict | None, list, str | None]":
        """(the bytes to forward, the record's `context`, its `injected`, a note): the harness's bytes and no
        context when nothing is added."""
        try:
            doc = json.loads(body.decode("utf-8"))
            if not isinstance(doc, dict):
                return body, None, [], "injection: the request is not a JSON object; nothing was added"
            question = question_of(doc)
            if not question:
                return body, None, [], "injection: the request holds no user text; nothing was added"
            s = self.services
            handle = None
            async for frame, payload in prepare_turn(
                    s.state, question, retriever=s.retriever, hippocampus=s.hippocampus, procedural=s.procedural,
                    vector_store=s.vector, predictive=s.predictive, scope="default", session_id=SESSION,
                    accountable=self.owner):
                if frame is None:
                    handle = payload
            block = (handle.dynamic_block if handle is not None else "") or ""
            if not block.strip():
                return body, None, [], "injection: retrieval found nothing; nothing was added"
            forwarded = json.dumps(with_block(protocol, doc, OPEN + "\n" + block + "\n" + CLOSE), ensure_ascii=False,
                                   separators=(",", ":")).encode("utf-8")
            injected = [{"belief_id": rt.triple.id, "weight_at_injection": round(rt.score, 6),
                         "tier_at_injection": rt.triple.tier, "cited": False} for rt in handle.hits]
            context = {"render": render_version(), "hash": context_hash(block),
                       "activation": [list(r) for r in handle.rendered_activation]}
            return forwarded, context, injected, (
                f"injection: a retrieval block of {len(injected)} claim(s) was added to the request as a system "
                "block; request_hash is the harness's bytes, the exchange row's is what was sent; `cited` is not "
                "tested here")
        except Exception as e:
            logger.warning("injection failed (%s); the harness's bytes are forwarded as they came", type(e).__name__)
            return body, None, [], f"injection failed ({type(e).__name__}); nothing was added"

    def close(self) -> None:
        for name in ("store", "lineage", "archive_chain"):
            closing = getattr(getattr(self.services, name, None), "close", None)
            if closing is not None:
                try:
                    closing()
                except Exception as e:
                    logger.warning("closing the injection link's %s: %s", name, type(e).__name__)
        release = getattr(getattr(self.services, "store_lock", None), "release", None)
        if release is not None:
            release()
