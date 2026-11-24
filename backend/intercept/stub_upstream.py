"""W10c (run/specs/W10c.md) — a stub upstream: a provider that is not one, speaking both wire protocols.

CI cannot call a real provider (no keys), so this is the upstream the interceptor's tests and the simulated UAT
forward to, and what a person can try the interceptor against without a key:

    python -m backend.intercept.stub_upstream --port 8791

It answers `POST /v1/messages` (Anthropic Messages) and `POST /v1/chat/completions` (OpenAI-compatible), streamed
when the request says `"stream": true` and whole otherwise, from a fixed script:

  - a request whose tools it may use, with no tool result yet: it asks for the tool `get_weather` (the first tool's
    name, when the request names one) with the input `{"city": "Paris"}`;
  - a request that reports a tool result: it answers in words, naming nothing it was not sent;
  - any other request: one sentence.

It checks no key, keeps nothing and calls nothing. `GET /health` says how many requests it has answered.
`StubUpstream.requests` and `.responses` hold what it was sent and what it sent back, for the tests that compare
bytes; the constructor's options (a compressed stream, a stream that breaks, a gate between events) are theirs too.
"""
from __future__ import annotations

import asyncio
import json
import zlib

MODEL = "stub-model-1"
ANSWER = "It is 18 degrees and clear in Paris."
HELLO = "Hello from the stub upstream."


def _dump(value) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _sse(name: "str | None", data) -> bytes:
    payload = data if isinstance(data, bytes) else _dump(data)
    return (b"event: " + name.encode() + b"\n" if name else b"") + b"data: " + payload + b"\n\n"


