#!/usr/bin/env python3
"""W4 part 4 — the simulated UAT (RUN_CHARTER.md D6; run/specs/W4.md D20 to D23).

    python scripts/uat_simulate.py                         every section; the report at
                                                           reports/uat_simulation_<platform>.md
    python scripts/uat_simulate.py --report PATH           the report elsewhere
    python scripts/uat_simulate.py --database-url URL      the hosted section's testers on Postgres
                                                           (a superuser URL: their databases are made
                                                           by deploy/postgres-init.sh and dropped after)
    python scripts/uat_simulate.py --sections console,mcp  only those sections (and what they need)
    python scripts/uat_simulate.py --work DIR [--keep-work]   its folder (new or empty), and keep it

It drives the product from outside, as a tester and an operator would. It
starts the product's own entry points as child processes and talks HTTP and
stdio to them: `start.py --serve` (the console, and each hosted tester's
process), `python -m backend.mcp` (through `scripts/bundle_smoke.py`'s stdio
client), `scripts/backup.py`, `scripts/restore_verify.py`, the operator scripts and
`scripts/nexus_verify.py` (copied alone into an empty folder and run
isolated, as a stranger runs it). It imports no store module and opens no
store (it is no freeze driver: tests/_fz_trace.py); what it knows of a store
it reads from the product's routes, tools and scripts. One act is not
reached through an entry point, because none reaches it yet: a tombstone
(W8-B3; W14 decides its route). The verification section makes it with
`tests/_uat_tombstone.py`, a child run on a restored copy nothing serves,
and its row says so. The model is the stub provider
everywhere (`LLM_BACKEND=stub`): the real-model run is the operator's
(docs/UAT_GUIDE.md).

It works in a folder of its own (a new temporary one unless --work names a
new or empty one; removed afterwards unless --keep-work) and never touches
the checkout's data/, the operator's .env or a real instance: every child
gets NEXUS_ENV_ROOT pointing at one of the simulation's own install folders,
whose .env it writes from .env.example with its scripted values (the
"install settings" step), and an environment with every NEXUS_*, provider,
libpq (PG*) and uvicorn setting of the caller's removed, so only that .env
configures it. Its own .env search is pointed at an empty folder of its
own too, so nothing of the operator's is read.

Sections, each a list of steps. A step is one row of the report: the
section, who (tester or operator), what they do, the scripted input, what
was expected, what was seen, and PASS, FAIL or NOT RUN with its reason.
  1. console    install settings; first run (/healthz 200, learning frozen and
                said so); three /chat turns read to their `done` frame; the
                assertion offer (kb_pressure_alert assertion) and its confirm,
                the same unconfirmed refused and recorded; recall; as-of at an
                instant with an offset, and a bare date refused by name;
                verify; status.
  2. verification  a held note committed (unconfirmed refused and recorded,
                then confirmed) and sealed; a credential for a console turn
                verified by nexus-verify with no store access; a tampered
                one failing by name; two fields disclosed, the rest as
                commitments; a planted unsalted hash named; then another
                session (a backup restored into a fresh data root, so a new
                key and the rotation recorded): a tombstoned record's
                credential verifies, and the first session's commitment is
                revealed and verified offline.
  3. mcp        `python -m backend.mcp` with its own data folder and owner,
                through a scripted stdio client: the nine tools, the four
                bundle_smoke.py never calls included; the coverage counters as
                nexus_status reports them, all three exercised.
  4. hosted     three testers, each a `start.py --serve` child in proxy mode
                with its own port, data root and owner, behind a streaming
                loopback forwarder of the simulation's own that does what an
                identity proxy does (removes a client's copy of the identity
                header, sets it to the tester's login, adds X-Forwarded-For,
                never buffers the stream); the refused cases (another login,
                the owner's own login in another case, a body accountable
                that differs, no owner, no identity, a cross-site form POST
                (W4's close: it writes nothing, and the same request from the
                console's own origin then finds its work unit first seen), a
                non-loopback peer with and without X-Forwarded-For). On
                SQLite stores, or with --database-url on databases made by
                deploy/postgres-init.sh itself, dropped at the end.
  5. backup     scripts/backup.py on the console's running store;
                scripts/restore_verify.py on it; then the backup restored INTO
                SERVICE as docs/OPERATOR_RUNBOOK.md says (the console stopped,
                the backup's files into a fresh data root, a console started on
                it), and every record the console had is there and verifies.
  6. upgrade    a copy of tests/fixtures/pre_run_data/ served by a console:
                every record its MANIFEST.json names verifies, and the record
                hashes, the chain head, the governance verdict and the as-of
                counts are the manifest's.
  7. uninstall  every process stopped; the install's settings removed; the
                data roots kept (NEXUS never deletes a store); a fresh install
                on the kept console data root serves the same records, each
                verifying (on win32 a forced stop leaves the lock to be taken
                over and logged: the takeover is read from kb.db.lock.log).
  8. operator   the instance scripts the runbook names, each on a stopped copy
                of the console store in its dry or read form: exit 0, its first
                line names the store it opened (law 15), the copy unchanged.
  9. actions    (W10b) a console of its own, its search pointed at a loopback
                stand-in and NEXUS_SIGN_REQUESTS=on: a search the tester
                consents to is one action on the turn's record, the request it
                sent signed (RFC 9421); sealed, its receipt verified by
                nexus-verify with no store access; the stand-in, holding the
                signed request, retrieves its authorisation with no token and
                verifies it, the accountable identity never named; a tampered
                receipt failing by name. The mcp section reports an action
                (nexus_record_action), stored client_reported.

The run's exit code is 0 when every step passed; 1 on any FAIL and on any
required step NOT RUN (the report's last lines say which and why); 2 for a
usage error (an unknown section, a --work folder that is not new or empty).
The report lists, in one section, every scripted input a tester or operator
supplied (settings, questions, sentences, confirm flags, instants, logins,
commands), and the coverage counters as the product reports them. The
verdict lines are the script's own (`PASS`, `FAIL`, `NOT RUN` per step and
`uat-simulate:` summary lines): CI prints them, never verdicts of its own.

Expect a few minutes; on Windows the children start slower. Nothing here is
a test of the model: every answer is the stub's one fixed text.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from _console import force_utf8_stdio  # noqa: E402
from _env import auth_header, load_env  # noqa: E402
import bundle_smoke  # noqa: E402  (its stdio JSON-RPC client, Server: W2's smoke, reused for the MCP section)
from _interpreter import this_interpreter  # noqa: E402  (W4b: a child started as this interpreter is it)
import nexus_verify  # noqa: E402  (W9: its canonical form, to plant an unsalted hash; it imports no NEXUS module)

PLATFORM = sys.platform
DEFAULT_REPORT = ROOT / "reports" / f"uat_simulation_{PLATFORM}.md"
START = ROOT / "start.py"
BACKUP = ROOT / "scripts" / "backup.py"
RESTORE_VERIFY = ROOT / "scripts" / "restore_verify.py"
VERIFIER = ROOT / "scripts" / "nexus_verify.py"
TOMBSTONE_HELPER = ROOT / "tests" / "_uat_tombstone.py"   # W9: the stand-in for W14's route (W8-B3)
HOLD_READ_HELPER = ROOT / "tests" / "_uat_hold_read.py"   # W10c: another program reading the interceptor's store
KEY_FILE = "instance_key.json"   # backend/memory/instance_key.py's KEY_FILE (pinned equal by U3)
INIT_SCRIPT = ROOT / "deploy" / "postgres-init.sh"
FIXTURE = ROOT / "tests" / "fixtures" / "pre_run_data"
ENV_EXAMPLE = ROOT / ".env.example"
EXIT_PASS, EXIT_FAIL, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130
LOOPBACK = "127.0.0.1"
SERVE_TIMEOUT_S = 300.0          # a cold start on a windows runner, five at once in the hosted section
SCRIPT_TIMEOUT_S = 900.0
# The identity header proxy mode reads unless NEXUS_IDENTITY_HEADER names another: the testers' .env keep
# the line as .env.example ships it (backend/identity.py's default).
IDENTITY_HEADER = "Tailscale-User-Login"
# A documentation address (RFC 5737). Only the route a datagram socket would take to it is read: a
# connect on a datagram socket sends nothing.
ROUTE_PROBE = ("192.0.2.1", 9)

# --- the script: every input a tester or an operator supplies ------------------------------------------
QUESTION_1 = "What does interval training improve?"
SENTENCE = "The dose was 600mg in the second cohort."
FOLLOW_UP = "What was the dose in the second cohort?"
MCP_OWNER = "mcp-tester@uat.example.test"
MCP_SENTENCE = "The mean placebo-corrected QTc change at 600mg was 11.4ms."
MCP_QUESTION = "What was the mean placebo-corrected QTc change at 600mg?"
MCP_ANSWER = "The mean placebo-corrected QTc change at 600mg was 11.4ms, from the thorough QT study."
MCP_MODEL = "uat-simulate client"
MCP_HANDLE_TIMEOUT_S = 8         # NEXUS_MCP_HANDLE_TIMEOUT: short, so an unrecorded retrieval expires in the run
TESTERS = (("t1", "tester1@uat.example.test"), ("t2", "tester2@uat.example.test"),
           ("t3", "tester3@uat.example.test"))
OTHER_LOGIN = "intruder@uat.example.test"
NON_LOOPBACK_OWNER = "tester4@uat.example.test"
HOSTED_QUESTION = "What should {login} know before the next review?"
OFFSET = timezone(timedelta(hours=8))   # the as-of instant is written with an offset (a +08:00 reader)
HELD_NOTE = "Held before the review: the QTc change at 600 mg was 11.4 ms.\r\nA second line, its CRLF kept."
SEARCH_QUESTION = "what is the latest on QTc studies"   # W10b: a temporal word, so the console proposes a search
SEARCH_BODY = json.dumps({"results": [{"title": "Thorough QT studies", "url": "https://example.org/qt",
                                       "content": "a stand-in result", "engine": "stand-in"}]}).encode("utf-8")
REPORTED_ACTION = {"kind": "file_write", "target": "notes/qtc.md", "status": "ok"}
TOMBSTONE_ACTOR = "steward@uat.example.test"
TOMBSTONE_REASON = "the tester asked for this turn's content to be disposed of"

SECRET_SHOWN = "(generated by the run: {how}; not shown)"
NOT_RUN = "NOT RUN"


# --- the record of a run: values, replaced, never mutated (CLAUDE.md Law 1) -----------------------------

@dataclass(frozen=True)
class Step:
    id: str
    who: str
    what: str
    scripted_input: str
    expected: str
    seen: str
    result: str                  # PASS | FAIL | NOT RUN
    reason: str = ""             # NOT RUN's reason
    required: bool = True


@dataclass(frozen=True)
class Input:
    who: str
    name: str
    shown: str                   # the value, or how a generated secret was made (never the secret)
    used_in: tuple = ()


@dataclass(frozen=True)
class Ledger:
    steps: tuple = ()
    inputs: tuple = ()
    coverage: tuple = ()         # (where, what the product reported)
    kept: tuple = ()             # (what, path): the data roots the run made, never deleted by NEXUS
    notes: tuple = ()

    def with_step(self, step: Step) -> "Ledger":
        return dataclasses.replace(self, steps=self.steps + (step,))

    def with_input(self, who: str, name: str, shown: str, step_id: str) -> "Ledger":
        for i, x in enumerate(self.inputs):
            if (x.who, x.name, x.shown) == (who, name, shown):
                used = x.used_in if step_id in x.used_in else x.used_in + (step_id,)
                return dataclasses.replace(self, inputs=self.inputs[:i] + (dataclasses.replace(x, used_in=used),)
                                           + self.inputs[i + 1:])
        return dataclasses.replace(self, inputs=self.inputs + (Input(who, name, shown, (step_id,)),))

    def failed(self) -> list:
        return [s for s in self.steps if s.result == "FAIL"]

    def unrun_required(self) -> list:
        return [s for s in self.steps if s.result == NOT_RUN and s.required]


@dataclass(frozen=True)
class Reply:
    status: int
    text: str

    def json(self):
        try:
            return json.loads(self.text)
        except ValueError:
            return None


# --- the children ----------------------------------------------------------------------------------------

class Child:
    """A process the simulation started, its output read on a thread the
    whole time it runs (W3b-D13: a wait that does not read the pipe blocks on
    Windows; tests/test_w4_identity.py's _ReadingChild), waited for with
    plain sockets, stopped with SIGINT on POSIX (uvicorn's Ctrl+C: start.py
    execs the server there) and as a tree on Windows (start.py waits on the
    server there; the forced stop leaves the store's lock to be taken over)."""

    def __init__(self, label: str, argv: list, env: dict, cwd: Path = ROOT):
        self.label, self.argv = label, argv
        argv, env = this_interpreter(argv, env)
        self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        self.lines: list = []
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        for line in self.proc.stdout:
            self.lines.append(line)

    def wait_serving(self, host: str, port: int, timeout: float = SERVE_TIMEOUT_S) -> str:
        """'serving' once /healthz answers 200, 'exited' when the process
        ends first, 'neither' at the timeout."""
        deadline = time.monotonic() + timeout
        http_ = Http(host, port)
        while time.monotonic() < deadline:
            try:
                if http_.call("GET", "/healthz", timeout=3.0).status == 200:
                    return "serving"
            except (OSError, http.client.HTTPException):
                pass
            if self.proc.poll() is not None:
                return "exited"
            time.sleep(0.25)
        return "neither"

    def wait(self, timeout: float = SCRIPT_TIMEOUT_S) -> "int | None":
        try:
            code = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        self._reader.join(timeout=30)
        return code

    def stop(self) -> "int | None":
        if self.proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
            else:
                import signal
                self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=30)
        self._reader.join(timeout=30)
        return self.proc.returncode

    def output(self) -> str:
        return "".join(self.lines)

    def first_line(self) -> str:
        return (self.output().splitlines() or [""])[0]

    def tail(self, n: int = 12) -> str:
        return " | ".join(line.strip() for line in self.output().strip().splitlines()[-n:])


class Http:
    """One NEXUS process's routes over http.client (which reads no proxy
    setting from the environment)."""

    def __init__(self, host: str, port: int, headers: "dict | None" = None):
        self.host, self.port, self.headers = host, port, dict(headers or {})

    def call(self, method: str, path: str, *, body=None, headers: "dict | None" = None,
             timeout: float = 120.0) -> Reply:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            data = None if body is None else json.dumps(body).encode("utf-8")
            sent = {**self.headers, **(headers or {})}
            if data is not None:
                sent["Content-Type"] = "application/json"
            conn.request(method, path, body=data, headers=sent)
            resp = conn.getresponse()
            return Reply(resp.status, resp.read().decode("utf-8", errors="replace"))
        finally:
            conn.close()


def sse_frames(text: str) -> list:
    """The stream's frames, (event, data) in order, as /chat sends them."""
    out = []
    for block in text.split("\n\n"):
        event = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        if event:
            try:
                out.append((event, json.loads(data) if data else None))
            except ValueError:
                out.append((event, data))
    return out


class Forwarder:
    """A streaming loopback forwarder that does what an identity proxy does
    (run/specs/W4.md D21): for each request a client sends it, it removes
    every copy of the identity header the client sent, sets the header to
    its tester's login, adds the client's address to X-Forwarded-For, and
    relays the response as it arrives, piece by piece, never buffering the
    stream. Every other header the client sent passes through as it came
    (Sec-Fetch-Site and Origin among them, which W4's cross-site step
    sends), but the hop-by-hop ones. One request per connection (it asks the
    upstream to close)."""

    def __init__(self, upstream_port: int, header: str, login: "str | None"):
        self.upstream_port, self.header, self.login = upstream_port, header, login
        self.sock = socket.create_server((LOOPBACK, 0))
        self.port = self.sock.getsockname()[1]
        self.relayed = 0
        self.pieces = 0
        self.stripped = 0
        self._closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self._closed:
            try:
                client, peer = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve_one, args=(client, peer), daemon=True).start()

    def _serve_one(self, client: socket.socket, peer) -> None:
        try:
            client.settimeout(120)
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 65536:
                chunk = client.recv(4096)
                if not chunk:
                    return
                head += chunk
            head, _, body = head.partition(b"\r\n\r\n")
            request_line, *header_lines = head.decode("latin-1").split("\r\n")
            kept, forwarded, length = [], [], 0
            for line in header_lines:
                name, _, value = line.partition(":")
                key = name.strip().lower()
                if key == self.header.lower():
                    self.stripped += 1            # a client's own copy: never passed on
                    continue
                if key == "x-forwarded-for":
                    forwarded.append(value.strip())
                    continue
                if key in ("connection", "keep-alive", "proxy-connection"):
                    continue
                if key == "content-length":
                    length = int(value.strip() or 0)
                kept.append(line)
            while len(body) < length:
                chunk = client.recv(65536)
                if not chunk:
                    break
                body += chunk
            if self.login is not None:
                kept.append(f"{self.header}: {self.login}")
            kept.append("X-Forwarded-For: " + ", ".join(forwarded + [peer[0]]))
            kept.append("Connection: close")
            upstream = socket.create_connection((LOOPBACK, self.upstream_port), timeout=120)
            try:
                upstream.sendall(("\r\n".join([request_line, *kept]) + "\r\n\r\n").encode("latin-1") + body)
                self.relayed += 1
                while True:
                    piece = upstream.recv(65536)
                    if not piece:
                        break
                    self.pieces += 1
                    client.sendall(piece)
            finally:
                upstream.close()
        except OSError:
            pass
        finally:
            client.close()

    def close(self) -> None:
        self._closed = True
        self.sock.close()


