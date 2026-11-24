#!/usr/bin/env python3
"""NEXUS startup: check the environment, then launch the console.

    python start.py                   check, then launch on 127.0.0.1:8000
    python start.py --port 8100       another port (beats NEXUS_PORT)
    python start.py --data-root D     every console file under D (NEXUS_DATA_ROOT)
    python start.py --dev             uvicorn --reload (development only)
    python start.py --check           the checks only: launches nothing, writes no data
    python start.py --serve           no prompt: the checks, then the launch (a container's
                                      entrypoint; W3)

Run it from the repo root. It CHECKS the model server (Ollama, or LM Studio
when LLM_BACKEND=lmstudio) and names the reason when it cannot reach it. It
never starts one: start Ollama yourself (`ollama serve`, or the Ollama app).
Its first line names the database it reads (law 15). Enter is No at every
prompt (law 6). A launch that nothing sets NEXUS_LEARNING for is frozen
(law 12), and says so.

Exit codes: 0 launched (on Windows, the server's own status once it ends),
or every check passed and the operator declined only the launch itself (the
command to run it was printed); 1 the operator declined, or gave no answer
(EOF: a pipe, a container) to, a step the launch needs: "Begin startup
sequence?", the model-server choice when creating .env, the venv choices, or
"Start NEXUS now?" after a check failed and was continued past (nothing
passed, nothing launched); 2 a usage error (a bad --port or NEXUS_PORT); 3
the model server is not reachable; 4 a dependency does not import (a line
of requirements.txt in this interpreter, or the launch target,
`backend.server`, in a child with the launch's environment); 5 the
archive chain does not verify, or cannot be verified; 6 Python older than
3.11; 130 interrupted (Ctrl+C at any prompt: 128 + SIGINT, the shell's
convention). The table is the contract: tests/test_w1_start.py's C-series
drives each code through the script. LLM_BACKEND names `ollama`, `lmstudio`,
`stub` (W3: the deterministic provider for CI and the `stub` compose
profile, with no model server to check), `openai` (an OpenAI-compatible
endpoint) or `anthropic` (the Messages API; W3 part 2, the `api` profile:
the model each asks for is named by OPENAI_MODEL or ANTHROPIC_MODEL, and an
unset one is the same usage error); any other value is a usage error here
(2) and is refused by name by the server itself at startup. NEXUS_AUTH_MODE
(W4) is `token` (unset: the bearer token, as before) or `proxy` (an identity
proxy in front supplies the login; the launch then runs uvicorn with
--no-proxy-headers, and says the mode, the header and whether the owner is
set); any other value is the same usage error, in --serve, --check and the
interactive flow alike, and the server refuses it by name too.

--serve (W3, run/specs/W3.md D1) is the non-interactive launch, a
container's entrypoint: it answers no prompt and never writes .env; it runs
the checks that need no answer and leaves with the first failure's code (2
a usage error, an unknown LLM_BACKEND or --serve with --dev among them; 4 a
dependency or the launch target; 5 the archive chain; 6 Python); it SAYS
whether the model server answered and starts regardless (never 3: a compose
profile starts the model server beside NEXUS, and the stub profile has
none; /healthz reports it); it sets NEXUS_LEARNING=frozen when nothing sets
it (law 12); it binds NEXUS_HOST; and it execs uvicorn, so in a container
uvicorn is PID 1 and a `docker stop` releases the store's lock (W2's F4,
measured by W3's establish: under --init, or any wrapper that does not
exec, the lock is left for the next start to take over).

W1 (the tidy) rewrote this file. What it fixed is in RUN_LOG.md's W1 entry
and in run/specs/W1.md: every child ran through the shell with an unquoted
interpreter path; an existing .env was rewritten and lost its comments; the
"Ollama check" was curl's exit code, and start_ollama started the daemon;
--reload was always on; a broken archive chain printed "Chain integrity";
consent to an act (a 15 GB pull among them) defaulted to yes; and no
argument was parsed at all.
"""
from __future__ import annotations

import argparse
import importlib
import ipaddress
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

EXIT_OK = 0
EXIT_DECLINED = 1
EXIT_USAGE = 2
EXIT_MODEL_SERVER = 3
EXIT_DEPENDENCIES = 4
EXIT_CHAIN = 5
EXIT_PYTHON = 6
EXIT_INTERRUPTED = 130           # 128 + SIGINT: what a shell reports for Ctrl+C

# R.2 measured a refused connection at ~2.45 s per probe on the operator's
# Windows box (against `localhost`, so ::1 and then 127.0.0.1; CLAUDE.md's
# startup-probe paragraph), against ~2 ms on Linux. A connect budget below
# that would report a closed port as "timed out". 5.0 s is the pre-R.2 flat
# timeout, and it is per attempt here (`socket.create_connection`), so every
# platform finishes refusing inside it. The providers' own 0.25 s probe
# budget (`backend/providers/base.py`) is for a different question.
CONNECT_BUDGET_S = 5.0

REEXEC_MARKER = "NEXUS_START_REEXEC"
DATA_ROOT_SETTING = "NEXUS_DATA_ROOT"      # backend/data_root.py reads it

# The two prompts that ask about start.py's own flow. Like every confirm()
# they default to No (law 6: consent and confirmation never default to true,
# a §3 law no ruling relaxes); tests/test_w1_start.py holds the rule by AST,
# with no exemption. Ruling 3 had let both default to Yes on loopback and was
# withdrawn at W1's close (the third verifier's critic, C2: the launch's
# first act writes the store; lens 2, N4: the first prompt's check reads a
# model server that may be remote).
FLOW_PROMPTS = ("Begin startup sequence?", "Start NEXUS now?")
FROZEN = "frozen"                  # NEXUS_LEARNING's frozen value (backend/learning.py)

# The two settings the created .env fills in (`ensure_env_file`): the
# operator's model server, and the day learning was frozen (RUN_CHARTER law
# 12: a new instance is frozen; `backend/learning.py` reads both).
LEARNING_SETTING = "NEXUS_LEARNING"
LEARNING_SINCE_SETTING = "NEXUS_LEARNING_FROZEN_SINCE"
LEARNING_ROLLBACK = f"{LEARNING_SETTING}=live"

REQUIREMENTS = ROOT / "requirements.txt"
# The requirements whose import name is not their name with "-" read as "_"
# (the rest are derived: tests/test_w1_start.py checks every line maps to a
# module that imports). Before W1's close the check was a hand list of 8 of
# the 14 lines, so a broken langgraph passed it (the third verifier's critic,
# C1, law 5).
IMPORT_NAMES = {"python-dotenv": "dotenv", "llama-index-core": "llama_index.core",
                "pyinstaller": "PyInstaller", "pyinstaller-hooks-contrib": "_pyinstaller_hooks_contrib"}
REQUIREMENT_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^;#]*?)\s*(;[^#]*)?\s*(#.*)?$")

# The launch target, imported the way the launch imports it: in a child of
# this interpreter, in the directory the launch runs in (start.py never
# changes it, and the hand-over keeps it), with the environment the launch
# hands the server, and by uvicorn's own rule: its --app-dir defaults to ""
# (the working directory), which uvicorn inserts at sys.path[0] before
# `uvicorn.importer.import_from_string` resolves "module:attribute". A
# requirement can import while the server cannot (a dependency of a
# dependency, a broken source), and a check run anywhere else answers a
# different question (W1's delta check, F1: the child ran in the repo root,
# so --check passed a launch that could not import from the operator's
# directory; and under PYTHONSAFEPATH a bare `-c` import failed where
# uvicorn's own insert succeeds, F2).
LAUNCH_TARGET = "backend.server:create_app"
LAUNCH_IMPORT = ("import sys; sys.path.insert(0, ''); "
                 "from uvicorn.importer import import_from_string; "
                 f"import_from_string({LAUNCH_TARGET!r})")
