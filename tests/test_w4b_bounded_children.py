"""W4b part 1 (run/specs/W4b.md): every child process a test starts is
started by `tests/_proc.py`, and no wait on a child is unbounded, so a stuck
child fails its test by name and never hangs the suite.

The operator's merge gate hung for 1 h 45 min on 14 November: W2's holder
child was alive, waiting on its stdin as designed, while the test waited on
its pipe with no bound (`readline()`, and `read()` inside the assertion's own
message, which returns only when the child exits). 112 sites started or waited
on a child directly: 106 process starts in 47 tracked test files and 6
unbounded waits, 48 files in all (the census, run/specs/W4b.md S1.1).

G1  Every child is started by tests/_proc.py. Over every tracked .py under
    tests/ but the helper: no call or reference to a process starter, where
    the starters are DERIVED from the standard library (the functions in
    `subprocess.__all__` and its classes that have `communicate`; the names
    in `asyncio.subprocess.__all__` and the event loop's `subprocess*`
    methods; the classes `concurrent.futures` exports from its process
    module; the `os` process families, by prefix), resolved through every
    import alias, plus any import of the process modules (`multiprocessing`,
    `pty`).
G2  No test waits on a child without a bound: no call to a method that
    `subprocess.Popen` defines with a `timeout` (derived) without one (or
    with `timeout=None`), whatever the receiver; no read of a child's pipe
    (a Popen parameter named std*, then a reading method of
    `io.BufferedReader`, derived), called, referenced or iterated, whatever
    the receiver but `sys`.
G3  The helper's own waits are bounded: G2 over the helper, every thread it
    starts is a daemon, it reads and writes pipes only inside a function a
    thread runs, every wait, join and get passes a bound, and every `timeout`
    parameter is keyword-only with no default (and None is refused when
    passed, by behaviour).
B1  The consequence, planted (OQ-16 rule 3): a child that prints nothing, or
    the wrong first line, and then waits on its stdin forever (W2's holder
    shape) fails within its bound, named, and is dead afterwards; the
    PRE-FIX helper code run on the same child hangs (law 16: its run in a
    bounded child is killed at the bound, never returns).
B2  A tree: a child whose own child holds the pipe is killed with it within
    the bound; subprocess.run on the same tree (pre-fix) leaves the
    grandchild alive (POSIX) or waits on the pipe with no bound (Windows).
B3  A child that writes more than a pipe holds and exits completes under a
    wait that never reads (the drain), where Popen's wait without a reader
    deadlocks (pre-fix, W3b-D13's class).
"""
from __future__ import annotations

import ast
import asyncio.subprocess
import concurrent.futures
import inspect
import io
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tests import _proc
from tests._tracked import tracked

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "tests" / "_proc.py"


# --- the derived classes -------------------------------------------------------------------

def subprocess_starters() -> set[str]:
    """The names in subprocess's public API that start a process: its
    functions, and its classes that have a `communicate` method."""
    out = set()
    for name in subprocess.__all__:
        obj = getattr(subprocess, name, None)
        if inspect.isfunction(obj) or (inspect.isclass(obj) and hasattr(obj, "communicate")):
            out.add(name)
    return out


def asyncio_starters() -> set[str]:
    return set(asyncio.subprocess.__all__)


def loop_starters() -> set[str]:
    return {m for m in dir(asyncio.AbstractEventLoop) if m.startswith("subprocess")}


def futures_starters() -> set[str]:
    return {n for n in concurrent.futures.__all__
            if getattr(getattr(concurrent.futures, n, None), "__module__", "") == "concurrent.futures.process"}


OS_FAMILIES = ("exec", "spawn", "posix_spawn", "fork")   # os's process functions, by family prefix
OS_SINGLES = ("system", "popen", "startfile")
PROCESS_MODULES = ("multiprocessing", "pty")             # importing either starts processes or a terminal


def os_starter(name: str) -> bool:
    return name.startswith(OS_FAMILIES) or name in OS_SINGLES


def starter_classes() -> list:
    """The process classes among the starters (Popen), derived, so this
    module's own introspection names no starter."""
    return [getattr(subprocess, n) for n in sorted(subprocess_starters()) if inspect.isclass(getattr(subprocess, n))]


