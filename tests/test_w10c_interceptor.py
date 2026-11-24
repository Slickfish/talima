"""W10c (run/specs/W10c.md S3 to S7) — the interceptor against a stub upstream speaking both protocols.

The upstream is `backend/intercept/stub_upstream.py` (CI calls no provider), reached through the interceptor's own
forwarder over an in-process transport; the harness is an HTTP client on the interceptor's ASGI application. The
store is a real one in a temp folder. Each test says what would have to be false for it to fail.

    T1   transparency, four ways (two protocols, streamed and whole): the bytes the harness receives are the bytes
         the upstream sent; the bytes the upstream received are the bytes the harness sent; nothing is added to
         the request's headers and the connection's own are not relayed.
    T1b  a planted rewrite of one streamed event is named by the same check.
    T2   a request whose record cannot be written is answered `talima_request_not_recorded`, the upstream is
         never called, and nothing of the request is in the store (its kept bytes included: one transaction); the
         same when it is the commit that fails, under another program's open read, and the store recovers after.
    T3   a response whose record cannot be written: a stream ends with an error event in place of the terminal
         one, a whole response is withheld, a compressed stream is cut; the exchange reads `incomplete`; no record
         and no tool-use row is written.
    T4   a key in `x-api-key` and `Authorization` is stored as a fingerprint only: its value is in no file of
         the store's folder and in no log line.
    T5   a two-turn tool-use session is one `intercepted` action with its reported result linked, never
         `runtime`; an error result reads `reported_error`; a result sent again writes nothing to a tool use that
         has its outcome (what linking cannot tell apart is pinned in T5w); an outcome is
         written once, by the statement itself, and a request that would write one again writes nothing at all.
    T6   the scope: an intercepted record declares itself, its hashes are over the bytes, and a record that
         contradicts its scope is named.
    T7   the chain verifies; a record's credential and the action's receipt pass `talima-verify` from an empty
         folder; a changed byte in the store is named by `verify`.
    T8   what the interceptor refuses to forward: another path, a browser's cross-site request, a foreign host.
    T9   an upstream that cannot be reached, one whose stream breaks, a harness that goes away: each is recorded
         as what it was.
    T10  a process that stopped mid-exchange leaves a row the next start marks `incomplete`.
    T11  the store is the interceptor's own: another mode's store, the repository's `data/` and the console's
         root are refused before anything opens; so is a bind beyond this machine outside proxy mode.
    T12  five exchanges at once are five records.
    T13  behind the identity proxy the record names the proxy's login and how it was established, and the
         proxy's headers do not reach the upstream.
    T14  the terminal event of a stream is relayed only after the record is written (the order, observed).
    T15  a stream whose last line has no blank line after it is still held at its terminal event.
    T16  the kept request is the harness's bytes, under its own encoding; the keep switch is honoured and noted.
    T17  the optional link (NEXUS_INTERCEPT_INJECT): off, the bytes forwarded are the harness's; on, one delimited
         system block is added and nothing else changes, and the record holds both requests' hashes.
    T18  an injection that fails adds nothing, and the exchange is recorded all the same.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import sqlite3
import sys
import uuid
import zlib
from pathlib import Path

import httpx
import pytest

from backend.intercept import app as iapp
from backend.intercept import config as iconfig
from backend.intercept import protocols
from backend.intercept.forward import Forwarder
from backend.intercept.recorder import EXCHANGE_KIND, MARKER, STOPPED, TOOL_USE_KIND, Recorder, tool_id_of
from backend.intercept.stub_upstream import ANSWER, MODEL, StubUpstream
from backend.mcp import scope as record_scope
from backend.memory import actions as act
from backend.memory.lineage import INTERCEPTED, REQUEST_ENCODING_HTTP_BODY
from tests import _proc

REPO = Path(__file__).resolve().parent.parent
KEY = "sk-not-a-real-key-W10c-4f9a1c7e2b"
BEARER = "not-a-real-bearer-W10c-77c1d09e"
BASE = "http://127.0.0.1:8790"
ANTHROPIC, OPENAI = "/v1/messages", "/v1/chat/completions"
TOOLS = {ANTHROPIC: [{"name": "get_weather", "description": "the weather", "input_schema": {"type": "object"}}],
         OPENAI: [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}]}


class Streaming(httpx.AsyncBaseTransport):
    """An ASGI app as an upstream whose response arrives piece by piece, as a provider's does (httpx's own ASGI
    transport hands a response over whole). A stream the app breaks is reported as the client reports a broken
    connection: `httpx.ReadError`."""

    def __init__(self, app):
        self.app = app

    async def handle_async_request(self, request):
        body = await request.aread()
        queue: asyncio.Queue = asyncio.Queue()
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": request.method,
                 "path": request.url.path, "raw_path": request.url.raw_path, "query_string": request.url.query,
                 "headers": [(n.lower(), v) for n, v in request.headers.raw], "client": ("127.0.0.1", 50000),
                 "server": (request.url.host, request.url.port or 80), "scheme": request.url.scheme}
        fed = False

        async def receive():
            nonlocal fed
            if not fed:
                fed = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.sleep(600)                     # the client does not disconnect first
            return {"type": "http.disconnect"}

        async def serve():
            try:
                await self.app(scope, receive, queue.put)
            except Exception as e:                       # the upstream broke its stream
                await queue.put(e)
            await queue.put(None)

        task = asyncio.ensure_future(serve())
        start = await queue.get()
        if not isinstance(start, dict):
            raise httpx.ConnectError("the upstream refused the connection")

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self_inner):
                while True:
                    message = await queue.get()
                    if message is None:
                        return
                    if isinstance(message, Exception):
                        raise httpx.ReadError("the upstream's connection broke")
                    if message.get("body"):
                        yield message["body"]

            async def aclose(self_inner):
                task.cancel()

        return httpx.Response(start["status"], headers=start["headers"], stream=Body())


def environ(root: Path, **more) -> dict:
    return {"NEXUS_INTERCEPT_UPSTREAM": "http://127.0.0.1:9", "NEXUS_INTERCEPT_DATA_ROOT": str(root),
            "NEXUS_INTERCEPT_OWNER": "tester@example.test", **more}


class Rig:
    """A stub upstream, the interceptor over a real store, and a harness client."""

    def __init__(self, root: Path, *, stub: "StubUpstream | None" = None, wrap=None, **env):
        self.root = root
        self.stub = stub or StubUpstream()
        # the one setting a test may give as the process's environment, as an operator does
        ambient = {k: v for k, v in os.environ.items() if k == "NEXUS_INTERCEPT_INJECT"}
        self.config = iconfig.load({**ambient, **environ(root, **env)})
        self.recorder = Recorder.open_at(self.config.data_root)
        self.forwarder = Forwarder(transport=Streaming(self.stub))
        self.app = iapp.build(self.config, self.recorder, self.forwarder)
        self.inner = self.app.app if self.config.proxy_mode else self.app
        served = wrap(self.app) if wrap is not None else self.app
        self.harness = httpx.AsyncClient(transport=httpx.ASGITransport(app=served), base_url=BASE)

    async def __aenter__(self):
        await self.inner.startup()
        return self

    async def __aexit__(self, *exc):
        await self.harness.aclose()
        await self.inner.shutdown()

    async def post(self, path: str, doc: "dict | bytes", headers: "dict | None" = None):
        """One exchange: (status, response headers, the body's bytes as received, the request as sent)."""
        body = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
        sent = {"content-type": "application/json", "x-api-key": KEY, "authorization": f"Bearer {BEARER}",
                **(headers or {})}
        async with self.harness.stream("POST", path, content=body, headers=sent) as response:
            raw = b"".join([chunk async for chunk in response.aiter_raw()])
            return response.status_code, response.headers, raw, response.request

    # what the store holds
    def actions(self, **where) -> list:
        rows = [dict(r) for r in self.recorder.lineage.conn.execute("SELECT * FROM action_records").fetchall()]
        return sorted((r for r in rows if all(r[k] == v for k, v in where.items())),
                      key=lambda r: (r["requested_at"], r["action_id"]))

    def records(self) -> list:
        return list(reversed(self.recorder.lineage.recent(100)))


def run(coro):
    return asyncio.run(coro)


def ask(path: str, *, stream: bool, tools: bool = False, messages: "list | None" = None) -> dict:
    doc = {"model": "claude-test" if path == ANTHROPIC else "gpt-test", "stream": stream,
           "messages": messages or [{"role": "user", "content": "What is the weather in Paris?"}]}
    if path == ANTHROPIC:
        doc["max_tokens"] = 64
    if tools:
        doc["tools"] = TOOLS[path]
    return doc


def first_difference(sent: bytes, received: bytes) -> "str | None":
    """None when the two are the same bytes; else which server-sent event first differs, by number and text."""
    if sent == received:
        return None
    a, tail_a = protocols.split_events(sent)
    b, tail_b = protocols.split_events(received)
    for n, (x, y) in enumerate(zip(a + [tail_a], b + [tail_b]), 1):
        if x != y:
            return f"event {n} differs: the upstream sent {x!r}, the harness received {y!r}"
    return f"the upstream sent {len(a)} event(s) and the harness received {len(b)}"


# ---------------------------------------------------------------- T1

@pytest.mark.parametrize("path", [ANTHROPIC, OPENAI])
@pytest.mark.parametrize("stream", [True, False])
def test_t1_the_harness_receives_the_upstreams_bytes_and_the_upstream_the_harnesss(tmp_path, path, stream):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            doc = json.dumps(ask(path, stream=stream, tools=True), indent=1).encode() + b"\n"   # not canonical: as sent
            status, headers, raw, request = await rig.post(
                path + "?beta=true", doc, {"anthropic-version": "2023-06-01", "anthropic-beta": "tools-2024, x",
                                           "x-harness": "a\tb", "connection": "keep-alive, x-per-hop",
                                           "x-per-hop": "never-forwarded", "accept-encoding": "identity"})
            got = rig.stub.requests[0]
            return status, headers, raw, request, got, bytes(rig.stub.responses[0]), doc, rig.records(), rig.actions()
    status, headers, raw, request, got, sent, doc, records, actions = run(go())
    assert status == 200
    assert first_difference(sent, raw) is None, first_difference(sent, raw)
    assert got["body"] == doc and got["path"] == path and got["query"] == "beta=true"
    # the request's headers: every one the harness sent that is not the connection's own, its value and its place
    # as sent, and no other. A name arrives in lower case: HTTP's names have no case, and the server hands them so.
    harness = [(n.decode("latin-1").lower(), v.decode("latin-1")) for n, v in request.headers.raw]
    per_connection = {"host", "content-length", "connection", "x-per-hop"}
    expected = [(n, v) for n, v in harness if n not in per_connection]
    assert [(n, v) for n, v in got["headers"] if n.lower() not in ("host", "content-length")] == expected
    assert ("x-harness", "a\tb") in got["headers"] and ("anthropic-beta", "tools-2024, x") in got["headers"]
    assert not [n for n, _ in got["headers"] if n.lower() in ("x-per-hop", "connection", "user-agent-added")]
    assert dict(got["headers"])["host"] == "127.0.0.1:9", "the upstream's own host, not the interceptor's"
    # the response's headers: the upstream's, less the connection's own
    assert headers["request-id"] == "req_stub_0001"
    assert headers["content-type"] == ("text/event-stream" if stream else "application/json")
    # the record: over the bytes
    assert len(records) == 1 and records[0].record_scope == INTERCEPTED
    assert records[0].request_hash == hashlib.sha256(doc).hexdigest()
    assert records[0].response_hash == hashlib.sha256(sent).hexdigest()
    assert records[0].model_id == f"upstream-reported:{MODEL}"
    exchange = [a for a in actions if a["kind"] == EXCHANGE_KIND]
    assert len(exchange) == 1 and exchange[0]["state"] == "completed" and exchange[0]["status"] == "200"
    assert exchange[0]["request_hash"] == records[0].request_hash and exchange[0]["lineage_id"] == records[0].lineage_id
    assert exchange[0]["target"] == f"http://127.0.0.1:9{path}" and exchange[0]["trigger_route"] == f"intercept:{path}"


def test_t1b_a_rewritten_event_is_named(tmp_path):
    """The same check, on an interceptor made to alter one event on its way through."""
    def rewriting(app):
        async def served(scope, receive, send):
            async def altered(message):
                if message["type"] == "http.response.body" and b"content_block_delta" in message.get("body", b""):
                    message = {**message, "body": message["body"].replace(b"Paris", b"Lyon", 1)}
                await send(message)
            await app(scope, receive, altered)
        return served

    async def go():
        async with Rig(tmp_path / "root", wrap=rewriting) as rig:
            _status, _headers, raw, _request = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))
            return bytes(rig.stub.responses[0]), raw
    sent, raw = run(go())
    named = first_difference(sent, raw)
    assert named is not None and "content_block_delta" in named and "Lyon" in named, named


# ---------------------------------------------------------------- T2, T3: fail closed

def _refuse_inserts(recorder, table: str) -> None:
    """The store itself refuses the next insert into `table` (a real failure, inside the write's transaction)."""
    recorder.lineage.conn.execute(f"CREATE TRIGGER planted_{table} BEFORE INSERT ON {table} "
                                  f"BEGIN SELECT RAISE(ABORT, 'planted: {table} cannot be written'); END")
    recorder.lineage.conn.commit()


@pytest.mark.parametrize("path", [ANTHROPIC, OPENAI])
def test_t2_a_request_that_cannot_be_recorded_is_never_sent(tmp_path, path):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            _refuse_inserts(rig.recorder, "action_records")
            status, headers, raw, _request = await rig.post(path, ask(path, stream=True))
            bodies = rig.recorder.lineage.conn.execute("SELECT COUNT(*) FROM request_bodies").fetchone()[0]
            return status, headers, raw, rig.stub.calls, rig.actions(), rig.records(), bodies, dict(rig.inner.stats)
    status, headers, raw, calls, actions, records, bodies, stats = run(go())
    assert status == 503 and headers["content-type"] == "application/json"
    doc = json.loads(raw)
    message = doc["error"]["message"]
    assert message.startswith("talima_request_not_recorded: ") and "it was not sent" in message, message
    assert (doc.get("type") == "error") == (path == ANTHROPIC), "the protocol's own error shape"
    assert calls == 0, "the upstream was called"
    assert actions == [] and records == [] and bodies == 0, "something of the request was written"
    assert stats["refused_before_sending"] == 1 and stats["recorded"] == 0


def test_t2b_a_commit_that_fails_under_another_programs_open_read_sends_nothing_and_the_store_recovers(tmp_path):
    """The failure at the COMMIT, not at a statement: another program has a read open on the store's file, so the
    request's transaction cannot commit. Nothing is sent, nothing of it stays, and when the read ends the next
    exchange is recorded (the failed transaction was rolled back, not left open)."""
    async def go():
        async with Rig(tmp_path / "root") as rig:
            await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True))
            rig.recorder.lineage.conn.execute("PRAGMA busy_timeout = 200")       # how long the commit waits
            other = sqlite3.connect(tmp_path / "root" / "kb.db")
            reading = other.execute("SELECT name FROM sqlite_master")
            reading.fetchone()                                                    # a read begun and not finished
            try:
                status, _h, raw, _r = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))
                during = (status, raw, rig.stub.calls)
            finally:
                reading.close()
                other.close()
            status, _h, _raw, _r = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True))
            return during, status, rig.stub.calls, rig.actions(kind=EXCHANGE_KIND), rig.records(), rig.recorder.verify()
    (held_status, held_raw, held_calls), status, calls, exchanges, records, verdict = run(go())
    assert held_status == 503 and b"talima_request_not_recorded" in held_raw and held_calls == 1, "it was sent"
    assert status == 200 and calls == 2, "the store did not recover once the read ended"
    assert [e["state"] for e in exchanges] == ["completed", "completed"] and len(records) == 2 and verdict["ok"]


