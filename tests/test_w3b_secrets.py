"""W3b close (F6) — a database URL's secrets, in every form libpq reads, are
never printed, recorded or passed on a command line.

docs/CONTRACTS.md's env block says NEXUS_DATABASE_URL's password "is never printed
or recorded (every line that names the store redacts it)". The verifier's
probe found the redaction blanked only the userinfo password
(`postgresql://user:pw@host/db`): libpq's own query form,
`postgresql://user@host/db?password=pw` (and `sslpassword=`), passed through
verbatim to the first lines of start.py and the instance scripts, the lock's
store name, MANIFEST.json and pg_dump's command line; and a percent-encoded
userinfo password reached PGPASSWORD still encoded, so the hosted backup
failed authenticating (exit 5).

A SECRET is every value libpq takes as one: the userinfo password, and the
query parameters libpq marks as password fields (`password`, `sslpassword`,
libpq 18's `oauth_client_secret`) or that carry credential material
(libpq 18's `scram_client_key` and `scram_server_key`), matched on the
percent-decoded key in any letter case, raw and decoded; where libpq and
RFC 3986 read a URL's userinfo differently, both readings.
`backend/store/target.py` is the one place that knows them.

U   THE HELPERS, everywhere (no server). The oracle is libpq itself, the
    driver's (psycopg's) and, where installed, the system's (the client
    tools'), so the check shares none of the redaction's machinery:
    U1  every option libpq marks as a password field is a secret NEXUS
        knows (both libpqs' PQconndefaults);
    U2  every value libpq reads as a secret, over a corpus of every form,
        is absent from redacted_url's output, and to libpq the redacted URL
        is the same URL with each secret reading `***`; a URL libpq refuses
        to parse, or would misread, is shown as nothing of itself (D5);
    U3  libpq's own error texts (measured: some echo a token, some the
        whole URI) carry no secret through the error redaction;
    U4  url_without_password: the URL a client tool is given carries no
        secret to libpq, and the password is the one libpq would use
        (percent-decoded; a query password= wins over the userinfo, the
        last one wins); a secret with no environment variable is refused
        by name, and so are a password libpq cannot decode, one holding a
        raw space (libpq 16 keeps it, 18 strips or refuses it), and a URL
        whose userinfo libpq and RFC 3986 read differently;
    U5  a StoreTarget's repr carries no secret; a store setting that is not
        a postgres URL is refused naming none of it;
    U6  the driver's error at connect carries no secret. Since D5 a URL
        libpq refuses to PARSE never reaches the driver (U8), so the texts
        left to it are the ones libpq gives for a URL it parses: its option
        checks quote a value (`invalid sslmode value: "…"`), and a secret
        is quoted with it only where another parameter holds the same
        text. U6: three such URLs in process, the driver's own text
        measured carrying the secret and connect()'s not. U6a: the URLs
        that reached the driver before D5, libpq's own exception for each
        through `redacted_error` directly, and connect() refusing each
        before the driver is called. U6b: end to end through `start.py
        --serve`, whose startup traceback was the printed place: the one
        test here that waits on the SERVER'S OWN failure, and only for a
        URL libpq parses (an invalid percent-encoding or a raw space is
        refused at config time, exit 2, nothing launched: U8e).
    U7  (F6b) a URL libpq would not read as its writer wrote it is refused
        by name before anything connects, naming none of it: a password
        whose `/` was not percent-encoded (a base64 password: to libpq and
        to RFC 3986 alike such a URL has no password at all, so neither
        reading blanked it and start.py's first line printed it whole), a
        raw `@` in a password, an `@` after the host, a host libpq cannot
        read. The redaction's third reading (the writer's: from the first
        `:` to the last `@`) blanks it on a line (U2's last rows); the
        refusal comes first because libpq's own refusal of such a URL
        echoes a fragment no redaction can know: the host or the port it
        read out of the password (its reading, by its own parse, is
        printed; F6b measured the echo by connecting, `failed to resolve
        host 'ZQy3@127.0.0.1'`, which is a DNS lookup and is no longer
        run). U7b: end to end through `start.py --serve`, exit 2, nothing
        launched.
    U8  (D5, the delta check) a URL libpq REFUSES TO PARSE, or would
        misread, is refused by name before anything connects, and no line
        shows any of it. The delta check found two things false. A secret
        under a key libpq does not know (`?passwd=`, a mistyped
        `password`) was a secret to no reading, so start.py's first line
        and the instance scripts' printed it whole. And the documents'
        "refused by name, none of it shown" held for an unencoded `/` or
        `@` only: an invalid percent-encoding or a raw space reached the
        driver, exit 1 with its traceback, the rest of the URL on the
        first line. `url_unreadable_reason` now ASKS LIBPQ (the driver's
        PQconninfoParse) after its own readings, and names the CLASS of a
        refusal from a closed table of how libpq's texts begin, never the
        text (it quotes a token or the whole URI); `redacted_url` shows a
        URL NEXUS refuses as one fixed marker. The corpus (UNREADABLE):
        unknown keys carrying a secret (passwd, pwd, pass, secret, beside
        a real password, a secret parameter in another letter case), a
        password holding an unencoded `&` or `=`, an invalid
        percent-encoding (in the password, in a query value, in another
        value, %00), a raw space, an upper-case scheme, a value that is
        not UTF-8 once decoded, and the F6b shapes; every part a writer
        chose is a token, so "none of it" is checked on every part.
        U8   per row, in process: the setting refused naming the class and
             none of it; connect() refuses before the driver is called;
             every line that names the store (redacted_url, a target's
             describe and repr, the scripts' store_label and redacted, an
             error text naming it) is the marker; the scripts' helpers and
             the backup's URL split refuse by name;
        U8b  a NUL in the text, where libpq (a C library) ends its
             reading: refused before libpq is asked;
        U8c  the closed table: the system libpq's own texts, replayed
             through the driver's place, are classed as the driver's are;
             the companion plants texts the table names nowhere (an
             invented one quoting the whole URL, a translated one) and the
             URL is still refused, naming none of it;
        U8d  where the driver is not installed libpq cannot be asked: the
             step is skipped, the store is refused for the driver's
             absence, and a line shows none of any postgres URL;
        U8e  end to end through `start.py --serve`, the whole corpus: exit
             2, nothing launched, no traceback, none of the URL printed.
S   EVERY URL-TAKING SCRIPT, WITH NO SERVER (D5; every platform: the
    refusal comes before any connect). S1: the scripts derived as P2
    derives them, each run as a process with every corpus URL: exit 2 by a
    named refusal, never a traceback, none of the URL on any line, nothing
    created. S2: backup.py and restore_verify.py in process with each
    module's `subprocess` wrapped: refused by name, exit 2, nothing
    written, no client tool run (not even a version probe).
G   THE GUARD, derived over the tracked backend, scripts and root files
    (tests/_tracked.py): no f-string, %-format, str.format, print, logging
    call, raise, json.dump(s), write, execute parameter, HTTP response or
    subprocess argv takes a database URL's text, or a secret split from
    it, except through the redaction helpers. The URL's carriers are
    derived, not listed: what a scope passes to `is_url`, reads from a
    `*DATABASE_URL` setting or `database_url()`, a `.url` attribute, and
    (in a scope that calls a URL helper) a URL-named value; through
    assignments, string operations, containers, a class's attributes, a
    module's globals and the same module's calls. Companions plant members
    the guard's source names nowhere; the cross-module edge is pinned
    `xfail(strict)`.
P   ON POSTGRES (skips by name without NEXUS_TEST_DATABASE_URL): a fresh
    writer role (the server's) and a fresh backup role (SELECT and
    CREATEDB), each password holding @ / : % & ? # space + =, in four
    forms: the userinfo (a URL-safe password), the percent-encoded
    userinfo, the query password=, and both at once (a decoy in the
    userinfo, the real one in the query: libpq takes the query's).
    P1  per form: the role connects where NEXUS connects; `start.py
        --serve` serves, and no secret is in its output (its first lines
        among them), /healthz, the lock's holder row or its takeover log
        (a stale holder planted so a takeover is written and logged), a
        StoreTarget's or a lock's repr;
    P2  per form: every instance script that takes a URL (derived by AST)
        runs read-only as the backup role, and no secret is in its output
        (its first line names the store, redacted) or the report it wrote;
    P3  per form: backup.py and restore_verify.py, in this process with
        each module's `subprocess` wrapped: the backup and its restore
        verification pass, 5 of 5; no secret is in their stdout or stderr,
        MANIFEST.json, or the argv of any client tool (pg_dump, pg_restore,
        createdb, psql, dropdb); every tool connected with PGPASSWORD equal
        to the decoded password libpq would use;
    P4  a URL carrying sslpassword= is refused by name by both scripts,
        nothing run (no client tool, not even a version probe) and nothing
        written.
B   backup.sh hands the URL to backup.py in its environment, never on its
    command line (another user's `ps` reads argv); backup.py reads
    NEXUS_BACKUP_DATABASE_URL when --database-url is not given, as
    restore_verify.py already did.

Every object this module makes on the server carries a random suffix and is
dropped; restore_verify's scratch databases are counted and dropped only by
the names this module's runs printed.

WAITS AND CONNECTS (D5). No test here resolves a name or waits on a connect
without a bound: outside P, whose server is the one the job names, a URL is
read by libpq's own parse (PQconninfoParse: nothing is resolved, nothing
connects), the URLs U6 and B2 hand the driver name an address and carry
`connect_timeout`, and U7, U8 and S2 replace the driver's connect. A
child is waited for by the serve helpers' own default (`_Child.wait_until`'s,
150 s, read from the helper and written nowhere here), and a failure prints
the last 4,000 characters of what the child printed. A child that is
expected to END BY ITSELF is READ while it is waited for
(`_read_to_its_exit`). At 00e9c96 U6b waited on the server's own failure
without reading it and failed on CI's windows-latest: 'neither' at 120 s,
and the output it then showed stops in the middle of the traceback, which
is how a child blocked writing to a pipe nobody reads looks (the server's
own failure prints 4,964 bytes here, a refusal at config time some 800).
That cause is read from the CI log; it was not reproduced on Windows.
"""
from __future__ import annotations

import ast
import contextlib
import ctypes
import ctypes.util
import inspect
import io
import json
import os
import re
import secrets
import shutil
import subprocess
from tests import _proc
import sys
import textwrap
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote, quote_plus, urlsplit

import pytest

from tests._tracked import REPO, tracked, tracked_backend
from tests.conftest import POSTGRES_TEST_URL as PG_URL  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "pre_run_data"
NO_PG = "no Postgres named: NEXUS_TEST_DATABASE_URL is unset (the Postgres job sets it; windows and macos run SQLite)"
REDACTION = "***"
#: What a line shows for a postgres URL NEXUS refuses because libpq would not
#: read it as it was written (D5): this, whole, and nothing of the URL. Pinned
#: in full: it is what an operator reads on a first line, and U8 holds
#: `backend.store.target.UNREADABLE_URL` to it.
NOT_SHOWN = "<a postgresql:// URL libpq would not read as it was written: not shown>"
#: The same where the driver is not installed, so libpq cannot be asked (U8d).
NOT_ASKED = ("<a postgresql:// URL, not shown: the psycopg driver is not installed here, so libpq cannot be asked "
             "how it reads it>")
#: A word of each class a refusal names (D5). The reasons' full texts are the
#: product's; these are the words an operator is told to act on.
UNKNOWN, NO_EQUALS, SECOND_EQUALS = "a parameter libpq does not know", "has no `=`", "a second `=`"
PERCENT, SPACE, SCHEME, NOT_UTF8 = "percent-encoding", "a space that is not percent-encoded", "lower case", "not UTF-8"
MISREAD, HOST, NUL = "RFC 3986", "host part is malformed", "NUL"


def _spellings(secret: str) -> list[str]:
    """The ways a secret can be written: raw and every percent-encoding a
    URL builder uses."""
    return sorted({secret, quote(secret, safe=""), quote(secret), quote_plus(secret)})


def _leaks(text: str, tags) -> list[str]:
    """The tags found in `text`. Every secret this module plants holds a
    unique tag of hex digits, which no encoding changes, so a tag in a text
    is some part of a secret in it."""
    return [t for t in tags if t and t in (text or "")]


#: How much of a child's output a failure prints: its end, where it stopped.
TAIL = 4000


def _tail(out: str) -> str:
    """The end of a child's output, for a failure's message."""
    return f"the child's output ({len(out or '')} characters; its last {TAIL}):\n{(out or '')[-TAIL:]}"


def _child_wait() -> float:
    """How long a child is waited for: the serve helpers' own default
    (`_Child.wait_until`'s), read from the helper, so a test here never
    waits a shorter time than the helpers do."""
    from tests.test_w3_serve_stub import _Child
    return float(inspect.signature(_Child.wait_until).parameters["timeout"].default)


def _read_to_its_exit(child) -> tuple:
    """(state, exit code, output) of a `start.py --serve` child that is
    expected to end by itself (a refusal at config time; the server's own
    failure at startup): 'exited' when it ended within the helpers' default
    wait, 'neither' when it did not (it is then stopped). Its output is READ
    while it is waited for, which `wait_until` does not do: a child that
    prints more than its pipe holds otherwise blocks on its write and never
    ends (the module's note, WAITS AND CONNECTS)."""
    try:
        out, _ = child.proc.communicate(timeout=_child_wait())
    except subprocess.TimeoutExpired:
        code = child.stop()
        return "neither", code, child.output()
    return "exited", child.proc.returncode, out or ""


# ---------------------------------------------------------------------------
# U — the helpers, against libpq
# ---------------------------------------------------------------------------

def _psycopg_parse(url: str) -> "dict | None":
    """The driver's libpq reading of `url` (PQconninfoParse), or None when
    it refuses it."""
    from psycopg.conninfo import conninfo_to_dict
    try:
        return conninfo_to_dict(url)
    except Exception:                                   # noqa: BLE001 — libpq refused the text
        return None


class _Opt(ctypes.Structure):
    _fields_ = [("keyword", ctypes.c_char_p), ("envvar", ctypes.c_char_p), ("compiled", ctypes.c_char_p),
                ("val", ctypes.c_char_p), ("label", ctypes.c_char_p), ("dispchar", ctypes.c_char_p),
                ("dispsize", ctypes.c_int)]


def _system_libpq():
    """The system's libpq (the client tools' library), or None where it is
    not installed (it is named by the skip in each caller)."""
    name = ctypes.util.find_library("pq")
    if not name:
        return None
    try:
        lib = ctypes.CDLL(name)
    except OSError:
        return None
    lib.PQconninfoParse.restype = ctypes.POINTER(_Opt)
    lib.PQconninfoParse.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_char_p)]
    lib.PQconndefaults.restype = ctypes.POINTER(_Opt)
    lib.PQlibVersion.restype = ctypes.c_int
    return lib