def pipe_attributes() -> set[str]:
    """A child's pipes: the process class's parameters whose name begins std."""
    return {p for cls in starter_classes() for p in inspect.signature(cls).parameters if p.startswith("std")}


def read_methods() -> set[str]:
    """The reading methods of a buffered reader (a predicate such as
    readable is not one)."""
    return {m for m in dir(io.BufferedReader) if m.startswith(("read", "peek")) and not m.endswith("able")}


def bounded_methods() -> dict[str, int]:
    """Popen's public methods with a `timeout` parameter, and the position
    of that parameter among the call's positional arguments."""
    out = {}
    for name, fn in [item for cls in starter_classes() for item in vars(cls).items()]:
        if name.startswith("_") or not callable(fn):
            continue
        params = list(inspect.signature(fn).parameters)
        if "timeout" in params:
            out[name] = params.index("timeout") - 1          # less self
    return out


# --- the AST ------------------------------------------------------------------------------

def _aliases(tree: ast.AST) -> dict[str, str]:
    """Each name a module binds by import, to the dotted name it means."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _dotted(node: ast.AST, aliases: dict[str, str]) -> "str | None":
    """The dotted name an expression means, through the import aliases; an
    attribute named for a process module (`x.subprocess`, `x.os`) is that
    module whatever its receiver."""
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value, aliases)
        if node.attr in ("subprocess", "os", "asyncio") and head != "asyncio":
            return node.attr
        return f"{head}.{node.attr}" if head else None
    return None


def starter_problems(source: str, path: str) -> list[str]:
    """G1 over one module's source."""
    tree = ast.parse(source)
    aliases = _aliases(tree)
    sp, aio, loop, fut = subprocess_starters(), asyncio_starters(), loop_starters(), futures_starters()
    problems = []
    for node in ast.walk(tree):
        where = f"{path}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in PROCESS_MODULES:
                    problems.append(f"{where}: imports {a.name}, which starts processes outside tests/_proc.py")
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.split(".")[0] in PROCESS_MODULES:
                problems.append(f"{where}: imports from {node.module}, which starts processes outside tests/_proc.py")
        elif isinstance(node, (ast.Attribute, ast.Name)) and isinstance(node.ctx, ast.Load):
            if isinstance(node, ast.Attribute) and node.attr in loop:
                problems.append(f"{where}: {ast.unparse(node)} starts a process outside tests/_proc.py")
                continue
            name = _dotted(node, aliases)
            if not name or "." not in name:
                continue
            module, _, attr = name.rpartition(".")
            if (module == "subprocess" and attr in sp) or (module in ("asyncio", "asyncio.subprocess") and attr in aio) \
                    or (module == "concurrent.futures" and attr in fut) or (module == "os" and os_starter(attr)):
                problems.append(f"{where}: {ast.unparse(node)} starts a process outside tests/_proc.py")
    return sorted(set(problems))


def _bounded_call(node: ast.Call, position: int) -> bool:
    for kw in node.keywords:
        if kw.arg == "timeout":
            return not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
        if kw.arg is None:                                   # **kwargs: the bound may be inside
            return True
    if len(node.args) > position:
        given = node.args[position]
        return not (isinstance(given, ast.Constant) and given.value is None)
    return False


def wait_problems(source: str, path: str) -> list[str]:
    """G2 over one module's source."""
    tree = ast.parse(source)
    pipes, reads, bounded = pipe_attributes(), read_methods(), bounded_methods()

    def child_pipe(expr: ast.AST) -> bool:
        return isinstance(expr, ast.Attribute) and expr.attr in pipes and \
            not (isinstance(expr.value, ast.Name) and expr.value.id == "sys")

    # An awaitable handed to asyncio.wait_for with a bound is bounded there.
    wrapped = {id(node.args[0]) for node in ast.walk(tree)
               if isinstance(node, ast.Call) and node.args and isinstance(node.func, (ast.Attribute, ast.Name))
               and (node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id) == "wait_for"
               and _bounded_call(node, 1)}
    problems = []
    for node in ast.walk(tree):
        where = f"{path}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in bounded:
            if id(node) not in wrapped and not _bounded_call(node, bounded[node.func.attr]):
                problems.append(f"{where}: {ast.unparse(node)[:90]} waits with no bound")
        elif isinstance(node, ast.Attribute) and node.attr in reads and child_pipe(node.value):
            problems.append(f"{where}: {ast.unparse(node)[:90]} reads a child's pipe with no bound")
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) and child_pipe(node.iter):
            problems.append(f"{where}: iterates over {ast.unparse(node.iter)}, a child's pipe, with no bound")
    return sorted(set(problems))


