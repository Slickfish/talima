"""Shared test configuration.

Package A added bearer-token auth to all state-changing endpoints. The
existing suite exercises endpoint *semantics*, not auth (tests/test_api_auth.py
covers auth itself), so every TestClient authenticates by default with a
deterministic token. Auth tests strip or override the header explicitly to
exercise the 401 paths.

Packet A — the suite must never write to the real `data/` tree.
--------------------------------------------------------------
REGROUND_REPORT §3.2 measured the defect: one suite run appended 8 rows to
`data/calibration_log.jsonl` and one row to `data/kb.db`. Both files are
gitignored, so it was invisible to `git status` from P3 until the audit.

**Why the redirect is still needed (rationale rewritten by Packet J).**

This guard's original justification was import ordering: `backend/server.py`
ran `app = create_app()` at module scope, so *importing* the module built a
full production stack against the real `data/` tree before any fixture could
run, and patching after the import was too late.

**Packet J removed the module-scope call. That justification is now false.**
Importing `backend.server` constructs nothing and touches no file — proven
by `tests/test_import_purity.py` in a fresh interpreter.

**The guard itself is not obsolete, and must not be removed.** Eight test
files still call `create_app()` with no path arguments —
`test_api_auth`, `test_event_recorder`, `test_kairos_telemetry`,
`test_lineage`, `test_phase_memory_column`, `test_sentinels`,
`test_vocabulary`, `test_accrual_integrity` — and every one of those lands on
the relative `data/` defaults. Without this redirect they write to the
operator's real store. `test_accrual_integrity.py` exists precisely to catch
"the next time someone adds a bare `create_app()`".

What changed is only *when* the patching must happen: it no longer has to
precede the import of `backend.server`, because the import no longer
constructs anything. The patches are applied to `__init__` on the class
objects, so every binding of those classes sees them regardless of import
order. The `import backend.server` below is retained — it is now
side-effect-free, and removing it would be unrelated churn.

A stale rationale on a load-bearing guard is how load-bearing guards get
deleted, which is why this paragraph was rewritten in the same commit that
made it stale rather than left for someone to discover.

Constructor defaults are bound at import time (`def __init__(self,
db_path=DEFAULT_DB)`), so patching the `DEFAULT_*` constants would not work;
the class objects themselves must be replaced.

Semantics preserved deliberately: one *session-shared* tmp tree, mirroring
today's shared-`data/` behaviour minus the pollution. Per-test isolation
would change sharing semantics as well as location — two changes at once.
Only paths that resolve under the repo's real `data/` are rewritten;
explicit `tmp_path` arguments are left exactly as the test wrote them.
"""
import hashlib
import os
import pathlib
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_DATA = (REPO_ROOT / "data").resolve()

# Created at import so it exists before any backend module is imported.
SESSION_TMP = Path(tempfile.mkdtemp(prefix="nexus_test_session_"))