def _system_parse(lib, url: str) -> "tuple[dict | None, str]":
    """(the system libpq's reading of `url` or None, its error text)."""
    err = ctypes.c_char_p()
    opts = lib.PQconninfoParse(url.encode("utf-8"), ctypes.byref(err))
    if not opts:
        return None, (err.value or b"").decode("utf-8", "replace")
    out, i = {}, 0
    while opts[i].keyword:
        if opts[i].val is not None:
            out[opts[i].keyword.decode()] = opts[i].val.decode("utf-8", "replace")
        i += 1
    return out, ""


def _defaults(lib=None) -> list[tuple[str, str, "str | None"]]:
    """(keyword, dispchar, envvar) of every option a libpq knows."""
    if lib is None:
        from psycopg.pq import Conninfo
        return [(o.keyword.decode(), (o.dispchar or b"").decode(), o.envvar.decode() if o.envvar else None)
                for o in Conninfo.get_defaults()]
    opts, i, out = lib.PQconndefaults(), 0, []
    while opts[i].keyword:
        out.append((opts[i].keyword.decode(), (opts[i].dispchar or b"").decode(),
                    opts[i].envvar.decode() if opts[i].envvar else None))
        i += 1
    return out


#: libpq's secret options, by its own mark ('*', "password field: hide
#: value"), plus the credential material libpq 18 marks only as a debug
#: option ('D'): the SCRAM keys (a client key authenticates as the role
#: without the password). The oracle U2 and U4 read secrets from.
LIBPQ_SCRAM_KEYS = ("scram_client_key", "scram_server_key")


def _libpq_secret_keys() -> set[str]:
    keys = {k for k, disp, _env in _defaults() if disp == "*"}
    lib = _system_libpq()
    if lib is not None:
        keys |= {k for k, disp, _env in _defaults(lib) if disp == "*"}
    return keys | set(LIBPQ_SCRAM_KEYS)


def _libpq_secrets_of(parsed: dict) -> list[str]:
    return [v for k, v in parsed.items() if k in _libpq_secret_keys() and v]


def test_u1_every_option_libpq_marks_as_a_password_field_is_a_secret_nexus_knows():
    from backend.store.target import SECRET_PARAMETERS
    seen = {"driver": sorted(k for k, d, _e in _defaults() if d == "*")}
    lib = _system_libpq()
    if lib is not None:
        seen[f"system libpq {lib.PQlibVersion()}"] = sorted(k for k, d, _e in _defaults(lib) if d == "*")
    print(f"\nU1 libpq's password fields: {seen}; NEXUS's secret parameters: {sorted(SECRET_PARAMETERS)}")
    missing = sorted(set().union(*seen.values()) - set(SECRET_PARAMETERS))
    assert not missing, f"libpq hides these options' values and NEXUS would print them: {missing}"
    assert set(LIBPQ_SCRAM_KEYS) <= set(SECRET_PARAMETERS), "the SCRAM keys are credential material"
    # The one secret a client tool can be given outside its command line is
    # the one libpq reads from the environment (PGPASSWORD); derived, not
    # assumed: every other secret option has no environment variable.
    envs = {k: e for k, _d, e in _defaults() if k in SECRET_PARAMETERS}
    assert envs.get("password") == "PGPASSWORD" and not any(e for k, e in envs.items() if k != "password"), envs


OK = "ok"
#: (url, the secret tokens it carries, whether the redacted URL is to libpq
#: the same URL with each secret reading `***` (False where libpq and RFC
#: 3986 read the userinfo differently: NEXUS refuses such a URL, MISREAD, and
#: since D5 shows none of it), and what url_without_password gives: OK, or
#: the word its refusal names. "RFC 3986" (MISREAD) is NEXUS's own reading and
#: answers first; any other word is the narrower reason given WHERE LIBPQ
#: PARSES THE URL: where the driver's libpq refuses to parse it, the refusal
#: names libpq's class instead, UNKNOWN for every such row here, and none of
#: the URL. U4 derives which from libpq itself, so the two rows only libpq 18
#: knows hold under an older one). Every token is unique to its row.
CORPUS = [
    ("postgresql://u:ZQa1@h:5432/db", ["ZQa1"], True, OK),
    ("postgresql://u:ZQb2%40c%2Fd%3Ae%25f%26g%3Fh%23i%20j@h/db", ["ZQb2"], True, OK),   # percent-encoded userinfo
    ("postgres://u:ZQc3:more@h/db", ["ZQc3:more"], True, OK),                             # ':' after the first
    ("postgresql://u:ZQd4?x@h/db", ["ZQd4?x"], True, OK),            # raw '?': libpq takes it (RFC ends the host)
    ("postgresql://u:ZQe5#x@h/db", ["ZQe5#x"], True, OK),            # raw '#': the same
    ("postgresql://u:ZQf6@ss@h/db", ["ZQf6@ss"], False, "RFC 3986"),  # raw '@': libpq 'ZQf6' and host 'ss@h'
    ("postgresql://u@h/db?password=ZQg7", ["ZQg7"], True, OK),                            # the query form
    ("postgresql://u@h/db?application_name=a&password=ZQh8%23x&sslmode=disable", ["ZQh8"], True, OK),
    ("postgresql://u@h/db?password=ZQi9#x", ["ZQi9#x"], True, OK),    # libpq's query has no fragment
    ("postgresql://u@h/db?pass%77ord=ZQj10", ["ZQj10"], True, OK),    # a percent-encoded key
    ("postgresql://u:ZQk11@h/db?password=ZQl12&password=ZQm13", ["ZQk11", "ZQl12", "ZQm13"], True, OK),
    ("postgresql://u@h/db?sslpassword=ZQn14", ["ZQn14"], True, "sslpassword"),
    ("postgresql://u@h/db?oauth_client_secret=ZQo15", ["ZQo15"], True, "oauth_client_secret"),   # libpq 18
    ("postgresql://u@h/db?scram_client_key=ZQp16&scram_server_key=ZQq17", ["ZQp16", "ZQq17"], True,
     "scram_client_key"),
    # A secret parameter in another letter case is a secret to NEXUS (its value is blanked wherever it is
    # met) and a parameter libpq does not know: libpq refuses the URL, so NEXUS does, showing none of it (D5).
    ("postgresql://u@h/db?PASSWORD=ZQr18&SslPassword=ZQs19", ["ZQr18", "ZQs19"], True, UNKNOWN),
    ("postgresql://u@h/db?PassWord=ZQr20", ["ZQr20"], True, UNKNOWN),
    ("postgresql://u@h1:1,h2:2/db?password=ZQt20", ["ZQt20"], True, OK),                 # a host list
    ("postgresql://u:ZQu21@[::1]:5432/db?options=-c%20search_path%3Dx", ["ZQu21"], True, OK),
    ("postgresql://u@h/db?options=-c%20search_path%3Dx&password=ZQv22", ["ZQv22"], True, OK),
    ("postgresql://h?password=ZQw23@y/db", ["ZQw23"], False, "RFC 3986"),   # libpq: the user 'h?password=ZQw23'
    # F6b: a password whose `/` was not percent-encoded (a base64 password).
    # To libpq AND to RFC 3986 the URL then has no password at all (`u` is
    # the host, the text after `:` a port, after `/` a database name), so
    # neither reading blanked it; its writer meant the userinfo to run to
    # the `@`. The third reading: from the first `:` to the LAST `@`.
    ("postgresql://u:ZQza1/x@h/db", ["ZQza1"], False, "RFC 3986"),
    ("postgresql://u:123/ZQzb2@h/db", ["ZQzb2"], False, "RFC 3986"),              # digits first: a port libpq accepts
    ("postgresql://u:ZQzc3/x?k=ZQzc4@h:5432/db", ["ZQzc3", "ZQzc4"], False, "RFC 3986"),   # a '/' and then a '?'
    ("postgresql://u:ZQzd5@h/db?application_name=a@b", ["ZQzd5"], False, "RFC 3986"),     # an '@' after the host
]


@pytest.mark.parametrize("url,tokens,agree,_u4", CORPUS, ids=[c[1][0] for c in CORPUS])
def test_u2_every_secret_libpq_reads_is_absent_from_redacted_url_and_the_rest_is_as_written(url, tokens, agree, _u4):
    from backend.store.target import redacted_url
    out = redacted_url(url)
    parsed = _psycopg_parse(url)
    libpq_secrets = _libpq_secrets_of(parsed) if parsed else []
    rfc = None
    with contextlib.suppress(ValueError):
        rfc = urlsplit(url).password
    print(f"U2 {url!r} -> {out!r}; libpq's secrets {libpq_secrets}; RFC's password {rfc!r}")
    named = [s for s in tokens + libpq_secrets + ([rfc] if rfc else []) for s in _spellings(s) if s in out]
    assert not named, f"redacted_url left a secret in {out!r}: {named}"
    if parsed is None or not agree:
        # D5: a URL libpq refuses to parse (the oracle's own verdict), or one
        # NEXUS refuses because libpq would read its secrets elsewhere (the
        # rows where the readings differ), is shown as nothing of itself.
        assert out == NOT_SHOWN, f"a URL NEXUS refuses is shown in part: {out!r}"
        return
    assert REDACTION in out
    if parsed and agree:
        again = _psycopg_parse(out)
        assert again is not None, f"the redacted URL is not a URL libpq reads: {out!r}"
        secret_keys = _libpq_secret_keys()
        kept = {k: v for k, v in parsed.items() if k not in secret_keys}
        assert {k: v for k, v in again.items() if k not in secret_keys} == kept, \
            f"to libpq the redacted URL is another URL: {again} against {parsed}"
        assert {k: again.get(k) for k in parsed if k in secret_keys and parsed[k]} == \
            {k: REDACTION for k in parsed if k in secret_keys and parsed[k]}, again


def test_u2b_the_userinfo_cases_already_pinned_and_a_value_that_is_not_a_url():
    from backend.store.target import redacted_url
    assert redacted_url("postgresql://u:pw@h:5433/db_test") == "postgresql://u:***@h:5433/db_test"
    assert redacted_url("postgresql://u@h/db") == "postgresql://u@h/db", "no secret: unchanged"
    assert redacted_url("postgresql://u:p%40ss@[::1]:5432/db?sslmode=require") == \
        "postgresql://u:***@[::1]:5432/db?sslmode=require"
    assert redacted_url("postgresql://u@h/db?password=ZQx1&sslmode=require") == \
        "postgresql://u@h/db?password=***&sslmode=require", "the rest of the URL as it was"
    # libpq's keyword/value form, and text that names no scheme, are not a
    # postgres URL: nothing of them is printed.
    for value in ("host=h password=ZQy1 dbname=d", "u:ZQy2@h/db", "mysql://u:ZQy3@h/db"):
        out = redacted_url(value)
        assert not _leaks(out, ["ZQy1", "ZQy2", "ZQy3"]) and "ZQ" not in out, (value, out)


#: (url, tokens): libpq refuses each, and its error text echoes a token or
#: the whole URI (measured on libpq 16 and 18).
ERROR_CORPUS = [
    ("postgresql://u:ZQe1%zz@h/db", ["ZQe1"]),                    # invalid percent-encoded token: "ZQe1%zz"
    ("postgresql://u@h/db?password=ZQe2%zz", ["ZQe2"]),
    ("postgresql://u@h/db?password=ZQe3%00", ["ZQe3"]),           # forbidden value %00 in percent-encoded value
    ("postgresql://u:ZQe4@[::1/db", ["ZQe4"]),                    # ... matching "]" in IPv6 host address in URI: "<the URI>"
    ("postgresql://u:ZQe5@[::1]x/db", ["ZQe5"]),                  # unexpected character ... in URI: "<the URI>"
    ("POSTGRESQL://u:ZQe6@h/db", ["ZQe6"]),                       # missing "=" after "<the whole text>"
    ("postgresql://u:ZQe7 x@h/db", ["ZQe7"]),                     # libpq 18: unexpected spaces found in "ZQe7 x"
    ("postgresql://u@h/db?sslpassword=ZQe8%zz", ["ZQe8"]),
]


@pytest.mark.parametrize("url,tokens", ERROR_CORPUS, ids=[c[1][0] for c in ERROR_CORPUS])
def test_u3_libpqs_own_error_texts_carry_no_secret_through_the_error_redaction(url, tokens):
    from psycopg.conninfo import conninfo_to_dict
    from scripts._readonly import _redact_text
    texts = []
    try:
        conninfo_to_dict(url)
    except Exception as e:                              # noqa: BLE001 — libpq's refusal is the text under test
        texts.append(("driver", str(e)))
    lib = _system_libpq()
    if lib is not None:
        parsed, err = _system_parse(lib, url)
        if parsed is None:
            texts.append((f"system libpq {lib.PQlibVersion()}", err))
    assert texts, f"libpq accepted {url!r}: not a case for this test"
    echoed = [who for who, t in texts if _leaks(t, tokens)]
    print(f"U3 {url!r}: libpq's texts {texts}; echo a secret: {echoed}")
    for who, text in texts:
        out = _redact_text(text, url)
        assert not _leaks(out, tokens), f"{who}'s error text keeps a secret after the redaction: {out!r}"


@pytest.mark.parametrize("url,tokens,_agree,outcome", CORPUS, ids=[c[1][0] for c in CORPUS])
def test_u4_the_url_a_client_tool_is_given_carries_no_secret_and_the_password_is_libpqs(url, tokens, _agree, outcome):
    from scripts.backup import BackupRefused, url_without_password
    parsed = _psycopg_parse(url)
    if outcome != OK:
        with pytest.raises(BackupRefused) as refused:
            url_without_password(url)
        text = str(refused.value)
        print(f"U4 {url!r}: refused: {text}")
        # D5: NEXUS's own reading answers first (MISREAD, whatever libpq
        # makes of the rest); then a URL the driver's libpq refuses to parse
        # is refused as that, by libpq's class; only a URL libpq parses is
        # looked at for a narrower reason.
        named = outcome if parsed is not None or outcome == MISREAD else UNKNOWN
        assert named in text and not _leaks(text, tokens), text
        envless = [k for k in (parsed or {}) if k in _libpq_secret_keys() and k != "password"]
        if envless:                                     # libpq has no environment variable for these
            assert "environment" in text, text
        return
    assert parsed is not None, f"libpq refuses {url!r}: not a case for the OK branch"
    plain, password = url_without_password(url)
    again = _psycopg_parse(plain)
    print(f"U4 {url!r} -> {plain!r}, password {password!r}")
    assert again is not None, f"the client tool's URL is not one libpq reads: {plain!r}"
    assert not _leaks(plain, tokens), f"a secret on the client tool's command line: {plain!r}"
    assert not [k for k in again if k in _libpq_secret_keys()], again
    assert password == parsed.get("password"), \
        f"the client tool would authenticate with {password!r}; libpq reads {parsed.get('password')!r}"
    assert {k: v for k, v in again.items()} == {k: v for k, v in parsed.items() if k not in _libpq_secret_keys()}
    lib = _system_libpq()
    if lib is not None:                                 # the client tools' own libpq agrees
        system, _err = _system_parse(lib, url)
        assert system is not None and system.get("password") == password, system
        system_plain, _err = _system_parse(lib, plain)
        assert system_plain is not None and "password" not in system_plain, system_plain


