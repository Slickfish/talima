r"""The one read-only SQLite open (W1, law 15).

Every tracked script that reads a store without the right to change it opens
it here. `tests/test_w1_readonly_open.py` forbids building a `file:` URI or
passing `uri` to a SQLite open (as a keyword, positionally, or hidden in
`**` keywords) anywhere else in the tracked backend, scripts and root `.py`
files, and forbids every `sqlite3.connect` or `sqlite3.Connection` (any
spelling, any import alias) in the tracked scripts and root files outside
this module and an answered-for list of deliberate read-write opens of temp
copies.

Three barriers. Each stops a write the other two do not, measured in
`tests/test_w1_readonly_open.py` (U), one case per line below:

1. **`mode=ro` on a percent-encoded URI** (`read_only_uri`). SQLite opens the
   file read-only, and a missing file is an error, never a new database.
   Built by hand (`f"file:{path}?mode=ro"`, 36 scripts at W1's base), a `#`
   or `?` in a folder name ended the path early: SQLite created a stray file
   beside that folder and the connection could write it, and a `%HH` was
   percent-decoded, so another existing database was read instead. The URI
   is built from the absolute path with an EMPTY authority (`uri_for_absolute`):
   SQLite refuses any other authority but `localhost`, and `PurePath.as_uri()`
   puts a host there for a Windows UNC path and `%3F` for an extended-length
   `\\?\` path (W1 part 5f, SF7).
2. **`PRAGMA query_only = 1`**. The barrier for writes that reach SQLite with
   no statement the authorizer sees: an incremental-blob write
   (`Connection.blobopen(..., readonly=False)` then `write`) and
   `BEGIN IMMEDIATE`, which takes the write lock and blocks every other
   writer on the file. Through the authorizer alone both land — the blob
   write changed the store's bytes (measured). Set before the authorizer,
   because the authorizer is what then keeps it on.
3. **An authorizer** (`_authorize`) that refuses every write action and every
   pragma outside a read-only allowlist, at prepare time, so a refused
   statement never reaches the file. It is the barrier against switching
   barrier 2 off: `PRAGMA query_only = 0` sets a value, and the pre-W1
   helpers allowed every pragma, so with `mode=ro` lost a query could switch
   `query_only` off and then write through another pragma (`user_version`,
   `journal_mode`; demonstrated). `ANALYZE` is in `WRITE_ACTIONS` because it
   writes `sqlite_stat1`: on a store where that table already exists the
   pre-W1 set let it run and the file changed; on a fresh store SQLite
   itself refuses it, which is why the first test of it proved nothing.

`assert_write_refused` is the selftest the instance scripts print. It issues
an UPDATE that matches no row on a table the store MUST have
(`lineage_records` for kb.db, `archive_blocks` for archive.db), so the table
is resolved before the authorizer is consulted: on the wrong file (an empty
one; a stray `C` beside a folder named `C#`) SQLite says "no such table" and
the selftest raises instead of printing a refusal. The earlier probe was a
CREATE TABLE, which the authorizer refused at prepare on any file at all, so
a selftest on a misopened file printed "write refused" exactly as on the
right one. It accepts only the refusal itself; the pre-W1 selftests accepted
any `sqlite3.DatabaseError`, so "no such table" read as "write refused"
there too.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path, PurePath, PureWindowsPath
from urllib.parse import quote

WRITE_ACTIONS = frozenset({
    sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
    sqlite3.SQLITE_REINDEX, sqlite3.SQLITE_CREATE_TEMP_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_INDEX, sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
    sqlite3.SQLITE_CREATE_TEMP_VIEW, sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_TRIGGER,
    sqlite3.SQLITE_DROP_TEMP_VIEW, sqlite3.SQLITE_CREATE_VTABLE,
    sqlite3.SQLITE_DROP_VTABLE,
    # Not in the three pre-W1 copies: ANALYZE writes `sqlite_stat1`. On a
    # store whose statistics table exists the pre-W1 set let it run and the
    # file changed (W1 part 5f, MF1 b).
    sqlite3.SQLITE_ANALYZE,
})

# Introspection pragmas: they take an argument (a table or index name) and
# never write. W1's traced run of all 38 scripts saw `table_info` only.
INTROSPECTION_PRAGMAS = frozenset({
    "table_info", "table_xinfo", "table_list", "index_list", "index_info",
    "index_xinfo", "foreign_key_list", "database_list", "collation_list",
    "function_list", "module_list", "pragma_list", "compile_options",
})

# Pragmas whose VALUE may be read (no argument). Setting any of them, and
# every pragma not named in either set (`optimize`, `incremental_vacuum` and
# `wal_checkpoint` write with no argument at all), is refused.
READABLE_PRAGMAS = frozenset({
    "user_version", "schema_version", "application_id", "data_version",
    "journal_mode", "page_count", "page_size", "freelist_count", "encoding",
    "query_only", "foreign_keys",
})

# Barrier 3's refusal, then barrier 1's / barrier 2's.
WRITE_REFUSAL_MESSAGES = ("not authorized", "attempt to write a readonly database")

# The table a selftest probes, per store: one the store must have.
SELFTEST_TABLES = {"kb.db": "lineage_records", "archive.db": "archive_blocks"}

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_EXTENDED_PREFIX = "\\\\?\\"          # \\?\  (Win32's extended-length prefix)
_EXTENDED_UNC = "UNC\\"               # \\?\UNC\host\share\x  ->  \\host\share\x


def _authorize(action, arg1, arg2, _db, _trigger):
    if action in WRITE_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA:
        name = (arg1 or "").lower()
        if name in INTROSPECTION_PRAGMAS:
            return sqlite3.SQLITE_OK
        if name in READABLE_PRAGMAS and arg2 is None:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def refuse_writes(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Barriers 2 and 3 on an open connection, in that order.

    Kept apart from the open so a test can run them on a connection that has
    no `mode=ro` (barrier 1 absent) and show what each one stops. The two are
    not interchangeable: `PRAGMA query_only = 1` (barrier 2) is what refuses
    an incremental-blob write and `BEGIN IMMEDIATE`, which the authorizer
    never sees as a write action; the authorizer (barrier 3) is what refuses
    every writing statement at prepare and keeps barrier 2 on, because
    `PRAGMA query_only = 0` is a pragma that sets a value. A test that wants
    the authorizer ALONE calls `conn.set_authorizer(_authorize)` itself.
    """
    conn.execute("PRAGMA query_only = 1")
    conn.set_authorizer(_authorize)
    return conn