def helper_problems(source: str, path: str) -> list[str]:
    """G3 over the helper's source."""
    tree = ast.parse(source)
    problems = list(wait_problems(source, path))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _dotted(node.func, _aliases(tree)) in ("threading.Thread",):
            kw = {k.arg: k.value for k in node.keywords}
            if not (isinstance(kw.get("daemon"), ast.Constant) and kw["daemon"].value is True):
                problems.append(f"{path}:{node.lineno}: a thread that is not a daemon")
            target = kw.get("target")
            if isinstance(target, ast.Attribute):
                targets.add(target.attr)
            elif isinstance(target, ast.Name):
                targets.add(target.id)
    reads = read_methods()
    aliases = _aliases(tree)
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for fn in functions:
        inner = {id(n) for sub in ast.walk(fn) if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub is not fn
                 for n in ast.walk(sub)}
        for node in ast.walk(fn):
            if id(node) in inner or not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            where = f"{path}:{node.lineno}"
            attr = node.func.attr
            pipe_io = attr in reads or _dotted(node.func, aliases) == "os.read" or \
                (attr == "write" and isinstance(node.func.value, ast.Name) and node.func.value.id == "stdin")
            if pipe_io and fn.name not in targets:
                problems.append(f"{where}: {ast.unparse(node)[:80]} reads or writes a pipe outside a thread's target")
            if attr in ("wait", "join", "get", "acquire") and not node.args and \
                    not any(k.arg in ("timeout", None) for k in node.keywords):
                problems.append(f"{where}: {ast.unparse(node)[:80]} waits with no bound")
        args = fn.args
        for i, a in enumerate(args.posonlyargs + args.args):
            if a.arg == "timeout":
                problems.append(f"{path}:{fn.lineno}: {fn.name}'s timeout can be passed positionally")
        for a, default in zip(args.kwonlyargs, args.kw_defaults):
            if a.arg == "timeout" and default is not None:
                problems.append(f"{path}:{fn.lineno}: {fn.name}'s timeout has a default ({ast.unparse(default)})")
    return sorted(set(problems))


def guarded_files() -> list[Path]:
    return [p for p in tracked("tests/*.py") if p.resolve() != HELPER.resolve()]


def _all(check) -> tuple[list[str], int]:
    files = guarded_files()
    problems = []
    for path in files:
        problems += check(path.read_text(encoding="utf-8"), path.relative_to(REPO).as_posix())
    return problems, len(files)


# --- G1 to G3 -----------------------------------------------------------------------------

def test_g1_every_child_a_tracked_test_starts_is_started_by_tests_proc():
    problems, n = _all(starter_problems)
    print(f"G1 over {n} tracked test files; starters: subprocess {sorted(subprocess_starters())}, asyncio "
          f"{sorted(asyncio_starters())}, loop {sorted(loop_starters())}, futures {sorted(futures_starters())}, os by "
          f"prefix {OS_FAMILIES} and {OS_SINGLES}, modules {PROCESS_MODULES}; problems {len(problems)}")
    assert n > 150 and subprocess_starters() >= {"run"}, "non-vacuity: the population and the derived class"
    assert not problems, "\n".join(problems)


def test_g2_no_tracked_test_waits_on_a_child_without_a_bound():
    problems, n = _all(wait_problems)
    print(f"G2 over {n} files; bounded methods {bounded_methods()}, pipes {sorted(pipe_attributes())}, reads "
          f"{sorted(read_methods())}; problems {len(problems)}")
    assert bounded_methods() and pipe_attributes() and read_methods(), "non-vacuity: the derived classes"
    assert not problems, "\n".join(problems)


