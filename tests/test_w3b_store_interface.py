"""W3b part 1 — the store interface over SQLite (`run/specs/W3b.md` S2: D1,
D2, D3, D4, D6, D14; S3: T1, T14, T16).

The interface (`backend/store/`) is the one place SQL meets a driver. Part 1
puts every store class behind it with the SQLite connection exactly as it
was, so the claim this file proves is NEGATIVE as much as positive: the
default backend's behaviour did not move. The CE rig's pins
(`tests/test_mcp2_build.py`) and the LS fixture literal hold that claim at
the record; this file holds it at each site part 1 touched:

G   THE GUARD (D1): in the tracked backend the name `sqlite3` is bound or
    referenced only under `backend/store/` — by AST over imports (any
    alias) and names, never by text, so a docstring mentioning sqlite3 is
    not a hit. Companions plant `import sqlite3 as s; s.connect(p)` (the
    class the worklist names), `from sqlite3 import Row` and a bare name
    in a backend module and are named by file and line; the same plant
    under `backend/store/` is allowed, so the rule is the directory.
I   THE INTERFACE: `connect(path)` is today's connection (check_same_thread
    off, sqlite3.Row, the measured PRAGMA defaults untouched); `columns()`
    is `PRAGMA table_info`'s tuples; `has_table`, `describe`, `changes`; the
    connection-manager protocol commits, rolls back and never closes; the
    trace hook sees every statement once per parameter set; `ConnHandle`
    answers both call shapes; a Postgres target is refused by name until
    part 2 builds it.
S   THE SUMS (D6, T14): `adjustments`, `adjustment_for` and
    `cumulative_movement` moved from SQL `SUM` to Python's `math.fsum`. On
    SQLite 3.45 the two are equal on random deltas (the measured fact the
    decision rests on), so the SQLite path did not move; the test asserts
    exactly that equality, which is also what Postgres must reproduce.
O   THE ORDERS (D3, D14, T16): the `ORDER BY` clauses added to the graph
    load, `get_relevant(concepts=[])`, the trail channel's sibling query
    and the semantic candidates return the rows SQLite's bare queries
    returned, in the same order, on planted ties — the scan order spelled
    out, not a new order.
J   THE PORTABLE REWRITES (D4): the recall index's backfill and the recall
    rows, now read in Python, equal what the pre-W3b `json_each` SQL (run
    here verbatim, SQLite has it) returns on the same store; `admit`'s
    count is the statement's own.

Law 16: the pre-fix run of this file against `f9bbec6` (the tree before part
1) is printed in part 1's commit body: G fails on its consequence (the six
connect sites named), and every test that imports `backend.store` fails on
the import, declared as such.
"""
from __future__ import annotations

import ast
import json
import math
import random
import sqlite3
import time
from pathlib import Path

import pytest

from tests._tracked import tracked_backend

REPO = Path(__file__).resolve().parent.parent
INTERFACE_DIR = Path("backend") / "store"


# ---------------------------------------------------------------------------
# G — the guard
# ---------------------------------------------------------------------------

