"""W3b part 4 (run/specs/W3b.md D10) — the copy both migration scripts make,
and the checks that say the copy is the same store.

`scripts/migrate_sqlite_to_postgres.py` and `scripts/export_postgres_to_sqlite.py`
resolve the source and the target, refuse what must be refused, and call
this module. The copy is the same in both directions:

- The SCHEMA is the product's, never a translation of the source's text:
  the target is created by opening the store classes on it (`open_schema`;
  the lazily created tables `mcp_coverage` and `triple_embeddings` only when
  the source has them), and the source's columns must equal the target's,
  table by table, or the copy is refused by name.
- Every ROW is copied with its id, in one transaction per target database.
  The two tables whose insertion order SQL reads (`archival_events`, read in
  `(ts, rowid)` order by as-of, and `hebbian_edges`, loaded in rowid order
  into the graph) carry their ordinal across: SQLite's implicit rowid into
  Postgres's `rowid` column, and back. Every other table's rowid is read by
  nothing (W3b's establish); on SQLite it is the id where the key is an
  integer and is assigned in the source's key order elsewhere.
- The SEQUENCES are carried as they are: `sqlite_sequence` and
  `nexus_sequence` hold the largest id ever handed out, which an upsert hit
  moves past the largest id stored (D3, measured), so the next claim on the
  target gets the id the source would have given it.
- The COUNTS are compared inside the transaction, before it commits: a table
  whose target count differs from the source's is named with both counts and
  the transaction is rolled back, so a copy that is short writes nothing.
  A caller may hand `copy_rows` a `prove` as well (the migration hands it
  the full comparison of `facts` below), run in the same transaction: a copy
  that does not prove is rolled back the same way.

`facts(kb, archive)` reads what the two stores must agree on, through plain
queries a read-only session may run: the row counts; every table's rows, as a
digest per table that the read order cannot part (the dialect's ordinal tables'
ordinals folded in); the sequences; the sorted record hashes; the chain's
length and head; the graph's rows in load order; and the as-of pages at the
earliest admission, at every archival event's instant and after the last act.
`product_checks(stores)` runs the product's own verifiers on the target (the
chain verifies, every record recomputes, the graph loads). `compare` lines
up two stores' facts; each finding is a line and an empty list is the pass.

A table's rows are compared as a set, not in the order SQLite's hidden rowid
kept them: Postgres has no such order to give back (the dialect's ordinal tables
aside, D3), and nothing reads it (the establish's census of every ORDER BY,
W3b S1), so a store exported back from Postgres holds the same rows as the
one migrated in, while its text-keyed tables' physical order may differ.
"""
from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field

#: The tables whose implicit rowid SQL reads (as-of's ledger order; the graph's load order; the boundary
#: ledger's order since W6). The dialect's own list, never a copy of it: a copy kept here missed W6's table,
#: and the export read the Postgres ledger in another order (W6's delta check, D1).
from backend.store.dialect import ROWID_TABLES  # noqa: E402
#: Support tables: the store's own bookkeeping, never copied as data (the
#: sequences are carried apart; the lock belongs to the running process).
SUPPORT_TABLES = ("sqlite_sequence", "nexus_sequence", "nexus_store_lock", "nexus_store_lock_log")
#: Lazily created tables: made on the target only when the source has them.
LAZY_TABLES = ("mcp_coverage", "triple_embeddings")
#: The tables archive.db holds (the archive chain's); every other table is kb.db's.
#: W9 (D7): the authorities' receipts sit beside the blocks.
ARCHIVE_TABLES = ("archive_blocks", "block_anchors")


class CopyRefused(RuntimeError):
    """The copy cannot be made as asked; the message names why."""


@dataclass
class Stores:
    """The product's store classes opened on one store (one Postgres target,
    or a kb.db and an archive.db)."""
    triples: object
    lineage: object
    recalibration: object
    trails: object
    graph: object
    chain: object

    def close(self) -> None:
        for s in (self.triples, self.lineage, self.recalibration, self.trails, self.graph, self.chain):
            try:
                s.close()
            except Exception:                       # noqa: BLE001 — a close never decides the copy
                pass


