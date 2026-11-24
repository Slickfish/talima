"""INVESTIGATE — does the claim graph have the shape curiosity would need? Read-only.

    python scripts/claim_graph_probe.py [--kb data/kb.db] [--top 20]
        [--permutations 500] [--out claim_graph.json]

Curiosity scores the ASSOCIATION graph, which on a used store is saturated.
The hypothesis under test is that a partial-connection score over the CLAIM
graph (concepts joined by gate-admitted rows) would discriminate where the
association graph cannot. This probe establishes, from the store alone:

  [1] the claim graph: concepts, edges, components, degree distribution;
      and the association graph beside it — its degree distribution, the
      share of its edges with NO claim behind them, and its nodes that are
      not claim concepts at all (bare query terms, co-firings).
  [2] the partial-connection shape: OPEN TRIADS (a—c—b with no a—b edge),
      counted, with the share sitting on the top hubs and examples printed.
      If the graph is a star forest the shape is everywhere and means
      "neighbour of a hub", which is degree under another name.
  [3] `claim_count ~ cited`: signal or arithmetic. A permutation null that
      shuffles the cited flags WITHIN each lineage record — preserving every
      record's cited count and every concept's injection count — says what
      correlation arithmetic alone produces. Also the correlation with the
      cited RATE, which arithmetic does not inflate.
  [4] two candidate second factors, each tested for who can move it:
      - contradiction: recorded on lineage, and re-scanned over the live
        store with the pairs in force, with the SOURCE CLASS of both sides
        of every pair — so "cannot be reached by extraction alone" is
        measured rather than assumed;
      - injected-repeatedly-never-cited: over EXPLICIT flags only (null is
        the F.5 exemption rule, not evidence of use), split by the
        `citation_test` that produced the flag, since AU changed the
        instrument.
  [5] cost: wall time of degree + open-triad counting over the claim graph,
      the computation a rebuilt curiosity would pay per pass.

Store opened mode=ro; md5 before and after. Nothing is written but the
report.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import random
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

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
    return "0" if n == 0 else "1" if n == 1 else "2" if n == 2 else "3-5" if n <= 5 \
        else "6-20" if n <= 20 else "21-100" if n <= 100 else "101+"


def _components(adj: dict[str, set[str]]) -> list[int]:
    seen: set[str] = set()
    sizes = []
    for start in adj:
        if start in seen:
            continue
        stack, size = [start], 0
        seen.add(start)
        while stack:
            n = stack.pop()
            size += 1
            for m in adj[n]:
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        sizes.append(size)
    return sorted(sizes, reverse=True)


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--permutations", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="claim_graph.json")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    from backend.cortex.contradiction import ContradictionDetector, load_pairs
    from backend.kb.schema import SUBSTRATE_RELATIONS
    from backend.kb.tiered_store import classify

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

    # ---- [1] the two graphs ------------------------------------------------------
    rows = con.execute("SELECT id, subject, relation, object, source, created_at "
                       "FROM triples WHERE archived = 0").fetchall()
    claims = [r for r in rows if r["relation"] not in SUBSTRATE_RELATIONS]
    adj: dict[str, set[str]] = defaultdict(set)
    claim_pairs: set[tuple[str, str]] = set()
    count: Counter = Counter()
    belief_concepts: dict[int, tuple[str, str]] = {}
    for r in claims:
        s, o = r["subject"], r["object"]
        belief_concepts[r["id"]] = (s, o)
        count[s] += 1; count[o] += 1
        if s != o:
            adj[s].add(o); adj[o].add(s)
            claim_pairs.add((s, o)); claim_pairs.add((o, s))
        else:
            adj.setdefault(s, set())
    concepts = set(adj)
    deg = {c: len(adj[c]) for c in concepts}
    comps = _components(adj)
    hubs = sorted(concepts, key=lambda c: -deg[c])[:args.top]

    assoc: dict = {"available": "hebbian_edges" in tables}
    if assoc["available"]:
        edges = con.execute("SELECT source, target, weight, demoted FROM hebbian_edges").fetchall()
        live_edges = [e for e in edges if not e["demoted"]]
        in_deg: Counter = Counter()
        nodes: set[str] = set()
        no_claim = 0
        for e in live_edges:
            in_deg[e["target"]] += 1
            nodes.add(e["source"]); nodes.add(e["target"])
            if (e["source"], e["target"]) not in claim_pairs:
                no_claim += 1
        non_concept = sorted(nodes - concepts, key=lambda n: -in_deg[n])
        n_nodes = len(nodes)
        assoc.update({
            "edges": len(edges), "live_edges": len(live_edges), "nodes": n_nodes,
            "density": round(len(live_edges) / max(1, n_nodes * (n_nodes - 1)), 4),
            "edges_with_no_claim_behind_them": no_claim,
            "share_edges_with_no_claim": round(no_claim / max(1, len(live_edges)), 4),
            "in_degree_distribution": dict(Counter(_bucket(in_deg[n]) for n in nodes)),
            "max_in_degree": max(in_deg.values()) if in_deg else 0,
            "nodes_not_claim_concepts": len(non_concept),
            "examples_nodes_not_claim_concepts": [(n, in_deg[n]) for n in non_concept[:args.top]],
        })
    report["claim_graph"] = {
        "claims": len(claims), "concepts": len(concepts),
        "edges": len(claim_pairs) // 2, "components": len(comps),
        "largest_component": comps[0] if comps else 0,
        "singleton_pairs": sum(1 for s in comps if s == 2),
        "degree_distribution": dict(Counter(_bucket(deg[c]) for c in concepts)),
        "hubs": [(c, deg[c]) for c in hubs],
    }
    report["association_graph"] = assoc
    cg = report["claim_graph"]
    print(f"[1] claim graph: {cg['claims']} claims, {cg['concepts']} concepts, "
          f"{cg['edges']} edges, {cg['components']} components (largest "
          f"{cg['largest_component']}, two-node components {cg['singleton_pairs']}); "
          f"degree {cg['degree_distribution']}")
    print(f"    hubs: {cg['hubs'][:10]}")
    if assoc["available"]:
        print(f"    association graph: {assoc['live_edges']} live edges over {assoc['nodes']} "
              f"nodes, density {assoc['density']}, max in-degree {assoc['max_in_degree']}, "
              f"in-degree {assoc['in_degree_distribution']}")
        print(f"    edges with no claim behind them {assoc['edges_with_no_claim_behind_them']} "
              f"({assoc['share_edges_with_no_claim']}); nodes that are not claim concepts "
              f"{assoc['nodes_not_claim_concepts']}: {assoc['examples_nodes_not_claim_concepts'][:10]}")

    # ---- [2] open triads ---------------------------------------------------------
    t0 = time.perf_counter()
    open_by_centre: Counter = Counter()
    closed_by_centre: Counter = Counter()
    examples: dict[str, list] = {}
    open_pairs: set[frozenset] = set()
    for c in concepts:
        nb = sorted(adj[c])
        if len(nb) < 2:
            continue
        ex = []
        for a, b in itertools.combinations(nb, 2):
            if b in adj[a]:
                closed_by_centre[c] += 1
            else:
                open_by_centre[c] += 1
                open_pairs.add(frozenset((a, b)))
                if len(ex) < 3:
                    ex.append((a, b))
        if ex:
            examples[c] = ex
    triad_ms = (time.perf_counter() - t0) * 1000.0
    total_open = sum(open_by_centre.values())
    total_closed = sum(closed_by_centre.values()) // 3
    top_centres = [c for c, _ in open_by_centre.most_common(args.top)]
    top5_share = (sum(open_by_centre[c] for c in top_centres[:5]) / total_open) if total_open else None
    report["open_triads"] = {
        "open_triads": total_open, "closed_triangles": total_closed,
        "centres_with_an_open_triad": len(open_by_centre),
        "distinct_unlinked_pairs_sharing_a_neighbour": len(open_pairs),
        "share_of_open_triads_on_top5_centres": round(top5_share, 4) if top5_share is not None else None,
        "top_centres": [(c, open_by_centre[c], deg[c]) for c in top_centres],
        "examples": {c: examples[c] for c in top_centres[:args.top] if c in examples},
        "verdict": ("no open triads: the partial-connection shape does not exist here"
                    if not total_open else
                    "open triads exist; see share on hubs — if most sit on a few "
                    "centres, the shape is hub adjacency"),
    }
    ot = report["open_triads"]
    print(f"\n[2] open triads {total_open}, closed triangles {total_closed}, centres "
          f"{ot['centres_with_an_open_triad']}, distinct unlinked pairs {len(open_pairs)}, "
          f"share on top-5 centres {ot['share_of_open_triads_on_top5_centres']}")
    for c, n, d in ot["top_centres"][:10]:
        print(f"      {n:6d} open  deg={d:4d}  centre {c!r}")
        for a, b in examples.get(c, [])[:3]:
            print(f"               {a!r} — {c!r} — {b!r}   (no {a!r}—{b!r} claim)")

    # ---- [3] claim_count ~ cited: signal or arithmetic -------------------------------
    records: list[list[tuple[int, bool | None, str | None]]] = []
    if "lineage_records" in tables:
        for row in con.execute("SELECT injected_json, citation_test FROM lineage_records"):
            try:
                items = json.loads(row["injected_json"] or "[]")
            except (TypeError, ValueError):
                continue
            rec = [(it.get("belief_id"), it.get("cited", None), row["citation_test"])
                   for it in items if isinstance(it, dict) and it.get("belief_id") in belief_concepts]
            if rec:
                records.append(rec)
    explicit = [[(b, f, v) for b, f, v in rec if f is not None] for rec in records]
    explicit = [rec for rec in explicit if rec]
    null_flags = sum(1 for rec in records for _, f, _ in rec if f is None)

    def per_concept(recs):
        inj: Counter = Counter(); cit: Counter = Counter()
        for rec in recs:
            for b, f, _ in rec:
                for c in belief_concepts[b]:
                    inj[c] += 1
                    if f:
                        cit[c] += 1
        return inj, cit

    inj, cit = per_concept(explicit)
    shared = sorted(c for c in concepts if inj[c] > 0)
    xs = [float(count[c]) for c in shared]
    observed = pearson(xs, [float(cit[c]) for c in shared])
    observed_rate = pearson(xs, [cit[c] / inj[c] for c in shared])
    inj_corr = pearson(xs, [float(inj[c]) for c in shared])
    rng = random.Random(args.seed)
    null_vals = []
    for _ in range(args.permutations if shared else 0):
        shuffled = []
        for rec in explicit:
            flags = [f for _, f, _ in rec]
            rng.shuffle(flags)
            shuffled.append([(b, f, v) for (b, _, v), f in zip(rec, flags)])
        _, cit_n = per_concept(shuffled)
        r = pearson(xs, [float(cit_n[c]) for c in shared])
        if r is not None:
            null_vals.append(r)
    null_vals.sort()
    def pct(p):
        return null_vals[min(len(null_vals) - 1, int(p * len(null_vals)))] if null_vals else None
    report["claim_count_vs_cited"] = {
        "records": len(records), "records_with_explicit_flags": len(explicit),
        "null_flags_excluded": null_flags, "concepts_compared": len(shared),
        "observed_claim_count~cited": observed,
        "observed_claim_count~injected": inj_corr,
        "observed_claim_count~cited_rate": observed_rate,
        "permutation_null": {"n": len(null_vals),
                             "mean": round(statistics.mean(null_vals), 4) if null_vals else None,
                             "p2.5": pct(0.025), "p97.5": pct(0.975)},
        "reading": ("arithmetic: the observed value sits inside the permutation null, "
                    "which shuffles WHO was cited but keeps how many claims each concept "
                    "has and how often it was injected"
                    if observed is not None and null_vals and pct(0.025) <= observed <= pct(0.975)
                    else "outside the null: more than arithmetic is present (or too few rows)"),
    }
    cc = report["claim_count_vs_cited"]
    print(f"\n[3] claim_count~cited observed {observed} | ~injected {inj_corr} | ~cited RATE "
          f"{observed_rate}; permutation null (n={len(null_vals)}) mean {cc['permutation_null']['mean']} "
          f"[{cc['permutation_null']['p2.5']}, {cc['permutation_null']['p97.5']}]  "
          f"→ {cc['reading']}")

    # ---- [4] the two candidate second factors --------------------------------------
    # 4a contradiction — recorded, and re-scanned with source classes
    recorded_pairs: set[tuple[str, str]] = set()
    recorded_records = 0
    if "lineage_records" in tables:
        for row in con.execute("SELECT contradictions_json FROM lineage_records"):
            try:
                cs = json.loads(row["contradictions_json"] or "[]")
            except (TypeError, ValueError):
                continue
            if cs:
                recorded_records += 1
                for c in cs:
                    recorded_pairs.add((c.get("subject"), c.get("object")))
    det = ContradictionDetector(load_pairs())
    # sqlite3.Row has no attributes, and the detector's coercion reads
    # attributes — hand it plain objects so every field is actually seen.
    scanned = det.scan_recent(SimpleNamespace(
        subject=r["subject"], relation=r["relation"], object=r["object"],
        timestamp=r["created_at"], source=r["source"]) for r in claims)
    class_pairs = Counter(tuple(sorted((classify(c.source_a), classify(c.source_b))))
                          for c in scanned)
    extraction_only = sum(1 for c in scanned
                          if classify(c.source_a) == "extraction" == classify(c.source_b))
    contra_concepts = {c.subject for c in scanned} | {c.object for c in scanned}
    report["contradiction"] = {
        "pairs_in_force": len(det.pairs),
        "recorded_on_lineage": {"records": recorded_records, "distinct_subject_object": len(recorded_pairs)},
        "store_scan": {"pairs": len(scanned), "concepts_involved": len(contra_concepts),
                       "by_source_class_pair": {" / ".join(k): v for k, v in class_pairs.items()},
                       "extraction_vs_extraction": extraction_only},
        "examples": [c.to_dict() for c in scanned[:args.top]],
    }
    cd = report["contradiction"]
    print(f"\n[4a] contradiction: {cd['pairs_in_force']} pairs in force; recorded on "
          f"{recorded_records} lineage records ({len(recorded_pairs)} distinct s/o); store "
          f"scan {len(scanned)} pairs over {len(contra_concepts)} concepts, by class "
          f"{cd['store_scan']['by_source_class_pair']}, extraction-vs-extraction {extraction_only}")
    for c in scanned[:8]:
        print(f"      {c.subject!r} {c.relation_a} / {c.relation_b} {c.object!r}  "
              f"[{c.source_a} / {c.source_b}]")

    # 4b injected repeatedly, never cited — explicit flags, by instrument
    inj_by_ver: dict[str, Counter] = defaultdict(Counter)
    for rec in explicit:
        for b, f, v in rec:
            for c in belief_concepts[b]:
                inj_by_ver[v or "f5.1-lexical(NULL)"][c] += 1
    never = {c: inj[c] for c in shared if cit[c] == 0}
    thresholds = {}
    for n in (1, 3, 5, 10):
        thresholds[str(n)] = sum(1 for c, k in never.items() if k >= n)
    only_old = sum(1 for c in never
                   if all(v.startswith("f5.1") for v in inj_by_ver if inj_by_ver[v][c] > 0))
    report["never_cited"] = {
        "concepts_with_explicit_injections": len(shared),
        "never_cited_by_min_injections": thresholds,
        "never_cited_whose_evidence_is_all_pre_AU": only_old,
        "instruments_seen": {v: sum(cnt.values()) for v, cnt in inj_by_ver.items()},
        "examples": sorted(never.items(), key=lambda kv: -kv[1])[:args.top],
    }
    nc = report["never_cited"]
    print(f"\n[4b] never cited (explicit flags): by min injections {thresholds}; "
          f"all-pre-AU evidence {only_old}; instruments {nc['instruments_seen']}")
    for c, k in nc["examples"][:10]:
        print(f"      injected {k:4d}  cited 0  {c!r}")

    # ---- [5] cost ---------------------------------------------------------------------
    t0 = time.perf_counter()
    _ = {c: (deg[c], open_by_centre[c]) for c in concepts}
    score_ms = (time.perf_counter() - t0) * 1000.0
    report["cost"] = {"triad_count_ms": round(triad_ms, 2), "score_pass_ms": round(score_ms, 3),
                      "scales_with": {"claims": len(claims), "concepts": len(concepts),
                                      "sum_deg_choose_2": sum(d * (d - 1) // 2 for d in deg.values())}}
    print(f"\n[5] open-triad count {triad_ms:.1f} ms; the per-pass score over it "
          f"{score_ms:.2f} ms; sum of deg-choose-2 {report['cost']['scales_with']['sum_deg_choose_2']}")
    con.close()

    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False, default=list),
                              encoding="utf-8")
    print(f"\nwrote {args.out}")
    md5_after = _md5(kb)
    print(f"store md5 {md5_before} -> {md5_after} "
          f"{'UNCHANGED' if md5_after == md5_before else '*** CHANGED ***'}")
    return 0 if md5_after == md5_before else 3


if __name__ == "__main__":
    sys.exit(main())
