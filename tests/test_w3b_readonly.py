"""W3b part 3b — the read-only open on Postgres (D9, T8) and the instance
scripts on a database URL.

R   THE OPEN. `scripts/_readonly.py` dispatches on a URL: a script that does
    not take one (`url_ok` unset) is refused by name before any connection;
    the hosted store opens as a `backend.store.Database` on a read-only
    session, and a role that could write is refused BY NAME before the first
    query, because a setting a role can switch off is not a barrier and a
    privilege it does not hold is. The selftest accepts only SQLSTATE 25006
    or 42501, and the companion switches the session to READ WRITE and shows
    the privilege still refuses. A superuser's refusal names that role and
    nothing else (R2b).
R6  THE CLASS. "Could write" is a class, never a list of named privileges:
    any role the session can act as (the session user and the current user,
    each with every role it is a member of), anywhere in the store's
    database, holding anything that changes it. One companion per member of
    the class an earlier form of the check did not read, each on its own
    fresh schema with a store: the role is refused by name, naming the reason
    and the role it comes through, and on the check as it stood before
    (law 16) the same role OPENS and its write LANDS. Thirty-two of them, in
    four rounds: F1's nine (the verifier: column privileges, a non-inherited
    membership, ownership, TRIGGER, a sequence, a default privilege, a
    SECURITY DEFINER function); F1b's six (the builder's reading of F1's
    query: a default privilege naming no schema, a function in another
    schema, `pg_write_server_files`, CREATEROLE, REFERENCES, CREATE on the
    database); the delta check's eleven (D1: a role named by a setting while
    the login behind it holds the write, three ways; D2: `pg_execute_server_program`,
    a function owned by a plain writer; D3: a view, a rule and a definer
    trigger in another schema; D4: no schema of its own, the store second on
    the path; D7: column REFERENCES); and six found closing that class
    (ownership of a function the store's trigger runs, a chain of two
    SECURITY DEFINER functions through an owner that cannot write, ALTER
    SYSTEM on a parameter, a schema put in front of `pg_catalog`, and two
    that show USAGE on a schema is no barrier: a SECURITY DEFINER function
    in a schema the role cannot name, reached through a view, and an INSERT
    privilege there, used through a pre-parsed function).
    R6b: the SELECT+CREATEDB role the deployment makes still opens. R6c: a
    column-UPDATE role is refused through backup.py too. R6d: the runbook's
    remedy holds (a SECURITY DEFINER function the role may NOT execute does
    not refuse it, and the call is refused by the server). R6e: a role that
    sees no schema is refused by name. R6f: a temporary table in another
    session of the role is not ownership; a table that is not temporary is.
R7  THE SENTENCES. `write_path_reasons` builds the refusal from the query's
    facts with no database: one sentence per role and kind, five names and a
    count of the rest, a superuser alone.
S   THE SCRIPTS. The six instance scripts that take a store argument read a
    hosted store by URL as the operator would run them, print the store on
    their first line with the URL's password redacted (law 15), and create
    nothing; the confirmed archiver takes the lock on the URL's target;
    backup.py refuses a URL as its data root by name (its hosted form,
    `--database-url`, is part 5's: tests/test_w3b_backup.py).
G   THE SET. The scripts that pass `url_ok=True` are derived by AST from the
    tracked scripts and root files and pinned: the six, and since W3b part 4
    `scripts/export_postgres_to_sqlite.py`, whose source is a hosted store
    (tests/test_w3b_migration.py), and since part 5 `scripts/backup.py`,
    whose `--database-url` dumps one (tests/test_w3b_backup.py); the refusal
    names every one; the companion plants a ninth and is named.

Every Postgres test skips BY NAME without `NEXUS_TEST_DATABASE_URL`. The
roles the tests need are created on the test database by the test (the
superuser the fixture connects as can), named after the schema, and dropped
with it.

Law 16: the pre-fix run against `ca2d974` (part 3a) is printed in part 3b's
commit body: R and G fail on the import (`is_url`, `ReadOnlyRefused`: a new
API, declared), S on `REFUSED: postgresql://… is not a file`. R6's rounds:
F1's nine against part 5 (`ee4533e`), in the fix commit's body; the other
twenty-three, with R2b, R6e, R6f and R7, against the fix commit (`00e9c96`),
in the follow-up commit's body (R6f fails on the refusal's new wording and
R7 on a new API, declared).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import subprocess
from tests import _proc
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from tests._tracked import tracked
from tests.conftest import POSTGRES_TEST_URL as PG_URL  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
NO_PG = "no Postgres named: NEXUS_TEST_DATABASE_URL is unset (the Postgres job sets it; windows and macos run SQLite)"
URL_CAPABLE = {"scripts/replay_check.py", "scripts/archive_pregate.py", "scripts/archive_consolidation.py",
               "scripts/cp_b49_tally.py", "scripts/as2_instance_windows.py", "scripts/diag_downstream.py",
               "scripts/export_postgres_to_sqlite.py",                  # W3b part 4: the export's source
               "scripts/backup.py"}                                     # W3b part 5: the hosted store's backup
RO_PASSWORD = "ro-pw-planted"


def _as_role(url: str, role: str, password: str, schema: str) -> str:
    """The test URL as `role`, with the session's search_path set to `schema`."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    netloc = f"{role}:{password}@{host}" + (f":{parts.port}" if parts.port else "")
    query = (parts.query + "&" if parts.query else "") + f"options=-c%20search_path%3D{schema}"
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


@pytest.fixture
def pg_store(tmp_path):
    """A schema holding a small NEXUS store, and four roles on it: `ro`
    (SELECT only), `rw1` (SELECT and INSERT on triples), `cr` (SELECT and
    CREATE on the schema), `wa` (SELECT and pg_write_all_data). Everything is
    dropped afterwards. Yields a dict of URLs by role plus the superuser's."""
    if not PG_URL:
        pytest.skip(NO_PG)
    import psycopg
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    from backend.memory.lineage import LineageStore
    from backend.store import StoreTarget
    from psycopg import sql
    h = hashlib.sha1(str(tmp_path).encode()).hexdigest()[:16]
    schema = f"t_{h}"
    roles = {name: f"{name}_{h}" for name in ("ro", "rw1", "cr", "wa")}
    created: list[str] = []

    def drop_everything():
        # Every role this fixture created and the schema, whether the setup
        # finished or failed partway: a setup that raised before its yield
        # leaves no teardown to run (W3b part 3: two failed runs left 26
        # schemas behind on the scratch cluster).
        with psycopg.connect(PG_URL, autocommit=True) as c:
            for role in created:
                c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s", (role,))
                c.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(role)))
                c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))

    try:
        store = TripleStore(StoreTarget.postgres(PG_URL, schema=schema))
        for i in range(3):
            store.add(subject="ganymede", relation="orbits", object=f"jupiter {i}", weight=0.8, tier=1,
                      granularity=Granularity.TOPIC, source="asserted")
        LineageStore(StoreTarget.postgres(PG_URL, schema=schema)).close()
        store.close()
        with psycopg.connect(PG_URL, autocommit=True) as c:
            for role in roles.values():
                # DDL takes no bind parameters: the password (a fixed test constant) is a quoted literal
                c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(RO_PASSWORD)))
                created.append(role)
                c.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
                c.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
            c.execute(f'GRANT INSERT ON "{schema}".triples TO "{roles["rw1"]}"')
            c.execute(f'GRANT CREATE ON SCHEMA "{schema}" TO "{roles["cr"]}"')
            c.execute(f'GRANT pg_write_all_data TO "{roles["wa"]}"')
    except BaseException:
        drop_everything()
        raise
    urls = {name: _as_role(PG_URL, role, RO_PASSWORD, schema) for name, role in roles.items()}
    sep = "&" if "?" in PG_URL else "?"
    urls["superuser"] = f"{PG_URL}{sep}options=-c%20search_path%3D{schema}"
    urls["schema"] = schema
    try:
        yield urls
    finally:
        drop_everything()


# ---------------------------------------------------------------------------
# R — the open
# ---------------------------------------------------------------------------

def test_r1_a_script_that_does_not_take_a_url_is_refused_by_name_before_connecting():
    from scripts._readonly import ReadOnlyRefused, open_read_only
    with pytest.raises(ReadOnlyRefused, match="reads SQLite stores only") as e:
        open_read_only("postgresql://u:hunter2@127.0.0.1:1/x_test")      # port 1: a connect would fail loudly
    assert "hunter2" not in str(e.value) and "cp_b49_tally.py" in str(e.value)


