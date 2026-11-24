"""W3b part 2 — the Postgres backend (`run/specs/W3b.md` S2: D3 to D7, D12,
D13; S3: T2 to T6, T12, T15).

Two populations of tests: the DIALECT's rules, pure functions of SQL text,
run everywhere; the BACKEND's behaviour runs against the Postgres named by
`NEXUS_TEST_DATABASE_URL` and is skipped BY NAME where none is named
(windows and macos in CI; a sandbox without the scratch cluster). Under
`NEXUS_TEST_STORE=postgres` the whole suite's stores run on it
(`tests/conftest.py`); this file is what proves the backend itself.

R   THE RULES (D4): every rewrite the construct table asked for, on text;
    every refusal by name; masking (a `?` or a `%` in a literal, a word in
    a comment, are left alone).
D   THE DDL TWIN: every tracked DDL translates; the twin has no SQLite type
    names, drops `REFERENCES`, gives the three AUTOINCREMENT tables their
    trigger and the two rowid tables their ordinal; applied on Postgres,
    `columns()` equals SQLite's `PRAGMA table_info` tuples for every table
    (the one declared difference: a PRIMARY KEY column reads `notnull = 1`
    on Postgres and `0` for SQLite's rowid alias; the ordinal column is
    excluded).
G   THE DERIVED GUARD (T3): every SQL string the tracked backend holds
    (collected by AST from execute/executemany/executescript sites, with
    module constants resolved into f-strings) translates and PREPAREs on
    a Postgres schema holding the twin DDL. A statement the dialect cannot
    carry fails here, not a tester. Companion: a planted GROUP_CONCAT is
    refused by name.
P   THE PARITY CLAIMS the backend alone can show: P6 (ids through inserts,
    an upsert hit, a rollback and an explicit id, the same on both), P7
    (values bit for bit, `-0.0` declared), T4 (transactions: sqlite3's rule
    on Postgres, the auto-rollback of a failed opening statement, the busy
    timeout's twin), the `extra_float_digits` refusal.
S   THE SKIP LIST is pinned (T12): every module listed in
    `tests/_sqlite_only.py` touches SQLite directly, by AST.
B   THE BUNDLE excludes the driver (D13, T15): `never_frozen` names it.
"""
from __future__ import annotations

import ast
import math
import os
import random
import re
import sqlite3
import struct
from pathlib import Path

import pytest

from backend.store import dialect
from backend.store.dialect import DialectError, translate, translate_ddl
from tests._tracked import tracked, tracked_backend

REPO = Path(__file__).resolve().parent.parent
# The URL is read by tests/conftest.py and removed from the environment there
# (no subprocess may inherit the fixture: a child pytest session would sweep
# this session's schemas), so it is taken from the conftest, not os.environ.
from tests.conftest import POSTGRES_TEST, POSTGRES_TEST_URL as PG_URL  # noqa: E402
NO_PG = "no Postgres named: NEXUS_TEST_DATABASE_URL is unset (the Postgres job sets it; windows and macos run SQLite)"

DDLS = [("backend.kb.schema", "SCHEMA_SQL"), ("backend.kb.schema", "ARCHIVAL_EVENTS_DDL"),
        ("backend.kb.schema", "WORK_UNIT_DDL"), ("backend.kb.schema", "REFUSED_REQUESTS_DDL"),
        ("backend.memory.lineage", "SCHEMA_SQL"), ("backend.kb.recalibration", "SCHEMA_SQL"),
        ("backend.memory.graph_store", "SCHEMA_SQL"), ("backend.kb.trails", "SCHEMA_SQL"),
        ("backend.mcp.coverage", "SCHEMA_SQL"), ("backend.kb.semantic_index", "_DDL"),
        ("backend.memory.chain", "SCHEMA_SQL")]


def _ddl(mod: str, name: str) -> str:
    import importlib
    return getattr(importlib.import_module(mod), name)


# ---------------------------------------------------------------------------
# R — the rules
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sql,expected", [
    ("SELECT * FROM t WHERE a = ? AND b = ?", "SELECT * FROM t WHERE a = %s AND b = %s"),
    ("SELECT '?' AS q, '%' AS p FROM t WHERE a = ?", "SELECT '?' AS q, '%%' AS p FROM t WHERE a = %s"),   # the literal's % doubled too (psycopg scans it)
    ("INSERT OR IGNORE INTO t (a) VALUES (?)", "INSERT INTO t (a) VALUES (%s) ON CONFLICT DO NOTHING"),
    ("INSERT OR IGNORE INTO t (a) VALUES (?) RETURNING id",
     "INSERT INTO t (a) VALUES (%s) ON CONFLICT DO NOTHING RETURNING id"),
    ("UPDATE t SET w = MAX(t.w, excluded.w), tier = MIN(t.tier, excluded.tier), m = MAX(ts)",
     "UPDATE t SET w = GREATEST(t.w, excluded.w), tier = LEAST(t.tier, excluded.tier), m = MAX(ts)"),
    ("SELECT * FROM t WHERE LOWER(subject) LIKE ? OR LOWER(object) LIKE ?",       # part 4: ESCAPE '' on each
     "SELECT * FROM t WHERE nexus_lower(subject) LIKE %s ESCAPE '' OR nexus_lower(object) LIKE %s ESCAPE ''"),
    ("SELECT * FROM t WHERE LOWER(r) LIKE 'gate:%' AND a = ?",                     # a literal pattern, % doubled
     "SELECT * FROM t WHERE nexus_lower(r) LIKE 'gate:%%' ESCAPE '' AND a = %s"),
    ("SELECT * FROM t WHERE LOWER(a) LIKE ? ESCAPE '!'",                          # a stated ESCAPE is kept
     "SELECT * FROM t WHERE nexus_lower(a) LIKE %s ESCAPE '!'"),
    ("SELECT IFNULL(a, 0), CAST(x AS INTEGER), CAST(y AS REAL) FROM t",
     "SELECT COALESCE(a, 0), CAST(x AS BIGINT), CAST(y AS DOUBLE PRECISION) FROM t"),
    ("SELECT COALESCE(MAX(ts), 0) FROM t", "SELECT COALESCE(MAX(ts), 0) FROM t"),
    ("SELECT a FROM t -- a comment with ? and PRAGMA and %\nWHERE b = ?", "SELECT a FROM t  \nWHERE b = %s"),
    ("UPDATE t SET last = CASE WHEN ? = 1 THEN excluded.last ELSE t.last END",
     "UPDATE t SET last = CASE WHEN %s = 1 THEN excluded.last ELSE t.last END"),
])
def test_r_each_rewrite(sql, expected):
    assert translate(sql) == expected


