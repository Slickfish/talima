"""W4 part 1 — the identity boundary: who a request is taken to be from
(run/specs/W4.md S2, D1 to D9).

`NEXUS_AUTH_MODE` selects one of two gates. It is decided once, at the
factory (`backend.server.create_app` reads it and keeps the result on
`app.state`), never at import and never per request:

    token   (unset or empty) today's gate, byte for byte: the bearer token on
            the writers and the evidence reads, every other GET open under the
            localhost posture, and who a request is from asserted in its body.
    proxy   an identity proxy in front (Tailscale Serve, Cloudflare Access, a
            pod's reverse proxy) authenticates the person and passes their
            login in one header; this process answers only for its configured
            owner, and only to a loopback peer. NEXUS authenticates nobody.

Any other value is refused by name before anything is opened (`start.py`:
exit 2, nothing launched; the console's factory: the reason on stderr, exit 2).
MCP has no HTTP app and no peer: nothing under `backend/mcp/` reads these
settings or imports this module (tests/test_w4_identity.py, I15).

THE RULE IN PROXY MODE, IN D2'S ORDER, applied by `IdentityGate` to every
HTTP request the app serves but the exact paths /health and /healthz:
  1. the peer (`scope["client"]`) is a loopback address with a real port, and
     not an address the request's X-Forwarded-For names, else 401;
  2. the identity header arrives exactly once and is non-empty once the
     spaces and tabs around it are trimmed, else 401;
  3. no owner configured is 403 "owner not set", never adoption;
  4. a login that is not the owner's, compared exactly, is 403;
  5. an unsafe request (any method but GET, HEAD and OPTIONS) that a browser
     sent from another site is 403 (W4's close, V1): its Sec-Fetch-Site is
     present and is not `same-origin`, or it carries an Origin and no
     Sec-Fetch-Site. The owner's own browser is the case this step is for,
     so it comes after the owner checks.
With no owner, then, a request the first two rules refuse is still 401 and
every other is 403; none is accepted. On success the established identity
is left on the scope under one key (`SCOPE_KEY`). `require_token` in
backend/server.py requires it in proxy mode, so a gated route refuses even if
the gate were missing (D3).

"EXACTLY" (W4's close, V2 and V6). The login is compared with the owner's by
`!=` and nothing else: no case folding, no normalisation, no splitting on a
comma. The only characters trimmed are HTTP's optional whitespace, space and
horizontal tab (RFC 9110's OWS), from the header's value and from
NEXUS_OWNER_IDENTITY alike. Python's `str.strip()` also removes U+00A0,
U+2003, U+3000 and the rest of Unicode's whitespace, so with it the owner's
login padded with one of those was taken as the owner's (the verifier's V6,
on a real socket: h11 passes those characters' UTF-8 bytes as they came).

WHY THE CROSS-SITE STEP READS SEC-FETCH-SITE (W4's close, V1). In proxy mode
the identity is ambient: the proxy attaches the tester's login to every
request the tester's browser sends, so a page from another site that the
tester opens could post to NEXUS as the tester (the verifier forged
/kb/seed/offline?confirmed=true, /kb/trails/backfill/confirm?confirmed=true
and /kairos/cycle with a plain form). NEXUS cannot compare Origin with its
own origin: it reads no Host, and a proxy may rewrite it. The browser can,
and says what it found in Sec-Fetch-Site (Chrome 76, Firefox 90, Safari 16.4
and later); the console's own fetches say `same-origin`. A client that is no
browser (curl, the operator's scripts, the simulated UAT) sends neither
header and is not refused. A browser too old to send Sec-Fetch-Site sends
Origin on a POST and is refused: the step fails closed. GET and HEAD are left
as they are: no GET route writes, and no response carries CORS headers, so a
page from another site cannot read one. Token mode has no such step: a page
from another site cannot attach the bearer.

WHY THE PEER RULE NAMES X-FORWARDED-FOR (D6, and what W4's build measured).
uvicorn wraps the app in `ProxyHeadersMiddleware` unless it is started with
`--no-proxy-headers`; for a peer it trusts, it replaces `scope["client"]`
with an address from X-Forwarded-For and keeps the original nowhere. D6
refuses port 0, which such a peer used to carry and no socket does. uvicorn
0.54.0 parses `host:port` and `[v6]:port` entries and carries the port too:
a remote peer sending `X-Forwarded-For: 127.0.0.1:4444` to a launch that
trusts every address reaches the app as ('127.0.0.1', 4444). So the rule
also refuses a peer equal to an address the request's X-Forwarded-For names,
each entry parsed as that middleware parses it. Under start.py's launch
(`--no-proxy-headers`) the peer is the socket's own, and a real proxy's
X-Forwarded-For names its client, never the proxy's own connection.

THE HEADERS ARE READ IN ONE FUNCTION (D5): `read_request_headers`, one pass
over `scope["headers"]` for the identity header, the forwarded-for entries
the peer rule needs, and Sec-Fetch-Site and Origin for the cross-site step.
No refusal echoes a presented login or origin, and nothing here logs one.

Standard library only, the shape of `backend/providers/backends.py`:
`start.py` imports this before its dependency check.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass

MODE_SETTING = "NEXUS_AUTH_MODE"
HEADER_SETTING = "NEXUS_IDENTITY_HEADER"
OWNER_SETTING = "NEXUS_OWNER_IDENTITY"
TOKEN, PROXY = "token", "proxy"
KNOWN_MODES = (TOKEN, PROXY)
DEFAULT_HEADER = "Tailscale-User-Login"
# start.py's EXIT_USAGE, the same number: a wrong setting is a usage error.
EXIT_AUTH_CONFIG = 2
# The only paths proxy mode leaves open (D2): liveness for a container's
# HEALTHCHECK and a monitor. They carry no belief content.
OPEN_PATHS = frozenset({"/health", "/healthz"})
# Where the gate leaves the established identity on the ASGI scope.
SCOPE_KEY = "nexus.identity"
FORWARDED_FOR = b"x-forwarded-for"
# W4's close (V1): the two headers the cross-site step reads, as an ASGI scope carries them.
FETCH_SITE = b"sec-fetch-site"
ORIGIN = b"origin"
# The methods the cross-site step leaves alone. No GET route writes; OPTIONS finds no CORS headers.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# The one Sec-Fetch-Site value a browser sends for a request from the console's own pages.
SAME_ORIGIN = "same-origin"
# W4's close (V6): HTTP's optional whitespace (RFC 9110, OWS: space and horizontal tab), the only
# characters trimmed from the identity header's value and from NEXUS_OWNER_IDENTITY.
OWS = " \t"
# RFC 9110's token: what an HTTP field name is made of.
_FIELD_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
# A websocket is refused before it is accepted: close code 1008, policy.
WEBSOCKET_REFUSED = 1008

# The refusals: fixed sentences. None names a presented value.
NOT_LOOPBACK = "unauthorized: proxy mode accepts a request only from a loopback peer"
REWRITTEN = ("unauthorized: proxy mode accepts a request only from a loopback peer, and this peer was "
             "rewritten from a forwarded header (its port is 0, or it is an address X-Forwarded-For names): "
             "uvicorn must be started without proxy headers (--no-proxy-headers), as start.py starts it "
             "in proxy mode")
NO_IDENTITY = "unauthorized: no identity: the identity header must arrive exactly once, non-empty"
OWNER_NOT_SET = ("owner not set: NEXUS_OWNER_IDENTITY is unset, so this process answers for no one "
                 "and never adopts a login")
NOT_OWNER = "forbidden: the identity is not this instance's owner"
CROSS_SITE = ("forbidden: proxy mode accepts a request that can change state (any method but GET, HEAD and "
              "OPTIONS) from a browser only when the browser says it came from this site's own pages "
              "(Sec-Fetch-Site: same-origin); this one came from another site, or from a browser that does not say")
# require_token's, in proxy mode, when no verdict is on the request.
NO_ESTABLISHED = "unauthorized: no identity was established for this request (proxy mode)"

# GET /whoami's note, per mode.
NOTES = {
    TOKEN: ("token mode: the bearer token gates the writers and the evidence reads; who a request is from "
            "is what its body asserts, recorded and never authenticated"),
    PROXY: ("proxy mode: this process took the login from the identity header of a loopback peer and "
            "answers only for its configured owner; the proxy authenticated the person, NEXUS did not"),
}


class AuthConfigError(ValueError):
    """The identity settings do not describe a gate NEXUS can build. The
    console's factory prints it and leaves with `EXIT_AUTH_CONFIG`, having
    opened nothing; start.py refuses it first, as a usage error."""


class UnknownAuthMode(AuthConfigError):
    """`NEXUS_AUTH_MODE` names no mode NEXUS has."""


class BadIdentityHeader(AuthConfigError):
    """Proxy mode's `NEXUS_IDENTITY_HEADER` is not an HTTP field name, so no
    request could ever carry it."""


@dataclass(frozen=True)
class IdentityConfig:
    """The three settings as the factory read them (Law 1: replaced, never
    mutated). `header` is the name as written; matching is case-insensitive,
    as HTTP field names are."""
    mode: str
    header: str
    owner: "str | None"

    @property
    def is_proxy(self) -> bool:
        return self.mode == PROXY

    @property
    def owner_set(self) -> bool:
        return bool(self.owner)

    @property
    def header_key(self) -> bytes:
        """The name as an ASGI scope carries it: lower case, bytes."""
        return self.header.lower().encode("latin-1", "replace")


def _trimmed(value: str) -> str:
    """`value` with the spaces and tabs at either end removed, and nothing
    else (V6): never `str.strip()`, which also removes U+00A0, U+2003,
    U+3000 and the rest of Unicode's whitespace."""
    return value.strip(OWS)