def uri_for_absolute(path: PurePath) -> str:
    """The `file:` URI for an ABSOLUTE path of either flavour, with an EMPTY
    authority. SQLite's URI parser refuses any authority but empty or
    `localhost` ("invalid uri authority: ...") unless built with
    SQLITE_ALLOW_URI_AUTHORITY, before any file is touched.

    `PurePath.as_uri()` is kept byte for byte where it already has none: a
    POSIX path and a Windows drive-letter path (`file:///C:/...`, which CI
    run 17 established on Windows). Where it does not:

    - a UNC path `\\\\host\\share\\x` becomes `file:////host/share/x`: the
      authority is empty and the URI path is `//host/share/x`, the UNC path
      with forward slashes, which Win32 reads as separators (`as_uri()` gives
      `file://host/share/x`, authority `host`);
    - an extended-length path (`\\\\?\\C:\\x`, `\\\\?\\UNC\\host\\share\\x`)
      has that prefix removed and continues as the drive-letter or UNC case
      (`as_uri()` gives `file://%3F/C%3A/x`, authority `%3F`). The prefix
      exists to bypass Win32's path normalisation; a URI path cannot carry
      it, since SQLite would hand Win32 `//?/...` with forward slashes, which
      is not read as the prefix. The file is named without it.

    The prefix is read off `str(path)`, never off `PurePath.drive`, whose
    spelling of an extended-length drive is pathlib's own and changed between
    3.11 and 3.12. A relative Windows path is refused as `as_uri()` refuses
    it: `read_only_uri` makes every path absolute first.

    Measured on Linux against SQLite's parser (`tests/test_w1_readonly_open.py`):
    the planted authorities `nas` and `%3F` are refused by name, and the forms
    built here are parsed as paths (the error is then about the absent file).
    That Windows then opens `//host/share/x` and `C:/x` through these URIs
    is reasoned from Win32's slash handling, not run here.
    """
    if isinstance(path, PureWindowsPath):
        spelled = str(path)
        if spelled.startswith(_EXTENDED_PREFIX):
            spelled = spelled[len(_EXTENDED_PREFIX):]
            if spelled[:len(_EXTENDED_UNC)].upper() == _EXTENDED_UNC:
                spelled = "\\\\" + spelled[len(_EXTENDED_UNC):]
            path = PureWindowsPath(spelled)
        if not path.is_absolute():
            raise ValueError(f"relative path can't be expressed as a file URI: {path!r}")
        if len(path.drive) == 2 and path.drive[1] == ":":
            return path.as_uri()                     # file:///C:/..., as as_uri() builds it
        # UNC: as_posix() is //host/share/...; `file://` + that keeps the
        # authority empty and the path //host/share/...
        return "file://" + quote(path.as_posix())
    return path.as_uri()