# ---------------------------------------------------------------------------
# The suite does not inherit the operator's configuration
# ---------------------------------------------------------------------------
#
# MEASURED: with every documented setting at a plausible deployment value, 47
# tests fail — 39 of them from `NEXUS_RECALIBRATION=off` alone, which is S.2's
# documented hard kill switch. Every rollback the project ships breaks tests
# when it is used. A suite that only passes on an unconfigured machine stops
# working the moment the software is used, which is O.1's
# fixtures-are-constructed-never-accumulated finding wearing different clothes.
#
# This is R.4's defect one layer out. There, `create_services()` defaulted to
# the LIVE provider, so every test that did not think about providers got one.
# Here, every test that does not think about configuration inherits the
# operator's. Same treatment, and for the same stated reason: *a documented
# rule is not a control.*
#
# NOT A TEST-ONLY BYPASS, by R.4's own test — nothing here changes what the
# code under test computes. It removes an input the suite was never meant to
# receive. A test that WANTS a value still sets it with `monkeypatch.setenv`,
# unchanged, and gets it.
#
# DERIVED from docs/CONTRACTS.md's env block, never hand-listed, so a knob is covered
# the day it is documented rather than the day someone remembers.
def _documented_settings() -> list[str]:
    import re
    md = (REPO_ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    try:
        block = md.split("## Env (.env in repo root)")[1].split("```")[1]
    except IndexError:                                   # pragma: no cover
        return []
    return [m.group(1) for m in re.finditer(r"^([A-Z_][A-Z0-9_]*)=", block, re.M)]


_SCRUBBED = sorted(k for k in _documented_settings() if k in os.environ)
for _key in _SCRUBBED:
    del os.environ[_key]

# The repo-root `.env` must not be read either, and scrubbing cannot stop that
# — `load_env_files()` reads the FILE at import of `backend.server`, long after
# this. `NEXUS_ENV_ROOT` points discovery at an empty directory instead, and
# suppresses the cwd fallback that would otherwise find the same file whenever
# pytest is run from the repo root. This is the half that makes
# `test_dotenv_settings_take_effect` runnable on a configured machine, which it
# was not: five of its cases failed on the operator's box purely because their
# working `.env` sets those keys, and the only remedy was deleting their
# configuration. A guard nobody can run clean is a guard that gets muted.
_ENV_ROOT_SANDBOX = SESSION_TMP / "env_root"
_ENV_ROOT_SANDBOX.mkdir(parents=True, exist_ok=True)
os.environ["NEXUS_ENV_ROOT"] = str(_ENV_ROOT_SANDBOX)

os.environ.setdefault("NEXUS_API_TOKEN", "test-token")
# Redirect the calibration accrual before anything can append to it. The
# logger resolves this at call time (calibration_log.log_path()).
os.environ["NEXUS_CALIBRATION_LOG"] = str(SESSION_TMP / "calibration_log.jsonl")
# AO — same discipline for the capture instrument: off by default, but any
# test that enables it must land in the sandbox, not the repo's data/.
os.environ["NEXUS_CITATION_CAPTURE_PATH"] = str(
    SESSION_TMP / "citation_capture.jsonl")
# CP — the fire capture, AO's shape, the same discipline.
os.environ["NEXUS_FIRE_CAPTURE_PATH"] = str(
    SESSION_TMP / "fire_capture.jsonl")

# W1 — the three redirects above are what keep the function writers out of
# the repo's data/. A test module that changes one and does not restore it
# hands every later module its value: `tests/test_just_culture_guard.py`'s
# module fixture did, for NEXUS_CALIBRATION_LOG, until W1. Checked at the end
# of every module (`_redirects_restored_per_module`, below).
REDIRECT_SETTINGS = ("NEXUS_CALIBRATION_LOG", "NEXUS_CITATION_CAPTURE_PATH",
                     "NEXUS_FIRE_CAPTURE_PATH",
                     # W1's verifier, N7: the .env search's redirect too. Only
                     # this file sets it directly; a module that left it
                     # changed would send every later module's .env reads
                     # elsewhere.
                     "NEXUS_ENV_ROOT")


def redirect_snapshot() -> dict:
    return {name: os.environ.get(name) for name in REDIRECT_SETTINGS}


def redirect_changes(before: dict) -> list[str]:
    """The redirect variables whose value differs from `before`."""
    return [f"{name}={os.environ.get(name)!r} (was {value!r})"
            for name, value in before.items() if os.environ.get(name) != value]


def _reroute(p):
    """Rewrite a path that lands under the repo's real data/ into SESSION_TMP.

    Anything else (pytest tmp_path, in-memory markers) passes through
    untouched, so explicit per-test paths keep their meaning.
    """
    if not isinstance(p, (str, Path)):
        return p
    try:
        rel = Path(p).resolve().relative_to(REAL_DATA)
    except (ValueError, OSError):
        return p
    target = SESSION_TMP / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


# ---------------------------------------------------------------------------
# W3b — the Postgres backend fixture (run/specs/W3b.md D7, D12)
# ---------------------------------------------------------------------------
#
# `NEXUS_TEST_STORE=postgres` with `NEXUS_TEST_DATABASE_URL` runs the suite's
# stores on Postgres: every PATH a store class is given (the 151 TripleStore,
# 128 create_services and 139 create_app call sites spell paths) becomes a
# SCHEMA in the one test database, named from the resolved path, so every
# class opened on one path shares a store exactly as the classes opened on
# one file do, and no call site changes. The database's name must end in
# `_test`: the fixture creates and drops schemas in it, and an operator's
# database must never be reachable from here (the suite's isolation is
# path-based, and a URL is not a path: agent B's G-6). The schemas are
# dropped at session end, and any `nx_` schema a crashed run left is dropped
# at session start. These two settings are the fixture's own; they are not
# in docs/CONTRACTS.md's env block and the product never reads them.
POSTGRES_TEST = (os.environ.get("NEXUS_TEST_STORE") or "").strip().lower() == "postgres"
POSTGRES_TEST_URL = (os.environ.get("NEXUS_TEST_DATABASE_URL") or "").strip()
if POSTGRES_TEST:
    from urllib.parse import urlsplit as _urlsplit
    if not POSTGRES_TEST_URL:
        raise RuntimeError("NEXUS_TEST_STORE=postgres needs NEXUS_TEST_DATABASE_URL (a postgresql:// URL "
                           "whose database name ends in _test)")
    _dbname = _urlsplit(POSTGRES_TEST_URL).path.lstrip("/")
    if not _dbname.endswith("_test"):
        raise RuntimeError(f"the Postgres test database must be named *_test, not {_dbname!r}: the suite "
                           f"creates and drops schemas in it (W3b D7)")
elif (os.environ.get("NEXUS_TEST_STORE") or "").strip():
    raise RuntimeError(f"NEXUS_TEST_STORE={os.environ['NEXUS_TEST_STORE']!r} names no backend fixture "
                       f"(sqlite is the default; postgres is the other)")
# The two settings are read once, above, and removed from the environment:
# no subprocess may see them. A script the suite runs opens the SQLite file
# it is given (the URL form is W3b part 3's), and a child pytest session —
# tests/test_w1_conftest_once.py and tests/test_w1_ignore_rules.py spawn
# one — would otherwise run THIS fixture too and drop the parent's schemas
# at its start (the stale-schema sweep in `_postgres_test_schemas`). The
# first full run under the fixture showed it: three child sessions, their
# probes never ran.
for _k in ("NEXUS_TEST_STORE", "NEXUS_TEST_DATABASE_URL"):
    os.environ.pop(_k, None)
_PG_SCHEMAS: set[str] = set()
_PG_SCHEMA_PREFIX = "nx_"

# --- the Postgres run's record of SQLite opens (W3b T12, direction B) -----
#
# Under the Postgres fixture a store that opens on SQLite is one the fixture
# did not reach: a path handed to `backend.store.connect` directly, a store
# built outside the derived wrap, a committed fixture file opened by name.
# Every such open is recorded against the running test; at session end every
# recorded test's module must be acknowledged in
# `tests/_sqlite_only.py:OPENS_SQLITE_UNDER_POSTGRES` (the backend twins and
# the SQLite backend's own tests, which must RUN here), or the session fails
# naming the rest. The population is derived from the run and the list is
# its acknowledgement, so "the Postgres job ran this test on SQLite" is never
# silent. A test that wants the store the fixture redirected, without a
# store class, asks `store_target_for(path)`.
_CURRENT_NODEID: "str | None" = None
_SQLITE_OPENS_UNDER_PG: dict[str, int] = {}

if POSTGRES_TEST:
    from backend.store import sqlite_backend as _sqlite_backend

    _orig_open_path = _sqlite_backend.SQLiteDatabase.open_path.__func__

    def _recording_open_path(cls, path):
        if _CURRENT_NODEID is not None:
            _SQLITE_OPENS_UNDER_PG[_CURRENT_NODEID] = _SQLITE_OPENS_UNDER_PG.get(_CURRENT_NODEID, 0) + 1
        return _orig_open_path(cls, path)

    _sqlite_backend.SQLiteDatabase.open_path = classmethod(_recording_open_path)


def sqlite_opens_unacknowledged() -> dict[str, int]:
    """The tests that opened a SQLite store under the Postgres run whose
    module is not acknowledged in `OPENS_SQLITE_UNDER_POSTGRES`."""
    from tests._sqlite_only import OPENS_SQLITE_UNDER_POSTGRES
    return {nodeid: n for nodeid, n in _SQLITE_OPENS_UNDER_PG.items()
            if nodeid.split("::", 1)[0] not in OPENS_SQLITE_UNDER_POSTGRES}


def _pg_target(p):
    """The Postgres store a path stands for under the fixture: one schema per
    resolved path, created on first open by the backend."""
    from backend.store import StoreTarget
    if not isinstance(p, (str, Path)):
        return p
    schema = _PG_SCHEMA_PREFIX + hashlib.sha1(str(Path(p).resolve()).encode("utf-8")).hexdigest()[:24]
    _PG_SCHEMAS.add(schema)
    return StoreTarget.postgres(POSTGRES_TEST_URL, schema=schema)


def store_target_for(path):
    """The store a path stands for under the running fixture: the path itself
    on SQLite, its schema's target under Postgres. For a test that needs a
    raw connection to a store the fixture redirected (`backend.store.connect(
    store_target_for(p))`), where it used to write `sqlite3.connect(p)`."""
    return _pg_target(path) if POSTGRES_TEST else path


def _store_arg(value, *, store: bool):
    value = _reroute(value)
    if store and POSTGRES_TEST:
        value = _pg_target(value)
    return value


def _guard_path_first(cls, default, param, *, store: bool = False):
    """Guard a constructor whose path is the first positional arg (or `param`).

    Patches `__init__` rather than replacing the class object: the classes
    carry classmethods and class constants (`LineageStore.seal_digest`,
    `NexusArchiveChain.GENESIS_HASH`) that a function wrapper would destroy.
    `store`: the path names a database (W3b: mapped to a Postgres schema
    under the fixture), not a log or a folder.
    """
    orig_init = cls.__init__

    def __init__(self, *args, **kwargs):
        if param in kwargs:
            kwargs[param] = _store_arg(kwargs[param], store=store) if kwargs[param] else _store_arg(default, store=store)
        elif args:
            args = (_store_arg(args[0], store=store),) + args[1:]
        else:
            args = (_store_arg(default, store=store),)
        orig_init(self, *args, **kwargs)

    cls.__init__ = __init__
    return cls


def _guard_kwarg(cls, default, param, *, store: bool = False):
    """Guard a constructor whose path is a keyword arg among other args."""
    orig_init = cls.__init__

    def __init__(self, *args, **kwargs):
        kwargs[param] = _store_arg(kwargs[param], store=store) if kwargs.get(param) else _store_arg(default, store=store)
        orig_init(self, *args, **kwargs)

    cls.__init__ = __init__
    return cls


# --- guard every persistent writer before backend.server is imported ------
#
# DERIVED, NOT LISTED (Packet AC). This section used to name each writer by
# hand, and three escaped it by the identical hole — E.3's HebbianGraphStore,
# S.2's RecalibrationStore, AB's TrailStore — each a module-scope
# create_services() reaching the real data/kb.db before the mutation baseline
# was taken. The list is now derived by AST from the tracked backend
# (tests/_writers.py, four rules, cross-module constant resolution): a new
# CLASS-based writer is wrapped automatically, with no line to remember.
#
# A FUNCTION-based writer — one with no class for the derivation to see —
# still needs its env redirect added by hand beside NEXUS_CALIBRATION_LOG
# above. But forgetting is no longer silent: every module-level data/
# constant in the tracked backend must either reach a derived writer class
# or be acknowledged with a reason in ACKNOWLEDGED_DATA_CONSTANTS
# (tests/_writers.py), so the twelfth writer's constant FAILS the suite
# until someone answers for it. The comment you are reading is a
# description of that ratchet, not the control itself.
#
# A derived writer that cannot be wrapped FAILS THE WHOLE SUITE, loudly,
# naming the class and the rule that found it — silently skipping is the
# original hole with better manners. The residual shape a wrapper cannot
# guard — a writer constructed at its own module's scope, which runs at
# import before any wrap exists — is forbidden by AST
# (tests/test_derived_writer_guard.py).
import importlib  # noqa: E402
import sys as _sys  # noqa: E402

_sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _writers import derive_writers  # noqa: E402
from _worktree import compare as _wt_compare, describe as _wt_describe, porcelain as _wt_porcelain  # noqa: E402

# B-44 — the worktree sentinel's snapshot, taken at session start, before any
# test runs. Compared against at teardown (below); never against a clean
# tree, because the operator keeps untracked files deliberately.
WORKTREE_BASELINE = _wt_porcelain()

for _wname, _winfo in sorted(derive_writers().items()):
    try:
        _wmod = importlib.import_module(_winfo["module"])
        _wcls = getattr(_wmod, _wname)
    except Exception as _we:
        raise RuntimeError(
            f"derived persistent writer {_wname!r} "
            f"(rules {sorted(_winfo['rules'])}, module {_winfo['module']}) "
            f"could not be wrapped: {type(_we).__name__}: {_we}. The suite "
            f"must not run with an unguarded writer.") from _we
    _wtarget = SESSION_TMP / pathlib.Path(_winfo["default_path"]).relative_to("data")
    # W3b — a writer whose default is a database file is a STORE (its path
    # maps to a Postgres schema under the fixture); a log or a folder is not.
    _wstore = str(_winfo["default_path"]).endswith(".db")
    if _winfo["first_positional"]:
        _guard_path_first(_wcls, _wtarget, _winfo["param"], store=_wstore)
    else:
        _guard_kwarg(_wcls, _wtarget, _winfo["param"], store=_wstore)

# W2 — the single-writer lock is a FUNCTION writer (`backend/store_lock.py`
# writes `<kb.db>.lock` and `<kb.db>.lock.log` beside the store it guards),
# which the class derivation above cannot see. Its path is the store's kb.db,
# so it is rerouted exactly as the stores are, before backend.server binds the
# name: without this, `create_services()` with no paths locked the real
# `data/kb.db.lock` while its stores opened under SESSION_TMP (the data/
# sentinel named it on the first run).
import backend.store_lock as _store_lock  # noqa: E402

_orig_store_lock_acquire = _store_lock.acquire


def _rerouted_store_lock_acquire(db_path, *args, **kwargs):
    return _orig_store_lock_acquire(_reroute(db_path), *args, **kwargs)


_store_lock.acquire = _rerouted_store_lock_acquire

# Now safe: server's `from … import …` binds the guarded constructors, so its
# module-level `app = create_app()` never touches real data/.
from fastapi.testclient import TestClient  # noqa: E402
import backend.server as _server  # noqa: E402

_orig_init = TestClient.__init__


def _authed_init(self, *args, **kwargs):
    _orig_init(self, *args, **kwargs)
    self.headers.setdefault(
        "Authorization", f"Bearer {os.environ['NEXUS_API_TOKEN']}"
    )


TestClient.__init__ = _authed_init


# ---------------------------------------------------------------------------
# Packet R.4 — the suite does not talk to a model daemon it was not given
# ---------------------------------------------------------------------------
#
# R.0 measured 145 outbound requests to localhost:11434 across one suite run,
# ~2ms each here and ~2.45s each on the operator's Windows box. 82 of them were
# `/api/embeddings`, issued by KAIROS's dedup scan through the vector store.
#
# My R.0 report attributed those to tests whose injected stub provider was
# being bypassed by `chroma.py`'s module-global `PROVIDER`. **That was wrong,
# and R.4 measured it wrong.** Threading `services.provider` into `VectorStore`
# removed one of the 82. The other 81 come from tests that inject no provider
# at all — there was no stub to bypass. The bypass was real and worth fixing
# (Fix 0 says "NexusServices.provider per-app instance for test isolation" and
# one layer opted out), but it was not what cost the seconds.
#
# What costs the seconds is that `create_services()` defaults to the LIVE
# provider, so every test that does not think about providers gets one and
# every embedding it triggers is a refused connection. This is the same shape
# as the path guard above — a default that lands on the operator's real world —
# and it gets the same treatment, for the same stated reason: *a documented
# rule is not a control.*
#
# NOT A TEST-ONLY BYPASS. Packet R rejected those: "the suite is fast because
# it stopped testing startup". The distinction is that nothing here changes
# what the code under test computes. Today a refused `get_embedding` raises,
# `chroma.py` catches it, and falls back to `_offline_embed`. This provider
# returns `_offline_embed` directly. Same vector, same ranking, same asserts —
# only the wait is gone. `list_models() -> []` and `health() -> False` are
# likewise exactly what a refused daemon yields.
#
# The escape hatch is explicit and one keyword wide: a test that passes its own
# `provider=` keeps it, and anything constructing `OllamaProvider` directly
# (test_phase_1's live-Ollama tests, the Fix 0 backend-switch tests) is
# untouched — this guards `create_services`, not `get_provider`.

from backend.vector.embeddings import FULL_DIM, _offline_embed  # noqa: E402


class OfflineTestProvider:
    """An LLMProvider that reproduces a down daemon without the socket."""

    def __init__(self):
        self.calls = {"stream_chat": 0, "get_embedding": 0,
                      "list_models": 0, "health": 0}

    async def stream_chat(self, model, messages, num_ctx=8192):
        # Raises, because that is what the live provider does with nothing
        # listening. Returning tokens here would invent a behaviour no test
        # has ever seen.
        #
        # S.4 — this comment used to end "Measured: no test reaches this
        # path", and that stopped being true the moment a test needed a whole
        # turn (tests/test_assertion_alert.py). It reaches this path, reads
        # the message, and passes its own provider — which is the escape hatch
        # working, not a hole in it. A stale measurement on a guard is how
        # guards get removed.
        self.calls["stream_chat"] += 1
        raise ConnectionRefusedError(
            "OfflineTestProvider: no daemon (tests/conftest.py, Packet R.4). "
            "Pass provider= to create_services() if this test needs one.")
        yield ""                                    # pragma: no cover

    async def get_embedding(self, text: str) -> list[float]:
        self.calls["get_embedding"] += 1
        return _offline_embed(text, dim=FULL_DIM)

    async def list_models(self) -> list[str]:
        self.calls["list_models"] += 1
        return []

    async def health(self) -> bool:
        self.calls["health"] += 1
        return False



@pytest.fixture
def recalibration_on(monkeypatch):
    """Declares that this test needs S.2's recalibration ENABLED.

    Requested by name rather than inherited. Before the scrub above, 39 tests
    passed because `NEXUS_RECALIBRATION` happened to be unset and its compiled
    default is `on`; with the operator's kill switch engaged — the documented,
    supported, cautious-first-deployment configuration — every one of them
    failed. None of them said it depended on the switch.

    Two kinds of test request this, and the docstrings distinguish them:

      SUBJECT   the test is about recalibration, so `on` is definitional —
                `test_governed_adaptation*`, `test_learning_provenance`.
      VEHICLE   the test is about something else and uses recalibration
                records as the payload it needs to exist —
                `test_governance_cadence` (seal cadence),
                `test_law3_discoverability` (endpoint discoverability),
                `test_packet_u` (seal reporting). Those 13 would be better
                served by a fixture built on lineage records, which are not
                behind a kill switch. Queued, not done.

    A test that then flips the switch OFF mid-body still can: this only sets
    the starting position, and `monkeypatch.setenv` inside the body wins.
    `test_the_kill_switch_stops_every_move` does exactly that.
    """
    monkeypatch.setenv("NEXUS_RECALIBRATION", "on")


_orig_create_services = _server.create_services


def _offline_by_default(*args, **kwargs):
    if kwargs.get("provider") is None:
        kwargs["provider"] = OfflineTestProvider()
    return _orig_create_services(*args, **kwargs)


_server.create_services = _offline_by_default


# ---------------------------------------------------------------------------
# Sentinel: the permanent invariant. Catches any future writer, not just the
# ones known today.
# ---------------------------------------------------------------------------

def guarded_real_paths() -> dict:
    """W1 — derived, never listed: every class writer's default path and
    every acknowledged function writer's constant, the population the writer
    ratchet (`tests/_writers.py`) already derives. The hand list this replaced
    named six paths and missed `data/citation_capture.jsonl` and
    `data/fire_capture.jsonl`, both gitignored, so a local run that wrote
    either passed every local sentinel."""
    from _writers import unconsumed_data_constants
    paths = {info["default_path"] for info in derive_writers().values()}
    paths |= set(unconsumed_data_constants().values())
    return {p: REPO_ROOT / p for p in sorted(paths)}


GUARDED_REAL_PATHS = guarded_real_paths()


def fingerprint(path: Path):
    """(size, mtime_ns, md5) for a file; recursive (name, size, mtime_ns) for
    a directory. mtime is included deliberately: the chroma leak changed only
    the mtime, and a size-only fingerprint would have missed it.

    None when absent — a missing path is skipped cleanly so CI without a
    populated data/ still runs.
    """
    if not path.exists():
        return None
    if path.is_dir():
        return sorted(
            # M.5 — posix keys. Compared only against a sibling fingerprint
            # so the separator cancels out today, but a baseline taken on
            # one platform and read on another would not, and the rule is
            # the class rather than the cases that happen to hurt.
            (c.relative_to(path).as_posix(), c.stat().st_size,
             c.stat().st_mtime_ns)
            for c in path.rglob("*") if c.is_file()
        )
    blob = path.read_bytes()
    return (len(blob), path.stat().st_mtime_ns, hashlib.md5(blob).hexdigest())


@pytest.fixture(scope="session")
def session_tmp() -> Path:
    return SESSION_TMP


@pytest.fixture(scope="session")
def real_data_baseline() -> dict:
    """Fingerprints taken once, at session start."""
    return {name: fingerprint(p) for name, p in GUARDED_REAL_PATHS.items()}


@pytest.fixture(scope="session", autouse=True)
def _assert_real_data_untouched(real_data_baseline):
    """Fail loudly at session teardown if the suite mutated real data/."""
    yield
    changed = [
        f"  {name}: {real_data_baseline[name]!r} -> {fingerprint(p)!r}"
        for name, p in GUARDED_REAL_PATHS.items()
        if fingerprint(p) != real_data_baseline[name]
    ]
    assert not changed, (
        "TEST SUITE MUTATED REAL DATA (Packet A invariant violated):\n"
        + "\n".join(changed)
    )


@pytest.fixture(scope="session", autouse=True)
def _postgres_test_schemas():
    """W3b — under the Postgres fixture: drop any `nx_` schema a crashed run
    left in the test database at session start, and every schema this
    session created at its end."""
    if POSTGRES_TEST:
        import psycopg
        with psycopg.connect(POSTGRES_TEST_URL, autocommit=True) as c:
            stale = [r[0] for r in c.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE %s",
                (_PG_SCHEMA_PREFIX + "%",))]
            for s in stale:
                c.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')
        print(f"\nW3b Postgres fixture: {POSTGRES_TEST_URL.rsplit('@', 1)[-1]}; {len(stale)} stale schema(s) dropped")
    yield
    if POSTGRES_TEST and _PG_SCHEMAS:
        import psycopg
        with psycopg.connect(POSTGRES_TEST_URL, autocommit=True) as c:
            for s in sorted(_PG_SCHEMAS):
                c.execute(f'DROP SCHEMA IF EXISTS "{s}" CASCADE')
        print(f"\nW3b Postgres fixture: {len(_PG_SCHEMAS)} schema(s) dropped")
    if POSTGRES_TEST:
        assert_sqlite_opens_acknowledged()


