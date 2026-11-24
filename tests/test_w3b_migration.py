"""W3b part 4 — migration both ways (run/specs/W3b.md D10; S3: T10; P6's
continuation). The two scripts run as the operator runs them, as processes:
`scripts/migrate_sqlite_to_postgres.py` from a copy of the committed pre-run
fixture (`tests/fixtures/pre_run_data/`, a store written before W0b) into a
fresh schema, and `scripts/export_postgres_to_sqlite.py` back out of it,
read through a role that cannot write. Each takes the URL the server would
be given as `NEXUS_DATABASE_URL`, the schema named in its `options`.

T1  THE MIGRATION, dry then confirmed: the dry run writes nothing (the
    target schema holds no NEXUS table); the confirmed run proves itself (the
    chain verifies with the source's head, every record recomputes with the
    source's hashes, the graph's rows load in order, the as-of pages and the
    sequences are equal); the source's bytes are unchanged; and this test
    re-reads the target through the product's classes: every record intact,
    the chain valid.
T2  THE ROUND TRIP: exported back to SQLite through a SELECT-only role, the
    store equals the source as the product reads it (the source migrated in
    a copy): every table's rows, the sequences, the product's verifiers.
T3  THE CONTINUATION (P6): the next claim added to the migrated store gets
    the id the source would have given it. The sequence is carried, not
    re-derived from the largest id: an upsert hit moved it past.
T4  THE REFUSALS, each having written nothing: a target holding NEXUS rows
    (a second migration into the same schema); a URL whose schema does not
    exist; an export into a folder holding a store; an export through a role
    that could write.
T5  THE LOCK: a NEXUS process holding the target's lock refuses the
    confirmed migration with exit 7, and the target holds no NEXUS table.
T6  COMPANION: a copy that leaves out one row (planted in the row reader,
    which the count check does not name) is named with the table and both
    counts and rolled back: the target holds nothing.
T7  COMPANION: a copy whose counts all agree but whose one value changed on
    the way commits, and the per-table row digest names the table.
T8  THE PROOF IS INSIDE THE TRANSACTION: the same changed value through the
    script (run in this process, the row reader planted) exits 6 naming the
    table, the target holds none of the source's rows, and a second run,
    unplanted, is not refused and proves.

Every test skips by name without `NEXUS_TEST_DATABASE_URL`.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
from tests import _proc
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from tests.conftest import POSTGRES_TEST_URL as PG_URL  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "pre_run_data"
MIGRATE = REPO / "scripts" / "migrate_sqlite_to_postgres.py"
EXPORT = REPO / "scripts" / "export_postgres_to_sqlite.py"
NO_PG = "no Postgres named: NEXUS_TEST_DATABASE_URL is unset (the Postgres job sets it; windows and macos run SQLite)"
RO_PASSWORD = "Pw" + hashlib.sha1(b"w3b part 4 export role").hexdigest()[:20]


def _url(schema: str, role: "str | None" = None, password: "str | None" = None) -> str:
    """The test URL (as `role` when given), the session's search_path set to
    `schema` through libpq's `options`: the form NEXUS_DATABASE_URL takes."""
    parts = urlsplit(PG_URL)
    host = parts.hostname or ""
    host = f"[{host}]" if ":" in host else host
    user = role or parts.username or ""
    secret = password if role else parts.password
    netloc = (f"{user}:{secret}@" if secret else (f"{user}@" if user else "")) + host + \
        (f":{parts.port}" if parts.port else "")
    query = (parts.query + "&" if parts.query else "") + f"options=-c%20search_path%3D{schema}"
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


