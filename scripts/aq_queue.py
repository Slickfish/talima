"""AQ step 3 — the blind queue, and the two hashes taken before judging.

Thirteen per disagreement stratum in seeded order (26 judged; the first 20
non-unsure counted), plus six calibration items (three agreed-used, three
agreed-unused) MIXED into one shuffle. The queue carries answer and
verbalised claim only — no score, no bit, no stratum. The key file holds
the rest; do not open it until scoring.

Records sha256 of the scores file and of the queue file into
aq_prereg.json BEFORE any label exists, so the result can show nothing was
rescored after labels existed.

    python scripts/aq_queue.py aq_scores.json [--seed 7] [--out aq_queue.json]

VERSION TWO (pre-registration in scripts/_aq.py, version one left standing):
after a calibration stop, draw the seeded replacements for excluded
calibration items from the LABELLED version-one queue — each exclusion is
justified on the labels first, never on assertion — and freeze the
version-one labels by hash before any replacement is labelled:

    python scripts/aq_queue.py aq_scores.json --replace aq_queue.json \
        --exclude 23:incoherent_unsure --exclude 25:shared_error
        [--out aq_replacements.json]

The seed is read from the queue file. Label aq_replacements.json blind,
then `aq_result.py ... --replacements aq_replacements.json`.
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
    CALIBRATION_EXCLUSION_RULES, PREREG_VERSION, build_queue, file_sha256,
    justify_exclusions, replacement_calibration,
)
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402


def _replace(args, scores: dict) -> int:
    """Version two. Refuses an unjustified exclusion, a blank version-one
    queue, or a replacement file that already exists (labels are never
    overwritten). Records the labelled queue's hash and the blind
    replacement file's hash in aq_prereg.json before any label exists."""
    qpath = Path(args.replace)
    queue = json.loads(qpath.read_text(encoding="utf-8"))
    key = json.loads(qpath.with_suffix(".key.json").read_text(encoding="utf-8"))
    blank = [e["qid"] for e in queue["entries"]
             if (e.get("label") or "").strip().lower() not in ("used", "unused", "unsure")]
    if blank:
        print(f"REFUSED: version-one queue has {len(blank)} unlabelled entries "
              f"(first qid {blank[0]}); replacements are drawn from a LABELLED queue")
        return 4
    exclusions = []
    for spec in args.exclude:
        qid, _, reason = spec.partition(":")
        exclusions.append({"qid": int(qid), "reason": reason.strip()})
    if not exclusions:
        print("REFUSED: --replace needs at least one --exclude QID:REASON")
        return 4
    just = justify_exclusions(queue["entries"], key["entries"], exclusions)
    for j in just:
        print(f"qid {j['qid']} {j['reason']}: "
              f"{'JUSTIFIED' if j['justified'] else 'REFUSED'} — {j['basis']}")
    if not all(j["justified"] for j in just):
        print("REFUSED: an exclusion is not justified on the labelled queue")
        return 4
    out = Path(args.out or "aq_replacements.json")
    if out.exists():
        print(f"REFUSED: {out} exists — a replacement file is never overwritten")
        return 4
    rqueue, rkey = replacement_calibration(
        scores["items"], seed=int(queue["seed"]), key=key["entries"],
        exclusions=exclusions, next_qid=max(e["qid"] for e in queue["entries"]) + 1)
    out.write_text(json.dumps({
        "seed": queue["seed"], "prereg_version": PREREG_VERSION,
        "replaces_queue": str(qpath),
        "note": ("label each entry used | unused | unsure against its answer. "
                 "Same criterion as everywhere else: did the answer assert or "
                 "depend on this proposition, about the subject it names. A "
                 "claim too fragmentary to carry a proposition is unsure, "
                 "never unused. Do not open the key file until scoring."),
        "entries": rqueue,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    out.with_suffix(".key.json").write_text(json.dumps({
        "seed": queue["seed"], "exclusions": just, "entries": rkey},
        indent=2), encoding="utf-8")
    prereg_path = Path("aq_prereg.json")
    prereg = json.loads(prereg_path.read_text(encoding="utf-8")) if prereg_path.is_file() else {}
    prereg["v2"] = {
        "labelled_queue_sha256": file_sha256(qpath),
        "replacements_sha256": file_sha256(out),
        "exclusions": [{"qid": j["qid"], "reason": j["reason"]} for j in just],
        "note": ("version-one labels frozen by hash; replacements hashed "
                 "before any replacement label existed"),
    }
    prereg_path.write_text(json.dumps(prereg, indent=2), encoding="utf-8")
    print(f"wrote {out} (label this blind), {out.with_suffix('.key.json')} "
          f"(do not open); aq_prereg.json gained v2 (labelled queue "
          f"{prereg['v2']['labelled_queue_sha256'][:12]}…, replacements "
          f"{prereg['v2']['replacements_sha256'][:12]}…)")
    return 0


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scores_file")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None)
    ap.add_argument("--replace", default=None, metavar="QUEUE_FILE",
                    help="version two: draw replacements for excluded "
                         "calibration items of this LABELLED queue")
    ap.add_argument("--exclude", action="append", default=[],
                    metavar="QID:REASON",
                    help="qid and reason (" + " | ".join(CALIBRATION_EXCLUSION_RULES) + ")")
    args = ap.parse_args()

    scores = json.loads(Path(args.scores_file).read_text(encoding="utf-8"))
    if scores.get("meta", {}).get("NOT_A_MEASUREMENT"):
        print("note: stub scores — mechanics only, not a measurement")
    if args.replace:
        return _replace(args, scores)
    args.out = args.out or "aq_queue.json"
    queue, key, comp = build_queue(scores["items"], seed=args.seed)
    for stratum in ("fp", "fn"):
        if comp[f"{stratum}_candidates_total"] < 13:
            print(f"stratum {stratum}: only {comp[f'{stratum}_candidates_total']} "
                  f"disagreements — the sample IS that population")
    print(f"composition (key-side only): {comp}")

    out = Path(args.out)
    out.write_text(json.dumps({
        "seed": args.seed,
        "note": ("label each entry used | unused | unsure against its answer. "
                 "Same criterion as everywhere else: did the answer assert or "
                 "depend on this proposition, about the subject it names. Do "
                 "not open the key file until scoring."),
        "entries": queue,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    key_path = out.with_suffix(".key.json")
    key_path.write_text(json.dumps({"seed": args.seed, "composition": comp,
                                    "entries": key}, indent=2),
                        encoding="utf-8")
    prereg = {
        "scores_sha256": file_sha256(args.scores_file),
        "queue_sha256": file_sha256(out),
        "seed": args.seed,
        "vmap_version": scores.get("vmap_version"),
        "note": "taken before any label existed",
    }
    Path("aq_prereg.json").write_text(json.dumps(prereg, indent=2),
                                      encoding="utf-8")
    print(f"wrote {out} (label this), {key_path} (do not open), "
          f"aq_prereg.json (hashes: scores {prereg['scores_sha256'][:12]}… "
          f"queue {prereg['queue_sha256'][:12]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