def assert_sqlite_opens_acknowledged() -> None:
    """T12 direction B's consequence: every SQLite store opened under the
    Postgres run is in an acknowledged module, or the session fails naming
    the rest (called at session end; tests/test_w3b_dialect.py S plants one)."""
    opened = sqlite_opens_unacknowledged()
    modules = {n.split("::", 1)[0] for n in _SQLITE_OPENS_UNDER_PG}
    print(f"\nW3b Postgres fixture: {sum(_SQLITE_OPENS_UNDER_PG.values())} SQLite store(s) opened in "
          f"{len(_SQLITE_OPENS_UNDER_PG)} test(s) of {len(modules)} module(s); {len(opened)} unacknowledged")
    assert not opened, (
        "under the Postgres run these tests opened a SQLite store and their module is not acknowledged in "
        "tests/_sqlite_only.py (OPENS_SQLITE_UNDER_POSTGRES, with a reason; or skip it there by module or by "
        "the sqlite_only marker):\n  " + "\n  ".join(f"{k} (opened {v})" for k, v in sorted(opened.items())))


@pytest.fixture(autouse=True)
def _name_the_running_test(request):
    """W3b — the running test's id, for the Postgres run's record of SQLite
    opens (T12, direction B)."""
    global _CURRENT_NODEID
    _CURRENT_NODEID = request.node.nodeid
    yield
    _CURRENT_NODEID = None


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "sqlite_only(reason): W3b — a test about SQLite's own behaviour (its SQL as the oracle, "
                   "its files, its barriers), skipped by name under the Postgres fixture")


