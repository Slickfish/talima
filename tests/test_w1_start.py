"""W1 part 1 — `start.py` checks, never starts, and launches what it says.

Declared behaviour (tier 3), one test per claim in run/specs/W1.md S3.1:

S1  THE EXIT TEST. `python start.py --check` against a bound-then-closed
    127.0.0.1 port exits 3 and names the refusal and the URL. A real closed
    port, a real subprocess, no mock of the check. Elapsed time printed:
    Linux refuses in about a millisecond, R.2 measured ~2.45 s on Windows.
S2  The check's other named reasons, each against a real local listener.
S3  The launch: this interpreter, no --reload unless --dev, the printed
    command is the argv, UVICORN_* stripped, the data root carried.
S4  The one hand-over exits with the child's status and its output (and
    what was printed before it) arrives, on every platform.
S5  An existing .env is never rewritten; a created one is .env.example
    with the chosen backend, carrying the empty NEXUS_API_TOKEN.
S6  A broken archive chain is reported broken, read-only.
S7  NEXUS_DATA_ROOT: `create_app()` with no arguments (uvicorn's factory
    call) puts every console file under it; unset, today's paths.
S8  The bind: --port beats NEXUS_PORT; a malformed port is named; any
    non-loopback host is flagged.

Guards (derived from start.py's AST, each with a planted companion that
the guard's source does not name):
G1  start.py creates processes only at five answered-for sites (three
    interpreter children whose argv starts with sys.executable, and the
    one hand-over), never through a shell.
G2  Every confirm() defaults to No (law 6), the two flow prompts included:
    no exemption (W1's close withdrew Ruling 3, the third verifier's
    critic, C2).

W1 part 5g (the completeness critic's C1, C2, C6, C10), at the end of the
file: the interactive exit codes are the docstring's table (1 for a declined
or unanswered step the launch needs, 130 for Ctrl+C, 0 only when launched or
when every check passed and only the launch was declined); the .env start.py
creates freezes learning from the day it is created (law 12), and the
server's own loader reads it so; the launch defaults to No beyond loopback;
the seed tip carries the Bearer header the gated routes need.

W1's close (the third verifier's must-fix M1 to M5, run/specs/W1.md S2b), at
the end of the file: --check and the launch fail, exit 4, on a requirement
or a launch target that does not import (M1); Enter at either flow prompt
starts nothing (M2); a launch that nothing sets learning for is frozen and
says so (M3); the database is named on the first line (M4); QUICKSTART's
manual launch is start.py's argv (M5).
"""
from __future__ import annotations

import ast
import functools
import hashlib
import http.server
import json
import os
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
from tests import _proc
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
START = ROOT / "start.py"
FIXTURE = ROOT / "tests" / "fixtures" / "pre_run_data"

import start  # noqa: E402  (the repo root is on sys.path under pytest)


def _closed_port() -> int:
    """A port that was bound and then closed: nothing listens there."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _child_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """The suite's environment, with the model-server settings removed and
    the .env search confined to `tmp_path` (never the operator's file)."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("OLLAMA_URL", "LM_STUDIO_URL", "LLM_BACKEND",
                        "NEXUS_PORT", "NEXUS_HOST", "NEXUS_DATA_ROOT")}
    env["NEXUS_ENV_ROOT"] = str(tmp_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# S1 — the exit test
# ---------------------------------------------------------------------------

def test_s1_the_check_refuses_a_closed_port_with_a_named_reason(tmp_path):
    port = _closed_port()
    url = f"http://127.0.0.1:{port}"
    root = tmp_path / "root"
    before = sorted(p.name for p in tmp_path.iterdir())
    t0 = time.perf_counter()
    # From the repo root, as start.py's docstring says to run it: since W1's
    # delta check (F1) --check imports the console where the launch would,
    # so from another directory its first failure is the import (exit 4,
    # test_m1_check_away_from_the_repo_root_checks_the_import_where_the_launch_runs).
    proc = _proc.run(
        [sys.executable, str(START), "--check", "--data-root", str(root)],
        cwd=ROOT, env=_child_env(tmp_path, OLLAMA_URL=url),
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)
    elapsed = time.perf_counter() - t0
    print(f"S1 ({sys.platform}) exit {proc.returncode} in {elapsed:.2f} s; "
          f"output:\n{proc.stdout}{proc.stderr}")
    # The documented code as a literal, never start.py's own constant (W1's
    # verifier, F1: with the constant set to 0 this passed while --check said
    # "every check passed").
    assert proc.returncode == 3, (
        f"--check against a closed port exited {proc.returncode}, not 3:"
        f"\n{proc.stdout}\n{proc.stderr}")
    assert "result: a check failed (exit 3)" in proc.stdout, proc.stdout
    assert "connection refused" in proc.stdout, (
        f"the reason is not named as a refusal:\n{proc.stdout}")
    assert f"{url}/api/tags" in proc.stdout, "the URL it tried is not named"
    assert "does not start Ollama" in proc.stdout
    assert sorted(p.name for p in tmp_path.iterdir()) == before, (
        "--check wrote something")
    assert not root.exists(), "--check created the data root"


# ---------------------------------------------------------------------------
# S2 — every other way a model server can fail, against real listeners
# ---------------------------------------------------------------------------

class _Fixed(http.server.BaseHTTPRequestHandler):
    status = 200
    body = b""

    def do_GET(self):  # noqa: N802
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


def _serve(status: int, body: bytes):
    handler = type("H", (_Fixed,), {"status": status, "body": body})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.mark.parametrize("status,body,expected", [
    (404, b"not here", "answered HTTP 404"),
    (200, b"<html>a web page</html>", "not with JSON"),
    (200, b'{"version": "0.12"}', "not as Ollama does"),
])
def test_s2_a_listener_that_is_not_ollama_is_named(status, body, expected):
    server, url = _serve(status, body)
    try:
        result = start.check_model_server("ollama", url, timeout=5.0)
    finally:
        server.shutdown()
    print(f"S2 {status} {body[:20]!r}: {result.reason}")
    assert not result.reachable
    assert expected in result.reason


def test_s2_a_live_ollama_lists_its_models():
    payload = json.dumps({"models": [{"name": "phi4-mini:3.8b-q4_K_M"},
                                     {"name": "nomic-embed-text"}]}).encode()
    server, url = _serve(200, payload)
    try:
        result = start.check_model_server("ollama", url)
    finally:
        server.shutdown()
    assert result.reachable and result.reason == ""
    assert result.models == ("phi4-mini:3.8b-q4_K_M", "nomic-embed-text")


def test_s2_lm_studio_is_checked_the_same_way():
    server, url = _serve(200, json.dumps({"data": [{"id": "r1-distill"}]}).encode())
    try:
        result = start.check_model_server("lmstudio", url)
    finally:
        server.shutdown()
    assert result.reachable and result.models == ("r1-distill",)
    assert result.url == f"{url}/v1/models"


def test_s2_a_listener_that_never_answers_is_a_timeout():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)                       # the handshake completes; no reply
        url = f"http://127.0.0.1:{s.getsockname()[1]}"
        result = start.check_model_server("ollama", url, timeout=0.5)
    print(f"S2 silent listener: {result.reason}")
    assert not result.reachable and "timed out" in result.reason


def test_s2_an_unusable_url_is_named_before_any_connection():
    result = start.check_model_server("ollama", "localhost:11434")
    assert not result.reachable and "not an http(s) URL" in result.reason


# ---------------------------------------------------------------------------
# S3 — the launch
# ---------------------------------------------------------------------------

def test_s3_reload_only_under_dev_and_this_interpreter_always():
    plain = start.launch_argv("127.0.0.1", 8000, dev=False)
    dev = start.launch_argv("127.0.0.1", 8000, dev=True)
    assert plain[0] == sys.executable and dev[0] == sys.executable
    assert "--reload" not in plain, "the default launch reloads"
    assert dev[-1] == "--reload" and dev[:-1] == plain
    assert "backend.server:create_app" in plain and "--factory" in plain
    assert plain[plain.index("--port") + 1] == "8000"


def test_s3_the_printed_command_is_the_launch():
    argv = start.launch_argv("127.0.0.1", 8123, dev=False)
    printed = start.manual_command(argv)
    if os.name == "nt":
        # W1's verifier, N1: comparing with list2cmdline restated the code.
        # What the operator needs: every argument in it, and which shell the
        # syntax is for.
        assert all(a in printed for a in argv), printed
        assert "cmd.exe" in start.manual_advice(argv) and "PowerShell" in start.manual_advice(argv)
    else:
        assert shlex.split(printed) == argv, (
            f"the printed command {printed!r} does not parse back to the argv "
            f"start.py launches")
        assert start.manual_advice(argv) == printed


def test_s3_the_server_environment_drops_uvicorn_settings_and_carries_the_root():
    environ = {"PATH": "/bin", "UVICORN_RELOAD": "true", "uvicorn_workers": "4",
               "NEXUS_API_TOKEN": "t"}
    env = start.launch_env(environ, data_root="/srv/nexus")
    assert not [k for k in env if k.upper().startswith("UVICORN_")]
    assert env["NEXUS_DATA_ROOT"] == "/srv/nexus"
    assert env["PATH"] == "/bin" and env["NEXUS_API_TOKEN"] == "t"
    assert "NEXUS_DATA_ROOT" not in start.launch_env(environ)


# ---------------------------------------------------------------------------
# S4 — the one hand-over, on every platform
# ---------------------------------------------------------------------------

