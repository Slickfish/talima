"""AP.2 — the judge. Reads the STRIPPED extract only; writes its own file.

One call per row: the judge sees the answer and the numbered claims —
shuffled per row under a seed derived from (master seed, lineage_id), so
prominence order cannot leak through position — and labels each
USED | UNUSED | UNSURE with, for USED, the shortest evidence span. Spans
are verified programmatically against the answer; a judgement whose span
is not there is DISCARDED AND COUNTED.

CONTAMINATION, closed by construction rather than care:
  - the payload is the extract, which never contained flags, weights,
    group tags, belief ids, or the user's question;
  - the judge is NEVER the answering model — refused by base family, from
    the extract's key file, with no exception path;
  - the criterion is entailment about the named subject, not overlap, and
    the prompt says so in as many words (the wording is the instrument —
    it is reported verbatim by --show-prompt).

Never modifies the capture, the store, or any flag. Writes judgements to
its own file only.

    python scripts/citation_judge.py citation_labelling.json
        [--judge general] [--seed 7] [--out citation_judgements.json]
        [--show-prompt]
"""
from __future__ import annotations

import argparse
import asyncio
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

PROMPT_HEADER = """You are checking whether a written answer relies on specific claims. You will
see the answer and a numbered list of claims. Judge each claim independently.

A claim is USED if the answer states the claim's proposition, or depends on
it, about the same subject the claim names. Wording does not matter: a
restatement, a summary, or a brief mention of the same fact counts. Sharing
words does not count: if the answer uses the same terms but says something
different, or says it about a different subject, the claim is UNUSED.

A claim is UNSURE if the answer gestures at it without committing, or if the
claim itself is too fragmentary to have a clear proposition.

For each claim, return one line of JSON:
{"item": <number>, "label": "USED"|"UNUSED"|"UNSURE", "span": "<exact text
from the answer that carries the claim, or empty>"}

For USED, span is required and must be copied exactly from the answer.
For UNUSED and UNSURE, span is empty.
Return nothing else.
"""


class JudgeIsAnswerer(SystemExit):
    """The judge would be marking its own work. Refused, never excused."""


def _base(model: str) -> str:
    return (model or "").split(":")[0].strip().lower()


def check_judge(judge_model: str, answer_model: str) -> None:
    if _base(judge_model) == _base(answer_model):
        raise JudgeIsAnswerer(
            f"judge {judge_model!r} shares a base family with the answering "
            f"model {answer_model!r} — a model marking its own work is not "
            f"a judgement. Pick another judge for this row; if none exists, "
            f"the row is unjudgeable and is reported as such.")


def shuffle_items(items: list, *, master_seed: int, lineage_id: str) -> list:
    """Deterministic per (seed, row): reproducible, and different rows do
    not share a permutation — so agreement-by-position stays a sanity
    check rather than an artefact of one fixed shuffle."""
    h = hashlib.sha256(f"{master_seed}:{lineage_id}".encode()).hexdigest()
    rng = random.Random(int(h[:16], 16))
    out = list(items)
    rng.shuffle(out)
    return out


def build_prompt(row: dict, *, master_seed: int) -> tuple[str, list]:
    """The prompt actually sent, and the presented order (original item
    numbers, in presentation position) for the position sanity check.

    Only `answer` and each item's `claim` are read — a row carrying more
    (flags, question, ids) leaks nothing because nothing else is touched.
    """
    shuffled = shuffle_items(row["items"], master_seed=master_seed,
                             lineage_id=row["lineage_id"])
    claims = "\n".join(f"{pos}. {it['claim']}"
                       for pos, it in enumerate(shuffled, start=1))
    prompt = (f"{PROMPT_HEADER}\nANSWER:\n{row['answer']}\n\n"
              f"CLAIMS:\n{claims}\n")
    return prompt, [it["n"] for it in shuffled]


def validate_judgement(items: list, answer: str) -> tuple[list, int]:
    """Keep judgements whose evidence holds; discard and COUNT the rest.
    A USED with no span, or a span not present in the answer, is a
    judgement that could not point at text."""
    kept, discarded = [], 0
    for j in items:
        label = str(j.get("label", "")).upper()
        span = str(j.get("span", "") or "")
        if label == "USED" and (not span or span not in answer):
            discarded += 1
            continue
        kept.append(j)
    return kept, discarded


def _parse_lines(text: str) -> list:
    out = []
    for line in text.splitlines():
        line = line.strip().strip(",")
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