def sqlite3_references(source: "str | ast.AST") -> list[tuple[int, str]]:
    """Every binding or use of the name `sqlite3` in `source`, by AST:
    `import sqlite3 [as x]`, `import sqlite3.dbapi2`, `from sqlite3 import
    …`, and any `sqlite3` Name node (an attribute chain's root, a bare
    reference). Comments and docstrings are not hits."""
    tree = source if isinstance(source, ast.AST) else ast.parse(source)
    hits: list[tuple[int, str]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "sqlite3" or a.name.startswith("sqlite3."):
                    hits.append((n.lineno, f"import {a.name}" + (f" as {a.asname}" if a.asname else "")))
        elif isinstance(n, ast.ImportFrom):
            if (n.module or "").split(".")[0] == "sqlite3":
                hits.append((n.lineno, f"from {n.module} import {', '.join(a.name for a in n.names)}"))
        elif isinstance(n, ast.Name) and n.id == "sqlite3":
            hits.append((n.lineno, "the name sqlite3"))
    return sorted(set(hits))


def allowed(rel: Path) -> bool:
    return INTERFACE_DIR in Path(rel).parents


def guard_problems(sources: dict[str, str]) -> list[str]:
    """`sources`: repo-relative path -> source text. One line per hit outside
    the interface package."""
    problems = []
    for rel, text in sorted(sources.items()):
        if allowed(Path(rel)):
            continue
        for line, what in sqlite3_references(text):
            problems.append(f"{rel}:{line}: {what} — the driver is named outside backend/store/ "
                            f"(W3b D1: the dialect is confined to the interface)")
    return problems


def _backend_sources() -> dict[str, str]:
    out = {}
    for p in tracked_backend():
        p = Path(p)
        rel = p.relative_to(REPO) if p.is_absolute() else p
        out[rel.as_posix()] = (REPO / rel).read_text(encoding="utf-8")
    return out


def test_g_sqlite3_is_named_only_inside_the_interface():
    sources = _backend_sources()
    inside = [rel for rel in sources if allowed(Path(rel)) and sqlite3_references(sources[rel])]
    print(f"G population: {len(sources)} tracked backend files; sqlite3 referenced inside the "
          f"interface by {sorted(inside)}")
    # The consequence first (law 16: on the pre-W3b tree this names the ten
    # modules that bound or used the driver), then non-vacuity.
    assert guard_problems(sources) == []
    assert inside, "non-vacuous: the interface itself names the driver"


PLANTS = [
    ("backend/kb/planted.py", "import sqlite3 as s\nconn = s.connect(p)\n", "import sqlite3 as s"),
    ("backend/memory/planted.py", "from sqlite3 import Row\n", "from sqlite3 import Row"),
    ("backend/planted.py", "def f(p):\n    return sqlite3.connect(p)\n", "the name sqlite3"),
    ("backend/kb/planted2.py", "import sqlite3.dbapi2\n", "import sqlite3.dbapi2"),
]


@pytest.mark.parametrize("rel,text,named", PLANTS)
def test_g_companions_a_planted_driver_reference_is_named(rel, text, named):
    problems = guard_problems({rel: text})
    assert len(problems) == 1, problems
    assert problems[0].startswith(f"{rel}:") and named in problems[0]


def test_g_companion_the_rule_is_the_directory():
    """The same plant under the interface package is allowed, so what the
    guard checks is WHERE the driver is named, not whether."""
    assert guard_problems({"backend/store/planted.py": "import sqlite3 as s\nconn = s.connect(p)\n"}) == []


def test_g_a_docstring_or_comment_is_not_a_hit():
    assert sqlite3_references('"""uses sqlite3.Row"""\n# import sqlite3\nx = 1\n') == []


# ---------------------------------------------------------------------------
# I — the interface over SQLite
# ---------------------------------------------------------------------------

def _tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "store" / "kb.db"


def test_i_connect_is_todays_connection(tmp_path):
    from backend.store import SQLiteDatabase, StoreTarget, connect
    db = connect(_tmp_db(tmp_path))
    assert isinstance(db, SQLiteDatabase) and db.backend == "sqlite"
    assert db.describe() == str(_tmp_db(tmp_path))
    raw = db.raw
    assert isinstance(raw, sqlite3.Connection) and raw.row_factory is sqlite3.Row
    # The measured defaults, untouched (agent A §3.2): no PRAGMA is set.
    assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert raw.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    assert raw.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert raw.isolation_level == ""
    # A target is what a path becomes.
    assert StoreTarget.of(_tmp_db(tmp_path)) == StoreTarget.sqlite(_tmp_db(tmp_path))
    assert connect(StoreTarget.of(_tmp_db(tmp_path))).describe() == db.describe()


def test_i_columns_are_table_infos_tuples_and_has_table_answers(tmp_path):
    from backend.kb.schema import connect as schema_connect
    db = schema_connect(_tmp_db(tmp_path))
    pragma = [tuple(r) for r in db.raw.execute("PRAGMA table_info(triples)")]
    assert [tuple(c) for c in db.columns("triples")] == pragma and len(pragma) == 14
    assert db.columns("triples")[0].name == "id" and db.columns("triples")[0].pk == 1
    assert db.has_table("triples") and db.has_table("archival_events") and not db.has_table("nope")
    with pytest.raises(ValueError):
        db.columns("triples; DROP TABLE triples")
    assert db.columns("no_such_table") == []


def test_i_the_connection_manager_commits_rolls_back_and_never_closes(tmp_path):
    from backend.store import connect
    path = _tmp_db(tmp_path)
    db = connect(path)
    db.execute("CREATE TABLE t (v TEXT)")
    db.commit()
    other = sqlite3.connect(path)
    with db:
        db.execute("INSERT INTO t (v) VALUES ('kept')")
    assert other.execute("SELECT v FROM t").fetchall() == [("kept",)], "committed on a clean exit"
    with pytest.raises(RuntimeError):
        with db:
            db.execute("INSERT INTO t (v) VALUES ('lost')")
            raise RuntimeError("inside")
    assert other.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1, "rolled back on an exception"
    assert db.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1, "and still open (psycopg's `with conn:` closes)"
    assert db.in_transaction is False
    assert db.changes() == db.raw.total_changes


def test_i_conn_handle_answers_both_shapes(tmp_path):
    from backend.store import ConnHandle, connect
    db = connect(_tmp_db(tmp_path))
    handle = ConnHandle(db)
    handle.execute("CREATE TABLE t (v TEXT)")
    assert handle() is db
    with handle() as c:
        assert c is db
        c.execute("INSERT INTO t (v) VALUES ('x')")
    assert handle.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    assert handle._conn is db           # the attribute two stores and the establish read


def test_i_the_trace_hook_sees_every_statement_once_per_parameter_set(tmp_path):
    from backend.store import add_trace, connect, remove_trace, tracing
    db = connect(_tmp_db(tmp_path))
    db.execute("CREATE TABLE t (v TEXT)")
    seen: list[str] = []
    assert tracing() is False
    add_trace(seen.append)
    try:
        assert tracing() is True
        db.execute("INSERT INTO t (v) VALUES (?)", ("a",))
        db.executemany("INSERT INTO t (v) VALUES (?)", (("b",), ("c",), ("d",)))
        db.executescript("CREATE TABLE u (w TEXT); INSERT INTO u (w) VALUES ('e');")
        db.execute("SELECT * FROM t")
    finally:
        remove_trace(seen.append)
    assert seen.count("INSERT INTO t (v) VALUES (?)") == 4, seen
    assert sum(1 for s in seen if s.startswith("CREATE TABLE u")) == 1
    assert seen[-1] == "SELECT * FROM t"
    assert tracing() is False
    db.execute("INSERT INTO t (v) VALUES ('f')")
    assert len(seen) == 6, "nothing is reported after the hook is removed"


def test_i_a_postgres_target_opens_through_the_driver_and_a_bad_one_is_named():
    """Part 2: a Postgres target connects through the driver; a server that
    does not answer is the neutral OperationalError (the driver's own), and
    a target that names no backend or no URL is refused before any connect."""
    from backend.store import OperationalError, StoreTarget, connect
    with pytest.raises(OperationalError):
        connect(StoreTarget.postgres("postgresql://nexus@127.0.0.1:1/nexus_nowhere?connect_timeout=2"))
    with pytest.raises(ValueError, match="unknown store backend"):
        StoreTarget(backend="oracle", path=Path("x"))
    with pytest.raises(ValueError, match="needs a database URL"):
        StoreTarget(backend="postgres")


def test_i_is_row_and_the_neutral_exceptions(tmp_path):
    from backend.store import Error, IntegrityError, OperationalError, connect, is_row
    db = connect(_tmp_db(tmp_path))
    db.execute("CREATE TABLE t (v TEXT PRIMARY KEY)")
    db.execute("INSERT INTO t (v) VALUES ('a')")
    row = db.execute("SELECT * FROM t").fetchone()
    assert is_row(row) and not is_row(("a",)) and not is_row({"v": "a"})
    with pytest.raises(IntegrityError):
        db.execute("INSERT INTO t (v) VALUES ('a')")
    with pytest.raises(OperationalError):
        db.execute("SELECT * FROM no_such_table")
    assert issubclass(sqlite3.IntegrityError, Error)


def test_i_a_raw_connection_is_wrapped_for_the_migration_and_nothing_else_is(tmp_path):
    """The answered-for raw opens (a script's temp copy, a kill-switch test's
    scratch store) hand `schema.migrate()` a bare sqlite3 connection; the
    interface wraps it, names its file, and refuses any other object."""
    from backend.kb.schema import SCHEMA_SQL, migrate
    from backend.store import SQLiteDatabase, as_database
    path = _tmp_db(tmp_path)
    path.parent.mkdir(parents=True)
    raw = sqlite3.connect(path)
    raw.executescript(SCHEMA_SQL)
    with pytest.raises(TypeError, match="row_factory = sqlite3.Row"):
        as_database(raw)                      # rows must read by name, as the callers' do
    raw.row_factory = sqlite3.Row
    wrapped = as_database(raw)
    assert isinstance(wrapped, SQLiteDatabase) and wrapped.raw is raw and wrapped.describe() == str(path)
    assert as_database(wrapped) is wrapped
    assert migrate(raw) == {"columns_added": [], "legacy_tier4_migrated": 0}
    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    assert as_database(mem).describe() == ":memory:"
    with pytest.raises(TypeError, match="not a store connection"):
        as_database(object())


def test_i_every_store_class_opens_through_the_interface(tmp_path):
    """The six classes that opened their own connection now hold a
    `Database`; the two that ride another's connection receive one."""
    from backend.kb.recalibration import RecalibrationStore
    from backend.kb.tiered_store import TripleStore
    from backend.kb.trails import TrailStore
    from backend.kb.work_units import WorkUnitStore
    from backend.memory.chain import NexusArchiveChain
    from backend.memory.graph_store import HebbianGraphStore
    from backend.memory.lineage import LineageStore
    from backend.mcp.coverage import CoverageStore
    from backend.store import Database, StoreTarget
    kb, archive = tmp_path / "kb.db", tmp_path / "archive.db"
    store = TripleStore(kb)
    opened = [store.conn(), LineageStore(kb).conn, RecalibrationStore(kb).conn, TrailStore(kb).conn,
              HebbianGraphStore(kb).conn, NexusArchiveChain(archive).conn]
    assert all(isinstance(c, Database) for c in opened)
    assert len({c.backend for c in opened}) == 1, "every class opened the same backend"
    WorkUnitStore(store.conn)
    CoverageStore(LineageStore(kb).conn)
    if store.target.backend == "sqlite":
        assert store.target == StoreTarget.sqlite(kb) and store.db_path == kb
        assert LineageStore(StoreTarget.sqlite(kb)).conn.describe() == str(kb), "a target opens as a path does"
    else:                                   # the Postgres fixture mapped the path to a schema (D7)
        assert store.db_path is None and store.target.schema and opened[0].describe().startswith("postgres ")
        assert LineageStore(kb).conn.describe() == opened[0].describe(), "the same path, the same schema"


# ---------------------------------------------------------------------------
# S — the sums (D6, T14)
# ---------------------------------------------------------------------------

def _random_doubles(rng: random.Random, n: int) -> list[float]:
    # Deltas of the sizes an envelope allows, with a long tail and both
    # signs, so the accumulation order can matter: naive sums differ here.
    out = []
    for _ in range(n):
        mag = 10.0 ** rng.uniform(-9, 0)
        out.append(rng.choice((-1.0, 1.0)) * mag * rng.random())
    return out


@pytest.mark.sqlite_only("SQLite's exactly rounded SUM is the oracle; Postgres's sum is the naive one D6 removes")
def test_s_the_python_sums_equal_sqlites_sum_on_random_deltas(tmp_path, monkeypatch):
    from backend.kb.recalibration import RecalibrationStore
    monkeypatch.setenv("NEXUS_RECALIBRATION", "on")
    rs = RecalibrationStore(tmp_path / "kb.db")
    rng = random.Random(20261109)
    expected: dict[int, list[float]] = {}
    rows = []
    for bid in range(1, 6):
        deltas = _random_doubles(rng, 400)
        expected[bid] = deltas
        for i, d in enumerate(deltas):
            rows.append((f"e{bid}-{i}", 1_700_000_000.0 + i, bid, d, 0.5, 0.5 + d, 0.5, "v0", "operator",
                         "local_operator", "local", "", 1, "", "", "h"))
    rs.conn.executemany(
        "INSERT INTO recalibration_events (event_id, ts, belief_id, delta, prominence_before, "
        "prominence_after, stored_weight, envelope_version, actor_id, actor_role, actor_source, "
        "outcome_ref, applied, refusal_rule, refusal_detail, record_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    rs.conn.commit()
    # SQLite's own SUM (the pre-W3b query), the Python sum, and fsum: all three equal.
    sql = {int(r["belief_id"]): float(r["total"]) for r in rs.conn.execute(
        "SELECT belief_id, SUM(delta) AS total FROM recalibration_events WHERE applied = 1 GROUP BY belief_id")}
    got = rs.adjustments()
    assert set(got) == set(sql) == set(expected)
    for bid, deltas in expected.items():
        assert got[bid] == math.fsum(deltas) == sql[bid], (bid, got[bid], sql[bid], math.fsum(deltas))
        assert rs.adjustment_for(bid) == sql[bid]
        sql_abs = rs.conn.execute("SELECT SUM(ABS(delta)) AS t FROM recalibration_events "
                                  "WHERE belief_id = ? AND applied = 1", (bid,)).fetchone()["t"]
        assert rs.cumulative_movement(bid) == float(sql_abs) == math.fsum(abs(d) for d in deltas)
        naive = 0.0
        for d in deltas:
            naive += d
        # Not asserted equal: the naive scan-order sum is what Postgres's
        # sum(double precision) computes, and it differs from fsum here.
        print(f"belief {bid}: fsum {got[bid]!r}, naive {naive!r}, equal={naive == got[bid]}")
    assert rs.adjustment_for(999) == 0.0 and rs.cumulative_movement(999) == 0.0


def test_s_companion_a_naive_sum_differs_from_fsum_on_these_deltas():
    """The decision's premise, shown: on deltas of mixed magnitude the naive
    accumulation differs from the exactly rounded sum (and from itself in
    reverse order), so a backend that summed in scan order would not match."""
    rng = random.Random(7)
    deltas = _random_doubles(rng, 400)
    forward = 0.0
    for d in deltas:
        forward += d
    backward = 0.0
    for d in reversed(deltas):
        backward += d
    exact = math.fsum(deltas)
    assert forward != exact or backward != exact, "these deltas did not exercise the rounding"
    assert forward != backward or forward != exact


# ---------------------------------------------------------------------------
# O — the orders spelled out (D3, D14, T16)
# ---------------------------------------------------------------------------

def _claim_store(tmp_path):
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    store = TripleStore(tmp_path / "kb.db")
    # Ties on (tier, weight): the same weight and tier, distinct objects, in
    # a deliberate non-alphabetical order so scan order is visible.
    for obj in ("zeta outcome", "alpha outcome", "mid outcome", "beta outcome"):
        store.add(subject="interval training", relation="improves", object=obj,
                  weight=0.7, tier=1, granularity=Granularity.TOPIC, source="asserted")
    return store


def test_o_get_relevant_without_concepts_returns_sqlites_tie_order(tmp_path):
    store = _claim_store(tmp_path)
    bare = [r["id"] for r in store.conn.execute(
        "SELECT * FROM triples WHERE archived = 0 ORDER BY tier ASC, weight DESC LIMIT ?", (3,))]
    got = [t["id"] for t in store.get_relevant(concepts=[], top_k=3)]
    assert got == bare and len(got) == 3
    assert got == sorted(got), "SQLite's tie order was the rowid order; `id ASC` says so"


@pytest.mark.sqlite_only("the bare query's order is SQLite's UNIQUE-index order, which the clause spells out")
def test_o_the_trail_siblings_return_the_bare_querys_order(tmp_path):
    store = _claim_store(tmp_path)
    bare = [r["id"] for r in store.conn.execute(
        "SELECT * FROM triples WHERE subject = ? AND relation = ? AND archived = 0",
        ("interval training", "improves"))]
    got = [t.id for t in store.search_subject_relation("interval training", "improves")]
    by_object = [r["id"] for r in store.conn.execute(
        "SELECT id FROM triples ORDER BY object ASC, source ASC, id ASC")]
    # SQLite answered the bare query through the UNIQUE (subject, relation,
    # object, source) index, so the siblings came back in OBJECT order, not
    # rowid order (measured here: [2, 4, 3, 1] for objects zeta, alpha, mid,
    # beta); the clause spells that order out.
    assert got == bare == by_object and len(got) == 4 and bare != sorted(bare)


def test_o_the_graph_reloads_in_save_order(tmp_path):
    """The read the establish found ordering the prompt without saying so
    (H2): `load_into` consumes rows in save order, kept across an upsert
    rewrite; `ORDER BY rowid` names it and changes nothing on SQLite."""
    from backend.cortex.edges import SynapticEdge
    from backend.memory.graph_store import HebbianGraphStore
    from backend.memory.hippocampus import Hippocampus
    gs = HebbianGraphStore(tmp_path / "kb.db")
    h = Hippocampus()
    for a, b in (("zeta", "alpha"), ("mid", "beta"), ("alpha", "zeta"), ("beta", "alpha"), ("alpha", "beta")):
        h.add_edge(SynapticEdge(source=a, target=b, weight=0.5))
    gs.save(h)
    gs.save(h)                                   # a full upsert rewrite keeps every rowid
    bare = [(r["source"], r["target"]) for r in gs.conn.execute("SELECT * FROM hebbian_edges")]
    ordered = [(r["source"], r["target"]) for r in gs.conn.execute("SELECT * FROM hebbian_edges ORDER BY rowid")]
    assert ordered == bare and len(bare) == 5
    assert ordered != sorted(ordered), "the key order is a different order (measured): the rowid is the rule"
    fresh = Hippocampus()
    gs.load_into(fresh)
    # What the load order decides is each node's ADJACENCY order (the
    # observable the establish measured: `outgoing('alpha')` is the save
    # order, `['zeta', 'beta']`, not the key order `['beta', 'zeta']`).
    for node in ("zeta", "mid", "alpha", "beta"):
        expected = [t for s, t in ordered if s == node]
        assert [t for t, _edge in fresh.outgoing(node)] == expected, node
    assert [t for t, _edge in fresh.outgoing("alpha")] == ["zeta", "beta"]


def test_o_the_semantic_candidates_return_the_bare_joins_order(tmp_path, monkeypatch):
    from backend.kb.semantic_index import SemanticIndex
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "1")
    store = _claim_store(tmp_path)
    index = SemanticIndex(store)
    ids = [r["id"] for r in store.conn.execute("SELECT id FROM triples ORDER BY id DESC")]
    for i, tid in enumerate(ids):                # indexed in reverse id order on purpose
        index.upsert(tid, index.surface_for(tid), [1.0 if j == i else 0.0 for j in range(index.dim)],
                     model="toy")
    clause, params = index._exclusion_clause("t")
    bare = [r["id"] for r in store.conn.execute(
        "SELECT e.triple_id AS id, e.vec AS vec FROM triple_embeddings e JOIN triples t ON t.id = e.triple_id "
        "WHERE t.archived = 0 AND e.dim = ?" + clause, (index.dim, *params))]
    ordered = [r["id"] for r in store.conn.execute(
        "SELECT e.triple_id AS id, e.vec AS vec FROM triple_embeddings e JOIN triples t ON t.id = e.triple_id "
        "WHERE t.archived = 0 AND e.dim = ?" + clause + " ORDER BY t.id", (index.dim, *params))]
    assert ordered == bare == sorted(ids) and len(ids) == 4


# ---------------------------------------------------------------------------
# J — the portable rewrites (D4)
# ---------------------------------------------------------------------------

def _lineage_with_records(tmp_path):
    from backend.memory.lineage import RECALL_INDEX_TABLE, LineageStore
    ls = LineageStore(tmp_path / "kb.db")
    injected = {
        "r1": [{"belief_id": 1, "weight_at_injection": 0.5, "tier_at_injection": 1, "cited": True},
               {"belief_id": 2, "weight_at_injection": 0.25, "tier_at_injection": 2, "cited": False}],
        "r2": [{"belief_id": 2, "weight_at_injection": 0.75, "tier_at_injection": 1, "cited": True}],
        "r3": [{"belief_id": 1, "weight_at_injection": 0.125, "tier_at_injection": 3}],
    }
    for i, (lid, items) in enumerate(injected.items()):
        ls.conn.execute(
            "INSERT INTO lineage_records (lineage_id, ts, query_hash, injected_json, contradictions_json, "
            "model_id, prompt_prefix_hash, response_hash, record_hash, scope) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (lid, 1_700_000_000.0 + i, "q", json.dumps(items, sort_keys=True), "[]", "stub:primary", "p", "r", "h",
             "default"))
    ls.conn.commit()
    return ls, RECALL_INDEX_TABLE, injected


