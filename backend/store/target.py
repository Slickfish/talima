"""What a store IS: a file (SQLite) or a database (Postgres, W3b part 2).

Every store class takes `db_path: Path | str | StoreTarget`. A path means
SQLite exactly as before W3b, so every call site and every test keeps its
meaning; a `StoreTarget` names the backend. `create_services` reads the
backend from the settings (`NEXUS_STORE`, `NEXUS_DATABASE_URL`, part 3).

A database URL carries secrets, and this module is the one place that knows
them (W3b close, F6): `url_secrets`, `redacted_url`, `redacted_text`,
`redacted_error` and `url_without_secrets`, below the settings. It is also
the one place that says whether libpq would read a URL as it was written
(`url_unreadable_reason`: F6b, and D5, which asks libpq itself); a URL it
would not is refused by name before anything connects, and no line shows
any of it.

The driver is never imported at this module's top: the desktop bundle
leaves it out, and a SQLite host need not install it. libpq is asked through
a lazy import, and where the driver is absent the step is skipped.
"""
from __future__ import annotations

import os
import re
import traceback
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, unquote_plus

SQLITE = "sqlite"
POSTGRES = "postgres"
BACKENDS = (SQLITE, POSTGRES)


@dataclass(frozen=True)
class StoreTarget:
    backend: str
    path: Path | None = None      # SQLite: the database file
    url: str | None = None        # Postgres: the connection URL (its repr is redacted_url's: F6, D5)
    schema: str | None = None     # Postgres: the schema holding the store (None: the URL's own)

    def __repr__(self) -> str:
        # Written here, so the dataclass keeps it: the generated repr named the
        # URL with its secrets, and a target reaches log lines, exception
        # messages and pytest's assertion output as its repr (W3b close, F6).
        shown = redacted_url(self.url) if self.url else None
        return f"StoreTarget(backend={self.backend!r}, path={self.path!r}, url={shown!r}, schema={self.schema!r})"

    def __post_init__(self) -> None:
        if self.backend not in BACKENDS:
            raise ValueError(f"unknown store backend {self.backend!r} (one of {BACKENDS})")
        if self.backend == SQLITE and self.path is None:
            raise ValueError("a sqlite target needs a path")
        if self.backend == POSTGRES and not self.url:
            raise ValueError("a postgres target needs a database URL")

    @classmethod
    def sqlite(cls, path: "Path | str | os.PathLike") -> "StoreTarget":
        return cls(backend=SQLITE, path=Path(path))

    @classmethod
    def postgres(cls, url: str, *, schema: str | None = None) -> "StoreTarget":
        return cls(backend=POSTGRES, url=url, schema=schema)

    @classmethod
    def of(cls, value: "StoreTarget | Path | str | os.PathLike") -> "StoreTarget":
        """A target from what a store constructor was given: a target as it
        is, anything path-like as a SQLite target."""
        if isinstance(value, StoreTarget):
            return value
        return cls.sqlite(value)

    def describe(self) -> str:
        if self.backend == SQLITE:
            return str(self.path)
        return f"postgres {redacted_url(self.url)}" + (f" schema {self.schema}" if self.schema else "")


# ---------------------------------------------------------------------------
# The settings (W3b part 3, D1): read one way everywhere, as LLM_BACKEND is
# (`backend/providers/backends.py`). `NEXUS_STORE` names the backend; unset
# or empty is sqlite, as it always was. `postgres` needs `NEXUS_DATABASE_URL`,
# a postgresql:// URL naming the database that holds every table kb.db and
# archive.db hold today (D7); the data root keeps chroma/, snapshots/ and the
# logs. A value NEXUS does not know, a missing URL or a URL of another
# scheme is refused BY NAME at startup — the console's factory, MCP's main
# and start.py — having opened nothing: the store settings are resolved
# before the lock is taken and before any store connects.
# ---------------------------------------------------------------------------

STORE_SETTING = "NEXUS_STORE"
URL_SETTING = "NEXUS_DATABASE_URL"
URL_SCHEMES = ("postgresql://", "postgres://")
#: start.py's EXIT_USAGE, the same number as an unknown LLM_BACKEND's: a wrong setting is a usage error.
EXIT_STORE_CONFIG = 2


class StoreConfigError(ValueError):
    """The store settings do not describe a store NEXUS can open."""


def store_backend_name(environ=None) -> str:
    """`NEXUS_STORE` stripped and lowered; unset or empty is `sqlite`."""
    environ = os.environ if environ is None else environ
    return (environ.get(STORE_SETTING) or "").strip().lower() or SQLITE


def database_url(environ=None) -> str:
    """`NEXUS_DATABASE_URL` stripped; empty when unset."""
    environ = os.environ if environ is None else environ
    return (environ.get(URL_SETTING) or "").strip()


#: A URI scheme as RFC 3986 spells one; a value's text before `://` is named
#: in a refusal only when it is one.
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,31}")