def test_s4_the_hand_over_exits_with_the_childs_status_and_keeps_its_output(tmp_path):
    driver = tmp_path / "driver.py"
    driver.write_text(textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(ROOT)!r})
        import start
        print("printed before the hand-over")     # buffered under a pipe
        start._hand_over([sys.executable, "-c",
                          "print('the child ran'); raise SystemExit(7)"],
                         dict(os.environ))
        print("unreachable")
    """), encoding="utf-8")
    proc = _proc.run([sys.executable, str(driver)], cwd=tmp_path,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    print(f"S4 ({sys.platform}) exit {proc.returncode}: {proc.stdout!r}")
    assert proc.returncode == 7, (
        "the launcher's exit status is not the server's: on Windows os.exec* "
        "returns control to the console at once")
    assert "printed before the hand-over" in proc.stdout, (
        "output printed before the exec was lost (exec discards Python's "
        "buffers unless they are flushed)")
    assert "the child ran" in proc.stdout and "unreachable" not in proc.stdout


# ---------------------------------------------------------------------------
# S5 — .env
# ---------------------------------------------------------------------------

OPERATOR_ENV = (
    "# my settings, with comments\n"
    "export NEXUS_API_TOKEN=\"s3cr3t token\"\n"
    "\n"
    "LLM_BACKEND='ollama'   # inline comment\n"
    "SEARX=\"a # b\"\n"
    "MULTI=\"line one\nline two\"\n")


def test_s5_an_existing_env_is_read_and_never_rewritten(tmp_path):
    env = tmp_path / ".env"
    env.write_text(OPERATOR_ENV, encoding="utf-8")
    before = hashlib.sha256(env.read_bytes()).hexdigest()
    assert start.ensure_env_file(env, ROOT / ".env.example") == env
    assert hashlib.sha256(env.read_bytes()).hexdigest() == before, (
        "start.py rewrote the operator's .env")


def test_s5_a_created_env_is_the_example_with_the_chosen_backend(tmp_path, monkeypatch):
    """Exactly two lines differ from .env.example: the chosen backend and
    the day learning was frozen (part 5g, C2; the test owns the clock, and
    `raising=False` so a start.py without the clock fails on the file it
    writes, not on the missing name)."""
    answers = iter(["y", "2"])
    monkeypatch.setattr("builtins.input", lambda *_a: next(answers))
    monkeypatch.setattr(start, "utc_today", lambda: "2026-11-01", raising=False)
    env = tmp_path / ".env"
    example = ROOT / ".env.example"
    assert start.ensure_env_file(env, example) == env
    made = env.read_text(encoding="utf-8").splitlines()
    want = example.read_text(encoding="utf-8").splitlines()
    assert len(made) == len(want)
    diff = {a: b for a, b in zip(want, made) if a != b}
    assert len(diff) == 2, diff
    backend_line = next((b for a, b in diff.items() if a.startswith("LLM_BACKEND=")), "")
    assert backend_line.startswith("LLM_BACKEND=lmstudio"), diff
    assert "# or lmstudio" in backend_line, "the example's comment was dropped"
    assert diff.get("NEXUS_LEARNING_FROZEN_SINCE=") == "NEXUS_LEARNING_FROZEN_SINCE=2026-11-01", diff
    assert "NEXUS_API_TOKEN=" in made, (
        "the created .env lacks the empty key the server's banner names; a "
        "launcher that writes a generated secret has made a decision")


def test_s5_no_input_creates_nothing(tmp_path, monkeypatch):
    def eof(*_a):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    assert start.ensure_env_file(tmp_path / ".env", ROOT / ".env.example") is None
    assert not (tmp_path / ".env").exists(), "EOF was taken as consent"


# ---------------------------------------------------------------------------
# S6 — the archive chain, read-only and honest
# ---------------------------------------------------------------------------

def _md5_tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.md5(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.mark.sqlite_only("start.py's checks read the console's SQLite files under the data root (the archive chain, the stores' presence); the URL form is W3b part 3")
def test_s6_a_valid_chain_is_valid_and_a_broken_one_is_reported_broken(tmp_path):
    good, bad = tmp_path / "good", tmp_path / "bad"
    for d in (good, bad):
        d.mkdir()
        shutil.copy2(FIXTURE / "archive.db", d / "archive.db")
    with sqlite3.connect(bad / "archive.db") as c:
        c.execute("UPDATE archive_blocks SET timestamp = timestamp + 1 "
                  "WHERE idx = (SELECT MIN(idx) FROM archive_blocks)")
    trees = {d: _md5_tree(d) for d in (good, bad)}

    ok_report = start.report_data_root(good)
    bad_report = start.report_data_root(bad)
    print(f"S6 good: {ok_report.chain_text} | bad: {bad_report.chain_text}")
    assert ok_report.chain_valid is True
    assert bad_report.chain_valid is False and "DOES NOT VERIFY" in bad_report.chain_text
    for d in (good, bad):
        assert _md5_tree(d) == trees[d], f"reading {d.name}'s chain wrote to it"


def test_s6_an_empty_root_reports_absence_and_creates_nothing(tmp_path):
    report = start.report_data_root(tmp_path / "nowhere")
    assert report.kb_bytes is None and report.chain_valid is None
    assert not (tmp_path / "nowhere").exists()


# ---------------------------------------------------------------------------
# S7 — NEXUS_DATA_ROOT, through uvicorn's zero-argument factory call
# ---------------------------------------------------------------------------

WRITER_SETTINGS = ("NEXUS_CALIBRATION_LOG", "NEXUS_CITATION_CAPTURE_PATH",
                   "NEXUS_FIRE_CAPTURE_PATH")


def _console_paths(app) -> dict[str, Path]:
    from backend.eval import calibration_log, citation_capture, fire_capture
    svc = app.state.services
    return {
        "kb.db": Path(svc.store.db_path),
        "archive.db": Path(svc.archive_chain.db_path),
        "turn_metrics.jsonl": Path(svc.evaluator.log_path),
        "calibration_log.jsonl": calibration_log.log_path(),
        "citation_capture.jsonl": citation_capture.capture_path(),
        "fire_capture.jsonl": fire_capture.capture_path(),
    }


@pytest.mark.sqlite_only("start.py's checks read the console's SQLite files under the data root (the archive chain, the stores' presence); the URL form is W3b part 3")
def test_s7_the_data_root_holds_every_console_file(tmp_path, monkeypatch):
    from backend.server import create_app
    cwd, root = tmp_path / "cwd", tmp_path / "root"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    for name in WRITER_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NEXUS_DATA_ROOT", str(root))
    app = create_app()                          # what `--factory` calls
    paths = _console_paths(app)
    print(f"S7 under the root: {sorted(p.relative_to(root).as_posix() for p in paths.values())}")
    for name, path in paths.items():
        assert path == root / name, f"{name} is at {path}, not under the root"
    for made in ("kb.db", "archive.db", "chroma", "snapshots"):
        assert (root / made).exists(), f"{made} was not created under the root"
    assert not (cwd / "data").exists(), "a console file landed under ./data"


def test_s7_unset_create_app_builds_exactly_what_it_built_before(tmp_path, monkeypatch):
    """Unset (or empty), `create_app()` calls `create_services()` with no
    arguments at all, so every store keeps its own default, and the three
    writers resolve to their constants. (Where those defaults then land in
    the suite is conftest's redirection, not this setting's.)"""
    import backend.server as server
    from backend.eval import calibration_log, citation_capture, fire_capture
    seen = []
    real = server.create_services

    def recording(**kw):
        # W2 adds `lock_mode` (the single-writer lock's taker name), which is
        # not a path; the claim is about the path arguments.
        mode = kw.pop("lock_mode", "services")
        seen.append(kw)
        return real(**{k: tmp_path / Path(v).name for k, v in kw.items()}, lock_mode=mode) if kw else real(
            kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
            eval_log_path=tmp_path / "m.jsonl", archive_db_path=tmp_path / "a.db",
            snapshots_dir=tmp_path / "snap", lock_mode=mode)
    monkeypatch.setattr(server, "create_services", recording)
    for value in (None, "", "   "):
        if value is None:
            monkeypatch.delenv("NEXUS_DATA_ROOT", raising=False)
        else:
            monkeypatch.setenv("NEXUS_DATA_ROOT", value)
        server.create_app()
    assert seen == [{}, {}, {}], f"create_app() passed paths with no root set: {seen}"
    for name in WRITER_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("NEXUS_DATA_ROOT", raising=False)
    assert calibration_log.log_path() == Path(calibration_log.DEFAULT_LOG_PATH)
    assert citation_capture.capture_path() == Path(citation_capture.DEFAULT_CAPTURE_PATH)
    assert fire_capture.capture_path() == Path(fire_capture.DEFAULT_FIRE_CAPTURE_PATH)


def test_s7_a_writers_own_setting_beats_the_root(tmp_path, monkeypatch):
    from backend.eval import calibration_log
    monkeypatch.setenv("NEXUS_DATA_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "mine.jsonl"))
    assert calibration_log.log_path() == tmp_path / "mine.jsonl"


# ---------------------------------------------------------------------------
# S8 — the bind
# ---------------------------------------------------------------------------

def test_s8_port_precedence_and_named_errors(monkeypatch):
    monkeypatch.delenv("NEXUS_HOST", raising=False)
    monkeypatch.setenv("NEXUS_PORT", "9001")
    assert start.resolve_bind() == ("127.0.0.1", 9001)
    assert start.resolve_bind(8100) == ("127.0.0.1", 8100), "--port lost to NEXUS_PORT"
    for bad in ("", "abc"):
        monkeypatch.setenv("NEXUS_PORT", bad)
        with pytest.raises(start.BindError, match="NEXUS_PORT"):
            start.resolve_bind()
    with pytest.raises(start.BindError, match="--port=70000"):
        start.resolve_bind(70000)


@pytest.mark.parametrize("host,loopback", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True), ("127.0.0.2", True),
    ("0.0.0.0", False), ("::", False), ("192.168.1.50", False), ("nexus.lan", False),
])
def test_s8_every_non_loopback_host_is_flagged(host, loopback):
    assert start.is_loopback(host) is loopback


# ---------------------------------------------------------------------------
# G1 — processes, by AST
# ---------------------------------------------------------------------------

SPAWN_MODULE_CALLS = {
    "subprocess": {"run", "Popen", "call", "check_call", "check_output",
                   "getoutput", "getstatusoutput"},
    "os": {"system", "popen", "execv", "execve", "execvp", "execvpe", "execl",
           "execle", "execlp", "execlpe", "spawnv", "spawnve", "spawnvp",
           "spawnvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "startfile",
           "posix_spawn", "posix_spawnp", "fork", "forkpty"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    "pty": {"spawn", "fork"},
    "multiprocessing": {"Process", "Pool", "get_context"},
    "concurrent.futures": {"ProcessPoolExecutor"},
}
SPAWN_NAMES = set().union(*SPAWN_MODULE_CALLS.values())

# (function, call): the sites start.py may create a process at, and why.
ANSWERED_SPAWNS = {
    ("step_venv", "subprocess.run"): "python -m venv, argv[0] sys.executable",
    ("step_dependencies", "subprocess.run"): "pip install -r requirements.txt, argv[0] sys.executable",
    ("launch_target_problem", "subprocess.run"): "python -c 'import backend.server' with the launch's "
                                                 "environment, argv[0] sys.executable (W1's close, M1)",
    ("_hand_over", "os.execve"): "the one hand-over, POSIX",
    ("_hand_over", "subprocess.Popen"): "the one hand-over, Windows",
}
# Who may call the hand-over, and with what argv (W1's verifier, F6: a
# `start_ollama` that called `_hand_over(["ollama", "serve"], ...)` passed).
ANSWERED_HAND_OVERS = {
    "step_launch": "argv from launch_argv(...)",
    "step_venv": "[str(venv_python()), str(ROOT / 'start.py'), *sys.argv[1:]]",
    # W3 — --serve: the same argv from launch_argv(...), with dev=False, after
    # the checks that need no answer; the container's PID 1 becomes uvicorn.
    "run_serve": "argv from launch_argv(..., dev=False)",
}


def _aliases(tree) -> dict[str, str]:
    """local name -> module or module.call, from every import."""
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def _spawn_call(f, aliases) -> "str | None":
    """The module.call a call's function names, if it is a spawn: a plain or
    aliased name, or an attribute chain ending module.call (start.subprocess.Popen)."""
    if isinstance(f, ast.Name):
        target = aliases.get(f.id, "")
        mod, _, call = target.rpartition(".")
        return target if call in SPAWN_MODULE_CALLS.get(mod, ()) else None
    if isinstance(f, ast.Attribute):
        chain = ast.unparse(f).split(".")
        for k in range(len(chain) - 1):
            mod = ".".join(chain[k:-1])
            mod = aliases.get(mod, mod)
            if chain[-1] in SPAWN_MODULE_CALLS.get(mod, ()):
                return f"{mod}.{chain[-1]}"
    return None


def spawn_sites(source: str) -> list[tuple[str, str, int, bool]]:
    """(function, module.call, line, shell=True) for every process-creating
    call, and for every call of the hand-over (call `_hand_over`)."""
    return [(fn, call, node.lineno, shell) for fn, call, node, shell in _spawn_nodes(source)]


def _spawn_nodes(source: str) -> list:
    tree = ast.parse(source)
    aliases = _aliases(tree)
    out = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack = ["<module>"]

        def _fn(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = _fn

        def visit_Call(self, node):
            dotted = _spawn_call(node.func, aliases)
            if dotted is None and isinstance(node.func, ast.Name) and node.func.id == "_hand_over":
                dotted = "_hand_over"
            if dotted is not None:
                shell = any(k.arg == "shell" and not (isinstance(k.value, ast.Constant)
                                                      and k.value.value is False)
                            for k in node.keywords)
                out.append((self.stack[-1], dotted, node, shell))
            self.generic_visit(node)

    Visitor().visit(tree)
    return sorted(out, key=lambda s: s[2].lineno)


def _dynamic_spawns(source: str) -> list[str]:
    """Process creation the call table cannot see: a module imported by a
    string, or a spawn function fetched by `getattr`."""
    found = []
    for n in ast.walk(ast.parse(source)):
        if not isinstance(n, ast.Call):
            continue
        name = ast.unparse(n.func)
        first = n.args[0] if n.args else None
        if name in ("importlib.import_module", "__import__", "import_module") \
                and isinstance(first, ast.Constant) and str(first.value).split(".")[0] in SPAWN_MODULE_CALLS:
            found.append(f"line {n.lineno}: {name}({first.value!r}) imports a process module by name")
        if name == "getattr" and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant) \
                and n.args[1].value in SPAWN_NAMES:
            found.append(f"line {n.lineno}: getattr(..., {n.args[1].value!r}) fetches a spawn function")
    return found


def _hand_over_argv_ok(fn: str, node: ast.Call, tree) -> bool:
    argv = node.args[0] if node.args else None
    if fn in ("step_launch", "run_serve"):
        # The launch (W1) and --serve (W3) hand over the one argv `launch_argv`
        # builds, bound exactly once in the function.
        launch = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == fn)
        bound = [a.value for a in ast.walk(launch) if isinstance(a, ast.Assign)
                 and any(getattr(t, "id", None) == "argv" for t in a.targets)]
        return (isinstance(argv, ast.Name) and argv.id == "argv" and len(bound) == 1
                and isinstance(bound[0], ast.Call) and ast.unparse(bound[0].func) == "launch_argv")
    if fn == "step_venv":
        return (isinstance(argv, ast.List) and len(argv.elts) >= 2
                and ast.unparse(argv.elts[0]) == "str(venv_python())"
                and ast.unparse(argv.elts[1]) == "str(ROOT / 'start.py')")
    return False


def spawn_violations(source: str) -> list[str]:
    tree = ast.parse(source)
    found = []
    for fn, call, node, shell in _spawn_nodes(source):
        if shell:
            found.append(f"{fn}:{node.lineno} {call}(shell=True)")
        if call == "_hand_over":
            if fn not in ANSWERED_HAND_OVERS:
                found.append(f"{fn}:{node.lineno} calls the hand-over, and is not an answered caller")
            elif not _hand_over_argv_ok(fn, node, tree):
                found.append(f"{fn}:{node.lineno} hands over an argv that is not {ANSWERED_HAND_OVERS[fn]}")
            continue
        if (fn, call) not in ANSWERED_SPAWNS:
            found.append(f"{fn}:{node.lineno} {call} is not an answered-for site")
        elif call == "subprocess.run":
            argv = node.args[0] if node.args else None
            if not (isinstance(argv, ast.List) and argv.elts and ast.unparse(argv.elts[0]) == "sys.executable"):
                found.append(f"{fn}:{node.lineno}: a child whose argv does not start with sys.executable")
    return found + _dynamic_spawns(source)


def test_g1_start_py_creates_processes_only_at_the_answered_sites():
    source = START.read_text(encoding="utf-8")
    sites = spawn_sites(source)
    print(f"G1 spawn sites, derived: {sites}")
    assert {(fn, call) for fn, call, _l, _s in sites if call != "_hand_over"} == set(ANSWERED_SPAWNS), (
        "the derived spawn sites are not the answered-for set (in either direction)")
    assert {fn for fn, call, _l, _s in sites if call == "_hand_over"} == set(ANSWERED_HAND_OVERS)
    assert not spawn_violations(source), spawn_violations(source)
    launched = start.launch_argv("127.0.0.1", 8000, dev=False)
    assert launched[0] == sys.executable


G1_PLANTED = {
    "Popen": 'def start_ollama():\n    subprocess.Popen(["ollama", "serve"])\n',
    "os.system": 'def check():\n    os.system("ollama serve")\n',
    "shell=True": 'def probe(url):\n    subprocess.run(f"curl -s {url}/api/tags", shell=True)\n',
    "an aliased Popen": 'from subprocess import Popen as P\ndef go():\n    P(["ollama", "serve"])\n',
    # F6: the forms the first guard's table did not name
    "the hand-over to a daemon": 'def start_ollama():\n    _hand_over(["ollama", "serve"], dict(os.environ))\n',
    "pty.spawn": 'import pty\ndef go():\n    pty.spawn(["ollama", "serve"])\n',
    "a module imported by name": 'import importlib\ndef go():\n    importlib.import_module("subprocess").Popen(["ollama", "serve"])\n',
    "getattr on os": 'def go():\n    getattr(os, "system")("ollama serve")\n',
    "multiprocessing": 'import multiprocessing\ndef go():\n    multiprocessing.Process(target=print).start()\n',
    "an attribute chain": 'import start\ndef go():\n    start.subprocess.Popen(["ollama", "serve"])\n',
    "fork": 'def go():\n    os.fork()\n',
}


@pytest.mark.parametrize("name", sorted(G1_PLANTED))
def test_g1_companion_a_planted_daemon_start_is_named(name):
    source = START.read_text(encoding="utf-8") + "\n" + G1_PLANTED[name]
    assert spawn_violations(source), f"the guard let this through:\n{G1_PLANTED[name]}"


def test_g1_companion_a_second_child_deeper_inside_an_answered_function_is_named():
    """F6: only the first `.run` of an answered function used to be read."""
    source = START.read_text(encoding="utf-8")
    marker = '    step(3, "Dependencies")\n'
    assert source.count(marker) == 1
    planted = source.replace(marker, marker + '    if os.environ.get("X"):\n'
                                               '        subprocess.run(["ollama", "serve"])\n')
    assert any("does not start with sys.executable" in v for v in spawn_violations(planted))


# ---------------------------------------------------------------------------
# G2 — consent defaults to No, by AST
# ---------------------------------------------------------------------------

# The two prompts about start.py's own flow, as the operator reads them. The
# test names them itself (W1's verifier, F5: reading start.FLOW_PROMPTS made
# the guard crash on base instead of failing on base's default-Yes acts).
FLOW_PROMPT_TEXTS = ("Begin startup sequence?", "Start NEXUS now?")
# Raw input() outside confirm(), each answered for: none of them is consent.
ANSWERED_INPUTS = {"ensure_env_file": "which model server, 1 or 2: a choice, not an act"}

# No prompt is exempt (W1's close). Part 5g's table let the first flow prompt
# default to True and the launch prompt to `is_loopback(host)` (Ruling 3);
# the third verifier's critic (C2) showed the launch's first act writes the
# store, and law 6 is a §3 law no ruling relaxes, so the table is gone and a
# default other than the literal False is a violation wherever it is written.


def _confirm_default(tree) -> bool:
    """What confirm() defaults to when a call passes nothing: its own
    signature's default (base's was True)."""
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "confirm"), None)
    if fn is None or not fn.args.defaults:
        return False
    d = fn.args.defaults[-1]
    return bool(d.value) if isinstance(d, ast.Constant) else True


def _flow_prompts(tree) -> dict[str, str]:
    """`FLOW_PROMPTS[i]` -> its text, read from the module's own literal."""
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "FLOW_PROMPTS" for t in n.targets) \
                and isinstance(n.value, ast.Tuple):
            return {f"FLOW_PROMPTS[{i}]": e.value for i, e in enumerate(n.value.elts) if isinstance(e, ast.Constant)}
    return {}


