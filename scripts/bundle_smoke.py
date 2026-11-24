#!/usr/bin/env python3
"""W2 — the bundle smoke: RUN_WORKLIST.md W2's exit, run/specs/W2.md D7.

    python scripts/bundle_smoke.py dist/nexus-<version>-<platform>.mcpb
    python scripts/bundle_smoke.py --source      the source entry in the binary's place

It drives the binary the bundle carries, launched the way the host launches
it (`build_bundle.host_launch`: the platform's override, `${__dirname}` and
`${user_config.*}` substituted), with a planted home, a fresh data folder,
and a working directory of its own holding a `.env` that says learning is
live and names another owner. The desktop app's install itself cannot be
driven (the worklist's ring-fence); this is the same binary it would start.
Each check prints PASS or FAIL with what it saw:

  S1  the process starts over stdio and answers `initialize`;
  S2  `tools/list` is the pinned seven, in order;
  S3  the network: every connect, name lookup, send, bind and subprocess
      call the process makes is recorded by the entry's audit hook
      (`NEXUS_AUDIT_NETWORK`), and there is none, a loopback call included.
      A bind or connect inside the standard library's own socketpair (found
      by the call's stack, never by its address), which is how Python on
      Windows makes the event loop's socket pair, is named and allowed, and
      so is urllib3's import-time IPv6 probe (a bind to ::1 on port 0 inside
      its `_has_ipv6`, by stack and shape: M.3's rule), and so is the
      standard library's own `ver` query (a shell running `ver` inside
      `platform._syscmd_ver`, by stack and shape: how Python 3.11 on
      Windows reads the OS version when anything asks `platform` for it).
      The hook sees Python's calls, not a native library's own (the
      establish's strace on Linux saw none);
  S4  `user_config` reaches the process: `nexus_status` reports the
      configured owner and learning frozen, set by the environment;
  S5  the fresh data folder's first record reads `learning_mode = frozen`,
      the configured identity and `record_scope = mcp_block` (kb.db read
      read-only);
  S6  no `.env` was read, and nothing was written in the working directory;
  S7  two processes on one store: the second is refused by name ("this
      store is open in another NEXUS process (pid N"), exit 7, and the first
      still answers;
  S8  a stale lock, its holder killed outright, is taken over by the next
      process, and the takeover is in `kb.db.lock.log`.

Exit 0 when every check passes, 1 when any fails, 2 when it cannot start.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import build_bundle as bb  # noqa: E402
from _console import force_utf8_stdio  # noqa: E402
from _env import load_env  # noqa: E402
from _readonly import open_read_only  # noqa: E402
from _interpreter import this_interpreter  # noqa: E402  (W4b: a source-form child is this interpreter, one process)

PINNED_TOOLS = ["nexus_retrieve", "nexus_record", "nexus_recall", "nexus_asof", "nexus_assert",
                "nexus_assert_confirm", "nexus_commit", "nexus_record_action", "nexus_status"]   # W10b: + record_action
OWNER = "smoke-tester@example.org"
EXIT_STORE_LOCKED = 7          # the documented literal (backend/store_lock.py)
PROTOCOL = "2025-11-25"
NETWORK_EVENTS = {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
                  "socket.gethostbyaddr", "socket.getnameinfo", "socket.sendto", "socket.sendmsg", "socket.bind"}
PROCESS_EVENTS = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile"}


class Server:
    """One launched process, spoken to over newline-delimited JSON-RPC."""

    def __init__(self, argv: list, env: dict, cwd: Path, stderr: Path):
        self._stderr_path = Path(stderr)
        self._err = open(stderr, "w", encoding="utf-8")
        # W4b: under a Windows virtual environment `sys.executable` is the
        # venv launcher, and the pid held here would be the launcher's while
        # the store's lock names the interpreter's (S7, S8).
        argv, env = this_interpreter(argv, env)
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err,
                                     env=env, cwd=cwd)
        self._lines: "queue.Queue" = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self._id = 0

    def _read(self) -> None:
        for raw in self.proc.stdout:
            if raw.strip():
                self._lines.put(raw)
        self._lines.put(None)

    def request(self, method: str, params: "dict | None" = None, timeout: float = 120.0) -> dict:
        self._id += 1
        message = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            message["params"] = params
        self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            raw = self._lines.get(timeout=max(0.1, deadline - time.monotonic()))
            if raw is None:
                raise EOFError(f"the process closed its output before answering {method}: {self.death()}")
            reply = json.loads(raw)
            if reply.get("id") == self._id and ("result" in reply or "error" in reply):
                return reply

    def death(self) -> str:
        """What a process that closed its output left behind: its exit code,
        or the signal that killed it, waited for briefly, and the tail of its
        stderr. A smoke that says only "it closed its output" has observed
        the death and reported none of it."""
        try:
            code = self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            code = None
        self._err.flush()
        lines = self._stderr_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        tail = " | ".join(line.strip() for line in lines[-12:]) if lines else "nothing written"
        if code is None:
            fate = "still running with its stdout closed"
        elif code < 0:
            fate = f"killed by signal {-code}"
        else:
            fate = f"exit {code}"
        return f"{fate}; stderr: {tail}"

    def initialize(self) -> dict:
        reply = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                            "clientInfo": {"name": "nexus-bundle-smoke", "version": "1"}})
        self.proc.stdin.write(b'{"jsonrpc": "2.0", "method": "notifications/initialized"}\n')
        self.proc.stdin.flush()
        return reply

    def call(self, name: str, **arguments) -> dict:
        reply = self.request("tools/call", {"name": name, "arguments": arguments})
        result = reply.get("result") or {}
        return result.get("structuredContent") or result

    def close(self, timeout: float = 60.0) -> int:
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            return self.proc.wait(timeout=timeout)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
            self._err.close()

    def kill(self) -> None:
        self.proc.kill()
        self.proc.wait(timeout=60)
        self._err.close()


def unpack(bundle: Path, into: Path) -> Path:
    """The bundle unpacked, modes restored (a zip reader does not)."""
    with zipfile.ZipFile(bundle) as z:
        for info in z.infolist():
            z.extract(info, into)
            mode = (info.external_attr >> 16) & 0o777
            if mode and os.name != "nt":
                os.chmod(into / info.filename, mode)
    return into


def is_ver_command(args: list) -> bool:
    """The shape of the standard library's OS-version query: a shell running
    `ver` (`ver`, `command /c ver` or `cmd /c ver`, the three
    `platform._syscmd_ver` tries), and nothing else. The audit's argument is
    the shell's argv on POSIX (`['/bin/sh', '-c', 'ver']`) and one command
    line on Windows (`C:\\Windows\\system32\\cmd.exe /c "ver"`, the word
    quoted by the shell): the last word, unquoted, must be `ver`."""
    command = args[1] if len(args) > 1 else None
    text = command[-1] if isinstance(command, list) and command else command
    if not isinstance(text, str) or not text.split():
        return False
    return text.split()[-1].strip("\"'") == "ver"


def network_findings(events: list[dict]) -> tuple[list[dict], list[dict]]:
    """(allowed, refused). Allowed: a bind or connect the entry's hook found
    inside the standard library's own socketpair (by the call's stack: how
    Python on Windows makes the event loop's socket pair), urllib3's
    import-time IPv6 probe, by its stack and its shape (a bind to ::1 on
    port 0, on any host with IPv6), and the standard library's own `ver`
    query, by its stack and its shape (a shell running `ver` inside
    `platform._syscmd_ver`: how Python 3.11 on Windows reads the OS version
    for `platform.platform()`, which the record's environment capture asks).
    Refused: every other network or process event, a loopback one included,
    because a call to a service on this machine (a model server, a proxy) is
    still a network call (W2's verifier, F1)."""
    allowed, refused = [], []
    for e in events:
        name, args = e.get("event"), e.get("args") or []
        if name in ("socket.bind", "socket.connect") and e.get("socketpair") is True:
            allowed.append(e)
        elif name == "socket.bind" and e.get("ipv6_probe") is True and len(args) > 1 and args[1] == ["::1", 0]:
            # urllib3's import-time IPv6 probe, by its stack and its shape (a
            # bind to ::1 on port 0, never a connect): M.3's rule, W2's D1.
            allowed.append(e)
        elif name == "subprocess.Popen" and e.get("ver_query") is True and is_ver_command(args):
            allowed.append(e)
        elif name in NETWORK_EVENTS | PROCESS_EVENTS:
            refused.append(e)
    return allowed, refused


def network_verdict(audit: Path) -> tuple[bool, str]:
    """S3's decision on one process's audit file: no file is a failure (the
    hook did not run, so nothing was observed), never a pass."""
    events = ([json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines() if line.strip()]
              if audit.is_file() else [])
    if not any(e.get("event") == "nexus.audit_started" for e in events):
        return False, "no audit start line: the hook did not run, so nothing was observed"
    allowed, refused = network_findings(events)
    def show(e: dict) -> str:
        args = e.get("args") or []
        # A socket call's first argument is the socket itself; its address follows.
        kept = args[1:2] if e["event"] in ("socket.bind", "socket.connect", "socket.sendto", "socket.sendmsg") else args[:2]
        return f"{e['event']} {json.dumps(kept)}"
    shown = [show(e) for e in refused]
    pairs = sum(1 for e in allowed if e.get("socketpair") is True)
    ver = sum(1 for e in allowed if e.get("ver_query") is True)
    return not refused, (f"{len(refused)} refused {shown[:5]}; allowed: {pairs} inside the standard library's "
                         f"socketpair (the event loop's socket pair, where Python builds one), "
                         f"{len(allowed) - pairs - ver} urllib3's IPv6 probe, {ver} the standard library's `ver` "
                         f"query (platform._syscmd_ver: Python 3.11 on Windows reading the OS version)")


def host_env(home: Path, launch_env: dict, audit: Path, source: bool) -> dict:
    """What the host gives the process: the user's environment without any
    NEXUS_* setting of this one's, a planted home, and the manifest's env."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEXUS_") and k != "XDG_DATA_HOME"}
    if not source:
        env = {k: v for k, v in env.items() if not k.startswith("PYTHON")}
    env.update(HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home / "AppData" / "Local"),
               NEXUS_AUDIT_NETWORK=str(audit))
    if source:
        env.update(PYTHONPATH=os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")])),
                   PYTHONDONTWRITEBYTECODE="1")
    env.update(launch_env)
    return env


def run(bundle: "Path | None", work: Path) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, saw: str) -> bool:
        results.append((name, bool(ok), saw))
        print(f"{'PASS' if ok else 'FAIL'} {name}: {saw}", flush=True)
        return bool(ok)

    plat = sys.platform if sys.platform in bb.PLATFORMS else "linux"
    if bundle is None:
        unpacked = ROOT
        manifest = bb.stamp(bb.load_manifest(), plat)
    else:
        unpacked = unpack(bundle, work / "unpacked")
        manifest = json.loads((unpacked / "manifest.json").read_text(encoding="utf-8"))
        check("S0 the bundle serves this platform", manifest["compatibility"]["platforms"] == [plat],
              f"compatibility.platforms {manifest['compatibility']['platforms']}")
    home, cwd, data = work / "home", work / "cwd", work / "data"
    for d in (home, cwd):
        d.mkdir(parents=True, exist_ok=True)
    (cwd / ".env").write_text("NEXUS_LEARNING=live\nNEXUS_MCP_OWNER=intruder\n", encoding="utf-8")
    command, args, launch_env = bb.host_launch(manifest, plat, str(unpacked), {"owner": OWNER, "data_folder": str(data)})
    if bundle is None:
        argv = [sys.executable, str(ROOT / bb.ENTRY), *args]
        # The source tree has no manifest folder of its own: the .env search
        # goes where the bundle's would, an empty folder.
        empty = work / "bundle_dir"
        empty.mkdir(exist_ok=True)
        launch_env["NEXUS_ENV_ROOT"] = str(empty)
    else:
        argv = [command, *args]
    print(f"launch: {argv[0]} (cwd {cwd})", flush=True)

    def start(label: str) -> tuple[Server, Path]:
        audit = work / f"audit-{label}.jsonl"
        return Server(argv, host_env(home, launch_env, audit, bundle is None), cwd, work / f"stderr-{label}.txt"), audit

    # S1 to S6: one process through a turn
    first, audit = start("first")
    try:
        started = time.monotonic()
        init = first.initialize()
        check("S1 starts over stdio", "result" in init,
              f"{(init.get('result') or {}).get('serverInfo')} in {time.monotonic() - started:.1f} s")
        names = [t["name"] for t in first.request("tools/list")["result"]["tools"]]
        check("S2 tools/list is the pinned set", names == PINNED_TOOLS, str(names))
        status = first.call("nexus_status")
        learning = (status.get("learning") or {}).get("state")
        set_by = (status.get("learning_applied") or {}).get("set_by")
        check("S4 user_config reaches the process", status.get("owner") == OWNER and learning == "frozen"
              and set_by == "environment", f"owner {status.get('owner')!r}, learning {learning!r} set by {set_by!r}")
        retrieved = first.call("nexus_retrieve", question="what does interval training improve?")
        recorded = first.call("nexus_record", retrieval_id=retrieved.get("retrieval_id"),
                              answer="Interval training improves VO2 max.", model="bundle-smoke")
    finally:
        code = first.close()
    stderr = (work / "stderr-first.txt").read_text(encoding="utf-8", errors="replace")
    ok, saw = network_verdict(audit)
    check("S3 the network set is empty", ok, saw)
    row = None
    if (data / "kb.db").is_file():
        con = open_read_only(data / "kb.db", row_factory=None)
        try:
            row = con.execute("SELECT accountable_identity, learning_mode, record_scope FROM lineage_records "
                              "ORDER BY rowid LIMIT 1").fetchone()
        finally:
            con.close()
    check("S5 the first record is frozen with the configured identity",
          row == (OWNER, "frozen", "mcp_block") and recorded.get("lineage_id"), f"exit {code}; first record {row}")
    written = sorted(p.name for p in cwd.iterdir())
    check("S6 no .env read, nothing written in the working directory",
          "env files []" in stderr and written == [".env"], f"working directory {written}; "
          + next((line.split("NEXUS MCP: ")[1] for line in stderr.splitlines() if "env files" in line), "no startup line"))

    # S7: two processes on one store
    holder, _ = start("holder")
    try:
        holder.initialize()
        second_argv, second_env = this_interpreter(argv, host_env(home, launch_env, work / "audit-second.jsonl",
                                                                  bundle is None))
        second = subprocess.run(second_argv, input=b"", capture_output=True, cwd=cwd, timeout=300, env=second_env)
        said = second.stderr.decode("utf-8", errors="replace")
        named = f"this store is open in another NEXUS process (pid {holder.proc.pid}"
        still = holder.call("nexus_status").get("owner")
        check("S7 a second process is refused by name", second.returncode == EXIT_STORE_LOCKED and named in said
              and still == OWNER, f"exit {second.returncode}; {said.strip().splitlines()[-1] if said.strip() else 'no stderr'}; "
              f"the first still answers: {still == OWNER}")
    finally:
        holder.close()

    # S8: a stale lock, its holder killed outright
    doomed, _ = start("doomed")
    doomed.initialize()
    doomed_pid = doomed.proc.pid
    doomed.kill()
    log = data / "kb.db.lock.log"
    before = log.read_text(encoding="utf-8") if log.is_file() else ""
    heir, _ = start("heir")
    try:
        heir_ok = "result" in heir.initialize() and heir.call("nexus_status").get("owner") == OWNER
    finally:
        heir.close()
    after = log.read_text(encoding="utf-8") if log.is_file() else ""
    added = [json.loads(line) for line in after[len(before):].splitlines() if line.strip()]
    taken = [e for e in added if e.get("event") == "stale_lock_taken_over"
             and (e.get("previous") or {}).get("pid") == doomed_pid]
    check("S8 a stale lock is taken over and logged", heir_ok and len(taken) == 1,
          f"the next process served: {heir_ok}; kb.db.lock.log gained {len(added)} line(s), "
          f"{len(taken)} naming the killed holder (pid {doomed_pid})")
    return results


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle", nargs="?", type=Path, help="the .mcpb to smoke")
    ap.add_argument("--source", action="store_true", help="the source entry in the binary's place")
    ap.add_argument("--work", type=Path, help="keep everything under this folder (default: a temporary one)")
    args = ap.parse_args(argv)
    force_utf8_stdio()
    # Every script loads .env (the ratchet); nothing in it reaches the process
    # under test, whose environment drops every NEXUS_* setting (host_env).
    load_env()
    if (args.bundle is None) == (not args.source):
        print("give a bundle, or --source", file=sys.stderr)
        return 2
    work = args.work or Path(tempfile.mkdtemp(prefix="nexus-bundle-smoke-"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"bundle smoke: {args.bundle or 'the source entry'} on {sys.platform}; work {work}", flush=True)
    try:
        results = run(None if args.source else args.bundle.resolve(), work)
    except Exception as e:                      # a crash is a failed smoke, said with its cause
        print(f"FAIL the smoke could not complete: {type(e).__name__}: {e}", flush=True)
        return 1
    failed = sorted(name for name, ok, _ in results if not ok)
    print(f"bundle smoke: {len(results) - len(failed)} of {len(results)} checks passed"
          + (f"; failed: {failed}" if failed else ""), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