def auth_mode_name(environ=None) -> str:
    """The setting, stripped and lowered; unset or empty is `token`."""
    environ = os.environ if environ is None else environ
    return (environ.get(MODE_SETTING) or "").strip().lower() or TOKEN


def _header_name(environ) -> str:
    return (environ.get(HEADER_SETTING) or "").strip() or DEFAULT_HEADER


def auth_config_reason(environ=None) -> "str | None":
    """Why the factory would refuse these settings, else None: a mode NEXUS
    does not know (named, with the two that exist), or, in proxy mode, an
    identity header no request could carry. One text for start.py's checks
    and the factory's exception."""
    environ = os.environ if environ is None else environ
    name = auth_mode_name(environ)
    if name not in KNOWN_MODES:
        return (f"{MODE_SETTING}={environ.get(MODE_SETTING)!r} is not an auth mode NEXUS knows "
                f"({', '.join(KNOWN_MODES)})")
    if name == PROXY and not _FIELD_NAME.fullmatch(_header_name(environ)):
        return (f"{HEADER_SETTING}={environ.get(HEADER_SETTING)!r} is not an HTTP header name, so no "
                f"request could carry it")
    return None


def identity_config(environ=None) -> IdentityConfig:
    """The settings, read now (the factory's call, never an import's).
    Raises `UnknownAuthMode` or `BadIdentityHeader` by name."""
    environ = os.environ if environ is None else environ
    reason = auth_config_reason(environ)
    if reason is not None and auth_mode_name(environ) not in KNOWN_MODES:
        raise UnknownAuthMode(reason)
    if reason is not None:
        raise BadIdentityHeader(reason)
    # The owner is trimmed as the header's value is, spaces and tabs only (V6), so the two compare exactly.
    return IdentityConfig(mode=auth_mode_name(environ), header=_header_name(environ),
                          owner=_trimmed(environ.get(OWNER_SETTING) or "") or None)