def test_r_a_percent_is_doubled_only_with_parameters_inside_literals_too():
    """psycopg's placeholder scan is textual and reads string literals
    (W3b part 3 measured `'a%b'` counted as a `%b` placeholder and `'gate:%'`
    refused), so with parameters every `%` is doubled, the literals'
    included; without parameters none is."""
    assert translate("SELECT 'a%b' FROM t WHERE x = ?", has_params=True) == "SELECT 'a%%b' FROM t WHERE x = %s"
    assert translate("SELECT 'a%b' FROM t WHERE x = 1", has_params=False) == "SELECT 'a%b' FROM t WHERE x = 1"
    assert translate("SELECT 50 % 7", has_params=True) == "SELECT 50 %% 7"
    assert translate("SELECT 50 % 7", has_params=False) == "SELECT 50 % 7"


def test_r_a_literal_percent_round_trips_with_parameters(pg):
    """The consequence on the driver: the literal arrives as written, the
    parameter is bound, and a `format('%I.%I', …)` call (the read-only open's
    privilege probe) runs."""
    db, _ = pg
    assert db.execute("SELECT 'a%b' WHERE 1 = ?", (1,)).fetchone()[0] == "a%b"
    assert db.execute("SELECT 'gate:%' WHERE ? = 1", (1,)).fetchone()[0] == "gate:%"
    assert db.execute("SELECT format('%I.%I', 'a b', ?)", ("c",)).fetchone()[0] == '"a b".c'


def test_r_a_backslash_in_a_pattern_matches_the_same_rows_on_both(pg, tmp_path):
    """W3b part 4 (found by P5): Postgres's LIKE escapes with the backslash,
    SQLite's has no escape character. The same statement, `ESCAPE ''`
    appended by the dialect, finds the same rows on both; the pattern
    without the rule finds others on Postgres (the measurement that found
    it, kept as the second half)."""
    from backend.store import StoreTarget, connect
    db, _ = pg
    lite = connect(StoreTarget.sqlite(tmp_path / "like.db"))
    words = ["a\\b", "ab", "trail\\", "trail", "x\\%y", "x%y"]
    patterns = ["%a\\b%", "%trail\\", "%trail\\%", "%\\%", "x\\%y"]
    found = {}
    try:
        for name, conn in (("sqlite", lite), ("postgres", db)):
            conn.executescript("CREATE TABLE IF NOT EXISTS w (k INTEGER PRIMARY KEY, s TEXT NOT NULL)")
            conn.executemany("INSERT INTO w (k, s) VALUES (?, ?)", list(enumerate(words, 1)))
            conn.commit()
            found[name] = {p: [r[0] for r in conn.execute("SELECT k FROM w WHERE LOWER(s) LIKE ? ORDER BY k", (p,))]
                           for p in patterns}
        raw = {}
        for p in patterns:                   # the same pattern as the driver sends it without the rule
            try:
                raw[p] = [r[0] for r in db.raw.execute("SELECT k FROM w WHERE lower(s) LIKE %s ORDER BY k", (p,))]
            except Exception as e:           # noqa: BLE001 — a pattern ending in the escape is refused
                raw[p] = f"refused: {type(e).__name__}"
    finally:
        lite.close()
    print(f"R: SQLite {found['sqlite']}; Postgres without the rule {raw}")
    assert found["postgres"] == found["sqlite"] and found["sqlite"]["%a\\b%"] == [1], found
    assert raw != found["sqlite"], "without the rule the backends agree: the measurement no longer stands"


@pytest.mark.parametrize("sql,named", [
    ("PRAGMA table_info(triples)", "PRAGMA"),
    ("SELECT name FROM sqlite_master WHERE type = 'table'", "sqlite_master"),
    ("SELECT GROUP_CONCAT(a) FROM t", "GROUP_CONCAT"),
    ("SELECT RANDOM()", "RANDOM"),
    ("SELECT typeof(a) FROM t", "typeof"),
    ("SELECT strftime('%s', 'now')", "date functions"),
    ("SELECT * FROM t WHERE a GLOB 'x*'", "GLOB"),
    ("INSERT OR REPLACE INTO t (a) VALUES (?)", "INSERT OR"),
    ("REPLACE INTO t (a) VALUES (?)", "REPLACE INTO"),
    ("SELECT * FROM t WHERE subject LIKE ?", "LIKE"),
    ("SELECT * FROM t WHERE UPPER(subject) LIKE ?", "LIKE"),
    ("SELECT * FROM t WHERE LOWER(subject) LIKE LOWER(?)", "right operand"),       # part 4: the pattern's shape
    ('SELECT * FROM t WHERE LOWER(subject) LIKE "pattern_column"', "right operand"),
    ("INSERT OR IGNORE INTO t (a) VALUES (?) ON CONFLICT (a) DO NOTHING", "ON CONFLICT"),
])
def test_r_each_refusal_is_by_name(sql, named):
    with pytest.raises(DialectError) as e:
        translate(sql)
    assert named in str(e.value)