def open_schema(kb_target, archive_target, *, lazy=()) -> Stores:
    """Open every store class on the target, which creates its schema (and on
    an older SQLite copy migrates it), plus the lazily created tables named."""
    from backend.kb.recalibration import RecalibrationStore
    from backend.kb.tiered_store import TripleStore
    from backend.kb.trails import TrailStore
    from backend.kb.work_units import WorkUnitStore
    from backend.memory.chain import NexusArchiveChain
    from backend.memory.graph_store import HebbianGraphStore
    from backend.memory.lineage import LineageStore
    triples = TripleStore(kb_target)
    stores = Stores(triples=triples, lineage=LineageStore(kb_target), recalibration=RecalibrationStore(kb_target),
                    trails=TrailStore(kb_target), graph=HebbianGraphStore(kb_target),
                    chain=NexusArchiveChain(archive_target))
    WorkUnitStore(triples.conn)
    if "mcp_coverage" in lazy:
        from backend.mcp.coverage import CoverageStore
        CoverageStore(triples.conn)
    if "triple_embeddings" in lazy:
        from backend.kb.semantic_index import SemanticIndex
        SemanticIndex(triples)._ensure()
    return stores


def database_of(store):
    """The `backend.store.Database` behind a store class (its connection
    handle unwrapped)."""
    conn = store.conn
    return getattr(conn, "_conn", conn)


def open_time_rows(*, lazy=()) -> dict[str, int]:
    """The rows a fresh open of the product's store classes writes, by table,
    with the lazily created tables named (derived on a scratch SQLite store:
    today `scope_meta`'s one row, and `mcp_meta`'s when `mcp_coverage` is
    named). A target holding no more than these holds no store, so a database
    a NEXUS server once opened empty, or a copy that was rolled back, can be
    migrated into."""
    import tempfile
    from pathlib import Path
    from backend.store import StoreTarget
    with tempfile.TemporaryDirectory(prefix="nexus-open-rows-") as d:
        stores = open_schema(StoreTarget.sqlite(Path(d) / "kb.db"), StoreTarget.sqlite(Path(d) / "archive.db"),
                             lazy=lazy)
        try:
            out = {}
            for store in (stores.triples, stores.chain):
                db = database_of(store)
                for t in data_tables(db):
                    n = db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    if n:
                        out[t] = n
            return out
        finally:
            stores.close()


def data_tables(db) -> list[str]:
    """The store's data tables, sorted: every table but the support tables."""
    return [t for t in db.tables() if t not in SUPPORT_TABLES]


def columns_of(db, table: str) -> list[str]:
    """Column names in declaration order; Postgres's ordinal column is left
    out (it is the row's insertion ordinal and is carried as `rowid`)."""
    return [c.name for c in db.columns(table) if c.name != "rowid"]


def rows_of(db, table: str, cols: list[str]) -> list[tuple]:
    """Every row as `(ordinal, *cols)` in a deterministic order: by rowid on
    SQLite and for the dialect's ordinal tables on Postgres; by id, else by every
    column, for the rest on Postgres (whose ordinal is then None)."""
    quoted = ", ".join(f'"{c}"' for c in cols)
    if db.backend == "sqlite" or table in ROWID_TABLES:
        return [tuple(r) for r in db.execute(f'SELECT rowid, {quoted} FROM "{table}" ORDER BY rowid')]
    order = '"id"' if "id" in cols else quoted
    return [(None, *tuple(r)) for r in db.execute(f'SELECT {quoted} FROM "{table}" ORDER BY {order}')]


def sequences_of(db, tables) -> dict[str, int]:
    """The id high-water mark per table, for the tables named."""
    if db.backend == "sqlite":
        if not db.has_table("sqlite_sequence"):
            return {}
        rows = db.execute("SELECT name, seq FROM sqlite_sequence").fetchall()
    else:
        rows = db.execute("SELECT name, seq FROM nexus_sequence").fetchall()
    return {r[0]: int(r[1]) for r in rows if r[0] in tables}


def _set_sequences(db, seqs: dict[str, int]) -> None:
    for name, seq in sorted(seqs.items()):
        if db.backend == "sqlite":
            if db.execute("SELECT 1 FROM sqlite_sequence WHERE name = ?", (name,)).fetchone():
                db.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (seq, name))
            else:
                db.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (name, seq))
        else:
            db.execute("INSERT INTO nexus_sequence (name, seq) VALUES (?, ?) "
                       "ON CONFLICT (name) DO UPDATE SET seq = excluded.seq", (name, seq))


@dataclass
class CopyReport:
    tables: dict = field(default_factory=dict)          # table -> rows copied
    sequences: dict = field(default_factory=dict)       # table -> id high-water mark carried


