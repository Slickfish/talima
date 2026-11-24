"""The Postgres backend (W3b part 2; `run/specs/W3b.md` D4 to D7).

A `Database` over one psycopg 3 connection that behaves as the SQLite
connection behaves wherever a store can tell the difference:

- **Transactions as sqlite3 makes them (D5).** The connection runs in
  autocommit; a statement whose first word is INSERT, UPDATE, DELETE or
  REPLACE opens a transaction (`BEGIN`) when none is open, a SELECT never
  does, `commit()` and `rollback()` end it, and `executescript` commits
  pending work first. So a `deferred_pass()` is one transaction here too, a
  record write that fails rolls back here too, and no connection sits idle
  in a transaction after a read (psycopg's default would open one on every
  SELECT: measured). Where Postgres aborts the transaction on a failed
  statement and SQLite does not, the statement that OPENED the transaction
  is rolled back here when it fails (nothing else was in it); a failure
  deeper in a transaction is left for the caller's `rollback()`, loudly.
- **The busy timeout's twin.** `lock_timeout` is 5000 ms, SQLite's measured
  busy timeout: a writer behind another writer's open pass fails after
  five seconds with `LockNotAvailable`, which the neutral `OperationalError`
  covers, the failure shape the F.2 handler and W0b part 5 were written for.
- **Values bit for bit (D6).** `extra_float_digits` is set to 3 on every
  connection and read back; a session that reports less than 1 is refused
  by name, because every recomputed hash passes through a double. A Python
  `str` is sent as `text` (psycopg's default sends it as unknown, which a
  bare `CASE WHEN %s` cannot type: measured).
- **The dialect** (`backend/store/dialect.py`) rewrites every statement and
  every DDL script; a `PRAGMA` reaching here is refused by name.
- **Introspection as `table_info` tells it.** `columns()` renders
  `information_schema.columns` as the six-tuples SQLite's pragma gives,
  types mapped back to the declared names (`bigint` to `INTEGER`, `double
  precision` to `REAL`, `text` to `TEXT`, `bytea` to `BLOB`), so the DDL
  pin's twin compares like with like. Two differences are the pin's to
  declare: a `PRIMARY KEY` column reads `notnull = 1` here and `0` for
  SQLite's rowid alias, and the two rowid tables carry their ordinal.
- **One schema per store (D7).** A target with a schema creates it, sets
  `search_path` to it alone and installs the support DDL (the sequence
  table and `nexus_lower`) there, so a test's thousand stores are a
  thousand schemas in one database and production's one store is the
  URL's own schema.

Nothing is forwarded to the driver connection: a module that still leans
on a SQLite-only attribute fails here by name, which is how the suite under
this backend finds it.
"""
from __future__ import annotations

import re
import threading
from typing import Iterable, Sequence

import psycopg
from psycopg.types.string import StrDumper

from backend.store import dialect
from backend.store.base import BackendUnavailable, ColumnInfo, Database, PgRow, _notify, check_identifier, tracing

LOCK_TIMEOUT_MS = 5000          # SQLite's busy timeout, measured 5000 ms on every store connection
EXTRA_FLOAT_DIGITS = 3          # exact doubles in the text protocol; below 1 the round trip loses bits (measured)
APPLICATION_NAME = "nexus"
#: TCP keepalives on every connection (libpq's), so a session whose host
#: vanished is noticed by the server in about a minute and a half — the
#: advisory lock a dead host held (D8) is then freed, not held forever.
KEEPALIVES = {"keepalives": 1, "keepalives_idle": 60, "keepalives_interval": 10, "keepalives_count": 3}
REVERSE_TYPES = {"bigint": "INTEGER", "double precision": "REAL", "text": "TEXT", "bytea": "BLOB"}


def pg_row_factory(cursor):
    """psycopg's row-factory protocol: called per result, returns the maker."""
    names = [d.name for d in cursor.description] if cursor.description else []

    def make(values):
        return PgRow(values, names)
    return make


