"""AQ — shared, pure logic for the MiniCheck experiment. No models here.

Everything a sentinel needs to prove is a pure function in this module:
the verbalisation map (hand-written head, templated tail, versioned as one
artefact), the strata, the blind queue with calibration mixed in, the
pre-registered thresholds, and the evaluation. The four CLI scripts are
thin wrappers; the scorer's model backends import torch lazily inside
main() so the repository's suite never needs them.

PRE-REGISTERED, FIXED BEFORE THE FIRST RUN — these do not move afterwards.
"""
from __future__ import annotations

import hashlib
import json
import random

# ---------------------------------------------------------------------------
# Pre-registration (packet AQ §2, §3, §4, §5.5). Recorded in source.
# ---------------------------------------------------------------------------

PRIMARY = "minicheck"                 # H1 — the only candidate adoption reads
EXPLORATORY = "nli"                   # DeBERTa NLI head: class-of-method only
MINICHECK_THRESHOLD = 0.5             # published default; NOT fitted here
SAMPLE_PER_STRATUM = 13               # 26 judged in seeded order ...
JUDGE_COUNT = 20                      # ... first 20 non-unsure are counted
CALIBRATION_PER_SIDE = 3              # 3 agreed-used + 3 agreed-unused; pass 6/6
THRESHOLDS = {"adopt": 14,            # P(>=14 | fair coin, n=20) = 0.0577
              "inconclusive": (11, 13),
              "no_evidence": 10}
TIE_RULE = "inconclusive keeps the incumbent; novelty does not win a tie"
UNSURE_RULE = ("judge all sampled items; count the first JUDGE_COUNT "
               "non-unsure in queue order; if fewer remain, report the "
               "count and express thresholds as proportions of 14/20")
CALIBRATION_UNSURE_RULE = "an unsure on a calibration item is a miss"

# ---------------------------------------------------------------------------
# Pre-registration, VERSION TWO. Version one is left standing above, word
# for word, and its outcome is recorded here. Fixed 2026-09-19, before the
# counted set was scored: the run stopped at calibration under version one
# and no counted-set figure existed when this block was written.
# ---------------------------------------------------------------------------
PREREG_VERSION = 2
PREREG_HISTORY = {
    1: {
        "calibration_unsure_rule": CALIBRATION_UNSURE_RULE,
        "outcome": ("stopped at calibration: 2 of 6 failed. qid 23 — an "
                    "agreed-unused item whose subject was a markdown heading "
                    "lifted from a prior answer; the operator said unsure on "
                    "incoherence, both scorers said unused, and version one "
                    "scored the unsure as a miss. qid 25 — an agreed-used item "
                    "(Study TAK-441-302 causes QT prolongation risk) the "
                    "operator judged unused, consistently, across four "
                    "answers; both scorers were wrong the same way because "
                    "every word of the claim was in the answer. No third "
                    "kind. No precision figure was computed."),
        "stopped": "2026-09-19",
    },
    2: {
        "fixed": "2026-09-19, before the counted set was scored",
        "changes": ("incoherent claims are labelled unsure; an unsure on a "
                    "calibration item is an exclusion with a seeded "
                    "replacement, not a miss; a calibration miss that is a "
                    "shared scorer error is excluded as evidence about the "
                    "scorers, with a seeded replacement; the 32 version-one "
                    "labels are untouched and the counted set is unchanged"),
    },
}
# The judge prompt's sentence (citation_judge.py), fixed into the labelling
# note: a claim with no proposition is neither used nor unused.
INCOHERENT_RULE = ("a claim too fragmentary to carry a proposition is labelled "
                   "unsure — never unused; unused is a verdict on the answer, "
                   "and there is no proposition for the answer to have used")
