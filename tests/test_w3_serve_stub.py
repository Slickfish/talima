"""W3 part 1 — `start.py --serve`, the stub provider, an unknown LLM_BACKEND
refused (run/specs/W3.md S2, D1 and D2; S1's H1 and H2).

V1  `--serve` answers no prompt and serves: with no stdin it runs the
    checks, execs uvicorn (the pid is the server's), learning is frozen
    because nothing set it, no .env is written, and Ctrl+C (SIGINT) stops
    it with exit 0 and the store's lock released.
V2  `--serve` refuses a LLM_BACKEND that names no backend, by name, exit 2,
    launching nothing (before W3 any such value ran Ollama, with a warning
    the interactive path printed and the server never did).
V3  `--serve` SAYS a model server that does not answer and serves anyway
    (never exit 3: a compose profile starts the model server beside NEXUS).
V4  `--serve` refuses a chain that does not verify (5), a package of
    requirements.txt that does not import (4; a planted shadow module), a bad port (2) and
    `--dev` (2), launching nothing.
V5  The interactive default is unchanged: with no stdin, start.py stops at
    its first prompt with exit 1 and launches nothing.
V6  The factory: `stub` selects the stub, the name is read stripped and
    lowered, an unknown value is `UnknownBackend` naming it and every known
    name, and the module-level default is resolved on first read.
V7  The console (uvicorn's factory call) and MCP refuse an unknown
    LLM_BACKEND at startup with one line, exit 2, nothing opened (before
    W3: both served, with Ollama).
V8  The stub's contract: one fixed answer naming itself and the model, the
    same bytes twice; `[]` for its extraction model; Law 5's ceiling
    refuses; no seed; no embedder, declared degraded by the chokepoint; its
    request options recorded, its digest and model-server facts absent with
    their reasons; its roster installed; `wire_body` rebuilds its record's
    body and refuses a seed.
V9  A turn through the app under the stub seals a record whose `model_id`
    is `stub:primary`, frozen, seed and digest absent with reasons,
    extraction zero triples measured (not failed), and a second turn's
    answer is byte for byte the first's.
V10 `MODEL_MAP["stub"]` is the stub's roles, every tag has a profile in its
    roster, and `--check` under the stub says no model server is checked.
V11 Every provider's `health()`, which `/healthz` awaits on every call,
    connects on R.2's short budget (`health_timeout()`: the same connect as
    `list_models()`, the read unchanged), derived over every tracked
    provider module by AST, and the env override reaches it. Found by V3 on
    CI's Windows runner: a connect to a closed port is dropped there, not
    refused, so a flat 2.0 s cost `/healthz` the whole two seconds and a
    two-second health poll never saw an answer (R.2 measured 2.45 s per
    probe on Windows against 2 ms on Linux).

Every child runs from the repo root (start.py's docstring says to; the
launch imports the console from the directory it runs in), with the .env
search confined to tmp_path and every model-server setting removed.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import sqlite3
import subprocess
from tests import _proc
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from tests._tracked import REPO

START = REPO / "start.py"
FIXTURE = REPO / "tests" / "fixtures" / "pre_run_data"
EXIT_USAGE, EXIT_DEPENDENCIES, EXIT_CHAIN = 2, 4, 5      # the documented literals
KNOWN = "ollama, lmstudio, stub, openai, anthropic"          # part 2 added the two api backends
UNSET = ("LLM_BACKEND", "OLLAMA_URL", "LM_STUDIO_URL", "NEXUS_PORT", "NEXUS_HOST", "NEXUS_DATA_ROOT",
         "NEXUS_MCP_DATA_ROOT", "NEXUS_LEARNING", "NEXUS_LEARNING_FROZEN_SINCE")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(tmp_path: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in UNSET}
    env.update(NEXUS_ENV_ROOT=str(tmp_path / "envroot"), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    (tmp_path / "envroot").mkdir(exist_ok=True)
    env.update(extra)
    return env


def _get(port: int, path: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return json.loads(r.read())


def _probe(port: int) -> str | None:
    """None when /healthz answered within two seconds; else what the probe
    saw, with the time it took (the failure's own evidence: a slow health
    check reads as a never-served start otherwise)."""
    started = time.monotonic()
    try:
        _get(port, "/healthz", timeout=2.0)
        return None
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:120]} after {time.monotonic() - started:.2f} s"


def _serving(port: int) -> bool:
    return _probe(port) is None


class _Child:
    """A child process whose output is read until it ends; it is stopped
    with SIGINT on POSIX (uvicorn's Ctrl+C) and killed as a tree on Windows,
    where start.py waits on the server rather than becoming it."""

    def __init__(self, argv: list, cwd: Path, env: dict):
        # W4b: started by tests/_proc.py, which reads the output the whole
        # time (W3b-D13: this helper used to read it only at the end) and
        # bounds every wait.
        self.proc = _proc.Child(argv, label="start.py --serve", cwd=cwd, env=env, stdin=_proc.DEVNULL,
                                stdout=_proc.PIPE, stderr=_proc.STDOUT, text=True, encoding="utf-8",
                                errors="replace")

    def wait_until(self, port: int, timeout: float = 150.0) -> str:
        """'serving' once /healthz answers, 'exited' when the process ends
        first, 'neither' at the timeout; `last_probe` keeps what the last
        health probe saw, for the message."""
        deadline = time.monotonic() + timeout
        self.last_probe = "not probed"
        while time.monotonic() < deadline:
            seen = _probe(port)
            if seen is None:
                return "serving"
            self.last_probe = seen
            if self.proc.poll() is not None:
                return "exited"
            time.sleep(0.25)
        return "neither"

    def stop(self) -> int:
        """SIGINT on POSIX (uvicorn's Ctrl+C), the tree killed on Windows;
        then the exit, within 90 s or the tree is killed and named."""
        if self.proc.poll() is None:
            self.proc.interrupt()
        return self.proc.wait(timeout=90)

    def output(self) -> str:
        """What the child has printed so far (all of it once stopped)."""
        return self.proc.output() or ""


def _serve(tmp_path: Path, root: Path, port: int, *extra_args: str, **env: str) -> _Child:
    return _Child([sys.executable, str(START), "--serve", "--port", str(port), "--data-root", str(root),
                   *extra_args], cwd=REPO, env=_env(tmp_path, **env))


# --- V1 to V5: start.py ------------------------------------------------------

def test_v1_serve_answers_no_prompt_and_serves_frozen_as_the_server_itself(tmp_path):
    root, port = tmp_path / "root", _free_port()
    (tmp_path / "envroot").mkdir()
    before = sorted(p.name for p in tmp_path.iterdir())
    child = _serve(tmp_path, root, port, LLM_BACKEND="stub")
    state = child.wait_until(port)
    try:
        assert state == "serving", (f"--serve did not serve: {state}; the last health probe saw {child.last_probe}\n"
                                    f"{child.output()[-2500:]}")
        health = _get(port, "/healthz")
        learning = _get(port, "/cortex/stats")["learning"]
        if sys.platform.startswith("linux"):
            cmdline = Path(f"/proc/{child.proc.pid}/cmdline").read_text(encoding="utf-8", errors="replace")
            assert "uvicorn" in cmdline, f"the pid is not the server's (exec): {cmdline!r}"
    finally:
        code = child.stop()
    out = child.output()
    print(f"V1 ({sys.platform}) exit {code}; healthz {health.get('ollama')}, learning {learning.get('display')}")
    assert "kb_triples" in health
    assert learning["state"] == "frozen" and learning["display"] == "frozen since: undeclared", learning
    assert "no prompt" in out and "[y/N]" not in out, out[-1500:]
    assert "learning    frozen: nothing sets NEXUS_LEARNING" in out, out[-1500:]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(before + ["root"]), "--serve wrote beside its data root"
    assert not (tmp_path / "envroot" / ".env").exists() and not (root / ".env").exists(), ".env was written"
    if os.name != "nt":
        assert code == 0, f"Ctrl+C did not stop the server cleanly: exit {code}\n{out[-1500:]}"
        lock = json.loads((root / "kb.db.lock").read_text(encoding="utf-8").strip("\0 \n"))
        assert lock["released"] is True, lock


def test_v2_serve_refuses_an_unknown_backend_by_name_and_launches_nothing(tmp_path):
    root, port = tmp_path / "root", _free_port()
    child = _serve(tmp_path, root, port, LLM_BACKEND="nonsense")
    state = child.wait_until(port, timeout=120)
    code = child.stop()
    out = child.output()
    print(f"V2 exit {code}: {[l for l in out.splitlines() if 'model' in l or 'result' in l]}")
    assert state == "exited" and code == EXIT_USAGE, f"state {state}, exit {code}\n{out[-1500:]}"
    assert "LLM_BACKEND='nonsense' is not a backend NEXUS knows" in out and KNOWN in out, out[-1500:]
    assert "nothing was launched" in out and not root.exists()


def test_v3_serve_says_an_unreachable_model_server_and_serves_anyway(tmp_path):
    root, port = tmp_path / "root", _free_port()
    child = _serve(tmp_path, root, port, LLM_BACKEND="ollama", OLLAMA_URL=f"http://127.0.0.1:{_free_port()}")
    state = child.wait_until(port)
    code = child.stop()
    out = child.output()
    print(f"V3 state {state}, exit {code}: {[l for l in out.splitlines() if 'model' in l][:3]}")
    assert state == "serving", (f"--serve refused where it should have said: {state}; the last health probe saw "
                                f"{child.last_probe}\n{out[-1500:]}")
    assert "Ollama NOT REACHABLE" in out and "connection refused" in out and "starts anyway" in out, out[-1500:]


def test_v4_serve_refuses_a_broken_chain_a_bad_port_and_dev(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    import shutil
    shutil.copy2(FIXTURE / "archive.db", root / "archive.db")
    with sqlite3.connect(root / "archive.db") as c:
        c.execute("UPDATE archive_blocks SET timestamp = timestamp + 1 WHERE idx = (SELECT MIN(idx) FROM archive_blocks)")
    # The chain case runs on the tampered archive; the two usage cases on a
    # root of their own, so each refusal is its own check's.
    fresh = tmp_path / "fresh"
    # The dependency case (exit 4, the W3 verifier's F9: declared, never
    # driven): a package of requirements.txt that does not import, planted
    # as a shadow module first on PYTHONPATH.
    shadow = tmp_path / "shadow" / "SPARQLWrapper"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("raise ImportError('planted: this SPARQLWrapper does not import')\n",
                                        encoding="utf-8")
    cases = {"chain": (root, (), EXIT_CHAIN, "archive chain", {}),
             "port": (fresh, ("--port", "70000"), EXIT_USAGE, "outside 1-65535", {}),
             "dev": (fresh, ("--dev",), EXIT_USAGE, "--dev is not allowed with --serve", {}),
             "dependency": (fresh, (), EXIT_DEPENDENCIES, "do not import: SPARQLWrapper (ImportError: planted",
                            {"PYTHONPATH": str(shadow.parent)})}
    for name, (data_root, args, expected, words, extra_env) in cases.items():
        port = _free_port()
        child = _serve(tmp_path, data_root, port, *args, LLM_BACKEND="stub", **extra_env)
        state = child.wait_until(port, timeout=120)
        code = child.stop()
        out = child.output()
        print(f"V4 {name}: state {state}, exit {code}")
        assert state == "exited" and code == expected, (name, state, code, out[-1200:])
        assert words in out and "nothing was launched" in out, (name, out[-1200:])
    assert sorted(p.name for p in root.iterdir()) == ["archive.db"], "a refused --serve wrote in the data root"
    assert not fresh.exists(), "a refused --serve created the data root"


def test_v5_the_interactive_default_still_stops_at_its_first_prompt_without_stdin(tmp_path):
    root = tmp_path / "root"
    proc = _proc.run([sys.executable, str(START), "--data-root", str(root)], cwd=REPO,
                          env=_env(tmp_path, LLM_BACKEND="stub"), stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180)
    assert proc.returncode == 1 and "Begin startup sequence?" in proc.stdout and not root.exists(), proc.stdout[-800:]


# --- V6, V7: the factory and the two startups ------------------------------------

def test_v6_the_factory_selects_the_stub_and_refuses_an_unknown_name(monkeypatch):
    """Asserted on what the factory SELECTS, so the pre-fix factory fails
    here on its consequence (Ollama for every value but `lmstudio`), not on
    a name it lacks."""
    import backend.providers as providers
    from backend.providers.factory import get_provider
    refusal = getattr(providers, "UnknownBackend", None)
    for value, kind in ((" Stub ", "StubProvider"), ("stub", "StubProvider"), ("lmstudio ", "LMStudioProvider"),
                        ("OLLAMA", "OllamaProvider"), ("", "OllamaProvider")):
        monkeypatch.setenv("LLM_BACKEND", value)
        assert type(get_provider()).__name__ == kind, (value, type(get_provider()).__name__)
    monkeypatch.delenv("LLM_BACKEND")
    assert type(get_provider()).__name__ == "OllamaProvider"
    for value in ("api", "nonsense", "stub provider"):
        monkeypatch.setenv("LLM_BACKEND", value)
        try:
            selected = get_provider()
        except Exception as refused:
            assert refusal is not None and isinstance(refused, refusal), refused
            assert repr(value) in str(refused) and KNOWN in str(refused), str(refused)
        else:
            pytest.fail(f"LLM_BACKEND={value!r} selected {type(selected).__name__} instead of being refused by name")
    from backend.providers.backends import backend_name, unknown_backend_reason
    assert backend_name({}) == "ollama" and backend_name({"LLM_BACKEND": " LMStudio "}) == "lmstudio"
    assert unknown_backend_reason({"LLM_BACKEND": "stub"}) is None
    assert "'x'" in unknown_backend_reason({"LLM_BACKEND": "x"}) and KNOWN in unknown_backend_reason({"LLM_BACKEND": "x"})


def test_v6_the_module_level_default_is_resolved_on_first_read(monkeypatch):
    """New API (W3): `PROVIDER` is read lazily, cached, and still importable
    by name, so an unknown setting is a refusal at startup rather than a
    traceback from whatever imported the package first."""
    import backend.providers as providers
    from backend.providers import UnknownBackend
    from backend.providers.stub import StubProvider
    monkeypatch.setattr(providers.factory, "_default", None)
    monkeypatch.setenv("LLM_BACKEND", "nonsense")
    with pytest.raises(UnknownBackend):
        providers.PROVIDER
    monkeypatch.setenv("LLM_BACKEND", "stub")
    first = providers.PROVIDER
    assert isinstance(first, StubProvider) and providers.default_provider() is first
    from backend.providers import PROVIDER
    assert PROVIDER is first


def test_v7_the_console_and_mcp_refuse_an_unknown_backend_at_startup(tmp_path):
    # The console, as uvicorn's --factory calls it: one line, exit 2, no store.
    root, port = tmp_path / "console", _free_port()
    child = _Child([sys.executable, "-m", "uvicorn", "backend.server:create_app", "--factory", "--host",
                    "127.0.0.1", "--port", str(port), "--workers", "1"], cwd=REPO,
                   env=_env(tmp_path, LLM_BACKEND="nonsense", NEXUS_DATA_ROOT=str(root)))
    state = child.wait_until(port, timeout=120)
    code = child.stop()
    out = child.output()
    print(f"V7 console: state {state}, exit {code}: {out.strip().splitlines()[-1:] }")
    assert state == "exited", f"the console served under an unknown backend\n{out[-1500:]}"
    assert code == EXIT_USAGE and "NEXUS: LLM_BACKEND='nonsense' is not a backend NEXUS knows" in out, out[-1500:]
    assert "Nothing was opened" in out and "Traceback" not in out and not root.exists()
    # MCP mode, the same (W2's S6 shape: stdin at EOF ends a served session with 0).
    mcp_root = tmp_path / "mcp"
    proc = _proc.run([sys.executable, "-m", "backend.mcp"], cwd=REPO,
                          env=_env(tmp_path, LLM_BACKEND="nonsense", NEXUS_MCP_DATA_ROOT=str(mcp_root)),
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=240)
    print(f"V7 mcp: exit {proc.returncode}: {proc.stderr.strip().splitlines()[-1:]}")
    assert proc.returncode == EXIT_USAGE and "NEXUS MCP: LLM_BACKEND='nonsense' is not a backend" in proc.stderr
    assert "Traceback" not in proc.stderr and not (mcp_root / "kb.db").exists()


# --- V8 to V10: the stub -------------------------------------------------------------

def _collect(gen):
    async def go():
        return [x async for x in gen]
    return asyncio.run(go())


def test_v8_the_stubs_contract(monkeypatch):
    from backend.providers.replay_capture import SEPARATION_KEY, wire_body
    from backend.providers.stub import (EMPTY_EXTRACTION, STUB_EXTRACTION, STUB_PRIMARY, STUB_ROLES, StubProvider,
                                        stub_answer)
    from backend.vector.embeddings import FULL_DIM, EmbeddingUnavailable, embed_via_provider, embedding_status
    p = StubProvider()
    msgs = [{"role": "user", "content": "what does interval training improve?"}]
    first = "".join(_collect(p.stream_chat(STUB_PRIMARY, msgs, num_ctx=8192)))
    second = "".join(_collect(p.stream_chat(STUB_PRIMARY, [{"role": "user", "content": "another question"}], num_ctx=4096)))
    print(f"V8 answer: {first[:90]}")
    assert first == second == stub_answer(STUB_PRIMARY), "the stub's answer is not one fixed text"
    assert "stub provider" in first and STUB_PRIMARY in first and "No language model ran" in first
    assert "".join(_collect(p.stream_chat(STUB_EXTRACTION, msgs, num_ctx=4096))) == EMPTY_EXTRACTION == "[]"
    assert _collect(p.stream_pieces(STUB_PRIMARY, msgs, num_ctx=8192, separate=True)) == [("content", first)]
    with pytest.raises(ValueError, match="Law V"):
        _collect(p.stream_chat(STUB_PRIMARY, msgs, num_ctx=8193))
    assert p.takes_seed is False and p.has_embedder is False
    config = p.request_config(STUB_PRIMARY, 4096, separate=True)
    assert config == {"provider": "stub", "endpoint": None, "model": STUB_PRIMARY, "num_ctx": 4096}
    digest, why = asyncio.run(p.served_model_digest(STUB_PRIMARY))
    assert digest is None and "no model weights" in why
    assert "model_server_absent" in asyncio.run(p.inference_environment())
    assert asyncio.run(p.health()) is True
    assert sorted(asyncio.run(p.list_models())) == sorted(set(STUB_ROLES.values()))
    # No embedder: the chokepoint substitutes its hash vector and declares it.
    before = embedding_status()["fallbacks"]
    vec = asyncio.run(embed_via_provider(p, "cardiac safety"))
    status = embedding_status()
    print(f"V8 embedding: degraded {status['degraded']} ({status['reason']}), fallbacks {before} -> {status['fallbacks']}")
    assert len(vec) == FULL_DIM and status["degraded"] is True and "NotImplementedError" in status["reason"]
    assert status["fallbacks"] == before + 1
    with pytest.raises(EmbeddingUnavailable):
        asyncio.run(embed_via_provider(p, "cardiac safety", strict=True))
    # W0b's wire body: rebuilt from a stub record (no seed), refused with one.
    recorded = {**config, SEPARATION_KEY: True}
    assert wire_body(recorded, msgs, None) == {"model": STUB_PRIMARY, "num_ctx": 4096, "messages": msgs}
    with pytest.raises(ValueError, match="takes no seed"):
        wire_body(recorded, msgs, 7)


def _chat(client, question: str) -> tuple[dict, str]:
    with client.stream("POST", "/chat", json={"message": question}) as r:
        text = "".join(r.iter_text())
    frames = [json.loads(line.split("data: ", 1)[1]) for line in text.splitlines() if line.startswith("data: ")]
    events = [line[len("event: "):] for line in text.splitlines() if line.startswith("event: ")]
    done = frames[events.index("done")]
    answer = "".join(f["t"] for f, e in zip(frames, events) if e == "token")
    return done, answer


def test_v9_a_turn_under_the_stub_seals_a_record_of_the_stub(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.data_root import paths_under
    from backend.providers.stub import STUB_EXTRACTION, STUB_PRIMARY, StubProvider, stub_answer
    from backend.server import create_app, create_services
    monkeypatch.setenv("NEXUS_LEARNING", "frozen")
    monkeypatch.setenv("NEXUS_LEARNING_FROZEN_SINCE", "2026-11-08")
    services = create_services(**paths_under(tmp_path / "root"), provider=StubProvider())
    try:
        assert services.kernel.fast_extractor().name == STUB_EXTRACTION
        assert sorted(p.name for p in services.kernel.registry.all()) == sorted(
            {"stub:primary", "stub:code", "stub:extraction", "stub:multimodal", "stub:embed"})
        client = TestClient(create_app(services))
        done1, answer1 = _chat(client, "what does interval training improve?")
        done2, answer2 = _chat(client, "and what about strength training?")
        records = services.lineage.recent(2)
    finally:
        services.store_lock.release()
    rec = next(r for r in records if r.lineage_id == done1["lineage_id"])
    print(f"V9 model_id {rec.model_id}, learning {rec.learning_mode}, notes {rec.capture_notes}")
    assert rec.model_id == STUB_PRIMARY and rec.learning_mode == "frozen"
    assert rec.inference_seed is None and rec.model_digest is None
    assert rec.capture_notes["inference_seed"] == "provider takes no seed"
    assert "no model weights" in rec.capture_notes["model_digest"]
    assert answer1 == answer2 == stub_answer(STUB_PRIMARY), "two turns, two answers: the stub is not deterministic"
    assert done1["extraction_gate"]["extracted"] == 0 and done1["extraction_gate"]["extraction_failed"] is False
    assert done1["answer_chars"] == len(answer1) and done1["learning"]["state"] == "frozen"
    assert {r.answer_hash for r in records} == {rec.answer_hash}, "the same answer, two hashes"


def test_v10_the_stubs_table_and_roster_agree_and_check_names_no_model_server(tmp_path, monkeypatch):
    from backend.providers.model_map import MODEL_MAP, ROLES, resolve
    from backend.providers.stub import STUB_ROLES, StubProvider
    assert MODEL_MAP["stub"] == STUB_ROLES and set(STUB_ROLES) == set(ROLES)
    roster = StubProvider.model_registry()
    for role, tag in STUB_ROLES.items():
        assert roster.by_name(tag).is_pulled, (role, tag)
    assert resolve("primary", backend="stub") == "stub:primary"
    monkeypatch.setenv("LLM_BACKEND", " STUB ")
    assert resolve("extraction") == "stub:extraction" and resolve("embed") == "stub:embed"
    monkeypatch.delenv("LLM_BACKEND")
    proc = _proc.run([sys.executable, str(START), "--check", "--data-root", str(tmp_path / "root")], cwd=REPO,
                          env=_env(tmp_path, LLM_BACKEND="stub"), stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=240)
    print(f"V10 --check exit {proc.returncode}: {[l for l in proc.stdout.splitlines() if l.startswith('  model')]}")
    assert proc.returncode == 0 and "no model server is checked" in proc.stdout, proc.stdout[-1500:]
    assert not (tmp_path / "root").exists()


# --- V11: every health() connects on the short budget ---------------------------------------

def _health_client_timeouts(path: Path) -> list[str]:
    """What each `health` method's `httpx.AsyncClient(timeout=...)` is given,
    as source text: a number is the flat budget, `health_timeout()` the split."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == "health":
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "AsyncClient"):
                    for kw in call.keywords:
                        if kw.arg == "timeout":
                            found.append(ast.unparse(kw.value))
    return found


def test_v11_every_providers_health_connects_on_the_short_budget(monkeypatch):
    from tests._tracked import tracked
    from backend.providers.base import DEFAULT_HEALTH_TIMEOUT, health_timeout, probe_connect_timeout
    providers = {p: _health_client_timeouts(p) for p in tracked("backend/providers/*.py")}
    with_health = {p.name: t for p, t in providers.items() if t}
    print(f"V11 health clients: {with_health}")
    assert len(with_health) >= 4, "the population is not empty: the four HTTP providers each have a health()"
    flat = {name: t for name, t in with_health.items() if any(x != "health_timeout()" for x in t)}
    assert not flat, f"a health() connects on a flat budget, not health_timeout(): {flat}"
    monkeypatch.delenv("NEXUS_PROBE_CONNECT_TIMEOUT", raising=False)
    t = health_timeout()
    assert t.connect == probe_connect_timeout() and t.read == DEFAULT_HEALTH_TIMEOUT == 2.0, t
    monkeypatch.setenv("NEXUS_PROBE_CONNECT_TIMEOUT", "5.0")
    assert health_timeout().connect == 5.0, "the R.2 override does not reach health()"