def test_r2_the_superuser_is_refused_by_name(pg_store):
    from scripts._readonly import ReadOnlyRefused, open_read_only
    with pytest.raises(ReadOnlyRefused, match="is a superuser"):
        open_read_only(pg_store["superuser"], url_ok=True)


def test_r2b_a_superusers_refusal_names_that_role_and_nothing_else(pg_store):
    """The delta check's D9: the superuser's refusal read every fact for every
    role on the server (a superuser is a member of all of them) and printed
    2,326 characters. It names the superuser, and stops: with a SECOND
    superuser role and the fixture's four other roles on the server, none of
    them is named."""
    import psycopg
    from scripts._readonly import ReadOnlyRefused, open_read_only
    other = f"su2_{pg_store['schema'][2:]}"
    with psycopg.connect(PG_URL, autocommit=True) as c:
        me = c.execute("SELECT session_user").fetchone()[0]
        c.execute(f'CREATE ROLE "{other}" NOLOGIN SUPERUSER')
    try:
        with pytest.raises(ReadOnlyRefused) as e:
            open_read_only(pg_store["superuser"], url_ok=True)
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(f'DROP ROLE IF EXISTS "{other}"')
    said = str(e.value).split("read-only: ", 1)[1]
    print(f"R2b: {said}")
    assert said == f"the role {me} is a superuser. Use a role with SELECT only (the runbook's read-only role).", said


def test_r3_a_select_only_role_opens_and_the_selftest_returns_the_refusal(pg_store):
    from scripts._readonly import assert_write_refused, open_read_only
    db = open_read_only(pg_store["ro"], url_ok=True)
    try:
        assert db.backend == "postgres" and db.read_only is True
        assert db.execute("SELECT COUNT(*) FROM triples").fetchone()[0] == 3
        refusal = assert_write_refused(db, table="triples")
        assert refusal.startswith(("ReadOnlySqlTransaction", "InsufficientPrivilege")), refusal
        # The companion: the role switches the session to READ WRITE (any role
        # may); the privilege it does not hold still refuses.
        db.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
        refusal = assert_write_refused(db, table="triples")
        assert refusal.startswith("InsufficientPrivilege"), refusal
        assert db.execute("SELECT COUNT(*) FROM triples").fetchone()[0] == 3, "nothing changed"
    finally:
        db.close()
    print(f"R3: {refusal}")


@pytest.mark.parametrize("role, named", [("rw1", "may write"), ("cr", "has CREATE on schema"),
                                         ("wa", "member of pg_write_all_data")])
def test_r4_a_role_that_could_write_is_refused_by_name(pg_store, role, named):
    from scripts._readonly import ReadOnlyRefused, open_read_only
    with pytest.raises(ReadOnlyRefused, match=named) as e:
        open_read_only(pg_store[role], url_ok=True)
    assert RO_PASSWORD not in str(e.value)
    if role == "rw1":
        assert "triples" in str(e.value)


def test_r5_the_helpers_answer_for_both_kinds_of_store(pg_store, tmp_path):
    from scripts._readonly import (column_names, is_url, open_read_only, read_only_how, store_exists, store_label,
                                   store_target, table_exists, table_names)
    assert is_url(pg_store["ro"]) and not is_url(str(tmp_path / "kb.db")) and not is_url(tmp_path / "kb.db")
    assert store_label(pg_store["ro"]).startswith("postgres postgresql://") and RO_PASSWORD not in store_label(pg_store["ro"])
    assert read_only_how(pg_store["ro"]) == "a read-only session and a role that cannot write"
    assert store_target(pg_store["ro"]).backend == "postgres" and store_target("x.db") == Path("x.db")
    assert store_exists(pg_store["ro"]) == (True, "")
    exists, why = store_exists("postgresql://u:hunter2@127.0.0.1:1/x_test")
    assert not exists and "does not answer" in why and "hunter2" not in why
    assert store_exists(str(tmp_path / "absent.db")) == (False, f"{tmp_path / 'absent.db'} is not a file")
    db = open_read_only(pg_store["ro"], url_ok=True)
    try:
        assert table_exists(db, "triples") and not table_exists(db, "nowhere")
        assert {"triples", "lineage_records"} <= table_names(db) and "nexus_store_lock" not in table_names(db)
        assert {"id", "subject", "relation"} <= column_names(db, "triples")
    finally:
        db.close()
    lite = sqlite3.connect(tmp_path / "lite.db")
    lite.execute("CREATE TABLE triples (id INTEGER PRIMARY KEY, subject TEXT)")
    assert table_exists(lite, "triples") and table_names(lite) == {"triples"} and column_names(lite, "triples") == {"id", "subject"}
    lite.close()


# ---------------------------------------------------------------------------
# R6 — the CLASS (the module docstring lists its four rounds). Part 3's
# barrier 3 refused four named facts about `current_user` (superuser,
# pg_write_all_data, CREATE on the schema, table-level INSERT/UPDATE/DELETE/
# TRUNCATE). Each case below plants ONE member of the class an earlier form
# of the check did not read, on its own fresh schema with a store. The
# consequence asserted is the refusal BY NAME (the reason, and the role the
# write comes through); on the earlier check the same test fails with the
# role opened and the proof that its write landed. OQ-16 rules 1 (the
# companion plants a member the guard's source does not name) and 3 (it
# asserts the consequence, never a flag).
# ---------------------------------------------------------------------------

def _role_url(schema: str, role: str) -> str:
    return _as_role(PG_URL, role, RO_PASSWORD, schema)


def _rw(db):
    """Switch the opened session to READ WRITE (any role may; barriers 1 and 2
    are not the barrier) on the driver connection, so the pre-fix branch can
    show the write landing."""
    db.raw.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")


def _su_exec(c, *stmts):
    for s in stmts:
        c.execute(s)


def _only_the_role_may_execute(function: str, role: str) -> list[str]:
    """A planted function is the planted role's to execute, never PUBLIC's
    (the default): the check reads every schema of the database, so a
    SECURITY DEFINER function PUBLIC may execute would refuse every other
    read-only open on the same database while the case lives."""
    return [f"REVOKE EXECUTE ON FUNCTION {function} FROM PUBLIC", f'GRANT EXECUTE ON FUNCTION {function} TO "{role}"']


# Each case: (setup -> (named_role, extra_roles), reason_substr, land -> proof).
# setup runs as the superuser `c`; it may open a role connection itself.
# land runs on the opened Database `db` (pre-fix only) and reads back through
# the superuser `su` to prove the write persisted.

def _c_colupd(c, q, schema, role):
    _su_exec(c, f'GRANT UPDATE (weight) ON {q}.triples TO "{role}"')
    return role, []


def _l_colupd(db, su, q, schema, role):
    db.raw.execute("UPDATE triples SET weight = 0.99 WHERE subject = 'io'")
    return "triples.weight now " + str(su.execute(f"SELECT weight FROM {q}.triples WHERE subject='io'").fetchone()[0])


def _c_colins(c, q, schema, role):
    # `environments` has a text PK, no CHECK and no id trigger (triples' trigger
    # needs nexus_sequence, which a column-INSERT grant does not reach; scope_meta
    # is a CHECK (id = 1) singleton), so the column-INSERT write lands cleanly.
    _su_exec(c, f'GRANT INSERT (environment_hash, descriptor_json, first_seen_ts) ON {q}.environments TO "{role}"')
    return role, []


def _l_colins(db, su, q, schema, role):
    db.raw.execute("INSERT INTO environments (environment_hash, descriptor_json, first_seen_ts) "
                   "VALUES ('f1probe', '{}', 1.0)")
    return "environments now has " + str(su.execute(f"SELECT count(*) FROM {q}.environments").fetchone()[0]) + " row(s)"


def _c_noinherit(c, q, schema, role):
    writer = role + "_w"
    _su_exec(c, f'CREATE ROLE "{writer}" NOLOGIN',
             f'GRANT USAGE ON SCHEMA {q} TO "{writer}"',
             # SELECT too, so the UPDATE's WHERE can read the row it rewrites
             f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {q} TO "{writer}"',
             f'GRANT "{writer}" TO "{role}" WITH INHERIT FALSE')
    return writer, [writer]           # the SET ROLE path: the writer is named, not the connecting role


def _l_noinherit(db, su, q, schema, role):
    db.raw.execute(f'SET ROLE "{role}_w"')
    db.raw.execute("UPDATE triples SET weight = 0.99 WHERE subject = 'io'")
    return "triples.weight now " + str(su.execute(f"SELECT weight FROM {q}.triples WHERE subject='io'").fetchone()[0])