# Why two rules treated one label differently: UNSURE_RULE excludes an
# unsure from the counted set as an item property; CALIBRATION_UNSURE_RULE
# scored it as an operator failure to stop hard items being dodged. For an
# incoherent claim the item is undecidable, not the operator — and the
# agreed-unused stratum is ENRICHED with incoherent claims by construction,
# because "nothing to entail" is exactly what both scorers return on one.
# The anti-dodging intent survives as a replacement: an unsure costs the
# operator another blind item, never a pass.
CALIBRATION_EXCLUSION_RULES = {
    "incoherent_unsure": (
        "an unsure on a calibration item is an exclusion, not a miss; it is "
        "replaced by the next seeded item from the same stratum, labelled "
        "blind; the replacement's own unsure or miss is a miss"),
    "shared_error": (
        "a calibration miss is excluded as evidence about the scorers rather "
        "than the operator when the operator's label on at least one OTHER "
        "queue entry carrying the same claim text equals the label given "
        "here — a consistent reading, not drift; replaced by seed"),
}
CALIBRATION_MISS_RULE = ("every other calibration miss is a miss and stops the "
                         "run, as under version one")
# The mechanical half of the incoherence pre-screen, applied to replacement
# CANDIDATES only (the version-one queue is not re-drawn): a claim whose
# relation is empty, or whose verbalised form has fewer than three tokens,
# has no proposition. The heading-as-subject case and a measurement under
# a relation that does not measure are NOT mechanical and stay with the
# label, which is why the rule is called partial in the packet.
MIN_CLAIM_TOKENS = 3

MODELS = {
    "minicheck": "lytang/MiniCheck-Flan-T5-Large",
    "nli": "MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli",
}
# The name the `minicheck` package uses for the same checkpoint.
MINICHECK_PACKAGE_NAME = "flan-t5-large"

# THE INSTRUMENT RECORD: the variant was switched once, BEFORE any judging,
# and the switch is recorded here rather than silently overwritten. Both
# digests are carried in the scores file when --previous-scores is given.
VARIANT_RECORD = {
    "minicheck": {
        "current": "lytang/MiniCheck-Flan-T5-Large",
        "previous": "lytang/MiniCheck-DeBERTa-v3-Large",
        "switched": "2026-09-09, before any label existed",
        "reason": ("published LLM-AggreFact balanced accuracy: "
                   "MiniCheck-Flan-T5-Large 74.7% vs MiniCheck-DeBERTa-v3-"
                   "Large 72.7% (GPT-4 75.3%, Claude-3 Opus 74.1% for "
                   "reference); 770M parameters, still under 1B, still "
                   "CPU-feasible. DeBERTa was the wrong default; switching "
                   "after seeing scores would not have been acceptable."),
    },
}

