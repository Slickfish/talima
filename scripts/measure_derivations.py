#!/usr/bin/env python3
"""Read-only structural measurement of the NEXUS belief graph.

Writes reports/derivation_structure.md. Opens every database read-only
(SQLite URI mode=ro) and issues no writes of any kind.

METHODOLOGY NOTE (load-bearing — read before the tables mean anything)
---------------------------------------------------------------------
The requested measurements (derivation depth, primitive roots, provenance
polynomials, shared-root overlap, derivation cycles) presuppose a
belief->antecedent relation. The `triples` schema is:

    id, subject, relation, object, weight, tier, granularity, source,
    fire_count, created_at, last_fired

There is no antecedent / derived_from / justification column, and no table
records "belief B was derived from beliefs X,Y". Every stored belief is a
primitive assertion attributed to a `source` string. A native derivation
graph therefore does not exist, and this script does not invent one.

What it measures instead, each labelled at the point of use:

  G_concept  (PROXY)  Directed concept graph: one edge subject -> object per
                      triple, read as "subject is supported by object".
                      Items 1-3 and 5 are computed on this graph. A
                      "primitive root" is a node with out-degree 0.
  G_cocite   (REAL)   Beliefs co-injected into the same turn, from
                      lineage_records.injected_json. This is literally
                      "beliefs cited together in one inference" and is the
                      basis for item 4.
  G_hebb     (REAL)   Association graph. Prefers persisted co-activation
                      triples (relation='co_activated'); falls back to
                      G_concept, and says which it used.

Depth semantics: beliefs have no antecedents, so a belief's depth is
reported as the depth of its SUBJECT concept in G_concept.

Usage:
    python scripts/measure_derivations.py [--kb data/kb.db] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

# M.4 — see scripts/_console.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8_stdio  # noqa: E402
from _readonly import open_read_only, store_line  # noqa: E402
from _env import load_env  # noqa: E402

DEPTH_CAP = 20          # traversal cap required by the brief
MONOMIAL_CAP = 10_000   # per-belief cap on enumerated derivation paths
COCITE_PAIR_CAP = 200_000


# --------------------------------------------------------------------------
# read-only db access
# --------------------------------------------------------------------------

def connect_ro(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = open_read_only(path, row_factory=None)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def load_triples(conn: sqlite3.Connection) -> list[dict]:
    if not table_exists(conn, "triples"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM triples")]


def load_lineage(conn: sqlite3.Connection) -> list[dict]:
    if not table_exists(conn, "lineage_records"):
        return []
    out = []
    for r in conn.execute("SELECT lineage_id, ts, injected_json FROM lineage_records"):
        try:
            injected = json.loads(r["injected_json"])
        except (TypeError, ValueError):
            injected = []
        out.append({"lineage_id": r["lineage_id"], "ts": r["ts"], "injected": injected})
    return out


def schema_probe(conn: sqlite3.Connection) -> dict:
    """Report whether any antecedent-bearing column exists."""
    cols: dict[str, list[str]] = {}
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        t = r["name"]
        cols[t] = [c[1] for c in conn.execute(f"PRAGMA table_info({t})")]
    antecedent_like = {"antecedent", "antecedents", "derived_from", "premise",
                       "premises", "justification", "parent_belief", "supports",
                       "supported_by", "because_of", "entailed_by"}
    found = {t: [c for c in cs if c.lower() in antecedent_like] for t, cs in cols.items()}
    found = {t: cs for t, cs in found.items() if cs}
    return {"tables": cols, "antecedent_columns": found}


# --------------------------------------------------------------------------
# graph construction
# --------------------------------------------------------------------------

def build_concept_graph(triples: list[dict]) -> tuple[dict, dict, set]:
    """subject -> {object}. Returns (adj, edge_weight, nodes)."""
    adj: dict[str, set[str]] = defaultdict(set)
    weight: dict[tuple[str, str], float] = defaultdict(float)
    nodes: set[str] = set()
    for t in triples:
        s, o = str(t["subject"]), str(t["object"])
        nodes.add(s); nodes.add(o)
        adj[s].add(o)
        weight[(s, o)] += float(t["weight"])
    return adj, weight, nodes


def build_hebbian_graph(triples: list[dict]) -> tuple[dict, dict, set, str]:
    """Prefer persisted co-activation edges; fall back to the concept graph."""
    coact = [t for t in triples if str(t["relation"]).lower() == "co_activated"]
    if coact:
        adj, weight, nodes = build_concept_graph(coact)
        return adj, weight, nodes, "co_activated triples (persisted Hebbian promotions)"
    adj, weight, nodes = build_concept_graph(triples)
    return adj, weight, nodes, "FALLBACK: full concept graph (no co_activated triples present)"


# --------------------------------------------------------------------------
# stats helpers
# --------------------------------------------------------------------------

def percentile(sorted_vals: list[float], q: float):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def dist_row(label: str, vals: list[float]) -> dict:
    if not vals:
        return {"label": label, "n": 0, "median": None, "p50": None, "p90": None,
                "p95": None, "p99": None, "max": None, "mean": None}
    sv = sorted(vals)
    return {
        "label": label, "n": len(sv),
        "median": percentile(sv, 0.50), "p50": percentile(sv, 0.50),
        "p90": percentile(sv, 0.90), "p95": percentile(sv, 0.95),
        "p99": percentile(sv, 0.99), "max": sv[-1],
        "mean": sum(sv) / len(sv),
    }


def fmt(v, nd=3):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{nd}f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))
    return str(v)


def dist_table(rows: list[dict]) -> str:
    head = ("| metric | n | median | p50 | p90 | p95 | p99 | max | mean |\n"
            "|---|---|---|---|---|---|---|---|---|\n")
    body = "".join(
        f"| {r['label']} | {r['n']} | {fmt(r['median'])} | {fmt(r['p50'])} | "
        f"{fmt(r['p90'])} | {fmt(r['p95'])} | {fmt(r['p99'])} | {fmt(r['max'])} | "
        f"{fmt(r['mean'])} |\n"
        for r in rows
    )
    return head + body


# --------------------------------------------------------------------------
# 1/2/3: depth, roots, monomials (on G_concept, capped)
# --------------------------------------------------------------------------

def longest_depth(node: str, adj: dict, cap: int) -> tuple[int, bool]:
    """Longest simple path length from node to an out-degree-0 sink.
    Cycle-safe (tracks the current path). Returns (depth, hit_cap)."""
    best = 0
    hit_cap = False
    stack = [(node, 0, {node})]
    while stack:
        cur, d, on_path = stack.pop()
        succs = adj.get(cur) or ()
        if not succs:
            best = max(best, d)
            continue
        if d >= cap:
            hit_cap = True
            best = max(best, d)
            continue
        for nxt in succs:
            if nxt in on_path:          # cycle — do not traverse
                best = max(best, d)
                continue
            stack.append((nxt, d + 1, on_path | {nxt}))
    return best, hit_cap


def root_set(node: str, adj: dict, cap: int) -> tuple[set, bool]:
    """Distinct out-degree-0 sinks reachable within `cap` hops (BFS)."""
    roots: set[str] = set()
    hit_cap = False
    seen = {node}
    q = deque([(node, 0)])
    while q:
        cur, d = q.popleft()
        succs = adj.get(cur) or ()
        if not succs:
            roots.add(cur)
            continue
        if d >= cap:
            hit_cap = True
            continue
        for nxt in succs:
            if nxt not in seen:
                seen.add(nxt)
                q.append((nxt, d + 1))
    return roots, hit_cap


def count_monomials(node: str, adj: dict, cap: int,
                    mono_cap: int) -> tuple[int, bool, bool]:
    """Count distinct complete derivation paths (monomials) to sinks.
    Returns (count, hit_depth_cap, hit_mono_cap)."""
    count = 0
    hit_depth = False
    hit_mono = False
    stack = [(node, 0, {node})]
    while stack:
        if count >= mono_cap:
            hit_mono = True
            break
        cur, d, on_path = stack.pop()
        succs = adj.get(cur) or ()
        if not succs:
            count += 1
            continue
        if d >= cap:
            hit_depth = True
            count += 1              # truncated path still counts as one
            continue
        pushed = False
        for nxt in succs:
            if nxt in on_path:
                continue
            stack.append((nxt, d + 1, on_path | {nxt}))
            pushed = True
        if not pushed:              # every successor was a cycle back-edge
            count += 1
    return count, hit_depth, hit_mono


# --------------------------------------------------------------------------
# 5: cycles
# --------------------------------------------------------------------------

def find_cycles(adj: dict, nodes: set) -> dict:
    try:
        import networkx as nx
    except ImportError:
        return {"available": False}
    g = nx.DiGraph()
    g.add_nodes_from(nodes)
    for s, outs in adj.items():
        for o in outs:
            g.add_edge(s, o)
    self_loops = [(u, v) for u, v in g.edges() if u == v]
    sccs = [c for c in nx.strongly_connected_components(g) if len(c) > 1]
    examples: list[list[str]] = []
    try:
        for i, cyc in enumerate(nx.simple_cycles(g)):
            if i >= 10:
                break
            examples.append(list(cyc))
    except Exception:
        pass
    return {
        "available": True,
        "n_self_loops": len(self_loops),
        "n_multi_node_sccs": len(sccs),
        "largest_scc_size": max((len(c) for c in sccs), default=0),
        "examples": examples,
        "self_loop_examples": self_loops[:10],
    }


# --------------------------------------------------------------------------
# 6: degree distribution + power-law MLE
# --------------------------------------------------------------------------

def powerlaw_mle(degrees: list[int]) -> dict:
    """Discrete-tail MLE (Clauset-style): alpha = 1 + n / sum(ln(x/xmin)),
    x_min chosen to minimise the KS distance. Reported without judgement."""
    vals = sorted(d for d in degrees if d > 0)
    if len(vals) < 10:
        return {"fitted": False, "reason": f"only {len(vals)} non-zero-degree nodes (<10)"}
    best = None
    for xmin in sorted(set(vals)):
        tail = [v for v in vals if v >= xmin]
        if len(tail) < 10:
            continue
        try:
            s = sum(math.log(v / xmin) for v in tail)
        except ValueError:
            continue
        if s <= 0:
            continue
        alpha = 1.0 + len(tail) / s
        n = len(tail)
        ks = 0.0
        for i, v in enumerate(sorted(tail)):
            emp = (i + 1) / n
            theo = 1.0 - (v / xmin) ** (1.0 - alpha)
            ks = max(ks, abs(emp - theo))
        if best is None or ks < best["ks"]:
            best = {"fitted": True, "alpha": alpha, "x_min": xmin,
                    "ks": ks, "n_tail": n}
    return best or {"fitted": False, "reason": "no admissible x_min (tail < 10)"}


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def build_report(kb_path: Path) -> str:
    L: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

    conn = connect_ro(kb_path)
    if conn is None:
        return f"# Derivation Structure Report\n\nNo database at `{kb_path}`.\n"

    triples = load_triples(conn)
    lineage = load_lineage(conn)
    probe = schema_probe(conn)
    conn.close()

    L.append("# Derivation Structure Report")
    L.append("")
    L.append(f"- Generated: {now}")
    L.append(f"- Source database: `{kb_path}` (opened read-only, `mode=ro`)")
    L.append(f"- Generator: `scripts/measure_derivations.py`")
    L.append(f"- Traversal depth cap: {DEPTH_CAP}; monomial cap per belief: {MONOMIAL_CAP}")
    L.append("")

    # ---- schema / methodology ------------------------------------------
    L.append("## 0. Schema probe and graph definitions")
    L.append("")
    L.append("Columns per table:")
    L.append("")
    L.append("| table | columns |")
    L.append("|---|---|")
    for t, cs in sorted(probe["tables"].items()):
        L.append(f"| `{t}` | {', '.join('`%s`' % c for c in cs)} |")
    L.append("")
    if probe["antecedent_columns"]:
        L.append(f"Antecedent-bearing columns found: `{probe['antecedent_columns']}`.")
    else:
        L.append("**Antecedent-bearing columns found: none.** No table records a "
                 "belief-to-antecedent relation; every stored belief is a primitive "
                 "assertion attributed to a `source` string.")
    L.append("")
    L.append("Graphs used below:")
    L.append("")
    L.append("| graph | status | definition |")
    L.append("|---|---|---|")
    L.append("| `G_concept` | PROXY | one directed edge `subject -> object` per triple, "
             "read as \"subject is supported by object\"; a primitive root is a node "
             "with out-degree 0 |")
    L.append("| `G_cocite` | REAL | beliefs co-injected into the same turn, from "
             "`lineage_records.injected_json` |")
    L.append("| `G_hebb` | REAL | association graph, preferring persisted "
             "`co_activated` triples |")
    L.append("")
    L.append("Items 1-3 and 5 are computed on `G_concept`. Because beliefs carry no "
             "antecedents, a belief's depth is reported as the depth of its **subject "
             "concept** in `G_concept`.")
    L.append("")

    # ---- dataset --------------------------------------------------------
    by_source = Counter(str(t["source"]) for t in triples)
    by_relation = Counter(str(t["relation"]) for t in triples)
    adj, edge_w, nodes = build_concept_graph(triples)
    L.append("## Dataset")
    L.append("")
    L.append("| quantity | value |")
    L.append("|---|---|")
    L.append(f"| triples (beliefs) | {len(triples)} |")
    L.append(f"| distinct concepts (`G_concept` nodes) | {len(nodes)} |")
    L.append(f"| `G_concept` edges | {sum(len(v) for v in adj.values())} |")
    L.append(f"| lineage records | {len(lineage)} |")
    L.append(f"| distinct sources | {len(by_source)} |")
    L.append("")
    if by_source:
        L.append("| source | triples |")
        L.append("|---|---|")
        for s, n in by_source.most_common():
            L.append(f"| `{s}` | {n} |")
        L.append("")
    if by_relation:
        L.append("| relation (top 10) | triples |")
        L.append("|---|---|")
        for r, n in by_relation.most_common(10):
            L.append(f"| `{r}` | {n} |")
        L.append("")

    # ---- 1/2/3 ----------------------------------------------------------
    depths, rootcounts, monos = [], [], []
    depth_cap_hits = mono_depth_hits = mono_cap_hits = 0
    for t in triples:
        subj = str(t["subject"])
        d, hc = longest_depth(subj, adj, DEPTH_CAP)
        depths.append(d)
        depth_cap_hits += 1 if hc else 0
        rs, rc = root_set(subj, adj, DEPTH_CAP)
        rootcounts.append(len(rs))
        m, hd, hm = count_monomials(subj, adj, DEPTH_CAP, MONOMIAL_CAP)
        monos.append(m)
        mono_depth_hits += 1 if hd else 0
        mono_cap_hits += 1 if hm else 0

    L.append("## 1. Derivation depth per belief")
    L.append("")
    L.append("Longest path from the belief's subject concept to an out-degree-0 node "
             "in `G_concept`, cycle-safe, capped at "
             f"{DEPTH_CAP}.")
    L.append("")
    L.append(dist_table([dist_row("derivation depth", depths)]))
    L.append(f"- Beliefs whose traversal reached the depth cap ({DEPTH_CAP}): "
             f"**{depth_cap_hits}** of {len(triples)}")
    zero = sum(1 for d in depths if d == 0)
    L.append(f"- Beliefs at depth 0 (subject is itself a primitive root): "
             f"**{zero}** of {len(triples)}")
    L.append("")
    if depths:
        hist = Counter(depths)
        L.append("| depth | beliefs |")
        L.append("|---|---|")
        for d in sorted(hist):
            L.append(f"| {d} | {hist[d]} |")
        L.append("")

    L.append("## 2. Distinct primitive source roots per belief")
    L.append("")
    L.append(dist_table([dist_row("distinct roots", rootcounts)]))
    L.append("")

    L.append("## 3. Provenance polynomial size per belief")
    L.append("")
    L.append("Monomial = one complete set of co-required antecedents, i.e. one "
             "distinct path from the belief to a root in `G_concept`.")
    L.append("")
    L.append(dist_table([dist_row("distinct monomials", monos)]))
    L.append(f"- Beliefs hitting the depth cap ({DEPTH_CAP}) during enumeration: "
             f"**{mono_depth_hits}**")
    L.append(f"- Beliefs hitting the monomial cap ({MONOMIAL_CAP}): "
             f"**{mono_cap_hits}**")
    L.append("")

    # ---- 4: shared roots over co-cited pairs -----------------------------
    L.append("## 4. Shared-root analysis over co-cited belief pairs")
    L.append("")
    L.append("Pairs are taken from `G_cocite`: beliefs injected into the same turn "
             "(`lineage_records.injected_json`). Root sets come from `G_concept`.")
    L.append("")
    id_to_subject = {int(t["id"]): str(t["subject"]) for t in triples if t["id"] is not None}
    root_cache: dict[str, set] = {}

    def roots_for_belief(bid: int) -> set:
        subj = id_to_subject.get(bid)
        if subj is None:
            return set()
        if subj not in root_cache:
            root_cache[subj] = root_set(subj, adj, DEPTH_CAP)[0]
        return root_cache[subj]

    pairs, unresolved_pairs = [], 0
    for rec in lineage:
        ids = []
        for item in rec["injected"]:
            if isinstance(item, dict) and item.get("belief_id") is not None:
                ids.append(int(item["belief_id"]))
        ids = sorted(set(ids))
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.append((ids[i], ids[j]))
                if len(pairs) >= COCITE_PAIR_CAP:
                    break
    pairs = sorted(set(pairs))

    jaccards, n_intersect = [], 0
    for a, b in pairs:
        ra, rb = roots_for_belief(a), roots_for_belief(b)
        if not ra and not rb:
            unresolved_pairs += 1
            continue
        inter, union = ra & rb, ra | rb
        if inter:
            n_intersect += 1
        jaccards.append(len(inter) / len(union) if union else 0.0)

    L.append("| quantity | value |")
    L.append("|---|---|")
    L.append(f"| lineage records scanned | {len(lineage)} |")
    L.append(f"| distinct co-cited belief pairs | {len(pairs)} |")
    L.append(f"| pairs with both root sets empty/unresolvable | {unresolved_pairs} |")
    L.append(f"| pairs evaluated | {len(jaccards)} |")
    L.append(f"| pairs whose root sets intersect | {n_intersect} |")
    frac = (n_intersect / len(jaccards)) if jaccards else None
    L.append(f"| fraction intersecting | {fmt(frac) if frac is not None else 'n/a'} |")
    L.append("")
    L.append(dist_table([dist_row("|intersection| / |union|", jaccards)]))
    L.append("")

    # ---- 5: cycles -------------------------------------------------------
    L.append("## 5. Cycle check")
    L.append("")
    cyc = find_cycles(adj, nodes)
    if not cyc.get("available"):
        L.append("networkx unavailable; cycle detection skipped.")
    else:
        L.append("| quantity | value |")
        L.append("|---|---|")
        L.append(f"| self-loops | {cyc['n_self_loops']} |")
        L.append(f"| strongly-connected components with >1 node | {cyc['n_multi_node_sccs']} |")
        L.append(f"| largest such component (nodes) | {cyc['largest_scc_size']} |")
        L.append(f"| simple cycles enumerated (capped at 10) | {len(cyc['examples'])} |")
        L.append("")
        if cyc["examples"]:
            L.append("Examples (up to 10):")
            L.append("")
            L.append("| # | cycle |")
            L.append("|---|---|")
            for i, c in enumerate(cyc["examples"], 1):
                L.append(f"| {i} | {' -> '.join(str(x) for x in c)} -> {c[0]} |")
            L.append("")
        if cyc["self_loop_examples"]:
            L.append("Self-loop examples: " +
                     ", ".join(f"`{u}`" for u, _ in cyc["self_loop_examples"]))
            L.append("")

    # ---- 6: hebbian degree ----------------------------------------------
    L.append("## 6. Degree distribution of the Hebbian graph")
    L.append("")
    h_adj, h_w, h_nodes, h_src = build_hebbian_graph(triples)
    L.append(f"- Edge source: {h_src}")
    deg = Counter()
    for s, outs in h_adj.items():
        for o in outs:
            deg[s] += 1
            deg[o] += 1
    for n in h_nodes:
        deg.setdefault(n, 0)
    degrees = [deg[n] for n in h_nodes]
    total_w = sum(h_w.values())
    L.append(f"- Nodes: {len(h_nodes)}; edges: {sum(len(v) for v in h_adj.values())}; "
             f"total edge weight: {fmt(total_w)}")
    L.append("")
    L.append(dist_table([dist_row("degree", degrees)]))
    L.append("")
    fit = powerlaw_mle(degrees)
    L.append("Power-law tail fit (MLE, x_min by KS minimisation):")
    L.append("")
    L.append("| quantity | value |")
    L.append("|---|---|")
    if fit.get("fitted"):
        L.append(f"| alpha | {fmt(fit['alpha'])} |")
        L.append(f"| x_min | {fit['x_min']} |")
        L.append(f"| KS distance | {fmt(fit['ks'])} |")
        L.append(f"| n in tail | {fit['n_tail']} |")
    else:
        L.append(f"| fitted | no |")
        L.append(f"| reason | {fit.get('reason','n/a')} |")
    L.append("")
    node_w = defaultdict(float)
    for (s, o), w in h_w.items():
        node_w[s] += w
        node_w[o] += w
    top = sorted(h_nodes, key=lambda n: (deg[n], node_w[n]), reverse=True)[:10]
    top_set = set(top)
    L.append("Top 10 nodes by degree:")
    L.append("")
    L.append("| # | node | degree | incident edge weight | incident weight / total |")
    L.append("|---|---|---|---|---|")
    for i, n in enumerate(top, 1):
        share = (node_w[n] / total_w) if total_w else 0.0
        L.append(f"| {i} | `{n}` | {deg[n]} | {fmt(node_w[n])} | {fmt(share)} |")
    L.append("")
    # Deduplicated: each edge counted once, even when both endpoints are top-10.
    union_w = sum(w for (s, o), w in h_w.items() if s in top_set or o in top_set)
    L.append(f"- Edges with at least one endpoint in the top 10 carry "
             f"**{fmt((union_w/total_w) if total_w else 0.0)}** of total edge weight "
             f"({fmt(union_w)} of {fmt(total_w)}; each edge counted once).")
    L.append("- Per-node `incident weight / total` above counts an edge at each of "
             "its endpoints, so that column sums to more than 1 across nodes.")
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    # M.4 — the first statement, before anything is printed. Scripts
    # print em dashes; a piped stdout would otherwise carry cp1252
    # bytes that a UTF-8 reader cannot decode. See scripts/_console.py.
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db", help="path to kb.db (read-only)")
    ap.add_argument("--out", default="reports/derivation_structure.md")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    report = build_report(Path(args.kb))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # U.1-adjacent: Packet I.1's encoding sweep appended `encoding=` to a
    # call that already had it. `ast.parse` accepts a repeated keyword —
    # it is a COMPILE-time check — so every AST guard in this repo passed
    # over a script that could not run, from e36efb3 (2026-08-10) until now.
    out.write_text(report, encoding="utf-8")
    print(f"wrote {out} ({len(report)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