def _c_inherit(c, q, schema, role):
    member = role + "_m"
    _su_exec(c, f'CREATE ROLE "{member}" NOLOGIN',
             f'GRANT UPDATE (weight) ON {q}.triples TO "{member}"',
             f'GRANT "{member}" TO "{role}" WITH INHERIT TRUE')
    return role, [member]             # inherited: the connecting role itself holds the write


def _l_inherit(db, su, q, schema, role):
    db.raw.execute("UPDATE triples SET weight = 0.99 WHERE subject = 'io'")
    return "triples.weight now " + str(su.execute(f"SELECT weight FROM {q}.triples WHERE subject='io'").fetchone()[0])


def _c_owner(c, q, schema, role):
    import psycopg
    _su_exec(c, f'GRANT CREATE ON SCHEMA {q} TO "{role}"')
    with psycopg.connect(_role_url(schema, role), autocommit=True) as oc:
        oc.execute(f'CREATE TABLE {q}.owned (x int)')
    _su_exec(c, f'REVOKE ALL ON {q}.owned FROM "{role}"', f'REVOKE CREATE ON SCHEMA {q} FROM "{role}"')
    return role, []                   # SELECT-only and no CREATE now, but still the table's owner


def _l_owner(db, su, q, schema, role):
    db.raw.execute(f'GRANT INSERT ON owned TO "{role}"')   # an owner re-grants itself what was revoked
    db.raw.execute("INSERT INTO owned VALUES (7)")
    return "owned now has " + str(su.execute(f"SELECT count(*) FROM {q}.owned").fetchone()[0]) + " row(s)"


def _c_trigger(c, q, schema, role):
    _su_exec(c, f'GRANT TRIGGER ON {q}.triples TO "{role}"')
    return role, []


def _l_trigger(db, su, q, schema, role):
    db.raw.execute(f'CREATE TRIGGER f1probe BEFORE INSERT ON triples FOR EACH ROW '
                   f'EXECUTE FUNCTION {schema}.nexus_autoinc__triples()')
    n = su.execute("SELECT count(*) FROM information_schema.triggers WHERE trigger_schema=%s "
                   "AND trigger_name='f1probe'", (schema,)).fetchone()[0]
    return f"a new trigger f1probe on the store's triples table exists ({n})"


def _c_sequence(c, q, schema, role):
    _su_exec(c, f'CREATE SEQUENCE {q}.planted_seq', f'GRANT USAGE ON SEQUENCE {q}.planted_seq TO "{role}"')
    return role, []


def _l_sequence(db, su, q, schema, role):
    v = db.raw.execute(f"SELECT nextval('{schema}.planted_seq')").fetchone()[0]
    last = su.execute(f"SELECT last_value FROM {q}.planted_seq").fetchone()[0]
    return f"nextval returned {v}; the sequence advanced (last_value {last})"


def _c_default_priv(c, q, schema, role):
    _su_exec(c, f'ALTER DEFAULT PRIVILEGES FOR ROLE {urlsplit(PG_URL).username or "postgres"} '
                f'IN SCHEMA {q} GRANT INSERT ON TABLES TO "{role}"')
    return role, []


def _l_default_priv(db, su, q, schema, role):
    su.execute(f'CREATE TABLE {q}.future_t (x int)')       # the store's lazy table, made after the check
    db.raw.execute("INSERT INTO future_t VALUES (1)")        # the role has INSERT by the default privilege
    return "future_t (created after the open) now has " + \
        str(su.execute(f"SELECT count(*) FROM {q}.future_t").fetchone()[0]) + " row(s)"


def _c_secdef(c, q, schema, role):
    _su_exec(c, f"CREATE FUNCTION {q}.escalate() RETURNS int LANGUAGE sql SECURITY DEFINER AS "
                f"$$ UPDATE {q}.triples SET weight = 0.99 WHERE subject = 'io'; SELECT 1 $$",
             *_only_the_role_may_execute(f"{q}.escalate()", role))
    return role, []                   # the function runs as its owner (the superuser)


def _l_secdef(db, su, q, schema, role):
    db.raw.execute(f"SELECT {schema}.escalate()")
    return "triples.weight now " + str(su.execute(f"SELECT weight FROM {q}.triples WHERE subject='io'").fetchone()[0]) + \
        " (written by the SECURITY DEFINER function, run as its owner)"


def _c_global_default_priv(c, q, schema, role):
    # F1b: a default privilege that names NO schema (pg_default_acl's
    # defaclnamespace is 0): every table its role creates, in any schema, the
    # store's among them. The creator may create in the store's schema.
    creator = role + "_c"
    _su_exec(c, f'CREATE ROLE "{creator}" NOLOGIN',
             f'GRANT USAGE, CREATE ON SCHEMA {q} TO "{creator}"',
             f'ALTER DEFAULT PRIVILEGES FOR ROLE "{creator}" GRANT INSERT ON TABLES TO "{role}"')
    return role, [creator]


def _l_global_default_priv(db, su, q, schema, role):
    su.execute(f'SET ROLE "{role}_c"')
    su.execute(f'CREATE TABLE {q}.future_g (x int)')        # a table made after the check, by that creator
    su.execute('RESET ROLE')
    db.raw.execute("INSERT INTO future_g VALUES (1)")
    return "future_g (created after the open) now has " + \
        str(su.execute(f"SELECT count(*) FROM {q}.future_g").fetchone()[0]) + " row(s)"


def _c_secdef_elsewhere(c, q, schema, role):
    # F1b: a SECURITY DEFINER function in ANOTHER schema of the database,
    # owned by a role that can write the store (the superuser here), which
    # the role may reach (USAGE on its schema; EXECUTE defaults to PUBLIC).
    other = f'"{schema}_x"'
    _su_exec(c, f'CREATE SCHEMA {other}', f'GRANT USAGE ON SCHEMA {other} TO "{role}"',
             f"CREATE FUNCTION {other}.escalate() RETURNS int LANGUAGE sql SECURITY DEFINER AS "
             f"$$ UPDATE {q}.triples SET weight = 0.99 WHERE subject = 'io'; SELECT 1 $$",
             *_only_the_role_may_execute(f"{other}.escalate()", role))
    return role, []


def _l_secdef_elsewhere(db, su, q, schema, role):
    db.raw.execute(f'SELECT "{schema}_x".escalate()')
    return "triples.weight now " + str(su.execute(f"SELECT weight FROM {q}.triples WHERE subject='io'").fetchone()[0]) + \
        " (written by a SECURITY DEFINER function in another schema, run as its owner)"


def _c_server_files(c, q, schema, role):
    # F1b: a predefined role that writes the server's own files (COPY TO a
    # file, as the server's OS user): the store's files are among them.
    _su_exec(c, f'GRANT pg_write_server_files TO "{role}"')
    return "pg_write_server_files", []


def _l_server_files(db, su, q, schema, role):
    import tempfile
    target = Path(tempfile.gettempdir()) / f"w6_{schema}.txt"
    db.raw.execute(f"COPY (SELECT 'written by a role opened read-only') TO '{target.as_posix()}'")
    wrote = target.is_file()
    target.unlink(missing_ok=True)                       # the server's file, on this host: not left behind
    return f"the server wrote a file of the role's choosing ({target.name}: {wrote}) as its own OS user"


def _c_createrole(c, q, schema, role):
    # F1b: CREATEROLE. Before PostgreSQL 16 such a role can grant itself any
    # non-superuser role's membership, a writer's among them; refused on
    # every version (a read-only role has no use for it).
    _su_exec(c, f'ALTER ROLE "{role}" CREATEROLE')
    return role, []


def _l_createrole(db, su, q, schema, role):
    made = role + "_n"
    db.raw.execute(f'CREATE ROLE "{made}" NOLOGIN')
    su.execute(f'DROP ROLE "{made}"')
    return f"the role created another role ({made}); before PostgreSQL 16 it could grant itself a writer's membership"


def _c_references(c, q, schema, role):
    # F1b: REFERENCES. With a table of its own anywhere, the role adds a
    # foreign key to the store's table: constraint triggers ON the store's
    # table, which then refuse the store's own deletes and key updates.
    other = f'"{schema}_x"'
    _su_exec(c, f'CREATE SCHEMA {other}', f'GRANT USAGE, CREATE ON SCHEMA {other} TO "{role}"',
             f'GRANT REFERENCES ON {q}.triples TO "{role}"')
    return role, []