def test_u4b_refusals_by_name_naming_no_secret():
    from scripts.backup import BackupRefused, url_without_password
    cases = {
        "postgresql://u@h/db?sslpassword=ZQs1": "sslpassword",
        "postgresql://u@h/db?SSLPASSWORD=ZQs2": UNKNOWN,          # libpq does not know the key in this case (D5)
        "postgresql://u@h/db?oauth_client_secret=ZQs3": "oauth_client_secret",   # libpq 18's; an older one: UNKNOWN
        "postgresql://u@h/db?scram_client_key=ZQs4": "scram_client_key",
        "postgresql://u:ZQs5%zz@h/db": "percent",                 # libpq cannot decode it
        "postgresql://u@h/db?password=ZQs6%00": "percent",
        "postgresql://u:ZQs7 x@h/db": "space",                    # libpq 16 keeps it, libpq 18 refuses it
        "postgresql://u@h/db?password= ZQs8": "space",            # libpq 16 keeps it, libpq 18 strips it
        "postgresql://u:ZQs9@x@h/db": "RFC 3986",                 # libpq reads 'ZQs9' and the host 'x@h'
    }
    for url, named in cases.items():
        if named in ("oauth_client_secret", "scram_client_key") and _psycopg_parse(url) is None:
            named = UNKNOWN                                       # a libpq older than 18 does not know the key
        with pytest.raises(BackupRefused) as refused:
            url_without_password(url)
        text = str(refused.value)
        print(f"U4b {url!r}: {text}")
        assert named in text and not _leaks(text, ["ZQs"]), text


def test_u5_a_store_target_and_a_refused_setting_name_no_secret():
    from backend.store import StoreTarget, store_config_reason
    for url in ("postgresql://u:ZQt1@h/db", "postgresql://u@h/db?password=ZQt2", "postgresql://u@h/db?sslpassword=ZQt3"):
        target = StoreTarget.postgres(url, schema="s")
        for shown in (repr(target), str(target), target.describe(), repr([target]), f"{target}", f"{target!r}"):
            assert not _leaks(shown, ["ZQt1", "ZQt2", "ZQt3"]), shown
        assert target.url == url, "the target still carries the URL it connects with"
    # A value that is not a postgres URL is refused by name; none of it is
    # echoed. The old message quoted its first 12 characters when it named
    # no scheme, which for libpq's keyword/value form, or a URL whose scheme
    # was left off, began with the password.
    for value in ("password=ZQu1longer host=h", "u:ZQu2longer@h/db", "pg:ZQu3longer@h://x"):
        reason = store_config_reason({"NEXUS_STORE": "postgres", "NEXUS_DATABASE_URL": value})
        print(f"U5 {value!r}: {reason}")
        assert reason and "postgresql://" in reason and "ZQu" not in reason, reason
    reason = store_config_reason({"NEXUS_STORE": "postgres", "NEXUS_DATABASE_URL": "mysql://u:ZQu4@h/db"})
    assert "'mysql'" in reason and "ZQu4" not in reason, reason


#: (url, tokens, the option libpq's text names): URLs libpq PARSES, so NEXUS
#: does not refuse them, and then refuses at connect, in its option checks,
#: before any network round trip (measured: 0.00 s), quoting the value it
#: will not take. Since D5 these are the driver's texts that can still carry
#: a secret's text: a URL libpq refuses to parse never reaches the driver
#: (U8), and libpq quotes no password of a URL it parses. So the secret is
#: quoted only where another parameter holds the same text, which is what
#: each row plants. (That other position is the URL's own, and a line that
#: names the store shows it: it is not a secret to libpq.) These are the one
#: place this module hands a URL of its own to the driver, so every row
#: names an address, never a host name, and carries `connect_timeout`: were
#: a libpq to connect before it checks its options, nothing would be
#: resolved and the wait is three seconds.
CONNECT_ERRORS = [
    # invalid sslmode value: "ZQv1"
    ("postgresql://u:ZQv1@127.0.0.1:1/db?sslmode=ZQv1&connect_timeout=3", ["ZQv1"], "sslmode"),
    # invalid integer value "ZQv2x" for connection option "port"
    ("postgresql://u:ZQv2x@127.0.0.1:ZQv2x/db?connect_timeout=3", ["ZQv2x"], "port"),
    # invalid target_session_attrs value: "ZQv3"
    ("postgresql://u@127.0.0.1:1/db?target_session_attrs=ZQv3&password=ZQv3&connect_timeout=3", ["ZQv3"],
     "target_session_attrs"),
]
assert all("@127.0.0.1:" in url and "connect_timeout=3" in url for url, _tokens, _option in CONNECT_ERRORS)


@pytest.mark.parametrize("url,tokens,option", CONNECT_ERRORS, ids=[c[1][0] for c in CONNECT_ERRORS])
def test_u6_the_drivers_error_at_connect_carries_no_secret(url, tokens, option):
    psycopg = pytest.importorskip("psycopg")
    from backend.store import StoreConfigError, StoreTarget, connect, url_unreadable_reason
    assert url_unreadable_reason(url) is None, "not a case: NEXUS refuses this URL before the driver is called"
    # The driver's own refusal, measured: it quotes the secret's text.
    with pytest.raises(psycopg.Error) as raw:
        psycopg.connect(url).close()                    # the URL's own connect_timeout bounds it
    said = str(raw.value)
    assert _leaks(said, tokens) and option in said, f"non-vacuity: libpq's text does not quote the value: {said!r}"
    with pytest.raises(Exception) as raised:
        connect(StoreTarget.postgres(url))
    shown = "".join(traceback.format_exception(raised.value))
    print(f"U6 {url!r}: the driver says {said!r}; connect() raises {type(raised.value).__name__}: {raised.value}")
    assert type(raised.value) is type(raw.value) and not isinstance(raised.value, StoreConfigError), \
        f"connect() did not reach the driver, or changed its error's class: {type(raised.value).__name__}"
    assert option in str(raised.value), f"the driver's refusal is no longer named: {raised.value}"
    assert not _leaks(shown, tokens), f"the connect error carries a secret:\n{shown}"
    assert not _leaks(repr(raised.value), tokens), repr(raised.value)


#: (url, tokens): libpq refuses to PARSE each, and its text echoes a secret
#: (U3's measurement). Until D5 they reached the driver through connect(),
#: whose re-raise blanked the echo; now connect() refuses them by name before
#: the driver is called (U8), and `redacted_error`, kept as the barrier behind
#: that one, is held to libpq's own exception for each directly.
PARSE_ERRORS = [
    ("postgresql://u:ZQv1%zz@127.0.0.1:1/db", ["ZQv1"]),
    ("postgresql://u@127.0.0.1:1/db?password=ZQv2%zz", ["ZQv2"]),
    ("postgresql://u:ZQv3@[::1/db", ["ZQv3"]),
    ("POSTGRESQL://u:ZQv4@127.0.0.1:1/db", ["ZQv4"]),
    ("postgresql://u:ZQv5 x@127.0.0.1:1/db", ["ZQv5"]),          # libpq 17 and later
]


@pytest.mark.parametrize("url,tokens", PARSE_ERRORS, ids=[c[1][0] for c in PARSE_ERRORS])
def test_u6a_libpqs_exception_for_a_url_it_will_not_parse_is_blanked_and_no_longer_reached(url, tokens, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from psycopg import pq
    from psycopg.conninfo import conninfo_to_dict
    from backend.store import StoreConfigError, StoreTarget, connect, redacted_error
    try:
        conninfo_to_dict(url)
    except Exception as e:                              # noqa: BLE001 — libpq's refusal is the exception under test
        raw = e
    else:
        pytest.skip(f"the driver's libpq {pq.version()} parses this URL (a raw space is refused from libpq 17): "
                    f"not a text it gives")
    clean = redacted_error(raw, url)
    print(f"U6a {url!r}: libpq raises {type(raw).__name__}: {str(raw).strip()!r}; echoes a secret: "
          f"{bool(_leaks(str(raw), tokens))}; blanked: {str(clean).strip()!r}")
    assert type(clean) is type(raw), f"the blanked error is another class: {type(clean).__name__}"
    for shown in (str(clean), repr(clean), "".join(traceback.format_exception(clean))):
        assert not _leaks(shown, tokens), f"libpq's exception keeps a secret after the redaction: {shown!r}"
    # D5: connect() no longer hands such a URL to the driver at all.
    calls = []
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
        AssertionError("the driver was called")))
    with pytest.raises(StoreConfigError) as raised:
        connect(StoreTarget.postgres(url))
    assert not calls and not _leaks("".join(traceback.format_exception(raised.value)), tokens), raised.value


def test_u6b_start_py_serves_no_secret_when_the_driver_refuses_the_url(tmp_path):
    """End to end: the console's startup traceback was the printed place.
    The URL is one libpq parses and its option check refuses (U6's first
    row): the settings pass, the server is launched, the store's connect
    fails in the driver, and uvicorn prints the traceback. The driver's
    line shows `***` where libpq quoted the password's text; the token is
    on no line but in the URL's own `sslmode=`, a value libpq does not
    hold secret.

    The child ends by itself, and it is read while it is waited for: its
    traceback is some 5,000 bytes, more than a pipe nobody reads is sure to
    hold (the module's note). The URL names an address and carries
    `connect_timeout`; libpq refuses it before it connects."""
    pytest.importorskip("psycopg")
    from tests.test_w3_serve_stub import _free_port, _serve
    root, port = tmp_path / "root", _free_port()
    (tmp_path / "envroot").mkdir()
    token = "ZQw1"
    url = f"postgresql://f6:{token}@127.0.0.1:1/nexus_f6?sslmode={token}&connect_timeout=3"
    child = _serve(tmp_path, root, port, LLM_BACKEND="stub", NEXUS_STORE="postgres", NEXUS_DATABASE_URL=url)
    state, code, out = _read_to_its_exit(child)
    said = [l for l in out.splitlines() if "invalid sslmode value" in l]
    print(f"U6b ({sys.platform}): {state}, exit {code}, {len(out.encode('utf-8'))} bytes printed; lines naming the "
          f"driver's refusal: {said[:3]}")
    assert state == "exited" and code not in (0, 2), f"{state}, exit {code}; {_tail(out)}"
    assert said, f"non-vacuity: the driver's refusal is not in the server's output; {_tail(out)}"
    assert all(REDACTION in l and token not in l for l in said), f"the driver's refusal carries the password: {said}"
    elsewhere = [l for l in out.splitlines() if token in l.replace(f"sslmode={token}", "")]
    assert not elsewhere, f"the server's output carries the URL's password: {elsewhere}; {_tail(out)}"
    assert out.splitlines()[0].startswith("database: postgres postgresql://f6:***@"), out.splitlines()[0]


#: (url, tokens): URLs libpq would not read as their writer meant. Each is
#: refused by name before anything connects, naming none of it (F6b): the
#: redaction blanks the secret on a line, but libpq's own refusal of such a
#: URL echoes a FRAGMENT of it that no redaction can know (the port it read
#: out of the password, the host it read out of its tail).
MISREAD_URLS = [
    ("postgresql://u:ZQy1/x@127.0.0.1:1/db", ["ZQy1"]),                       # libpq's port is 'ZQy1'
    ("postgresql://u:ZQy2@ZQy3@127.0.0.1:1/db", ["ZQy2", "ZQy3"]),            # libpq's host is 'ZQy3@127.0.0.1'
    # An `@` after the host. To libpq this URL is well formed; NEXUS cannot
    # tell it from a password that was not encoded (the writer's reading
    # runs to the last `@`), so it asks for %40. Nothing after the last `@`
    # can be a password in any reading, and a line may show it.
    ("postgresql://u:ZQy4@127.0.0.1:1/db?application_name=a@b", ["ZQy4"]),
    ("postgresql://u:ZQy6@[::1/db", ["ZQy6"]),                                # a host libpq cannot read
]


@pytest.mark.parametrize("url,tokens", MISREAD_URLS, ids=[c[1][0] for c in MISREAD_URLS])
def test_u7_a_url_libpq_would_not_read_as_written_is_refused_by_name_before_anything_connects(url, tokens,
                                                                                              monkeypatch):
    pytest.importorskip("psycopg")
    import psycopg
    from backend.store import StoreConfigError, StoreTarget, connect, store_config_reason
    # What libpq itself reads of it, printed (a measurement, not an
    # assertion), by its own parse: nothing is resolved and nothing connects.
    # The host or the port it takes out of the password is the fragment its
    # refusal at connect names, which no redaction can know, and why NEXUS's
    # refusal must come first. (F6b measured that echo by connecting,
    # `failed to resolve host 'ZQy3@127.0.0.1'`: a DNS lookup of whatever
    # libpq took for a host, so it is no longer run.)
    reading = _psycopg_parse(url)
    misplaced = {k: v for k, v in (reading or {}).items() if k not in _libpq_secret_keys() and _leaks(v, tokens)}
    print(f"U7 {url!r}: libpq reads {reading if reading is not None else 'nothing: it refuses to parse it'}; "
          f"a part of a secret it reads as something else: {misplaced}")
    # The setting is refused by name, naming none of it, so start.py and
    # MCP's main stop with exit 2 having opened nothing (the console's
    # factory raises the same refusal: U8's note).
    reason = store_config_reason({"NEXUS_STORE": "postgres", "NEXUS_DATABASE_URL": url})
    assert reason and "NEXUS_DATABASE_URL" in reason and not _leaks(reason, tokens), reason
    assert "percent-encode" in reason or "cannot read" in reason, reason
    # connect(), the one place NEXUS connects (the instance scripts' URL
    # argument reaches it without the setting): refused before the driver
    # is called at all.
    calls = []
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
        AssertionError("the driver was called")))
    with pytest.raises(StoreConfigError) as raised:
        connect(StoreTarget.postgres(url))
    shown = "".join(traceback.format_exception(raised.value))
    assert not calls and not _leaks(shown, tokens), shown
    # And a line that names it all the same shows none of the secret: since
    # D5, none of the URL at all (before it, the parts around the writer's
    # reading of the password: the user name, the host, the database).
    from backend.store.target import redacted_url
    assert not _leaks(redacted_url(url), tokens), redacted_url(url)
    assert redacted_url(url) == NOT_SHOWN, f"a URL NEXUS refuses is shown in part: {redacted_url(url)!r}"


def test_u7b_start_py_refuses_a_password_with_an_unencoded_slash_and_prints_none_of_it(tmp_path):
    """End to end: a base64 password (`/` in it) written into the URL as it
    is. The server refuses the setting by name, exit 2, having opened
    nothing; neither the first line nor the refusal shows any of it."""
    from tests.test_w3_serve_stub import _free_port, _serve
    root, port = tmp_path / "root", _free_port()
    (tmp_path / "envroot").mkdir()
    url = "postgresql://f6:ZQx7ab/cdZQx8+ef==@127.0.0.1:1/nexus_f6"
    child = _serve(tmp_path, root, port, LLM_BACKEND="stub", NEXUS_STORE="postgres", NEXUS_DATABASE_URL=url)
    state, code, out = _read_to_its_exit(child)
    print(f"U7b ({sys.platform}): {state}, exit {code}; {[l for l in out.splitlines() if 'store' in l or 'result' in l][:3]}")
    assert state == "exited" and code == 2, f"{state}, exit {code}; {_tail(out)}"
    assert not _leaks(out, ["ZQx7", "ZQx8"]), f"the server's output carries the URL's password; {_tail(out)}"
    assert "percent-encode" in out and "nothing was launched" in out, _tail(out)


