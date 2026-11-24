"""W3b part 3 — the store the settings name (D1, T13) and the single-writer
lock on Postgres (D8, T7).

S   THE SETTINGS, read one way everywhere (`backend/store/target.py`, the
    twin of `backend/providers/backends.py`): `NEXUS_STORE` stripped and
    lowered, unset or empty is sqlite; `postgres` needs `NEXUS_DATABASE_URL`;
    an unknown value, a missing URL or a URL of another scheme is refused by
    name — the value as set, the names accepted, never the URL's secret.
C   CREATE_SERVICES resolves the store before the lock and before any store
    opens: a refusal leaves the data root empty (no lock file, no store); on
    Postgres every store class opens the one database (kb.db's and
    archive.db's tables alike, D7), the data root keeps chroma/ and
    snapshots/, and `/healthz` names the backend and the schema, never the
    URL.
L   THE LOCK on Postgres (T7): a second process is refused by name with the
    holder's pid and exit 7; a holder killed outright leaves a lock the next
    process takes over, recorded in `nexus_store_lock_log` and logged; one
    process reopening its own store is counted, not refused; two schemas
    lock independently.
V   START.PY: `--serve` on Postgres serves and `/healthz` says `postgres`;
    the output names the store with the URL's password redacted; `postgres`
    without a URL is refused with exit 2 before anything is opened; `--check`
    names the hosted store on its first line (law 15).
M   MCP's main refuses an unknown `NEXUS_STORE` by name with exit 2, having
    opened nothing.

Every Postgres test skips BY NAME without `NEXUS_TEST_DATABASE_URL` (the
Postgres CI job sets it); the settings and refusal tests run everywhere.

Law 16: the pre-fix run against `7e107b6` (part 2) is printed in part 3's
commit body: the S and C tests fail on the import (a new API, declared),
the V and M tests on the exit code and the missing line.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
from tests import _proc
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import POSTGRES_TEST, POSTGRES_TEST_URL as PG_URL  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
NO_PG = "no Postgres named: NEXUS_TEST_DATABASE_URL is unset (the Postgres job sets it; windows and macos run SQLite)"


def _role_url(role: str, password: str, schema: str) -> str:
    """The test URL as `role` with `password`, the session's search_path set
    to `schema` through libpq's `options` (percent-encoded in the URI)."""
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(PG_URL)
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    netloc = f"{role}:{password}@{host}" + (f":{parts.port}" if parts.port else "")
    query = (parts.query + "&" if parts.query else "") + f"options=-c%20search_path%3D{schema}"
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


@pytest.fixture
def pg_schema(tmp_path):
    """A fresh schema on the named Postgres, dropped afterwards."""
    if not PG_URL:
        pytest.skip(NO_PG)
    import psycopg
    schema = "t_" + hashlib.sha1(str(tmp_path).encode()).hexdigest()[:20]
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    yield schema
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest.fixture
def pg_role(pg_schema):
    """(URL, secret): a dedicated role, with USAGE and CREATE on the test
    schema and a random password, and a URL naming that schema. The secret a
    test looks for in what NEXUS prints must be one that can occur in no line
    for another reason: the fixture URL's own password cannot be, because
    CI's is the word `postgres`, which every line that names the store prints
    legitimately (the backend, the scheme, the role), so `ca2d974`'s `ci` run
    63 could not tell a leak from the store's name. The role's sessions are
    ended (a test's services may still hold some: the app caches release
    them after this teardown) and the role is dropped afterwards."""
    import secrets
    import psycopg
    from psycopg import sql
    role, password = f"sel_{pg_schema[2:18]}", "Pw" + secrets.token_hex(10)
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
    try:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA {} TO {}").format(sql.Identifier(pg_schema),
                                                                              sql.Identifier(role)))
        yield _role_url(role, password, pg_schema), password
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s", (role,))
            c.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(role)))
            c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


# ---------------------------------------------------------------------------
# S — the settings
# ---------------------------------------------------------------------------