def confirm_defaults(source: str) -> list[tuple[str, object, int]]:
    """(prompt as written, default, line) for every call of confirm, under
    any name: the bare name, an attribute (`start.confirm`), an alias, or a
    `partial` of it."""
    tree = ast.parse(source)
    names = {"confirm"} | {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                           and isinstance(n.value, ast.Name) and n.value.id == "confirm"
                           for t in n.targets if isinstance(t, ast.Name)}
    flow, omitted = _flow_prompts(tree), _confirm_default(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f, args = node.func, list(node.args)
        is_partial = ast.unparse(f) in ("partial", "functools.partial") and args \
            and ast.unparse(args[0]).split(".")[-1] in names
        if is_partial:
            args = args[1:]
        elif not ((isinstance(f, ast.Name) and f.id in names)
                  or (isinstance(f, ast.Attribute) and f.attr == "confirm")):
            continue
        prompt = ast.unparse(args[0]) if args else "?"
        prompt = flow.get(prompt, prompt.strip("'\"f"))
        default = next((k.value for k in node.keywords if k.arg == "default"), None)
        if default is None and len(args) > 1:
            default = args[1]
        # A literal default is its value; a computed one is its source, so a
        # computed default is a violation like any value but False, and the
        # violation names it as written.
        value = (default.value if isinstance(default, ast.Constant) else
                 ast.unparse(default) if default is not None else omitted)
        out.append((prompt, value, node.lineno))
    return out


def consent_violations(source: str) -> list[str]:
    tree = ast.parse(source)
    found = [f"line {line}: {prompt} defaults to {value!r}"
             for prompt, value, line in confirm_defaults(source)
             if value is not False]

    class Visitor(ast.NodeVisitor):
        stack = ["<module>"]

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == "input" \
                    and self.stack[-1] != "confirm" and self.stack[-1] not in ANSWERED_INPUTS:
                found.append(f"line {node.lineno}: a raw input() in {self.stack[-1]}, outside confirm()")
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


def test_g2_every_act_prompt_defaults_to_no():
    source = START.read_text(encoding="utf-8")
    calls = confirm_defaults(source)
    print(f"G2 confirm() calls, derived: {calls}")
    assert len(calls) >= 6, "the population collapsed"
    assert not consent_violations(source), consent_violations(source)
    # The two flow prompts are in the population the rule reads (each read
    # through FLOW_PROMPTS), so "no violation" covers them too.
    prompts = {p for p, _v, _l in calls}
    assert set(FLOW_PROMPT_TEXTS) <= prompts, (set(FLOW_PROMPT_TEXTS) - prompts, prompts)


# The two flow prompts' calls as start.py writes them (W1's close: both No).
FLOW_CALLS = {"Begin startup sequence?": "confirm(FLOW_PROMPTS[0], default=False)",
              "Start NEXUS now?": "confirm(FLOW_PROMPTS[1], default=False)"}

# The class G2 forbids at a flow prompt: any default but No. The first two
# are Ruling 3's (part 5g's True at the first prompt, the loopback predicate
# at the launch); the others are members neither the ruling nor the guard's
# source names.
G2_FLOW_PLANTED = {
    "True": "default=True",
    "the loopback predicate (Ruling 3, withdrawn)": "default=is_loopback(host)",
    "another predicate": "default=bool(host)",
    "a truthy literal": "default=1",
}


@pytest.mark.parametrize("prompt", sorted(FLOW_CALLS))
@pytest.mark.parametrize("name", sorted(G2_FLOW_PLANTED))
def test_g2_companion_a_flow_prompt_defaulting_to_anything_but_no_is_named(prompt, name):
    """COMPANION (OQ-16 rules 1 and 3). Pre-fix (law 16), at 1a1a521 the
    table exempted True at the first prompt and `is_loopback(host)` at the
    launch, so those two plants passed (W1.md S2b, M2)."""
    source = START.read_text(encoding="utf-8")
    call = FLOW_CALLS[prompt]
    assert source.count(call) == 1, f"the call moved; re-pin it: {call}"
    planted = source.replace(call, call.replace("default=False", G2_FLOW_PLANTED[name]))
    found = consent_violations(planted)
    assert any(prompt in v for v in found), f"{prompt!r} with {name} passed: {found}"


G2_PLANTED = {
    "a default-Yes act": '\ndef pull():\n    if confirm(f"Pull {MODEL} now? (~15GB)", default=True):\n        pass\n',
    # F7: the forms the first guard, bound to the bare name, did not see
    "an alias": '\nask = confirm\ndef pull():\n    if ask("Pull the models now? (~15GB)", default=True):\n        pass\n',
    "an attribute call": '\nimport start as _s\ndef pull():\n    if _s.confirm("Pull the models now?", default=True):\n        pass\n',
    "a partial": '\nimport functools\ndef pull():\n    if functools.partial(confirm, default=True)("Pull now?"):\n        pass\n',
    "a raw input taking empty as yes": '\ndef pull():\n    if input("Pull now? [Y/n]").strip() in ("", "y"):\n        pass\n',
}


@pytest.mark.parametrize("name", sorted(G2_PLANTED))
def test_g2_companion_a_planted_default_yes_act_is_named(name):
    source = START.read_text(encoding="utf-8") + G2_PLANTED[name]
    assert consent_violations(source), f"{name} passed"


# ===========================================================================
# W1 part 5b — the verifier's lens 1 findings
# ===========================================================================
#
# C-series: start.py driven as the operator drives it, a real subprocess on a
# temporary copy of the files it runs from (so it can be given a .env and a
# data root of its own, and so the same test runs on base: the pre-fix runs
# fail on these tests' assertions, W1's verifier F5).

def _checkout(tmp_path: Path) -> Path:
    """A copy of start.py, .env.example, requirements.txt and the tracked
    backend/ and scripts/ trees: what start.py runs from."""
    from tests._tracked import tracked
    dest = tmp_path / "checkout"
    for src in [START, ROOT / ".env.example", ROOT / "requirements.txt",
                *tracked("backend", "scripts")]:
        target = dest / src.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    return dest


def _ollama(models=("deepseek-r1:14b-qwen-distill-q4_K_M", "phi4-mini:3.8b-q4_K_M",
                    "nomic-embed-text")):
    """A live listener that answers /api/tags as Ollama does."""
    return _serve(200, json.dumps({"models": [{"name": m} for m in models]}).encode())


# The interpreter is the series' input (CI run 20). QUICKSTART has the
# operator create .venv, activate it and then run `python start.py`, so step
# 2 finds a virtual environment and step 3 finds every requirement, and
# neither asks anything. The series ran start.py with pytest's own
# interpreter: here that is .venv's, and on CI's runners it is the runner's
# Python, which is not a virtual environment's. There step 2 asked "Create
# .venv now?" and "Continue without venv?" and took the series' answers for
# them; in C7b the "y" meant for the chain created a .venv in the checkout,
# and start.py re-ran inside it with its piped answers already read. Five
# tests failed on all three runners and passed here. So the series fixes the
# interpreter: a virtual environment made for the module, without pip,
# handed this interpreter's site directories so it imports what this one
# does. Its premise is checked before it is used, and `_run` refuses any
# interpreter that is not a virtual environment's, by name.

PREMISE_DEPENDENCIES = (
    "import json, sys; sys.path.insert(0, sys.argv[1]); import start; "
    "print(json.dumps(start.dependency_problems()))")


@functools.lru_cache(maxsize=None)
def _is_venv(python: str) -> bool:
    """Whether `python` says it is a virtual environment's interpreter: asked
    of the interpreter, never read from its path. One that cannot be asked
    is not."""
    try:
        proc = _proc.run(
            [python, "-c", "import sys; print(sys.prefix != sys.base_prefix)"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except OSError:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "True"


def _premise_problems(python: str, env: dict) -> list[str]:
    """What would make steps 2 and 3 ask a question the series does not
    answer: not a virtual environment's interpreter, or a requirement that
    does not import under `env`, by start.py's own check."""
    if not _is_venv(python):
        return [f"{python} is not a virtual environment's interpreter"]
    proc = _proc.run([python, "-c", PREMISE_DEPENDENCIES, str(ROOT)], env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=240)
    if proc.returncode != 0:
        return [f"start.py's dependency check did not run: {(proc.stderr or proc.stdout)[-300:]}"]
    problems = json.loads(proc.stdout.strip().splitlines()[-1])
    return [f"does not import {pkg} ({why})" for pkg, why in sorted(problems.items())]


def _operator_python(directory: Path, env: dict, *, hand_site_dirs: bool = True) -> str:
    """A virtual environment in `directory`, without pip; with
    `hand_site_dirs`, a .pth gives it this interpreter's site directories
    (each through site.addsitedir, so their own .pth files are read).
    Refuses, as the premise, an interpreter the series cannot use."""
    import site
    import venv
    venv.EnvBuilder(with_pip=False, symlinks=(os.name != "nt"), clear=True).create(directory)
    python = directory / "Scripts" / "python.exe" if os.name == "nt" else directory / "bin" / "python"
    if hand_site_dirs:
        # ascii() on the child's side: the path crosses the pipe as ASCII, so
        # no console code page can change it.
        purelib = ast.literal_eval(_proc.run(
            [str(python), "-c", "import sysconfig; print(ascii(sysconfig.get_paths()['purelib']))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
            timeout=60).stdout.strip())
        on_path = {os.path.normcase(os.path.abspath(p)) for p in sys.path if p}
        dirs = [d for d in [*site.getsitepackages(), site.getusersitepackages()]
                if Path(d).name in ("site-packages", "dist-packages")
                and os.path.normcase(os.path.abspath(d)) in on_path]
        # ascii(): site.py reads a .pth in the locale's encoding, so the
        # file is ASCII and reads the same under every locale.
        Path(purelib, "nexus_operator_paths.pth").write_text(
            "import site; " + "; ".join(f"site.addsitedir({ascii(d)})" for d in dirs) + "\n",
            encoding="ascii")
    problems = _premise_problems(str(python), env)
    assert not problems, f"premise: the series' interpreter cannot be used: {problems}"
    return str(python)


@pytest.fixture(scope="module")
def operator_python(tmp_path_factory) -> str:
    """The interpreter every C-series run of start.py uses, on every
    platform: a virtual environment's that imports the requirements."""
    base = tmp_path_factory.mktemp("operator")
    return _operator_python(base / "venv", _child_env(base))


def _run(checkout: Path, args, *, answers: str | None, env: dict, python: str, timeout=240,
         cwd: "Path | None" = None):
    """`python start.py <args>` in `checkout` with the interpreter given (never
    pytest's own: CI run 20), `answers` on stdin (None: no stdin at all, EOF
    at the first prompt). `cwd`, when given, is the directory the operator
    runs it from (default: the checkout, its repo root)."""
    assert _is_venv(python), (
        f"premise: {python} is not a virtual environment's interpreter, so step 2 "
        f"would ask what the series does not answer")
    proc = _proc.run(
        [python, str(checkout / "start.py"), *args], cwd=cwd or checkout, env=env,
        input=answers, stdin=subprocess.DEVNULL if answers is None else None,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return proc


NO_TO_EVERYTHING = "y\n" + "n\n" * 40          # begin, then decline every act and the launch


def test_c2_an_existing_env_survives_a_full_interactive_run(tmp_path, operator_python):
    """S5's claim through the CLI: start.py reads the operator's .env and
    leaves its bytes alone, however far the run goes (base rewrote it)."""
    checkout = _checkout(tmp_path)
    env_file = checkout / ".env"
    env_file.write_text(OPERATOR_ENV + f"OLLAMA_URL=http://127.0.0.1:{_closed_port()}\n", encoding="utf-8")
    before = env_file.read_bytes()
    proc = _run(checkout, [], answers=NO_TO_EVERYTHING, python=operator_python,
                env=_child_env(tmp_path, NEXUS_ENV_ROOT=str(checkout)))
    print(f"C2 ({sys.platform}) exit {proc.returncode}; tail:\n{proc.stdout[-600:]}")
    assert env_file.read_bytes() == before, "start.py rewrote the operator's .env"
    assert proc.returncode == 3, "the run should stop at the unreachable model server"


@pytest.mark.parametrize("dev", [False, True])
@pytest.mark.sqlite_only("start.py's checks read the console's SQLite files under the data root (the archive chain, the stores' presence); the URL form is W3b part 3")
def test_c3_the_printed_command_carries_the_port_the_root_and_reload_only_under_dev(tmp_path, dev, operator_python):
    """The launch the operator is told to run, through `main`: --port and
    --data-root reach it, and --reload is in it only under --dev (base
    printed its own port and --reload always)."""
    checkout = _checkout(tmp_path)
    server, url = _ollama()
    root = tmp_path / "root"
    # A .env, so the run reaches the launch (base refused to go on without one).
    (checkout / ".env").write_text(f"LLM_BACKEND=ollama\nOLLAMA_URL={url}\n", encoding="utf-8")
    try:
        proc = _run(checkout, ["--port", "9001", "--data-root", str(root), *(["--dev"] if dev else [])],
                    answers=NO_TO_EVERYTHING, python=operator_python,
                    env=_child_env(tmp_path, OLLAMA_URL=url,
                                                             NEXUS_ENV_ROOT=str(checkout)))
    finally:
        server.shutdown()
    print(f"C3 dev={dev} ({sys.platform}) exit {proc.returncode}; tail:\n{proc.stdout[-900:]}")
    # The printed command found by what it is, not by its wording, so the
    # same assertion reads base's line ("To start manually: uvicorn ...").
    line = next((l for l in proc.stdout.splitlines() if "backend.server:create_app" in l), "")
    assert line, f"no printed command:\n{proc.stdout[-900:]}"
    assert "--port 9001" in line, line
    assert ("--reload" in line) is dev, line
    assert f"NEXUS_DATA_ROOT={root}" in proc.stdout, "the data root was not carried"
    assert proc.returncode == 0


def test_c4_no_input_at_all_is_a_no_at_the_first_flow_prompt(tmp_path, operator_python):
    """EOF (a pipe, a container) at a prompt that defaults to Yes is a no,
    said so, and nothing goes further (W1's verifier, F8: a confirm() that
    returned its default on EOF passed every test)."""
    checkout = _checkout(tmp_path)
    server, url = _ollama()
    try:
        proc = _run(checkout, [], answers=None, python=operator_python,
                    env=_child_env(tmp_path, OLLAMA_URL=url, NEXUS_ENV_ROOT=str(checkout)),
                    timeout=60)
    finally:
        server.shutdown()
    print(f"C4 ({sys.platform}) exit {proc.returncode}; output tail:\n{proc.stdout[-400:]}")
    # Part 5g, C1, asserted first: no answer to a step the launch needs is
    # exit 1, said beside the word (a918e25 exited 0, the code of a launch,
    # and so did the pre-W1 script; both are quoted in part 5g's evidence).
    assert proc.returncode == 1, (
        f"EOF at the first prompt exited {proc.returncode}, not 1:\n{proc.stdout[-400:]}")
    assert "(no answer: taken as no)" in proc.stdout, proc.stdout[-400:]
    assert "Aborted." in proc.stdout
    assert "exit 1" in proc.stdout, proc.stdout[-400:]
    assert "Python Version" not in proc.stdout, "EOF was taken as yes: the run went on"
    assert "Starting..." not in proc.stdout and "backend.server:create_app" not in proc.stdout, (
        "nothing may be handed over or advised after a declined start")


def test_c5_the_interactive_run_survives_a_cp1252_console(tmp_path, operator_python):
    """Windows' pipe encoding (W1's verifier, F9: removing the UTF-8 switch
    killed the run at the banner, and no test noticed)."""
    checkout = _checkout(tmp_path)
    proc = _proc.run([operator_python, str(checkout / "start.py")], cwd=checkout,
                          env=_child_env(tmp_path, NEXUS_ENV_ROOT=str(checkout), PYTHONIOENCODING="cp1252"),
                          input=b"n\n", capture_output=True, timeout=120)
    print(f"C5 ({sys.platform}) exit {proc.returncode}; stderr tail: {proc.stderr[-300:]!r}")
    # "n" to "Begin startup sequence?" declines a step the launch needs: exit
    # 1 (part 5g, C1), and not a crash: a traceback (an encoding error at the
    # banner, F9's shape) also exits 1, so stderr must carry none.
    assert proc.returncode == 1, proc.stderr.decode("utf-8", "replace")[-600:]
    assert b"Traceback" not in proc.stderr, proc.stderr.decode("utf-8", "replace")[-600:]
    text = proc.stdout.decode("utf-8")              # strict: the bytes are UTF-8
    assert "███" in text and "Aborted." in text


def _tampered_archive(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "archive.db"
    shutil.copy2(FIXTURE / "archive.db", archive)
    with sqlite3.connect(archive) as c:
        c.execute("UPDATE archive_blocks SET timestamp = timestamp + 1 "
                  "WHERE idx = (SELECT MIN(idx) FROM archive_blocks)")
    return archive


def _pre_migration_archive(directory: Path) -> Path:
    """A valid archive in the pre-F-16 shape (seven columns): snapshot blocks
    hashed as the product hashed them then (layout 1: W8's blocks hash a
    header instead, and a pre-F-16 archive cannot hold one), written into the
    seven-column table. The product's constructor re-adds the later columns
    with their defaults, which no layout-1 block hash includes, so the product
    reads it as valid."""
    from backend.memory.chain import GENESIS_HASH, ArchiveBlock, NexusArchiveChain
    directory.mkdir(parents=True, exist_ok=True)
    rows, previous = [], GENESIS_HASH
    for i in range(3):
        snap = directory / f"snap{i}.json"
        snap.write_text(json.dumps({"i": i}), encoding="utf-8")
        block = ArchiveBlock(index=i, timestamp=1_700_000_000.0 + i, fingerprint={"signature": [i, i + 1]},
                             snapshot_path=str(snap), snapshot_hash=NexusArchiveChain._hash_file(str(snap)),
                             previous_hash=previous)
        previous = block.compute_hash()
        rows.append((i, block.timestamp, json.dumps(block.fingerprint, sort_keys=True), block.snapshot_path,
                     block.snapshot_hash, block.previous_hash, previous))
    archive = directory / "archive.db"
    with sqlite3.connect(archive) as c:
        c.execute("CREATE TABLE archive_blocks (idx INTEGER PRIMARY KEY, timestamp REAL NOT NULL, "
                  "fingerprint_json TEXT NOT NULL, snapshot_path TEXT NOT NULL, snapshot_hash TEXT NOT NULL, "
                  "previous_hash TEXT NOT NULL, block_hash TEXT NOT NULL UNIQUE)")
        c.executemany("INSERT INTO archive_blocks VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        c.commit()
    with sqlite3.connect(archive) as c:
        assert len(c.execute("PRAGMA table_info(archive_blocks)").fetchall()) == 7
        assert c.execute("SELECT COUNT(*) FROM archive_blocks").fetchone()[0] == 3
    return archive


def _garbage_archive(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "archive.db").write_bytes(b"this is not a database" * 100)
    return directory / "archive.db"


def _foreign_archive(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(directory / "archive.db") as c:
        c.execute("CREATE TABLE notes (body TEXT)")
    return directory / "archive.db"


CHAIN_CASES = {
    "tampered": (_tampered_archive, 5, "DOES NOT VERIFY"),
    "garbage": (_garbage_archive, 5, "cannot be read"),
    "not a NEXUS archive": (_foreign_archive, 5, "is not a NEXUS archive"),
    "pre-migration, valid": (_pre_migration_archive, 0, "valid (3 blocks)"),
}


@pytest.mark.parametrize("case", sorted(CHAIN_CASES))
@pytest.mark.sqlite_only("start.py's checks read the console's SQLite files under the data root (the archive chain, the stores' presence); the URL form is W3b part 3")
def test_c6_check_fails_an_archive_it_cannot_verify_and_passes_an_old_valid_one(tmp_path, case, operator_python):
    """--check's exit is 5 for an archive that does not verify AND for one
    that cannot be verified (W1's verifier, F2: a garbage archive passed);
    a valid archive in an older schema verifies as the product reads it;
    and the archive's bytes never change (F16)."""
    make, code, text = CHAIN_CASES[case]
    root = tmp_path / "root"
    archive = make(root)
    before = archive.read_bytes()
    checkout = _checkout(tmp_path)
    server, url = _ollama()
    try:
        proc = _run(checkout, ["--check", "--data-root", str(root)], answers=None,
                    python=operator_python,
                    env=_child_env(tmp_path, OLLAMA_URL=url, NEXUS_ENV_ROOT=str(checkout)))
    finally:
        server.shutdown()
    print(f"C6 {case} ({sys.platform}) exit {proc.returncode}:\n{proc.stdout}")
    assert proc.returncode == code, proc.stdout + proc.stderr
    assert text in proc.stdout, proc.stdout
    assert archive.read_bytes() == before, "verifying the archive changed it"
    assert sorted(p.name for p in root.iterdir() if p.suffix in (".db", ".db-journal", ".db-wal")) == ["archive.db"]


@pytest.mark.sqlite_only("start.py's checks read the console's SQLite files under the data root (the archive chain, the stores' presence); the URL form is W3b part 3")
def test_c7_an_interactive_run_never_calls_a_broken_chain_complete(tmp_path, operator_python):
    """After a cross, the run asks, defaults to stopping, and never prints
    "All checks complete" (base printed a tick for a broken chain)."""
    root = tmp_path / "root"
    _tampered_archive(root)
    checkout = _checkout(tmp_path)
    server, url = _ollama()
    try:
        proc = _run(checkout, ["--data-root", str(root)], answers=NO_TO_EVERYTHING,
                    python=operator_python,
                    env=_child_env(tmp_path, OLLAMA_URL=url, NEXUS_ENV_ROOT=str(checkout)))
    finally:
        server.shutdown()
    print(f"C7 ({sys.platform}) exit {proc.returncode}; tail:\n{proc.stdout[-700:]}")
    assert "DOES NOT VERIFY" in proc.stdout
    assert "All checks complete" not in proc.stdout
    assert proc.returncode == 5


@pytest.mark.sqlite_only("start.py's checks read the console's SQLite files under the data root (the archive chain, the stores' presence); the URL form is W3b part 3")
def test_c8_a_bad_port_is_exit_2_and_names_it(tmp_path, operator_python):
    checkout = _checkout(tmp_path)
    server, url = _ollama()
    try:
        proc = _run(checkout, ["--check", "--port", "70000", "--data-root", str(tmp_path / "r")],
                    answers=None, python=operator_python,
                    env=_child_env(tmp_path, OLLAMA_URL=url, NEXUS_ENV_ROOT=str(checkout)))
    finally:
        server.shutdown()
    assert proc.returncode == 2 and "--port=70000 is outside 1-65535" in proc.stdout, proc.stdout


def test_c_companion_the_runners_interpreter_fails_the_series_on_its_premise(tmp_path):
    """COMPANION (OQ-16 rules 1 and 3; CI run 20). The interpreter CI's
    runners gave the series, planted: the base interpreter, which is no
    virtual environment's. The consequence: C2 run with it fails, naming its
    premise, before start.py runs.

    Pre-fix (law 16): at cbd50f6 the series ran start.py with pytest's own
    interpreter, so pytest run by a base interpreter reproduces CI's runners.
    Five tests failed, downstream of the premise and not naming it: C2 "the
    run should stop at the unreachable model server" (exit 1, not 3), C3 "no
    printed command" twice, C7 and C7b (RUN_LOG.md, W1)."""
    planted = getattr(sys, "_base_executable", "") or sys.executable
    assert not _is_venv(planted), f"could not plant a base interpreter: {planted}"
    with pytest.raises(AssertionError, match=r"premise: .* is not a virtual environment's interpreter"):
        test_c2_an_existing_env_survives_a_full_interactive_run(tmp_path, planted)


def test_c_companion_a_venv_path_without_its_venv_fails_on_the_premise(tmp_path):
    """COMPANION, a member the premise's source does not name: an
    interpreter at a virtual environment's path whose pyvenv.cfg is gone. It
    runs as the base interpreter (on Windows the launcher cannot start at
    all). The premise asks the interpreter and never reads its path, so C3
    run with it fails naming the premise."""
    import venv
    directory = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False, symlinks=(os.name != "nt"), clear=True).create(directory)
    (directory / "pyvenv.cfg").unlink()
    planted = str(directory / "Scripts" / "python.exe" if os.name == "nt" else directory / "bin" / "python")
    with pytest.raises(AssertionError, match=r"premise: .* is not a virtual environment's interpreter"):
        test_c3_the_printed_command_carries_the_port_the_root_and_reload_only_under_dev(tmp_path, False, planted)


def test_c_companion_a_venv_that_lacks_the_requirements_fails_on_the_premise(tmp_path):
    """COMPANION, step 3's member: a virtual environment's interpreter that
    does not import the requirements (no site directories handed and no
    PYTHONPATH) would make step 3 ask "Install the requirements now?" and
    take one of the series' answers. The series' interpreter is refused,
    naming the premise and the packages."""
    env = {k: v for k, v in _child_env(tmp_path).items() if k != "PYTHONPATH"}
    with pytest.raises(AssertionError, match=r"premise: the series' interpreter cannot be used: .*does not import fastapi"):
        _operator_python(tmp_path / "bare", env, hand_site_dirs=False)


# ---------------------------------------------------------------------------
# The launch composed through main(), in process (W1's verifier, F4: the
# helpers were tested alone, and each could be disconnected at the launch
# with the suite green)
# ---------------------------------------------------------------------------

def _arrange(monkeypatch, tmp_path, environ=None, *, import_problem=None, checks=None) -> list:
    """Every check passing, the hand-over captured, the environment clean.
    Returns the list the captured hand-over appends (argv, env) to. The
    launch target's import (M1) answers `import_problem` (None: it imports)
    and appends the environment it was asked about to `checks`, if given;
    `raising=False`, so the pre-fix start.py (which has no such check) runs
    the same tests and fails on their consequence."""
    handed = []
    monkeypatch.setattr(start, "in_venv", lambda: True)
    monkeypatch.setattr(start, "dependency_problems", lambda: {})

    def import_check(env):
        if checks is not None:
            checks.append(dict(env))
        return import_problem
    monkeypatch.setattr(start, "launch_target_problem", import_check, raising=False)
    monkeypatch.setattr(start, "ensure_env_file", lambda *a, **k: None)
    monkeypatch.setattr(start, "load_env", lambda: [])
    monkeypatch.setattr(start, "_console_utf8", lambda: None)      # C5 covers it
    monkeypatch.setattr(start, "check_model_server", lambda kind, url, timeout=5.0: start.ServerCheck(
        kind, url, True, "", ("deepseek-r1", "phi4-mini", "nomic-embed-text")))

    def capture(argv, env):
        handed.append((argv, env))
        raise SystemExit(0)
    monkeypatch.setattr(start, "_hand_over", capture)
    for key in [k for k in os.environ if k.upper().startswith("UVICORN_")]:
        monkeypatch.delenv(key)
    for key in ("NEXUS_PORT", "NEXUS_HOST", "NEXUS_DATA_ROOT", *LEARNING_KEYS):
        monkeypatch.delenv(key, raising=False)
    # Planted: settings uvicorn would obey, which must not reach the server.
    monkeypatch.setenv("UVICORN_RELOAD", "true")
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    for key, value in (environ or {}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    return handed


def _composed(monkeypatch, tmp_path, argv, environ=None, **arranged) -> tuple[list, list]:
    """main(argv) with every check passing and the hand-over captured.
    Returns ((argv, env) handed over, prompts asked)."""
    handed, asked = _arrange(monkeypatch, tmp_path, environ, **arranged), []

    def confirm(prompt, default=False):
        asked.append(prompt)
        if prompt in ("Begin startup sequence?", "Start NEXUS now?"):
            return True
        raise AssertionError(f"an unscripted prompt: {prompt!r}")
    monkeypatch.setattr(start, "confirm", confirm)
    with pytest.raises(SystemExit) as stop:
        start.main(argv)
    assert stop.value.code == 0
    return handed, asked


def _composed_answering(monkeypatch, tmp_path, argv, answers: dict, environ=None, **arranged):
    """main(argv) with the REAL confirm(): `builtins.input` answers each
    prompt by the flow-prompt text it carries ("" is Enter), so the default
    and the hint are the ones start.py computes (C6). Returns (handed over,
    the raw prompt strings input() received, the exit code: None when main
    returned). `arranged` reaches `_arrange` (the launch-import stub)."""
    handed, prompts = _arrange(monkeypatch, tmp_path, environ, **arranged), []

    def answer(text=""):
        prompts.append(text)
        for key, value in answers.items():
            if key in text:
                return value
        raise AssertionError(f"an unscripted prompt: {text!r}")
    monkeypatch.setattr("builtins.input", answer)
    try:
        start.main(argv)
    except SystemExit as stop:
        return handed, prompts, stop.code
    return handed, prompts, None


def test_f4_main_launches_the_port_and_the_root_it_was_given_without_reload(tmp_path, monkeypatch, capsys):
    root = tmp_path / "root"
    handed, asked = _composed(monkeypatch, tmp_path, ["--port", "9001", "--data-root", str(root)])
    out = capsys.readouterr().out
    assert len(handed) == 1, handed
    argv, env = handed[0]
    print(f"F4 argv {argv}")
    assert argv[0] == sys.executable and argv[argv.index("--port") + 1] == "9001"
    assert "--reload" not in argv
    assert argv[argv.index("--workers") + 1] == "1"
    assert env["NEXUS_DATA_ROOT"] == str(root)
    assert not [k for k in env if k.upper().startswith("UVICORN_") or k in start.UVICORN_ENV_SETTINGS]
    assert f"Data root: {root}" in out, "the report was of another root"
    assert "All checks complete" in out
    assert asked == ["Begin startup sequence?", "Start NEXUS now?"]


def test_f4_main_reloads_only_under_dev(tmp_path, monkeypatch):
    handed, _asked = _composed(monkeypatch, tmp_path, ["--dev"])
    assert handed[0][0][-1] == "--reload"


def test_f4_main_warns_before_serving_beyond_this_machine(tmp_path, monkeypatch, capsys):
    handed, _asked = _composed(monkeypatch, tmp_path, [], environ={"NEXUS_HOST": "0.0.0.0"})
    out = capsys.readouterr().out
    assert "NEXUS_HOST=0.0.0.0" in out and "reachable beyond this machine" in out
    assert handed[0][0][handed[0][0].index("--host") + 1] == "0.0.0.0"


def test_f4_an_empty_host_is_unset(monkeypatch):
    """N5: uvicorn reads an empty host as every interface."""
    monkeypatch.setenv("NEXUS_HOST", "")
    assert start.resolve_bind(8000) == ("127.0.0.1", 8000)


# ---------------------------------------------------------------------------
# F3 — the argv is uvicorn's whole configuration
# ---------------------------------------------------------------------------

def test_f3_the_argv_fixes_what_uvicorn_would_read_from_its_environment():
    argv = start.launch_argv("127.0.0.1", 8000, dev=False)
    assert argv[argv.index("--workers") + 1] == "1"
    assert argv[argv.index("--forwarded-allow-ips") + 1] == "127.0.0.1,::1"
    # NEXUS_LEARNING given, so the frozen default (M3) adds nothing here.
    env = start.launch_env({"WEB_CONCURRENCY": "2", "FORWARDED_ALLOW_IPS": "*", "PATH": "/bin",
                            "NEXUS_LEARNING": "live"})
    assert env == {"PATH": "/bin", "NEXUS_LEARNING": "live"}


def environment_reads(source: str) -> set[str]:
    """Every environment variable a module reads by a literal name:
    os.environ[...], os.environ.get(...), os.getenv(...), "X" in os.environ."""
    names = set()
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Subscript) and ast.unparse(n.value) == "os.environ" \
                and isinstance(n.slice, ast.Constant):
            names.add(n.slice.value)
        elif isinstance(n, ast.Call) and ast.unparse(n.func) in ("os.environ.get", "os.getenv") \
                and n.args and isinstance(n.args[0], ast.Constant):
            names.add(n.args[0].value)
        elif isinstance(n, ast.Compare) and any(isinstance(op, ast.In) for op in n.ops) \
                and isinstance(n.left, ast.Constant) and any(ast.unparse(c) == "os.environ" for c in n.comparators):
            names.add(n.left.value)
    return names


def test_f3_every_setting_the_installed_uvicorn_reads_is_answered_for():
    """Derived from the installed uvicorn, so a version that reads a new
    variable fails here until start.py fixes it in the argv (UVICORN_* are
    dropped wholesale; click reads them by prefix, not by name)."""
    import uvicorn
    package = Path(uvicorn.__file__).parent
    reads = set().union(*(environment_reads(p.read_text(encoding="utf-8")) for p in package.rglob("*.py")))
    print(f"F3 uvicorn {uvicorn.__version__} reads: {sorted(reads)}")
    assert reads, "non-vacuity: the derivation found no read"
    assert reads <= set(start.UVICORN_ENV_SETTINGS), sorted(reads - set(start.UVICORN_ENV_SETTINGS))


def test_f3_companion_a_new_read_is_found():
    planted = "import os\nworkers = os.environ.get('UVICORN_NEW_KNOB')\nif 'TIMEOUT_KEEP_ALIVE' in os.environ:\n    pass\n"
    assert environment_reads(planted) == {"UVICORN_NEW_KNOB", "TIMEOUT_KEEP_ALIVE"}


# ---------------------------------------------------------------------------
# F13 — the check probes the server the server will call
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("environ", [
    {}, {"OLLAMA_URL": ""}, {"OLLAMA_URL": "http://gpu-box:11434"}, {"LLM_BACKEND": "lmstudio"},
    {"LLM_BACKEND": "LMSTUDIO"}, {"LLM_BACKEND": "lmstudio "},
    {"LLM_BACKEND": "lmstudio", "LM_STUDIO_URL": "http://studio:1234"}, {"LLM_BACKEND": "something"},
    {"LLM_BACKEND": "stub"}, {"LLM_BACKEND": " Stub "},
    {"LLM_BACKEND": "openai", "OPENAI_MODEL": "m"},
    {"LLM_BACKEND": "openai", "OPENAI_MODEL": "m", "OPENAI_BASE_URL": "http://gateway.invalid/v1/"},
    {"LLM_BACKEND": "anthropic", "ANTHROPIC_MODEL": "m", "ANTHROPIC_BASE_URL": "http://api.invalid"},
    {"LLM_BACKEND": "openai"}, {"LLM_BACKEND": "anthropic", "ANTHROPIC_MODEL": "m", "NEXUS_EMBED_URL": "http://e"},
])
def test_f13_the_check_resolves_the_target_as_the_providers_do(monkeypatch, environ):
    """The check and the factory read LLM_BACKEND the same way. W3: one
    reader for both (`backend/providers/backends.py`), so `lmstudio ` with a
    trailing space is LM Studio for both (it was Ollama for the factory and
    a warning here), an unknown value is refused by both (the factory's
    `UnknownBackend`, the check's `BackendError`), `stub` has no model
    server to check, and (part 2) an api backend's endpoint is the one its
    settings name, refused by both when its model is unset or the embedder
    is half named."""
    import backend.providers as providers
    from backend.providers.factory import get_provider
    for key in ("LLM_BACKEND", "OLLAMA_URL", "LM_STUDIO_URL", "OPENAI_MODEL", "OPENAI_BASE_URL", "ANTHROPIC_MODEL",
                "ANTHROPIC_BASE_URL", "NEXUS_EMBED_URL", "NEXUS_EMBED_MODEL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    try:
        provider = get_provider()
    except getattr(providers, "BackendConfigError", getattr(providers, "UnknownBackend", ())):
        # The factory refused: so must the check, with its own error.
        with pytest.raises(getattr(start, "BackendError", ValueError)):
            start.model_server_target()
        return
    kind = {"LMStudioProvider": "lmstudio", "StubProvider": "stub", "OpenAICompatProvider": "openai",
            "AnthropicProvider": "anthropic"}.get(type(provider).__name__, "ollama")
    expected = (kind, "") if kind == "stub" else (kind, provider.BASE_URL)
    assert start.model_server_target() == expected, environ


# ---------------------------------------------------------------------------
# F17 and N6 — the smaller declared paths
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="exec keeps the process id on POSIX only; Windows waits for a child")
def test_f17_on_posix_the_hand_over_keeps_the_process_id(tmp_path):
    driver = tmp_path / "driver.py"
    driver.write_text(textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(ROOT)!r})
        import start
        print("driver pid", os.getpid())
        start._hand_over([sys.executable, "-c", "import os; print('child pid', os.getpid())"],
                         dict(os.environ))
    """), encoding="utf-8")
    proc = _proc.run([sys.executable, str(driver)], cwd=tmp_path, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)
    pids = dict(line.rsplit(" ", 1) for line in proc.stdout.splitlines() if " pid " in line)
    print(f"F17 {pids}")
    assert pids["driver pid"] == pids["child pid"], "the hand-over did not exec"


def test_n6_check_exits_6_on_an_old_python(monkeypatch, tmp_path):
    monkeypatch.setattr(start, "check_python_version", lambda: (False, "Python 3.8.10"))
    monkeypatch.setattr(start, "check_model_server", lambda kind, url, timeout=5.0: start.ServerCheck(kind, url, True))
    monkeypatch.setattr(start, "load_env", lambda: [])
    args = start.parse_args(["--check", "--data-root", str(tmp_path / "r")])
    assert start.run_check(args) == 6


def test_n6_the_re_exec_is_bounded_by_its_marker(monkeypatch):
    monkeypatch.setattr(start, "in_venv", lambda: False)
    monkeypatch.setenv(start.REEXEC_MARKER, "1")
    monkeypatch.setattr(start, "_hand_over", lambda *a: pytest.fail("re-launched again"))
    start.step_venv()                                   # returns: no second re-exec


def test_n6_a_writers_own_setting_present_but_empty_still_wins(tmp_path, monkeypatch):
    """As before W1: an own variable that is set, even empty, is the path."""
    from backend.eval import calibration_log
    monkeypatch.setenv("NEXUS_DATA_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", "")
    assert calibration_log.log_path() == Path("")


def test_n6_a_host_that_does_not_resolve_is_named():
    result = start.check_model_server("ollama", "http://nexus-start-check.invalid:11434", timeout=5.0)
    print(f"N6 unresolvable: {result.reason}")
    assert not result.reachable and "does not resolve" in result.reason


def test_n6_lm_studio_answering_in_another_shape_is_named():
    server, url = _serve(200, json.dumps({"models": []}).encode())
    try:
        result = start.check_model_server("lmstudio", url)
    finally:
        server.shutdown()
    assert not result.reachable and "not as LM Studio does" in result.reason


def test_n2_check_says_it_writes_no_data_not_nothing():
    """N2: Python may write its bytecode cache; the claim is about data."""
    assert "writes no data" in START.read_text(encoding="utf-8")
    assert "launches nothing, writes nothing" not in START.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# F18 — MCP's stores and its three logs under its own root (W2)
# ---------------------------------------------------------------------------

@pytest.mark.sqlite_only("start.py's checks read the console's SQLite files under the data root (the archive chain, the stores' presence); the URL form is W3b part 3")
def test_f18_mcp_puts_its_stores_and_its_logs_under_its_own_root(tmp_path, monkeypatch):
    """W1 pinned the state then declared (docs/CONTRACTS.md's NEXUS_MCP_DATA_ROOT
    line): the five stores under NEXUS_MCP_DATA_ROOT, the three logs under
    the console's NEXUS_DATA_ROOT when it is set, else the working
    directory's data/. W2 moved the logs (run/specs/W2.md, G1): with the
    console's root set as well, all eight files are under MCP's own."""
    from backend.eval import calibration_log, citation_capture, fire_capture
    from backend.mcp.mcp_server import NexusRuntime
    mcp_root, console_root = tmp_path / "mcp", tmp_path / "console"
    for name in WRITER_SETTINGS:
        # Set, then removed, through monkeypatch: the runtime sets each one
        # that is absent, and a variable that was absent before delenv would
        # keep the runtime's value after this test.
        monkeypatch.setenv(name, "set-by-the-test")
        monkeypatch.delenv(name)
    monkeypatch.setenv("NEXUS_MCP_DATA_ROOT", str(mcp_root))
    monkeypatch.setenv("NEXUS_DATA_ROOT", str(console_root))
    # Both set through monkeypatch: the runtime's learning default writes
    # straight into os.environ when they are absent, and that would outlive
    # this test.
    monkeypatch.setenv("NEXUS_LEARNING", "frozen")
    monkeypatch.setenv("NEXUS_LEARNING_FROZEN_SINCE", "2026-11-01")
    svc = NexusRuntime.open().services
    stores = {"kb.db": Path(svc.store.db_path), "archive.db": Path(svc.archive_chain.db_path),
              "turn_metrics.jsonl": Path(svc.evaluator.log_path)}
    print(f"F18 stores {sorted(str(p) for p in stores.values())}")
    logs = {"calibration_log.jsonl": calibration_log.log_path(),
            "citation_capture.jsonl": citation_capture.capture_path(),
            "fire_capture.jsonl": fire_capture.capture_path()}
    print(f"F18 logs {sorted(str(p) for p in logs.values())}")
    for name, path in {**stores, **logs}.items():
        assert path == mcp_root / name, (name, path)
    svc.store_lock.release()


def test_f12_a_package_that_is_present_but_fails_to_import_is_named(tmp_path, monkeypatch):
    """F12: `find_spec` said "importable" of a package whose import raises (a
    DLL that will not load on Windows). The check imports each one."""
    shadow = tmp_path / "shadow"
    (shadow / "networkx").mkdir(parents=True)
    (shadow / "networkx" / "__init__.py").write_text(
        'raise ImportError("DLL load failed while importing _core")\n', encoding="utf-8")
    monkeypatch.syspath_prepend(str(shadow))
    monkeypatch.delitem(sys.modules, "networkx", raising=False)
    for name in [m for m in sys.modules if m.startswith("networkx.")]:
        monkeypatch.delitem(sys.modules, name)
    problems = start.dependency_problems()
    print(f"F12 {problems}")
    assert "networkx" in problems and "DLL load failed" in problems["networkx"]
    assert set(problems) == {"networkx"}, "only the shadowed package fails"


@pytest.mark.parametrize("version,good", [((3, 10, 14), False), ((3, 11, 0), True), ((3, 13, 1), True),
                                          ((3, 8, 10), False), ((4, 0, 0), False)])
def test_n6_the_python_floor_is_3_11(version, good):
    import collections
    V = collections.namedtuple("V", "major minor micro releaselevel serial")
    assert start.check_python_version(V(*version, "final", 0))[0] is good


@pytest.mark.sqlite_only("start.py's checks read the console's SQLite files under the data root (the archive chain, the stores' presence); the URL form is W3b part 3")
def test_c7b_continuing_past_a_failure_is_said_at_the_end(tmp_path, operator_python):
    """The operator may continue past a cross; the end of the checks then
    names what failed instead of calling them complete."""
    root = tmp_path / "root"
    _tampered_archive(root)
    checkout = _checkout(tmp_path)
    server, url = _ollama()
    (checkout / ".env").write_text(f"LLM_BACKEND=ollama\nOLLAMA_URL={url}\n", encoding="utf-8")
    try:
        # begin: yes; the chain's "Continue anyway?": yes; "Start NEXUS now?": no
        proc = _run(checkout, ["--data-root", str(root)], answers="y\ny\nn\n", python=operator_python,
                    env=_child_env(tmp_path, OLLAMA_URL=url, NEXUS_ENV_ROOT=str(checkout)))
    finally:
        server.shutdown()
    print(f"C7b ({sys.platform}) exit {proc.returncode}; tail:\n{proc.stdout[-500:]}")
    assert "Checks complete; continuing past what failed: archive chain" in proc.stdout
    assert "All checks complete" not in proc.stdout
    # Part 5g, C1: declining the launch after a check failed is declining a
    # step the launch needs — nothing passed, nothing launched — so exit 1,
    # with the failed check named beside the code (a918e25 exited 0, the code
    # of a launch).
    assert proc.returncode == 1, proc.stdout[-500:]
    assert "exit 1" in proc.stdout and "archive chain" in proc.stdout.rsplit("exit 1", 1)[1], (
        proc.stdout[-500:])
    assert "backend.server:create_app" in proc.stdout, "the command to start it was not printed"


# ===========================================================================
# W1 part 5g — the completeness critic's C1, C2, C6 and C10
# ===========================================================================

# ---------------------------------------------------------------------------
# C1 — the exit codes are the docstring's table. The C-series above drives
# EOF at the first prompt (C4: 1), "n" at it (C5: 1), a declined launch after
# a failed check (C7b: 1) and a declined launch with every check passed (C3:
# 0) through the script; these drive the two prompts a pipe cannot reach the
# same way, in process.
# ---------------------------------------------------------------------------

def test_c1_ctrl_c_at_a_prompt_exits_130(monkeypatch, capsys):
    """Ctrl+C at any confirm() is 128 + SIGINT, the shell's convention, so a
    wrapper can tell an interruption from a refusal (a918e25 exited 0, the
    code of a launch)."""
    def interrupted(*_a):
        raise KeyboardInterrupt
    monkeypatch.setattr("builtins.input", interrupted)
    with pytest.raises(SystemExit) as stop:
        start.confirm("Continue anyway?", default=False)
    out = capsys.readouterr().out
    assert stop.value.code == 130, (stop.value.code, out)
    assert "Aborted." in out and "exit 130" in out, out


def test_c1_declining_to_begin_exits_1_in_process(tmp_path, monkeypatch, capsys):
    handed, prompts, code = _composed_answering(monkeypatch, tmp_path, [],
                                                {"Begin startup sequence?": "n"})
    out = capsys.readouterr().out
    assert code == 1 and handed == [], (code, out)
    assert "Aborted." in out and "exit 1" in out, out
    assert len(prompts) == 1 and "Python Version" not in out, "the run went on"


def test_c1_no_answer_to_the_backend_choice_exits_1_and_creates_nothing(tmp_path, monkeypatch, capsys):
    """The backend choice is a raw input() (G2's one answered-for site), so
    EOF there is handled apart from confirm(): the choice is a step creating
    .env needs, and no answer to it is exit 1 with nothing written (a918e25
    exited 0 there)."""
    answers = iter(["y"])

    def answer(*_a):
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None
    monkeypatch.setattr("builtins.input", answer)
    with pytest.raises(SystemExit) as stop:
        start.ensure_env_file(tmp_path / ".env", ROOT / ".env.example")
    out = capsys.readouterr().out
    assert stop.value.code == 1, (stop.value.code, out)
    assert "Aborted." in out and "exit 1" in out, out
    assert not (tmp_path / ".env").exists(), "a .env was created with no backend chosen"


def test_c1_ctrl_c_at_the_backend_choice_exits_130(tmp_path, monkeypatch, capsys):
    answers = iter(["y"])

    def answer(*_a):
        try:
            return next(answers)
        except StopIteration:
            raise KeyboardInterrupt from None
    monkeypatch.setattr("builtins.input", answer)
    with pytest.raises(SystemExit) as stop:
        start.ensure_env_file(tmp_path / ".env", ROOT / ".env.example")
    assert stop.value.code == 130 and not (tmp_path / ".env").exists()
    assert "exit 130" in capsys.readouterr().out


def test_c1_the_exit_constants_are_the_documented_table():
    """The docstring is the contract; the constants are what the code
    raises. Pinned as literals, never through start.py's own names (W1's
    verifier, F1); `getattr` so a start.py without the new code fails on
    the table, not on the missing name."""
    names = ("EXIT_OK", "EXIT_DECLINED", "EXIT_USAGE", "EXIT_MODEL_SERVER",
             "EXIT_DEPENDENCIES", "EXIT_CHAIN", "EXIT_PYTHON", "EXIT_INTERRUPTED")
    assert tuple(getattr(start, n, None) for n in names) == (0, 1, 2, 3, 4, 5, 6, 130)
    doc = start.__doc__
    for phrase in ("Exit codes: 0 launched", "; 1 the operator declined, or gave no answer",
                   "; 2 a usage error", "; 3", "; 4 a dependency", "; 5 the",
                   "; 6 Python older than", "; 130 interrupted"):
        assert phrase in doc, f"the docstring's table no longer says {phrase!r}"


# ---------------------------------------------------------------------------
# C2 — law 12 on the new-instance path: the .env start.py creates freezes
# learning from the day it is created, and the server reads it so.
# ---------------------------------------------------------------------------

LEARNING_KEYS = ("NEXUS_LEARNING", "NEXUS_LEARNING_FROZEN_SINCE")


def _utc_today() -> str:
    """The test's own reading of the UTC date (not start.py's helper, so a
    start.py without it fails on the file it writes, not on a name)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _create_env(tmp_path, monkeypatch, choice="1") -> tuple[Path, str, str]:
    """ensure_env_file answered y then `choice` into tmp_path, on the real
    clock. Returns (the file, the UTC date before, the UTC date after): the
    written date must be one of the two, so a run across midnight cannot
    flake (law 10)."""
    answers = iter(["y", choice])
    monkeypatch.setattr("builtins.input", lambda *_a: next(answers))
    before = _utc_today()
    created = start.ensure_env_file(tmp_path / ".env", ROOT / ".env.example")
    after = _utc_today()
    assert created == tmp_path / ".env"
    return created, before, after


def test_c2_law12_the_created_env_freezes_learning_from_today(tmp_path, monkeypatch, capsys):
    """RUN_CHARTER law 12: a new instance is frozen. The console's code
    default stays live (FZ-2); the .env start.py creates is the new-instance
    path W1 built, and it says frozen, dated the day it was made (a918e25's
    created file had no NEXUS_LEARNING line at all)."""
    from dotenv import dotenv_values
    created, before, after = _create_env(tmp_path, monkeypatch)
    out = capsys.readouterr().out
    values = dotenv_values(created)
    assert values.get("NEXUS_LEARNING") == "frozen", values
    since = values.get("NEXUS_LEARNING_FROZEN_SINCE")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", since or ""), since
    assert since in (before, after), (since, before, after)
    # Said on screen: the state, the date, the law, and how to switch it on.
    assert f"frozen since {since}" in out and "law 12" in out, out
    assert "NEXUS_LEARNING=live" in out, "the way to switch learning on is not printed"


@pytest.fixture
def environ_restored():
    """The whole of os.environ, put back as it was. The loader below writes
    every key of the created .env that is absent from the environment
    (`load_dotenv(..., override=False)`), and `monkeypatch.delenv(raising=False)`
    on an absent key records nothing to restore, so without this the
    created file's NEXUS_LEARNING=frozen, NEXUS_HOST, OLLAMA_URL and the rest
    would outlive the test (law 10: order-dependence is a defect)."""
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


def test_c2_law12_the_servers_loader_reads_the_created_env_as_frozen(environ_restored, tmp_path, monkeypatch):
    """The created file, loaded exactly as the server loads .env
    (`backend/env.py`'s loader) and read by `backend.learning`'s
    declaration: frozen since that date. In process; the child below is the
    same claim through production's import."""
    from backend.env import load_env_files
    from backend.learning import learning_declaration
    created, before, after = _create_env(tmp_path, monkeypatch)
    for key in LEARNING_KEYS:              # only the file may supply them
        monkeypatch.delenv(key, raising=False)
    assert load_env_files([created]) == [created]
    declared = learning_declaration()
    print(f"C2 in-process declaration: {declared}")
    assert declared["state"] == "frozen", declared
    assert declared["since"] in (before, after) and declared["display"] == f"frozen since {declared['since']}"


def test_c2_the_in_process_load_leaves_the_environment_as_it_found_it(tmp_path, monkeypatch):
    """COMPANION for the fixture above (law 10): the same load without it
    would leave the created file's keys in os.environ; with it, a later test
    finds the environment it had before. Run as the fixture runs, inside a
    guarded frame, so this test itself leaks nothing."""
    from backend.env import load_env_files
    snapshot = dict(os.environ)
    created, _b, _a = _create_env(tmp_path, monkeypatch)
    try:
        for key in LEARNING_KEYS:
            monkeypatch.delenv(key, raising=False)
        load_env_files([created])
        leaked = sorted(k for k in os.environ if k not in snapshot)
        assert "NEXUS_LEARNING" in leaked, (
            "the load no longer writes the environment, so the fixture guards nothing")
    finally:
        os.environ.clear()
        os.environ.update(snapshot)
    assert dict(os.environ) == snapshot


def test_c2_law12_the_created_env_reaches_a_fresh_server_process(tmp_path, monkeypatch):
    """A fresh interpreter importing `backend.server` (which loads .env as
    production does) with NEXUS_ENV_ROOT at the created file: the learning
    layer reads frozen, dated. The environment is copied and the two keys
    pruned, so only the file can supply them."""
    created, before, after = _create_env(tmp_path, monkeypatch)
    env = {k: v for k, v in os.environ.items() if k not in LEARNING_KEYS}
    env["NEXUS_ENV_ROOT"] = str(tmp_path)
    code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import backend.server; "
            "from backend.learning import learning_declaration; "
            "print(json.dumps(learning_declaration()))")
    proc = _proc.run([sys.executable, "-c", code, str(ROOT)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=240)
    assert proc.returncode == 0, proc.stderr[-1500:]
    declared = json.loads(proc.stdout.strip().splitlines()[-1])
    print(f"C2 child declaration: {declared}")
    assert declared["state"] == "frozen" and declared["since"] in (before, after), declared


def test_c2_the_example_itself_freezes_a_new_instance():
    """`.env.example` is what the created file is a copy of, and what
    QUICKSTART tells the operator to copy by hand: it names frozen, an empty
    since-date for start.py to fill, the law, and the literal that switches
    learning on."""
    from dotenv import dotenv_values
    example = ROOT / ".env.example"
    values = dotenv_values(example)
    assert values.get("NEXUS_LEARNING") == "frozen", values.get("NEXUS_LEARNING")
    assert values.get("NEXUS_LEARNING_FROZEN_SINCE") == "", values.get("NEXUS_LEARNING_FROZEN_SINCE")
    text = example.read_text(encoding="utf-8")
    assert "law 12" in text and "NEXUS_LEARNING=live" in text, "the block does not say why, or how to switch"


@pytest.mark.parametrize("case", ["no learning lines", "frozen with no since line"])
def test_c2_an_example_that_does_not_freeze_and_date_is_said_not_assumed(tmp_path, monkeypatch, capsys, case):
    """COMPANION (law 5: no false blank): given an example with no learning
    lines, the created .env carries none and start.py says the console will
    run live; given one that freezes but has no since line to fill, it says
    NEXUS will read "frozen since: undeclared". Never the dated frozen
    message with nothing behind it."""
    example = tmp_path / "bare.example"
    body = "LLM_BACKEND=ollama\nNEXUS_API_TOKEN=\n"
    if case == "frozen with no since line":
        body += "NEXUS_LEARNING=frozen\n"
    example.write_text(body, encoding="utf-8")
    answers = iter(["y", "1"])
    monkeypatch.setattr("builtins.input", lambda *_a: next(answers))
    created = start.ensure_env_file(tmp_path / ".env", example)
    out = capsys.readouterr().out
    assert created is not None
    made = created.read_text(encoding="utf-8")
    assert "NEXUS_LEARNING_FROZEN_SINCE" not in made, "a since line was invented"
    assert not re.search(r"frozen since \d", out), out
    if case == "no learning lines":
        assert "NEXUS_LEARNING" not in made
        assert "learning live" in out, out
    else:
        assert "NEXUS_LEARNING=frozen" in made
        assert "frozen since: undeclared" in out and "NEXUS_LEARNING=live" in out, out


# ---------------------------------------------------------------------------
# C6 — "Start NEXUS now?" defaults to No on every bind (part 5g made it No
# beyond loopback; W1's close, M2, made it No on loopback too)
# ---------------------------------------------------------------------------

def test_c6_beyond_loopback_the_launch_defaults_to_no_and_enter_does_not_start_it(tmp_path, monkeypatch, capsys):
    """With NEXUS_HOST=0.0.0.0 the server would be reachable beyond this
    machine, so the hint reads [y/N] and an empty answer hands nothing over
    (a918e25: [Y/n], and Enter launched). Every check passed and only the
    launch was declined, so the command is printed and the exit is 0."""
    handed, prompts, code = _composed_answering(
        monkeypatch, tmp_path, [], {"Begin startup sequence?": "y", "Start NEXUS now?": ""},
        environ={"NEXUS_HOST": "0.0.0.0"})
    out = capsys.readouterr().out
    launch = next(p for p in prompts if "Start NEXUS now?" in p)
    print(f"C6 off-loopback prompt: {launch!r}; handed {len(handed)}; exit {code}")
    assert "[y/N]" in launch and "[Y/n]" not in launch, launch
    assert handed == [], "an empty answer started a server reachable beyond this machine"
    assert "reachable beyond this machine" in out, out
    assert "To start it yourself:" in out and "--host 0.0.0.0" in out, out
    # main() returns, and the process exits 0 (C3 pins that through the
    # script): every check passed and only the launch was declined.
    assert code is None, code
    assert "Not started. (exit 0" in out, out


def test_c6_on_loopback_enter_does_not_launch_either(tmp_path, monkeypatch, capsys):
    """M2 (the third verifier's critic, C2): on the default bind too the
    hint reads [y/N] and Enter hands nothing over, because the launch's first
    act writes the store. Pre-fix (law 16), at 1a1a521 the hint read [Y/n]
    and Enter launched (this test's predecessor asserted exactly that)."""
    handed, prompts, code = _composed_answering(
        monkeypatch, tmp_path, ["--port", "9001"],
        {"Begin startup sequence?": "y", "Start NEXUS now?": ""})
    out = capsys.readouterr().out
    launch = next(p for p in prompts if "Start NEXUS now?" in p)
    print(f"M2 loopback prompt: {launch!r}; handed {len(handed)}; exit {code}")
    assert "[y/N]" in launch and "[Y/n]" not in launch, launch
    assert handed == [], "Enter started a server that writes the store"
    assert code is None and "Not started. (exit 0" in out, (code, out[-400:])
    assert "To start it yourself:" in out and "--port 9001" in out, out


def test_c6_on_loopback_y_launches(tmp_path, monkeypatch, capsys):
    """The answer that does launch: "y", on the bind and port given."""
    handed, prompts, code = _composed_answering(
        monkeypatch, tmp_path, ["--port", "9001"],
        {"Begin startup sequence?": "y", "Start NEXUS now?": "y"})
    assert len(handed) == 1 and code == 0, (handed, code)
    argv = handed[0][0]
    assert argv[argv.index("--host") + 1] == "127.0.0.1" and argv[argv.index("--port") + 1] == "9001"


def test_c6_a_non_loopback_host_the_bind_accepts_also_defaults_to_no(tmp_path, monkeypatch, capsys):
    """COMPANION on the bind side: a LAN address, not the literal 0.0.0.0
    the warning text names, takes the same default."""
    handed, prompts, _code = _composed_answering(
        monkeypatch, tmp_path, [], {"Begin startup sequence?": "y", "Start NEXUS now?": ""},
        environ={"NEXUS_HOST": "192.168.1.50"})
    assert "[y/N]" in next(p for p in prompts if "Start NEXUS now?" in p)
    assert handed == []


# ---------------------------------------------------------------------------
# C10 — the seed tip carries the header the gated routes need
# ---------------------------------------------------------------------------

def test_c10_the_seed_tip_shows_the_bearer_header(tmp_path, monkeypatch, capsys):
    """Both seed routes are token-gated (tests/test_auth_coverage.py's
    writer set), so a request copied from the tip without the header gets a
    401 (the critic's C10; a918e25 printed none)."""
    _composed(monkeypatch, tmp_path, [])
    out = capsys.readouterr().out
    tips = [line for line in out.splitlines() if "/kb/seed/" in line]
    print(f"C10 tips: {tips}")
    assert len(tips) == 2, tips
    for line in tips:
        assert "Authorization: Bearer" in line, line
        assert "\"confirmed\":true" in line, line


# ===========================================================================
# W1's close — the third verifier's must-fix M1 to M5 (run/specs/W1.md S2b).
# Each test's pre-fix run (law 16) is against 1a1a521's start.py and
# QUICKSTART.md, quoted in the closing commit.
# ===========================================================================

# ---------------------------------------------------------------------------
# M1 — --check and the launch pass only a console that imports (the critic's
# C1 and lens 2's F3; law 5: no false pass)
# ---------------------------------------------------------------------------

# requirement -> import name where it is not the requirement with "-" read as
# "_": the test's own table, so a start.py that maps one wrongly fails here
# rather than agreeing with itself.
REQUIREMENT_MODULES = {"python-dotenv": "dotenv", "llama-index-core": "llama_index.core",
                       # W2: the bundle's build tool
                       "pyinstaller": "PyInstaller", "pyinstaller-hooks-contrib": "_pyinstaller_hooks_contrib"}


def _requirement_names(text: str) -> list[str]:
    """The requirement names of a requirements file, read by the test: each
    line that is not blank or a comment, up to the first character a name
    cannot hold."""
    names = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line).group(0))
    return names


def test_m1_the_dependency_check_imports_every_line_of_requirements_txt(monkeypatch):
    """Every requirement line is imported, not a hand list. Pre-fix (law 16),
    at 1a1a521 the check imported 8 of the 14 (PACKAGES), so a broken
    langgraph passed --check and the launch then failed (the critic's C1)."""
    names = _requirement_names((ROOT / "requirements.txt").read_text(encoding="utf-8"))
    assert len(names) >= 10, f"non-vacuity: {names}"
    expected = {REQUIREMENT_MODULES.get(n, n.replace("-", "_")) for n in names}
    real, imported = start.importlib.import_module, []

    def recording(name, package=None):
        imported.append(name)
        return real(name, package)
    monkeypatch.setattr(start.importlib, "import_module", recording)
    problems = start.dependency_problems()
    print(f"M1 requirements {names}\nM1 imported {imported}\nM1 problems {problems}")
    assert not problems, problems
    assert expected <= set(imported), f"never imported: {sorted(expected - set(imported))}"


def test_m1_companion_a_requirement_no_list_named_is_named_when_it_does_not_import(tmp_path, monkeypatch):
    """COMPANION (OQ-16 rules 1 and 3): a requirements file with a line that
    neither the old hand list nor start.py's source names, and that does not
    import. The check names it, and only it."""
    planted = tmp_path / "requirements.txt"
    planted.write_text((ROOT / "requirements.txt").read_text(encoding="utf-8")
                       + "\nnexus-w1-not-installed>=1.0  # planted\n", encoding="utf-8")
    monkeypatch.setattr(start, "REQUIREMENTS", planted, raising=False)
    problems = start.dependency_problems()
    print(f"M1c {problems}")
    assert set(problems) == {"nexus-w1-not-installed"}, problems
    assert "ModuleNotFoundError" in problems["nexus-w1-not-installed"], problems


def _broken_package(tmp_path: Path, package: str, message: str) -> str:
    """A directory holding a package of that name whose import raises; first
    on PYTHONPATH it shadows the installed one (installed, and broken)."""
    shadow = tmp_path / "shadow"
    (shadow / package).mkdir(parents=True)
    (shadow / package / "__init__.py").write_text(f"raise ImportError({message!r})\n", encoding="utf-8")
    return str(shadow)


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _line(stdout: str, label: str) -> str:
    """The first line that begins with `label` once its colour codes and its
    leading marks (the arrow, the tick) are removed; "" when none does."""
    for line in stdout.splitlines():
        text = re.sub(r"^[^A-Za-z]*", "", ANSI.sub("", line))
        if text.startswith(label):
            return text
    return ""


def test_m1_check_exits_4_on_a_requirement_the_old_list_never_imported(tmp_path, operator_python):
    """The critic's C1 through the script: langgraph present and broken.
    --check names it on the packages line and exits 4. Pre-fix (law 16), at
    1a1a521: "result: every check passed (exit 0)"."""
    checkout = _checkout(tmp_path)
    server, url = _ollama()
    env = _child_env(tmp_path, OLLAMA_URL=url, NEXUS_ENV_ROOT=str(checkout))
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [_broken_package(tmp_path, "langgraph", "planted: langgraph is broken"),
                      env.get("PYTHONPATH")]))
    try:
        proc = _run(checkout, ["--check", "--data-root", str(tmp_path / "root")], answers=None,
                    python=operator_python, env=env)
    finally:
        server.shutdown()
    print(f"M1 broken requirement ({sys.platform}) exit {proc.returncode}:\n{proc.stdout}")
    assert proc.returncode == 4, proc.stdout + proc.stderr[-800:]
    packages = _line(proc.stdout, "packages")
    assert "langgraph" in packages and "planted: langgraph is broken" in packages, packages
    assert "every check passed" not in proc.stdout


def test_m1_check_exits_4_when_the_launch_target_does_not_import(tmp_path, operator_python):
    """Every requirement imports and the server does not (planted: the
    checkout's backend/server.py raises at import). --check imports the
    launch target in a child with the launch's environment, names the error
    and exits 4. Pre-fix (law 16), at 1a1a521: exit 0, "every check passed"."""
    checkout = _checkout(tmp_path)
    server_py = checkout / "backend" / "server.py"
    server_py.write_text(server_py.read_text(encoding="utf-8")
                         + '\nraise RuntimeError("planted: the launch target is broken")\n',
                         encoding="utf-8")
    server, url = _ollama()
    try:
        proc = _run(checkout, ["--check", "--data-root", str(tmp_path / "root")], answers=None,
                    python=operator_python,
                    env=_child_env(tmp_path, OLLAMA_URL=url, NEXUS_ENV_ROOT=str(checkout)))
    finally:
        server.shutdown()
    print(f"M1 broken target ({sys.platform}) exit {proc.returncode}:\n{proc.stdout}")
    assert proc.returncode == 4, proc.stdout + proc.stderr[-800:]
    launch = _line(proc.stdout, "launch")
    assert "does NOT import" in launch and "planted: the launch target is broken" in launch, launch
    assert "every check passed" not in proc.stdout


@pytest.mark.parametrize("pythonpath", [False, True], ids=["no-path-to-the-console", "console-on-PYTHONPATH"])
def test_m1_check_away_from_the_repo_root_checks_the_import_where_the_launch_runs(tmp_path, operator_python, pythonpath):
    """W1's delta check (F1; law 5): the launch hands over in the directory
    start.py runs in, and uvicorn imports the console from there (its
    --app-dir defaults to it), but the check's child ran in the repo root,
    so --check run from elsewhere printed "backend.server imports" and
    "every check passed" for a launch that then failed. Now the child imports
    where and as the launch does. Away from the root with no path to the
    console it fails by name, exit 4, and says to run start.py from the repo
    root; with the console on PYTHONPATH (where the launch works too) it
    passes, so the check fails no launch that would work. Pre-fix (law 16),
    at 336ada6, the first case: exit 0, "launch      backend.server imports"."""
    checkout = _checkout(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    server, url = _ollama()
    env = _child_env(tmp_path, OLLAMA_URL=url, NEXUS_ENV_ROOT=str(checkout))
    if pythonpath:
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(checkout), env.get("PYTHONPATH")]))
    try:
        proc = _run(checkout, ["--check", "--data-root", str(tmp_path / "root")], answers=None,
                    python=operator_python, env=env, cwd=elsewhere)
    finally:
        server.shutdown()
    launch = _line(proc.stdout, "launch")
    print(f"F1 pythonpath={pythonpath} ({sys.platform}) exit {proc.returncode}; {launch!r}")
    if pythonpath:
        assert proc.returncode == 0 and "backend.server imports" in launch, proc.stdout + proc.stderr[-600:]
    else:
        assert proc.returncode == 4, proc.stdout + proc.stderr[-600:]
        assert "does NOT import" in launch and "No module named 'backend'" in launch, launch
        assert "run start.py from the repo root" in launch, launch
        assert "every check passed" not in proc.stdout