def pytest_collection_modifyitems(config, items):
    """W3b — under the Postgres fixture the SQLite-only tests are skipped by
    name: whole modules from the pinned list (`tests/_sqlite_only.py`), single
    tests by the `sqlite_only` marker. The counts are printed: the
    subtraction is declared, never silent (law 5)."""
    if not POSTGRES_TEST:
        return
    from tests._sqlite_only import SQLITE_ONLY_MODULES
    by_module, by_marker = 0, 0
    for item in items:
        rel = pathlib.Path(str(item.fspath)).resolve().relative_to(REPO_ROOT).as_posix()
        reason = SQLITE_ONLY_MODULES.get(rel)
        if reason:
            item.add_marker(pytest.mark.skip(reason=f"SQLite only under the Postgres fixture: {reason}"))
            by_module += 1
            continue
        marker = item.get_closest_marker("sqlite_only")
        if marker is not None:
            why = marker.args[0] if marker.args else "SQLite's own behaviour"
            item.add_marker(pytest.mark.skip(reason=f"SQLite only under the Postgres fixture: {why}"))
            by_marker += 1
    print(f"\nW3b Postgres fixture: {by_module} test(s) in {len(SQLITE_ONLY_MODULES)} listed module(s) and "
          f"{by_marker} marked test(s) skipped as SQLite-only")


