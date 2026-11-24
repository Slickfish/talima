"""The SQLite backend: the connection every store opened before W3b, behind
the interface, byte for byte (`run/specs/W3b.md` D2).

`sqlite3.connect(path, check_same_thread=False)` and `row_factory =
sqlite3.Row` were the only two things the six connect sites did (agent A's
table: no `timeout=`, no `uri=`, no `isolation_level=`, no PRAGMA set), so
that is all this does. The measured defaults stand: `journal_mode=delete`,
`synchronous=FULL`, `foreign_keys=0`, `busy_timeout` 5000 ms,
`isolation_level=''` (an implicit BEGIN before a writing statement, none
before a read).

Unknown attributes are forwarded to the raw connection, so a test that
reaches for a SQLite-only API (`set_trace_callback`, `backup`, `iterdump`,
`PRAGMA` through `execute`) keeps working on this backend; the Postgres
backend forwards nothing, which is how a module that still leans on a
SQLite-only attribute is found by the suite under it (part 2).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

from backend.store.base import ColumnInfo, Database, TracingCursor, _notify, check_identifier, tracing


class SQLiteDatabase(Database):
    backend = "sqlite"

    def __init__(self, raw: sqlite3.Connection, path: Path):
        self.raw = raw
        self.path = Path(path)

    @classmethod
    def open_path(cls, path: "Path | str") -> "SQLiteDatabase":
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False — the KAIROS daemon runs on a different
        # thread to the request handler that opened the connection; SQLite
        # rejects cross-thread access by default (schema.connect's reason,
        # kept verbatim).
        raw = sqlite3.connect(path, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        return cls(raw, path)

    @classmethod
    def wrap(cls, raw: sqlite3.Connection) -> "SQLiteDatabase":
        """The interface over a connection somebody else opened: the
        answered-for raw opens (a temp copy a script migrates on purpose,
        `scripts/verify_packet_f.py`; a kill-switch test's scratch store) hand
        `schema.migrate()` their own connection. The path is the main
        database's, as SQLite names it (`''` for `:memory:`). The connection
        must already return `sqlite3.Row` rows (every store method reads
        rows by name); one that does not is refused by name here rather
        than failing inside a migration."""
        if raw.row_factory is not sqlite3.Row:
            raise TypeError("a raw connection handed to the store interface must have "
                            "row_factory = sqlite3.Row (every store method reads rows by name)")
        row = raw.execute("PRAGMA database_list").fetchone()
        return cls(raw, Path(row[2] or ":memory:"))

    # -- statements ---------------------------------------------------------

    def execute(self, sql: str, params: Sequence | None = None):
        if tracing():
            _notify(sql)
        if params is None:
            return self.raw.execute(sql)
        return self.raw.execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence]):
        if tracing():
            seq_of_params = list(seq_of_params)
            for _ in seq_of_params:
                _notify(sql)
        return self.raw.executemany(sql, seq_of_params)

    def executescript(self, script: str) -> None:
        if tracing():
            _notify(script)
        self.raw.executescript(script)

    def cursor(self) -> TracingCursor:
        return TracingCursor(self.raw.cursor())

    # -- transactions -------------------------------------------------------

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    @property
    def in_transaction(self) -> bool:
        return self.raw.in_transaction

    def close(self) -> None:
        self.raw.close()

    # -- introspection ------------------------------------------------------

    def columns(self, table: str) -> list[ColumnInfo]:
        check_identifier(table)
        return [ColumnInfo(*tuple(r)) for r in self.raw.execute(f"PRAGMA table_info({table})")]

    def has_table(self, name: str) -> bool:
        return self.raw.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    def tables(self) -> list[str]:
        return sorted(r[0] for r in self.raw.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"))

    def describe(self) -> str:
        return str(self.path)

    def changes(self) -> int:
        return self.raw.total_changes

    # -- everything else the raw connection offers --------------------------

    def __getattr__(self, name: str):
        # Only reached for names this class does not define.
        return getattr(self.raw, name)


__all__ = ["SQLiteDatabase"]
