"""A child started as this interpreter is this interpreter (W4b part 2,
run/specs/W4b.md).

On Windows a virtual environment's `Scripts\\python.exe` is not Python: it
is the venv launcher. It reads `pyvenv.cfg` and starts the base
interpreter (`home` in that file) as ITS OWN child with
`__PYVENV_LAUNCHER__` set to its own path, and waits for it. So a process
running in a virtual environment that starts `sys.executable` starts two
processes: the launcher, whose pid the caller holds, and the interpreter,
which does the work, holds the store's lock under its own pid and outlives
the launcher only as long as the launcher's job object lets it. CPython's
own multiprocessing does not start the launcher
(`Lib/multiprocessing/popen_spawn_win32.py`, bpo-35797): when it would run
`sys.executable` in a virtual environment it runs `sys._base_executable`
with `__PYVENV_LAUNCHER__` set to `sys.executable`, one process that is this
interpreter with this environment's packages. `this_interpreter` does the
same for the suite's children and for the scripts that drive the product
from outside (`scripts/bundle_smoke.py`, `scripts/uat_simulate.py`).

Everywhere else nothing changes: on POSIX a virtual environment's
`bin/python` is a link to the interpreter itself, one process, and the
arguments and environment are returned as given. On Windows a child whose
first argument is any other program gets an environment without
`__PYVENV_LAUNCHER__`, so it is the interpreter its arguments name (a base
interpreter a test starts on purpose stays the base interpreter).

Standard library only: scripts and tests import it before anything is
installed.
"""
from __future__ import annotations

import os
import sys

LAUNCHER_VARIABLE = "__PYVENV_LAUNCHER__"
WINDOWS = os.name == "nt"          # a module constant, so the rule can be exercised on every platform


def _same(a, b) -> bool:
    return os.path.normcase(os.path.abspath(os.fspath(a))) == os.path.normcase(os.path.abspath(os.fspath(b)))


def in_windows_venv() -> bool:
    """True when this interpreter runs in a Windows virtual environment
    whose `sys.executable` is the launcher, not the interpreter."""
    base = getattr(sys, "_base_executable", None)
    return WINDOWS and bool(base) and bool(sys.executable) and not _same(sys.executable, base)


def this_interpreter(argv, env=None) -> "tuple[list, dict | None]":
    """(argv, env) for a child process. When argv[0] is this interpreter's
    `sys.executable` and that is a Windows venv launcher: argv[0] becomes
    `sys._base_executable` and env gains `__PYVENV_LAUNCHER__ =
    sys.executable` (env None meaning this process's environment), as
    multiprocessing does. Any other Windows child: env without that
    variable. POSIX: both unchanged."""
    argv = list(argv)
    if not WINDOWS:
        return argv, env
    child_env = dict(os.environ if env is None else env)
    for key in [k for k in child_env if k.upper() == LAUNCHER_VARIABLE]:
        del child_env[key]
    if argv and in_windows_venv() and _same(argv[0], sys.executable):
        argv[0] = sys._base_executable
        child_env[LAUNCHER_VARIABLE] = sys.executable
    return argv, child_env