LAUNCH_IMPORT_TIMEOUT_S = 180

RECOMMENDED_MODELS = (
    "deepseek-r1:14b-qwen-distill-q4_K_M",
    "phi4-mini:3.8b-q4_K_M",
    "nomic-embed-text",
)


# ── output ───────────────────────────────────────────────────────
class C:
    RESET  = '\033[0m'
    BOLD   = '\033[1m'
    CYAN   = '\033[96m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    RED    = '\033[91m'
    DIM    = '\033[2m'
    WHITE  = '\033[97m'


def banner():
    print(f"""
{C.CYAN}{C.BOLD}
  ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗
  ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝
  ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗
  ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║
  ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║
  ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝
{C.RESET}{C.DIM}  Cognitive Governance Substrate — Startup{C.RESET}
""")


def step(n, label):
    print(f"\n{C.CYAN}{C.BOLD}[{n}] {label}{C.RESET}")
    print(f"{C.DIM}{'─' * 50}{C.RESET}")


def ok(msg):
    print(f"  {C.GREEN}✓{C.RESET}  {msg}")


def warn(msg):
    print(f"  {C.YELLOW}⚠{C.RESET}  {msg}")


def err(msg):
    print(f"  {C.RED}✗{C.RESET}  {msg}")


def info(msg):
    print(f"  {C.DIM}→{C.RESET}  {msg}")


def abort_interrupted():
    """Ctrl+C at a prompt: exit 130, the code a shell reports for SIGINT, so
    a wrapper can tell an interruption from a refusal (exit 1)."""
    print(f"\n\nAborted. (exit {EXIT_INTERRUPTED}: interrupted at a prompt, Ctrl+C)")
    sys.exit(EXIT_INTERRUPTED)


def abort_declined(what: str):
    """A step the launch needs was declined, or got no answer: exit 1 with
    nothing launched. The literal "Aborted." stays (tests pin it); what the
    code means is printed beside it."""
    print(f"\n  Aborted. (exit {EXIT_DECLINED}: {what})\n")
    sys.exit(EXIT_DECLINED)


def confirm(prompt, default=False):
    """Ask yes or no. No input at all (EOF: a pipe, a container) is a no,
    never the default: consent is never assumed (law 6). Ctrl+C exits 130."""
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"\n  {C.WHITE}{prompt} {C.DIM}{hint}{C.RESET} ").strip().lower()
    except KeyboardInterrupt:
        abort_interrupted()
    except EOFError:
        print("(no answer: taken as no)")
        return False
    if answer == "":
        return default
    return answer in ("y", "yes")


