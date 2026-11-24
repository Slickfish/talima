"""INVESTIGATE — can "how well known" be DERIVED for a concept? Read-only.

    python scripts/epistemic_derivation_probe.py [--kb data/kb.db] [--top 20]
        [--out epistemic_derivation.json]

Curiosity scores `in_degree × (1 − evidential weight)` over nodes in a
curious epistemic state. Nothing in the backend writes a node's state or
weight after creation and neither is persisted, so in production the second
factor is a constant and the state is the node's creation path. This probe
asks what the STORE already records about a concept that could stand in for
"how well known", and prints what each candidate can actually distinguish.

  [1] the concept population: every subject/object surface of a live claim
      row (substrate relations excluded and counted apart), and what the
      persisted graph holds beside it — nodes that are not claim concepts
      (query terms, co-firings) and claim concepts that are not nodes.
  [2] three candidates, per concept, each from gate-admitted or sealed rows
      and nothing else:
        A  evidence class — the slowest-decaying SOURCE_CLASS among the
           concept's claims (asserted > external > … > extraction);
        B  citation history — injected / cited counts over SEALED lineage
           records, read by the rule `sealed_cited_belief_ids` uses;
        C  claim count — live claim rows naming the concept.
      For each: the distribution, the share of concepts it moves off its
      default, and examples at both ends.
  [3] whether the candidates are the same thing as connectivity: Pearson of
      each against the rehydrated graph's in_degree, and against each other.
      A candidate that tracks degree adds nothing to a degree score.
  [4] the current curiosity top-N on the rehydrated graph, annotated with
      all three — what each candidate would say about what curiosity
      surfaces today.
  [5] cost: wall time of the derivation over this store, and the row counts
      it scales with.

Nothing is written but the report: the store is opened mode=ro (md5 before
and after), the graph is rehydrated in memory only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._as import pearson                                  # noqa: E402
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "-"


def _bucket(n: int) -> str:
    return "1" if n <= 1 else "2" if n == 2 else "3-5" if n <= 5 else "6-20" if n <= 20 else "21+"


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default="epistemic_derivation.json")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    from backend.cortex.curiosity import CuriosityEngine
    from backend.kb.schema import SUBSTRATE_RELATIONS
    from backend.kb.tiered_store import CLASS_ORDER, classify
    from backend.memory.graph_store import HebbianGraphStore
    from backend.memory.hippocampus import Hippocampus

    kb = Path(args.kb)
    if not kb.is_file():
        print(f"MISSING: {kb}")
        return 2
    md5_before = _md5(kb)
    con = open_read_only(kb, row_factory=None)
    con.row_factory = sqlite3.Row
    tables = {r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    report: dict = {"kb": str(kb)}
    t0 = time.perf_counter()

    # ---- [1] the concept population -------------------------------------------
    rows = con.execute(
        "SELECT id, subject, relation, object, source, archived FROM triples").fetchall()
    live = [r for r in rows if not r["archived"]]
    claims = [r for r in live if r["relation"] not in SUBSTRATE_RELATIONS]
    substrate = [r for r in live if r["relation"] in SUBSTRATE_RELATIONS]
    concepts: set[str] = set()
    for r in claims:
        concepts.add(r["subject"]); concepts.add(r["object"])
    rank = {c: i for i, c in enumerate(CLASS_ORDER)}     # 0 = slowest = strongest

    # ---- [2] the candidates ---------------------------------------------------
    count: Counter = Counter()
    by_class: dict[str, Counter] = defaultdict(Counter)
    belief_concepts: dict[int, tuple[str, str]] = {}
    for r in claims:
        cls = classify(r["source"])
        belief_concepts[r["id"]] = (r["subject"], r["object"])
        for c in (r["subject"], r["object"]):
            count[c] += 1
            by_class[c][cls] += 1
    best_class = {c: min(by_class[c], key=lambda k: rank.get(k, 99)) for c in concepts}

    injected: Counter = Counter()
    cited: Counter = Counter()
    lineage_rows = sealed = 0
    if "lineage_records" in tables:
        for row in con.execute("SELECT injected_json, sealed_block FROM lineage_records"):
            lineage_rows += 1
            if row["sealed_block"] is None:
                continue          # unsealed confers nothing (lineage.py rule)
            sealed += 1
            try:
                items = json.loads(row["injected_json"] or "[]")
            except (TypeError, ValueError):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                so = belief_concepts.get(it.get("belief_id"))
                if not so:
                    continue
                flag = it.get("cited", None)
                for c in so:
                    injected[c] += 1
                    if flag is None or flag:      # null counts as cited (F.5 rule)
                        cited[c] += 1
    derive_ms = (time.perf_counter() - t0) * 1000.0

    n = len(concepts)
    class_dist = Counter(best_class.values())
    asserted = [c for c in concepts if best_class[c] == "asserted"]
    external = [c for c in concepts if best_class[c] == "external"]
    extraction_only = [c for c in concepts if best_class[c] == "extraction"]
    ever_injected = [c for c in concepts if injected[c] > 0]
    ever_cited = [c for c in concepts if cited[c] > 0]
    injected_never_cited = [c for c in ever_injected if cited[c] == 0]
    count_dist = Counter(_bucket(count[c]) for c in concepts)
    multi = [c for c in concepts if count[c] > 1]

    def ex(items, key, k=None, reverse=True):
        k = k or args.top
        return [(c, key(c)) for c in sorted(items, key=key, reverse=reverse)[:k]]

    report["population"] = {
        "triples": len(rows), "live": len(live), "claims": len(claims),
        "substrate_rows": len(substrate), "concepts": n,
        "lineage_records": lineage_rows, "sealed": sealed,
    }
    report["A_evidence_class"] = {
        "distribution": dict(class_dist),
        "concepts_with_an_asserted_claim": len(asserted),
        "share_off_default": round(1 - len(extraction_only) / n, 4) if n else None,
        "examples_asserted": ex(asserted, lambda c: count[c]),
        "examples_external": ex(external, lambda c: count[c]),
        "examples_extraction_only_high_degree": ex(extraction_only, lambda c: count[c]),
    }
    report["B_citation_history"] = {
        "concepts_ever_injected": len(ever_injected),
        "concepts_ever_cited": len(ever_cited),
        "concepts_injected_never_cited": len(injected_never_cited),
        "share_off_default": round(len(ever_injected) / n, 4) if n else None,
        "examples_most_cited": ex(ever_cited, lambda c: (cited[c], injected[c])),
        "examples_injected_never_cited": ex(injected_never_cited, lambda c: injected[c]),
        "cited_rate_by_class": {
            cls: round(sum(cited[c] for c in concepts if best_class[c] == cls)
                       / max(1, sum(injected[c] for c in concepts if best_class[c] == cls)), 3)
            for cls in class_dist},
    }
    report["C_claim_count"] = {
        "distribution": dict(count_dist),
        "concepts_in_one_claim": count_dist.get("1", 0),
        "share_off_default": round(len(multi) / n, 4) if n else None,
        "examples_top": ex(concepts, lambda c: count[c]),
    }
    print(f"[1] triples {len(rows)} live {len(live)} claims {len(claims)} "
          f"substrate {len(substrate)}; concepts {n}; lineage {lineage_rows} "
          f"(sealed {sealed})")
    print(f"\n[2A] evidence class: {dict(class_dist)}  off-default share "
          f"{report['A_evidence_class']['share_off_default']}")
    for c, k in report["A_evidence_class"]["examples_asserted"][:8]:
        print(f"      asserted   {k:3d} claims  {c!r}")
    for c, k in report["A_evidence_class"]["examples_extraction_only_high_degree"][:8]:
        print(f"      extraction {k:3d} claims  {c!r}")
    print(f"[2B] citation: injected {len(ever_injected)} cited {len(ever_cited)} "
          f"injected-never-cited {len(injected_never_cited)}  off-default share "
          f"{report['B_citation_history']['share_off_default']}; cited rate by class "
          f"{report['B_citation_history']['cited_rate_by_class']}")
    for c, (k, i) in report["B_citation_history"]["examples_most_cited"][:8]:
        print(f"      cited {k:3d}/{i:3d}  {c!r}")
    for c, i in report["B_citation_history"]["examples_injected_never_cited"][:8]:
        print(f"      never {0:3d}/{i:3d}  {c!r}")
    print(f"[2C] claim count: {dict(count_dist)}  off-default share "
          f"{report['C_claim_count']['share_off_default']}")
    for c, k in report["C_claim_count"]["examples_top"][:8]:
        print(f"      {k:3d}  {c!r}")

    # ---- [3] against connectivity, and each other ------------------------------
    cur: dict = {"available": "hebbian_edges" in tables}
    if cur["available"]:
        gs = HebbianGraphStore.__new__(HebbianGraphStore)
        gs.conn = con
        hippo = Hippocampus()
        loaded = gs.load_into(hippo)
        nodes = hippo.nodes
        states = Counter(hippo.get_node(x).epistemic_state.value for x in nodes)
        confs = Counter(round(float(hippo.get_node(x).confidence), 2) for x in nodes)
        both = sorted(concepts & nodes)
        deg = {c: hippo.in_degree(c) for c in both}
        series = {
            "in_degree": [float(deg[c]) for c in both],
            "claim_count": [float(count[c]) for c in both],
            "class_rank": [float(rank.get(best_class[c], 99)) for c in both],
            "injected": [float(injected[c]) for c in both],
            "cited": [float(cited[c]) for c in both],
        }
        keys = list(series)
        corr = {f"{a}~{b}": pearson(series[a], series[b])
                for i, a in enumerate(keys) for b in keys[i + 1:]}
        curious = CuriosityEngine().find_curious_nodes(hippo, top_k=args.top)
        cur.update({
            "edges_loaded": loaded, "nodes": len(nodes),
            "nodes_not_claim_concepts": len(nodes - concepts),
            "claim_concepts_not_nodes": len(concepts - nodes),
            "examples_nodes_not_claim_concepts": sorted(nodes - concepts)[:args.top],
            "states_after_rehydration": dict(states),
            "confidences_after_rehydration": dict(confs),
            "correlations_over_shared": corr, "shared": len(both),
            "curiosity_top": [{
                "node": c.node_id, "score": c.score, "in_degree": c.in_degree,
                "claim_count": count[c.node_id], "best_class": best_class.get(c.node_id),
                "injected": injected[c.node_id], "cited": cited[c.node_id],
                "is_claim_concept": c.node_id in concepts,
            } for c in curious],
        })
        print(f"\n[3] graph {loaded} edges, {len(nodes)} nodes; states {dict(states)}; "
              f"weights {dict(confs)}; nodes that are not claim concepts "
              f"{len(nodes - concepts)}, claim concepts not in graph {len(concepts - nodes)}")
        print(f"    correlations over {len(both)} shared concepts: {corr}")
        print(f"\n[4] curiosity top-{len(curious)} on the rehydrated graph:")
        print("      score  deg  claims  class        inj  cited  node")
        for c in cur["curiosity_top"]:
            print(f"      {c['score']:5.2f}  {c['in_degree']:3d}  {c['claim_count']:5d}   "
                  f"{str(c['best_class']):11s} {c['injected']:4d}  {c['cited']:4d}  {c['node']!r}")
    report["graph"] = cur
    report["cost"] = {"derivation_ms": round(derive_ms, 2),
                      "scales_with": {"claims": len(claims), "lineage_rows": lineage_rows}}
    print(f"\n[5] derivation {derive_ms:.1f} ms over {len(claims)} claims and "
          f"{lineage_rows} lineage rows")
    con.close()

    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    print(f"\nwrote {args.out}")
    md5_after = _md5(kb)
    print(f"store md5 {md5_before} -> {md5_after} "
          f"{'UNCHANGED' if md5_after == md5_before else '*** CHANGED ***'}")
    return 0 if md5_after == md5_before else 3


if __name__ == "__main__":
    sys.exit(main())