def _l_references(db, su, q, schema, role):
    before = su.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid = %s::regclass AND tgconstraint <> 0",
                        (f"{schema}.triples",)).fetchone()[0]
    db.raw.execute(f'CREATE TABLE "{schema}_x".holds (triple_id bigint REFERENCES {q}.triples (id))')
    after = su.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid = %s::regclass AND tgconstraint <> 0",
                       (f"{schema}.triples",)).fetchone()[0]
    return f"the store's triples table gained {after - before} constraint trigger(s) from the role's foreign key"


def _c_database_create(c, q, schema, role):
    # F1b: CREATE on the DATABASE. The role makes schemas; one named after the
    # server's own role comes first on the default search_path ("$user",
    # public) and would shadow a store that lives in `public`.
    import psycopg
    with psycopg.connect(PG_URL, autocommit=True) as probe:
        database = probe.execute("SELECT current_database()").fetchone()[0]
    _su_exec(c, f'GRANT CREATE ON DATABASE "{database}" TO "{role}"')
    return role, []


def _l_database_create(db, su, q, schema, role):
    db.raw.execute(f'CREATE SCHEMA "{schema}_x"')
    owner = su.execute("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = %s",
                       (f"{schema}_x",)).fetchone()[0]
    return f"the role made a schema of its own in the store's database ({schema}_x, owned by {owner})"


# --- the delta check's D1: the role a SETTING names is not the role the session is ---------
# PostgreSQL judges SET ROLE from the SESSION user, so a login keeps its
# rights while `current_user` is a read-only role a connection-time setting
# named. (Measured: `session_user` cannot be disguised the same way; a
# `session_authorization` in the URL's options or in ALTER ROLE ... SET is
# accepted and has no effect at login.)

def _login_holding_update(c, q, role, *, noinherit: bool = False) -> str:
    login = role + "_l"
    _su_exec(c, f"CREATE ROLE \"{login}\" LOGIN {'NOINHERIT ' if noinherit else ''}PASSWORD '{RO_PASSWORD}'")
    return login


def _c_role_named_in_the_url(c, q, schema, role):
    login = _login_holding_update(c, q, role)
    _su_exec(c, f'GRANT USAGE ON SCHEMA {q} TO "{login}"', f'GRANT SELECT, UPDATE ON {q}.triples TO "{login}"',
             f'GRANT "{role}" TO "{login}"')
    return login, [login], _role_url(schema, login) + f"%20-c%20role%3D{role}"


def _c_role_named_by_alter_role(c, q, schema, role):
    login = _login_holding_update(c, q, role)
    _su_exec(c, f'GRANT USAGE ON SCHEMA {q} TO "{login}"', f'GRANT SELECT, UPDATE ON {q}.triples TO "{login}"',
             f'GRANT "{role}" TO "{login}"', f'ALTER ROLE "{login}" SET role = "{role}"')
    return login, [login], _role_url(schema, login)


def _l_back_to_the_session_user(db, su, q, schema, role):
    seen = db.raw.execute("SELECT session_user, current_user").fetchone()
    db.raw.execute("SET ROLE NONE")
    db.raw.execute("UPDATE triples SET weight = 0.99 WHERE subject = 'io'")
    return (f"opened as current_user {seen[1]} with session_user {seen[0]}; after SET ROLE NONE triples.weight now "
            + str(su.execute(f"SELECT weight FROM {q}.triples WHERE subject='io'").fetchone()[0]))


def _c_noinherit_login_between_two_groups(c, q, schema, role):
    login, writer = _login_holding_update(c, q, role, noinherit=True), role + "_w"
    _su_exec(c, f'CREATE ROLE "{writer}" NOLOGIN', f'GRANT USAGE ON SCHEMA {q} TO "{writer}"',
             f'GRANT SELECT, UPDATE ON {q}.triples TO "{writer}"',
             f'GRANT "{role}", "{writer}" TO "{login}"', f'ALTER ROLE "{login}" SET role = "{role}"')
    return writer, [writer, login], _role_url(schema, login)


def _l_set_role_to_the_writer(db, su, q, schema, role):
    seen = db.raw.execute("SELECT session_user, current_user").fetchone()
    db.raw.execute(f'SET ROLE "{role}_w"')
    db.raw.execute("UPDATE triples SET weight = 0.99 WHERE subject = 'io'")
    return (f"opened as current_user {seen[1]} with session_user {seen[0]}; after SET ROLE to the writer group "
            "triples.weight now " + str(su.execute(f"SELECT weight FROM {q}.triples WHERE subject='io'").fetchone()[0]))


# --- D2: the two refusals F1b declared and no test held ------------------------------------

def _c_server_program(c, q, schema, role):
    _su_exec(c, f'GRANT pg_execute_server_program TO "{role}"')
    return "pg_execute_server_program", []


def _l_server_program(db, su, q, schema, role):
    db.raw.execute("COPY (SELECT 1) TO PROGRAM 'true'")
    return "the server ran a program of the role's choosing as its own OS user (COPY ... TO PROGRAM)"


def _c_secdef_owned_by_a_plain_writer(c, q, schema, role):
    # The owner is NOT a superuser: it could write the store because it holds
    # UPDATE on one table, which only the `writers` set reads.
    owner, other = role + "_o", f'"{schema}_x"'
    _su_exec(c, f'CREATE ROLE "{owner}" NOLOGIN', f'GRANT USAGE ON SCHEMA {q} TO "{owner}"',
             f'GRANT SELECT, UPDATE ON {q}.triples TO "{owner}"',
             f'CREATE SCHEMA {other}', f'GRANT USAGE, CREATE ON SCHEMA {other} TO "{owner}"',
             f'GRANT USAGE ON SCHEMA {other} TO "{role}"', f'SET ROLE "{owner}"',
             f"CREATE FUNCTION {other}.escalate() RETURNS int LANGUAGE sql SECURITY DEFINER AS "
             f"$$ UPDATE {q}.triples SET weight = 0.99 WHERE subject = 'io'; SELECT 1 $$",
             *_only_the_role_may_execute(f"{other}.escalate()", role), "RESET ROLE")
    return role, [owner]


# --- D7: column-level REFERENCES ------------------------------------------------------------

def _c_column_references(c, q, schema, role):
    other = f'"{schema}_x"'
    _su_exec(c, f'CREATE SCHEMA {other}', f'GRANT USAGE, CREATE ON SCHEMA {other} TO "{role}"',
             f'GRANT REFERENCES (id) ON {q}.triples TO "{role}"')
    return role, []


# --- D3: a relation ELSEWHERE that runs with its owner's rights -----------------------------

def _c_view_elsewhere(c, q, schema, role):
    other = f'"{schema}_x"'
    _su_exec(c, f'CREATE SCHEMA {other}', f'GRANT USAGE ON SCHEMA {other} TO "{role}"',
             f'CREATE VIEW {other}.v AS SELECT id, subject, weight FROM {q}.triples',
             f'GRANT SELECT, UPDATE ON {other}.v TO "{role}"')
    return role, []


def _l_view_elsewhere(db, su, q, schema, role):
    db.raw.execute(f"UPDATE \"{schema}_x\".v SET weight = 0.99 WHERE subject = 'io'")
    return ("triples.weight now " + str(su.execute(f"SELECT weight FROM {q}.triples WHERE subject='io'").fetchone()[0])
            + " (through a view in another schema, which writes with its owner's rights)")


def _c_rule_elsewhere(c, q, schema, role):
    other = f'"{schema}_x"'
    _su_exec(c, f'CREATE SCHEMA {other}', f'GRANT USAGE ON SCHEMA {other} TO "{role}"',
             f'CREATE TABLE {other}.inbox (x int)',
             f"CREATE RULE touch AS ON INSERT TO {other}.inbox DO ALSO "
             f"UPDATE {q}.triples SET weight = 0.99 WHERE subject = 'io'",
             f'GRANT INSERT ON {other}.inbox TO "{role}"')
    return role, []


def _c_definer_trigger_elsewhere(c, q, schema, role):
    other = f'"{schema}_x"'
    _su_exec(c, f'CREATE SCHEMA {other}', f'GRANT USAGE ON SCHEMA {other} TO "{role}"',
             f"CREATE FUNCTION {other}.touch() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS "
             f"$$ BEGIN UPDATE {q}.triples SET weight = 0.99 WHERE subject = 'io'; RETURN NEW; END $$",
             f'REVOKE EXECUTE ON FUNCTION {other}.touch() FROM PUBLIC',     # so the function rule is not what names it
             f'CREATE TABLE {other}.inbox (x int)',
             f'CREATE TRIGGER t BEFORE INSERT ON {other}.inbox FOR EACH ROW EXECUTE FUNCTION {other}.touch()',
             f'GRANT INSERT ON {other}.inbox TO "{role}"')
    return role, []