async def _judge_row(provider, model: str, row: dict, master_seed: int) -> dict:
    prompt, order = build_prompt(row, master_seed=master_seed)
    from backend.providers.base import collect_reply, num_ctx_for
    # W1 — through the reply chokepoint (C-38's `reply_stream`). Read from
    # `stream_chat`, a draft judgement inside an inline reasoning span (a
    # daemon older than Ollama 0.12.4, or LM Studio) was parsed as the
    # judge's verdict. A seed is now sent with each request, as on every
    # answering request since W0b; NEXUS_INFERENCE_SEED=off sends none. Each
    # row records the seed it was sent, or why none was (W1's verifier, F3).
    reply, capture = await collect_reply(
        provider, model, [{"role": "user", "content": prompt}],
        num_ctx=num_ctx_for(len(prompt)))
    seed = ({"seed": capture.seed, "seed_note": capture.notes.get("inference_seed")}
            if capture is not None else {"seed": None, "seed_note": "no capture"})
    if reply.answer_empty:
        # Declared on the row, and read downstream: the queue gives the
        # row's groups to a human (`judge_no_answer`) and the scorer counts
        # them apart, never as a judge that found nothing (law 5).
        return {"lineage_id": row["lineage_id"], "items": [], "discarded": 0,
                "answer_empty": True,
                "answer_empty_reason": reply.answer_empty_reason, **seed}
    raw = _parse_lines(reply.answer)
    # Map presentation position back to the ORIGINAL item number, and keep
    # the position so the scorer can report agreement by list position.
    mapped = []
    for j in raw:
        try:
            pos = int(j.get("item"))
        except (TypeError, ValueError):
            continue
        if not (1 <= pos <= len(order)):
            continue
        mapped.append({"item": order[pos - 1], "position": pos,
                       "label": str(j.get("label", "")).upper(),
                       "span": str(j.get("span", "") or "")})
    kept, discarded = validate_judgement(mapped, row["answer"])
    return {"lineage_id": row["lineage_id"], "items": kept,
            "discarded": discarded, **seed}


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("labelling_file")
    ap.add_argument("--judge", default="general",
                    help="canonical role or raw model name")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="citation_judgements.json")
    ap.add_argument("--show-prompt", action="store_true")
    args = ap.parse_args()

    from backend.providers.factory import get_provider
    from backend.providers.model_map import ROLES, resolve
    judge_model = resolve(args.judge) if args.judge in ROLES else args.judge

    doc = json.loads(Path(args.labelling_file).read_text(encoding="utf-8"))
    key_path = Path(args.labelling_file).with_suffix(".key.json")
    answer_models = {}
    if key_path.is_file():
        key = json.loads(key_path.read_text(encoding="utf-8"))
        answer_models = {r["lineage_id"]: r.get("answer_model", "unknown")
                        for r in key["rows"]}

    if args.show_prompt and doc["rows"]:
        print(build_prompt(doc["rows"][0], master_seed=args.seed)[0])
        return 0

    provider = get_provider()
    results, refused = [], []
    for row in doc["rows"]:
        am = answer_models.get(row["lineage_id"], "unknown")
        try:
            check_judge(judge_model, am)
        except JudgeIsAnswerer as e:
            refused.append({"lineage_id": row["lineage_id"],
                            "answer_model": am, "reason": str(e)})
            continue
        results.append(asyncio.run(
            _judge_row(provider, judge_model, row, args.seed)))
        if results[-1].get("answer_empty"):
            print(f"judged {row['lineage_id']}: NO STATED ANSWER "
                  f"({results[-1]['answer_empty_reason']}): a human labels this row")
        else:
            print(f"judged {row['lineage_id']}: "
                  f"{len(results[-1]['items'])} kept, "
                  f"{results[-1]['discarded']} discarded")

    out = Path(args.out)
    out.write_text(json.dumps({
        "judge_model": judge_model,
        "master_seed": args.seed,
        "prompt_sha256": hashlib.sha256(
            PROMPT_HEADER.encode()).hexdigest(),
        "rows": results,
        "refused_rows": refused,
        "discarded": sum(r["discarded"] for r in results),
        "answer_empty_rows": sum(1 for r in results if r.get("answer_empty")),
        "seeds": "one per request, drawn fresh and recorded on each row (seed, or seed_note "
                 "saying why none was sent); master_seed is the presentation shuffle",
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out}: {len(results)} rows judged, {len(refused)} refused "
          f"(judge == answerer), {sum(r['discarded'] for r in results)} "
          f"span-discards, {sum(1 for r in results if r.get('answer_empty'))} "
          f"with no stated answer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