def utc_today() -> str:
    """Today's UTC date, YYYY-MM-DD: the from-date written into a created
    .env (`backend/learning.py` displays it as asserted; the store's
    timestamps are UTC epoch seconds, so the date is UTC too)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── arguments ────────────────────────────────────────────────────
def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="start.py",
        description="Check the environment, then launch the NEXUS console.")
    ap.add_argument("--port", type=int, default=None,
                    help="the port to serve on (beats NEXUS_PORT; default 8000)")
    ap.add_argument("--data-root", default=None,
                    help="the directory every console file lives in (sets "
                         "NEXUS_DATA_ROOT for the server; default: data/ under "
                         "the working directory)")
    ap.add_argument("--dev", action="store_true",
                    help="reload the server on every source change (uvicorn "
                         "--reload); never for the operator's instance")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="run the checks that need no answer, then exit: "
                           "launches nothing and writes no data")
    mode.add_argument("--serve", action="store_true",
                      help="no prompt: the checks that need no answer, then the "
                           "launch; never writes .env; a container's entrypoint (W3)")
    return ap.parse_args(argv)


# ── the checks ───────────────────────────────────────────────────
def check_python_version(v=None) -> tuple[bool, str]:
    """(3.11 or later, its text). `v` is sys.version_info unless given."""
    v = sys.version_info if v is None else v
    text = f"Python {v.major}.{v.minor}.{v.micro}"
    return (v.major == 3 and v.minor >= 11), text


def in_venv() -> bool:
    return hasattr(sys, "real_prefix") or sys.base_prefix != sys.prefix


def required_imports(path: "Path | None" = None) -> list[tuple[str, "str | None"]]:
    """(requirement, import name) for every requirement line of
    requirements.txt, in its order. The import name is IMPORT_NAMES's, else
    the name with "-" read as "_". A line with an environment marker (`;`)
    gets None: whether it applies here is pip's question, and the check does
    not answer it for pip."""
    out = []
    for line in (path or REQUIREMENTS).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = REQUIREMENT_LINE.match(line)
        if m is None:
            out.append((line.strip(), None))
            continue
        name = m.group(1)
        out.append((name, None if m.group(4) else IMPORT_NAMES.get(name, name.replace("-", "_"))))
    return out


def dependency_problems() -> dict[str, str]:
    """{requirement: why it does not import}, for each requirement of
    requirements.txt that does not. Each is imported in this process, so a
    package that is installed but fails to load (a DLL on Windows) is named
    as well as one that is absent (W1's verifier, F12: `find_spec` said
    "importable" of both). A line the check cannot read, or one with an
    environment marker, is named as not checked, never passed. No child, no
    shell."""
    problems = {}
    for requirement, module in required_imports():
        if module is None:
            problems[requirement] = "not checked: the line has an environment marker or cannot be read"
            continue
        try:
            importlib.import_module(module)
        except Exception as e:                    # an ImportError, an OSError from a DLL
            problems[requirement] = f"{type(e).__name__}: {e}"
    return problems


def launch_target_problem(env: dict) -> "str | None":
    """None when the launch target imports as the launch will import it
    (LAUNCH_IMPORT, in a child of this interpreter, in this process's
    working directory, with `env`: the environment the launch hands the
    server, .env values included); else why not, the child's last error
    line. A requirement can import while the server cannot (the third
    verifier's critic, C1: a broken langgraph passed a check of 8 packages,
    and the launch failed). The child is given no cwd, so it runs where the
    launch runs; it writes no bytecode, and its stdin is closed."""
    child = dict(env)
    child["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run([sys.executable, "-c", LAUNCH_IMPORT], env=child,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=LAUNCH_IMPORT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return f"did not finish importing within {LAUNCH_IMPORT_TIMEOUT_S} s"
    except OSError as e:
        return f"the child could not start ({type(e).__name__}: {e})"
    if proc.returncode == 0:
        return None
    lines = [l for l in (proc.stderr or proc.stdout or "").splitlines() if l.strip()]
    problem = lines[-1].strip() if lines else f"the child exited {proc.returncode} with no output"
    here = os.path.realpath(os.getcwd())
    if (os.path.normcase(here) != os.path.normcase(os.path.realpath(ROOT))
            and re.search(r"""(No module named|Could not import module) ['"]backend""", problem)):
        problem += (f" (the launch imports the console from the directory it runs in, {here}, "
                    f"not from start.py's repo root, {ROOT}: run start.py from the repo root)")
    return problem


def missing_dependencies() -> list[str]:
    """The requirements that do not import (`dependency_problems`)."""
    return list(dependency_problems())


def _repo_importable() -> None:
    """`backend` and `scripts` import from the repo root whatever the cwd."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def load_env() -> list[Path]:
    """`.env` loaded exactly as the server loads it (`backend/env.py`: the
    repo root, then the working directory; a real environment variable wins),
    so every check below sees the value the server will use."""
    _repo_importable()
    from backend.env import load_env_files
    return load_env_files()


class BackendError(ValueError):
    """LLM_BACKEND names no backend NEXUS has: a usage error (exit 2). The
    server refuses the same value by name at startup
    (backend/providers/factory.py)."""


STUB = "stub"
STUB_MODEL_LINE = ("none: LLM_BACKEND=stub, the stub provider answers in-process "
                   "(backend/providers/stub.py); no model server is checked")


def model_server_target(environ=None) -> tuple[str, str]:
    """(kind, base URL) of the model server the server will call, resolved
    exactly as the server resolves it: the backend name read as the factory
    reads it (`backend/providers/backends.py`, one reader for both since W3:
    stripped and lowered; an unknown name raises BackendError, as the
    factory refuses it by name), and each provider's URL read with its own
    default, keeping a value that is set but empty (W1's verifier, F13: this
    used to strip the URL and treat empty as unset, so it could check one
    server while the server called another). `stub` has no model server:
    `(STUB, "")`. tests/test_w1_start.py compares this with the factory's
    own provider."""
    environ = os.environ if environ is None else environ
    _repo_importable()
    from backend.providers.backends import API_BACKENDS, LMSTUDIO, backend_name, backend_problem
    # W3 part 2 — `backend_problem`: an unknown name, or an api backend whose
    # model is unset (or whose embedder is half named), the factory's refusal.
    reason = backend_problem(environ)
    if reason is not None:
        raise BackendError(reason)
    name = backend_name(environ)
    if name == LMSTUDIO:
        return "lmstudio", environ.get("LM_STUDIO_URL", "http://localhost:1234")
    if name == STUB:
        return STUB, ""
    if name in API_BACKENDS:
        # W3 part 2 — an api backend's endpoint, read as its provider reads it
        # (`backend/providers/api_settings.py`, standard library only): the
        # URL as set with a trailing slash stripped, the default when unset.
        from backend.providers.api_settings import api_settings
        return name, api_settings(name, environ).base_url
    return "ollama", environ.get("OLLAMA_URL", "http://localhost:11434")


SERVER_NAMES = {"ollama": "Ollama", "lmstudio": "LM Studio",
                "openai": "the OpenAI-compatible endpoint", "anthropic": "the Anthropic API"}
SERVER_PATHS = {"ollama": "/api/tags", "lmstudio": "/v1/models", "openai": "/models", "anthropic": "/v1/models"}
API_KINDS = ("openai", "anthropic")


def _server_headers(kind: str) -> dict:
    """W3 part 2 — the headers an api backend's model list wants: the key
    (read by `api_settings`, standard library only, and sent as the
    provider sends it) and, for Anthropic, the API version. A local server
    wants none."""
    if kind not in API_KINDS:
        return {}
    from backend.providers.api_settings import ANTHROPIC_VERSION, api_settings
    settings = api_settings(kind, os.environ)
    if kind == "openai":
        return {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
    headers = {"anthropic-version": ANTHROPIC_VERSION}
    if settings.api_key:
        headers["x-api-key"] = settings.api_key
    return headers


@dataclass(frozen=True)
class ServerCheck:
    kind: str
    url: str
    reachable: bool
    reason: str = ""                  # empty exactly when reachable
    models: tuple[str, ...] = ()


def _reason_for(exc: BaseException, url: str, timeout: float) -> str:
    """A named reason, from the exception's TYPE (W1's establish: the message
    text differs by client and platform, and a refusal is a refusal)."""
    if isinstance(exc, ConnectionRefusedError):
        return f"nothing is listening at {url} (connection refused)"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return f"no answer from {url} within {timeout:g} s (timed out)"
    if isinstance(exc, socket.gaierror):
        return f"the host in {url} does not resolve ({exc})"
    if isinstance(exc, OSError):
        return f"cannot reach {url}: {exc.strerror or exc}"
    return f"cannot reach {url}: {exc}"


def check_model_server(kind: str, base_url: str,
                       timeout: float = CONNECT_BUDGET_S) -> ServerCheck:
    """One GET of the model server's model list. Checks; never starts it.

    The connection is direct for a local server: no HTTP proxy, because the
    model server is local (or a compose service) in those profiles, and a
    proxy answering for it would make a closed port look like a working one.
    An api backend's endpoint (W3 part 2) is reached through whatever proxy
    the environment names, as the server's own client reaches it, with the
    key as a header."""
    name = SERVER_NAMES[kind]
    url = base_url.rstrip("/") + SERVER_PATHS[kind]
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ServerCheck(kind, url, False,
                           f"{base_url!r} is not an http(s) URL with a host")
    opener = (urllib.request.build_opener() if kind in API_KINDS
              else urllib.request.build_opener(urllib.request.ProxyHandler({})))
    try:
        with opener.open(urllib.request.Request(url, method="GET", headers=_server_headers(kind)),
                         timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        if kind in API_KINDS and e.code in (401, 403):
            return ServerCheck(kind, url, False,
                               f"{url} answered HTTP {e.code}: {name} refused the key "
                               f"({'OPENAI_API_KEY' if kind == 'openai' else 'ANTHROPIC_API_KEY'})")
        return ServerCheck(kind, url, False,
                           f"{url} answered HTTP {e.code}: that is not {name} "
                           f"answering, or {name} is failing")
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, BaseException):
            return ServerCheck(kind, url, False, _reason_for(reason, url, timeout))
        return ServerCheck(kind, url, False, f"cannot reach {url}: {reason}")
    except (OSError, ValueError) as e:            # a read timeout, a reset
        return ServerCheck(kind, url, False, _reason_for(e, url, timeout))
    try:
        data = json.loads(body)
    except ValueError:
        return ServerCheck(kind, url, False,
                           f"{url} answered, but not with JSON: something "
                           f"other than {name} is listening there")
    if kind == "ollama":
        rows = data.get("models") if isinstance(data, dict) else None
        key = "name"
    else:
        rows = data.get("data") if isinstance(data, dict) else None
        key = "id"
    if not isinstance(rows, list):
        return ServerCheck(kind, url, False,
                           f"{url} answered JSON, but not as {name} does (no "
                           f"model list): something other than {name} is "
                           f"listening there")
    models = tuple(r[key] for r in rows
                   if isinstance(r, dict) and isinstance(r.get(key), str))
    return ServerCheck(kind, url, True, "", models)


# ── the data root ────────────────────────────────────────────────
def resolve_data_root(arg=None, environ=None) -> Path:
    """--data-root, else NEXUS_DATA_ROOT, else data/ (the server's default).
    Both are stripped, as the server strips NEXUS_DATA_ROOT."""
    environ = os.environ if environ is None else environ
    raw = (arg or "").strip() or (environ.get(DATA_ROOT_SETTING) or "").strip()
    return Path(raw) if raw else Path("data")


# The archive's four states. Only ABSENT and VALID pass: an archive that is
# present and cannot be verified is not a verified archive (W1's verifier,
# F2: it used to read as a pass).
ABSENT, VALID, BROKEN, UNVERIFIABLE = "absent", "valid", "broken", "unverifiable"


@dataclass(frozen=True)
class DataReport:
    root: Path
    kb_bytes: "int | None"
    archive_bytes: "int | None"
    chain: str                        # ABSENT | VALID | BROKEN | UNVERIFIABLE
    chain_text: str

    @property
    def chain_valid(self) -> "bool | None":
        """True verified, False broken, None absent or not verifiable."""
        return {VALID: True, BROKEN: False}.get(self.chain)

    @property
    def chain_passes(self) -> bool:
        return self.chain in (ABSENT, VALID)


def verify_chain_read_only(archive: Path) -> tuple[str, str]:
    """(state, text): the archive chain verified by the product's own
    verifier, never writing the archive (law 15).

    The archive is opened read-only (`scripts/_readonly.py`) and copied by
    SQLite's backup API, which reads it under SQLite's own locking, into a
    temporary directory. The product's `NexusArchiveChain` then opens the
    copy, so an archive written under any earlier schema is migrated in the
    copy and verified exactly as the server would verify it (W1's verifier,
    F2: the read-only graft raised on a pre-F-16 archive the product reads as
    valid). The copy is removed. `valid` is read from the result, never from
    its text (before W1, "valid" in the repr of valid=False printed a tick)."""
    _repo_importable()
    import sqlite3
    import tempfile
    from backend.memory.chain import NexusArchiveChain
    from scripts._readonly import open_read_only
    try:
        src = open_read_only(archive)
    except sqlite3.Error as e:
        return UNVERIFIABLE, f"cannot be opened read-only ({type(e).__name__}: {e})"
    try:
        with tempfile.TemporaryDirectory(prefix="nexus-start-") as tmp:
            copy = Path(tmp) / "archive.db"
            try:
                tables = {r[0] for r in src.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")}
                if "archive_blocks" not in tables:
                    return UNVERIFIABLE, "is not a NEXUS archive (it has no archive_blocks table)"
                dst = sqlite3.connect(copy)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            except sqlite3.Error as e:
                return UNVERIFIABLE, f"cannot be read ({type(e).__name__}: {e})"
            try:
                chain = NexusArchiveChain(copy)
                try:
                    result = chain.verify_chain()
                finally:
                    chain.close()
            except Exception as e:                # report, never a tick
                return UNVERIFIABLE, f"cannot be verified ({type(e).__name__}: {e})"
    finally:
        src.close()
    if result.valid:
        return VALID, f"valid ({result.n_blocks} blocks)"
    return BROKEN, (f"DOES NOT VERIFY: broken at block {result.broken_at}: "
                    f"{result.reason}")


def database_line(root: Path) -> str:
    """The first line start.py prints (law 15: a script that reads the
    instance names the database first): the archive under the data root the
    run reads, read-only, and whether it is there yet. W3b part 3: under
    `NEXUS_STORE=postgres` the database the server will open (its URL without
    the password), which this script reads nothing from; the data root then
    holds chroma/, snapshots/ and the logs. A store setting the server would
    refuse is left for the checks below to name."""
    _repo_importable()
    from backend.store.target import (POSTGRES, URL_SETTING, database_url, redacted_url, store_backend_name,
                                      store_config_reason)
    if store_backend_name() == POSTGRES and store_config_reason() is None:
        return (f"database: postgres {redacted_url(database_url())} ({URL_SETTING}; not read by this script: "
                f"the hosted store is the server's to open)")
    archive = root / "archive.db"
    there = "present" if archive.is_file() else "not present yet"
    return f"database: {archive} (read only: the archive the checks verify; {there})"


def report_data_root(root: Path) -> DataReport:
    """What the data root holds. Writes nothing under it."""
    kb, archive = root / "kb.db", root / "archive.db"
    kb_bytes = kb.stat().st_size if kb.is_file() else None
    archive_bytes = archive.stat().st_size if archive.is_file() else None
    if archive_bytes is None:
        state, text = ABSENT, "no archive yet (created on the first KAIROS cycle)"
    else:
        state, text = verify_chain_read_only(archive)
    return DataReport(root, kb_bytes, archive_bytes, state, text)


# ── the bind and the launch ──────────────────────────────────────
class BindError(ValueError):
    """A port that cannot be served on, named."""


def resolve_bind(port_arg=None) -> tuple[str, int]:
    """Server bind address. Default is localhost-only; LAN exposure is an
    explicit opt-in via NEXUS_HOST (see THREAT_MODEL.md before doing so).
    The port is --port, else NEXUS_PORT, else 8000."""
    # An empty NEXUS_HOST is unset (W1's verifier, N5: uvicorn reads an empty
    # host as every interface).
    host = (os.environ.get("NEXUS_HOST") or "").strip() or "127.0.0.1"
    if port_arg is not None:
        port, source = port_arg, "--port"
    else:
        raw = os.environ.get("NEXUS_PORT", "8000").strip()
        try:
            port, source = int(raw), "NEXUS_PORT"
        except ValueError:
            raise BindError(f"NEXUS_PORT={raw!r} is not a port number") from None
    if not 1 <= port <= 65535:
        raise BindError(f"{source}={port} is outside 1-65535")
    return host, port


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# The two settings uvicorn reads from its environment beside UVICORN_*
# (uvicorn/config.py): WEB_CONCURRENCY sets the worker count (two workers
# would be two writers on one kb.db), FORWARDED_ALLOW_IPS which peers'
# proxy headers to trust. The argv fixes both, to uvicorn's own defaults,
# and the environment drops them. tests/test_w1_start.py derives uvicorn's
# environment reads from the installed source and fails on a new one.
UVICORN_ENV_SETTINGS = ("WEB_CONCURRENCY", "FORWARDED_ALLOW_IPS")


def launch_argv(host: str, port: int, *, dev: bool, proxy_mode: bool = False) -> list[str]:
    """The exact command the server runs as: this interpreter, uvicorn's
    factory target, one worker, uvicorn's own default for trusted proxy
    headers, and `--reload` only under --dev. W4 part 1 (D6): in proxy mode
    (NEXUS_AUTH_MODE=proxy) uvicorn also gets `--no-proxy-headers`, so the
    peer the identity gate reads is the socket's own: with proxy headers on,
    uvicorn replaces a loopback proxy's address with whatever X-Forwarded-For
    says, and the gate would refuse every proxied request. Token mode's argv
    is unchanged."""
    argv = [sys.executable, "-m", "uvicorn", "backend.server:create_app",
            "--factory", "--host", host, "--port", str(port),
            "--workers", "1", "--forwarded-allow-ips", "127.0.0.1,::1"]
    if proxy_mode:
        argv.append("--no-proxy-headers")
    if dev:
        argv.append("--reload")
    return argv


def auth_check(environ=None) -> tuple["str | None", object]:
    """(problem, config): the identity settings as the server's factory will
    read them (`backend/identity.py`, standard library only). `problem` is
    the factory's own refusal text (a NEXUS_AUTH_MODE that names no mode,
    or a proxy-mode header no request could carry), with `config` None;
    else None and the `IdentityConfig`."""
    environ = os.environ if environ is None else environ
    _repo_importable()
    from backend.identity import auth_config_reason, identity_config
    reason = auth_config_reason(environ)
    if reason is not None:
        return reason, None
    return None, identity_config(environ)


def auth_line(config) -> str:
    """Proxy mode's startup line: the mode, the header, whether the owner is
    set, and the launch's flag; nothing about a bearer, which is no
    credential there. Token mode prints no line: its lines are today's."""
    owner = ("owner set" if config.owner_set
             else "owner NOT SET: every request will be refused (403 \"owner not set\")")
    return (f"proxy: the login from the {config.header} header of a loopback peer; {owner}; "
            f"uvicorn runs with --no-proxy-headers")