@pytest.mark.parametrize("path", [ANTHROPIC, OPENAI])
def test_t3_a_stream_whose_record_cannot_be_written_ends_with_an_error_event(tmp_path, path):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            _refuse_inserts(rig.recorder, "lineage_records")
            status, _headers, raw, _request = await rig.post(path, ask(path, stream=True, tools=True))
            return status, raw, bytes(rig.stub.responses[0]), rig.actions(), rig.records(), rig.stub.calls
    status, raw, sent, actions, records, calls = run(go())
    assert status == 200 and calls == 1, "a stream's status is sent before its record can fail"
    events, tail = protocols.split_events(raw)
    assert tail == b"" and not any(protocols.is_terminal(protocols.protocol_of(path), e) and b"talima" not in e
                                   for e in events), "the upstream's terminal event reached the harness"
    assert b"talima_response_not_recorded" in events[-1], events[-1]
    upstream_events, _ = protocols.split_events(sent)
    assert events[:-1] == upstream_events[:-1], "everything before the terminal event is the upstream's, unchanged"
    assert records == [], "a record was written"
    assert [a["kind"] for a in actions] == [EXCHANGE_KIND], "a tool-use row outlived the record it belongs to"
    assert actions[0]["state"] == "incomplete" and actions[0]["status"].startswith("response_not_recorded: ")
    assert actions[0]["response_hash"] == hashlib.sha256(sent).hexdigest() and actions[0]["record_hash"]


def test_t3b_a_whole_response_is_withheld_and_a_compressed_stream_is_cut(tmp_path):
    async def whole():
        async with Rig(tmp_path / "whole") as rig:
            _refuse_inserts(rig.recorder, "lineage_records")
            status, _headers, raw, _request = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=False))
            return status, raw, bytes(rig.stub.responses[0]), rig.actions()
    status, raw, sent, actions = run(whole())
    assert status == 503 and b"talima_response_not_recorded" in raw and b"the response is withheld" in raw
    assert sent not in raw and b"Hello from the stub" not in raw, "the response reached the harness"
    assert actions[0]["state"] == "incomplete"

    async def compressed():
        async with Rig(tmp_path / "gz", stub=StubUpstream(gzip=True)) as rig:
            _refuse_inserts(rig.recorder, "lineage_records")
            with pytest.raises(iapp.StreamCut):
                await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True), {"accept-encoding": "gzip"})
            return rig.actions()
    assert run(compressed())[0]["state"] == "incomplete"


def test_t3c_a_compressed_stream_is_relayed_as_it_came_and_read_for_the_record(tmp_path):
    async def go():
        async with Rig(tmp_path / "root", stub=StubUpstream(gzip=True)) as rig:
            status, headers, raw, _request = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True),
                                                            {"accept-encoding": "gzip"})
            return status, headers, raw, bytes(rig.stub.responses[0]), rig.records(), rig.actions(kind=TOOL_USE_KIND)
    status, headers, raw, sent, records, uses = run(go())
    assert status == 200 and headers["content-encoding"] == "gzip" and raw == sent
    assert b"message_stop" in gzip.decompress(raw)
    assert records[0].response_hash == hashlib.sha256(sent).hexdigest(), "over the bytes as the upstream sent them"
    assert records[0].model_id == f"upstream-reported:{MODEL}" and len(uses) == 1, "the compressed body was not read"


class Scripted:
    """An upstream that sends the pieces it is given, under the headers it is given: for a response the stub
    does not make (two gzip members, an encoding nothing here reads, an event named oddly)."""

    def __init__(self, pieces: list, headers: list):
        self.pieces, self.headers, self.calls, self.requests, self.responses = pieces, headers, 0, [], []

    async def __call__(self, scope, receive, send):
        self.calls += 1
        self.requests.append({"headers": [(n.decode("latin-1"), v) for n, v in scope["headers"]]})
        sent = bytearray()
        self.responses.append(sent)
        await send({"type": "http.response.start", "status": 200, "headers": self.headers})
        for n, piece in enumerate(self.pieces, 1):
            sent += piece
            await send({"type": "http.response.body", "body": piece, "more_body": n < len(self.pieces)})


STREAM = [(b"content-type", b"text/event-stream")]
_START = b'event: message_start\ndata: {"type":"message_start","message":{"model":"m-1"}}\n\n'
_USE = (b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":'
        b'{"type":"tool_use","id":"toolu_r1","name":"get_weather","input":{"city":"Paris"}}}\n\n')
_STOP = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'


@pytest.mark.parametrize("last", [
    b'event: message_stop \ndata: {"type":"message_stop"}\n\n',          # a space after the name
    b'event: completion\ndata: {"type":"message_stop"}\n\n',             # another name; the data says what it is
    b'event:message_stop\ndata:{"type":"message_stop"}\n\n',             # no space after the colons
    b'data: {"type":"error","error":{"type":"overloaded_error"}}\n\n',    # an error, unnamed
])
def test_p1_the_terminal_event_is_known_by_its_data_as_well_as_its_name(tmp_path, last):
    """Whatever the upstream calls its last event, the harness does not hold it before the record is written."""
    assert protocols.is_terminal(protocols.ANTHROPIC, last)

    async def go():
        async with Rig(tmp_path / "root", stub=Scripted([_START, last], STREAM)) as rig:
            _refuse_inserts(rig.recorder, "lineage_records")
            _status, _headers, raw, _request = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True))
            return raw
    raw = run(go())
    assert raw.startswith(_START) and last not in raw and b"talima_response_not_recorded" in raw, raw[-300:]


def test_p2_a_gzip_body_of_two_members_is_read_to_its_end(tmp_path):
    """A gzip body may be several members one after another. The second holds the tool use and the last event:
    both are read, and the last event is held like any other."""
    members = [gzip.compress(_START), gzip.compress(_USE + _STOP)]
    assert protocols.decoded(b"".join(members), "gzip") == (_START + _USE + _STOP, None)
    headers = STREAM + [(b"content-encoding", b"gzip")]

    async def go(root, refuse):
        async with Rig(root, stub=Scripted(members, headers)) as rig:
            if refuse:
                _refuse_inserts(rig.recorder, "lineage_records")
                with pytest.raises(iapp.StreamCut):
                    await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))
                return None, rig.actions(kind=TOOL_USE_KIND), rig.records()
            _status, _headers, raw, _request = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))
            return raw, rig.actions(kind=TOOL_USE_KIND), rig.records()
    raw, uses, records = run(go(tmp_path / "ok", False))
    assert raw == b"".join(members) and [u["target"] for u in uses] == ["tool_use:toolu_r1:get_weather"]
    assert records[0].model_id == "upstream-reported:m-1"
    _raw, uses, records = run(go(tmp_path / "refused", True))
    assert uses == [] and records == []


@pytest.mark.parametrize("trailing", [b"\x00\x00\x00\x00", b"\x1f", b"junk after the body", b"",
                                      b"\x1f\x8b and then nothing a member is made of", b"\x1f\x8b",
                                      zlib.compress(b"data: a stream of another kind, not the body's\n\n")])
def test_p2b_bytes_after_a_gzip_bodys_last_member_do_not_make_it_unread(trailing):
    """What follows the last member and is not another member is not part of the body: what the members held is
    read (delta check 1's D7: four zero bytes made the whole response unread; delta check 2's E13: so did bytes
    that open like a member and are not one). And nothing else is: a stream of another kind after the body is not
    decoded into it."""
    whole = gzip.compress(_START) + gzip.compress(_USE + _STOP) + trailing
    body, note = protocols.decoded(whole, "gzip")
    assert body == _START + _USE + _STOP
    # and the record says so: how many bytes were passed over, or nothing when none were (delta check 3's F6)
    assert note == (f"{len(trailing)} byte(s) after the body's last readable member were not read"
                    if trailing else None)
    piecewise = protocols.Inflater()
    assert b"".join(piecewise.feed(whole[i:i + 7]) for i in range(0, len(whole), 7)) == _START + _USE + _STOP
    facts = protocols.read_response(protocols.ANTHROPIC, protocols.decoded(whole, "gzip")[0], streamed=True)
    assert facts.model == "m-1" and len(facts.uses) == 1


@pytest.mark.parametrize("protocol", [protocols.ANTHROPIC, protocols.OPENAI])
def test_p6_a_tool_use_whose_input_is_nested_deep_is_still_read(protocol):
    """An input nested 700 deep is read, hashed and linked like any other: the response's model and tool use, and
    the request that repeats it (delta check 2's E10: comparing numbers by value walked the input in Python, and
    past 500 levels the whole response read as unreadable)."""
    deep = json.loads("[" * 700 + "30.0" + "]" * 700)
    respelled = json.loads("[" * 700 + "30" + "]" * 700)
    if protocol == protocols.ANTHROPIC:
        body = json.dumps({"model": "m-1", "content": [{"type": "tool_use", "id": "t1", "name": "f",
                                                        "input": {"x": deep}}]}).encode()
        request = json.dumps({"messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "f", "input": {"x": respelled}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}]}).encode()
    else:
        body = json.dumps({"model": "m-1", "choices": [{"message": {"tool_calls": [
            {"id": "t1", "function": {"name": "f", "arguments": json.dumps({"x": deep})}}]}}]}).encode()
        request = json.dumps({"messages": [
            {"role": "assistant", "tool_calls": [{"id": "t1", "function": {
                "name": "f", "arguments": json.dumps({"x": respelled})}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "ok"}]}).encode()
    facts = protocols.read_response(protocol, body, streamed=False)
    assert facts.model == "m-1" and len(facts.uses) == 1, facts
    read = protocols.read_request(protocol, request)
    assert [r.ask_hash for r in read.results] == [facts.uses[0].request_hash], read


@pytest.mark.parametrize("streamed", [True, False])
def test_p3_a_response_in_an_encoding_nothing_here_reads_is_held_whole_and_says_it_was_not_read(tmp_path, streamed):
    """Nothing of it can be read for a last event, so none of it reaches the harness before the record is
    written: recorded, it is relayed whole as it came; unrecorded, the harness gets the error and no part of it."""
    pieces = [b"\x1b\x00not-", b"brotli-", b"really"]
    headers = (STREAM if streamed else [(b"content-type", b"application/json")]) + [(b"content-encoding", b"br")]

    async def go(root, refuse):
        async with Rig(root, stub=Scripted(pieces, headers)) as rig:
            if refuse:
                _refuse_inserts(rig.recorder, "lineage_records")
            status, _headers, raw, _request = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=streamed))
            return status, raw, rig.records(), rig.actions(kind=EXCHANGE_KIND)
    status, raw, records, actions = run(go(tmp_path / "ok", False))
    assert status == 200 and raw == b"".join(pieces) and [a["state"] for a in actions] == ["completed"]
    assert "not read" in records[0].capture_notes["response_encoding"], records[0].capture_notes
    status, raw, records, actions = run(go(tmp_path / "refused", True))
    assert status == 503 and b"talima_response_not_recorded" in raw and pieces[0] not in raw
    assert records == [] and [a["state"] for a in actions] == ["incomplete"]


@pytest.mark.parametrize("sent, forwarded", [
    ("gzip, deflate, br, zstd", "gzip, deflate"), ("br;q=1.0, gzip;q=0.8, *;q=0.1", "gzip;q=0.8"),
    ("br", "identity"), ("zstd, br", "identity"), ("identity", "identity"), ("GZIP", "GZIP"),
    # what the harness refused (a weight of zero) is not asked for, and `*` stands for encodings not read here
    ("gzip;q=0, br", "identity"), ("identity;q=0, gzip;q=0.5, br", "gzip;q=0.5"), ("deflate; q=0.0 , gzip", "gzip"),
    ("", "identity"), ("*", "identity"),
])
def test_p4_the_request_asks_only_for_encodings_the_interceptor_reads(tmp_path, sent, forwarded):
    """The one header value that is not forwarded as sent, and why: a response the interceptor cannot read is a
    response whose tool uses it cannot record. The harness's order and weights are kept."""
    assert protocols.accept_encoding(sent) == forwarded

    async def go():
        async with Rig(tmp_path / "root") as rig:
            await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=False), {"accept-encoding": sent})
            # a harness that sends no such header (driven by hand: the test's own client always sends one)
            body = json.dumps(ask(ANTHROPIC, stream=False)).encode()
            scope, receive, send, _sent, _first, _leave = _by_hand(rig.app, ANTHROPIC, body)
            await rig.app(scope, receive, send)
            return rig.stub.requests
    first, second = run(go())
    assert dict(first["headers"])["accept-encoding"] == forwarded
    assert "accept-encoding" not in dict(second["headers"]), "a header the harness did not send was added"


def test_p5_a_header_value_that_is_not_ascii_is_forwarded_as_its_bytes(tmp_path):
    """A header's value is bytes on the wire. It reaches the upstream as the harness sent it, and a credential's
    fingerprint is over those bytes."""
    title, token = "caf\u00e9 \u2603".encode("utf-8"), "cl\u00e9-secr\u00e8te".encode("utf-8")

    async def go():
        async with Rig(tmp_path / "root") as rig:
            body = json.dumps(ask(ANTHROPIC, stream=False)).encode()
            scope, receive, send, sent, _first, _leave = _by_hand(
                rig.app, ANTHROPIC, body, [(b"x-title", title), (b"x-custom-token", token)])
            await rig.app(scope, receive, send)
            return sent, rig.stub.requests, rig.actions(kind=EXCHANGE_KIND)
    sent, requests, actions = run(go())
    assert sent[0]["status"] == 200, sent
    raw = dict(requests[0]["raw_headers"])
    assert raw[b"x-title"] == title and raw[b"x-custom-token"] == token
    assert [a["state"] for a in actions] == ["completed"]
    recorded = {c["header"]: c["fingerprint"] for c in json.loads(actions[0]["credentials_json"])}
    assert recorded["x-custom-token"] == hashlib.sha256(token).hexdigest()[:16]


# ---------------------------------------------------------------- T4: credentials