def free_port() -> int:
    """A loopback port bound and closed: nothing listens there (another
    process could take it before the child binds; not seen, and the window
    every test in the suite that starts a child has)."""
    with socket.socket() as s:
        s.bind((LOOPBACK, 0))
        return s.getsockname()[1]


def host_address() -> "tuple[str | None, str]":
    """(the host's own non-loopback IPv4 address, how it was found) or
    (None, why there is none): the source address of the route a datagram
    socket would take to a documentation address (nothing is sent), else
    the addresses the host's own name resolves to."""
    tried = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(ROUTE_PROBE)
            candidates = [s.getsockname()[0]]
    except OSError as e:
        candidates = []
        tried.append(f"no route to read an address from ({type(e).__name__}: {e})")
    try:
        candidates += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError as e:
        tried.append(f"the host's name does not resolve ({type(e).__name__}: {e})")
    for value in candidates:
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            continue
        if not (ip.is_loopback or ip.is_unspecified or ip.is_link_local or ip.is_multicast):
            return value, "the route's source address" if value == candidates[0] and not tried else "the host's name"
        tried.append(f"{value} is not usable (loopback, unspecified or link-local)")
    return None, "; ".join(tried) or "no address found"


# --- install settings ------------------------------------------------------------------------------------

SETTING_PREFIXES = ("NEXUS_", "PG", "UVICORN_")
CALLER_SETTINGS = ("LLM_BACKEND", "OLLAMA_URL", "LM_STUDIO_URL", "SEARXNG_URL", "WEB_CONCURRENCY",
                   "FORWARDED_ALLOW_IPS", "POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD")


def example_keys() -> set:
    keys = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^#?\s*([A-Z][A-Z0-9_]*)=", line.strip())
        if m:
            keys.add(m.group(1))
    return keys