def _scheme_named(value: str) -> str:
    """The scheme a value that is not a postgres URL names (`mysql` of
    `mysql://…`), or "" when it names none. Nothing else of the value: a
    value with no scheme can begin with its password (libpq's keyword/value
    form, `password=… host=…`, or a URL whose scheme was left off), and the
    refusal used to quote its first twelve characters (F6)."""
    head, sep, _rest = value.partition("://")
    return head if sep and _SCHEME.fullmatch(head) else ""


def store_config_reason(environ=None) -> "str | None":
    """The refusal's text when the settings name no store NEXUS can open,
    else None. Names the setting and what is accepted; of the URL, at most
    the scheme of a value that is not a postgres URL, and never a secret: a
    URL libpq would not read as it was written is refused naming the class
    of what is wrong with it and none of its text (`url_unreadable_reason`)."""
    environ = os.environ if environ is None else environ
    name = store_backend_name(environ)
    if name not in BACKENDS:
        return (f"{STORE_SETTING}={environ.get(STORE_SETTING)!r} is not a store backend NEXUS knows "
                f"({', '.join(BACKENDS)})")
    if name == POSTGRES:
        url = database_url(environ)
        if not url:
            return (f"{STORE_SETTING}=postgres needs {URL_SETTING}, a postgresql:// URL naming the database "
                    f"that holds the store; it is unset")
        if not url.lower().startswith(URL_SCHEMES):
            scheme = _scheme_named(url)
            if scheme:
                return f"{URL_SETTING} must begin with postgresql:// or postgres://; it begins with {scheme!r}"
            return (f"{URL_SETTING} must begin with postgresql:// or postgres://; it names no scheme (none of "
                    f"it is shown: a value that is not a URL can begin with its password)")
        unreadable = url_unreadable_reason(url)
        if unreadable:
            return f"{URL_SETTING} is not a URL libpq reads as it was written (none of it is shown): {unreadable}"
    return None


def configured_store_target(environ=None) -> "StoreTarget | None":
    """The Postgres target the settings name, or None when the store is
    SQLite (the paths decide, exactly as before W3b). Raises
    `StoreConfigError` by name for settings that describe no store."""
    reason = store_config_reason(environ)
    if reason is not None:
        raise StoreConfigError(reason)
    if store_backend_name(environ) == POSTGRES:
        return StoreTarget.postgres(database_url(environ))
    return None


# ---------------------------------------------------------------------------
# A URL's secrets (W3b close, F6): the one place that knows them.
#
# CLAUDE.md says the URL's password "is never printed or recorded (every
# line that names the store redacts it)". The redaction blanked only the
# userinfo password, read by `urllib.parse.urlsplit`, and libpq reads more:
#
# - the QUERY. `postgresql://u@h/db?password=…` is libpq's own form, and it
#   reached start.py's first lines, the lock's store name, the instance
#   scripts' first lines, MANIFEST.json and pg_dump's command line verbatim.
#   libpq percent-decodes a key as well as a value (`pass%77ord=` is
#   `password=`), its query runs to the end of the text (it has no fragment:
#   `?password=a#b` is `a#b`), and when a key repeats the last one wins, over
#   the userinfo's too;
# - the USERINFO as libpq splits it, which is not RFC 3986's: libpq's
#   userinfo ends at the FIRST `@` before the first `/` and is not ended by
#   `?` or `#` (`u:pw#x@h` is the password `pw#x`; urlsplit read no password
#   at all), where RFC 3986 ends the authority at `/`, `?` or `#` and its
#   userinfo at the LAST `@`;
# - libpq's own error texts echo a token (`invalid percent-encoded token:
#   "…"`) or the whole text (an IPv6 error, an upper-case scheme libpq does
#   not read as a URI, `missing "=" after "…"`).
#
# - and a password its WRITER did not percent-encode can be a password to
#   NEITHER reader (F6b): for `postgresql://u:pa/ss@h/db`, a base64
#   password, libpq and RFC 3986 both take `u` for the host and `pa` for the
#   port, and the first line printed it whole. The third reading is the
#   writer's (`_intended_secret_spans`: from the first `:` to the last
#   `@`), and a URL whose secrets libpq would not read where they were
#   written is refused by name before anything connects
#   (`url_unreadable_reason`), because libpq's refusal of it echoes a
#   fragment no redaction can know.
#
# So a secret is every value any of the three readings takes as one: the
# userinfo password and every query parameter whose percent-decoded key is a
# secret parameter, in any letter case, raw and decoded. Measured against both
# libpqs NEXUS meets (the driver's, 18, and the client tools', 16), whose
# URI parser is fe-connect.c's conninfo_uri_parse_options, read and matched
# (tests/test_w3b_secrets.py U2 to U4 use libpq itself as the oracle).
#
# AND WHAT NO READING KNOWS (W3b close, D5; the delta check). Three readings
# of where a secret sits cannot find one its writer put somewhere else:
#
# - under a key libpq does not know. `postgresql://u@h/db?passwd=…` (a
#   mistyped `password`; `pwd`, `pass`, `secret` alike) is a secret to no
#   reading, so start.py's first line and the instance scripts' printed it
#   whole; libpq would have refused the URL (`invalid URI query parameter`);
# - past an `&` or `=` its writer did not percent-encode: both readers end a
#   query value at the `&`, and the tail of the password was printed;
# - in a URL libpq refuses for another reason (an invalid percent-encoding,
#   a raw space, an upper-case scheme): the documents said such a URL "is
#   refused by name, none of it shown", and it reached the driver, exit 1
#   with its traceback, the user name, the host and the database on the
#   first line.
#
# NEXUS does not guess a fourth reading. It ASKS LIBPQ, the driver's
# PQconninfoParse, after its own readings (`url_unreadable_reason`), and a
# URL libpq refuses to parse is refused by name before anything connects:
# by `store_config_reason` (the setting: start.py and MCP's main stop with
# exit 2, nothing opened), by `connect()` (before the driver's connect is
# called) and by `url_without_secrets` (before a client tool is run). The
# console's factory raises the setting's refusal as `StoreConfigError`: by
# name too, but where uvicorn is run without start.py, whose own check comes
# first, the process ends by uvicorn's traceback and its exit 1 (measured at
# D5; so for every refused store setting since part 3, and left as it is
# here). The refusal names the CLASS of what is wrong, from a
# closed table of how libpq's texts begin (`LIBPQ_REFUSALS`), and none of
# libpq's own text: that text quotes a token or the whole URI. And every
# line that names the store shows such a URL as one fixed marker
# (`UNREADABLE_URL`), none of it: whatever was mistyped in it, wherever.
#
# What stays shown, by design: a URL libpq parses, with every value libpq
# reads as a secret blanked. A value under a key libpq knows and does not
# hold secret (`application_name`, `options`, `sslkey`, a `passfile` path)
# is not a secret to libpq and is shown as written.
# ---------------------------------------------------------------------------