# ---------------------------------------------------------------------------
# Verbalisation — the instrument, written before any score exists
# ---------------------------------------------------------------------------
# Hand-written phrasings. The packet's rule is "the top twenty by frequency
# by hand, the tail templated"; the builder marks every entry with its
# source and REFUSES to finalise a map whose top-twenty contains a
# templated form, so the hand set must cover them before scoring.
HAND_WRITTEN = {
    "has":                          "{s} has {o}",
    "causes":                       "{s} causes {o}",
    "is_metabolized_by":            "{s} is metabolized by {o}",
    "affinity_for":                 "{s} has affinity for {o}",
    "was":                          "{s} was {o}",
    "demonstrates_affinity_for":    "{s} demonstrates affinity for {o}",
    "contains":                     "{s} contains {o}",
    "produces":                     "{s} produces {o}",
    "is_a":                         "{s} is a {o}",
    "is_an":                        "{s} is an {o}",
    "is":                           "{s} is {o}",
    "are":                          "{s} are {o}",
    "is_part_of":                   "{s} is part of {o}",
    "part_of":                      "{s} is part of {o}",
    "is_used_for":                  "{s} is used for {o}",
    "inhibits":                     "{s} inhibits {o}",
    "increases":                    "{s} increases {o}",
    "decreases":                    "{s} decreases {o}",
    "reduces":                      "{s} reduces {o}",
    "raises":                       "{s} raises {o}",
    "prolongs":                     "{s} prolongs {o}",
    "binds_to":                     "{s} binds to {o}",
    "requires":                     "{s} requires {o}",
    "supports":                     "{s} supports {o}",
    "prevents":                     "{s} prevents {o}",
    "indicates":                    "{s} indicates {o}",
    "measures":                     "{s} measures {o}",
    "represents":                   "{s} represents {o}",
    "includes":                     "{s} includes {o}",
    "consists_of":                  "{s} consists of {o}",
    "belongs_to":                   "{s} belongs to {o}",
    "refers_to":                    "{s} refers to {o}",
    "primarily_metabolized_by":     "{s} is primarily metabolized by {o}",
    "is_metabolized_by_primary_enzyme_of":
                                    "{s} is metabolized by the primary enzyme of {o}",
    "carries":                      "{s} carries {o}",
    "stands_for":                   "{s} stands for {o}",
    "enables":                      "{s} enables {o}",
    "co_activated":                 "{s} was co-activated with {o}",
    "recurring_query_term":         "{s} recurred as a query term with {o}",
    "measured_as":                  "{s} was measured as {o}",
    "can_use_for":                  "{s} can be used for {o}",
    "has_low_likelihood_of":        "{s} has a low likelihood of {o}",
    "is_mediated_by":               "{s} is mediated by {o}",
    "is_key_pathway_for":           "{s} is a key pathway for {o}",
    "reports":                      "{s} reports {o}",
    "mean_placebo_corrected_qtc_change_at_highest_dose":
                                    "{s} had a mean placebo-corrected QTc change at the highest dose of {o}",
    "shows_mean_placebo_corrected_qtc_change_at_highest_dose_test":
                                    "{s} shows a mean placebo-corrected QTc change at the highest dose of {o}",
    "metabolized_by":               "{s} is metabolized by {o}",
    "eliminated_by":                "{s} is eliminated by {o}",
    "strengthened_by":              "{s} is strengthened by {o}",
    "associated_with":              "{s} is associated with {o}",
    "align_with_one_another":       "{s} aligns with {o}",
    "mean_at_dosage":               "{s} has a mean at the dosage of {o}",
    "is_not_documented_in_context": "{s} is not documented in the context of {o}",
    "has_function_in_drug_dynamics":
                                    "{s} has a function in drug dynamics relating to {o}",
    "requires_monitoring_due_to_potential_for_arrhythmias":
                                    "{s} requires monitoring due to the potential for arrhythmias relating to {o}",
    "shows_minimal_qtc_changes_at_lower_doses":
                                    "{s} shows minimal QTc changes at lower doses, with {o}",
    "has_mean_qtc_change_at_highest_dose_of_200mg":
                                    "{s} has a mean QTc change at the highest 200mg dose of {o}",
    "requires_knowledge_for_online_searching":
                                    "{s} requires knowledge for online searching, namely {o}",
    "highlight_the_importance_of_monitoring_levels_and_adjusting_":
                                    "{s} highlight the importance of monitoring levels and adjusting {o}",
    "is_an_exercise_strategy_that_alternates_between_periods_of":
                                    "{s} is an exercise strategy that alternates between periods of {o}",
}


def template_phrase(relation: str) -> str:
    """The tail rule: strip underscores, nothing else. Subject/object order
    is preserved, so a passive relation reads in its stored direction."""
    return "{s} " + " ".join(relation.strip().split("_")) + " {o}"


def build_map(frequencies: dict[str, int], *, top_n: int = 20,
              hand: dict | None = None) -> dict:
    """The versioned artefact: every relation, its phrase, its source, its
    count; the version is a digest of the phrases alone (frequencies are
    data about the sample, not the instrument)."""
    hand = HAND_WRITTEN if hand is None else hand
    entries = {}
    for rel, n in frequencies.items():
        if rel in hand:
            entries[rel] = {"phrase": hand[rel], "source": "hand", "count": n}
        else:
            entries[rel] = {"phrase": template_phrase(rel),
                            "source": "template", "count": n}
    top = sorted(frequencies, key=lambda r: (-frequencies[r], r))[:top_n]
    missing = [r for r in top if entries[r]["source"] != "hand"]
    body = json.dumps({r: e["phrase"] for r, e in sorted(entries.items())},
                      sort_keys=True)
    return {
        "version": f"vmap-{len(entries)}-{hashlib.sha256(body.encode()).hexdigest()[:10]}",
        "top_n": top_n,
        "top_n_templated": missing,     # must be [] before scoring
        "hand_count": sum(1 for e in entries.values() if e["source"] == "hand"),
        "template_count": sum(1 for e in entries.values()
                              if e["source"] == "template"),
        "entries": entries,
    }


