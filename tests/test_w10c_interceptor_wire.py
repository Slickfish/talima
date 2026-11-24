"""W10c (run/specs/W10c.md S4, S7) — the interceptor over real sockets, and its command line end to end.

`tests/test_w10c_interceptor.py` drives the application in process, where a response is handed over when it is
whole. Three things are properties of the wire and are checked here, on loopback, with the stub upstream and the
interceptor each served by a real server:

    W1  a stream is relayed event by event: the harness holds the first events while the upstream has not sent the
        rest; the bytes it ends up with are the upstream's.
    W2  a compressed stream whose record cannot be written is cut: the harness's read fails, it does not end.
    W3  `python -m backend.intercept` as a person runs it: it serves; a second process on the same store is refused
        (exit 7, the holder named); stopped, its store verifies, and its record, its action and its exchange are
        verified by `talima-verify` from an empty folder with the instance's identity.
    W4  `scripts/intercept_smoke.py`, the operator's check against a real provider, run here against the stub: one
        request through the interceptor, one more exchange recorded, the key printed nowhere; with no key or no
        interceptor it sends nothing and says so (exit 2).
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from backend.intercept import app as iapp
from backend.intercept import config as iconfig
from backend.intercept import protocols
from backend.intercept.recorder import EXCHANGE_KIND, Recorder
from backend.intercept.stub_upstream import StubUpstream
from backend.store_lock import EXIT_STORE_LOCKED
from tests import _proc

REPO = Path(__file__).resolve().parent.parent
ANTHROPIC = "/v1/messages"
TOOLS = [{"name": "get_weather", "description": "the weather", "input_schema": {"type": "object"}}]
KEY = "sk-not-a-real-key-W10c-wire-91ab"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Served:
    """An ASGI application on a loopback port, in a thread, until the block ends."""

    def __init__(self, app):
        self.port = _free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="critical",
                                                    proxy_headers=False, server_header=False, date_header=False))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "Served":
        self.thread.start()
        for _ in range(400):
            if self.server.started:
                return self
            time.sleep(0.025)
        raise AssertionError("the test server never came up")

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=20)


class Gate:
    """A gate a test opens from its own thread; the stub waits on it between two events."""

    def __init__(self):
        self.open = threading.Event()

    async def wait(self) -> None:
        while not self.open.is_set():
            await asyncio.sleep(0.01)


def _ask(*, stream: bool = True, tools: bool = False, messages=None) -> dict:
    doc = {"model": "claude-test", "max_tokens": 64, "stream": stream,
           "messages": messages or [{"role": "user", "content": "What is the weather in Paris?"}]}
    if tools:
        doc["tools"] = TOOLS
    return doc


def _interceptor(root: Path, upstream: str):
    config = iconfig.load({"NEXUS_INTERCEPT_UPSTREAM": upstream, "NEXUS_INTERCEPT_DATA_ROOT": str(root),
                           "NEXUS_INTERCEPT_OWNER": "tester@example.test"})
    recorder = Recorder.open_at(config.data_root)
    return iapp.build(config, recorder), recorder


def test_w1_a_stream_is_relayed_event_by_event_over_the_wire(tmp_path):
    gate = Gate()
    stub = StubUpstream(gate=gate, gate_after=2)                 # after the ping, the upstream waits
    with Served(stub) as upstream:
        app, _recorder = _interceptor(tmp_path / "root", upstream.url)
        with Served(app) as interceptor, httpx.Client(timeout=30) as harness:
            received = b""
            with harness.stream("POST", interceptor.url + ANTHROPIC, json=_ask(),
                                headers={"x-api-key": KEY}) as response:
                assert response.status_code == 200
                chunks = response.iter_raw()
                while b"event: ping" not in received:
                    received += next(chunks)
                # the harness holds the ping; the upstream has sent nothing after it
                assert not gate.open.is_set() and b"content_block_start" not in received
                assert b"content_block_start" not in bytes(stub.responses[0])
                gate.open.set()
                for chunk in chunks:
                    received += chunk
            health = harness.get(interceptor.url + "/health").json()
    assert received == bytes(stub.responses[0]) and received.endswith(b'data: {"type":"message_stop"}\n\n')
    assert health["recorded"] == 1 and health["exchanges"] == 1 and health["status"] == "ok"
    assert KEY.encode() not in json.dumps(health).encode()


def test_w2_a_compressed_stream_whose_record_cannot_be_written_is_cut_on_the_wire(tmp_path):
    stub = StubUpstream(gzip=True)
    with Served(stub) as upstream:
        app, recorder = _interceptor(tmp_path / "root", upstream.url)
        with Served(app) as interceptor, httpx.Client(timeout=30) as harness:
            recorder.lineage.conn.execute("CREATE TRIGGER planted BEFORE INSERT ON lineage_records "
                                          "BEGIN SELECT RAISE(ABORT, 'planted'); END")
            recorder.lineage.conn.commit()
            received = b""
            with pytest.raises(httpx.RemoteProtocolError):
                with harness.stream("POST", interceptor.url + ANTHROPIC, json=_ask(),
                                    headers={"accept-encoding": "gzip"}) as response:
                    for chunk in response.iter_raw():
                        received += chunk
            rows = [dict(r) for r in recorder.lineage.conn.execute(
                "SELECT state, status FROM action_records WHERE kind = ?", (EXCHANGE_KIND,)).fetchall()]
    assert received and received != bytes(stub.responses[0]), "the whole stream reached the harness"
    assert len(received) < len(bytes(stub.responses[0]))
    assert [r["state"] for r in rows] == ["incomplete"] and rows[0]["status"].startswith("response_not_recorded")


def _cli(args: list, env: dict, *, cwd=REPO, timeout: float = 120):
    return _proc.run([sys.executable, "-m", "backend.intercept", *args], cwd=cwd, env=env, capture_output=True,
                     text=True, encoding="utf-8", errors="replace", timeout=timeout)


def test_w3_the_command_line_serves_refuses_a_second_writer_and_its_store_verifies_for_a_stranger(tmp_path):
    root, port = tmp_path / "root", _free_port()
    with Served(StubUpstream()) as upstream:
        env = {k: v for k, v in os.environ.items() if not k.startswith("NEXUS_")}
        env.update({"NEXUS_INTERCEPT_UPSTREAM": upstream.url, "NEXUS_INTERCEPT_DATA_ROOT": str(root),
                    "NEXUS_INTERCEPT_OWNER": "tester@example.test", "NEXUS_ENV_ROOT": str(tmp_path / "no-env"),
                    "PYTHONUNBUFFERED": "1"})
        base = f"http://127.0.0.1:{port}"
        with _proc.Child([sys.executable, "-m", "backend.intercept", "serve", "--port", str(port)], cwd=REPO, env=env,
                         text=True, encoding="utf-8", errors="replace", label="talima-intercept serve") as served:
            with httpx.Client(timeout=30) as harness:
                for _ in range(400):
                    try:
                        if harness.get(base + "/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        time.sleep(0.05)
                else:
                    raise AssertionError("the interceptor never answered /health:\n" + served.error_output()[-2000:])
                first = harness.post(base + ANTHROPIC + "?beta=true", json=_ask(tools=True),
                                     headers={"x-api-key": KEY, "anthropic-version": "2023-06-01"})
                use = protocols.read_response(protocols.ANTHROPIC, first.content, streamed=True).uses[0]
                second = harness.post(base + ANTHROPIC + "?beta=true", headers={"x-api-key": KEY}, json=_ask(
                    tools=True, messages=[
                        {"role": "user", "content": "What is the weather in Paris?"},
                        {"role": "assistant", "content": [{"type": "tool_use", "id": use.tool_use_id,
                                                           "name": use.name, "input": {"city": "Paris"}}]},
                        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": use.tool_use_id,
                                                      "content": "18C and clear"}]}]))
                health = harness.get(base + "/health").json()
                # a second process on the same store: refused by name, exit 7
                refused = _cli(["verify"], env)
            served.interrupt()
            served.wait(timeout=60)
        assert first.status_code == 200 and second.status_code == 200 and b"message_stop" in second.content
        assert health["exchanges"] == 2 and health["recorded"] == 2
        assert refused.returncode == EXIT_STORE_LOCKED and "intercept" in refused.stderr, (refused.returncode,
                                                                                         refused.stderr[-800:])
        # stopped: the store verifies, and what it gives a stranger verifies with nothing but the identity
        verified = _cli(["verify"], env)
        assert verified.returncode == 0, verified.stdout[-1500:] + verified.stderr[-800:]
        verdict = json.loads(verified.stdout)
        assert verdict["ok"] and verdict["actions"]["failed"] == [] and verdict["actions"]["checked"] == 3
        listing = [json.loads(line) for line in _cli(["list"], env).stdout.splitlines() if line.startswith("{")]
        assert [e["state"] for e in listing] == ["completed", "completed"] and all(e["recorded"] for e in listing)
        oldest = listing[-1]
        assert [u["status"] for u in oldest["tool_uses"]] == ["reported"], oldest
        stranger = tmp_path / "stranger"
        stranger.mkdir()
        for args in (["identity", "--out", str(stranger / "identity.json")],
                     ["credential", oldest["lineage_id"], "--disclose", "record_scope,model_id", "--out",
                      str(stranger / "credential.json")],
                     ["receipt", oldest["tool_uses"][0]["action_id"], "--out", str(stranger / "receipt.json")],
                     ["receipt", oldest["exchange_action_id"], "--out", str(stranger / "exchange.json")],
                     ["chain", "--out", str(stranger / "chain.json")]):            # W11d: the chain document
            done = _cli(args, env)
            assert done.returncode == 0, (args, done.stdout[-800:], done.stderr[-800:])
        clean = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        for argv, word in ((["credential.json"], 'record_scope = "intercepted"'),
                           (["--action", "receipt.json"], "action: intercepted tool_use"),
                           (["--action", "exchange.json"], "action: runtime model_exchange POST"),
                           (["--chain", "chain.json"], "CHAIN ")):
            done = _proc.run([sys.executable, "-I", str(REPO / "scripts" / "talima_verify.py"), *argv, "--identity",
                              "identity.json"], cwd=stranger, env=clean, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120)
            assert done.returncode == 0 and "VERIFIED" in done.stdout and word in done.stdout, (argv, done.stdout[-1500:])
    # the stopped store, read as a file: two records, both sealed
    conn = sqlite3.connect(root / "kb.db")
    try:
        assert conn.execute("SELECT COUNT(*), COUNT(sealed_block) FROM lineage_records").fetchone() == (2, 2)
    finally:
        conn.close()
    # the key the harness sent is in no file the interceptor wrote, and in nothing it printed
    for p in root.rglob("*"):
        if p.is_file():
            assert KEY.encode() not in p.read_bytes(), f"{p.name} holds the key"
    assert KEY not in served.output() and KEY not in served.error_output()


def test_w4_the_operators_smoke_sends_one_request_and_says_what_was_recorded(tmp_path):
    script = [sys.executable, str(REPO / "scripts" / "intercept_smoke.py")]
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEXUS_") and not k.endswith("_API_KEY")}
    env.update(NEXUS_ENV_ROOT=str(tmp_path / "no-env"), PYTHONIOENCODING="utf-8")

    def run(argv, **more):
        child = {k: v for k, v in os.environ.items() if k in env}       # the caller's environment, pruned as above
        child.update(env)
        child.update(more)
        return _proc.run(script + argv, cwd=REPO, env=child, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=180)
    stub = StubUpstream()
    with Served(stub) as upstream:
        app, _recorder = _interceptor(tmp_path / "root", upstream.url)
        with Served(app) as interceptor:
            no_key = run(["--base-url", interceptor.url])
            done = run(["--base-url", interceptor.url], ANTHROPIC_API_KEY=KEY)
            other = run(["--base-url", interceptor.url, "--protocol", "openai", "--model", "any"], OPENAI_API_KEY=KEY)
            calls = stub.calls
            sent = [dict(r["headers"]) for r in stub.requests]
    nobody = run(["--base-url", f"http://127.0.0.1:{_free_port()}"], ANTHROPIC_API_KEY=KEY)
    assert no_key.returncode == 2 and "ANTHROPIC_API_KEY is not set" in no_key.stdout and "Nothing was sent" in no_key.stdout
    assert nobody.returncode == 2 and "no interceptor answered" in nobody.stdout
    assert done.returncode == 0 and "PASS" in done.stdout and "recorded 1 more exchange(s)" in done.stdout, done.stdout
    assert "stub-model-1" in done.stdout and "Hello from the stub" in done.stdout
    assert other.returncode == 0 and "recorded 1 more exchange(s)" in other.stdout, other.stdout
    assert calls == 2 and sent[0]["x-api-key"] == KEY and sent[1]["authorization"] == f"Bearer {KEY}"
    for out in (no_key, done, other, nobody):
        assert KEY not in out.stdout and KEY not in out.stderr, "the script printed the key"