def test_r_is_dml_follows_sqlite3s_first_word_rule():
    assert dialect.is_dml("INSERT INTO t VALUES (1)") and dialect.is_dml("  update t set a = 1")
    assert dialect.is_dml("-- note\nDELETE FROM t") and dialect.is_dml("(REPLACE INTO t VALUES (1))")
    assert not dialect.is_dml("SELECT 1") and not dialect.is_dml("WITH c AS (SELECT 1) INSERT INTO t SELECT * FROM c")
    assert not dialect.is_dml("CREATE TABLE t (a)") and not dialect.is_dml("BEGIN")


def test_r_masking_restores_literals_and_identifiers_byte_for_byte():
    sql = """SELECT "weird col", 'it''s ?%' FROM t /* c */ WHERE a = ?"""
    assert translate(sql) == """SELECT "weird col", 'it''s ?%%' FROM t   WHERE a = %s"""


# ---------------------------------------------------------------------------
# D — the DDL twin
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mod,name", DDLS)
def test_d_every_tracked_ddl_translates_without_sqlite_spellings(mod, name):
    twin = translate_ddl(_ddl(mod, name))
    masked, _ = dialect.mask(twin)
    for spelling in (" INTEGER", " REAL ", " REAL,", " BLOB", "AUTOINCREMENT", "REFERENCES"):
        assert spelling not in masked, (spelling, twin)
    assert "BIGINT" in masked and ("DOUBLE PRECISION" in masked or "ts" not in masked)


def test_d_the_three_autoincrement_tables_get_the_trigger_and_the_two_rowid_tables_their_ordinal():
    schema = translate_ddl(_ddl("backend.kb.schema", "SCHEMA_SQL"))
    assert "nexus_autoinc__triples" in schema and "nexus_autoinc__chunks" in schema
    coverage = translate_ddl(_ddl("backend.mcp.coverage", "SCHEMA_SQL"))
    assert "nexus_autoinc__mcp_coverage" in coverage and "nexus_autoinc__mcp_meta" not in coverage
    archival = translate_ddl(_ddl("backend.kb.schema", "ARCHIVAL_EVENTS_DDL"))
    assert "rowid BIGINT" in archival and "nexus_rowid__archival_events" in archival
    graph = translate_ddl(_ddl("backend.memory.graph_store", "SCHEMA_SQL"))
    assert "rowid BIGINT" in graph and "nexus_rowid__hebbian_edges" in graph
    lineage = translate_ddl(_ddl("backend.memory.lineage", "SCHEMA_SQL"))
    assert "rowid" not in lineage and "nexus_autoinc" not in lineage, "a TEXT-keyed table gets neither"


def test_d_unknown_ddl_is_refused_by_name():
    with pytest.raises(DialectError, match="not spelled here"):
        translate_ddl("CREATE VIEW v AS SELECT 1")
    with pytest.raises(DialectError, match="column type"):
        translate_ddl("CREATE TABLE t (a DATETIME)")


# ---------------------------------------------------------------------------
# the Postgres fixture for this file
# ---------------------------------------------------------------------------

@pytest.fixture
def pg(tmp_path):
    """A fresh schema on the named Postgres, dropped afterwards."""
    if not PG_URL:
        pytest.skip(NO_PG)
    import hashlib
    import psycopg
    from backend.store import StoreTarget, connect
    schema = "t_" + hashlib.sha1(str(tmp_path).encode()).hexdigest()[:20]
    target = StoreTarget.postgres(PG_URL, schema=schema)
    db = connect(target)
    yield db, target
    db.close()
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _real_schema(target):
    """Every table as the store classes make it on `target`: the DDL, the
    open-time migrations (`ALTER TABLE … ADD COLUMN`), the shared tables."""
    from backend.kb.recalibration import RecalibrationStore
    from backend.kb.semantic_index import SemanticIndex
    from backend.kb.tiered_store import TripleStore
    from backend.kb.trails import TrailStore
    from backend.kb.work_units import WorkUnitStore
    from backend.memory.chain import NexusArchiveChain
    from backend.memory.graph_store import HebbianGraphStore
    from backend.memory.lineage import LineageStore
    from backend.mcp.coverage import CoverageStore
    store = TripleStore(target)
    LineageStore(target), RecalibrationStore(target), TrailStore(target), HebbianGraphStore(target)
    NexusArchiveChain(target)
    WorkUnitStore(store.conn)
    CoverageStore(store.conn)
    SemanticIndex(store)._ensure()
    return store


def test_d_columns_equal_table_infos_tuples_for_every_table(pg, tmp_path):
    """The DDL pin's twin: Postgres's `columns()` against SQLite's for every
    table the store classes make (the DDL and the open-time migrations),
    with the two declared differences applied."""
    from backend.store import StoreTarget
    db, target = pg
    pg_store = _real_schema(target)
    # a SQLite target by name: under the Postgres fixture a bare path would be
    # mapped to a schema, and this test needs the real SQLite twin beside it
    lite_store = _real_schema(StoreTarget.sqlite(tmp_path / "lite.db"))
    tables = lite_store.conn.tables()
    assert len(tables) >= 20, tables
    assert pg_store.conn.tables() == tables, "tables(): the same names in the same order on both backends"
    for table in tables:
        s = [tuple(c) for c in lite_store.conn.columns(table)]
        p = [tuple(c) for c in pg_store.conn.columns(table) if c.name != "rowid"]
        # The declared difference: SQLite reports notnull 0 for any PRIMARY KEY
        # column not declared NOT NULL (the rowid alias, and a TEXT key such
        # as archival_events.event_id); Postgres's PRIMARY KEY implies NOT NULL.
        s = [(cid, n, t, (1 if pk else nn), d, pk) for cid, n, t, nn, d, pk in s]
        assert p == s, (table, p, s)
    print(f"D: {len(tables)} tables compared column by column")