def launch_env(environ, data_root=None) -> dict[str, str]:
    """The server's environment: this one, minus every UVICORN_* variable
    (uvicorn reads them, so UVICORN_RELOAD=true reloaded with no flag) and
    the two settings above (W1's verifier, F3: WEB_CONCURRENCY=2 started two
    servers on one store), so the argv is uvicorn's whole configuration;
    plus NEXUS_DATA_ROOT when --data-root was given; plus NEXUS_LEARNING=frozen
    when nothing sets NEXUS_LEARNING (neither the environment nor the .env
    files loaded into it): RUN_CHARTER law 12, a new instance is frozen. The
    console's own code default is live (FZ-2's) and is W3's to change; an
    instance launched through start.py is frozen unless the operator says
    otherwise (the third verifier's lens 2, F2: Enter at the .env offer, the
    default, left a new instance live and said nothing)."""
    env = {k: v for k, v in environ.items()
           if not k.upper().startswith("UVICORN_") and k.upper() not in UVICORN_ENV_SETTINGS}
    if data_root is not None:
        env[DATA_ROOT_SETTING] = str(data_root)
    if not (env.get(LEARNING_SETTING) or "").strip():
        env[LEARNING_SETTING] = FROZEN
    return env


def learning_default_applied(environ) -> bool:
    """True when the launch will freeze learning because nothing set it."""
    return not (environ.get(LEARNING_SETTING) or "").strip()


