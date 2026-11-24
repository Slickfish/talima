"""W10c (run/specs/W10c.md S3, S7) — the interceptor's one way out: the request it forwards to the upstream.

The only module of `backend/intercept/` that holds a network client. A request is sent as it is given: the method
`POST`, the URL, the headers in the order given and the body's bytes. The client adds nothing of its own (its
default headers are cleared; it sets `Host` and `Content-Length`, which belong to the connection it makes), follows
no redirect and decodes nothing: `chunks()` gives the response's body as the upstream's bytes arrive, compressed if
the upstream compressed them.

The exchange's row is committed before `send` is called (`Recorder.exchange_requested`), which is what the action
census holds this module to (tests/test_w10b_actions.py, NOT_ACTIONS).
"""
from __future__ import annotations

import logging

import httpx

# How long the upstream may take: to connect, and between two pieces of a response (a model's stream pings; a
# whole response may take minutes to begin).
CONNECT_SECONDS = 10.0
READ_SECONDS = 600.0


class _NoQueryInTheRequestLine(logging.Filter):
    """The network client logs each request's whole URL (logger `httpx`, INFO), and a provider's key can ride in
    the URL's query (`?key=`). Wherever this module is loaded, that line names the URL without its query: a log
    never holds what the store does not."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 2 and isinstance(args[1], httpx.URL) and args[1].query:
            record.args = (args[0], args[1].copy_with(query=None), *args[2:])
        return True


logging.getLogger("httpx").addFilter(_NoQueryInTheRequestLine())


class UpstreamError(RuntimeError):
    """The upstream could not be reached, or its response broke off. `kind` is the error's class name, the only
    part recorded: an error's text can carry the URL's own text and is not kept."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


class Upstream:
    """One response being read: its status, its headers as they came, and its body in the upstream's own bytes."""

    def __init__(self, response: httpx.Response):
        self._response = response
        self.status = response.status_code
        self.response_headers = [(name, value) for name, value in response.headers.raw]
        self.content_type = response.headers.get("content-type", "").strip().lower()
        self.content_encoding = response.headers.get("content-encoding", "")

    async def chunks(self):
        try:
            async for chunk in self._response.aiter_raw():
                if chunk:
                    yield chunk
        except httpx.HTTPError as e:
            raise UpstreamError(type(e).__name__) from None

    async def aclose(self) -> None:
        try:
            await self._response.aclose()
        except httpx.HTTPError:
            pass


class Forwarder:
    def __init__(self, *, transport=None):
        self._client = httpx.AsyncClient(
            transport=transport, follow_redirects=False,
            timeout=httpx.Timeout(READ_SECONDS, connect=CONNECT_SECONDS))
        self._client.headers.clear()                 # nothing of the client's own goes to the upstream

    async def send(self, url: str, headers: list, body: bytes) -> Upstream:
        """Send the request and return its response once the headers are in; `UpstreamError` when none came."""
        try:
            request = self._client.build_request("POST", url, headers=headers, content=body)
            return Upstream(await self._client.send(request, stream=True))
        except httpx.HTTPError as e:
            raise UpstreamError(type(e).__name__) from None
        except (httpx.InvalidURL, ValueError) as e:      # a path the client cannot put in a URL
            raise UpstreamError(type(e).__name__) from None

    async def aclose(self) -> None:
        await self._client.aclose()