# ---------------------------------------------------------------------------
# G — the derived guard: every backend statement prepares on Postgres
# ---------------------------------------------------------------------------

def _module_constants(tree: ast.Module) -> dict[str, str]:
    consts = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = n.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                consts[n.targets[0].id] = v.value
    return consts


DYNAMIC = object()          # a statement whose text the AST cannot spell (a runtime identifier)


def _local_strings(fn: ast.AST) -> dict[str, str]:
    """Function-local names bound to a string: a constant, or a `.join(...)`
    of placeholders (an IN list), spelled as one `?`."""
    out: dict[str, str] = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = n.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                out[n.targets[0].id] = v.value
            elif isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "join":
                out[n.targets[0].id] = "?"
    return out


def _sql_text(node: ast.AST, names: dict[str, str]):
    """The SQL a call argument spells, with known names resolved: a string
    constant, a constant's name, an f-string (a `join` of placeholders is one
    `?`), a concatenation. `DYNAMIC` when a formatted value is unknown (a
    runtime identifier); None when the argument is not SQL text at all."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                inner = part.value
                if isinstance(inner, ast.Name) and inner.id in names:
                    out.append(names[inner.id])
                elif isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr == "join":
                    out.append("?")
                else:
                    return DYNAMIC
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _sql_text(node.left, names), _sql_text(node.right, names)
        if left is DYNAMIC or right is DYNAMIC:
            return DYNAMIC
        if left is not None and right is not None:
            return left + right
        return left if right is None else None
    return None


def backend_statements() -> tuple[list[tuple[str, int, str, str]], list[str]]:
    """Every execute/executemany/executescript call in the tracked backend:
    `(file, line, method, sql)` for the ones the AST can spell, and the
    `file:line` of the ones it cannot (a runtime identifier in the text)."""
    spelled: dict[tuple, tuple] = {}
    dynamic: set[str] = set()
    for p in tracked_backend():
        p = Path(p)
        rel = (p.relative_to(REPO) if p.is_absolute() else p).as_posix()
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        consts = _module_constants(tree)
        scopes = [(tree, consts)] + [(fn, {**consts, **_local_strings(fn)})
                                     for fn in ast.walk(tree) if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for scope, names in scopes:            # outer scopes first; an inner function's names win
            for n in ast.walk(scope):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                        and n.func.attr in ("execute", "executemany", "executescript") and n.args:
                    key = (rel, n.lineno, n.col_offset)
                    sql = _sql_text(n.args[0], names)
                    if sql is DYNAMIC:
                        if not rel.startswith("backend/store/"):
                            dynamic.add(f"{rel}:{n.lineno}")    # the interface's own SQL is the driver's, not a store's
                        spelled.pop(key, None)
                    elif sql and sql.strip():
                        spelled[key] = (rel, n.lineno, n.func.attr, sql)
                        dynamic.discard(f"{rel}:{n.lineno}")
    return sorted(spelled.values()), sorted(dynamic)


def test_g_the_population_is_the_backends_statements():
    stmts, dynamic = backend_statements()
    files = {f for f, _l, _m, _s in stmts}
    print(f"G population: {len(stmts)} statements spelled in {len(files)} files; {len(dynamic)} with a runtime "
          f"identifier the AST cannot spell (exercised by the suite under the fixture, not here): {dynamic}")
    assert len(stmts) >= 150 and "backend/kb/tiered_store.py" in files and "backend/memory/lineage.py" in files
    # Measured 12 at W3b part 2: the open-time migrations' `ALTER TABLE {table}
    # ADD COLUMN {col} {decl}` loops (schema, lineage, recalibration) and the
    # graph store's column backfill — a table or column name from a loop
    # variable, which the AST cannot spell and the fixture runs for real.
    assert len(dynamic) <= 12, dynamic


def test_g_every_backend_statement_translates():
    """Pure: the dialect carries every statement the backend holds."""
    problems = []
    stmts, _dynamic = backend_statements()
    for rel, line, method, sql in stmts:
        if rel.startswith("backend/store/"):
            continue                                    # the interface's own introspection SQL is the driver's
        try:
            translate_ddl(sql) if (method == "executescript" or dialect.is_ddl(sql)) else translate(sql)
        except DialectError as e:
            problems.append(f"{rel}:{line}: {e}")
    assert problems == [], "\n".join(problems)


def test_g_every_backend_statement_prepares_on_postgres(pg):
    """The derived guard proper: PREPARE every translated statement against
    the schema the store classes make. A statement whose parameters PREPARE
    cannot type is counted and printed (the server infers them from context
    at run time); any other error is a statement the dialect does not carry."""
    import psycopg
    db, target = pg
    _real_schema(target)
    untyped, problems, prepared, ddl = [], [], 0, 0
    stmts, _dynamic = backend_statements()
    for i, (rel, line, method, sql) in enumerate(stmts):
        if rel.startswith("backend/store/"):
            continue
        if method == "executescript" or dialect.is_ddl(sql):
            translate_ddl(sql)                           # DDL has no PREPARE; the twin's text is the check
            ddl += 1
            continue
        pg_sql = translate(sql)
        n = pg_sql.count("%s")
        numbered = pg_sql
        for k in range(1, n + 1):
            numbered = numbered.replace("%s", f"${k}", 1)
        try:
            db.raw.execute(f"PREPARE g{i} AS {numbered}")
            db.raw.execute(f"DEALLOCATE g{i}")
            prepared += 1
        except psycopg.errors.IndeterminateDatatype:
            untyped.append(f"{rel}:{line}")
        except psycopg.Error as e:
            problems.append(f"{rel}:{line}: {type(e).__name__}: {str(e).splitlines()[0]} :: {pg_sql[:100]}")
    print(f"G: {prepared} statements prepared, {ddl} DDL statements translated; {len(untyped)} with parameters "
          f"PREPARE cannot type: {untyped}")
    assert problems == [], "\n".join(problems)
    assert prepared > 100


def test_g_companion_a_planted_statement_the_dialect_cannot_carry_is_named():
    with pytest.raises(DialectError, match="GROUP_CONCAT"):
        translate("SELECT GROUP_CONCAT(subject) FROM triples")


# ---------------------------------------------------------------------------
# P — the backend's parity claims
# ---------------------------------------------------------------------------

def _ids_through_the_three_scenarios(store, Granularity) -> list:
    out = []
    add = lambda o, w=0.5: store.add(subject="interval training", relation="improves", object=o, weight=w,
                                     tier=1, granularity=Granularity.TOPIC, source="asserted")
    out.append([add(f"o{i}") for i in range(3)])            # 1, 2, 3
    out.append(add("o0", 0.9))                               # the hit: id 1, a value consumed
    out.append(add("new after hit"))                         # 5
    try:
        with store.deferred_pass():
            out.append(add("rolled back"))                   # 6, then gone
            raise RuntimeError("abort the pass")
    except RuntimeError:
        pass
    out.append(add("new after rollback"))                    # 6 again: the sequence rolled back
    return out


def test_p6_ids_are_assigned_as_sqlite_assigns_them(pg, tmp_path):
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    _db, target = pg
    on_pg = _ids_through_the_three_scenarios(TripleStore(target), Granularity)
    on_lite = _ids_through_the_three_scenarios(TripleStore(tmp_path / "lite.db"), Granularity)
    assert on_pg == on_lite == [[1, 2, 3], 1, 5, 6, 6], (on_pg, on_lite)


def test_p6_an_explicit_id_moves_the_sequence_on_both(pg, tmp_path):
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    _db, target = pg
    nxt = {}
    for label, s in (("postgres", TripleStore(target)), ("sqlite", TripleStore(tmp_path / "lite.db"))):
        s.conn.execute("INSERT INTO triples (id, subject, relation, object, weight, tier, granularity, source, "
                       "fire_count, created_at, last_fired) VALUES (100, 'explicit id', 'improves', 'x', 0.5, 1, "
                       "'topic', 'asserted', 0, 1.0, 1.0)")
        s.conn.commit()
        nxt[label] = s.add(subject="interval training", relation="improves", object="a later claim", weight=0.5,
                           tier=1, granularity=Granularity.TOPIC, source="asserted")
    assert nxt == {"postgres": 101, "sqlite": 101}


def test_p7_values_round_trip_bit_for_bit_and_negative_zero_is_the_declared_difference(pg):
    db, _ = pg
    db.executescript("CREATE TABLE IF NOT EXISTS rt (k INTEGER PRIMARY KEY, d REAL, i INTEGER, t TEXT, b BLOB)")
    rng = random.Random(3)
    doubles = []
    while len(doubles) < 20000:
        d = struct.unpack("<d", rng.randbytes(8))[0]
        if math.isfinite(d):
            doubles.append(d)
    doubles += [0.1, 1 / 3, 1e300, 5e-324, 2.2250738585072014e-308, -0.0]
    ints = [2 ** 63 - 1, -2 ** 63, 2 ** 53 + 1, 0, -1]
    texts = ["plain", "Élan vital", "Σίσυφος", "\U0001F600 astral", "tab\tnewline\n", "quote ' and \" and \\"]
    blobs = [bytes(range(256)), b"", rng.randbytes(1024)]
    rows = [(k, d, ints[k % len(ints)], texts[k % len(texts)], blobs[k % len(blobs)]) for k, d in enumerate(doubles)]
    db.executemany("INSERT INTO rt (k, d, i, t, b) VALUES (?, ?, ?, ?, ?)", rows)
    db.commit()
    back = {r["k"]: r for r in db.execute("SELECT * FROM rt ORDER BY k")}
    mismatches = [k for k, d, i, t, b in rows
                  if struct.pack("<d", back[k]["d"]) != struct.pack("<d", d) or back[k]["i"] != i
                  or back[k]["t"] != t or bytes(back[k]["b"]) != b]
    assert mismatches == [], mismatches[:5]
    minus_zero_key = len(doubles) - 1
    assert struct.pack("<d", back[minus_zero_key]["d"]) == struct.pack("<d", -0.0), \
        "Postgres keeps -0.0's sign; SQLite stores it as 0.0 (the declared exception, D6)"
    lite = sqlite3.connect(":memory:")
    lite.execute("CREATE TABLE rt (d REAL)")
    lite.execute("INSERT INTO rt VALUES (?)", (-0.0,))
    assert struct.pack("<d", lite.execute("SELECT d FROM rt").fetchone()[0]) == struct.pack("<d", 0.0)


def test_t4_transactions_follow_sqlite3s_rule_on_postgres(pg):
    import psycopg
    from backend.store import IntegrityError, OperationalError, connect
    db, target = pg
    db.executescript("CREATE TABLE IF NOT EXISTS tx (v TEXT PRIMARY KEY)")
    other = connect(target)
    assert db.in_transaction is False
    db.execute("SELECT 1")
    assert db.in_transaction is False, "a SELECT opens nothing (psycopg's default would)"
    db.execute("INSERT INTO tx (v) VALUES (?)", ("a",))
    assert db.in_transaction is True
    assert other.execute("SELECT COUNT(*) FROM tx").fetchone()[0] == 0, "uncommitted: invisible to a second connection"
    db.rollback()
    assert other.execute("SELECT COUNT(*) FROM tx").fetchone()[0] == 0 and db.in_transaction is False
    db.execute("INSERT INTO tx (v) VALUES (?)", ("a",))
    db.commit()
    assert other.execute("SELECT COUNT(*) FROM tx").fetchone()[0] == 1
    # A failed statement that OPENED the transaction leaves the connection usable (sqlite3's shape).
    with pytest.raises(IntegrityError):
        db.execute("INSERT INTO tx (v) VALUES (?)", ("a",))
    assert db.in_transaction is False and db.execute("SELECT COUNT(*) FROM tx").fetchone()[0] == 1
    # The busy timeout's twin: a writer behind another writer's open transaction fails after lock_timeout
    # with the neutral OperationalError (LockNotAvailable), never hanging.
    db.execute("UPDATE tx SET v = 'b' WHERE v = 'a'")           # open transaction holds the row lock
    import time
    t0 = time.monotonic()
    with pytest.raises(OperationalError) as e:
        other.execute("UPDATE tx SET v = 'c' WHERE v = 'a'")
    waited = time.monotonic() - t0
    assert isinstance(e.value, psycopg.errors.LockNotAvailable) and 4.0 <= waited <= 30.0, waited
    db.rollback()
    assert other.in_transaction is False, "the refused writer's transaction was rolled back (it opened it)"
    other.close()


def test_t6_a_session_below_extra_float_digits_1_is_refused_by_name(pg, monkeypatch):
    from backend.store import BackendUnavailable
    from backend.store import postgres_backend as PB
    _db, target = pg
    monkeypatch.setattr(PB, "EXTRA_FLOAT_DIGITS", 0)
    with pytest.raises(BackendUnavailable, match="extra_float_digits is 0"):
        PB.PostgresDatabase.open_target(target)


def test_p_a_pragma_reaching_the_backend_is_refused_by_name(pg):
    db, _ = pg
    with pytest.raises(DialectError, match="PRAGMA"):
        db.execute("PRAGMA table_info(triples)")


# ---------------------------------------------------------------------------
# S — the skip list is pinned; B — the bundle excludes the driver
# ---------------------------------------------------------------------------

#
# The set skipped or acknowledged under the Postgres fixture is pinned two
# ways (D12, T12). Direction A, static, both runs: every listed module, and
# every module holding a `sqlite_only`-marked test, touches SQLite BY NAME —
# `sqlite3` bound or referenced under any alias, a SQLite-only product name,
# or a string constant that is a SQLite fact — found by AST, so a reason
# alone (prose) lists nothing. Direction B, dynamic, the Postgres run only:
# tests/conftest.py records every SQLite store opened under the fixture
# against the running test and fails the session for one whose module is
# not acknowledged (`OPENS_SQLITE_UNDER_POSTGRES`). The counts are literals
# here, so growth and shrinkage are both acts (the SSE two-event gap's shape).

SQLITE_NAMES = {"sqlite3", "open_read_only", "read_only_uri", "refuse_writes", "_readonly", "store_lock",
                "SQLiteDatabase", "open_path", "set_trace_callback"}
SQLITE_STRINGS = re.compile(r"\.db\b|\bPRAGMA\b|sqlite_master|mode=ro|sqlite", re.I)


def sqlite_touches(path: Path) -> list[str]:
    """How a test module touches SQLite by name, by AST; empty for one that
    touches none."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            found.update(f"import {a.name}" for a in n.names if a.name.split(".")[0] == "sqlite3")
        elif isinstance(n, ast.ImportFrom) and n.module:
            if n.module.split(".")[0] == "sqlite3":
                found.add(f"from {n.module} import …")
            found.update(f"from {n.module} import {a.name}" for a in n.names if a.name in SQLITE_NAMES)
        elif isinstance(n, (ast.Name, ast.Attribute)):
            name = n.id if isinstance(n, ast.Name) else n.attr
            if name in SQLITE_NAMES:
                found.add(name)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and SQLITE_STRINGS.search(n.value):
            found.add(f"string {n.value[:40]!r}")
    return sorted(found)


