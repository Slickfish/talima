"""W4b's closing run: the image build's key fetch, which made a single attempt (deploy-smoke run 28, 14 November).

THE FINDING
    `deploy-smoke` run 28, on W4b's first closing commit `092b0c9`: the hosted job's image build died in the RUN that
    installs postgresql-client-16 (deploy/Dockerfile), with exit status 56. In that RUN only curl returns 56
    (CURLE_RECV_ERROR: data failed to arrive; apt-get fails with 100), and its one curl fetched the PGDG signing key
    from www.postgresql.org in a single attempt. The stub job built the same file at the same time on another runner
    (its build step 19:50:20 to 19:51:19 UTC; the hosted job's 19:50:16 to 19:52:07). Law 10: the job was not re-run
    to green. The fetch retries now. curl retries nothing unless asked, and `--retry` alone covers "a timeout, an
    FTP 4xx response code or an HTTP 408, 429, 500, 502, 503 or 504 response code" (curl's manual), so a failed
    receive is retried only under `--retry-all-errors` (added in 7.71.0). apt retries a failed download three times
    by default (Acquire::Retries, since apt 2.3.2, LP #1876035; exponential backoff since 2.3.7: apt's changelog). The
    build stage's pip (24.0 today, from the base image's ensurepip) retries a connection that fails before its
    response five times (`pip install --help`), but makes one attempt at a body cut short (BACKLOG W4b-R4). So the key
    was the one fetch in the build's RUN lines with no retry at all. BuildKit's own pulls are not measured here.

F1   The population, in two halves. THE CENSUS reads no syntax. It takes every `scheme://` token in the whole text of
     each tracked Dockerfile (an instruction, a heredoc's body and a comment are all text to it; a line continuation
     inside a token is removed first) and requires each to be answered for, once, in ACCOUNTED, with what it is there
     for: fetched by a curl that F2 runs, or text, a label, the run-time healthcheck. A token that is not answered for
     fails F1 wherever it stands, however the other half reads it, and so does an answer the file no longer holds. So
     no new `scheme://` token can arrive in a tracked Dockerfile without this module being edited. The census sees
     tokens, not URLs: what it does not see is under NOT COVERED. THE READING finds
     the curls for F2, and the URLs it finds as curls must be exactly the ones ACCOUNTED calls fetched. To the reading,
     a fetch is a simple command whose own words carry a literal URL, in a shell-form or exec-form RUN or in the script
     a shell (sh, bash, dash) is given with `-c`: the first operand after it, where a short `-o` or `-O` takes the next
     word, alone or in a cluster (`-eo pipefail`), and bash's `--rcfile` and `--init-file` do before it. As in
     BuildKit's parser, a Dockerfile is split at newlines only, a comment line inside a continuation is dropped, a line
     that ends, past spaces and tabs, in exactly one backslash is joined to the next with nothing between, and an
     instruction's name ends at BuildKit's whitespace (a tab included). In a script, a # at a statement's start, or
     after an unescaped space or tab, begins a comment, as in sh, and a # inside a word does not (curl's `-#`, a URL's
     fragment, a # after an escaped space or a no-break space). The three text commands (echo, printf, sed) write a URL
     and fetch nothing. Each fetch must be a curl that F2 can run: its output a separate `-o PATH` or `--output PATH`,
     or stdout; no option F1 knows to write another file (a header dump, a trace, a cookie jar and the like), by its
     full name, by its short letter or by a prefix of the name, which curl takes when it is unambiguous; no `--expand-`
     option; no write-out's `%output{}`; no redirect of another descriptor except to /dev/null or to a copy of a
     descriptor (`2>&1`); and no `>` inside a word. Any other fetch is named. So are these forms, which the reading does
     not read as fetches: an ADD of a URL; a heredoc RUN (BuildKit's heredoc pattern applied to the RUN's words as
     Python's shlex splits them, quotes kept, a bare `<<` joined to the word after it); a URL between a statement's
     first command-substitution opening (`$(` or a backquote outside single quotes, or an escaped one inside double
     quotes) and its last `)` or backquote; an `escape` parser directive among the leading `# key=value` lines,
     byte-order marks dropped first (BuildKit reads a narrower set of lines as directives: three known keys, each with a
     value); and a command F1 cannot split into words (an open quote, a lone backslash at its end), named by raising.
     The reading has gaps, listed under NOT COVERED; the census is what answers for a literal URL in them. F1 only reads
     text, so it runs everywhere.
F1c  The census's companions plant a URL the table does not hold in twelve forms, and each is named by its token: a
     `case` inside a substitution, a heredoc opened after a quote inside a quote, bash's `-rcfile` with one dash, a
     process substitution, an `ONBUILD RUN`, a COPY heredoc's body, a comment line, a scheme split across a
     continuation, a heredoc opened with a blank after `<<`, an escaped substitution in a double-quoted script, an ADD,
     and another scheme. The first seven are forms the reading passes without a word. A second copy of an answered URL,
     an answer the file no longer holds, a URL called fetched that no curl fetches, and a Dockerfile with no entry are
     named too. The reading's companions plant a wget, a Python urlretrieve and a wget inside an `sh -euc` script; F1's
     own code (the functions and constants it runs) names none of them. Its other plants each test a rule that names its
     form, each alone after the shipped file and each with the one message it must produce: an ADD of a URL; four
     heredoc RUNs, two of them with a blank after the `<<`; ten curls F2 cannot run (an output inside an option cluster,
     `-O`, a header dump by `-D` and by the abbreviation `--dump-h`, `--expand-output`, a stdout redirect, a redirect
     glued to a word, a stderr redirect to a file and an append to one, a write-out's `%output{}`); a fetch after a
     comment line inside a continuation; a wget under `bash --norc -c`, `sh -c -e`, `bash -c -O extglob`, `sh -c -`,
     `bash -eo pipefail -c`, `bash -ceo pipefail` and `bash --rcfile /etc/profile -c`; `bash --norc -x` with a URL in
     its words (no `-c`, so bash's own words are the statement); a wget after a shell-form RUN's `[`; a wget in the RUN
     after a line that ends in two backslashes (no continuation), and a RUN that ends in three (no continuation either,
     so it ends in a lone backslash F1 cannot read); and thirteen URLs F1 places in a command substitution: quoted and
     not, in `$(...)` and in backquotes, one after a quoted `)` inside the substitution, one that only sits between two
     substitutions, three escaped inside a double-quoted script, one after an apostrophe inside double quotes, one after
     an escaped apostrophe, and one in backquotes after a closed `$(...)`. Five Dockerfiles test where a parser
     directive is read: as the first line, after a byte-order mark and after another directive, an escape directive is
     named; after a plain comment or an instruction, it is not. Nineteen plants F1 must read as they are. Fifteen are
     curls, each compared word for word: with `-#`; with a URL's fragment; with stderr sent to /dev/null and to stdout;
     with `-HAccept:text/plain` (an option whose argument holds a `c`); with a URL split across a continuation, where
     the line ends in a newline, in a carriage return and a newline, and in two carriage returns and a newline; with a
     tab after RUN; with `--cookie`, curl's own option and a prefix of `--cookie-jar`; with a form feed inside a line,
     which Docker does not split at; with a # after an escaped space and after a no-break space; after an `sh -euc`
     statement holding a substitution; and with a substitution inside the URL. Four are no fetch at all: an echo whose
     substitution holds no URL, a here-string, a quoted `<<EOF`, and a URL in a comment.
F2   The consequence. It needs a POSIX sh and a curl that knows `--retry-all-errors`; elsewhere it is skipped by name,
     Windows included, because the image's RUN lines run under a Linux container's sh. Every curl F1 found runs as its
     RUN runs it: the script's own assignments first, with the build secret absent, as on CI. Like the container, the
     child reaches 127.0.0.1 with no proxy and reads no curlrc: each curl it runs is given `-q` first (curl's --disable,
     which skips every curlrc, the one in the account's passwd home included), and HOME, CURL_HOME and XDG_CONFIG_HOME
     point at a folder of the test's own with no curlrc (F2q plants one there). Its URL points at a server on 127.0.0.1
     that resets the first connection (RST, not FIN) and serves a stand-in key on the next. The first arm resets before
     any response byte; the second sends the headers and half the body, waits half a second, then resets. Each fetch
     must exit 0 after exactly two connections, holding exactly the stand-in's bytes.
F2p  The fetch as `092b0c9` shipped it (that RUN, verbatim) dies on both arms after one connection, with curl's exit
     56 on both, which is an RST's (a FIN would give 52 and 18), and the second arm leaves the half it received: law
     16's pre-fix run, kept as a test.
F2c  A curl given `--retry 5` and no `--retry-all-errors` reads as retried, and dies the same way on both arms. F2's
     verdict reads what a fetch survives, never which flags it carries; its host check names `--retry-all-errors`
     only to skip a curl older than 7.71.
F2r  A curl given `--retry 5 --retry-all-errors` whose output goes into a pipe, as cloud-init's NVIDIA fetches do
     (BACKLOG W4b-R2), survives the first arm and fails the second, the stand-in's half duplicated. F2 runs a
     pipeline's fetch alone, into a `>` file. curl's manual says a pipe and a `>` file both keep a failed attempt's
     bytes, and a pipe into cat measured the same 6828 bytes (RUN_LOG). It witnesses the byte check, the second arm,
     and the Dockerfile comment's reason for `-o`.
F2q  A curlrc that adds the retry flags, planted where CURL_HOME points, leaves the fetch as `092b0c9` shipped it dying
     on both arms: the `-q` each curl is given skips it. It fails when that wrapper is removed.
F2v  A curl whose retry flags come from an assignment before it survives both arms: F2 replays the script's
     assignments, without which `sh -u` refuses the unset variable.
F2u  F2 replaces the fetch's URL where the RUN spells it. A URL it cannot find as written (`k\\&x=1`, whose word is
     `k&x=1`) is refused before anything runs: otherwise curl would be handed the Dockerfile's real URL from the
     host that runs the suite. It starts no child, so it runs everywhere.

NOT COVERED (declared)
    apt's and pip's downloads carry no URL on the command line, so they rest on their own retries: apt's three, and pip
    24.0's five for a connection that fails before its response and none for a body cut short (BACKLOG W4b-R4).
    BuildKit's own pulls (the syntax frontend, the FROM images) and the smoke's compose pulls are not RUN lines. Neither
    half sees a fetch whose URL has no scheme (`curl example.invalid/k`), or one slash after its scheme
    (`https:/example.invalid/k`, which curl fetches), or reaches it through a variable, a config file or stdin, whose
    pieces are no `scheme://` token. The census does not see a `://` that the text splits (by a quote, a backslash, a
    JSON escape, or a comment or a blank line inside a continuation): the reading names those in a plain RUN, and
    nothing does inside one of its gaps. And the census counts tokens, not uses: a token it already answers as text can
    be fetched by a new command inside one of the reading's gaps while its count stays one, and an ARG's default
    answered as text can be fetched through the variable. The reading's known gaps, each named by the census when it
    holds a new `scheme://` token and by nothing when it does not: an `ONBUILD RUN`, which runs in a later build; a
    `SHELL` instruction,
    which changes the shell that a later shell-form RUN runs under; a `case` or a function inside a command
    substitution, where the reading ends the statement at the first `)`; a heredoc opened after a quote inside a quote,
    which Python's shlex splits otherwise than BuildKit; bash's long options written with one dash (`-login`,
    `-rcfile`), read as clusters; and bash's process substitution under a text command (BACKLOG W4b-R6). F2 replays a
    script's assignments before its fetch as they are written, so an assignment that runs a command (`A="$(uname -m)"`)
    runs it on the host that runs the suite; the shipped RUN's assignments run none. A stalled fetch (a connection that
    never answers) has no per-attempt bound, and a long Retry-After is waited out between attempts with no bound
    (BACKLOG W4b-R1). deploy/cloud-init.yaml's three curl fetches run on the operator's host at first boot, not in a
    Dockerfile, so they are outside F1 (BACKLOG W4b-R2). The curlrc curl falls back to in the account's passwd home,
    through getpwuid, is not planted by a test, because a test must not write the account's home: RUN_LOG shows it
    planted once ("W4b's closing run refused, and the fix's review"), and F2q plants the one in CURL_HOME.
"""
from __future__ import annotations