def verbalise(vmap: dict, subject: str, relation: str, object_: str) -> str:
    entry = vmap["entries"].get(relation)
    phrase = entry["phrase"] if entry else template_phrase(relation)
    return phrase.format(s=subject, o=object_)


def spot_check(vmap: dict, examples: dict[str, tuple[str, str]], *,
               seed: int, n: int = 50) -> list[dict]:
    """Seeded sample of TEMPLATED forms, each rendered on a real (s, o)
    from the capture — for a human to read looking for PATTERNING (a
    class of relations coming out backwards), not individual clumsiness."""
    templated = sorted(r for r, e in vmap["entries"].items()
                       if e["source"] == "template" and r in examples)
    rng = random.Random(seed)
    chosen = templated if len(templated) <= n else rng.sample(templated, n)
    return [{"relation": r,
             "rendered": verbalise(vmap, examples[r][0], r, examples[r][1])}
            for r in chosen]


# ---------------------------------------------------------------------------
# Strata, queue, calibration
# ---------------------------------------------------------------------------

def strata(items: list[dict]) -> dict[str, list[dict]]:
    """Each item carries `lexical` (bit) and `minicheck` (bit). The two
    disagreement directions are kept apart — pooling would let one scorer
    win every item of one kind and lose every item of the other."""
    out = {"fp_candidates": [], "fn_candidates": [],
           "agreed_used": [], "agreed_unused": []}
    for it in items:
        lex, mc = bool(it["lexical"]), bool(it["minicheck"])
        if lex and not mc:
            out["fp_candidates"].append(it)
        elif mc and not lex:
            out["fn_candidates"].append(it)
        elif lex and mc:
            out["agreed_used"].append(it)
        else:
            out["agreed_unused"].append(it)
    return out


def _seeded(pool: list[dict], n: int, rng: random.Random) -> list[dict]:
    ordered = sorted(pool, key=lambda it: (it["lineage_id"], it["n"]))
    return ordered if len(ordered) <= n else rng.sample(ordered, n)


def build_queue(items: list[dict], *, seed: int) -> tuple[list, list, dict]:
    """(queue, key, composition). The queue carries answer + verbalised
    claim ONLY. Calibration items are drawn from the agreed strata and
    shuffled in with the sample under one seed — mixed, never appended."""
    s = strata(items)
    rng = random.Random(seed)
    fp = _seeded(s["fp_candidates"], SAMPLE_PER_STRATUM, rng)
    fn = _seeded(s["fn_candidates"], SAMPLE_PER_STRATUM, rng)
    cal_used = _seeded(s["agreed_used"], CALIBRATION_PER_SIDE, rng)
    cal_unused = _seeded(s["agreed_unused"], CALIBRATION_PER_SIDE, rng)
    tagged = ([(it, "fp_candidate") for it in fp]
              + [(it, "fn_candidate") for it in fn]
              + [(it, "calibration_used") for it in cal_used]
              + [(it, "calibration_unused") for it in cal_unused])
    rng2 = random.Random(int(hashlib.sha256(
        f"aq-queue:{seed}".encode()).hexdigest()[:16], 16))
    tagged.sort(key=lambda t: (t[0]["lineage_id"], t[0]["n"]))
    rng2.shuffle(tagged)
    queue, key = [], []
    for qid, (it, tag) in enumerate(tagged, start=1):
        queue.append({"qid": qid, "answer": it["answer"],
                      "claim": it["claim"], "label": ""})
        key.append({"qid": qid, "lineage_id": it["lineage_id"], "n": it["n"],
                    "tag": tag})
    composition = {
        "fp_candidates_total": len(s["fp_candidates"]),
        "fn_candidates_total": len(s["fn_candidates"]),
        "fp_sampled": len(fp), "fn_sampled": len(fn),
        "calibration_used": len(cal_used),
        "calibration_unused": len(cal_unused),
        "disagreements_total": len(s["fp_candidates"]) + len(s["fn_candidates"]),
        "items_total": len(items),
    }
    return queue, key, composition