REDACTED = "***"
#: What a line names for a value that is not a postgres URL: none of it.
NOT_A_URL = "<not a postgresql:// URL>"
#: What a line names for a postgres URL NEXUS refuses because libpq would not
#: read it as it was written (`url_unreadable_reason`): none of it either
#: (D5). One fixed text, so nothing mistyped in the URL can reach a line.
UNREADABLE_URL = "<a postgresql:// URL libpq would not read as it was written: not shown>"
#: And where the driver is not installed, so libpq cannot be asked: none of
#: any postgres URL. Nothing there can open the store (`connect()` refuses it
#: for the driver's absence) or say what in the URL libpq would not read.
UNASKED_URL = ("<a postgresql:// URL, not shown: the psycopg driver is not installed here, so libpq cannot be asked "
               "how it reads it>")

#: The connection parameters whose value is a secret. `password` and
#: `sslpassword` are libpq's password fields (PQconndefaults' display
#: character `*`, "hide the value") in every libpq; libpq 18 adds
#: `oauth_client_secret` (`*`) and the SCRAM keys, which it marks only as
#: debug options (`D`) but which are credential material (a client key
#: authenticates as the role without the password). Matched on the
#: percent-decoded key in any letter case: libpq itself refuses `PASSWORD=`,
#: and the value is still the operator's secret. Derived from both libpqs by
#: tests/test_w3b_secrets.py U1, which fails when one marks another.
SECRET_PARAMETERS = frozenset({"password", "sslpassword", "oauth_client_secret", "scram_client_key",
                               "scram_server_key"})
#: The one secret libpq also reads from the environment, so the one a client
#: tool can be given off its command line (PQconndefaults' envvar; every
#: other secret parameter has none, U1).
SECRET_ENVIRONMENT = {"password": "PGPASSWORD"}


class SecretNotPassable(ValueError):
    """A URL a client tool cannot be given without a secret on its command
    line, or one libpq would read differently from what NEXUS read. The
    message names why and never a secret."""


@dataclass(frozen=True)
class _Span:
    start: int
    end: int


@dataclass(frozen=True)
class _Param:
    key: str                      # percent-decoded, its letter case kept
    value: "_Span | None"         # None: no `=`
    whole: _Span                  # `key=value`, without its `&`


@dataclass(frozen=True)
class _Layout:
    """Where libpq reads each part of a postgres URL."""
    password: "_Span | None"      # the userinfo password: after the first `:`, to the first `@`
    dbname: "_Span | None"
    query: "_Span | None"         # after the `?`, to the end of the text
    params: tuple
    complete: bool                # False: libpq refuses the text where the reading stopped