def test_s1_unset_or_empty_is_sqlite_and_the_value_is_read_stripped_and_lowered():
    from backend.store import configured_store_target, store_backend_name, store_config_reason
    for environ in ({}, {"NEXUS_STORE": ""}, {"NEXUS_STORE": "  SQLite "}):
        assert store_backend_name(environ) == "sqlite", environ
        assert store_config_reason(environ) is None
        assert configured_store_target(environ) is None, "sqlite: the paths decide, as before"
    assert store_backend_name({"NEXUS_STORE": " Postgres "}) == "postgres"


def test_s2_postgres_without_a_url_is_refused_by_name():
    from backend.store import StoreConfigError, configured_store_target, store_config_reason
    for environ in ({"NEXUS_STORE": "postgres"}, {"NEXUS_STORE": "postgres", "NEXUS_DATABASE_URL": "  "}):
        reason = store_config_reason(environ)
        assert reason and "NEXUS_DATABASE_URL" in reason and "unset" in reason, reason
        with pytest.raises(StoreConfigError, match="NEXUS_DATABASE_URL"):
            configured_store_target(environ)


def test_s3_an_unknown_value_is_refused_naming_it_and_the_backends():
    from backend.store import StoreConfigError, configured_store_target, store_config_reason
    reason = store_config_reason({"NEXUS_STORE": "nonsense"})
    assert reason and "'nonsense'" in reason and "sqlite, postgres" in reason, reason
    with pytest.raises(StoreConfigError, match="nonsense"):
        configured_store_target({"NEXUS_STORE": "nonsense"})


def test_s4_a_url_of_another_scheme_is_refused_without_printing_the_secret():
    from backend.store import store_config_reason
    reason = store_config_reason({"NEXUS_STORE": "postgres", "NEXUS_DATABASE_URL": "mysql://u:hunter2@h/db"})
    assert reason and "'mysql'" in reason and "postgresql://" in reason, reason
    assert "hunter2" not in reason


def test_s5_the_target_carries_the_url_and_every_line_that_names_it_redacts_the_password():
    from backend.store import configured_store_target, redacted_url
    target = configured_store_target({"NEXUS_STORE": "postgres",
                                      "NEXUS_DATABASE_URL": " postgresql://u:pw@h:5433/db_test "})
    assert (target.backend, target.url, target.schema) == ("postgres", "postgresql://u:pw@h:5433/db_test", None)
    assert target.describe() == "postgres postgresql://u:***@h:5433/db_test"
    assert redacted_url("postgresql://u@h/db") == "postgresql://u@h/db", "no password: unchanged"
    assert redacted_url("postgresql://u:p%40ss@[::1]:5432/db?sslmode=require") == \
        "postgresql://u:***@[::1]:5432/db?sslmode=require"
    assert "pw" not in redacted_url("postgres://u:pw@h/db")


# ---------------------------------------------------------------------------
# C — create_services
# ---------------------------------------------------------------------------

def _services(root: Path, **overrides):
    from backend.server import create_services
    from tests.conftest import OfflineTestProvider
    return create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma", eval_log_path=root / "m.jsonl",
                           archive_db_path=root / "archive.db", snapshots_dir=root / "snap",
                           provider=OfflineTestProvider(), **overrides)


def test_c1_postgres_without_a_url_is_refused_before_anything_opens(tmp_path, monkeypatch):
    from backend.store import StoreConfigError
    monkeypatch.setenv("NEXUS_STORE", "postgres")
    monkeypatch.delenv("NEXUS_DATABASE_URL", raising=False)
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(StoreConfigError, match="NEXUS_DATABASE_URL"):
        _services(root)
    assert sorted(p.name for p in root.iterdir()) == [], "nothing was opened: no store, no lock file, no folder"


def test_c2_an_unknown_store_is_refused_before_anything_opens(tmp_path, monkeypatch):
    from backend.store import StoreConfigError
    monkeypatch.setenv("NEXUS_STORE", "nonsense")
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(StoreConfigError, match="nonsense"):
        _services(root)
    assert sorted(p.name for p in root.iterdir()) == []