def test_t4_a_key_is_stored_as_its_fingerprint_and_its_value_nowhere(tmp_path, caplog):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))
            await rig.post(OPENAI, ask(OPENAI, stream=False))
            rig.inner.seal_once()
            return rig.actions(kind=EXCHANGE_KIND), rig.stub.requests
    with caplog.at_level(logging.DEBUG):
        exchanges, requests = run(go())
    assert all(dict(r["headers"])["x-api-key"] == KEY for r in requests), "the key did not pass through"
    assert all(dict(r["headers"])["authorization"] == f"Bearer {BEARER}" for r in requests)
    expected = [{"header": "authorization", "fingerprint": hashlib.sha256(f"Bearer {BEARER}".encode()).hexdigest()[:16]},
                {"header": "x-api-key", "fingerprint": hashlib.sha256(KEY.encode()).hexdigest()[:16]}]
    assert [json.loads(e["credentials_json"]) for e in exchanges] == [expected, expected]
    files = [p for p in (tmp_path / "root").rglob("*") if p.is_file()]
    assert len(files) >= 4, files
    for secret in (KEY.encode(), BEARER.encode()):
        for p in files:
            data = p.read_bytes()
            assert secret not in data and secret.decode().encode("utf-16-le") not in data, f"{p.name} holds a key"
        assert secret.decode() not in caplog.text, "a log line holds a key"
    # nor inside a kept request body, which is compressed
    conn = sqlite3.connect(tmp_path / "root" / "kb.db")
    try:
        import zlib
        for (body,) in conn.execute("SELECT body FROM request_bodies"):
            assert KEY.encode() not in zlib.decompress(body) and BEARER.encode() not in zlib.decompress(body)
    finally:
        conn.close()


def test_t4b_a_key_under_another_headers_name_or_in_the_urls_query_passes_through_and_is_kept_nowhere(tmp_path,
                                                                                                    caplog):
    """Providers name their key headers differently, and some take the key in the URL. Each such header is
    recorded by name and fingerprint; a key in the query is forwarded and recorded nowhere, a log line included
    (the network client's own request line names the whole URL, and is not written)."""
    named = {"x-goog-api-key": "goog-not-a-real-key-W10c-1b7d", "api-key": "azure-not-a-real-key-W10c-9c2e",
             "cookie": "session=not-a-real-cookie-W10c-5a1f"}
    in_query = "query-not-a-real-key-W10c-3e8b"

    async def go():
        async with Rig(tmp_path / "root") as rig:
            status, _h, _raw, _request = await rig.post(OPENAI + "?key=" + in_query, ask(OPENAI, stream=True),
                                                        headers=named)
            rig.inner.seal_once()
            return status, rig.actions(kind=EXCHANGE_KIND), rig.stub.requests
    with caplog.at_level(logging.DEBUG):
        status, exchanges, requests = run(go())
    assert status == 200 and requests[0]["query"] == "key=" + in_query, "the query did not pass through as sent"
    sent = dict(requests[0]["headers"])
    assert all(sent[name] == value for name, value in named.items()), "a key header did not pass through"
    recorded = {c["header"]: c["fingerprint"] for c in json.loads(exchanges[0]["credentials_json"])}
    assert {name: recorded.get(name) for name in named} == \
        {name: hashlib.sha256(value.encode()).hexdigest()[:16] for name, value in named.items()}
    assert exchanges[0]["target"].endswith(OPENAI) and "?" not in exchanges[0]["target"]
    for secret in [in_query, *named.values()]:
        for p in (f for f in (tmp_path / "root").rglob("*") if f.is_file()):
            assert secret.encode() not in p.read_bytes(), f"{p.name} holds a key"
        assert secret not in caplog.text, "a log line holds a key"


# ---------------------------------------------------------------- T5: tool use

def _second_turn(path: str, first_raw: bytes, *, is_error: bool = False) -> dict:
    """The harness's next request: the history, the model's tool use as it asked it, and the tool's result."""
    facts = protocols.read_response(protocols.protocol_of(path), first_raw, streamed=True)
    use = facts.uses[0]
    user = {"role": "user", "content": "What is the weather in Paris?"}
    if path == OPENAI:
        return ask(path, stream=True, tools=True, messages=[user, {"role": "assistant", "content": None, "tool_calls": [
            {"id": use.tool_use_id, "type": "function", "function": {"name": use.name, "arguments": '{"city": "Paris"}'}}]},
            {"role": "tool", "tool_call_id": use.tool_use_id, "content": "18C and clear"}])
    return ask(path, stream=True, tools=True, messages=[user, {"role": "assistant", "content": [
        {"type": "tool_use", "id": use.tool_use_id, "name": use.name, "input": {"city": "Paris"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": use.tool_use_id,
                                      "content": "18C and clear", **({"is_error": True} if is_error else {})}]}])


@pytest.mark.parametrize("path", [ANTHROPIC, OPENAI])
def test_t5_a_two_turn_tool_use_session_is_one_intercepted_action_with_its_result_linked(tmp_path, path):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            _s, _h, first, _r = await rig.post(path, ask(path, stream=True, tools=True))
            pending = rig.actions(kind=TOOL_USE_KIND)
            second = _second_turn(path, first)
            _s, _h, answer, _r = await rig.post(path, second)
            linked = rig.actions(kind=TOOL_USE_KIND)
            await rig.post(path, second)                 # the same history again: the result is not new
            return pending, linked, rig.actions(), rig.records(), answer, rig.recorder.lineage.action_counts()
    pending, linked, actions, records, answer, counts = run(go())
    assert ANSWER.encode()[:10] in answer
    assert len(pending) == 1 and pending[0]["state"] == "requested" and pending[0]["record_hash"] is None
    assert pending[0]["evidence_class"] == "intercepted" and pending[0]["lineage_id"] == records[0].lineage_id
    assert pending[0]["method"] == protocols.protocol_of(path) and pending[0]["target"].endswith(":get_weather")
    assert tool_id_of(pending[0]["target"]).startswith(("toolu_stub_", "call_stub_"))
    assert len(linked) == 1 and linked[0]["action_id"] == pending[0]["action_id"]
    assert linked[0]["state"] == "completed" and linked[0]["status"] == "reported"
    assert linked[0]["response_hash"] == protocols.result_hash("18C and clear")
    assert linked[0]["record_hash"] == act.record_hash(linked[0])
    # one intercepted action in the whole session, three exchanges, and the two classes never mixed
    assert [a["kind"] for a in actions if a["evidence_class"] == "intercepted"] == [TOOL_USE_KIND]
    assert [a["kind"] for a in actions if a["evidence_class"] == "runtime"] == [EXCHANGE_KIND] * 3
    assert counts["intercepted"] == {"completed": 1} and counts["runtime"]["completed"] == 3
    assert [a for a in actions if a["kind"] == TOOL_USE_KIND][0]["record_hash"] == linked[0]["record_hash"], \
        "a result sent again rewrote the action"
    assert len(records) == 3
    # the record of each exchange says which tool use its request's results were linked to (the first: no results)
    said = [r.capture_notes.get("tool_results") for r in records]
    assert said[0] is None and linked[0]["action_id"] in said[1] and said[2].endswith(": none"), said


def test_t5b_a_result_the_harness_calls_an_error_reads_reported_error(tmp_path):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            _s, _h, first, _r = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))
            await rig.post(ANTHROPIC, _second_turn(ANTHROPIC, first, is_error=True))
            return rig.actions(kind=TOOL_USE_KIND)
    uses = run(go())
    assert [(u["state"], u["status"]) for u in uses] == [("completed", "reported_error")]


def test_t5c_an_outcome_is_written_once_and_a_request_that_would_write_it_again_writes_nothing(tmp_path):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            _s, _h, first, _r = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))
            await rig.post(ANTHROPIC, _second_turn(ANTHROPIC, first))
            linked = rig.actions(kind=TOOL_USE_KIND)[0]
            store = rig.recorder.lineage
            again = {"state": "completed", "status": "reported_error", "response_hash": "ab" * 32,
                     "completed_at": linked["completed_at"] + 1}
            direct = store.action_outcome(linked["action_id"], record_hash="cd" * 32, **again)
            before = len(rig.actions())
            row = {**linked, "action_id": "a-new-exchange", "kind": EXCHANGE_KIND, "evidence_class": "runtime",
                   "state": "requested", "lineage_id": "l-new"}
            with pytest.raises(RuntimeError, match="was not `requested`"):
                store.exchange_requested(row, request_hash=hashlib.sha256(b"{}").hexdigest(), request_body=b"{}",
                                         encoding=REQUEST_ENCODING_HTTP_BODY,
                                         outcomes=[(linked["action_id"], again, "cd" * 32)])
            kept = store.request_body(hashlib.sha256(b"{}").hexdigest())
            return direct, linked, rig.actions(kind=TOOL_USE_KIND)[0], before, len(rig.actions()), kept
    direct, linked, after, before, count, kept = run(go())
    assert direct == 0 and after == linked, "a written outcome was written again"
    assert count == before and kept is None, "a request whose write failed left its row or its bytes behind"


class Conversation:
    """A harness's side of a tool-use conversation, at the recorder: each `turn` is one exchange, its request the
    history so far (read by `protocols.read_request`, as the interceptor reads it) and its response the tool uses
    the model asks for. The requests are real request bodies; no model and no upstream is needed."""

    def __init__(self, rig, protocol: str = protocols.ANTHROPIC):
        self.rec, self.protocol = rig.recorder, protocol
        self.messages = [{"role": "user", "content": "What is the weather in Paris?"}]

    def asked(self, tool_use_id: str, name: str, value) -> None:
        """The harness adds the model's ask to its history, as the protocol spells it."""
        if self.protocol == protocols.OPENAI:
            self.messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": tool_use_id, "type": "function", "function": {"name": name, "arguments": value}}]})
        else:
            self.messages.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": tool_use_id, "name": name, "input": value}]})

    def result(self, tool_use_id: str, content: str, is_error: bool = False) -> None:
        if self.protocol == protocols.OPENAI:
            self.messages.append({"role": "tool", "tool_call_id": tool_use_id, "content": content})
        else:
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": content,
                 **({"is_error": True} if is_error else {})}]})

    def turn(self, *asks, body: "bytes | None" = None) -> bytes:
        """One exchange: the request (the history, or `body` sent again) and a response asking `asks`."""
        body = body or json.dumps({"model": "m", "messages": self.messages}).encode()
        path = ANTHROPIC if self.protocol == protocols.ANTHROPIC else OPENAI
        row = self.rec.exchange_requested(
            lineage_id=uuid.uuid4().hex, protocol=self.protocol, path=path, url="http://127.0.0.1:9" + path,
            harness_body=body, forwarded_body=body, headers=[], identity=None, mechanism=None,
            results=protocols.read_request(self.protocol, body).results)
        uses = tuple(protocols.ToolUse(i, n, protocols.tool_use_hash(self.protocol, i, n, v)) for i, n, v in asks)
        self.rec.exchange_recorded(row, protocol=self.protocol, path=path, harness_hash="cd" * 32, response=b"{}",
                                   facts=protocols.ResponseFacts(uses=uses), state="completed", status="200")
        return body


def _uses(rig) -> list:
    return [(a["target"], a["state"], a["status"], a["response_hash"])
            for a in sorted(rig.actions(kind=TOOL_USE_KIND), key=lambda a: (a["requested_at"], a["action_id"]))]


SUNNY, DELETED = protocols.result_hash("sunny"), protocols.result_hash("deleted")


def test_t5d_a_result_answers_the_ask_its_own_request_repeats_before_it(tmp_path):
    """A harness sends its whole history with every request. A result is linked to the recorded tool use that
    the request itself repeats before it (the same id, name and input), never to another by its id alone: so an
    id the model gives twice, and a request sent twice, link each result to its own ask (the verifier's V3)."""
    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig)
            c.turn(("toolu_same", "get_weather", {"city": "Paris"}))        # the model asks
            c.asked("toolu_same", "get_weather", {"city": "Paris"})
            c.result("toolu_same", "sunny")
            second = c.turn(("toolu_same", "delete_file", {"path": "/tmp/x"}))   # it asks again, under the same id
            after_second = _uses(rig)
            c.turn(body=second)                                             # the harness sends that request again
            after_resend = _uses(rig)
            c.asked("toolu_same", "delete_file", {"path": "/tmp/x"})
            c.result("toolu_same", "deleted")
            c.turn()
            return after_second, after_resend, _uses(rig)
    after_second, after_resend, final = run(go())
    weather, delete = "tool_use:toolu_same:get_weather", "tool_use:toolu_same:delete_file"
    assert after_second == [(weather, "completed", "reported", SUNNY), (delete, "requested", None, None)]
    assert after_resend == after_second, "the first ask's result was linked to the second ask"
    assert final == [(weather, "completed", "reported", SUNNY), (delete, "completed", "reported", DELETED)]


def test_t5e_the_same_ask_twice_is_answered_in_order_and_a_request_sent_again_answers_nothing_new(tmp_path):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            c, ask = Conversation(rig), ("toolu_same", "get_weather", {"city": "Paris"})
            c.turn(ask)
            c.asked(*ask)
            c.result("toolu_same", "sunny")
            second = c.turn(ask)                         # the very same ask again: id, name and input
            c.turn(body=second)
            after_resend = _uses(rig)
            c.asked(*ask)
            c.result("toolu_same", "the tool failed", is_error=True)
            c.turn()
            return after_resend, _uses(rig)
    after_resend, final = run(go())
    target = "tool_use:toolu_same:get_weather"
    assert after_resend == [(target, "completed", "reported", SUNNY), (target, "requested", None, None)]
    assert final == [(target, "completed", "reported", SUNNY),
                     (target, "completed", "reported_error", protocols.result_hash("the tool failed"))]


def test_t5f_another_conversation_cannot_answer_a_tool_use_by_naming_its_id(tmp_path):
    """The verifier's forgeries: a result under the id with no ask before it; under the id with another ask before
    it; and under an id that differs past the 200 characters a row keeps of it."""
    long_id = "a" * 204
    async def go():
        async with Rig(tmp_path / "root") as rig:
            Conversation(rig).turn(("toolu_A1", "get_weather", {"city": "Paris"}), (long_id + "1", "get_weather", {}))
            bare = Conversation(rig)
            bare.result("toolu_A1", "FORGED by B")
            bare.turn()
            other = Conversation(rig)
            other.asked("toolu_A1", "get_weather", {"city": "Rome"})
            other.result("toolu_A1", "FORGED by B")
            other.asked(long_id + "2", "get_weather", {})
            other.result(long_id + "2", "FORGED by B")
            other.turn()
            return _uses(rig)
    assert [(state, status) for _target, state, status, _hash in run(go())] == [("requested", None)] * 2


def test_t5h_two_tool_uses_asked_at_once_are_each_answered_by_their_own_result(tmp_path):
    """A model asks for several tools in one response and the harness answers them in one request: each result
    goes to its own ask, whatever order the harness gives them in."""
    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig)
            c.turn(("toolu_a", "get_weather", {"city": "Paris"}), ("toolu_b", "get_time", {"city": "Paris"}))
            c.asked("toolu_a", "get_weather", {"city": "Paris"})
            c.messages[-1]["content"].append({"type": "tool_use", "id": "toolu_b", "name": "get_time",
                                              "input": {"city": "Paris"}})
            c.result("toolu_b", "noon")                  # answered in the other order
            c.result("toolu_a", "sunny")
            c.turn()
            return _uses(rig)
    assert sorted(run(go())) == [
        ("tool_use:toolu_a:get_weather", "completed", "reported", SUNNY),
        ("tool_use:toolu_b:get_time", "completed", "reported", protocols.result_hash("noon"))]


