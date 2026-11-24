"""AX.2 — which pairing exclusion, and what does each lose? Read-only.

    python scripts/pairing_exclusion_probe.py [--kb data/kb.db]
        [--queries questions.txt] [--top 20] [--out pairing_exclusion.json]

AM bounded the Hebbian pair loop to the top-K co-activated nodes. Query-term
seeds fire at 1.00 by construction, so they are always in that top-K: every
turn pairs the question's own words with each other and with everything
retrieved, and the per-turn save persists it. Two exclusions are on the
table and this probe measures both WITHOUT applying either:

  A  exclude seeds (hop-0 nodes) from pairing;
  B  require both nodes of a pair to be claim concepts.

  [1] the persisted graph, classified: every node is C (subject or object
      of a live non-substrate claim) or Q (not — by elimination a query
      token, since nothing else creates a node without a claim); every edge
      is C-C, C-Q or Q-Q. Candidate B is EXACT from the store: drop every
      edge with a Q endpoint. Candidate A is NOT decidable from the store —
      a Q node, once connected, is reachable at hop>0 from any neighbour
      seed and would pair as a non-seed under A — so A is measured only by
      replay ([3]) and the structural fact is reported as the finding.
  [2] does degree discriminate: Pearson of in-degree against claim count
      and against sealed citation count, over shared concepts, for the
      graph as persisted and under B (Q endpoints removed). The projected
      degree distribution under B beside the current one.
  [3] REPLAY, when --queries names a file of questions (one per line): three
      in-memory copies of the rehydrated graph evolve under baseline, A and
      B — each query is retrieved against a COPY of the store, propagated
      with the production walk, its top-K taken with AM's K, and its pairs
      classified; surviving pairs become edges on that copy so later turns
      see what earlier ones formed. Reports per candidate: pairs dropped,
      edges never created, and the projected degree distribution and
      correlations at the end. The questions are not in any record (the
      lineage keeps a hash), so this is the only faithful simulation and
      it needs the operator's questions.
  [4] blast radius, derived by AST over tracked backend files: every module
      that calls an edge or degree API of the association graph, with the
      names it calls — the list of what a pairing change reaches.
  [5] what else the saturation harms: structural reach from claim concepts
      within MAX_HOPS with and without Q nodes, and the share of the
      store's co_activated rows (consolidation's output) with a Q endpoint.

Store opened mode=ro; when replaying, retrieval runs on a temp copy that is
discarded. md5 before and after. Nothing is written but the report.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import itertools
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._as import pearson                                  # noqa: E402
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402

# `current_weight` is deliberately absent: `Triple` has a method of the same
# name (the store's read-time weight), so an attribute-name scan cannot tell
# the two apart. The edge's `current_weight` is read inside `transmit`, which
# IS listed, so no reader of edge weight is missed by leaving it out.
EDGE_APIS = ("outgoing", "edges", "in_degree", "out_degree",
             "transmit", "get_edge", "active_edges", "demoted_edges",
             "predecessors", "successors", "prune_below", "add_edge")


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "-"


def _bucket(n: int) -> str:
    return "0" if n == 0 else "1" if n == 1 else "2" if n == 2 else "3-5" if n <= 5 \
        else "6-20" if n <= 20 else "21-100" if n <= 100 else "101+"


def _dist(deg: dict) -> dict:
    return dict(sorted(Counter(_bucket(v) for v in deg.values()).items()))


def _reach(adj: dict[str, set[str]], starts, hops: int) -> float:
    """Mean number of nodes reachable within `hops` from each start."""
    total = 0
    for s in starts:
        seen, frontier = {s}, {s}
        for _ in range(hops):
            nxt = set()
            for n in frontier:
                nxt |= adj.get(n, set()) - seen
            seen |= nxt
            frontier = nxt
            if not frontier:
                break
        total += len(seen) - 1
    return total / max(1, len(list(starts)))


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--queries", default=None, help="one question per line; enables [3]")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default="pairing_exclusion.json")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    from backend.cortex.propagation import MAX_HOPS
    from backend.kb.schema import SUBSTRATE_RELATIONS
    from tests._tracked import tracked_backend

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

    # ---- [1] classify the persisted graph ---------------------------------------
    rows = con.execute("SELECT id, subject, relation, object FROM triples "
                       "WHERE archived = 0").fetchall()
    claim_count: Counter = Counter()
    belief_concepts: dict[int, tuple[str, str]] = {}
    substrate_pairs: list[tuple[str, str]] = []
    for r in rows:
        if r["relation"] in SUBSTRATE_RELATIONS:
            substrate_pairs.append((r["subject"], r["object"]))
            continue
        belief_concepts[r["id"]] = (r["subject"], r["object"])
        claim_count[r["subject"]] += 1
        claim_count[r["object"]] += 1
    concepts = set(claim_count)

    if "hebbian_edges" not in tables:
        print("no hebbian_edges table — nothing persisted to classify")
        return 2
    edges = [(e["source"], e["target"]) for e in con.execute(
        "SELECT source, target FROM hebbian_edges WHERE demoted = 0")]
    nodes = {n for e in edges for n in e}
    q_nodes = nodes - concepts
    klass = Counter()
    in_deg: Counter = Counter()
    in_deg_b: Counter = Counter()
    adj: dict[str, set[str]] = defaultdict(set)
    adj_b: dict[str, set[str]] = defaultdict(set)
    for s, t in edges:
        k = "C-C" if s in concepts and t in concepts else \
            "Q-Q" if s in q_nodes and t in q_nodes else "C-Q"
        klass[k] += 1
        in_deg[t] += 1
        adj[s].add(t); adj[t].add(s)
        if k == "C-C":
            in_deg_b[t] += 1
            adj_b[s].add(t); adj_b[t].add(s)
    for n in nodes:
        in_deg.setdefault(n, 0)
        if n in concepts:
            in_deg_b.setdefault(n, 0)
    top_q = sorted(q_nodes, key=lambda n: -in_deg[n])[:args.top]
    report["persisted"] = {
        "nodes": len(nodes), "claim_concept_nodes": len(nodes & concepts),
        "q_nodes": len(q_nodes), "edges": len(edges), "edge_classes": dict(klass),
        "share_edges_with_a_q_endpoint": round((klass["C-Q"] + klass["Q-Q"]) / max(1, len(edges)), 4),
        "in_degree_now": _dist(in_deg), "in_degree_under_B": _dist(in_deg_b),
        "max_in_degree_now": max(in_deg.values(), default=0),
        "max_in_degree_under_B": max(in_deg_b.values(), default=0),
        "top_q_nodes": [(n, in_deg[n]) for n in top_q],
        "A_from_store": ("not decidable: a Q node, once connected, is reachable at "
                         "hop>0 and pairs as a non-seed under A; only replay says"),
    }
    p = report["persisted"]
    print(f"[1] persisted: {p['nodes']} nodes ({p['q_nodes']} without a claim), "
          f"{p['edges']} edges {p['edge_classes']}; share with a Q endpoint "
          f"{p['share_edges_with_a_q_endpoint']}")
    print(f"    in-degree now {p['in_degree_now']} (max {p['max_in_degree_now']}); "
          f"under B {p['in_degree_under_B']} (max {p['max_in_degree_under_B']})")
    print(f"    top Q nodes: {p['top_q_nodes'][:10]}")

    # ---- [2] does degree discriminate ------------------------------------------
    cited: Counter = Counter()
    if "lineage_records" in tables:
        for row in con.execute("SELECT injected_json FROM lineage_records "
                               "WHERE sealed_block IS NOT NULL"):
            try:
                items = json.loads(row["injected_json"] or "[]")
            except (TypeError, ValueError):
                continue
            for it in items:
                so = belief_concepts.get(it.get("belief_id")) if isinstance(it, dict) else None
                if so and (it.get("cited", None) is None or it.get("cited")):
                    for c in so:
                        cited[c] += 1

    def corr(deg: dict) -> dict:
        shared = sorted(c for c in concepts if c in deg)
        xs = [float(deg[c]) for c in shared]
        return {"n": len(shared),
                "in_degree~claim_count": pearson(xs, [float(claim_count[c]) for c in shared]),
                "in_degree~cited": pearson(xs, [float(cited[c]) for c in shared])}

    report["discrimination"] = {"now": corr(in_deg), "under_B": corr(in_deg_b)}
    print(f"\n[2] discrimination now {report['discrimination']['now']}; under B "
          f"{report['discrimination']['under_B']}")

    # ---- [3] replay ---------------------------------------------------------------
    replay: dict = {"ran": False}
    if args.queries:
        queries = [q.strip() for q in Path(args.queries).read_text(encoding="utf-8").splitlines()
                   if q.strip()]
        tmp = Path(tempfile.mkdtemp(prefix="nexus_pairing_"))
        copy = tmp / "kb.db"
        shutil.copy(kb, copy)
        try:
            from backend.cortex.edges import SynapticEdge
            from backend.cortex.hebbian import pairing_topk
            from backend.cortex.propagation import propagate
            from backend.kb.retriever import TripleRetriever, extract_terms
            from backend.kb.tiered_store import TripleStore
            from backend.memory.graph_store import HebbianGraphStore
            from backend.memory.hippocampus import Hippocampus

            store = TripleStore(copy)
            retriever = TripleRetriever(store)
            graphs = {}
            for name in ("baseline", "A", "B"):
                h = Hippocampus()
                HebbianGraphStore(copy).load_into(h)
                graphs[name] = h
            k = pairing_topk()
            stats = {name: {"pairs": 0, "pairs_dropped": 0, "edges_never_created": 0}
                     for name in graphs}
            now = 1_700_000_000.0
            for qi, q in enumerate(queries):
                now += 60.0          # clear the transmission refractory between turns
                hits = retriever.retrieve(q)
                for name, h in graphs.items():
                    seeds = []
                    for rt in hits:
                        h.ingest_triple(rt.triple.subject, rt.triple.relation,
                                        rt.triple.object, weight=rt.triple.weight,
                                        restore_weight=rt.triple.relation not in SUBSTRATE_RELATIONS)
                        seeds.append(rt.triple.subject)
                    for term in extract_terms(q):
                        h.upsert_node(term)
                        seeds.append(term)
                    am = propagate(h, list(dict.fromkeys(seeds)), now=now)
                    hop0 = {n for n, hop in am.fired_sequence if hop == 0}
                    fired = [n for n, _ in am.top_k(k)]
                    for a, b in itertools.combinations(fired, 2):
                        if a == b:
                            continue
                        stats[name]["pairs"] += 1
                        drop = ((name == "A" and (a in hop0 or b in hop0))
                                or (name == "B" and (a not in concepts or b not in concepts)))
                        if drop:
                            stats[name]["pairs_dropped"] += 1
                            if h.get_edge(a, b) is None:
                                stats[name]["edges_never_created"] += 1
                            continue
                        if h.get_edge(a, b) is None:
                            h.add_edge(SynapticEdge(source=a, target=b, weight=0.3))
                    h.relax_all()
            store.close()
            for name, h in graphs.items():
                deg = {n: h.in_degree(n) for n in h.nodes}
                stats[name]["in_degree"] = _dist(deg)
                stats[name]["max_in_degree"] = max(deg.values(), default=0)
                stats[name]["nodes"] = len(h.nodes)
                stats[name]["edges"] = sum(1 for _ in h.edges())
                stats[name]["correlations"] = corr(deg)
            replay = {"ran": True, "queries": len(queries), "k": k, **stats}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        print(f"\n[3] replay over {len(queries)} queries at K={k}:")
        for name in ("baseline", "A", "B"):
            s = replay[name]
            print(f"    {name:8s} pairs {s['pairs']:6d} dropped {s['pairs_dropped']:6d} "
                  f"never-created {s['edges_never_created']:6d} | nodes {s['nodes']} edges "
                  f"{s['edges']} max in-degree {s['max_in_degree']} {s['in_degree']} | "
                  f"{s['correlations']}")
    else:
        print("\n[3] replay skipped — pass --queries FILE (one question per line); the "
              "questions are not in any record")
    report["replay"] = replay

    # ---- [4] blast radius, by AST ---------------------------------------------------
    radius: dict[str, list[str]] = {}
    for path in tracked_backend():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = sorted({node.func.attr for node in ast.walk(tree)
                        if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in EDGE_APIS})
        if names and path.name not in ("hippocampus.py", "edges.py"):
            radius[path.relative_to(ROOT).as_posix()] = names
    report["blast_radius"] = radius
    print(f"\n[4] modules reading the association graph's edges or degree ({len(radius)}):")
    for f, names in sorted(radius.items()):
        print(f"    {f}: {', '.join(names)}")

    # ---- [5] what else the saturation harms ------------------------------------------
    starts = sorted(nodes & concepts)
    reach_now = _reach(adj, starts, MAX_HOPS)
    reach_b = _reach(adj_b, starts, MAX_HOPS)
    sub_q = sum(1 for s, o in substrate_pairs if s in q_nodes or o in q_nodes)
    report["harm"] = {
        "mean_reach_within_hops_now": round(reach_now, 2),
        "mean_reach_within_hops_under_B": round(reach_b, 2),
        "hops": MAX_HOPS,
        "substrate_rows": len(substrate_pairs),
        "substrate_rows_with_a_q_endpoint": sub_q,
    }
    print(f"\n[5] mean reach within {MAX_HOPS} hops from a claim concept: now {reach_now:.1f}, "
          f"under B {reach_b:.1f}; co_activated rows in the store {len(substrate_pairs)}, "
          f"with a Q endpoint {sub_q}")
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