def test_c3_on_postgres_every_store_opens_the_database_and_healthz_names_the_backend(tmp_path, monkeypatch, pg_schema,
                                                                                    pg_role):
    from fastapi.testclient import TestClient
    from backend import store_lock
    from backend.server import create_app
    from backend.store import StoreTarget
    url, secret = pg_role
    monkeypatch.setenv("NEXUS_STORE", "postgres")
    monkeypatch.setenv("NEXUS_DATABASE_URL", url)
    root = tmp_path / "root"
    root.mkdir()
    services = _services(root)
    try:
        stores = {"store": services.store, "lineage": services.lineage, "recalibration": services.recalibration,
                  "trails": services.trails, "archive_chain": services.archive_chain}
        for name, st in stores.items():
            assert st.conn.backend == "postgres", name
            assert st.conn.describe().endswith(f"schema {pg_schema}"), (name, st.conn.describe())
            assert secret not in st.conn.describe()
        assert not (root / "kb.db").exists() and not (root / "archive.db").exists(), "the stores live in Postgres"
        assert (root / "chroma").is_dir() and (root / "snap").is_dir(), "the data root keeps chroma/ and snapshots/"
        assert store_lock.held_here(StoreTarget.postgres(url)) == 1, "the lock is the advisory lock on the target"
        assert not (root / "kb.db.lock").exists(), "no lock file: the lock is the database's"
        health = TestClient(create_app(services)).get("/healthz").json()
        assert health["store"] == {"backend": "postgres", "target": services.store.conn.describe()}, health["store"]
        assert secret not in json.dumps(health)
        assert "nexus_store_lock" not in services.store.conn.tables(), "the lock's tables are support tables"
    finally:
        services.store_lock.release()
    assert store_lock.held_here(StoreTarget.postgres(url)) == 0
    assert store_lock.read_holder(StoreTarget.postgres(url))["released"] is True


def test_c4_on_sqlite_healthz_names_the_file(tmp_path):
    from fastapi.testclient import TestClient
    from backend.server import create_app
    root = tmp_path / "root"
    root.mkdir()
    services = _services(root)
    try:
        health = TestClient(create_app(services)).get("/healthz").json()
        assert health["store"]["backend"] == services.store.conn.backend
        assert health["store"]["target"] == services.store.conn.describe()
    finally:
        services.store_lock.release()


# ---------------------------------------------------------------------------
# L — the lock on Postgres (T7)
# ---------------------------------------------------------------------------

CHILD = r"""
import sys, json
from backend.store import StoreTarget
from backend.store_lock import EXIT_STORE_LOCKED, StoreLocked, acquire
url, schema, action = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    lock = acquire(StoreTarget.postgres(url, schema=schema), mode="script:test_child")
except StoreLocked as e:
    print(f"REFUSED {e}", flush=True)
    sys.exit(EXIT_STORE_LOCKED)
print("ACQUIRED", flush=True)
if action == "hold":
    sys.stdin.readline()          # hold until the parent closes stdin, or kills us
    lock.release()
"""


def _child(url: str, schema: str, action: str) -> "_proc.Child":
    env = {k: v for k, v in os.environ.items() if k not in ("NEXUS_STORE", "NEXUS_DATABASE_URL")}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               PYTHONPATH=os.pathsep.join(filter(None, [str(REPO), env.get("PYTHONPATH")])))
    return _proc.Child([sys.executable, "-c", CHILD, url, schema, action], label=f"the lock child ({action})",
                       cwd=REPO, env=env, stdin=_proc.PIPE, stdout=_proc.PIPE, stderr=_proc.STDOUT,
                       text=True, encoding="utf-8", errors="replace")


def _first_line(proc: "_proc.Child", timeout: float = 60.0) -> str:
    """W4b: the line within the bound (the old loop's `readline()` could
    block past its deadline: it had none of its own)."""
    line = proc.readline(timeout=timeout)
    if not line:
        raise AssertionError(f"the child printed nothing (exit {proc.poll()}): {proc.output()!r}")
    return line.rstrip("\n")