@pytest.fixture
def pg(tmp_path):
    """A fresh schema (created: a URL names a schema, it does not make one),
    and on request a SELECT-only role on it (`grant_reader`, after the
    migration made the tables); both dropped after."""
    if not PG_URL:
        pytest.skip(NO_PG)
    import psycopg
    from psycopg import sql
    h = hashlib.sha1(str(tmp_path).encode()).hexdigest()[:16]
    schema, role = f"m_{h}", f"mro_{h}"
    state = {"role": None}
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    def grant_reader() -> str:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role),
                                                                       sql.Literal(RO_PASSWORD)))
            state["role"] = role
            c.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
            c.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(sql.Identifier(schema),
                                                                                     sql.Identifier(role)))
        return _url(schema, role, RO_PASSWORD)

    try:
        yield {"url": _url(schema), "schema": schema, "grant_reader": grant_reader}
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as c:
            if state["role"]:
                c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s", (role,))
                c.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(role)))
                c.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _run(script: Path, args: list[str], tmp_path: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("NEXUS_STORE", "NEXUS_DATABASE_URL", "NEXUS_DATA_ROOT")}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", NEXUS_ENV_ROOT=str(tmp_path),
               PYTHONPATH=os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")])))
    return _proc.run([sys.executable, str(script), *args], cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "source"
    shutil.copytree(FIXTURE, src)
    return src


def _target(pg):
    from backend.store import StoreTarget
    return StoreTarget.postgres(pg["url"])


def _migrate(tmp_path, pg, *extra, url=None):
    return _run(MIGRATE, ["--data-root", str(tmp_path / "source"), "--to", url or pg["url"], *extra], tmp_path)


def _nexus_tables(pg) -> list:
    from backend.store import connect
    probe = connect(_target(pg), read_only=True)
    try:
        return probe.tables()
    finally:
        probe.close()


def test_t1_a_dry_run_writes_nothing_and_the_confirmed_run_proves_itself(tmp_path, pg):
    from backend.memory.chain import NexusArchiveChain
    from backend.memory.lineage import LineageStore
    src = _source(tmp_path)
    md5_before = {n: hashlib.md5((src / n).read_bytes()).hexdigest() for n in ("kb.db", "archive.db")}
    dry = _migrate(tmp_path, pg)
    lines = dry.stdout.splitlines()
    print(f"T1 dry run: exit {dry.returncode}; {[l for l in lines if l.startswith('plan')]}")
    assert dry.returncode == 0 and "dry run: nothing was written" in dry.stdout, dry.stdout + dry.stderr
    assert lines[0].startswith(f"source: {src.resolve().as_posix()}/kb.db"), lines[0]
    assert _nexus_tables(pg) == [], "the dry run created tables"
    done = _migrate(tmp_path, pg, "--confirm")
    print(f"T1 confirmed: exit {done.returncode}; "
          f"{[l for l in done.stdout.splitlines() if l.startswith(('copied', 'proven'))]}")
    assert done.returncode == 0 and "proven:" in done.stdout, done.stdout + done.stderr
    assert {n: hashlib.md5((src / n).read_bytes()).hexdigest() for n in ("kb.db", "archive.db")} == md5_before, \
        "the source was written"
    lineage = LineageStore(_target(pg))
    try:
        ids = [r[0] for r in lineage.conn.execute("SELECT lineage_id FROM lineage_records")]
        assert ids and all(lineage.verify_record(i)["record_intact"] for i in ids)
    finally:
        lineage.close()
    chain = NexusArchiveChain(_target(pg))
    try:
        result = chain.verify_chain()
    finally:
        chain.close()
    assert result.valid and result.n_blocks > 0, result


ORDINAL_TABLES = ("archival_events", "hebbian_edges")       # the two whose rowid SQL reads (D3)


def _sqlite_rows(path: Path) -> dict:
    """Every table of a SQLite file read with plain sqlite3, none of the
    copy's own machinery: the two ordinal tables with their rowid, in rowid
    order; every other table as a sorted list (its rowid is read by nothing,
    D3, and Postgres keeps no order to give back); and sqlite_sequence."""
    con = sqlite3.connect(path)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        out = {}
        for t in names:
            if t in ORDINAL_TABLES:
                out[t] = con.execute(f'SELECT rowid, * FROM "{t}" ORDER BY rowid').fetchall()
            else:
                out[t] = sorted(con.execute(f'SELECT * FROM "{t}"').fetchall(), key=repr)
        has_seq = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone()
        out["sqlite_sequence"] = sorted(con.execute("SELECT name, seq FROM sqlite_sequence")) if has_seq else []
        return out
    finally:
        con.close()


def test_t2_the_round_trip_returns_the_source_as_the_product_reads_it(tmp_path, pg):
    from backend.store import StoreTarget
    from scripts import _store_copy as sc
    _source(tmp_path)
    assert _migrate(tmp_path, pg, "--confirm").returncode == 0
    reader = pg["grant_reader"]()
    out = tmp_path / "exported"
    exp = _run(EXPORT, ["--from", reader, "--out", str(out), "--confirm"], tmp_path)
    print(f"T2 export: exit {exp.returncode}; "
          f"{[l for l in exp.stdout.splitlines() if l.startswith(('copied', 'proven'))]}")
    assert exp.returncode == 0 and "proven and written" in exp.stdout, exp.stdout + exp.stderr
    assert RO_PASSWORD not in exp.stdout + exp.stderr
    assert sorted(p.name for p in out.iterdir()) == ["archive.db", "kb.db"], "the scratch folder was left behind"
    # The source as the product reads it: a copy opened (and so migrated) by the store classes.
    copy = tmp_path / "source-as-read"
    shutil.copytree(FIXTURE, copy)
    con = sqlite3.connect(copy / "kb.db")                       # the lazy tables the source holds, as the script reads them
    lazy = tuple(t for t in sc.LAZY_TABLES if con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone())
    con.close()
    sc.open_schema(StoreTarget.sqlite(copy / "kb.db"), StoreTarget.sqlite(copy / "archive.db"), lazy=lazy).close()
    # W8 (D4): a record written before W8 is salted at the first open of the store that holds it, at random, so
    # the source read here and the migrated target salt the same records independently: those two tables are
    # compared by their keys (which records, which fields), never by their random values.
    salted = {"record_salts": (0, 1), "record_commitments": (0, 3)}   # keys; bound_block is deterministic
    for name in ("kb.db", "archive.db"):
        got, want = _sqlite_rows(out / name), _sqlite_rows(copy / name)
        for table, key in salted.items():
            for rows in (got, want):
                if table in rows:
                    rows[table] = sorted({tuple(tuple(r)[i] for i in key) for r in rows[table]})
        assert sorted(got) == sorted(want), (name, sorted(set(got) ^ set(want)))
        parted = [t for t in want if got[t] != want[t]]
        print(f"T2 {name}: {len(want)} tables, {sum(len(v) for v in want.values())} rows; parted {parted}")
        assert not parted, (name, parted)
    exported = sc.open_schema(StoreTarget.sqlite(out / "kb.db"), StoreTarget.sqlite(out / "archive.db"))
    try:
        assert sc.product_checks(exported) == []
    finally:
        exported.close()


def test_t3_the_next_claim_gets_the_id_the_source_would_have_given(tmp_path, pg):
    """An upsert hit consumes an id (D3, measured), so the source's sequence
    stands above its largest id; the migrated store must continue from the
    sequence, not from the largest id."""
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    from backend.store import StoreTarget
    src = _source(tmp_path)
    store = TripleStore(StoreTarget.sqlite(src / "kb.db"))
    try:
        first = store.conn.execute("SELECT subject, relation, object, source, tier, granularity FROM triples "
                                   "ORDER BY id LIMIT 1").fetchone()
        hit = store.add(subject=first[0], relation=first[1], object=first[2], source=first[3], weight=0.99,
                        tier=first[4], granularity=Granularity(first[5]))
    finally:
        store.close()
    raw = sqlite3.connect(src / "kb.db")
    try:
        seq = raw.execute("SELECT seq FROM sqlite_sequence WHERE name = 'triples'").fetchone()[0]
        largest = raw.execute("SELECT MAX(id) FROM triples").fetchone()[0]
    finally:
        raw.close()
    assert seq > largest, f"the upsert hit (id {hit}) consumed no id: sequence {seq}, largest id {largest}"
    twin = tmp_path / "twin"
    shutil.copytree(src, twin)
    assert _migrate(tmp_path, pg, "--confirm").returncode == 0
    claim = dict(subject="europa", relation="orbits", object="jupiter", weight=0.7, tier=1,
                 granularity=Granularity.TOPIC, source="asserted")
    lite = TripleStore(StoreTarget.sqlite(twin / "kb.db"))
    try:
        want = lite.add(**claim)
    finally:
        lite.close()
    hosted = TripleStore(_target(pg))
    try:
        got = hosted.add(**claim)
    finally:
        hosted.close()
    print(f"T3: sequence {seq}, largest id {largest}; the next id {want} on SQLite and {got} on the migrated store")
    assert got == want == seq + 1


def test_t4_the_refusals_each_write_nothing(tmp_path, pg):
    from backend.store import connect
    _source(tmp_path)
    assert _migrate(tmp_path, pg, "--confirm").returncode == 0
    probe = connect(_target(pg), read_only=True)
    before = probe.execute("SELECT COUNT(*) FROM lineage_records").fetchone()[0]
    probe.close()
    again = _migrate(tmp_path, pg, "--confirm")
    assert again.returncode == 2 and "already holds NEXUS rows" in again.stdout, again.stdout + again.stderr
    probe = connect(_target(pg), read_only=True)
    assert probe.execute("SELECT COUNT(*) FROM lineage_records").fetchone()[0] == before, "the refused run wrote"
    probe.close()
    nowhere = _migrate(tmp_path, pg, "--confirm", url=_url(pg["schema"] + "_absent"))
    assert nowhere.returncode == 2 and "schema does not exist" in nowhere.stdout, nowhere.stdout + nowhere.stderr
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "kb.db").write_bytes(b"")
    exp = _run(EXPORT, ["--from", pg["url"], "--out", str(occupied), "--confirm"], tmp_path)
    assert exp.returncode == 2 and "already holds ['kb.db']" in exp.stdout, exp.stdout
    assert (occupied / "kb.db").read_bytes() == b""
    writer = _run(EXPORT, ["--from", pg["url"], "--out", str(tmp_path / "w"), "--confirm"], tmp_path)
    assert writer.returncode == 2 and "is a superuser" in writer.stdout, writer.stdout
    assert not (tmp_path / "w").exists()