class StubUpstream:
    def __init__(self, *, gzip: bool = False, break_after: "int | None" = None, gate: "asyncio.Event | None" = None,
                 gate_after: int = 1, status: int = 200, done_without_blank_line: bool = False):
        self.requests: list = []          # {"path", "query", "headers", "raw_headers", "body"} per request
        self.responses: list = []         # the body bytes of each response, as sent
        self.calls = 0
        self._gzip, self._break_after, self._gate, self._gate_after = gzip, break_after, gate, gate_after
        self._status, self._bare_done = status, done_without_blank_line

    # ---------------------------------------------------------------- the script

    def _plan(self, protocol: str, doc: dict) -> "tuple[str, dict]":
        """("tool", {"id", "name", "input"}) | ("text", {"text"}) for a request."""
        messages = doc.get("messages") if isinstance(doc.get("messages"), list) else []
        if protocol == "openai":
            reported = any(isinstance(m, dict) and m.get("role") == "tool" for m in messages)
            tools = [t.get("function", {}).get("name") for t in doc.get("tools") or [] if isinstance(t, dict)]
        else:
            reported = any(isinstance(b, dict) and b.get("type") == "tool_result" for m in messages
                           if isinstance(m, dict) and isinstance(m.get("content"), list) for b in m["content"])
            tools = [t.get("name") for t in doc.get("tools") or [] if isinstance(t, dict)]
        if reported:
            return "text", {"text": ANSWER}
        if tools and tools[0]:
            prefix = "call_stub_" if protocol == "openai" else "toolu_stub_"
            return "tool", {"id": f"{prefix}{self.calls:04d}", "name": tools[0], "input": {"city": "Paris"}}
        return "text", {"text": HELLO}

    def _anthropic(self, kind: str, plan: dict, stream: bool) -> "list | bytes":
        mid = f"msg_stub_{self.calls:04d}"
        if not stream:
            content = ([{"type": "tool_use", "id": plan["id"], "name": plan["name"], "input": plan["input"]}]
                       if kind == "tool" else [{"type": "text", "text": plan["text"]}])
            return _dump({"id": mid, "type": "message", "role": "assistant", "model": MODEL, "content": content,
                          "stop_reason": "tool_use" if kind == "tool" else "end_turn", "stop_sequence": None,
                          "usage": {"input_tokens": 12, "output_tokens": 9}})
        events = [_sse("message_start", {"type": "message_start", "message": {
            "id": mid, "type": "message", "role": "assistant", "model": MODEL, "content": [], "stop_reason": None,
            "stop_sequence": None, "usage": {"input_tokens": 12, "output_tokens": 1}}}),
            _sse("ping", {"type": "ping"})]
        if kind == "tool":
            text = json.dumps(plan["input"])
            events += [_sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
                "type": "tool_use", "id": plan["id"], "name": plan["name"], "input": {}}})]
            events += [_sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {
                "type": "input_json_delta", "partial_json": part}}) for part in (text[:7], text[7:])]
        else:
            events += [_sse("content_block_start", {"type": "content_block_start", "index": 0,
                                                    "content_block": {"type": "text", "text": ""}})]
            events += [_sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {
                "type": "text_delta", "text": part}}) for part in (plan["text"][:10], plan["text"][10:])]
        events += [_sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
                   _sse("message_delta", {"type": "message_delta", "delta": {
                       "stop_reason": "tool_use" if kind == "tool" else "end_turn", "stop_sequence": None},
                       "usage": {"output_tokens": 9}}),
                   _sse("message_stop", {"type": "message_stop"})]
        return events

    def _openai(self, kind: str, plan: dict, stream: bool) -> "list | bytes":
        cid = f"chatcmpl-stub-{self.calls:04d}"
        if not stream:
            message = ({"role": "assistant", "content": None, "tool_calls": [{
                "id": plan["id"], "type": "function", "function": {"name": plan["name"],
                                                                 "arguments": json.dumps(plan["input"])}}]}
                       if kind == "tool" else {"role": "assistant", "content": plan["text"]})
            return _dump({"id": cid, "object": "chat.completion", "created": 0, "model": MODEL, "choices": [{
                "index": 0, "message": message, "finish_reason": "tool_calls" if kind == "tool" else "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21}})

        def chunk(delta: dict, finish=None) -> bytes:
            return _sse(None, {"id": cid, "object": "chat.completion.chunk", "created": 0, "model": MODEL,
                               "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
        if kind == "tool":
            text = json.dumps(plan["input"])
            events = [chunk({"role": "assistant", "content": None, "tool_calls": [{
                "index": 0, "id": plan["id"], "type": "function", "function": {"name": plan["name"],
                                                                             "arguments": ""}}]})]
            events += [chunk({"tool_calls": [{"index": 0, "function": {"arguments": part}}]})
                       for part in (text[:7], text[7:])]
            events += [chunk({}, "tool_calls")]
        else:
            events = [chunk({"role": "assistant", "content": ""})]
            events += [chunk({"content": part}) for part in (plan["text"][:10], plan["text"][10:])]
            events += [chunk({}, "stop")]
        return events + [b"data: [DONE]\n" if self._bare_done else b"data: [DONE]\n\n"]

    # ---------------------------------------------------------------- ASGI

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["method"] == "GET" and scope["path"] in ("/health", "/healthz"):
            out = _dump({"status": "ok", "stub": True, "calls": self.calls})      # how many requests it has answered
            await send({"type": "http.response.start", "status": 200, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(out)).encode())]})
            await send({"type": "http.response.body", "body": out})
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        self.calls += 1
        self.requests.append({"path": scope["path"], "query": (scope.get("query_string") or b"").decode("latin-1"),
                              "headers": [(n.decode("latin-1"), v.decode("latin-1")) for n, v in scope["headers"]],
                              "raw_headers": list(scope["headers"]),
                              "body": bytes(body)})
        protocol = {"/v1/messages": "anthropic", "/v1/chat/completions": "openai"}.get(scope["path"])
        try:
            doc = json.loads(bytes(body).decode("utf-8"))
            doc = doc if isinstance(doc, dict) else None
        except (ValueError, UnicodeDecodeError):
            doc = None
        if protocol is None or scope["method"] != "POST" or doc is None or self._status != 200:
            status = self._status if self._status != 200 else (404 if protocol is None else 400)
            out = _dump({"type": "error", "error": {"type": "invalid_request_error",
                                                    "message": "the stub upstream does not answer this"}})
            self.responses.append(out)
            await send({"type": "http.response.start", "status": status, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(out)).encode()),
                (b"request-id", f"req_stub_{self.calls:04d}".encode())]})
            await send({"type": "http.response.body", "body": out})
            return
        stream = doc.get("stream") is True
        kind, plan = self._plan(protocol, doc)
        made = (self._openai if protocol == "openai" else self._anthropic)(kind, plan, stream)
        headers = [(b"request-id", f"req_stub_{self.calls:04d}".encode())]
        if not stream:
            self.responses.append(made)
            await send({"type": "http.response.start", "status": 200, "headers": headers + [
                (b"content-type", b"application/json"), (b"content-length", str(len(made)).encode())]})
            await send({"type": "http.response.body", "body": made})
            return
        headers += [(b"content-type", b"text/event-stream"), (b"cache-control", b"no-cache")]
        packer = zlib.compressobj(wbits=31) if self._gzip else None
        if packer is not None:
            headers.append((b"content-encoding", b"gzip"))
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        sent = bytearray()
        self.responses.append(sent)                      # grows as it is sent: a test may read it mid-stream
        for n, event in enumerate(made, 1):
            if self._break_after is not None and n > self._break_after:
                raise ConnectionError("the stub upstream broke its stream, as it was asked to")
            piece = event if packer is None else packer.compress(event) + packer.flush(zlib.Z_SYNC_FLUSH)
            if packer is not None and n == len(made):
                piece += packer.flush()
            sent += piece
            await send({"type": "http.response.body", "body": piece, "more_body": n < len(made)})
            if self._gate is not None and n == self._gate_after:
                await self._gate.wait()


def main(argv=None) -> int:
    import argparse

    import uvicorn
    ap = argparse.ArgumentParser(prog="python -m backend.intercept.stub_upstream", description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8791)
    args = ap.parse_args(argv)
    uvicorn.run(StubUpstream(), host=args.host, port=args.port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