def _scheme_length(text: str) -> int:
    """The length of the `postgresql://` or `postgres://` that begins `text`,
    in any letter case (as NEXUS accepts it; libpq reads only lower case and
    echoes a text it does not read as a URI whole), else 0."""
    head = text[:len("postgresql://")].lower()
    for prefix in URL_SCHEMES:
        if head.startswith(prefix):
            return len(prefix)
    return 0


def _decoded(raw: str) -> str:
    """`raw` percent-decoded where it decodes; else as written."""
    try:
        return unquote(raw, errors="strict")
    except UnicodeDecodeError:
        return raw


def _is_secret_key(key: str) -> bool:
    return key.strip(" ").lower() in SECRET_PARAMETERS


def _params(text: str, start: int, end: int) -> tuple:
    """The `key=value` parameters of `text[start:end]`, split on `&` (a
    second `=` stays in the value: libpq refuses it, and the value is still
    a secret)."""
    out, p = [], start
    while p < end:
        amp = text.find("&", p, end)
        stop = end if amp < 0 else amp
        if stop > p:
            eq = text.find("=", p, stop)
            key = _decoded(text[p:stop] if eq < 0 else text[p:eq])
            out.append(_Param(key, None if eq < 0 else _Span(eq + 1, stop), _Span(p, stop)))
        p = stop + 1
    return tuple(out)


def _libpq_layout(text: str, start: int) -> _Layout:
    """libpq's reading of the URL after its scheme (fe-connect.c,
    conninfo_uri_parse_options): the userinfo when an `@` comes before the
    first `/`, then `host[:port]` separated by `,` (`[v6]` bracketed), then
    `/dbname`, then `?` and the parameters to the end of the text."""
    n, p = len(text), start
    while p < n and text[p] not in "@/":
        p += 1
    password = None
    if p < n and text[p] == "@":
        at, q = p, start
        while text[q] not in ":@":
            q += 1
        if text[q] == ":":
            password = _Span(q + 1, at)
        p = at + 1
    else:
        p = start
    while True:
        if p < n and text[p] == "[":
            close = text.find("]", p + 1)
            if close < 0 or close == p + 1:
                return _Layout(password, None, None, (), False)
            p = close + 1
            if p < n and text[p] not in ":/?,":
                return _Layout(password, None, None, (), False)
        else:
            while p < n and text[p] not in ":/?,":
                p += 1
        prev = text[p] if p < n else ""
        if prev == ":":
            p += 1
            while p < n and text[p] not in "/?,":
                p += 1
            prev = text[p] if p < n else ""
        if prev != ",":
            break
        p += 1
    dbname = None
    if prev == "/":
        p += 1
        first = p
        while p < n and text[p] != "?":
            p += 1
        dbname = _Span(first, p)
        prev = text[p] if p < n else ""
    if prev != "?":
        return _Layout(password, dbname, None, (), True)
    return _Layout(password, dbname, _Span(p + 1, n), _params(text, p + 1, n), True)


def _rfc_secret_spans(text: str, start: int) -> list:
    """RFC 3986's reading (urlsplit's): the authority runs to the first `/`,
    `?` or `#`, its userinfo to the authority's last `@`, the password after
    the userinfo's first `:`; the query from the first `?` after the
    authority to the first `#`."""
    n, end = len(text), start
    while end < n and text[end] not in "/?#":
        end += 1
    spans = []
    at = text.rfind("@", start, end)
    colon = text.find(":", start, at) if at >= 0 else -1
    if colon >= 0:
        spans.append(_Span(colon + 1, at))
    fragment = text.find("#", end)
    query_end = n if fragment < 0 else fragment
    mark = text.find("?", end, query_end)
    if mark >= 0:
        spans += [p.value for p in _params(text, mark + 1, query_end) if p.value and _is_secret_key(p.key)]
    return spans


def _intended_secret_spans(text: str, start: int) -> list:
    """The reading its WRITER meant, where libpq and RFC 3986 both read
    otherwise (W3b close, F6b): the userinfo runs to the LAST `@` in the
    text, and the password is what follows the first `:`.

    A password whose `/` was not percent-encoded (a base64 password) is a
    password to neither reader: for `postgresql://u:pa/ss@h/db` libpq and
    RFC 3986 both take `u` for the host, `pa` for the port and `ss@h/db`
    for the database name, so neither reading blanked it and start.py's
    first line printed it whole. Every password written before an `@` lies
    inside this span, whatever in it was left unencoded; a URL whose only
    `@` ends its userinfo gives the span the other readings give. An `@`
    after the host (in a database name or a query value) widens the span to
    it: such a URL is refused by name before it is used
    (`url_unreadable_reason`), because it cannot be told from a password
    that was not encoded. A line shows none of a URL so refused (D5); the
    span is what `url_secrets` blanks of it in an error's text."""
    at = text.rfind("@", start)
    colon = text.find(":", start, at) if at >= 0 else -1
    return [_Span(colon + 1, at)] if colon >= 0 else []


