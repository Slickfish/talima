"""BA fixes 5 and 8 — the live rows the gate would refuse TODAY. Read-only.

    python scripts/gate_population_probe.py [--kb data/kb.db] [--top 20]
        [--out gate_population.json]

I-9 governs admission, not retrievability: a rule introduced today governs
what may enter from today, and rows admitted before it stay. This probe
runs the gate's own `inspect_triple` over every live row, with the row's
own evidence class and weight, exactly as `add()` would judge it now, and
reports:

  [1] the population: rows refused, by rule and by evidence class, with
      examples printed as surfaces; the share that a SEALED lineage record
      cites (I-4 exempt, `sealed_cited_belief_ids` — null counts as cited);
      and the population is DERIVED from the gate, never listed, so a rule
      change moves it and the number after fix 5 is the one to act on.
  [2] fix 5 alone: rows whose subject or object opens an imperative clause
      (`imperative_clause`), whether or not another rule refuses them
      first — the number the packet asked for, not a cleanup list.
  [3] the cost in prompt tokens: the token estimate of the rendered line for
      refused rows against all live rows, and how many injected items in
      sealed lineage records were refused rows.
  [4] the exemption question, measured: how many refused rows are exempt,
      and of those how many were cited only under the pre-AU (F.5 lexical)
      instrument — the forty the packet named.

Store opened mode=ro; md5 before and after. Nothing is written but the
report. Retrieval is injection (retriever.retrieve -> hits -> rendered), so
there is no "retrievable but not injected" state; fix 4 changed what a
line carries, not whether a hit is rendered.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
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


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default="gate_population.json")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    from backend.kb.gate import IMPERATIVE_MARKERS_VERSION, imperative_clause, inspect_triple
    from backend.kb.retriever import render_kb_line
    from backend.kb.tiered_store import classify
    from backend.loop.compaction import estimate_tokens_text
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
    rows = con.execute("SELECT id, subject, relation, object, source, weight, granularity, "
                       "created_at FROM triples WHERE archived = 0 ORDER BY id").fetchall()
    exempt_ids, lineage_stats = (sealed_cited_belief_ids(con) if "lineage_records" in tables
                                 else (set(), {}))

    # ---- [1] the population, derived from the gate -----------------------------------
    refused = []
    by_rule: Counter = Counter()
    by_class: Counter = Counter()
    tokens_all = tokens_refused = 0
    for r in rows:
        cls = classify(r["source"])
        line = render_kb_line(r["subject"], r["relation"], r["object"], r["weight"],
                              r["granularity"], cls)
        tk = estimate_tokens_text(line)
        tokens_all += tk
        reason = inspect_triple(r["subject"], r["relation"], r["object"],
                                evidence_class=cls, weight=r["weight"])
        imper = imperative_clause(r["subject"]) or imperative_clause(r["object"])
        if reason is None and imper is None:
            continue
        if reason is not None:
            by_rule[reason] += 1
            by_class[cls] += 1
            tokens_refused += tk
        refused.append({"id": r["id"], "reason": reason, "class": cls,
                        "exempt": r["id"] in exempt_ids,
                        "imperative_clause": imper,
                        "surface": f"{r['subject']} | {r['relation']} | {r['object']}",
                        "created_at": r["created_at"]})
    refused_rows = [x for x in refused if x["reason"] is not None]
    exempt = [x for x in refused_rows if x["exempt"]]
    imperative_only = [x for x in refused if x["imperative_clause"]]

    # ---- [4] the exemption, by instrument ------------------------------------------------
    old_only = 0
    if exempt and "lineage_records" in tables:
        cited_by_new: set[int] = set()
        for row in con.execute("SELECT injected_json, citation_test FROM lineage_records "
                               "WHERE sealed_block IS NOT NULL AND citation_test IS NOT NULL"):
            for it in json.loads(row["injected_json"] or "[]"):
                if isinstance(it, dict) and it.get("cited") is True:
                    cited_by_new.add(it.get("belief_id"))
        old_only = sum(1 for x in exempt if x["id"] not in cited_by_new)

    # ---- [3] injected refused rows in sealed records -------------------------------------
    injected_refused = 0
    injected_items = 0
    refused_id_set = {x["id"] for x in refused_rows}
    if "lineage_records" in tables:
        for row in con.execute("SELECT injected_json FROM lineage_records WHERE sealed_block IS NOT NULL"):
            for it in json.loads(row["injected_json"] or "[]"):
                if isinstance(it, dict):
                    injected_items += 1
                    if it.get("belief_id") in refused_id_set:
                        injected_refused += 1

    report = {
        "kb": str(kb), "markers_version": IMPERATIVE_MARKERS_VERSION,
        "live_rows": len(rows),
        "refused_by_gate_today": len(refused_rows),
        "refused_share": round(len(refused_rows) / len(rows), 4) if rows else None,
        "by_rule": dict(by_rule.most_common()), "by_class": dict(by_class),
        "exempt_sealed_cited": len(exempt), "exempt_cited_only_pre_AU": old_only,
        "not_exempt": len(refused_rows) - len(exempt),
        "imperative_shape_would_refuse": len(imperative_only),
        "imperative_examples": [(x["surface"], x["imperative_clause"]) for x in imperative_only[:args.top]],
        "prompt_tokens": {"all_live_rows": tokens_all, "refused_rows": tokens_refused,
                          "refused_share": round(tokens_refused / tokens_all, 4) if tokens_all else None},
        "sealed_injected_items": injected_items, "sealed_injected_items_refused": injected_refused,
        "lineage": lineage_stats,
        "examples": [x for x in refused_rows[:args.top]],
        "latest_refused_created_at": max((x["created_at"] for x in refused_rows), default=None),
        "population_is_gate_derived": True,
    }
    print(f"[1] live rows {len(rows)}; refused by the gate today {len(refused_rows)} "
          f"({report['refused_share']}); by rule {report['by_rule']}; by class {dict(by_class)}")
    print(f"    exempt (sealed-cited) {len(exempt)}, of which cited only under the pre-AU "
          f"instrument {old_only}; not exempt {report['not_exempt']}")
    for x in refused_rows[:12]:
        print(f"      {x['reason']:28s} {'EXEMPT ' if x['exempt'] else '       '}{x['surface']}")
    print(f"\n[2] imperative_shape would refuse {len(imperative_only)} rows "
          f"(markers {IMPERATIVE_MARKERS_VERSION})")
    for s, c in report["imperative_examples"][:8]:
        print(f"      {s}   <- clause {c!r}")
    print(f"\n[3] prompt tokens: refused rows {tokens_refused} of {tokens_all} "
          f"({report['prompt_tokens']['refused_share']}); sealed injected items that were "
          f"refused rows {injected_refused} of {injected_items}")
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