def marked_sqlite_only_modules() -> dict[str, int]:
    """Tracked test modules holding `@pytest.mark.sqlite_only(...)` tests, with
    the count of marked functions, by AST."""
    out: dict[str, int] = {}
    for p in tracked("tests/test_*.py"):
        tree = ast.parse(Path(p).read_text(encoding="utf-8"))
        n = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in node.decorator_list:
                    target = d.func if isinstance(d, ast.Call) else d
                    if isinstance(target, ast.Attribute) and target.attr == "sqlite_only":
                        n += 1
        if n:
            out[Path(p).resolve().relative_to(REPO).as_posix()] = n
    return out


def test_s_every_listed_or_marked_module_touches_sqlite_by_name():
    """Direction A: a module skipped or acknowledged under the fixture, whole
    or by a marked test, touches SQLite by name."""
    from tests._sqlite_only import OPENS_SQLITE_UNDER_POSTGRES, SQLITE_ONLY_MODULES
    problems = []
    both = set(SQLITE_ONLY_MODULES) & set(OPENS_SQLITE_UNDER_POSTGRES)
    if both:
        problems.append(f"listed as skipped AND as acknowledged: {sorted(both)}")
    for rel, reason in {**SQLITE_ONLY_MODULES, **OPENS_SQLITE_UNDER_POSTGRES}.items():
        path = REPO / rel
        if not path.is_file():
            problems.append(f"{rel}: listed but does not exist")
            continue
        if not reason.strip():
            problems.append(f"{rel}: listed without a reason")
        if not sqlite_touches(path):
            problems.append(f"{rel}: listed but touches SQLite by no name")
    marked = marked_sqlite_only_modules()
    for rel in sorted(marked):
        if not sqlite_touches(REPO / rel):
            problems.append(f"{rel}: carries a sqlite_only mark but touches SQLite by no name")
    assert not problems, "\n".join(problems)
    print(f"S: {len(SQLITE_ONLY_MODULES)} skipped modules, {len(OPENS_SQLITE_UNDER_POSTGRES)} acknowledged, "
          f"{sum(marked.values())} marked tests in {len(marked)} modules; every one touches SQLite by name")