def _split(url) -> "tuple[str, int, _Layout, list] | None":
    """(the text, where its URL begins after the scheme, libpq's layout,
    every secret span any of the three readings takes: libpq's, RFC 3986's
    and its writer's), or None for a value that is not a postgres URL."""
    text = str(url)
    lead = len(text) - len(text.lstrip())
    length = _scheme_length(text[lead:])
    if not length:
        return None
    start = lead + length
    layout = _libpq_layout(text, start)
    spans = ([layout.password] if layout.password else []) + \
        [p.value for p in layout.params if p.value and _is_secret_key(p.key)] + _rfc_secret_spans(text, start) + \
        _intended_secret_spans(text, start)
    return text, start, layout, spans


def _libpq_decoded(raw: str) -> "str | None":
    """libpq's percent-decoding (conninfo_uri_decode), or None where libpq
    refuses the text (an invalid `%XX`, a `%00`) or it is not UTF-8 once
    decoded. Spaces are not decided here: libpq 16 keeps a raw space, 18
    strips one at either end and refuses one inside."""
    data, out, i = raw.encode("utf-8"), bytearray(), 0
    while i < len(data):
        if data[i] == 0x25:
            pair = data[i + 1:i + 3]
            if len(pair) < 2 or not all(c in b"0123456789abcdefABCDEF" for c in pair) or pair == b"00":
                return None
            out.append(int(pair, 16))
            i += 3
        else:
            out.append(data[i])
            i += 1
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return None


def url_secrets(url) -> tuple:
    """Every text a URL's secrets can appear as, longest first: each secret
    value either reading takes (see above), raw, percent-decoded as libpq
    and as urllib decode it, and trimmed as libpq 18 trims. Empty for a
    value that is not a postgres URL."""
    split = _split(url)
    if split is None:
        return ()
    text, _start, _layout, spans = split
    found: set = set()
    for span in spans:
        raw = text[span.start:span.end]
        forms = {raw, unquote(raw), unquote_plus(raw), _libpq_decoded(raw) or ""}
        found |= forms | {f.strip(" ") for f in forms}
    found.discard("")
    return tuple(sorted(found, key=lambda s: (-len(s), s)))


def redacted_url(url: str) -> str:
    """What a line that names the store shows for `url` (start.py, the lock,
    the instance scripts, MANIFEST.json, a target's repr):

    - a URL libpq reads as it was written: the URL with every secret value
      replaced by `***` and the rest as written (an empty password is left
      empty: there is no secret there to stand for);
    - a URL NEXUS refuses because libpq would not (`url_unreadable_reason`:
      a secret libpq would read as something else, or a text libpq refuses
      to parse): `UNREADABLE_URL`, none of it. What is wrong with it may be
      a secret where no reading looks (`?passwd=…`), so no part is shown;
    - any postgres URL where the driver is not installed: `UNASKED_URL`,
      none of it, since libpq cannot be asked;
    - a value that is not a postgres URL: `NOT_A_URL`, none of it."""
    split = _split(url)
    if split is None:
        return NOT_A_URL
    if url_unreadable_reason(url) is not None:
        return UNREADABLE_URL
    if _libpq_parse() is None:
        return UNASKED_URL
    text, _start, _layout, spans = split
    merged: list = []
    for span in sorted((s for s in spans if s.end > s.start), key=lambda s: (s.start, s.end)):
        if merged and span.start <= merged[-1].end:
            merged[-1] = _Span(merged[-1].start, max(merged[-1].end, span.end))
        else:
            merged.append(span)
    out, pos = [], 0
    for span in merged:
        out += [text[pos:span.start], REDACTED]
        pos = span.end
    return "".join(out) + text[pos:]


def redacted_text(text: str, url) -> str:
    """`text` (a driver's or a client tool's message) with every occurrence
    of the URL itself replaced by what a line may show of it
    (`redacted_url`: nothing, for a URL NEXUS refuses), and of each of its
    secrets in every spelling `url_secrets` gives, blanked: libpq repeats a
    token or the whole URI in some of its errors."""
    if not text:
        return text
    out = str(text)
    for whole in {str(url), str(url).strip()} - {""}:
        out = out.replace(whole, redacted_url(whole))
    for secret in url_secrets(url):
        out = out.replace(secret, REDACTED)
    return out


def redacted_error(exc: BaseException, url) -> BaseException:
    """`exc` when nothing it would print (its text, and every exception it
    chains) carries one of `url`'s secrets; else an exception of the same
    class whose text is `redacted_text`'s, to be raised `from None` so the
    original is not printed with it."""
    shown = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    if redacted_text(shown, url) == shown:
        return exc
    text = redacted_text(str(exc), url)
    try:
        clean = type(exc)(text)
    except Exception:                                   # noqa: BLE001 — a class that takes other arguments
        clean = RuntimeError(f"{type(exc).__name__}: {text}")
    return clean


