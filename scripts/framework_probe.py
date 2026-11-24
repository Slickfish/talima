"""PACKET AY — is a completeness framework derivable from this store? Read-only.

    python scripts/framework_probe.py [--kb data/kb.db] [--top 20]
        [--out framework.json]

The partial completeness assumption (Recoin, Balaraman/Razniewski/Nutt
2018): for an entity of class C, the relations most members of C carry are
expected of every member, and a member lacking one is relatively
incomplete. The expectation is computed at class level and applied at
instance level. Here the store is populated by inquiry, not by editors
aiming at coverage, so the framework encodes WHAT HAS BEEN ASKED and a gap
is "what was asked of this entity's peers and never of it". This probe
asks whether such a framework, the not-applicable discipline and the test
are each constructible from what exists, and builds nothing.

PRE-REGISTERED, before any number exists (the constants below):
  EXPECTATION_THRESHOLD  a relation is expected of a member when at least
                         this share of its PEERS (leave-one-out) carry it;
  MIN_CLASS_SIZE         the smallest class the arithmetic supports — with
                         n members, leave-one-out resolves shares in steps
                         of 1/(n-1), and a step wider than the margin to the
                         threshold flips the verdict on a single claim;
  FUNCTIONAL_SHARE       a relation is near-functional in a class when at
                         least this share of the members carrying it carry
                         exactly one object.
  THE TEST: precision of surfaced gaps, judged by the operator over cycles,
  against a random draw from ALL framework-covered cells (the null that
  asks whether the framework adds anything). The design under test is
  "surface every expected-but-empty cell", unranked, so a draw from
  expected cells IS the design and is not a null. Pass criterion:
  precision exceeds the null by 0.20 over at least 30 judged cells. A
  not-applicable cell scores as a miss until NA is expressible.

  [1] typing claims: every live non-substrate row whose normalised relation
      is `is_a` (gate.py RELATION_ALIASES), plus `instance_of` and
      `subclass_of` kept apart as the alias map deliberately keeps them;
      raw spellings; counts by evidence class. MEMBERSHIP COMES FROM
      asserted AND external CLAIMS ONLY — a class the model asserted is a
      framework the model wrote.
  [2] the classes that yields, printed with their members and each
      membership's source; the size distribution; the viable ones.
  [3] whether typing is expressible: object reuse (a class name used by
      two or more subjects), long descriptive objects, and scaffolding —
      typing subjects that are pronouns, filler or null literals.
  [4] the relation profile of every viable class, printed in full, from all
      sources and from asserted+external only, with the evidence class of
      each cell — question 16's number falls out of the same table.
  [5] cardinality per (class, relation): distinct objects per member; which
      relations qualify as near-functional.
  [6] leave-one-out, unconditionally: the number of (member, relation)
      verdicts that flip when the member's own claims are removed, and the
      mean shift, beside the 1/n arithmetic.
  [7] the gaps the framework would surface — expected (leave-one-out) and
      empty — and the cells filled ONLY by extraction ("present but
      unverified"); the two null universes sized.
  [8] not-applicable: what the gate does with the naive encoding
      (`X | has_y | none`), demonstrated on the gate itself.
  [9] declared-dark: `framework.derivable` with a named reason, so "no
      framework" is never reported as "no gaps"; and the framework hash a
      snapshot block could carry.

Store opened mode=ro; md5 before and after. Nothing is written but the
report.
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

EXPECTATION_THRESHOLD = 0.5
MIN_CLASS_SIZE = 5
FUNCTIONAL_SHARE = 0.8
TYPING = ("is_a", "instance_of", "subclass_of")
FRAMEWORK_SOURCES = ("asserted", "external")
NA_PROBES = (("X", "has_active_metabolite", "none"),
             ("X", "has_active_metabolite", "not applicable"),
             ("X", "has_active_metabolite", "n/a"))


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "-"


def _bucket(n: int) -> str:
    return "1" if n <= 1 else "2" if n == 2 else "3-4" if n <= 4 else "5-9" if n <= 9 else "10+"


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", default="framework.json")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    from backend.kb.gate import (FILLER_TOKENS, NULL_LITERALS, PRONOUNS,
                                 inspect_triple, normalise_relation)
    from backend.kb.schema import SUBSTRATE_RELATIONS
    from backend.kb.tiered_store import classify

    kb = Path(args.kb)
    if not kb.is_file():
        print(f"MISSING: {kb}")
        return 2
    md5_before = _md5(kb)
    con = open_read_only(kb, row_factory=None)
    con.row_factory = sqlite3.Row
    report: dict = {"kb": str(kb), "pre_registered": {
        "expectation_threshold": EXPECTATION_THRESHOLD, "min_class_size": MIN_CLASS_SIZE,
        "functional_share": FUNCTIONAL_SHARE,
        "test": ("precision of surfaced gaps vs a random draw from ALL framework-covered "
                 "cells; pass = precision exceeds null by 0.20 over >= 30 judged cells; "
                 "NA scores as a miss until expressible")}}

    rows = [r for r in con.execute(
        "SELECT id, subject, relation, object, source FROM triples WHERE archived = 0")
        if r["relation"] not in SUBSTRATE_RELATIONS]
    claims = [{"id": r["id"], "s": r["subject"], "r": normalise_relation(r["relation"]),
               "raw": r["relation"], "o": r["object"], "cls": classify(r["source"]),
               "src": r["source"]} for r in rows]

    # ---- [1] typing claims ------------------------------------------------------------
    typing = [c for c in claims if c["r"] in TYPING]
    by_rel = Counter(c["r"] for c in typing)
    by_cls = Counter(c["cls"] for c in typing)
    raw_spellings = Counter(c["raw"] for c in typing)
    report["typing"] = {
        "claims": len(claims), "typing_claims": len(typing),
        "by_relation": dict(by_rel), "by_evidence_class": dict(by_cls),
        "raw_spellings": dict(raw_spellings),
        "framework_grade": sum(1 for c in typing if c["cls"] in FRAMEWORK_SOURCES),
        "extraction_only": sum(1 for c in typing if c["cls"] == "extraction"),
    }
    t = report["typing"]
    print(f"[1] {len(claims)} live claims; typing claims {len(typing)} {dict(by_rel)}; by "
          f"evidence class {dict(by_cls)}; framework-grade (asserted+external) "
          f"{t['framework_grade']}, extraction {t['extraction_only']}; spellings "
          f"{dict(raw_spellings)}")

    # ---- [2] the classes --------------------------------------------------------------
    members: dict[str, dict[str, str]] = defaultdict(dict)      # class -> member -> cls
    members_any: dict[str, set[str]] = defaultdict(set)
    for c in typing:
        members_any[c["o"]].add(c["s"])
        if c["cls"] in FRAMEWORK_SOURCES:
            members[c["o"]][c["s"]] = c["cls"]
    sizes = Counter(_bucket(len(m)) for m in members.values())
    viable = {k: v for k, v in members.items() if len(v) >= MIN_CLASS_SIZE}
    viable_any = sum(1 for m in members_any.values() if len(m) >= MIN_CLASS_SIZE)
    report["classes"] = {
        "classes_framework_grade": len(members), "size_distribution": dict(sizes),
        "viable": len(viable), "viable_if_extraction_counted": viable_any,
        "list": {k: v for k, v in sorted(members.items(), key=lambda kv: -len(kv[1]))},
    }
    print(f"\n[2] classes from framework-grade typing {len(members)} (sizes {dict(sizes)}); "
          f"viable at >= {MIN_CLASS_SIZE}: {len(viable)}; if extraction counted: {viable_any}")
    for k, v in list(report["classes"]["list"].items())[:args.top]:
        print(f"      {k!r} ({len(v)}): " + ", ".join(f"{m} [{c}]" for m, c in v.items()))

    # ---- [3] is typing expressible ---------------------------------------------------------
    objs = Counter(c["o"] for c in typing)
    reused = sum(1 for o, n in objs.items() if len(members_any[o]) >= 2)
    long_objs = [o for o in objs if len(o.split()) > 4]
    scaffold = [c for c in typing if (c["s"].lower() in FILLER_TOKENS or c["s"].lower() in PRONOUNS
                                      or c["s"].lower() in NULL_LITERALS)]
    report["expressibility"] = {
        "distinct_typing_objects": len(objs), "objects_with_two_or_more_subjects": reused,
        "long_descriptive_objects": len(long_objs), "examples_long": long_objs[:args.top],
        "scaffolding_typing_claims": len(scaffold),
        "examples_scaffolding": [f"{c['s']} | {c['raw']} | {c['o']}" for c in scaffold[:args.top]],
    }
    e = report["expressibility"]
    print(f"\n[3] typing objects {len(objs)}, reused by 2+ subjects {reused}, long descriptive "
          f"{len(long_objs)}; scaffolding subjects {len(scaffold)}")
    for s in e["examples_long"][:5]:
        print(f"      long object: {s!r}")
    for s in e["examples_scaffolding"][:5]:
        print(f"      scaffolding: {s}")

    # ---- [4]-[7] profiles, cardinality, leave-one-out, gaps ---------------------------------
    by_subject: dict[str, list[dict]] = defaultdict(list)
    for c in claims:
        if c["r"] not in TYPING:
            by_subject[c["s"]].append(c)
    profiles: dict = {}
    gaps: list = []
    unverified: list = []
    flips = 0
    shifts: list[float] = []
    cells_all = cells_expected = 0
    for cname, mem in sorted(viable.items(), key=lambda kv: -len(kv[1])):
        n = len(mem)
        has: dict[str, dict[str, list[dict]]] = {m: defaultdict(list) for m in mem}
        for m in mem:
            for c in by_subject.get(m, []):
                has[m][c["r"]].append(c)
        rels = sorted({r for m in mem for r in has[m]})
        prof = {}
        for r in rels:
            carriers = [m for m in mem if has[m][r]]
            fw_carriers = [m for m in carriers if any(c["cls"] in FRAMEWORK_SOURCES for c in has[m][r])]
            card = [len({c["o"] for c in has[m][r]}) for m in carriers]
            single = sum(1 for k in card if k == 1)
            prof[r] = {
                "share_all_sources": round(len(carriers) / n, 3),
                "share_asserted_external": round(len(fw_carriers) / n, 3),
                "carriers": len(carriers), "extraction_only_carriers": len(carriers) - len(fw_carriers),
                "cardinality": dict(Counter(_bucket(k) for k in card)),
                "near_functional": bool(card) and single / len(card) >= FUNCTIONAL_SHARE,
            }
            for m in mem:
                cells_all += 1
                share_with = len(carriers) / n
                share_loo = (len(carriers) - (1 if has[m][r] else 0)) / (n - 1)
                shifts.append(abs(share_with - share_loo))
                exp_with = share_with >= EXPECTATION_THRESHOLD
                exp_loo = share_loo >= EXPECTATION_THRESHOLD
                if exp_with != exp_loo:
                    flips += 1
                if exp_loo:
                    cells_expected += 1
                    if not has[m][r]:
                        gaps.append({"class": cname, "member": m, "relation": r,
                                     "peer_share": round(share_loo, 3)})
                    elif not any(c["cls"] in FRAMEWORK_SOURCES for c in has[m][r]):
                        unverified.append({"class": cname, "member": m, "relation": r})
        profiles[cname] = {"n": n, "relations": prof}
    report["profiles"] = profiles
    report["leave_one_out"] = {
        "cells": cells_all, "verdict_flips": flips,
        "mean_shift": round(statistics.mean(shifts), 4) if shifts else None,
        "arithmetic_max_shift_by_class": {k: round(1 / len(v), 3) for k, v in viable.items()},
    }
    report["gaps"] = {"expected_and_empty": gaps, "present_but_extraction_only": unverified,
                      "null_universe_all_cells": cells_all,
                      "null_universe_expected_cells": cells_expected}
    print(f"\n[4] relation profiles for {len(viable)} viable classes:")
    for cname, p in profiles.items():
        print(f"      class {cname!r} (n={p['n']}):")
        for r, d in sorted(p["relations"].items(), key=lambda kv: -kv[1]["share_all_sources"]):
            print(f"        {r:32s} share {d['share_all_sources']:.2f} (asserted+external "
                  f"{d['share_asserted_external']:.2f}) card {d['cardinality']} "
                  f"{'near-functional' if d['near_functional'] else ''}")
    lo = report["leave_one_out"]
    print(f"\n[6] leave-one-out over {cells_all} cells: verdict flips {flips}, mean shift "
          f"{lo['mean_shift']}, arithmetic max shift {lo['arithmetic_max_shift_by_class']}")
    print(f"\n[7] gaps (expected by peers, empty) {len(gaps)}; present-but-extraction-only "
          f"{len(unverified)}; null universes: all cells {cells_all}, expected cells "
          f"{cells_expected}")
    for g in gaps[:args.top]:
        print(f"      GAP  {g['member']!r} lacks {g['relation']!r} (peers {g['peer_share']:.2f}) in {g['class']!r}")
    for u in unverified[:args.top]:
        print(f"      UNVERIFIED  {u['member']!r} {u['relation']!r} only by extraction in {u['class']!r}")

    # ---- [8] not-applicable ---------------------------------------------------------------
    na = {}
    for s, r, o in NA_PROBES:
        na[f"{s} | {r} | {o}"] = inspect_triple(s, r, o, evidence_class="asserted") or "ADMITTED"
    report["not_applicable"] = {
        "gate_verdicts_on_naive_encoding": na,
        "mechanism_today": ("none: no record kind states that a cell is known empty; the "
                            "gate's NULL_LITERALS refuses the naive object form (gate.py), "
                            "and the negated relations in CONTRADICTION_PAIRS (is_not_a, "
                            "excludes, prevents) name an opposite claim, not an empty cell"),
    }
    print(f"\n[8] naive NA encodings at the gate: {na}")

    # ---- [9] declared-dark, and the hash ----------------------------------------------------
    if not typing:
        reason = "no typing claims: nothing to plot against"
    elif not members:
        reason = "typing claims exist but none from asserted or external sources"
    elif not viable:
        reason = f"no class reaches MIN_CLASS_SIZE={MIN_CLASS_SIZE}"
    else:
        reason = ""
    canonical = json.dumps({k: {r: d["share_all_sources"] for r, d in p["relations"].items()}
                            for k, p in profiles.items()}, sort_keys=True)
    report["framework"] = {
        "derivable": not reason, "reason": reason or "viable classes with relation profiles",
        "hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest() if not reason else None,
        "external_share_of_framework_memberships": (
            round(sum(1 for v in viable.values() for c in v.values() if c == "external")
                  / max(1, sum(len(v) for v in viable.values())), 3) if viable else None),
    }
    fw = report["framework"]
    print(f"\n[9] framework derivable: {fw['derivable']} — {fw['reason']}; hash {fw['hash']}; "
          f"external share of memberships {fw['external_share_of_framework_memberships']}")
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