def _l_insert_elsewhere(db, su, q, schema, role):
    db.raw.execute(f'INSERT INTO "{schema}_x".inbox VALUES (1)')
    return ("triples.weight now " + str(su.execute(f"SELECT weight FROM {q}.triples WHERE subject='io'").fetchone()[0])
            + " (an INSERT elsewhere, whose rule or trigger wrote the store with its owner's rights)")


# --- D4: the query read `current_schema()` only ---------------------------------------------

def _c_no_schema_of_its_own(c, q, schema, role):
    # The login holds USAGE on no schema of its path (current_schema() is
    # NULL); the group it may SET ROLE to holds the write.
    login, group = _login_holding_update(c, q, role, noinherit=True), role + "_g"
    _su_exec(c, f'CREATE ROLE "{group}" NOLOGIN', f'GRANT USAGE ON SCHEMA {q} TO "{group}"',
             f'GRANT SELECT, UPDATE ON {q}.triples TO "{group}"', f'GRANT "{group}" TO "{login}"')
    return group, [group, login], _role_url(schema, login)


def _l_set_role_to_the_group(db, su, q, schema, role):
    seen = db.raw.execute("SELECT current_schema()").fetchone()[0]
    db.raw.execute(f'SET ROLE "{role}_g"')
    db.raw.execute(f"UPDATE {q}.triples SET weight = 0.99 WHERE subject = 'io'")
    return (f"opened with current_schema() {seen!r}; after SET ROLE to the group triples.weight now "
            + str(su.execute(f"SELECT weight FROM {q}.triples WHERE subject='io'").fetchone()[0]))


def _c_store_second_on_the_path(c, q, schema, role):
    other = f'"{schema}_x"'
    _su_exec(c, f'CREATE SCHEMA {other}', f'GRANT USAGE ON SCHEMA {other} TO "{role}"',
             f'GRANT UPDATE ON {q}.triples TO "{role}"')
    return role, [], _as_role(PG_URL, role, RO_PASSWORD, f"{schema}_x,{schema}")


def _l_store_second_on_the_path(db, su, q, schema, role):
    seen = db.raw.execute("SELECT current_schema()").fetchone()[0]
    db.raw.execute("UPDATE triples SET weight = 0.99 WHERE subject = 'io'")
    return (f"opened with current_schema() {seen!r}, the store second on the path; triples.weight now "
            + str(su.execute(f"SELECT weight FROM {q}.triples WHERE subject='io'").fetchone()[0]))


# --- found closing the delta check's class (the builder's own reading, 11 November) ---------

def _c_owns_a_function(c, q, schema, role):
    # Ownership of something that is not a relation: the store's own trigger
    # function. The role holds no privilege on any table, and an owner drops
    # its function CASCADE, the store table's trigger with it.
    _su_exec(c, f'ALTER FUNCTION {q}.nexus_autoinc__triples() OWNER TO "{role}"')
    return role, []


def _l_owns_a_function(db, su, q, schema, role):
    count = "SELECT count(*) FROM pg_trigger WHERE tgrelid = %s::regclass AND NOT tgisinternal"
    before = su.execute(count, (f"{schema}.triples",)).fetchone()[0]
    db.raw.execute("DROP FUNCTION nexus_autoinc__triples() CASCADE")
    after = su.execute(count, (f"{schema}.triples",)).fetchone()[0]
    return (f"the store's triples table had {before} trigger(s) and has {after}: the role dropped the function it "
            "owns CASCADE, and the table's id trigger went with it")


def _c_definer_chain(c, q, schema, role):
    # Two SECURITY DEFINER functions. The one the role may run is owned by a
    # role that can write NOTHING; it calls a second, owned by a writer,
    # which only that first owner may execute. No rule that asks "could this
    # function's owner write the store" sees it.
    front, writer, other = role + "_o", role + "_w", f'"{schema}_x"'
    _su_exec(c, f'CREATE ROLE "{front}" NOLOGIN', f'CREATE ROLE "{writer}" NOLOGIN',
             f'GRANT USAGE ON SCHEMA {q} TO "{writer}"', f'GRANT SELECT, UPDATE ON {q}.triples TO "{writer}"',
             f'CREATE SCHEMA {other}', f'GRANT USAGE, CREATE ON SCHEMA {other} TO "{front}", "{writer}"',
             f'GRANT USAGE ON SCHEMA {other} TO "{role}"',
             f'SET ROLE "{writer}"',
             f"CREATE FUNCTION {other}.inner_write() RETURNS int LANGUAGE sql SECURITY DEFINER AS "
             f"$$ UPDATE {q}.triples SET weight = 0.99 WHERE subject = 'io'; SELECT 1 $$",
             f'REVOKE EXECUTE ON FUNCTION {other}.inner_write() FROM PUBLIC',
             f'GRANT EXECUTE ON FUNCTION {other}.inner_write() TO "{front}"',
             "RESET ROLE", f'SET ROLE "{front}"',
             f"CREATE FUNCTION {other}.escalate() RETURNS int LANGUAGE sql SECURITY DEFINER AS "
             f"$$ SELECT {other}.inner_write() $$",
             *_only_the_role_may_execute(f"{other}.escalate()", role),
             "RESET ROLE", f'REVOKE CREATE ON SCHEMA {other} FROM "{front}", "{writer}"')
    return role, [front, writer]


PLANTED_PARAMETER = "log_min_duration_statement"


def _c_alter_system(c, q, schema, role):
    # PostgreSQL 15 and later: a privilege on a configuration parameter.
    # ALTER SYSTEM rewrites postgresql.auto.conf, and a read-only transaction
    # does not refuse it (PostgreSQL classes it as read-only: it writes no WAL).
    if c.info.server_version < 150000:
        pytest.skip("this server is older than PostgreSQL 15, which added privileges on parameters")
    _su_exec(c, f'GRANT ALTER SYSTEM ON PARAMETER {PLANTED_PARAMETER} TO "{role}"')
    return role, []


def _l_alter_system(db, su, q, schema, role):
    db.raw.execute(f"ALTER SYSTEM SET {PLANTED_PARAMETER} = -1")
    wrote = su.execute("SELECT count(*) FROM pg_file_settings WHERE name = %s AND sourcefile LIKE %s",
                       (PLANTED_PARAMETER, "%postgresql.auto.conf")).fetchone()[0]
    su.execute(f"ALTER SYSTEM RESET {PLANTED_PARAMETER}")        # the server's own file: not left changed
    return (f"the role rewrote the server's postgresql.auto.conf ({wrote} line for {PLANTED_PARAMETER}) in a "
            "session opened read-only")


def _c_definer_behind_a_view(c, q, schema, role):
    # The role holds USAGE on the store's schema and on `_y`, NOT on `_x`,
    # where the SECURITY DEFINER function lives. USAGE is asked when a name
    # is looked up; the view in `_y` looked the function up when it was made.
    hidden, front = f'"{schema}_x"', f'"{schema}_y"'
    _su_exec(c, f'CREATE SCHEMA {hidden}', f'CREATE SCHEMA {front}', f'GRANT USAGE ON SCHEMA {front} TO "{role}"',
             f"CREATE FUNCTION {hidden}.escalate() RETURNS int LANGUAGE sql SECURITY DEFINER AS "
             f"$$ UPDATE {q}.triples SET weight = 0.99 WHERE subject = 'io'; SELECT 1 $$",
             *_only_the_role_may_execute(f"{hidden}.escalate()", role),
             f'CREATE VIEW {front}.peek AS SELECT {hidden}.escalate() AS n',
             f'GRANT SELECT ON {front}.peek TO "{role}"')
    return role, []


def _l_through_the_view(db, su, q, schema, role):
    try:
        db.raw.execute(f'SELECT "{schema}_x".escalate()')
        direct = "the direct call answered"
    except Exception as e:                                # noqa: BLE001 — the refusal that looked like a barrier
        direct = f"the direct call is refused ({str(e).splitlines()[0]})"
    db.raw.execute(f'SELECT n FROM "{schema}_y".peek')
    return (f"{direct}, and through the view triples.weight is now "
            + str(su.execute(f"SELECT weight FROM {q}.triples WHERE subject='io'").fetchone()[0]))


