"""INVESTIGATE — is there a target variable in the citation forecast? Read-only.

    python scripts/forecast_target_probe.py [--kb data/kb.db]
        [--calibration data/calibration_log.jsonl]
        [--capture data/citation_capture.jsonl] [--top 20]
        [--out forecast_target.json]

OQ-15 asks whether curiosity should score uncertainty reduction rather than
connectedness. That needs a target variable; the candidate is the citation
forecast (AF.1). This probe reports whether the candidate is usable, from
the records that exist, and designs nothing.

  [1] the series, reconstructed durably: every forecast from the calibration
      ledger's `prediction` rows (forecast_id, k, rank, activation, coverage
      in the row's context) joined through its `resolution` rows (outcome,
      lineage_id) to the lineage record (injected weights and cited flags),
      which is enough to recompute precision@k AND the prominence null for
      every scored turn — the done frame is not the only copy.
      Question 2's distribution first: if precision is flat, OQ-15 closes.
  [2] what varies with precision — correlation only: injected count,
      substrate share, cited share, forecastable-term coverage, mean anchor
      activation, answer length where the capture has it.
  [3] the per-ROW shape: each forecast row is a Bernoulli with a stated
      confidence (its anchor activation) and a resolved outcome. Outcome
      rate by activation band and a Brier score against the constant
      baseline say whether the stated p carries information — the shape an
      entropy calculation would need, as opposed to precision@k, which is
      a proportion over a set.
  [4] curiosity, retrospectively: the persisted graph rehydrated read-only,
      the current scorer run over it, and the top-scored nodes' citation
      record across all lineage compared with every node's — a cheap test
      of whether the degree × (1 − weight) proxy tracks anything. Also
      reported: the epistemic states and weights the rehydrated graph
      actually carries, because if they are all the default the score is
      degree alone.

Nothing is written: the store is opened mode=ro (md5 before and after), the
ledger and capture are read, the graph is rehydrated in memory only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
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


def _stats(vals) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "mean": round(statistics.mean(vals), 4),
            "std": round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0,
            "min": min(vals), "max": max(vals)}


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--calibration", default=None,
                    help="defaults to NEXUS_CALIBRATION_LOG or data/calibration_log.jsonl")
    ap.add_argument("--capture", default="data/citation_capture.jsonl")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default="forecast_target.json")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    from backend.cortex.curiosity import CuriosityEngine
    from backend.eval.calibration_log import log_path, read_entries
    from backend.kb.schema import SUBSTRATE_RELATIONS
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
    cal = Path(args.calibration) if args.calibration else log_path()
    report: dict = {"kb": str(kb), "calibration": str(cal), "calibration_exists": cal.is_file()}

    lineage: dict[str, dict] = {}
    if "lineage_records" in tables:
        for r in con.execute("SELECT lineage_id, injected_json, ts FROM lineage_records"):
            lineage[r["lineage_id"]] = {"injected": json.loads(r["injected_json"] or "[]"),
                                        "ts": r["ts"]}
    relation_of = {r["id"]: r["relation"] for r in con.execute("SELECT id, relation FROM triples")}
    answers: dict[str, int] = {}
    cap = Path(args.capture)
    if cap.is_file():
        for line in cap.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                answers[row["lineage_id"]] = len(row.get("answer") or "")

    # ---- [1] the series ------------------------------------------------------
    predictions: dict[str, list[dict]] = defaultdict(list)
    resolutions: dict[str, dict] = {}
    entries = list(read_entries(cal)) if cal.is_file() else []
    for row in entries:
        mech = row.get("mechanism")
        ctx = row.get("context") or {}
        if mech == "prediction" and ctx.get("forecast_id"):
            predictions[ctx["forecast_id"]].append(row)
        elif mech == "resolution" and row.get("entry_id"):
            resolutions[row["entry_id"]] = row
    turns = []
    for fid, rows in predictions.items():
        rows = sorted(rows, key=lambda r: (r["context"].get("rank", 0)))
        k = rows[0]["context"].get("k", len(rows))
        outcomes, lid = [], None
        for r in rows:
            res = resolutions.get(r["entry_id"])
            if res is None:
                outcomes.append(None)
                continue
            outcomes.append(bool(res.get("resolved_outcome")))
            lid = lid or (res.get("context") or {}).get("lineage_id")
        rec = lineage.get(lid) if lid else None
        injected = rec["injected"] if rec else []
        cited_ids = {it["belief_id"] for it in injected if it.get("cited") is True}
        known = [o for o in outcomes if o is not None]
        precision = (sum(1 for o in known if o) / k) if known and k else None
        null_ids = {it["belief_id"] for it in
                    sorted(injected, key=lambda it: -float(it.get("weight_at_injection") or 0))[:k]}
        null_precision = (len(null_ids & cited_ids) / k) if injected and k else None
        scorable = bool(rec) and bool(injected) and bool(cited_ids) and bool(known)
        reason = (None if scorable else
                  "unresolved" if not known else
                  "no_lineage" if not rec else
                  "no_injected" if not injected else "none_cited")
        ctx0 = rows[0]["context"]
        turns.append({
            "forecast_id": fid, "lineage_id": lid, "k": k,
            "turn": ctx0.get("turn"), "model_id": ctx0.get("model_id"),
            "precision": precision, "null_precision": null_precision,
            "scorable": scorable, "reason": reason,
            "total_terms": ctx0.get("total_terms"),
            "forecastable_terms": ctx0.get("forecastable_terms"),
            "coverage": ((ctx0.get("forecastable_terms") or 0) / ctx0["total_terms"]
                         if ctx0.get("total_terms") else None),
            "mean_activation": round(statistics.mean(
                float(r["stated_confidence"]) for r in rows), 4),
            "seed_anchored_share": sum(1 for r in rows if r["context"].get("seed_anchored")) / len(rows),
            "injected": len(injected),
            "cited_share": (len(cited_ids) / len(injected)) if injected else None,
            "substrate_share": (sum(1 for it in injected
                                    if relation_of.get(it["belief_id"]) in SUBSTRATE_RELATIONS)
                                / len(injected)) if injected else None,
            "answer_chars": answers.get(lid),
            "rows": [{"belief_id": r["belief_id"], "rank": r["context"].get("rank"),
                      "activation": r["stated_confidence"], "outcome": o}
                     for r, o in zip(rows, outcomes)],
        })
    scored = [t for t in turns if t["scorable"]]
    prec = [t["precision"] for t in scored]
    nulls = [t["null_precision"] for t in scored]
    diffs = [p - n for p, n in zip(prec, nulls)]
    flat = (len(prec) >= 2 and statistics.pstdev(prec) < 0.05) or len(set(prec)) <= 1
    report["series"] = {
        "forecasts": len(turns), "scored": len(scored),
        "excluded_by_reason": dict(Counter(t["reason"] for t in turns if not t["scorable"])),
        "precision": _stats(prec), "null_precision": _stats(nulls),
        "precision_minus_null": _stats(diffs),
        "turns_above_null": sum(1 for d in diffs if d > 0),
        "turns_equal_null": sum(1 for d in diffs if d == 0),
        "turns_below_null": sum(1 for d in diffs if d < 0),
        "flat": flat,
        "verdict": ("no signal: precision is flat across scored turns" if flat and scored
                    else "too few scored turns to say" if len(scored) < 5
                    else "precision varies; see correlations"),
        "turns": turns,
    }
    print(f"[1] forecasts {len(turns)}, scored {len(scored)}, excluded "
          f"{report['series']['excluded_by_reason']}")
    print(f"    precision {report['series']['precision']}")
    print(f"    null      {report['series']['null_precision']}")
    print(f"    diff      {report['series']['precision_minus_null']}  above/equal/below null: "
          f"{report['series']['turns_above_null']}/{report['series']['turns_equal_null']}/"
          f"{report['series']['turns_below_null']}")
    print(f"    verdict: {report['series']['verdict']}")
    for t in scored[:args.top]:
        print(f"      {str(t['lineage_id'])[:8]} k={t['k']} p={t['precision']:.2f} "
              f"null={t['null_precision']:.2f} inj={t['injected']} cov={t['coverage']}")

    # ---- [2] correlations ------------------------------------------------------
    def corr(key):
        pairs = [(t["precision"], t[key]) for t in scored if t.get(key) is not None]
        return pearson([p for p, _ in pairs], [float(v) for _, v in pairs])
    report["correlations"] = {k: corr(k) for k in
                              ("injected", "substrate_share", "cited_share", "coverage",
                               "mean_activation", "seed_anchored_share", "answer_chars")}
    print(f"\n[2] correlations with precision (n={len(scored)}): {report['correlations']}")

    # ---- [3] the per-row shape --------------------------------------------------
    rows_all = [(r["activation"], r["outcome"]) for t in scored for r in t["rows"]
                if r["outcome"] is not None]
    bands: dict[str, list] = defaultdict(list)
    for a, o in rows_all:
        bands[f"{min(int(a * 5), 4) * 20}-{min(int(a * 5), 4) * 20 + 20}%"].append(int(o))
    base = statistics.mean(o for _, o in rows_all) if rows_all else None
    brier = statistics.mean((a - o) ** 2 for a, o in rows_all) if rows_all else None
    brier_base = statistics.mean((base - o) ** 2 for _, o in rows_all) if rows_all else None
    report["per_row"] = {
        "rows": len(rows_all), "base_rate": base,
        "outcome_rate_by_activation_band": {b: {"n": len(v), "rate": round(statistics.mean(v), 3)}
                                            for b, v in sorted(bands.items())},
        "brier_stated": brier, "brier_constant_baseline": brier_base,
        "stated_p_beats_baseline": (brier is not None and brier_base is not None
                                    and brier < brier_base),
        "note": ("each forecast row is a Bernoulli: stated p = anchor activation, "
                 "outcome = cited. This is the distribution-shaped object; "
                 "precision@k is a proportion over the committed set"),
    }
    print(f"\n[3] per-row rows {len(rows_all)}, base rate {base}, Brier stated {brier} "
          f"vs constant {brier_base}: {report['per_row']['outcome_rate_by_activation_band']}")

    # ---- [4] curiosity, retrospectively ------------------------------------------
    cur: dict = {"available": "hebbian_edges" in tables}
    if cur["available"]:
        gs = HebbianGraphStore.__new__(HebbianGraphStore)
        gs.conn = con
        hippo = Hippocampus()
        loaded = gs.load_into(hippo)
        states = Counter(hippo.get_node(n).epistemic_state.value for n in hippo.nodes)
        confs = Counter(round(float(hippo.get_node(n).confidence), 2) for n in hippo.nodes)
        cited_count: Counter = Counter()
        inj_count: Counter = Counter()
        term_of = {r["id"]: (r["subject"], r["object"])
                   for r in con.execute("SELECT id, subject, object FROM triples")}
        for rec in lineage.values():
            for it in rec["injected"]:
                so = term_of.get(it["belief_id"])
                if not so:
                    continue
                for term in so:
                    inj_count[term] += 1
                    if it.get("cited") is True:
                        cited_count[term] += 1
        curious = CuriosityEngine().find_curious_nodes(hippo, top_k=args.top)
        top_ids = [c.node_id for c in curious]
        eligible = [n for n in hippo.nodes if hippo.in_degree(n) >= 1]
        def rate(ids):
            return (sum(1 for n in ids if cited_count[n] > 0) / len(ids)) if ids else None
        cur.update({
            "edges_loaded": loaded, "nodes": len(hippo.nodes),
            "epistemic_states_after_rehydration": dict(states),
            "confidences_after_rehydration": dict(confs),
            "score_is_degree_only": len(confs) == 1 and len(states) == 1,
            "top": [{"node": c.node_id, "score": c.score, "in_degree": c.in_degree,
                     "later_cited": cited_count[c.node_id], "injected": inj_count[c.node_id]}
                    for c in curious],
            "top_ever_cited_rate": rate(top_ids),
            "all_eligible_ever_cited_rate": rate(eligible),
            "eligible_nodes": len(eligible),
        })
        print(f"\n[4] graph: {loaded} edges, {len(hippo.nodes)} nodes; states after "
              f"rehydration {dict(states)}; confidences {dict(confs)}")
        print(f"    curiosity top-{len(curious)} ever-cited rate {cur['top_ever_cited_rate']} "
              f"vs all eligible nodes {cur['all_eligible_ever_cited_rate']} (n={len(eligible)})")
        for c in cur["top"][:args.top]:
            print(f"      {c['score']:7.2f} deg={c['in_degree']:3d} cited={c['later_cited']:3d} "
                  f"inj={c['injected']:3d}  {c['node']}")
    report["curiosity"] = cur
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
