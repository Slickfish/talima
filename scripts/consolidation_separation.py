"""AH steps 2-4 — can recurrence separate at all? READ ONLY.

Scores every candidate signal against the SAME labelled sample, the one
`consolidation_sample.py` drew and the operator labelled. The primary
output is separation — do the genuine and scaffolding populations divide
on the counters AT ALL — reported before any threshold talk, because if
the distributions overlap substantially no threshold saves the gate.

    python scripts/consolidation_separation.py consolidation_sample.json
                                              [--db data/kb.db]

WHAT IT REFUSES, AND WHY
  - a sample where any edge is unlabelled -> the sample was not fixed
    before scoring ("unsure" is a label; blank is not);
  - a sample that is all one class -> every candidate scores perfectly
    and the run means nothing (the null-citation fixture lesson).

WHAT IT FLAGS ON EVERY LINE IT APPLIES TO (the measuring-itself defect,
caught twice in this project): any threshold FITTED on this sample is
marked FITTED — a ceiling, not an estimate; it needs a held-out sample
before it is a number anyone quotes.

CROSS-SESSION BREADTH IS UNTESTABLE RETROACTIVELY and is declared rather
than approximated: hebbian_edges persists only the LATEST counters and two
timestamps under a MAX() upsert — no session ids, no per-episode history —
so "does the pair recur across distinct sessions" cannot be asked of data
that only ever kept the running total. recurrence_count is itself the
nearest recorded proxy (episodes >= 12h apart), and it is already one of
the counters under test.

Positive class = genuine. precision = genuine admitted / admitted;
recall = genuine admitted / all genuine. The random null admits the same
proportion as the current gate, 1000 seeded draws.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
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

SUBSTRATE_RELATIONS = ("co_activated", "recurring_query_term")
GATE_FIRE, GATE_REC = 7, 3


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def quartiles(xs: list[float]) -> str:
    if not xs:
        return "-"
    s = sorted(xs)
    q = lambda p: s[min(len(s) - 1, int(p * (len(s) - 1)))]
    return (f"min {s[0]:g}  q1 {q(.25):g}  med {q(.5):g}  "
            f"q3 {q(.75):g}  max {s[-1]:g}")


def auc(pos: list[float], neg: list[float]) -> float:
    """Mann-Whitney AUC: P(genuine > scaffolding) + half-ties. 0.5 = the
    counter cannot tell the classes apart; 1.0 = clean separation."""
    if not pos or not neg:
        return float("nan")
    wins = ties = 0
    for a in pos:
        for b in neg:
            wins += a > b
            ties += a == b
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def pr(edges, admitted: set) -> tuple[float, float, int]:
    genuine = {i for i, e in enumerate(edges) if e["label"] == "genuine"}
    tp = len(genuine & admitted)
    precision = tp / len(admitted) if admitted else float("nan")
    recall = tp / len(genuine) if genuine else float("nan")
    return precision, recall, len(admitted)


def best_threshold(edges, value, *, admit_below=False):
    """The (threshold, F1) maximising F1 on THIS sample — fitted, and every
    caller prints it flagged as such."""
    vals = sorted({value(e) for e in edges})
    best = (None, -1.0, set())
    for t in vals:
        admitted = {i for i, e in enumerate(edges)
                    if (value(e) <= t if admit_below else value(e) >= t)}
        p, r, _ = pr(edges, admitted)
        f1 = 0.0 if (math.isnan(p) or p + r == 0) else 2 * p * r / (p + r)
        if f1 > best[1]:
            best = (t, f1, admitted)
    return best


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sample")
    ap.add_argument("--db", default="data/kb.db")
    args = ap.parse_args()
    print(store_line(args.db), flush=True)              # law 15: the store first (W4 part 4)

    sample_path = Path(args.sample)
    if not sample_path.is_file():
        print(f"MISSING: {sample_path}")
        return 2
    print(f"sample: {sample_path}   md5 {_md5(sample_path)}   "
          f"(fixed before scoring — compare against the sampler's print)")
    doc = json.loads(sample_path.read_text(encoding="utf-8"))
    edges = doc["edges"]

    blank = [e for e in edges if not (e.get("label") or "").strip()]
    if blank:
        print(f"REFUSED: {len(blank)} edges are unlabelled (first: "
              f"{blank[0]['source']!r} ~ {blank[0]['target']!r}). The sample "
              f"must be fixed and fully labelled before any candidate is "
              f"scored; 'unsure' is a label, blank is not.")
        return 4
    unsure = [e for e in edges if e["label"] == "unsure"]
    edges = [e for e in edges if e["label"] in ("genuine", "scaffolding")]
    n_gen = sum(1 for e in edges if e["label"] == "genuine")
    n_sca = len(edges) - n_gen
    print(f"labelled: {len(edges)} scored ({n_gen} genuine, {n_sca} "
          f"scaffolding), {len(unsure)} unsure excluded")
    if n_gen == 0 or n_sca == 0:
        print("REFUSED: the sample holds only one population — every "
              "candidate would score perfectly and the run would mean "
              "nothing (non-vacuity sentinel).")
        return 4
    if min(n_gen, n_sca) < 5:
        print(f"WARNING: only {min(n_gen, n_sca)} in the smaller class — "
              f"treat every number below as indicative, not established.")

    db = Path(args.db)
    if not db.is_file():
        print(f"MISSING: {db}")
        return 2
    md5_before = _md5(db)
    conn = open_read_only(db, row_factory=None)
    conn.row_factory = sqlite3.Row

    # ---- step 2: SEPARATION, before any threshold talk -------------------
    print("\n== STEP 2 — do the populations separate on the counters? ==")
    for counter in ("fire_count", "recurrence_count"):
        pos = [float(e[counter]) for e in edges if e["label"] == "genuine"]
        neg = [float(e[counter]) for e in edges if e["label"] == "scaffolding"]
        print(f"  {counter}:")
        print(f"    genuine      {quartiles(pos)}")
        print(f"    scaffolding  {quartiles(neg)}")
        print(f"    AUC {auc(pos, neg):.3f}   "
              f"(0.5 = no line exists; 1.0 = clean separation)")

    # ---- store-side structural signals -----------------------------------
    ph = ",".join("?" for _ in SUBSTRATE_RELATIONS)

    def in_claims(term: str) -> bool:
        return conn.execute(
            f"SELECT 1 FROM triples WHERE archived = 0 AND relation NOT IN "
            f"({ph}) AND (subject = ? OR object = ?) LIMIT 1",
            (*SUBSTRATE_RELATIONS, term, term)).fetchone() is not None

    def pair_in_claim(a: str, b: str) -> bool:
        return conn.execute(
            f"SELECT 1 FROM triples WHERE archived = 0 AND relation NOT IN "
            f"({ph}) AND ((subject = ? AND object = ?) OR "
            f"(subject = ? AND object = ?)) LIMIT 1",
            (*SUBSTRATE_RELATIONS, a, b, b, a)).fetchone() is not None

    # Surfaces of beliefs cited per lineage record, for pair co-citation.
    cited_surfaces_per_turn: list[set[str]] = []
    try:
        for r in conn.execute("SELECT injected_json FROM lineage_records"):
            surfaces: set[str] = set()
            for item in json.loads(r["injected_json"] or "[]"):
                if not (isinstance(item, dict) and item.get("cited") is True):
                    continue
                row = conn.execute(
                    "SELECT subject, object FROM triples WHERE id = ?",
                    (item.get("belief_id"),)).fetchone()
                if row:
                    surfaces.update((row["subject"], row["object"]))
            if surfaces:
                cited_surfaces_per_turn.append(surfaces)
    except sqlite3.Error as e:
        print(f"  (lineage unavailable for co-citation: {e})")

    def pair_co_cited(a: str, b: str) -> bool:
        return any(a in s and b in s for s in cited_surfaces_per_turn)

    # Endpoint fire mass for PMI, and degree.
    mass: dict[str, float] = {}
    degree: dict[str, int] = {}
    total_fire = 0.0
    for r in conn.execute(
            "SELECT source, target, fire_count FROM hebbian_edges"):
        f = float(r["fire_count"])
        total_fire += f
        for t in (r["source"], r["target"]):
            mass[t] = mass.get(t, 0.0) + f
            degree[t] = degree.get(t, 0) + 1

    def pmi(e) -> float:
        fa, fb = mass.get(e["source"], 0.0), mass.get(e["target"], 0.0)
        if not (fa and fb and e["fire_count"] and total_fire):
            return float("-inf")
        return math.log2(e["fire_count"] * total_fire / (fa * fb))

    # ---- steps 3-4: every candidate, same sample, same measure -----------
    print("\n== STEPS 3-4 — every candidate, same sample, same measure ==")
    print(f"  {'candidate':<34} {'admitted':>8} {'precision':>9} "
          f"{'recall':>7}   note")

    def report(name, admitted: set, note=""):
        p, r, n = pr(edges, admitted)
        print(f"  {name:<34} {n:>8} {p:>9.3f} {r:>7.3f}   {note}")
        return p, r

    gate_admitted = {i for i, e in enumerate(edges)
                     if e["fire_count"] >= GATE_FIRE
                     and e["recurrence_count"] >= GATE_REC}
    report(f"current gate (fire>={GATE_FIRE} & rec>={GATE_REC})",
           gate_admitted)

    for label, key in (("fire_count alone", "fire_count"),
                       ("recurrence_count alone", "recurrence_count"),
                       ("edge weight", "weight")):
        t, _f1, admitted = best_threshold(edges, lambda e, k=key: float(e[k]))
        report(f"{label} >= {t:g}", admitted,
               "FITTED on this sample — ceiling, not estimate")

    t, _f1, admitted = best_threshold(
        edges, lambda e: float(max(e["context"]["source_degree"],
                                   e["context"]["target_degree"])),
        admit_below=True)
    report(f"max endpoint degree <= {t:g}", admitted,
           "FITTED on this sample — ceiling, not estimate")

    t, _f1, admitted = best_threshold(edges, pmi)
    report(f"PMI >= {t:.2f}", admitted,
           "FITTED on this sample — ceiling, not estimate")

    report("either term in a claim",
           {i for i, e in enumerate(edges)
            if in_claims(e["source"]) or in_claims(e["target"])})
    report("both terms in claims",
           {i for i, e in enumerate(edges)
            if in_claims(e["source"]) and in_claims(e["target"])})
    report("pair IS a claim (s,o)",
           {i for i, e in enumerate(edges)
            if pair_in_claim(e["source"], e["target"])})
    report("pair co-cited in a turn",
           {i for i, e in enumerate(edges)
            if pair_co_cited(e["source"], e["target"])},
           f"over {len(cited_surfaces_per_turn)} cited turns")

    # The null: admit the gate's proportion at random, 1000 seeded draws.
    prop = len(gate_admitted) / len(edges) if edges else 0.0
    rng = random.Random(7)
    ps, rs = [], []
    for _ in range(1000):
        admitted = {i for i in range(len(edges)) if rng.random() < prop}
        p, r, _n = pr(edges, admitted)
        if not math.isnan(p):
            ps.append(p)
        rs.append(r)
    base = n_gen / len(edges)
    print(f"  {'random @ gate proportion':<34} {'-':>8} "
          f"{(sum(ps) / len(ps) if ps else float('nan')):>9.3f} "
          f"{(sum(rs) / len(rs)):>7.3f}   null; precision -> base rate "
          f"{base:.3f} — a signal below this is not a signal")

    print("\n  cross-session breadth: UNTESTABLE RETROACTIVELY — "
          "hebbian_edges keeps only latest counters and two timestamps "
          "under a MAX() upsert; no session ids, no per-episode history. "
          "recurrence_count is the nearest recorded proxy and is already "
          "under test above.")

    conn.close()
    md5_after = _md5(db)
    print(f"\nstore md5 before {md5_before}  after {md5_after}  "
          f"{'UNCHANGED' if md5_after == md5_before else '*** CHANGED ***'}")
    return 0 if md5_after == md5_before else 3


if __name__ == "__main__":
    sys.exit(main())