def coherent_candidate(item: dict) -> bool:
    """The mechanical pre-screen (partial, see MIN_CLAIM_TOKENS)."""
    if not (item.get("relation") or "").strip():
        return False
    return len((item.get("claim") or "").split()) >= MIN_CLAIM_TOKENS


def replacement_calibration(items: list[dict], *, seed: int,
                            key: list[dict], exclusions: list[dict],
                            next_qid: int) -> tuple[list, list]:
    """(rqueue, rkey): one seeded replacement per excluded calibration item,
    from the SAME stratum, disjoint from everything the version-one queue
    drew, coherent by the mechanical pre-screen, qids continuing after the
    version-one queue. Version one's draw is reproduced first so the
    replacement pool is exactly what version one did not draw."""
    s = strata(items)
    rng = random.Random(seed)
    _seeded(s["fp_candidates"], SAMPLE_PER_STRATUM, rng)
    _seeded(s["fn_candidates"], SAMPLE_PER_STRATUM, rng)
    _seeded(s["agreed_used"], CALIBRATION_PER_SIDE, rng)
    _seeded(s["agreed_unused"], CALIBRATION_PER_SIDE, rng)
    drawn = {(k["lineage_id"], k["n"]) for k in key}
    tag_of = {k["qid"]: k["tag"] for k in key}
    rng2 = random.Random(int(hashlib.sha256(
        f"aq-replace:{seed}".encode()).hexdigest()[:16], 16))
    pools = {"calibration_used": [it for it in sorted(
                 s["agreed_used"], key=lambda it: (it["lineage_id"], it["n"]))
                 if (it["lineage_id"], it["n"]) not in drawn and coherent_candidate(it)],
             "calibration_unused": [it for it in sorted(
                 s["agreed_unused"], key=lambda it: (it["lineage_id"], it["n"]))
                 if (it["lineage_id"], it["n"]) not in drawn and coherent_candidate(it)]}
    for pool in pools.values():
        rng2.shuffle(pool)
    rqueue, rkey = [], []
    qid = next_qid
    for ex in sorted(exclusions, key=lambda e: e["qid"]):
        tag = tag_of.get(ex["qid"])
        if tag not in pools:
            raise ValueError(f"qid {ex['qid']} is not a calibration item ({tag})")
        if not pools[tag]:
            raise ValueError(f"no coherent replacement left in {tag}")
        it = pools[tag].pop(0)
        rqueue.append({"qid": qid, "answer": it["answer"], "claim": it["claim"],
                       "label": ""})
        rkey.append({"qid": qid, "lineage_id": it["lineage_id"], "n": it["n"],
                     "tag": tag, "replaces": ex["qid"], "reason": ex["reason"]})
        qid += 1
    return rqueue, rkey