# ---------------------------------------------------------------------------
# U8 — W3b close (D5): a URL libpq refuses to parse, or would misread
# ---------------------------------------------------------------------------

#: Where every corpus URL points: nothing listens there, and no row reaches a
#: connect at all once it is refused. It is a part of the URL, so no line may
#: show it either.
HOSTPORT = "127.0.0.1:1"
#: The words every refusal of such a URL carries, whichever place refuses it.
NONE_SHOWN = "as it was written (none of it is shown)"


class _Row(NamedTuple):
    id: str
    url: str
    word: str               # a word of the class the refusal names
    misread: bool = False   # libpq parses it; NEXUS refuses it, because a secret would be read as something else (F6b)
    since: int = 0          # the libpq major that first refuses to parse it; 0: every libpq measured (16 and 18)

    @property
    def tokens(self) -> list:
        """Every part its writer chose (the user name, the password, the
        database, a parameter's value): none may appear on a line."""
        return re.findall(r"ZQ\d\d[a-z]", self.url)


#: URLs libpq refuses to parse, or would read otherwise than their writer
#: wrote them. Every part a writer chose is a token unique to its row.
UNREADABLE = [
    # A secret under a key libpq does not know: a secret to no reading, so
    # nothing blanked it and the first lines printed it whole (D5's finding).
    _Row("passwd", "postgresql://ZQ01u@127.0.0.1:1/ZQ01d?passwd=ZQ01p", UNKNOWN),
    _Row("pwd-beside-a-user", "postgresql://127.0.0.1:1/ZQ02d?user=ZQ02u&pwd=ZQ02p", UNKNOWN),
    _Row("pass", "postgresql://ZQ03u@127.0.0.1:1/ZQ03d?pass=ZQ03p", UNKNOWN),
    _Row("secret", "postgresql://ZQ04u@127.0.0.1:1/ZQ04d?sslmode=disable&secret=ZQ04p", UNKNOWN),
    _Row("token-beside-a-password", "postgresql://ZQ05u@127.0.0.1:1/ZQ05d?password=ZQ05p&token=ZQ05q", UNKNOWN),
    _Row("apikey-beside-a-userinfo-password", "postgresql://ZQ06u:ZQ06p@127.0.0.1:1/ZQ06d?apikey=ZQ06q", UNKNOWN),
    _Row("PASSWORD-in-upper-case", "postgresql://ZQ07u@127.0.0.1:1/ZQ07d?PASSWORD=ZQ07p", UNKNOWN),
    # A query password whose `&` or `=` was not percent-encoded. Both readers
    # end the value at the `&`, so its tail was printed (F6 declared it).
    _Row("ampersand-in-a-password", "postgresql://ZQ08u@127.0.0.1:1/ZQ08d?password=ZQ08p&ZQ08q", NO_EQUALS),
    _Row("equals-in-a-password", "postgresql://ZQ09u@127.0.0.1:1/ZQ09d?password=ZQ09p=ZQ09q", SECOND_EQUALS),
    # An invalid percent-encoding: it reached the driver, exit 1 with its
    # traceback, the rest of the URL on the first line.
    _Row("percent-in-the-password", "postgresql://ZQ10u:ZQ10p%zz@127.0.0.1:1/ZQ10d", PERCENT),
    _Row("a-lone-percent", "postgresql://ZQ11u:ZQ11p100%@127.0.0.1:1/ZQ11d", PERCENT),
    _Row("percent-in-a-query-password", "postgresql://ZQ12u@127.0.0.1:1/ZQ12d?password=ZQ12p%zz", PERCENT),
    _Row("percent-in-another-value", "postgresql://ZQ13u:ZQ13p@127.0.0.1:1/ZQ13d?application_name=ZQ13a%zz", PERCENT),
    _Row("percent-00", "postgresql://ZQ14u:ZQ14p%00@127.0.0.1:1/ZQ14d", PERCENT),
    # A raw space inside a value: libpq 17 and later refuse it (16 keeps it).
    _Row("space-in-the-password", "postgresql://ZQ15u:ZQ15p ZQ15q@127.0.0.1:1/ZQ15d", SPACE, since=17),
    _Row("space-in-a-query-password", "postgresql://ZQ16u@127.0.0.1:1/ZQ16d?password=ZQ16p ZQ16q", SPACE, since=17),
    # NEXUS takes the scheme in any letter case; libpq reads only lower case.
    _Row("upper-case-scheme", "POSTGRESQL://ZQ17u:ZQ17p@127.0.0.1:1/ZQ17d", SCHEME),
    # libpq decodes it to a byte that is not UTF-8, and the driver cannot read libpq's answer.
    _Row("not-utf8-once-decoded", "postgresql://ZQ18u:ZQ18p%ff@127.0.0.1:1/ZQ18d", NOT_UTF8),
    # The F6b shapes: refused before D5 too, with the parts around the
    # writer's reading of the password still on the first line.
    _Row("slash-in-the-password", "postgresql://ZQ19u:ZQ19p/ZQ19q@127.0.0.1:1/ZQ19d", MISREAD, misread=True),
    _Row("at-in-the-password", "postgresql://ZQ20u:ZQ20p@ZQ20q@127.0.0.1:1/ZQ20d", MISREAD, misread=True),
    _Row("at-after-the-host", "postgresql://ZQ21u:ZQ21p@127.0.0.1:1/ZQ21d?application_name=ZQ21a@ZQ21b", MISREAD,
         misread=True),
    _Row("a-host-libpq-cannot-read", "postgresql://ZQ22u:ZQ22p@[::1/ZQ22d", HOST),
]
ROW_IDS = [r.id for r in UNREADABLE]
# Every row carries at least a user or a database and a secret, and no token
# is in two rows or twice in one: a token on a line is that row's part.
assert len(ROW_IDS) == len(set(ROW_IDS)) and all(len(r.tokens) >= 2 for r in UNREADABLE), ROW_IDS
assert len({t for r in UNREADABLE for t in r.tokens}) == sum(len(r.tokens) for r in UNREADABLE), "a token repeats"


def _libpq_text(url: str) -> str:
    """What the driver's libpq says of `url` when it will not parse it
    (psycopg's exception, whose text is libpq's; psycopg's own class where
    it cannot decode libpq's answer), or "" when libpq parses it. The
    oracle: none of NEXUS's reading is in it."""
    from psycopg.conninfo import conninfo_to_dict
    try:
        conninfo_to_dict(url)
    except Exception as e:                              # noqa: BLE001 — libpq's refusal is the measurement
        return f"{type(e).__name__}: {str(e).strip()}"
    return ""


def _not_a_case_here(row: _Row) -> "str | None":
    """None when the driver's libpq refuses `row` here or NEXUS's own reading
    does (a misread row, which libpq parses: asserted). Else the skip's text,
    for a row only a later libpq refuses; a row every libpq measured refuses
    and this one parses is not skipped, it fails."""
    from psycopg import pq
    said = _libpq_text(row.url)
    if row.misread:
        assert not said, f"{row.id} is marked misread, and libpq refuses it outright: {said}"
        return None
    if said:
        return None
    assert row.since and pq.version() // 10000 < row.since, \
        f"the driver's libpq {pq.version()} parses {row.id}: not a URL libpq refuses, so not a row of this corpus"
    return (f"the driver's libpq {pq.version()} reads {row.id} as it was written (libpq refuses it from "
            f"{row.since}): not a URL NEXUS refuses here")


def _rows_here() -> list:
    """The corpus rows that are cases on this driver (the pinned driver's
    libpq is 18 on every platform: all of them), the others named."""
    rows = []
    for row in UNREADABLE:
        why = _not_a_case_here(row)
        if why is None:
            rows.append(row)
        else:
            print(f"    not run: {why}")
    assert len(rows) >= len(UNREADABLE) - sum(1 for r in UNREADABLE if r.since), "non-vacuity: the corpus is run"
    return rows


def _shows(text: str, row: _Row) -> list:
    """The parts of `row`'s URL found in `text`: its tokens, and its host and port."""
    return _leaks(text, row.tokens) + ([HOSTPORT] if HOSTPORT in (text or "") else [])


@pytest.mark.parametrize("row", UNREADABLE, ids=ROW_IDS)
def test_u8_a_url_libpq_refuses_is_refused_by_name_before_anything_connects_and_no_line_shows_any_of_it(row,
                                                                                                       monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from backend.store import StoreConfigError, StoreTarget, configured_store_target, connect, store_config_reason
    from backend.store import target as target_mod
    from scripts._readonly import ReadOnlyRefused, open_read_only, redacted, store_exists, store_label
    from scripts.backup import BackupRefused, url_without_password
    why = _not_a_case_here(row)
    if why:
        pytest.skip(why)
    environ = {"NEXUS_STORE": "postgres", "NEXUS_DATABASE_URL": row.url}
    # (a) The setting is refused by name, naming the class and none of the
    # URL: start.py (U8e) and MCP's main stop with exit 2, having opened
    # nothing. The console's factory raises the same refusal
    # (`configured_store_target`, below); run by uvicorn without start.py it
    # then leaves by uvicorn's traceback and exit 1, the reason by name and
    # none of the URL, as for every refused store setting since part 3
    # (measured at D5 and not changed by it).
    reason = store_config_reason(environ)
    print(f"U8 {row.id}: libpq says {_libpq_text(row.url)[:100]!r}; NEXUS: {reason}")
    assert reason is not None, (f"the setting is accepted: the server is launched on a URL libpq refuses, and "
                                f"its first line reads {target_mod.redacted_url(row.url)!r}")
    assert "NEXUS_DATABASE_URL" in reason and NONE_SHOWN in reason and row.word in reason, reason
    assert not _shows(reason, row), f"the refusal names a part of the URL: {_shows(reason, row)}: {reason}"
    with pytest.raises(StoreConfigError):
        configured_store_target(environ)
    # (b) connect(), which the instance scripts' URL argument reaches without
    # the setting: refused before the driver's connect is called.
    calls = []
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
        AssertionError("the driver was called")))
    with pytest.raises(StoreConfigError) as raised:
        connect(StoreTarget.postgres(row.url))
    shown = "".join(traceback.format_exception(raised.value))
    assert not calls and row.word in str(raised.value) and not _shows(shown, row), shown
    # Every line that names the store shows none of it: one fixed marker.
    target = StoreTarget.postgres(row.url, schema="s")
    lines = {"redacted_url": target_mod.redacted_url(row.url), "describe": target.describe(), "repr": repr(target),
             "store_label": store_label(row.url), "redacted": redacted(row.url),
             "an error text naming it": target_mod.redacted_text(f"cannot open {row.url}: refused", row.url)}
    shown_in = {name: _shows(line, row) for name, line in lines.items() if _shows(line, row)}
    assert not shown_in, f"a line that names the store shows a part of a URL NEXUS refuses: {shown_in}: {lines}"
    assert lines["redacted_url"] == NOT_SHOWN == target_mod.UNREADABLE_URL, lines["redacted_url"]
    assert lines["describe"] == f"postgres {NOT_SHOWN} schema s" and lines["store_label"] == f"postgres {NOT_SHOWN}"
    assert target.url == row.url, "the target still carries the URL it was given"
    # The scripts' helpers, and the backup's split of the URL for a client tool.
    exists, said = store_exists(row.url)
    assert not exists and not calls and row.word in said and not _shows(said, row), said
    with pytest.raises(StoreConfigError):
        open_read_only(row.url, url_ok=True)
    with pytest.raises(ReadOnlyRefused) as no_url:                     # a script that reads SQLite stores only
        open_read_only(row.url)
    assert not _shows(str(no_url.value), row), no_url.value
    with pytest.raises(BackupRefused) as refused:
        url_without_password(row.url)
    assert row.word in str(refused.value) and NONE_SHOWN in str(refused.value) and \
        not _shows(str(refused.value), row), refused.value


def test_u8b_a_nul_in_the_url_is_refused_before_libpq_is_asked(monkeypatch):
    """libpq is a C library: its reading of a text ends at a NUL. Measured
    and printed: of `postgresql://U:P1<NUL>P2@host/db` it reads the host `U`
    and the port `P1`, and its refusal at connect quotes `P1`, half of a
    password that no reading blanks whole. So a NUL is refused by name
    before libpq is asked, whose answer would be about another text. (No
    environment variable or argument can hold one; a caller's string can.)"""
    psycopg = pytest.importorskip("psycopg")
    from backend.store import StoreConfigError, StoreTarget, connect, store_config_reason
    from backend.store import target as target_mod
    row = _Row("nul", "postgresql://ZQ40u:ZQ40p\x00ZQ40q@127.0.0.1:1/ZQ40d", NUL)
    print(f"U8b libpq reads of it only {_psycopg_parse(row.url)}")
    reason = store_config_reason({"NEXUS_STORE": "postgres", "NEXUS_DATABASE_URL": row.url})
    assert reason is not None, "the setting is accepted: libpq would read the text only up to its NUL"
    assert row.word in reason and NONE_SHOWN in reason and not _shows(reason, row), reason
    calls = []
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
        AssertionError("the driver was called")))
    with pytest.raises(StoreConfigError) as raised:
        connect(StoreTarget.postgres(row.url))
    assert not calls and not _shows("".join(traceback.format_exception(raised.value)), row), raised.value
    assert target_mod.redacted_url(row.url) == NOT_SHOWN


def _as_the_driver_would_raise(text: str):
    """A stand-in for the driver's parse (`psycopg.conninfo.conninfo_to_dict`)
    that refuses every text with `text`, in the class psycopg gives libpq's
    refusals."""
    import psycopg

    def parse(_conninfo, **_kwargs):
        raise psycopg.ProgrammingError(text)
    return parse


def test_u8c_the_system_libpqs_own_texts_are_classed_as_the_drivers_are(monkeypatch):
    """The closed table was measured on both libpqs. Where a driver is built
    on the system's libpq (psycopg's pure-Python form), its texts are the
    ones the table meets: each corpus row the system libpq refuses, its own
    text replayed through the driver's place, gets the row's class and shows
    none of the URL."""
    pytest.importorskip("psycopg")
    import psycopg.conninfo
    from backend.store import store_config_reason
    lib = _system_libpq()
    if lib is None:
        pytest.skip("no system libpq here (the client tools' library is not installed): the driver's own texts "
                    "are U8's")
    replayed = []
    for row in UNREADABLE:
        parsed, said = _system_parse(lib, row.url)
        if parsed is not None or row.misread or row.word == HOST:      # NEXUS's own readings answer for the last two
            continue
        monkeypatch.setattr(psycopg.conninfo, "conninfo_to_dict", _as_the_driver_would_raise(said))
        reason = store_config_reason({"NEXUS_STORE": "postgres", "NEXUS_DATABASE_URL": row.url})
        replayed.append((row.id, said.strip(), reason))
        assert reason is not None, f"{row.id}: the system libpq says {said!r}, and the setting is accepted"
        assert row.word in reason and not _shows(reason, row), (row.id, said, reason)
    print(f"\nU8c system libpq {lib.PQlibVersion()}: {len(replayed)} of its refusals replayed:")
    for rid, said, reason in replayed:
        print(f"    {rid}: {said!r} -> {reason.split(NONE_SHOWN + ': ', 1)[-1][:70]!r}")
    assert len(replayed) >= 14, f"non-vacuity: the system libpq refuses most of the corpus: {len(replayed)}"


