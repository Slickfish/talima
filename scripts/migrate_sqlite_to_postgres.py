#!/usr/bin/env python3
"""W3b part 4 (run/specs/W3b.md D10) — copy a NEXUS data root's SQLite store
into a Postgres database, and prove the copy is the same store.

    python scripts/migrate_sqlite_to_postgres.py --data-root DIR --to URL [--confirm]

The SOURCE is read only (RUN_CHARTER law 15). The first line printed names
it. kb.db and archive.db are opened through `scripts/_readonly.py` (mode=ro,
query_only, a write-refusing authorizer) and copied by SQLite's online
backup API into a scratch folder this script owns. The product's store
classes open that COPY, so a store written under an earlier schema is
migrated in the copy exactly as the server would migrate it. The source is
never written.

The TARGET is the URL the server would be given as `NEXUS_DATABASE_URL`
(its schema, when not `public`, named in the URL's `options`). It is refused
when any of its tables already holds a NEXUS row beyond the ones a fresh open
writes: a migration fills an empty store and never merges two. Under
`--confirm` it is written behind the single-writer lock (W3b D8), so while a
NEXUS process serves it the run is refused with exit 7. The product's store
classes create its schema. Every row is copied with its id in one
transaction, the two ordinal tables keep their insertion order, the id
sequences are carried, and the counts are compared before the commit. A
short copy is rolled back and named (`scripts/_store_copy.py`).

Then it proves the copy. Inside the copy's own transaction, by plain queries
on both: every table holds the source's rows (as a set: the two ordinal
tables with their order); the archive chain's length and head are the
source's; the record hashes are the source's; the association graph's rows
load in the source's order; the as-of pages are equal at the earliest
admission, at every archival event's instant and after the last act; and
the sequences are equal. A copy that differs in any of these is rolled back,
so nothing is written. After the commit, the product's own verifiers run on
the target: the chain verifies and every record recomputes to its hash.

Without `--confirm` it is a dry run: the source is read, the target is
checked, the plan is printed, and nothing is written anywhere.

chroma/, snapshots/ and the logs stay in the data root. Under
`NEXUS_STORE=postgres` the server keeps them there (D7).

Exit codes:
- 0: done, or a dry run that found nothing to refuse.
- 2: bad arguments, a source that is not a NEXUS store, or a target that
  holds rows or does not answer.
- 6: the copy does not prove (each finding printed): rolled back, or, when
  the product's verifiers refuse the committed copy, left on the target.
- 7: the target's lock is held by a NEXUS process.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402
from scripts._readonly import open_read_only, redacted, store_exists, table_exists  # noqa: E402
from scripts import _store_copy as sc                            # noqa: E402

EXIT_OK, EXIT_ARGS, EXIT_UNPROVEN, EXIT_LOCKED = 0, 2, 6, 7


def snapshot(data_root: Path, scratch: Path) -> dict[str, Path]:
    """kb.db and (when present) archive.db, each opened read-only and copied
    into `scratch` by SQLite's online backup API; the copies' paths."""
    from backend.store import connect
    out = {}
    for name in ("kb.db", "archive.db"):
        src_path = data_root / name
        if not src_path.is_file():
            continue
        src = open_read_only(src_path)
        dst = connect(scratch / name)                 # the copy: this script's own file
        try:
            src.backup(dst.raw)
        finally:
            dst.close()
            src.close()
        out[name] = scratch / name
    return out


class TargetRefused(RuntimeError):
    """The target cannot receive the copy; the message names why."""


def check_target(target, fresh: dict) -> None:
    """Refuse a target whose schema does not exist or whose data tables hold
    more than a fresh open writes (`fresh`), counted on a read-only session.
    Run before the plan and again once the lock is held, so a NEXUS process
    that wrote between the two is refused too, never cleared by the copy."""
    from backend.store import connect
    check = connect(target, read_only=True)
    try:
        if check.schema is None:
            raise TargetRefused("the URL's schema does not exist on the target (the session resolves no "
                                "schema); create it, or name one that exists, first")
        held = {t: check.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in sc.data_tables(check)}
    finally:
        check.close()
    held = {t: n for t, n in held.items() if n > fresh.get(t, 0)}
    if held:
        raise TargetRefused(f"the target already holds NEXUS rows ({held}); a migration fills an empty store "
                            f"and never merges two")


def copy_into(target, sources: dict, tables: list, source_facts: dict) -> "sc.CopyReport":
    """The copy, written to the target in one transaction (the caller holds
    the target's lock) and compared with the source inside it: a copy whose
    facts differ from the source's is rolled back (`sc.CopyRefused`)."""
    from backend.store import connect
    db = connect(target)
    try:
        return sc.copy_rows(sources, db, tables, prove=lambda t: sc.compare(source_facts, sc.facts(t, t)))
    finally:
        db.close()


def main(argv: "list[str] | None" = None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", required=True, help="the data root holding kb.db (and archive.db); read only")
    ap.add_argument("--to", required=True, help="the target Postgres URL (postgresql://...), as NEXUS_DATABASE_URL")
    ap.add_argument("--confirm", action="store_true", help="write the target; without it, a dry run")
    args = ap.parse_args(argv)
    load_env()
    data_root = Path(args.data_root).resolve()
    print(f"source: {data_root.as_posix()}/kb.db and archive.db (read only: mode=ro + write authorizer; copied "
          f"to a scratch folder and opened there)", flush=True)
    print(f"target: postgres {redacted(args.to)}", flush=True)
    if not args.to.strip().lower().startswith(("postgresql://", "postgres://")):
        print("REFUSED: --to must be a postgresql:// URL")
        return EXIT_ARGS
    if not (data_root / "kb.db").is_file():
        print(f"REFUSED: {data_root / 'kb.db'} is not a file")
        return EXIT_ARGS
    probe = open_read_only(data_root / "kb.db")
    try:
        nexus = all(table_exists(probe, t) for t in ("triples", "lineage_records"))
        lazy = tuple(t for t in sc.LAZY_TABLES if table_exists(probe, t))
    finally:
        probe.close()
    if not nexus:
        print(f"REFUSED: {data_root / 'kb.db'} is not a NEXUS store (no triples or lineage_records table)")
        return EXIT_ARGS

    from backend.store import BackendUnavailable, StoreTarget
    target = StoreTarget.postgres(args.to.strip())
    scratch = Path(tempfile.mkdtemp(prefix="nexus-migrate-"))
    stores = tstores = lock = None
    try:
        copies = snapshot(data_root, scratch)
        stores = sc.open_schema(StoreTarget.sqlite(copies["kb.db"]), StoreTarget.sqlite(scratch / "archive.db"),
                                lazy=lazy)                                  # migrated in the copy
        kb_db, ar_db = sc.database_of(stores.triples), sc.database_of(stores.chain)
        sources = {t: kb_db for t in sc.data_tables(kb_db)}
        sources.update({t: ar_db for t in sc.data_tables(ar_db)})
        tables = sorted(sources)
        answers, why = store_exists(args.to)
        if not answers:
            print(f"REFUSED: the target does not answer: {why}")
            return EXIT_ARGS
        fresh = sc.open_time_rows(lazy=lazy)
        try:
            check_target(target, fresh)
        except (TargetRefused, BackendUnavailable) as e:
            print(f"REFUSED: {e}. Nothing was written.")
            return EXIT_ARGS
        plan = {t: sources[t].execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        seqs = {}
        for db in (kb_db, ar_db):
            seqs.update(sc.sequences_of(db, tables))
        print(f"plan: {len(tables)} tables, {sum(plan.values())} rows: {plan}")
        print(f"      id sequences carried: {seqs}; lazily created tables: {list(lazy) or 'none'}")
        if not args.confirm:
            print("dry run: nothing was written; --confirm copies and proves the copy")
            return EXIT_OK

        from backend.store_lock import StoreLocked, StoreLockUnavailable, acquire
        try:
            lock = acquire(target, mode="script:migrate_sqlite_to_postgres")
        except (StoreLocked, StoreLockUnavailable) as e:
            print(f"REFUSED: {e}. Nothing was written.")
            return EXIT_LOCKED
        try:
            check_target(target, fresh)                               # again, under the lock
        except TargetRefused as e:
            print(f"REFUSED: {e}. Nothing was written.")
            return EXIT_ARGS
        tstores = sc.open_schema(target, target, lazy=lazy)
        try:
            report = copy_into(target, sources, tables, sc.facts(kb_db, ar_db))
        except sc.CopyRefused as e:
            print(f"NOT PROVEN: {e}")
            return EXIT_UNPROVEN
        print(f"copied: {sum(report.tables.values())} rows in {len(report.tables)} tables in one transaction; "
              f"sequences {report.sequences}")
        findings = sc.product_checks(tstores)
        if findings:
            print("NOT PROVEN — the product's verifiers refuse the committed copy (the target holds it; clear "
                  "its tables before a second run):")
            for f in findings:
                print(f"  {f}")
            return EXIT_UNPROVEN
        print("proven: every table holds the source's rows, the chain verifies with the source's head, every "
              "record recomputes with the source's hashes, the graph's rows load in the source's order, the as-of "
              "pages and the sequences are equal")
        return EXIT_OK
    finally:
        for s in (stores, tstores):
            if s is not None:
                s.close()
        if lock is not None:
            lock.release()
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
