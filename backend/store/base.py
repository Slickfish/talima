"""The store interface (W3b part 1): the one place SQL meets a driver.

Every store class (`TripleStore`, `LineageStore`, `RecalibrationStore`,
`NexusArchiveChain`, `HebbianGraphStore`, `TrailStore`, and the two that
ride another's connection, `WorkUnitStore` and `CoverageStore`) talks to a
`Database`, never to a driver. Part 1 gives the interface one backend, the
SQLite connection exactly as every store opened it before
(`sqlite3.connect(path, check_same_thread=False)`, `row_factory =
sqlite3.Row`, no PRAGMA set), so nothing the default backend does changes;
part 2 adds Postgres behind the same methods (`run/specs/W3b.md` S2, D1).

What a `Database` offers, and why each method exists:

- `execute`, `executemany`, `executescript`, `commit`, `rollback`,
  `in_transaction`, `close`: the DB-API surface the backend already uses
  (189 call sites, agent A's table), with sqlite3's transaction rule as the
  contract (an implicit transaction before a writing statement, none
  before a read; `executescript` commits pending work first).
- `columns(table)`: what `PRAGMA table_info` told six migration sites,
  as `ColumnInfo` tuples with table_info's shape, so the one SQLite pin
  that reads them (`tests/test_scope_recorded.py`) keeps its meaning.
- `has_table(name)`: the `sqlite_master` idiom of three sites.
- `describe()`: the human name of the store (the file path; a database
  on Postgres), for the MCP banner that read `PRAGMA database_list`.
- `changes()`: the connection-wide modified-row counter two sites read as
  `total_changes`; on Postgres the adapter counts its own statements.
- the connection-manager protocol sqlite3 gives: `with db:` commits on
  success and rolls back on an exception and NEVER closes (psycopg's
  `with conn:` closes; the dedup lookup relies on the sqlite3 shape).
- a class-level trace hook (`add_trace`/`remove_trace`): every statement
  a `Database` runs is reported, with its text, to every registered
  callback, once per parameter set of an `executemany`. `tests/_fz_trace.py`
  derives the freeze guard's `(table, host, method)` pairs from it on both
  backends, where it used SQLite's `set_trace_callback` and so could never
  have run on Postgres (agent B's G-8).

`Row` is the row type the backend reads (`r["col"]`, `r[0]`, `.keys()`,
`dict(row)`, `tuple(row)`, unpacking: the idioms counted in the establish);
on SQLite it is `sqlite3.Row` itself, so no row the default backend returns
changes class. `is_row()` is the neutral form of the two
`isinstance(r, sqlite3.Row)` checks (as_of.py). `Error`, `IntegrityError`
and `OperationalError` are tuples of the drivers' classes, usable in an
`except`, so no module outside this package names a driver's exception.

The guard (`tests/test_w3b_store_interface.py`): the name `sqlite3` is
referenced in the tracked backend only under `backend/store/`; a planted
`import sqlite3 as s; s.connect(p)` in any other backend module is named.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from abc import ABC, abstractmethod
from typing import Any, Callable, Iterable, NamedTuple, Sequence, Union

# The Postgres driver is optional at import: the desktop bundle excludes it
# (D13) and a SQLite-only host need not install it. Without it the neutral
# names cover SQLite alone and a Postgres target is refused by name.
try:
    import psycopg as _psycopg
    from psycopg import errors as _pg_errors
except ImportError:                                   # pragma: no cover - the bundle
    _psycopg = None
    _pg_errors = None

POSTGRES_DRIVER_AVAILABLE = _psycopg is not None

# ---------------------------------------------------------------------------
# Neutral names: tuples of the drivers' classes, usable in an `except`, so
# callers write `except IntegrityError:` and never learn which driver raised.
# `OperationalError` carries the two Postgres errors the backend's uses of
# sqlite3.OperationalError stand for: a table that does not exist (as_of's
# "no such table") and a lock that did not come in time (D5: lock_timeout as
# the busy timeout's twin).
# ---------------------------------------------------------------------------

Error: tuple = (sqlite3.Error,) + ((_psycopg.Error,) if _psycopg else ())
IntegrityError: tuple = (sqlite3.IntegrityError,) + ((_psycopg.IntegrityError,) if _psycopg else ())
OperationalError: tuple = (sqlite3.OperationalError,) + (
    (_psycopg.OperationalError, _pg_errors.UndefinedTable, _pg_errors.LockNotAvailable) if _psycopg else ())


class PgRow(tuple):
    """The Postgres backend's row: name AND index access, `keys()`,
    `dict(row)`, `tuple(row)` and unpacking, the idioms the backend uses
    (≈390 by name, ≈20 by position, ≈30 `.keys()`, four `dict(row)`, one
    unpacking: the establish's count). No psycopg row factory gives both
    accesses (measured), so this tuple subclass does; it lives here, not in
    the backend module, so `is_row()` knows it without importing the driver.
    (A tuple subclass cannot declare slots; the names ride the instance.)"""

    def __new__(cls, values, names):
        obj = super().__new__(cls, values)
        obj._names = names
        return obj

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return tuple.__getitem__(self, self._names.index(key))
            except ValueError:
                raise IndexError(f"No item with that key: {key}") from None   # sqlite3.Row's error
        return tuple.__getitem__(self, key)

    def keys(self) -> list[str]:
        return list(self._names)


#: The row types a `Database` returns: `sqlite3.Row` on SQLite (unchanged
#: from before W3b), `PgRow` on Postgres. `Row` is the annotation.
Row = Union[sqlite3.Row, PgRow]
ROW_TYPES: tuple = (sqlite3.Row, PgRow)


def is_row(value: Any) -> bool:
    """True for a row object of any backend (name AND index access)."""
    return isinstance(value, ROW_TYPES)


class ColumnInfo(NamedTuple):
    """One column as `PRAGMA table_info` describes it: the same six fields in
    the same order, so `tuple(info)` equals the pragma's row."""
    cid: int
    name: str
    type: str
    notnull: int
    dflt_value: Any
    pk: int


class BackendUnavailable(RuntimeError):
    """A store backend that is named but not built or not installed."""


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def check_identifier(name: str) -> str:
    """A bare SQL identifier (a table name the code owns), or ValueError."""
    if not isinstance(name, str) or not _IDENTIFIER.match(name):
        raise ValueError(f"not a bare identifier: {name!r}")
    return name


# ---------------------------------------------------------------------------
# The trace hook: the freeze guard's derivation source on every backend.
# ---------------------------------------------------------------------------

TraceCallback = Callable[[str], None]
_trace_lock = threading.Lock()
_trace_callbacks: list[TraceCallback] = []


def add_trace(callback: TraceCallback) -> None:
    """Report every statement any `Database` runs to `callback(sql)`."""
    with _trace_lock:
        if callback not in _trace_callbacks:
            _trace_callbacks.append(callback)


def remove_trace(callback: TraceCallback) -> None:
    with _trace_lock:
        try:
            _trace_callbacks.remove(callback)
        except ValueError:
            pass


def tracing() -> bool:
    return bool(_trace_callbacks)


def _notify(sql: str) -> None:
    for cb in list(_trace_callbacks):
        cb(sql)


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------

class Database(ABC):
    """One open store. The contract is sqlite3's: see the module docstring."""

    #: "sqlite" | "postgres"
    backend: str = ""

    # -- statements ---------------------------------------------------------

    @abstractmethod
    def execute(self, sql: str, params: Sequence | None = None):
        """Run one statement; return a cursor (iterable, `fetchone`,
        `fetchall`, `rowcount`)."""

    @abstractmethod
    def executemany(self, sql: str, seq_of_params: Iterable[Sequence]):
        """Run one statement once per parameter set; the cursor's `rowcount`
        is the total of rows affected."""

    @abstractmethod
    def executescript(self, script: str) -> None:
        """Run several statements (DDL, as a rule), committing any pending
        transaction first, as sqlite3 does."""

    @abstractmethod
    def cursor(self) -> "TracingCursor":
        """A cursor whose `execute`/`executemany` report to the trace hook
        like the connection's own (the trail store writes through one)."""

    # -- transactions -------------------------------------------------------

    @abstractmethod
    def commit(self) -> None: ...

    @abstractmethod
    def rollback(self) -> None: ...

    @property
    @abstractmethod
    def in_transaction(self) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...

    # -- introspection ------------------------------------------------------

    @abstractmethod
    def columns(self, table: str) -> list[ColumnInfo]:
        """The table's columns as `PRAGMA table_info` describes them."""

    @abstractmethod
    def has_table(self, name: str) -> bool: ...

    @abstractmethod
    def tables(self) -> list[str]:
        """The store's table names, sorted, without the driver's own
        (`sqlite_sequence`; the Postgres schema's `nexus_sequence`): the twin
        of `SELECT name FROM sqlite_master WHERE type = 'table'`."""

    @abstractmethod
    def describe(self) -> str:
        """The store's human name: a file path, or a database and schema."""

    @abstractmethod
    def changes(self) -> int:
        """Rows modified on this connection since it opened (sqlite3's
        `total_changes`)."""

    # -- the connection-manager protocol, sqlite3's ------------------------

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


class TracingCursor:
    """A driver cursor whose statements are reported to the trace hook, so
    a write issued through `db.cursor()` is seen exactly as one issued
    through `db.execute()`. Every other attribute (`fetchone`, `fetchall`,
    `rowcount`, `description`, iteration) is the driver cursor's."""

    def __init__(self, raw_cursor):
        self._cur = raw_cursor

    def execute(self, sql: str, params: Sequence | None = None):
        if tracing():
            _notify(sql)
        if params is None:
            self._cur.execute(sql)
        else:
            self._cur.execute(sql, params)
        return self

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence]):
        if tracing():
            seq_of_params = list(seq_of_params)
            for _ in seq_of_params:
                _notify(sql)
        self._cur.executemany(sql, seq_of_params)
        return self

    def __iter__(self):
        return iter(self._cur)

    def __getattr__(self, name: str):
        return getattr(self._cur, name)


class ConnHandle:
    """Wraps a `Database` so both `.execute(...)` (attribute style) AND
    `with handle() as c:` (callable style) work: the latter is required by
    Fix A's deduplicator and the dedup-confirm endpoint, which scope a
    transaction with `with self.store.conn() as c:`. Every other attribute
    is forwarded, so `store.conn.execute(...)` keeps working unchanged.
    (The pre-W3b `_SQLiteConnHandle` of `tiered_store.py`, generalised over
    the interface.)"""

    def __init__(self, db: Database):
        self._conn = db

    def __call__(self) -> Database:
        return self._conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, *args):
        return self._conn.__exit__(*args)


__all__ = ["BackendUnavailable", "ColumnInfo", "ConnHandle", "Database", "Error",
           "IntegrityError", "OperationalError", "POSTGRES_DRIVER_AVAILABLE", "PgRow", "ROW_TYPES",
           "Row", "TracingCursor", "add_trace", "check_identifier", "is_row", "remove_trace",
           "tracing"]