def _c_insert_behind_an_atomic_function(c, q, schema, role):
    # INSERT on a table in a schema the role cannot name (`_x`), whose rule
    # writes the store with its owner's rights; a function with a pre-parsed
    # body (BEGIN ATOMIC) in a schema it can use (`_y`) does the INSERT with
    # the CALLER's privileges and no lookup.
    hidden, front = f'"{schema}_x"', f'"{schema}_y"'
    _su_exec(c, f'CREATE SCHEMA {hidden}', f'CREATE SCHEMA {front}', f'GRANT USAGE ON SCHEMA {front} TO "{role}"',
             f'CREATE TABLE {hidden}.inbox (x int)',
             f"CREATE RULE touch AS ON INSERT TO {hidden}.inbox DO ALSO "
             f"UPDATE {q}.triples SET weight = 0.99 WHERE subject = 'io'",
             f'GRANT INSERT ON {hidden}.inbox TO "{role}"',
             f'CREATE FUNCTION {front}.feed() RETURNS void LANGUAGE sql BEGIN ATOMIC '
             f'INSERT INTO {hidden}.inbox VALUES (1); END')
    return role, []


def _l_through_the_atomic_function(db, su, q, schema, role):
    try:
        db.raw.execute(f'INSERT INTO "{schema}_x".inbox VALUES (2)')
        direct = "the direct INSERT answered"
    except Exception as e:                                # noqa: BLE001
        direct = f"the direct INSERT is refused ({str(e).splitlines()[0]})"
    db.raw.execute(f'SELECT "{schema}_y".feed()')
    return (f"{direct}, and through the pre-parsed function triples.weight is now "
            + str(su.execute(f"SELECT weight FROM {q}.triples WHERE subject='io'").fetchone()[0]))


def _c_catalog_shadowed(c, q, schema, role):
    # A schema in FRONT of pg_catalog on the URL's own search_path, holding a
    # view named after the catalog the check reads. Read by that path, the
    # check finds a `pg_roles` with no rows and so no role to refuse.
    other = f'"{schema}_x"'
    _su_exec(c, f'CREATE SCHEMA {other}', f'GRANT USAGE ON SCHEMA {other} TO "{role}"',
             f'CREATE VIEW {other}.pg_roles AS SELECT * FROM pg_catalog.pg_roles WHERE false',
             f'GRANT SELECT ON {other}.pg_roles TO "{role}"', f'GRANT UPDATE ON {q}.triples TO "{role}"')
    return role, [], _as_role(PG_URL, role, RO_PASSWORD, f"{schema}_x,pg_catalog,{schema}")


WRITE_PATH_CASES = {
    "column_update": (_c_colupd, "may write columns (UPDATE)", _l_colupd),
    "column_insert": (_c_colins, "may write columns (INSERT)", _l_colins),
    "noinherit_membership_in_writer": (_c_noinherit, "may write (", _l_noinherit),
    "inherit_membership_column_update": (_c_inherit, "may write columns (UPDATE)", _l_inherit),
    "owner_with_privs_revoked": (_c_owner, "owns ", _l_owner),
    "trigger_on_table": (_c_trigger, "may write (TRIGGER", _l_trigger),
    "usage_on_sequence": (_c_sequence, "has USAGE on sequence", _l_sequence),
    "default_privilege_future_tables": (_c_default_priv, "on future tables", _l_default_priv),
    "security_definer_function": (_c_secdef, "may execute SECURITY DEFINER", _l_secdef),
    # F1b (the builder's reading of F1's own query, after the fix commit):
    "global_default_privilege": (_c_global_default_priv, "on future tables", _l_global_default_priv),
    "security_definer_in_another_schema": (_c_secdef_elsewhere, "may execute SECURITY DEFINER", _l_secdef_elsewhere),
    "member_of_pg_write_server_files": (_c_server_files, "pg_write_server_files", _l_server_files),
    "createrole": (_c_createrole, "CREATEROLE", _l_createrole),
    "references_on_table": (_c_references, "REFERENCES", _l_references),
    "create_on_the_database": (_c_database_create, "may create schemas in the database", _l_database_create),
    # The delta check over the fix commit (D1 to D4, D7):
    "role_named_in_the_urls_options": (_c_role_named_in_the_url, "may write (UPDATE", _l_back_to_the_session_user),
    "role_named_by_alter_role_set": (_c_role_named_by_alter_role, "may write (UPDATE", _l_back_to_the_session_user),
    "noinherit_login_between_two_groups": (_c_noinherit_login_between_two_groups, "may write (UPDATE",
                                           _l_set_role_to_the_writer),
    "member_of_pg_execute_server_program": (_c_server_program, "pg_execute_server_program", _l_server_program),
    "security_definer_owned_by_a_plain_writer": (_c_secdef_owned_by_a_plain_writer, "may execute SECURITY DEFINER",
                                                 _l_secdef_elsewhere),
    "column_references": (_c_column_references, "may write columns (REFERENCES", _l_references),
    "update_on_a_view_elsewhere": (_c_view_elsewhere, "may write (UPDATE", _l_view_elsewhere),
    "insert_where_a_rule_writes_the_store": (_c_rule_elsewhere, "may write (INSERT", _l_insert_elsewhere),
    "insert_where_a_definer_trigger_writes_the_store": (_c_definer_trigger_elsewhere, "may write (INSERT",
                                                        _l_insert_elsewhere),
    "no_schema_of_its_own_and_a_writer_group": (_c_no_schema_of_its_own, "may write (UPDATE", _l_set_role_to_the_group),
    "the_store_second_on_the_path": (_c_store_second_on_the_path, "may write (UPDATE", _l_store_second_on_the_path),
    # Found closing that class (11 November):
    "owner_of_a_function_the_store_depends_on": (_c_owns_a_function, "owns function", _l_owns_a_function),
    "security_definer_chain_through_an_owner_that_cannot_write": (_c_definer_chain, "may execute SECURITY DEFINER",
                                                                  _l_secdef_elsewhere),
    "alter_system_on_a_parameter": (_c_alter_system, "may ALTER SYSTEM SET", _l_alter_system),
    "the_catalog_shadowed_on_the_urls_search_path": (_c_catalog_shadowed, "may write (UPDATE",
                                                     _l_store_second_on_the_path),
    # USAGE on a schema is no barrier (measured, 11 November): the reference that reaches in is planted too.
    "a_security_definer_function_in_a_schema_it_cannot_name": (_c_definer_behind_a_view,
                                                               "may execute SECURITY DEFINER", _l_through_the_view),
    "insert_on_a_table_in_a_schema_it_cannot_name": (_c_insert_behind_an_atomic_function, "may write (INSERT",
                                                     _l_through_the_atomic_function),
}


# The remedy the runbook gives, and the base the tests around a clean role
# plant on. Each: (setup, None, the statement the write would need or None).

def _n_definer_it_may_not_execute(c, q, schema, role):
    # In the STORE's own schema, which the role uses: EXECUTE revoked from
    # PUBLIC and granted to no role the session can act as.
    _su_exec(c, f"CREATE FUNCTION {q}.escalate() RETURNS int LANGUAGE sql SECURITY DEFINER AS "
                f"$$ UPDATE {q}.triples SET weight = 0.99 WHERE subject = 'io'; SELECT 1 $$",
             f"REVOKE EXECUTE ON FUNCTION {q}.escalate() FROM PUBLIC")
    return role, []


def _n_nothing(c, q, schema, role):
    return role, []


NOT_A_WRITE_PATH = {
    "a_security_definer_function_it_may_not_execute": (_n_definer_it_may_not_execute, None,
                                                       'SELECT "{schema}".escalate()'),
    "nothing_planted": (_n_nothing, None, None),
}