def test_t5i_two_conversations_holding_the_same_ask_each_get_their_own_result(tmp_path):
    """Some providers number their tool calls from the start in every conversation (`call_0`), so two
    conversations can hold the very same ask at once: same id, name and input. Each report goes to an ask still
    waiting (delta check 1's D1: the second conversation's result went to the first's row, and the first's own
    report then went nowhere)."""
    ask = ("call_0", "get_time", {})

    async def go():
        async with Rig(tmp_path / "root") as rig:
            a, b = Conversation(rig, protocols.OPENAI), Conversation(rig, protocols.OPENAI)
            a.turn(("call_0", "get_time", "{}"))
            b.turn(("call_0", "get_time", "{}"))
            b.asked("call_0", "get_time", "{}")
            b.result("call_0", "noon in B")
            b.turn()
            after_b = _uses(rig)
            a.asked("call_0", "get_time", "{}")
            a.result("call_0", "noon in A")
            a.turn()
            return after_b, _uses(rig)
    after_b, final = run(go())
    target = "tool_use:call_0:get_time"
    # B was asked last and reports first: its result goes to the ask made last, which is its own
    assert [s for _t, s, _st, _h in after_b] == ["requested", "completed"], after_b
    assert sorted(h for _t, _s, _st, h in final) == sorted([protocols.result_hash("noon in A"),
                                                           protocols.result_hash("noon in B")]), final
    assert all((t_, s, st) == (target, "completed", "reported") for t_, s, st, _h in final)


def test_t5p_two_conversations_given_one_id_for_different_asks_each_answer_their_own(tmp_path):
    """The id is the provider's and may repeat across conversations. Two tool uses waiting under one id, asking
    different things: each result goes to the ask its own request repeats, whichever was asked last. Never by the
    id alone."""
    async def go():
        async with Rig(tmp_path / "root") as rig:
            a, b = Conversation(rig), Conversation(rig)
            a.turn(("toolu_1", "get_weather", {"city": "Paris"}))
            b.turn(("toolu_1", "delete_file", {"path": "/tmp/x"}))          # asked last, under the same id
            a.asked("toolu_1", "get_weather", {"city": "Paris"})
            a.result("toolu_1", "sunny")
            a.turn()
            after_a = _uses(rig)
            b.asked("toolu_1", "delete_file", {"path": "/tmp/x"})
            b.result("toolu_1", "deleted")
            b.turn()
            return after_a, _uses(rig)
    after_a, final = run(go())
    weather, delete = "tool_use:toolu_1:get_weather", "tool_use:toolu_1:delete_file"
    assert after_a == [(weather, "completed", "reported", SUNNY), (delete, "requested", None, None)], after_a
    assert final == [(weather, "completed", "reported", SUNNY), (delete, "completed", "reported", DELETED)]


def test_t5q_an_ask_nobody_answered_stays_waiting_when_the_same_ask_is_made_again_and_answered(tmp_path):
    """A session that ends before its tool use is answered leaves that ask waiting for good. When a later session
    is asked the very same thing (the provider's ids start again) and reports, the report is for the ask just
    made: the latest one waiting, not the abandoned one."""
    ask = ("toolu_stub_0001", "get_weather", {"city": "Paris"})

    async def go():
        async with Rig(tmp_path / "root") as rig:
            Conversation(rig).turn(ask)                  # asked, and the session ends there
            c = Conversation(rig)
            c.turn(ask)
            c.asked(*ask)
            c.result(ask[0], "sunny")
            c.turn()
            return _uses(rig)
    target = "tool_use:toolu_stub_0001:get_weather"
    assert run(go()) == [(target, "requested", None, None), (target, "completed", "reported", SUNNY)]


def test_t5j_a_second_session_whose_ids_start_again_is_answered_like_the_first(tmp_path):
    """Two sessions on one store, the provider's ids starting again in the second: the same requests, byte for
    byte, as the first session's. The second session's tool use is answered by the second session's report."""
    async def go():
        async with Rig(tmp_path / "root") as rig:
            for _session in (1, 2):
                c = Conversation(rig)
                c.turn(("toolu_stub_0001", "get_weather", {"city": "Paris"}))
                c.asked("toolu_stub_0001", "get_weather", {"city": "Paris"})
                c.result("toolu_stub_0001", "sunny")
                c.turn()
            return _uses(rig)
    assert run(go()) == [("tool_use:toolu_stub_0001:get_weather", "completed", "reported", SUNNY)] * 2


def test_t5k_a_harness_that_trims_its_history_still_answers_its_latest_ask(tmp_path):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            c, ask = Conversation(rig), ("toolu_same", "get_weather", {"city": "Paris"})
            c.turn(ask)
            c.asked(*ask)
            c.result("toolu_same", "sunny")
            c.turn(ask)                                  # the very same ask again
            c.messages = c.messages[:1]                  # the harness drops the earlier turns
            c.asked(*ask)
            c.result("toolu_same", "cloudy")
            c.turn()
            return _uses(rig)
    target = "tool_use:toolu_same:get_weather"
    assert run(go()) == [(target, "completed", "reported", SUNNY),
                         (target, "completed", "reported", protocols.result_hash("cloudy"))]


def test_t5l_a_result_from_an_earlier_round_is_history_and_answers_nothing(tmp_path):
    """An old round, sent again as history: an assistant message follows its result, and the store already holds
    as many such reports as the request does. It is not linked to an ask that happens to be waiting under the
    same id, name and input."""
    async def go():
        async with Rig(tmp_path / "root") as rig:
            old, ask = Conversation(rig), ("call_0", "get_time", {})
            old.turn(ask)
            old.asked(*ask)
            old.result("call_0", "noon")
            old.turn()
            Conversation(rig).turn(ask)                  # another conversation asks the same; it waits
            old.messages.append({"role": "assistant", "content": [{"type": "text", "text": "It is noon."}]})
            old.messages.append({"role": "user", "content": "Thanks. And tomorrow?"})
            old.turn()                                   # the old conversation goes on: its round is history
            return _uses(rig)
    states = [(s, st) for _t, s, st, _h in run(go())]
    assert states == [("completed", "reported"), ("requested", None)], states


@pytest.mark.parametrize("protocol, wrote, sent_back", [
    (protocols.ANTHROPIC, '{"t": 30.0, "n": 1e2}', '{"t":30,"n":100}'),
    (protocols.OPENAI, '{"t": 30.0}', '{"t":30}'),
    (protocols.ANTHROPIC, '{"big": 12345678901234567890}', '{"big": 12345678901234567000}'),
    (protocols.OPENAI, '{"x": [1.50, -0.0, 2E0]}', '{"x":[1.5,0,2]}'),
    # a number past every float is the infinity a float reader makes of it, however it was written, and a value
    # that holds one is still compared whatever order its members come in (delta check 2's E9)
    (protocols.ANTHROPIC, '{"limit": 1e400}', '{"limit": 1' + "0" * 400 + '}'),
    (protocols.OPENAI, '{"a": 1, "limit": 1e400}', '{"limit": 1e400, "a": 1}'),
])
def test_t5m_a_harness_that_respells_a_number_still_answers_its_ask(tmp_path, protocol, wrote, sent_back):
    """A harness parses the model's ask and writes it again in its own language's spelling: `30.0` comes back as
    `30`, `1e2` as `100`, and a number too large for its floats comes back rounded. The ask is compared by its
    values, not its spelling (delta check 1's D2)."""
    as_model = wrote if protocol == protocols.OPENAI else json.loads(wrote)
    as_harness = sent_back if protocol == protocols.OPENAI else json.loads(sent_back)

    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig, protocol)
            c.turn(("call_n", "set_timer", as_model))
            c.asked("call_n", "set_timer", as_harness)
            c.result("call_n", "sunny")
            c.turn()
            return _uses(rig)
    assert run(go()) == [("tool_use:call_n:set_timer", "completed", "reported", SUNNY)]
    assert protocols.tool_use_hash(protocol, "i", "n", as_model) != protocols.tool_use_hash(
        protocol, "i", "n", json.loads('{"t": 31}') if protocol == protocols.ANTHROPIC else '{"t": 31}')


def test_t5n_a_prefill_after_the_results_is_the_harnesss_words_and_the_result_is_still_new(tmp_path):
    """A request may end with an assistant message the harness wrote itself, for the model to go on from (a
    prefill). It is not a turn of the model's: the results before it still answer the model's last turn."""
    async def go():
        async with Rig(tmp_path / "root") as rig:
            c, ask = Conversation(rig), ("toolu_p", "get_weather", {"city": "Paris"})
            c.turn(ask)
            c.asked(*ask)
            c.result("toolu_p", "sunny")
            c.messages.append({"role": "assistant", "content": "The weather in Paris is"})
            c.turn()
            return _uses(rig)
    assert run(go()) == [("tool_use:toolu_p:get_weather", "completed", "reported", SUNNY)]


@pytest.mark.parametrize("wrote, sent_back", [("", "{}"), ("{}", ""), (None, "{}"), ("  ", "{ }"), ("", None),
                                              ("null", "{}"), ("{}", " null ")])
def test_t5o_no_arguments_are_no_arguments_however_an_openai_harness_spells_them(tmp_path, wrote, sent_back):
    """A model that calls a tool with no arguments may write an empty string, and a harness that parses what it
    was asked sends back `{}`: both ask nothing, and are one ask. An ask with arguments is another."""
    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig, protocols.OPENAI)
            c.turn(("call_e", "get_time", wrote))
            c.asked("call_e", "get_time", sent_back)
            c.result("call_e", "sunny")
            c.turn()
            return _uses(rig)
    assert run(go()) == [("tool_use:call_e:get_time", "completed", "reported", SUNNY)]
    assert protocols.tool_use_hash(protocols.OPENAI, "i", "n", wrote) != protocols.tool_use_hash(
        protocols.OPENAI, "i", "n", '{"zone": "UTC"}')
    assert protocols.tool_use_hash(protocols.OPENAI, "i", "n", "") != protocols.tool_use_hash(
        protocols.OPENAI, "i", "n", "not json")
    assert protocols.tool_use_hash(protocols.ANTHROPIC, "i", "n", None) == protocols.tool_use_hash(
        protocols.ANTHROPIC, "i", "n", {}), "an input of null and an empty input are two asks"


A_ASK, B_ASK, C_ASK = (("toolu_a", "get_weather", {"city": "Paris"}), ("toolu_b", "get_time", {"city": "Paris"}),
                       ("toolu_c", "get_news", {"city": "Paris"}))


def _text_turn(c, text: str) -> None:
    c.messages.append({"role": "assistant", "content": [{"type": "text", "text": text}]})


def _one_message_each(c):
    for ask in (A_ASK, B_ASK, C_ASK):
        c.asked(*ask)
    for ask in (C_ASK, A_ASK, B_ASK):
        c.result(ask[0], "sunny")


def _text_after_the_ask(c):
    c.messages = c.messages[:1]
    c.asked(*A_ASK)
    _text_turn(c, "Let me check.")
    c.result("toolu_a", "sunny")
    for ask in (B_ASK, C_ASK):
        c.asked(*ask)
        c.result(ask[0], "sunny")


def _unrolled(c):
    for ask in (A_ASK, B_ASK, C_ASK):                    # one turn of the model, sent back as three rounds
        c.asked(*ask)
        c.result(ask[0], "sunny")


def _late(c):
    c.asked(*A_ASK)
    c.asked(*B_ASK)
    c.asked(*C_ASK)
    c.messages.append({"role": "user", "content": "One moment."})
    c.turn()                                             # a request that reports nothing: the three still wait
    _text_turn(c, "Take your time.")
    for ask in (A_ASK, B_ASK, C_ASK):
        c.result(ask[0], "sunny")


@pytest.mark.parametrize("protocol", [protocols.ANTHROPIC, protocols.OPENAI])
@pytest.mark.parametrize("sends", [_one_message_each, _text_after_the_ask, _unrolled, _late],
                         ids=lambda f: f.__name__.strip("_"))
def test_t5r_a_turn_sent_back_in_another_shape_still_answers_every_ask(tmp_path, protocol, sends):
    """A harness need not send the model's turn back as the one message it came as. Three tool uses asked in one
    response, sent back one assistant message each; with a text-only assistant message after an ask; unrolled
    into three rounds; and reported one request late, after a later turn: every ask is answered (delta check 2's
    E1 and E5: only the last message's ask was)."""
    def spelled(ask):
        return (ask[0], ask[1], json.dumps(ask[2])) if protocol == protocols.OPENAI else ask

    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig, protocol)
            c.turn(*(spelled(a) for a in (A_ASK, B_ASK, C_ASK)))
            real, c.asked = c.asked, lambda i, n, v: real(*spelled((i, n, v)))
            if protocol == protocols.OPENAI and sends is _text_after_the_ask:
                c.messages = c.messages[:1]
                c.asked(*A_ASK)
                c.messages.append({"role": "assistant", "content": "Let me check."})
                c.result("toolu_a", "sunny")
                for ask in (B_ASK, C_ASK):
                    c.asked(*ask)
                    c.result(ask[0], "sunny")
            elif protocol == protocols.OPENAI and sends is _late:
                for ask in (A_ASK, B_ASK, C_ASK):
                    c.asked(*ask)
                c.messages.append({"role": "user", "content": "One moment."})
                c.turn()
                c.messages.append({"role": "assistant", "content": "Take your time."})
                for ask in (A_ASK, B_ASK, C_ASK):
                    c.result(ask[0], "sunny")
            else:
                sends(c)
            c.turn()
            return _uses(rig)
    assert sorted((s, st, h) for _t, s, st, h in run(go())) == [("completed", "reported", SUNNY)] * 3


def test_t5s_a_result_given_twice_in_one_request_is_one_report(tmp_path):
    """One ask is answered once: the first result after it under its id. A second result under the same id, with
    no ask of its own before it, reports nothing, so it cannot become the outcome of another tool use that asked
    the same (delta check 2's E7)."""
    ask = ("call_0", "get_time", {})

    async def go():
        async with Rig(tmp_path / "root") as rig:
            Conversation(rig).turn(ask)                  # another conversation asked the same; it waits
            c = Conversation(rig)
            c.turn(ask)
            c.asked(*ask)
            c.result("call_0", "noon")
            c.result("call_0", "midnight")               # a second result under the id, with no ask of its own
            c.turn()
            return _uses(rig)
    assert [(s, st, h) for _t, s, st, h in run(go())] == [
        ("requested", None, None), ("completed", "reported", protocols.result_hash("noon"))]


def test_t5u_a_report_already_recorded_and_followed_by_the_models_words_is_history(tmp_path):
    """A request that goes on from a cut answer ends with the assistant's partial words. The result before them
    was reported, and recorded, by the request before: sent again, it is history, and is not taken for the report
    of another tool use that asked the same (delta check 2's E7)."""
    ask = ("call_0", "get_time", {})

    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig)
            c.turn(ask)
            c.asked(*ask)
            c.result("call_0", "noon")
            c.turn()                                     # reported, and recorded
            Conversation(rig).turn(ask)                  # another conversation asks the same; it waits
            c.messages.append({"role": "assistant", "content": "It is noon, and"})
            c.turn()                                     # the first goes on from its cut answer
            return _uses(rig)
    assert [(s, st) for _t, s, st, _h in run(go())] == [("completed", "reported"), ("requested", None)]