def manual_command(argv: list[str]) -> str:
    """The launch as a command line the operator can paste, built from the
    same argv (so the advice and the launch cannot drift apart). On Windows
    it is cmd.exe syntax: PowerShell needs `& ` before it (W1's verifier,
    N1), which the printed advice says."""
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def manual_advice(argv: list[str]) -> str:
    """The pasteable command, and on Windows which shell it is for."""
    if os.name == "nt":
        return f"{manual_command(argv)}  (cmd.exe; in PowerShell put & before it)"
    return manual_command(argv)


def _hand_over(argv: list[str], env: dict[str, str]):
    """Become `argv`. The ONE hand-over: the launch and the venv re-exec.

    POSIX: exec, so the server keeps this process id (a container's PID 1
    then receives its signals) and nothing is left behind. Windows has no
    exec: `os.exec*` starts the child and returns the console to the shell at
    once (python-dotenv's cli.py and CPython's own regrtest runner both say
    so and both do this instead), so wait for the child and exit with its
    status. Output is flushed first: exec discards Python's buffers."""
    sys.stdout.flush()
    sys.stderr.flush()
    if os.name != "nt":
        os.execve(argv[0], argv, env)
    proc = subprocess.Popen(argv, env=env)
    interrupted = False
    while True:
        try:
            sys.exit(proc.wait())
        except KeyboardInterrupt:
            # The console delivered Ctrl+C to the child as well; let it shut
            # down cleanly. A second Ctrl+C ends it.
            if interrupted:
                proc.kill()
            interrupted = True


# ── the interactive steps ────────────────────────────────────────
def step_python() -> "str | None":
    """None when the check passed; else what failed, once the operator has
    chosen to continue past it (each step below returns the same way, so the
    end of the checks can say whether they all passed)."""
    step(1, "Python Version")
    good, text = check_python_version()
    if good:
        ok(text)
        return None
    warn(f"{text} detected — 3.11+ is required")
    if not confirm("Continue anyway?", default=False):
        sys.exit(EXIT_PYTHON)
    return "Python version"


def step_venv():
    step(2, "Virtual Environment")
    if in_venv():
        ok(f"Active: {sys.prefix}")
        return
    warn("Not running inside a virtual environment")
    if os.environ.get(REEXEC_MARKER):
        warn("start.py was re-launched inside .venv and this interpreter is "
             "still not a virtual environment's; continuing as it is")
        return
    if sys.platform == "win32":
        info("Recommended: python -m venv .venv && .venv\\Scripts\\activate")
    else:
        info("Recommended: python -m venv .venv && source .venv/bin/activate")
    if not (ROOT / ".venv").exists():
        if not confirm("Create .venv now?", default=False):
            if not confirm("Continue without venv?", default=False):
                abort_declined("the operator declined both to create .venv "
                               "and to continue without one")
            return
        info("Creating .venv...")
        if subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")]).returncode != 0:
            err("Failed to create .venv")
            sys.exit(EXIT_DECLINED)
        ok(".venv created")
    if not venv_python().exists():
        warn(".venv python not found — continuing without")
        return
    info("Re-launching inside .venv...")
    env = dict(os.environ)
    env[REEXEC_MARKER] = "1"
    _hand_over([str(venv_python()), str(ROOT / "start.py"), *sys.argv[1:]], env)


def step_dependencies() -> "str | None":
    step(3, "Dependencies")
    problems = dependency_problems()
    for requirement, _module in required_imports():
        if requirement in problems:
            err(f"{requirement} — does not import ({problems[requirement]})")
        else:
            ok(requirement)
    missing = list(problems)
    if not missing:
        return None
    req = ROOT / "requirements.txt"
    if confirm(f"Install the requirements now (pip install -r {req.name})?",
               default=False):
        if subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)]).returncode == 0:
            ok("Requirements installed")
            return None
        err(f"Install failed — run it yourself: {manual_advice([sys.executable, '-m', 'pip', 'install', '-r', str(req)])}")
        sys.exit(EXIT_DEPENDENCIES)
    warn(f"Missing: {', '.join(missing)}; the server will not start without them")
    return "dependencies"


def _set_value(text: str, key: str, value: str) -> tuple[str, bool]:
    """`text` with the value of its first `KEY=` line replaced (the key's
    comment and every other line kept as they are), and whether the line was
    there to replace."""
    pattern = re.compile(rf"(?m)^({re.escape(key)}=)[^\s#]*")
    if pattern.search(text) is None:
        return text, False
    return pattern.sub(lambda m: m.group(1) + value, text, count=1), True


def ensure_env_file(env_path: "Path | None" = None,
                    example_path: "Path | None" = None) -> "Path | None":
    """An existing .env is read and NEVER written: before W1 every run
    rewrote it and lost its comments, quoting and export lines. With none,
    offer to create one as a copy of .env.example (the settings .env.example
    names, with their values) whose LLM_BACKEND line is the operator's
    choice and whose NEXUS_LEARNING_FROZEN_SINCE line is today's UTC date:
    the example freezes learning (RUN_CHARTER law 12, a new instance is
    frozen) and start.py dates the freeze from the day it created the file.
    Those two values are the only lines that change."""
    example_path = example_path or ROOT / ".env.example"
    step(4, "Environment Configuration")
    if env_path is None:
        # Where the server's loader looks (backend/env.py): NEXUS_ENV_ROOT's
        # .env when it is set, else the repo root's and then the working
        # directory's (W1's verifier, N5).
        _repo_importable()
        from backend.env import env_file_candidates
        candidates = env_file_candidates()
        found = [c for c in candidates if c.exists()]
        if found:
            ok(f".env found ({', '.join(str(c) for c in found)}); read, never rewritten")
            return found[0]
        env_path = candidates[0]
    if env_path.exists():
        ok(f".env found ({env_path}); read, never rewritten")
        return env_path
    warn(".env not found: NEXUS runs on its defaults without one")
    if not example_path.is_file():
        warn(f"{example_path.name} is missing too; continuing on the defaults")
        return None
    if not confirm(f"Create .env from {example_path.name} now?", default=False):
        info(f"To create it yourself: copy {example_path.name} to .env")
        return None
    print(f"\n  {C.WHITE}Which model server?{C.RESET}")
    print(f"  {C.DIM}1) Ollama (default){C.RESET}")
    print(f"  {C.DIM}2) LM Studio{C.RESET}")
    try:
        choice = input(f"\n  {C.WHITE}Enter 1 or 2:{C.RESET} ").strip()
    except KeyboardInterrupt:
        abort_interrupted()
    except EOFError:
        print("(no answer)")
        abort_declined("no answer to the model-server choice, which creating "
                       ".env needs; nothing was created")
    backend = "lmstudio" if choice == "2" else "ollama"
    text = example_path.read_text(encoding="utf-8")
    # Only the values of the LLM_BACKEND and NEXUS_LEARNING_FROZEN_SINCE
    # lines change; their comments, and every other line of the example, are
    # copied as they are.
    text, _ = _set_value(text, "LLM_BACKEND", backend)
    since = utc_today()
    text, dated = _set_value(text, LEARNING_SINCE_SETTING, since)
    env_path.write_text(text, encoding="utf-8")
    ok(f".env created from {example_path.name} with LLM_BACKEND={backend}")
    frozen = re.search(rf"(?m)^{LEARNING_SETTING}=frozen\b", text) is not None
    if frozen and dated:
        ok(f"Learning is frozen since {since} in .env (RUN_CHARTER law 12: a "
           f"new instance learns nothing automatically until you switch it on)")
        info(f"To switch learning on, set {LEARNING_ROLLBACK} in .env and "
             f"restart NEXUS: your deliberate act, and the FZ-2 rollback")
    elif frozen:
        # Frozen, but the example carries no since line for start.py to fill:
        # the console will read "frozen since: undeclared" (backend/learning.py).
        warn(f"Learning is frozen in .env, but {example_path.name} has no "
             f"{LEARNING_SINCE_SETTING} line to date it: NEXUS will read "
             f"\"frozen since: undeclared\"")
        info(f"To switch learning on, set {LEARNING_ROLLBACK} in .env and "
             f"restart NEXUS: your deliberate act, and the FZ-2 rollback")
    else:
        # The example given names no freeze, so the console will run on the
        # code's default (live) unless its environment says otherwise. Said,
        # never assumed (law 12 wants frozen).
        warn(f"{example_path.name} does not freeze learning (no "
             f"{LEARNING_SETTING}=frozen line): without {LEARNING_SETTING}=frozen "
             f"in .env or in its environment, the console runs with learning live")
    return env_path


