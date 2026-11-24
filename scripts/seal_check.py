"""NEXUS SEALING CHECK -- READ ONLY

Answers "did the turn's lineage record get digest-sealed into the chain?"
against COPIES of the live databases, never the originals. Its findings fed
the sealing investigation; kept so the check can be repeated after any
change to the seal path.

PREREQUISITE: a real chat turn must have happened, or lineage_records
will be empty and this answers nothing.

  1. Start the server, send ONE message through the UI, wait for the
     reply to finish, stop the server.
  2. copy data\\kb.db      data\\kb_measure3.db
     copy data\\archive.db data\\archive_measure3.db
  3. python scripts/seal_check.py

Column names below are taken from PRAGMA output only -- nothing assumed.
  archive_blocks: idx, timestamp, fingerprint_json, snapshot_path,
                  snapshot_hash, previous_hash, block_hash, snapshot_missing
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402
from scripts._readonly import open_read_only, store_line                    # noqa: E402

KB = os.path.join("data", "kb_measure3.db")
AR = os.path.join("data", "archive_measure3.db")


def ro(path):
    if not os.path.exists(path):
        print("MISSING: {}".format(path))
        return None
    return open_read_only(path, row_factory=None)


def q(conn, label, sql):
    if conn is None:
        return
    try:
        print("{}: {}".format(label, conn.execute(sql).fetchall()))
    except sqlite3.Error as e:
        print("{}: [ERROR] {}".format(label, e))


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    print(store_line(KB), flush=True)              # law 15: the store first (W4 part 4)
    print(store_line(AR), flush=True)              # law 15: the store first (W4 part 4)
    k, a = ro(KB), ro(AR)

    q(k, "LINEAGE (rows, sealed)",
      "SELECT COUNT(*), COUNT(sealed_block) FROM lineage_records")
    q(k, "LINEAGE DETAIL",
      "SELECT lineage_id, sealed_block FROM lineage_records "
      "ORDER BY rowid DESC LIMIT 5")
    q(a, "BLOCKS (n, min_idx, max_idx)",
      "SELECT COUNT(*), MIN(idx), MAX(idx) FROM archive_blocks")
    q(a, "BLOCKS MENTIONING LINEAGE",
      "SELECT COUNT(*) FROM archive_blocks "
      "WHERE fingerprint_json LIKE '%lineage%'")
    q(a, "LATEST BLOCK",
      "SELECT idx, timestamp, substr(fingerprint_json,1,400) "
      "FROM archive_blocks ORDER BY idx DESC LIMIT 1")

    for c in (k, a):
        if c:
            c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