def test_t5v_history_this_store_never_recorded_does_not_answer_the_ask_just_made(tmp_path):
    """A conversation begun before the store saw it carries rounds the store holds nothing of. When the model then
    asks the very same thing again and the harness does not answer (it sends something else), the old round's
    result is not the answer to the new ask: only the last time a request holds an ask can be reported new."""
    ask = ("call_0", "get_time", {})

    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig)
            c.asked(*ask)
            c.result("call_0", "nine in the morning")    # a round from before this store
            c.messages.append({"role": "assistant", "content": [{"type": "text", "text": "It was nine."}]})
            c.messages.append({"role": "user", "content": "And now?"})
            c.turn(ask)                                  # the model asks the same again: this one is recorded
            c.asked(*ask)
            c.messages.append({"role": "user", "content": "Never mind."})
            c.turn()
            return _uses(rig)
    assert [(s, st) for _t, s, st, _h in run(go())] == [("requested", None)]


def _one_turn(c, *asks) -> None:
    """The harness sends back one turn of the model that asked several tools: one assistant message."""
    if c.protocol == protocols.OPENAI:
        c.messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": i, "type": "function", "function": {"name": n, "arguments": json.dumps(v)}} for i, n, v in asks]})
    else:
        c.messages.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": i, "name": n, "input": v} for i, n, v in asks]})


def _spelled(protocol, ask):
    return (ask[0], ask[1], json.dumps(ask[2])) if protocol == protocols.OPENAI else ask


def _by_ask(rig) -> dict:
    return {a["request_hash"]: (a["state"], a["response_hash"]) for a in rig.actions(kind=TOOL_USE_KIND)}


@pytest.mark.parametrize("protocol", [protocols.ANTHROPIC, protocols.OPENAI])
def test_t5x_two_asks_under_one_id_each_take_their_own_result_in_order(tmp_path, protocol):
    """Some providers give every call of one function the same id (the function's name). Two asks under one id
    in one response, answered in the order they were asked: the first result is the first ask's (delta check
    3's F1: the later ask replaced the earlier one, so `read_file(b.py)` was recorded with a.py's contents)."""
    a, b = ("read_file", "read_file", {"path": "a.py"}), ("read_file", "read_file", {"path": "b.py"})

    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig, protocol)
            c.turn(_spelled(protocol, a), _spelled(protocol, b))
            _one_turn(c, a, b)
            c.result("read_file", "CONTENTS OF a.py")
            c.result("read_file", "CONTENTS OF b.py")
            c.turn()
            return _by_ask(rig)
    assert run(go()) == {
        protocols.tool_use_hash(protocol, *_spelled(protocol, a)): ("completed", protocols.result_hash("CONTENTS OF a.py")),
        protocols.tool_use_hash(protocol, *_spelled(protocol, b)): ("completed", protocols.result_hash("CONTENTS OF b.py"))}


@pytest.mark.parametrize("protocol", [protocols.ANTHROPIC, protocols.OPENAI])
def test_t5y_the_same_ask_made_twice_in_one_response_is_answered_twice(tmp_path, protocol):
    """One response that asks the very same thing twice (one id, name and input) is two tool uses, and the
    request that answers both reports on both (delta check 3: one of the two stayed waiting for good)."""
    ask = ("call_0", "get_time", {})

    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig, protocol)
            c.turn(_spelled(protocol, ask), _spelled(protocol, ask))
            _one_turn(c, ask, ask)
            c.result("call_0", "noon")
            c.result("call_0", "a second past noon")
            c.turn()
            return _uses(rig)
    assert sorted((s, st, h) for _t, s, st, h in run(go())) == sorted(
        ("completed", "reported", protocols.result_hash(r)) for r in ("noon", "a second past noon"))


def test_t5z_a_poll_that_ends_every_request_with_its_own_words_is_still_answered_each_time(tmp_path):
    """A harness that polls one tool under a numbered id, keeps its whole history, gets the same result each time
    and ends every request with words of its own for the model to go on from. Each request holds one more answer
    than the store does: that one is new, though the store already holds its like (delta check 3's F4: taken
    for history, three reports of four were dropped)."""
    ask = ("call_0", "get_time", {})

    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig)
            c.turn(ask)
            for _round in range(4):
                c.asked(*ask)
                c.result("call_0", "noon")
                c.messages.append({"role": "assistant", "content": "The time is"})
                c.turn(ask)
                c.messages.pop()
            return _uses(rig)
    assert [(s, st) for _t, s, st, _h in run(go())] == [("completed", "reported")] * 4 + [("requested", None)]


def test_t5aa_an_ask_given_up_does_not_take_the_result_of_the_next_ask_under_its_id(tmp_path):
    """An ask the harness never answered (it said something else instead) is given up when the model asks again
    under the same id: the result that follows is the later ask's, not the earlier one's."""
    first, second = ("call_0", "delete_file", {"path": "/tmp/x"}), ("call_0", "get_time", {})

    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig)
            c.turn(first)
            c.asked(*first)
            c.messages.append({"role": "user", "content": "Never mind that."})
            c.turn(second)
            c.asked(*second)
            c.result("call_0", "noon")
            c.turn()
            return _by_ask(rig)
    assert run(go()) == {protocols.tool_use_hash(protocols.ANTHROPIC, *first): ("requested", None),
                         protocols.tool_use_hash(protocols.ANTHROPIC, *second): ("completed", protocols.result_hash("noon"))}


def test_t5w_what_linking_cannot_tell_apart_stays_as_the_page_says(tmp_path):
    """The limits `docs/INTERCEPTOR.md` states, pinned so the page and the code move together. Two tool uses with
    one id, name and input are one ask to the recorder. (1) A request sent again while another such ask waits is
    taken for that ask's report: the recorder cannot tell it from a second conversation reporting the same. (2) A
    request that is byte for byte the one a tool use was asked in reply to is taken for that request sent again,
    and reports nothing to that tool use."""
    ask = ("call_0", "get_time", {})

    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig)
            c.turn(ask)
            c.asked(*ask)
            c.result("call_0", "noon")
            sent = c.turn()                              # reported
            Conversation(rig).turn(ask)                  # another conversation asks the same; it waits
            c.turn(body=sent)                            # (1) the first sends its request again
            first = _uses(rig)
            polled = c.turn(ask)                         # the model asks the same again, in reply to these bytes
            c.turn(body=polled)                          # (2) and the harness sends those very bytes again
            return first, _uses(rig)
    first, second = run(go())
    assert [(s, st) for _t, s, st, _h in first] == [("completed", "reported")] * 2
    assert [(s, st) for _t, s, st, _h in second] == [("completed", "reported")] * 2 + [("requested", None)]


def test_t5g_an_openai_harness_that_respells_the_arguments_still_answers_its_ask(tmp_path):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig, protocols.OPENAI)
            c.turn(("call_1", "get_weather", '{"city": "Paris"}'))          # as the model wrote them
            c.asked("call_1", "get_weather", '{"city":"Paris"}')            # as the harness sends them back
            c.result("call_1", "sunny")
            c.turn()
            return _uses(rig)
    assert run(go()) == [("tool_use:call_1:get_weather", "completed", "reported", SUNNY)]


# ---------------------------------------------------------------- T6: the scope

def test_t6_an_intercepted_record_declares_itself_and_a_contradiction_is_named(tmp_path):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True))
            return rig.records()[0]
    rec = run(go())
    declared = record_scope.declaration(rec.record_scope)
    assert declared["record_scope"] == INTERCEPTED and "coverage" in declared
    for field in ("request_hash", "response_hash", "model_id", "learning_mode", "query_hash", "prompt_prefix_hash"):
        assert field in declared["changed_in_meaning"], field
    for field in ("model_digest", "inference_seed", "answer_hash", "reasoning_hash", "work_unit_id"):
        assert field in declared["undefined"], field
    assert record_scope.verify_scope(rec) == []
    empty = hashlib.sha256(b"").hexdigest()
    assert rec.query_hash == empty and rec.prompt_prefix_hash == empty and rec.learning_mode == "frozen"
    assert rec.accountable_identity == "tester@example.test" and rec.identity_mechanism is None
    assert rec.answer_hash is None and rec.model_digest is None and rec.inference_seed is None
    assert set(rec.capture_notes) >= {"model_digest", "inference_seed", "inference_config_hash", "environment_hash"}
    # a record that says what an intercepted record cannot
    import dataclasses
    for change, word in (({"model_id": "claude-x"}, "upstream-reported"), ({"answer_hash": "ab" * 32}, "answer_hash"),
                         ({"model_digest": "sha256:1"}, "model_digest"), ({"learning_mode": None}, "learning_mode"),
                         ({"request_hash": None}, "request_hash")):
        problems = record_scope.verify_scope(dataclasses.replace(rec, **change))
        assert problems and any(word in p for p in problems), (change, problems)


# ---------------------------------------------------------------- T7: the chain, the credential, the receipt

def _talima_verify(folder: Path, name: str, doc: dict, *, action: bool) -> "_proc.subprocess.CompletedProcess":
    """`talima-verify` as a stranger runs it: from a folder that holds the document and the instance's identity."""
    (folder / name).write_text(json.dumps(doc), encoding="utf-8")
    argv = (["--action", name] if action else [name]) + ["--identity", "identity.json"]
    return _proc.run([sys.executable, "-I", str(REPO / "scripts" / "talima_verify.py"), *argv], cwd=folder,
                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


def test_t7_the_chain_verifies_and_a_stranger_verifies_the_record_and_the_action(tmp_path):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            _s, _h, first, _r = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))
            await rig.post(ANTHROPIC, _second_turn(ANTHROPIC, first))
            assert rig.inner.seal_once() is not None
            use = rig.actions(kind=TOOL_USE_KIND)[0]
            exchange = rig.actions(kind=EXCHANGE_KIND)[0]
            rec = rig.records()[0]
            return (rig.recorder.verify(),
                    rig.recorder.credential(rec.lineage_id, disclose=("model_id", "record_scope", "request_hash")),
                    rig.recorder.receipt(use["action_id"]), rig.recorder.receipt(exchange["action_id"]),
                    rig.recorder.identity())
    verdict, credential, receipt, exchange_receipt, who = run(go())
    assert verdict["ok"] and verdict["chain"]["valid"] and verdict["records"]["verdict"] == "intact"
    assert verdict["actions"]["failed"] == [] and verdict["actions"]["sealed"] == 3
    empty = tmp_path / "stranger"
    empty.mkdir()
    (empty / "identity.json").write_text(json.dumps(who), encoding="utf-8")
    for name, doc, word in (("credential.json", credential, 'record_scope = "intercepted"'),
                            ("receipt.json", receipt, "action: intercepted tool_use anthropic-messages"),
                            ("receipt.json", receipt, "the recorder did not see the tool run"),
                            ("exchange.json", exchange_receipt, "action: runtime model_exchange POST")):
        done = _talima_verify(empty, name, doc, action=name != "credential.json")
        assert done.returncode == 0 and "VERIFIED" in done.stdout, (name, done.stdout[-1500:], done.stderr[-500:])
        assert word in done.stdout, (name, done.stdout[-1500:])
    # a changed byte in the store is named
    conn = sqlite3.connect(tmp_path / "root" / "kb.db")
    conn.execute("UPDATE action_records SET status = 'reported_error' WHERE kind = ?", (TOOL_USE_KIND,))
    conn.commit()
    conn.close()
    reopened = Recorder.open_at(tmp_path / "root")
    try:
        after = reopened.verify()
    finally:
        reopened.close()
    assert not after["ok"] and after["actions"]["failed"][0]["reason"].startswith("its fields do not hash")


def test_t7b_a_tool_use_never_answered_is_held_by_the_record_that_names_it(tmp_path):
    """A tool use has no outcome until a harness reports one, so no action seal covers it while it waits (the
    verifier's V4). The sealed record of the exchange names every tool use its response asked for, and `verify`
    holds the rows to that: one renamed, one deleted and one added are each named."""
    def tamper(statement: str, *values):
        conn = sqlite3.connect(tmp_path / "root" / "kb.db")
        conn.execute(statement, values)
        conn.commit()
        conn.close()

    def verified() -> dict:
        reopened = Recorder.open_at(tmp_path / "root")
        try:
            return reopened.verify()
        finally:
            reopened.close()

    async def go():
        async with Rig(tmp_path / "root") as rig:
            await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))       # asked, never answered
            assert rig.inner.seal_once() is not None
            return rig.recorder.verify(), rig.actions(kind=TOOL_USE_KIND)[0], rig.recorder.listing()
    verdict, use, listing = run(go())
    assert verdict["ok"] and verdict["tool_uses"] == {"records": 1, "named": 1, "failed": []}, verdict
    assert use["state"] == "requested" and use["record_hash"] is None and use["sealed_block"] is None
    assert listing[0]["tool_uses"][0]["evidence_class"] == "intercepted", "the listing does not say what was seen"
    tamper("UPDATE action_records SET target = ? WHERE action_id = ?", "tool_use:x:transfer_funds", use["action_id"])
    renamed = verified()
    assert not renamed["ok"] and renamed["tool_uses"]["failed"][0]["lineage_id"] == use["lineage_id"], renamed
    tamper("DELETE FROM action_records WHERE action_id = ?", use["action_id"])
    deleted = verified()
    assert not deleted["ok"] and len(deleted["tool_uses"]["failed"]) == 1, deleted
    tamper("INSERT INTO action_records (action_id, kind, evidence_class, method, target, request_hash, "
           "credentials_json, lineage_id, requested_at, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
           "planted", TOOL_USE_KIND, "intercepted", use["method"], use["target"], use["request_hash"], "[]",
           "no-such-record", 1.0, "requested")
    added = verified()
    assert not added["ok"] and {f["lineage_id"] for f in added["tool_uses"]["failed"]} >= {"no-such-record"}, added


@pytest.mark.parametrize("column, value", [
    ("method", "openai-chat"), ("requested_at", 1.0), ("trigger_route", "intercept:/v1/other"),
    ("identity_commitment", "ab" * 32), ("credentials_json", '[{"header":"x","fingerprint":"y"}]'),
    ("accountable_identity", "mallory@example.test"), ("identity_salt", "cd" * 32),
    # what a waiting row does not have yet, and must not be given (delta check 3's F10)
    ("sealed_block", 0), ("record_hash", "ab" * 32),
])
def test_t7c_every_field_of_a_waiting_tool_use_is_held_not_only_its_name(tmp_path, column, value):
    """The record names a tool use by the hash of its whole row as it was asked, so nothing of a waiting row can
    be rewritten: not its protocol, its time, its route, nor who it is attributed to (delta check 1's D4). The
    hash holds the identity as a commitment; the name and the salt beside it are held by opening it (delta check
    2's E2)."""
    async def go():
        async with Rig(tmp_path / "root", NEXUS_INTERCEPT_OWNER="owner@example.test") as rig:
            await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))
            rig.inner.seal_once()
            return rig.recorder.verify(), rig.actions(kind=TOOL_USE_KIND)[0]
    verdict, use = run(go())
    assert verdict["ok"] and use["accountable_identity"] == "owner@example.test", verdict
    conn = sqlite3.connect(tmp_path / "root" / "kb.db")
    conn.execute(f"UPDATE action_records SET {column} = ? WHERE action_id = ?", (value, use["action_id"]))
    conn.commit()
    conn.close()
    reopened = Recorder.open_at(tmp_path / "root")
    try:
        after = reopened.verify()
    finally:
        reopened.close()
    assert not after["ok"] and after["tool_uses"]["failed"][0]["lineage_id"] == use["lineage_id"], after


@pytest.mark.parametrize("column, value", [("accountable_identity", "mallory@example.test"),
                                           ("identity_salt", "cd" * 32)])