def read_only_uri(path: "Path | str") -> str:
    """The percent-encoded `mode=ro` URI for `path`.

    The path is made absolute against the CWD with `os.path.abspath` (the
    join and `normpath`, nothing else), so a relative path names the file it
    names everywhere else. Not `Path.resolve()`: that calls
    `os.path.realpath`, which on Windows returns the final path by handle, so
    a store on a mapped network drive (`Z:\\...`) came back as its UNC path
    and its URI carried an authority SQLite refuses (SF7; reasoned from
    `ntpath.realpath`'s source, not run on Windows). A path reached through a
    symlink is likewise named as given; it is the same file.
    """
    return uri_for_absolute(Path(os.path.abspath(path))) + "?mode=ro"


def open_read_only(path: "Path | str", *,
                   row_factory=sqlite3.Row, url_ok: bool = False):
    """The store at `path`, read-only by all three barriers.

    `row_factory` is the caller's: the default `sqlite3.Row` is what the
    instance scripts that already shared a helper used; a script that read
    tuples before W1 passes `row_factory=None` and keeps them.

    W3b part 3 (D9): a database URL opens the hosted store through
    `open_read_only_postgres` and returns a `backend.store.Database`, but
    only for a script that says it takes one (`url_ok=True`: the instance
    scripts the runbook names); every other script handed a URL is refused
    by name, so a tool written for SQLite files never runs its SQLite-shaped
    checks against a database by accident."""
    if is_url(path):
        if not url_ok:
            raise ReadOnlyRefused(
                f"this script reads SQLite stores only; a database URL was given ({redacted(str(path))}). "
                f"The scripts that take one are replay_check.py, archive_pregate.py, "
                f"archive_consolidation.py, cp_b49_tally.py, as2_instance_windows.py, diag_downstream.py, "
                f"export_postgres_to_sqlite.py and backup.py (its --database-url)")
        return open_read_only_postgres(str(path))
    conn = sqlite3.connect(read_only_uri(path), uri=True)
    if row_factory is not None:
        conn.row_factory = row_factory
    return refuse_writes(conn)


def assert_write_refused(conn: sqlite3.Connection, *, table: str) -> str:
    """Attempt one write on `table` and return SQLite's refusal. Anything
    else, a write that runs or an error that is not a refusal, raises
    AssertionError.

    `table` is one the store must have (`SELFTEST_TABLES`: `lineage_records`
    for kb.db, `archive_blocks` for archive.db). The probe is
    `UPDATE <table> SET rowid = rowid WHERE 0`: it matches no row, so even
    an unrefused run changes nothing, and SQLite resolves the table before
    it consults the authorizer, so a connection on the wrong file says
    "no such table: <table>" and this raises instead of returning a refusal
    (the earlier CREATE TABLE probe was refused at prepare on any file at
    all). Every NEXUS store table is a rowid table; a WITHOUT ROWID table
    would be reported as "no such column: rowid", which is the right side
    to fail on.
    """
    if not _IDENTIFIER.match(table):
        raise ValueError(f"table must be a bare identifier, got {table!r}")
    if getattr(conn, "backend", None) == "postgres":
        return _assert_write_refused_postgres(conn, table)
    try:
        conn.execute(f"UPDATE {table} SET rowid = rowid WHERE 0")
    except sqlite3.DatabaseError as e:
        message = str(e)
        if any(m in message for m in WRITE_REFUSAL_MESSAGES):
            return f"{type(e).__name__}: {message}"
        raise AssertionError(
            f"the write failed, but not as a refusal ({type(e).__name__}: "
            f"{message}); the connection is not on a store that has {table}: "
            f"the wrong file, or not a NEXUS store") from e
    conn.rollback()
    raise AssertionError(
        f"a write succeeded on a connection meant to be read-only "
        f"(UPDATE {table} ran unrefused)")