def test_g3_the_helper_bounds_its_own_waits_and_requires_every_timeout():
    problems = helper_problems(HELPER.read_text(encoding="utf-8"), "tests/_proc.py")
    live = []
    for owner in [_proc] + [c for _, c in inspect.getmembers(_proc, inspect.isclass) if c.__module__ == _proc.__name__]:
        for name, fn in inspect.getmembers(owner, inspect.isfunction):
            if name.startswith("_") or fn.__module__ != _proc.__name__:
                continue
            p = inspect.signature(fn).parameters.get("timeout")
            if p is not None and (p.kind is not p.KEYWORD_ONLY or p.default is not p.empty):
                live.append(f"{owner.__name__}.{name}: timeout is {p.kind.name} with default {p.default!r}")
    takes = sorted(f"{getattr(o, '__name__', '?')}.{n}" for o in [_proc, _proc.Child]
                   for n, f in inspect.getmembers(o, inspect.isfunction)
                   if not n.startswith("_") and "timeout" in inspect.signature(f).parameters)
    print(f"G3 the helper's waiting API: {takes}; problems {problems + live}")
    assert len(takes) >= 6, takes
    assert not problems and not live, "\n".join(problems + live)


def test_g3_none_is_refused_where_a_bound_is_required(tmp_path):
    child = _proc.Child([sys.executable, "-c", "import time; time.sleep(30)"], label="sleeper")
    try:
        # The None goes through a name: the guard (G2) names a literal None
        # as no bound, and this test passes one to see it refused at run time.
        unbounded = None
        for call in (lambda: child.wait(timeout=unbounded), lambda: child.readline(timeout=unbounded),
                     lambda: child.expect("x", timeout=unbounded), lambda: _proc.run(["x"], timeout=unbounded)):
            with pytest.raises(TypeError, match="a bound"):
                call()
    finally:
        child.kill()


# --- the companions -----------------------------------------------------------------------

def test_g1_companions_every_planted_starter_is_named():
    """Members of the class the guard's source does not name, through
    aliases: an import-renamed subprocess function, an os family member, an
    asyncio starter, an event loop's method, a process pool, a process
    module, a starter reached through another module's attribute, and a
    starter held as a value."""
    planted = {
        "from subprocess import getoutput as out\nout('echo')\n": "out",
        "import os as o\no.spawnvpe(o.P_WAIT, 'x', ['x'], {})\n": "o.spawnvpe",
        "import asyncio\nasyncio.create_subprocess_shell('x')\n": "asyncio.create_subprocess_shell",
        "def f(loop):\n    return loop.subprocess_shell(None, 'x')\n": "loop.subprocess_shell",
        "from concurrent.futures import ProcessPoolExecutor as Pool\nPool()\n": "Pool",
        "import multiprocessing.pool\n": "imports multiprocessing.pool",
        "import start\nstart.subprocess.check_call(['x'])\n": "start.subprocess.check_call",
        "import subprocess as sp\nspawn = sp.Popen\n": "sp.Popen",
    }
    for source, named in planted.items():
        said = starter_problems(source, "planted.py")
        print(f"G1 planted {source.strip()!r}: {said}")
        assert said and any(named in s for s in said), (source, said)
    for clean in ("import subprocess\nx = subprocess.PIPE\ntry:\n    pass\nexcept subprocess.TimeoutExpired:\n    pass\n",
                  "import _proc\n_proc.run(['x'], timeout=5)\n", "s = 'subprocess.run'\n",
                  "def f(monkeypatch, start):\n    monkeypatch.setattr(start.subprocess, 'run', None)\n"):
        assert starter_problems(clean, "clean.py") == [], clean


def test_g2_companions_every_planted_unbounded_wait_is_named():
    planted = {
        "def f(p, buf):\n    p.stdout.readinto1(buf)\n": "readinto1",
        "def f(proc):\n    for line in proc.stderr:\n        pass\n": "iterates over proc.stderr",
        "def f(p):\n    return [l for l in p.stdout]\n": "iterates over p.stdout",
        "def f(p):\n    return iter(p.stdout.readline, '')\n": "p.stdout.readline",
        "def f(p):\n    return p.communicate(b'x')\n": "p.communicate",
        "def f(p):\n    return p.communicate(b'x', None)\n": "p.communicate",
        "def f(child):\n    return child.wait()\n": "child.wait()",
        "def f(child):\n    return child.wait(timeout=None)\n": "child.wait(timeout=None)",
        "import asyncio\nasync def f(e):\n    await asyncio.wait_for(e.wait(), None)\n": "e.wait()",
    }
    for source, named in planted.items():
        said = wait_problems(source, "planted.py")
        print(f"G2 planted {source.strip()!r}: {said}")
        assert said and any(named in s for s in said), (source, said)
    for clean in ("def f(cp):\n    return cp.stdout.strip().splitlines()\n",
                  "def f(child):\n    return child.wait(timeout=5)\n",
                  "def f(p):\n    return p.communicate(b'x', 5)\n",
                  "import sys\nsys.stdout.write('x')\n",
                  "def f(p, **kw):\n    return p.wait(**kw)\n",
                  "import asyncio\nasync def f(e):\n    await asyncio.wait_for(e.wait(), timeout=5)\n"):
        assert wait_problems(clean, "clean.py") == [], clean