def copy_rows(sources: dict, target_db, tables, *, prove=None) -> CopyReport:
    """Copy `tables` from `sources` (table -> the `Database` holding it) into
    `target_db` in ONE transaction: the target's open-time rows cleared (a
    fresh store's `scope_meta` row; the caller has refused a target holding
    more), every row inserted with its id and, for the dialect's ordinal tables,
    its ordinal, the sequences carried, the counts compared before the
    commit. `prove(target_db)`, when given, runs inside the same
    transaction (plain queries see the uncommitted rows) and returns
    findings; any finding rolls the copy back, so a copy that does not prove
    leaves the target as it was."""
    report = CopyReport()
    try:
        for table in tables:
            src = sources[table]
            cols, tcols = columns_of(src, table), columns_of(target_db, table)
            if cols != tcols:
                raise CopyRefused(f"{table}: the source's columns {cols} are not the target's {tcols}; "
                                  f"a copy is made between stores of one schema only")
            rows = rows_of(src, table, cols)
            carry = table in ROWID_TABLES
            names = (["rowid"] if carry else []) + cols
            payload = [r if carry else r[1:] for r in rows]
            target_db.execute(f'DELETE FROM "{table}"')
            if payload:
                quoted = ", ".join(f'"{n}"' for n in names)
                marks = ", ".join("?" for _ in names)
                target_db.executemany(f'INSERT INTO "{table}" ({quoted}) VALUES ({marks})', payload)
            report.tables[table] = len(payload)
        seqs: dict[str, int] = {}
        for db in {id(sources[t]): sources[t] for t in tables}.values():
            seqs.update(sequences_of(db, tables))
        _set_sequences(target_db, seqs)
        report.sequences = seqs
        short = []
        for table in tables:
            want = sources[table].execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            got = target_db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if want != got:
                short.append(f"{table}: source {want} rows, target {got}")
        if short:
            raise CopyRefused("the copy does not hold the source's rows, so it was rolled back and nothing was "
                              "written: " + "; ".join(short))
        findings = prove(target_db) if prove is not None else []
        if findings:
            raise CopyRefused("the copy does not prove, so it was rolled back and nothing was written: "
                              + "; ".join(findings))
        target_db.commit()
    except BaseException:
        target_db.rollback()
        raise
    return report


# ---------------------------------------------------------------------------
# what the two stores must agree on, read by plain queries
# ---------------------------------------------------------------------------

def _as_of_instants(kb) -> list[float]:
    from backend.kb.as_of import earliest_admission
    first = earliest_admission(kb)
    if first is None:
        return []
    events = [float(r[0]) for r in kb.execute("SELECT DISTINCT ts FROM archival_events ORDER BY ts")]
    last_admit = kb.execute("SELECT MAX(created_at) FROM triples").fetchone()[0]
    last = max([first] + events + ([float(last_admit)] if last_admit is not None else []))
    return [first] + events + [math.nextafter(last, math.inf)]


def as_of_pages(kb) -> dict:
    from backend.kb.as_of import held_at
    out = {}
    for ts in _as_of_instants(kb):
        page, total = held_at(kb, ts, limit=1_000_000)
        out[repr(ts)] = (total, [repr(sorted(r.items())) for r in page])
    return out


def _canonical(value) -> bytes:
    """One stored value as bytes both backends give alike: a type tag, a
    length and the payload. A zero REAL is packed unsigned, because SQLite
    stores -0.0 as 0.0 and Postgres keeps the sign (the declared
    difference, D6; tests/test_w3b_dialect.py P7)."""
    if value is None:
        tag, body = b"N", b""
    elif isinstance(value, bool):
        raise CopyRefused(f"a boolean value ({value!r}) has no SQLite twin: the store holds integers")
    elif isinstance(value, int):
        tag, body = b"I", str(value).encode()
    elif isinstance(value, float):
        tag, body = b"F", struct.pack("<d", 0.0 if value == 0.0 else value)
    elif isinstance(value, str):
        tag, body = b"S", value.encode("utf-8")
    elif isinstance(value, (bytes, bytearray, memoryview)):
        tag, body = b"B", bytes(value)
    else:
        raise CopyRefused(f"a stored value of type {type(value).__name__} has no canonical form here")
    return tag + len(body).to_bytes(8, "little") + body


def table_digest(db, table: str) -> str:
    """Every row of `table` folded into one digest that does not depend on
    the order the rows are read in (the sum of the rows' SHA-256 values,
    modulo 2**256), so the two backends' scan orders cannot part it and any
    changed, missing or extra row does. The dialect's ordinal tables fold their
    ordinal in with the row: their order is data (as-of's ledger order, the
    graph's load order)."""
    cols = columns_of(db, table)
    quoted = ", ".join(f'"{c}"' for c in cols)
    carry = table in ROWID_TABLES
    sql = f'SELECT {"rowid, " if carry else ""}{quoted} FROM "{table}"'
    total = 0
    for row in db.execute(sql):
        digest = hashlib.sha256()
        for value in tuple(row):
            digest.update(_canonical(value))
        total = (total + int.from_bytes(digest.digest(), "big")) % (1 << 256)
    return f"{total:064x}"