def test_m1_the_launch_hands_over_nothing_when_the_target_does_not_import(tmp_path, monkeypatch, capsys):
    """The same check at the launch, before its prompt: exit 4, nothing
    handed over, the error named, the launch not offered. Pre-fix (law 16), at
    1a1a521: the launch was offered and handed over (start.py had no check),
    and the server's import failed after the hand-over."""
    handed, prompts, code = _composed_answering(
        monkeypatch, tmp_path, [], {"Begin startup sequence?": "y", "Start NEXUS now?": "y"},
        import_problem="ImportError: planted")
    out = capsys.readouterr().out
    print(f"M1 launch: exit {code}, handed {len(handed)}, prompts {prompts}")
    assert code == 4 and handed == [], (code, handed)
    assert "backend.server does not import" in out and "ImportError: planted" in out, out[-600:]
    assert not any("Start NEXUS now?" in p for p in prompts), "the launch was offered anyway"


def test_m1_the_import_is_checked_with_the_environment_the_launch_hands_over(tmp_path, monkeypatch):
    """What the check imports under is what the server gets: the same dict.
    Pre-fix (law 16), at 1a1a521: no check was asked about anything."""
    checks = []
    handed, _asked = _composed(monkeypatch, tmp_path, ["--data-root", str(tmp_path / "root")],
                               checks=checks)
    assert len(checks) == 1, checks
    assert checks[0] == handed[0][1], "the import was checked under another environment"


