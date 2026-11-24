"""`backend.store` — the store interface and its backends (W3b).

    from backend.store import connect, Database, Row, StoreTarget

`connect(target_or_path)` opens a store: a path (or a SQLite `StoreTarget`)
through `SQLiteDatabase`, today's connection exactly; a Postgres target is
part 2's and is refused by name until it exists (`BackendUnavailable`).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.store.base import (POSTGRES_DRIVER_AVAILABLE, ROW_TYPES, BackendUnavailable, ColumnInfo,
                                ConnHandle, Database, Error, IntegrityError, OperationalError, PgRow, Row,
                                TracingCursor, add_trace, check_identifier, is_row, remove_trace, tracing)
from backend.store.dialect import DialectError
from backend.store.sqlite_backend import SQLiteDatabase
from backend.store.target import (BACKENDS, EXIT_STORE_CONFIG, POSTGRES, SQLITE, STORE_SETTING, URL_SETTING,
                                  SecretNotPassable, StoreConfigError, StoreTarget, configured_store_target,
                                  database_url, redacted_error, redacted_text, redacted_url, store_backend_name,
                                  store_config_reason, url_secrets, url_unreadable_reason, url_without_secrets)


def connect(target: "StoreTarget | Path | str", *, read_only: bool = False) -> Database:
    """Open the store `target` names (a path is SQLite). Every store class
    opens its connection here and nowhere else. `read_only` (W3b part 3,
    D9) is the Postgres form only: a session with
    `default_transaction_read_only = on`, no schema created and no support
    DDL run; SQLite's read-only open is `scripts/_readonly.py`'s (its three
    barriers, law 15) and is refused here by name so the two are never
    confused."""
    t = StoreTarget.of(target)
    if t.backend == SQLITE:
        if read_only:
            raise BackendUnavailable("read_only is the Postgres form; the SQLite read-only open is "
                                     "scripts/_readonly.py's (mode=ro, query_only, the authorizer)")
        return SQLiteDatabase.open_path(t.path)
    if t.backend == POSTGRES:
        if not POSTGRES_DRIVER_AVAILABLE:
            raise BackendUnavailable(
                "store backend 'postgres' needs the psycopg driver, which is not installed here "
                "(requirements.txt pins it; the desktop bundle leaves it out on purpose)")
        from backend.store.postgres_backend import PostgresDatabase    # the driver, only when asked for
        # A URL libpq would not read as it was written is refused here by
        # name, before the driver is called: a password whose `/` or `@` was
        # not percent-encoded (F6b: libpq's own refusal of it prints a
        # fragment of the password that no redaction can know), and a text
        # libpq refuses to parse (D5: an invalid percent-encoding, a raw
        # space, a parameter libpq does not know, whose value may be the
        # mistyped password). The reason names the class, never the text.
        unreadable = url_unreadable_reason(t.url)
        if unreadable:
            raise StoreConfigError(f"the store's URL is not one libpq reads as it was written (none of it is "
                                   f"shown): {unreadable}")
        try:
            return PostgresDatabase.open_target(t, read_only=read_only)
        except Exception as e:
            # The one place NEXUS connects (W3b close, F6). The driver's
            # refusal can quote a part of the URL, and the console's startup
            # traceback printed it. Since D5 the texts libpq gives for a URL
            # it will not PARSE (`invalid percent-encoded token: "…"`, an
            # IPv6 error, an upper-case scheme's `missing "=" after "…"`) no
            # longer get this far; what is left is libpq's check of a parsed
            # URL's options, which quotes the value it will not take
            # (`invalid sslmode value: "…"`), a secret's text where it is
            # that value's too. Re-raised with every secret blanked, of the
            # same class, and not chained to the original.
            clean = redacted_error(e, t.url)
            if clean is e:
                raise
            raise clean from None
    raise BackendUnavailable(f"store backend {t.backend!r} is not built")


def as_database(conn) -> Database:
    """A `Database` for what a caller holds: one already, or a raw SQLite
    connection from one of the answered-for raw opens (wrapped, so the
    migration and introspection methods exist on it). Anything else is a
    TypeError: no second kind of connection is quietly accepted."""
    if isinstance(conn, Database):
        return conn
    if isinstance(conn, sqlite3.Connection):
        return SQLiteDatabase.wrap(conn)
    raise TypeError(f"not a store connection: {type(conn).__name__}")


__all__ = ["BACKENDS", "BackendUnavailable", "ColumnInfo", "ConnHandle", "Database", "DialectError",
           "EXIT_STORE_CONFIG", "Error", "IntegrityError", "OperationalError", "POSTGRES",
           "POSTGRES_DRIVER_AVAILABLE", "PgRow", "ROW_TYPES", "Row", "SQLITE", "SQLiteDatabase",
           "STORE_SETTING", "StoreConfigError", "StoreTarget", "TracingCursor", "URL_SETTING", "add_trace",
           "SecretNotPassable", "as_database", "check_identifier", "configured_store_target", "connect",
           "database_url", "is_row", "redacted_error", "redacted_text", "redacted_url", "remove_trace",
           "store_backend_name", "store_config_reason", "tracing", "url_secrets", "url_unreadable_reason",
           "url_without_secrets"]