# ---------------------------------------------------------------------------
# W3b part 3 (run/specs/W3b.md D9) — the same open where the store is a
# Postgres database. Three barriers here too, each stopping a write the
# others do not, measured on the scratch cluster in W3b's establish:
#
# 1. **A read-only session.** `default_transaction_read_only = on` in the
#    connection's options (`backend.store.connect(..., read_only=True)`), so
#    every transaction starts read-only and a write is refused with SQLSTATE
#    25006. A role can undo this (`SET transaction_read_only = off` is
#    allowed to any role: measured), which is why barrier 3 exists.
# 2. **`SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`**, the same
#    setting asserted on the session after connecting, so a URL whose own
#    options reset it is still read-only.
# 3. **The privilege set, over every role the session can act as, across the
#    store's whole database.** A role that could write is refused BY NAME
#    before the first query, because a setting it can switch off is not a
#    barrier; only a privilege it does not hold is. "Could write" is the
#    class (W3b close: F1, F1b, and the delta check's D1 to D4), never a list
#    of named privileges:
#
#    *Who.* The SESSION user and the current user, each with every role it
#    is a member of (`pg_has_role(..., 'MEMBER')` over `pg_roles`: direct or
#    indirect, whatever the membership's INHERIT and SET options, because an
#    `INHERIT FALSE` member still `SET ROLE`s to the writer and a grant can
#    be changed). Both, because PostgreSQL judges SET ROLE from the session
#    user: a connection-time setting (the URL's `-c role=`, `ALTER ROLE ...
#    SET role`) names `current_user` and takes nothing away from the login
#    behind it (D1; `session_user` itself cannot be named by a setting:
#    measured).
#
#    *Where.* Everything in the database that did not come with PostgreSQL
#    itself (OID 16384 and above), outside other sessions' temporary
#    schemas; not the store's schema alone, and not only the schemas the
#    role holds USAGE on. A view, a rule or a trigger in another schema
#    writes the store with its OWNER's rights (D3); the store need not be
#    first on the search path (D4); and USAGE is no barrier, because it is
#    asked when a name is looked up and a view or a pre-parsed function has
#    already looked it up (measured: a SECURITY DEFINER function in a schema
#    the role cannot name runs through a view that calls it, and an INSERT
#    privilege there is used through a `BEGIN ATOMIC` function).
#
#    *What.* Any one of these, on any such role:
#    - a superuser (named alone: it can do all the rest);
#    - `pg_write_all_data`, `pg_write_server_files` or
#      `pg_execute_server_program` (the last two write as the server's own
#      OS user, whose files the store is), or CREATEROLE (before PostgreSQL
#      16 such a role can grant itself any role's membership);
#    - ALTER SYSTEM on a configuration parameter (PostgreSQL 15 and later:
#      it rewrites the server's own configuration, and a read-only
#      transaction does not refuse it);
#    - the database's owner; the owner of ANY object in the database, a
#      function among them (`pg_shdepend`: an owner re-grants itself what
#      was revoked, and DROP ... CASCADE takes what depends on the object
#      with it, a store table's trigger for one; a session's temporary
#      objects are not counted);
#    - CREATE on any schema, the system's own included, or on the database
#      (a schema named after the server's own role comes first on the
#      default search_path and would shadow a store in `public`);
#    - INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER or REFERENCES on any
#      relation, or column-level INSERT, UPDATE or REFERENCES (REFERENCES
#      lets a foreign key put constraint triggers on the store's table);
#      USAGE or UPDATE on any sequence;
#    - a default privilege granting a table write on FUTURE tables
#      (`pg_default_acl`: a schema's, or one that names no schema), because
#      the store creates its lazy tables after the role was checked;
#    - EXECUTE on any SECURITY DEFINER function, WHOEVER owns it: it runs
#      with its owner's rights and its body is not read, so a chain of two
#      (the first owned by a role that cannot write, calling a second owned
#      by one that can) is refused with the first. A function the role may
#      NOT execute does not refuse it: revoking EXECUTE from PUBLIC is the
#      remedy.
#
#    A role that sees no schema on its search path at all is refused too:
#    there is no store there that it could read.
#
#    *The query's own names.* It runs with `search_path` pinned to
#    `pg_catalog` for its one transaction, so a schema the URL or the role
#    puts in front of `pg_catalog` cannot answer for `pg_roles`.
#
#    **Not read, declared:** MAINTAIN (PostgreSQL 17: it locks and rewrites
#    storage, it does not change a row); `pg_signal_backend` (it ends
#    sessions, the lock's among them: BACKLOG W3b-F17); what a function's
#    own code does when that code is not checked by the privilege system
#    (C and untrusted-language functions: PostgreSQL revokes its own such
#    functions, `lo_export` for one, from PUBLIC, and a role granted one
#    has been handed the write on purpose); a SECURITY DEFINER function
#    that a planted mechanism runs for the caller without asking the
#    caller's EXECUTE (the second delta check's E2, measured: an
#    aggregate's transition function, whose EXECUTE is asked of the
#    aggregate's owner, and an event trigger's function, which fires on
#    any session's DDL, a temporary table's included; each needs a writer
#    or a superuser to plant it: BACKLOG W3b-H2); a large object the role
#    was granted UPDATE on, and USAGE on a foreign-data wrapper, which are
#    writes in the database and not in the store (E3: W3b-H3);
#    credentials held outside the privilege system (a foreign server's
#    user mapping, a writer's password); and what PostgreSQL lets every
#    login do with no privilege at all: change its own role's settings
#    and password, make temporary objects and large objects of its own
#    (none of them the store, unless an event trigger of the kind above
#    is planted), and take an advisory lock (the single-writer lock's key
#    among them: a reader that holds it keeps NEXUS from starting, the
#    same family as W3b-F17: W3b-D16).
#
#    Every fact is one a role holding only SELECT can read (measured on the
#    cluster), and `tests/test_w3b_readonly.py` plants one role per path:
#    refused by name here, and on the check as it stood before, opened with
#    its write landing.
#
# The selftest issues `UPDATE <table> SET <pk> = <pk> WHERE false` and
# accepts only SQLSTATE 25006 (read_only_sql_transaction) or 42501
# (insufficient_privilege). The instance scripts name the store on their
# first line with the URL's secrets redacted (law 15; every secret libpq
# reads, the query's `password=` among them: W3b close, F6), and the helpers
# below answer the questions a script asks of either kind of store: does it
# exist, which tables and columns does it have.
# ---------------------------------------------------------------------------