import collections
import json
import os
import re
import shlex
import shutil
import socket
import struct
import threading
import time
from dataclasses import dataclass

import pytest

from tests import _proc
from tests._tracked import REPO, tracked
from tests.test_w3_deploy import DOCKERFILE

# A URL in a word: a scheme, then `://`.
URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
# The census's token: a scheme, `://`, and what follows up to a blank, a quote or a character the shell would end a
# word at. A backslash, spaces and tabs, and a newline are a line continuation, removed before the tokens are read.
URL_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'`|;<>()\\]*")
CONTINUATION = re.compile(r"\\[ \t]*\r*\n")
# What a URL is in a tracked Dockerfile for. Every URL in the file's text is answered for here, once. A URL that
# arrives in a Dockerfile fails F1 until someone decides what it is: fetched (then it must be a curl F2 runs), or not.
FETCHED = "fetched by a curl that F2 runs"
ACCOUNTED = {
    "deploy/Dockerfile": {
        "https://www.postgresql.org/media/keys/ACCC4CF8.asc": FETCHED,
        "http://deb.debian.org": "text: the pattern of the sed over apt's sources",
        "https://deb.debian.org": "text: that sed's replacement",
        "https://apt.postgresql.org/pub/repos/apt": "text: the apt source line an echo writes; apt fetches from it",
        "https://github.com/Slickfish/talima": "a label",
        "http://127.0.0.1:8000/healthz": "the HEALTHCHECK's request, made when the container runs, not at the build",
    },
}
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# Reserved words that may open a simple command: a fetch after one still runs in the build.
KEYWORDS = frozenset({"if", "then", "elif", "else", "fi", "do", "done", "while", "until", "!", "{", "}"})
LEADING_KEYWORDS = re.compile(r"^(?:(?:if|then|elif|else|do|while|until|!|\{)\s+)+")
# They write a URL into a file or a stream and fetch nothing.
TEXT_COMMANDS = frozenset({"echo", "printf", "sed"})
SHELLS = frozenset({"sh", "bash", "dash"})
# bash's long options that take the next word as their argument. A short `-o` or `-O` takes one too, alone or in a
# cluster (cluster_arguments); every other option takes none.
SHELL_WITH_ARGUMENT = frozenset({"--rcfile", "--init-file"})
# RUN's own options (--mount, --network, --security), before its command.
RUN_OPTIONS = re.compile(r"^(?:--[a-z-]+(?:=\S+)?\s+)+")
# curl's ways to choose an output other than a separate `-o PATH` (or `--output PATH`) or stdout, and its options that
# write another file. F2 cannot point them at its own folder, so it would write where the Dockerfile writes, on the
# host that runs it.
OTHER_OUTPUTS = frozenset({"-O", "--remote-name", "--remote-name-all", "--output-dir", "-K", "--config",
                           "-D", "--dump-header", "--stderr", "--trace", "--trace-ascii", "-c", "--cookie-jar",
                           "--etag-save", "--libcurl", "--hsts", "--alt-svc"})