def test_s_the_pinned_counts_are_deliberate_numbers():
    from tests._sqlite_only import OPENS_SQLITE_UNDER_POSTGRES, SQLITE_ONLY_MODULES
    marked = marked_sqlite_only_modules()
    # W10c: (5, 4) -> (7, 4), the interceptor's two test modules (its store is SQLite files under its own root).
    # The public tree: tests/test_bb3_harness.py stays private (W11c), so its module is not counted: (7, 4) -> (6, 4).
    assert (len(SQLITE_ONLY_MODULES), len(OPENS_SQLITE_UNDER_POSTGRES)) == (6, 4), \
        (sorted(SQLITE_ONLY_MODULES), sorted(OPENS_SQLITE_UNDER_POSTGRES))
    # W4 part 2: (18, 48) -> (19, 49), tests/test_w4_identity_record.py R2b — it
    # opens a copy of the committed pre-run SQLite fixture, as
    # tests/test_pre_run_fixture.py does; its Postgres twin is the migration
    # round trip (tests/test_w3b_migration.py T1/T2), which copies that fixture.
    # W6's delta checks: (19, 49) -> (20, 53), tests/test_w6_close_record.py's four
    # D2 tests — they drive scripts/backup.py's copy of the SQLite files; a hosted
    # backup is one pg_dump snapshot, so no sealed act can be missing from it.
    # W8: (20, 53) -> (24, 64): tests/test_w8_proofs.py P6, the same backup on a lineage seal,
    # and the ten W8 tests in four modules that copy the committed pre-run SQLite fixture
    # (their Postgres twin is the migration round trip).
    # W8's first delta check: (24, 64) -> (25, 65), tests/test_w8_rebinding.py R2 (its R3 is the Postgres path).
    # W8's second delta check: (25, 65) -> (25, 67), R7 and R8 on records sealed before W8 (the same module).
    # W8's third delta check: (25, 67) -> (25, 69), R10 on a record sealed before W8 and R12 (five such records).
    # W9 part 2a: (25, 69) -> (26, 71), tests/test_w9_signing.py S2 (the pre-run fixture) and S3 (a file restore).
    # W9 part 2c: (26, 71) -> (27, 73), tests/test_w9_credentials.py C5 (the pre-run fixture) and C7 (a restore).
    # W9's verifier fixes: (27, 73) -> (28, 78), tests/test_w9_verifier_fixes.py F1 and F3 (a file restore), F2, G7
    # and G12 (the pre-run fixture); a restore on Postgres is the hosted dump (BACKLOG W9-B6).
    # W9's delta check 1: (28, 78) -> (29, 81), tests/test_w9_delta_fixes.py D1 (a file restore), E1 and E8 (the
    # pre-run fixture), the same two reasons. W9's delta check 3: (29, 81) -> (30, 82),
    # tests/test_w9_delta2_fixes.py J2b (a file restore). W11c: (30, 82) -> (31, 83), tests/test_tb_golden.py G2b
    # (copies of step 1's committed SQLite stores; W11c's Postgres run found G2 reading the test database there).
    # W11d: (31, 83) -> (32, 84), tests/test_w11d_reference.py E5 (a copy of the same committed store's archive).
    # W11d's delta check 2: (32, 84) -> (32, 85), the same module's E6 (the pre-run fixture: an older store's bound
    # credential, the only place a layout-1 block and its binding exist; its Postgres twin is the migration round trip).
    assert (len(marked), sum(marked.values())) == (32, 85), marked