URL_SCHEMES = ("postgresql://", "postgres://")
READ_ONLY_SQLSTATES = ("25006", "42501")

#: Barrier 3: the facts. One row per write path a role the session can act as
#: holds, as (ord, role, code, item, detail); `write_path_reasons` makes the
#: sentences. Hand-written Postgres catalog SQL (never SQLite-spelled), so it
#: runs on the driver connection (`db.raw`), not through the dialect, and
#: with `search_path` pinned to `pg_catalog` (`write_paths`). No row means
#: no role the session can act as holds any of the write paths barrier 3
#: names, on any relation, function, schema or setting of this database.
WRITE_PATHS_SQL = r"""
WITH members AS (
  SELECT r.oid, r.rolname, r.rolsuper, r.rolcreaterole
  FROM pg_roles r
  WHERE pg_has_role(current_user, r.oid, 'MEMBER') OR pg_has_role(session_user, r.oid, 'MEMBER')
),
nsp AS (
  -- every schema but the sessions' temporary ones
  SELECT n.oid, n.nspname FROM pg_namespace n WHERE n.nspname !~ '^pg_(temp|toast_temp)_'
),
rels AS (
  -- Every relation that did not come with PostgreSQL itself (OID 16384 and
  -- above: its own catalogs and information_schema lie below), WHETHER OR
  -- NOT the role holds USAGE on the schema: USAGE is asked when a name is
  -- looked up, and a view or a pre-parsed function in another schema has
  -- already looked it up (measured: the write lands).
  SELECT m.oid AS member, m.rolname, c.oid, c.relkind, n.nspname || '.' || c.relname AS name
  FROM members m CROSS JOIN pg_class c JOIN nsp n ON n.oid = c.relnamespace
  WHERE c.oid >= 16384 AND c.relkind IN ('r','p','v','m','f','S')
),
held AS MATERIALIZED (
  -- the pairs worth naming: two calls each, the privileges named below
  SELECT t.* FROM rels t
  WHERE t.relkind = 'S'
     OR has_table_privilege(t.member, t.oid, 'INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES')
     OR has_any_column_privilege(t.member, t.oid, 'INSERT, UPDATE, REFERENCES')
)
SELECT ord, role, code, item, detail FROM (
  SELECT 1 AS ord, m.rolname::text AS role, 'superuser' AS code, NULL::text AS item, NULL::text AS detail
    FROM members m WHERE m.rolsuper
  UNION ALL
  SELECT 2, session_user::text, 'member', m.rolname::text, NULL
    FROM members m
    WHERE m.rolname IN ('pg_write_all_data', 'pg_write_server_files', 'pg_execute_server_program')
  UNION ALL
  SELECT 2, m.rolname::text, 'createrole', NULL, NULL FROM members m WHERE m.rolcreaterole
  UNION ALL
  SELECT 3, m.rolname::text, 'owns_database', current_database()::text, NULL
    FROM members m JOIN pg_database d ON d.datdba = m.oid AND d.datname = current_database()
  UNION ALL
  SELECT 4, m.rolname::text, 'owns', o.type || ' ' || o.identity, NULL
    FROM members m
    JOIN pg_shdepend d ON d.refclassid = 'pg_authid'::regclass AND d.refobjid = m.oid AND d.deptype = 'o'
                      AND d.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
    CROSS JOIN LATERAL pg_identify_object(d.classid, d.objid, d.objsubid) o
    -- another session's temporary objects are not the database's
    WHERE o.identity IS NOT NULL AND (o.schema IS NULL OR o.schema !~ '^pg_(temp|toast_temp)_')
  UNION ALL
  SELECT 6, m.rolname::text, 'create_schema', n.nspname::text, NULL
    FROM members m JOIN nsp n ON has_schema_privilege(m.oid, n.oid, 'CREATE')
  UNION ALL
  SELECT 6, m.rolname::text, 'create_database', current_database()::text, NULL
    FROM members m WHERE has_database_privilege(m.oid, current_database(), 'CREATE')
  UNION ALL
  SELECT 7, t.rolname::text, 'table', t.name, p.priv
    FROM held t
    CROSS JOIN LATERAL (VALUES ('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),('TRIGGER'),('REFERENCES')) AS p(priv)
    WHERE t.relkind <> 'S' AND has_table_privilege(t.member, t.oid, p.priv)
  UNION ALL
  SELECT 8, t.rolname::text, 'column', t.name, p.priv
    FROM held t CROSS JOIN LATERAL (VALUES ('INSERT'),('UPDATE'),('REFERENCES')) AS p(priv)
    WHERE t.relkind <> 'S' AND NOT has_table_privilege(t.member, t.oid, p.priv)
      AND has_any_column_privilege(t.member, t.oid, p.priv)
  UNION ALL
  SELECT 9, t.rolname::text, 'sequence', t.name, p.priv
    FROM held t CROSS JOIN LATERAL (VALUES ('USAGE'),('UPDATE')) AS p(priv)
    WHERE t.relkind = 'S' AND has_sequence_privilege(t.member, t.oid, p.priv)
  UNION ALL
  SELECT 10, g.who::text, 'default',
         CASE WHEN d.defaclnamespace = 0 THEN 'in every schema' ELSE 'in schema ' || n.nspname END
           || ' by a default privilege of the role ' || pg_get_userbyid(d.defaclrole),
         a.privilege_type::text
    FROM pg_default_acl d
    LEFT JOIN nsp n ON n.oid = d.defaclnamespace
    CROSS JOIN LATERAL aclexplode(d.defaclacl) a
    CROSS JOIN LATERAL (SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC'
                               ELSE (SELECT m.rolname FROM members m WHERE m.oid = a.grantee) END AS who) g
    WHERE d.defaclobjtype = 'r' AND g.who IS NOT NULL
      AND a.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE','TRIGGER','REFERENCES')
      AND (n.oid IS NOT NULL
           -- one that names no schema covers every table its role creates, wherever it may create
           OR (d.defaclnamespace = 0
               AND EXISTS (SELECT 1 FROM nsp s WHERE has_schema_privilege(d.defaclrole, s.oid, 'CREATE'))))
  UNION ALL
  SELECT 11, m.rolname::text, 'definer',
         n.nspname || '.' || p.proname || ' (owner ' || pg_get_userbyid(p.proowner) || ')', NULL
    FROM members m CROSS JOIN pg_proc p JOIN nsp n ON n.oid = p.pronamespace
    WHERE p.prosecdef AND p.oid >= 16384 AND has_function_privilege(m.oid, p.oid, 'EXECUTE')
) q ORDER BY ord, role, code, item, detail
""".strip()