def test_g3_companions_a_planted_helper_is_named_for_each_rule():
    base = HELPER.read_text(encoding="utf-8")
    mutants = {
        "a thread that is not a daemon": base.replace('args=(pipe, sink), daemon=True,', 'args=(pipe, sink),', 1),
        "outside a thread's target": base.replace("    def poll(self):\n",
                                                  "    def peek_now(self):\n        return self._popen.stdout.read1(1)\n\n"
                                                  "    def poll(self):\n", 1),
        "waits with no bound": base.replace("code = self._popen.wait(timeout=timeout)", "code = self._popen.wait()", 1),
        "timeout has a default": base.replace("def stop(self, *, timeout: float)", "def stop(self, *, timeout: float = 60.0)", 1),
        "can be passed positionally": base.replace("def readline(self, *, timeout: float)", "def readline(self, timeout: float)", 1),
    }
    assert helper_problems(base, "tests/_proc.py") == []
    for rule, source in mutants.items():
        assert source != base, f"the mutant for {rule!r} did not apply"
        said = helper_problems(source, "tests/_proc.py")
        print(f"G3 planted ({rule}): {said}")
        assert any(rule in s for s in said), (rule, said)


# --- B1 to B3: the consequence ------------------------------------------------------------

HOLDER_STUCK = "import sys, time\n{first}sys.stdin.read()\n"      # W2's holder shape, its line planted


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = _proc.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], timeout=60, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                                        # a zombie is not alive
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def test_b1_a_holder_that_never_says_ready_fails_its_test_by_name_within_the_bound():
    for first, waited in (("", "print a line"), ("print('a warning from an import', flush=True)\n", "print 'HOLDER READY'")):
        child = _proc.Child([sys.executable, "-c", HOLDER_STUCK.format(first=first)], label="the planted holder",
                            stdin=_proc.PIPE, stdout=_proc.PIPE, stderr=_proc.STDOUT, text=True, encoding="utf-8", errors="replace")
        started = time.monotonic()
        with pytest.raises(_proc.ChildTimeout) as raised:
            if first:
                child.expect("HOLDER READY", timeout=3)
            else:
                child.readline(timeout=3)
        took = time.monotonic() - started
        said = str(raised.value)
        print(f"B1 ({sys.platform}) {waited!r}: failed after {took:.1f} s: {said.splitlines()[0]}")
        assert "the planted holder" in said and waited in said and f"pid {child.pid}" in said, said
        assert took < 3 + _proc.KILL_WAIT_S, took
        assert child.poll() is not None and not _alive(child.pid), "the stuck holder was left alive"
        if first:
            assert "a warning from an import" in said, said


PRE_FIX_HOLDER = '''
import subprocess, sys
class _Holder:                         # tests/test_w2_store_lock.py at 6910c64, verbatim but for the body
    def __init__(self, code):
        self.proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        line = self.proc.stdout.readline()
        assert "HOLDER READY" in line, f"the holder did not start: {line}{self.proc.stdout.read()[-800:]}"
print("PRE-FIX STARTED", flush=True)
try:
    _Holder(sys.argv[1])
    print("PRE-FIX RETURNED")
except AssertionError as e:
    print("PRE-FIX FAILED BY NAME:", str(e)[:200])
'''


def test_b1_pre_fix_the_same_holder_hangs_the_w2_helper():
    """Law 16: W2's helper as it was, on the planted holder that prints a
    warning first, never returns; its run, bounded here, is killed at the
    bound with its holder."""
    stuck = HOLDER_STUCK.format(first="print('a warning from an import', flush=True)\nprint('HOLDER READY', flush=True)\n")
    with pytest.raises(_proc.ChildTimeout) as raised:
        _proc.run([sys.executable, "-c", PRE_FIX_HOLDER, stuck], timeout=10, label="the pre-fix W2 helper",
                  capture_output=True, text=True, encoding="utf-8", errors="replace")
    said = str(raised.value)
    print(f"B1 pre-fix ({sys.platform}): {said.splitlines()[0]}")
    assert "PRE-FIX STARTED" in (raised.value.output or "") and "RETURNED" not in said and "BY NAME" not in said, said