@pytest.fixture
def write_path(tmp_path):
    """Build a fresh schema with a NEXUS store and a login role holding SELECT
    plus one write path, for F1's companions. Yields a builder; everything it
    makes is dropped afterwards (schemas and roles, whether setup finished or
    raised)."""
    if not PG_URL:
        pytest.skip(NO_PG)
    import psycopg
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    from backend.memory.lineage import LineageStore
    from backend.store import StoreTarget
    from psycopg import sql
    built: list[tuple[str, list[str]]] = []

    def build(kind: str):
        setup, reason, land = (WRITE_PATH_CASES | NOT_A_WRITE_PATH)[kind]
        h = hashlib.sha1((str(tmp_path) + kind).encode()).hexdigest()[:16]
        schema, role = f"w6_{h}", f"w6r_{h}"
        roles = [role]
        built.append((schema, roles))
        store = TripleStore(StoreTarget.postgres(PG_URL, schema=schema))
        store.add(subject="io", relation="orbits", object="jupiter", weight=0.5, tier=1,
                  granularity=Granularity.TOPIC, source="asserted")
        LineageStore(StoreTarget.postgres(PG_URL, schema=schema)).close()
        store.close()
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(RO_PASSWORD)))
            c.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
            c.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
            made = setup(c, f'"{schema}"', schema, role)
            named, extra = made[0], made[1]
            roles.extend(extra)
        # A case may name the URL it opens with (another login; a setting in
        # the URL's options); by default the planted role's own.
        url = made[2] if len(made) > 2 else _role_url(schema, role)
        return {"url": url, "role": role, "named": named,
                "reason": reason, "land": land, "schema": schema, "q": f'"{schema}"'}

    try:
        yield build
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            for schema, roles in built:
                for r in roles[::-1]:
                    c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s", (r,))
                    c.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(r)))
                    c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(r)))
                for made in (schema + "_y", schema + "_x", schema):
                    c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(made)))


@pytest.mark.parametrize("kind", list(WRITE_PATH_CASES))
def test_r6_a_role_that_could_write_through_any_path_is_refused_by_name(write_path, kind):
    from scripts._readonly import ReadOnlyRefused, open_read_only
    case = write_path(kind)
    try:
        db = open_read_only(case["url"], url_ok=True)
    except ReadOnlyRefused as e:
        msg = str(e)
        assert case["reason"] in msg, f"the refusal does not state the write path: {msg}"
        assert case["named"] in msg, f"the refusal does not name the role {case['named']!r} it comes through: {msg}"
        assert RO_PASSWORD not in msg, "the URL's password leaked into the refusal"
        print(f"R6 {kind}: {msg.split('read-only: ', 1)[-1][:180]}")
        return
    # The pre-fix branch: the old barrier 3 let a write-capable role in. Show
    # the consequence — the role writes the store through its path (law 16).
    import psycopg
    try:
        _rw(db)
        with psycopg.connect(_as_role_superuser(case["schema"]), autocommit=True) as su:
            proof = case["land"](db, su, case["q"], case["schema"], case["role"])
    finally:
        db.close()
    pytest.fail(f"{kind}: open_read_only ADMITTED a role that can write, and its write LANDED: {proof}")


def _as_role_superuser(schema: str) -> str:
    sep = "&" if "?" in PG_URL else "?"
    return f"{PG_URL}{sep}options=-c%20search_path%3D{schema}"


def test_r6b_a_select_plus_createdb_role_opens(write_path, tmp_path):
    """The positive case the deployment needs: the backup role holds SELECT
    and CREATEDB (CREATEDB is a role attribute, not a store privilege) and
    opens read-only, the selftest returning the refusal."""
    if not PG_URL:
        pytest.skip(NO_PG)
    import psycopg
    from psycopg import sql
    from scripts._readonly import assert_write_refused, open_read_only
    h = hashlib.sha1((str(tmp_path) + "createdb").encode()).hexdigest()[:16]
    schema, role = f"w6b_{h}", f"w6br_{h}"
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    from backend.store import StoreTarget
    try:
        TripleStore(StoreTarget.postgres(PG_URL, schema=schema)).close()
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(sql.SQL("CREATE ROLE {} LOGIN CREATEDB PASSWORD {}").format(sql.Identifier(role), sql.Literal(RO_PASSWORD)))
            c.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
            c.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
        db = open_read_only(_role_url(schema, role), url_ok=True)
        try:
            assert db.backend == "postgres" and db.read_only is True
            refusal = assert_write_refused(db, table="triples")
            assert refusal.startswith(("ReadOnlySqlTransaction", "InsufficientPrivilege")), refusal
        finally:
            db.close()
        print(f"R6b: SELECT+CREATEDB opened; selftest {refusal[:60]}")
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s", (role,))
            c.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(role)))
            c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
            c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def test_r6d_a_security_definer_function_the_role_may_not_execute_does_not_refuse_it(write_path):
    """The other side of R6, and the remedy the runbook gives for it: revoke
    EXECUTE from PUBLIC. The same function that refuses a role in R6, in the
    store's own schema, leaves the role OPEN once the role may not execute
    it, and the consequence that makes that right is shown: with the session
    switched to READ WRITE the call is refused by the server, and the store
    is as it was."""
    import psycopg
    from scripts._readonly import assert_write_refused, open_read_only
    case = write_path("a_security_definer_function_it_may_not_execute")
    db = open_read_only(case["url"], url_ok=True)         # a ReadOnlyRefused here is the over-refusal
    try:
        assert assert_write_refused(db, table="triples").startswith(("ReadOnlySqlTransaction", "InsufficientPrivilege"))
        _rw(db)
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as e:
            db.raw.execute(case["land"].format(schema=case["schema"]))
        assert "permission denied for function escalate" in str(e.value), e.value
    finally:
        db.close()
    with psycopg.connect(_as_role_superuser(case["schema"]), autocommit=True) as su:
        assert su.execute("SELECT weight FROM triples WHERE subject = 'io'").fetchone()[0] == 0.5
    print(f"R6d: opened; the call it would need: {str(e.value).splitlines()[0]}")


def test_r6e_a_role_that_sees_no_schema_is_refused_by_name(write_path):
    """The delta check's D4, its plain half: a login with USAGE on no schema
    of its search path opened with `current_schema()` NULL and failed later,
    at a script's first statement, on a table that "does not exist". It is
    refused by name at the open."""
    import psycopg
    from scripts._readonly import ReadOnlyRefused, open_read_only
    case = write_path("nothing_planted")
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'REVOKE USAGE ON SCHEMA {case["q"]} FROM "{case["role"]}"')
    try:
        db = open_read_only(case["url"], url_ok=True)
    except ReadOnlyRefused as e:
        assert "sees no schema on its search path" in str(e) and RO_PASSWORD not in str(e), str(e)
        print(f"R6e: {str(e).split('read-only: ', 1)[-1]}")
        return
    try:
        seen = db.raw.execute("SELECT current_schema()").fetchone()[0]
        try:
            db.execute("SELECT COUNT(*) FROM triples")
            later = "and its first read answered"
        except Exception as e:                            # noqa: BLE001 — the unnamed failure the refusal replaces
            later = f"and its first read failed unnamed: {type(e).__name__}: {str(e).splitlines()[0]}"
    finally:
        db.close()
    pytest.fail(f"open_read_only ADMITTED a role that sees no schema (current_schema() {seen!r}) {later}")


def test_r6f_a_temporary_table_in_another_session_of_the_role_is_not_ownership(write_path):
    """The check refuses a role that owns anything in the database, and a
    temporary table is owned by its session's role for as long as that session
    lives: another session of the same read-only role (an operator's psql)
    must not refuse the backup. The companion is in the same test: the same
    role owning a table that is NOT temporary is refused, naming it."""
    import psycopg
    from scripts._readonly import ReadOnlyRefused, open_read_only
    case = write_path("nothing_planted")
    with psycopg.connect(case["url"], autocommit=True) as elsewhere:
        elsewhere.execute("CREATE TEMP TABLE held_elsewhere (x int)")
        owned = elsewhere.execute("SELECT count(*) FROM pg_class WHERE relname = 'held_elsewhere' "
                                  "AND pg_get_userbyid(relowner) = current_user").fetchone()[0]
        assert owned == 1, "the fixture did not make the temporary table it meant to"
        db = open_read_only(case["url"], url_ok=True)     # a ReadOnlyRefused here names pg_temp: the over-refusal
        db.close()
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'CREATE TABLE {case["q"]}.kept (x int)')
        c.execute(f'ALTER TABLE {case["q"]}.kept OWNER TO "{case["role"]}"')
    with pytest.raises(ReadOnlyRefused, match=f"owns table {case['schema']}.kept"):
        open_read_only(case["url"], url_ok=True)
    print("R6f: opened beside its own temporary table; refused once it owned a table that is not temporary")