def facts(kb, archive) -> dict:
    """Everything the two stores must agree on, from `Database`s a read-only
    session may hold: counts, sequences, every table's rows (a digest per
    table), record hashes, the chain, the graph's rows in load order, the
    as-of pages."""
    tables = {t: kb for t in data_tables(kb)}
    if archive is not None:
        tables.update({t: archive for t in data_tables(archive)})
    counts = {t: db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t, db in sorted(tables.items())}
    rows = {t: table_digest(db, t) for t, db in sorted(tables.items())}
    seqs: dict[str, int] = {}
    for db in {id(d): d for d in tables.values()}.values():
        seqs.update(sequences_of(db, tables))
    hashes = sorted(r[0] for r in kb.execute("SELECT record_hash FROM lineage_records"))
    chain = None
    if archive is not None and archive.has_table("archive_blocks"):
        head = archive.execute("SELECT idx, block_hash FROM archive_blocks ORDER BY idx DESC LIMIT 1").fetchone()
        chain = (counts.get("archive_blocks", 0), tuple(head) if head else None)
    gcols = columns_of(kb, "hebbian_edges") if kb.has_table("hebbian_edges") else []
    graph = [tuple(r[1:]) for r in rows_of(kb, "hebbian_edges", gcols)] if gcols else []
    return {"counts": counts, "rows": rows, "sequences": seqs, "record_hashes": hashes, "chain": chain,
            "graph": graph, "as_of": as_of_pages(kb)}


def product_checks(stores: Stores) -> list[str]:
    """The product's own verifiers on a store: the chain verifies, every
    record recomputes to its stored hash, the graph loads."""
    from backend.memory.hippocampus import Hippocampus
    out = []
    result = stores.chain.verify_chain()
    if not result.valid:
        out.append(f"the archive chain does not verify: broken at block {result.broken_at} ({result.reason})")
    ids = [r[0] for r in stores.lineage.conn.execute("SELECT lineage_id FROM lineage_records ORDER BY lineage_id")]
    bad = [i for i in ids if not stores.lineage.verify_record(i)["record_intact"]]
    if bad:
        out.append(f"{len(bad)} of {len(ids)} records do not recompute to their stored hash, the first {bad[0]}")
    stores.graph.load_into(Hippocampus())
    return out


def compare(source_facts: dict, target_facts: dict) -> list[str]:
    """What differs between two stores' facts, one line each; empty is the
    pass. A table one side lacks counts as empty there: a lazily created
    table the target already had (an empty `triple_embeddings`) is the same
    store as one without it."""
    out = []
    s_counts, t_counts = source_facts["counts"], target_facts["counts"]
    diff = {t: (s_counts.get(t, 0), t_counts.get(t, 0)) for t in sorted(set(s_counts) | set(t_counts))
            if s_counts.get(t, 0) != t_counts.get(t, 0)}
    if diff:
        out.append(f"the row counts differ (table: source, target): {diff}")
    empty = f"{0:064x}"
    s_rows, t_rows = source_facts["rows"], target_facts["rows"]
    parted = sorted(t for t in set(s_rows) | set(t_rows) if s_rows.get(t, empty) != t_rows.get(t, empty))
    if parted:
        out.append(f"the rows differ in {parted}")
    for key in ("sequences", "record_hashes", "chain", "graph"):
        if source_facts[key] != target_facts[key]:
            out.append(f"the {key.replace('_', ' ')} differ between the source and the target")
    s, t = source_facts["as_of"], target_facts["as_of"]
    if s != t:
        diff = [k for k in sorted(set(s) | set(t)) if s.get(k) != t.get(k)]
        out.append(f"the as-of pages differ at {len(diff)} of {len(s)} instants, the first {diff[0]}")
    return out


__all__ = ["ARCHIVE_TABLES", "CopyRefused", "CopyReport", "LAZY_TABLES", "ROWID_TABLES", "SUPPORT_TABLES", "Stores",
           "as_of_pages", "columns_of", "compare", "copy_rows", "data_tables", "database_of", "facts",
           "open_schema", "open_time_rows",
           "product_checks", "rows_of", "sequences_of"]
