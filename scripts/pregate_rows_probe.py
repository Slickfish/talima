"""INVESTIGATE the pre-gate rows — read-only, report, recommend nothing here.

    python scripts/pregate_rows_probe.py [--kb data/kb.db]
        [--tokeniser stub|hf] [--hf-repo REPO | --tokenizer-json PATH]
        [--top 10] [--all] [--out pregate_rows.json]

AS measured malformed claims at 15.2% of injected prompt tokens. Tokens are
not rows: this probe reports the ROWS, so the disposition can be argued on
what would actually be touched.

  [1] rows      every LIVE row the gate would refuse today under its own
                evidence class, by rule, by class, by source — the gate's
                rules replayed over the stored strings (AS's reading) and,
                beside it, over the normalised strings (verify_packet_k's
                reading), with the rows where the two verdicts differ.
  [2] tokens    the rendered surface of each refused row counted with the
                answering model's tokeniser (or a stub, marked); the
                distribution, and the longest few printed in full.
  [3] injection how many refused rows ever reached a prompt (from lineage),
                how often, and their share of all injected token mass —
                store-wide, so it can be set beside AS's 85-turn figure.
  [4] exempt    refused rows cited by a SEALED lineage record (I-4 — the
                same function L.2b's pass uses, so the number read here is
                the set exempted there), printed in full; refused rows cited
                only in UNSEALED records, which become exempt at the next
                seal; refused rows injected and never cited.
  [5] dates     when the refused rows were written, against the two dates
                the rules shipped (2026-08-16 the gate, 2026-09-04 the
                relation-level null literal) — a refused row written AFTER
                its rule shipped entered through a lenient class or with the
                gate off, and that is a different finding.
  [6] surfaces  every refused surface grouped by rule (full text with
                --all; the input to a decision, not the decision — see
                verify_packet_k.py [3b] for the per-class list L.2c is
                gated on).

Nothing is written to the store: opened read-only, md5 printed before and
after. This script archives nothing, ever.
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

from scripts._as import (                                        # noqa: E402
    TokeniserUnavailable, hf_tokeniser, stub_tokeniser, tokeniser_repo_for)
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402

# When each rule first shipped (git log -S over backend/kb/gate.py). The
# whole gate landed in one commit; the relation-level null literal came
# later (the Hemoglobin incident). Data, so the probe can bucket rows.
RULE_SHIPPED = {
    "gate": ("2026-08-16", "b9be2e3"),
    "relation:null_literal": ("2026-09-04", "ad6681d"),
}


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "-"


def _ts(date: str) -> float:
    return time.mktime(time.strptime(date, "%Y-%m-%d"))


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--tokeniser", choices=("stub", "hf"), default="stub")
    ap.add_argument("--hf-repo", default=None)
    ap.add_argument("--tokenizer-json", default=None)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--all", action="store_true",
                    help="print every refused surface in full, grouped by rule")
    ap.add_argument("--out", default="pregate_rows.json")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    from backend.kb.gate import inspect_triple, normalise_relation, normalise_term
    from backend.kb.retriever import RetrievedTriple
    from backend.kb.tiered_store import TripleStore, classify
    from backend.memory.lineage import sealed_cited_belief_ids

    kb = Path(args.kb)
    if not kb.is_file():
        print(f"MISSING: {kb}")
        return 2
    md5_before = _md5(kb)
    con = open_read_only(kb, row_factory=None)
    con.row_factory = sqlite3.Row
    tables = {r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "triples" not in tables:
        print("NO RECORDS: no triples table")
        return 2
    store = TripleStore.__new__(TripleStore)       # read-only: _row_to_triple only
    store.conn = con
    live = [store._row_to_triple(r) for r in con.execute(
        "SELECT * FROM triples WHERE archived = 0 ORDER BY id")]
    report: dict = {"live_rows": len(live)}

    # ---- [1] rows ------------------------------------------------------------
    refused: dict[int, dict] = {}
    by_rule: Counter = Counter()
    by_class: Counter = Counter()
    class_total: Counter = Counter()
    by_source: Counter = Counter()
    differs = []
    for t in live:
        cls = classify(t.source or "")
        class_total[cls] += 1
        stored = inspect_triple(t.subject, t.relation, t.object,
                                evidence_class=cls, weight=t.weight)
        normal = inspect_triple(normalise_term(t.subject),
                                normalise_relation(t.relation),
                                normalise_term(t.object),
                                evidence_class=cls, weight=t.weight)
        if (stored is None) != (normal is None) or (
                stored and normal and stored.split(":")[:2] != normal.split(":")[:2]):
            differs.append({"id": t.id, "stored_verdict": stored,
                            "normalised_verdict": normal,
                            "triple": f"{t.subject} | {t.relation} | {t.object}"})
        if stored is None:
            continue
        rule = ":".join(stored.split(":")[:2])
        refused[t.id] = {"id": t.id, "rule": rule, "reason": stored,
                         "class": cls, "source": t.source,
                         "created_at": t.created_at, "tier": t.tier,
                         "weight": t.weight,
                         "triple": f"{t.subject} | {t.relation} | {t.object}",
                         "surface": RetrievedTriple(triple=t, score=t.weight).render()}
        by_rule[rule] += 1
        by_class[cls] += 1
        by_source[t.source] += 1
    report["refused"] = {
        "rows": len(refused),
        "share_of_live": (len(refused) / len(live)) if live else None,
        "by_rule": dict(by_rule.most_common()),
        "by_class": {c: {"refused": by_class.get(c, 0), "total": n}
                     for c, n in class_total.most_common()},
        "by_source": dict(by_source.most_common()),
        "stored_vs_normalised_differ": len(differs),
        "differ_samples": differs[:args.top],
    }
    print(f"[1] live rows {len(live)}; the gate would refuse {len(refused)} "
          f"({(len(refused) / len(live)) if live else 0:.1%}) under each row's own class")
    for rule, n in by_rule.most_common():
        print(f"      {rule:40} {n:5d}")
    print("    by class (refused / total):",
          {c: f"{by_class.get(c, 0)}/{n}" for c, n in class_total.most_common()})
    print(f"    stored-string vs normalised verdicts differ on {len(differs)} rows")
    for d in differs[:args.top]:
        print(f"      id={d['id']} stored={d['stored_verdict']} "
              f"normalised={d['normalised_verdict']}  {d['triple']}")

    # ---- [2] tokens -----------------------------------------------------------
    model_id = None
    if "lineage_records" in tables:
        row = con.execute("SELECT model_id, COUNT(*) AS n FROM lineage_records "
                          "GROUP BY model_id ORDER BY n DESC LIMIT 1").fetchone()
        model_id = row["model_id"] if row else None
    try:
        if args.tokeniser == "stub":
            count, inst = stub_tokeniser()
        else:
            count, inst = hf_tokeniser(
                tokenizer_json=args.tokenizer_json,
                repo=tokeniser_repo_for(model_id or "", args.hf_repo))
    except TokeniserUnavailable as e:
        print(f"TOKENISER UNAVAILABLE: {e} — exit 5")
        return 5
    for r in refused.values():
        r["tokens"] = count(r["surface"])
    toks = sorted((r["tokens"] for r in refused.values()), reverse=True)
    longest = sorted(refused.values(), key=lambda r: -r["tokens"])[:args.top]
    report["tokens"] = {
        "instrument": {**inst, "model_id": model_id},
        "total": sum(toks),
        "mean": statistics.mean(toks) if toks else None,
        "median": statistics.median(toks) if toks else None,
        "p90": toks[max(0, len(toks) // 10 - 1)] if toks else None,
        "max": toks[0] if toks else None,
        "longest": longest,
    }
    print(f"\n[2] tokens over refused rows ({inst['backend']}): total {sum(toks)}, "
          f"mean {report['tokens']['mean'] and round(report['tokens']['mean'], 1)}, "
          f"median {report['tokens']['median']}, max {report['tokens']['max']}")
    print(f"    the longest {len(longest)} in full:")
    for r in longest:
        print(f"      [{r['tokens']:4d}] ({r['rule']}) {r['surface']}")

    # ---- [3] injection / [4] exempt ------------------------------------------
    inj: Counter = Counter()
    cited_true: Counter = Counter()
    unsealed_cited: set = set()
    all_inj_mass = 0
    refused_inj_mass = 0
    surface_tokens: dict[int, int] = {}
    if "lineage_records" in tables:
        by_id = {t.id: t for t in live}
        for rec in con.execute("SELECT injected_json, sealed_block FROM lineage_records"):
            for it in json.loads(rec["injected_json"] or "[]"):
                bid = it.get("belief_id")
                if bid is None:
                    continue
                inj[bid] += 1
                if it.get("cited") is not False:
                    cited_true[bid] += 1
                    if rec["sealed_block"] is None:
                        unsealed_cited.add(bid)
                t = by_id.get(bid)
                if t is not None:
                    if bid not in surface_tokens:
                        surface_tokens[bid] = count(
                            RetrievedTriple(triple=t, score=t.weight).render())
                    all_inj_mass += surface_tokens[bid]
                    if bid in refused:
                        refused_inj_mass += surface_tokens[bid]
        sealed_ids, lstats = sealed_cited_belief_ids(con)
    else:
        sealed_ids, lstats = set(), {"error": "no lineage_records table"}
    ever = [r for r in refused.values() if inj.get(r["id"], 0) > 0]
    never = [r for r in refused.values() if inj.get(r["id"], 0) == 0]
    exempt = [dict(r, cited_sealed=True, injections=inj.get(r["id"], 0))
              for r in refused.values() if r["id"] in sealed_ids]
    pending = [r for r in refused.values()
               if r["id"] in unsealed_cited and r["id"] not in sealed_ids]
    uncited = [r for r in ever if cited_true.get(r["id"], 0) == 0]
    report["injection"] = {
        "refused_ever_injected": len(ever),
        "refused_never_injected": len(never),
        "injections_of_refused": sum(inj.get(r["id"], 0) for r in ever),
        "top_injected": sorted(({"id": r["id"], "injections": inj[r["id"]],
                                 "rule": r["rule"], "surface": r["surface"]}
                                for r in ever), key=lambda x: -x["injections"])[:args.top],
        "refused_share_of_injected_token_mass": (
            refused_inj_mass / all_inj_mass if all_inj_mass else None),
        "note": ("store-wide over every lineage record with a resolvable "
                 "belief, stub or real tokens as recorded above; AS's 15.2% "
                 "was 85 turns with the real tokeniser"),
    }
    report["exempt"] = {
        "sealed_cited_refused": len(exempt), "rows": exempt,
        "unsealed_cited_refused_pending_exemption": len(pending),
        "pending_rows": pending[:args.top],
        "injected_never_cited": len(uncited),
        "lineage": lstats,
    }
    print(f"\n[3] refused rows ever injected {len(ever)} ({sum(inj.get(r['id'], 0) for r in ever)} "
          f"injections), never injected {len(never)}; refused share of all injected "
          f"token mass "
          f"{'-' if all_inj_mass == 0 else f'{refused_inj_mass / all_inj_mass:.1%}'}")
    for x in report["injection"]["top_injected"]:
        print(f"      x{x['injections']:3d} ({x['rule']}) {x['surface']}")
    print(f"\n[4] refused AND cited by a sealed record (I-4 exempt): {len(exempt)}")
    for r in exempt:
        print(f"      id={r['id']} injected x{r['injections']} ({r['rule']}) {r['surface']}")
    print(f"    refused, cited only in unsealed records (exempt at next seal): {len(pending)}")
    print(f"    refused, injected, never cited: {len(uncited)}")
    print(f"    lineage: {lstats}")

    # ---- [5] dates ------------------------------------------------------------
    gate_ts = _ts(RULE_SHIPPED["gate"][0])
    null_ts = _ts(RULE_SHIPPED["relation:null_literal"][0])
    before_gate = [r for r in refused.values() if r["created_at"] < gate_ts]
    after_gate = [r for r in refused.values() if r["created_at"] >= gate_ts]
    after_by_rule = Counter(r["rule"] for r in after_gate)
    after_by_class = Counter(r["class"] for r in after_gate)
    rel_null_after = [r for r in refused.values()
                      if r["rule"] == "relation:null_literal" and r["created_at"] >= null_ts]
    report["dates"] = {
        "rules_shipped": RULE_SHIPPED,
        "refused_written_before_gate": len(before_gate),
        "refused_written_after_gate": len(after_gate),
        "after_gate_by_rule": dict(after_by_rule.most_common()),
        "after_gate_by_class": dict(after_by_class.most_common()),
        "relation_null_after_its_rule": len(rel_null_after),
        "note": ("a refused row written after its rule shipped entered "
                 "through a lenient class (the strict rules apply to "
                 "model-derived classes only) or with NEXUS_INGEST_GATE=off"),
    }
    print(f"\n[5] refused rows written before the gate shipped "
          f"({RULE_SHIPPED['gate'][0]}): {len(before_gate)}; after: {len(after_gate)}")
    if after_gate:
        print(f"    after, by rule: {dict(after_by_rule.most_common())}")
        print(f"    after, by class: {dict(after_by_class.most_common())}")
    print(f"    relation:null_literal rows written after its rule "
          f"({RULE_SHIPPED['relation:null_literal'][0]}): {len(rel_null_after)}")

    # ---- [6] surfaces ---------------------------------------------------------
    grouped: dict[str, list] = defaultdict(list)
    for r in refused.values():
        grouped[r["rule"]].append(r)
    print("\n[6] refused surfaces by rule" + (" (all)" if args.all else f" (top {args.top} each; --all for every row)"))
    for rule, rs in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        print(f"    {rule} ({len(rs)}):")
        for r in (rs if args.all else rs[:args.top]):
            flag = " [SEALED-CITED]" if r["id"] in sealed_ids else ""
            print(f"      id={r['id']} x{inj.get(r['id'], 0)} {r['triple']}{flag}")
    report["surfaces_by_rule"] = {rule: [{"id": r["id"], "triple": r["triple"],
                                          "injections": inj.get(r["id"], 0),
                                          "sealed_cited": r["id"] in sealed_ids}
                                         for r in rs]
                                  for rule, rs in grouped.items()}
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
