#!/usr/bin/env python
"""Which opposition pairs would actually fire on THIS store?

THE ARGUMENT FOR MEASURING RATHER THAN LISTING
    A larger antonym set is the obvious next step after T.2's seam, and the
    obvious way to build one is to write down thirty plausible opposites. That
    would be a list of my guesses about a store I cannot see.

    `ContradictionDetector` pairs relations that share a **(subject, object)**
    — `scan_recent` groups on exactly that and then asks whether the two
    relations oppose. So a pair can only ever fire where the store already
    holds two different relations about the same two terms. Every other pair,
    however sound as English, is inert here.

    That makes the candidate set a QUERY, not an opinion:

        SELECT subject, object, relation
          FROM triples
         WHERE archived = 0
      GROUP BY subject, object
        HAVING COUNT(DISTINCT relation) > 1

    Those rows are the entire surface on which contradiction detection can
    operate. This script runs that, reports the relation combinations found,
    and marks which the current set already catches.

WHAT IT DOES NOT DO
    It does not decide which combinations are oppositions. `TAK-441
    primarily_metabolized_by CYP3A4` and `TAK-441
    is_metabolized_by_primary_enzyme_of CYP3A4` share a (subject, object) and
    are the SAME claim twice, not a contradiction — that is a job for the
    deduplicator. Only a person who knows the domain can tell those apart, so
    the output is a ranked candidate list for a human to cut down, and the
    file it writes is a starting point to edit rather than a set to install.

    Read-only on the store. Opens it `mode=ro` and writes nothing to it.

USAGE
    python scripts/propose_contradiction_pairs.py --db data/kb.db
    python scripts/propose_contradiction_pairs.py --db data/kb.db --write candidates.json

    Then edit the file, keep only real oppositions, and:
        NEXUS_CONTRADICTION_PAIRS=/path/to/pairs.json
    Confirm it took effect at GET /cortex/stats -> contradiction_pairs.
"""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.cortex.contradiction import (                         # noqa: E402
    CONTRADICTION_PAIRS, ContradictionDetector, load_pairs)
from scripts._console import force_utf8_stdio
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                      # noqa: E402


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "kb.db")
    ap.add_argument("--write", type=Path, default=None,
                    help="write the candidates as a pair file to edit")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()
    print(store_line(args.db), flush=True)              # law 15: the store first (W4 part 4)

    print(__doc__.split("USAGE")[0].strip())
    print("=" * 72)
    if not args.db.is_file():
        print(f"no store at {args.db}")
        return 2

    # mode=ro — a read-only caller must never be forced through a constructor
    # that migrates (the reason `sealed_cited_belief_ids` takes a bare conn).
    conn = open_read_only(args.db, row_factory=None)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT subject, object, relation FROM triples WHERE archived = 0"
    ).fetchall()
    total = len(rows)
    conn.close()

    by_pair: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for r in rows:
        by_pair[(r["subject"], r["object"])].add(r["relation"])
    multi = {k: v for k, v in by_pair.items() if len(v) > 1}

    print(f"\nretrievable triples        : {total}")
    print(f"distinct (subject, object) : {len(by_pair)}")
    print(f"...with >1 relation        : {len(multi)}   "
          f"<- the ENTIRE surface detection can act on")
    if not multi:
        print("\nNo (subject, object) carries two relations. A larger pair set "
              "would change nothing on this store today — the detector has "
              "nowhere to fire. That is the finding; do not install a set on "
              "the strength of it looking sensible.")
        return 0

    active = load_pairs()
    detector = ContradictionDetector(pairs=active)
    combos = collections.Counter()
    caught = collections.Counter()
    for (subj, obj), rels in multi.items():
        for a, b in itertools.combinations(sorted(rels), 2):
            key = (ContradictionDetector._norm(a), ContradictionDetector._norm(b))
            combos[key] += 1
            if detector.is_contradictory(a, b):
                caught[key] += 1

    print(f"relation combinations      : {len(combos)}")
    print(f"...already caught by the {len(active)} active pair(s): {len(caught)}")
    print(f"\ntop {args.top} combinations — the candidate list, for a human to cut:")
    print(f"{'n':>4}  {'caught':>7}  combination")
    for (a, b), n in combos.most_common(args.top):
        print(f"{n:>4}  {'yes' if (a, b) in caught else '':>7}  {a}  <->  {b}")

    print("\nREAD THIS BEFORE INSTALLING ANY OF IT: a shared (subject, object) "
          "is necessary for a contradiction and nowhere near sufficient. Two "
          "spellings of one predicate share it too, and those are duplicates "
          "for the deduplicator, not oppositions. Cut the list by hand.")

    if args.write:
        proposed = [[a, b] for (a, b), _n in combos.most_common(args.top)
                    if (a, b) not in caught]
        payload = [list(p) for p in CONTRADICTION_PAIRS] + proposed
        args.write.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"\nwrote {len(payload)} pair(s) to {args.write} "
              f"({len(CONTRADICTION_PAIRS)} compiled + {len(proposed)} "
              f"candidates). EDIT IT — it is a starting point, not a set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