#: The same, for the one privilege PostgreSQL 15 added outside every schema:
#: ALTER SYSTEM on a configuration parameter (`pg_parameter_acl`; an older
#: server has no such privilege and is not asked).
PARAMETER_PATHS_SQL = r"""
SELECT 2 AS ord, r.rolname::text AS role, 'alter_system' AS code, pa.parname::text AS item, NULL::text AS detail
FROM pg_roles r CROSS JOIN pg_parameter_acl pa
WHERE (pg_has_role(current_user, r.oid, 'MEMBER') OR pg_has_role(session_user, r.oid, 'MEMBER'))
  AND has_parameter_privilege(r.oid, pa.parname, 'ALTER SYSTEM')
""".strip()
PARAMETER_PRIVILEGES_SINCE = 150000

#: A superuser session is answered by this alone: every other fact follows
#: from it, and the facts' query would read every role on the server.
SUPERUSER_SQL = ("SELECT r.rolname::text FROM pg_roles r "
                 "WHERE r.rolsuper AND r.rolname IN (session_user, current_user) ORDER BY 1")

#: How many names one reason lists before it counts the rest.
NAMED_PER_REASON = 5

SERVER_OS_USER = " (it writes or runs programs as the server's own OS user, whose files the store is)"
NO_SCHEMA_REASON = ("the role sees no schema on its search path (it holds USAGE on none of them), "
                    "so there is no store here that it can read")


def _listed(names) -> str:
    names = sorted(set(names))
    shown = ", ".join(names[:NAMED_PER_REASON])
    return shown if len(names) <= NAMED_PER_REASON else f"{shown} and {len(names) - NAMED_PER_REASON} more"