# curl's own long option that is also a prefix of one of those names, read as itself. Checked against the options
# `curl --help all` lists (8.5.0): `--cookie` alone, beside `--output`, which is read before the prefixes are.
EXACT_PREFIXES = frozenset({"--cookie"})
# In a cluster of short options: the letters among those, and the letters whose argument is the rest of the cluster.
CLUSTER_OUTPUTS = frozenset("oOcDK")
CLUSTER_ARGUMENT = frozenset("AbCdeEFHmPQrtTuUwxXyYz")
# A redirect of stdout, the fetch's body. A redirect of another descriptor writes its target, which only /dev/null
# or a copy of another descriptor (`&1`, `&-`) leaves alone.
REDIRECT = re.compile(r"^1?>")
OTHER_FD_REDIRECT = re.compile(r"^(\d+)>>?(.*)$")
# curl's write-out writes a file through %output{...}.
WRITE_OUT_FILE = "%output{"
# BuildKit looks for a heredoc at every word of a RUN, on words that keep their quotes (heredocsFromLine calls
# ParseHeredoc per word), and opens one where a word matches this with a marker after the `<<` (and the marker
# lexes to one word, which F1 does not check).
HEREDOC = re.compile(r"^(\d*)<<(-?)\s*([^<]*)$")
# A word that is only the `<<`: BuildKit's lexer keeps the blanks after it in the word (since v0.22.0), so the
# marker is the next word Python's shlex gives.
HEREDOC_BARE = re.compile(r"^\d*<<-?$")
# Where an instruction's name ends: BuildKit's reWhitespace.
INSTRUCTION_END = re.compile(r"[\t\v\f\r ]+")
# A parser directive: `# key=value`, before any other line. BuildKit stops looking at the first line that is not one.
DIRECTIVE = re.compile(r"^#\s*([A-Za-z][A-Za-z0-9_-]*)\s*=\s*(.*?)\s*$")
# What the server serves: not a key, the size of one.
KEY = (b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n" + b"not a key, only the size of one\n" * 140
       + b"-----END PGP PUBLIC KEY BLOCK-----\n")
RETRY_ALL_ERRORS_SINCE = (7, 71)
FIRST_ARM, SECOND_ARM = "before any response byte", "after the headers and half the body"
ARMS = ((FIRST_ARM, False), (SECOND_ARM, True))
# The second arm's wait between sending the half and the reset, so the half is delivered before the RST.
HALF_DELIVERED_S = 0.5

# The RUN as `092b0c9` shipped it (deploy/Dockerfile lines 53 to 69), verbatim, under a FROM.
PRE_FIX_RUN = r'''FROM python:3.11-slim AS runtime
RUN --mount=type=secret,id=proxyca,target=/run/secrets/proxyca.crt \
    sh -euc 'CA=""; if [ -s /run/secrets/proxyca.crt ]; then CA=/run/secrets/proxyca.crt; fi; \
             APT="apt-get ${CA:+-o Acquire::https::CaInfo=$CA}"; \
             sed -i "s|http://deb.debian.org|https://deb.debian.org|g" /etc/apt/sources.list.d/debian.sources; \
             $APT update; \
             $APT install -y --no-install-recommends ca-certificates curl; \
             install -d /usr/share/postgresql-common/pgdg; \
             curl -fsSL ${CA:+--cacert $CA} -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
                  https://www.postgresql.org/media/keys/ACCC4CF8.asc; \
             . /etc/os-release; \
             echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
                  > /etc/apt/sources.list.d/pgdg.list; \
             $APT update; \
             $APT install -y --no-install-recommends postgresql-client-16; \
             apt-get purge -y --auto-remove curl; \
             rm -rf /var/lib/apt/lists/*; \
             pg_dump --version'
'''


@dataclass(frozen=True)
class Fetch:
    where: str
    run: int                        # the RUN's ordinal in its file, from 1
    prelude: tuple[str, ...]        # the script's assignments before it, as written
    raw: str                        # the simple command, as written
    words: tuple[str, ...]
    unreadable: str = ""            # why F1 names it without reading it as a fetch, if it does

    @property
    def command(self) -> str:
        k = command_index(self.words)
        return os.path.basename(self.words[k]) if k is not None else ""

    def label(self) -> str:
        return f"{self.where} RUN #{self.run}: {' '.join(self.raw.split())[:160]}"


@dataclass(frozen=True)
class Outcome:
    fetch: Fetch
    arm: str
    status: int
    connections: int
    received: int                   # bytes in the fetch's output file afterwards
    err: str
    key: bool = False               # the output holds exactly the stand-in's bytes

    @property
    def survived(self) -> bool:
        return self.status == 0 and self.connections == 2 and self.key


def instructions(text: str) -> list[tuple[str, str]]:
    """(INSTRUCTION, arguments) per instruction, as BuildKit's parser gives them, except a heredoc's body, whose
    lines F1 reads as instructions: leading byte-order marks dropped, the text split at newlines only, every
    carriage return at a line's end trimmed, blank lines and comment lines dropped, inside a continuation too, and
    a line that ends, past spaces and tabs, in exactly one backslash joined to the next with nothing between
    (BuildKit's continuation pattern: two or more backslashes there end the line)."""
    out: list[tuple[str, str]] = []
    logical = ""
    for raw in text.lstrip("\ufeff").split("\n"):
        line = raw.rstrip("\r").rstrip(" \t")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.rstrip("\\")) == 1:
            logical += line[:-1]
            continue
        out.append(instruction(logical + line))
        logical = ""
    if logical.strip():
        out.append(instruction(logical))
    return out


def instruction(logical: str) -> tuple[str, str]:
    """(INSTRUCTION, arguments): the name ends at BuildKit's whitespace (`[\\t\\v\\f\\r ]+`), a tab included."""
    head, *rest = INSTRUCTION_END.split(logical.strip(), maxsplit=1)
    return head.upper(), rest[0].strip() if rest else ""


def statements(script: str) -> list[str]:
    """The script's simple commands, as written: split at `;`, `&&`, `||`, `|`, `&` and newlines outside quotes,
    `${...}` and `$(...)`; the `&` of a redirection's `>&` or `<&` is not a split. A `#` at a statement's start, or
    after an unescaped space or tab, begins a comment that runs to the end of its line, as in sh."""
    out: list[str] = []
    cur: list[str] = []
    quote = ""
    depth = 0
    escaped = False                                     # the last character kept was escaped by a backslash
    i = 0
    while i < len(script):
        c = script[i]
        nxt = script[i + 1] if i + 1 < len(script) else ""
        was_escaped, escaped = escaped, False
        if quote == "'":
            if c == "'":
                quote = ""
        elif quote:                                     # inside "..." or `...`
            if c == "\\" and nxt:
                cur.append(c)
                i += 1
                c = nxt
            elif c == quote:
                quote = ""
        elif c == "\\" and nxt:
            cur.append(c)
            i += 1
            c = nxt
            escaped = True
        elif c in "'\"`":
            quote = c
        elif c == "$" and nxt and nxt in "{(":
            depth += 1
            cur.append(c)
            i += 1
            c = nxt
        elif depth and c in "})":
            depth -= 1
        elif not depth and c == "#" and (not cur or (cur[-1] in " \t" and not was_escaped)):
            end = script.find("\n", i)
            i = len(script) if end < 0 else end
            continue
        elif not depth and (c in ";\n|" or (c == "&" and not (cur and cur[-1] in "<>"))):
            if c in "&|" and nxt == c:
                i += 1
            out.append("".join(cur).strip())
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    out.append("".join(cur).strip())
    return [s for s in out if s]