def test_s_companion_a_a_module_touching_no_sqlite_name_is_named(tmp_path):
    portable = tmp_path / "test_portable.py"
    portable.write_text("def test_x():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    assert sqlite_touches(portable) == []
    by_alias = tmp_path / "test_by_alias.py"
    by_alias.write_text("import sqlite3 as s\n\ndef test_y(tmp_path):\n    s.connect(tmp_path / 'x')\n", encoding="utf-8")
    assert sqlite_touches(by_alias) == ["import sqlite3"]
    by_string = tmp_path / "test_by_string.py"
    by_string.write_text("def test_z(store):\n    store.conn.execute('PRAGMA table_info(triples)')\n", encoding="utf-8")
    assert sqlite_touches(by_string) == ["string 'PRAGMA table_info(triples)'"]
    by_product_name = tmp_path / "test_by_name.py"
    by_product_name.write_text("from scripts._readonly import open_read_only\n", encoding="utf-8")
    assert "from scripts._readonly import open_read_only" in sqlite_touches(by_product_name)


def test_s_companion_b_a_bare_path_open_under_the_fixture_is_recorded_and_an_unacknowledged_one_fails(
        tmp_path, request, monkeypatch):
    """Direction B's two halves, in process: under the fixture a store opened
    by a bare path is recorded against THIS test (this module is acknowledged,
    so the session does not fail for it), and a planted record in a module no
    list acknowledges makes the session-end check raise, naming the test."""
    import tests.conftest as cf
    from backend.store import connect
    if not POSTGRES_TEST:
        pytest.skip("the recorder is wired under the Postgres fixture only (NEXUS_TEST_STORE=postgres)")
    before = cf._SQLITE_OPENS_UNDER_PG.get(request.node.nodeid, 0)
    db = connect(tmp_path / "bare.db")
    assert db.backend == "sqlite"
    db.close()
    assert cf._SQLITE_OPENS_UNDER_PG.get(request.node.nodeid, 0) == before + 1
    assert request.node.nodeid not in cf.sqlite_opens_unacknowledged(), "this module is acknowledged"
    monkeypatch.setitem(cf._SQLITE_OPENS_UNDER_PG, "tests/test_nowhere.py::test_opens_sqlite", 2)
    with pytest.raises(AssertionError, match="tests/test_nowhere.py::test_opens_sqlite \\(opened 2\\)"):
        cf.assert_sqlite_opens_acknowledged()


def test_b_the_bundle_excludes_the_driver():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_bundle", REPO / "scripts" / "build_bundle.py")
    bb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bb)
    args = list(bb.PYINSTALLER_ARGS)
    for mod in ("psycopg", "psycopg_binary", "psycopg_c"):
        assert ["--exclude-module", mod] == args[args.index(mod) - 1:args.index(mod) + 1]
        assert mod in bb.NEVER_FROZEN and "W3b" in bb.NEVER_FROZEN[mod]
    assert bb.never_frozen(["psycopg", "psycopg_binary._psycopg", "backend.store.base", "numpy"]) == \
        ["psycopg", "psycopg_binary._psycopg"]


