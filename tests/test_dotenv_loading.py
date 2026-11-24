"""`.env` is read by the thing that reads the variable.

THE DEFECT, MEASURED
    `.env` contained `NEXUS_API_TOKEN=PK_dK…` and the server announced
    "NEXUS_API_TOKEN not set", generated its own, and 401'd the file's value.
    The loader existed — twice, at `start.py:177` and `start.py:519` — and
    nothing under `backend/` had ever imported `dotenv`. So the file was read
    by the launcher and ignored by the application, and every launch that was
    not `start.py`'s happy path got nothing at all. Including the manual
    command `start.py` itself prints.

THE DEFECT IN THE FIRST DRAFT OF THIS FILE, WHICH IS THE SHARPER LESSON
    Every test here wrote a `.env` into a temp cwd and asserted the server
    served it. That passes only on a machine with no `.env` in the repo root —
    a sandbox — because the repo root is consulted FIRST. On the operator's
    box, where the real file lives, two failed.

    A third was worse: `test_with_no_dotenv_the_server_generates_its_own`
    PASSED there, and passed for the wrong reason. It asserted the served
    token was not one of two fixture strings and was at least 32 characters —
    and the operator's real 43-character token satisfies both. It reported
    "the server generated a token" while the server was serving their file.
    A test that cannot fail is worse than no test, and this one could not fail
    on the machine it most needed to run on.

    So the tests now either CONTROL both locations (the pure ones, which need
    no subprocess) or ACCOUNT for precedence by reading what the repo root
    actually says (the end-to-end ones). Nothing here assumes the repo is
    clean.

WHY SOME TESTS USE SUBPROCESSES
    `conftest.py` imports `backend.server` before any test runs, and the load
    happens at import. Re-importing in-process hits `sys.modules` and measures
    nothing — the same reason `test_import_purity.py` spawns interpreters.
    The subprocess tests prove the WIRING (that import really loads); the pure
    tests prove the RULE.
"""
from __future__ import annotations

import ast
import os
from tests import _proc
import sys
from pathlib import Path

import pytest

from backend.server import env_file_candidates, load_env_files

ROOT = Path(__file__).resolve().parent.parent
REPO_ENV = ROOT / ".env"

PROBE = (
    "import sys\n"
    f"sys.path.insert(0, r{str(ROOT)!r})\n"
    "from backend.server import active_token\n"
    "print(active_token())\n"
)

FILE_TOKEN = "token-from-the-cwd-dotenv-file"
ENV_TOKEN = "token-from-the-real-environment"
# Never NEXUS_API_TOKEN: the pure tests below load real files into this
# process's environment, and clobbering the suite's token would break every
# test that runs after them.
PROBE_KEY = "NEXUS_DOTENV_PRECEDENCE_PROBE"


@pytest.fixture(autouse=True)
def _default_discovery(monkeypatch):
    """This whole file is about DEFAULT `.env` discovery.

    conftest sets `NEXUS_ENV_ROOT` so the suite never reads the operator's
    file, and that override deliberately suppresses the cwd candidate — which
    is exactly what the precedence tests below exist to exercise. They run
    without it; the subprocess helpers pop it from the child env for the same
    reason. `test_the_env_root_override_*` re-enables it on purpose.
    """
    monkeypatch.delenv("NEXUS_ENV_ROOT", raising=False)


def repo_env_value(key: str) -> str | None:
    """What the repo-root `.env` says about `key`, or None.

    Parsed with python-dotenv itself rather than by hand, so this reads the
    file the same way the loader does — quoting, `export `, comments and all.
    THIS is what makes the end-to-end assertions precedence-aware instead of
    assuming a clean repo.
    """
    if not REPO_ENV.is_file():
        return None
    from dotenv import dotenv_values
    return dotenv_values(REPO_ENV).get(key)