@pytest.mark.sqlite_only("SQLite's json_each is the oracle the Python rewrite is held to")
def test_j_the_backfill_in_python_equals_the_json_each_sql(tmp_path):
    ls, table, injected = _lineage_with_records(tmp_path)
    assert ls.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    # What the pre-W3b statement would insert, computed by SQLite's own json_each.
    expected = sorted(tuple(r) for r in ls.conn.execute(
        f"SELECT CAST(json_extract(j.value, '$.belief_id') AS INTEGER), r.lineage_id "
        f"FROM lineage_records r, json_each(r.injected_json) j "
        f"WHERE json_extract(j.value, '$.belief_id') IS NOT NULL "
        f"AND r.lineage_id NOT IN (SELECT lineage_id FROM {table})"))
    assert ls._index_backfill() == len(expected) == 4
    got = sorted(tuple(r) for r in ls.conn.execute(f"SELECT belief_id, lineage_id FROM {table}"))
    assert got == expected
    assert ls._index_backfill() == 0, "a second pass finds nothing (and reports 0, not a stale delta)"


@pytest.mark.sqlite_only("SQLite's json_each is the oracle the Python rewrite is held to")
def test_j_the_recall_rows_in_python_equal_the_json_each_sql(tmp_path):
    ls, table, injected = _lineage_with_records(tmp_path)
    ls._index_backfill()
    for bid in (1, 2, 3):
        old = ls.conn.execute(
            f"SELECT r.lineage_id, r.ts, r.work_unit_id, r.accountable_identity, r.scope, "
            f"r.response_hash, r.sealed_block, "
            f"json_extract(j.value, '$.weight_at_injection') AS weight_at_injection, "
            f"json_extract(j.value, '$.cited') AS cited, "
            f"json_extract(j.value, '$.tier_at_injection') AS tier_at_injection "
            f"FROM {table} i JOIN lineage_records r ON r.lineage_id = i.lineage_id, "
            f"json_each(r.injected_json) j "
            f"WHERE i.belief_id = ? AND json_extract(j.value, '$.belief_id') = ? "
            f"ORDER BY r.ts ASC, r.lineage_id ASC LIMIT ? OFFSET ?", (bid, bid, 100, 0)).fetchall()
        rows, total = ls.records_injecting(bid)
        assert total == len(old) == len(rows)
        for new, o in zip(rows, old):
            assert new["lineage_id"] == o["lineage_id"] and new["ts"] == o["ts"]
            assert new["weight_at_injection"] == o["weight_at_injection"]
            assert new["tier_at_injection"] == o["tier_at_injection"]
            assert new["cited"] == (None if o["cited"] is None else bool(o["cited"]))
            assert isinstance(new["weight_at_injection"], float) and isinstance(new["tier_at_injection"], int)
    assert ls.records_injecting(1, limit=1, offset=1)[0][0]["lineage_id"] == "r3"
    assert ls.records_injecting(3) == ([], 0)


