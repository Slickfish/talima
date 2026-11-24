"""Does consolidation eat its own output? READ ONLY.

The third account from the consolidation-content investigation, made
measurable: the gate's counters are fed by the system's own machinery —
retrieval re-ingests consolidated rows as edges, and term seeding mints a
bare node for every query word — so a promoted edge's endpoints may trace
to the mechanism's own prior output rather than to any claim.

For every edge that passes the gate (fire_count >= 7 AND
recurrence_count >= 3 by default), each ENDPOINT is classified by where
its surface appears in the belief store:

    claim      subject or object of at least one live non-substrate triple
               (a real claim; the charitable class wins when a term appears
               in both claim and substrate rows)
    substrate  appears ONLY in substrate rows (co_activated /
               recurring_query_term) — i.e. traces to previously
               consolidated output
    seeded     appears in NO triple at all. The only node-minting path
               besides triple ingestion is assemble_context's term seeding
               (upsert_node per query word), so these are bare query terms.

Edges are then bucketed by their endpoint pair (claim~claim, claim~seeded,
substrate~substrate, ...), with counts, proportions and printed EXAMPLES
per bucket — the rows, not just a percentage. Also reported: how many
promoted edges are themselves already a co_activated row in the store
(re-promotions of prior output, the loop closing exactly).

    python scripts/consolidation_feedback.py [--db data/kb.db]
                                             [--fire 7] [--rec 3]
                                             [--examples 5]

The store is opened mode=ro and its file md5 is printed before and after —
the read-only sentinel, held by the probe itself (exit 3 if it moved).
"""
from __future__ import annotations

import argparse
import hashlib
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

SUBSTRATE_RELATIONS = ("co_activated", "recurring_query_term")


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/kb.db")
    ap.add_argument("--fire", type=int, default=7)
    ap.add_argument("--rec", type=int, default=3)
    ap.add_argument("--examples", type=int, default=5)
    args = ap.parse_args()

    db = Path(args.db)
    if not db.is_file():
        print(f"MISSING: {db}")
        return 2
    md5_before = _md5(db)
    # Law 15's first line through the one formatter (W4's verifier, V3: the
    # script's own line printed a native path, which on Windows is not the
    # store_label spelling the first-line test holds every script to).
    print(store_line(db), flush=True)
    print(f"store: {db}   md5 {md5_before}")
    conn = open_read_only(db, row_factory=None)
    conn.row_factory = sqlite3.Row

    if conn.execute("SELECT name FROM sqlite_master "
                    "WHERE name='hebbian_edges'").fetchone() is None:
        print("no hebbian_edges table — this store predates E.3 or holds "
              "no association graph; nothing to classify")
        conn.close()
        return 2

    # Every surface, split claim vs substrate, in two passes over triples.
    ph = ",".join("?" for _ in SUBSTRATE_RELATIONS)
    claim_terms: set[str] = set()
    substrate_terms: set[str] = set()
    for r in conn.execute(
            f"SELECT subject, object, relation FROM triples "
            f"WHERE archived = 0"):
        bucket = (substrate_terms if r["relation"] in SUBSTRATE_RELATIONS
                  else claim_terms)
        bucket.add(r["subject"])
        bucket.add(r["object"])
    consolidated_pairs = {
        (r["subject"], r["object"]) for r in conn.execute(
            f"SELECT subject, object FROM triples "
            f"WHERE relation IN ({ph})", SUBSTRATE_RELATIONS)}

    def classify(term: str) -> str:
        if term in claim_terms:
            return "claim"           # charitable: claim wins a tie
        if term in substrate_terms:
            return "substrate"
        return "seeded"

    total_edges = conn.execute(
        "SELECT COUNT(*) n FROM hebbian_edges").fetchone()["n"]
    promoted = conn.execute(
        "SELECT source, target, weight, fire_count, recurrence_count "
        "FROM hebbian_edges WHERE fire_count >= ? AND recurrence_count >= ? "
        "ORDER BY fire_count DESC",
        (args.fire, args.rec)).fetchall()
    print(f"hebbian_edges: {total_edges} total, "
          f"{len(promoted)} pass the gate (fire>={args.fire} & "
          f"rec>={args.rec})")
    if not promoted:
        print("no edge passes the gate — nothing to classify")
        conn.close()
        return 0

    buckets: dict[tuple[str, str], list] = defaultdict(list)
    endpoint_tally: dict[str, int] = defaultdict(int)
    repromotions = 0
    for e in promoted:
        cs, ct = classify(e["source"]), classify(e["target"])
        endpoint_tally[cs] += 1
        endpoint_tally[ct] += 1
        buckets[tuple(sorted((cs, ct)))].append(e)
        if (e["source"], e["target"]) in consolidated_pairs \
                or (e["target"], e["source"]) in consolidated_pairs:
            repromotions += 1

    n = len(promoted)
    print("\n== endpoint origins (2 per edge) ==")
    for k in ("claim", "substrate", "seeded"):
        c = endpoint_tally.get(k, 0)
        print(f"  {k:<10} {c:6d}  ({c / (2 * n):6.1%})")

    print("\n== promoted edges by endpoint pair ==")
    order = sorted(buckets.items(), key=lambda kv: -len(kv[1]))
    for (a, b), edges in order:
        print(f"\n  {a} ~ {b}: {len(edges)}  ({len(edges) / n:6.1%})")
        for e in edges[:args.examples]:
            print(f"      {e['source']!r} ~ {e['target']!r}   "
                  f"fire={e['fire_count']} rec={e['recurrence_count']} "
                  f"w={e['weight']:.2f}")

    loop_edges = n - len(buckets.get(("claim", "claim"), []))
    print(f"\n== the loop, summarised ==")
    print(f"  edges with BOTH endpoints on claim surfaces : "
          f"{len(buckets.get(('claim', 'claim'), []))} "
          f"({len(buckets.get(('claim', 'claim'), [])) / n:6.1%})")
    print(f"  edges with at least one non-claim endpoint  : "
          f"{loop_edges} ({loop_edges / n:6.1%})")
    print(f"  edges that ARE already a consolidated row   : "
          f"{repromotions} ({repromotions / n:6.1%})  <- re-promotions of "
          f"the mechanism's own prior output")

    conn.close()
    md5_after = _md5(db)
    print(f"\nstore md5 before {md5_before}  after {md5_after}  "
          f"{'UNCHANGED' if md5_after == md5_before else '*** CHANGED ***'}")
    return 0 if md5_after == md5_before else 3


if __name__ == "__main__":
    sys.exit(main())
