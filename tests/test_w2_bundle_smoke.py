"""W2 part 4 — the bundle smoke (RUN_WORKLIST.md W2's exit; run/specs/W2.md D7).

CI's `bundle` job runs `scripts/bundle_smoke.py` on the binary it builds,
on windows and macos. Here it runs `--source`: the entry PyInstaller builds
from (`backend/mcp/bundle/entry.py`), as a script, in the binary's place,
launched from the tracked manifest the way the host launches the binary.

K1  Every check passes on the source entry (S1 to S8).
K2  S3 refuses every network call, a loopback one included (W2's
    verifier, F1: a process calling a model server or a proxy on this
    machine passed): planted real loopback calls, a lookup of `localhost`,
    a connect to 127.0.0.1:11434, a borrowed `socketpair` name and two calls
    off the machine all fail it by name; the standard library's own
    socketpair, found by the call's stack, is allowed and named.
K3  S3 fails, never passes, when no audit was written (a binary without the
    hook): nothing observed is not nothing done (law 5).
K4  A process that closes its output before answering is named with what
    it left: its exit code (or the signal that killed it) and the tail of
    its stderr, in the EOFError the smoke prints. CI's first macOS bundle
    smoke died 130 ms after launch and the smoke said only that it had
    closed its output.
K5  The standard library's own `ver` query is allowed and named: Python
    3.11 on Windows reads the OS version for `platform.platform()` (which
    the record's environment capture asks, W0b) by running `ver` through a
    shell inside `platform._syscmd_ver`, one `subprocess.Popen` the hook
    sees. Allowed by its stack and its shape; the same spawn from a function
    merely named `_syscmd_ver` under a borrowed module name, and another
    command from the real function's module, are refused. CI's first Windows
    run of K1 refused it; the frozen binary had made the query before the
    hook was installed and passed.
"""
from __future__ import annotations

import json
from tests import _proc
import sys
import textwrap
from pathlib import Path

import pytest

from tests._tracked import REPO

sys.path.insert(0, str(REPO / "scripts"))
import bundle_smoke as smoke  # noqa: E402


