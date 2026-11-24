"""AP.4 — the scorer. Imports NOTHING from the citation machinery it scores.

REDESIGNED for adjudication by exception, and it reports RANGES, not
points:

  FALSE POSITIVES — fully measured, no judge involved: the cited stratum
  was exhausted by hand, so the FP rate is exact over that stratum, with
  a Wilson 95% interval for sampling honesty.

  FALSE NEGATIVES — a range: the confirmed cases (human-used among
  lexical-uncited) are the floor; the ceiling adds the shared-error
  allowance from the seeded agreement sample (measured rate, or the rule
  of three when the sample showed zero) extrapolated over the unlabelled
  judge-unused remainder. Where the test and the judge err the same way,
  no human ever looked — that is the term the ceiling bounds, and the
  layout says so.

  Plus: judge-vs-human agreement (chance-corrected) on the cited stratum,
  where it is computable in full; WITHIN-GROUP INCONSISTENCY of the
  lexical test (one group, mixed flags — a free, direct measurement of
  surface sensitivity); unsure counted and excluded, with the
  high-unsure-on-cited finding named when it fires; the alias version and
  seeds echoed; and the grouped case list.

    python scripts/citation_judge_score.py citation_stripped.json \
        citation_judgements.json citation_queue.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402

SCALE_THRESHOLD_NOTE = (
    "decision rule, recorded before scoring: an exact FP rate stands on "
    "its own; the FN range is actionable when its ceiling is below the "
    "FP rate or above 2x the floor — otherwise enlarge the agreement "
    "sample before concluding anything about false negatives.")


def wilson(k: int, n: int, z: float = 1.96) -> tuple | None:
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _kappa(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    po = sum(1 for h, j in pairs if h == j) / n
    ph = sum(1 for h, _ in pairs if h == "used") / n
    pj = sum(1 for _, j in pairs if j == "used") / n
    pe = ph * pj + (1 - ph) * (1 - pj)
    return None if pe == 1.0 else (po - pe) / (1 - pe)


def score(key_doc: dict, judgements: dict, queue: dict,
          queue_key: dict) -> dict:
    """Pure over the four inputs; tested directly."""
    labels: dict[int, str] = {e["qid"]: (e.get("label") or "").strip().lower()
                              for e in queue["entries"]}
    qk = {k["qid"]: k for k in queue_key["entries"]}
    j_by_row = {r["lineage_id"]: {it["item"]: it for it in r.get("items", [])}
                for r in judgements.get("rows", [])}
    # W1's verifier (lens 3, F2): rows whose judge stated no answer, counted
    # apart. Their items carry no judgement (`judge` None), the queue gives
    # them to a human under `judge_no_answer`, and they never enter the
    # agreement sample, so they are not scored as judge-UNUSED.
    no_answer_rows = sorted(r["lineage_id"] for r in judgements.get("rows", []) if r.get("answer_empty"))
    # W1's close (M8): rows the judge was refused on (judge == answerer),
    # counted apart the same way; and the agreement sample is held to items
    # the judge labelled, here as well as in the queue, so a key built by an
    # older queue cannot score a non-judgement as judge-UNUSED.
    refused_rows = sorted(r["lineage_id"] for r in judgements.get("refused_rows", []))

    # Per-item human label, propagated from the group's queue entry.
    human: dict[tuple, str] = {}
    reason: dict[tuple, str] = {}
    for qid, k in qk.items():
        lab = labels.get(qid, "")
        for n in k["members"]:
            human[(k["lineage_id"], n)] = lab
            reason[(k["lineage_id"], n)] = k["reason"]

    cited, uncited = [], []
    groups: dict[tuple, dict] = {}
    for row in key_doc["rows"]:
        lid = row["lineage_id"]
        for item in row["items"]:
            rec = {"lineage_id": lid, "n": item["n"],
                   "claim": item["claim"],
                   "lexical": bool(item["lexical_cited"]),
                   "judge": (j_by_row.get(lid, {}).get(item["n"]) or
                             {}).get("label", "").lower() or None,
                   "span": (j_by_row.get(lid, {}).get(item["n"]) or
                            {}).get("span", ""),
                   "human": human.get((lid, item["n"]), ""),
                   "consequence": bool(item.get("consequence"))}
            (cited if rec["lexical"] else uncited).append(rec)
            g = groups.setdefault((lid, item["group"]),
                                  {"flags": set(), "members": 0})
            g["flags"].add(bool(item["lexical_cited"]))
            g["members"] += 1

    # ---- FP: exact over the exhausted cited stratum --------------------
    fp_cases = [r for r in cited if r["human"] == "unused"]
    cited_unsure = [r for r in cited if r["human"] == "unsure"]
    cited_judged = [r for r in cited if r["human"] in ("used", "unused")]
    fp = {
        "cited_items": len(cited),
        "human_labelled": len(cited_judged),
        "unsure": len(cited_unsure),
        "false_positives": len(fp_cases),
        "rate_exact": (len(fp_cases) / len(cited_judged)
                       if cited_judged else None),
        "wilson_95": wilson(len(fp_cases), len(cited_judged)),
        "cases": fp_cases,
        "consequence_bearing_fp": [r for r in fp_cases if r["consequence"]],
    }

    # ---- FN: floor from confirmations, ceiling from the sample ---------
    fn_confirmed = [r for r in uncited if r["human"] == "used"]
    sampled = [r for r in uncited
               if reason.get((r["lineage_id"], r["n"])) == "agreement_sample"]
    sampled_unjudged = [r for r in sampled if r["judge"] is None]
    sample = [r for r in sampled
              if r["judge"] is not None and r["human"] in ("used", "unused")]
    unjudged_rows = set(no_answer_rows) | set(refused_rows)
    sample_used = [r for r in sample if r["human"] == "used"]
    n_s = len(sample)
    shared_rate = (len(sample_used) / n_s) if n_s else None
    ceiling_rate = ((3.0 / n_s) if (n_s and not sample_used)
                    else shared_rate)
    unlabelled_remainder = sum(
        1 for r in uncited if r["human"] not in ("used", "unused", "unsure"))
    floor = len(fn_confirmed) / len(uncited) if uncited else None
    ceiling = (None if (floor is None or ceiling_rate is None) else
               min(1.0, (len(fn_confirmed)
                         + ceiling_rate * unlabelled_remainder)
                   / len(uncited)))
    fn = {
        "uncited_items": len(uncited),
        "confirmed_cases": fn_confirmed,
        "rate_floor": floor,
        "agreement_sample": {"n": n_s, "human_used": len(sample_used),
                             "shared_error_rate": shared_rate,
                             "ceiling_rate_rule_of_three":
                                 (3.0 / n_s) if n_s else None},
        "unlabelled_remainder": unlabelled_remainder,
        "judge_no_answer_items": sum(1 for r in uncited if r["lineage_id"] in set(no_answer_rows)),
        "judge_refused_items": sum(1 for r in uncited if r["lineage_id"] in set(refused_rows)),
        "judge_no_judgement_items": sum(1 for r in uncited if r["judge"] is None
                                        and r["lineage_id"] not in unjudged_rows),
        "agreement_sample_unjudged_excluded": len(sampled_unjudged),
        "rate_ceiling": ceiling,
        "range": ([floor, ceiling]
                  if floor is not None and ceiling is not None else None),
    }

    # ---- judge vs human on the cited stratum (computable in full) ------
    pairs = [(r["human"], r["judge"]) for r in cited
             if r["human"] in ("used", "unused")
             and r["judge"] in ("used", "unused")]
    judge_block = {
        "cited_stratum_pairs": len(pairs),
        "kappa": _kappa(pairs),
        "judge_unsure_vs_human_certain": [
            {"lineage_id": r["lineage_id"], "n": r["n"], "claim": r["claim"],
             "human": r["human"]}
            for r in cited + uncited
            if r["human"] in ("used", "unused") and r["judge"] == "unsure"],
        "span_discards": judgements.get("discarded", 0),
        "judge_no_answer_rows": len(no_answer_rows),
        "judge_refused_rows": len(refused_rows),
    }

    # ---- within-group inconsistency: free surface-sensitivity number ---
    multi = [g for g in groups.values() if g["members"] > 1]
    mixed = [g for g in multi if len(g["flags"]) > 1]
    inconsistency = {
        "groups": len(groups),
        "multi_member_groups": len(multi),
        "mixed_flag_groups": len(mixed),
        "rate_among_multi": (len(mixed) / len(multi)) if multi else None,
        "note": ("the lexical test flagged some members of one proposition "
                 "group and not others — a direct measurement of surface "
                 "sensitivity, at zero labelling cost."),
    }

    unsure_total = sum(1 for r in cited + uncited if r["human"] == "unsure")
    findings = []
    if cited and len(cited_unsure) / len(cited) > 0.2:
        findings.append(
            "high unsure rate on the CITED stratum: the flagged claims are "
            "fragments no test could adjudicate — that points at "
            "extraction, not at the citation test.")

    return {
        "alias_version": queue.get("alias_version"),
        "seed": queue.get("seed"), "sample_n": queue.get("sample_n"),
        "scale_threshold": SCALE_THRESHOLD_NOTE,
        "false_positive": fp,
        "false_negative": fn,
        "judge": judge_block,
        "within_group_inconsistency": inconsistency,
        "unsure_total": unsure_total,
        "findings": findings,
        "dependency": (
            "the FP block is judge-independent: the cited stratum was "
            "exhausted by hand. The FN block depends on the judge exactly "
            "where the human did not look — items the test and the judge "
            "both called unused outside the sample — and the ceiling is "
            "the stated bound on that shared-error term, not a "
            "measurement of it."),
    }


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stripped_file")
    ap.add_argument("judgements_file")
    ap.add_argument("queue_file")
    args = ap.parse_args()

    key_doc = json.loads(Path(args.stripped_file).with_suffix(".key.json")
                         .read_text(encoding="utf-8"))
    judgements = json.loads(
        Path(args.judgements_file).read_text(encoding="utf-8"))
    queue = json.loads(Path(args.queue_file).read_text(encoding="utf-8"))
    queue_key = json.loads(Path(args.queue_file).with_suffix(".key.json")
                           .read_text(encoding="utf-8"))

    blank = [e["qid"] for e in queue["entries"]
             if (e.get("label") or "").strip().lower()
             not in ("used", "unused", "unsure")]
    if blank:
        print(f"REFUSED: {len(blank)} queue entries unlabelled (first qid "
              f"{blank[0]}). The queue is fixed and fully labelled before "
              f"scoring; 'unsure' is a label, blank is not.")
        return 4

    print(json.dumps(score(key_doc, judgements, queue, queue_key),
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