#: Refusal texts the table names nowhere (OQ-16 rule 1): an invented one
#: quoting the whole URL as libpq's IPv6 texts do, a translated one (the
#: system libpq is built with NLS; the driver's wheel is not) quoting a token,
#: and one that begins with nothing libpq says. `{url}` and `{token}` are the
#: row's.
UNNAMED_TEXTS = ('could not make sense of the connection string "{url}"\n',
                 'ungültiger URI-Abfrageparameter: »{token}«\n',
                 '{token}')


@pytest.mark.parametrize("text", UNNAMED_TEXTS, ids=["quotes-the-url", "translated", "only-a-token"])
def test_u8c_companion_a_refusal_the_table_does_not_name_still_refuses_and_shows_nothing(text, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    import psycopg.conninfo
    from backend.store import StoreConfigError, StoreTarget, connect, store_config_reason
    from backend.store import target as target_mod
    # A URL every one of NEXUS's own readings passes: only libpq's verdict
    # (here the planted one) refuses it.
    row = _Row("planted", "postgresql://ZQ50u:ZQ50p@127.0.0.1:1/ZQ50d?application_name=ZQ50a",
               "libpq refuses to parse it, for a reason not named here")
    assert store_config_reason({"NEXUS_STORE": "postgres", "NEXUS_DATABASE_URL": row.url}) is None, \
        "non-vacuity: without the plant the URL is one NEXUS accepts"
    said = text.format(url=row.url, token=row.tokens[1])
    monkeypatch.setattr(psycopg.conninfo, "conninfo_to_dict", _as_the_driver_would_raise(said))
    reason = store_config_reason({"NEXUS_STORE": "postgres", "NEXUS_DATABASE_URL": row.url})
    print(f"U8c companion: libpq is made to say {said!r}; NEXUS: {reason}")
    assert reason is not None, "a refusal whose text the table does not name is not a refusal to NEXUS"
    assert row.word in reason and not _shows(reason, row), reason
    assert target_mod.redacted_url(row.url) == NOT_SHOWN, target_mod.redacted_url(row.url)
    calls = []
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
        AssertionError("the driver was called")))
    with pytest.raises(StoreConfigError) as raised:
        connect(StoreTarget.postgres(row.url))
    assert not calls and not _shows("".join(traceback.format_exception(raised.value)), row), raised.value
    # The plant is a member the table's source names nowhere (it would
    # otherwise prove a string comparison).
    assert not any(said.lstrip().startswith(begins) for begins, _reason in target_mod.LIBPQ_REFUSALS), said


def test_u8d_where_the_driver_is_absent_libpq_is_not_asked_and_a_line_shows_none_of_the_url(monkeypatch):
    """The desktop bundle leaves the driver out, and a host may lack it.
    There libpq cannot be asked: the step is skipped, so the setting passes
    this check and the store is refused at connect for the driver's absence.
    A line that names the store before that refusal (start.py's first line
    comes before its package check) shows none of any postgres URL, since
    nothing there can say whether libpq would read it as it was written."""
    import backend.store as store
    from backend.store import BackendUnavailable, StoreTarget, store_config_reason
    from backend.store import target as target_mod
    from scripts._readonly import store_label
    row = _Row("driverless", "postgresql://ZQ60u@127.0.0.1:1/ZQ60d?passwd=ZQ60p", UNKNOWN)
    monkeypatch.setitem(sys.modules, "psycopg", None)                  # `import psycopg` now raises ImportError
    monkeypatch.setitem(sys.modules, "psycopg.conninfo", None)
    monkeypatch.setattr(store, "POSTGRES_DRIVER_AVAILABLE", False)
    target = StoreTarget.postgres(row.url)
    lines = {"redacted_url": target_mod.redacted_url(row.url), "describe": target.describe(), "repr": repr(target),
             "store_label": store_label(row.url)}
    print(f"U8d without the driver: {lines['describe']!r}")
    shown_in = {name: _shows(line, row) for name, line in lines.items() if _shows(line, row)}
    assert not shown_in, f"without the driver a line shows a part of the URL: {shown_in}: {lines}"
    assert lines["redacted_url"] == NOT_ASKED == target_mod.UNASKED_URL
    # The step is skipped, not answered: nothing here refuses the URL as unreadable ...
    assert target_mod.url_unreadable_reason(row.url) is None
    assert store_config_reason({"NEXUS_STORE": "postgres", "NEXUS_DATABASE_URL": row.url}) is None
    # ... the store is refused for the driver's absence, by name ...
    with pytest.raises(BackendUnavailable, match="psycopg driver"):
        store.connect(target)
    # ... and NEXUS's own readings, which need no driver, still refuse what they see.
    misread = "postgresql://ZQ61u:ZQ61p/ZQ61q@127.0.0.1:1/ZQ61d"
    assert MISREAD in (target_mod.url_unreadable_reason(misread) or "")
    assert target_mod.redacted_url(misread) == NOT_SHOWN


def _serve_to_its_exit(tmp_path: Path, row: _Row) -> tuple:
    """`start.py --serve` on `row`'s URL, read to its end within the helpers'
    default wait: (state, exit code, output, whether the data root was
    made)."""
    from tests.test_w3_serve_stub import _free_port, _serve
    root = tmp_path / f"serve-{row.id}" / "root"
    child = _serve(tmp_path, root, _free_port(), LLM_BACKEND="stub", NEXUS_STORE="postgres",
                   NEXUS_DATABASE_URL=row.url)
    state, code, out = _read_to_its_exit(child)
    return state, code, out, root.exists()


def test_u8e_start_py_refuses_every_unreadable_url_by_name_and_prints_none_of_it(tmp_path):
    """End to end, the whole corpus, four at a time: start.py refuses each at
    config time, exit 2, nothing launched, no traceback, the class named,
    and no part of the URL on any line of the output. U7b's shape, for every
    row: an invalid percent-encoding and a raw space among them, which
    reached the server before D5, so no test waits on the server's own
    failure for them."""
    pytest.importorskip("psycopg")
    rows = _rows_here()
    (tmp_path / "envroot").mkdir()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda row: _serve_to_its_exit(tmp_path, row), rows))
    problems, first_tail = {}, ""
    print(f"\nU8e ({sys.platform}): {len(rows)} URLs through start.py --serve:")
    for row, (state, code, out, root_made) in zip(rows, results):
        store = next((l.strip() for l in out.splitlines() if l.startswith("  store")), "")
        print(f"    {row.id}: {state}, exit {code}; {store[:150]}")
        wrong = []
        if _shows(out, row):
            wrong.append(f"its output shows {_shows(out, row)} of the URL: "
                         f"{[l for l in out.splitlines() if _shows(l, row)][:2]}")
        if state != "exited" or code != 2:
            wrong.append(f"{state}, exit {code} (the refusal is exit 2)")
        if "nothing was launched" not in out or "Traceback" in out:
            wrong.append(f"not refused before the launch: last line {out.strip().splitlines()[-1][:160] if out.strip() else ''!r}")
        if row.word not in out or NONE_SHOWN not in out:
            wrong.append(f"the refusal does not name {row.word!r}")
        if root_made:
            wrong.append("the data root was made")
        if wrong:
            problems[row.id] = wrong
            first_tail = first_tail or f"\n{row.id}: {_tail(out)}"
    assert not problems, "start.py --serve on a URL libpq refuses or would misread: " + \
        "; ".join(f"{rid}: {' / '.join(w)}" for rid, w in problems.items()) + first_tail


# ---------------------------------------------------------------------------
# G — the guard
# ---------------------------------------------------------------------------

#: The helpers that name the store by URL. A call to one marks its scope as
#: one that handles a database URL (its URL-named values are then the
#: store's), and the value given to `is_url` is a URL in that scope.
URL_HELPERS = frozenset({"database_url", "is_url", "redacted", "redacted_url", "redacted_text", "store_label",
                         "store_target", "store_exists", "url_without_password", "url_secrets",
                         "open_read_only_postgres", "configured_store_target", "pg_url"})
#: A value inside one of these calls reaches its sink redacted (or as a
#: value that is not the URL's text: a truth value, a length).
SANITIZERS = frozenset({"redacted_url", "redacted", "redacted_text", "store_label", "is_url", "read_only_how",
                        "bool", "len", "isinstance", "type", "id", "hash"})
#: Calls that return a secret, whole (None) or as one element of a tuple.
SECRET_RESULTS = {"url_secrets": None, "url_without_password": 1}
STRING_METHODS = frozenset({"strip", "lstrip", "rstrip", "lower", "upper", "casefold", "split", "rsplit", "partition",
                            "rpartition", "replace", "format", "join", "encode", "removeprefix", "removesuffix",
                            "title", "splitlines", "ljust", "rjust", "center", "zfill", "expandtabs"})
LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log"})
SPAWN = frozenset({"run", "Popen", "call", "check_call", "check_output", "execv", "execve", "execvp", "execvpe",
                   "spawnv", "spawnve", "spawnl", "system", "popen"})
RESPONSES = frozenset({"JSONResponse", "PlainTextResponse", "HTMLResponse", "Response", "StreamingResponse"})
ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete", "api_route"})
URL_NAMED = re.compile(r"(?i)url")

#: Sites the guard names that carry a URL which is not the store's, each
#: answered for. Keyed by (file, scope, carrier), so a moved line does not
#: rot the table; an entry whose site is gone fails G1.
ANSWERED_FOR = {
    ("backend/loop/reference_gateway.py", "legacy_block", "r.url"):
        "a web search result's own URL (SearXNG's), shown as the result's link: not the store's (W5 moved the "
        "line here from the loop, unchanged)",
    ("backend/providers/api_common.py", "OpenAICompatEmbedder.embed", "self.settings.url"):
        "the embedder's endpoint (NEXUS_EMBED_URL): its key travels in a header, never in the URL (W3 part 2)",
    ("scripts/replay_check.py", "render", "provider_url"):
        "the model server the replay re-sends to (OLLAMA_URL), named in the report beside the store's "
        "store_label: not the store's URL",
}


def _callee(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")


def _key(node) -> str:
    return ast.unparse(node)


def _scope_nodes(scope) -> list:
    """The scope's own nodes, in source order, not those of a nested def or class."""
    out, stack = [], list(ast.iter_child_nodes(scope))
    while stack:
        n = stack.pop()
        out.append(n)
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(n))
    return sorted((n for n in out if hasattr(n, "lineno")), key=lambda n: (n.lineno, n.col_offset))


def _env_url_read(n) -> bool:
    """`os.environ.get("…DATABASE_URL")`, `environ["…DATABASE_URL"]`, `environ.get(URL_SETTING)`."""
    if isinstance(n, ast.Call) and _callee(n) in ("get", "getenv") and n.args:
        arg = n.args[0]
    elif isinstance(n, ast.Subscript):
        arg = n.slice
    else:
        return False
    return (isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "DATABASE_URL" in arg.value) or \
        (isinstance(arg, ast.Name) and arg.id == "URL_SETTING")