#: Why a URL whose secrets libpq would not read where they were written is
#: refused. Names no part of the URL.
MISREAD = ("an `@`, `/`, `?` or `#` in its user name or password is not percent-encoded, or an `@` after its "
           "host is not, so libpq and RFC 3986 would not read them as written: part of a secret would be taken "
           "for a host, a port, a user name or a database name, and libpq's own refusal would print it; "
           "percent-encode them (`@` is %40, `/` is %2F, `:` is %3A)")


def _covered_by_libpq(layout: _Layout, spans) -> bool:
    """Whether every secret span lies inside a value libpq itself reads as a
    secret (its userinfo password, a secret query parameter's value)."""
    covered = ([layout.password] if layout.password else []) + \
        [p.value for p in layout.params if p.value and _is_secret_key(p.key)]
    return all(any(c.start <= span.start and span.end <= c.end for c in covered) for span in spans)


#: Why a URL holding a NUL is refused, before libpq is asked: libpq is a C
#: library and its reading of a text ends there, so its answer would be about
#: another text (measured: of `postgresql://u:pw<NUL>rest@h/db` it reads the
#: host `u` and the port `pw`, and its refusal at connect quotes `pw`).
NUL_HELD = ("it holds a NUL character, where libpq (a C library) stops reading: the rest of it, a part of a "
            "secret perhaps, would be read as no part of the URL")

# ---------------------------------------------------------------------------
# libpq's own verdict (W3b close, D5). The classes a refusal names, and the
# closed table that reads the class out of libpq's text.
#
# MEASURED, on PQconninfoParse, which is all `psycopg.conninfo.conninfo_to_dict`
# calls (no connection is made, nothing is resolved): libpq 18.6, the
# driver's (psycopg-binary 3.3.8's own, on Linux; each of these twelve format
# strings is also in the Windows and the macOS wheels' libpq, read out of
# both wheels' binaries), and libpq 16.15, the system's (the client tools').
# Each text goes on to quote a token or the whole URI, which is why none of
# it is passed on:
#
#   how the text begins                                  16   18   class
#   invalid percent-encoded token                        yes  yes  a `%` that begins no percent-encoding
#   forbidden value %00 in percent-encoded value         yes  yes  the same
#   unexpected spaces found in                           no   yes  a raw space (libpq 17 and later; 16 keeps it)
#   invalid URI query parameter                          yes  yes  a parameter libpq does not know
#   missing key/value separator "=" in URI query …       yes  yes  a parameter with no `=`
#   extra key/value separator "=" in URI query …         yes  yes  a parameter with a second `=`
#   end of string reached when looking for matching "]"  yes  yes  a malformed host (NEXUS's own reading
#   IPv6 host address may not be empty in URI            yes  yes    refuses these three first: `_libpq_layout`
#   unexpected character "x" at position N in URI        yes  yes    reads the host part as libpq does)
#   missing "=" after "<the text>" in connection info …  yes  yes  not a URI to libpq: NEXUS takes the scheme in
#   invalid connection option "<the text up to =>"       yes  yes    any letter case and libpq reads only lower
#   unterminated quoted string in connection info …      yes  yes    case, so the text is read as key=value pairs
#
# and, from the driver rather than libpq, a `UnicodeError`: a value that is
# not UTF-8 once percent-decoded (`%ff`; libpq keeps the byte, psycopg cannot
# decode libpq's answer, and so cannot connect with the URL either).
#
# A text the table does not name is still a refusal, of the class "for a
# reason not named here": a later libpq's new text, or a translated one (the
# system libpq is built with NLS; the driver's wheel is not). The class only
# words the refusal. THAT the URL is refused, and shown as nothing, depends
# on libpq raising and on nothing in its text.
# ---------------------------------------------------------------------------

_REFUSES = "libpq refuses to parse it"
_BAD_PERCENT = (f"{_REFUSES}: a `%` in it does not begin a percent-encoding libpq accepts (two hexadecimal "
                f"digits, and never %00); write a literal `%` as %25")
_RAW_SPACE = f"{_REFUSES}: it holds a space that is not percent-encoded; write a space as %20"
_UNKNOWN_PARAMETER = (f"{_REFUSES}: its query names a parameter libpq does not know; check each parameter's name "
                      f"(libpq's are lower case, and the password's is `password`), and write an `&` inside a "
                      f"value as %26")
_NO_EQUALS = f"{_REFUSES}: a parameter in its query has no `=`; write an `&` inside a value as %26"
_SECOND_EQUALS = f"{_REFUSES}: a parameter in its query holds a second `=`; write an `=` inside a value as %3D"
_BAD_HOST = f"{_REFUSES}: its host part is malformed (an IPv6 address is written between `[` and `]`)"
_NOT_A_URI = "libpq does not read it as a URI: write its scheme in lower case (`postgresql://` or `postgres://`)"
_NOT_UTF8 = ("the driver cannot read it: a percent-encoded value in it is not UTF-8 once decoded, or the text "
             "itself is not UTF-8")
