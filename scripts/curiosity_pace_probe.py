"""INVESTIGATE — curiosity as prediction error (PACE), not structure. Read-only.

    python scripts/curiosity_pace_probe.py [--kb data/kb.db]
        [--calibration data/calibration_log.jsonl] [--top 20]
        [--out curiosity_pace.json]

PACE (Gruber & Ranganath 2019): curiosity is triggered by significant
prediction errors that are appraised, and a cycle closes when the gap
closes. Two prediction errors already exist here. The predictive cortex's
surprise set (agentic_loop.py, Stage 4.5) diffs the answer's extracted
concepts against the retrieval-seeded activation and lives on the wire
only. The citation forecast (AF.1) commits per belief before retrieval and
resolves per belief against citation, in the calibration ledger, durably.
This probe asks whether a trigger, an appraisal and a closure are each
computable from what is recorded, and designs nothing.

  [1] the trigger's contents: every forecast row joined through its
      resolution to the lineage record, classed as
        hit         predicted, injected, cited
        unused      predicted, injected, NOT cited — the store had it, the
                    prompt showed it, the answer did not use it
        unsurfaced  predicted, NOT injected — the store had it, retrieval
                    did not surface it
        unresolved  no resolution row
      plus `surprise`: injected and cited but not predicted. Counts, with
      examples printed as surfaces.
  [2] per concept: the same rows attributed to subject and object, so the
      trigger names a thing — rows, errors, error rate, distinct turns.
  [3] significance: the stated activation on error rows against hit rows.
      If both sit at 1.00 the ledger cannot say which errors are significant.
  [4] appraisal: the coping split (unused vs unsurfaced) per concept; and
      whether the noise class (`about`, `mg`, `qtc`, `change`, `dose`) can
      appear at all, since forecast rows anchor on claim rows only.
  [5] closure: concepts forecast in two or more turns, error rate first
      turn against last; and the number of surfaced-question -> answering-
      turn pairs the record holds, which is zero by construction (curiosity
      output is never persisted and the query is hashed).
  [6] side by side: the current curiosity top-N on the rehydrated graph
      beside an error-trigger top-N (errors, then error rate), with each
      row's degree, claim count and noise-class flag, and the overlap.

Store opened mode=ro (md5 before and after); the ledger is read; the graph
is rehydrated in memory only. Nothing is written but the report.
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

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402

NOISE = ("about", "mg", "qtc", "change", "dose")


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "-"


def _stats(vals) -> dict:
    vals = [float(v) for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "mean": round(statistics.mean(vals), 4),
            "share_at_1.0": round(sum(1 for v in vals if v >= 0.999) / len(vals), 4),
            "min": min(vals), "max": max(vals)}


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--calibration", default=None,
                    help="defaults to NEXUS_CALIBRATION_LOG or data/calibration_log.jsonl")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default="curiosity_pace.json")
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

    surface: dict[int, str] = {}
    concepts_of: dict[int, tuple[str, str]] = {}
    claim_count: Counter = Counter()
    for r in con.execute("SELECT id, subject, relation, object FROM triples WHERE archived = 0"):
        surface[r["id"]] = f"{r['subject']} | {r['relation']} | {r['object']}"
        if r["relation"] in SUBSTRATE_RELATIONS:
            continue
        concepts_of[r["id"]] = (r["subject"], r["object"])
        claim_count[r["subject"]] += 1
        claim_count[r["object"]] += 1
    lineage: dict[str, dict] = {}
    if "lineage_records" in tables:
        for r in con.execute("SELECT lineage_id, injected_json FROM lineage_records"):
            items = json.loads(r["injected_json"] or "[]")
            lineage[r["lineage_id"]] = {
                "injected": {it["belief_id"] for it in items if isinstance(it, dict)},
                "cited": {it["belief_id"] for it in items
                          if isinstance(it, dict) and it.get("cited") is True},
            }

    # ---- [1] the trigger's contents ----------------------------------------------
    by_mechanism: Counter = Counter()
    predictions: list[dict] = []
    resolutions: dict[str, dict] = {}
    for row in (read_entries(cal) if cal.is_file() else []):
        by_mechanism[row.get("mechanism")] += 1
        ctx = row.get("context") or {}
        if row.get("mechanism") == "prediction" and ctx.get("forecast_id"):
            predictions.append(row)
        elif row.get("mechanism") == "resolution" and row.get("entry_id"):
            resolutions[row["entry_id"]] = row
    rows = []
    for p in predictions:
        res = resolutions.get(p["entry_id"])
        ctx = p["context"]
        lid = (res.get("context") or {}).get("lineage_id") if res else None
        rec = lineage.get(lid) if lid else None
        bid = p["belief_id"]
        if res is None:
            cls = "unresolved"
        elif rec is None:
            cls = "no_lineage"
        elif bid not in rec["injected"]:
            cls = "unsurfaced"
        elif bid in rec["cited"]:
            cls = "hit"
        else:
            cls = "unused"
        rows.append({"belief_id": bid, "class": cls, "activation": float(p["stated_confidence"]),
                     "turn": ctx.get("turn"), "forecast_id": ctx["forecast_id"],
                     "seed_anchored": bool(ctx.get("seed_anchored")),
                     "citation_test": (res.get("context") or {}).get("citation_test") if res else None})
    predicted_by_lineage: dict[str, set[int]] = defaultdict(set)
    for p in predictions:
        res = resolutions.get(p["entry_id"])
        lid = (res.get("context") or {}).get("lineage_id") if res else None
        if lid:
            predicted_by_lineage[lid].add(p["belief_id"])
    surprise = [(lid, b) for lid, rec in lineage.items()
                for b in rec["cited"] if b not in predicted_by_lineage.get(lid, set())]
    classes = Counter(r["class"] for r in rows)

    def ex(cls, k=6):
        return [surface.get(r["belief_id"], f"<{r['belief_id']}>")
                for r in rows if r["class"] == cls][:k]

    report["trigger"] = {
        "ledger_rows_by_mechanism": dict(by_mechanism),
        "forecast_rows": len(rows), "classes": dict(classes),
        "surprise_rows": len(surprise),
        "resolutions_without_citation_test": sum(
            1 for r in rows if r["class"] not in ("unresolved",) and r["citation_test"] is None),
        "examples": {c: ex(c) for c in ("hit", "unused", "unsurfaced")},
        "surprise_examples": [surface.get(b, f"<{b}>") for _l, b in surprise[:6]],
        "predictive_cortex": ("wire only: agentic_loop.py Stage 4.5 emits high_surprise/"
                              "confirmed/missed on the prediction_error frame; the state "
                              "keeps one magnitude and the predicted node list in an "
                              "InMemorySaver checkpoint (checkpoint.py) — nothing durable"),
    }
    t = report["trigger"]
    print(f"[1] ledger {dict(by_mechanism)}; forecast rows {len(rows)} {dict(classes)}; "
          f"surprise rows {len(surprise)}; resolutions lacking citation_test "
          f"{t['resolutions_without_citation_test']}")
    for c in ("hit", "unused", "unsurfaced"):
        for s in t["examples"][c][:3]:
            print(f"      {c:10s} {s}")
    for s in t["surprise_examples"][:3]:
        print(f"      {'surprise':10s} {s}")

    # ---- [2] per concept ---------------------------------------------------------------
    per: dict[str, dict] = defaultdict(lambda: {"rows": 0, "hit": 0, "unused": 0,
                                                "unsurfaced": 0, "turns": set()})
    for r in rows:
        if r["class"] in ("unresolved", "no_lineage"):
            continue
        for c in concepts_of.get(r["belief_id"], ()):
            d = per[c]
            d["rows"] += 1
            d[r["class"]] += 1
            if r["turn"] is not None:
                d["turns"].add(r["turn"])
    for c, d in per.items():
        d["errors"] = d["unused"] + d["unsurfaced"]
        d["error_rate"] = round(d["errors"] / d["rows"], 3) if d["rows"] else None
        d["turns"] = sorted(d["turns"])
    report["per_concept"] = {"concepts": len(per),
                             "with_an_error": sum(1 for d in per.values() if d["errors"]),
                             "in_two_or_more_turns": sum(1 for d in per.values() if len(d["turns"]) >= 2)}
    print(f"\n[2] concepts with forecast rows {len(per)}; with an error "
          f"{report['per_concept']['with_an_error']}; in two or more turns "
          f"{report['per_concept']['in_two_or_more_turns']}")

    # ---- [3] significance ----------------------------------------------------------------
    err_act = [r["activation"] for r in rows if r["class"] in ("unused", "unsurfaced")]
    hit_act = [r["activation"] for r in rows if r["class"] == "hit"]
    report["significance"] = {"error_rows": _stats(err_act), "hit_rows": _stats(hit_act),
                              "seed_anchored_share": round(sum(1 for r in rows if r["seed_anchored"])
                                                           / len(rows), 4) if rows else None}
    print(f"\n[3] activation on error rows {report['significance']['error_rows']} vs hit rows "
          f"{report['significance']['hit_rows']}; seed-anchored share "
          f"{report['significance']['seed_anchored_share']}")

    # ---- [4] appraisal ------------------------------------------------------------------
    unused_only = [c for c, d in per.items() if d["unused"] and not d["unsurfaced"]]
    unsurfaced_only = [c for c, d in per.items() if d["unsurfaced"] and not d["unused"]]
    both = [c for c, d in per.items() if d["unused"] and d["unsurfaced"]]
    noise_present = {w: (w in per) for w in NOISE}
    report["appraisal"] = {
        "concepts_unused_only": len(unused_only), "concepts_unsurfaced_only": len(unsurfaced_only),
        "concepts_both": len(both),
        "examples_unused": sorted(unused_only, key=lambda c: -per[c]["unused"])[:args.top],
        "examples_unsurfaced": sorted(unsurfaced_only, key=lambda c: -per[c]["unsurfaced"])[:args.top],
        "noise_class_present_as_forecast_concept": noise_present,
        "note": ("forecast rows anchor on live claim rows with substrate excluded "
                 "(forecast.py anchored_exact), so a concept reaches the trigger only "
                 "as the exact subject or object of a claim"),
    }
    a = report["appraisal"]
    print(f"\n[4] coping split: unused-only {a['concepts_unused_only']}, unsurfaced-only "
          f"{a['concepts_unsurfaced_only']}, both {a['concepts_both']}; noise class present "
          f"{noise_present}")
    for c in a["examples_unused"][:6]:
        print(f"      unused     {per[c]['unused']:3d}/{per[c]['rows']:3d}  {c!r}")
    for c in a["examples_unsurfaced"][:6]:
        print(f"      unsurfaced {per[c]['unsurfaced']:3d}/{per[c]['rows']:3d}  {c!r}")

    # ---- [5] closure ---------------------------------------------------------------------
    def rate_in_turn(c, turn):
        rs = [r for r in rows if r["turn"] == turn and r["class"] in ("hit", "unused", "unsurfaced")
              and c in concepts_of.get(r["belief_id"], ())]
        return (sum(1 for r in rs if r["class"] != "hit") / len(rs)) if rs else None

    improved = worsened = flat = 0
    closure_examples = []
    for c, d in per.items():
        if len(d["turns"]) < 2:
            continue
        first, last = rate_in_turn(c, d["turns"][0]), rate_in_turn(c, d["turns"][-1])
        if first is None or last is None:
            continue
        if last < first:
            improved += 1
        elif last > first:
            worsened += 1
        else:
            flat += 1
        if len(closure_examples) < args.top:
            closure_examples.append((c, d["turns"][0], round(first, 2), d["turns"][-1], round(last, 2)))
    report["closure"] = {
        "concepts_with_two_turns": improved + worsened + flat,
        "improved": improved, "worsened": worsened, "flat": flat,
        "examples": closure_examples,
        "surfaced_question_to_answering_turn_pairs": 0,
        "why_zero": ("curiosity output is never persisted (SSE and the event ring only) "
                     "and the lineage record keeps query_hash, so no record links a "
                     "surfaced question to the turn that asked it"),
        "pre_AU_resolutions_in_series": t["resolutions_without_citation_test"],
    }
    print(f"\n[5] closure over concepts forecast in 2+ turns: improved {improved}, worsened "
          f"{worsened}, flat {flat}; surfaced-question->turn pairs in the record: 0 (not recorded)")
    for c, t0, r0, t1, r1 in closure_examples[:6]:
        print(f"      {c!r}: turn {t0} error {r0} -> turn {t1} error {r1}")

    # ---- [6] side by side ------------------------------------------------------------
    side: dict = {"available": "hebbian_edges" in tables}
    if side["available"]:
        gs = HebbianGraphStore.__new__(HebbianGraphStore)
        gs.conn = con
        hippo = Hippocampus()
        gs.load_into(hippo)
        current = CuriosityEngine().find_curious_nodes(hippo, top_k=args.top)
        trigger = sorted((c for c, d in per.items() if d["errors"]),
                         key=lambda c: (-per[c]["errors"], -(per[c]["error_rate"] or 0), c))[:args.top]

        def row(c, score):
            return {"concept": c, "score": score, "in_degree": hippo.in_degree(c),
                    "claims": claim_count.get(c, 0), "noise": c in NOISE,
                    "errors": per[c]["errors"] if c in per else 0,
                    "rows": per[c]["rows"] if c in per else 0}
        cur_rows = [row(n.node_id, round(n.score, 2)) for n in current]
        trig_rows = [row(c, per[c]["errors"]) for c in trigger]
        cur_set, trig_set = {r["concept"] for r in cur_rows}, {r["concept"] for r in trig_rows}
        side.update({
            "current": cur_rows, "trigger": trig_rows,
            "overlap": len(cur_set & trig_set),
            "jaccard": round(len(cur_set & trig_set) / max(1, len(cur_set | trig_set)), 3),
            "noise_in_current": sum(1 for r in cur_rows if r["noise"]),
            "noise_in_trigger": sum(1 for r in trig_rows if r["noise"]),
            "current_without_a_claim": sum(1 for r in cur_rows if r["claims"] == 0),
        })
        print(f"\n[6] side by side (top {args.top}): overlap {side['overlap']}, jaccard "
              f"{side['jaccard']}; noise words in current {side['noise_in_current']}, in "
              f"trigger {side['noise_in_trigger']}; current rows with no claim "
              f"{side['current_without_a_claim']}")
        print(f"      {'CURRENT (degree x 0.5)':44s} | {'ERROR TRIGGER (errors/rows)':44s}")
        for i in range(max(len(cur_rows), len(trig_rows))):
            l = cur_rows[i] if i < len(cur_rows) else None
            r = trig_rows[i] if i < len(trig_rows) else None
            ls = f"{l['score']:6.2f} deg={l['in_degree']:3d} cl={l['claims']:3d} {l['concept'][:22]!r}" if l else ""
            rs = f"{r['errors']:3d}/{r['rows']:<3d} deg={r['in_degree']:3d} cl={r['claims']:3d} {r['concept'][:22]!r}" if r else ""
            print(f"      {ls:44s} | {rs:44s}")
    report["side_by_side"] = side
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