@pytest.fixture(scope="session", autouse=True)
def _assert_worktree_unchanged():
    """B-44 — the generalised sentinel: `git status --porcelain` at teardown
    must equal its session-start snapshot. `not a checkout` is a failure in
    its own words, never read as unchanged. The `data/` fingerprint above is
    kept: it sees ignored files under `data/`, which this cannot."""
    yield
    cmp = _wt_compare(WORKTREE_BASELINE, _wt_porcelain())
    assert cmp["verdict"] == "unchanged", _wt_describe(cmp)


@pytest.fixture(scope="module", autouse=True)
def _redirects_restored_per_module(request):
    """W1 — a module may change a redirect for its own tests (monkeypatch, or
    a module fixture that restores); it may not leave the change behind.
    Compared with the module's own start, so only the module that leaks is
    named; the value is then put back, so the leak reaches no later module."""
    before = redirect_snapshot()
    yield
    changes = redirect_changes(before)
    for name, value in before.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    assert not changes, (
        f"{request.module.__name__} left a writer redirect changed: {changes}. "
        f"Restore it (monkeypatch, or undo in the fixture's teardown): every "
        f"later module would otherwise write where it points.")


@pytest.fixture(autouse=True)
def _release_fastapi_callable_caches():
    """W3b part 2 — the apps a test builds are released when the test ends.

    FastAPI (0.143 here) classifies every route handler and dependency once
    (generator, async generator, coroutine) and keeps the answers in three
    module-level `lru_cache`s keyed by the callable's identity, 4096 entries
    each, so the callable itself lives for the process. Every `create_app`
    closes its handlers over the `NexusServices` it built, so each test that
    built an app left its seven stores alive. Measured under the Postgres
    fixture: 98 open store connections after sixteen tests, and a server with
    the default hundred slots refusing connections before a quarter of the
    suite had run. On SQLite the same leak is seven descriptors per app,
    under a limit of 20,000 on this host, which is why it was never seen.

    The caches are cleared after every test; FastAPI recomputes a
    classification on demand, so a module-scoped app keeps working. An app
    is a reference cycle (its routes hold handlers whose closures hold the
    app), so the release waits for the cyclic collector; under the Postgres
    fixture, where a hundred slots is the bound, the collector runs here,
    every test (six modules sampled each second: 74 connections at the peak
    without it, 14 with it). The consequence — the services and the store
    connection of every app but the last gone by the next test (the last
    app's are held by `agentic_loop.set_event_recorder`, one set, by design:
    production has one app) — is pinned by tests/test_w3b_store_interface.py
    R on both backends."""
    yield
    release_fastapi_callable_caches()
    if POSTGRES_TEST:
        import gc
        gc.collect()