# ---------------------------------------------------------------------------
# M2 — Enter starts nothing (the critic's C2 and lens 2's N4; law 6; Ruling 3
# withdrawn). G2 holds the defaults by AST; these are the consequence.
# ---------------------------------------------------------------------------

def test_m2_enter_at_the_first_prompt_begins_nothing(tmp_path, monkeypatch, capsys):
    """Enter at "Begin startup sequence?" is No: exit 1, nothing checked or
    handed over, the hint [y/N]. Pre-fix (law 16), at 1a1a521 the hint read
    [Y/n], Enter began, and Enter at the launch handed over: exit 0."""
    handed, prompts, code = _composed_answering(
        monkeypatch, tmp_path, [], {"Begin startup sequence?": "", "Start NEXUS now?": ""})
    out = capsys.readouterr().out
    print(f"M2 first prompt: {prompts}; exit {code}")
    assert code == 1 and handed == [], (code, handed)
    assert len(prompts) == 1 and "[y/N]" in prompts[0] and "[Y/n]" not in prompts[0], prompts
    assert "Python Version" not in out, "Enter began the checks"
    assert "Enter is No at every prompt" in out, "the run does not say what Enter means"


# ---------------------------------------------------------------------------
# M3 — a launch that nothing sets learning for is frozen (lens 2's F2; law 12)
# ---------------------------------------------------------------------------

