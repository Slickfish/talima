#!/usr/bin/env python
"""Which predicates does THIS store use that the vocabulary does not offer?

WHY THIS MEASURES RATHER THAN PROPOSES
    The compiled 33 came from unioning what the codebase happened to contain —
    `decompose._HEADS`, `RELATION_ALIASES` targets, `CONTRADICTION_PAIRS`. That
    is a fair default and a poor domain vocabulary: it has no
    `metabolized_by`, no `binds_to`, no `treats`, no dose or temporal
    predicate. V.1 measured the consequence — the model picks from the list
    38% of the time and invents the rest, and it never uses the `other` escape
    hatch, so the 53% miss is invention rather than abstention. **The model
    does not know it is off the list.** Widening the list from real data is
    the lever that remains.

    The obvious way to widen it is to write down thirty plausible predicates.
    That would be a list of my guesses about a store I cannot see. The store
    already contains the evidence: 216 distinct relations over 867 rows, and
    the frequent ones absent from the vocabulary are exactly the candidates.

WHAT IT DOES NOT DO
    It does not decide which to add. The store's relations contain legitimate
    domain predicates AND extraction artefacts in the same list —
    `primarily_metabolized_by` and `is_metabolized_by_primary_enzyme_of` are
    the same claim twice, and only someone who knows the domain can say which
    spelling to canonicalise on. So the output is a RANKED CANDIDATE LIST for
    a human to cut, and `--write` produces a file to edit rather than a
    vocabulary to install.

    Same discipline as `propose_contradiction_pairs.py`, and for the same
    reason: a shared shape is necessary evidence and nowhere near sufficient.

    Read-only on the store. Opens it `mode=ro` and writes nothing to it.

THE WIDENING NEGATIVE — READ THIS BEFORE RUNNING IT EXPECTING A FIX
    This script was built to answer "add the missing predicates and see if the
    number improves". After the V.2 run that lever is recorded as a NEGATIVE,
    for three reasons in order of weight:

    1. Widening cannot create identity, only compliance.
       `primarily_metabolized_by` and `is_metabolized_by_primary_enzyme_of` are
       one claim twice. Put BOTH on the list and the model picks correctly
       twice: pick rate rises, distinct predicates does not fall, and the two
       rows still do not compose. Compliance was never the goal.
    2. The candidates are mostly artefacts. ~1.3 rows per predicate on the
       operator's store means "most frequent off-list predicate" and "used
       once" are nearly the same set — see the singleton count this script
       prints. Widening from here installs the extractor's inventions as
       vocabulary and offers them back to it.
    3. At 20 chunks the answer is unreadable anyway. The V.2 harness run
       measured arm A twice on a byte-identical prompt and got 0.08 then 0.13
       pick, 106 then 92 distinct. A widening effect is that size or smaller.

    The second run measured 0.38 pick — unchanged from V.1's arm B. Nothing
    measured has moved it off 0.38.

    So the script stays and the lever stays unpulled. It remains genuinely
    useful for the case it was really for: a customer arriving WITH a domain
    ontology, where `NEXUS_RELATION_VOCABULARY` is written by someone who knows
    the domain rather than derived from this extractor's output. What is
    settled is narrower — widening from the store's OWN output is not a route
    to predicate identity. That is OQ-2, blocked on F-04/F-05. Full reasoning:
    `OQ-2_PREDICATE_IDENTITY.md`, "The widening negative".

USAGE
    python scripts/propose_relation_vocabulary.py --db data/kb.db
    python scripts/propose_relation_vocabulary.py --db data/kb.db --write relations.json

    Then edit the file, keep the real predicates, and:
        NEXUS_RELATION_VOCABULARY=/path/to/relations.json
    Confirm it took effect at GET /cortex/stats -> relation_vocabulary,
    then re-run scripts/measure_vocabulary_compliance.py to see whether the
    pick rate moved.
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.kb.gate import normalise_relation                     # noqa: E402
from backend.kb.vocabulary import load_relations                   # noqa: E402
from scripts._console import force_utf8_stdio                      # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                  # noqa: E402

# Relations the substrate mints for itself. They are not extraction output and
# offering them to the extractor would be a category error — `co_activated` is
# a statement about two terms having fired together, not a claim anyone made.
SUBSTRATE_RELATIONS = {"co_activated", "recurring_query_term"}


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "kb.db")
    ap.add_argument("--write", type=Path, default=None,
                    help="write the vocabulary plus candidates, as a file to edit")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--min-rows", type=int, default=1,
                    help="only propose predicates covering at least this many rows")
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
        "SELECT relation, source, COUNT(*) AS n FROM triples "
        "WHERE archived = 0 GROUP BY relation, source").fetchall()
    conn.close()

    vocabulary = set(load_relations())
    by_relation: collections.Counter = collections.Counter()
    substrate_rows = 0
    for r in rows:
        rel = normalise_relation(r["relation"])
        if not rel:
            continue
        if rel in SUBSTRATE_RELATIONS:
            substrate_rows += r["n"]
            continue
        by_relation[rel] += r["n"]

    total_rows = sum(by_relation.values())
    on_list = {r: n for r, n in by_relation.items() if r in vocabulary}
    off_list = {r: n for r, n in by_relation.items() if r not in vocabulary}
    covered = sum(on_list.values())

    print(f"\nvocabulary in force        : {len(vocabulary)} relation(s)")
    print(f"substrate-minted rows      : {substrate_rows}  "
          f"({'/'.join(sorted(SUBSTRATE_RELATIONS))}, excluded)")
    print(f"claim rows                 : {total_rows}")
    print(f"distinct predicates        : {len(by_relation)}")
    print(f"...already on the list     : {len(on_list)}  "
          f"covering {covered} row(s) "
          f"({covered / max(1, total_rows):.0%})")
    print(f"...NOT on the list         : {len(off_list)}  "
          f"covering {total_rows - covered} row(s) "
          f"({(total_rows - covered) / max(1, total_rows):.0%})")

    if not off_list:
        print("\nEvery predicate in this store is already offered. Widening "
              "the vocabulary would change nothing; the remaining lever is "
              "OQ-2 (emergent predicate identity), which is blocked on "
              "F-04/F-05.")
        return 0

    ranked = [(rel, n) for rel, n in
              sorted(off_list.items(), key=lambda kv: (-kv[1], kv[0]))
              if n >= args.min_rows]

    print(f"\ntop {min(args.top, len(ranked))} predicates not offered — the "
          f"candidate list, for a human to cut:")
    print(f"{'rows':>5}  predicate")
    for rel, n in ranked[:args.top]:
        print(f"{n:>5}  {rel}")

    singletons = sum(1 for _r, n in ranked if n == 1)
    print(f"\n{singletons} of {len(ranked)} appear exactly once. A predicate "
          f"used once is more likely an extraction artefact than a domain "
          f"term, and the ones worth adding are usually the ones a domain "
          f"expert recognises rather than the ones with the highest count.")

    print("\nREAD THIS BEFORE INSTALLING ANY OF IT: this list contains real "
          "domain predicates and extraction artefacts side by side, and "
          "several spellings of the same claim. Adding a spelling does not "
          "merge it with its siblings — that is OQ-2, and it is blocked on "
          "F-04/F-05. Cut the list by hand, keep the predicates you would "
          "want a colleague to use, and re-measure.")

    if args.write:
        payload = sorted(vocabulary) + [rel for rel, _n in ranked[:args.top]]
        args.write.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"\nwrote {len(payload)} relation(s) to {args.write} "
              f"({len(vocabulary)} in force + {min(args.top, len(ranked))} "
              f"candidates). EDIT IT — it is a starting point, not a "
              f"vocabulary.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