def test_t7g_who_an_exchange_is_attributed_to_is_held_like_a_tool_uses(tmp_path, column, value):
    """Every action's row holds the accountable identity as a salted commitment, in its hash, and the name and
    the salt beside it. `verify` opens the commitment on every row, the exchanges' as well as the tool uses'
    (delta check 3's F11: only the tool uses' were opened)."""
    async def go():
        async with Rig(tmp_path / "root", NEXUS_INTERCEPT_OWNER="owner@example.test") as rig:
            await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True))
            rig.inner.seal_once()
            exchange = rig.actions(kind=EXCHANGE_KIND)[0]
            assert rig.recorder.verify()["ok"]
            rig.recorder.lineage.conn.execute(f"UPDATE action_records SET {column} = ? WHERE action_id = ?",
                                              (value, exchange["action_id"]))
            rig.recorder.lineage.conn.commit()
            return rig.recorder.verify(), exchange
    verdict, exchange = run(go())
    assert not verdict["ok"], verdict
    assert [f["action_id"] for f in verdict["identities"]["failed"]] == [exchange["action_id"]], verdict


@pytest.mark.parametrize("record_hash", [None, "ab" * 32])
def test_t7e_an_outcome_written_by_hand_onto_a_waiting_tool_use_is_named(tmp_path, record_hash):
    """An outcome the recorder writes carries the hash of its row, and the next seal takes it. One written by hand
    with no hash would never be sealed and never be looked at: `verify` holds every tool use that is not waiting
    to the hash of its own row (delta check 2's E15)."""
    async def go():
        async with Rig(tmp_path / "root") as rig:
            await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))
            rig.inner.seal_once()
            use = rig.actions(kind=TOOL_USE_KIND)[0]
            rig.recorder.lineage.conn.execute(
                "UPDATE action_records SET state = 'completed', status = 'reported', response_hash = ?, "
                "completed_at = ?, record_hash = ? WHERE action_id = ?", ("cd" * 32, 5.0, record_hash, use["action_id"]))
            rig.recorder.lineage.conn.commit()
            rig.inner.seal_once()
            rig.inner.seal_once()
            return rig.recorder.verify(), use
    verdict, use = run(go())
    assert not verdict["ok"], verdict
    named = verdict["tool_uses"]["failed"] + verdict["actions"]["failed"]
    assert any(use["lineage_id"] in json.dumps(f) or use["action_id"] in json.dumps(f) for f in named), verdict


@pytest.mark.parametrize("forgery", ["replaced", "erased"])
def test_t7f_a_sealed_outcome_replaced_and_sealed_again_or_erased_is_named(tmp_path, forgery):
    """A tool use's reported result, sealed, then rewritten by hand with the hash of its new self and its seal
    mark cleared, so the next pass seals the forgery too; or set back to waiting. The first seal still holds the
    first leaf, and `verify` reads from the block to the row (delta check 3's F2: both read `ok: true`)."""
    async def go():
        async with Rig(tmp_path / "root") as rig:
            c = Conversation(rig)
            c.turn(("toolu_f", "get_weather", {"city": "Paris"}))
            c.asked("toolu_f", "get_weather", {"city": "Paris"})
            c.result("toolu_f", "18C and clear")
            c.turn()
            rig.inner.seal_once()
            use = rig.actions(kind=TOOL_USE_KIND)[0]
            assert use["state"] == "completed" and use["sealed_block"] is not None and rig.recorder.verify()["ok"]
            conn = rig.recorder.lineage.conn
            if forgery == "replaced":
                forged = {**use, "status": "reported_error", "response_hash": protocols.result_hash("it failed")}
                conn.execute("UPDATE action_records SET status = ?, response_hash = ?, record_hash = ?, "
                             "sealed_block = NULL WHERE action_id = ?",
                             (forged["status"], forged["response_hash"], act.record_hash(forged), use["action_id"]))
            else:
                conn.execute("UPDATE action_records SET state = 'requested', status = NULL, response_hash = NULL, "
                             "completed_at = NULL, record_hash = NULL, sealed_block = NULL WHERE action_id = ?",
                             (use["action_id"],))
            conn.commit()
            rig.inner.seal_once()
            return rig.recorder.verify(), use
    verdict, use = run(go())
    assert not verdict["ok"] and [f["action_id"] for f in verdict["actions"]["failed"]] == [use["action_id"]], verdict


# The ids are given: a parameter's own text is its id on an older pytest, and the nested case is 200,000 characters,
# which Windows cannot hold in PYTEST_CURRENT_TEST (the operator's gate of 24 November; tests/test_node_id_length.py).
@pytest.mark.parametrize("note", ["5", '{"a":1}', '"5"', "[1, 2]", '[["only-one"]]', "not json",
                                  "[" * 100000 + "]" * 100000],
                         ids=["a-number", "an-object", "a-string", "a-list-of-numbers", "a-list-of-one", "not-json",
                              "nested-200000"])
def test_t7d_a_note_of_tool_uses_that_cannot_be_read_is_named_not_raised(tmp_path, note):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))
            rec = rig.records()[0]
            notes = json.loads(rig.recorder.lineage.conn.execute(
                "SELECT capture_notes FROM lineage_records WHERE lineage_id = ?", (rec.lineage_id,)).fetchone()[0])
            notes["tool_uses"] = note
            rig.recorder.lineage.conn.execute("UPDATE lineage_records SET capture_notes = ? WHERE lineage_id = ?",
                                              (json.dumps(notes), rec.lineage_id))
            rig.recorder.lineage.conn.commit()
            return rig.recorder.tool_uses_named(), rec.lineage_id
    named, lineage_id = run(go())
    assert [f["lineage_id"] for f in named["failed"]] == [lineage_id], named


def _refuse_updates(recorder, table: str) -> None:
    recorder.lineage.conn.execute(f"CREATE TRIGGER planted_update_{table} BEFORE UPDATE ON {table} "
                                  f"BEGIN SELECT RAISE(ABORT, 'planted: {table} cannot be updated'); END")
    recorder.lineage.conn.commit()


def _allow(recorder, *triggers: str) -> None:
    for name in triggers:
        recorder.lineage.conn.execute(f"DROP TRIGGER {name}")
    recorder.lineage.conn.commit()


def test_s1_a_seal_whose_marks_could_not_be_written_is_marked_at_the_next_pass_not_sealed_again(tmp_path):
    """The block is appended in one file and the marks written in another, so a pass can append and then fail to
    mark. The next pass finds that very seal at the chain's head and marks it: one block, not one per attempt."""
    async def go():
        async with Rig(tmp_path / "root") as rig:
            await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True))
            before = rig.recorder.chain.count()
            _refuse_updates(rig.recorder, "lineage_records")
            first, second = rig.inner.seal_once(), rig.inner.seal_once()
            during = rig.recorder.chain.count()
            _allow(rig.recorder, "planted_update_lineage_records")
            third = rig.inner.seal_once()
            return before, first, second, during, third, rig.recorder.chain.count(), rig.recorder.pending(), \
                rig.recorder.verify()
    before, first, second, during, third, after, pending, verdict = run(go())
    assert first is None and second is None and during == before + 1, f"{during - before} blocks for one seal"
    assert third is not None and after == before + 1 and pending == 0 and verdict["ok"], (after, pending, verdict)


def test_s2_an_exchange_whose_record_and_outcome_both_fail_is_marked_when_the_store_takes_a_write(tmp_path):
    """Both writes fail, so the row cannot say `incomplete` yet (the verifier's V15). It says so at the next
    pass that can write, with the reason it failed, never the reason a stopped process would leave."""
    async def go():
        async with Rig(tmp_path / "root") as rig:
            _refuse_inserts(rig.recorder, "lineage_records")
            _refuse_updates(rig.recorder, "action_records")
            await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True))
            stuck = rig.actions(kind=EXCHANGE_KIND)[0]
            _allow(rig.recorder, "planted_lineage_records", "planted_update_action_records")
            rig.inner.seal_once()
            return stuck, rig.actions(kind=EXCHANGE_KIND)[0]
    stuck, after = run(go())
    assert stuck["state"] == "requested"
    assert after["state"] == "incomplete" and after["status"].startswith("response_not_recorded"), after


def test_s2b_an_outcome_that_no_longer_has_a_row_to_go_to_is_not_tried_for_ever(tmp_path):
    """The retry is for a row still `requested`. One that has its outcome (or is gone) is let go, so the list of
    what is still owed cannot grow without bound."""
    async def go():
        async with Rig(tmp_path / "root") as rig:
            await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True))
            done = rig.actions(kind=EXCHANGE_KIND)[0]
            rig.inner._unclosed.append((done, {"state": "incomplete", "status": "planted: owed to a finished row"}))
            rig.inner._unclosed.append(({**done, "action_id": "no-such-row"}, {"state": "incomplete", "status": "x"}))
            rig.inner.seal_once()
            return rig.inner._unclosed, rig.actions(kind=EXCHANGE_KIND)[0]
    owed, after = run(go())
    assert owed == [] and (after["state"], after["status"]) == ("completed", "200")


def test_s3_an_error_of_the_interceptors_own_after_the_request_is_recorded_closes_the_exchange(tmp_path, monkeypatch):
    """Whatever goes wrong here once the request's row is written, the row does not stay `requested` (V16)."""
    def broken(*_a, **_k):
        raise RuntimeError("planted: the response could not be read")

    async def go():
        async with Rig(tmp_path / "root") as rig:
            monkeypatch.setattr(protocols, "read_response", broken)
            with pytest.raises(RuntimeError):
                await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=False))
            return rig.actions(kind=EXCHANGE_KIND)
    actions = run(go())
    assert [(a["state"], a["status"]) for a in actions] == [("incomplete", "interceptor_error: RuntimeError")]


def test_s3b_an_error_while_the_request_is_being_sent_closes_the_exchange_too(tmp_path):
    """Between the request's commit and the first byte of a response there is the sending. An error there that
    is not the upstream's (delta check 1's D9) leaves no exchange `requested` either."""
    class Broken:
        async def send(self, *_a, **_k):
            raise RuntimeError("planted: the request could not be built")

        async def aclose(self):
            return None

    async def go():
        async with Rig(tmp_path / "root") as rig:
            await rig.forwarder.aclose()
            rig.inner.forwarder = rig.forwarder = Broken()
            with pytest.raises(RuntimeError):
                await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=False))
            return rig.actions(kind=EXCHANGE_KIND)
    actions = run(go())
    assert [(a["state"], a["status"]) for a in actions] == [("incomplete", "interceptor_error: RuntimeError")]


def test_s4_a_tool_use_whose_id_is_not_text_a_store_can_hold_is_still_recorded(tmp_path):
    """A model's id is the model's to choose. One holding half a character pair (a lone surrogate, which JSON can
    spell and a file cannot hold) is recorded with that half spelled out, and the exchange completes."""
    odd = (b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":'
           b'{"type":"tool_use","id":"toolu_\\ud83d","name":"get_weather","input":{}}}\n\n')

    async def go():
        async with Rig(tmp_path / "root", stub=Scripted([_START, odd, _STOP], STREAM)) as rig:
            status, _headers, raw, _request = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))
            return status, raw, rig.actions(kind=EXCHANGE_KIND), rig.actions(kind=TOOL_USE_KIND)
    status, raw, exchanges, uses = run(go())
    assert status == 200 and raw == _START + odd + _STOP and [e["state"] for e in exchanges] == ["completed"]
    assert len(uses) == 1 and "ud83d" in uses[0]["target"], uses


# ---------------------------------------------------------------- T8: what is not forwarded

def test_t8_another_path_a_cross_site_request_and_a_foreign_host_are_refused_and_forwarded_nowhere(tmp_path):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            out = {}
            for name, method, path, headers in (
                    ("models", "GET", "/v1/models", {}), ("count", "POST", "/v1/messages/count_tokens", {}),
                    ("hello", "HEAD", "/api/hello", {}), ("get", "GET", ANTHROPIC, {}),
                    ("cross", "POST", ANTHROPIC, {"sec-fetch-site": "cross-site"}),
                    ("origin", "POST", ANTHROPIC, {"origin": "https://evil.example"}),
                    ("rebind", "POST", ANTHROPIC, {"host": "evil.example:8790"})):
                response = await rig.harness.request(method, path, headers=headers, content=b"{}")
                out[name] = (response.status_code, response.content)
            health = await rig.harness.get("/health")
            return out, health.json(), rig.stub.calls, rig.actions(), rig.records()
    out, health, calls, actions, records = run(go())
    assert [out[n][0] for n in ("models", "count", "hello", "get")] == [404] * 4
    assert b"talima_not_a_model_endpoint" in out["models"][1]
    assert out["cross"][0] == 403 and b"talima_cross_site_refused" in out["cross"][1]
    assert out["origin"][0] == 403 and out["rebind"][0] == 403 and b"talima_host_refused" in out["rebind"][1]
    assert health["status"] == "ok" and health["exchanges"] == 0
    assert calls == 0 and actions == [] and records == [], "a refused request was forwarded or recorded"


# ---------------------------------------------------------------- T9: what went wrong upstream, recorded as it was

def test_t9_an_unreachable_upstream_and_a_broken_stream_are_recorded_as_what_they_were(tmp_path):
    class Unreachable(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("planted: nothing listens there")

    async def unreachable():
        async with Rig(tmp_path / "down") as rig:
            await rig.forwarder.aclose()
            rig.inner.forwarder = rig.forwarder = Forwarder(transport=Unreachable())
            status, _headers, raw, _request = await rig.post(OPENAI, ask(OPENAI, stream=True))
            return status, raw, rig.actions(), rig.records()
    status, raw, actions, records = run(unreachable())
    assert status == 502 and b"talima_upstream_unreachable" in raw and b"ConnectError" in raw
    assert b"nothing listens there" not in raw, "the error's own text reached the harness"
    assert [(a["state"], a["status"]) for a in actions] == [("failed", "ConnectError")] and records == []

    async def broken():
        async with Rig(tmp_path / "broken", stub=StubUpstream(break_after=3)) as rig:
            with pytest.raises(iapp.StreamCut):
                await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True, tools=True))
            return rig.actions(), rig.records(), bytes(rig.stub.responses[0])
    actions, records, sent = run(broken())
    assert [(a["kind"], a["state"]) for a in actions] == [(EXCHANGE_KIND, "incomplete")]
    assert actions[0]["status"].startswith("upstream_stream_broke: ")
    assert len(records) == 1 and records[0].response_hash == hashlib.sha256(sent).hexdigest()
    assert "response_cut" in records[0].capture_notes


def test_t9b_a_response_that_breaks_before_anything_reached_the_harness_is_answered_whole(tmp_path):
    """Nothing relayed yet, so there is no connection to cut and no event to replace: the harness gets the error
    whole (502, by name), and the store holds what a broken stream always leaves (docs/INTERCEPTOR.md)."""
    async def broken():
        async with Rig(tmp_path / "broken0", stub=StubUpstream(break_after=0)) as rig:
            status, _headers, raw, _request = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True))
            return status, raw, rig.actions(), rig.records()
    status, raw, actions, records = run(broken())
    assert status == 502 and b"talima_upstream_stream_broke" in raw, (status, raw)
    assert [(a["kind"], a["state"]) for a in actions] == [(EXCHANGE_KIND, "incomplete")]
    assert actions[0]["status"].startswith("upstream_stream_broke: ")
    assert len(records) == 1 and records[0].response_hash == hashlib.sha256(b"").hexdigest()
    assert "response_cut" in records[0].capture_notes


