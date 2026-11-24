"""AP.4 — the blind human queue. READ ONLY over its inputs.

One queue, one pass, and the human cannot tell why any entry is in it.
The queue holds, shuffled together under the recorded seed, with no
stratum, flag, or judge label visible:

  - every CITED-stratum group (the exhaustive half — the FP rate needs no
    judge);
  - every uncited group the judge called USED (the FN candidates);
  - a seeded sample of uncited judge-UNUSED groups (the shared-error
    check — sized by --sample-n for the rule-of-three ceiling wanted:
    30 -> ~10%, 45 -> ~7%);
  - every consequence-bearing group not already included;
  - every group the judge did not judge, each under its own reason: its
    row stated no answer (`judge_no_answer`), the judge was refused on its
    row (`judge_refused`: judge == answerer), or a member carries no label
    (`judge_no_judgement`: the reply parsed to no line, omitted the item,
    or the row is absent from the judgements). A judge that did not judge
    is not a judge that found nothing (law 5; W1's close, M8).

Blindness is the point: presented as "the judge said X, the test said Y"
the human anchors on both, and an agreement sample presented as such
reads as "please check these are fine". Entries are GROUPS within a row
(one decision covers near-duplicate spellings; membership is in the key,
which the human should not open until scoring).

    python scripts/citation_queue.py citation_stripped.json \
        citation_judgements.json [--seed 7] [--sample-n 30] \
        [--out citation_queue.json]

Label each entry: used | unused | unsure. Then run
citation_judge_score.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402


# The labels that are a judgement. Anything else on an item (no line, an
# empty or unknown label) is none, and its group goes to a human.
JUDGED = ("USED", "UNUSED", "UNSURE")


def build_queue(key_doc: dict, judgements: dict, *, seed: int,
                sample_n: int) -> tuple[list, list]:
    """(queue entries, key entries). Pure; tested directly.

    A group's stratum is per MEMBER (the lexical test is per item, so one
    group can straddle) — a group enters the exhaustive half if ANY member
    is cited; judge-USED if the judge marked any member used."""
    j_by_row = {r["lineage_id"]: {it["item"]: str(it.get("label", "") or "").upper()
                                  for it in r.get("items", [])}
                for r in judgements.get("rows", [])}
    # W1's verifier (lens 3, F2): a row whose judge stated no answer is not
    # a judge that found nothing. Its groups go to a human under their own
    # reason, never into the agreement sample or the unsampled remainder.
    no_answer = {r["lineage_id"] for r in judgements.get("rows", []) if r.get("answer_empty")}
    # W1's close (M8; the third verifier's lens 3, F7): the rest of that
    # class. A row the judge was refused on, and any member the judge gave
    # no label (a reply that parsed to no line, an item it never named, a
    # row missing from the file), are not judgements either.
    refused = {r["lineage_id"] for r in judgements.get("refused_rows", [])}

    groups: list[dict] = []
    for row in key_doc["rows"]:
        by_gid: dict[str, dict] = {}
        for item in row["items"]:
            g = by_gid.setdefault(item["group"], {
                "lineage_id": row["lineage_id"], "group": item["group"],
                "claims": [], "members": [], "any_cited": False,
                "any_judge_used": False, "any_unjudged": False, "consequence": False})
            g["members"].append(item["n"])
            if item["claim"] not in g["claims"]:
                g["claims"].append(item["claim"])
            g["any_cited"] |= bool(item["lexical_cited"])
            g["consequence"] |= bool(item.get("consequence"))
            label = j_by_row.get(row["lineage_id"], {}).get(item["n"])
            if label == "USED":
                g["any_judge_used"] = True
            if label not in JUDGED:
                g["any_unjudged"] = True
        groups.extend(by_gid.values())

    chosen, reasons = [], {}
    remainder = []
    for g in groups:
        gid = (g["lineage_id"], g["group"])
        if g["any_cited"]:
            chosen.append(g); reasons[gid] = "cited_stratum"
        elif g["any_judge_used"]:
            chosen.append(g); reasons[gid] = "fn_candidate"
        elif g["consequence"]:
            chosen.append(g); reasons[gid] = "consequence"
        elif g["lineage_id"] in no_answer:
            chosen.append(g); reasons[gid] = "judge_no_answer"
        elif g["lineage_id"] in refused:
            chosen.append(g); reasons[gid] = "judge_refused"
        elif g["any_unjudged"]:
            chosen.append(g); reasons[gid] = "judge_no_judgement"
        else:
            remainder.append(g)

    # The shared-error sample: seeded over the sorted remainder, so file
    # order cannot move the choice; recorded as such in the key.
    remainder.sort(key=lambda g: (g["lineage_id"], g["group"]))
    rng = random.Random(seed)
    sample = (remainder if len(remainder) <= sample_n
              else rng.sample(remainder, sample_n))
    for g in sample:
        chosen.append(g)
        reasons[(g["lineage_id"], g["group"])] = "agreement_sample"

    # ONE blind shuffle over everything the human sees. Seeded from the
    # digest's hex, NOT str.__hash__ (which is salted per process and
    # would make the recorded seed a lie).
    rng2 = random.Random(int(hashlib.sha256(
        f"queue:{seed}".encode()).hexdigest()[:16], 16))
    order = sorted(chosen, key=lambda g: (g["lineage_id"], g["group"]))
    rng2.shuffle(order)

    queue, key = [], []
    for qid, g in enumerate(order, start=1):
        queue.append({"qid": qid,
                      "answer": None,          # filled by caller from rows
                      "lineage_id": g["lineage_id"],
                      "claims": g["claims"], "label": ""})
        key.append({"qid": qid, "lineage_id": g["lineage_id"],
                    "group": g["group"], "members": g["members"],
                    "reason": reasons[(g["lineage_id"], g["group"])]})
    return queue, key


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stripped_file")
    ap.add_argument("judgements_file")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sample-n", type=int, default=30)
    ap.add_argument("--out", default="citation_queue.json")
    args = ap.parse_args()

    stripped = json.loads(Path(args.stripped_file).read_text(encoding="utf-8"))
    key_doc = json.loads(Path(args.stripped_file).with_suffix(".key.json")
                         .read_text(encoding="utf-8"))
    judgements = json.loads(
        Path(args.judgements_file).read_text(encoding="utf-8"))

    queue, key = build_queue(key_doc, judgements,
                             seed=args.seed, sample_n=args.sample_n)
    answers = {r["lineage_id"]: r["answer"] for r in stripped["rows"]}
    for entry in queue:
        entry["answer"] = answers.get(entry["lineage_id"], "")

    reasons: dict[str, int] = {}
    for k in key:
        reasons[k["reason"]] = reasons.get(k["reason"], 0) + 1
    print(f"queue: {len(queue)} entries "
          f"(composition, key-side only: {reasons})")

    out = Path(args.out)
    out.write_text(json.dumps({
        "seed": args.seed, "sample_n": args.sample_n,
        "alias_version": key_doc.get("alias_version"),
        "note": ("label each entry used | unused | unsure against its "
                 "answer. Entries are shuffled and carry no indication of "
                 "why they are here — do not open the key file until "
                 "scoring."),
        "entries": queue,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    qkey = out.with_suffix(".key.json")
    qkey.write_text(json.dumps({
        "seed": args.seed, "sample_n": args.sample_n,
        "alias_version": key_doc.get("alias_version"),
        "entries": key,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out} (label this) and {qkey} (do not open until scoring)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