def test_k1_every_check_passes_on_the_source_entry(tmp_path):
    proc = _proc.run([sys.executable, str(REPO / "scripts" / "bundle_smoke.py"), "--source",
                           "--work", str(tmp_path / "smoke")], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900)
    lines = [line for line in proc.stdout.splitlines() if line.startswith(("PASS", "FAIL", "bundle smoke"))]
    print("\n".join(lines))
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    passed = {line.split(":")[0][5:] for line in lines if line.startswith("PASS")}
    assert {name.split(" ")[0] for name in passed} >= {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"}, lines


def _audited_child(tmp_path, body: str) -> list[dict]:
    """Run `body` in a child under the entry's audit hook; return its events."""
    audit = tmp_path / "audit.jsonl"
    child = textwrap.dedent(f"""
        import importlib.util, socket, sys
        spec = importlib.util.spec_from_file_location("nexus_entry", {str(REPO / "backend" / "mcp" / "bundle" / "entry.py")!r})
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)
        entry.audit_network({str(audit)!r})
    """) + textwrap.dedent(body)
    proc = _proc.run([sys.executable, "-c", child], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]


def test_k2_network_calls_fail_s3_by_name_loopback_included(tmp_path):
    """W2's verifier, F1: S3 allowed any loopback call, so a process calling a
    model server or a proxy on this machine passed. Planted here: a real
    listener and a real connect on 127.0.0.1, the events a lookup of
    `localhost` and a connect to 127.0.0.1:11434 (Ollama's port) raise, a
    function merely named `socketpair` outside the `socket` module, and two
    calls off the machine (through `sys.audit`, so nothing is sent)."""
    events = _audited_child(tmp_path, r"""
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        client = socket.create_connection(listener.getsockname(), timeout=5)
        client.close(); listener.close()
        sys.audit("socket.getaddrinfo", "localhost", 11434, 0, 0, 0)
        sys.audit("socket.connect", client, ("127.0.0.1", 11434))
        namespace = {"__name__": "socket", "sys": sys, "client": client}
        exec("def socketpair():\n    sys.audit('socket.bind', client, ('127.0.0.1', 0))\n", namespace)
        namespace["socketpair"]()
        sys.audit("socket.getaddrinfo", "example.invalid", 443, 0, 0, 0)
        sys.audit("socket.connect", client, ("192.0.2.1", 9))
    """)
    audit = tmp_path / "audit.jsonl"
    allowed, refused = smoke.network_findings(events)
    ok, saw = smoke.network_verdict(audit)
    print(f"K2 {saw}")
    assert events[0]["event"] == "nexus.audit_started", "the hook says it is watching before any event"
    assert allowed == [], allowed
    targets = [json.dumps(e["args"][1] if e["event"] in ("socket.bind", "socket.connect") else e["args"][0])
               for e in refused]
    print(f"K2 refused {targets}")
    # the listener's bind; create_connection's own lookup and connect; the
    # planted lookup of localhost and connect to 11434; the borrowed name's
    # bind; the two off the machine
    assert [e["event"] for e in refused] == ["socket.bind", "socket.getaddrinfo", "socket.connect",
                                             "socket.getaddrinfo", "socket.connect", "socket.bind",
                                             "socket.getaddrinfo", "socket.connect"], targets
    for needle in ("localhost", "11434", "example.invalid", "192.0.2.1", "127.0.0.1"):
        assert needle in saw or any(needle in t for t in targets), needle
    assert not ok


def _python_socketpair():
    """The standard library's socketpair written in Python, where it exists:
    `_fallback_socketpair` on 3.11.17 and later, `socketpair` itself on
    Windows."""
    import os
    import socket
    if hasattr(socket, "_fallback_socketpair"):
        return "_fallback_socketpair"
    return "socketpair" if os.name == "nt" else None


def test_k2_the_stdlib_socketpair_is_allowed_and_named(tmp_path):
    name = _python_socketpair()
    if name is None:
        import pytest
        pytest.skip("this Python's socketpair is native: it makes no bind or connect to allow")
    events = _audited_child(tmp_path, f"""
        a, b = socket.{name}()
        a.close(); b.close()
    """)
    allowed, refused = smoke.network_findings(events)
    print(f"K2 socketpair {name}: allowed {[e['event'] for e in allowed]}, refused {[e['event'] for e in refused]}")
    assert {e["event"] for e in allowed} >= {"socket.bind", "socket.connect"}, events
    assert refused == [], refused
    ok, saw = smoke.network_verdict(tmp_path / "audit.jsonl")
    assert ok, saw


def test_k4_a_process_that_dies_before_answering_is_named_with_its_exit_and_stderr(tmp_path):
    import os
    import signal
    import time
    env = {**os.environ}
    dying = [sys.executable, "-c", "import sys; sys.stderr.write('planted: Library not loaded: @rpath/Nothing\\n'); sys.exit(3)"]
    server = smoke.Server(dying, env, tmp_path, tmp_path / "stderr-dying.txt")
    try:
        server.request("initialize", {}, timeout=120)
    except EOFError as e:
        said = str(e)
    else:
        raise AssertionError("a process that exited before answering raised nothing")
    finally:
        server.close(timeout=10)
    print(f"K4 {said}")
    assert "before answering initialize" in said and "exit 3" in said and "Library not loaded: @rpath/Nothing" in said, said

    if sys.platform != "win32":
        sleeping = [sys.executable, "-c", "import time; time.sleep(60)"]
        server = smoke.Server(sleeping, env, tmp_path, tmp_path / "stderr-killed.txt")
        try:
            time.sleep(0.5)
            os.kill(server.proc.pid, signal.SIGKILL)
            with pytest.raises(EOFError) as err:
                server.request("initialize", {}, timeout=120)
        finally:
            server.close(timeout=10)
        print(f"K4 killed: {err.value}")
        assert "killed by signal 9" in str(err.value) and "nothing written" in str(err.value), str(err.value)


def test_k5_the_stdlib_ver_query_is_allowed_by_stack_and_shape(tmp_path):
    """The real `platform._syscmd_ver`, run as on Windows (it returns at once
    on any other platform, so the child says it is win32 before calling it):
    its shells are the hook's `ver_query` events, allowed and counted."""
    events = _audited_child(tmp_path, r"""
import platform, subprocess
sys.platform = "win32"
platform._syscmd_ver()
""")
    spawned = [e for e in events if e["event"] == "subprocess.Popen"]
    allowed, refused = smoke.network_findings(events)
    ok, saw = smoke.network_verdict(tmp_path / "audit.jsonl")
    print(f"K5 {saw}")
    assert spawned and all(e.get("ver_query") is True for e in spawned), spawned
    assert refused == [] and ok and len(allowed) == len(spawned), saw
    assert f"{len(spawned)} the standard library's `ver` query" in saw
    # The shape on both shells (the audit's argument is the shell's argv on
    # POSIX and one quoted command line on Windows, as CI's windows run of
    # K1 and K5 recorded it), and two that are not it.
    windows = ["C:\\Windows\\system32\\cmd.exe", 'C:\\Windows\\system32\\cmd.exe /c "ver"', None, None]
    assert smoke.is_ver_command(windows) and smoke.is_ver_command(["/bin/sh", ["/bin/sh", "-c", "cmd /c ver"], None, None])
    assert not smoke.is_ver_command(["/bin/sh", ["/bin/sh", "-c", "verify"], None, None])
    assert not smoke.is_ver_command(["C:\\Windows\\system32\\cmd.exe", 'C:\\Windows\\system32\\cmd.exe /c "ver" && del x', None, None])


def test_k5_the_same_spawn_elsewhere_is_refused(tmp_path):
    """The exemption is the function's own stack and the command's shape:
    `ver` run from a function merely named `_syscmd_ver` under a borrowed
    module name, and a different command run from inside the real
    function's module, are both refused."""
    events = _audited_child(tmp_path, r"""
import platform, subprocess
namespace = {"__name__": "platform", "subprocess": subprocess}
exec("def _syscmd_ver():\n    subprocess.run('ver', shell=True, capture_output=True)\n", namespace)
namespace["_syscmd_ver"]()                      # the borrowed name: the shape without the stack
exec("def _syscmd_ver():\n    __import__('subprocess').run([sys.executable, '-c', 'pass'])\n", platform.__dict__)
platform.__dict__["_syscmd_ver"]()              # the real module's namespace: the stack with another command
""")
    spawned = [e for e in events if e["event"] == "subprocess.Popen"]
    allowed, refused = smoke.network_findings(events)
    print(f"K5 not the query: allowed {len(allowed)}, refused {[(e.get('ver_query'), e['args'][1]) for e in refused]}")
    assert len(spawned) == 2 and allowed == [] and len(refused) == 2, events
    assert [e.get("ver_query") for e in refused] == [False, True], \
        "the borrowed name is not the stack; the real stack with another command is not the shape"


def test_k3_no_audit_file_is_a_failure_never_a_pass(tmp_path):
    ok, saw = smoke.network_verdict(tmp_path / "never-written.jsonl")
    assert not ok and "nothing was observed" in saw
    # A file with events but no start line was not written by the hook.
    stray = tmp_path / "stray.jsonl"
    stray.write_text('{"event": "socket.bind", "args": ["s", ["127.0.0.1", 0]]}\n', encoding="utf-8")
    ok, saw = smoke.network_verdict(stray)
    assert not ok and "nothing was observed" in saw


EMULATED_IPV6 = r"""
real_socket = socket.socket
class EmulatedV6(real_socket):
    # A host with IPv6, as on Windows, macOS and CI's runners (this sandbox
    # has none): an AF_INET6 socket is made as AF_INET, and its bind raises
    # the audit event a real bind raises and binds nothing.
    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        self._v6 = family == socket.AF_INET6
        super().__init__(socket.AF_INET if self._v6 else family, type, proto, fileno)
    def bind(self, address):
        if self._v6:
            sys.audit("socket.bind", self, address)
            return None
        return super().bind(address)
socket.socket = EmulatedV6
socket.has_ipv6 = True
"""


def test_k2_urllib3s_ipv6_probe_is_allowed_by_stack_and_shape(tmp_path):
    """W2's delta check, D1: urllib3 binds ::1 on port 0 when it is imported
    (the MCP process imports it), and S3 refused that on every host with
    IPv6. Here the real probe runs on an emulated IPv6 host: allowed, named."""
    events = _audited_child(tmp_path, EMULATED_IPV6 + r"""
import urllib3.util.connection as connection
assert connection.HAS_IPV6, "the emulation did not reach urllib3's probe"
""")
    allowed, refused = smoke.network_findings(events)
    ok, saw = smoke.network_verdict(tmp_path / "audit.jsonl")
    print(f"K2 ipv6 probe: {saw}")
    assert [(e["event"], e["args"][1]) for e in allowed] == [("socket.bind", ["::1", 0])], events
    assert refused == [] and ok, saw


def test_k2_the_same_bind_elsewhere_is_refused(tmp_path):
    """The exemption is the probe's stack and its shape, never the address:
    a bind to ::1 on port 0 from any other frame, from a function merely
    named `_has_ipv6` under a borrowed module name, or to another port
    inside the real probe's module, is refused."""
    events = _audited_child(tmp_path, r"""
sock = socket.socket()
sys.audit("socket.bind", sock, ("::1", 0))
namespace = {"__name__": "urllib3.util.connection", "sys": sys, "sock": sock}
exec("def _has_ipv6():\n    sys.audit('socket.bind', sock, ('::1', 0))\n", namespace)
namespace["_has_ipv6"]()
""" + EMULATED_IPV6 + r"""
import urllib3.util.connection as connection
connection._has_ipv6.__globals__["socket"].socket(socket.AF_INET6).bind(("::1", 8080))
""")
    allowed, refused = smoke.network_findings(events)
    ok, saw = smoke.network_verdict(tmp_path / "audit.jsonl")
    print(f"K2 not the probe: allowed {[e['args'][1] for e in allowed]}, refused {[e['args'][1] for e in refused]}")
    assert [e["args"][1] for e in refused] == [["::1", 0], ["::1", 0], ["::1", 8080]], refused
    assert [e["args"][1] for e in allowed] == [["::1", 0]], "only the real probe's own bind"
    assert not ok


def test_k2_a_call_with_no_python_frame_below_it_is_recorded_and_refused(tmp_path):
    """W2's delta check, D2: the hook read the caller's frame with a call
    that raises when there is none (a thread started on a C function), and
    dropped the record: S3 then passed a connect it never saw."""
    events = _audited_child(tmp_path, r"""
import _thread, time
_thread.start_new_thread(sys.audit, ("socket.connect", None, ("192.0.2.1", 9)))
time.sleep(1.0)
""")
    allowed, refused = smoke.network_findings(events)
    ok, saw = smoke.network_verdict(tmp_path / "audit.jsonl")
    print(f"K2 C thread: {saw}")
    assert [(e["event"], e["args"][1]) for e in refused] == [("socket.connect", ["192.0.2.1", 9])], events
    assert not ok and "192.0.2.1" in saw