def write_path_reasons(rows) -> list[str]:
    """The facts as sentences, one per (role, kind), each naming the role the
    write comes through; a superuser among them is named alone. `rows` are
    `WRITE_PATHS_SQL`'s: (ord, role, code, item, detail)."""
    grouped: dict[tuple, tuple[list, list]] = {}
    for row in rows:
        ord_, role, code, item, detail = row[0], row[1], row[2], row[3], row[4]
        # one sentence per predefined role and per default privilege; one per kind otherwise
        own = item if code in ("member", "default") else None
        items, details = grouped.setdefault((ord_, role, code, own), ([], []))
        if item is not None:
            items.append(item)
        if detail is not None:
            details.append(detail)
    reasons = []
    in_order = sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2], kv[0][3] or ""))
    for (_ord, role, code, _own), (items, details) in in_order:
        names, privs = _listed(items), ", ".join(sorted(set(details)))
        reasons.append({
            "superuser": f"the role {role} is a superuser",
            "member": f"the role {role} is a member of {names}"
                      + ("" if names == "pg_write_all_data" else SERVER_OS_USER),
            "createrole": f"the role {role} has CREATEROLE (before PostgreSQL 16 such a role can grant itself "
                          f"any role's membership, a writer's among them)",
            "alter_system": f"the role {role} may ALTER SYSTEM SET {names} (it rewrites the server's own "
                            f"configuration, which a read-only transaction does not refuse)",
            "owns_database": f"the role {role} owns the database {names}",
            "owns": f"the role {role} owns {names} in this database (an owner can drop or re-grant what it "
                    f"owns, and what depends on it goes with it)",
            "create_schema": f"the role {role} has CREATE on schema {names}",
            "create_database": f"the role {role} may create schemas in the database {names} (one named after "
                               f"the server's own role comes first on the default search_path and would "
                               f"shadow the store's tables)",
            "table": f"the role {role} may write ({privs}) on {names}",
            "column": f"the role {role} may write columns ({privs}) on {names}",
            "sequence": f"the role {role} has {privs} on sequence {names}",
            "default": f"the role {role} is granted {privs} on future tables {names}",
            "definer": f"the role {role} may execute SECURITY DEFINER {names} (it runs with its owner's "
                       f"rights, and its body is not read)",
        }[code])
    superuser = [r for r in reasons if r.endswith(" is a superuser")]
    return superuser or reasons


def write_paths(db) -> list[str]:
    """Barrier 3 on an open hosted store: every reason a role this session
    can act as could change the database, as sentences; empty when none.
    Read-only itself (catalog SELECTs in one transaction whose `search_path`
    is `pg_catalog`, so no schema put in front of it by the URL or the role
    answers for a catalog's name)."""
    raw = db.raw
    with raw.transaction():
        raw.execute("SET LOCAL search_path = pg_catalog, pg_temp")
        superusers = [r[0] for r in raw.execute(SUPERUSER_SQL).fetchall()]
        if superusers:
            return [f"the role {name} is a superuser" for name in superusers]
        rows = list(raw.execute(WRITE_PATHS_SQL).fetchall())
        if raw.info.server_version >= PARAMETER_PRIVILEGES_SINCE:
            rows += raw.execute(PARAMETER_PATHS_SQL).fetchall()
    reasons = write_path_reasons(rows)
    if db.schema is None:
        reasons.append(NO_SCHEMA_REASON)
    return reasons


class ReadOnlyRefused(RuntimeError):
    """The hosted store cannot be opened read-only as asked: the role could
    write, or the script does not take a URL."""


def is_url(value) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(URL_SCHEMES)


def redacted(url: str) -> str:
    """The URL as a line may name it: every secret libpq reads, in the
    userinfo and in the query, blanked (`backend.store.target`, the one
    place that knows a URL's secrets: W3b close, F6)."""
    from backend.store.target import redacted_url
    return redacted_url(url.strip())


def store_label(value) -> str:
    """What a script prints on its first line for the store it reads: the
    absolute posix path (as before), or `postgres <url>` with the password
    redacted."""
    if is_url(value):
        return f"postgres {redacted(str(value))}"
    return Path(os.path.abspath(value)).as_posix()


def read_only_how(value) -> str:
    """The barriers, for the first line's parenthesis."""
    if is_url(value):
        return "a read-only session and a role that cannot write"
    return "mode=ro + write authorizer"


def store_line(value) -> str:
    """Law 15's first line (W4 part 4, the W1 register line): the store a
    script opens, named before anything else it prints, the path or the URL
    with its secrets blanked by `store_label`.

    Each script prints it, first; `open_read_only` does not. The line must
    come before everything a script prints, and the open does not: at the
    base 30 of the 39 scripts tests/test_w1_migrated_scripts_smoke.py runs
    printed a header, a banner or a count before they opened; several open
    two stores (kb.db and archive.db) or open again per check; and some
    open stores that are not the one they were handed (`restore_verify.py`
    its scratch copies, `bundle_smoke.py` its child's store, verify_packet_f
    a temp copy), where a line printed by the open would come mid-output and
    name the wrong store. A script knows the store it was handed before it
    opens it, so the line is its own, and the format is one."""
    return f"database: {store_label(value)} (opened read-only: {read_only_how(value)})"