# ---------------------------------------------------------------------------
# The peer
# ---------------------------------------------------------------------------

def _peer(client) -> "tuple[str, int] | None":
    """`scope["client"]` as (host, port) when it is a pair of a string and an
    integer (a bool is not a port), else None: no client, a Unix socket's
    path, anything else an ASGI server might hand over."""
    if not isinstance(client, (tuple, list)) or len(client) != 2:
        return None
    host, port = client
    if not isinstance(host, str) or not isinstance(port, int) or isinstance(port, bool):
        return None
    return host, port


def is_loopback_address(host: str) -> bool:
    """127.0.0.0/8, ::1, or an IPv4-mapped loopback (::ffff:127.0.0.1), the
    last unwrapped here so the answer does not depend on the Python patch
    version's reading of it. A host name, `testclient` or anything
    unparseable is not."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def is_loopback_peer(client) -> bool:
    """A loopback address with a real port (1 to 65535): a rewritten peer
    carries port 0, and no socket does."""
    peer = _peer(client)
    return peer is not None and 0 < peer[1] <= 65535 and is_loopback_address(peer[0])


def _forwarded_address(entry: str) -> tuple[str, int]:
    """One X-Forwarded-For entry as uvicorn's ProxyHeadersMiddleware turns
    it into a peer (0.54.0's `_parse_host_port`): a bare address with port 0,
    `host:port`, `[v6]:port`; anything malformed whole, with port 0."""
    if entry.startswith("["):
        end = entry.find("]")
        if end == -1:
            return entry, 0
        host, rest = entry[1:end], entry[end + 1:]
        if not rest:
            return host, 0
        if not rest.startswith(":"):
            return entry, 0
        try:
            return host, int(rest[1:])
        except ValueError:
            return host, 0
    if entry.count(":") == 1:
        host, port = entry.rsplit(":", 1)
        try:
            return host, int(port)
        except ValueError:
            return entry, 0
    return entry, 0


# ---------------------------------------------------------------------------
# The one reader of a request's headers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RequestHeaders:
    """What the gate needs from a request's headers: the identity header's
    value (None unless it arrived exactly once, as UTF-8 text, non-empty once
    the spaces and tabs around it are trimmed), the addresses its
    X-Forwarded-For entries name, and, for the cross-site step (W4's close),
    every Sec-Fetch-Site value in arrival order (each trimmed as a field
    value is) and every Origin value, as they came. An absent header is ()."""
    identity: "str | None"
    forwarded: frozenset
    fetch_site: tuple = ()
    origin: tuple = ()


def read_request_headers(scope, header_key: bytes) -> RequestHeaders:
    """THE one function that reads a request's headers in proxy mode (D5):
    one pass over the ASGI scope's header list, for the identity header
    (`header_key`, lower case), the forwarded-for entries, Sec-Fetch-Site and
    Origin. Nothing else in the backend but the bearer's `require_token`
    reads a request's headers (tests/test_w4_identity.py, I7)."""
    identity_values, forwarded_values, fetch_site, origin = [], [], [], []
    for name, value in scope.get("headers") or ():
        name = bytes(name).lower()
        if name == header_key:
            identity_values.append(bytes(value))
        if name == FORWARDED_FOR:
            forwarded_values.append(bytes(value).decode("latin-1"))
        if name == FETCH_SITE:
            fetch_site.append(_trimmed(bytes(value).decode("latin-1")))
        if name == ORIGIN:
            origin.append(bytes(value).decode("latin-1"))
    identity = None
    if len(identity_values) == 1:
        try:
            identity = _trimmed(identity_values[0].decode("utf-8")) or None
        except UnicodeDecodeError:
            identity = None
    entries = ", ".join(forwarded_values).split(",") if forwarded_values else []
    return RequestHeaders(identity=identity,
                          forwarded=frozenset(_forwarded_address(e.strip()) for e in entries),
                          fetch_site=tuple(fetch_site), origin=tuple(origin))


# ---------------------------------------------------------------------------
# The decision, and the gate that applies it
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EstablishedIdentity:
    """The login this process took the request to be from. How it was
    established is the record's vocabulary, not this module's:
    `backend/memory/lineage.py`'s IDENTITY_MECHANISMS (`proxy` for a login
    this gate established), mapped in one place, `server._identity_mechanism_of`
    (W4 part 1b, which removed a second spelling read by nothing)."""
    login: str


@dataclass(frozen=True)
class Refusal:
    status: int
    detail: str


def is_cross_site(method, headers: RequestHeaders) -> bool:
    """W4's close (V1): whether a request may change state and a browser
    sent it from another site. A safe method (GET, HEAD, OPTIONS) never is.
    Any other is when Sec-Fetch-Site arrived and is not exactly one
    `same-origin` (`cross-site`, `same-site`, `none`, any other value, or
    the header twice), or when no Sec-Fetch-Site arrived and an Origin did
    (a browser too old to send Sec-Fetch-Site: refused, failing closed). A
    request with neither header is from a client that is no browser."""
    if method in SAFE_METHODS:
        return False
    if headers.fetch_site:
        return headers.fetch_site != (SAME_ORIGIN,)
    return bool(headers.origin)


def decide(config: IdentityConfig, scope) -> "EstablishedIdentity | Refusal":
    """D2's rule, in its order, for one HTTP request, and the cross-site
    step after the owner checks (W4's close)."""
    headers = read_request_headers(scope, config.header_key)
    peer = _peer(scope.get("client"))
    rewritten = peer is not None and (peer[1] == 0 or peer in headers.forwarded)
    if peer is None or rewritten or not is_loopback_peer(peer):
        return Refusal(401, REWRITTEN if rewritten else NOT_LOOPBACK)
    if headers.identity is None:
        return Refusal(401, NO_IDENTITY)
    if not config.owner:
        return Refusal(403, OWNER_NOT_SET)
    if headers.identity != config.owner:
        return Refusal(403, NOT_OWNER)
    if is_cross_site(scope.get("method"), headers):
        return Refusal(403, CROSS_SITE)
    return EstablishedIdentity(login=headers.identity)


def identity_on_scope(scope) -> "EstablishedIdentity | None":
    """The gate's verdict on this request, if it established one."""
    value = scope.get(SCOPE_KEY)
    return value if isinstance(value, EstablishedIdentity) else None


async def _refuse(send, refusal: Refusal) -> None:
    body = json.dumps({"detail": refusal.detail}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    await send({"type": "http.response.start", "status": refusal.status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii"))]})
    await send({"type": "http.response.body", "body": body})


class IdentityGate:
    """The proxy-mode gate: pure ASGI, not `BaseHTTPMiddleware`, because
    `/chat` streams. `lifespan` passes through; an `http` request is decided
    (`decide`) unless its path is exactly /health or /healthz; a refusal is
    answered as JSON `{"detail": ...}`; a `websocket` is refused before it is
    accepted; any other scope type is refused by raising. The only identity
    on a scope below the gate is the gate's own verdict: a key an outer
    layer planted is overwritten or removed."""

    def __init__(self, app, config: IdentityConfig):
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind == "lifespan":
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": WEBSOCKET_REFUSED})
            return
        if kind != "http":
            raise RuntimeError(f"IdentityGate refuses an ASGI scope type it does not know: {kind!r}")
        scope.pop(SCOPE_KEY, None)
        if scope.get("path") in OPEN_PATHS:
            await self.app(scope, receive, send)
            return
        verdict = decide(self.config, scope)
        if isinstance(verdict, Refusal):
            await _refuse(send, verdict)
            return
        scope[SCOPE_KEY] = verdict
        await self.app(scope, receive, send)


__all__ = ["AuthConfigError", "BadIdentityHeader", "CROSS_SITE", "DEFAULT_HEADER", "EXIT_AUTH_CONFIG",
           "EstablishedIdentity", "HEADER_SETTING", "IdentityConfig", "IdentityGate", "KNOWN_MODES",
           "MODE_SETTING", "NOTES", "NO_ESTABLISHED", "NO_IDENTITY", "NOT_LOOPBACK", "NOT_OWNER", "OPEN_PATHS",
           "OWNER_NOT_SET", "OWNER_SETTING", "PROXY", "REWRITTEN", "Refusal", "RequestHeaders", "SAFE_METHODS",
           "SCOPE_KEY", "TOKEN", "UnknownAuthMode", "auth_config_reason", "auth_mode_name", "decide",
           "identity_config", "identity_on_scope", "is_cross_site", "is_loopback_address", "is_loopback_peer",
           "read_request_headers"]