def test_t5_a_nexus_process_holding_the_target_refuses_the_migration(tmp_path, pg):
    from backend.store_lock import acquire
    _source(tmp_path)
    lock = acquire(_target(pg), mode="services")
    try:
        held = _migrate(tmp_path, pg, "--confirm")
    finally:
        lock.release()
    assert held.returncode == 7 and "open in another NEXUS process" in held.stdout, held.stdout + held.stderr
    assert _nexus_tables(pg) == [], "the refused run created the store's tables"


def test_t6_companion_a_copy_short_of_one_row_is_named_and_rolled_back(tmp_path, pg, monkeypatch):
    from backend.store import StoreTarget, connect
    from scripts import _store_copy as sc
    copy = _source(tmp_path)
    stores = sc.open_schema(StoreTarget.sqlite(copy / "kb.db"), StoreTarget.sqlite(copy / "archive.db"))
    kb, ar = sc.database_of(stores.triples), sc.database_of(stores.chain)
    sources = {t: kb for t in sc.data_tables(kb)}
    sources.update({t: ar for t in sc.data_tables(ar)})
    target = _target(pg)
    tstores = sc.open_schema(target, target, lazy=tuple(t for t in sc.LAZY_TABLES if kb.has_table(t)))
    real = sc.rows_of
    monkeypatch.setattr(sc, "rows_of", lambda db, t, cols: real(db, t, cols)[:-1] if t == "lineage_records"
                        else real(db, t, cols))
    db = connect(target)
    want = kb.execute("SELECT COUNT(*) FROM lineage_records").fetchone()[0]
    try:
        with pytest.raises(sc.CopyRefused, match=f"lineage_records: source {want} rows, target {want - 1}"):
            sc.copy_rows(sources, db, sorted(sources))
        assert db.execute("SELECT COUNT(*) FROM lineage_records").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM triples").fetchone()[0] == 0, "the rolled-back copy left rows"
    finally:
        db.close()
        stores.close()
        tstores.close()


