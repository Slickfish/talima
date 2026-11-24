"""AB.0 — is co-citation SEPARATION recoverable by normalising? READ ONLY.

The sequel to count_traces.py: for the most frequent co-cited relation
pairs, prints PMI and the two structural signals (shared subjects, shared
(subject, object) arguments). The operator's run is what established that
PMI is INVERTED on this store while shared_(s,o) separates perfectly —
1 for is_metabolized_by~primarily_metabolized_by, 0 for every topic pair —
which is the measurement Packet AB's three-leg conjunction is built on.
Kept to reproduce that table.

    python scripts/count_traces2.py       # from the repo root, over data/kb.db
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
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
    rel_of = {r["id"]: r["relation"]
              for r in con.execute("SELECT id, relation FROM triples")}
    args = defaultdict(set)
    subs = defaultdict(set)
    for r in con.execute(
            "SELECT subject, relation, object FROM triples WHERE archived=0"):
        args[r["relation"]].add((r["subject"], r["object"]))
        subs[r["relation"]].add(r["subject"])
    turns_with, pair_n = defaultdict(int), defaultdict(int)
    n_turns = 0
    for r in con.execute("SELECT injected_json FROM lineage_records"):
        rc = {rel_of.get(i.get("belief_id"))
              for i in json.loads(r["injected_json"])
              if i.get("cited") is not False} - {None}
        if len(rc) < 2:
            continue
        n_turns += 1
        for x in rc:
            turns_with[x] += 1
        for a in rc:
            for b in rc:
                if a < b:
                    pair_n[(a, b)] += 1
    print(f"{'pair':<70} n  PMI    shared_subj shared_(s,o)")
    for (a, b), n in sorted(pair_n.items(), key=lambda kv: -kv[1])[:15]:
        pmi = math.log2((n / n_turns)
                        / ((turns_with[a] / n_turns) * (turns_with[b] / n_turns)))
        print(f"{(a[:33] + '~' + b[:33]):<70} {n:<2} {pmi:5.2f}  "
              f"{len(subs[a] & subs[b]):<11} {len(args[a] & args[b])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
