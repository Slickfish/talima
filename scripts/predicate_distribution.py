#!/usr/bin/env python
"""How many predicates does this store have anything to learn from?

WHY THIS EXISTS
    OQ-2's sketch is that predicate identity should EMERGE from use:
    predicates associated by proximity and co-occurrence, the canonical form
    emerging as the most reinforced member of a cluster. That shape needs
    clusters, and a cluster needs members.

    On the operator's store the mean is roughly 1.3 rows per predicate. If
    most predicates appear exactly once there is no usage to learn from, and
    the emergent design is blocked on a corpus that does not exist yet rather
    than on a mechanism nobody has written. **That is a result, and it is the
    result this script exists to establish or refute.**

WHY IT TAKES NO MODEL, DELIBERATELY
    It answers the population question, and `predicate_similarity.py` answers
    the separation question. Keeping them apart means the population answer
    survives with no daemon running, and — the harder-won reason — a check
    that shares machinery with the thing it checks is not a check. W.4 turned
    on exactly that: the retrieval harness scored `expect` as a conjunction,
    made 32 of 36 pairs unanswerable, and reported it as a fact about the
    store. `tests/test_predicate_probes.py` asserts this file cannot import an
    embedder.

WHAT IT DOES NOT DO
    It does not propose a floor, a cluster or a merge. It prints the
    histogram and the two derived counts a floor would be chosen from
    (predicates with >=2 rows, with >=3), and stops. Same discipline as
    `propose_relation_vocabulary.py`: the shape of the evidence is ours, the
    decision is the operator's.

    It also reports the subject-set size per predicate, because OQ-2's second
    half — "connect similar subject/object types" — needs a type signature,
    and a signature drawn from one subject and one object is a sample of size
    one. That is a constraint of the corpus, not a gap in the code.

READ-ONLY, AND CHECKED RATHER THAN PROMISED
    The store is copied to a temp directory and read there; the original's
    md5 is printed before and after. This script never constructs a
    `TripleStore`, so `mode=ro` alone would avoid H.2's migration — the copy
    is here for one rule across the family and for a stable snapshot if a
    daemon is writing while it runs.

USAGE
    python scripts/predicate_distribution.py --db data/kb.db
    python scripts/predicate_distribution.py --top 40

    Exit 0 when at least MIN_CLUSTERABLE predicates have two or more rows,
    1 when fewer — so "there is nothing to cluster" is a gate a caller can
    branch on rather than a paragraph someone has to read.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.kb.semantic_index import excluded_relations         # noqa: E402
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402

# PROVISIONAL, and named rather than buried. Below this many predicates with
# two or more rows there is no population to cluster and the emergent design
# in OQ-2 is blocked on the corpus. 20 is a judgement, not a measurement, and
# the histogram above it is what an argument should be made from.
MIN_CLUSTERABLE = 20


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _rows(db: Path) -> list[dict]:
    conn = open_read_only(db, row_factory=None)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT subject, relation, object, source FROM triples "
        "WHERE archived = 0")]
    conn.close()
    return rows


def report(rows: list[dict], *, top: int) -> int:
    excl = set(excluded_relations())
    claims = [r for r in rows if r["relation"] not in excl]
    counts = collections.Counter(r["relation"] for r in claims)
    hist = collections.Counter(counts.values())
    n_pred, n_rows = len(counts), len(claims)

    print(f"live rows                  : {len(rows)}")
    print(f"substrate rows excluded    : {len(rows) - n_rows}  "
          f"({sorted(excl)})")
    print(f"claim rows                 : {n_rows}")
    print(f"distinct predicates        : {n_pred}")
    print(f"rows per predicate (mean)  : {n_rows / max(1, n_pred):.2f}")
    if not n_pred:
        print("\nno claim rows — nothing to report")
        return 1

    print("\n  rows   predicates   share of predicates   share of claim rows")
    for n in sorted(hist):
        p = hist[n]
        print(f"  {n:>4}   {p:>10}   {p / n_pred:>18.1%}   "
              f"{n * p / max(1, n_rows):>18.1%}")

    singles = hist.get(1, 0)
    ge2 = sum(p for n, p in hist.items() if n >= 2)
    ge3 = sum(p for n, p in hist.items() if n >= 3)
    print(f"\n  appearing exactly once : {singles} of {n_pred} "
          f"({singles / n_pred:.0%}) — no usage to learn from")
    print(f"  with >=2 rows          : {ge2}")
    print(f"  with >=3 rows          : {ge3}")

    print(f"\ntop {min(top, n_pred)} predicates by row count:")
    for rel, n in counts.most_common(top):
        print(f"  {n:>4}  {rel}")

    # OQ-2's second half, and whether the corpus can support it.
    subj = collections.defaultdict(set)
    obj = collections.defaultdict(set)
    for r in claims:
        subj[r["relation"]].add(r["subject"].lower())
        obj[r["relation"]].add(r["object"].lower())
    one_subject = sum(1 for s in subj.values() if len(s) == 1)
    print(f"\nusage agreement — 'connects similar subject/object types':")
    print(f"  predicates with exactly ONE distinct subject: {one_subject} "
          f"of {n_pred} ({one_subject / n_pred:.0%})")
    print(f"  A type signature drawn from one subject and one object is a "
          f"sample of size one. Computable, and worth nothing at that n — the "
          f"constraint is the corpus, not the code.")

    print("\n" + "=" * 72)
    if ge2 >= MIN_CLUSTERABLE:
        print(f"{ge2} predicates have two or more rows, at or above the "
              f"MIN_CLUSTERABLE={MIN_CLUSTERABLE} mark. A cluster-by-usage "
              f"design has a population to work on — though it reaches only "
              f"these {ge2}, not the {singles} that appear once.")
        return 0
    print(f"ONLY {ge2} predicate(s) have two or more rows, below "
          f"MIN_CLUSTERABLE={MIN_CLUSTERABLE}.")
    print("Cluster-by-usage has almost nothing to cluster on this corpus. "
          "That is a finding about the store, not a defect: record it in "
          "OQ-2 and wait for a corpus that has recurrence, exactly as V.2 "
          "concluded about widening the vocabulary.")
    print("It says nothing about MAP-TO-KNOWN, which works on the "
          "single-row bulk and is what predicate_similarity.py measures.")
    return 1


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "kb.db")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()
    print(store_line(args.db), flush=True)              # law 15: the store first (W4 part 4)

    print(__doc__.split("USAGE")[0].strip())
    print("=" * 72)
    if not args.db.is_file():
        print(f"no store at {args.db}")
        return 2

    before = _md5(args.db)
    print(f"store md5 before : {before}")
    tmp = tempfile.mkdtemp(prefix="nexus_preddist_")
    try:
        copy = Path(tmp) / f"dist_{args.db.name}"
        shutil.copy2(args.db, copy)
        rows = _rows(copy)
    finally:
        # Nothing holds this open — no TripleStore is constructed here — but
        # the family's rule is one rule: close, then remove.
        shutil.rmtree(tmp, ignore_errors=True)
    after = _md5(args.db)
    print(f"store md5 after  : {after}  "
          f"{'UNCHANGED' if after == before else '*** MUTATED ***'}\n")
    if after != before:
        return 4
    return report(rows, top=args.top)


if __name__ == "__main__":
    raise SystemExit(main())