def _by_hand(app, path: str, body: bytes, headers: "list | None" = None):
    """One request driven as a server drives the application, so the harness's side can be made to go away:
    (the scope, `receive`, `send`'s messages, an event set at the first body bytes sent, an event that makes the
    harness disconnect)."""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "path": path,
             "raw_path": path.encode(), "query_string": b"", "client": ("127.0.0.1", 50001),
             "server": ("127.0.0.1", 8790), "scheme": "http",
             "headers": [(b"host", b"127.0.0.1:8790"), (b"content-type", b"application/json"),
                         (b"x-api-key", KEY.encode()), *(headers or [])]}
    sent, first_piece, leave, fed = [], asyncio.Event(), asyncio.Event(), []

    async def receive():
        if not fed:
            fed.append(True)
            return {"type": "http.request", "body": body, "more_body": False}
        try:
            await asyncio.wait_for(leave.wait(), 120)    # bounded: a harness that never leaves is a test that ends
        except asyncio.TimeoutError:
            pass
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            first_piece.set()

    return scope, receive, send, sent, first_piece, leave


def test_t9c_a_harness_that_goes_away_mid_response_leaves_a_record_of_what_arrived(tmp_path):
    """The upstream is between two pieces of its response when the harness closes the connection. The relay ends
    without an error of its own, the exchange reads `incomplete` and says why, and what had arrived is recorded
    (docs/INTERCEPTOR.md, "The harness goes away mid-response")."""
    async def go():
        pause = asyncio.Event()                          # never set: the upstream stops after its first event
        async with Rig(tmp_path / "root", stub=StubUpstream(gate=pause, gate_after=1)) as rig:
            body = json.dumps(ask(ANTHROPIC, stream=True)).encode()
            scope, receive, send, sent, first_piece, leave = _by_hand(rig.app, ANTHROPIC, body)
            relay = asyncio.ensure_future(rig.app(scope, receive, send))
            await asyncio.wait_for(first_piece.wait(), 20)
            leave.set()
            await asyncio.wait_for(relay, 20)            # the relay ends by itself, raising nothing
            arrived = bytes(rig.stub.responses[0])
            return rig.actions(kind=EXCHANGE_KIND), rig.records(), dict(rig.inner.stats), arrived
    actions, records, stats, arrived = run(go())
    assert [(a["state"], a["status"].split(":")[0]) for a in actions] == [("incomplete", "harness_disconnected")]
    assert len(records) == 1 and records[0].response_hash == hashlib.sha256(arrived).hexdigest() and arrived
    assert "response_cut" in records[0].capture_notes
    assert (stats["exchanges"], stats["recorded"], stats["incomplete"]) == (1, 0, 1), stats


class Slow:
    """An upstream that waits before it answers at all, then answers whole and at once."""

    def __init__(self, gate):
        self.gate, self.calls, self.responses = gate, 0, []

    async def __call__(self, scope, receive, send):
        self.calls += 1
        await asyncio.wait_for(self.gate.wait(), 60)
        self.responses.append(_START + _STOP)
        await send({"type": "http.response.start", "status": 200, "headers": STREAM})
        await send({"type": "http.response.body", "body": _START + _STOP, "more_body": False})


def test_t9d_a_harness_that_left_before_the_upstream_answered_is_not_counted_as_having_its_response(tmp_path):
    """The harness goes away while the request is still on its way; then the upstream answers, whole. The
    exchange does not read `completed`: nobody received it (delta check 1's D16: it read completed, recorded)."""
    async def go():
        answer = asyncio.Event()
        async with Rig(tmp_path / "root", stub=Slow(answer)) as rig:
            body = json.dumps(ask(ANTHROPIC, stream=True)).encode()
            scope, receive, send, sent, _first, leave = _by_hand(rig.app, ANTHROPIC, body)
            relay = asyncio.ensure_future(rig.app(scope, receive, send))
            for _ in range(200):                          # until the upstream has the request
                if rig.stub.calls:
                    break
                await asyncio.sleep(0.01)
            leave.set()                                  # the harness goes away; only then does the upstream answer
            await asyncio.sleep(0.05)
            answer.set()
            await asyncio.wait_for(relay, 20)
            return rig.actions(kind=EXCHANGE_KIND), rig.records(), dict(rig.inner.stats)
    actions, records, stats = run(go())
    assert [(a["state"], a["status"].split(":")[0]) for a in actions] == [("incomplete", "harness_disconnected")]
    assert len(records) == 1 and "response_cut" in records[0].capture_notes
    assert (stats["recorded"], stats["incomplete"]) == (0, 1), stats


# ---------------------------------------------------------------- T10: a process that stopped

def test_t10_an_exchange_left_requested_by_a_stopped_process_is_marked_incomplete_at_the_next_start(tmp_path):
    root = tmp_path / "root"
    first = Recorder.open_at(root)
    row = first.exchange_requested(lineage_id="l-1", protocol=protocols.ANTHROPIC, path=ANTHROPIC,
                                   url="http://127.0.0.1:9/v1/messages", harness_body=b"{}", forwarded_body=b"{}",
                                   headers=[], identity=None, mechanism=None)
    first.close()                                        # the process ends; the outcome was never written

    async def go():
        async with Rig(root) as rig:
            return rig.actions(), rig.recorder.verify()
    actions, verdict = run(go())
    assert [(a["action_id"], a["state"], a["status"]) for a in actions] == [(row["action_id"], "incomplete", STOPPED)]
    assert actions[0]["sealed_block"] is not None and verdict["ok"], "the swept row was not sealed"
    # a command that opens the store sweeps too: no interceptor is serving while it holds the lock
    other = tmp_path / "other"
    left = Recorder.open_at(other)
    left.exchange_requested(lineage_id="l-2", protocol=protocols.ANTHROPIC, path=ANTHROPIC,
                            url="http://127.0.0.1:9/v1/messages", harness_body=b"{}", forwarded_body=b"{}",
                            headers=[], identity=None, mechanism=None)
    left.close()
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEXUS_")}
    env.update(environ(other), NEXUS_ENV_ROOT=str(tmp_path / "no-env-here"))
    done = _proc.run([sys.executable, "-m", "backend.intercept", "list"], cwd=REPO, env=env, capture_output=True,
                     text=True, encoding="utf-8", errors="replace", timeout=120)
    listed = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
    assert done.returncode == 0 and [(e["state"], e["status"]) for e in listed] == [("incomplete", STOPPED)], done.stdout


# ---------------------------------------------------------------- T11: its own store, its own machine

def test_t11_the_store_and_the_bind_are_refused_by_name_before_anything_opens(tmp_path, monkeypatch):
    foreign = tmp_path / "console"
    foreign.mkdir()
    sqlite3.connect(foreign / "kb.db").close()
    with pytest.raises(iconfig.ConfigRefused, match="not_the_interceptors_store"):
        Recorder.open_at(foreign)
    assert sorted(p.name for p in foreign.iterdir()) == ["kb.db"], "a refused open made something"
    cases = {
        "no_upstream": environ(tmp_path / "r", NEXUS_INTERCEPT_UPSTREAM=""),
        "bad_upstream": environ(tmp_path / "r", NEXUS_INTERCEPT_UPSTREAM="https://user:pw@api.example.test"),
        "no_data_root": environ(tmp_path / "r", NEXUS_INTERCEPT_DATA_ROOT=" "),
        "data_root_refused": environ(REPO / "data" / "intercept"),
        "bad_inject": environ(tmp_path / "r", NEXUS_INTERCEPT_INJECT="yes"),
        "bad_auth_mode": environ(tmp_path / "r", NEXUS_AUTH_MODE="oauth"),
    }
    for reason, env in cases.items():
        with pytest.raises(iconfig.ConfigRefused, match=f"^{reason}: "):
            iconfig.load(env)
    for setting in ("NEXUS_DATA_ROOT", "NEXUS_MCP_DATA_ROOT"):
        with pytest.raises(iconfig.ConfigRefused, match="^data_root_refused: "):
            iconfig.load(environ(tmp_path / "shared", **{setting: str(tmp_path / "shared")}))
    with pytest.raises(iconfig.ConfigRefused, match="^bad_upstream: .*plain http"):
        iconfig.load(environ(tmp_path / "r", NEXUS_INTERCEPT_UPSTREAM="http://api.example.test"))
    with pytest.raises(iconfig.ConfigRefused, match="^bind_refused: "):
        iconfig.load(environ(tmp_path / "r"), host="0.0.0.0")
    proxied = iconfig.load(environ(tmp_path / "r", NEXUS_AUTH_MODE="proxy", NEXUS_OWNER_IDENTITY="o@example.test"),
                           host="0.0.0.0")
    assert proxied.proxy_mode and proxied.host == "0.0.0.0"
    assert not (tmp_path / "r").exists() and not (tmp_path / "shared").exists(), "a refused load made a folder"
    # the command line: exit 2, named on stderr, nothing made
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEXUS_")}
    env.update(environ(tmp_path / "cli"), NEXUS_ENV_ROOT=str(tmp_path / "no-env-here"))
    done = _proc.run([sys.executable, "-m", "backend.intercept", "--host", "0.0.0.0"], cwd=REPO, env=env,
                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert done.returncode == iconfig.EXIT_CONFIG and "bind_refused: " in done.stderr, (done.returncode, done.stderr)
    assert not (tmp_path / "cli").exists()


def _cli_env(root: Path, tmp_path: Path, **more) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEXUS_")}
    env.update(environ(root, **more), NEXUS_ENV_ROOT=str(tmp_path / "no-env-here"))
    return env


def _cli(args: list, env: dict):
    return _proc.run([sys.executable, "-m", "backend.intercept", *args], cwd=REPO, env=env, capture_output=True,
                     text=True, encoding="utf-8", errors="replace", timeout=120)


def test_t11b_a_bind_by_name_is_refused_and_the_reading_commands_need_no_upstream(tmp_path):
    """`localhost` is a name, and a name is whatever the machine's resolver says: the interceptor listens on an
    address (the verifier's V20). The commands that only read a stopped store send nothing, so they do not ask
    which provider the store's exchanges went to (V22)."""
    with pytest.raises(iconfig.ConfigRefused, match="^bind_refused: "):
        iconfig.load(environ(tmp_path / "r"), host="localhost")
    for host in ("127.0.0.1", "::1", "127.0.0.2"):
        assert iconfig.load(environ(tmp_path / "r"), host=host).host == host
    # behind the identity proxy the bind may leave this machine, and it is still an address (delta check 1's D6)
    behind = environ(tmp_path / "r", NEXUS_AUTH_MODE="proxy", NEXUS_OWNER_IDENTITY="o@example.test")
    for name in ("localhost", "proxy.internal", "0.0.0.0/0", "not a host"):
        with pytest.raises(iconfig.ConfigRefused, match="^bind_refused: .* is not an address"):
            iconfig.load(behind, host=name)
    for address in ("0.0.0.0", "10.1.2.3", "::"):
        assert iconfig.load(behind, host=address).host == address
    assert not (tmp_path / "r").exists()
    env = _cli_env(tmp_path / "store", tmp_path, NEXUS_INTERCEPT_UPSTREAM="")
    for command in (["verify"], ["list"], ["identity"]):
        done = _cli(command, env)
        assert done.returncode == 0, (command, done.returncode, done.stderr[-600:])
    served = _cli(["serve", "--port", "0"], env)
    assert served.returncode == iconfig.EXIT_CONFIG and "no_upstream: " in served.stderr, served.stderr[-600:]


def test_t11c_injection_asked_of_a_program_built_without_the_engine_is_refused_before_anything_opens(tmp_path,
                                                                                                    monkeypatch):
    """The released binary is the recorder alone. Asked for injection, it says so by name and exits 2, having
    opened no store (the verifier's V7: it opened the store, then failed at start with exit 3)."""
    import types

    from backend.intercept import __main__ as cli

    def no_engine(name):
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)

    monkeypatch.setattr(iapp, "importlib", types.SimpleNamespace(import_module=no_engine))
    for key, value in _cli_env(tmp_path / "store", tmp_path, NEXUS_INTERCEPT_INJECT="on").items():
        if key.startswith("NEXUS_"):
            monkeypatch.setenv(key, value)
    assert cli.main(["serve", "--port", "0"]) == iconfig.EXIT_CONFIG
    assert not (tmp_path / "store").exists(), "a refused start opened its store"
    with pytest.raises(iconfig.ConfigRefused, match="^inject_unavailable: "):
        iapp.link(iconfig.load(environ(tmp_path / "store", NEXUS_INTERCEPT_INJECT="on")))


# ---------------------------------------------------------------- T12: at once

def test_t12_five_exchanges_at_once_are_five_records(tmp_path):
    async def go():
        async with Rig(tmp_path / "root") as rig:
            paths = [ANTHROPIC, OPENAI, ANTHROPIC, OPENAI, ANTHROPIC]
            results = await asyncio.gather(*(rig.post(p, ask(p, stream=bool(i % 2), tools=bool(i % 3)))
                                             for i, p in enumerate(paths)))
            rig.inner.seal_once()
            return [r[0] for r in results], rig.records(), rig.actions(kind=EXCHANGE_KIND), rig.recorder.verify()
    statuses, records, exchanges, verdict = run(go())
    assert statuses == [200] * 5 and len(records) == 5 and len({r.lineage_id for r in records}) == 5
    assert sorted(e["lineage_id"] for e in exchanges) == sorted(r.lineage_id for r in records)
    assert all(e["state"] == "completed" for e in exchanges) and verdict["ok"]


# ---------------------------------------------------------------- T13: behind the identity proxy

def test_t13_behind_the_proxy_the_record_names_the_proxys_login_and_the_proxys_headers_stay_here(tmp_path):
    proxy = {"NEXUS_AUTH_MODE": "proxy", "NEXUS_OWNER_IDENTITY": "owner@example.test",
             "NEXUS_IDENTITY_HEADER": "X-Proxy-Login"}
    # what a proxy sets beside the login: its own family of headers, and the forwarding headers (the verifier's V6)
    theirs = {"x-proxy-name": "Alice Example", "x-proxy-profile-pic": "https://example.test/a.png",
              "x-forwarded-host": "box.example.test", "x-forwarded-proto": "https", "forwarded": "for=203.0.113.9",
              "x-real-ip": "203.0.113.9"}

    async def go():
        async with Rig(tmp_path / "root", **proxy) as rig:
            ok = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True),
                                {"x-proxy-login": "owner@example.test", "x-forwarded-for": "203.0.113.9", **theirs,
                                 "x-stainless-lang": "python"})
            other = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True), {"x-proxy-login": "someone@example.test"})
            none = await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True))
            return ok, other, none, rig.stub.requests, rig.records(), rig.actions()
    ok, other, none, requests, records, actions = run(go())
    assert (ok[0], other[0], none[0]) == (200, 403, 401)
    assert len(requests) == 1 and len(records) == 1 and len(actions) == 1, "a refused request went further"
    names = [n.lower() for n, _ in requests[0]["headers"]]
    assert "x-proxy-login" not in names and "x-forwarded-for" not in names, "the proxy's headers reached the upstream"
    assert not set(theirs) & set(names), f"the proxy's own headers reached the upstream: {set(theirs) & set(names)}"
    assert "x-stainless-lang" in names and "x-api-key" in names, "the harness's own headers were dropped"
    assert records[0].accountable_identity == "owner@example.test" and records[0].identity_mechanism == "proxy"
    assert actions[0]["identity_mechanism"] == "proxy"


