#!/usr/bin/env python
"""MEASURE — will extraction pick from an offered relation vocabulary?

WHAT THIS IS FOR
    Everything downstream of a relation-vocabulary build rests on one
    unmeasured assumption: that the extractor will choose from a list when it
    is offered one. The evidence available cuts against optimism — the shipped
    prompt already says "Use snake_case for relations" and receives snake-cased
    whole clauses, which proves compliance with a FORMATTING instruction and
    says nothing about a SEMANTIC one.

    A vocabulary the extractor ignores is worse than no vocabulary: it looks
    like a solution while the store keeps growing ~206 predicates over ~271
    rows. So the deliverable here is a number, not a design.

WHAT THIS IS NOT
    Not a build. It changes nothing in `backend/`, writes nothing to the
    store, and does not import `backend.server` — H.2 measured that importing
    that module migrated the operator's live `data/kb.db`.

    Arm A does not RE-STATE the shipped prompt, it IMPORTS it. A control arm
    that carries its own copy is measuring a prompt nobody ships.

THE ARMS
    A  bare         `EXTRACTION_PROMPT` alone — the pre-V.1 prompt
    S  shipped      what `extraction_prompt()` actually sends today
    B  offered      bare + the vocabulary and "use one of these, or `other`"
    C  threatened   B + "a predicate outside the list will be rejected"
    D  no hatch     B minus the `other` line

    C existed because "offered" and "required" may get very different
    compliance; measured, they did not (both 0.38).

    S and D are V.2. **S because V.1 changed what ships** — arm A is now the
    bare prompt and no longer describes production, so an A-vs-B table would
    compare two things neither of which is running. D because `other`
    measured 0.00 in every arm and the honest way to decide whether it earns
    its tokens is a fourth arm, not a removal on a hunch.

USAGE (on a machine with the model running)

    python scripts/measure_vocabulary_compliance.py --chunks 20

    # or against your own transcript, one answer per line, JSON {"text": ...}
    python scripts/measure_vocabulary_compliance.py --corpus my_answers.jsonl

    Results land in reports/vocabulary_compliance_<stamp>.json plus a printed
    summary. Nothing else is written.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.cortex.contradiction import CONTRADICTION_PAIRS      # noqa: E402
from backend.cortex.extraction import (                           # noqa: E402
    EXTRACTION_PROMPT, ExtractedTriple, _parse_triples, extraction_prompt)
from backend.kb.decompose import _HEADS                           # noqa: E402
from backend.kb.gate import (                                     # noqa: E402
    RELATION_ALIASES, inspect_triple, normalise_relation, normalise_term)
from backend.kb.vocabulary import (                               # noqa: E402
    OTHER, default_relations, load_relations, vocabulary_block)
from backend.providers.factory import get_provider                # noqa: E402
from backend.providers.model_map import resolve                   # noqa: E402
from scripts._console import force_utf8_stdio                     # noqa: E402
from scripts._env import load_env                                # noqa: E402


# ---------------------------------------------------------------------------
# The candidate vocabulary — DERIVED, never hand-written
# ---------------------------------------------------------------------------
#
# The investigation found three relation vocabularies in the repo that do not
# know about each other. The candidate set is their union, put through the
# shipped normaliser so the eight alias spellings collapse the way ingest
# already collapses them:
#
#     decompose._HEADS          32   relational heads, used by P.2's decomposer
#     RELATION_ALIASES targets   1   everything maps to `is_a`
#     CONTRADICTION_PAIRS       14   both sides of the seven antonym pairs
#     ------------------------------------------------------------------
#     raw union                 41 -> 33 after normalise_relation()
#
# Deriving rather than inventing is the point: if the union is deficient for
# the corpus, that is a FINDING about what the repo already believes a
# relation is, not a gap to quietly paper over. Anything added here would make
# the measurement about my judgement instead of the model's behaviour.

# V.1 — the derivation moved into `backend/kb/vocabulary.py` when the list
# started shipping, and this imports it. Two copies of the candidate set is
# how a harness comes to measure a prompt nobody sends; arm A already refuses
# to restate the shipped prompt for the same reason.
def candidate_vocabulary() -> tuple[str, ...]:
    return default_relations()


VOCAB = load_relations()
VOCAB_BLOCK = vocabulary_block(VOCAB)
CONSTRAINT_LINE = (
    f"A triple whose relation is not in that list (or `{OTHER}`) will be "
    "REJECTED and discarded.\n"
)


NO_HATCH_BLOCK = vocabulary_block(VOCAB, escape_hatch=False)


def arm_prompt(arm: str, text: str) -> str:
    """The arms. A is the shipped constant untouched; S is what actually ships.

    V.2 — arm S exists because V.1 changed what the extractor sends. Arm A is
    now the BARE prompt and no longer describes production, so a table of
    A vs B would compare two things neither of which is shipping. S calls
    `extraction_prompt()` — the real function, imported not restated, for the
    same reason arm A imports `EXTRACTION_PROMPT`.

    Arm D is B minus the escape hatch. `other` measured 0.00 in every arm, so
    the hatch buys nothing OBSERVABLE — but removing it changes the prompt
    that produced 0.38, and this project's record on unmeasured prompt changes
    is why this harness exists. D decides it on the same corpus instead.
    """
    if arm == "A":
        return EXTRACTION_PROMPT.format(text=text)
    if arm == "S":
        return extraction_prompt(text)
    # Insert before "TEXT:" so the instruction sits with the other
    # instructions rather than after the payload, where a long chunk would
    # bury it.
    head, _, tail = EXTRACTION_PROMPT.partition("TEXT:")
    extra = (NO_HATCH_BLOCK if arm == "D"
             else VOCAB_BLOCK + (CONSTRAINT_LINE if arm == "C" else ""))
    return (head + extra + "TEXT:" + tail).format(text=text)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _tokens(relation: str) -> list[str]:
    return [t for t in normalise_relation(relation).split("_") if t]


def classify_predicate(relation: str) -> tuple[str, str]:
    """(verdict, matched_member). Verdicts: exact | other | near | miss.

    NEAR-MISS RULE, stated because it decides the recommendation:
    a predicate is a near miss when a vocabulary member appears in it as a
    CONTIGUOUS TOKEN RUN — `causes_of` near `causes`, `is_a_segment_of` near
    `is_a` — or when the predicate is itself a token run inside a member.
    Substring matching on raw characters is deliberately NOT used: it would
    make `increases` a near miss for `decreases` on the shared tail, which is
    the opposite of the truth and would inflate the number the whole
    recommendation turns on.
    """
    canon = normalise_relation(relation)
    if not canon:
        return "miss", ""
    if canon == OTHER:
        return "other", OTHER
    if canon in VOCAB:
        return "exact", canon

    toks = _tokens(canon)
    best = ""
    for member in VOCAB:
        m = member.split("_")
        n = len(m)
        # member inside predicate, as whole tokens
        if n <= len(toks) and any(toks[i:i + n] == m for i in range(len(toks) - n + 1)):
            if len(member) > len(best):
                best = member
        # predicate inside member, as whole tokens
        elif len(toks) < n and any(m[i:i + len(toks)] == toks
                                   for i in range(n - len(toks) + 1)):
            if len(member) > len(best):
                best = member
    return ("near", best) if best else ("miss", "")


def quality_metrics(triples: list[ExtractedTriple]) -> dict:
    """Objective proxies only. The semantic judgement is the operator's, and
    the per-arm dumps in the report exist so they can make it.

    - `gate_rejected` uses the SHIPPED gate, so "worse" means "the substrate
      would refuse more of it", not "I thought it looked worse".
    - term length is P.1's metric, which measured the fragment problem.
    """
    if not triples:
        return {"n": 0}
    rejected = collections.Counter()
    subj_tokens, obj_tokens = [], []
    for t in triples:
        reason = inspect_triple(normalise_term(t.subject), t.relation,
                               normalise_term(t.object),
                               evidence_class="extraction", weight=t.confidence)
        if reason:
            rejected[reason] += 1
        subj_tokens.append(len(str(t.subject).split()))
        obj_tokens.append(len(str(t.object).split()))
    n = len(triples)
    return {
        "n": n,
        "gate_rejected": sum(rejected.values()),
        "gate_rejected_rate": round(sum(rejected.values()) / n, 4),
        "gate_reasons": dict(rejected),
        "mean_subject_tokens": round(sum(subj_tokens) / n, 2),
        "mean_object_tokens": round(sum(obj_tokens) / n, 2),
        "subjects_over_7_tokens": sum(1 for v in subj_tokens if v > 7),
        "objects_over_7_tokens": sum(1 for v in obj_tokens if v > 7),
    }


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

# Assistant answers, not curated prose — extraction runs on what NEXUS says,
# not on what it is asked. These questions are shaped like the operator's own
# session (clinical/pharmacological, the domain the 851-triple store came
# from) so the answers exercise the same predicate space.
DEFAULT_QUESTIONS = [
    "What is the QT interval and why is it monitored?",
    "How is TAK-441 metabolised?",
    "What does a QTc prolongation of 11.4ms suggest in a phase 1 study?",
    "Explain the role of CYP3A4 in drug interactions.",
    "What is a dose-limiting toxicity?",
    "How does renal impairment affect drug clearance?",
    "What is the difference between Cmax and AUC?",
    "Why are food-effect studies run?",
    "What does a hazard ratio below 1 mean in an oncology trial?",
    "How do you interpret a confidence interval that crosses 1?",
    "What is the purpose of a washout period?",
    "Explain first-pass metabolism.",
    "What is therapeutic drug monitoring for?",
    "How does protein binding change free drug concentration?",
    "What is an adverse event of special interest?",
    "Describe how a crossover trial design works.",
    "What causes a prolonged half-life in elderly patients?",
    "What is bioequivalence and how is it demonstrated?",
    "How does hepatic impairment alter dosing?",
    "What is the role of a data monitoring committee?",
]


async def build_corpus(provider, model: str, n: int, num_ctx: int) -> list[dict]:
    """Generate assistant answers to extract from.

    Generated ONCE and shared by all three arms — the arms must differ only in
    the extraction prompt. Generating per-arm would confound the measurement
    with corpus variance, which at these sample sizes would swamp the effect.
    """
    corpus = []
    questions = (DEFAULT_QUESTIONS * ((n // len(DEFAULT_QUESTIONS)) + 1))[:n]
    from backend.providers.base import collect_reply
    for i, q in enumerate(questions, 1):
        # W1 — through the reply chokepoint, so the reasoning is separated by
        # C-38's per-model rule, never by a global `</think>` split (which
        # kept an unclosed span as the answer, kept a second span, and cut a
        # literal `</think>` out of any model's prose). The extraction arms
        # below stay on stream_chat (Ruling 2).
        # A model with no tag pair in model_map.THINK_TAGS keeps any inline
        # reasoning in its answer (C-38 separates per model, never by a
        # global split); every thinking model the map names has one.
        reply, capture = await collect_reply(
            provider, model, [{"role": "user", "content": q}], num_ctx=num_ctx)
        answer = reply.answer.strip()
        # W1's verifier (lens 3, F3): the seed this request was sent, or why
        # none was, recorded on the entry.
        entry = {"question": q, "text": answer,
                 "seed": capture.seed if capture is not None else None,
                 "seed_note": (capture.notes.get("inference_seed") if capture is not None
                               else "no capture")}
        if reply.answer_empty:
            entry["answer_empty_reason"] = reply.answer_empty_reason
            print(f"  corpus {i}/{n}: NO STATED ANSWER ({reply.answer_empty_reason}) — {q[:48]}")
        else:
            print(f"  corpus {i}/{n}: {len(answer)} chars — {q[:48]}")
        corpus.append(entry)
    return corpus


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

async def run_arm(provider, model: str, arm: str, corpus: list[dict],
                  num_ctx: int, *, through_chokepoint: bool = False) -> dict:
    """One extraction arm over the corpus. Ruling 2: by default the arm
    mirrors production extraction's own direct call (`stream_chat`, a
    global `</think>` split). `through_chokepoint` (the
    --arm-through-chokepoint flag, off by default, W1's verifier N6) reads
    the arm's reply through `collect_reply` instead.

    An entry with no text (an answer the corpus model never stated) is not
    sent: production's extraction makes no model call for blank text (W1's
    verifier, F7), and an arm that did would score what the model invents
    for an empty TEXT. Skipped entries are counted on the result."""
    triples: list[ExtractedTriple] = []
    per_chunk = []
    skipped = 0
    for i, chunk in enumerate(corpus, 1):
        if not chunk["text"].strip():
            skipped += 1
            print(f"  arm {arm} {i}/{len(corpus)}: skipped (no stated answer to extract from)")
            continue
        prompt = arm_prompt(arm, chunk["text"])
        if through_chokepoint:
            from backend.providers.base import collect_reply
            reply, _capture = await collect_reply(
                provider, model, [{"role": "user", "content": prompt}], num_ctx=num_ctx)
            raw = reply.answer
        else:
            buf = []
            async for tok in provider.stream_chat(
                    model, [{"role": "user", "content": prompt}], num_ctx=num_ctx):
                buf.append(tok)
            raw = "".join(buf)
            if "</think>" in raw:
                raw = raw.split("</think>", 1)[1]
        got = _parse_triples(raw)
        per_chunk.append({"question": chunk["question"],
                          "triples": [t.to_dict() for t in got]})
        triples.extend(got)
        print(f"  arm {arm} {i}/{len(corpus)}: {len(got)} triples")

    verdicts = collections.Counter()
    matches = collections.Counter()
    predicates = collections.Counter()
    for t in triples:
        verdict, member = classify_predicate(t.relation)
        verdicts[verdict] += 1
        predicates[normalise_relation(t.relation)] += 1
        if verdict == "near":
            matches[f"{normalise_relation(t.relation)} -> {member}"] += 1

    n = len(triples) or 1
    return {
        "arm": arm,
        "chunks_sent": len(corpus) - skipped,
        "chunks_skipped_no_answer": skipped,
        "read_through": "collect_reply" if through_chokepoint else "stream_chat (Ruling 2)",
        "triples_total": len(triples),
        "distinct_predicates": len(predicates),
        "predicates_per_triple": round(len(predicates) / n, 3),
        "pick_rate": round(verdicts["exact"] / n, 4),
        "near_miss_rate": round(verdicts["near"] / n, 4),
        "other_rate": round(verdicts["other"] / n, 4),
        "miss_rate": round(verdicts["miss"] / n, 4),
        "verdicts": dict(verdicts),
        "near_misses": matches.most_common(30),
        "top_predicates": predicates.most_common(25),
        "quality": quality_metrics(triples),
        "per_chunk": per_chunk,
    }


async def main() -> int:
    force_utf8_stdio()          # M.4 — before the first print, not after
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chunks", type=int, default=20,
                    help="how many assistant answers to extract from")
    ap.add_argument("--corpus", type=Path, default=None,
                    help="JSONL of {'text': ...} to use instead of generating")
    ap.add_argument("--arms", default="A,S,B,C,D",
                    help="A bare | S shipped | B offered | "
                         "C threatened | D offered-without-hatch")
    ap.add_argument("--num-ctx", type=int, default=8192)  # Law V ceiling
    ap.add_argument("--arm-through-chokepoint", action="store_true",
                    help="read each arm's reply through collect_reply (off: the "
                         "arm mirrors production extraction, Ruling 2)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    print(__doc__.split("USAGE")[0].strip())
    print("=" * 72)
    print(f"candidate vocabulary: {len(VOCAB)} relations")
    print("  " + ", ".join(VOCAB))
    print()

    provider = get_provider()
    if not await provider.health():
        print("NO MODEL REACHABLE. Start the daemon and re-run; this script "
              "measures model behaviour and a stub would answer a different "
              "question.")
        return 2
    chat_model = resolve("primary")
    extract_model = resolve("extraction")
    print(f"corpus model:     {chat_model}")
    print(f"extraction model: {extract_model}   (FAST_EXTRACTION, as shipped)")
    print()

    if args.corpus:
        corpus = [json.loads(l) for l in
                  args.corpus.read_text(encoding="utf-8").splitlines() if l.strip()]
        corpus = corpus[:args.chunks]
        print(f"corpus: {len(corpus)} chunks from {args.corpus}")
    else:
        print(f"generating {args.chunks} answers…")
        corpus = await build_corpus(provider, chat_model, args.chunks, args.num_ctx)

    results = {}
    for arm in [a.strip().upper() for a in args.arms.split(",") if a.strip()]:
        print(f"\n--- arm {arm} ---")
        results[arm] = await run_arm(provider, extract_model, arm, corpus,
                                     args.num_ctx,
                                     through_chokepoint=args.arm_through_chokepoint)

    stamp = time.strftime("%Y%m%dT%H%M%S")
    out = args.out or (ROOT / "reports" / f"vocabulary_compliance_{stamp}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": stamp,
        "vocabulary": list(VOCAB),
        "vocabulary_size": len(VOCAB),
        "chunks": len(corpus),
        "chunks_with_no_stated_answer": sum(1 for c in corpus if not c["text"].strip()),
        "seeds": "the corpus requests' seeds are on each corpus entry; " + (
            "the arms read through collect_reply and send one each, not recorded"
            if args.arm_through_chokepoint else
            "the arms send none (stream_chat, as production extraction does)"),
        "corpus_model": chat_model,
        "extraction_model": extract_model,
        "corpus": corpus,
        "arms": results,
    }, indent=1, sort_keys=True), encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"{'arm':<5}{'triples':>8}{'distinct':>10}{'pick':>8}{'near':>8}"
          f"{'other':>8}{'miss':>8}{'gate_rej':>10}")
    for arm, r in results.items():
        q = r["quality"]
        print(f"{arm:<5}{r['triples_total']:>8}{r['distinct_predicates']:>10}"
              f"{r['pick_rate']:>8.2f}{r['near_miss_rate']:>8.2f}"
              f"{r['other_rate']:>8.2f}{r['miss_rate']:>8.2f}"
              f"{q.get('gate_rejected_rate', 0):>10.2f}")
    print(f"\nfull results: {out}")
    print("Report back: the table above, plus `near_misses` and the per-chunk "
          "triples for two or three questions so the terms can be compared "
          "across arms.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