def test_m3_a_launch_nothing_sets_learning_for_is_frozen_and_says_so(tmp_path, monkeypatch, capsys):
    """Enter at the .env offer (its default) leaves no .env, and the
    console's code default is live (FZ-2), so the launch started a new
    instance learning and said nothing. Now the server is handed
    NEXUS_LEARNING=frozen when nothing sets it, and the run says so and how
    to switch. Pre-fix (law 16), at 1a1a521: no NEXUS_LEARNING in the handed
    environment and no Learning line."""
    handed, _asked = _composed(monkeypatch, tmp_path, [])
    out = capsys.readouterr().out
    env = handed[0][1]
    line = _line(out, "Learning:")
    print(f"M3 handed NEXUS_LEARNING={env.get('NEXUS_LEARNING')!r}; {line!r}")
    assert env.get("NEXUS_LEARNING") == "frozen", env.get("NEXUS_LEARNING")
    assert "frozen" in line and "law 12" in line and "NEXUS_LEARNING=live" in line, line


@pytest.mark.parametrize("given", ["live", "frozen"])
def test_m3_the_operators_own_learning_setting_is_handed_over_unchanged(tmp_path, monkeypatch, capsys, given):
    handed, _asked = _composed(monkeypatch, tmp_path, [], environ={"NEXUS_LEARNING": given})
    out = capsys.readouterr().out
    assert handed[0][1]["NEXUS_LEARNING"] == given
    assert _line(out, "Learning:").startswith(f"Learning: {given} "), _line(out, "Learning:")