def release_fastapi_callable_caches() -> int:
    """Clear every `functools.lru_cache` in FastAPI's dependency modules;
    returns how many were cleared (0 when FastAPI is absent)."""
    import functools
    cleared = 0
    for modname in ("fastapi.dependencies.models", "fastapi.dependencies.utils"):
        try:
            mod = importlib.import_module(modname)
        except Exception:                                   # fastapi absent: nothing to release
            continue
        for name in dir(mod):
            obj = getattr(mod, name, None)
            if isinstance(obj, functools._lru_cache_wrapper):
                obj.cache_clear()
                cleared += 1
    return cleared


@pytest.fixture(autouse=True)
def _release_chroma_systems():
    """W0b part 5d — the Chroma systems a test builds are released when the
    test ends.

    chromadb keeps every PersistentClient's system, with its Rust runtime's
    threads and its open files, in a class-level cache for the life of the
    process. Each `create_services` leaves about eight threads and seven
    descriptors behind, measured on Linux: 95 threads after twelve builds,
    and 7 once the cache was cleared. Over a suite of hundreds of builds,
    macos's per-process limits are reached first. CI runs 9, 11 and 12 on
    macos hung or failed with "Resource temporarily unavailable" (os error
    35), each time in a late test that built the services several times.
    Linux and windows, with higher limits, passed the same commits.

    Only systems created during the test are released, so a module-scoped
    fixture's client, set up before this fixture, is untouched. A
    persistence test reopening a path within one test still shares one
    system, because release happens only after the test."""
    try:
        from chromadb.api.client import SharedSystemClient
    except Exception:                                   # chromadb absent: nothing to release
        yield
        return
    before = set(SharedSystemClient._identifier_to_system)
    yield
    release_chroma_systems_created_since(before)


def release_chroma_systems_created_since(before: set) -> int:
    """Stop and forget every Chroma system not in `before`; returns how many
    (tests/test_chroma_release.py pins it)."""
    from chromadb.api.client import SharedSystemClient
    released = 0
    for ident in [i for i in SharedSystemClient._identifier_to_system if i not in before]:
        system = SharedSystemClient._identifier_to_system.pop(ident, None)
        SharedSystemClient._identifier_to_refcount.pop(ident, None)
        if system is not None:
            released += 1
            try:
                system.stop()
            except Exception:                           # a stop that fails must not fail the test
                pass
    return released