class _Scope:
    def __init__(self, node, qualname: str, cls: "str | None"):
        self.node, self.qualname, self.cls = node, qualname, cls
        self.nodes = _scope_nodes(node)
        self.handles_urls = any(
            (isinstance(n, ast.Call) and (_callee(n) in URL_HELPERS
                                          or (_callee(n) == "postgres" and isinstance(n.func, ast.Attribute))))
            or (isinstance(n, ast.Name) and n.id == "URL_SETTING")
            or (isinstance(n, ast.Attribute) and n.attr == "url") or _env_url_read(n)
            for n in self.nodes)
        self.seeds: set[str] = set()
        self.secrets: set[str] = set()
        self.params = ([a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else [])


class _Guard:
    """One module's analysis: every sink whose operand carries a database
    URL's text, or a secret split from it, outside the redaction helpers."""

    def __init__(self, source: str, rel: str):
        self.rel, self.tree = rel, ast.parse(source)
        self.scopes = [_Scope(self.tree, "<module>", None)]
        self.functions: dict[str, _Scope] = {}             # module-level functions and Class.method
        self._collect(self.tree, "", None)
        self.class_seeds: dict[str, set[str]] = {}
        self.redacted_sinks: list[tuple] = []

    def _collect(self, node, prefix: str, cls: "str | None"):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                self._collect(child, f"{prefix}{child.name}.", child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                scope = _Scope(child, qual, cls)
                self.scopes.append(scope)
                self.functions.setdefault(qual, scope)
                self._collect(child, f"{qual}.<locals>.", None)
            else:
                self._collect(child, prefix, cls)

    # -- what carries the text ------------------------------------------------
    @staticmethod
    def _flow(expr, leaf, source) -> list[str]:
        """The sub-expressions through which a text reaches `expr`'s value:
        names and attributes `leaf` accepts, and calls or subscripts
        `source` accepts, followed through string operations, f-strings,
        concatenation, containers and conditionals, never into another
        call's value (a call's result is not its argument's text) nor into
        a redaction helper. In `X if is_url(v) else Y`, `v` in the branch
        where it is not a URL is not a URL."""
        out: list[str] = []

        def visit(n, safe):
            if isinstance(n, (ast.Call, ast.Subscript)):
                hit = source(n)
                if hit is not None:
                    if hit:
                        out.append(_key(n))
                    return
            if isinstance(n, ast.Call):
                c = _callee(n)
                if c in SANITIZERS:
                    return
                if c == "str" and n.args:
                    visit(n.args[0], safe)
                elif c in STRING_METHODS and isinstance(n.func, ast.Attribute):
                    visit(n.func.value, safe)
                    for a in n.args:
                        visit(a, safe)
                return                                  # another call's value is not the text
            if isinstance(n, ast.IfExp):
                test, neg = n.test, False
                if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
                    test, neg = test.operand, True
                if isinstance(test, ast.Call) and _callee(test) == "is_url" and test.args:
                    k = _key(test.args[0])
                    visit(n.body, safe | ({k} if neg else set()))
                    visit(n.orelse, safe | (set() if neg else {k}))
                    return
                visit(n.body, safe)
                visit(n.orelse, safe)
                return
            if isinstance(n, ast.Subscript):
                visit(n.value, safe)
                return
            if isinstance(n, (ast.Name, ast.Attribute)):
                k = _key(n)
                if k not in safe and leaf(n, k):
                    out.append(k)
                return
            if isinstance(n, ast.JoinedStr):
                for v in n.values:
                    if isinstance(v, ast.FormattedValue):
                        visit(v.value, safe)
                return
            if isinstance(n, (ast.BinOp, ast.BoolOp, ast.Dict, ast.List, ast.Tuple, ast.Set, ast.Starred,
                              ast.FormattedValue, ast.keyword)):
                for c in ast.iter_child_nodes(n):
                    visit(c, safe)
        visit(expr, frozenset())
        return out

    def carriers(self, expr, scope: _Scope) -> list[str]:
        """Where a database URL's text reaches `expr`."""
        seeds = scope.seeds | self.class_seeds.get(scope.cls or "", set()) | self.scopes[0].seeds

        def leaf(n, k):
            ident = n.id if isinstance(n, ast.Name) else n.attr
            return k in seeds or (isinstance(n, ast.Attribute) and n.attr == "url") or \
                (scope.handles_urls and bool(URL_NAMED.search(ident)) and not ident.isupper())

        def source(n):
            if isinstance(n, ast.Call) and _callee(n) == "database_url":
                return True
            return True if _env_url_read(n) else None
        return self._flow(expr, leaf, source)

    def secrets_in(self, expr, scope: _Scope) -> list[str]:
        """Where a secret split from a URL reaches `expr`: the password a
        `url_without_password` gives (its element 1, or the whole tuple),
        what `url_secrets` gives, a `.password` attribute (urlsplit's), and
        what was assigned from them."""
        def leaf(n, k):
            return k in scope.secrets or (isinstance(n, ast.Attribute) and n.attr == "password")

        def source(n):
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Call) and \
                    _callee(n.value) in SECRET_RESULTS and isinstance(n.slice, ast.Constant):
                index = SECRET_RESULTS[_callee(n.value)]
                return index is None or n.slice.value == index
            if isinstance(n, ast.Call) and _callee(n) in SECRET_RESULTS:
                return True
            return None
        return self._flow(expr, leaf, source)

    # -- propagation ---------------------------------------------------------
    @staticmethod
    def _targets(t):
        if isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                yield from _Guard._targets(e)
        elif isinstance(t, ast.Subscript):
            yield _key(t.value)
        elif isinstance(t, ast.Starred):
            yield _key(t.value)
        elif isinstance(t, (ast.Name, ast.Attribute)):
            yield _key(t)

    def _propagate(self) -> bool:
        changed = False
        for scope in self.scopes:
            for n in scope.nodes:
                if isinstance(n, ast.Call) and _callee(n) == "is_url" and n.args:
                    changed |= self._add(scope.seeds, [_key(n.args[0])])
                if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and n.value is not None:
                    targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                    value = n.value
                    if isinstance(value, ast.Call) and _callee(value) in SECRET_RESULTS:
                        index = SECRET_RESULTS[_callee(value)]
                        for t in targets:
                            if index is None or not isinstance(t, ast.Tuple):
                                changed |= self._add(scope.secrets, self._targets(t))
                            elif len(t.elts) > index:
                                changed |= self._add(scope.secrets, self._targets(t.elts[index]))
                        continue
                    if self.carriers(value, scope):
                        for t in targets:
                            keys = list(self._targets(t))
                            changed |= self._add(scope.seeds, keys)
                            if scope.cls:
                                changed |= self._add(self.class_seeds.setdefault(scope.cls, set()),
                                                     [k for k in keys if k.startswith("self.")])
                    if self.secrets_in(value, scope):
                        for t in targets:
                            changed |= self._add(scope.secrets, self._targets(t))
                if isinstance(n, ast.Call):
                    changed |= self._seed_callee(n, scope)
        return changed

    @staticmethod
    def _add(into: set, keys) -> bool:
        keys = set(keys) - into
        into |= keys
        return bool(keys)

    def _seed_callee(self, call: ast.Call, scope: _Scope) -> bool:
        """A carrier passed to a function of the same module seeds that
        function's parameter."""
        f = call.func
        target = None
        if isinstance(f, ast.Name):
            target = self.functions.get(f.id)
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self" and scope.cls:
            target = self.functions.get(f"{scope.cls}.{f.attr}")
        if target is None or not target.params:
            return False
        params = target.params[1:] if (target.cls and target.params and target.params[0] in ("self", "cls")) \
            else target.params
        changed = False
        for i, arg in enumerate(call.args):
            if i < len(params) and self.carriers(arg, scope):
                changed |= self._add(target.seeds, [params[i]])
        for kw in call.keywords:
            if kw.arg in params and self.carriers(kw.value, scope):
                changed |= self._add(target.seeds, [kw.arg])
        return changed

    # -- the sinks -----------------------------------------------------------
    def _sinks(self, scope: _Scope):
        route = isinstance(scope.node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ROUTE_METHODS
            for d in scope.node.decorator_list)
        for n in scope.nodes:
            kind, ops = None, []
            if isinstance(n, ast.JoinedStr):
                kind, ops = "f-string", [v.value for v in n.values if isinstance(v, ast.FormattedValue)]
            elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mod) and isinstance(n.left, ast.Constant) \
                    and isinstance(n.left.value, str):
                kind, ops = "%-format", [n.right]
            elif isinstance(n, ast.Call):
                c = _callee(n)
                attr = isinstance(n.func, ast.Attribute)
                if c == "print":
                    kind = "print"
                elif c in LOG_METHODS and attr:
                    kind = "log"
                elif c == "format" and attr:
                    kind = ".format"
                elif c in ("dump", "dumps") and attr:
                    kind = "json"
                elif c in ("write", "write_text", "writelines"):
                    kind = "write"
                elif c in SPAWN:
                    kind = "spawn"
                elif c in RESPONSES:
                    kind = "response"
                elif c in ("execute", "executemany") and attr and len(n.args) > 1:
                    kind, ops = "execute", list(n.args[1:])
                if kind and not ops:
                    ops = list(n.args) + [k.value for k in n.keywords if k.arg != "env"]
            elif isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call):
                kind, ops = "raise", list(n.exc.args) + [k.value for k in n.exc.keywords]
            elif route and isinstance(n, ast.Return) and n.value is not None:
                kind, ops = "response", [n.value]
            if kind:
                yield n, kind, ops

    def violations(self) -> list[tuple[str, int, str, str, str]]:
        for _ in range(8):
            if not self._propagate():
                break
        out = []
        for scope in self.scopes:
            for n, kind, ops in self._sinks(scope):
                for op in ops:
                    for carrier in self.carriers(op, scope) + self.secrets_in(op, scope):
                        out.append((self.rel, n.lineno, scope.qualname, kind, carrier))
                    for call in ast.walk(op):
                        if isinstance(call, ast.Call) and _callee(call) in SANITIZERS - {
                                "bool", "len", "isinstance", "type", "id", "hash", "is_url", "read_only_how"}:
                            self.redacted_sinks.append((self.rel, n.lineno, scope.qualname, kind, _callee(call)))
        return sorted(set(out))


def _population() -> list[Path]:
    """The tracked backend, every tracked script (helpers included: they
    print and record too) and the tracked root files."""
    root = [p for p in tracked("*.py") if p.parent == REPO]
    return sorted(set(tracked_backend()) | set(tracked("scripts/*.py")) | set(root))


def _guard_tree() -> tuple[list[tuple], list[tuple], int]:
    found, redacted = [], []
    files = _population()
    for p in files:
        g = _Guard(p.read_text(encoding="utf-8"), p.relative_to(REPO).as_posix())
        found += g.violations()
        redacted += g.redacted_sinks
    return found, sorted(set(redacted)), len(files)


def test_g1_no_database_url_reaches_a_sink_but_through_the_redaction_helpers():
    found, redacted, n = _guard_tree()
    answered = {(f, q, c) for (f, _l, q, _k, c) in found if (f, q, c) in ANSWERED_FOR}
    violations = [v for v in found if (v[0], v[2], v[4]) not in ANSWERED_FOR]
    print(f"\nG1: {n} files; {len(redacted)} sinks take a database URL through a redaction helper "
          f"(the enumeration):")
    for r in redacted:
        print(f"    {r[0]}:{r[1]} {r[2]} {r[3]} via {r[4]}")
    print(f"G1: answered for: {sorted(answered)}; unanswered: {violations}")
    assert n > 150 and redacted, "non-vacuity: the population and the redacted sinks are found"
    assert not violations, "a database URL (or a secret split from it) reaches a sink unredacted: " + \
        "; ".join(f"{f}:{l} {q} {k} carries {c}" for f, l, q, k, c in violations)
    stale = sorted(set(ANSWERED_FOR) - answered)
    assert not stale, f"answered-for sites that no longer exist: {stale}"


PLANTED = textwrap.dedent('''
    import json, logging, os, subprocess
    from pathlib import Path
    from backend.store.target import database_url, redacted_url
    from scripts._readonly import is_url, redacted
    from scripts.backup import url_without_password
    log = logging.getLogger("planted")
    STORE_DSN = database_url()                                   # a module global

    def from_the_environment():
        dsn = os.environ.get("NEXUS_BACKUP_DATABASE_URL")
        log.warning("backup store %s", dsn)                       # PLANT an env read, logged

    def named_nothing_like_a_url(args):
        if is_url(args.store):
            print("store:", args.store)                          # PLANT what the scope gives is_url, printed

    def secret_on_argv(where):
        plain, pw = url_without_password(where)
        subprocess.run(["pg_dump", "--password", pw, plain])      # PLANT the split secret on a command line

    def recorded(target):
        rec = {"target": str(target.url).strip()}
        Path("m.json").write_text(json.dumps(rec), encoding="utf-8")   # PLANT a .url through str(), strip(), a dict

    def raised():
        conninfo = database_url()
        raise RuntimeError("cannot open " + conninfo)             # PLANT concatenated into an exception

    class Holder:
        def __init__(self, target):
            self.where = target.url
        def show(self):
            return f"holding {self.where}"                        # PLANT a class attribute, another method

    def shown(x):
        print(x)                                                  # PLANT a parameter seeded at its call site

    def caller():
        shown(database_url())

    def from_a_global():
        log.info("global %s", STORE_DSN)                          # PLANT a module global

    def stored(db, cfg_target):
        db.execute("INSERT INTO notes (body) VALUES (?)", (cfg_target.url,))   # PLANT recorded in the store

    def tuple_whole(u):
        print(url_without_password(u))                            # PLANT the tuple, password and all

    @router.get("/where")
    def where_route(services):
        return {"store": services.target.url}                     # PLANT an HTTP response
''')

PLANTED_LINES = {i for i, line in enumerate(PLANTED.splitlines(), 1) if "# PLANT" in line}

SANITIZED = textwrap.dedent('''
    import json, logging, os, subprocess
    from pathlib import Path
    from backend.store.target import database_url, redacted_url
    from scripts._readonly import is_url, redacted
    from scripts.backup import url_without_password
    log = logging.getLogger("planted")

    def from_the_environment():
        dsn = os.environ.get("NEXUS_BACKUP_DATABASE_URL")
        log.warning("backup store %s", redacted_url(dsn))

    def named_nothing_like_a_url(args):
        print("store:", redacted(args.store) if is_url(args.store) else args.store)

    def secret_in_the_environment(where):
        plain, pw = url_without_password(where)
        env = dict(os.environ)
        env["PGPASSWORD"] = pw
        subprocess.run(["pg_dump", f"--dbname={plain}"], env=env)

    def recorded(target):
        Path("m.json").write_text(json.dumps({"target": redacted_url(target.url)}), encoding="utf-8")
''')


def test_g2_companion_every_planted_member_is_named_and_its_redacted_twin_is_not():
    """OQ-16 rules 1 and 3: each plant is a member of the class the guard's
    source names nowhere (no carrier in it is a name the guard lists), and
    the consequence asserted is the guard's failure naming its line."""
    planted = _Guard(PLANTED, "backend/planted.py").violations()
    named = {line for (_f, line, _q, _k, _c) in planted}
    print(f"\nG2 planted: {planted}")
    assert len(PLANTED_LINES) == 11, "non-vacuity: the plants are marked"
    assert named == PLANTED_LINES, (f"the guard missed {sorted(PLANTED_LINES - named)} and named "
                                    f"{sorted(named - PLANTED_LINES)}")
    module = Path(__file__).read_text(encoding="utf-8")
    guard_source = module.split("# G — the guard", 1)[1].split("PLANTED = ", 1)[0]
    for carrier in ("dsn", "args.store", "pw", "rec", "conninfo", "self.where", "STORE_DSN", "cfg_target",
                    "services.target.url"):
        assert not re.search(rf"[\"']{re.escape(carrier)}[\"']", guard_source), \
            f"the guard's source names {carrier}: that plant would prove a string comparison"
    clean = _Guard(SANITIZED, "backend/sanitized.py").violations()
    assert clean == [], f"the redacted twins are named: {clean}"


@pytest.mark.xfail(strict=True, reason="the declared edge: the guard follows a URL through the same module's "
                                       "calls, not into another module's function (each module is analysed "
                                       "alone); the consequence tests (P, U6b) cover the shipped paths")
def test_g3_the_edge_the_guard_cannot_see_a_url_handed_to_another_modules_printer():
    caller = textwrap.dedent('''
        from backend.store.target import database_url
        from backend.helpers import show
        def main():
            show(database_url())
    ''')
    callee = textwrap.dedent('''
        def show(x):
            print(x)
    ''')
    found = _Guard(caller, "backend/caller.py").violations() + _Guard(callee, "backend/helpers.py").violations()
    assert found, "the cross-module flow is named"


# ---------------------------------------------------------------------------
# P — on Postgres
# ---------------------------------------------------------------------------

FORMS = ("userinfo", "encoded_userinfo", "query", "both")
_SCRATCH_NAMED: set[str] = set()
SCRATCH_LINE = "scratch databases: "


def _special(tag: str) -> str:
    """A password holding every character a URL must encode, around a tag."""
    return f"Nx@{tag}/q:r%s&t?u#v w+y=z"


def _form_password(form: str, tag: str) -> str:
    return f"Nx{tag}" if form == "userinfo" else _special(tag)


def _form_url(form: str, role: str, password: str, *, decoy: str, schema: "str | None" = None,
              database: "str | None" = None) -> str:
    """The test server's URL as `role` in `form`, the session's search_path
    set to `schema` through libpq's options."""
    parts = urlsplit(PG_URL)
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    hostport = host + (f":{parts.port}" if parts.port else "")
    query = [q for q in parts.query.split("&") if q]
    if form == "userinfo":
        netloc = f"{role}:{password}@{hostport}"
    elif form == "encoded_userinfo":
        netloc = f"{role}:{quote(password, safe='')}@{hostport}"
    elif form == "query":
        netloc = f"{role}@{hostport}"
        query += ["application_name=nexus_f6", f"password={quote(password, safe='')}"]
    elif form == "both":
        netloc = f"{role}:{quote(decoy, safe='')}@{hostport}"
        query += ["application_name=nexus_f6", f"password={quote(password, safe='')}"]
    else:
        raise ValueError(form)
    if schema:
        query.append(f"options=-c%20search_path%3D{schema}")
    path = "/" + database if database else parts.path
    return f"{parts.scheme}://{netloc}{path}" + ("?" + "&".join(query) if query else "")