def step_model_server() -> "str | None":
    step(5, "Model Server")
    try:
        kind, url = model_server_target(os.environ)
    except BackendError as e:
        # A setting the server itself refuses at startup: a usage error, not
        # a check to continue past (W3, H2: before, any unknown value ran
        # Ollama with a warning here and nothing at the server).
        err(str(e))
        info("The server refuses this value by name at startup; fix LLM_BACKEND in .env "
             "or the environment, then run start.py again.")
        sys.exit(EXIT_USAGE)
    if kind == STUB:
        ok(f"Model server: {STUB_MODEL_LINE}")
        return None
    result = check_model_server(kind, url)
    name = SERVER_NAMES[kind]
    if result.reachable:
        ok(f"{name} reachable at {url}")
        if result.models:
            ok(f"Models: {', '.join(result.models[:4])}")
            if kind == "ollama":
                for needed in ("deepseek", "phi", "nomic"):
                    if not any(needed in m.lower() for m in result.models):
                        warn(f"No {needed} model listed — consider pulling one")
        else:
            warn(f"{name} lists no models")
            if kind == "ollama":
                info("Pull them yourself (about 15 GB; start.py runs no ollama command):")
                for model in RECOMMENDED_MODELS:
                    info(f"  ollama pull {model}")
            elif kind == "lmstudio":
                info("Load a model in LM Studio (8192 context, all GPU layers)")
            else:
                info("The endpoint lists no models for this key; the model NEXUS asks for is the one "
                     "named in .env (OPENAI_MODEL or ANTHROPIC_MODEL), checked when it answers.")
        return None
    err(f"Cannot reach {name}: {result.reason}")
    if kind == "ollama":
        info("NEXUS does not start Ollama. Start it yourself (ollama serve, or "
             "the Ollama app), then run start.py again.")
    elif kind in API_KINDS:
        info("Check the endpoint's URL and key in .env (OPENAI_BASE_URL and OPENAI_API_KEY, or "
             "ANTHROPIC_BASE_URL and ANTHROPIC_API_KEY); an HTTP 401 means the key was refused.")
    else:
        info(f"NEXUS does not start LM Studio. Start its server (Developer tab "
             f"→ Start Server) at {url.rsplit('/v1/', 1)[0]}, then run start.py again.")
    if not confirm("Continue anyway?", default=False):
        sys.exit(EXIT_MODEL_SERVER)
    return "model server"


def print_data_report(report: DataReport) -> None:
    where = report.root if report.root.is_absolute() else f"{report.root} (under {Path.cwd()})"
    ok(f"Data root: {where}")
    if report.kb_bytes is None:
        info("No kb.db yet: the server creates it on first start")
    else:
        ok(f"kb.db {report.kb_bytes / 1024:.0f} KB")
    if report.archive_bytes is not None:
        ok(f"archive.db {report.archive_bytes / 1024:.0f} KB")
    if report.chain == VALID:
        ok(f"Archive chain: {report.chain_text}")
    elif report.chain == ABSENT:
        info(f"Archive chain: {report.chain_text}")
    else:
        err(f"Archive chain {report.chain_text}" if report.chain == UNVERIFIABLE
            else f"Archive chain: {report.chain_text}")


def store_check(root: Path) -> tuple[bool, str]:
    """(problem, text): the store the settings name (W3b part 3). SQLite:
    the data root's files are the store. Postgres: the database's URL without
    its password, and the data root's remaining role. A setting the server
    would refuse by name is the text, with `problem` True."""
    _repo_importable()
    from backend.store.target import (POSTGRES, URL_SETTING, database_url, redacted_url, store_backend_name,
                                      store_config_reason)
    reason = store_config_reason()
    if reason is not None:
        return True, reason
    if store_backend_name() == POSTGRES:
        return False, (f"postgres {redacted_url(database_url())} ({URL_SETTING}): every store table lives there; "
                       f"the data root keeps chroma/, snapshots/ and the logs")
    return False, f"sqlite: kb.db and archive.db under the data root {root}"


def step_data_root(arg) -> "str | None":
    step(6, "Data Root (read only)")
    root = resolve_data_root(arg)
    store_problem, store_text = store_check(root)
    if store_problem:
        err(f"Store: {store_text}")
        info("The server refuses this value by name at startup; fix NEXUS_STORE or NEXUS_DATABASE_URL in .env")
        return "store"
    if store_text.startswith("postgres"):
        ok(f"Store: {store_text}")
        info("Archive chain: not checked here (the check reads SQLite files; the hosted store is the server's)")
        return None
    report = report_data_root(root)
    print_data_report(report)
    if report.chain_passes:
        return None
    # A chain that does not verify, or cannot be, is not "all checks
    # complete" (W1's verifier, F2): the operator decides, and the default
    # is to stop.
    if not confirm("Continue anyway?", default=False):
        sys.exit(EXIT_CHAIN)
    return "archive chain"


def step_auth():
    """W4 part 1 — the identity settings, beside the backend and store
    checks and before anything is launched: a NEXUS_AUTH_MODE that names no
    mode is a usage error (exit 2), as the server's factory refuses it by
    name. Returns the `IdentityConfig`; prints nothing when the settings
    pass (token mode's lines are today's; proxy mode's line is the launch's)."""
    problem, config = auth_check()
    if problem is None:
        return config
    err(problem)
    info("The server refuses this value by name at startup; fix NEXUS_AUTH_MODE (token or proxy) or "
         "NEXUS_IDENTITY_HEADER in .env or the environment, then run start.py again.")
    sys.exit(EXIT_USAGE)