@pytest.mark.parametrize("identity_header, family", [
    ("Tailscale-User-Login", "tailscale-"), ("X-Auth-Request-Email", "x-auth-"), ("Remote-User", "remote-"),
    ("X-Proxy-Login", "x-proxy-"), ("X-User", None), ("X-Email", None), ("User", None), ("Login", None),
])
def test_t13b_the_proxys_family_is_its_own_word_and_never_the_harnesss_headers(tmp_path, identity_header, family):
    """The proxy's other headers are known by the word its identity header begins with (`Tailscale-...`). A name
    too short to have such a word (`X-User`, `User`) has no family: only that one header stays here. Whatever
    the name, the harness's own headers reach the upstream (delta check 1's D3: `X-User` made the family `x-`,
    and `x-api-key` never arrived)."""
    assert iapp.proxys_family(identity_header) == family
    proxy = {"NEXUS_AUTH_MODE": "proxy", "NEXUS_OWNER_IDENTITY": "owner@example.test",
             "NEXUS_IDENTITY_HEADER": identity_header}
    harness = {"x-request-id": "r-1", "x-stainless-lang": "python", "user-agent": "harness/1.0",
               "anthropic-version": "2023-06-01", "login-hint": "none"}
    theirs = {(family or "zz-none-") + "name": "Alice Example"} if family else {}

    async def go():
        async with Rig(tmp_path / "root", **proxy) as rig:
            status, _h, _raw, _request = await rig.post(
                ANTHROPIC, ask(ANTHROPIC, stream=True),
                {identity_header.lower(): "owner@example.test", **harness, **theirs})
            return status, rig.stub.requests, rig.actions(kind=EXCHANGE_KIND)
    status, requests, actions = run(go())
    assert status == 200 and len(requests) == 1
    sent = dict(requests[0]["headers"])
    assert identity_header.lower() not in sent and not set(theirs) & set(sent)
    assert {name: sent.get(name) for name in harness} == harness, "one of the harness's own headers was dropped"
    assert sent.get("x-api-key") == KEY and sent.get("authorization") == f"Bearer {BEARER}"
    assert [c["header"] for c in json.loads(actions[0]["credentials_json"])] == ["authorization", "x-api-key"]


def test_t13c_the_proxys_headers_can_be_named_where_the_guess_would_be_wrong(tmp_path):
    """The family is a guess from the identity header's name, and a guess can be wrong both ways. Behind a proxy
    whose identity header is `X-Goog-Authenticated-User-Email` it would keep back `X-Goog-Api-Key`, a provider's
    key header; behind one whose header is `X-User` it sees no family, and `X-Email` beside it would travel.
    `NEXUS_INTERCEPT_PROXY_HEADERS` names the proxy's headers instead: exact names, or a prefix ending in `*`
    (delta check 2's E11 and E12). The identity header and the forwarding headers stay here whatever it names."""
    def proxy(header, named):
        return environ(tmp_path / "r", NEXUS_AUTH_MODE="proxy", NEXUS_OWNER_IDENTITY="owner@example.test",
                       NEXUS_IDENTITY_HEADER=header, **({"NEXUS_INTERCEPT_PROXY_HEADERS": named} if named else {}))
    guessed = iconfig.load(proxy("X-Goog-Authenticated-User-Email", None))
    assert guessed.proxy_headers is None
    assert iapp.proxys_own("x-goog-api-key", guessed), "the guess is what it was: this is the case the setting is for"
    named = iconfig.load(proxy("X-Goog-Authenticated-User-Email", " X-Goog-Iap-* , X-Serverless-Authorization "))
    assert named.proxy_headers == ("x-goog-iap-*", "x-serverless-authorization")
    for name, own in {"x-goog-api-key": False, "x-goog-iap-jwt-assertion": True, "x-goog-iap": False,
                      "x-serverless-authorization": True, "x-serverless-authorization-2": False,
                      "x-goog-authenticated-user-email": True, "x-forwarded-for": True, "via": True,
                      "x-api-key": False}.items():
        assert iapp.proxys_own(name, named) is own, name
    for bad in ("*", "x-*-y", "x y", "x-a,,x-b", "x:y", ","):
        with pytest.raises(iconfig.ConfigRefused, match="^bad_proxy_headers: "):
            iconfig.load(proxy("X-User", bad))
    assert not (tmp_path / "r").exists()

    async def go():
        env = {k: v for k, v in proxy("X-User", "X-Email, X-Groups").items() if k.startswith("NEXUS_") and
               k not in ("NEXUS_INTERCEPT_UPSTREAM", "NEXUS_INTERCEPT_DATA_ROOT")}
        async with Rig(tmp_path / "root", **env) as rig:
            status, _h, _raw, _request = await rig.post(
                ANTHROPIC, ask(ANTHROPIC, stream=True),
                {"x-user": "owner@example.test", "x-email": "owner@example.test", "x-groups": "staff",
                 "x-request-id": "r-1"})
            return status, rig.stub.requests
    status, requests = run(go())
    sent = dict(requests[0]["headers"])
    assert status == 200 and not {"x-user", "x-email", "x-groups"} & set(sent), sent
    assert sent.get("x-request-id") == "r-1" and sent.get("x-api-key") == KEY


# ---------------------------------------------------------------- T14, T15: the order of things

def test_t14_the_terminal_event_is_relayed_only_after_the_record_is_written(tmp_path):
    seen = []

    def watching(app, rig_box):
        async def served(scope, receive, send):
            async def noting(message):
                if message["type"] == "http.response.body" and message.get("body"):
                    rig = rig_box[0]
                    seen.append((b"message_stop" in message["body"], b"ping" in message["body"], len(rig.records()),
                                 [a["state"] for a in rig.actions(kind=EXCHANGE_KIND)]))
                await send(message)
            await app(scope, receive, noting)
        return served

    async def go():
        box = []
        rig = Rig(tmp_path / "root", wrap=lambda app: watching(app, box))
        box.append(rig)
        async with rig:
            await rig.post(ANTHROPIC, ask(ANTHROPIC, stream=True))
    run(go())
    assert len(seen) >= 3, "the stream was relayed in one piece"
    before = [s for s in seen if not s[0]]
    assert before and all(s[2] == 0 and s[3] == ["requested"] for s in before), \
        "the request's row was not written first, or a record preceded its response"
    assert any(s[1] for s in before), "the ping was held with the terminal event"
    assert seen[-1][0] and seen[-1][2] == 1 and seen[-1][3] == ["completed"], \
        "the terminal event reached the harness before the record was written"


def test_t15_a_last_line_with_no_blank_line_after_it_is_still_the_terminal_event(tmp_path):
    async def go(root, refuse):
        async with Rig(root, stub=StubUpstream(done_without_blank_line=True)) as rig:
            if refuse:
                _refuse_inserts(rig.recorder, "lineage_records")
            _status, _headers, raw, _request = await rig.post(OPENAI, ask(OPENAI, stream=True))
            return raw, bytes(rig.stub.responses[0])
    raw, sent = run(go(tmp_path / "ok", False))
    assert raw == sent and raw.endswith(b"data: [DONE]\n")
    raw, sent = run(go(tmp_path / "refused", True))
    assert b"[DONE]" not in raw and b"talima_response_not_recorded" in raw, raw[-300:]


class NoLastEvent:
    """An upstream whose stream ends cleanly without its protocol's last event (no `data: [DONE]`)."""

    EVENTS = [b'data: {"id":"c1","object":"chat.completion.chunk","model":"m","choices":[{"index":0,"delta":'
              b'{"content":"Hel"}}]}\n\n',
              b'data: {"id":"c1","object":"chat.completion.chunk","model":"m","choices":[{"index":0,"delta":'
              b'{"content":"lo"},"finish_reason":"stop"}]}\n\n']

    def __init__(self):
        self.calls, self.responses = 0, []

    async def __call__(self, scope, receive, send):
        self.calls += 1
        sent = bytearray()
        self.responses.append(sent)
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        for n, event in enumerate(self.EVENTS, 1):
            sent += event
            await send({"type": "http.response.body", "body": event, "more_body": n < len(self.EVENTS)})


def test_t15b_a_stream_with_no_last_event_has_nothing_to_hold_and_still_ends_in_an_error_when_unrecorded(tmp_path):
    """The terminal event is what is held. A stream that closes without one (docs/INTERCEPTOR.md says so) has
    reached the harness whole when it closes: recorded, it is the upstream's bytes; unrecorded, the error event
    follows them and the exchange reads `incomplete`. Bytes already relayed are not recalled."""
    async def go(root, refuse):
        async with Rig(root, stub=NoLastEvent()) as rig:
            if refuse:
                _refuse_inserts(rig.recorder, "lineage_records")
            status, _headers, raw, _request = await rig.post(OPENAI, ask(OPENAI, stream=True))
            return status, raw, bytes(rig.stub.responses[0]), rig.actions(kind=EXCHANGE_KIND), rig.records()
    status, raw, sent, actions, records = run(go(tmp_path / "ok", False))
    assert status == 200 and raw == sent
    assert [a["state"] for a in actions] == ["completed"] and len(records) == 1
    assert records[0].response_hash == hashlib.sha256(sent).hexdigest()
    status, raw, sent, actions, records = run(go(tmp_path / "refused", True))
    assert raw.startswith(sent) and b"talima_response_not_recorded" in raw[len(sent):], raw[-300:]
    assert [a["state"] for a in actions] == ["incomplete"] and records == []


def test_t16_the_kept_request_is_the_harnesss_bytes_under_its_own_encoding(tmp_path, monkeypatch):
    async def go(root):
        async with Rig(root) as rig:
            doc = json.dumps(ask(ANTHROPIC, stream=False)).encode()
            await rig.post(ANTHROPIC, doc)
            rec = rig.records()[0]
            row = rig.recorder.lineage.conn.execute("SELECT encoding FROM request_bodies WHERE request_hash = ?",
                                                    (rec.request_hash,)).fetchone()
            return doc, rig.recorder.lineage.request_body(rec.request_hash), row, rec
    doc, kept, row, rec = run(go(tmp_path / "kept"))
    assert kept == doc and row["encoding"] == REQUEST_ENCODING_HTTP_BODY and "request_body" not in rec.capture_notes
    monkeypatch.setenv("NEXUS_KEEP_REQUESTS", "off")
    doc, kept, row, rec = run(go(tmp_path / "off"))
    assert kept is None and row is None and rec.capture_notes["request_body"] == "not kept: NEXUS_KEEP_REQUESTS=off"
    assert (tmp_path / "kept" / MARKER).is_file()


# ---------------------------------------------------------------- T17: the optional link, built and off

@pytest.mark.parametrize("path", [ANTHROPIC, OPENAI])
def test_t17_injection_on_adds_one_delimited_system_block_and_the_record_says_both_requests(tmp_path, path,
                                                                                               monkeypatch):
    from backend.intercept import inject
    from backend.kb.granularity import Granularity
    from backend.loop.prompt_render import context_hash
    question = "what was the QTc change at 600mg?"

    async def go(root, inject):
        async with Rig(root, **({"NEXUS_INTERCEPT_INJECT": inject} if inject == "on" else {})) as rig:
            if inject == "on":
                rig.inner._inject.services.store.add(subject="qtc change at 600mg", relation="was", object="11.4ms",
                                                     weight=0.8, tier=1, granularity=Granularity.TOPIC,
                                                     source="human_assertion")
            doc = ask(path, stream=True, messages=[{"role": "user", "content": question}])
            if path == ANTHROPIC:
                doc["system"] = "You are terse."
            else:
                doc["messages"].insert(0, {"role": "system", "content": "You are terse."})
            body = json.dumps(doc).encode()
            status, _h, _raw, _r = await rig.post(path, body)
            assert status == 200
            rec, exchange = rig.records()[0], rig.actions(kind=EXCHANGE_KIND)[0]
            return body, rig.stub.requests[0]["body"], rec, exchange, rig.recorder.lineage.request_body(rec.request_hash)
    # off, set as an operator sets it: the bytes forwarded are the harness's
    monkeypatch.setenv("NEXUS_INTERCEPT_INJECT", "off")
    body, sent, rec, exchange, kept = run(go(tmp_path / "off", "off"))
    assert sent == body and not rec.injected and rec.context is None and "injection" not in rec.capture_notes
    assert exchange["request_hash"] == rec.request_hash == hashlib.sha256(body).hexdigest()
    # on: one more system block, and nothing else of the request changed
    body, sent, rec, exchange, kept = run(go(tmp_path / "on", "on"))
    assert sent != body and kept == body, "what is kept for request_hash is the harness's bytes"
    assert rec.request_hash == hashlib.sha256(body).hexdigest()
    assert exchange["request_hash"] == hashlib.sha256(sent).hexdigest() != rec.request_hash
    was, now = json.loads(body), json.loads(sent)
    if path == ANTHROPIC:
        assert now["system"][0] == {"type": "text", "text": "You are terse."} and len(now["system"]) == 2
        block = now["system"][1]["text"]
        assert {k: v for k, v in now.items() if k != "system"} == {k: v for k, v in was.items() if k != "system"}
    else:
        assert now["messages"][0] == was["messages"][0] and now["messages"][2:] == was["messages"][1:]
        assert now["messages"][1]["role"] == "system"
        block = now["messages"][1]["content"]
        assert {k: v for k, v in now.items() if k != "messages"} == {k: v for k, v in was.items() if k != "messages"}
    opened, closed = inject.OPEN + "\n", "\n" + inject.CLOSE
    assert block.startswith(opened) and block.endswith(closed) and inject.OPEN == '<nexus-block source="retrieval">'
    assert "11.4ms" in block
    inner = block[len(opened):-len(closed)]
    assert rec.context["hash"] == context_hash(inner) and len(rec.injected) >= 1
    assert all(i["cited"] is False for i in rec.injected) and "injection" in rec.capture_notes
    assert record_scope.verify_scope(rec) == []


def test_t18_an_injection_that_fails_adds_nothing_and_the_exchange_is_still_recorded(tmp_path, monkeypatch):
    async def go():
        async with Rig(tmp_path / "root", NEXUS_INTERCEPT_INJECT="on") as rig:
            def broken(*a, **kw):
                raise RuntimeError("planted: the store cannot be searched")
            monkeypatch.setattr(rig.inner._inject.services.retriever, "retrieve", broken, raising=False)
            monkeypatch.setattr("backend.intercept.inject.prepare_turn", broken)
            body = json.dumps(ask(ANTHROPIC, stream=True)).encode()
            status, _h, _raw, _r = await rig.post(ANTHROPIC, body)
            return status, body, rig.stub.requests[0]["body"], rig.records()[0]
    status, body, sent, rec = run(go())
    assert status == 200 and sent == body, "a failed injection changed the request"
    assert rec.capture_notes["injection"].startswith("injection failed (") and not rec.injected
    assert "planted" not in json.dumps(rec.capture_notes), "an error's own text reached the record"

