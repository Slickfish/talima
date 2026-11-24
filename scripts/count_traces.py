"""AB.0 — how often do relations co-occur in the answer record? READ ONLY.

Counts, per lineage record, the distinct relations among the injected and
among the cited beliefs, and tallies co-occurring relation pairs. The
operator's run of this (41 records, 34 co-injection turns / 1,441 pairs,
24 co-citation turns / 460 pairs) is what moved Packet AB from volume to
separation, and its findings are recorded in the AB trail — this script is
kept so that number can be reproduced, not because it is expected to run
routinely.

    python scripts/count_traces.py        # from the repo root, over data/kb.db
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    print(store_line("data/kb.db"), flush=True)              # law 15: the store first (W4 part 4)
    con = open_read_only('data/kb.db', row_factory=None)
    con.row_factory = sqlite3.Row
    rel = {r["id"]: r["relation"]
           for r in con.execute("SELECT id, relation FROM triples")}
    inj = cit = 0
    pairs_inj: dict = {}
    pairs_cit: dict = {}
    for r in con.execute("SELECT injected_json FROM lineage_records"):
        items = json.loads(r["injected_json"])
        ri = {rel.get(i.get("belief_id")) for i in items} - {None}
        rc = {rel.get(i.get("belief_id")) for i in items
              if i.get("cited") is not False} - {None}
        for s, name in ((ri, pairs_inj), (rc, pairs_cit)):
            for a in s:
                for b in s:
                    if a < b:
                        name[(a, b)] = name.get((a, b), 0) + 1
        inj += len(ri) > 1
        cit += len(rc) > 1
    n = con.execute("SELECT COUNT(*) FROM lineage_records").fetchone()[0]
    print(f"lineage records: {n}")
    print(f"co-injection events: {inj} turns, {len(pairs_inj)} distinct relation pairs")
    print(f"co-citation events : {cit} turns, {len(pairs_cit)} distinct relation pairs")
    print("top pairs:", sorted(pairs_cit.items(), key=lambda kv: -kv[1])[:10])
    return 0


if __name__ == "__main__":
    sys.exit(main())