def _run_env(tmp_path: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("NEXUS_STORE", "NEXUS_DATABASE_URL", "NEXUS_DATA_ROOT", "NEXUS_BACKUP_DATABASE_URL",
                        "PGPASSWORD")}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", NEXUS_ENV_ROOT=str(tmp_path),
               PYTHONPATH=os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")])))
    env.update(extra)
    return env


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    """Two fresh schemas and two fresh roles, each name with a random
    suffix: the serve schema (the writer role's own, USAGE and CREATE, the
    V1 shape) and the store schema (the pre-run fixture migrated into it as
    the superuser; the backup role holds SELECT on its tables and CREATEDB,
    the deployment's backup role). Every one is dropped afterwards, and
    restore_verify's scratch databases by the names this module's runs
    printed."""
    if not PG_URL:
        pytest.skip(NO_PG)
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    h = secrets.token_hex(5)
    names = {"serve": f"f6s_{h}", "store": f"f6b_{h}", "writer": f"f6w_{h}", "backup": f"f6k_{h}"}
    made_roles: list[str] = []
    tmp = tmp_path_factory.mktemp("f6_cluster")
    with psycopg.connect(PG_URL, autocommit=True) as c:
        for s in ("serve", "store"):
            c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(names[s])))
    try:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            for role, extra in (("writer", ""), ("backup", " CREATEDB")):
                c.execute(sql.SQL("CREATE ROLE {} LOGIN" + extra + " PASSWORD {}").format(
                    sql.Identifier(names[role]), sql.Literal("Nx" + secrets.token_hex(10))))
                made_roles.append(names[role])
            c.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA {} TO {}").format(sql.Identifier(names["serve"]),
                                                                              sql.Identifier(names["writer"])))
        src = tmp / "source"
        shutil.copytree(FIXTURE, src)
        su = PG_URL + ("&" if urlsplit(PG_URL).query else "?") + f"options=-c%20search_path%3D{names['store']}"
        migrated = _proc.run([sys.executable, str(REPO / "scripts" / "migrate_sqlite_to_postgres.py"),
                                   "--data-root", str(src), "--to", su, "--confirm"], cwd=tmp, env=_run_env(tmp),
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=600)
        assert migrated.returncode == 0, migrated.stdout + migrated.stderr
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(names["store"]),
                                                                     sql.Identifier(names["backup"])))
            c.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
                sql.Identifier(names["store"]), sql.Identifier(names["backup"])))
        yield dict(names, source=src, tmp=tmp)
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            if _SCRATCH_NAMED:
                left = [r[0] for r in c.execute("SELECT datname FROM pg_database WHERE datname = ANY(%s)",
                                                (sorted(_SCRATCH_NAMED),))]
                for name in left:
                    c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
            for role in made_roles:
                c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s", (role,))
                c.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(role)))
                c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            for s in ("serve", "store"):
                c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(names[s])))


def _set_password(role: str, password: str) -> None:
    import psycopg
    from psycopg import sql
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))


def _form(form: str) -> dict:
    tag, decoy_tag = secrets.token_hex(6), secrets.token_hex(6)
    return {"password": _form_password(form, tag), "decoy": f"Dk@{decoy_tag}/x y", "tags": [tag, decoy_tag]}


@pytest.mark.parametrize("form", FORMS)
def test_p1_the_server_names_the_store_and_records_its_lock_with_no_secret(form, cluster, tmp_path):
    import psycopg
    from backend import store_lock
    from backend.store import StoreTarget, connect
    from tests.test_w3_serve_stub import _free_port, _get, _serve
    f = _form(form)
    writer, schema = cluster["writer"], cluster["serve"]
    _set_password(writer, f["password"])
    url = _form_url(form, writer, f["password"], decoy=f["decoy"], schema=schema)
    target = StoreTarget.postgres(url)
    db = connect(target)                                # where NEXUS connects: the store interface
    try:
        who, where = db.execute("SELECT current_user, current_schema()").fetchone()
    finally:
        db.close()
    assert (who, where) == (writer, schema), (who, where)
    lock = store_lock.acquire(target, mode="script:test_f6")
    try:
        shown_lock = f"{lock!r} {lock.db_path} {target!r}"
    finally:
        lock.release()
    database = urlsplit(PG_URL).path.lstrip("/")
    key = f"{store_lock.PG_LOCK_PREFIX}{database}/{schema}"
    stale = json.dumps({"nexus_store_lock": 1, "pid": 999999, "mode": "console", "host": "planted-by-test-f6",
                        "since": "2026-11-01T00:00:00Z", "released": False}, sort_keys=True)
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'SET search_path TO "{schema}"')
        c.execute("INSERT INTO nexus_store_lock (lock_key, holder) VALUES (%s, %s) "
                  "ON CONFLICT (lock_key) DO UPDATE SET holder = excluded.holder", (key, stale))
    root, port = tmp_path / "root", _free_port()
    (tmp_path / "envroot").mkdir()
    child = _serve(tmp_path, root, port, LLM_BACKEND="stub", NEXUS_STORE="postgres", NEXUS_DATABASE_URL=url)
    state = child.wait_until(port)
    try:
        health = _get(port, "/healthz") if state == "serving" else {}
    finally:
        child.stop()
    out = child.output()
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'SET search_path TO "{schema}"')
        holders = [r[0] for r in c.execute("SELECT holder FROM nexus_store_lock")]
        events = [r[0] for r in c.execute("SELECT event FROM nexus_store_lock_log")]
    lines = out.splitlines()
    print(f"P1 {form}: {state}; first line {lines[0] if lines else ''!r}; store "
          f"{[l for l in lines if l.startswith('  store')]}; healthz store {health.get('store')}; "
          f"takeovers logged {sum('took over a stale lock' in l for l in lines)}")
    assert state == "serving", f"--serve did not serve: {state}; {child.last_probe}; {_tail(out)}"
    leaked = {"output": _leaks(out, f["tags"]), "healthz": _leaks(json.dumps(health), f["tags"]),
              "lock holder rows": _leaks(json.dumps(holders), f["tags"]),
              "lock log rows": _leaks(json.dumps(events), f["tags"]), "reprs": _leaks(shown_lock, f["tags"])}
    assert not any(leaked.values()), f"a secret in: {leaked}; {_tail(out)}"
    assert lines[0].startswith("database: postgres postgresql://") and REDACTION in lines[0], lines[0]
    assert any(json.loads(e).get("previous", {}).get("pid") == 999999 for e in events), \
        f"the planted stale holder was not taken over, so the log was not exercised: {events}"
    assert any("took over a stale lock" in l for l in lines), "the takeover was not logged"
    assert health["store"]["backend"] == "postgres" and health["store"]["target"].endswith(f"schema {schema}")


def _url_taking_scripts() -> list[str]:
    """The tracked entry points that take a database URL, derived by AST:
    a call to `open_read_only(..., url_ok=True)`, `store_exists`, `is_url`,
    `redacted`, `store_label` or `StoreTarget.postgres`. start.py reads its
    URL from the environment and is P1's."""
    takers = {"store_exists", "is_url", "redacted", "store_label"}
    out = []
    for p in tracked("scripts/*.py") + [p for p in tracked("*.py") if p.parent == REPO]:
        if p.name.startswith("_") or p.name == "start.py":
            continue
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and (
                    _callee(n) in takers
                    or (_callee(n) == "open_read_only" and any(k.arg == "url_ok" for k in n.keywords))
                    or (_callee(n) == "postgres" and isinstance(n.func, ast.Attribute)
                        and getattr(n.func.value, "id", "") == "StoreTarget")):
                out.append(p.relative_to(REPO).as_posix())
                break
    return sorted(out)


#: How each URL-taking script is run read-only, and the exits it may give.
#: A new URL-taking script fails P2 until it has a recipe here.
SCRIPT_RECIPES = {
    "scripts/archive_consolidation.py": (["--kb", "{url}", "--report", "{tmp}/ac.json"], {0}),
    "scripts/archive_pregate.py": (["--kb", "{url}", "--report", "{tmp}/ap.json"], {0}),
    "scripts/as2_instance_windows.py": (["--db", "{url}"], {0}),
    "scripts/cp_b49_tally.py": (["--db", "{url}"], {0}),
    "scripts/diag_downstream.py": (["--db", "{url}"], {0}),
    "scripts/export_postgres_to_sqlite.py": (["--from", "{url}", "--out", "{tmp}/export"], {0}),
    "scripts/replay_check.py": (["--db", "{url}", "--report-dir", "{tmp}/reports"], {0}),
    # the backup role cannot write: the migration refuses the target by name (a dry run writes nothing anyway)
    "scripts/migrate_sqlite_to_postgres.py": (["--data-root", "{source}", "--to", "{url}"], {2}),
}
IN_PROCESS = {"scripts/backup.py", "scripts/restore_verify.py"}       # P3 runs them with subprocess wrapped
#: The scripts whose first line is the store, by law 15 (the two archivers
#: print a report whose store line is inside it).
FIRST_LINE_NAMES_THE_STORE = {"scripts/as2_instance_windows.py", "scripts/cp_b49_tally.py", "scripts/diag_downstream.py",
                              "scripts/export_postgres_to_sqlite.py", "scripts/replay_check.py"}


def test_p2_population_every_url_taking_script_has_a_recipe():
    derived = _url_taking_scripts()
    print(f"\nP2 population (derived): {derived}")
    assert set(derived) == set(SCRIPT_RECIPES) | IN_PROCESS, \
        f"recipes for {sorted(set(derived) - set(SCRIPT_RECIPES) - IN_PROCESS)}; stale {sorted(set(SCRIPT_RECIPES) | IN_PROCESS - set(derived))}"


@pytest.mark.parametrize("form", FORMS)
def test_p2_every_instance_script_names_the_store_with_no_secret(form, cluster, tmp_path):
    f = _form(form)
    backup = cluster["backup"]
    _set_password(backup, f["password"])
    url = _form_url(form, backup, f["password"], decoy=f["decoy"], schema=cluster["store"])
    procs = {}
    for script, (argv, _ok) in SCRIPT_RECIPES.items():
        args = [a.format(url=url, tmp=tmp_path.as_posix(), source=cluster["source"].as_posix()) for a in argv]
        procs[script] = _proc.Child([sys.executable, str(REPO / script), *args], label=script, cwd=tmp_path,
                                    env=_run_env(tmp_path), stdin=_proc.DEVNULL, stdout=_proc.PIPE,
                                    stderr=_proc.STDOUT, text=True, encoding="utf-8", errors="replace")
    outputs = {}
    for script, p in procs.items():
        out, _ = p.communicate(timeout=600)
        outputs[script] = (p.returncode, out)
    written = {p.relative_to(tmp_path).as_posix(): p.read_text(encoding="utf-8", errors="replace")
               for p in tmp_path.rglob("*") if p.is_file()}
    print(f"P2 {form}: " + "; ".join(f"{Path(s).name} exit {c}" for s, (c, _o) in outputs.items())
          + f"; written {sorted(written)}")
    for script, (code, out) in outputs.items():
        assert code in SCRIPT_RECIPES[script][1], f"{script} exit {code}; {_tail(out)}"
        assert not _leaks(out, f["tags"]), f"{script} printed a secret; {_tail(out)}"
        if script in FIRST_LINE_NAMES_THE_STORE:
            first = out.splitlines()[0]
            assert "postgres postgresql://" in first and REDACTION in first, (script, first)
    assert written, "non-vacuity: the scripts wrote their reports"
    for name, text in written.items():
        assert not _leaks(text, f["tags"]), f"{name} records a secret"