def url_in_substitution(stmt: str) -> bool:
    """Whether a URL starts after the statement's first command substitution opens (`$(` or a backquote, outside
    single quotes) and before its last `)` or backquote; with no closing after the opening, before the statement's
    end. This rule pairs no opening with a closing, so a `)` inside quotes cannot end its span early, and a URL
    that only sits between two substitutions is named too. Inside double quotes an escaped opening (`\\$(`) counts
    as well: it is escaped for this shell and opens a substitution in the shell the text is handed to
    (`sh -c "echo \\$(curl URL)"`), and Python's shlex keeps that backslash where sh removes it. Single-quoted text
    opens none, so an `sh -c '...'` script is read as a script instead."""
    quote = ""
    i = 0
    while i < len(stmt):
        c = stmt[i]
        nxt = stmt[i + 1] if i + 1 < len(stmt) else ""
        if quote == "'":
            if c == "'":
                quote = ""
        elif c == "\\" and nxt:
            if quote == '"' and (nxt == "`" or stmt.startswith("$(", i + 1)):
                return span_holds_url(stmt, i + 1)
            i += 2
            continue
        elif c == "'" and quote != '"':
            quote = "'"
        elif c == '"':
            quote = "" if quote == '"' else '"'
        elif c == "`" or (c == "$" and nxt == "("):
            return span_holds_url(stmt, i)
        i += 1
    return False


def span_holds_url(stmt: str, start: int) -> bool:
    """Whether a URL starts between `start` and the statement's last `)` or backquote (its end, if neither follows)."""
    end = max(stmt.rfind(")"), stmt.rfind("`"))
    return bool(URL.search(stmt[start:end] if end > start else stmt[start:]))


def command_index(words) -> int | None:
    """Where the command is: past any reserved word and NAME=value assignment."""
    for k, w in enumerate(words):
        if w not in KEYWORDS and not ASSIGNMENT.match(w):
            return k
    return None


def shell_script(args) -> str | None:
    """The script a shell is given with `-c`, in any cluster of short options (`-c`, `-ec`, `-euc`), or None.
    Long options are not clusters: `--norc` holds no `-c`, and bash's `--rcfile` and `--init-file` take the next
    word. So does each `-o` and `-O` of a cluster."""
    k = 0
    while k < len(args):
        a = args[k]
        if a in SHELL_WITH_ARGUMENT:
            k += 2
            continue
        if a.startswith("--") and a != "--":
            k += 1
            continue
        if len(a) > 1 and a[0] in "-+" and a != "--":
            takes = cluster_arguments(a)
            if "c" in a[1:]:
                return command_string(args[k + 1 + takes:])
            k += 1 + takes
            continue
        return None
    return None


def cluster_arguments(cluster: str) -> int:
    """How many of the next words a cluster of a shell's short options takes: one for each `o` (`-o pipefail`,
    `-eo pipefail`) and each of bash's `O`."""
    return sum(cluster[1:].count(ch) for ch in "oO")


def command_string(args) -> str | None:
    """The first operand after `-c`: clusters of short options may still come between (`sh -c -e 'script'`,
    `bash -c -O extglob 'script'`), and a lone `-` or `--` ends them. A long option there is one neither shell
    takes after `-c`, so F1 reads no script (None)."""
    k = 0
    while k < len(args):
        a = args[k]
        if a in ("-", "--"):
            return args[k + 1] if k + 1 < len(args) else None
        if a.startswith("--"):
            return None
        if len(a) > 1 and a[0] in "-+":
            k += 1 + cluster_arguments(a)
            continue
        return a
    return None


def script_fetches(script: str, where: str, run: int, prelude: tuple[str, ...] = ()) -> list[Fetch]:
    found: list[Fetch] = []
    assigned = list(prelude)
    for stmt in statements(script):
        try:
            words = shlex.split(stmt)
        except ValueError as exc:
            raise ValueError(f"{where} RUN #{run}: a command F1 cannot read ({exc}): {stmt[:160]}") from None
        if url_in_substitution(stmt):
            # Named, never read, and never replayed as an assignment: F2 would fetch the real URL.
            found.append(Fetch(where, run, tuple(assigned), stmt, tuple(words),
                               unreadable="a URL between a command substitution's opening and the statement's "
                                          "last closing, which F1 does not read"))
            continue
        k = command_index(words)
        if k is None:
            if words and all(ASSIGNMENT.match(w) for w in words):
                assigned.append(stmt)
            continue
        name = os.path.basename(words[k])
        if name in SHELLS:
            inner = shell_script(words[k + 1:])
            if inner is not None:
                found += script_fetches(inner, where, run, tuple(assigned))
                continue
        if name in TEXT_COMMANDS:
            continue
        if any(URL.search(w) for w in words[k:]):
            found.append(Fetch(where, run, tuple(assigned), stmt, tuple(words)))
    return found


def unreadable(text: str, where: str) -> list[str]:
    """What F1 names without reading it as a fetch: an ADD of a URL (BuildKit fetches it), a heredoc RUN and an
    `escape` parser directive."""
    problems: list[str] = []
    for raw in text.lstrip("\ufeff").split("\n"):    # the parser directives, before any other line
        m = DIRECTIVE.match(raw.strip())
        if not m:
            break
        if m.group(1).lower() == "escape":
            problems.append(f"an escape directive, which F1 cannot read: {where}: {raw.strip()}")
    runs = 0
    for ins, args in instructions(text):
        if ins == "RUN":
            runs += 1
            if heredoc_run(args):
                problems.append(f"a heredoc RUN, which F1 cannot read: {where} RUN #{runs}: {args[:120]}")
        elif ins == "ADD" and URL.search(args):
            problems.append(f"an ADD of a URL, a fetch F2 cannot run: {where}: ADD {args[:120]}")
    return problems


def exec_form(body: str) -> list | None:
    """The RUN's exec-form words, or None: Docker reads a body that is not a JSON list as shell form."""
    if not body.startswith("["):
        return None
    try:
        words = json.loads(body)
    except ValueError:
        return None
    return words if isinstance(words, list) and all(isinstance(w, str) for w in words) else None


def heredoc_run(args: str) -> bool:
    """Whether F1 names this RUN a heredoc RUN: BuildKit's heredoc pattern matches one of its words. The words are
    Python's shlex's, split at blanks with their quotes kept, and a word that is only `<<` is joined to the word
    after it (`sh << EOF`), as BuildKit's lexer keeps the blanks after `<<` in the word."""
    body = RUN_OPTIONS.sub("", args, count=1)
    if exec_form(body) is not None:
        return False
    try:
        words = shlex.split(body, posix=False)
    except ValueError:
        words = body.split()
    glued = [w + " " + words[k + 1] if HEREDOC_BARE.match(w) and k + 1 < len(words) else w
             for k, w in enumerate(words)]
    return any((m := HEREDOC.match(w)) and m.group(3) for w in glued)


def dockerfile_fetches(text: str, where: str) -> list[Fetch]:
    found: list[Fetch] = []
    runs = [args for ins, args in instructions(text) if ins == "RUN"]
    for n, args in enumerate(runs, 1):
        if heredoc_run(args):                           # named by unreadable()
            continue
        body = RUN_OPTIONS.sub("", args, count=1)
        words = exec_form(body)
        if words is not None:                           # the exec form: the same words, no shell around them
            body = shlex.join(words)
        found += script_fetches(body, where, n)
    return found


def dockerfiles() -> list:
    return [p for p in tracked("*")
            if p.name == "Dockerfile" or p.name.startswith("Dockerfile.") or p.name.endswith(".Dockerfile")]


def shipped() -> list[Fetch]:
    found: list[Fetch] = []
    for path in dockerfiles():
        found += dockerfile_fetches(path.read_text(encoding="utf-8"), path.relative_to(REPO).as_posix())
    return found


def census(text: str) -> collections.Counter:
    """Every URL token in the text, counted. It reads no syntax: an instruction, a heredoc's body and a comment are
    all text to it. A line continuation inside a token is removed first."""
    return collections.Counter(URL_TOKEN.findall(CONTINUATION.sub("", text)))


def fetched_urls(found: list[Fetch]) -> list[str]:
    """The URLs of the curls the reading found and F2 can be handed."""
    return sorted(w for f in found if f.command == "curl" and not f.unreadable for w in f.words if URL.search(w))