def test_l1_a_second_process_is_refused_by_name_with_the_holder_and_exit_7(pg_schema):
    from backend.store import StoreTarget
    from backend.store_lock import EXIT_STORE_LOCKED, acquire
    target = StoreTarget.postgres(PG_URL, schema=pg_schema)
    lock = acquire(target, mode="services")
    try:
        child = _child(PG_URL, pg_schema, "try")
        line = _first_line(child)
        code = child.wait(timeout=60)
    finally:
        lock.release()
    print(f"L1: {line}")
    assert code == EXIT_STORE_LOCKED and line.startswith("REFUSED this store is open in another NEXUS process"), (code, line)
    assert f"pid {os.getpid()}" in line and f"schema {pg_schema}" in line, line


def test_l2_a_holder_killed_outright_leaves_a_lock_the_next_process_takes_over(pg_schema, caplog):
    import logging
    import psycopg
    from backend.store import StoreTarget
    from backend.store_lock import PG_LOCK_LOG_TABLE, acquire
    child = _child(PG_URL, pg_schema, "hold")
    assert _first_line(child) == "ACQUIRED"
    child.kill()
    child.wait(timeout=60)
    target = StoreTarget.postgres(PG_URL, schema=pg_schema)
    deadline = time.monotonic() + 30.0
    with caplog.at_level(logging.WARNING, logger="backend.store_lock"):
        while True:
            try:
                lock = acquire(target, mode="services")
                break
            except Exception as e:                   # the server has not yet noticed the dead client
                if time.monotonic() > deadline:
                    raise AssertionError(f"the killed holder's lock was not freed within 30 s: {e}") from e
                time.sleep(0.1)
    try:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(f'SET search_path TO "{pg_schema}"')
            rows = [json.loads(r[0]) for r in c.execute(f"SELECT event FROM {PG_LOCK_LOG_TABLE}")]
    finally:
        lock.release()
    assert len(rows) == 1 and rows[0]["event"] == "stale_lock_taken_over", rows
    assert rows[0]["previous"]["pid"] == child.pid and rows[0]["by"]["pid"] == os.getpid(), rows[0]
    assert any("took over a stale lock" in r.message for r in caplog.records), [r.message for r in caplog.records]


def test_l3_one_process_reopening_its_own_store_is_counted_not_refused(pg_schema):
    from backend.store import StoreTarget
    from backend.store_lock import acquire, held_here, read_holder
    target = StoreTarget.postgres(PG_URL, schema=pg_schema)
    first = acquire(target, mode="services")
    second = acquire(target, mode="services")
    assert held_here(target) == 2
    second.release()
    assert held_here(target) == 1 and read_holder(target)["released"] is False
    first.release()
    assert held_here(target) == 0 and read_holder(target)["released"] is True
    first.release()                                   # a second release of the same taker is a no-op
    assert held_here(target) == 0


def test_l4_two_schemas_in_one_database_lock_independently(pg_schema):
    import psycopg
    from backend.store import StoreTarget
    from backend.store_lock import acquire
    other = pg_schema + "_b"
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA IF NOT EXISTS "{other}"')
    try:
        lock = acquire(StoreTarget.postgres(PG_URL, schema=pg_schema), mode="services")
        try:
            child = _child(PG_URL, other, "try")
            line = _first_line(child)
            code = child.wait(timeout=60)
        finally:
            lock.release()
        assert (code, line) == (0, "ACQUIRED"), (code, line)
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(f'DROP SCHEMA IF EXISTS "{other}" CASCADE')


# ---------------------------------------------------------------------------
# V — start.py on Postgres (T13)
# ---------------------------------------------------------------------------