@pytest.mark.parametrize("environ,expected", [
    ({}, "frozen"), ({"NEXUS_LEARNING": ""}, "frozen"), ({"NEXUS_LEARNING": "  "}, "frozen"),
    ({"NEXUS_LEARNING": "live"}, "live"), ({"NEXUS_LEARNING": "frozen"}, "frozen"),
])
def test_m3_launch_env_freezes_only_when_nothing_sets_learning(environ, expected):
    assert start.launch_env({"PATH": "/bin", **environ}).get("NEXUS_LEARNING") == expected


def test_m3_the_advice_after_a_declined_launch_carries_the_frozen_setting(tmp_path, monkeypatch, capsys):
    """Declined, the operator is told to start it themselves: the command
    alone would start it live, so the advice names the setting."""
    _handed, _prompts, _code = _composed_answering(
        monkeypatch, tmp_path, [], {"Begin startup sequence?": "y", "Start NEXUS now?": "n"})
    after = capsys.readouterr().out.split("To start it yourself:", 1)[-1]
    assert "NEXUS_LEARNING=frozen" in after, after[:600]


@pytest.mark.sqlite_only("start.py's checks read the console's SQLite files under the data root (the archive chain, the stores' presence); the URL form is W3b part 3")
def test_m3_through_the_script_enter_at_the_env_offer_launches_frozen(tmp_path, operator_python):
    """Lens 2's F2 as it was found: no .env, Enter at "Create .env ...?", y
    at the start, n at the launch. The run says the launch is frozen, and the
    advice carries the setting. Pre-fix (law 16), at 1a1a521: no Learning
    line, and the advice was the bare command."""
    checkout = _checkout(tmp_path)
    server, url = _ollama()
    env = {k: v for k, v in _child_env(tmp_path, OLLAMA_URL=url, NEXUS_ENV_ROOT=str(checkout)).items()
           if k not in LEARNING_KEYS}
    try:
        proc = _run(checkout, ["--data-root", str(tmp_path / "root")], answers="y\n\n" + "n\n" * 10,
                    python=operator_python, env=env)
    finally:
        server.shutdown()
    print(f"M3 script ({sys.platform}) exit {proc.returncode}; tail:\n{proc.stdout[-900:]}")
    assert not (checkout / ".env").exists(), "Enter at the offer created a .env"
    line = _line(proc.stdout, "Learning:")
    assert "frozen" in line and "law 12" in line, line or proc.stdout[-900:]
    assert "NEXUS_LEARNING=frozen" in proc.stdout.split("To start it yourself:", 1)[-1]
    assert proc.returncode == 0, proc.stdout[-500:]


