"""INVESTIGATE the citation test's false-positive class — read-only.

    python scripts/citation_fp_probe.py [--kb data/kb.db]
        [--capture data/citation_capture.jsonl] [--labels labelled.jsonl]
        [--df-ceiling 0.5] [--top 15] [--out citation_fp.json]

`is_cited` (backend/kb/citation.py) marks a belief used when BOTH its
subject and its object appear as whole tokens in the answer. The pre-gate
probe found forty gate-refused rows exempt from archival under I-4 because
a sealed record cites them — `tell | co_activated | know`, `about |
co_activated | web_framework`, `BBC | ... | None` — terms that appear in
almost any answer. This probe prints what distinguishes those citations
from the rest, and tests candidate RULES (never a list) against the same
evidence:

  [1] the cited population, characterised: for every cited item across all
      lineage records — term length, token count, whether every token is
      gate filler, null literal, substrate relation, the gate's own verdict
      on the row, sealed or not, and the term's document frequency over the
      captured answers (how many answers contain it as a whole token).
      Printed as distributions for gate-refused vs gate-accepted citations.
  [2] candidate rules, each re-run over the CAPTURED turns (the only turns
      whose answer text exists), reporting per rule: citations it removes
      among gate-refused rows (closure) and among gate-accepted rows (the
      risk — printed in full, because a rule that un-cites a genuine use is
      worse than the defect). With --labels (AP.4's labelled queue, or any
      jsonl of {lineage_id, belief_id, label}), each rule is also scored
      against human labels.
        min_chars      both terms >= N characters (N = 4 and 6)
        informative    each term has >= 1 token outside the gate's filler
                       and null sets (the gate's own judgement, composed)
        df_ceiling     at least one term appears in <= --df-ceiling of the
                       answers (a term in every answer carries no signal)
        no_substrate   substrate relations (co_activated, ...) never cite
        gate_composed  a row the gate would refuse never cites
        recommended    informative AND no_substrate
  [3] the false-negative shape, sized: injected items NOT cited where the
      subject is mentioned and the object's tokens mostly are — the
      paraphrase / partial-match shape — to show it is a different problem.
  [4] blast radius, counted: sealed records whose cited flags a rule would
      have changed (they cannot change — sealed — so this is the population
      the rule would NOT reach), and the I-4 exemptions resting on them.

Read-only: the store is opened mode=ro and its md5 printed before and
after. Nothing is written, nothing is changed, no rule is installed.
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


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "-"


def _dist(vals) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "min": min(vals), "median": statistics.median(vals),
            "mean": round(statistics.mean(vals), 2), "max": max(vals)}


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--capture", default="data/citation_capture.jsonl")
    ap.add_argument("--labels", default=None)
    ap.add_argument("--df-ceiling", type=float, default=0.5)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default="citation_fp.json")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    from backend.kb.citation import _mentions, normalise
    from backend.kb.gate import FILLER_TOKENS, NULL_LITERALS, _tokens, inspect_triple
    from backend.kb.schema import SUBSTRATE_RELATIONS
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
    if not {"triples", "lineage_records"} <= tables:
        print("NO RECORDS: need triples and lineage_records")
        return 2
    store = TripleStore.__new__(TripleStore)
    store.conn = con
    by_id = {r["id"]: store._row_to_triple(r)
             for r in con.execute("SELECT * FROM triples")}

    answers: dict[str, str] = {}
    cap = Path(args.capture)
    if cap.is_file():
        for line in cap.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                answers[row["lineage_id"]] = normalise(row["answer"])
    labels: dict[tuple, str] = {}
    if args.labels and Path(args.labels).is_file():
        for line in Path(args.labels).read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                labels[(row["lineage_id"], int(row["belief_id"]))] = row["label"]

    # ---- term features -------------------------------------------------------
    def informative(term: str) -> bool:
        toks = _tokens((term or "").lower())
        return any(t not in FILLER_TOKENS and t not in NULL_LITERALS for t in toks)

    df_cache: dict[str, float] = {}

    def doc_freq(term: str) -> float | None:
        if not answers:
            return None
        if term not in df_cache:
            df_cache[term] = sum(1 for a in answers.values() if _mentions(a, term)) / len(answers)
        return df_cache[term]

    def gate_verdict(t) -> str | None:
        return inspect_triple(t.subject, t.relation, t.object,
                              evidence_class=classify(t.source or ""), weight=t.weight)

    # ---- [1] the cited population -------------------------------------------
    sealed_ids, lstats = sealed_cited_belief_ids(con)
    cited_items = []
    for rec in con.execute("SELECT lineage_id, sealed_block, injected_json FROM lineage_records"):
        for it in json.loads(rec["injected_json"] or "[]"):
            bid = it.get("belief_id")
            t = by_id.get(bid)
            if t is None or it.get("cited") is False:
                continue
            cited_items.append({
                "lineage_id": rec["lineage_id"], "belief_id": bid,
                "sealed": rec["sealed_block"] is not None,
                "flag": it.get("cited"),
                "subject": t.subject, "object": t.object, "relation": t.relation,
                "s_chars": len(t.subject), "o_chars": len(t.object),
                "s_tokens": len(_tokens(t.subject.lower())),
                "o_tokens": len(_tokens(t.object.lower())),
                "s_informative": informative(t.subject),
                "o_informative": informative(t.object),
                "substrate": t.relation in SUBSTRATE_RELATIONS,
                "gate": gate_verdict(t),
                "s_df": doc_freq(t.subject), "o_df": doc_freq(t.object),
            })
    refused_c = [c for c in cited_items if c["gate"]]
    accepted_c = [c for c in cited_items if not c["gate"]]

    def profile(items) -> dict:
        return {
            "citations": len(items),
            "distinct_beliefs": len({c["belief_id"] for c in items}),
            "sealed": sum(1 for c in items if c["sealed"]),
            "min_term_chars": _dist([min(c["s_chars"], c["o_chars"]) for c in items]),
            "min_term_tokens": _dist([min(c["s_tokens"], c["o_tokens"]) for c in items]),
            "both_informative": sum(1 for c in items if c["s_informative"] and c["o_informative"]),
            "neither_informative": sum(1 for c in items
                                       if not c["s_informative"] and not c["o_informative"]),
            "substrate": sum(1 for c in items if c["substrate"]),
            "max_term_df": _dist([max(c["s_df"], c["o_df"]) for c in items
                                  if c["s_df"] is not None]),
            "min_term_df": _dist([min(c["s_df"], c["o_df"]) for c in items
                                  if c["s_df"] is not None]),
        }
    report: dict = {"lineage": lstats, "answers_available": len(answers),
                    "cited": {"gate_refused": profile(refused_c),
                              "gate_accepted": profile(accepted_c)}}
    print(f"[1] cited items {len(cited_items)} across {lstats.get('records_total')} records "
          f"({len(answers)} answers captured); gate-refused {len(refused_c)}, "
          f"gate-accepted {len(accepted_c)}")
    for name, prof in report["cited"].items():
        print(f"    {name}: {json.dumps(prof)}")
    sealed_refused = sorted({(c["belief_id"], c["subject"], c["relation"], c["object"],
                              c["gate"]) for c in refused_c if c["sealed"]})
    print(f"    sealed-cited gate-refused beliefs: {len(sealed_refused)}")
    for bid, s, r, o, g in sealed_refused[:args.top]:
        print(f"      id={bid} [{g}] {s} | {r} | {o}   df(s)={doc_freq(s)} df(o)={doc_freq(o)}")

    # ---- [2] candidate rules over the captured turns ------------------------
    def cited_under(rule: str, t, ans: str) -> bool:
        base = _mentions(ans, t.subject) and _mentions(ans, t.object)
        if not base:
            return False
        if rule == "current":
            return True
        if rule == "min_chars_4":
            return len(t.subject) >= 4 and len(t.object) >= 4
        if rule == "min_chars_6":
            return len(t.subject) >= 6 and len(t.object) >= 6
        if rule == "informative":
            return informative(t.subject) and informative(t.object)
        if rule == "df_ceiling":
            return min(doc_freq(t.subject) or 0, doc_freq(t.object) or 0) <= args.df_ceiling
        if rule == "no_substrate":
            return t.relation not in SUBSTRATE_RELATIONS
        if rule == "gate_composed":
            return gate_verdict(t) is None
        if rule == "recommended":
            return (informative(t.subject) and informative(t.object)
                    and t.relation not in SUBSTRATE_RELATIONS)
        raise ValueError(rule)

    RULES = ("current", "min_chars_4", "min_chars_6", "informative", "df_ceiling",
             "no_substrate", "gate_composed", "recommended")
    captured = [(rec["lineage_id"], json.loads(rec["injected_json"] or "[]"))
                for rec in con.execute("SELECT lineage_id, injected_json FROM lineage_records")
                if rec["lineage_id"] in answers]
    results = {}
    for rule in RULES:
        removed_refused, removed_accepted, kept = [], [], 0
        tp = fp = fn = tn = 0
        for lid, injected in captured:
            ans = answers[lid]
            for it in injected:
                t = by_id.get(it.get("belief_id"))
                if t is None:
                    continue
                now = cited_under("current", t, ans)
                new = cited_under(rule, t, ans)
                lab = labels.get((lid, t.id))
                if lab in ("used", "unused"):
                    if new and lab == "used":
                        tp += 1
                    elif new and lab == "unused":
                        fp += 1
                    elif not new and lab == "used":
                        fn += 1
                    else:
                        tn += 1
                if now and not new:
                    (removed_refused if gate_verdict(t) else removed_accepted).append(
                        {"lineage_id": lid[:8], "belief_id": t.id,
                         "triple": f"{t.subject} | {t.relation} | {t.object}",
                         "label": lab})
                elif new:
                    kept += 1
        results[rule] = {
            "cited": kept,
            "removed_gate_refused": len(removed_refused),
            "removed_gate_accepted": len(removed_accepted),
            "removed_accepted_samples": removed_accepted[:args.top],
            "labelled": ({"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                          "precision": tp / (tp + fp) if tp + fp else None,
                          "recall": tp / (tp + fn) if tp + fn else None}
                         if labels else None),
        }
    report["rules"] = results
    print(f"\n[2] rules over {len(captured)} captured turns (df ceiling {args.df_ceiling}):")
    for rule, r in results.items():
        line = (f"    {rule:14} cited {r['cited']:4d}  removes refused {r['removed_gate_refused']:3d}"
                f"  removes ACCEPTED {r['removed_gate_accepted']:3d}")
        if r["labelled"]:
            line += f"  P={r['labelled']['precision']} R={r['labelled']['recall']}"
        print(line)
    for rule, r in results.items():
        if r["removed_accepted_samples"]:
            print(f"    {rule} would un-cite these gate-ACCEPTED rows (the risk):")
            for x in r["removed_accepted_samples"]:
                print(f"      {x['lineage_id']} id={x['belief_id']} {x['triple']}"
                      f"{'  label=' + x['label'] if x['label'] else ''}")

    # ---- [3] the false-negative shape ----------------------------------------
    near = []
    for lid, injected in captured:
        ans = answers[lid]
        for it in injected:
            t = by_id.get(it.get("belief_id"))
            if t is None or it.get("cited") is not False:
                continue
            s_hit = _mentions(ans, t.subject)
            o_toks = [x for x in _tokens(t.object.lower()) if x not in FILLER_TOKENS]
            o_frac = (sum(1 for x in o_toks if _mentions(ans, x)) / len(o_toks)) if o_toks else 0
            if s_hit and o_frac >= 0.5:
                near.append({"lineage_id": lid[:8], "belief_id": t.id,
                             "object_token_overlap": round(o_frac, 2),
                             "triple": f"{t.subject} | {t.relation} | {t.object}",
                             "label": labels.get((lid, t.id))})
    uncited_total = sum(1 for _l, inj in captured for it in inj if it.get("cited") is False)
    report["false_negative_shape"] = {"uncited_items": uncited_total,
                                      "near_misses": len(near),
                                      "samples": near[:args.top]}
    print(f"\n[3] uncited items {uncited_total}; near-misses (subject mentioned, >=50% of the "
          f"object's informative tokens mentioned) {len(near)} — the paraphrase shape")
    for x in near[:args.top]:
        print(f"      {x['lineage_id']} id={x['belief_id']} overlap={x['object_token_overlap']} "
              f"{x['triple']}{'  label=' + x['label'] if x['label'] else ''}")

    # ---- [4] blast radius, counted ---------------------------------------------
    blast = {}
    for rule in RULES[1:]:
        would_change = set()
        exempt_lost = set()
        for c in cited_items:
            t = by_id[c["belief_id"]]
            stays = (cited_under(rule, t, answers[c["lineage_id"]])
                     if c["lineage_id"] in answers else None)
            if stays is False:
                if c["sealed"]:
                    would_change.add(c["lineage_id"])
                    exempt_lost.add(c["belief_id"])
        blast[rule] = {"sealed_records_whose_flags_would_have_differed": len(would_change),
                       "exemptions_resting_on_them": len(exempt_lost),
                       "note": "sealed flags cannot change; these stay as recorded"}
    report["blast_radius"] = blast
    print("\n[4] sealed records whose cited flags a rule WOULD have set differently "
          "(they cannot change) and the I-4 exemptions resting on them:")
    for rule, b in blast.items():
        print(f"    {rule:14} records {b['sealed_records_whose_flags_would_have_differed']:3d}  "
              f"exemptions {b['exemptions_resting_on_them']:3d}")
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