class _DbCursor:
    """`db.cursor()`: statements go through the database's own `execute`,
    so the transaction rule and the trace apply; the results are the last
    statement's."""

    def __init__(self, db: "PostgresDatabase"):
        self._db = db
        self._cur = None

    def execute(self, sql: str, params: Sequence | None = None):
        self._cur = self._db.execute(sql, params)
        return self

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence]):
        self._cur = self._db.executemany(sql, seq_of_params)
        return self

    def __iter__(self):
        return iter(self._cur)

    def __getattr__(self, name: str):
        return getattr(self._cur, name)


class PostgresDatabase(Database):
    backend = "postgres"

    def __init__(self, raw: psycopg.Connection, target):
        self.raw = raw
        self.target = target
        self._in_tx = False
        self._changes = 0
        self._lock = threading.RLock()

    @classmethod
    def open_target(cls, target, *, read_only: bool = False) -> "PostgresDatabase":
        """The connection for `target`. `read_only` (W3b part 3, D9: the
        instance scripts' open, law 15) asks the server for a session whose
        every transaction is read-only (`default_transaction_read_only = on`,
        the first barrier; `scripts/_readonly.py` adds the role check that a
        role cannot undo) and creates nothing: no schema, no support DDL."""
        from psycopg.conninfo import conninfo_to_dict
        # A keyword overrides the URL's parameter of the same name, so the
        # URL's own `options` (a `-c search_path=…`, the one way a URL names
        # its schema) are kept in front of ours (measured: without this a
        # search_path in the URL never reached the session). `given` is that
        # one parameter's value, not the URL (named so: W3b close, F6's guard
        # reads a URL-named value in a scope that handles URLs as the URL).
        given = (conninfo_to_dict(target.url).get("options") or "").strip()
        options = f"{given} -c lock_timeout={LOCK_TIMEOUT_MS} -c extra_float_digits={EXTRA_FLOAT_DIGITS}".strip()
        if read_only:
            options += " -c default_transaction_read_only=on"
        raw = psycopg.connect(target.url, autocommit=True, row_factory=pg_row_factory, options=options,
                              application_name=APPLICATION_NAME, **KEEPALIVES)
        raw.adapters.register_dumper(str, StrDumper)
        db = cls(raw, target)
        db.read_only = read_only
        digits = int(raw.execute("SHOW extra_float_digits").fetchone()[0])
        if digits < 1:
            raw.close()
            raise BackendUnavailable(
                f"extra_float_digits is {digits} on this session: doubles would not round-trip "
                f"and every recomputed hash would move (law 4); refused")
        if target.schema:
            check_identifier(target.schema)
            if not read_only:
                raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{target.schema}"')
            raw.execute(f'SET search_path TO "{target.schema}"')
        # The schema the session actually resolves names to: the target's,
        # or the URL's own (a `search_path` in the URL's options, else the
        # role's default). `describe()` and the lock name THIS, never a guess.
        db.schema = raw.execute("SELECT current_schema()").fetchone()[0]
        if not read_only:
            raw.execute(dialect.SUPPORT_DDL)
        return db

    # -- statements ---------------------------------------------------------

    def _run(self, method: str, sql: str, arg, has_params: bool):
        if dialect.is_ddl(sql):
            # A migration's `ALTER TABLE … ADD COLUMN` or a `CREATE TABLE` run
            # through execute() is DDL: its types are the DDL translator's
            # (BIGINT, COLLATE "C"), never the statement translator's, which
            # would pass `INTEGER` and a bare `TEXT` through (the guard found
            # it: `tests/test_w3b_dialect.py` G).
            if arg is not None:
                raise dialect.DialectError(f"DDL takes no parameters: {sql.strip()[:80]!r}")
            translated, dml = dialect.translate_ddl(sql), False
        else:
            translated, dml = dialect.translate(sql, has_params), dialect.is_dml(sql)
        with self._lock:
            opened = False
            if dml and not self._in_tx:
                self.raw.execute("BEGIN")
                self._in_tx = True
                opened = True
            cur = self.raw.cursor()
            try:
                if arg is None:
                    cur.execute(translated)
                else:
                    getattr(cur, method)(translated, arg)
            except Exception:
                if opened:
                    try:
                        self.raw.execute("ROLLBACK")
                    finally:
                        self._in_tx = False
                raise
            if dml and cur.rowcount > 0:
                self._changes += cur.rowcount
            return cur

    def execute(self, sql: str, params: Sequence | None = None):
        if tracing():
            _notify(sql)
        return self._run("execute", sql, tuple(params) if params is not None else None,
                         has_params=params is not None)

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence]):
        seq = [tuple(p) for p in seq_of_params]
        if tracing():
            for _ in seq:
                _notify(sql)
        if not seq:
            return self.raw.cursor()                     # nothing to run: a cursor with rowcount -1, as sqlite3
        return self._run("executemany", sql, seq, has_params=True)

    def executescript(self, script: str) -> None:
        if tracing():
            _notify(script)
        with self._lock:
            self.commit()
            self.raw.execute(dialect.translate_ddl(script))

    def cursor(self) -> _DbCursor:
        return _DbCursor(self)

    # -- transactions -------------------------------------------------------

    def commit(self) -> None:
        with self._lock:
            if self._in_tx:
                self.raw.execute("COMMIT")
                self._in_tx = False

    def rollback(self) -> None:
        with self._lock:
            if self._in_tx:
                self.raw.execute("ROLLBACK")
                self._in_tx = False

    @property
    def in_transaction(self) -> bool:
        return self._in_tx

    def close(self) -> None:
        self.raw.close()

    def __del__(self) -> None:
        # sqlite3 closes its connection when the object is collected, silently;
        # the twin does the same, so a dropped store frees its server slot at
        # once and without the ResourceWarning psycopg's own dealloc raises
        # (the suite drops thousands of stores; a server has a hundred slots).
        raw = getattr(self, "raw", None)
        if raw is not None and not raw.closed:
            try:
                raw.close()
            except Exception:                   # pragma: no cover - interpreter shutdown
                pass

    # -- introspection ------------------------------------------------------

    def columns(self, table: str) -> list[ColumnInfo]:
        check_identifier(table)
        rows = self.raw.execute(
            "SELECT c.column_name, c.data_type, c.is_nullable, c.column_default, c.ordinal_position, "
            "  (SELECT k.ordinal_position FROM information_schema.table_constraints tc "
            "     JOIN information_schema.key_column_usage k ON k.constraint_name = tc.constraint_name "
            "      AND k.table_schema = tc.table_schema AND k.table_name = tc.table_name "
            "   WHERE tc.table_schema = c.table_schema AND tc.table_name = c.table_name "
            "     AND tc.constraint_type = 'PRIMARY KEY' AND k.column_name = c.column_name) AS pk "
            "FROM information_schema.columns c "
            "WHERE c.table_schema = current_schema() AND c.table_name = %s ORDER BY c.ordinal_position",
            (table,)).fetchall()
        return [ColumnInfo(int(r["ordinal_position"]) - 1, r["column_name"],
                           REVERSE_TYPES.get(r["data_type"], r["data_type"].upper()),
                           1 if r["is_nullable"] == "NO" else 0, _default(r["column_default"]),
                           int(r["pk"] or 0)) for r in rows]

    def has_table(self, name: str) -> bool:
        return self.raw.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() AND table_name = %s",
            (name,)).fetchone() is not None

    def tables(self) -> list[str]:
        # The schema's base tables minus the support tables: `nexus_sequence`
        # (the id triggers' row, `sqlite_sequence`'s twin) and the lock's two
        # (`nexus_store_lock`, `nexus_store_lock_log`: the twins of the lock
        # file and its log, which SQLite keeps beside the store, not in it).
        rows = self.raw.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' AND NOT (table_name = ANY(%s))",
            (list(dialect.SUPPORT_TABLES),)).fetchall()
        return sorted(r[0] for r in rows)

    def describe(self) -> str:
        return f"postgres {self.raw.info.dbname} schema {getattr(self, 'schema', None) or self.target.schema or 'public'}"

    def changes(self) -> int:
        return self._changes


_CAST_SUFFIX = re.compile(r"::[A-Za-z_\" ]+$")


def _default(value):
    """`PRAGMA table_info`'s spelling of a default is the DDL's text (`0.5`,
    `'x'`); Postgres renders `'x'::text`, so the cast is dropped."""
    if value is None:
        return None
    return _CAST_SUFFIX.sub("", value)


__all__ = ["APPLICATION_NAME", "EXTRA_FLOAT_DIGITS", "LOCK_TIMEOUT_MS", "PostgresDatabase", "pg_row_factory"]