def justify_exclusions(queue: list[dict], key: list[dict],
                       exclusions: list[dict]) -> list[dict]:
    """Each exclusion, with whether its rule actually holds on the labelled
    version-one queue. Nothing is excluded on assertion."""
    labels = {e["qid"]: (e.get("label") or "").strip().lower() for e in queue}
    claims = {e["qid"]: (e.get("claim") or "").strip().lower() for e in queue}
    tag_of = {k["qid"]: k["tag"] for k in key}
    out = []
    for ex in exclusions:
        qid, reason = ex["qid"], ex["reason"]
        tag = tag_of.get(qid, "")
        lab = labels.get(qid, "")
        expected = "used" if tag == "calibration_used" else "unused"
        row = {"qid": qid, "reason": reason, "tag": tag, "label": lab,
               "expected": expected, "justified": False, "basis": ""}
        if not tag.startswith("calibration"):
            row["basis"] = "not a calibration item"
        elif reason not in CALIBRATION_EXCLUSION_RULES:
            row["basis"] = "unknown reason"
        elif lab == expected:
            row["basis"] = "not a miss — nothing to exclude"
        elif reason == "incoherent_unsure":
            row["justified"] = lab == "unsure"
            row["basis"] = ("labelled unsure" if row["justified"]
                            else f"labelled {lab!r}, not unsure")
        elif reason == "shared_error":
            same = [q for q, c in claims.items()
                    if q != qid and c == claims.get(qid, "") and labels.get(q) == lab]
            row["justified"] = bool(same) and lab in ("used", "unused")
            row["consistent_with_qids"] = same
            row["basis"] = (f"same claim labelled {lab!r} on {len(same)} other "
                            f"entr{'y' if len(same) == 1 else 'ies'}" if same
                            else "no other entry carries this claim")
        out.append(row)
    return out