# ---------------------------------------------------------------------------
# M4 — the database is named on the first line (the critic's C10; law 15)
# ---------------------------------------------------------------------------

@pytest.mark.sqlite_only("start.py's checks read the console's SQLite files under the data root (the archive chain, the stores' presence); the URL form is W3b part 3")
def test_m4_check_names_the_database_on_its_first_line(tmp_path, operator_python):
    """The root from .env only (NEXUS_DATA_ROOT there; not in the
    environment, no --data-root), so the line names the archive the checks
    then read. Pre-fix (law 16), at 1a1a521 the first line was "NEXUS
    start.py --check (launches nothing, writes no data)", and .env was read
    after the packages line."""
    root = tmp_path / "root"
    _pre_migration_archive(root)
    checkout = _checkout(tmp_path)
    server, url = _ollama()
    (checkout / ".env").write_text(f"LLM_BACKEND=ollama\nOLLAMA_URL={url}\nNEXUS_DATA_ROOT={root}\n",
                                   encoding="utf-8")
    try:
        proc = _run(checkout, ["--check"], answers=None, python=operator_python,
                    env=_child_env(tmp_path, NEXUS_ENV_ROOT=str(checkout)))
    finally:
        server.shutdown()
    first = proc.stdout.splitlines()[0] if proc.stdout else ""
    print(f"M4 --check ({sys.platform}) exit {proc.returncode}; first line {first!r}")
    assert first == f"database: {root / 'archive.db'} (read only: the archive the checks verify; present)", first
    assert proc.returncode == 0, proc.stdout + proc.stderr[-600:]
    assert "valid (3 blocks)" in proc.stdout, "the line named one archive and the check read another"


def test_m4_the_interactive_run_names_the_database_before_anything_else(tmp_path, monkeypatch, capsys):
    """Pre-fix (law 16), at 1a1a521 the first line was the banner."""
    root = tmp_path / "root"
    _handed, _prompts, code = _composed_answering(monkeypatch, tmp_path, ["--data-root", str(root)],
                                                 {"Begin startup sequence?": "n"})
    first = next((l for l in capsys.readouterr().out.splitlines() if l.strip()), "")
    print(f"M4 interactive first line {first!r}")
    assert first == f"database: {root / 'archive.db'} (read only: the archive the checks verify; not present yet)", first
    assert code == 1


# ---------------------------------------------------------------------------
# M5 — QUICKSTART's manual launch is start.py's argv (lens 2's F1)
# ---------------------------------------------------------------------------

def test_m5_quickstarts_manual_launch_is_start_pys_argv():
    """Without --workers 1, a WEB_CONCURRENCY=2 in the operator's shell
    starts two writers on one store; start.py's argv fixes it and removes the
    variable, QUICKSTART's command did neither. Pre-fix (law 16), at 1a1a521:
    the command ended at --port 8000."""
    text = (ROOT / "docs" / "FIRST_RUN.md").read_text(encoding="utf-8")         # W11c: QUICKSTART.md merged into it
    commands = [l for l in text.splitlines() if l.startswith("python -m uvicorn ")]
    assert len(commands) == 1, commands
    expected = ["python", *start.launch_argv("127.0.0.1", 8000, dev=False)[1:]]
    print(f"M5 {commands[0]!r}")
    assert shlex.split(commands[0]) == expected, (shlex.split(commands[0]), expected)
    assert "WEB_CONCURRENCY" in text and "NEXUS_LEARNING=frozen" in text