TREE = ("import pathlib, subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "if len(sys.argv) > 1:\n"
        "    pathlib.Path(sys.argv[1]).write_text(str(g.pid), encoding='utf-8')\n"
        "print('GRANDCHILD', g.pid, flush=True)\n"
        "time.sleep(120)\n")


def _kill_pid_tree(pid: int) -> None:
    """Cleanup for a pre-fix arm, which leaves its grandchild on purpose
    (under a Windows venv the grandchild is a launcher with an interpreter
    behind it, so the tree is killed, not the pid)."""
    if not _alive(pid):
        return
    if os.name == "nt":
        _proc.run(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=60, capture_output=True)
    else:
        os.kill(pid, 9)
    deadline = time.monotonic() + 15                      # a kill is delivered, not instant
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)


def _grandchild(text) -> int:
    for line in (text or "").splitlines():
        if line.startswith("GRANDCHILD "):
            return int(line.split()[1])
    raise AssertionError(f"the child named no grandchild: {text!r}")


def test_b2_a_tree_whose_grandchild_holds_the_pipe_is_killed_within_the_bound():
    started = time.monotonic()
    with pytest.raises(_proc.ChildTimeout) as raised:
        _proc.run([sys.executable, "-c", TREE], timeout=5, capture_output=True, text=True, encoding="utf-8", errors="replace", label="the planted tree")
    took = time.monotonic() - started
    grandchild = _grandchild(raised.value.output)
    print(f"B2 ({sys.platform}): failed after {took:.1f} s; grandchild {grandchild} alive {_alive(grandchild)}")
    assert took < 5 + _proc.KILL_WAIT_S + _proc.DRAIN_WAIT_S, took
    assert not _alive(grandchild), "the grandchild survived the tree kill"


PRE_FIX_RUN = '''
import subprocess, sys, threading, time
done = []
def go():
    try:
        subprocess.run([sys.executable, "-c", sys.argv[1], sys.argv[2]], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=3)
    except subprocess.TimeoutExpired as e:
        done.append(e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode())
t = threading.Thread(target=go, daemon=True)
t.start()
t.join(25)
print("RETURNED" if done else "STILL WAITING", flush=True)
print(done[0] if done else "", flush=True)
'''