class _Recorder:
    """A module's `subprocess`, with every `run` recorded: the argv and the
    environment's PGPASSWORD."""

    def __init__(self, calls: list):
        self.calls = calls

    def run(self, argv, *args, **kwargs):
        self.calls.append(([str(a) for a in argv], (kwargs.get("env") or {}).get("PGPASSWORD")))
        kwargs.setdefault("timeout", 600)       # W4b: a run in a test is bounded, the product's own bound first
        return _proc.run(argv, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(subprocess, name)


def _in_process(main, argv: list, capsys) -> tuple[int, str, str]:
    code = main(argv)
    seen = capsys.readouterr()
    return code, seen.out, seen.err


def _wrap_subprocess(monkeypatch, calls: list):
    from scripts import backup as backup_mod
    from scripts import restore_verify as restore_mod
    monkeypatch.setattr(backup_mod, "subprocess", _Recorder(calls))
    monkeypatch.setattr(restore_mod, "subprocess", _Recorder(calls))
    return backup_mod, restore_mod


def _isolate(monkeypatch, tmp_path):
    for name in ("NEXUS_STORE", "NEXUS_DATABASE_URL", "NEXUS_DATA_ROOT", "NEXUS_BACKUP_DATABASE_URL", "PGPASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NEXUS_ENV_ROOT", str(tmp_path))


@pytest.mark.parametrize("form", FORMS)
def test_p3_the_backup_and_its_restore_pass_with_no_secret_on_any_command_line(form, cluster, tmp_path, monkeypatch,
                                                                              capsys):
    if shutil.which("pg_dump") is None or shutil.which("pg_restore") is None:
        pytest.skip("the PostgreSQL client tools (pg_dump, pg_restore) are not on PATH")
    f = _form(form)
    backup_role = cluster["backup"]
    _set_password(backup_role, f["password"])
    database = urlsplit(PG_URL).path.lstrip("/")
    url = _form_url(form, backup_role, f["password"], decoy=f["decoy"], schema=cluster["store"])
    server = _form_url(form, backup_role, f["password"], decoy=f["decoy"], database=database)
    _isolate(monkeypatch, tmp_path)
    calls: list = []
    backup_mod, restore_mod = _wrap_subprocess(monkeypatch, calls)
    root = tmp_path / "dataroot"
    (root / "snapshots").mkdir(parents=True)
    (root / "turn_metrics.jsonl").write_text("{}\n", encoding="utf-8")
    code, out, err = _in_process(backup_mod.main, ["--data-root", str(root), "--out", str(tmp_path / "backups"),
                                                   "--database-url", url], capsys)
    folder = next((Path(l.split("backup: ", 1)[1].strip()) for l in out.splitlines() if l.startswith("backup: ")),
                  None)
    manifest = (folder / "MANIFEST.json").read_text(encoding="utf-8") if folder and \
        (folder / "MANIFEST.json").is_file() else ""
    print(f"P3 {form} backup: exit {code}; {[l for l in out.splitlines() if l.startswith(('data root', 'FAIL', 'REFUSED', 'integrity'))]}")
    assert code == 0, f"the backup did not pass (exit {code}):\n{out[-2500:]}{err[-1500:]}"
    rcode, rout, rerr = _in_process(restore_mod.main, [str(folder), "--database-url", server], capsys)
    _SCRATCH_NAMED.update(name for line in rout.splitlines() if line.startswith(SCRATCH_LINE)
                          for name in line[len(SCRATCH_LINE):].split(" on ", 1)[0].split(", "))
    tools = sorted({Path(argv[0]).name for argv, _pw in calls})
    print(f"P3 {form} restore: exit {rcode}; {[l for l in rout.splitlines() if l.startswith(('PASS', 'FAIL', 'restore'))]}; "
          f"client tools run: {tools}")
    assert rcode == 0 and "restore verification: 5 of 5 checks passed" in rout, rout[-2500:] + rerr[-1500:]
    leaked = {"backup stdout": _leaks(out, f["tags"]), "backup stderr": _leaks(err, f["tags"]),
              "MANIFEST.json": _leaks(manifest, f["tags"]), "restore stdout": _leaks(rout, f["tags"]),
              "restore stderr": _leaks(rerr, f["tags"]),
              "argv": [a for argv, _pw in calls for a in argv if _leaks(a, f["tags"])]}
    assert not any(leaked.values()), f"a secret in: {leaked}"
    assert {"pg_dump", "pg_restore", "createdb", "dropdb", "psql"} <= set(tools), tools
    connecting = [(argv, pw) for argv, pw in calls if any(a.startswith(("--dbname=", "--maintenance-db="))
                                                          for a in argv)]
    assert connecting and all(pw == f["password"] for _argv, pw in connecting), \
        [(Path(a[0]).name, pw == f["password"]) for a, pw in connecting]
    assert json.loads(manifest)["store"]["target"].startswith("postgresql://"), "the manifest names the store"


def _hosted_manifest_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "hosted_backup"
    folder.mkdir()
    (folder / "store.pgdump").write_bytes(b"not read: the refusal comes first")
    (folder / "MANIFEST.json").write_text(json.dumps({
        "format": 2, "taken_at": "2026-11-10T00:00:00Z", "source": "/nowhere",
        "store": {"backend": "postgres", "target": "postgresql://x@h/db", "database": "db", "schema": "s",
                  "dump": "store.pgdump"},
        "counts": {"store": {}}, "chain": None, "lineage": None, "files": {}}), encoding="utf-8")
    return folder


def test_p4_a_url_carrying_sslpassword_is_refused_by_name_with_nothing_run(cluster, tmp_path, monkeypatch, capsys):
    if shutil.which("pg_dump") is None:
        pytest.skip("the PostgreSQL client tools are not on PATH")
    tag = secrets.token_hex(6)
    backup_role = cluster["backup"]
    password = _form_password("query", secrets.token_hex(6))
    _set_password(backup_role, password)
    url = _form_url("query", backup_role, password, decoy="", schema=cluster["store"]) + f"&sslpassword=Kp{tag}"
    _isolate(monkeypatch, tmp_path)
    calls: list = []
    backup_mod, restore_mod = _wrap_subprocess(monkeypatch, calls)
    root = tmp_path / "dataroot"
    root.mkdir()
    code, out, err = _in_process(backup_mod.main, ["--data-root", str(root), "--out", str(tmp_path / "backups"),
                                                   "--database-url", url], capsys)
    print(f"P4 backup: exit {code}: {out.strip().splitlines()[-1] if out.strip() else ''}; calls {calls}")
    assert code == 2 and "REFUSED" in out and "sslpassword" in out, out + err
    assert calls == [], f"a client tool ran: {calls}"
    assert not (tmp_path / "backups").exists(), "a backup folder was written"
    assert not _leaks(out + err, [tag]), out + err
    folder = _hosted_manifest_folder(tmp_path)
    server = _form_url("query", backup_role, password, decoy="",
                       database=urlsplit(PG_URL).path.lstrip("/")) + f"&sslpassword=Kp{tag}"
    rcode, rout, rerr = _in_process(restore_mod.main, [str(folder), "--database-url", server], capsys)
    _SCRATCH_NAMED.update(name for line in rout.splitlines() if line.startswith(SCRATCH_LINE)
                          for name in line[len(SCRATCH_LINE):].split(" on ", 1)[0].split(", "))
    print(f"P4 restore: exit {rcode}: {rout.strip().splitlines()[-1] if rout.strip() else ''}; calls {calls}")
    assert rcode == 2 and "REFUSED" in rout and "sslpassword" in rout, rout + rerr
    assert calls == [], f"a client tool ran: {calls}"
    assert not _leaks(rout + rerr, [tag]), rout + rerr


# ---------------------------------------------------------------------------
# S — every URL-taking script on a URL libpq refuses or would misread (D5).
# No server: the refusal comes before any connect, so this runs everywhere.
# ---------------------------------------------------------------------------

#: How each URL-taking script is run on a corpus URL: P2's recipes, and the
#: two P3 runs in process, here as processes too. `{root}` is an empty data
#: root and `{hosted}` a hosted backup's folder, both made in the run's own
#: folder; `{source}` is a copy of the pre-run fixture, shared and read only.
REFUSAL_RECIPES = {
    **{script: argv for script, (argv, _ok) in SCRIPT_RECIPES.items()},
    "scripts/backup.py": ["--data-root", "{root}", "--out", "{tmp}/backups", "--database-url", "{url}"],
    "scripts/restore_verify.py": ["{hosted}", "--database-url", "{url}"],
}
#: Every one of them refuses a store it cannot open with this exit.
EXIT_REFUSED = 2


def _listing(folder: Path) -> list:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


def _refused_run(job: tuple) -> tuple:
    """One script on one corpus URL, as a process in its own folder, read to
    its end within the helpers' default wait: (exit code or None at the
    timeout, its whole output, what its folder held before, what it holds
    after)."""
    script, row, run_dir, source = job
    run_dir.mkdir(parents=True)
    argv_template = REFUSAL_RECIPES[script]
    if any("{root}" in a for a in argv_template):
        (run_dir / "dataroot").mkdir()
    if any("{hosted}" in a for a in argv_template):
        _hosted_manifest_folder(run_dir)
    before = _listing(run_dir)
    argv = [a.format(url=row.url, tmp=run_dir.as_posix(), source=source.as_posix(),
                     root=(run_dir / "dataroot").as_posix(), hosted=(run_dir / "hosted_backup").as_posix())
            for a in argv_template]
    try:
        proc = _proc.run([sys.executable, str(REPO / script), *argv], cwd=run_dir, env=_run_env(run_dir),
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace", timeout=_child_wait())
        code, out = proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as e:
        code, out = None, (e.stdout or "") if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
    return code, out, before, _listing(run_dir)


def test_s1_every_url_taking_script_refuses_an_unreadable_url_by_name_and_prints_none_of_it(tmp_path):
    """Every URL-taking script (derived by AST, as P2 derives them), each
    run as a process with every corpus URL: exit 2 by a named refusal, never
    a traceback; no part of the URL on any line; where the first line names
    the store (law 15) it names the marker; and nothing is created."""
    pytest.importorskip("psycopg")
    scripts = _url_taking_scripts()
    assert set(scripts) == set(REFUSAL_RECIPES) and len(scripts) >= 10, \
        f"a URL-taking script without a recipe here, or a stale one: {sorted(set(scripts) ^ set(REFUSAL_RECIPES))}"
    rows = _rows_here()
    source = tmp_path / "source"
    shutil.copytree(FIXTURE, source)
    source_before = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    jobs = [(script, row, tmp_path / f"run-{i:02d}-{j:02d}", source)
            for i, script in enumerate(scripts) for j, row in enumerate(rows)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_refused_run, jobs))
    problems, first_tail = [], ""
    exits: dict = {}
    for (script, row, _dir, _source), (code, out, before, after) in zip(jobs, results):
        exits.setdefault(script, set()).add(code)
        wrong = []
        if _shows(out, row):
            wrong.append(f"prints {_shows(out, row)} of the URL: {[l for l in out.splitlines() if _shows(l, row)][:1]}")
        if code != EXIT_REFUSED or "Traceback" in out:
            wrong.append(f"exit {code}{', a traceback' if 'Traceback' in out else ''}: "
                         f"{out.strip().splitlines()[-1][:150] if out.strip() else ''!r}")
        if row.word not in out or NONE_SHOWN not in out:
            wrong.append(f"its refusal does not name {row.word!r}: {out.strip().splitlines()[-1][:150] if out.strip() else ''!r}")
        if script in FIRST_LINE_NAMES_THE_STORE and f"postgres {NOT_SHOWN}" not in (out.splitlines() or [""])[0]:
            wrong.append(f"its first line does not name the store by the marker: {(out.splitlines() or [''])[0][:150]!r}")
        if after != before:
            wrong.append(f"created {sorted(set(after) - set(before))}")
        if wrong:
            problems.append(f"{Path(script).name} on {row.id}: " + " / ".join(wrong))
            first_tail = first_tail or f"\n{Path(script).name} on {row.id}: {_tail(out)}"
    print(f"\nS1 ({sys.platform}): {len(scripts)} scripts x {len(rows)} URLs = {len(jobs)} runs; exits "
          f"{ {Path(s).name: sorted(c, key=str) for s, c in exits.items()} }; problems: {len(problems)}")
    source_after = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    assert source_after == source_before, "the migration's source was changed"
    assert not problems, f"{len(problems)} of {len(jobs)} runs: " + "; ".join(problems[:12]) + first_tail


@pytest.mark.parametrize("row", UNREADABLE, ids=ROW_IDS)
def test_s2_backup_and_restore_refuse_an_unreadable_url_with_nothing_written_and_no_client_tool_run(
        row, tmp_path, monkeypatch, capsys):
    """backup.py and restore_verify.py in this process with each module's
    `subprocess` wrapped (P4's shape, with no server): refused by name, exit
    2, none of the URL printed, nothing written, no client tool run, not
    even a version probe, and the driver never called (its connect is
    replaced: nothing here can resolve a name or wait on a connect)."""
    psycopg = pytest.importorskip("psycopg")
    why = _not_a_case_here(row)
    if why:
        pytest.skip(why)
    _isolate(monkeypatch, tmp_path)
    calls: list = []
    backup_mod, restore_mod = _wrap_subprocess(monkeypatch, calls)
    connects: list = []
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: connects.append(a) or (_ for _ in ()).throw(
        AssertionError("the driver was called")))
    root = tmp_path / "dataroot"
    root.mkdir()
    code, out, err = _in_process(backup_mod.main, ["--data-root", str(root), "--out", str(tmp_path / "backups"),
                                                   "--database-url", row.url], capsys)
    print(f"S2 {row.id} backup: exit {code}: {out.strip().splitlines()[-1][:170] if out.strip() else ''}; "
          f"client tools run: {[Path(a[0]).name for a, _pw in calls]}")
    assert not _shows(out + err, row), f"the backup prints {_shows(out + err, row)} of the URL:\n{out}{err}"
    assert code == 2 and "REFUSED" in out and row.word in out and NONE_SHOWN in out, out + err
    assert f"database: postgres {NOT_SHOWN}" in out.splitlines()[0], out.splitlines()[0]
    assert calls == [], f"a client tool ran: {calls}"
    assert not (tmp_path / "backups").exists(), "a backup folder was written"
    folder, scratch = _hosted_manifest_folder(tmp_path), tmp_path / "scratch"
    rcode, rout, rerr = _in_process(restore_mod.main, [str(folder), "--database-url", row.url, "--into",
                                                       str(scratch)], capsys)
    print(f"S2 {row.id} restore: exit {rcode}: {rout.strip().splitlines()[-1][:170] if rout.strip() else ''}; "
          f"client tools run: {[Path(a[0]).name for a, _pw in calls]}")
    assert not _shows(rout + rerr, row), f"the restore prints {_shows(rout + rerr, row)} of the URL:\n{rout}{rerr}"
    assert rcode == 2 and "REFUSED" in rout and row.word in rout and NONE_SHOWN in rout, rout + rerr
    assert calls == [], f"a client tool ran: {calls}"
    assert not scratch.exists(), "the backup was restored into a scratch folder"
    assert connects == [], f"the driver's connect was called {len(connects)} time(s)"


# ---------------------------------------------------------------------------
# B — backup.sh
# ---------------------------------------------------------------------------

def test_b1_backup_sh_hands_the_url_over_in_the_environment_never_on_the_command_line(tmp_path):
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("no sh on this platform")
    stand_in = tmp_path / "bin"
    stand_in.mkdir()
    (stand_in / "python").write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$RECORD\"\n"
                                     "printf '%s' \"$NEXUS_BACKUP_DATABASE_URL\" > \"$RECORD.env\"\n",
                                     encoding="utf-8")
    (stand_in / "python").chmod(0o755)
    record = tmp_path / "argv.txt"
    tag = secrets.token_hex(6)
    url = f"postgresql://nexus_backup:Bk{tag}@postgres:5432/nexus"
    env = {**os.environ, "PATH": f"{stand_in}{os.pathsep}{os.environ['PATH']}", "RECORD": str(record),
           "NEXUS_DATA_ROOT": "/var/lib/nexus", "NEXUS_BACKUP_DIR": "/var/backups/nexus",
           "NEXUS_BACKUP_DATABASE_URL": url}
    proc = _proc.run([sh, str(REPO / "scripts" / "backup.sh")], env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)
    argv = record.read_text(encoding="utf-8").split("\n")
    handed = Path(f"{record}.env").read_text(encoding="utf-8")
    print(f"B1 argv {argv}; environment carries the URL: {handed == url}")
    assert proc.returncode == 0 and argv[0].endswith("scripts/backup.py"), proc.stdout + proc.stderr
    assert not _leaks("\n".join(argv), [tag]) and "--database-url" not in argv, \
        f"the URL is on backup.py's command line: {argv}"
    assert handed == url, "backup.py's environment does not carry the URL"


def test_b2_backup_py_reads_the_backup_url_from_its_environment(tmp_path, monkeypatch, capsys):
    """Without --database-url, NEXUS_BACKUP_DATABASE_URL names the hosted
    store (restore_verify.py already read it). A closed port: the store does
    not answer, refused by name, nothing written, no secret printed. libpq's
    `connect_timeout` bounds the wait where a closed port's connect is
    dropped rather than refused (CI's Windows runner, CLAUDE.md W3)."""
    import socket
    from scripts import backup as backup_mod
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    tag = secrets.token_hex(6)
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("NEXUS_BACKUP_DATABASE_URL",
                       f"postgresql://f6:Bk{tag}@127.0.0.1:{port}/nexus_f6?connect_timeout=3")
    root = tmp_path / "dataroot"
    root.mkdir()
    code, out, err = _in_process(backup_mod.main, ["--data-root", str(root), "--out", str(tmp_path / "backups")],
                                 capsys)
    print(f"B2: exit {code}; {out.strip().splitlines()}")
    assert out.splitlines()[0].startswith("data root: ") and "database: postgres postgresql://f6:***@" in \
        out.splitlines()[0], out
    assert code == 2 and "REFUSED" in out and "does not answer" in out, out + err
    assert not _leaks(out + err, [tag]) and not (tmp_path / "backups").exists(), out + err