def evaluate(queue: list[dict], key: list[dict],
             items_by_ref: dict[tuple, dict], *,
             exclusions: list[dict] | None = None,
             replacements: tuple[list, list] | None = None) -> dict:
    """Calibration first; if it fails, the failed items and nothing else.
    Then per-stratum counts over the first JUDGE_COUNT non-unsure
    disagreement items in queue order, then the total against THRESHOLDS.

    With no `exclusions` this is VERSION ONE exactly. Under version two an
    exclusion must be justified on the labelled queue (`justify_exclusions`)
    and carry a labelled replacement from the same stratum; the replacement
    is then held to the version-one rule — its miss or unsure is a miss. The
    32 version-one labels and the counted set are never touched.
    """
    labels = {e["qid"]: (e.get("label") or "").strip().lower() for e in queue}
    kq = sorted(key, key=lambda k: k["qid"])
    exclusions = list(exclusions or [])
    version = 2 if exclusions else 1
    justified = justify_exclusions(queue, key, exclusions) if exclusions else []
    rqueue, rkey = replacements if replacements else ([], [])
    rlabels = {e["qid"]: (e.get("label") or "").strip().lower() for e in rqueue}
    by_replaced = {k["replaces"]: k for k in rkey}
    excluded_qids = {j["qid"] for j in justified if j["justified"]}

    cal_failed, cal_excluded = [], []
    for j in justified:
        if not j["justified"]:
            cal_failed.append({"qid": j["qid"], "expected": j["expected"],
                               "got": j["label"] or "(blank)",
                               "refused_exclusion": j["reason"], "basis": j["basis"]})
    for k in kq:
        if not k["tag"].startswith("calibration"):
            continue
        expected = "used" if k["tag"] == "calibration_used" else "unused"
        got = labels.get(k["qid"], "")
        if got == expected:
            continue
        it = items_by_ref[(k["lineage_id"], k["n"])]
        if k["qid"] in excluded_qids:
            rep = by_replaced.get(k["qid"])
            row = {"qid": k["qid"], "expected": expected, "got": got or "(blank)",
                   "claim": it["claim"], "reason": next(
                       j["reason"] for j in justified if j["qid"] == k["qid"]),
                   "replacement_qid": rep["qid"] if rep else None}
            if rep is None:
                cal_failed.append({**row, "basis": "excluded without a replacement"})
            else:
                rgot = rlabels.get(rep["qid"], "")
                rexp = "used" if rep["tag"] == "calibration_used" else "unused"
                row["replacement_label"] = rgot or "(blank)"
                if rgot != rexp:
                    cal_failed.append({**row, "basis": (
                        f"replacement qid {rep['qid']} labelled {rgot or '(blank)'!r}, "
                        f"expected {rexp!r} — a replacement's unsure or miss is a miss")})
            cal_excluded.append(row)
            continue
        cal_failed.append({"qid": k["qid"], "expected": expected,
                           "got": got or "(blank)", "claim": it["claim"],
                           "answer": it["answer"]})
    n_cal = sum(1 for k in kq if k["tag"].startswith("calibration"))
    if cal_failed:
        return {"calibration": {"passed": False, "n": n_cal,
                                "failed_items": cal_failed,
                                "excluded": cal_excluded,
                                "prereg_version": version,
                                "second_reading": (
                                    "either the operator is unreliable, or "
                                    "both scorers were wrong the same way on "
                                    "an item everyone called obvious — the "
                                    "shared-error case B-20 worried about")},
                "result": None}

    counted, unsure = [], 0
    for k in kq:
        if k["tag"] not in ("fp_candidate", "fn_candidate"):
            continue
        lab = labels.get(k["qid"], "")
        if lab == "unsure":
            unsure += 1
            continue
        if lab not in ("used", "unused"):
            continue
        if len(counted) >= JUDGE_COUNT:
            break
        it = items_by_ref[(k["lineage_id"], k["n"])]
        human_bit = lab == "used"
        counted.append({"stratum": k["tag"],
                        "minicheck_correct": bool(it["minicheck"]) == human_bit,
                        "nli_correct": (bool(it["nli"]) == human_bit
                                        if it.get("nli") is not None else None),
                        "nli_disagrees_with_lexical": (
                            it.get("nli") is not None
                            and bool(it["nli"]) != bool(it["lexical"])),
                        "discriminator": bool(it.get("discriminator")),
                        "qid": k["qid"]})

    per = {}
    for stratum in ("fp_candidate", "fn_candidate"):
        rows = [c for c in counted if c["stratum"] == stratum]
        per[stratum] = {"n": len(rows),
                        "minicheck_correct": sum(c["minicheck_correct"]
                                                 for c in rows)}
    total_n = len(counted)
    mc_total = sum(c["minicheck_correct"] for c in counted)
    if total_n >= JUDGE_COUNT:
        verdict = ("adopt_here" if mc_total >= THRESHOLDS["adopt"]
                   else "inconclusive_keep_incumbent"
                   if THRESHOLDS["inconclusive"][0] <= mc_total
                   <= THRESHOLDS["inconclusive"][1]
                   else "no_evidence_keep_incumbent")
        basis = f"{mc_total}/{total_n} against thresholds {THRESHOLDS}"
    else:
        prop = mc_total / total_n if total_n else None
        verdict = ("adopt_here" if prop is not None
                   and prop >= THRESHOLDS["adopt"] / JUDGE_COUNT
                   else "inconclusive_keep_incumbent")
        basis = (f"only {total_n} non-unsure items (< {JUDGE_COUNT}); "
                 f"proportion {prop} against {THRESHOLDS['adopt']}/"
                 f"{JUDGE_COUNT}")
    nli_rows = [c for c in counted if c["nli_correct"] is not None]
    return {
        "calibration": {"passed": True, "n": n_cal, "prereg_version": version,
                        "excluded": cal_excluded,
                        "replacements_labelled": len(cal_excluded),
                        "v1_outcome": (PREREG_HISTORY[1]["outcome"]
                                       if version == 2 else None)},
        "per_stratum": per,
        "total": {"n": total_n, "minicheck_correct": mc_total,
                  "unsure_skipped": unsure, "verdict": verdict,
                  "basis": basis, "tie_rule": TIE_RULE},
        "exploratory_nli": {
            "note": ("exploratory only — cannot trigger adoption; frame is "
                     "borrowed from MiniCheck's disagreement set"),
            "n": len(nli_rows),
            "nli_correct": sum(c["nli_correct"] for c in nli_rows),
            "on_items_where_nli_disagrees_with_lexical": {
                "n": sum(c["nli_disagrees_with_lexical"] for c in nli_rows),
                "nli_correct": sum(c["nli_correct"] for c in nli_rows
                                   if c["nli_disagrees_with_lexical"])},
            "agrees_with_minicheck": sum(
                1 for c in nli_rows if c["nli_correct"] == c["minicheck_correct"]),
        },
        "discriminators_in_counted": sum(c["discriminator"] for c in counted),
        "result": verdict,
    }


def file_sha256(path) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()
