"""W10c (run/specs/W10c.md S7) — the interceptor's settings, read once, and what is refused before anything opens.

    NEXUS_INTERCEPT_UPSTREAM    the provider the exchanges are forwarded to: scheme, host, an optional port and an
                                optional path prefix (`https://api.anthropic.com`). Required.
    NEXUS_INTERCEPT_DATA_ROOT   the folder the interceptor's own store lives in. Required; never the console's.
    NEXUS_INTERCEPT_OWNER       the accountable identity on every record, asserted and never authenticated;
                                unset = unattributed. Behind the identity proxy the proxy's login is used instead.
    NEXUS_INTERCEPT_INJECT      off (the default: a pure recorder, nothing added to a request) | on (NEXUS's
                                retrieval block added as a delimited system block; needs the memory engine).
    NEXUS_INTERCEPT_PROXY_HEADERS  behind the identity proxy: the proxy's own headers, named (each exact, or a
                                prefix ending in `*`), in place of the guess from the identity header's name.

The identity boundary is W4's (`backend/identity.py`: NEXUS_AUTH_MODE, NEXUS_IDENTITY_HEADER, NEXUS_OWNER_IDENTITY).
Every refusal is `<reason>: <why>` and the process exits `EXIT_CONFIG` having opened nothing: no store, no lock, no
socket. Nothing here imports the memory engine (tests/test_w10c_standalone.py).
"""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from backend import identity
from backend.data_root import DATA_ROOT_SETTING

UPSTREAM_SETTING = "NEXUS_INTERCEPT_UPSTREAM"
DATA_ROOT = "NEXUS_INTERCEPT_DATA_ROOT"
OWNER_SETTING = "NEXUS_INTERCEPT_OWNER"
INJECT_SETTING = "NEXUS_INTERCEPT_INJECT"
PROXY_HEADERS_SETTING = "NEXUS_INTERCEPT_PROXY_HEADERS"
_HEADER_NAME = re.compile(r"^[a-z0-9!#$%&'+.^_`|~-]+$")     # a header's name, lower case; `*` is kept for a prefix
MCP_DATA_ROOT_SETTING = "NEXUS_MCP_DATA_ROOT"      # the MCP process's root (backend/mcp/config.py), never ours
INJECT_VALUES = ("off", "on")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790
EXIT_CONFIG = 2
REPO = Path(__file__).resolve().parent.parent.parent


class ConfigRefused(ValueError):
    """`<reason>: <why>`; nothing was opened."""


@dataclass(frozen=True)
class InterceptConfig:
    upstream: str                       # scheme://host[:port][/prefix], no trailing slash
    data_root: Path
    owner: "str | None"
    inject: bool
    host: str
    port: int
    identity: identity.IdentityConfig
    proxy_headers: "tuple | None" = None    # the proxy's own headers, named; None: guessed from the identity header

    @property
    def proxy_mode(self) -> bool:
        return self.identity.is_proxy


def upstream(environ=None) -> str:
    """NEXUS_INTERCEPT_UPSTREAM, checked: the base the harness's path is added to."""
    return _upstream((os.environ if environ is None else environ).get(UPSTREAM_SETTING))


def data_root(environ=None) -> Path:
    """NEXUS_INTERCEPT_DATA_ROOT, checked: the interceptor's own folder."""
    environ = os.environ if environ is None else environ
    return _data_root(environ.get(DATA_ROOT), environ)


def owner(environ=None) -> "str | None":
    """NEXUS_INTERCEPT_OWNER: asserted, never authenticated; None when unset."""
    return ((os.environ if environ is None else environ).get(OWNER_SETTING) or "").strip() or None


def proxy_headers(environ=None) -> "tuple | None":
    """NEXUS_INTERCEPT_PROXY_HEADERS: the identity proxy's own headers, named, for where the guess from the
    identity header's name would be wrong. None when unset; else the names in lower case, a name ending in `*`
    being a prefix. Anything else is refused by name."""
    raw = ((os.environ if environ is None else environ).get(PROXY_HEADERS_SETTING) or "").strip()
    if not raw:
        return None
    names = tuple(part.strip().lower() for part in raw.split(","))
    for name in names:
        if not _HEADER_NAME.match(name[:-1] if name.endswith("*") else name):
            raise ConfigRefused(f"bad_proxy_headers: {PROXY_HEADERS_SETTING} is header names separated by commas, "
                                f"each exact or a prefix ending in *; {name!r} is neither")
    return names


