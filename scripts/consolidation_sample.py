"""AH step 1 — draw the labelled sample. READ ONLY, and it proves it.

Emits `consolidation_sample.json`: edges stratified ACROSS the counter
space — well below the gate, just below, fire-passing-but-recurrence-
failing, just above, well above — because the separation question needs
both populations, not the survivors. Each entry carries the two terms plus
enough store context to judge (claims each term appears in, its degree in
the association graph, its co-activation involvement), and an empty
`label` field.

THE OPERATOR LABELS, NOT THE MACHINERY UNDER TEST. Fill each `label` with
    genuine      a real domain association
    scaffolding  session/query debris
    unsure       leave in; the scorer excludes it and reports how many
then run scripts/consolidation_separation.py against the labelled file.
The file's md5 is printed here and again by the scorer, so "the sample was
fixed before any candidate was scored" is checkable rather than asserted.

    python scripts/consolidation_sample.py [--db data/kb.db] [--per-stratum 12]
                                           [--out consolidation_sample.json]
                                           [--seed 7]

The store is opened `mode=ro` and its file md5 is printed before and
after — the read-only sentinel, held by the probe itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402

# The relations the substrate mints for itself. Kept out of the "claims this
# term appears in" context, because showing an operator co_activated rows to
# judge whether an edge is co-activation debris would be circular.
SUBSTRATE_RELATIONS = ("co_activated", "recurring_query_term")

# The gate under test (neocortex.py: DEFAULT thresholds 7 and 3).
GATE_FIRE, GATE_REC = 7, 3

# Stratum -> SQL predicate over the counters. Spanning the space is the
# point: A/B cannot pass the gate, C passes intensity but not recurrence,
# D barely passes, E passes comfortably.
STRATA = {
    "A_well_below":   f"fire_count <= 2",
    "B_just_below":   f"fire_count BETWEEN 3 AND {GATE_FIRE - 1}",
    "C_fire_only":    f"fire_count >= {GATE_FIRE} AND recurrence_count < {GATE_REC}",
    "D_just_above":   (f"fire_count BETWEEN {GATE_FIRE} AND 12 "
                       f"AND recurrence_count >= {GATE_REC}"),
    "E_well_above":   f"fire_count > 12 AND recurrence_count >= {GATE_REC}",
}


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _claims_for(conn, term: str, limit: int = 3) -> list[str]:
    ph = ",".join("?" for _ in SUBSTRATE_RELATIONS)
    rows = conn.execute(
        f"SELECT subject, relation, object, source FROM triples "
        f"WHERE archived = 0 AND relation NOT IN ({ph}) "
        f"AND (subject = ? OR object = ?) LIMIT ?",
        (*SUBSTRATE_RELATIONS, term, term, limit)).fetchall()
    return [f"({r['subject']} {r['relation']} {r['object']}) [{r['source']}]"
            for r in rows]


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/kb.db")
    ap.add_argument("--per-stratum", type=int, default=12)
    ap.add_argument("--out", default="consolidation_sample.json")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    print(store_line(args.db), flush=True)              # law 15: the store first (W4 part 4)

    db = Path(args.db)
    if not db.is_file():
        print(f"MISSING: {db}")
        return 2
    md5_before = _md5(db)
    conn = open_read_only(db, row_factory=None)
    conn.row_factory = sqlite3.Row
    rng = random.Random(args.seed)

    total = conn.execute("SELECT COUNT(*) n FROM hebbian_edges").fetchone()["n"]
    print(f"hebbian_edges: {total} rows   (store md5 {md5_before})")
    if total == 0:
        print("nothing to sample — the association graph is empty here")
        conn.close()
        return 2

    # Endpoint degree, once — context for judging hub-ness.
    degree: dict[str, int] = {}
    for r in conn.execute(
            "SELECT source AS t, COUNT(*) AS n FROM hebbian_edges GROUP BY source"):
        degree[r["t"]] = degree.get(r["t"], 0) + int(r["n"])
    for r in conn.execute(
            "SELECT target AS t, COUNT(*) AS n FROM hebbian_edges GROUP BY target"):
        degree[r["t"]] = degree.get(r["t"], 0) + int(r["n"])

    sample, populations = [], {}
    for name, pred in STRATA.items():
        rows = conn.execute(
            f"SELECT source, target, weight, fire_count, recurrence_count, "
            f"last_seen, demoted FROM hebbian_edges WHERE {pred} "
            f"ORDER BY source, target").fetchall()
        populations[name] = len(rows)
        take = rng.sample(rows, min(args.per_stratum, len(rows)))
        for r in take:
            sample.append({
                "source": r["source"],
                "target": r["target"],
                "fire_count": int(r["fire_count"]),
                "recurrence_count": int(r["recurrence_count"]),
                "weight": round(float(r["weight"]), 4),
                "demoted": bool(r["demoted"]),
                "stratum": name,
                "context": {
                    "source_degree": degree.get(r["source"], 0),
                    "target_degree": degree.get(r["target"], 0),
                    "source_in_claims": _claims_for(conn, r["source"]),
                    "target_in_claims": _claims_for(conn, r["target"]),
                },
                "label": "",
            })
    conn.close()

    print("stratum populations (the sample size the store can support):")
    for name, n in populations.items():
        got = sum(1 for s in sample if s["stratum"] == name)
        print(f"  {name:<14} population {n:6d}   sampled {got}")
    thin = [n for n, p in populations.items() if p < args.per_stratum]
    if thin:
        print(f"  THIN STRATA {thin} — the report must say whether what "
              f"remains is enough to conclude anything")

    out = Path(args.out)
    out.write_text(json.dumps({
        "store": str(db), "store_md5": md5_before, "seed": args.seed,
        "gate": {"fire": GATE_FIRE, "recurrence": GATE_REC},
        "populations": populations,
        "edges": sample,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {out} ({len(sample)} edges)   md5 {_md5(out)}")
    print('label each edge "genuine" | "scaffolding" | "unsure", then run '
          "scripts/consolidation_separation.py")

    md5_after = _md5(db)
    print(f"store md5 after: {md5_after}   "
          f"{'UNCHANGED' if md5_after == md5_before else '*** CHANGED ***'}")
    return 0 if md5_after == md5_before else 3


if __name__ == "__main__":
    sys.exit(main())