_UNNAMED = (f"{_REFUSES}, for a reason not named here (libpq's own message can quote the URL); check it against "
            f"libpq's connection URI form")

#: (how libpq's text begins, the reason NEXUS gives). Closed: a text that
#: begins with none of these is `_UNNAMED`.
LIBPQ_REFUSALS = (
    ("invalid percent-encoded token", _BAD_PERCENT),
    ("forbidden value %00 in percent-encoded value", _BAD_PERCENT),
    ("unexpected spaces found in", _RAW_SPACE),
    ("invalid URI query parameter", _UNKNOWN_PARAMETER),
    ("missing key/value separator", _NO_EQUALS),
    ("extra key/value separator", _SECOND_EQUALS),
    ("end of string reached when looking for matching", _BAD_HOST),
    ("IPv6 host address may not be empty", _BAD_HOST),
    ("unexpected character", _BAD_HOST),
    ('missing "=" after', _NOT_A_URI),
    ("invalid connection option", _NOT_A_URI),
    ("unterminated quoted string in connection info string", _NOT_A_URI),
)


def _libpq_parse():
    """The driver's parse of a connection text (psycopg's `conninfo_to_dict`:
    libpq's PQconninfoParse, no connection made), or None where the driver is
    not installed. Imported here, at the call, never at this module's top:
    the desktop bundle leaves the driver out, and this module is read by
    start.py before its package check."""
    try:
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        return None
    return conninfo_to_dict


def _libpq_refusal(text: str) -> "str | None":
    """Why the driver's libpq refuses to parse `text`, as a class named from
    the closed table and never libpq's own words; None when libpq parses it,
    and when the driver is not installed (the step is skipped: nothing here
    can then open the store either, and `connect()` says so)."""
    parse = _libpq_parse()
    if parse is None:
        return None
    try:
        parse(text)
    except UnicodeError:
        return _NOT_UTF8
    except Exception as e:                              # noqa: BLE001 — libpq's refusal, in whatever class it comes
        said = str(e).lstrip()
        for begins, reason in LIBPQ_REFUSALS:
            if said.startswith(begins):
                return reason
        return _UNNAMED
    return None


def url_unreadable_reason(url) -> "str | None":
    """Why libpq would not read this postgres URL as its writer wrote it, or
    None. Names no part of the URL, and none of libpq's own text (W3b close,
    F6b and D5).

    NEXUS's own readings first (F6b): a URL whose secrets libpq would read
    as something else. The redaction blanks a secret on a line, but libpq's
    refusal of such a URL echoes a FRAGMENT no redaction can know: the port
    it read out of a password (`invalid port number`), the host it read out
    of a password's tail (`failed to resolve host 'ss@h'`: measured).

    Then libpq itself (D5): a text the driver's libpq refuses to parse. What
    is wrong with it can be a secret where no reading looks (a mistyped
    `?passwd=`), so NEXUS does not try to say which part: it names the class
    of libpq's refusal (`LIBPQ_REFUSALS`) and shows none of the URL
    (`redacted_url`). Where the driver is not installed this step is
    skipped.

    So the URL is refused by name before anything connects: by
    `store_config_reason` (start.py and MCP's main: exit 2, nothing opened;
    the console's factory raises it, `StoreConfigError`), by `connect()`
    (the instance scripts' URL argument) and by `url_without_secrets` (the
    backup's client tools)."""
    text = str(url).strip()
    split = _split(text)
    if split is None:
        return "the value is not a postgresql:// URL"
    _text, _start, layout, spans = split
    if "\x00" in text:
        return NUL_HELD
    if not layout.complete:
        return "libpq cannot read this URL (its host part is malformed)"
    if not _covered_by_libpq(layout, spans):
        return MISREAD
    return _libpq_refusal(text)