def store_target(value):
    """The store argument as a store class or the lock takes it: a
    `StoreTarget` for a URL, a `Path` otherwise."""
    if is_url(value):
        from backend.store import StoreTarget
        return StoreTarget.postgres(str(value).strip())
    return Path(value)


def store_exists(value) -> tuple[bool, str]:
    """(exists, why not): a path is a file; a URL is a database that
    answers a read-only connection (the driver's refusal, with the password
    redacted, when it does not). Nothing is created either way."""
    if not is_url(value):
        path = Path(value)
        return (True, "") if path.is_file() else (False, f"{path} is not a file")
    from backend.store import BackendUnavailable, StoreTarget, connect
    try:
        db = connect(StoreTarget.postgres(str(value).strip()), read_only=True)
    except BackendUnavailable as e:
        return False, str(e)
    except Exception as e:                        # noqa: BLE001 — the driver's own refusal, redacted
        return False, f"{redacted(str(value))} does not answer: {type(e).__name__}: {_redact_text(str(e), value)}"
    db.close()
    return True, ""


def _redact_text(text: str, url) -> str:
    """The driver's message with the URL, and each of its secrets raw and
    decoded, blanked wherever they appear (libpq repeats a token, or the
    whole URI, in some errors: W3b close, F6)."""
    from backend.store.target import redacted_text
    return redacted_text(text, str(url).strip())


def open_read_only_postgres(url: str):
    """The hosted store, read-only by the three barriers above; a
    `backend.store.Database` (the scripts' SQLite-spelled SQL translates
    through the dialect). Raises `ReadOnlyRefused` by name for a role that
    could write, having closed the connection."""
    from backend.store import StoreTarget, connect
    db = connect(StoreTarget.postgres(url.strip()), read_only=True)
    try:
        db.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        reasons = write_paths(db)
        if reasons:
            raise ReadOnlyRefused(f"{redacted(url)} cannot be read read-only: " + "; ".join(reasons)
                                  + ". Use a role with SELECT only (the runbook's read-only role).")
    except BaseException:
        db.close()
        raise
    return db


def _assert_write_refused_postgres(db, table: str) -> str:
    """The selftest on a hosted store: one UPDATE matching no row on `table`,
    accepted only as SQLSTATE 25006 or 42501."""
    cols = db.columns(table)
    if not cols:
        raise AssertionError(f"the connection is not on a store that has {table}: the wrong database or schema, "
                             f"or not a NEXUS store")
    pk = next((c.name for c in cols if c.pk), cols[0].name)
    try:
        db.execute(f"UPDATE {table} SET {pk} = {pk} WHERE false")
    except Exception as e:                        # noqa: BLE001 — classified by SQLSTATE below
        state = getattr(e, "sqlstate", None)
        if state in READ_ONLY_SQLSTATES:
            return f"{type(e).__name__}: {e}".strip()
        raise AssertionError(f"the write failed, but not as a refusal ({type(e).__name__}: {e})") from e
    db.rollback()
    raise AssertionError(f"a write succeeded on a connection meant to be read-only (UPDATE {table} ran unrefused)")


def table_exists(conn, name: str) -> bool:
    """Whether `conn`'s store has table `name`: the interface's `has_table`
    on a Database, `sqlite_master` on a sqlite3 connection."""
    if hasattr(conn, "has_table"):
        return bool(conn.has_table(name))
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def table_names(conn) -> set[str]:
    if hasattr(conn, "tables"):
        return set(conn.tables())
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def column_names(conn, table: str) -> set[str]:
    if not _IDENTIFIER.match(table):
        raise ValueError(f"table must be a bare identifier, got {table!r}")
    if hasattr(conn, "columns"):
        return {c.name for c in conn.columns(table)}
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


__all__ = ["INTROSPECTION_PRAGMAS", "NAMED_PER_REASON", "PARAMETER_PATHS_SQL", "READABLE_PRAGMAS",
           "READ_ONLY_SQLSTATES", "ReadOnlyRefused", "SELFTEST_TABLES", "URL_SCHEMES", "WRITE_ACTIONS",
           "WRITE_PATHS_SQL", "WRITE_REFUSAL_MESSAGES", "assert_write_refused", "column_names", "is_url",
           "open_read_only", "open_read_only_postgres", "read_only_how", "read_only_uri", "redacted",
           "refuse_writes", "store_exists", "store_label", "store_line", "store_target", "table_exists", "table_names",
           "uri_for_absolute", "write_path_reasons", "write_paths"]