def _run(tmp_path: Path, env_extra: dict | None = None) -> str:
    """A fresh interpreter, rooted in `tmp_path`, printing the served token."""
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "NEXUS_API_TOKEN"}
    # conftest points NEXUS_ENV_ROOT at an empty sandbox so the suite never
    # reads the operator's `.env`. These cases test DEFAULT discovery — repo
    # root, then cwd — so the child must not inherit it, or there is nothing
    # left to discover.
    env.pop("NEXUS_ENV_ROOT", None)
    env.update(env_extra or {})
    # M.4 — explicit encoding and error policy. `text=True` alone decodes the
    # child with the platform default, which is cp1252 on the operator's box.
    proc = _proc.run([sys.executable, str(probe)], cwd=tmp_path, env=env,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
    assert proc.returncode == 0, f"probe failed:\n{proc.stderr[-2000:]}"
    return proc.stdout.strip().splitlines()[-1]


def _write_env(directory: Path, **values) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / ".env"
    p.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return p


# ===========================================================================
# THE RULE — pure, both locations controlled, deterministic on any machine
# ===========================================================================

def test_the_candidate_order_is_repo_root_then_cwd(tmp_path):
    """The order is a decision (see `server.py`), so it is pinned as one."""
    root, cwd = tmp_path / "repo", tmp_path / "elsewhere"
    assert env_file_candidates(root, cwd) == [root / ".env", cwd / ".env"]


def test_the_same_directory_is_not_consulted_twice(tmp_path):
    """Launched from the repo root — the common case — there is one file, and
    loading it twice would be harmless but would make `LOADED_ENV_FILES` lie
    about what was read."""
    assert env_file_candidates(tmp_path, tmp_path) == [tmp_path / ".env"]


def test_the_repo_root_beats_the_cwd(tmp_path, monkeypatch):
    """SENTINEL — the precedence order, explicitly.

    Both files set the same key to different values. Whichever wins is the
    rule, and the rule is that the documented location wins: a cwd file
    silently shadowing the file an operator has been told to edit is how they
    end up debugging a value they cannot find.
    """
    monkeypatch.delenv(PROBE_KEY, raising=False)
    root, cwd = tmp_path / "repo", tmp_path / "elsewhere"
    _write_env(root, **{PROBE_KEY: "from-repo-root"})
    _write_env(cwd, **{PROBE_KEY: "from-cwd"})

    read = load_env_files(env_file_candidates(root, cwd))

    assert [p.parent.name for p in read] == ["repo", "elsewhere"], (
        "both files must be read; only the per-key precedence differs")
    assert os.environ[PROBE_KEY] == "from-repo-root", (
        "the cwd file overrode the repo root — precedence is inverted")


def test_the_cwd_fills_keys_the_repo_root_does_not_set(tmp_path, monkeypatch):
    """The other half, and why the fallback exists at all. If the repo root
    simply won wholesale, a deployment elsewhere could set nothing."""
    monkeypatch.delenv(PROBE_KEY, raising=False)
    monkeypatch.delenv(PROBE_KEY + "_2", raising=False)
    root, cwd = tmp_path / "repo", tmp_path / "elsewhere"
    _write_env(root, **{PROBE_KEY: "from-repo-root"})
    _write_env(cwd, **{PROBE_KEY: "from-cwd", PROBE_KEY + "_2": "cwd-only"})

    load_env_files(env_file_candidates(root, cwd))

    assert os.environ[PROBE_KEY] == "from-repo-root"
    assert os.environ[PROBE_KEY + "_2"] == "cwd-only"


def test_a_real_environment_variable_beats_both_files(tmp_path, monkeypatch):
    """SENTINEL — the precedence that must not change.

    Every `monkeypatch.setenv` in the suite and `conftest.py`'s
    `os.environ.setdefault("NEXUS_API_TOKEN", …)` depend on a real variable
    winning. All three values differ on purpose: equal ones would pass
    whichever source won.
    """
    monkeypatch.setenv(PROBE_KEY, "from-the-environment")
    root, cwd = tmp_path / "repo", tmp_path / "elsewhere"
    _write_env(root, **{PROBE_KEY: "from-repo-root"})
    _write_env(cwd, **{PROBE_KEY: "from-cwd"})

    load_env_files(env_file_candidates(root, cwd))

    assert os.environ[PROBE_KEY] == "from-the-environment", (
        "a file overrode the environment; load_dotenv is not override=False")


def test_a_missing_file_is_skipped_not_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv(PROBE_KEY, raising=False)
    root, cwd = tmp_path / "repo", tmp_path / "elsewhere"
    _write_env(cwd, **{PROBE_KEY: "from-cwd"})          # repo root has none
    read = load_env_files(env_file_candidates(root, cwd))
    assert [p.parent.name for p in read] == ["elsewhere"]
    assert os.environ[PROBE_KEY] == "from-cwd"


# ===========================================================================
# THE WIRING — end to end, and precedence-aware rather than repo-clean
# ===========================================================================

def test_with_no_dotenv_anywhere_the_server_generates_its_own(tmp_path):
    """THE TEST THAT PASSED FOR THE WRONG REASON.

    It used to assert the served token was neither fixture string and was
    32+ characters — which the operator's real 43-character token satisfies.
    It reported "generated" while the server served their file.

    Now it asserts against what the repo root ACTUALLY says, and skips only
    in the one case where the claim is untestable from here: the repo root
    defines the key, so no temp cwd can produce a machine with no `.env`.
    """
    from_repo = repo_env_value("NEXUS_API_TOKEN")
    if from_repo:
        pytest.skip(
            "the repo-root .env sets NEXUS_API_TOKEN, so 'no .env anywhere' "
            "cannot be reached without moving the operator's file. The "
            "precedence-aware test below covers this machine instead.")
    served = _run(tmp_path)
    assert served not in {FILE_TOKEN, ENV_TOKEN}
    assert len(served) >= 32, f"that does not look generated: {served!r}"


def test_the_served_token_is_the_one_precedence_says_it_should_be(tmp_path):
    """SENTINEL 1, end to end, on any machine.

    A `.env` in the cwd carries a known token. The expectation is derived
    from what the repo root actually says — its value if it sets the key,
    otherwise the cwd's. On a clean checkout this asserts the cwd file is
    served; on the operator's box it asserts THEIR file's token is served,
    which is literally the bug they reported.
    """
    _write_env(tmp_path, NEXUS_API_TOKEN=FILE_TOKEN)
    expected = repo_env_value("NEXUS_API_TOKEN") or FILE_TOKEN
    served = _run(tmp_path)
    assert served == expected, (
        f"served {served!r}; the repo root says "
        f"{repo_env_value('NEXUS_API_TOKEN')!r} and the cwd says "
        f"{FILE_TOKEN!r}, so precedence says {expected!r}")


def test_the_dotenv_token_survives_into_a_child_process(tmp_path):
    """`start.py` execs uvicorn, and uvicorn `--reload` respawns the app.
    A load that reached only the first process would look fixed and fail on
    the first reload — exactly how the generated token behaved."""
    _write_env(tmp_path, NEXUS_API_TOKEN=FILE_TOKEN)
    expected = repo_env_value("NEXUS_API_TOKEN") or FILE_TOKEN
    inner = tmp_path / "inner.py"
    inner.write_text(PROBE, encoding="utf-8")
    outer = tmp_path / "outer.py"
    outer.write_text(
        "import os, sys\n"
        f"os.execv(sys.executable, [sys.executable, r{str(inner)!r}])\n",
        encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "NEXUS_API_TOKEN"}
    # conftest points NEXUS_ENV_ROOT at an empty sandbox so the suite never
    # reads the operator's `.env`. These cases test DEFAULT discovery — repo
    # root, then cwd — so the child must not inherit it, or there is nothing
    # left to discover.
    env.pop("NEXUS_ENV_ROOT", None)
    proc = _proc.run([sys.executable, str(outer)], cwd=tmp_path, env=env,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == expected


def test_a_real_environment_variable_beats_the_files_end_to_end(tmp_path):
    """SENTINEL 2, end to end. Unlike the pure test above this one runs the
    real import path, and unlike the first draft it is true on any machine —
    the environment wins over both files, whatever they contain."""
    _write_env(tmp_path, NEXUS_API_TOKEN=FILE_TOKEN)
    served = _run(tmp_path, {"NEXUS_API_TOKEN": ENV_TOKEN})
    assert served == ENV_TOKEN
    assert served != FILE_TOKEN
    assert served != repo_env_value("NEXUS_API_TOKEN")


# ===========================================================================
# The contract, in the source
# ===========================================================================

def test_every_load_is_declared_non_overriding_and_names_its_path():
    """AST, because the behavioural tests pass if `override` is absent AND
    python-dotenv's default never changes. The keyword is the contract.

    Also: never the bare form, whose `find_dotenv()` walks up from the CALLING
    MODULE's directory rather than the cwd. Measured — the first draft used it
    and a `.env` in the child's own working directory was ignored.
    """
    # U.2 — the implementation moved to `backend/env.py` so scripts can import
    # it without importing `backend.server` (H.2). Derived from the function's
    # own module rather than hardcoded, so it follows another move.
    import backend.env
    source = Path(backend.env.__file__)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "load_dotenv"]
    assert calls, f"{source.name} no longer loads .env at all"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "override" in kw, (
            f"load_dotenv() at line {call.lineno} does not state its precedence")
        assert isinstance(kw["override"], ast.Constant), call.lineno
        assert kw["override"].value is False, (
            f"load_dotenv() at line {call.lineno} overrides the environment")
        assert call.args, (
            f"load_dotenv() at line {call.lineno} names no path; the bare form "
            f"searches from the module's directory, which is neither the "
            f"documented location nor the cwd")


def test_the_loader_lives_outside_backend_server():
    """U.2 — scripts need the same loader and may not import `backend.server`
    (H.2 measured that migrating the operator's live kb.db). One
    implementation, in a module that constructs nothing."""
    import backend.env
    import backend.server
    assert backend.server.load_env_files is backend.env.load_env_files
    assert Path(backend.env.__file__).name == "env.py"


def test_the_load_runs_at_import():
    """The whole point. A loader nothing calls is the defect this fixed."""
    tree = ast.parse((ROOT / "backend" / "server.py").read_text(encoding="utf-8"))
    module_level = [n for n in tree.body if isinstance(n, ast.Assign)
                    and isinstance(n.value, ast.Call)
                    and isinstance(n.value.func, ast.Name)
                    and n.value.func.id == "load_env_files"]
    assert module_level, (
        "load_env_files() is never called at module scope, so importing "
        "backend.server no longer loads .env")


def test_a_missing_dotenv_package_degrades_rather_than_stops():
    """The guard is `try/except ImportError`, and it must stay one: the app
    runs on real environment variables, and an install without python-dotenv
    must degrade rather than fail to start."""
    import backend.env
    tree = ast.parse(Path(backend.env.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "load_env_files")
    handlers = [h for n in ast.walk(fn) if isinstance(n, ast.Try)
                for h in n.handlers]
    assert handlers, "the dotenv import is not guarded"
    names = {h.type.id for h in handlers if isinstance(h.type, ast.Name)}
    assert names == {"ImportError"}, (
        f"the guard catches {names}; a broad except would hide a malformed "
        f".env as though the package were missing")


def test_python_dotenv_is_a_declared_requirement():
    """It was a dependency of `start.py`'s own checklist and of nothing else.
    `backend/server.py` imports it at module load now."""
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "python-dotenv" in req


# ===========================================================================
# start.py's printed command and its actual launch agree
# ===========================================================================

def test_start_py_manual_command_matches_what_it_launches():
    """THE DEFECT THAT PRODUCED THE FALSE ADVICE.

    `start.py` read `.env` at step 4, then handed the operator a command to
    run instead, and that command discarded the file, because the load lived
    in the launcher. Answering "no" to "Start NEXUS now?" put you in a
    different world from answering "yes", silently.

    Both worlds now load `.env`, because `backend/server.py` does. But the two
    must keep AGREEING about how the server is started. Before W1 this read
    two literals out of `launch()`'s AST (the exec's argv and `manual_cmd`),
    and they could only agree by being edited together. W1 builds the
    printed command FROM the argv that is launched (`start.manual_command`),
    so this asserts the behaviour: what is printed parses back to exactly
    what is run, under --dev and without it, and `--reload` is in it only
    under --dev.
    """
    import shlex
    import start
    for dev in (False, True):
        argv = start.launch_argv("127.0.0.1", 8000, dev=dev)
        printed = start.manual_command(argv)
        parsed = (shlex.split(printed, posix=False) if os.name == "nt"
                  else shlex.split(printed))
        assert [t.strip('"') for t in parsed] == argv, (
            f"start.py launches {argv} and tells the operator to run "
            f"{printed!r}. The printed command is advice for a different "
            f"program.")
        assert ("--reload" in argv) is dev, "--reload outside --dev"


def test_start_py_writes_the_key_its_banner_tells_you_to_set(tmp_path, monkeypatch):
    """`grep NEXUS_API_TOKEN start.py` used to return nothing, while
    `server.py` printed "Set NEXUS_API_TOKEN in .env". A `.env` created by
    `start.py` did not contain the key.

    Written EMPTY, never generated — a launcher that invents a secret and
    writes it to disk has made a decision nobody asked it to make. Asserted on
    the value too, so a future "helpful" default fails here. Since W1 the
    created file is a copy of `.env.example` (the old `defaults` dict wrote
    the four keys nothing reads and rewrote an existing file), so the key and
    its empty value come from there; this drives the creation for real.
    """
    import start
    answers = iter(["y", "1"])
    monkeypatch.setattr("builtins.input", lambda *_a: next(answers))
    created = start.ensure_env_file(tmp_path / ".env", ROOT / ".env.example")
    assert created is not None
    from dotenv import dotenv_values
    values = dotenv_values(created)
    assert "NEXUS_API_TOKEN" in values, (
        "start.py still writes a .env without the key its banner names")
    assert values["NEXUS_API_TOKEN"] == "", (
        "start.py writes a non-empty NEXUS_API_TOKEN — it is generating a "
        "secret into a file, which is a decision, not a convenience")


# ===========================================================================
# The banner says what is true
# ===========================================================================

def test_the_banner_says_a_restart_is_needed_and_that_reload_regenerates(
        monkeypatch, capsys):
    """The old text — "Set NEXUS_API_TOKEN in .env to fix it across restarts"
    — named a file nothing read, and promised survival across the very
    restarts (`--reload`) that regenerate the token."""
    import backend.server as server

    monkeypatch.delenv("NEXUS_API_TOKEN", raising=False)
    monkeypatch.setattr(server, "_SESSION_TOKEN", None)
    monkeypatch.setattr(server, "_TOKEN_BANNER_SHOWN", False)
    token = server.active_token()
    banner = capsys.readouterr().out

    assert token in banner
    # Whitespace-collapsed: the banner is hand-wrapped, and a test that broke
    # on a line break would be asserting about the wrapping.
    low = " ".join(banner.lower().split())
    assert "--reload" in low, "the banner does not warn that reload regenerates"
    assert "restart" in low, "the banner does not say a restart is needed"
    assert "nexus_api_token" in low
    assert ".env" in low
    assert "environment variable" in low, (
        "the banner names only the file; a real environment variable is the "
        "other mechanism and the one that wins")


# ---------------------------------------------------------------------------
# NEXUS_ENV_ROOT — the seam that makes this file runnable on a configured box
# ---------------------------------------------------------------------------

def test_the_env_root_override_relocates_discovery(tmp_path, monkeypatch):
    """A deployment whose config does not live beside `backend/` can say so,
    and so can the suite. Without it the root is derived from `__file__` and
    cannot be moved, so `test_dotenv_settings_take_effect` could only isolate
    on a machine with no `.env` — which is every machine except a working
    one."""
    monkeypatch.setenv("NEXUS_ENV_ROOT", str(tmp_path))
    assert env_file_candidates() == [tmp_path / ".env"]


def test_the_override_suppresses_the_cwd_fallback(tmp_path, monkeypatch):
    """Once something has SAID where config lives, consulting a second
    location would defeat the saying — and for the suite it would mean the
    operator's repo-root file is read anyway whenever pytest runs from the
    repo root, which is how everyone runs it."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("NEXUS_ENV_ROOT", str(other))
    assert env_file_candidates(cwd=tmp_path) == [other / ".env"]


def test_without_the_override_precedence_is_exactly_as_before(tmp_path, monkeypatch):
    """NON-VACUITY for the two above: the override must be the only thing that
    changes the shape, or they prove nothing about it."""
    monkeypatch.delenv("NEXUS_ENV_ROOT", raising=False)
    root, cwd = tmp_path / "root", tmp_path / "cwd"
    assert env_file_candidates(root, cwd) == [root / ".env", cwd / ".env"]


def test_an_empty_override_is_ignored(monkeypatch):
    """`NEXUS_ENV_ROOT=` in a file must not relocate discovery to the process
    working directory — an empty knob means unset, as everywhere else."""
    monkeypatch.setenv("NEXUS_ENV_ROOT", "   ")
    assert env_file_candidates()[0] == ROOT / ".env"