def url_without_secrets(url: str) -> "tuple[str, str | None]":
    """(the URL a client tool is given, the password it needs): the first
    carries no secret in either reading, the second is the password libpq
    would use (a query `password=` over the userinfo's, the last one over
    the others, percent-decoded as libpq decodes it), or None. The client
    tool gets the password in its environment (PGPASSWORD), never on a
    command line another user's `ps` reads.

    Raises `SecretNotPassable`, naming why and never a secret. First, and
    naming none of the URL, for a URL libpq would not read as it was written
    (`url_unreadable_reason`: one whose userinfo or query RFC 3986 reads
    differently from libpq, where a part of a secret would be left in a host
    or a user name; and, D5, one the driver's libpq refuses to parse, a
    secret under a key libpq does not know among them: it would otherwise be
    handed to the client tool on its command line). Then, for a URL libpq
    parses: a secret libpq has no environment variable for (sslpassword,
    oauth_client_secret, the SCRAM keys: they could only reach a command
    line); a secret parameter written as the client tools' libpq does not
    read it; a password libpq cannot decode; and a space not percent-encoded
    in a secret (libpq 16, the client tools', keeps it and libpq 18, the
    driver's, strips or refuses it, so the two would not authenticate
    alike)."""
    split = _split(str(url).strip())
    if split is None:
        raise SecretNotPassable("the value is not a postgresql:// URL")
    text, _start, layout, _spans = split
    unreadable = url_unreadable_reason(text)
    if unreadable is not None:
        raise SecretNotPassable(f"the URL is not one libpq reads as it was written (none of it is shown): "
                                f"{unreadable}")
    if any(p.value is None for p in layout.params):                # met only where the driver is absent
        raise SecretNotPassable("libpq cannot read this URL's query (a parameter without `=`)")
    secret_params = [p for p in layout.params if _is_secret_key(p.key)]
    for p in secret_params:
        name = p.key.strip(" ").lower()
        if name not in SECRET_ENVIRONMENT:
            spelled = "" if p.key == name else f" (written {p.key!r})"
            raise SecretNotPassable(
                f"the URL carries {name}{spelled}: libpq has no environment variable for it, so a client tool "
                f"(pg_dump, pg_restore, createdb, dropdb, psql) could take it only on its command line, which "
                f"another user's ps reads; give the tool a key that needs no passphrase, or run it by hand")
    for p in secret_params:
        if p.key != "password":
            # With the driver installed this is a key padded with a raw space
            # (`? password=`): libpq 17 and later read it as `password`, and
            # libpq 16, the client tools', refuses the URL. Another letter
            # case is a parameter libpq does not know, refused above; only
            # where the driver is absent is it met here.
            raise SecretNotPassable(
                f"the URL names {p.key!r}: the client tools' libpq reads only `password`, written exactly so, "
                f"and refuses the URL")
    covered = ([layout.password] if layout.password else []) + [p.value for p in secret_params]
    # libpq stores a query `password=` even empty, and the userinfo's only
    # when it is not empty (`if (*password && …)`).
    chosen = [p.value for p in secret_params] or \
        ([layout.password] if layout.password and layout.password.end > layout.password.start else [])
    password = None
    for span in covered:
        if " " in text[span.start:span.end]:
            raise SecretNotPassable(
                "the URL's password holds a space that is not percent-encoded: libpq 16 (the client tools') keeps "
                "it and libpq 18 (the driver's) strips or refuses it; write it as %20")
    if chosen:
        password = _libpq_decoded(text[chosen[-1].start:chosen[-1].end])
        if password is None:
            raise SecretNotPassable("the URL's password is not valid percent-encoding (an invalid or a %00 escape; "
                                    "libpq refuses it) or is not UTF-8 once decoded")
    # The client tool's URL: libpq's layout with the userinfo password (and
    # its `:`) and every secret parameter (and its `&`) taken out; the rest
    # as written.
    query = layout.query
    head = text if query is None else text[:query.start - 1]
    if layout.password:
        head = head[:layout.password.start - 1] + head[layout.password.end:]
    kept = [text[p.whole.start:p.whole.end] for p in layout.params if not _is_secret_key(p.key)]
    plain = head + ("?" + "&".join(kept) if kept else "")
    if url_secrets(plain):
        raise SecretNotPassable("the URL a client tool would be given still carries a secret; percent-encode the "
                                "user name and password")
    return plain, password


def replace_database(url: str, database: str, *, options: str) -> str:
    """`url` naming `database` instead of its own, with its `options`
    parameter replaced by `options` (already percent-encoded), the rest, its
    secrets among it, as written, in libpq's reading: a restore's scratch
    databases are the server and the role of the URL it was given."""
    split = _split(str(url).strip())
    if split is None or not split[2].complete:
        raise SecretNotPassable("the value is not a postgresql:// URL libpq can read")
    text, _start, layout, _spans = split
    if layout.dbname is not None:
        base = text[:layout.dbname.start] + database
    else:
        cut = layout.query.start - 1 if layout.query is not None else len(text)
        base = text[:cut] + "/" + database
    kept = [text[p.whole.start:p.whole.end] for p in layout.params if p.key.strip(" ").lower() != "options"]
    return base + "?" + "&".join(kept + [f"options={options}"])


__all__ = ["BACKENDS", "EXIT_STORE_CONFIG", "LIBPQ_REFUSALS", "MISREAD", "NOT_A_URL", "NUL_HELD", "POSTGRES",
           "REDACTED", "SECRET_ENVIRONMENT", "SECRET_PARAMETERS", "SQLITE", "STORE_SETTING", "SecretNotPassable",
           "StoreConfigError", "StoreTarget", "UNASKED_URL", "UNREADABLE_URL", "URL_SCHEMES", "URL_SETTING",
           "configured_store_target", "database_url", "redacted_error", "redacted_text", "redacted_url",
           "replace_database", "store_backend_name", "store_config_reason", "url_secrets", "url_unreadable_reason",
           "url_without_secrets"]
