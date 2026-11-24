"""W4b part 2 (run/specs/W4b.md): a child the suite starts as this
interpreter IS this interpreter, with these packages.

The operator's merge gate runs the suite as `.venv\\Scripts\\python.exe -m
pytest`. On Windows that file is the virtual environment's launcher: it
reads `pyvenv.cfg` and starts the base interpreter as ITS child, so a test
that started `sys.executable` started two processes, and the pid the test
held was the launcher's. Measured on CI's windows runner in a venv
(RUN_LOG W4b, the establish, `ci` run 78): I1 as it stood before the fix
(a plain Popen of `sys.executable`) failed with "the pid the test holds
(5732) is not the child's own (6676; its parent 5732)", and so did W2's
four pid checks and the bundle smoke's S7 and S8; the child's prefix and
site directories were the venv's, and a probe in the base interpreter's
site directories logged no child that had not asked for the base
interpreter by name.

I1  A child started as `sys.executable` through tests/_proc.py answers with
    the pid the test holds, this interpreter's executable, prefixes,
    standard library and site directories, and the same user-site setting.
I2  The rule itself, on every platform (`scripts/_interpreter.py` with its
    WINDOWS constant planted): in a Windows venv, `sys.executable` becomes
    `sys._base_executable` with `__PYVENV_LAUNCHER__`; any other first
    argument is left, and loses that variable; outside Windows nothing
    changes.
I3  The companion, where the launcher exists (a Windows venv; skipped by
    name elsewhere): with the rule taken out of tests/_proc.py, I1's check
    fails on the pid, the consequence the operator's gate met.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from scripts import _interpreter
from tests import _proc

FACTS = r"""
import json, os, site, sys, sysconfig
def norm(p):
    return os.path.normcase(os.path.abspath(p)) if p else p
def facts():
    return {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "executable": norm(sys.executable),
        "base_executable": norm(getattr(sys, "_base_executable", None)),
        "prefix": norm(sys.prefix),
        "base_prefix": norm(sys.base_prefix),
        "exec_prefix": norm(sys.exec_prefix),
        "stdlib": norm(sysconfig.get_paths()["stdlib"]),
        "purelib": norm(sysconfig.get_paths()["purelib"]),
        "platlib": norm(sysconfig.get_paths()["platlib"]),
        "site_packages": [norm(p) for p in site.getsitepackages()],
        "user_site": bool(site.ENABLE_USER_SITE),
        "version": sys.version,
        "launcher_env": os.environ.get("__PYVENV_LAUNCHER__"),
    }
"""
PROBE = FACTS + "print(json.dumps(facts()))\n"

COMPARED = ("executable", "base_executable", "prefix", "base_prefix", "exec_prefix", "stdlib", "purelib",
            "platlib", "site_packages", "user_site", "version")


def _here() -> dict:
    scope: dict = {}
    exec(compile(FACTS, "<facts>", "exec"), scope)
    return scope["facts"]()


def _identity_problems() -> list[str]:
    here = _here()
    child = _proc.Child([sys.executable, "-c", PROBE], label="the identity probe", stdout=_proc.PIPE,
                        stderr=_proc.PIPE, text=True, encoding="utf-8", errors="replace")
    out, err = child.communicate(timeout=120)
    facts = json.loads(out)
    print("I1 here:", json.dumps(here, sort_keys=True))
    print(f"I1 child (Child.pid {child.pid}):", json.dumps(facts, sort_keys=True))
    problems = []
    if facts["pid"] != child.pid:
        problems.append(f"the pid the test holds ({child.pid}) is not the child's own ({facts['pid']}; its parent "
                        f"{facts['ppid']}, this process {os.getpid()}): the child is a second process behind a launcher")
    differs = {k: (here[k], facts[k]) for k in COMPARED if here[k] != facts[k]}
    if differs:
        problems.append(f"the child is not this interpreter with these packages: {differs}")
    if err:
        problems.append(f"the child wrote to stderr: {err!r}")
    return problems


def test_i1_a_child_started_as_sys_executable_is_this_interpreter_with_these_packages():
    problems = _identity_problems()
    print(f"I1 ({sys.platform}, Windows venv: {_interpreter.in_windows_venv()}): {problems or 'the same interpreter'}")
    assert not problems, "\n".join(problems)


def test_i2_the_rule_on_every_platform(monkeypatch, tmp_path):
    venv = str(tmp_path / ".venv" / "Scripts" / "python.exe")
    base = str(tmp_path / "Python311" / "python.exe")
    monkeypatch.setattr(sys, "executable", venv)
    monkeypatch.setattr(sys, "_base_executable", base, raising=False)
    monkeypatch.setattr(_interpreter, "WINDOWS", True)
    env = {"PATH": "x", "__PYVENV_LAUNCHER__": "stale"}
    argv, got = _interpreter.this_interpreter([venv, "-c", "pass"], env)
    assert argv == [base, "-c", "pass"] and got["__PYVENV_LAUNCHER__"] == venv and got["PATH"] == "x", (argv, got)
    assert env == {"PATH": "x", "__PYVENV_LAUNCHER__": "stale"}, "the caller's environment was changed"
    argv, got = _interpreter.this_interpreter([base, "-c", "pass"], env)       # the base asked for by name
    assert argv == [base, "-c", "pass"] and "__PYVENV_LAUNCHER__" not in got, (argv, got)
    argv, got = _interpreter.this_interpreter(["git", "status"], {"__pyvenv_launcher__": "stale", "A": "1"})
    assert argv == ["git", "status"] and got == {"A": "1"}, got
    # The same path in another case is the same file where the platform's
    # paths fold case (Windows; multiprocessing compares with normcase too).
    folds = os.path.normcase(venv.upper()) == os.path.normcase(venv)
    argv, got = _interpreter.this_interpreter([venv.upper(), "-V"], None)
    assert argv[0] == (base if folds else venv.upper()), (folds, argv)
    assert (got.get("__PYVENV_LAUNCHER__") == venv) is folds, got.get("__PYVENV_LAUNCHER__")
    monkeypatch.setattr(sys, "_base_executable", venv)                          # not a venv: the launcher IS python
    argv, got = _interpreter.this_interpreter([venv, "-V"], {"A": "1"})
    assert argv == [venv, "-V"] and got == {"A": "1"}
    monkeypatch.setattr(_interpreter, "WINDOWS", False)
    monkeypatch.setattr(sys, "_base_executable", base)
    planted = {"A": "1", "__PYVENV_LAUNCHER__": "kept"}
    argv, got = _interpreter.this_interpreter([venv, "-V"], planted)
    assert argv == [venv, "-V"] and got is planted, "outside Windows the arguments and environment are as given"


@pytest.mark.skipif(not _interpreter.in_windows_venv(),
                    reason="the launcher exists only in a Windows virtual environment (CI's suite-windows-venv job)")
def test_i3_companion_without_the_rule_the_child_is_a_second_process(monkeypatch):
    monkeypatch.setattr(_proc, "this_interpreter", lambda argv, env=None: (list(argv), env))
    problems = _identity_problems()
    print(f"I3 ({sys.platform}) without the rule: {problems}")
    assert problems and "second process behind a launcher" in problems[0], problems