def test_j_admits_count_is_the_statements_own(tmp_path):
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    from backend.kb.work_units import WorkUnitStore
    store = TripleStore(tmp_path / "kb.db")
    ids = [store.add(subject="interval training", relation="improves", object=f"outcome {i}",
                     weight=0.6, tier=1, granularity=Granularity.TOPIC, source="asserted") for i in range(3)]
    units = WorkUnitStore(store.conn)
    units.conn.execute("INSERT INTO work_unit (work_unit_id, work_unit_type, opened_at, opened_by, "
                       "attribution_status) VALUES ('u1', 'claim', ?, 'operator', 'attributed')", (time.time(),))
    units.conn.commit()
    assert units.admit(ids, "u1", turn=1) == 3
    assert units.admit(ids, "u1", turn=2) == 0, "the same claims again: nothing inserted, nothing counted"
    assert units.admit(ids[:1] + [None], "u1", turn=3) == 0


# ---------------------------------------------------------------------------
# R — a test's services are released when the test ends (W3b part 2)
# ---------------------------------------------------------------------------
#
# Found under the Postgres fixture: 98 open store connections after sixteen
# tests, and a server with the default hundred slots refusing connections
# before a quarter of the suite had run. Every `create_app` closes its route
# handlers over the `NexusServices` it builds; FastAPI (0.143 here) keeps a
# classification of every handler in module-level lru_caches keyed by the
# callable's identity, 4096 entries, so the handlers, their closures and the
# seven stores behind them lived for the process. On SQLite the same leak
# held seven descriptors per app under a limit of 20,000 on this host, which
# is why nothing saw it. The conftest fixture `_release_fastapi_callable_caches`
# clears the caches after every test; r1 builds two apps the way the suite
# does and keeps weak references, r2 (run after r1's teardown) finds the
# first app's dead. The second app's may live: `agentic_loop.set_event_recorder`
# holds the LAST `create_services`'s alert closure, one set, by design
# (production has one app), so the claim is "every app but the last". On
# both backends: the holders are the same.

