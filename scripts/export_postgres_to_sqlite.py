#!/usr/bin/env python3
"""W3b part 4 (run/specs/W3b.md D10) — copy a NEXUS store out of a Postgres
database into a data root's kb.db and archive.db, and prove the copy is the
same store. The reverse of `scripts/migrate_sqlite_to_postgres.py`.

    python scripts/export_postgres_to_sqlite.py --from URL --out DIR [--confirm]

The SOURCE is the URL the server is given as `NEXUS_DATABASE_URL` (its
schema, when not `public`, named in the URL's `options`), read only
(RUN_CHARTER law 15). The first line printed names it with the password
redacted. It is opened through `scripts/_readonly.py`'s one read-only open:
a read-only session, and a role that cannot write, refused by name otherwise
(W3b D9). A running NEXUS may keep serving it.

The TARGET is two new files. The export refuses a folder that already holds
kb.db or archive.db. The files are built in a scratch folder inside the
target folder. The product's store classes create them, every row is copied
with its id in one transaction per file, the two ordinal tables keep their
insertion order, the id sequences are carried, and the counts are compared
before each commit (`scripts/_store_copy.py`). Only a copy that proves is
moved into place, so a failed export leaves the target folder as it was. The
checks: every table holds the source's rows (as a set: the two ordinal tables
with their order); the chain verifies and its head is the source's; every
record recomputes and the record hashes are the source's; the graph's rows
load in the source's order; the as-of pages are equal at the earliest
admission, at every archival event's instant and after the last act; and the
sequences are equal.

Without `--confirm` it is a dry run: the source is read, the plan is
printed, and nothing is written.

Exit codes:
- 0: done, or a dry run that found nothing to refuse.
- 2: bad arguments, a source that is not a NEXUS store or cannot be read
  read-only, or a target that already holds a store.
- 6: the copy does not prove (each finding printed; nothing was moved into
  place).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402
from scripts._readonly import ReadOnlyRefused, open_read_only, redacted, store_exists, table_exists  # noqa: E402
from scripts import _store_copy as sc                            # noqa: E402

EXIT_OK, EXIT_ARGS, EXIT_UNPROVEN = 0, 2, 6


def main(argv: "list[str] | None" = None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="source", required=True,
                    help="the source Postgres URL, as NEXUS_DATABASE_URL; opened read-only")
    ap.add_argument("--out", required=True, help="the data root to write kb.db and archive.db into")
    ap.add_argument("--confirm", action="store_true", help="write the target; without it, a dry run")
    args = ap.parse_args(argv)
    load_env()
    print(f"source: postgres {redacted(args.source)} (read only: a read-only session and a role that cannot "
          f"write)", flush=True)
    out = Path(args.out).resolve()
    print(f"target: {out.as_posix()}/kb.db and archive.db", flush=True)
    if not args.source.strip().lower().startswith(("postgresql://", "postgres://")):
        print("REFUSED: --from must be a postgresql:// URL")
        return EXIT_ARGS
    existing = [n for n in ("kb.db", "archive.db") if (out / n).exists()]
    if existing:
        print(f"REFUSED: {out} already holds {existing}; an export writes a new store and never overwrites one")
        return EXIT_ARGS
    answers, why = store_exists(args.source)
    if not answers:
        print(f"REFUSED: the source cannot be read: {why}")
        return EXIT_ARGS
    try:
        src = open_read_only(args.source, url_ok=True)
    except ReadOnlyRefused as e:
        print(f"REFUSED: {e}")
        return EXIT_ARGS
    scratch = tstores = None
    try:
        if not all(table_exists(src, t) for t in ("triples", "lineage_records")):
            print("REFUSED: the source is not a NEXUS store (no triples or lineage_records table)")
            return EXIT_ARGS
        tables = sc.data_tables(src)
        lazy = tuple(t for t in sc.LAZY_TABLES if t in tables)
        plan = {t: src.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        seqs = sc.sequences_of(src, tables)
        print(f"plan: {len(tables)} tables, {sum(plan.values())} rows: {plan}")
        print(f"      id sequences carried: {seqs}; lazily created tables: {list(lazy) or 'none'}")
        if not args.confirm:
            print("dry run: nothing was written; --confirm exports and proves the copy")
            return EXIT_OK
        out.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix=".nexus-export-", dir=out))
        from backend.store import StoreTarget
        tstores = sc.open_schema(StoreTarget.sqlite(scratch / "kb.db"), StoreTarget.sqlite(scratch / "archive.db"),
                                 lazy=lazy)
        kb_db, ar_db = sc.database_of(tstores.triples), sc.database_of(tstores.chain)
        kb_tables = [t for t in tables if t not in sc.ARCHIVE_TABLES]
        ar_tables = [t for t in tables if t in sc.ARCHIVE_TABLES]
        try:
            r1 = sc.copy_rows({t: src for t in kb_tables}, kb_db, kb_tables)
            r2 = sc.copy_rows({t: src for t in ar_tables}, ar_db, ar_tables)
        except sc.CopyRefused as e:
            print(f"REFUSED: {e}")
            return EXIT_UNPROVEN
        print(f"copied: {sum(r1.tables.values()) + sum(r2.tables.values())} rows in {len(tables)} tables "
              f"(one transaction per file); sequences {dict(r1.sequences, **r2.sequences)}")
        findings = sc.compare(sc.facts(src, src), sc.facts(kb_db, ar_db)) + sc.product_checks(tstores)
        if findings:
            print("NOT PROVEN — the copy differs from its source; nothing was moved into place:")
            for f in findings:
                print(f"  {f}")
            return EXIT_UNPROVEN
        tstores.close()
        tstores = None
        existing = [n for n in ("kb.db", "archive.db") if (out / n).exists()]
        if existing:                                                 # written there while this one ran
            print(f"REFUSED: {out} now holds {existing}; an export never overwrites a store")
            return EXIT_ARGS
        for name in ("kb.db", "archive.db"):
            os.replace(scratch / name, out / name)
        print(f"proven and written: {out / 'kb.db'} and {out / 'archive.db'} — every table holds the "
              f"source's rows, the chain verifies with the source's head, every record recomputes with the "
              f"source's hashes, the graph's rows load in the source's order, the as-of pages and the sequences "
              f"are equal")
        return EXIT_OK
    finally:
        if tstores is not None:
            tstores.close()
        src.close()
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