def test_t7_companion_a_copy_that_changes_one_value_is_named_by_the_rows(tmp_path, pg, monkeypatch):
    """Every count agrees and the copy commits, but one work unit's type was
    changed on the way: the per-table digest names the table, and the counts,
    the record hashes and the chain (which never read that column) do not."""
    from backend.store import StoreTarget, connect
    from scripts import _store_copy as sc
    copy = _source(tmp_path)
    stores = sc.open_schema(StoreTarget.sqlite(copy / "kb.db"), StoreTarget.sqlite(copy / "archive.db"))
    kb, ar = sc.database_of(stores.triples), sc.database_of(stores.chain)
    sources = {t: kb for t in sc.data_tables(kb)}
    sources.update({t: ar for t in sc.data_tables(ar)})
    target = _target(pg)
    tstores = sc.open_schema(target, target, lazy=tuple(t for t in sc.LAZY_TABLES if kb.has_table(t)))
    real = sc.rows_of
    cols = sc.columns_of(kb, "work_unit")
    at = cols.index("work_unit_type") + 1                        # rows_of's first cell is the ordinal

    def changed(db, t, c):
        rows = real(db, t, c)
        if t != "work_unit" or db is not kb:
            return rows
        last = list(rows[-1])
        last[at] = last[at] + " (changed)"
        return rows[:-1] + [tuple(last)]

    monkeypatch.setattr(sc, "rows_of", changed)
    db = connect(target)
    try:
        sc.copy_rows(sources, db, sorted(sources))                 # the counts agree: it commits
        monkeypatch.setattr(sc, "rows_of", real)
        t_db = sc.database_of(tstores.triples)
        findings = sc.compare(sc.facts(kb, ar), sc.facts(t_db, t_db))
    finally:
        db.close()
        stores.close()
        tstores.close()
    print(f"T7: {findings}")
    assert findings == ["the rows differ in ['work_unit']"], findings