_RELEASE_PROBE: dict = {}


def test_r1_two_apps_built_here_are_kept_only_by_weak_references():
    import weakref
    from backend.server import create_app
    first, last = create_app(), create_app()
    for name, app in (("first", first), ("last", last)):
        services = app.state.services
        store_db = services.store.conn._conn
        _RELEASE_PROBE[name] = {"services": weakref.ref(services), "store_db": weakref.ref(store_db)}
        _RELEASE_PROBE["backend"] = store_db.backend
    assert _RELEASE_PROBE["first"]["services"]() is first.state.services


def test_r2_every_app_but_the_last_is_gone_after_the_previous_tests_teardown():
    import gc
    assert "first" in _RELEASE_PROBE, "r1 runs first (file order)"
    gc.collect()
    alive = {k: r() for k, r in _RELEASE_PROBE["first"].items() if r() is not None}
    assert not alive, (f"the first app's {sorted(alive)} still alive after r1's teardown on the "
                       f"{_RELEASE_PROBE['backend']} backend: a process-lifetime holder keeps the app's handlers "
                       f"(FastAPI's callable caches, or a new one), and with them the seven stores and their "
                       f"connections")
    held = sorted(k for k, r in _RELEASE_PROBE["last"].items() if r() is not None)
    print(f"R: the first app released on {_RELEASE_PROBE['backend']}; the last app's {held or 'nothing'} "
          f"still held (the event recorder's one set)")


# ---------------------------------------------------------------------------
# T — tables(): the store's tables by name, the twin of a sqlite_master read
# ---------------------------------------------------------------------------

def test_t_tables_lists_the_stores_tables_without_the_drivers_own(tmp_path):
    """`tables()` is `SELECT name FROM sqlite_master WHERE type='table'` minus
    SQLite's own (`sqlite_sequence` appears once an AUTOINCREMENT table has a
    row), sorted; a test that used to read sqlite_master reads this on both
    backends (as_of's T9, the just-culture population, the semantic index's
    absence)."""
    from backend.store import connect
    db = connect(tmp_path / "kb.db")
    assert db.tables() == []
    db.executescript("CREATE TABLE b (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT); CREATE TABLE a (x REAL);")
    db.execute("INSERT INTO b (v) VALUES ('one')")
    db.commit()
    raw = sorted(r[0] for r in db.raw.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    assert raw == ["a", "b", "sqlite_sequence"], raw
    assert db.tables() == ["a", "b"]
    assert db.has_table("sqlite_sequence") and "sqlite_sequence" not in db.tables()