def step_launch(args) -> None:
    """Returns only when the launch was declined: the hand-over never
    returns (POSIX execs; Windows exits with the server's status)."""
    step(7, "Launch NEXUS")
    try:
        host, port = resolve_bind(args.port)
    except BindError as e:
        err(str(e))
        sys.exit(EXIT_USAGE)
    identity = step_auth()
    argv = launch_argv(host, port, dev=args.dev, proxy_mode=identity.is_proxy)
    data_root = (args.data_root or "").strip() or None
    env = launch_env(os.environ, data_root)

    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    print(f"\n  {C.WHITE}Ready to start NEXUS.{C.RESET}")
    print(f"  {C.DIM}URL:   http://{netloc}{C.RESET}")
    print(f"  {C.DIM}Docs:  http://{netloc}/docs{C.RESET}")
    print(f"  {C.DIM}Stop:  CTRL+C{C.RESET}\n")
    if identity.is_proxy:
        # W4 part 1 — proxy mode: the identity proxy in front supplies the
        # login, so nothing here speaks of a bearer (it is no credential there).
        info(f"Auth: {auth_line(identity)}")
        info("Tip: seed the KB via the UI left panel, reached through the identity proxy")
    else:
        # Both seed routes are token-gated (backend/server.py), so the request
        # shown carries the header, or the operator's first request gets a 401.
        body = "{\"concepts\":[\"your_domain\"],\"confirmed\":true}"
        header = "Authorization: Bearer <NEXUS_API_TOKEN>"
        info("Tip: seed the KB via the UI left panel or, with the token (both "
             "routes are gated):")
        info(f"  POST /kb/seed/wikidata   {header}   {body}")
        info(f"  POST /kb/seed/conceptnet {header}   {body}")
    if not is_loopback(host):
        warn(f"NEXUS_HOST={host} — the server will be reachable beyond this "
             f"machine. Read THREAT_MODEL.md before exposing it.")
    if args.dev:
        warn("--dev: uvicorn reloads on every source change. Never use it on "
             "the instance you keep.")
    if data_root is not None:
        info(f"Data root: {data_root} (NEXUS_DATA_ROOT, for the server only)")
    frozen_by_default = learning_default_applied(os.environ)
    if frozen_by_default:
        info(f"Learning: frozen. Nothing sets {LEARNING_SETTING} (not the environment, "
             f"not .env), so the launch sets {LEARNING_SETTING}={FROZEN} (RUN_CHARTER "
             f"law 12); to switch learning on, set {LEARNING_ROLLBACK} in .env")
    else:
        info(f"Learning: {env[LEARNING_SETTING]} ({LEARNING_SETTING}, from the environment or .env)")
    problem = launch_target_problem(env)
    if problem is not None:
        err(f"backend.server does not import with the launch's environment: {problem}")
        info("Nothing was launched. Fix the import (python -m pip install -r requirements.txt, "
             "or the package it names), then run start.py again.")
        sys.exit(EXIT_DEPENDENCIES)

    # No by default on every bind (law 6): the launch's first act writes the
    # store (create_app creates kb.db, archive.db, chroma/, snapshots/).
    if not confirm(FLOW_PROMPTS[1], default=False):
        info(f"To start it yourself: {manual_advice(argv)}")
        if data_root is not None:
            info(f"  with NEXUS_DATA_ROOT={data_root} in the environment or in .env")
        if frozen_by_default:
            info(f"  with {LEARNING_SETTING}={FROZEN} in the environment or in .env (law 12)")
        return
    print(f"\n  {C.GREEN}Starting...{C.RESET}\n")
    _hand_over(argv, env)


# ── --check ──────────────────────────────────────────────────────
def venv_python() -> Path:
    """The interpreter step_venv re-launches into: the repo's .venv."""
    if sys.platform == "win32":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def run_check(args) -> int:
    """Every check that needs no answer. Launches nothing and writes no
    data: the archive is verified on a temporary copy, which is removed
    (Python may still write its own bytecode cache). The exit code is the
    first failing check's, in the order printed."""
    read = load_env()        # first, so every line below sees what the server will
    root = resolve_data_root(args.data_root)
    # Law 15: a script that reads the instance prints the database path first.
    print(database_line(root))
    print("NEXUS start.py --check (launches nothing, writes no data)")
    code = EXIT_OK

    def fail(this):
        nonlocal code
        code = code or this

    good, text = check_python_version()
    print(f"  python      {text}{'' if good else ' — 3.11+ is required'}")
    if not good:
        fail(EXIT_PYTHON)
    # W1's verifier, F14: these checks are of THIS interpreter. Without a
    # virtual environment the launch re-runs start.py inside the repo's
    # .venv when it exists, so say which interpreter was checked and how to
    # check that one.
    print(f"  python at   {sys.executable}")
    if in_venv():
        print(f"  venv        {sys.prefix}")
    elif venv_python().exists():
        print(f"  venv        not in a virtual environment; the launch would re-run "
              f"inside {venv_python()}: check that one with "
              f"{manual_command([str(venv_python()), str(ROOT / 'start.py'), '--check'])}")
    else:
        print("  venv        not in a virtual environment (a warning)")
    print(f"  .env        {', '.join(str(p) for p in read) if read else 'none read: the defaults apply'}")
    problems = dependency_problems()
    count = len(required_imports())
    print(f"  packages    {f'all {count} of requirements.txt import' if not problems else 'do not import: ' + '; '.join(f'{k} ({v})' for k, v in problems.items())}")
    if problems:
        fail(EXIT_DEPENDENCIES)
    # W3 — the backend name before the launch check: the server refuses an
    # unknown LLM_BACKEND by name at startup, so the launch child would fail
    # for that reason and the line below would call a setting a dependency.
    try:
        kind, url = model_server_target()
        backend_problem = None
    except BackendError as e:
        kind, url, backend_problem = None, None, str(e)
    data_root_arg = (args.data_root or "").strip() or None
    if backend_problem is not None:
        print("  launch      not checked: LLM_BACKEND must name a backend first (below)")
    else:
        problem = launch_target_problem(launch_env(os.environ, data_root_arg))
        if problem is None:
            print("  launch      backend.server imports (as uvicorn will import it: a child of this "
                  "interpreter, in this directory, with the launch's environment)")
        else:
            print(f"  launch      backend.server does NOT import with the launch's environment: {problem}")
            fail(EXIT_DEPENDENCIES)
    if backend_problem is not None:
        print(f"  model       {backend_problem}")
        print("              the server refuses this value by name at startup (exit 2); fix LLM_BACKEND")
        fail(EXIT_USAGE)
    elif kind == STUB:
        print(f"  model       {STUB_MODEL_LINE}")
    else:
        result = check_model_server(kind, url)
        name = SERVER_NAMES[kind]
        if result.reachable:
            print(f"  model       {name} reachable at {url}: {len(result.models)} model(s)")
        else:
            print(f"  model       {name} NOT REACHABLE: {result.reason}")
            print(f"              NEXUS does not start {name}; start it yourself.")
            fail(EXIT_MODEL_SERVER)
    # W4 part 1 — the identity settings: refused by name like the backend's
    # and the store's; proxy mode says its line; token mode's lines are today's.
    auth_problem, identity = auth_check()
    if auth_problem is not None:
        print(f"  auth        {auth_problem}")
        print("              the server refuses this value by name at startup (exit 2); fix NEXUS_AUTH_MODE "
              "or NEXUS_IDENTITY_HEADER")
        fail(EXIT_USAGE)
    elif identity.is_proxy:
        print(f"  auth        {auth_line(identity)}")
    store_problem, store_text = store_check(root)
    print(f"  store       {store_text}")
    if store_problem:
        print("              the server refuses this value by name at startup (exit 2); fix NEXUS_STORE "
              "or NEXUS_DATABASE_URL")
        fail(EXIT_USAGE)
    if store_text.startswith("postgres"):
        print(f"  data root   {root}: chroma/, snapshots/ and the logs; the archive chain check reads "
              f"SQLite files and does not apply to a hosted store")
    else:
        report = report_data_root(root)
        sizes = ", ".join(f"{n} {b} bytes" for n, b in (("kb.db", report.kb_bytes),
                                                        ("archive.db", report.archive_bytes))
                          if b is not None) or "no stores yet"
        print(f"  data root   {report.root}: {sizes}; archive chain {report.chain_text}")
        if not report.chain_passes:
            fail(EXIT_CHAIN)
    try:
        host, port = resolve_bind(args.port)
        print(f"  bind        {host}:{port}{'' if is_loopback(host) else ' (beyond this machine)'}")
    except BindError as e:
        print(f"  bind        {e}")
        fail(EXIT_USAGE)
    print(f"result: {'every check passed' if code == EXIT_OK else 'a check failed'} (exit {code})")
    return code


