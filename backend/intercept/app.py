"""W10c (run/specs/W10c.md S3, S4, S7) — the interceptor: an agent harness's model endpoint, recording every
exchange on its way to the provider.

One ASGI application, no framework: a response is relayed as its bytes arrive, and a framework's response classes
would stand between the upstream's bytes and the harness. It serves the two model endpoints (`POST /v1/messages`,
`POST /v1/chat/completions`) and `GET /health`; every other request is answered 404 by name, forwarded nowhere and
recorded nowhere.

AN EXCHANGE, IN ORDER
  1. the request's body is read whole and hashed;
  2. its row is committed (`Recorder.exchange_requested`); if that fails the harness gets
     `talima_request_not_recorded` and the upstream is never called;
  3. the request is forwarded: the harness's body byte for byte (`NEXUS_INTERCEPT_INJECT=on`, off by default, is
     the one thing that adds to it: `backend/intercept/inject.py`), its headers as it sent them less the ones
     that belong to one connection (and, behind the identity proxy, the proxy's own);
  4. a streamed response is relayed event by event as it arrives, up to its terminal event; any other response is
     held whole;
  5. the response's record is committed (`Recorder.exchange_recorded`);
  6. what was held is relayed. If step 5 failed, the harness gets an error in its place (an error event; for a
     compressed stream the connection is cut; a whole response is withheld) and the exchange reads `incomplete`.

So a harness holds a completed response only if its record is written. Bytes already relayed when a write failed are
not recalled; the exchange's row says which it was.

Nothing here imports the memory engine (tests/test_w10c_standalone.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import logging
import uuid

from backend import identity
from backend.intercept import protocols
from backend.intercept.config import INJECT_SETTING, ConfigRefused, InterceptConfig
from backend.intercept.forward import Forwarder, UpstreamError
from backend.intercept.recorder import NotRecorded, Recorder
from backend.memory.lineage import IDENTITY_PROXY

logger = logging.getLogger(__name__)

SEAL_INTERVAL_SECONDS = 5.0
# Headers that belong to one connection (RFC 9110, 7.6.1): relayed in neither direction. `Host` and
# `Content-Length` are the upstream connection's own, set by the client that makes it.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "te",
              "trailer", "transfer-encoding", "upgrade"}
NOT_FORWARDED = HOP_BY_HOP | {"host", "content-length"}


def link(config):
    """The one optional link to the memory engine (S8): `backend/intercept/inject.py`, loaded by name, never
    imported. This function begins by refusing any configuration whose `inject` is not exactly on, so the engine
    is loaded only for a configuration that says injection is on: the one the process read from its settings
    (`NEXUS_INTERCEPT_INJECT=on`), or one a caller built to say so, which is that caller turning injection on.
    The standalone guard reads the interceptor's reach without the link and holds that first statement
    (tests/test_w10c_standalone.py). A program built without the engine (the released binary is the recorder
    alone) refuses by name."""
    if config.inject is not True:
        raise ConfigRefused(f"inject_off: the optional link is loaded only where {INJECT_SETTING}=on asked for it")
    try:
        return importlib.import_module("backend.intercept.inject")
    except ImportError as e:
        raise ConfigRefused(f"inject_unavailable: {INJECT_SETTING}=on adds retrieval from the memory engine to a "
                            f"request, and this program does not hold the memory engine ({type(e).__name__}: "
                            f"{getattr(e, 'name', None) or 'a module'}); run it from a checkout, or leave "
                            f"{INJECT_SETTING} off") from None


class StreamCut(RuntimeError):
    """Raised to end a response that has begun without completing it: the server closes the connection, which is
    what a harness reads as a response that did not finish."""


def _whole(status: int, body: bytes) -> list:
    return [{"type": "http.response.start", "status": status,
             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]},
            {"type": "http.response.body", "body": body}]


def _connection_named(headers: list) -> set:
    """The header names a `Connection` header lists: those belong to the connection too."""
    out = set()
    for name, value in headers:
        if name.lower() == b"connection":
            out |= {token.strip().lower() for token in value.decode("latin-1").split(",") if token.strip()}
    return out


# What a proxy in front adds to a request, whatever its make: the forwarding headers. Behind the identity proxy
# these and the identity header's own family (`proxys_own`) stay here.
FORWARDING = {"forwarded", "x-real-ip", "via"}
FORWARDING_PREFIX = "x-forwarded-"


def proxys_family(identity_header: str) -> "str | None":
    """The prefix the identity proxy's own headers share, or None when the identity header's name is too short
    to say: its first word (`tailscale-` for `Tailscale-User-Login`, `remote-` for `Remote-User`), or its first
    two when the first is only `x` (`x-auth-` for `X-Auth-Request-Email`). `X-User` and `User` have no word of
    the proxy's own before their last, so no family: a prefix `x-` or `user` would be the harness's headers."""
    words = identity_header.lower().split("-")
    own = 2 if words[0] == "x" else 1            # how many leading words name the proxy
    return "-".join(words[:own]) + "-" if len(words) > own else None


def proxys_own(name: str, config) -> bool:
    """A header the identity proxy added, by its name. Always: the identity header and the forwarding headers.
    And the proxy's other headers: the ones `NEXUS_INTERCEPT_PROXY_HEADERS` names (exact, or a prefix ending in
    `*`), or, where it names none, the identity header's family (`proxys_family`), which is a guess from one name
    and can be wrong both ways. They are not the harness's, and they would tell the provider who the person is
    and where they are."""
    if name == config.identity.header.lower() or name in FORWARDING or name.startswith(FORWARDING_PREFIX):
        return True
    if config.proxy_headers is not None:
        return any(name.startswith(named[:-1]) if named.endswith("*") else name == named
                   for named in config.proxy_headers)
    family = proxys_family(config.identity.header)
    return family is not None and name.startswith(family)


def forwardable(headers: list, config: InterceptConfig) -> list:
    """The harness's headers as the upstream gets them, as (name, value) in bytes: every one, in order, its value
    as sent, less the connection's own and, behind the identity proxy, the proxy's (`proxys_own`). One value is
    not as sent: `Accept-Encoding` asks only for encodings the interceptor reads (`protocols.accept_encoding`)."""
    drop = NOT_FORWARDED | _connection_named(headers)
    out = []
    for name, value in headers:
        low = name.decode("latin-1").lower()
        if low in drop or (config.proxy_mode and proxys_own(low, config)):
            continue
        if low == "accept-encoding":
            value = protocols.accept_encoding(value.decode("latin-1")).encode("latin-1")
        out.append((name, value))
    return out


def relayable(headers: list, *, streamed: bool) -> list:
    """The upstream's response headers as the harness gets them: less the connection's own, and less the length of
    a streamed body (the server frames a stream itself)."""
    drop = HOP_BY_HOP | _connection_named(headers) | ({"content-length"} if streamed else set())
    return [(n, v) for n, v in headers if n.decode("latin-1").lower() not in drop]


def _host_of(headers: list) -> str:
    """The name in the request's `Host` header, without its port; empty when there is none."""
    host = next((v.decode("latin-1") for n, v in headers if n.lower() == b"host"), "").strip().lower()
    if host.startswith("["):
        return host[1:host.index("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if ":" in host else host


class Interceptor:
    def __init__(self, config: InterceptConfig, recorder: Recorder, forwarder: Forwarder):
        self.config = config
        self.recorder = recorder
        self.forwarder = forwarder
        self.stats = {"exchanges": 0, "recorded": 0, "refused_before_sending": 0, "incomplete": 0,
                      "upstream_unreachable": 0, "sealed_blocks": 0}
        self._seal_task = None
        self._inject = None
        self._unclosed = []               # (row, outcome) of exchanges whose outcome could not be written yet

    # ---------------------------------------------------------------- lifespan: the sweep, the seal, the close

    async def startup(self) -> None:
        swept = self.recorder.sweep_unfinished()
        if swept:
            logger.warning("%d exchange(s) were left unfinished by a process that stopped: marked incomplete", swept)
        if self.config.inject:
            self._inject = link(self.config).Injector(self.config)
        self.seal_once()
        self._seal_task = asyncio.create_task(self._seal_loop())

    async def shutdown(self) -> None:
        if self._seal_task is not None:
            self._seal_task.cancel()
            try:
                await self._seal_task
            except asyncio.CancelledError:
                pass
        self.seal_once()
        await self.forwarder.aclose()
        if self._inject is not None:
            self._inject.close()
        self.recorder.close()

    def seal_once(self):
        """One governance block over what is unsealed, or None. Never raises: a seal that fails is tried again on
        the next tick, and the relay goes on. First, any exchange whose outcome could not be written when it
        ended is written now, with the reason it had then."""
        if self._unclosed:
            self._unclosed = self.recorder.still_owed(self._unclosed)
        try:
            block = self.recorder.seal()
        except Exception as e:
            logger.warning("the seal failed: %s: %s", type(e).__name__, e)
            return None
        if block is not None:
            self.stats["sealed_blocks"] += 1
        return block

    async def _seal_loop(self) -> None:
        while True:
            await asyncio.sleep(SEAL_INTERVAL_SECONDS)
            self.seal_once()

    # ---------------------------------------------------------------- ASGI

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            raise RuntimeError(f"the interceptor serves HTTP only, not {scope['type']!r}")
        path, method = scope.get("path", ""), scope.get("method", "")
        protocol = protocols.protocol_of(path)
        if method == "GET" and path in identity.OPEN_PATHS:
            await self._send(send, _whole(200, protocols.canonical(
                {"status": "ok", "recorder": "talima-intercept", **self.stats, "unsealed": self.recorder.pending()})))
            return
        if protocol is None or method != "POST":
            await self._send(send, _whole(404, protocols.error_body(
                protocols.ANTHROPIC, "talima_not_a_model_endpoint",
                "the interceptor serves POST /v1/messages and POST /v1/chat/completions; this request was "
                "forwarded nowhere and recorded nowhere")))
            return
        refusal = self._refusal(scope)
        if refusal:
            await self._send(send, _whole(403, protocols.error_body(protocol, *refusal)))
            return
        await self._exchange(scope, receive, send, protocol, path)

    async def _lifespan(self, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    await self.startup()
                except Exception as e:
                    await send({"type": "lifespan.startup.failed", "message": f"{type(e).__name__}: {e}"})
                    raise
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self.shutdown()
                await send({"type": "lifespan.shutdown.complete"})
                return

    @staticmethod
    async def _send(send, messages: list) -> None:
        for message in messages:
            await send(message)

    def _refusal(self, scope) -> "tuple[str, str] | None":
        """A request a browser sent from another site, or one addressed to a name that is not this machine's:
        refused, forwarded nowhere. Behind the identity proxy W4's gate has already decided."""
        if self.config.proxy_mode:
            return None
        headers = identity.read_request_headers(scope, self.config.identity.header_key)
        if identity.is_cross_site(scope.get("method"), headers):
            return ("talima_cross_site_refused", "a browser sent this from another site; the interceptor takes "
                                                 "requests from a harness on this machine")
        name = _host_of(scope.get("headers") or [])
        if name and name != "localhost" and not identity.is_loopback_address(name):
            return ("talima_host_refused", "the request is addressed to a name that is not this machine's")
        return None

    def _who(self, scope) -> "tuple[str | None, str | None]":
        """(the accountable identity, how it was established): the proxy's login behind the proxy, else the
        configured owner, asserted."""
        if self.config.proxy_mode:
            established = identity.identity_on_scope(scope)
            return (established.login if established is not None else None), IDENTITY_PROXY
        return self.config.owner, None

    async def _exchange(self, scope, receive, send, protocol: str, path: str) -> None:
        self.stats["exchanges"] += 1
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        body = bytes(body)
        headers = list(scope.get("headers") or [])
        query = scope.get("query_string") or b""
        url = self.config.upstream + path + (("?" + query.decode("latin-1")) if query else "")
        who, mechanism = self._who(scope)
        sent_headers = forwardable(headers, self.config)
        request = protocols.read_request(protocol, body)
        notes = {"request": request.note} if request.note else {}
        forwarded, context, injected = body, None, []
        if self._inject is not None:                     # off by default: then `forwarded` IS `body`
            forwarded, context, injected, said = await self._inject.apply(protocol, body)
            notes["injection"] = said

        # 2. the request's row, before anything leaves
        try:
            row = self.recorder.exchange_requested(
                lineage_id=str(uuid.uuid4()), protocol=protocol, path=path, url=url, harness_body=body,
                forwarded_body=forwarded, headers=sent_headers, identity=who, mechanism=mechanism,
                results=request.results)
        except NotRecorded as e:
            self.stats["refused_before_sending"] += 1
            reason, _, why = str(e).partition(": ")
            await self._send(send, _whole(503, protocols.error_body(protocol, reason, why)))
            return
        if row["request_note"]:
            notes["request_body"] = row["request_note"]
        if request.results:                              # which tool uses this request's results were linked to
            notes["tool_results"] = (f"{len(request.results)} in the request; linked by it to a recorded tool use: "
                                     f"{', '.join(row['results_linked']) or 'none'}")

        # 3. forward
        try:
            upstream = await self.forwarder.send(url, sent_headers, forwarded)
        except UpstreamError as e:
            self.stats["upstream_unreachable"] += 1
            self._close(row, state="failed", status=e.kind)
            await self._send(send, _whole(502, protocols.error_body(
                protocol, "talima_upstream_unreachable",
                f"the upstream did not answer ({e.kind}); the request is recorded")))
            return
        except Exception as e:                           # an error of the interceptor's own while sending
            self._close(row, state="incomplete", status=f"interceptor_error: {type(e).__name__}")
            raise
        try:
            await self._relay(receive, send, upstream, row, protocol, path, hashlib.sha256(body).hexdigest(), notes,
                              context, injected)
        except StreamCut:
            raise
        except Exception as e:
            # An error of the interceptor's own, after the request was recorded: the exchange does not stay
            # `requested` (the write changes only a row still in that state, so a finished exchange is untouched).
            self._close(row, state="incomplete", status=f"interceptor_error: {type(e).__name__}")
            raise
        finally:
            await upstream.aclose()

    def _close(self, row: dict, **outcome) -> None:
        """The exchange's outcome alone; kept for the next pass when the store will not take it now."""
        if not self.recorder.exchange_closed(row, **outcome):
            self._unclosed.append((row, outcome))

    async def _relay(self, receive, send, upstream, row, protocol, path, harness_hash, notes, context,
                     injected) -> None:
        streamed = upstream.content_type.startswith("text/event-stream")
        encoding = upstream.content_encoding
        decoder = protocols.decoder_for(encoding) if streamed else None
        scanner = protocols.Scanner(protocol)
        raw, released, started, cut, harness_gone = bytearray(), 0, False, None, False
        gone = asyncio.ensure_future(_disconnected(receive))

        async def start() -> None:
            nonlocal started
            if not started:
                started = True
                await send({"type": "http.response.start", "status": upstream.status,
                            "headers": relayable(upstream.response_headers, streamed=streamed)})

        async def release(upto: int) -> None:
            nonlocal released
            if upto > released:
                await start()
                await send({"type": "http.response.body", "body": bytes(raw[released:upto]), "more_body": True})
                released = upto

        # 4. the response, as it arrives
        chunks = upstream.chunks()
        try:
            while True:
                nxt = asyncio.ensure_future(chunks.__anext__())
                await asyncio.wait({nxt, gone}, return_when=asyncio.FIRST_COMPLETED)
                if not nxt.done():                       # the harness went away first
                    nxt.cancel()                         # the read ends before the stream is closed below: a
                    await asyncio.gather(nxt, return_exceptions=True)     # stream cannot be closed mid-read
                    harness_gone = True
                    cut = "harness_disconnected: the harness closed the connection before the response ended"
                    break
                try:
                    chunk = nxt.result()
                except StopAsyncIteration:
                    break
                except UpstreamError as e:
                    cut = f"upstream_stream_broke: the upstream's response ended early ({e.kind})"
                    break
                raw += chunk
                if not streamed:
                    continue                             # a whole body: held until its record is written
                if decoder is None:                      # as sent: every complete event before the terminal one
                    scanner.feed(chunk)
                    await release(scanner.safe)
                elif decoder is False:                   # an encoding not read here: its last event cannot be
                    continue                             # found, so all of it is held until its record is written
                else:                                    # compressed: read decoded, relayed as it came
                    try:
                        scanner.feed(decoder.feed(chunk))
                    except Exception:
                        decoder = False                  # it did not decode: held from here on
                        continue
                    if scanner.terminal_at is None and scanner.at_boundary:
                        await release(len(raw))
        finally:
            # The harness may have left without the loop seeing it: the loop looks only while it waits for the
            # upstream, and a response that was already there is read without waiting.
            if cut is None and gone.done() and not gone.cancelled():
                harness_gone = True
                cut = "harness_disconnected: the harness closed the connection before the response was relayed"
            gone.cancel()
            await chunks.aclose()
        scanner.finish()

        # 5. the response's record
        response = bytes(raw)
        if cut is None:
            body, why = protocols.decoded(response, encoding)
            facts = protocols.read_response(protocol, body, streamed=streamed)
        else:
            why, facts = None, protocols.ResponseFacts()     # a cut response is hashed, never read for tool uses
        for key, value in (("response_encoding", why), ("response", facts.note), ("response_cut", cut)):
            if value:
                notes[key] = value
        try:
            self.recorder.exchange_recorded(
                row, protocol=protocol, path=path, harness_hash=harness_hash, response=response, facts=facts,
                state="completed" if cut is None else "incomplete",
                status=str(upstream.status) if cut is None else cut, notes=notes, context=context, injected=injected)
        except NotRecorded as e:
            self.stats["incomplete"] += 1
            reason, _, why_not = str(e).partition(": ")
            self._close(row, state="incomplete", status=f"response_not_recorded: {why_not}", response=response)
            if harness_gone:
                return
            if not started:                              # nothing relayed yet: a whole error in its place
                await self._send(send, _whole(503, protocols.error_body(
                    protocol, reason, why_not + "; the response is withheld")))
                return
            if decoder is None and streamed and cut is None:
                # as sent: everything before the terminal event, then the error in the terminal event's place
                await release(scanner.terminal_at if scanner.terminal_at is not None else len(raw))
                await send({"type": "http.response.body", "more_body": False,
                            "body": protocols.error_event(protocol, reason, why_not)})
                return
            raise StreamCut(f"{reason}: {why_not}") from None
        if cut is not None:
            self.stats["incomplete"] += 1
            if harness_gone:
                return
            if not started:
                await self._send(send, _whole(502, protocols.error_body(
                    protocol, "talima_upstream_stream_broke", "the upstream's response ended early; what arrived "
                                                              "is recorded")))
                return
            raise StreamCut(cut)
        self.stats["recorded"] += 1

        # 6. what was held
        await start()
        await send({"type": "http.response.body", "body": bytes(raw[released:]), "more_body": False})


async def _disconnected(receive) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return


def build(config: InterceptConfig, recorder: Recorder, forwarder: "Forwarder | None" = None):
    """The application as it is served: the interceptor, behind W4's gate in proxy mode."""
    app = Interceptor(config, recorder, forwarder or Forwarder())
    return identity.IdentityGate(app, config.identity) if config.proxy_mode else app