def test_t8_a_copy_that_does_not_prove_is_rolled_back_by_the_script(tmp_path, pg, monkeypatch, capsys):
    """The migration proves inside the copy's own transaction: with one value
    changed on the way (every count agrees), the run exits 6 naming the
    table, and the target holds none of the source's rows: nothing was
    written, so a second run is not refused."""
    from backend.store import connect
    from scripts import _store_copy as sc
    from scripts import migrate_sqlite_to_postgres as migrate
    _source(tmp_path)
    monkeypatch.setenv("NEXUS_ENV_ROOT", str(tmp_path))
    real = sc.rows_of

    def changed(db, t, c):
        rows = real(db, t, c)
        if t != "work_unit" or db.backend != "sqlite":
            return rows
        at = c.index("work_unit_type") + 1
        last = list(rows[-1])
        last[at] = last[at] + " (changed)"
        return rows[:-1] + [tuple(last)]

    monkeypatch.setattr(sc, "rows_of", changed)
    rc = migrate.main(["--data-root", str(tmp_path / "source"), "--to", pg["url"], "--confirm"])
    out = capsys.readouterr().out
    print(f"T8: exit {rc}; {[l for l in out.splitlines() if l.startswith('NOT PROVEN')]}")
    assert rc == 6 and "rolled back and nothing was written" in out and "the rows differ in ['work_unit']" in out, out
    probe = connect(_target(pg), read_only=True)
    try:
        held = {t: probe.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in ("lineage_records", "work_unit",
                                                                                       "triples", "archive_blocks")}
    finally:
        probe.close()
    assert held == {"lineage_records": 0, "work_unit": 0, "triples": 0, "archive_blocks": 0}, held
    monkeypatch.setattr(sc, "rows_of", real)
    again = migrate.main(["--data-root", str(tmp_path / "source"), "--to", pg["url"], "--confirm"])
    assert again == 0 and "proven:" in capsys.readouterr().out


def test_t9_the_boundary_ledgers_order_survives_the_round_trip(tmp_path, pg):
    """W6's delta check, D1: the export read a table outside its own copy of the ordinal list in another order.
    Twelve units closed and reopened at one instant, migrated and exported: each still reads close, then reopen,
    which only the ordinal says."""
    from backend.kb import work_units as wu
    from backend.kb.schema import connect as kb_connect
    src = _source(tmp_path)
    store = wu.WorkUnitStore(kb_connect(src / "kb.db"))
    who = wu.actor_fields({"id": "alice"})
    instant = 4_000_000_000.0       # after every time the fixture holds: its closed unit predates the ledger
    for n in range(12):
        uid = f"order-{n}"
        store.open(uid, work_unit_type="t", opened_by=who, now=instant)
        store.close(uid, closed_by=who, now=instant)
        store.reopen(uid, reopened_by=who, now=instant)
    assert store.verify_events()["verdict"] == "consistent"
    store.conn.close()
    assert _migrate(tmp_path, pg, "--confirm").returncode == 0
    out = tmp_path / "exported"
    exp = _run(EXPORT, ["--from", pg["grant_reader"](), "--out", str(out), "--confirm"], tmp_path)
    assert exp.returncode == 0, exp.stdout + exp.stderr
    exported = wu.WorkUnitStore(kb_connect(out / "kb.db"))
    check = exported.verify_events()
    assert check["verdict"] == "consistent", check["problems"][:3]
    assert all([e["kind"] for e in exported.events(f"order-{n}")] == [wu.KIND_CLOSE, wu.KIND_REOPEN] for n in range(12))
