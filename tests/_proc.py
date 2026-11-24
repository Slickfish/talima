"""Every child process a test starts is started here (W4b part 1,
run/specs/W4b.md), so that a stuck child fails its test by name and never
hangs the suite.

The operator's merge gate hung for 1 h 45 min (14 November): a W2 test's
holder child was alive, waiting on its stdin as designed, while the test
waited on the holder's pipe with no bound (`readline()`, then `read()` in
the assertion's own message, which returns only when the child exits). Two
rules hold here and nowhere else:

1. **Every wait is bounded.** Every method that waits takes a keyword-only
   `timeout` with no default. The child's pipes are read the whole time it
   runs, on daemon threads, so a wait is a wait on a condition with a
   deadline, never on a pipe; a child that fills its pipe is never blocked
   by a test that is not reading. On timeout the child's whole tree is
   killed (Windows: `taskkill /T /F`; POSIX: the child leads its own
   session and the group is killed), the pipes are drained within a bound,
   and `ChildTimeout` names the child, what was waited for, for how long,
   and what it printed. A child never inherits the console's stdin: stdin
   is DEVNULL unless the test gives input or asks for a pipe.
2. **A child started as this interpreter is this interpreter**
   (`scripts/_interpreter.py`, part 2): under a Windows virtual environment
   `sys.executable` is the venv launcher, so `[sys.executable, ...]` is
   started as the base interpreter with `__PYVENV_LAUNCHER__`, one process
   whose pid is the one `Child.pid` gives.

The guard is `tests/test_w4b_bounded_children.py`: no tracked test starts a
process any other way, no test waits on a child without a bound, and this
module's own waits are bounded.
"""
from __future__ import annotations

import codecs
import io
import locale
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts._interpreter import this_interpreter  # noqa: E402

PIPE, DEVNULL, STDOUT = subprocess.PIPE, subprocess.DEVNULL, subprocess.STDOUT
CompletedProcess, CalledProcessError = subprocess.CompletedProcess, subprocess.CalledProcessError

KILL_WAIT_S = 30.0      # after a tree kill, how long the process may take to be gone
DRAIN_WAIT_S = 15.0     # after the process is gone, how long its pipes may take to close
TAIL_CHARS = 3000       # how much of what a child printed a failure shows


