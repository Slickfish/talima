"""AQ step 4 — the result, in the packet's order.

Calibration first: on a miss, the failed items and nothing else. Then the
two per-stratum counts, then the total against the pre-registered
thresholds in scripts/_aq.py. Then the exploratory NLI head (cannot trigger
adoption), the discriminators that survived into the counted set, the
disagreement fraction, the map version, the model digests, and the two
pre-judging hashes re-verified against the files as they are now.

    python scripts/aq_result.py aq_scores.json aq_queue.json

VERSION TWO: with `--replacements aq_replacements.json` (written by
`aq_queue.py --replace`, then labelled blind) the excluded calibration
items are scored by their replacements; version one's stop is carried in
the report verbatim, and the version-one labels are re-verified against
the hash taken before the replacements existed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._aq import (                                        # noqa: E402
    CALIBRATION_EXCLUSION_RULES, CALIBRATION_MISS_RULE, EXPLORATORY,
    INCOHERENT_RULE, PREREG_HISTORY, PRIMARY, THRESHOLDS, TIE_RULE,
    UNSURE_RULE, evaluate, file_sha256,
)
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scores_file")
    ap.add_argument("queue_file")
    ap.add_argument("--replacements", default=None,
                    help="version two: the labelled replacement file")
    args = ap.parse_args()

    scores = json.loads(Path(args.scores_file).read_text(encoding="utf-8"))
    if scores.get("meta", {}).get("NOT_A_MEASUREMENT"):
        print("REFUSED: stub scores are mechanics, not a measurement")
        return 4
    queue = json.loads(Path(args.queue_file).read_text(encoding="utf-8"))
    key = json.loads(Path(args.queue_file).with_suffix(".key.json")
                     .read_text(encoding="utf-8"))
    blank = [e["qid"] for e in queue["entries"]
             if (e.get("label") or "").strip().lower()
             not in ("used", "unused", "unsure")]
    if blank:
        print(f"REFUSED: {len(blank)} unlabelled entries (first qid {blank[0]})")
        return 4

    prereg = json.loads(Path("aq_prereg.json").read_text(encoding="utf-8"))
    hashes = {
        "scores_sha256_prereg": prereg["scores_sha256"],
        "scores_sha256_now": file_sha256(args.scores_file),
        "queue_prereg_matches_seed": prereg["seed"] == queue["seed"],
    }
    hashes["scores_unchanged_since_prereg"] = (
        hashes["scores_sha256_prereg"] == hashes["scores_sha256_now"])

    exclusions, replacements = None, None
    if args.replacements:
        rpath = Path(args.replacements)
        rq = json.loads(rpath.read_text(encoding="utf-8"))
        rk = json.loads(rpath.with_suffix(".key.json").read_text(encoding="utf-8"))
        rblank = [e["qid"] for e in rq["entries"]
                  if (e.get("label") or "").strip().lower()
                  not in ("used", "unused", "unsure")]
        if rblank:
            print(f"REFUSED: {len(rblank)} unlabelled replacements (first qid {rblank[0]})")
            return 4
        exclusions = [{"qid": j["qid"], "reason": j["reason"]} for j in rk["exclusions"]]
        replacements = (rq["entries"], rk["entries"])
        v2 = prereg.get("v2", {})
        hashes["v1_labels_unchanged_since_replacements_drawn"] = (
            v2.get("labelled_queue_sha256") == file_sha256(args.queue_file))
        hashes["replacements_file_matches_prereg"] = (
            v2.get("exclusions") == exclusions)

    by = {(i["lineage_id"], i["n"]): i for i in scores["items"]}
    out = evaluate(queue["entries"], key["entries"], by,
                   exclusions=exclusions, replacements=replacements)
    report = {"calibration": out["calibration"]}
    if args.replacements:
        report["prereg"] = {"version": 2, "v1": PREREG_HISTORY[1],
                            "rules": {"incoherent": INCOHERENT_RULE,
                                      "exclusions": CALIBRATION_EXCLUSION_RULES,
                                      "other_misses": CALIBRATION_MISS_RULE}}
    if not out["calibration"]["passed"]:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    report.update({
        "per_stratum": out["per_stratum"],
        "total": out["total"],
        "thresholds": THRESHOLDS, "primary": PRIMARY,
        "exploratory": {EXPLORATORY: out["exploratory_nli"]},
        "discriminators_in_counted": out["discriminators_in_counted"],
        "disagreements": scores["summary"],
        "vmap_version": scores["vmap_version"],
        "models": scores["meta"].get("models"),
        "packages": scores["meta"].get("packages"),
        "hashes": hashes,
        "rules": {"tie": TIE_RULE, "unsure": UNSURE_RULE},
        "scope": ("forty turns of synthetic single-domain content answered "
                  "by one model; adopt-here at most, never a mandate to "
                  "productionise; expires when the data changes"),
    })
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