def test_r7_the_reasons_are_sentences_built_from_the_facts():
    """`write_path_reasons` (no database): one sentence per role and kind,
    each list cut at NAMED_PER_REASON names with the rest counted (a store's
    owner opened read-only holds every table: the refusal stays readable);
    a superuser among the facts is named alone."""
    from scripts._readonly import NAMED_PER_REASON, write_path_reasons
    tables = [(7, "w", "table", f"s.t{i}", "UPDATE") for i in range(NAMED_PER_REASON + 3)]
    rows = tables + [(7, "w", "table", "s.t0", "INSERT"), (4, "o", "owns", "function s.f()", None),
                     (2, "login", "member", "pg_write_server_files", None),
                     (2, "login", "member", "pg_write_all_data", None),
                     (10, "PUBLIC", "default", "in every schema by a default privilege of the role c", "INSERT"),
                     (11, "w", "definer", "x.f (owner o)", None)]
    said = write_path_reasons(rows)
    shown = ", ".join(f"s.t{i}" for i in range(NAMED_PER_REASON))
    assert said == [
        "the role login is a member of pg_write_all_data",
        "the role login is a member of pg_write_server_files (it writes or runs programs as the server's own OS "
        "user, whose files the store is)",
        "the role o owns function s.f() in this database (an owner can drop or re-grant what it owns, and what "
        "depends on it goes with it)",
        f"the role w may write (INSERT, UPDATE) on {shown} and 3 more",
        "the role PUBLIC is granted INSERT on future tables in every schema by a default privilege of the role c",
        "the role w may execute SECURITY DEFINER x.f (owner o) (it runs with its owner's rights, and its body is "
        "not read)",
    ], said
    assert write_path_reasons(rows + [(1, "boss", "superuser", None, None)]) == ["the role boss is a superuser"]
    assert write_path_reasons([]) == []


def test_r6c_backup_refuses_a_column_update_role_by_name(write_path, tmp_path):
    """The script-level case: scripts/backup.py --database-url with a
    column-UPDATE role is refused by name, exits as the other refusals (2),
    and writes no output folder. The refusal is take_pg_backup's open, before
    any folder or pg_dump."""
    case = write_path("column_update")
    root = tmp_path / "dataroot"
    (root / "snapshots").mkdir(parents=True)
    (root / "turn_metrics.jsonl").write_text("{}\n", encoding="utf-8")
    out = tmp_path / "backups"
    env = {k: v for k, v in os.environ.items()
           if k not in ("NEXUS_STORE", "NEXUS_DATABASE_URL", "NEXUS_DATA_ROOT", "NEXUS_BACKUP_DATABASE_URL")}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", NEXUS_ENV_ROOT=str(tmp_path),
               PYTHONPATH=os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")])))
    proc = _proc.run([sys.executable, str(REPO / "scripts" / "backup.py"), "--data-root", str(root),
                           "--out", str(out), "--database-url", case["url"]], cwd=tmp_path, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    print(f"R6c: exit {proc.returncode}; {proc.stdout.splitlines()[-1] if proc.stdout else ''!r}")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "may write columns (UPDATE)" in proc.stdout and "Nothing was written" in proc.stdout, proc.stdout
    assert RO_PASSWORD not in proc.stdout + proc.stderr
    assert not out.exists(), "the refused backup wrote an output folder"


# ---------------------------------------------------------------------------
# S — the scripts on a URL
# ---------------------------------------------------------------------------

def _run(args: list[str], tmp_path: Path, **env: str) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k not in ("NEXUS_STORE", "NEXUS_DATABASE_URL", "NEXUS_DATA_ROOT")}
    e.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", NEXUS_ENV_ROOT=str(tmp_path),
             NEXUS_PORT="9", OLLAMA_URL="http://127.0.0.1:9",
             PYTHONPATH=os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")])))
    e.update(env)
    return _proc.run([sys.executable, *args], cwd=tmp_path, env=e, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)


SCRIPT_RUNS = [
    ("scripts/cp_b49_tally.py", ["--db", "{url}", "--selftest", "--capture", "{tmp}/absent.jsonl"], True),
    ("scripts/as2_instance_windows.py", ["--db", "{url}", "--selftest", "--frozen-since", "2026-10-01"], True),
    ("scripts/diag_downstream.py", ["--db", "{url}"], True),
    ("scripts/replay_check.py", ["--db", "{url}", "--report-dir", "{tmp}/reports"], True),
    ("scripts/archive_pregate.py", ["--kb", "{url}", "--report", "{tmp}/pregate.json"], False),
    ("scripts/archive_consolidation.py", ["--kb", "{url}", "--report", "{tmp}/consolidation.json"], False),
]


@pytest.mark.parametrize("script, argv, prints_first_line", SCRIPT_RUNS, ids=[s[0] for s in SCRIPT_RUNS])
def test_s1_an_instance_script_reads_the_hosted_store_by_url(pg_store, tmp_path, script, argv, prints_first_line):
    url = pg_store["ro"]
    args = [str(REPO / script)] + [a.format(url=url, tmp=tmp_path) for a in argv]
    before = sorted(p.name for p in tmp_path.iterdir())
    proc = _run(args, tmp_path)
    out = proc.stdout + proc.stderr
    print(f"S1 {script}: exit {proc.returncode}; first line {proc.stdout.splitlines()[0] if proc.stdout else ''!r}")
    assert proc.returncode == 0, out[-3000:]
    assert RO_PASSWORD not in out, "the URL's password was printed"
    if prints_first_line:
        first = proc.stdout.splitlines()[0]
        assert first.startswith("database: postgres postgresql://") and ":***@" in first, first
        assert "a read-only session and a role that cannot write" in first, first
    if "--selftest" in args:
        assert "selftest: write refused (" in out, out[-1500:]
    created = sorted(set(p.name for p in tmp_path.iterdir()) - set(before))
    assert all(n in ("reports", "pregate.json", "consolidation.json") for n in created), created


def test_s2_the_confirmed_archiver_takes_the_lock_on_the_urls_target(pg_store, tmp_path):
    from backend.store import StoreTarget
    from backend.store_lock import read_holder
    url = pg_store["superuser"]
    report = tmp_path / "confirmed.json"
    proc = _run([str(REPO / "scripts/archive_consolidation.py"), "--kb", url, "--confirm", "--source", "nothing_of_this_source",
                 "--report", str(report)], tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out[-3000:]
    record = json.loads(report.read_text(encoding="utf-8"))
    assert record["confirmed"] is True and record["archived"] == 0 and record["md5_before"] is None
    assert record["kb"].startswith("postgresql://") and RO_PASSWORD not in record["kb"]
    holder = read_holder(StoreTarget.postgres(url))
    assert holder is not None and holder["mode"] == "script:archive_consolidation" and holder["released"] is True, holder


def test_s3_backup_refuses_a_url_by_name(tmp_path):
    proc = _run([str(REPO / "scripts/backup.py"), "--data-root", "postgresql://u:pw@127.0.0.1:1/x_test",
                 "--out", str(tmp_path / "out")], tmp_path)
    assert proc.returncode == 2 and "a database URL was given there" in proc.stdout \
        and "--database-url" in proc.stdout, proc.stdout + proc.stderr          # part 5: the hosted form's flag named
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------------------
# G — the URL-capable set, derived and pinned
# ---------------------------------------------------------------------------

def url_capable_scripts(sources: dict[str, str]) -> set[str]:
    """The files whose `open_read_only(...)` calls pass `url_ok=True`, by AST."""
    out = set()
    for rel, text in sources.items():
        tree = ast.parse(text)
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == "open_read_only":
                for kw in n.keywords:
                    if kw.arg == "url_ok" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        out.add(rel)
    return out


def _tracked_sources() -> dict[str, str]:
    files = list(tracked("scripts/*.py", ":(glob)*.py"))        # the scripts and the ROOT files (glob: no depth)
    return {Path(p).resolve().relative_to(REPO).as_posix(): Path(p).read_text(encoding="utf-8") for p in files}


def test_g_the_url_capable_scripts_are_exactly_the_pinned_eight():
    from scripts._readonly import ReadOnlyRefused, open_read_only
    capable = url_capable_scripts(_tracked_sources())
    assert capable == URL_CAPABLE, sorted(capable ^ URL_CAPABLE)
    print(f"G: {len(capable)} scripts take a database URL: {sorted(capable)}")
    with pytest.raises(ReadOnlyRefused) as e:                  # the refusal names the set, every member
        open_read_only("postgresql://u@127.0.0.1:1/x_test")
    unnamed = sorted(rel for rel in capable if Path(rel).name not in str(e.value))
    assert not unnamed, f"the refusal does not name {unnamed}"


def test_g_companion_a_ninth_script_passing_url_ok_is_named():
    sources = _tracked_sources()
    sources["scripts/planted_url_reader.py"] = ("from scripts._readonly import open_read_only\n"
                                               "conn = open_read_only('x', url_ok=True)\n")
    capable = url_capable_scripts(sources)
    assert capable - URL_CAPABLE == {"scripts/planted_url_reader.py"}