def child_environment(install: "Path | None", tmp: Path, **extra: str) -> dict:
    """The caller's environment, copied, with every setting that would
    configure NEXUS or its store removed (NEXUS_*, the providers', libpq's
    PG*, uvicorn's, any *DATABASE_URL*), so only the install's .env
    configures the child; the
    .env search pointed at the install folder (or an empty one); temporary
    files under the run's own folder; bytecode never written into the
    checkout; and the child's output UTF-8 and unbuffered."""
    keys = example_keys()
    # A database URL the caller holds (CI hands the superuser's in its environment) never reaches a child.
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(SETTING_PREFIXES) and k.upper() not in CALLER_SETTINGS and k not in keys
           and "DATABASE_URL" not in k.upper()}
    tmp.mkdir(parents=True, exist_ok=True)
    env.update(NEXUS_ENV_ROOT=str(install if install is not None else tmp / "no-env"), PYTHONDONTWRITEBYTECODE="1",
               PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", TMPDIR=str(tmp), TEMP=str(tmp), TMP=str(tmp))
    (tmp / "no-env").mkdir(exist_ok=True)
    env.update(extra)
    return env


def write_install(folder: Path, settings: dict) -> tuple[Path, list]:
    """`folder/.env` written as an operator writes it: a copy of
    .env.example with the scripted values on their lines (a line .env.example
    leaves commented or lacks is added at the end, under a header saying
    so). Returns the file and the lines that differ from the example."""
    folder.mkdir(parents=True, exist_ok=True)
    lines, placed = [], set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if m and m.group(1) in settings:
            lines.append(f"{m.group(1)}={settings[m.group(1)]}")
            placed.add(m.group(1))
        else:
            lines.append(line)
    rest = [k for k in settings if k not in placed]
    if rest:
        lines += ["", "# --- set by the simulated UAT (scripts/uat_simulate.py): settings .env.example leaves",
                  "# --- commented or does not list"]
        lines += [f"{k}={settings[k]}" for k in rest]
    for k, v in settings.items():
        if re.search(r"\s#|[\r\n]", str(v)) or str(v)[:1] in "'\"":
            raise ValueError(f"{k}: a value a .env line cannot hold unquoted")
    path = folder / ".env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, [f"{k}={settings[k]}" for k in settings]


def env_values(path: Path) -> dict:
    """KEY=value lines as python-dotenv reads an unquoted line."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.split(" #", 1)[0].strip()
    return out


# --- a run -----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Console:
    """A console the simulation started: where it serves and what it was given."""
    label: str
    child: Child
    root: Path
    port: int
    token: str
    install: Path

    def http(self) -> Http:
        return Http(LOOPBACK, self.port, auth_header(self.token))


@dataclass(frozen=True)
class Context:
    console: "Console | None" = None
    console_records: tuple = ()          # (lineage_id, record_hash) of every turn the console recorded
    console_stopped: bool = False
    lock_holder_pid: "int | None" = None
    reinstall: "Path | None" = None


class Run:
    """The run's one place of state: the ledger (replaced at each step), the
    shared context between sections (replaced), and the children it
    started, every one of which is stopped at the end."""

    def __init__(self, work: Path, args):
        self.work, self.args = work, args
        self.tmp = work / "tmp"
        self.ledger = Ledger()
        self.ctx = Context()
        self.section = ""
        self.number = 0
        self.children: tuple = ()
        self.forwarders: tuple = ()
        self.mcp: tuple = ()

    # -- steps ------------------------------------------------------------------------------------------
    def _next_id(self) -> str:
        return f"{self.section}.{self.number + 1}"

    def inp(self, who: str, name: str, value, *, shown: "str | None" = None) -> str:
        """A scripted input: listed in the report (its value, or how a
        generated secret was made), returned for use."""
        value = str(value)
        self.ledger = self.ledger.with_input(who, name, value if shown is None else shown, self._next_id())
        return value

    def _record(self, step: Step) -> None:
        self.number += 1
        self.ledger = self.ledger.with_step(step)
        line = f"{step.result} {step.id} {step.who}: {step.what}"
        line += f": {step.reason}" if step.result == NOT_RUN else f": {step.seen}"
        print(line[:2000], flush=True)

    def check(self, who: str, what: str, scripted_input: str, *, expected: str, ok: bool, seen: str) -> bool:
        self._record(Step(self._next_id(), who, what, scripted_input, expected, seen, "PASS" if ok else "FAIL"))
        return bool(ok)

    def not_run(self, who: str, what: str, scripted_input: str, *, expected: str, reason: str,
                required: bool = True) -> None:
        self._record(Step(self._next_id(), who, what, scripted_input, expected, "", NOT_RUN, reason, required))

    def install(self, folder: Path, settings: dict, *, shown: "dict | None" = None) -> tuple:
        """An install's .env written from .env.example with `settings`, each
        one listed as an operator's scripted input (a secret the run
        generated described by `shown`, never shown). (the file, its lines
        as the step names them, the secrets blanked)."""
        shown = shown or {}
        for key, value in settings.items():
            self.inp("operator", key, value, shown=shown.get(key))
        path, lines = write_install(folder, {k: str(v) for k, v in settings.items()})
        return path, [f"{k}={shown[k]}" if k in shown else line for (k, _v), line in zip(settings.items(), lines)]

    def note(self, text: str) -> None:
        self.ledger = dataclasses.replace(self.ledger, notes=self.ledger.notes + (text,))

    def coverage(self, where: str, reported) -> None:
        self.ledger = dataclasses.replace(self.ledger, coverage=self.ledger.coverage + ((where, reported),))

    def keep(self, what: str, path: Path) -> None:
        self.ledger = dataclasses.replace(self.ledger, kept=self.ledger.kept + ((what, path),))

    # -- children --------------------------------------------------------------------------------------
    def start(self, label: str, argv: list, env: dict, cwd: Path = ROOT) -> Child:
        child = Child(label, argv, env, cwd)
        self.children = self.children + (child,)
        return child

    def run_script(self, label: str, argv: list, env: dict, timeout: float = SCRIPT_TIMEOUT_S,
                   cwd: Path = ROOT) -> tuple:
        """(exit code or None at the timeout, its output) of a child run to its end."""
        child = self.start(label, argv, env, cwd)
        code = child.wait(timeout)
        if code is None:
            child.stop()
        return code, child.output()

    def forwarder(self, upstream_port: int, login: "str | None", header: str = IDENTITY_HEADER) -> Forwarder:
        fwd = Forwarder(upstream_port, header, login)
        self.forwarders = self.forwarders + (fwd,)
        return fwd

    def env(self, install: "Path | None", **extra: str) -> dict:
        return child_environment(install, self.tmp, **extra)

    def live(self) -> list:
        return [c for c in self.children if c.proc.poll() is None]

    def stop_all(self) -> list:
        """Every child still running, stopped; (label, exit) of each."""
        stopped = [(c.label, c.stop()) for c in self.live()]
        for server in self.mcp:
            if server.proc.poll() is None:
                server.kill()
        for fwd in self.forwarders:
            fwd.close()
        self.forwarders = ()
        return stopped


def generated(how: str) -> str:
    return SECRET_SHOWN.format(how=how)


def names_path(line: str, path: Path) -> bool:
    """Whether a printed line names `path`: as the platform spells it, or
    with forward slashes (`scripts/_readonly.py`'s `store_label`)."""
    absolute = Path(os.path.abspath(path))
    return any(form in line for form in {str(path), str(absolute), path.as_posix(), absolute.as_posix()})


def instant_after_now() -> str:
    """An instant with an offset, strictly after now: the next whole second (an as-of
    instant truncated to the second can fall before a claim admitted within it)."""
    return (datetime.now(OFFSET) + timedelta(seconds=1)).replace(microsecond=0).isoformat()


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def short(value, n: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return text if len(text) <= n else text[:n] + "…"


def start_console(run: Run, label: str, install: Path, root: Path, port: int, token: str, *extra: str) -> tuple:
    """`python start.py --serve [extra]` with the install's .env; (Console, state, seconds)."""
    started = time.monotonic()
    child = run.start(label, [sys.executable, str(START), "--serve", *extra], run.env(install))
    state = child.wait_serving(LOOPBACK, port)
    console = Console(label, child, root, port, token, install)
    return console, state, time.monotonic() - started


def stop_console(run: Run, console: Console) -> tuple:
    """(exit code, the lock file's holder after the stop): SIGINT on POSIX,
    a forced stop as a tree on Windows (start.py waits on the server there)."""
    code = console.child.stop()
    return code, read_lock(console.root)


def read_lock(root: Path) -> dict:
    path = root / "kb.db.lock"
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace").strip("\0 \n"))
    except (OSError, ValueError):
        return {}


def lock_log(root: Path) -> list:
    path = root / "kb.db.lock.log"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def chat(http_: Http, message: str, **fields) -> tuple:
    """(status, frames, done) of one /chat turn, the stream read to its end."""
    reply = http_.call("POST", "/chat", body={"message": message, **fields}, timeout=300.0)
    frames = sse_frames(reply.text) if reply.status == 200 else []
    done = next((d for e, d in frames if e == "done"), None)
    return reply, frames, done


def needs_console(run: Run, what: str) -> "Console | None":
    console = run.ctx.console
    if console is None:
        run.not_run("operator", what, "-", expected="the console section's console",
                    reason="the console section did not leave a console (see its steps)")
    return console


# --- 1. console --------------------------------------------------------------------------------------------

def section_console(run: Run) -> None:
    install, root, port = run.work / "install", run.work / "console", free_port()
    token = secrets.token_urlsafe(32)
    settings = {"LLM_BACKEND": "stub", "NEXUS_API_TOKEN": token, "NEXUS_DATA_ROOT": root, "NEXUS_PORT": port,
                "NEXUS_LEARNING_FROZEN_SINCE": today_utc()}
    path, lines = run.install(install, settings, shown={"NEXUS_API_TOKEN": generated("secrets.token_urlsafe(32)")})
    written = env_values(path)
    run.check("operator", "install settings: the install's .env written from .env.example with the scripted values",
              "; ".join(lines),
              expected="each scripted value on its line, every other line as .env.example ships it "
                       "(NEXUS_LEARNING=frozen, NEXUS_HOST=127.0.0.1 among them)",
              ok=all(written.get(k) == str(v) for k, v in settings.items()) and written.get("NEXUS_LEARNING") == "frozen",
              seen=f"{path}: {len(settings)} settings set; NEXUS_LEARNING={written.get('NEXUS_LEARNING')}, "
                   f"NEXUS_HOST={written.get('NEXUS_HOST')}")
    command = run.inp("operator", "command", "python start.py --serve")
    console, state, took = start_console(run, "console", install, root, port, token)
    first = console.child.first_line()
    ok = state == "serving"
    health = console.http().call("GET", "/healthz") if ok else Reply(0, "")
    run.check("operator", "first run: GET /healthz on the served console", command,
              expected="200 within the start; start.py's first line names the database (law 15)",
              ok=ok and health.status == 200 and first.startswith("database: ") and names_path(first, root),
              seen=f"{state} in {took:.1f} s; GET /healthz {health.status}; first line {first!r}"
                   + ("" if ok else f"; {console.child.tail()}"))
    if not ok:
        for what in ("the turns, the offer, recall, as-of, verify and status",):
            run.not_run("tester", what, "-", expected="a serving console", reason=f"the console did not serve ({state})")
        return
    run.ctx = dataclasses.replace(run.ctx, console=console)
    http_ = console.http()
    stats = http_.call("GET", "/cortex/stats").json() or {}
    learning = stats.get("learning") or {}
    said = [line.strip() for line in console.child.output().splitlines() if line.strip().startswith("learning ")]
    run.check("operator", "first run: learning frozen, and said so (start.py's line, GET /cortex/stats)",
              "NEXUS_LEARNING=frozen as .env.example ships it",
              expected="learning frozen on start.py's startup line and on /cortex/stats, with the since-date set",
              ok=learning.get("state") == "frozen" and learning.get("since") == settings["NEXUS_LEARNING_FROZEN_SINCE"]
              and bool(said) and "frozen" in said[0],
              seen=f"start.py: {said[:1]}; /cortex/stats learning {learning.get('display')!r}")

    records = []

    def turn(message: str, what: str, expected: str, extra_check=None):
        reply, frames, done = chat(http_, run.inp("tester", "question" if message != SENTENCE else "sentence", message))
        events = [e for e, _ in frames]
        ok_ = reply.status == 200 and done is not None and bool(done.get("lineage_id")) \
            and (done.get("learning") or {}).get("state") == "frozen"
        extra_ok, extra_seen = extra_check(frames) if extra_check else (True, "")
        if ok_:
            records.append(done["lineage_id"])
        run.check("tester", what, f"POST /chat {{\"message\": {message!r}}}", expected=expected,
                  ok=ok_ and extra_ok,
                  seen=f"{reply.status}; {len(frames)} frames ({', '.join(dict.fromkeys(events))}); done: turn "
                       f"{(done or {}).get('turn')}, lineage_id {(done or {}).get('lineage_id')}, learning "
                       f"{((done or {}).get('learning') or {}).get('state')}" + (f"; {extra_seen}" if extra_seen else ""))
        return frames, done

    turn(QUESTION_1, "turn 1: POST /chat, the stream read to its done frame",
         "200; the stream ends with a done frame carrying a lineage_id, learning frozen")

    def offered(frames):
        offers = [d for e, d in frames if e == "kb_pressure_alert" and isinstance(d, dict) and d.get("type") == "assertion"]
        o = offers[0] if offers else {}
        good = len(offers) == 1 and o.get("sentence") == SENTENCE and o.get("shaped") is True and bool(o.get("triples")) \
            and o.get("endpoint") == "/kb/assert/confirm"
        return good, f"offer: {short({k: o.get(k) for k in ('type', 'sentence', 'shaped', 'triples', 'endpoint')}, 400)}"

    turn(SENTENCE, "turn 2: a sentence that states a claim; the stream offers it (kb_pressure_alert assertion)",
         "200; one kb_pressure_alert assertion naming the sentence, what would be stored, and POST /kb/assert/confirm",
         offered)

    before = (http_.call("GET", "/healthz").json() or {}).get("kb_triples")
    flag = run.inp("tester", "confirmed", "false")
    refused = http_.call("POST", "/kb/assert/confirm", body={"sentence": SENTENCE, "confirmed": False})
    rbody = refused.json() or {}
    after = (http_.call("GET", "/healthz").json() or {}).get("kb_triples")
    run.check("tester", "declines the offer: POST /kb/assert/confirm unconfirmed", f"confirmed: {flag}",
              expected="422 Law 3 by name, recorded (an event id), nothing stored",
              ok=refused.status == 422 and "Law 3" in str(rbody.get("error")) and rbody.get("recorded") is True
              and bool(rbody.get("event_id")) and before == after == 0,
              seen=f"{refused.status} {short(rbody, 240)}; kb_triples {before} -> {after}")
    ledger = http_.call("GET", "/governance/refusals").json() or {}
    rows = [r for r in ledger.get("refusals") or [] if r.get("event_id") == rbody.get("event_id")]
    run.check("operator", "the refusal is on the ledger: GET /governance/refusals", "-",
              expected="the declined request's event id, route /kb/assert/confirm, rule law3_unconfirmed",
              ok=len(rows) == 1 and rows[0].get("route") == "/kb/assert/confirm"
              and rows[0].get("rule") == "law3_unconfirmed",
              seen=short(rows[0] if rows else ledger, 300))
    flag = run.inp("tester", "confirmed", "true")
    stored = http_.call("POST", "/kb/assert/confirm", body={"sentence": SENTENCE, "confirmed": True})
    sbody = stored.json() or {}
    beliefs = [b for b in sbody.get("beliefs") or [] if "belief_id" in b]
    belief_id = beliefs[0]["belief_id"] if beliefs else None
    run.check("tester", "confirms the offer: POST /kb/assert/confirm", f"confirmed: {flag}",
              expected="200; the claim stored once, as the human's assertion, with its belief id",
              ok=stored.status == 200 and sbody.get("stored") == 1 and sbody.get("source") == "human_assertion"
              and belief_id is not None,
              seen=f"{stored.status}; stored {sbody.get('stored')}, {short(beliefs, 260)}")

    def injected(frames):
        rows_ = [d for e, d in frames if e == "triples_injected"]
        claims = rows_[0] if rows_ else []
        hit = any(isinstance(c, dict) and c.get("s") == "The dose" for c in claims)
        return hit, f"triples_injected {short(claims, 200)}"

    _frames, done3 = turn(FOLLOW_UP, "turn 3: a question about the claim; the claim is injected",
                          "200; the stream injects the confirmed claim (triples_injected) and ends with a done frame",
                          injected)
    if belief_id is not None and done3:
        recall = http_.call("GET", f"/belief/{belief_id}/recall").json() or {}
        ids = [r.get("lineage_id") for r in recall.get("records") or []]
        run.check("tester", "recall: GET /belief/{id}/recall on the confirmed claim", f"belief {belief_id}",
                  expected="the turn-3 record, read from the injected set, contract version 2",
                  ok=ids == [done3["lineage_id"]] and recall.get("read_from") == "injected"
                  and recall.get("contract_version") == 2,
                  seen=f"records {ids}, total {recall.get('total')}, read_from {recall.get('read_from')}, "
                       f"contract_version {recall.get('contract_version')}")
    else:
        run.not_run("tester", "recall: GET /belief/{id}/recall on the confirmed claim", "-",
                    expected="the turn-3 record", reason="no belief was stored, or turn 3 did not record")
    instant = run.inp("tester", "instant", instant_after_now())
    asof = http_.call("GET", "/kb/as-of?date=" + instant.replace("+", "%2B"))
    held = [c.get("belief_id") for c in (asof.json() or {}).get("claims") or []]
    run.check("tester", "as-of: GET /kb/as-of at an instant with an offset", f"date={instant}",
              expected="200; the confirmed claim held at that instant",
              ok=asof.status == 200 and belief_id is not None and held == [belief_id],
              seen=f"{asof.status}; held {held}; as_of {short((asof.json() or {}).get('as_of'), 160)}")
    bare = run.inp("tester", "instant", today_utc())
    refused = http_.call("GET", f"/kb/as-of?date={bare}")
    rb = refused.json() or {}
    run.check("tester", "as-of: GET /kb/as-of at a bare date, refused by name", f"date={bare}",
              expected="422 refused: bare_date, with a reason and an example",
              ok=refused.status == 422 and rb.get("refused") == "bare_date" and bool(rb.get("example")),
              seen=f"{refused.status} {short(rb, 240)}")
    cycle = http_.call("POST", "/kairos/cycle", body={})
    cb = cycle.json() or {}
    run.check("operator", "seals the records now: POST /kairos/cycle", "-",
              expected="200; a snapshot taken and an archive block written",
              ok=cycle.status == 200 and cb.get("snapshot_taken") is True and cb.get("archive_block_index") is not None,
              seen=f"{cycle.status}; snapshot_taken {cb.get('snapshot_taken')}, block {cb.get('archive_block_index')}, "
                   f"sealed {cb.get('sealed_records')}")
    hashes = []
    for lid in records:
        rec = http_.call("GET", f"/lineage/{lid}").json() or {}
        hashes.append((lid, rec.get("record_hash")))
    run.ctx = dataclasses.replace(run.ctx, console_records=tuple(hashes))
    verify = http_.call("GET", "/lineage/verify").json() or {}
    layers = verify.get("layers") or {}
    run.check("operator", "verify: GET /lineage/verify", "-",
              expected=f"intact; the {len(records)} records checked at the row and seal layers; the chain ok",
              ok=verify.get("verdict") == "intact" and verify.get("records_checked") == len(records) == 3
              and (layers.get("row") or {}).get("ok") == 3 and (layers.get("seal") or {}).get("ok") == 3
              and (layers.get("chain") or {}).get("ok") is True,
              seen=f"verdict {verify.get('verdict')}, checked {verify.get('records_checked')}, row "
                   f"{(layers.get('row') or {}).get('ok')}, seal {(layers.get('seal') or {}).get('ok')}, chain "
                   f"{short(layers.get('chain'), 120)}")
    each = {lid: (http_.call("GET", f"/lineage/{lid}/verify").json() or {}).get("verdict") for lid in records}
    run.check("operator", "verify: GET /lineage/{id}/verify for each turn", ", ".join(records),
              expected="intact, each", ok=bool(each) and set(each.values()) == {"intact"}, seen=short(each, 400))
    chain = http_.call("GET", "/chain/status").json() or {}
    run.check("operator", "verify: GET /chain/status", "-", expected="the chain valid, one block at least",
              ok=(chain.get("verification") or {}).get("valid") is True and (chain.get("n_blocks") or 0) >= 1,
              seen=f"n_blocks {chain.get('n_blocks')}, verification {short(chain.get('verification'), 160)}")
    gov = http_.call("GET", "/governance/verify").json() or {}
    run.check("operator", "verify: GET /governance/verify", "-",
              expected="intact; the refused request's row checked and sealed",
              ok=gov.get("verdict") == "intact" and (gov.get("row") or {}).get("checked", 0) >= 1,
              seen=short(gov, 300))
    health = http_.call("GET", "/healthz").json() or {}
    run.check("operator", "status: GET /healthz", "-",
              expected="the stub serving, the SQLite store under the data root, token mode, one claim",
              ok=(health.get("model_server") or {}).get("backend") == "stub"
              and (health.get("store") or {}).get("backend") == "sqlite"
              and (health.get("identity") or {}).get("mode") == "token" and health.get("kb_triples") == 1,
              seen=f"model_server {short(health.get('model_server'), 120)}; store {short(health.get('store'), 160)}; "
                   f"identity {short(health.get('identity'), 100)}; kb_triples {health.get('kb_triples')}")
    stats = http_.call("GET", "/cortex/stats").json() or {}
    run.coverage("console: GET /cortex/stats, mcp", stats.get("mcp"))
    run.check("operator", "status: GET /cortex/stats", "-",
              expected="learning frozen; the MCP coverage block present (MCP never opened this store)",
              ok=(stats.get("learning") or {}).get("state") == "frozen" and isinstance(stats.get("mcp"), dict),
              seen=f"learning {(stats.get('learning') or {}).get('display')!r}; mcp {short(stats.get('mcp'), 240)}")
    kairos = http_.call("GET", "/kairos/status").json() or {}
    last = kairos.get("last_cycle") or {}
    run.check("operator", "status: GET /kairos/status", "-",
              expected="a last cycle recorded, not skipped, no older than the forced one",
              ok=bool(last) and last.get("skipped") is False
              and float(last.get("timestamp") or 0) >= float(cb.get("timestamp") or 0) > 0,
              seen=f"is_idle {kairos.get('is_idle')}; last cycle at {last.get('timestamp')} (the forced one "
                   f"{cb.get('timestamp')}), block {last.get('archive_block_index')}, skipped {last.get('skipped')}")
    model = http_.call("GET", "/model/status").json() or {}
    run.check("operator", "status: GET /model/status", "-", expected="the stub's primary model, backend stub",
              ok=model.get("backend") == "stub" and model.get("active_model") == "stub:primary",
              seen=short(model, 240))
    run.keep("the console's data root", root)


# --- verification (W9: the second section) ----------------------------------------------------------------

def stranger_environment() -> dict:
    """What a stranger's shell hands the verifier: PATH, and on Windows the two variables Python needs to start;
    no NEXUS setting, no PYTHONPATH, nothing that names a store."""
    return {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "WINDIR")}


def as_a_stranger(run: Run, label: str, files: dict, args: list) -> tuple:
    """`nexus-verify` as a stranger runs it: a copy of its one file alone in a new folder with the files the
    stranger was handed (text written exactly, CRLF kept), run isolated (`-I`: neither that folder nor the
    checkout on its path) in the stranger's environment. (exit code or None, its output, the folder's names)."""
    base = run.work / "strangers"
    base.mkdir(parents=True, exist_ok=True)
    where = base / f"{len(list(base.iterdir())) + 1:02d}-{label}"
    where.mkdir()
    shutil.copy2(VERIFIER, where / VERIFIER.name)
    for name, content in files.items():
        with open(where / name, "w", encoding="utf-8", newline="") as f:
            f.write(content if isinstance(content, str) else json.dumps(content, sort_keys=True))
    names = sorted(p.name for p in where.iterdir())
    code, out = run.run_script(f"nexus-verify ({label})", [sys.executable, "-I", VERIFIER.name, *args],
                               stranger_environment(), cwd=where)
    return code, out, names


def failure_names(out: str) -> list:
    return [line[5:].split(":", 1)[0].strip() for line in out.splitlines() if line.startswith("FAIL ")]


def verdict_of(out: str) -> str:
    return next((line.strip() for line in out.splitlines() if line.startswith("nexus-verify: ")), "no verdict line")


def section_verification(run: Run) -> None:
    console = needs_console(run, "verification: credentials, nexus-verify, commit now and reveal later")
    if console is None:
        return
    http_ = console.http()
    lid = run.ctx.console_records[0][0] if run.ctx.console_records else ""
    note = run.inp("tester", "the held note", HELD_NOTE)
    flag = run.inp("tester", "confirmed", "false")
    refused = http_.call("POST", "/commit", body={"content": note, "confirmed": False})
    rb = refused.json() or {}
    run.check("tester", "commit now: POST /commit, not confirmed", f"content: the held note; confirmed: {flag}",
              expected="422 with the offer (the commitment that would be made), the refusal recorded, nothing held",
              ok=refused.status == 422 and rb.get("recorded") is True and bool(rb.get("offer")),
              seen=f"{refused.status}; recorded {rb.get('recorded')}; offer {short(rb.get('offer'), 160)}")
    flag = run.inp("tester", "confirmed", "true")
    made = http_.call("POST", "/commit", body={"content": note, "confirmed": True})
    mb = made.json() or {}
    salt, cid = str(mb.get("salt") or ""), str(mb.get("commitment_id") or "")
    run.check("tester", "commit now: POST /commit, confirmed", f"content: the held note; confirmed: {flag}",
              expected="200: the commitment's id, the salted commitment and its salt, handed back once",
              ok=made.status == 200 and bool(cid) and len(salt) == 64 and bool(mb.get("commitment")),
              seen=f"{made.status}; commitment {str(mb.get('commitment'))[:16]}…; the salt handed back "
                   f"({len(salt)} hex characters; the tester keeps it; not shown)")
    before = (http_.call("GET", "/chain/status").json() or {}).get("n_blocks")
    cycle = http_.call("POST", "/kairos/cycle", timeout=300.0)
    after = (http_.call("GET", "/chain/status").json() or {}).get("n_blocks")
    early = http_.call("GET", f"/commitments/{cid}/receipt")
    run.check("operator", "the seal: POST /kairos/cycle (the manual force-cycle control)", "-",
              expected="200; the chain grows; the held commitment now has a receipt",
              ok=cycle.status == 200 and isinstance(before, int) and isinstance(after, int) and after > before
              and early.status == 200, seen=f"cycle {cycle.status}; blocks {before} → {after}; receipt {early.status}")
    identity = http_.call("GET", "/identity").json() or {}
    disclose = run.inp("tester", "disclose", "model_id")
    reply = http_.call("GET", f"/lineage/{lid}/credential?disclose={disclose}")
    credential = reply.json() or {}
    command = "python -I nexus_verify.py credential.json --identity identity.json"
    code, out, names = as_a_stranger(run, "credential", {"credential.json": credential, "identity.json": identity},
                                     ["credential.json", "--identity", "identity.json"])
    run.check("tester", "a credential for the console's first turn, verified by nexus-verify with no store access",
              f"GET /lineage/{{id}}/credential?disclose={disclose}; GET /identity; {command}",
              expected="exit 0, VERIFIED, model_id disclosed; run isolated in a new folder holding only the "
                       "verifier's one file and the two files it was handed",
              ok=reply.status == 200 and code == 0 and verdict_of(out) == "nexus-verify: VERIFIED"
              and "DISCLOSED model_id" in out and names == ["credential.json", "identity.json", VERIFIER.name],
              seen=f"credential {reply.status}; exit {code}; {verdict_of(out)}; the folder {names}")
    tampered = json.loads(json.dumps(credential))
    if isinstance((tampered.get("disclosed") or {}).get("model_id"), dict):
        tampered["disclosed"]["model_id"]["value"] = "another-model"
    code, out, _n = as_a_stranger(run, "tampered", {"credential.json": tampered, "identity.json": identity},
                                  ["credential.json", "--identity", "identity.json"])
    run.check("tester", "a tampered credential: its disclosed model_id changed",
              f"disclosed.model_id.value: another-model; {command}",
              expected="exit 1, NOT VERIFIED, the failure named disclosure_mismatch",
              ok=code == 1 and "disclosure_mismatch" in failure_names(out) and "NOT VERIFIED" in verdict_of(out),
              seen=f"exit {code}; failures {failure_names(out)}")
    two = run.inp("tester", "disclose", "model_id,ts")
    reply = http_.call("GET", f"/lineage/{lid}/credential?disclose={two}")
    pair = reply.json() or {}
    asked = {hashlib.sha256(q.encode("utf-8")).hexdigest() for q in (QUESTION_1, SENTENCE, FOLLOW_UP)}
    leaked = sorted(h[:12] for h in asked if h in json.dumps(pair))
    code, out, _n = as_a_stranger(run, "two-fields", {"credential.json": pair, "identity.json": identity},
                                  ["credential.json", "--identity", "identity.json"])
    run.check("tester", "a credential disclosing two fields, every other field only as its salted commitment",
              f"GET /lineage/{{id}}/credential?disclose={two}; {command}",
              expected="exit 0, VERIFIED; model_id and ts disclosed; the rest commitments; no question's hash in it",
              ok=reply.status == 200 and code == 0 and verdict_of(out) == "nexus-verify: VERIFIED" and not leaked
              and sorted(pair.get("disclosed") or {}) == ["model_id", "ts"] and len(pair.get("commitments") or {}) > 2,
              seen=f"credential {reply.status}; exit {code}; disclosed {sorted(pair.get('disclosed') or {})}; "
                   f"{len(pair.get('commitments') or {})} commitments; question hashes in it {leaked}")
    planted = json.loads(json.dumps(pair))
    value = ((planted.get("disclosed") or {}).get("model_id") or {}).get("value")
    if value is not None:
        planted["commitments"]["model_id"] = hashlib.sha256(nexus_verify.canonical(value)).hexdigest()
        planted["disclosed"]["model_id"]["salt"] = ""
    code, out, _n = as_a_stranger(run, "unsalted", {"credential.json": planted, "identity.json": identity},
                                  ["credential.json", "--identity", "identity.json"])
    run.check("tester", "a planted unsalted content hash: model_id's commitment made the bare SHA-256 of its value",
              f"commitments.model_id: sha256(the value); its salt emptied; {command}",
              expected="exit 1, the failure named unsalted_hash", ok=code == 1 and "unsalted_hash" in failure_names(out),
              seen=f"exit {code}; failures {failure_names(out)}")
    # Another session: the console's store backed up while it serves and restored into a fresh data root.
    out_dir = run.work / "verification" / "backups"
    command = run.inp("operator", "command", f"python scripts/backup.py --data-root {console.root} --out {out_dir}")
    code, text = run.run_script("backup (verification)", [sys.executable, str(BACKUP), "--data-root", str(console.root),
                                                          "--out", str(out_dir)], run.env(console.install))
    folder = next((line.split("backup: ", 1)[1].strip() for line in text.splitlines() if line.startswith("backup: ")), "")
    files = sorted(p.name for p in Path(folder).rglob("*")) if folder else []
    run.check("operator", "another session, first: a backup of the console's store while it serves", command,
              expected=f"exit 0; one backup folder; the instance's private key ({KEY_FILE}) not in it",
              ok=code == 0 and bool(folder) and "kb.db" in files and KEY_FILE not in files,
              seen=f"exit {code}; {folder}: {files}")
    if not folder:
        run.not_run("tester", "the tombstoned record's credential and the reveal in another session", "-",
                    expected="a backup folder", reason="the backup wrote no folder")
        return
    second = run.work / "verification" / "session-2"
    second.mkdir(parents=True)
    for item in sorted(Path(folder).iterdir()):
        if item.name != "MANIFEST.json":
            (shutil.copytree if item.is_dir() else shutil.copy2)(item, second / item.name)
    actor = run.inp("operator", "tombstone actor", TOMBSTONE_ACTOR)
    reason = run.inp("operator", "tombstone reason", TOMBSTONE_REASON)
    command = run.inp("operator", "command", f"python tests/_uat_tombstone.py --data-root {second} --lineage-id {lid} "
                                             f"--actor {actor} --reason \"{reason}\"")
    code, text = run.run_script("tombstone", [sys.executable, str(TOMBSTONE_HELPER), "--data-root", str(second),
                                              "--lineage-id", lid, "--actor", actor, "--reason", reason],
                                run.env(None, LLM_BACKEND="stub", NEXUS_DATA_ROOT=str(second)))
    first = (text.splitlines() or [""])[0]
    run.check("operator", "a tombstone on the restored copy, which nothing serves: no route reaches it until W14 "
                          "(W8-B3), so tests/_uat_tombstone.py calls LineageStore.tombstone, the one act of this run "
                          "not reached through the product's own entry points", command,
              expected="exit 0; its first line names the restored data root; the act's row printed",
              ok=code == 0 and first.startswith("data root: ") and names_path(first, second) and lid in text,
              seen=f"exit {code}; first line {first[:160]!r}; {short((text.strip().splitlines() or [''])[-1], 200)}")
    port = free_port()
    command = run.inp("operator", "command", f"python start.py --serve --data-root {second} --port {port}")
    other, state, took = start_console(run, "console (another session)", console.install, second, port,
                                       console.token, "--data-root", str(second), "--port", str(port))
    ok = state == "serving"
    run.check("operator", "another session: a console started on the restored data root", command, expected="serving",
              ok=ok, seen=f"{state} in {took:.1f} s" + ("" if ok else f"; {other.child.tail()}"))
    if not ok:
        run.not_run("tester", "the tombstoned record's credential and the reveal in another session", "-",
                    expected="a serving console", reason="the other session's console did not serve")
        return
    h2 = other.http()
    cycle = h2.call("POST", "/kairos/cycle", timeout=300.0)
    identity2 = h2.call("GET", "/identity").json() or {}
    keys = identity2.get("keys") or []
    run.check("operator", "another session: the restore made a new key and /identity lists both, the rotation "
                          "recorded", "POST /kairos/cycle; GET /identity",
              expected="cycle 200; two keys, reasons first and rotation: the first session's key, its period closed "
                       "at a block, and a new current one",
              ok=cycle.status == 200 and [k.get("reason") for k in keys] == ["first", "rotation"]
              and keys[0].get("key_id") == identity.get("key_id") and keys[0].get("until_block") is not None
              and identity2.get("key_id") == keys[1].get("key_id") != identity.get("key_id"),
              seen=f"cycle {cycle.status}; " + "; ".join(f"{str(k.get('key_id'))[:12]}… {k.get('reason')} from block "
                                                       f"{k.get('announced_in')} to {k.get('until_block')}" for k in keys))
    kept = run.inp("tester", "disclose", "ts,lineage_id")
    reply = h2.call("GET", f"/lineage/{lid}/credential?disclose={kept}")
    tomb = reply.json() or {}
    dropped = h2.call("GET", f"/lineage/{lid}/credential?disclose=model_id")
    code, out, _n = as_a_stranger(run, "tombstoned", {"credential.json": tomb, "identity.json": identity2},
                                  ["credential.json", "--identity", "identity.json"])
    run.check("tester", "the tombstoned record's credential, issued in the other session, verified by nexus-verify "
                        "with no store access",
              f"GET /lineage/{{id}}/credential?disclose={kept}, then disclose=model_id (a dropped field); GET /identity; "
              f"{command}",
              expected="exit 0, VERIFIED on what it kept, signed under the first session's key and checked in its "
                       "period; disclosing a dropped field refused by name (409 dropped)",
              ok=reply.status == 200 and code == 0 and verdict_of(out) == "nexus-verify: VERIFIED"
              and dropped.status == 409 and (dropped.json() or {}).get("refused") == "dropped"
              and {s.get("key_id") for s in tomb.get("signatures") or []} == {identity.get("key_id")},
              seen=f"credential {reply.status}; exit {code}; {verdict_of(out)}; signed by "
                   f"{sorted(str(s.get('key_id'))[:12] for s in tomb.get('signatures') or [])}; model_id "
                   f"{dropped.status} {(dropped.json() or {}).get('refused')}")
    reply = h2.call("GET", f"/commitments/{cid}/receipt")
    receipt = reply.json() or {}
    reveal = ["--reveal", "receipt.json", "--content", "note.txt", "--salt", salt, "--identity", "identity.json"]
    shown = ("python -I nexus_verify.py --reveal receipt.json --content note.txt --salt <the salt the first session "
             "handed back> --identity identity.json")
    code, out, names = as_a_stranger(run, "reveal", {"receipt.json": receipt, "note.txt": note,
                                                     "identity.json": identity2}, reveal)
    run.check("tester", "reveal later: the commitment made in the first session, revealed in the other and verified "
                        "offline", f"GET /commitments/{{id}}/receipt; {shown}",
              expected="the receipt 200, holding neither the salt nor the note; exit 0, VERIFIED, held by the block's "
                       "own time",
              ok=reply.status == 200 and "salt" not in receipt and HELD_NOTE.split("\r\n")[0] not in json.dumps(receipt)
              and code == 0 and verdict_of(out) == "nexus-verify: VERIFIED" and "HELD by the block's own time" in out,
              seen=f"receipt {reply.status}; exit {code}; {verdict_of(out)}; the folder {names}")
    code, out, _n = as_a_stranger(run, "reveal-other", {"receipt.json": receipt, "note.txt": note + " ",
                                                        "identity.json": identity2}, reveal)
    run.check("tester", "reveal later, other content: the note with one space added", f"{shown} (the note + ' ')",
              expected="exit 1, the failure named reveal_mismatch",
              ok=code == 1 and "reveal_mismatch" in failure_names(out), seen=f"exit {code}; failures {failure_names(out)}")
    other.child.stop()
    run.keep("the verification section's restored data root (one record tombstoned)", second)


# --- 2. mcp ------------------------------------------------------------------------------------------------

class StandInSearch:
    """A SearXNG stand-in on loopback (W10b): what the console's search reaches, and what a recipient of its
    requests holds, every request kept with its path and headers."""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.requests, outer = [], self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):                       # noqa: N802 (http.server's name)
                outer.requests.append({"path": self.path, "headers": dict(self.headers)})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(SEARCH_BODY)))
                self.end_headers()
                self.wfile.write(SEARCH_BODY)

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer((LOOPBACK, 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://{LOOPBACK}:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def section_actions(run: Run) -> None:
    """W10b — a search the tester consents to is recorded as an action before it leaves, signed, sealed, and
    verified offline by a stranger: by the instance's receipt, and by the recipient of the signed request."""
    searx = StandInSearch()
    install, root, port = run.work / "actions-install", run.work / "actions-data", free_port()
    token = secrets.token_urlsafe(32)
    settings = {"LLM_BACKEND": "stub", "NEXUS_API_TOKEN": token, "NEXUS_DATA_ROOT": root, "NEXUS_PORT": port,
                "SEARXNG_URL": searx.url, "NEXUS_SIGN_REQUESTS": "on"}
    run.install(install, settings, shown={"NEXUS_API_TOKEN": generated("secrets.token_urlsafe(32)")})
    console, state, _took = start_console(run, "console (actions)", install, root, port, token)
    try:
        if state != "serving":
            run.check("operator", "a console with a stand-in search and signed requests", "NEXUS_SIGN_REQUESTS=on",
                      expected="serving", ok=False, seen=state)
            return
        http_ = console.http()
        question = run.inp("tester", "question", SEARCH_QUESTION)
        consent = http_.call("POST", "/search/confirm", body={"query": question, "confirmed": True})
        reply, frames, done = chat(http_, question)
        lineage_id = (done or {}).get("lineage_id") or ""
        listed = ((http_.call("GET", f"/lineage/{lineage_id}").json() or {}).get("actions") or []) if lineage_id else []
        sent = searx.requests[-1] if searx.requests else {}
        run.check("tester", "a search the tester consented to is one action, recorded before its result was used",
                  f"POST /search/confirm; POST /chat {question!r}",
                  expected="the search ran once; the turn's record lists one web_search action, runtime, completed",
                  ok=consent.status == 200 and reply.status == 200 and len(searx.requests) == 1
                  and any(e == "rag_injected" for e, _d in frames)
                  and [(a.get("kind"), a.get("evidence_class"), a.get("state")) for a in listed]
                  == [("web_search", "runtime", "completed")],
                  seen=f"consent {consent.status}; chat {reply.status}; requests {len(searx.requests)}; "
                       f"actions {short(listed, 200)}")
        signature_header = str((sent.get("headers") or {}).get("Signature") or "")
        run.check("tester", "the request left signed (RFC 9421, the instance's key)", "NEXUS_SIGN_REQUESTS=on",
                  expected="Signature-Input and Signature headers on the request the search stand-in received",
                  ok="Signature-Input" in (sent.get("headers") or {}) and signature_header.startswith("nexus=:"),
                  seen=f"Signature {signature_header[:28]}…")
        cycle = http_.call("POST", "/kairos/cycle", timeout=300.0)
        identity = http_.call("GET", "/identity").json() or {}
        action_id = (listed[0] if listed else {}).get("action_id", "")
        receipt = http_.call("GET", f"/actions/{action_id}")
        code, out, names = as_a_stranger(run, "action", {"action.json": receipt.json() or {}, "identity.json": identity},
                                         ["--action", "action.json", "--identity", "identity.json"])
        run.check("tester", "the action's receipt, verified by nexus-verify with no store access",
                  "POST /kairos/cycle; GET /actions/{id}; python -I nexus_verify.py --action action.json --identity "
                  "identity.json", expected="exit 0, VERIFIED, the accountable identity disclosed by the gated receipt",
                  ok=cycle.status == 200 and receipt.status == 200 and code == 0
                  and verdict_of(out) == "nexus-verify: VERIFIED" and "disclosed" in out,
                  seen=f"cycle {cycle.status}; receipt {receipt.status}; exit {code}; {verdict_of(out)}; "
                       f"the folder {names}")
        value = signature_header[len("nexus=:"):].rstrip(":")
        found = http_.call("GET", f"/identity/signed-action?signature={urllib_quote(value)}",
                           headers={"Authorization": ""})      # the recipient holds no token
        body = found.json() or {}
        code, out, _n = as_a_stranger(run, "signed-request", {"action.json": body, "identity.json": identity},
                                      ["--action", "action.json", "--identity", "identity.json"])
        run.check("stranger", "the recipient of the signed request retrieves its authorisation and verifies it",
                  "GET /identity/signed-action?signature=… (no token); nexus-verify --action",
                  expected="200; exit 0, VERIFIED; the accountable identity committed, never named",
                  ok=found.status == 200 and code == 0 and verdict_of(out) == "nexus-verify: VERIFIED"
                  and "accountable" not in json.dumps(body),
                  seen=f"lookup {found.status}; exit {code}; {verdict_of(out)}")
        tampered = json.loads(json.dumps(body))
        if isinstance(tampered.get("fields"), dict):
            tampered["fields"]["status"] = "500"
        code, out, _n = as_a_stranger(run, "action-tampered", {"action.json": tampered, "identity.json": identity},
                                      ["--action", "action.json", "--identity", "identity.json"])
        run.check("stranger", "a tampered receipt: its status changed", "fields.status: 500",
                  expected="exit 1, the failure named action_mismatch",
                  ok=code == 1 and "action_mismatch" in failure_names(out), seen=f"exit {code}; {failure_names(out)}")
    finally:
        console.child.stop()
        searx.stop()


INTERCEPT_TOOLS = [{"name": "get_weather", "description": "the weather in a city",
                    "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}}]
INTERCEPT_QUESTION = "What is the weather in Paris?"


def _intercept_ask(messages: "list | None" = None, *, tools: bool = True) -> dict:
    doc = {"model": "any", "max_tokens": 64, "stream": True,
           "messages": messages or [{"role": "user", "content": INTERCEPT_QUESTION}]}
    return {**doc, "tools": INTERCEPT_TOOLS} if tools else doc


def _tool_use_in(stream: str) -> dict:
    """The tool use a streamed Anthropic response asks for: its id and name, or {}."""
    for _event, data in sse_frames(stream):
        block = data.get("content_block") if isinstance(data, dict) else None
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return {"id": block.get("id"), "name": block.get("name")}
    return {}


def section_interceptor(run: Run) -> None:
    """W10c — a scripted harness runs a tool-use session through the interceptor to a stub provider: every exchange
    recorded and sealed, the tool use recorded `intercepted` with the result the harness reported, a request whose
    record cannot be written refused before it is sent, and the record, the action and the exchange verified
    offline by a stranger."""
    stub_port, port = free_port(), free_port()
    root, out = run.work / "intercept-data", run.work / "intercept-out"
    out.mkdir(parents=True, exist_ok=True)
    key = run.inp("harness", "x-api-key", secrets.token_urlsafe(24), shown=generated("secrets.token_urlsafe(24)"))
    owner = run.inp("operator", "NEXUS_INTERCEPT_OWNER", "tester-1@example.test")
    env = run.env(None, NEXUS_INTERCEPT_UPSTREAM=f"http://{LOOPBACK}:{stub_port}", NEXUS_INTERCEPT_DATA_ROOT=str(root),
                  NEXUS_INTERCEPT_OWNER=owner)
    stub = run.start("stub upstream", [sys.executable, "-m", "backend.intercept.stub_upstream", "--port",
                                       str(stub_port)], run.env(None))
    served = None
    try:
        stub_state = stub.wait_serving(LOOPBACK, stub_port)
        served = run.start("interceptor", [sys.executable, "-m", "backend.intercept", "serve", "--port", str(port)],
                           env) if stub_state == "serving" else None
        state = served.wait_serving(LOOPBACK, port) if served is not None else "not started"
        if not run.check("operator", "the interceptor serves on loopback, with its own data folder and a stub provider",
                         "python -m backend.intercept.stub_upstream --port <free>; python -m backend.intercept serve "
                         "--port <free>; NEXUS_INTERCEPT_UPSTREAM=<the stub>; NEXUS_INTERCEPT_DATA_ROOT=<a new folder>",
                         expected="both serving", ok=stub_state == "serving" and state == "serving",
                         seen=f"stub {stub_state}; interceptor {state}; {(served or stub).tail(2)}"):
            return
        harness = Http(LOOPBACK, port, {"x-api-key": key, "anthropic-version": "2023-06-01"})
        upstream = Http(LOOPBACK, stub_port)
        calls = lambda: (upstream.call("GET", "/health").json() or {}).get("calls")      # noqa: E731

        first = harness.call("POST", "/v1/messages?beta=true", body=_intercept_ask())
        use = _tool_use_in(first.text)
        run.check("harness", "a model exchange through the interceptor: the model asks for a tool",
                  f"POST /v1/messages?beta=true {INTERCEPT_QUESTION!r}, one tool offered",
                  expected="200; the stream ends with message_stop; one tool use asked for; the provider called once",
                  ok=first.status == 200 and first.text.rstrip().endswith('data: {"type":"message_stop"}')
                  and use.get("name") == "get_weather" and calls() == 1,
                  seen=f"{first.status}; tool use {use}; provider calls {calls()}")
        history = [{"role": "user", "content": INTERCEPT_QUESTION},
                   {"role": "assistant", "content": [{"type": "tool_use", "id": use.get("id"), "name": use.get("name"),
                                                      "input": {"city": "Paris"}}]},
                   {"role": "user", "content": [{"type": "tool_result", "tool_use_id": use.get("id"),
                                                 "content": "18C and clear"}]}]
        second = harness.call("POST", "/v1/messages?beta=true", body=_intercept_ask(history))
        health = harness.call("GET", "/health").json() or {}
        run.check("harness", "the harness reports the tool's result and the model answers",
                  "POST /v1/messages?beta=true with the tool_result",
                  expected="200; an answer; two exchanges recorded, none incomplete",
                  ok=second.status == 200 and "clear in Paris" in second.text and health.get("exchanges") == 2
                  and health.get("recorded") == 2 and health.get("incomplete") == 0,
                  seen=f"{second.status}; health {short(health, 200)}")

        # the forced failure: another program is reading the store's file and has not finished, so the request's
        # record cannot be committed (SQLite lets no writer commit under an open read)
        # (this runner opens no store itself: the other program is a child, tests/_uat_hold_read.py)
        holder = Child("another program reading the store",
                       [sys.executable, str(HOLD_READ_HELPER), "--db", str(root / "kb.db")], env)
        try:
            deadline = time.monotonic() + 60
            while "holding" not in holder.output() and holder.proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            holding = "holding" in holder.output()
            before = calls()
            refused = harness.call("POST", "/v1/messages?beta=true", body=_intercept_ask(), timeout=120.0)
            after = calls()
        finally:
            holder.stop()                            # the read ends when the other program does
        run.check("harness", "a request whose record cannot be written is refused before it is sent",
                  "another program holds the store's file open in a read it has not finished; POST /v1/messages",
                  expected="503 talima_request_not_recorded; the provider is not called",
                  ok=holding and refused.status == 503 and "talima_request_not_recorded" in refused.text
                  and after == before == 2,
                  seen=f"the other program {'held its read' if holding else 'never opened its read'}; "
                       f"{refused.status} {short(refused.text, 160)}; provider calls {before} -> {after}")
        third = harness.call("POST", "/v1/messages?beta=true", body=_intercept_ask(tools=False))
        run.check("harness", "the store is free again: the next exchange is recorded",
                  "POST /v1/messages (no tool offered)", expected="200; the provider called a third time",
                  ok=third.status == 200 and calls() == 3, seen=f"{third.status}; provider calls {calls()}")
    finally:
        if served is not None:
            served.stop()
        stub.stop()

    def cli(label: str, *args: str) -> tuple:
        return run.run_script(f"talima-intercept {label}", [sys.executable, "-m", "backend.intercept", *args], env)

    code, text = cli("verify", "verify")
    try:
        verdict = json.loads(text[text.index("{"):])
    except ValueError:
        verdict = {}
    listing_code, listing_text = cli("list", "list")
    listing = [json.loads(line) for line in listing_text.splitlines() if line.startswith("{")]
    uses = [u for e in listing for u in e.get("tool_uses") or []]
    run.check("operator", "stopped, the interceptor's store verifies: the chain, every record, every action",
              "python -m backend.intercept verify; python -m backend.intercept list",
              expected="exit 0, ok; three exchanges, each completed, recorded and sealed; one tool use, reported",
              ok=code == 0 and verdict.get("ok") is True and listing_code == 0 and len(listing) == 3
              and all(e.get("state") == "completed" and e.get("recorded") and e.get("sealed_block") is not None
                      for e in listing) and [u.get("status") for u in uses] == ["reported"],
              seen=f"verify exit {code}, ok {verdict.get('ok')}; {len(listing)} exchange(s) "
                   f"{[(e.get('state'), e.get('sealed_block')) for e in listing]}; tool uses {short(uses, 200)}")
    if not listing or not uses:
        return
    oldest = listing[-1]
    made = [cli("identity", "identity", "--out", str(out / "identity.json")),
            cli("credential", "credential", oldest["lineage_id"], "--disclose", "record_scope,model_id", "--out",
                str(out / "credential.json")),
            cli("receipt (the tool use)", "receipt", uses[0]["action_id"], "--out", str(out / "receipt.json")),
            cli("receipt (the exchange)", "receipt", oldest["exchange_action_id"], "--out", str(out / "exchange.json"))]
    read = lambda name: json.loads((out / name).read_text(encoding="utf-8")) if (out / name).is_file() else {}  # noqa: E731
    identity, credential = read("identity.json"), read("credential.json")
    run.coverage("interceptor (the record's own declaration)", (credential.get("scope") or {}).get("coverage"))
    results = []
    for label, name, args, word in (
            ("record", "credential.json", ["credential.json"], 'record_scope = "intercepted"'),
            ("tool use", "receipt.json", ["--action", "receipt.json"], "action: intercepted tool_use"),
            ("exchange", "exchange.json", ["--action", "exchange.json"], "action: runtime model_exchange")):
        vcode, vout, _names = as_a_stranger(run, f"intercepted-{label.replace(' ', '-')}",
                                            {name: read(name), "identity.json": identity},
                                            [*args, "--identity", "identity.json"])
        results.append((label, vcode, verdict_of(vout), word in vout))
    run.check("stranger", "the record, the tool use and the exchange, verified by nexus-verify with no store access",
              "python -m backend.intercept identity | credential | receipt; python -I nexus_verify.py … --identity "
              "identity.json",
              expected="each: exit 0, VERIFIED; the record's scope reads intercepted; the tool use reads intercepted, "
                       "the exchange runtime",
              ok=all(c == 0 for c, _o in made) and all(vc == 0 and v == "nexus-verify: VERIFIED" and seen_word
                                                       for _l, vc, v, seen_word in results),
              seen=f"made {[c for c, _o in made]}; {results}")
    # W11d: the whole chain as one document (docs/TALIMA_SPEC.md, section 11), checked by the same stranger
    chain_code, _chain_out = cli("chain", "chain", "--out", str(out / "chain.json"))
    ccode, cout, _names = as_a_stranger(run, "intercepted-chain",
                                        {"chain.json": read("chain.json"), "identity.json": identity},
                                        ["--chain", "chain.json", "--identity", "identity.json"])
    run.check("stranger", "the chain as one document: its blocks, links and seals, verified with no store access",
              "python -m backend.intercept chain; the verifier, isolated, on chain.json with --chain",
              expected="exit 0, VERIFIED; the chain's blocks counted from index 0",
              ok=chain_code == 0 and ccode == 0 and verdict_of(cout).endswith(": VERIFIED") and "CHAIN " in cout
              and "from index 0" in cout,
              seen=f"made {chain_code}; verified {ccode}: {short(cout, 200)}")
    held = sorted(p.name for p in root.rglob("*") if p.is_file() and key.encode() in p.read_bytes())
    run.check("operator", "the key the harness sent is in no file the interceptor wrote",
              "a search of every file under the data folder for the key's bytes", expected="none",
              ok=not held, seen=f"files holding it: {held}")


def urllib_quote(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")


def mcp_rpc(server, method: str, params: "dict | None" = None, timeout: float = 300.0) -> dict:
    """One JSON-RPC request to the MCP server over stdio."""
    return server.request(method, params, timeout=timeout)


def mcp_tool(server, name: str, **arguments) -> tuple:
    """(the tool's result, or {} for a refusal, and the refusal's text or
    ""). A tool with an output schema answers `structuredContent`; one
    typed `dict` (nexus_recall, nexus_asof) answers its JSON as text."""
    result = (mcp_rpc(server, "tools/call", {"name": name, "arguments": arguments}).get("result") or {})
    text = " ".join(c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict))
    if result.get("isError"):
        return {}, text
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"], ""
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}, f"the tool answered no JSON: {text[:200]!r}"
    return (parsed if isinstance(parsed, dict) else {"value": parsed}), ""


def section_mcp(run: Run) -> None:
    install, root = run.work / "mcp-install", run.work / "mcp-data"
    owner = MCP_OWNER
    settings = {"LLM_BACKEND": "stub", "NEXUS_MCP_OWNER": owner, "NEXUS_MCP_DATA_ROOT": root,
                "NEXUS_MCP_HANDLE_TIMEOUT": MCP_HANDLE_TIMEOUT_S}
    path, lines = run.install(install, settings)
    written = env_values(path)
    run.check("operator", "install settings: the MCP install's .env (its own data folder and owner)", "; ".join(lines),
              expected="each scripted value on its line", ok=all(written.get(k) == str(v) for k, v in settings.items()),
              seen=f"{path}: {len(settings)} settings set")
    command = run.inp("operator", "command", "python -m backend.mcp (stdio)")
    server = bundle_smoke.Server([sys.executable, "-m", "backend.mcp"], run.env(install), ROOT,
                                 run.work / "mcp-stderr.txt")
    run.mcp = run.mcp + (server,)
    try:
        init = server.initialize()
        names = [t.get("name") for t in (mcp_rpc(server, "tools/list").get("result") or {}).get("tools") or []]
    except (EOFError, OSError, ValueError) as e:
        run.check("operator", "starts the MCP server and lists its tools (initialize, tools/list)", command,
                  expected="the server answers; the pinned seven tools", ok=False,
                  seen=f"{type(e).__name__}: {e}")
        return
    run.check("operator", "starts the MCP server and lists its tools (initialize, tools/list)", command,
              expected=f"the server answers; tools/list is the pinned seven {bundle_smoke.PINNED_TOOLS}",
              ok="result" in init and names == bundle_smoke.PINNED_TOOLS,
              seen=f"{(init.get('result') or {}).get('serverInfo')}; tools {names}")
    status, _ = mcp_tool(server, "nexus_status")
    learning = status.get("learning") or {}
    run.check("tester", "nexus_status", "-", expected=f"the owner {owner}, learning frozen, the coverage counters present",
              ok=status.get("owner") == owner and learning.get("state") == "frozen"
              and (status.get("coverage") or {}).get("present") is True,
              seen=f"owner {status.get('owner')!r}, learning {learning.get('display')!r}, coverage "
                   f"{short(status.get('coverage'), 200)}")
    sentence = run.inp("tester", "sentence", MCP_SENTENCE)
    offer, why = mcp_tool(server, "nexus_assert", sentence=sentence)
    held_before, _ = mcp_tool(server, "nexus_asof", date=instant_after_now())
    run.check("tester", "nexus_assert: the offer, which stores nothing", f"sentence {sentence!r}",
              expected="detected as an assertion, shaped, the triples it would store; the store still holds no claim",
              ok=not why and offer.get("detected_as_assertion") is True and offer.get("shaped") is True
              and bool(offer.get("triples")) and held_before.get("total") == 0,
              seen=f"{short({k: offer.get(k) for k in ('detected_as_assertion', 'shaped', 'triples')}, 300)}; "
                   f"claims held now {held_before.get('total')}{why}")
    flag = run.inp("tester", "confirmed", "false")
    _r, why = mcp_tool(server, "nexus_assert_confirm", sentence=sentence, confirmed=False)
    run.check("tester", "nexus_assert_confirm unconfirmed", f"confirmed: {flag}",
              expected="refused by name (Law 3) and recorded (an event id)",
              ok="refused: Law 3" in why and "recorded, event" in why, seen=why or "not refused")
    flag = run.inp("tester", "confirmed", "true")
    stored, why = mcp_tool(server, "nexus_assert_confirm", sentence=sentence, confirmed=True)
    beliefs = [b for b in stored.get("beliefs") or [] if "belief_id" in b]
    belief_id = beliefs[0]["belief_id"] if beliefs else None
    run.check("tester", "nexus_assert_confirm confirmed", f"confirmed: {flag}",
              expected="the claim stored once, with its belief id",
              ok=not why and stored.get("stored") == 1 and belief_id is not None,
              seen=f"stored {stored.get('stored')}, {short(beliefs, 240)}{why}")
    question = run.inp("tester", "question", MCP_QUESTION)
    got, why = mcp_tool(server, "nexus_retrieve", question=question)
    run.check("tester", "nexus_retrieve: the block to answer from", f"question {question!r}",
              expected="a retrieval id and a block injecting the confirmed claim",
              ok=not why and bool(got.get("retrieval_id")) and belief_id in (got.get("injected") or [])
              and bool(got.get("block")),
              seen=f"retrieval_id {got.get('retrieval_id')}, injected {got.get('injected')}, block "
                   f"{len(got.get('block') or '')} chars{why}")
    answer, model = run.inp("tester", "answer", MCP_ANSWER), run.inp("tester", "model", MCP_MODEL)
    rec, why = mcp_tool(server, "nexus_record", retrieval_id=got.get("retrieval_id", ""), answer=answer, model=model)
    lineage_id = rec.get("lineage_id")
    run.check("tester", "nexus_record: the record sealed for the answer", f"answer {answer!r}, model {model!r}",
              expected="a lineage id and a record hash; mcp_block; coverage recorded 1",
              ok=not why and bool(lineage_id) and bool(rec.get("record_hash")) and rec.get("record_scope") == "mcp_block"
              and (rec.get("coverage") or {}).get("recorded") == 1,
              seen=f"lineage_id {lineage_id}, record_scope {rec.get('record_scope')}, coverage "
                   f"{short(rec.get('coverage'), 200)}{why}")
    reported, why = mcp_tool(server, "nexus_record_action", lineage_id=lineage_id or "",
                             request_hash=hashlib.sha256(b"write notes/qtc.md").hexdigest(), **REPORTED_ACTION)
    run.check("tester", "nexus_record_action: an action the client reports", short(REPORTED_ACTION, 120),
              expected="stored client_reported, completed: NEXUS did not see it happen, and says so",
              ok=not why and reported.get("evidence_class") == "client_reported" and reported.get("state") == "completed"
              and "did not see it happen" in str(reported.get("note")),
              seen=f"{short({k: reported.get(k) for k in ('evidence_class', 'state', 'kind')}, 200)}{why}")
    if belief_id is not None:
        recall, why = mcp_tool(server, "nexus_recall", belief_id=belief_id)
        rows = recall.get("records") or []
        run.check("tester", "nexus_recall on the confirmed claim", f"belief {belief_id}",
                  expected=f"the record, accountable {owner}, labelled asserted, contract version 2",
                  ok=not why and [r.get("lineage_id") for r in rows] == [lineage_id]
                  and rows[0].get("accountable") == owner and rows[0].get("identity_label") == "asserted"
                  and recall.get("contract_version") == 2,
                  seen=f"{short(rows, 300)}; contract_version {recall.get('contract_version')}{why}")
    else:
        run.not_run("tester", "nexus_recall on the confirmed claim", "-", expected="the record",
                    reason="no belief was stored")
    instant = run.inp("tester", "instant", instant_after_now())
    asof, why = mcp_tool(server, "nexus_asof", date=instant)
    run.check("tester", "nexus_asof at an instant with an offset", f"date={instant}",
              expected="the confirmed claim held",
              ok=not why and [c.get("belief_id") for c in asof.get("claims") or []] == [belief_id],
              seen=f"held {[c.get('belief_id') for c in asof.get('claims') or []]}{why}")
    bare = run.inp("tester", "instant", today_utc())
    _a, why = mcp_tool(server, "nexus_asof", date=bare)
    run.check("tester", "nexus_asof at a bare date, refused by name", f"date={bare}",
              expected="refused: bare_date", ok="refused: bare_date" in why, seen=why or "not refused")
    late, why = mcp_tool(server, "nexus_retrieve", question=question)
    time.sleep(MCP_HANDLE_TIMEOUT_S + 1.5)
    _r, refusal = mcp_tool(server, "nexus_record", retrieval_id=late.get("retrieval_id", ""), answer=answer, model=model)
    run.check("tester", f"a retrieval left unrecorded past the handle timeout ({MCP_HANDLE_TIMEOUT_S} s), then recorded",
              f"question {question!r}; a wait of {MCP_HANDLE_TIMEOUT_S + 1.5:g} s",
              expected="the late nexus_record refused by name (unknown_retrieval_id)",
              ok=not why and "refused: unknown_retrieval_id" in refusal, seen=refusal or "not refused")
    status, _ = mcp_tool(server, "nexus_status")
    cov = status.get("coverage") or {}
    run.coverage("mcp: nexus_status, coverage", cov)
    run.check("operator", "nexus_status: the coverage counters", "-",
              expected="recorded 1, retrieved_without_record 1, recorded_without_retrieve 1, and the sentence",
              ok=(cov.get("recorded"), cov.get("retrieved_without_record"), cov.get("recorded_without_retrieve"))
              == (1, 1, 1) and bool(cov.get("sentence")),
              seen=short(cov, 400))
    if lineage_id:
        body = (mcp_rpc(server, "resources/read", {"uri": f"nexus://lineage/{lineage_id}"}).get("result") or {})
        try:
            record = json.loads(((body.get("contents") or [{}])[0]).get("text") or "{}")
        except ValueError:
            record = {}
        run.check("operator", "the record read back: resources/read nexus://lineage/{id}", lineage_id,
                  expected=f"mcp_block, accountable {owner}, learning frozen, no identity mechanism, intact",
                  ok=record.get("record_scope") == "mcp_block" and record.get("accountable_identity") == owner
                  and record.get("learning_mode") == "frozen" and record.get("identity_mechanism") in (None, "asserted")
                  and (record.get("verify") or {}).get("record_intact") is True,
                  seen=f"record_scope {record.get('record_scope')}, accountable_identity "
                       f"{record.get('accountable_identity')!r}, learning_mode {record.get('learning_mode')}, "
                       f"identity_mechanism {record.get('identity_mechanism')}, verify {short(record.get('verify'), 160)}")
    code = server.close()
    run.check("operator", "stops the MCP server (its stdin closed)", "-", expected="exit 0",
              ok=code == 0, seen=f"exit {code}")
    run.keep("the MCP data folder", root)


# --- 3. hosted ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Tester:
    key: str
    login: str
    install: Path
    root: Path
    port: int


def pg_server(database_url: str) -> tuple:
    """(the superuser's connection parameters as libpq reads the URL, or
    None, and why not). The URL is never shown: a line names its host,
    port, user and database."""
    try:
        from psycopg.conninfo import conninfo_to_dict
    except ImportError as e:
        return None, f"the psycopg driver is not installed here ({e}): libpq cannot read the URL"
    try:
        p = conninfo_to_dict(database_url)
    except Exception as e:                       # noqa: BLE001 — libpq's text can quote the URL: not shown
        return None, f"libpq does not read the --database-url ({type(e).__name__}; its text is not shown)"
    return {"host": p.get("host") or "localhost", "port": str(p.get("port") or "5432"),
            "user": p.get("user") or "postgres", "password": p.get("password") or "",
            "dbname": p.get("dbname") or p.get("user") or "postgres"}, ""


def psql_env(run: Run, server: dict) -> dict:
    return run.env(None, PGHOST=server["host"], PGPORT=server["port"], PGUSER=server["user"],
                   PGPASSWORD=server["password"], PGDATABASE=server["dbname"])


def psql(run: Run, server: dict, sql: str) -> tuple:
    """(exit, rows) of SQL run as the superuser through psql, its
    connection in the environment, nothing on its command line."""
    proc = subprocess.run(["psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "--no-password"],
                          input=sql, env=psql_env(run, server), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    rows = [line for line in proc.stdout.splitlines() if line.strip()]
    return proc.returncode, rows if proc.returncode == 0 else [proc.stderr.strip()[-400:]]


def pg_present(run: Run, server: dict, names: list) -> tuple:
    quoted = ", ".join(f"'{n}'" for n in names)
    code, rows = psql(run, server, f"SELECT 'role:' || rolname FROM pg_roles WHERE rolname IN ({quoted}) UNION ALL "
                                   f"SELECT 'database:' || datname FROM pg_database WHERE datname IN ({quoted}) "
                                   f"ORDER BY 1;\n")
    return code, rows


def pg_drop(run: Run, server: dict, databases: list, roles: list) -> tuple:
    sql = "".join(f'DROP DATABASE IF EXISTS "{d}" WITH (FORCE);\n' for d in databases)
    array = ", ".join(f"'{r}'" for r in roles)
    sql += ("DO $$ DECLARE r text; BEGIN FOREACH r IN ARRAY ARRAY[" + array + "] LOOP "
            "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN "
            "EXECUTE format('DROP OWNED BY %I CASCADE', r); EXECUTE format('DROP ROLE %I', r); END IF; "
            "END LOOP; END $$;\n")
    return psql(run, server, sql)


def section_hosted(run: Run) -> None:
    database_url = run.args.database_url
    server, why = (None, "")
    tag = "uat" + secrets.token_hex(3)
    keys = [f"{tag}{key}" for key, _ in TESTERS]
    backup_role = f"nexus_backup_{tag}"
    roles = [f"nexus_{k}" for k in keys]
    passwords = {k: secrets.token_hex(16) for k in keys}
    if database_url:
        server, why = pg_server(database_url)
        shown = (f"a superuser URL: {server['user']} at {server['host']}:{server['port']}, database "
                 f"{server['dbname']} (the password not shown)") if server else "a URL libpq does not read (not shown)"
        run.inp("operator", "--database-url", database_url, shown=shown)
        tools = [t for t in ("psql", "sh") if shutil.which(t) is None]
        if server is None or tools:
            run.not_run("operator", "the testers' databases: deploy/postgres-init.sh run as the superuser",
                        "--database-url", expected="three tester roles and databases, a backup role",
                        reason=why or f"not on PATH: {', '.join(tools)}")
            return
        code, present = pg_present(run, server, roles + [backup_role])
        if code != 0 or present:
            run.check("operator", "the testers' names are this run's own (none exists on the server)", ", ".join(roles),
                      expected="none of the names exists", ok=False, seen=f"exit {code}: {present}")
            return
        init_env = run.env(None, PGHOST=server["host"], PGPORT=server["port"], PGPASSWORD=server["password"],
                           POSTGRES_USER=server["user"], POSTGRES_DB=server["dbname"],
                           NEXUS_TESTERS=run.inp("operator", "NEXUS_TESTERS", " ".join(keys)),
                           NEXUS_BACKUP_ROLE=run.inp("operator", "NEXUS_BACKUP_ROLE", backup_role),
                           NEXUS_BACKUP_PASSWORD=run.inp("operator", "NEXUS_BACKUP_PASSWORD", secrets.token_hex(16),
                                                         shown=generated("secrets.token_hex(16)")))
        for k in keys:
            init_env[f"NEXUS_{k.upper()}_PASSWORD"] = run.inp("operator", f"NEXUS_{k.upper()}_PASSWORD", passwords[k],
                                                              shown=generated("secrets.token_hex(16)"))
        code, out = run.run_script("postgres-init", [shutil.which("sh") or "sh", str(INIT_SCRIPT)], init_env)
        last = (out.strip().splitlines() or [""])[-1]
        secret_shown = [k for k in keys if passwords[k] in out]
        if not run.check("operator", "the testers' databases: deploy/postgres-init.sh run as the superuser",
                         f"sh deploy/postgres-init.sh (NEXUS_TESTERS={' '.join(keys)})",
                         expected="exit 0; its last line names the backup role and each tester's role and database",
                         ok=code == 0 and last.startswith("postgres-init:") and all(r in last for r in roles)
                         and backup_role in last and not secret_shown,
                         seen=f"exit {code}; {last[:300]}" + (f"; a password printed for {secret_shown}" if secret_shown else "")):
            _hosted_cleanup(run, server, roles, backup_role)
            return
    first_child = len(run.children)
    try:
        _hosted_processes(run, server, keys, passwords)
    finally:
        # Whatever the steps found, every process this section started is stopped and, under
        # --database-url, every name it made on the server is dropped and shown gone.
        for child in run.children[first_child:]:
            child.stop()
        for fwd in run.forwarders:
            fwd.close()
        run.forwarders = ()
        if server is not None:
            _hosted_cleanup(run, server, roles, backup_role)


def _hosted_processes(run: Run, server: "dict | None", keys: list, passwords: dict) -> None:
    """The three testers, the two refusal processes, the forwarders, and every step on them."""
    testers = []
    for (key, login), pg_key in zip(TESTERS, keys):
        install, root, port = run.work / f"hosted-{key}-install", run.work / f"hosted-{key}", free_port()
        settings = {"LLM_BACKEND": "stub", "NEXUS_AUTH_MODE": "proxy", "NEXUS_OWNER_IDENTITY": login,
                    "NEXUS_DATA_ROOT": root, "NEXUS_PORT": port}
        shown = {}
        if server is not None:
            settings["NEXUS_STORE"] = "postgres"
            host = f"[{server['host']}]" if ":" in server["host"] else server["host"]
            role = f"nexus_{pg_key}"
            settings["NEXUS_DATABASE_URL"] = f"postgresql://{role}:{passwords[pg_key]}@{host}:{server['port']}/{role}"
            shown["NEXUS_DATABASE_URL"] = (f"postgresql://{role}:***@{host}:{server['port']}/{role} (the password: "
                                           f"NEXUS_{pg_key.upper()}_PASSWORD's, not shown)")
        run.install(install, settings, shown=shown)
        testers.append(Tester(key, login, install, root, port))
        run.keep(f"tester {key}'s data root", root)
    lines = [f"{t.key}: {t.login} on port {t.port}" for t in testers]
    run.check("operator", "install settings: one .env per tester (proxy mode, its owner, data root and port)",
              "; ".join(lines) + ("; NEXUS_STORE=postgres, NEXUS_DATABASE_URL per tester" if server else "; SQLite"),
              expected="three installs, each in proxy mode with its own owner, data root and port",
              ok=all(env_values(t.install / ".env").get("NEXUS_AUTH_MODE") == "proxy" for t in testers),
              seen=", ".join(f"{t.key} {env_values(t.install / '.env').get('NEXUS_OWNER_IDENTITY')}" for t in testers))
    # the two refusal processes: no owner (loopback), and an owner bound beyond loopback
    no_owner = Tester("no-owner", "", run.work / "hosted-no-owner-install", run.work / "hosted-no-owner", free_port())
    run.install(no_owner.install, {"LLM_BACKEND": "stub", "NEXUS_AUTH_MODE": "proxy",
                                   "NEXUS_DATA_ROOT": no_owner.root, "NEXUS_PORT": no_owner.port})
    address, found = host_address()
    beyond = None
    if address is not None:
        beyond = Tester("beyond", NON_LOOPBACK_OWNER, run.work / "hosted-beyond-install", run.work / "hosted-beyond",
                        free_port())
        run.install(beyond.install, {"LLM_BACKEND": "stub", "NEXUS_AUTH_MODE": "proxy",
                                     "NEXUS_OWNER_IDENTITY": NON_LOOPBACK_OWNER, "NEXUS_HOST": address,
                                     "NEXUS_DATA_ROOT": beyond.root, "NEXUS_PORT": beyond.port})
    command = run.inp("operator", "command", "python start.py --serve (one per process)")
    started = time.monotonic()
    procs = [(t, run.start(f"hosted {t.key}", [sys.executable, str(START), "--serve"], run.env(t.install)))
             for t in [*testers, no_owner] + ([beyond] if beyond else [])]
    states = {t.key: child.wait_serving(address if t is beyond else LOOPBACK, t.port) for t, child in procs}
    took = time.monotonic() - started
    children = {t.key: child for t, child in procs}
    for t in testers:
        child = children[t.key]
        health = Http(LOOPBACK, t.port).call("GET", "/healthz").json() if states[t.key] == "serving" else {}
        identity = (health or {}).get("identity") or {}
        out = child.output()
        said = next((ln.strip() for ln in out.splitlines() if ln.strip().startswith("auth ")), "no auth line")
        run.check("operator", f"tester {t.key}'s NEXUS serves in proxy mode", command,
                  expected="serving; start.py says proxy mode and launches with --no-proxy-headers; /healthz: proxy, "
                           "owner set" + (", the store on postgres" if server else ""),
                  ok=states[t.key] == "serving" and identity.get("mode") == "proxy" and identity.get("owner_set") is True
                  and said.startswith("auth        proxy") and "--no-proxy-headers" in out
                  and ((health or {}).get("store") or {}).get("backend") == ("postgres" if server else "sqlite"),
                  seen=f"{states[t.key]} ({took:.1f} s for the {len(procs)} processes); start.py: {said[:150]!r}; /healthz identity "
                       f"{short(identity, 120)}; store {short((health or {}).get('store'), 140)}"
                       + ("" if states[t.key] == "serving" else f"; {child.tail()}"))
    serving = [t for t in testers if states[t.key] == "serving"]
    records, fwds = {}, {}
    for t in serving:
        fwd = fwds[t.key] = run.forwarder(t.port, t.login)
        via = Http(LOOPBACK, fwd.port)
        forged = run.inp("tester", "a forged identity header the client sends", OTHER_LOGIN)
        who = via.call("GET", "/whoami", headers={IDENTITY_HEADER: forged})
        wb = who.json() or {}
        run.check("tester", f"tester {t.key}: GET /whoami through the identity proxy's stand-in",
                  f"{IDENTITY_HEADER}: {forged} from the client (removed by the proxy)",
                  expected=f"200, login {t.login} (the proxy's, never the client's copy), mechanism proxy",
                  ok=who.status == 200 and wb.get("login") == t.login and wb.get("identity_mechanism") == "proxy"
                  and fwd.stripped >= 1,
                  seen=f"{who.status} login {wb.get('login')!r}, mechanism {wb.get('identity_mechanism')}; the proxy removed "
                       f"the identity header lines the client sent: {fwd.stripped}")
        question = run.inp("tester", "question", HOSTED_QUESTION.format(login=t.login))
        reply, frames, done = chat(via, question)
        lid = (done or {}).get("lineage_id")
        rec = (via.call("GET", f"/lineage/{lid}").json() or {}) if lid else {}
        ver = (via.call("GET", f"/lineage/{lid}/verify").json() or {}) if lid else {}
        if lid:
            records[t.key] = lid
        run.check("tester", f"tester {t.key}: a turn through the proxy; its record", f"POST /chat {{\"message\": {question!r}}}",
                  expected=f"200, a done frame; the record's accountable_identity {t.login}, identity_mechanism proxy, "
                           f"intact; the stream relayed in pieces as it came",
                  ok=reply.status == 200 and bool(lid) and rec.get("accountable_identity") == t.login
                  and rec.get("identity_mechanism") == "proxy" and ver.get("verdict") == "intact",
                  seen=f"{reply.status}; lineage_id {lid}; accountable_identity {rec.get('accountable_identity')!r}, "
                       f"identity_mechanism {rec.get('identity_mechanism')}, verify {ver.get('verdict')}; relayed "
                       f"{fwd.relayed} requests in {fwd.pieces} pieces")
    identities = {}
    for t in serving:
        lid = records.get(t.key)
        if lid:
            identities[t.key] = (Http(LOOPBACK, fwds[t.key].port).call("GET", f"/lineage/{lid}").json() or {}).get(
                "accountable_identity")
    run.check("operator", "three testers, three distinct accountable_identity values", "-",
              expected="three records, three logins, each the tester's own",
              ok=len(identities) == 3 and len(set(identities.values())) == 3
              and all(identities[t.key] == t.login for t in serving),
              seen=short(identities, 300))
    crossed = []
    for b in serving:
        via = Http(LOOPBACK, fwds[b.key].port)
        recent = via.call("GET", "/lineage/recent?limit=50").json() or {}
        ids = [r.get("lineage_id") for r in recent.get("records") or []]
        whose = {r.get("accountable_identity") for r in recent.get("records") or []}
        if ids != [records.get(b.key)] or whose != {b.login}:
            crossed.append(f"{b.key} holds {ids} for {sorted(whose, key=str)}")
        for a in serving:
            if a.key != b.key and records.get(a.key):
                status = via.call("GET", f"/lineage/{records[a.key]}").status
                if status != 404:
                    crossed.append(f"{b.key} answers {status} for {a.key}'s record")
    run.check("operator", "no tester's store holds another's record", "-",
              expected="each tester's /lineage/recent holds its own record alone, and another's record is 404 there",
              ok=len(serving) == 3 and not crossed, seen="; ".join(crossed) or "none crossed")
    t1, t2 = (testers[0], testers[1])
    if states[t1.key] == "serving":
        mismatch = run.forwarder(t1.port, t2.login)
        login = run.inp("tester", "another tester's login, presented to tester 1's process", t2.login)
        r = Http(LOOPBACK, mismatch.port).call("GET", "/whoami")
        run.check("tester", "refused: another login (tester 2's, presented to tester 1's process)", login,
                  expected="403, not the owner", ok=r.status == 403 and "forbidden" in str((r.json() or {}).get("detail")),
                  seen=f"{r.status} {short(r.json() or r.text, 200)}")
        # W4's close (V2): the login is compared exactly, case included, so tester 1's own login in another
        # case is another login.
        cased = run.inp("tester", "tester 1's own login in upper case, presented to tester 1's process",
                        t1.login.upper())
        r = Http(LOOPBACK, run.forwarder(t1.port, cased).port).call("GET", "/whoami")
        run.check("tester", "refused: tester 1's own login in another case (compared exactly, case included)", cased,
                  expected="403, not the owner",
                  ok=r.status == 403 and "forbidden" in str((r.json() or {}).get("detail")),
                  seen=f"{r.status} {short(r.json() or r.text, 200)}")
        via = Http(LOOPBACK, fwds[t1.key].port) if t1.key in fwds else None
        if via is not None:
            before = (via.call("GET", "/lineage/recent").json() or {}).get("total")
            claimed = run.inp("tester", "accountable (in the /chat body)", t2.login)
            r = via.call("POST", "/chat", body={"message": run.inp("tester", "question", FOLLOW_UP), "accountable": claimed})
            after = (via.call("GET", "/lineage/recent").json() or {}).get("total")
            run.check("tester", "refused: a /chat body whose accountable differs from the login",
                      f"{{\"accountable\": {claimed!r}}} through tester 1's proxy",
                      expected="422 accountable_mismatch by name; no record written",
                      # Both counts must have been read: two failed reads (None == None)
                      # would otherwise pass as "no record written" (the verifier's V9c).
                      ok=(r.status == 422 and (r.json() or {}).get("error") == "accountable_mismatch"
                          and isinstance(before, int) and before == after),
                      seen=f"{r.status} {short(r.json() or r.text, 200)}; records {before} -> {after}")
        r = Http(LOOPBACK, t1.port).call("GET", "/whoami", headers={})
        run.check("tester", "refused: no identity (straight to tester 1's process, no header)", "no identity header",
                  expected="401, no identity", ok=r.status == 401 and "no identity" in str((r.json() or {}).get("detail")),
                  seen=f"{r.status} {short(r.json() or r.text, 200)}")
        if via is not None:
            _hosted_cross_site(run, t1, via, fwds[t1.key])
        else:
            run.not_run("tester", "refused: a cross-site form POST, and its work unit read back", "-",
                        expected="403, then 200 with the unit first seen", reason="tester 1 has no proxy")
    else:
        run.not_run("tester", "refused: another login, a differing accountable, no identity", "-",
                    expected="403, 422, 401", reason="tester 1's NEXUS did not serve")
        run.not_run("tester", "refused: tester 1's login in another case, a cross-site form POST (and its work unit "
                    "read back)", "-", expected="403, 403, then 200 with the unit first seen",
                    reason="tester 1's NEXUS did not serve")
    if states[no_owner.key] == "serving":
        fwd = run.forwarder(no_owner.port, OTHER_LOGIN)
        r = Http(LOOPBACK, fwd.port).call("GET", "/whoami")
        run.check("tester", "refused: a process with no owner (NEXUS_OWNER_IDENTITY unset)",
                  f"{IDENTITY_HEADER}: {OTHER_LOGIN} through the proxy",
                  expected='403 "owner not set", never an adopted login',
                  ok=r.status == 403 and str((r.json() or {}).get("detail")).startswith("owner not set"),
                  seen=f"{r.status} {short(r.json() or r.text, 200)}")
    else:
        run.check("tester", "refused: a process with no owner (NEXUS_OWNER_IDENTITY unset)", "-",
                  expected="the process serves, and refuses", ok=False,
                  seen=f"{states[no_owner.key]}; {children[no_owner.key].tail()}")
    what = "refused: a non-loopback peer (a fourth process bound beyond loopback, reached at the host's own address)"
    if beyond is None:
        run.not_run("tester", what, "-", expected="401 with and without X-Forwarded-For: 127.0.0.1",
                    reason=f"this host has no non-loopback address: {found}")
    elif states[beyond.key] != "serving":
        run.check("tester", what, f"NEXUS_HOST={address}", expected="the process serves, and refuses", ok=False,
                  seen=f"{states[beyond.key]}; {children[beyond.key].tail()}")
    else:
        far = Http(address, beyond.port)
        plain = far.call("GET", "/whoami", headers={IDENTITY_HEADER: NON_LOOPBACK_OWNER})
        spoof = run.inp("tester", "X-Forwarded-For", "127.0.0.1")
        spoofed = far.call("GET", "/whoami", headers={IDENTITY_HEADER: NON_LOOPBACK_OWNER, "X-Forwarded-For": spoof})
        run.check("tester", what, f"{address}:{beyond.port} with the owner's header; then with X-Forwarded-For: {spoof}",
                  expected="401 both times, the peer rule's (the peer is the socket's, never a forwarded header)",
                  ok=plain.status == 401 and spoofed.status == 401
                  and all("from a loopback peer" in str((r.json() or {}).get("detail")) for r in (plain, spoofed)),
                  seen=f"{plain.status} {short(plain.json() or plain.text, 120)}; with X-Forwarded-For {spoofed.status} "
                       f"{short(spoofed.json() or spoofed.text, 120)} (the host's address {address}: {found})")


# W4's close (the verifier's V1): a page from another site, opened in a tester's browser, posting a form at
# the tester's console. The identity proxy attaches the tester's login to it, as to every request that
# browser sends.
CROSS_SITE_PAGE = "https://evil.example"


def _hosted_cross_site(run: Run, t1: Tester, via: Http, fwd: Forwarder) -> None:
    """W4's close (V1, and the critic's C1: the exit's own coverage of it). A
    cross-site form POST through tester 1's proxy, carrying what a browser
    sends with a form (the forwarder passes Sec-Fetch-Site and Origin through
    as they came), is refused and writes no claim. Then the same request
    from the console's own origin is served, and the product's answer says
    the work unit the forged one named is first seen then: the forged one
    made none."""
    unit = run.inp("tester", "work unit named in a forged form's action URL", f"WU-forged-{secrets.token_hex(3)}")
    target = f"/kb/seed/offline?confirmed=true&work_unit={unit}"
    page = run.inp("tester", "Origin of the page from another site that the tester opened", CROSS_SITE_PAGE)
    before = (via.call("GET", "/kb/stats").json() or {}).get("total")
    r = via.call("POST", target, headers={"Origin": page, "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                                          "Sec-Fetch-Dest": "document",
                                          "Content-Type": "application/x-www-form-urlencoded"})
    after = (via.call("GET", "/kb/stats").json() or {}).get("total")
    detail = str((r.json() or {}).get("detail"))
    run.check("tester", "refused: a cross-site form POST through tester 1's proxy (a page from another site, open in "
                        "the tester's browser, whose request the proxy gives the tester's login)",
              f"POST {target} as a form from {page} (Origin: {page}, Sec-Fetch-Site: cross-site)",
              expected="403, the cross-site refusal, naming neither the page's origin nor the login; no claim written",
              ok=(r.status == 403 and "same-origin" in detail and "evil.example" not in detail
                  and t1.login not in detail and isinstance(before, int) and before == after),
              seen=f"{r.status} {short(r.json() or r.text, 200)}; claims {before} -> {after}")
    own = f"http://{LOOPBACK}:{fwd.port}"
    r = via.call("POST", target, headers={"Origin": own, "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors",
                                          "Sec-Fetch-Dest": "empty"})
    attribution = (r.json() or {}).get("attribution") or {}
    run.check("tester", "the forged request's work unit was not created: the same request from the console's own "
                        "origin is served, and the product says the unit is first seen then",
              f"POST {target} from the console (Origin: {own}, Sec-Fetch-Site: same-origin)",
              expected=f"200; attribution work_unit_id {unit}, unit_first_seen true",
              ok=(r.status == 200 and attribution.get("work_unit_id") == unit
                  and attribution.get("unit_first_seen") is True),
              seen=f"{r.status}; attribution {short(attribution, 200)}")


def _hosted_cleanup(run: Run, server: dict, roles: list, backup_role: str) -> None:
    code, out = pg_drop(run, server, roles, roles + [backup_role])
    pcode, present = pg_present(run, server, roles + [backup_role])
    run.check("operator", "the testers' databases and roles dropped as the superuser, and gone", ", ".join(roles + [backup_role]),
              expected="dropped; none of the names left on the server",
              ok=code == 0 and pcode == 0 and present == [], seen=f"drop exit {code}; left {present}")


# --- 4. backup and restore ---------------------------------------------------------------------------------

def section_backup(run: Run) -> None:
    console = needs_console(run, "backup and restore")
    if console is None:
        return
    out = run.work / "backups"
    command = run.inp("operator", "command", f"python scripts/backup.py --data-root {console.root} --out {out}")
    code, text = run.run_script("backup", [sys.executable, str(BACKUP), "--data-root", str(console.root), "--out", str(out)],
                                run.env(console.install))
    folder = next((line.split("backup: ", 1)[1].strip() for line in text.splitlines() if line.startswith("backup: ")), "")
    first = (text.splitlines() or [""])[0]
    run.check("operator", "backup: scripts/backup.py on the console's running store", command,
              expected="exit 0; its first line names the data root (law 15); one backup folder with its manifest",
              ok=code == 0 and first.startswith("data root: ") and names_path(first, console.root) and bool(folder)
              and (Path(folder) / "MANIFEST.json").is_file() and console.child.proc.poll() is None,
              seen=f"exit {code}; first line {first[:160]!r}; backup {folder}; the console still serving "
                   f"{console.child.proc.poll() is None}")
    if not folder:
        run.not_run("operator", "restore verification and the restore into service", "-",
                    expected="a backup folder", reason="the backup wrote no folder")
        return
    command = run.inp("operator", "command", f"python scripts/restore_verify.py {folder}")
    code, text = run.run_script("restore_verify", [sys.executable, str(RESTORE_VERIFY), folder], run.env(console.install))
    verdict = next((line for line in text.splitlines() if line.startswith("restore verification:")), "")
    checks = [line[:4] + " " + line[5:7] for line in text.splitlines() if re.match(r"^(PASS|FAIL) R\d", line)]
    run.check("operator", "restore verification: scripts/restore_verify.py on the backup", command,
              expected="exit 0; 5 of 5 checks passed", ok=code == 0 and verdict.startswith("restore verification: 5 of 5"),
              seen=f"exit {code}; {verdict[:200]}; {checks}")
    code, lock = stop_console(run, console)
    run.ctx = dataclasses.replace(run.ctx, console_stopped=True, lock_holder_pid=lock.get("pid"))
    run.check("operator", "stops the console before restoring (docs/OPERATOR_RUNBOOK.md)", "Ctrl+C"
              if os.name != "nt" else "taskkill /T /F (a forced stop: start.py waits on the server on Windows)",
              expected="stopped" + ("; the lock released" if os.name != "nt" else "; the lock left for the next start"),
              ok=console.child.proc.poll() is not None and (os.name == "nt" or (code == 0 and lock.get("released") is True)),
              seen=f"exit {code}; kb.db.lock {short(lock, 200)}")
    restored = run.work / "restored"
    restored.mkdir(parents=True, exist_ok=True)
    copied = []
    for item in sorted(Path(folder).iterdir()):
        if item.name == "MANIFEST.json":
            continue
        if item.is_dir():
            shutil.copytree(item, restored / item.name)
        else:
            shutil.copy2(item, restored / item.name)
        copied.append(item.name)
    run.check("operator", "restore into service: the backup's files (all but MANIFEST.json) into a fresh data root",
              run.inp("operator", "restored data root", restored),
              expected="kb.db and archive.db among them; no lock file (a backup never holds one)",
              ok={"kb.db", "archive.db"} <= set(copied) and "kb.db.lock" not in copied, seen=f"{restored}: {copied}")
    port = free_port()
    command = run.inp("operator", "command", f"python start.py --serve --data-root {restored} --port {port}")
    rconsole, state, took = start_console(run, "restored console", console.install, restored, port, console.token,
                                          "--data-root", str(restored), "--port", str(port))
    ok = state == "serving"
    run.check("operator", "restore into service: a console started on the restored data root", command,
              expected="serving", ok=ok, seen=f"{state} in {took:.1f} s" + ("" if ok else f"; {rconsole.child.tail()}"))
    if ok:
        http_ = rconsole.http()
        found, bad = [], []
        for lid, digest in run.ctx.console_records:
            rec = http_.call("GET", f"/lineage/{lid}").json() or {}
            ver = (http_.call("GET", f"/lineage/{lid}/verify").json() or {}).get("verdict")
            (found if rec.get("record_hash") == digest and ver == "intact" else bad).append(lid)
        run.check("operator", "restore into service: every record the console had is there and verifies",
                  f"GET /lineage/{{id}} and /lineage/{{id}}/verify for {len(run.ctx.console_records)} records",
                  expected="each one, its record hash the console's, intact",
                  ok=bool(run.ctx.console_records) and not bad and len(found) == len(run.ctx.console_records),
                  seen=f"{len(found)} of {len(run.ctx.console_records)} there and intact; not: {bad}")
        verify = http_.call("GET", "/lineage/verify").json() or {}
        chain = http_.call("GET", "/chain/status").json() or {}
        run.check("operator", "restore into service: GET /lineage/verify and GET /chain/status", "-",
                  expected="intact; the chain valid", ok=verify.get("verdict") == "intact"
                  and (chain.get("verification") or {}).get("valid") is True,
                  seen=f"lineage {verify.get('verdict')} ({verify.get('records_checked')} checked); chain "
                       f"{short(chain.get('verification'), 120)}, {chain.get('n_blocks')} blocks")
    rconsole.child.stop()
    run.keep("the restored data root", restored)


# --- 5. upgrade -------------------------------------------------------------------------------------------------

def section_upgrade(run: Run) -> None:
    manifest = json.loads((FIXTURE / "MANIFEST.json").read_text(encoding="utf-8"))
    root = run.work / "upgrade"
    copied = {}
    for name, digest in manifest["files"].items():
        target = root / Path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FIXTURE / Path(name), target)
        copied[name] = hashlib.sha256(target.read_bytes()).hexdigest() == digest
    run.check("operator", "upgrade: a copy of tests/fixtures/pre_run_data/ as a data root",
              run.inp("operator", "the data root to upgrade", f"a copy of tests/fixtures/pre_run_data/ at {root}"),
              expected="every file the manifest names, each hashing to its sha256",
              ok=bool(copied) and all(copied.values()), seen=short(copied, 300))
    install, port, token = run.work / "upgrade-install", free_port(), secrets.token_urlsafe(32)
    run.install(install, {"LLM_BACKEND": "stub", "NEXUS_API_TOKEN": token, "NEXUS_DATA_ROOT": root, "NEXUS_PORT": port},
                shown={"NEXUS_API_TOKEN": generated("secrets.token_urlsafe(32)")})
    command = run.inp("operator", "command", f"python start.py --serve (NEXUS_DATA_ROOT={root})")
    console, state, took = start_console(run, "upgrade console", install, root, port, token)
    out = console.child.output()
    ok = state == "serving"
    valid = f"archive chain valid ({manifest['chain']['blocks']} blocks)"
    run.check("operator", "upgrade: today's console started on the copy", command,
              expected=f"serving; start.py verified the archive chain read-only first ({valid})",
              ok=ok and valid in out,
              seen=f"{state} in {took:.1f} s; {[ln.strip() for ln in out.splitlines() if 'archive chain' in ln][:1]}"
                   + ("" if ok else f"; {console.child.tail()}"))
    if not ok:
        return
    http_ = console.http()
    wanted = list(manifest["turns"]["console"]) + list(manifest["turns"]["mcp"])
    got = {lid: http_.call("GET", f"/lineage/{lid}").json() or {} for lid in wanted}
    hashes = sorted(r.get("record_hash") or "" for r in got.values())
    digest = hashlib.sha256("\n".join(hashes).encode()).hexdigest()
    run.check("operator", "upgrade: every record the manifest names, read through GET /lineage/{id}",
              f"{len(wanted)} lineage ids from MANIFEST.json",
              expected="each found; the record hashes, sorted, the manifest's; their digest the manifest's",
              ok=all(got.values()) and hashes == manifest["lineage"]["record_hashes"]
              and digest == manifest["lineage"]["record_hashes_sha256"],
              seen=f"{sum(1 for r in got.values() if r)} found; hashes equal {hashes == manifest['lineage']['record_hashes']}; "
                   f"digest {digest[:16]}… (the manifest's {manifest['lineage']['record_hashes_sha256'][:16]}…)")
    each = {lid: (http_.call("GET", f"/lineage/{lid}/verify").json() or {}).get("verdict") for lid in wanted}
    run.check("operator", "upgrade: GET /lineage/{id}/verify for each", "-", expected="intact, each",
              ok=set(each.values()) == {"intact"}, seen=short(each, 400))
    verify = http_.call("GET", "/lineage/verify").json() or {}
    chain = http_.call("GET", "/chain/status").json() or {}
    latest = chain.get("latest_block") or {}
    blocks, head = manifest["chain"]["blocks"], manifest["chain"]["head_hash"]
    # The console runs a KAIROS cycle as it starts, and one whose snapshot is due (the fixture's is from
    # 31 October; the interval is 30 days) appends a block: the fixture's chain is then unchanged when the
    # new block chains onto the manifest's head.
    kept = (chain.get("n_blocks") == blocks and latest.get("block_hash") == head) or \
        (chain.get("n_blocks") == blocks + 1 and latest.get("previous_hash") == head)
    run.check("operator", "upgrade: GET /lineage/verify and GET /chain/status", "-",
              expected=f"intact, {manifest['lineage']['count']} records; the chain valid, the manifest's {blocks} blocks "
                       f"unchanged (its head the latest, or the latest's previous when the start appended a block)",
              ok=verify.get("verdict") == "intact" and verify.get("records_checked") == manifest["lineage"]["count"]
              and (chain.get("verification") or {}).get("valid") is True and kept,
              seen=f"lineage {verify.get('verdict')} ({verify.get('records_checked')}); chain "
                   f"{(chain.get('verification') or {}).get('valid')}, {chain.get('n_blocks')} blocks, latest "
                   f"{str(latest.get('block_hash'))[:16]}… (previous {str(latest.get('previous_hash'))[:16]}…; "
                   f"the manifest's head {head[:16]}…)")
    gov = http_.call("GET", "/governance/verify").json() or {}
    held = [(i["held"], ((http_.call("GET", "/kb/as-of?date=" + i["iso"].replace("+", "%2B")).json() or {}).get("total")))
            for i in manifest["as_of_instants"]]
    run.check("operator", "upgrade: GET /governance/verify and GET /kb/as-of at the manifest's three instants",
              ", ".join(i["iso"] for i in manifest["as_of_instants"]),
              expected="governance intact (the base's one -0.0 row resolved); as-of holding what it held at capture",
              ok=gov.get("verdict") == "intact" and all(then == now for then, now in held),
              seen=f"governance {gov.get('verdict')} (zero_sign_legacy {(gov.get('row') or {}).get('zero_sign_legacy')}); "
                   f"held at capture / now {held}")
    console.child.stop()
    run.keep("the upgraded copy of the pre-run fixture", root)


# --- 6. uninstall ------------------------------------------------------------------------------------------------

def section_uninstall(run: Run) -> None:
    console = needs_console(run, "uninstall")
    if console is None:
        return
    stopped = run.stop_all()
    live = run.live()
    # Read after the stop: a forced stop leaves the holder's pid in the file, and on win32 a live holder's file is
    # not read here at all.
    pid_before = run.ctx.lock_holder_pid or read_lock(console.root).get("pid")
    run.check("operator", "uninstall: every process the run started is stopped",
              "Ctrl+C" if os.name != "nt" else "taskkill /T /F",
              expected="no process left", ok=not live, seen=f"stopped now {stopped}; still running {[c.label for c in live]}")
    lock = read_lock(console.root)
    if os.name != "nt":
        run.check("operator", "uninstall: the console's lock released by its stop", "-",
                  expected="kb.db.lock says released", ok=lock.get("released") is True, seen=short(lock, 200))
    else:
        run.note("On win32 a console is stopped by force as a tree (start.py waits on the server there), so its lock "
                 "is left for the next start, which takes it over and logs it (docs/OPERATOR_RUNBOOK.md): the "
                 "uninstall section reads the takeover from kb.db.lock.log instead of asserting a release.")
    installs = sorted(p for p in run.work.iterdir() if p.is_dir() and p.name.endswith("install"))
    for folder in installs:
        shutil.rmtree(folder)
    run.check("operator", "uninstall: the install's settings removed", ", ".join(str(p) for p in installs),
              expected="every install folder and its .env gone", ok=bool(installs) and not any(p.exists() for p in installs),
              seen=f"removed {[p.name for p in installs]}")
    kept = [(what, path, (Path(path) / "kb.db").is_file() or any(Path(path).glob("*"))) for what, path in run.ledger.kept]
    run.check("operator", "uninstall: the data roots kept (NEXUS never deletes a store)", "-",
              expected="every data root the run made still there, its store intact on disk",
              ok=bool(kept) and all(there for _w, _p, there in kept),
              seen="; ".join(f"{what}: {path} {'kept' if there else 'GONE'}" for what, path, there in kept))
    reinstall, port, token = run.work / "reinstall", free_port(), secrets.token_urlsafe(32)
    run.install(reinstall, {"LLM_BACKEND": "stub", "NEXUS_API_TOKEN": token, "NEXUS_DATA_ROOT": console.root,
                            "NEXUS_PORT": port}, shown={"NEXUS_API_TOKEN": generated("secrets.token_urlsafe(32)")})
    run.ctx = dataclasses.replace(run.ctx, reinstall=reinstall)
    before = len(lock_log(console.root))
    command = run.inp("operator", "command", "python start.py --serve (a fresh install on the kept data root)")
    fresh, state, took = start_console(run, "reinstalled console", reinstall, console.root, port, token)
    ok = state == "serving"
    run.check("operator", "uninstall: a fresh install on the kept console data root serves", command,
              expected="serving: no lock left that blocks a reinstall", ok=ok,
              seen=f"{state} in {took:.1f} s" + ("" if ok else f"; {fresh.child.tail()}"))
    if ok:
        http_ = fresh.http()
        same, bad = [], []
        for lid, digest in run.ctx.console_records:
            rec = http_.call("GET", f"/lineage/{lid}").json() or {}
            ver = (http_.call("GET", f"/lineage/{lid}/verify").json() or {}).get("verdict")
            (same if rec.get("record_hash") == digest and ver == "intact" else bad).append(lid)
        run.check("operator", "uninstall: the fresh install serves the same records, each verifying",
                  f"GET /lineage/{{id}} and /lineage/{{id}}/verify for {len(run.ctx.console_records)} records",
                  expected="every record the console had, its hash unchanged, intact",
                  ok=bool(run.ctx.console_records) and not bad, seen=f"{len(same)} the same and intact; not: {bad}")
        taken = [e for e in lock_log(console.root)[before:] if e.get("event") == "stale_lock_taken_over"]
        if os.name == "nt":
            run.check("operator", "uninstall: the forced stop's lock taken over and logged (kb.db.lock.log)", "-",
                      expected="one takeover naming the stopped console's server",
                      ok=len(taken) == 1 and (taken[0].get("previous") or {}).get("pid") == pid_before,
                      seen=f"{len(taken)} takeover(s): {short(taken, 300)}; the stopped holder pid {pid_before}")
        else:
            run.check("operator", "uninstall: nothing to take over (the stop released the lock)", "-",
                      expected="no takeover logged", ok=not taken, seen=f"{len(taken)} takeover(s) logged")
    fresh.child.stop()


# --- 7. operator scripts ------------------------------------------------------------------------------------

def _files(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.md5(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def section_operator(run: Run) -> None:
    console = needs_console(run, "operator scripts")
    if console is None:
        return
    if console.child.proc.poll() is None:
        console.child.stop()
    copy = run.work / "operator-copy"
    shutil.copytree(console.root, copy, ignore=shutil.ignore_patterns("kb.db.lock", "kb.db.lock.log"))
    kb, reports = copy / "kb.db", run.work / "operator-reports"
    reports.mkdir(exist_ok=True)
    install = run.ctx.reinstall
    if install is None or not (install / ".env").is_file():
        install = run.work / "operator-install"
        run.install(install, {"LLM_BACKEND": "stub", "NEXUS_DATA_ROOT": copy, "NEXUS_PORT": free_port()})
    run.check("operator", "a stopped copy of the console store", run.inp("operator", "the copy", copy),
              expected="the console stopped; its store files copied", ok=console.child.proc.poll() is not None and kb.is_file(),
              seen=f"{copy}: {sorted(_files(copy))[:8]}")
    absent = copy / "absent.jsonl"
    scripts = [
        ("start.py --check", [str(START), "--check", "--data-root", str(copy)], "archive.db"),
        ("scripts/replay_check.py (dry run)", [str(ROOT / "scripts" / "replay_check.py"), "--db", str(kb), "--report-dir",
                                              str(reports), "--capture", str(absent)], "kb.db"),
        ("scripts/cp_b49_tally.py", [str(ROOT / "scripts" / "cp_b49_tally.py"), "--db", str(kb), "--capture", str(absent)],
         "kb.db"),
        ("scripts/as2_instance_windows.py", [str(ROOT / "scripts" / "as2_instance_windows.py"), "--db", str(kb)], "kb.db"),
        ("diag_downstream.py", [str(ROOT / "scripts" / "diag_downstream.py"), "--db", str(kb)], "kb.db"),
        ("scripts/archive_pregate.py (dry run)", [str(ROOT / "scripts" / "archive_pregate.py"), "--kb", str(kb),
                                                 "--report", str(reports / "pregate.json")], "kb.db"),
        ("scripts/archive_consolidation.py (dry run)", [str(ROOT / "scripts" / "archive_consolidation.py"), "--kb", str(kb),
                                                       "--source", "hippocampus_consolidation", "--report",
                                                       str(reports / "consolidation.json")], "kb.db"),
    ]
    for label, argv, store in scripts:
        before = _files(copy)
        shown = " ".join(["python", Path(argv[0]).relative_to(ROOT).as_posix(), *argv[1:]])
        code, out = run.run_script(label, [sys.executable, *argv], run.env(install))
        first = (out.splitlines() or [""])[0]
        named = names_path(first, copy / store)
        changed = sorted(k for k, v in _files(copy).items() if before.get(k) != v) + sorted(set(before) - set(_files(copy)))
        run.check("operator", f"{label} on the stopped copy", run.inp("operator", "command", shown),
                  expected="exit 0; its first line names the store it opened (law 15); the copy unchanged",
                  ok=code == 0 and named and not changed,
                  seen=f"exit {code}; first line {first[:200]!r}; changed {changed}"
                       + ("" if code == 0 else f"; {' | '.join(out.strip().splitlines()[-4:])[:300]}"))
    for label, why in (("scripts/migrate_sqlite_to_postgres.py", "its target is a Postgres database it would write"),
                       ("scripts/export_postgres_to_sqlite.py", "its source is a Postgres store")):
        run.not_run("operator", f"{label} on the stopped copy", "-", expected="exit 0 in its dry form",
                    reason=f"not run here: {why}; its round trip is tests/test_w3b_migration.py's, in the Postgres job",
                    required=False)
    run.keep("the operator scripts' copy of the console store", copy)


SECTIONS = (
    ("console", "console (the stub model)", section_console),
    ("verification", "verification (decision credentials, nexus-verify, commit now and reveal later)",
     section_verification),
    ("mcp", "mcp (a scripted stdio client)", section_mcp),
    ("hosted", "hosted (three testers behind a stand-in for the identity proxy)", section_hosted),
    ("backup", "backup and restore", section_backup),
    ("upgrade", "upgrade (the pre-run data fixture)", section_upgrade),
    ("uninstall", "uninstall", section_uninstall),
    ("operator", "operator scripts", section_operator),
    ("actions", "actions (a search recorded, signed, sealed and verified by a stranger)", section_actions),
    ("interceptor", "interceptor (a scripted harness through the interceptor to a stub provider)", section_interceptor),
)
NEEDS = {"verification": ("console",), "backup": ("console",), "uninstall": ("console",), "operator": ("console",)}


# --- the report ------------------------------------------------------------------------------------------------

def cell(text) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def git_head() -> str:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return f"not known ({type(e).__name__})"
    return proc.stdout.strip() or "not a git checkout"


def verdict_lines(ledger: Ledger, selected: list) -> list:
    failed, unrun = ledger.failed(), ledger.unrun_required()
    passed = sum(1 for s in ledger.steps if s.result == "PASS")
    lines = [f"uat-simulate: {PLATFORM}: {passed} of {len(ledger.steps)} steps passed in {', '.join(selected)}"]
    if failed:
        lines.append(f"uat-simulate: {len(failed)} step(s) FAILED: "
                     + "; ".join(f"{s.id} ({s.what[:80]})" for s in failed))
    if unrun:
        lines.append(f"uat-simulate: {len(unrun)} required step(s) could not run (not run): "
                     + "; ".join(f"{s.id} ({s.reason[:120]})" for s in unrun))
    optional = [s for s in ledger.steps if s.result == NOT_RUN and not s.required]
    if optional:
        lines.append(f"uat-simulate: {len(optional)} step(s) not run and not required: " + ", ".join(s.id for s in optional))
    lines.append(f"uat-simulate: result {'FAIL' if failed or unrun else 'PASS'} "
                 f"(exit {EXIT_FAIL if failed or unrun else EXIT_PASS})")
    return lines


def render(ledger: Ledger, *, selected: list, added: list, argv: list, work: Path, kept: bool, started: datetime,
           seconds: float) -> str:
    out = [f"# NEXUS simulated UAT: {PLATFORM}", "",
           f"- when: {started.strftime('%Y-%m-%dT%H:%M:%SZ')} ({seconds:.0f} s)",
           f"- commit: {git_head()}",
           f"- python: {sys.version.split()[0]} on {PLATFORM}",
           f"- command: python scripts/uat_simulate.py {' '.join(argv)}".rstrip(),
           f"- sections run: {', '.join(selected)}" + (f" ({', '.join(added)} because a selected section needs it)"
                                                       if added else ""),
           f"- sections not selected: {', '.join(n for n, _t, _f in SECTIONS if n not in selected) or 'none'}",
           f"- work folder: {work} ({'kept' if kept else 'removed afterwards'})",
           f"- result: {'FAIL' if ledger.failed() or ledger.unrun_required() else 'PASS'}", ""]
    for number, (name, title, _fn) in enumerate(SECTIONS, 1):
        rows = [s for s in ledger.steps if s.id.startswith(f"{name}.")]
        if name not in selected and not rows:
            continue
        out += [f"## {number}. {title}", "", "| step | who | what they do | scripted input | expected | seen | result |",
                "|---|---|---|---|---|---|---|"]
        for s in rows:
            result = s.result if s.result != NOT_RUN else (f"{NOT_RUN} ({cell(s.reason).replace(')', ']')}"
                                                           f"{'' if s.required else '; not required'})")
            out.append(f"| {s.id} | {s.who} | {cell(s.what)} | {cell(s.scripted_input)} | {cell(s.expected)} | "
                       f"{cell(s.seen)} | {result} |")
        out.append("")
    extra = [s for s in ledger.steps if s.id.split(".")[0] not in {n for n, _t, _f in SECTIONS}]
    if extra:
        out += ["## Other steps", "", "| step | who | what they do | scripted input | expected | seen | result |",
                "|---|---|---|---|---|---|---|"]
        for s in extra:
            result = s.result if s.result != NOT_RUN else (f"{NOT_RUN} ({cell(s.reason).replace(')', ']')}"
                                                           f"{'' if s.required else '; not required'})")
            out.append(f"| {s.id} | {s.who} | {cell(s.what)} | {cell(s.scripted_input)} | {cell(s.expected)} | "
                       f"{cell(s.seen)} | {result} |")
        out.append("")
    out += ["## Scripted inputs", "",
            "Every input a tester or an operator supplied in this run, in the order first used: the settings each "
            "install's .env holds, the questions, sentences, confirm flags, instants, logins and commands. A secret "
            "the run generated is described, never shown.", "",
            "| who | input | value | used in |", "|---|---|---|---|"]
    out += [f"| {x.who} | {cell(x.name)} | {cell(x.shown)} | {', '.join(x.used_in)} |" for x in ledger.inputs]
    out += ["", "## Coverage counters, as the product reports them", ""]
    out += [f"- {where}: `{json.dumps(value, sort_keys=True)}`" for where, value in ledger.coverage] or \
        ["- none read in this run (the console and mcp sections report them)"]
    out += ["", "## The stores this run made", "",
            "NEXUS never deletes a store: the data roots below were kept by every section, uninstall included "
            "(removing the install's settings leaves the data). They go with the work folder, which is the "
            "simulation's own " + ("and was kept." if kept else "and was removed when the run ended."), ""]
    out += [f"- {what}: {path}" for what, path in ledger.kept] or ["- none"]
    if ledger.notes:
        out += ["", "## Notes", ""] + [f"- {n}" for n in ledger.notes]
    out += ["", "## Result", ""] + verdict_lines(ledger, selected)
    return "\n".join(out) + "\n"


# --- main ------------------------------------------------------------------------------------------------------

def shown_argv(argv: list) -> list:
    out, hide = [], False
    for a in argv:
        if hide:
            out.append("<the superuser URL: not shown>")
            hide = False
        elif a == "--database-url":
            out.append(a)
            hide = True
        elif a.startswith("--database-url="):
            out.append("--database-url=<the superuser URL: not shown>")
        else:
            out.append(a)
    return out


def main(argv: "list[str] | None" = None) -> int:
    force_utf8_stdio()
    raw = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT, help=f"where the report goes (default {DEFAULT_REPORT})")
    ap.add_argument("--work", type=Path, default=None, help="the run's own folder: new or empty (default: a temporary one)")
    ap.add_argument("--keep-work", action="store_true", help="keep the work folder (and every store in it) afterwards")
    ap.add_argument("--database-url", default=None,
                    help="a superuser postgresql:// URL: the hosted section's testers get databases made by "
                         "deploy/postgres-init.sh on that server, dropped at the end (never shown)")
    ap.add_argument("--sections", default=None,
                    help="a comma-separated subset of: " + ", ".join(n for n, _t, _f in SECTIONS))
    args = ap.parse_args(raw)
    names = [n for n, _t, _f in SECTIONS]
    wanted = [s.strip() for s in args.sections.split(",") if s.strip()] if args.sections else list(names)
    unknown = [s for s in wanted if s not in names]
    if unknown:
        print(f"uat-simulate: REFUSED: unknown section(s) {unknown}; the sections are {names}")
        return EXIT_USAGE
    added = sorted({d for s in wanted for d in NEEDS.get(s, ()) if d not in wanted}, key=names.index)
    selected = [n for n in names if n in wanted or n in added]
    if args.work is not None:
        work = args.work.resolve()
        if work.exists() and (not work.is_dir() or any(work.iterdir())):
            print(f"uat-simulate: REFUSED: --work {work} is not a new or empty folder (the run removes its folder "
                  f"afterwards unless --keep-work, so it never takes one that holds anything)")
            return EXIT_USAGE
        work.mkdir(parents=True, exist_ok=True)
    else:
        work = Path(tempfile.mkdtemp(prefix="nexus-uat-")).resolve()
    # Every script loads .env (the Packet U ratchet). This one's search is pointed at an empty folder of the run's
    # own first, so nothing of the operator's is read: each child is configured by its install's .env alone.
    os.environ["NEXUS_ENV_ROOT"] = str(work / "own-env")
    (work / "own-env").mkdir(exist_ok=True)
    load_env()
    started, clock = datetime.now(timezone.utc), time.monotonic()
    print(f"uat-simulate: {PLATFORM}; sections {', '.join(selected)}; work {work}; report {args.report}", flush=True)
    run = Run(work, args)
    code = EXIT_PASS
    try:
        for name, title, fn in SECTIONS:
            if name not in selected:
                continue
            run.section, run.number = name, 0
            print(f"uat-simulate: section {name}: {title}", flush=True)
            try:
                fn(run)
            except Exception as e:                   # a crash is a failed step, said with its cause
                run.check("operator", "the section could not complete", "-", expected="the section runs to its end",
                          ok=False, seen=f"{type(e).__name__}: {e}")
    except KeyboardInterrupt:
        run.section, run.number = "interrupted", 0
        run.check("operator", "the run was interrupted (Ctrl+C)", "-", expected="the run ends by itself", ok=False,
                  seen="KeyboardInterrupt")
        code = EXIT_INTERRUPTED
    finally:
        leftover = run.stop_all()
        if leftover:
            run.note(f"stopped at the end of the run: {leftover}")
    text = render(run.ledger, selected=selected, added=added, argv=shown_argv(raw), work=work, kept=args.keep_work,
                  started=started, seconds=time.monotonic() - clock)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(text, encoding="utf-8")
    for line in verdict_lines(run.ledger, selected):
        print(line, flush=True)
    print(f"uat-simulate: report {args.report}", flush=True)
    if not args.keep_work:
        for attempt in range(5):
            try:
                shutil.rmtree(work)
                break
            except OSError as e:                     # Windows: a handle released a moment after its process
                if attempt == 4:
                    print(f"uat-simulate: the work folder {work} could not be removed ({type(e).__name__}: {e})",
                          flush=True)
                time.sleep(2)
    if code == EXIT_INTERRUPTED:
        return code
    return EXIT_FAIL if run.ledger.failed() or run.ledger.unrun_required() else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