def test_b2_pre_fix_subprocess_run_leaves_the_grandchild_or_waits_on_it(tmp_path):
    """Law 16: subprocess.run(timeout=3) on the same tree. POSIX: it returns,
    and the grandchild is still alive. Windows: after killing the child it
    waits for the pipe the grandchild holds, with no bound (seen here as
    still waiting 25 s later). Either way the grandchild, whose pid the tree
    writes to a file, is alive afterwards, and this test kills it (it left
    two processes behind on CI's venv runner before it did: run 80)."""
    pidfile = tmp_path / "grandchild.pid"
    out = _proc.run([sys.executable, "-c", PRE_FIX_RUN, TREE, str(pidfile)], timeout=120, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", label="the pre-fix subprocess.run")
    grandchild = int(pidfile.read_text(encoding="utf-8"))
    alive = _alive(grandchild)
    try:
        print(f"B2 pre-fix ({sys.platform}): {out.stdout.strip()!r}; grandchild {grandchild} alive {alive}")
        if "STILL WAITING" in out.stdout:
            assert os.name == "nt", out.stdout
        else:
            assert _grandchild(out.stdout) == grandchild, out.stdout
        assert alive, "pre-fix, the grandchild was expected alive after subprocess.run's timeout"
    finally:
        _kill_pid_tree(grandchild)
    assert not _alive(grandchild), "the cleanup left the pre-fix arm's grandchild alive"


FLOOD = "import sys\nsys.stdout.write('x' * 2_000_000)\nsys.stdout.flush()\n"


def test_b3_a_child_that_writes_more_than_a_pipe_holds_completes_under_a_wait_that_never_reads():
    child = _proc.Child([sys.executable, "-c", FLOOD], label="the flood", text=True, encoding="utf-8", errors="replace")
    assert child.wait(timeout=60) == 0
    assert len(child.output()) == 2_000_000


PRE_FIX_FLOOD = '''
import subprocess, sys
p = subprocess.Popen([sys.executable, "-c", sys.argv[1]], stdout=subprocess.PIPE)
try:
    p.wait(timeout=10)
    print("EXITED")
except subprocess.TimeoutExpired:
    print("BLOCKED ON ITS FULL PIPE")
    p.kill()
    p.wait(timeout=30)
'''


def test_b3_pre_fix_a_wait_that_never_reads_blocks_the_flood():
    out = _proc.run([sys.executable, "-c", PRE_FIX_FLOOD, FLOOD], timeout=120, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(f"B3 pre-fix ({sys.platform}): {out.stdout.strip()!r}")
    assert "BLOCKED ON ITS FULL PIPE" in out.stdout, out.stdout + (out.stderr or "")


def test_b4_child_timeout_is_a_timeout_expired_that_names_the_child():
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        _proc.run([sys.executable, "-c", "print('partial', flush=True)\nimport time\ntime.sleep(60)"], timeout=2,
                  capture_output=True, text=True, encoding="utf-8", errors="replace", label="the named sleeper")
    said = str(raised.value)
    assert isinstance(raised.value, _proc.ChildTimeout)
    assert "the named sleeper" in said and "within 2 s" in said and "partial" in said, said


STDIN_DRIVER = '''
import subprocess, sys
sys.path.insert(0, sys.argv[1])
from tests import _proc
code = "import sys; print(repr(sys.stdin.read()))"
if sys.argv[2] == "proc":
    out = _proc.run([sys.executable, "-c", code], timeout=60, capture_output=True, text=True, encoding="utf-8",
                    errors="replace").stdout
elif sys.argv[2] == "child":
    out = _proc.Child([sys.executable, "-c", code], stdout=_proc.PIPE, text=True, encoding="utf-8",
                      errors="replace").communicate(timeout=60)[0]
else:                                   # pre-fix: subprocess.run, which hands the child this process's stdin
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=60).stdout
print("GRANDCHILD READ", out.strip(), flush=True)
'''


def test_b5_a_child_never_inherits_the_stdin_of_the_process_that_starts_it():
    """D4. A driver whose own stdin carries data (a console's keystrokes, a
    pipe) starts a child that reads its stdin: through tests/_proc.py the
    child reads nothing; through subprocess.run (the pre-fix arm) it reads
    the driver's data, which on a console means waiting for a key. Under
    pytest's default capture a test's own fd 0 is /dev/null already, so the
    default matters where it is not: a run with -s, a helper run outside
    pytest (scripts import tests._tracked), a console. The verifier's M1: the
    previous form of this test read pytest's /dev/null and passed with
    the default removed."""
    for how, expected in (("proc", "''"), ("child", "''"), ("subprocess", repr("typed by a person\n"))):
        out = _proc.run([sys.executable, "-c", STDIN_DRIVER, str(REPO), how], input="typed by a person\n",
                        timeout=180, capture_output=True, text=True, encoding="utf-8", errors="replace",
                        label=f"the stdin driver ({how})")
        print(f"B5 ({sys.platform}) {how}: {out.stdout.strip()!r}")
        assert f"GRANDCHILD READ {expected}" in out.stdout, (how, out.stdout, out.stderr)


def test_b6_run_matches_subprocess_run_on_what_tests_read():
    script = "import sys\nprint('out line')\nprint('err line', file=sys.stderr)\nsys.exit(4)\n"
    got = _proc.run([sys.executable, "-c", script], timeout=60, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert (got.returncode, got.stdout, got.stderr) == (4, "out line\n", "err line\n"), got
    got = _proc.run([sys.executable, "-c", script], timeout=60, capture_output=True)
    assert got.stdout.replace(b"\r\n", b"\n") == b"out line\n", got
    with pytest.raises(subprocess.CalledProcessError):
        _proc.run([sys.executable, "-c", script], timeout=60, capture_output=True, check=True)
    echoed = _proc.run([sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"], timeout=60,
                       input="a\nb\n", capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert echoed.stdout == "a\nb\n", echoed