def run_serve(args) -> int:
    """--serve (W3, run/specs/W3.md D1): the non-interactive launch, a
    container's entrypoint. No prompt is ever asked and .env is never
    written. The checks that need no answer run first, and the first that
    refuses is the exit (2 usage, 4 a dependency or the launch target, 5 the
    chain, 6 Python); the model server is checked and SAID, never refused
    (a compose profile starts it beside NEXUS; the stub profile has none);
    learning is frozen when nothing sets it (law 12); then the one hand-over
    execs uvicorn, so a container's PID 1 is the server and a `docker stop`
    releases the store's lock. Returns only when a check refused."""
    if args.dev:
        print("NEXUS start.py --serve: --dev is not allowed with --serve (the non-interactive "
              "launch never reloads)")
        print(f"result: refused (exit {EXIT_USAGE}); nothing was launched")
        return EXIT_USAGE
    read = load_env()
    root = resolve_data_root(args.data_root)
    # Law 15: a script that reads the instance prints the database path first.
    print(database_line(root))
    print("NEXUS start.py --serve (no prompt: the checks, then the launch; .env is never written)")
    good, text = check_python_version()
    print(f"  python      {text}{'' if good else ' — 3.11+ is required'}")
    if not good:
        print(f"result: refused (exit {EXIT_PYTHON}); nothing was launched")
        return EXIT_PYTHON
    print(f"  .env        {', '.join(str(p) for p in read) if read else 'none read: the defaults apply'}")
    problems = dependency_problems()
    if problems:
        print("  packages    do not import: " + "; ".join(f"{k} ({v})" for k, v in problems.items()))
        print(f"result: refused (exit {EXIT_DEPENDENCIES}); nothing was launched")
        return EXIT_DEPENDENCIES
    print(f"  packages    all {len(required_imports())} of requirements.txt import")
    try:
        kind, url = model_server_target()
    except BackendError as e:
        print(f"  model       {e}")
        print(f"result: refused (exit {EXIT_USAGE}); nothing was launched: the server refuses this "
              f"value by name at startup")
        return EXIT_USAGE
    if kind == STUB:
        print(f"  model       {STUB_MODEL_LINE}")
    else:
        result = check_model_server(kind, url)
        name = SERVER_NAMES[kind]
        if result.reachable:
            print(f"  model       {name} reachable at {url}: {len(result.models)} model(s)")
        else:
            print(f"  model       {name} NOT REACHABLE: {result.reason}")
            print(f"              the server starts anyway: a compose profile starts {name} beside "
                  f"NEXUS, and /healthz reports whether it answers")
    # W4 part 1 — an unknown NEXUS_AUTH_MODE is refused before anything is
    # launched (exit 2); proxy mode says the mode, the header and the owner.
    auth_problem, identity = auth_check()
    if auth_problem is not None:
        print(f"  auth        {auth_problem}")
        print(f"result: refused (exit {EXIT_USAGE}); nothing was launched: the server refuses this "
              f"value by name at startup")
        return EXIT_USAGE
    if identity.is_proxy:
        print(f"  auth        {auth_line(identity)}")
    store_problem, store_text = store_check(root)
    print(f"  store       {store_text}")
    if store_problem:
        print(f"result: refused (exit {EXIT_USAGE}); nothing was launched: the server refuses this "
              f"value by name at startup")
        return EXIT_USAGE
    if store_text.startswith("postgres"):
        print(f"  data root   {root}: chroma/, snapshots/ and the logs; the archive chain check reads "
              f"SQLite files and does not apply to a hosted store")
    else:
        report = report_data_root(root)
        sizes = ", ".join(f"{n} {b} bytes" for n, b in (("kb.db", report.kb_bytes),
                                                        ("archive.db", report.archive_bytes))
                          if b is not None) or "no stores yet"
        print(f"  data root   {report.root}: {sizes}; archive chain {report.chain_text}")
        if not report.chain_passes:
            print(f"result: refused (exit {EXIT_CHAIN}); nothing was launched")
            return EXIT_CHAIN
    try:
        host, port = resolve_bind(args.port)
    except BindError as e:
        print(f"  bind        {e}")
        print(f"result: refused (exit {EXIT_USAGE}); nothing was launched")
        return EXIT_USAGE
    print(f"  bind        {host}:{port}"
          + ("" if is_loopback(host) else " (beyond this machine: the container network or a "
                                           "reverse proxy is the boundary; THREAT_MODEL.md)"))
    data_root_arg = (args.data_root or "").strip() or None
    env = launch_env(os.environ, data_root_arg)
    if learning_default_applied(os.environ):
        print(f"  learning    frozen: nothing sets {LEARNING_SETTING}, so the launch sets "
              f"{LEARNING_SETTING}={FROZEN} (RUN_CHARTER law 12); {LEARNING_ROLLBACK} switches it on")
    else:
        print(f"  learning    {env[LEARNING_SETTING]} ({LEARNING_SETTING}, from the environment or .env)")
    problem = launch_target_problem(env)
    if problem is not None:
        print(f"  launch      backend.server does NOT import with the launch's environment: {problem}")
        print(f"result: refused (exit {EXIT_DEPENDENCIES}); nothing was launched")
        return EXIT_DEPENDENCIES
    argv = launch_argv(host, port, dev=False, proxy_mode=identity.is_proxy)
    print(f"  launch      {manual_command(argv)}")
    print("serving: this process becomes the server (exec); stop it with SIGTERM (docker stop) "
          "or Ctrl+C", flush=True)
    _hand_over(argv, env)
    return EXIT_OK                         # Windows: _hand_over exits with the server's status


def _console_utf8() -> None:
    """The banner and the ticks are not cp1252, Windows' pipe encoding."""
    _repo_importable()
    try:
        from scripts._console import force_utf8_stdio
    except ImportError:                       # pragma: no cover
        return
    force_utf8_stdio()


def main(argv=None):
    args = parse_args(argv)
    _console_utf8()
    if args.check:
        sys.exit(run_check(args))
    if args.serve:
        sys.exit(run_serve(args))

    load_env()
    # Law 15: a script that reads the instance prints the database path first.
    print(database_line(resolve_data_root(args.data_root)))
    banner()
    print(f"  {C.DIM}This script checks your environment and launches NEXUS.{C.RESET}")
    print(f"  {C.DIM}It never starts the model server. CTRL+C aborts at any time (exit {EXIT_INTERRUPTED}).{C.RESET}")
    print(f"  {C.DIM}Enter is No at every prompt (law 6): type y to begin.{C.RESET}")
    if not confirm(FLOW_PROMPTS[0], default=False):
        abort_declined("the operator declined to begin, or gave no answer; "
                       "nothing was checked and nothing was launched")

    failed = [step_python()]
    step_venv()
    failed.append(step_dependencies())
    ensure_env_file()
    load_env()
    failed.append(step_model_server())
    failed.append(step_data_root(args.data_root))
    step_auth()                          # W4 part 1: an unknown NEXUS_AUTH_MODE stops here, exit 2
    failed = [f for f in failed if f]
    if failed:
        # W1's verifier, F2: a cross above was followed by "All checks
        # complete". Only when every check passed does it say so.
        print(f"\n{C.YELLOW}{C.BOLD}  ⚠ Checks complete; continuing past what failed: "
              f"{', '.join(failed)}{C.RESET}\n")
    else:
        print(f"\n{C.GREEN}{C.BOLD}  ✓ All checks complete{C.RESET}\n")
    step_launch(args)
    # Here only when the launch was declined (the hand-over never returns).
    # Declining it after a check failed is declining a step the launch
    # needs: nothing passed and nothing was launched, so exit 1. With every
    # check passed, the command printed above is the result: exit 0.
    if failed:
        print(f"\n  Not started. (exit {EXIT_DECLINED}: a check failed — "
              f"{', '.join(failed)} — and the launch was declined)\n")
        sys.exit(EXIT_DECLINED)
    print(f"\n  Not started. (exit {EXIT_OK}: every check passed; the command "
          f"above starts it)\n")


if __name__ == "__main__":
    main()