def census_problems(text: str, where: str, accounted: dict, fetched: list[str]) -> list[str]:
    problems: list[str] = []
    seen = census(text)
    for url, n in sorted(seen.items()):
        if url not in accounted:
            problems.append(f"{where}: a URL this module does not answer for ({url}, {n} in the file). If the build "
                            f"fetches it, it must be a curl F2 runs; either way, answer for it in ACCOUNTED")
        elif n != 1:
            problems.append(f"{where}: {url} is in the file {n} times, and ACCOUNTED answers for one")
    for url in sorted(set(accounted) - set(seen)):
        problems.append(f"{where}: ACCOUNTED answers for {url}, which the file does not hold")
    said = sorted(url for url, why in accounted.items() if why == FETCHED)
    if said != fetched:
        problems.append(f"{where}: ACCOUNTED calls {said} fetched, and the curls F1 reads fetch {fetched}")
    return problems


def dockerfile_problems(text: str, where: str, accounted: dict) -> list[str]:
    """Everything F1 holds against one Dockerfile's text: the reading's problems, the forms it names, the census's."""
    found = dockerfile_fetches(text, where)
    return f1_problems(found) + unreadable(text, where) + census_problems(text, where, accounted, fetched_urls(found))


def output_problem(words) -> str | None:
    """Why F2 cannot point this curl's output at its own file, or None: the output is a separate `-o PATH` or
    `--output PATH`, or stdout."""
    k = command_index(words)
    args = list(words[k + 1:]) if k is not None else []
    for j, w in enumerate(args):
        if w in ("-o", "--output"):
            if j + 1 >= len(args):
                return f"{w} names no path"
            continue
        if w.startswith("--expand-"):                   # curl 8.3.0: any option's value through a variable
            return f"{w} takes its value through curl's variable expansion, which F1 does not read"
        if w in OTHER_OUTPUTS or w.startswith("--output-dir=") or w.startswith("--remote-name"):
            return f"its output is chosen by {w}"
        if len(w) > 2 and w.startswith("--") and w not in EXACT_PREFIXES:
            full = sorted(name for name in OTHER_OUTPUTS if name.startswith(w))
            if full:                                    # curl takes an unambiguous prefix of a long option
                return f"{w} abbreviates a file-writing option ({', '.join(full)})"
        if REDIRECT.match(w):
            return f"its output is redirected ({w})"
        m = OTHER_FD_REDIRECT.match(w)
        if m:
            target = m.group(2) or (args[j + 1] if j + 1 < len(args) else "")
            if target != "/dev/null" and not target.startswith("&"):
                return f"it writes {target} (a redirect of descriptor {m.group(1)})"
            continue
        if WRITE_OUT_FILE in w:
            return f"its write-out writes a file ({w})"
        if ">" in w:                                    # sh reads a `>` inside a word as a redirect unless quoted,
            return f"a redirect inside the word {w}"    # and F1's words have lost their quotes
        if len(w) > 2 and w[0] == "-" and w[1] != "-":
            for ch in w[1:]:
                if ch in CLUSTER_OUTPUTS:
                    return f"its output is chosen inside the option cluster {w}"
                if ch in CLUSTER_ARGUMENT:
                    break                               # the rest of the cluster is that option's argument
    return None


def f1_problems(found: list[Fetch]) -> list[str]:
    problems: list[str] = []
    for f in found:
        if f.unreadable:
            problems.append(f"{f.unreadable}: {f.label()}")
        elif f.command != "curl":
            problems.append(f"a fetch F2 cannot run ({f.command or 'no command'}; F2 runs curl): {f.label()}")
        else:
            why = output_problem(f.words)
            if why:
                problems.append(f"a curl F2 cannot run ({why}): {f.label()}")
    return problems


# --- F2: the reset -------------------------------------------------------------------