def _bound(value) -> float:
    """A wait's bound in seconds: a finite number, never None (None is
    `subprocess`'s spelling of "wait forever")."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value \
            or value < 0 or value == float("inf"):
        raise TypeError(f"a bound is required: timeout={value!r} (seconds, a finite number)")
    return float(value)


class ChildTimeout(subprocess.TimeoutExpired):
    """A child did not do what a test waited for within its bound. It is a
    `subprocess.TimeoutExpired`, so a test that expects one still catches
    it; its message names the child."""

    def __init__(self, child: "Child", waited_for: str, bound: float, note: str = ""):
        out = child.output()
        err = child.error_output()
        super().__init__(child.args, bound, output=out, stderr=err)
        self.label, self.pid, self.waited_for, self.note = child.label, child.pid, waited_for, note

    def __str__(self) -> str:
        shown = []
        for name, text in (("stdout", self.output), ("stderr", self.stderr)):
            if text:
                if isinstance(text, bytes):
                    text = text.decode("utf-8", errors="replace")
                shown.append(f"--- its {name}, the last {TAIL_CHARS} characters ---\n{text[-TAIL_CHARS:]}")
        return (f"the child {self.label!r} (pid {self.pid}) did not {self.waited_for} within {self.timeout:g} s; "
                f"it was killed with every process it started{self.note}\n" + ("\n".join(shown) or "it printed nothing"))


def _label(argv: list) -> str:
    words = [Path(str(argv[0])).name, *[str(a) for a in argv[1:]]]
    if "-c" in words[:-1]:
        code = words[words.index("-c") + 1].strip().splitlines()
        words = words[:words.index("-c") + 1] + [(code[0][:50] + " ...") if code else "''"]
    return " ".join(words[:4])[:120]


class _Sink:
    """One pipe's text or bytes as it arrives, decoded the way Popen's text
    mode decodes (the encoding, its error handler, universal newlines)."""

    def __init__(self, text: bool, encoding: str, errors: str):
        self.parts: list = []
        self.text = text
        self.closed = False
        self.error: "UnicodeDecodeError | None" = None
        self._value = "" if text else b""
        if text:
            self._decoder = io.IncrementalNewlineDecoder(codecs.getincrementaldecoder(encoding)(errors), translate=True)

    def feed(self, data: bytes, final: bool = False) -> None:
        """Decoding stops at the first undecodable byte under a strict error
        handler; the error is kept and raised by `communicate` (POSIX
        subprocess raises it from `run`; on Windows it died in a reader
        thread and the output read as None). The pipe is still read to its
        end, so the child never blocks on it."""
        if final:
            self.closed = True
        if self.error is not None:
            return
        if self.text:
            try:
                data = self._decoder.decode(data, final=final)
            except UnicodeDecodeError as e:
                self.error = e
                return
        if data:
            self.parts.append(data)

    def value(self):
        if self.parts:
            self._value += self._value[:0].join(self.parts)
            self.parts.clear()
        return self._value


class Child:
    """A child process whose output is read the whole time it runs. Every
    method that waits takes `timeout` (keyword-only, required) and, on
    timeout, kills the child's tree and raises `ChildTimeout`."""

    def __init__(self, argv, *, label: "str | None" = None, cwd=None, env=None, stdin=DEVNULL, stdout=PIPE,
                 stderr=PIPE, text=None, encoding=None, errors=None, universal_newlines=None, **popen):
        if isinstance(argv, (str, bytes, os.PathLike)):          # one command string (shell=True), or one program
            self.args = os.fspath(argv)
            argv_run, env_run = (this_interpreter([self.args], env)[0][0], env) if isinstance(argv, os.PathLike) \
                else (self.args, env)
        else:
            self.args = [os.fspath(a) if isinstance(a, os.PathLike) else a for a in argv]
            argv_run, env_run = this_interpreter(self.args, env)
        self.label = label or _label(self.args if isinstance(self.args, list) else [self.args])
        self.text = bool(text or encoding or errors or universal_newlines)
        self._encoding = encoding or locale.getpreferredencoding(False)
        self._errors = errors or "strict"
        popen.pop("bufsize", None)
        if os.name != "nt":
            popen.setdefault("start_new_session", True)
        self._own_group = os.name != "nt" and bool(popen.get("start_new_session"))
        self._popen = subprocess.Popen(argv_run, cwd=cwd, env=env_run, stdin=stdin, stdout=stdout, stderr=stderr,
                                       bufsize=0, **popen)
        self.pid = self._popen.pid
        self.started = time.monotonic()
        self._cond = threading.Condition()
        self._open = 0
        self._cursor = 0
        self._sinks: dict[str, _Sink] = {}
        for name in ("stdout", "stderr"):
            pipe = getattr(self._popen, name)
            if pipe is not None:
                sink = self._sinks[name] = _Sink(self.text, self._encoding, self._errors)
                self._open += 1
                threading.Thread(target=self._drain, args=(pipe, sink), daemon=True,
                                 name=f"_proc {name} of {self.label}").start()

    # -- reading (daemon threads only) ----------------------------------------
    def _drain(self, pipe, sink: _Sink) -> None:
        try:
            while True:
                try:
                    data = os.read(pipe.fileno(), 65536)
                except (OSError, ValueError):
                    data = b""
                with self._cond:
                    sink.feed(data, final=not data)
                    if not data:
                        self._open -= 1
                    self._cond.notify_all()
                if not data:
                    return
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    def output(self):
        """Everything the child has written to stdout so far (with stderr
        when it was merged), without waiting."""
        with self._cond:
            return self._sinks["stdout"].value() if "stdout" in self._sinks else None

    def error_output(self):
        with self._cond:
            return self._sinks["stderr"].value() if "stderr" in self._sinks else None

    def poll(self):
        return self._popen.poll()

    @property
    def returncode(self):
        return self._popen.returncode

    def _wait_for(self, ready, *, timeout: float, waited_for: str):
        timeout = _bound(timeout)
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                got = ready()
                if got is not None:
                    return got
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(min(left, 0.25))
        self._kill_tree()
        raise ChildTimeout(self, waited_for, timeout)

    def readline(self, *, timeout: float):
        """The next line of stdout (with its newline), the last partial line
        once the pipe is closed, or an empty string after that."""
        empty = "" if self.text else b""
        newline = "\n" if self.text else b"\n"

        def ready():
            sink = self._sinks["stdout"]
            text = sink.value()
            end = text.find(newline, self._cursor)
            if end >= 0 or sink.closed:
                stop = len(text) if end < 0 else end + 1
                line, self._cursor = text[self._cursor:stop], stop
                return line
            return None

        if "stdout" not in self._sinks:
            return empty
        return self._wait_for(ready, timeout=timeout, waited_for="print a line")

    def expect(self, needle, *, timeout: float):
        """stdout up to and including the first line that contains `needle`;
        ChildTimeout when the bound passes or the pipe closes without it."""

        if "stdout" not in self._sinks:
            raise ValueError(f"the child {self.label!r} has no stdout pipe to expect {needle!r} on")

        def ready():
            sink = self._sinks["stdout"]
            text = sink.value()
            at = text.find(needle, self._cursor)
            if at >= 0:
                newline = "\n" if self.text else b"\n"
                end = text.find(newline, at)
                stop = len(text) if end < 0 else end + 1
                seen, self._cursor = text[self._cursor:stop], stop
                return seen
            if sink.closed:
                return False
            return None

        got = self._wait_for(ready, timeout=timeout, waited_for=f"print {needle!r}")
        if got is False:
            self._kill_tree()
            raise ChildTimeout(self, f"print {needle!r} (its output ended without it)", timeout)
        return got

    # -- writing ---------------------------------------------------------------
    def _encoded(self, data):
        if isinstance(data, str):
            return data.replace("\n", os.linesep).encode(self._encoding, self._errors)
        return data

    def write(self, data, *, timeout: float) -> None:
        """Write to the child's stdin within the bound (a pipe whose reader
        does not read blocks the writer once its buffer is full)."""
        timeout = _bound(timeout)
        payload = self._encoded(data)
        stdin = self._popen.stdin
        if stdin is None:
            raise ValueError(f"the child {self.label!r} was started without a stdin pipe")
        done = threading.Event()

        def push():
            # A child that exits without reading its input is not an error
            # (subprocess.communicate ignores the broken pipe the same way).
            try:
                view = memoryview(payload)
                while view:
                    view = view[stdin.write(view):]
            except (OSError, ValueError):
                pass
            done.set()

        threading.Thread(target=push, daemon=True, name=f"_proc stdin of {self.label}").start()
        if not done.wait(timeout):
            self._kill_tree()
            raise ChildTimeout(self, f"read {len(payload)} bytes from its stdin", timeout)

    def close_stdin(self) -> None:
        if self._popen.stdin is not None:
            try:
                self._popen.stdin.close()
            except OSError:
                pass

    # -- ending ----------------------------------------------------------------
    def _drained(self, bound: float) -> bool:
        deadline = time.monotonic() + bound
        with self._cond:
            while self._open > 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(min(left, 0.25))
        return True

    def _kill_tree(self) -> None:
        """Kill the child and every process it started; wait for it within
        KILL_WAIT_S and for its pipes within DRAIN_WAIT_S."""
        if os.name == "nt":
            if self._popen.poll() is None:
                try:
                    killer = subprocess.Popen(["taskkill", "/PID", str(self.pid), "/T", "/F"],
                                              stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)
                    killer.wait(timeout=KILL_WAIT_S)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        elif self._own_group:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            self._popen.kill()
        except OSError:
            pass
        try:
            self._popen.wait(timeout=KILL_WAIT_S)
        except subprocess.TimeoutExpired:
            pass
        self.close_stdin()
        self._drained(DRAIN_WAIT_S)

    def wait(self, *, timeout: float) -> int:
        """The exit status, once the child has exited and its pipes have
        closed. A pipe still held open after the child exited means a
        process it started is still running: that tree is killed and named."""
        timeout = _bound(timeout)
        try:
            code = self._popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._kill_tree()
            raise ChildTimeout(self, "exit", timeout) from None
        if not self._drained(DRAIN_WAIT_S):
            self._kill_tree()
            if self._open > 0:
                raise ChildTimeout(self, "close its output after exiting", DRAIN_WAIT_S,
                                   note=" (a process it started still held its output)")
        return code

    def communicate(self, input=None, *, timeout: float):
        """(stdout, stderr) once the child has read `input` and exited."""
        timeout = _bound(timeout)
        deadline = time.monotonic() + timeout
        if input is not None:
            self.write(input, timeout=timeout)
        self.close_stdin()
        try:
            self.wait(timeout=max(0.0, deadline - time.monotonic()))
        except ChildTimeout as e:
            if e.waited_for != "exit":
                raise
            raise ChildTimeout(self, "exit", timeout) from None
        for sink in self._sinks.values():
            if sink.error is not None:
                raise sink.error
        return self.output(), self.error_output()

    def stop(self, *, timeout: float) -> int:
        """Close stdin (the child's signal to finish) and wait for it."""
        timeout = _bound(timeout)
        self.close_stdin()
        return self.wait(timeout=timeout)

    def interrupt(self) -> None:
        """POSIX: SIGINT to the child (a server's Ctrl+C). Windows, where a
        child cannot be sent Ctrl+C without sharing a console: the tree is
        killed."""
        if self._popen.poll() is not None:
            return
        if os.name == "nt":
            self._kill_tree()
        else:
            self._popen.send_signal(signal.SIGINT)

    def kill(self) -> int:
        """Kill the child and every process it started, within KILL_WAIT_S."""
        self._kill_tree()
        if self._popen.poll() is None:
            raise ChildTimeout(self, "die after a kill", KILL_WAIT_S)
        return self._popen.returncode

    def __enter__(self) -> "Child":
        return self

    def __exit__(self, *exc) -> None:
        if self._popen.poll() is None or self._open > 0:
            self._kill_tree()


def run(argv, *, timeout: float, input=None, capture_output: bool = False, check: bool = False, stdin=None,
        stdout=None, stderr=None, label: "str | None" = None, **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run`, bounded: the child's tree is killed when `timeout`
    passes (subprocess.run kills only the child, and on Windows then waits
    for its pipes with no bound), and stdin is DEVNULL unless input is
    given or stdin is named."""
    timeout = _bound(timeout)
    if capture_output:
        if stdout is not None or stderr is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output.")
        stdout = stderr = PIPE
    if input is not None:
        if stdin is not None:
            raise ValueError("stdin and input arguments may not both be used.")
        stdin = PIPE
    elif stdin is None:
        stdin = DEVNULL
    child = Child(argv, label=label, stdin=stdin, stdout=stdout, stderr=stderr, **kwargs)
    with child:
        out, err = child.communicate(input, timeout=timeout)
    done = subprocess.CompletedProcess(child.args, child.returncode, out, err)
    if check:
        done.check_returncode()
    return done