def test_v1_serve_on_postgres_serves_names_the_store_and_healthz_says_postgres(tmp_path, pg_schema, pg_role):
    from tests.test_w3_serve_stub import _free_port, _get, _serve
    root, port = tmp_path / "root", _free_port()
    (tmp_path / "envroot").mkdir()
    url, secret = pg_role
    child = _serve(tmp_path, root, port, LLM_BACKEND="stub", NEXUS_STORE="postgres", NEXUS_DATABASE_URL=url)
    state = child.wait_until(port)
    try:
        assert state == "serving", (f"--serve did not serve: {state}; the last probe saw {child.last_probe}\n"
                                    f"{child.output()[-2500:]}")
        health = _get(port, "/healthz")
    finally:
        child.stop()
    out = child.output()
    print(f"V1 ({sys.platform}): store {health.get('store')}")
    assert health["store"]["backend"] == "postgres" and health["store"]["target"].endswith(f"schema {pg_schema}")
    assert secret not in out and secret not in json.dumps(health), "the URL's password was printed"
    assert "  store       postgres postgresql://" in out and ":***@" in out, out[-2000:]
    assert out.splitlines()[0].startswith("database: postgres postgresql://"), out.splitlines()[0]
    assert not (root / "kb.db").exists() and not (root / "kb.db.lock").exists(), "the store and its lock are the database's"


def test_v2_serve_refuses_postgres_without_a_url_before_anything_opens(tmp_path):
    from tests.test_w3_serve_stub import EXIT_USAGE, _free_port, _serve
    root, port = tmp_path / "root", _free_port()
    (tmp_path / "envroot").mkdir()
    child = _serve(tmp_path, root, port, LLM_BACKEND="stub", NEXUS_STORE="postgres")
    state = child.wait_until(port, timeout=120)
    code = child.stop()
    out = child.output()
    print(f"V2 ({sys.platform}): exit {code}, {state}")
    assert state == "exited" and code == EXIT_USAGE, (state, code, out[-1500:])
    assert "  store       NEXUS_STORE=postgres needs NEXUS_DATABASE_URL" in out, out[-1500:]
    assert not root.exists() or not any(root.iterdir()), "nothing was opened under the data root"


def test_v3_serve_refuses_an_unknown_store_by_name(tmp_path):
    from tests.test_w3_serve_stub import EXIT_USAGE, _free_port, _serve
    root, port = tmp_path / "root", _free_port()
    (tmp_path / "envroot").mkdir()
    child = _serve(tmp_path, root, port, LLM_BACKEND="stub", NEXUS_STORE="nonsense")
    state = child.wait_until(port, timeout=120)
    code = child.stop()
    out = child.output()
    assert state == "exited" and code == EXIT_USAGE, (state, code, out[-1500:])
    assert "NEXUS_STORE='nonsense' is not a store backend NEXUS knows (sqlite, postgres)" in out, out[-1500:]


# ---------------------------------------------------------------------------
# M — MCP's main (T13)
# ---------------------------------------------------------------------------

def test_m1_mcp_main_refuses_an_unknown_store_by_name_having_opened_nothing(tmp_path):
    from backend.store import EXIT_STORE_CONFIG
    root = tmp_path / "mcp_root"
    env = {k: v for k, v in os.environ.items() if k not in ("NEXUS_DATA_ROOT", "NEXUS_MCP_DATA_ROOT", "NEXUS_MCP_OWNER")}
    env.update(PYTHONDONTWRITEBYTECODE="1", NEXUS_ENV_ROOT=str(tmp_path), PYTHONIOENCODING="utf-8",
               NEXUS_MCP_DATA_ROOT=str(root), NEXUS_STORE="nonsense", LLM_BACKEND="stub",
               PYTHONPATH=os.pathsep.join(filter(None, [str(REPO), env.get("PYTHONPATH")])))
    proc = _proc.run([sys.executable, "-m", "backend.mcp"], cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    print(f"M1: exit {proc.returncode}: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ''}")
    assert proc.returncode == EXIT_STORE_CONFIG, proc.stderr[-1500:]
    assert "NEXUS MCP: NEXUS_STORE='nonsense' is not a store backend NEXUS knows" in proc.stderr, proc.stderr[-1500:]
    assert not (root / "kb.db").exists() and not (root / "kb.db.lock").exists(), "nothing was opened"