def test_b_the_interface_runs_without_the_driver(monkeypatch, tmp_path):
    """The bundle's shape: `backend.store` imports the driver inside a
    try/except; without it SQLite opens and a Postgres target is refused by
    name (the module's own flag, read at the call)."""
    import backend.store as store
    from backend.store import BackendUnavailable, StoreTarget
    monkeypatch.setattr(store, "POSTGRES_DRIVER_AVAILABLE", False)
    assert store.connect(tmp_path / "kb.db").backend == "sqlite"
    with pytest.raises(BackendUnavailable, match="psycopg driver"):
        store.connect(StoreTarget.postgres("postgresql://x@127.0.0.1:1/x_test"))


# ---------------------------------------------------------------------------
# L — the connection's life is the object's, as sqlite3's is
# ---------------------------------------------------------------------------

def test_l_a_dropped_database_closes_its_connection_quietly(pg):
    """sqlite3 closes the connection when its object is collected, silently.
    The twin does the same: `PostgresDatabase.__del__` closes the driver
    connection, so the server's slot is freed when the store is dropped
    (the suite drops thousands) and without the ResourceWarning psycopg's
    own dealloc raises on every open connection it frees."""
    import gc
    import time
    import warnings
    from backend.store import connect
    db, target = pg
    other = connect(target)
    pid = other.execute("SELECT pg_backend_pid()").fetchone()[0]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        del other
        gc.collect()
    resource = [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)]
    assert not resource, resource
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if db.execute("SELECT 1 FROM pg_stat_activity WHERE pid = ?", (pid,)).fetchone() is None:
            break
        time.sleep(0.05)
    assert db.execute("SELECT 1 FROM pg_stat_activity WHERE pid = ?", (pid,)).fetchone() is None, \
        f"backend {pid} still holds its slot five seconds after the store object was dropped"


# ---------------------------------------------------------------------------
# A — aggregates: a sum's type is SQLite's, by a cast the dialect requires
# ---------------------------------------------------------------------------
#
# Found by the suite under the fixture: `SUM(bigint)` is `numeric` on Postgres
# and arrives as a Decimal, which no JSON surface can carry and SQLite never
# produces (its SUM over an integer column is an integer). The one site
# (`LineageStore.capture_stats`) casts; the dialect refuses a SUM outside a
# CAST by name, and AVG and TOTAL (no twin: AVG's type differs, TOTAL is
# SQLite's) outright, so the next site fails the suite, not a tester.

def test_a_a_sum_outside_a_cast_is_refused_by_name():
    with pytest.raises(DialectError, match="SUM"):
        translate("SELECT COUNT(*), COALESCE(SUM(raw_bytes), 0) FROM request_bodies", False)
    assert translate("SELECT CAST(COALESCE(SUM(raw_bytes), 0) AS INTEGER) FROM request_bodies", False) == \
        "SELECT CAST(COALESCE(SUM(raw_bytes), 0) AS BIGINT) FROM request_bodies"
    assert translate("SELECT CAST(SUM(w) AS REAL) FROM t", False) == "SELECT CAST(SUM(w) AS DOUBLE PRECISION) FROM t"
    for bad in ("SELECT AVG(w) FROM t", "SELECT TOTAL(w) FROM t", "SELECT CAST(AVG(w) AS REAL) FROM t"):
        with pytest.raises(DialectError):
            translate(bad, False)
    # a `sum` in a name or a literal is not the aggregate
    assert translate("SELECT checksum FROM t WHERE note = 'SUM(x)'", False) == \
        "SELECT checksum FROM t WHERE note = 'SUM(x)'"


def test_a_capture_stats_counts_are_integers_on_postgres(pg):
    """The consequence: the capture summary's byte counts are `int` on both
    backends (pre-fix: Decimal on Postgres, and `/cortex/stats` could not be
    serialised)."""
    from backend.memory.lineage import LineageStore
    _db, target = pg
    ls = LineageStore(target)
    stats = ls.capture_stats()
    ls.close()
    for key in ("request_bodies", "request_bytes_raw", "request_bytes_stored"):
        assert type(stats[key]) is int, (key, type(stats[key]), stats[key])