def inject(environ=None) -> bool:
    """NEXUS_INTERCEPT_INJECT: False (`off`, the default) or True (`on`); any other value is refused by name."""
    value = ((os.environ if environ is None else environ).get(INJECT_SETTING) or "").strip().lower() or "off"
    if value not in INJECT_VALUES:
        raise ConfigRefused(f"bad_inject: {INJECT_SETTING} is one of {' | '.join(INJECT_VALUES)}")
    return value == "on"


def _upstream(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise ConfigRefused(f"no_upstream: {UPSTREAM_SETTING} is not set; it names the provider the exchanges are "
                            "forwarded to (for example https://api.anthropic.com)")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigRefused(f"bad_upstream: {UPSTREAM_SETTING} must be an http or https URL with a host")
    if parts.username is not None or parts.password is not None:
        raise ConfigRefused(f"bad_upstream: {UPSTREAM_SETTING} carries a credential in its URL; a credential is the "
                            "harness's to send, in a header")
    if parts.query or parts.fragment:
        raise ConfigRefused(f"bad_upstream: {UPSTREAM_SETTING} carries a query or a fragment; it is a base the "
                            "harness's path is added to")
    try:
        parts.port
    except ValueError:
        raise ConfigRefused(f"bad_upstream: {UPSTREAM_SETTING} has a port that is not a number") from None
    if parts.scheme == "http" and not identity.is_loopback_address(parts.hostname) and parts.hostname != "localhost":
        raise ConfigRefused(f"bad_upstream: {UPSTREAM_SETTING} is plain http to a host that is not this machine; the "
                            "harness's credential would cross the network unencrypted")
    return value.rstrip("/")


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _data_root(raw: str, environ) -> Path:
    value = (raw or "").strip()
    if not value:
        raise ConfigRefused(f"no_data_root: {DATA_ROOT} is not set; the interceptor keeps its own store, in a folder "
                            "of its own")
    root = Path(value)
    if _inside(root, REPO / "data"):
        raise ConfigRefused(f"data_root_refused: {DATA_ROOT} is the repository's data folder, which is the console's")
    for setting in (DATA_ROOT_SETTING, MCP_DATA_ROOT_SETTING):
        other = (environ.get(setting) or "").strip()
        if other and _same(root, Path(other)):
            raise ConfigRefused(f"data_root_refused: {DATA_ROOT} is {setting}'s folder; the interceptor never "
                                "records into another mode's store")
    return root


def load(environ=None, *, host: "str | None" = None, port: "int | None" = None,
         need_upstream: bool = True) -> InterceptConfig:
    """The configuration, or `ConfigRefused` by name. Reads the environment once; opens nothing. A command that
    only reads a stopped store forwards nothing, so it may be given no upstream (`need_upstream=False`); one that
    is given is still checked."""
    environ = os.environ if environ is None else environ
    reason = identity.auth_config_reason(environ)
    if reason:
        raise ConfigRefused(f"bad_auth_mode: {reason}")
    ident = identity.identity_config(environ)
    injecting = inject(environ)
    bind = (host or DEFAULT_HOST).strip()
    # An address, never a name, in every mode: `localhost` is whatever the machine's resolver says it is.
    try:
        ipaddress.ip_address(bind)
    except ValueError:
        raise ConfigRefused(f"bind_refused: {bind} is not an address; the interceptor listens on an address, never "
                            "on a name (for this machine alone: 127.0.0.1 or ::1)") from None
    if not identity.is_loopback_address(bind) and ident.mode != identity.PROXY:
        raise ConfigRefused(f"bind_refused: {bind} is not a loopback address (127.0.0.1, ::1); the interceptor "
                            f"listens beyond this machine only behind the identity proxy "
                            f"({identity.MODE_SETTING}={identity.PROXY})")
    number = DEFAULT_PORT if port is None else port
    if not (isinstance(number, int) and 0 <= number < 65536):
        raise ConfigRefused("bad_port: a port is a number from 0 to 65535")
    given = (environ.get(UPSTREAM_SETTING) or "").strip()
    return InterceptConfig(upstream=upstream(environ) if (need_upstream or given) else "",
                           data_root=data_root(environ), owner=owner(environ),
                           inject=injecting, host=bind, port=number, identity=ident,
                           proxy_headers=proxy_headers(environ))