class ResettingServer:
    """HTTP/1.1 on 127.0.0.1: the first connection is reset (RST) once its request is read; when `mid_body`, after
    the headers and half the body and a wait for them to be delivered. Every later connection is served KEY."""

    def __init__(self, mid_body: bool):
        self.mid_body = mid_body
        self.connections = 0
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="w4b-resetting-server", daemon=True)

    def __enter__(self) -> "ResettingServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=10)
        self.listener.close()

    def _serve(self) -> None:
        head = (b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
                b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(KEY))
        while not self._stop.is_set():
            try:
                conn, _addr = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(10)
                self.connections += 1
                try:
                    request = b""
                    while b"\r\n\r\n" not in request:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        request += chunk
                    if self.connections == 1:
                        if self.mid_body:
                            conn.sendall(head + KEY[: len(KEY) // 2])
                            time.sleep(HALF_DELIVERED_S)
                        # l_onoff 1, l_linger 0: the close below sends RST, not FIN.
                        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                        continue
                    conn.sendall(head + KEY)
                except OSError:
                    continue


def f2_host() -> str | None:
    """Why F2 cannot run on this host, or None."""
    if os.name != "posix":
        return (f"F2 runs the image's RUN lines under a POSIX sh, as the Linux container does; this host is "
                f"{os.name!r}")
    if not shutil.which("sh"):
        return "no sh on PATH"
    curl = shutil.which("curl")
    if not curl:
        return "no curl on PATH"
    proc = _proc.run([curl, "--version"], timeout=30, capture_output=True, text=True, encoding="utf-8",
                     errors="replace")
    m = re.match(r"curl (\d+)\.(\d+)", proc.stdout)
    if not m or (int(m.group(1)), int(m.group(2))) < RETRY_ALL_ERRORS_SINCE:
        return f"this host's curl predates --retry-all-errors (7.71): {proc.stdout.splitlines()[:1]}"
    return None


def exercise(fetch: Fetch, url: str, out, home) -> tuple[int, str]:
    """Run `fetch` as its RUN runs it, its URL replaced by `url` and its output by `out`: (exit status, stderr)."""
    why = output_problem(fetch.words)
    if why:
        return -1, f"F2 cannot run this curl: {why}"
    raw = LEADING_KEYWORDS.sub("", fetch.raw, count=1)
    k = command_index(fetch.words)
    if fetch.words[k] != "curl":                        # a curl by path would bypass the -q wrapper below
        raw, n = re.subn(r"(?<!\S)" + re.escape(fetch.words[k]) + r"(?!\S)", "curl", raw, count=1)
        if n != 1:
            return -1, f"F2 could not find the command {fetch.words[k]!r} as written"
    urls = [w for w in fetch.words if URL.search(w)]
    if len(urls) != 1:
        return -1, f"F2 runs a fetch of one URL; this one names {len(urls)}"
    # The URL as the RUN spells it (a quote or a backslash inside it would differ from the word): replaced, or
    # nothing runs. Otherwise F2 would hand curl the Dockerfile's real URL from the host that runs the suite.
    raw, n = re.subn(re.escape(urls[0]), lambda m: url, raw)
    if n != 1:
        return -1, f"F2 could not find the fetch's URL as written ({n} places)"
    words = list(fetch.words)
    flag = next((k for k, w in enumerate(words) if w in ("-o", "--output")), None)
    target = shlex.quote(str(out))
    if flag is None:
        raw = f"{raw} > {target}"
    else:
        raw, n = re.subn(r"(?<!\S)(-o|--output)(\s+)" + re.escape(words[flag + 1]) + r"(?!\S)",
                         lambda m: m.group(1) + m.group(2) + target, raw, count=1)
        if n != 1:
            return -1, "F2 could not find the fetch's output path as written"
    # The build's container has no proxy and no curlrc, so the child has neither. curl reads a curlrc from
    # CURL_HOME, XDG_CONFIG_HOME, HOME and, failing those, the account's passwd home; `-q` first skips them all.
    env = {**os.environ, "no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1",
           "HOME": str(home), "CURL_HOME": str(home), "XDG_CONFIG_HOME": str(home)}
    script = "\n".join(['curl() { command curl -q "$@"; }', *fetch.prelude, raw])
    proc = _proc.run(["sh", "-euc", script], timeout=120, capture_output=True, text=True, encoding="utf-8",
                     errors="replace", env=env, cwd=str(home))
    return proc.returncode, proc.stderr


def f2_outcomes(found: list[Fetch], tmp_path, curlrc: str | None = None) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for n, fetch in enumerate(f for f in found if f.command == "curl" and not f.unreadable):
        for arm, mid_body in ARMS:
            home = tmp_path / f"fetch{n}-{int(mid_body)}"
            home.mkdir()
            if curlrc is not None:                      # where curl looks first: CURL_HOME
                (home / ".curlrc").write_text(curlrc, encoding="utf-8")
            out = home / "out"
            with ResettingServer(mid_body) as server:
                status, err = exercise(fetch, f"http://127.0.0.1:{server.port}/media/keys/ACCC4CF8.asc", out, home)
            got = out.read_bytes() if out.is_file() else b""
            outcome = Outcome(fetch, arm, status, server.connections, len(got), err, key=(got == KEY))
            print(f"F2 {fetch.label()} | reset {arm}: exit {status}, {server.connections} connection(s), "
                  f"{len(got)} of {len(KEY)} bytes")
            outcomes.append(outcome)
    return outcomes


def f2_problems(outcomes: list[Outcome]) -> list[str]:
    return [f"{o.fetch.label()} does not survive a reset {o.arm}: exit {o.status} after {o.connections} "
            f"connection(s), {'the stand-in' if o.key else 'not the stand-in'} ({o.received} bytes); "
            f"curl said {o.err.strip()[-300:]!r}"
            for o in outcomes if not o.survived]


@pytest.fixture
def f2_host_ready():
    why = f2_host()
    if why:
        pytest.skip(why)


# F1c: a planted line -> what F1's one message about it must say.
F1C_NAMED = {
    "\nADD https://example.invalid/k /tmp/k\n": "an ADD of a URL",
    "\nRUN <<EOF\ncurl -fsSL https://example.invalid/k\nEOF\n": "a heredoc RUN",
    "\nRUN sh <<EOF\ncurl -fsSL https://example.invalid/k -o /tmp/k\nEOF\n": "a heredoc RUN",
    # A blank after the `<<`: BuildKit opens that heredoc too (since v0.22.0).
    "\nRUN sh << EOF\ncurl -fsSL https://example.invalid/k | sh\nEOF\n": "a heredoc RUN",
    "\nRUN bash << 'EOF'\ncurl -fsSL https://example.invalid/k -o /tmp/i.sh\nEOF\n": "a heredoc RUN",
    "\nRUN curl -fsSLo /tmp/k https://example.invalid/k\n": "inside the option cluster -fsSLo",
    "\nRUN curl -fsSL -O https://example.invalid/k\n": "chosen by -O",
    "\nRUN curl -fsSL -D /tmp/h -o /tmp/k https://example.invalid/k\n": "chosen by -D",
    "\nRUN curl -fsSL --dump-h /tmp/h -o /tmp/k https://example.invalid/k\n": "--dump-h abbreviates",
    "\nRUN curl -fsSL --expand-output '{{out}}' https://example.invalid/k\n": "variable expansion",
    "\nRUN curl -fsSL https://example.invalid/k >/tmp/k\n": "redirected",
    "\nRUN curl -fsSL>/tmp/k https://example.invalid/k\n": "a redirect inside the word -fsSL>/tmp/k",
    "\nRUN curl -fsSL -o /tmp/k https://example.invalid/k 2>/tmp/curl.err\n": "it writes /tmp/curl.err",
    "\nRUN curl -fsSL -o /tmp/k https://example.invalid/k 2>> /tmp/fetch.log\n": "it writes /tmp/fetch.log",
    "\nRUN curl -fsSL -w '%output{/tmp/w}%{http_code}' -o /tmp/k https://example.invalid/k\n": "its write-out",
    "\nRUN true \\\n# a comment line inside the continuation\n  && wget -q https://example.invalid/k\n": "(wget;",
    "\nRUN bash --norc -c 'wget -q https://example.invalid/k'\n": "(wget;",
    # No -c: bash runs a file of that name, so the statement is bash's own; read as -c, it would be the wget.
    "\nRUN bash --norc -x 'wget -q https://example.invalid/k'\n": "(bash;",
    "\nRUN sh -c -e 'wget -q https://example.invalid/k'\n": "(wget;",
    "\nRUN bash -c -O extglob 'wget -q https://example.invalid/k'\n": "(wget;",
    "\nRUN sh -c - 'wget -q https://example.invalid/k'\n": "(wget;",
    "\nRUN bash -eo pipefail -c 'wget -q https://example.invalid/k'\n": "(wget;",
    "\nRUN bash -ceo pipefail 'wget -q https://example.invalid/k'\n": "(wget;",
    "\nRUN bash --rcfile /etc/profile -c 'wget -q https://example.invalid/k'\n": "(wget;",
    "\nRUN [ -f /etc/os-release ] && wget -q https://example.invalid/k\n": "(wget;",
    # Two backslashes end the line: the next RUN is its own, and its wget is read. Three end it too, and that RUN
    # then ends in a lone backslash, which F1 cannot read.
    "\nRUN echo a \\\\\nRUN wget -q https://example.invalid/k\n": "(wget;",
    "\nRUN echo a \\\\\\\nRUN wget -q https://example.invalid/k\n": "a command F1 cannot read",
    '\nRUN echo "$(curl -fsSL https://example.invalid/k)" > /tmp/k\n': "a command substitution",
    '\nRUN K="$(curl -fsSL https://example.invalid/k)"\n': "a command substitution",
    "\nRUN echo $(curl -fsSL https://example.invalid/k) > /tmp/k\n": "a command substitution",
    "\nRUN K=$(curl -fsSL https://example.invalid/k)\n": "a command substitution",
    "\nRUN echo `curl https://example.invalid/k -o /tmp/k`\n": "a command substitution",
    # A `)` inside quotes does not end the span: F1 reads it as running to the statement's last `)`.
    "\nRUN echo $(curl \")\" https://example.invalid/k) > /tmp/k\n": "a command substitution",
    # The cost of pairing nothing: a URL that only sits between two substitutions is named too.
    "\nRUN echo \"$(date) https://example.invalid/k $(date)\" > /tmp/k\n": "a command substitution",
    # Escaped for the outer shell, inside double quotes: the shell the script is handed to runs it.
    "\nRUN bash -c \"echo \\$(curl -fsSL https://example.invalid/k) > /tmp/k\"\n": "a command substitution",
    "\nRUN bash -c \"K=\\\"\\$(curl -fsSL https://example.invalid/k)\\\"\"\n": "a command substitution",
    "\nRUN sh -c \"echo \\`curl -fsSL https://example.invalid/k\\` > /tmp/k\"\n": "a command substitution",
    # The quote tracking before the first opening: an apostrophe inside double quotes opens no single quote, an
    # escaped apostrophe opens none either, and a backquote after a closed `$(...)` still ends the span.
    "\nRUN echo \"Today's key: $(curl -fsSL https://example.invalid/k)\" > /tmp/k\n": "a command substitution",
    "\nRUN echo it\\'s $(curl -fsSL https://example.invalid/k)\n": "a command substitution",
    "\nRUN echo $(date) `curl -fsSL https://example.invalid/k`\n": "a command substitution",
}
# F1c: a URL the census's table does not hold, in a form -> the token the census must name. The census reads no
# form at all. The reading passes each of these seven without a word (its known gaps, pinned as they are):
F1C_CENSUS_ONLY = {
    "\nRUN sh -euc 'echo $(case a in a) curl -fsSL https://example.invalid/census;; esac) > /tmp/k'\n":
        "https://example.invalid/census",
    "\nRUN MSG='say \"hi' tee /log <<EOF > \"/tmp/out\"\ncurl -fsSL https://example.invalid/census | sh\nEOF\n":
        "https://example.invalid/census",
    "\nRUN bash -rcfile /etc/profile -c 'curl -fsSL https://example.invalid/census -o /tmp/k'\n":
        "https://example.invalid/census",
    "\nRUN sed -n 1p <(curl -fsSL https://example.invalid/census)\n": "https://example.invalid/census",
    "\nONBUILD RUN curl -fsSL https://example.invalid/census -o /tmp/k\n": "https://example.invalid/census",
    "\nCOPY <<EOF /tmp/list\nhttps://example.invalid/census\nEOF\n": "https://example.invalid/census",
    "\n# see https://example.invalid/census\n": "https://example.invalid/census",
}
# and reads or names each of these five as well:
F1C_CENSUS_ALSO = {
    "\nRUN curl -fsSL -o /tmp/k https:/\\\n/example.invalid/census\n": "https://example.invalid/census",
    "\nRUN sh << EOF\ncurl -fsSL https://example.invalid/census | sh\nEOF\n": "https://example.invalid/census",
    "\nRUN bash -c \"V=\\\"\\$(curl -fsSL https://example.invalid/census)\\\"\"\n": "https://example.invalid/census",
    "\nADD https://example.invalid/census /tmp/k\n": "https://example.invalid/census",
    "\nRUN curl -o /tmp/k ftp://example.invalid/census\n": "ftp://example.invalid/census",
}
# F1c: a planted line F1 must read as it is -> every word of its one curl, or None for no fetch at all.
F1C_READ = {
    "\nRUN curl -# -o /tmp/k https://example.invalid/k\n":
        ("curl", "-#", "-o", "/tmp/k", "https://example.invalid/k"),
    "\nRUN curl -fsSL https://example.invalid/k#x -o /tmp/k\n":
        ("curl", "-fsSL", "https://example.invalid/k#x", "-o", "/tmp/k"),
    "\nRUN curl -fsSL -o /tmp/k https://example.invalid/k 2>/dev/null\n":
        ("curl", "-fsSL", "-o", "/tmp/k", "https://example.invalid/k", "2>/dev/null"),
    "\nRUN curl -fsSL -o /tmp/k https://example.invalid/k 2>&1\n":
        ("curl", "-fsSL", "-o", "/tmp/k", "https://example.invalid/k", "2>&1"),
    "\nRUN curl -fsSL -HAccept:text/plain -o /tmp/k https://example.invalid/k\n":
        ("curl", "-fsSL", "-HAccept:text/plain", "-o", "/tmp/k", "https://example.invalid/k"),
    "\nRUN curl -fsSL -o /tmp/k https://example.inv\\\nalid/k\n":
        ("curl", "-fsSL", "-o", "/tmp/k", "https://example.invalid/k"),
    "\r\nRUN curl -fsSL -o /tmp/k https://example.inv\\\r\nalid/k\r\n":
        ("curl", "-fsSL", "-o", "/tmp/k", "https://example.invalid/k"),
    # BuildKit trims every carriage return and newline at a line's end, not one of each.
    "\nRUN curl -fsSL -o /tmp/k https://example.inv\\\r\r\nalid/k2\n":
        ("curl", "-fsSL", "-o", "/tmp/k", "https://example.invalid/k2"),
    # An instruction's name ends at a tab as at a space.
    "\nRUN\tcurl -fsSL -o /tmp/k https://example.invalid/k\n":
        ("curl", "-fsSL", "-o", "/tmp/k", "https://example.invalid/k"),
    # curl's own --cookie is a prefix of --cookie-jar and no abbreviation of it.
    "\nRUN curl -fsSL --cookie a=b -o /tmp/k https://example.invalid/k\n":
        ("curl", "-fsSL", "--cookie", "a=b", "-o", "/tmp/k", "https://example.invalid/k"),
    # Docker splits a Dockerfile at newlines only: a form feed is inside its line, and inside sh's word.
    "\nRUN curl -fsSL -o /tmp/k\x0c https://example.invalid/k\n":
        ("curl", "-fsSL", "-o", "/tmp/k\x0c", "https://example.invalid/k"),
    # An escaped space, and a no-break space, before a # are not a comment's start.
    "\nRUN curl -fsSL -o /tmp/k https://example.invalid/k\\ #x\n":
        ("curl", "-fsSL", "-o", "/tmp/k", "https://example.invalid/k #x"),
    "\nRUN curl -fsSL -o /tmp/k https://example.invalid/k\u00a0#x\n":
        ("curl", "-fsSL", "-o", "/tmp/k", "https://example.invalid/k\u00a0#x"),
    # A substitution beside a URL, or inside one, is no URL inside a substitution.
    "\nRUN sh -euc 'A=$(dpkg --print-architecture); curl -fsSL -o /tmp/k https://example.invalid/k'\n":
        ("curl", "-fsSL", "-o", "/tmp/k", "https://example.invalid/k"),
    "\nRUN curl -fsSL -o /tmp/k \"https://example.invalid/$(uname -m)/k\"\n":
        ("curl", "-fsSL", "-o", "/tmp/k", "https://example.invalid/$(uname -m)/k"),
    "\nRUN echo \"deb [arch=$(dpkg --print-architecture)] https://example.invalid/apt main\" > /tmp/l\n": None,
    # Not heredocs to BuildKit: a here-string, and a quoted marker.
    "\nRUN cat <<<\"x\" > /tmp/x\n": None,
    "\nRUN echo \"<<EOF\" > /tmp/x\n": None,
    "\nRUN true # wget https://example.invalid/k\n": None,
}


def planted_run(curl_line: str) -> str:
    """092b0c9's RUN with its curl line replaced by `curl_line` (the same continuation layout)."""
    old = ("             curl -fsSL ${CA:+--cacert $CA} -o "
           "/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \\\n"
           "                  https://www.postgresql.org/media/keys/ACCC4CF8.asc; \\\n")
    assert PRE_FIX_RUN.count(old) == 1
    return PRE_FIX_RUN.replace(old, f"             {curl_line}; \\\n")


# --- the tests -----------------------------------------------------------------------

def test_f1_every_fetch_a_run_names_by_url_is_a_curl_f2_can_run():
    assert dockerfiles(), "no tracked Dockerfile: F1's population is empty"
    found = shipped()
    for f in found:
        print(f"F1 {f.command}: {f.label()}")
    assert found, "F1 found no fetch in any tracked Dockerfile, so F2 would pass on nothing"
    problems, names = [], set()
    for path in dockerfiles():
        text, where = path.read_text(encoding="utf-8"), path.relative_to(REPO).as_posix()
        names.add(where)
        print(f"F1 census {where}: {dict(census(text))}")
        problems += dockerfile_problems(text, where, ACCOUNTED.get(where, {}))
    problems += [f"ACCOUNTED answers for {name}, which is no tracked Dockerfile"
                 for name in sorted(set(ACCOUNTED) - names)]
    assert not problems, problems


def test_f1c_a_fetch_f1s_code_does_not_name_is_named():
    base = DOCKERFILE.read_text(encoding="utf-8")
    assert f1_problems(dockerfile_fetches(base, "deploy/Dockerfile")) == []
    assert unreadable(base, "deploy/Dockerfile") == []
    # The census: a URL the table does not hold is named by its token, whatever form holds it.
    answers = ACCOUNTED["deploy/Dockerfile"]

    def counted(text: str, accounted: dict = answers) -> list[str]:
        """What F1 itself would hold against this text, the census's lines among it."""
        return [p for p in dockerfile_problems(text, "planted", accounted) if "ACCOUNTED" in p]

    assert counted(base) == []
    reading = len(dockerfile_fetches(base, "planted"))
    for table, silent in ((F1C_CENSUS_ONLY, True), (F1C_CENSUS_ALSO, False)):
        for plant, token in table.items():
            problems = counted(base + plant)
            print(f"F1c census {plant.strip().splitlines()[0]!r}: {[p[:90] for p in problems]}")
            assert any(f"does not answer for ({token}, 1 in the file)" in p for p in problems), (plant, problems)
            found = dockerfile_fetches(base + plant, "planted")
            read = f1_problems(found) + unreadable(base + plant, "planted")
            assert (len(found) == reading and read == []) is silent, (plant, found[reading:], read)
    key = next(url for url, why in answers.items() if why == FETCHED)
    assert any(f"{key} is in the file 2 times" in p for p in counted(base + f"\nRUN echo {key}\n"))
    label = "https://github.com/Slickfish/talima"
    assert base.count(label) == 1
    assert counted(base.replace(label, "")) == [f"planted: ACCOUNTED answers for {label}, which the file does not hold"]
    text_only = next(url for url, why in answers.items() if why.startswith("text: the apt source"))
    said = counted(base, {**answers, text_only: FETCHED})
    assert len(said) == 1 and f"'{text_only}'" in said[0] and "the curls F1 reads fetch" in said[0], said
    assert sum("does not answer for" in p for p in counted(base, {})) == len(answers)   # a Dockerfile with no entry
    # The class F1's code does not name: fetches by other tools.
    unnamed = base + (
        "\nRUN wget -q -O /tmp/k https://example.invalid/k\n"
        "RUN python3 -c \"import urllib.request as u; u.urlretrieve('https://example.invalid/k', '/tmp/k')\"\n"
        "RUN sh -euc 'X=1; wget -qO- https://example.invalid/k > /tmp/k'\n")
    problems = f1_problems(dockerfile_fetches(unnamed, "planted"))
    print("\n".join(problems))
    assert sorted(p.split("(", 1)[1].split(";", 1)[0] for p in problems) == ["python3", "wget", "wget"], problems
    # The forms F1's rules name, each planted alone after the shipped file, with the message it must produce.
    for plant, says in F1C_NAMED.items():
        try:
            problems = f1_problems(dockerfile_fetches(base + plant, "planted")) + unreadable(base + plant, "planted")
        except ValueError as exc:                       # a command F1 cannot read is named by raising
            problems = [str(exc)]
        print(f"F1c named {plant.strip().splitlines()[0]!r}: {problems}")
        assert len(problems) == 1 and says in problems[0], (plant, problems)
    # A parser directive is read only before any other line, a byte-order mark dropped first.
    for text, named in (("# escape=`\nFROM python:3.11-slim\nRUN true\n", True),
                        ("\ufeff# escape=`\nFROM python:3.11-slim\nRUN true\n", True),
                        ("# syntax=docker/dockerfile:1\n# escape=`\nFROM python:3.11-slim\nRUN true\n", True),
                        ("# a plain comment\n# escape=`\nFROM python:3.11-slim\nRUN true\n", False),
                        (base + "\n# escape=`\nRUN true\n", False)):
        got = [p for p in unreadable(text, "planted") if "an escape directive" in p]
        assert bool(got) is named, (text[:60], got)
    # The forms F1 must read as they are: a curl F2 can run, with every one of its words, or no fetch at all.
    for plant, keeps in F1C_READ.items():
        found = dockerfile_fetches(base + plant, "planted")[len(dockerfile_fetches(base, "deploy/Dockerfile")):]
        problems = f1_problems(found) + unreadable(base + plant, "planted")
        print(f"F1c read {plant.strip().splitlines()[0]!r}: {[f.words for f in found]} {problems}")
        assert problems == [], (plant, problems)
        if keeps is None:
            assert found == [], (plant, found)
        else:
            assert len(found) == 1 and found[0].command == "curl", (plant, found)
            assert found[0].words == keeps, (plant, found[0].words, keeps)


def test_f2_every_fetch_survives_a_reset_connection(f2_host_ready, tmp_path):
    found = [f for f in shipped() if f.command == "curl" and not f.unreadable]
    assert found, "no curl fetch for F2 to run"
    problems = f2_problems(f2_outcomes(found, tmp_path))
    assert not problems, problems


def test_f2p_the_fetch_as_092b0c9_shipped_it_dies_on_a_reset(f2_host_ready, tmp_path):
    found = dockerfile_fetches(PRE_FIX_RUN, "092b0c9's deploy/Dockerfile")
    assert [f.command for f in found] == ["curl"], found
    outcomes = f2_outcomes(found, tmp_path)
    assert len(f2_problems(outcomes)) == 2, outcomes
    assert [(o.status, o.connections) for o in outcomes] == [(56, 1), (56, 1)], outcomes
    first, second = outcomes
    assert (first.received, second.received) == (0, len(KEY) // 2), outcomes


def test_f2c_a_retry_that_leaves_a_failed_receive_out_dies_the_same_way(f2_host_ready, tmp_path):
    planted = PRE_FIX_RUN.replace("curl -fsSL ", "curl -fsSL --retry 5 ", 1)
    assert planted != PRE_FIX_RUN
    found = dockerfile_fetches(planted, "planted")
    assert [f.command for f in found] == ["curl"], found
    outcomes = f2_outcomes(found, tmp_path)
    assert [(o.status, o.connections, o.survived) for o in outcomes] == [(56, 1, False), (56, 1, False)], outcomes


def test_f2r_a_retry_into_a_pipe_or_a_redirect_keeps_a_failed_attempts_bytes(f2_host_ready, tmp_path):
    planted = planted_run("curl -fsSL --retry 5 --retry-all-errors https://www.postgresql.org/media/keys/ACCC4CF8.asc"
                          " | gpg --dearmor -o /usr/share/keyrings/planted.gpg")
    found = dockerfile_fetches(planted, "planted")
    assert [f.command for f in found] == ["curl"] and f1_problems(found) == [], found
    first, second = f2_outcomes(found, tmp_path)
    assert first.survived, first
    assert (second.status, second.connections, second.key) == (0, 2, False), second
    assert second.received == len(KEY) + len(KEY) // 2, second
    assert f2_problems([first, second]) and len(f2_problems([first, second])) == 1


def test_f2v_retry_flags_through_an_assignment_survive(f2_host_ready, tmp_path):
    planted = planted_run('R="--retry 5 --retry-all-errors"; curl -fsSL $R ${CA:+--cacert $CA} '
                          '-o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc '
                          'https://www.postgresql.org/media/keys/ACCC4CF8.asc')
    found = dockerfile_fetches(planted, "planted")
    assert [f.command for f in found] == ["curl"], found
    assert any(p.startswith("R=") for p in found[0].prelude), found[0].prelude
    outcomes = f2_outcomes(found, tmp_path)
    assert f2_problems(outcomes) == [], outcomes


def test_f2q_a_curlrc_where_curl_looks_first_is_skipped(f2_host_ready, tmp_path):
    found = dockerfile_fetches(PRE_FIX_RUN, "092b0c9's deploy/Dockerfile")
    outcomes = f2_outcomes(found, tmp_path, curlrc="retry = 5\nretry-all-errors\n")
    assert [(o.status, o.connections) for o in outcomes] == [(56, 1), (56, 1)], outcomes


def test_f2u_a_url_f2_cannot_find_as_written_is_refused_and_nothing_runs(tmp_path):
    planted = "FROM python:3.11-slim\nRUN curl -fsSL -o /tmp/k https://example.invalid/k\\&x=1\n"
    found = dockerfile_fetches(planted, "planted")
    assert [f.command for f in found] == ["curl"] and f1_problems(found) == [], found
    assert found[0].words[-1] == "https://example.invalid/k&x=1", found[0].words    # the word, not the spelling
    status, err = exercise(found[0], "http://127.0.0.1:9/k", tmp_path / "out", tmp_path)
    assert status == -1 and "could not find the fetch's URL as written" in err, (status, err)
    assert list(tmp_path.iterdir()) == []
