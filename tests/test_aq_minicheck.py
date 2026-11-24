"""Packet AQ — is MiniCheck better here? Sentinels on the instrument.

The experiment's honesty is structural, and this file pins it:
  - the pre-registered thresholds live in SOURCE and match what the
    packet cites (14 / 11-13 / <=10; 13 per stratum, 26 judged, first 20
    non-unsure counted; 3+3 calibration, 6/6 to pass; MiniCheck at its
    published 0.5; MiniCheck primary, the NLI head exploratory);
  - the verbalisation map is ONE versioned artefact marking every entry
    hand or template, its version moves with any phrase, the tail rule is
    underscore-stripping and nothing else, and a top-twenty relation left
    templated is flagged rather than silently scored;
  - the blind queue carries answer + verbalised claim only — no score, no
    bit, no stratum, no tag — asserted against the constructed file;
  - the sample is seeded and reproducible; calibration items are MIXED
    into the shuffle, never appended;
  - evaluation reads calibration first and, on a miss, returns the failed
    items and nothing else; strata are counted apart; the unsure rule
    counts the first 20 non-unsure in queue order;
  - the scorer imports its model stack lazily (AST): the repository's
    suite never needs torch;
  - non-vacuity: the fixture's disagreement set is non-empty in BOTH
    directions and its calibration pool holds both agreed classes.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import _aq                                       # noqa: E402
from scripts._aq import (                                     # noqa: E402
    build_map, build_queue, evaluate, spot_check, strata, template_phrase,
    verbalise,
)


# ---------------------------------------------------------------------------
# Pre-registration in source
# ---------------------------------------------------------------------------

def test_thresholds_in_source_match_the_packet():
    assert _aq.THRESHOLDS == {"adopt": 14, "inconclusive": (11, 13),
                              "no_evidence": 10}
    assert _aq.SAMPLE_PER_STRATUM == 13 and _aq.JUDGE_COUNT == 20
    assert _aq.CALIBRATION_PER_SIDE == 3
    assert _aq.MINICHECK_THRESHOLD == 0.5
    assert _aq.PRIMARY == "minicheck" and _aq.EXPLORATORY == "nli"
    assert "incumbent" in _aq.TIE_RULE
    assert "calibration" in _aq.CALIBRATION_UNSURE_RULE
    # The one-sided 5% line, recomputed rather than trusted.
    from math import comb
    assert abs(sum(comb(20, k) for k in range(14, 21)) / 2 ** 20 - 0.0577) < 1e-3


def test_the_variant_switch_is_recorded_not_overwritten():
    """The MiniCheck variant is instrument. It moved once, before any label
    existed, and the record carries the previous variant and the published
    reason — a silent overwrite would let a later reader assume the
    default was always this one."""
    rec = _aq.VARIANT_RECORD["minicheck"]
    assert _aq.MODELS["minicheck"] == rec["current"] \
        == "lytang/MiniCheck-Flan-T5-Large"
    assert rec["previous"] == "lytang/MiniCheck-DeBERTa-v3-Large"
    assert "74.7" in rec["reason"] and "72.7" in rec["reason"]
    assert "before any label" in rec["switched"]
    assert _aq.MINICHECK_PACKAGE_NAME == "flan-t5-large"


# ---------------------------------------------------------------------------
# The verbalisation map
# ---------------------------------------------------------------------------

FREQ = {"has": 80, "causes": 74, "is_metabolized_by": 30, "affinity_for": 26,
        "was": 24, "demonstrates_affinity_for": 22, "contains": 18,
        "produces": 17, "is_inhibited_by": 3, "shows_no_effect_on": 2}


def test_map_marks_hand_and_template_and_strips_underscores_only():
    m = build_map(FREQ)
    assert m["entries"]["has"]["source"] == "hand"
    assert m["entries"]["is_inhibited_by"]["source"] == "template"
    assert m["entries"]["is_inhibited_by"]["phrase"] == "{s} is inhibited by {o}"
    assert template_phrase("shows_no_effect_on") == "{s} shows no effect on {o}"
    assert verbalise(m, "TAK-441", "is_metabolized_by", "CYP3A4") \
        == "TAK-441 is metabolized by CYP3A4"
    assert m["hand_count"] + m["template_count"] == len(FREQ)


def test_map_version_moves_with_any_phrase_and_not_with_counts():
    a = build_map(FREQ)
    b = build_map({**FREQ, "has": 81})
    c = build_map(FREQ, hand={**_aq.HAND_WRITTEN, "has": "{s} possesses {o}"})
    assert a["version"] == b["version"], "counts are data, not instrument"
    assert a["version"] != c["version"], "a changed phrase must move the version"


def test_a_templated_top_twenty_relation_is_flagged_not_silently_scored():
    freq = {f"rare_form_{i}": 100 - i for i in range(25)}   # none hand-written
    m = build_map(freq)
    assert len(m["top_n_templated"]) == 20, (
        "the map must refuse to finalise with templated forms in the head")
    # With fewer relations than top_n, EVERY relation is the head — so the
    # two templated forms in FREQ are flagged, by count order. (First draft
    # asserted [] here; the code was right and the assertion was wrong.)
    assert build_map(FREQ)["top_n_templated"] \
        == ["is_inhibited_by", "shows_no_effect_on"]
    assert build_map(FREQ, top_n=8)["top_n_templated"] == []


def test_spot_check_is_seeded_over_templated_forms_only():
    freq = {**FREQ, **{f"tail_{i}": 1 for i in range(60)}}
    m = build_map(freq)
    ex = {r: ("s", "o") for r in freq}
    a = spot_check(m, ex, seed=7, n=50)
    b = spot_check(m, ex, seed=7, n=50)
    assert a == b and len(a) == 50
    assert all(m["entries"][x["relation"]]["source"] == "template" for x in a)


# ---------------------------------------------------------------------------
# Fixture: scores with disagreement in BOTH directions and both agreed classes
# ---------------------------------------------------------------------------

def _items():
    items = []
    n = 0
    for lex, mc, count in ((1, 0, 15), (0, 1, 15), (1, 1, 5), (0, 0, 5)):
        for _ in range(count):
            n += 1
            items.append({"lineage_id": f"L-{n % 7}", "n": n,
                          "answer": f"answer text {n % 7}",
                          "claim": f"claim {n}", "lexical": lex,
                          "minicheck": mc, "minicheck_score": 0.9 * mc,
                          "nli": mc, "discriminator": n % 3 == 0})
    return items


def test_non_vacuity_disagreements_both_directions_and_both_agreed_classes():
    s = strata(_items())
    assert len(s["fp_candidates"]) > 0 and len(s["fn_candidates"]) > 0
    assert len(s["agreed_used"]) >= 3 and len(s["agreed_unused"]) >= 3


def test_queue_is_blind():
    queue, _key, _c = build_queue(_items(), seed=7)
    text = json.dumps(queue).lower()
    for leak in ("minicheck", "nli", "lexical", "score", "stratum", "tag",
                 "candidate", "calibration", "discriminator", "bit"):
        assert leak not in text, f"the queue leaks {leak!r} to the human"
    assert all(set(e) == {"qid", "answer", "claim", "label"} for e in queue)


def test_queue_is_seeded_and_calibration_is_mixed_not_appended():
    q1, k1, c1 = build_queue(_items(), seed=7)
    q2, k2, _ = build_queue(_items(), seed=7)
    q3, k3, _ = build_queue(_items(), seed=8)
    assert [e["claim"] for e in q1] == [e["claim"] for e in q2]
    assert [e["claim"] for e in q1] != [e["claim"] for e in q3]
    assert c1["fp_sampled"] == 13 and c1["fn_sampled"] == 13
    assert c1["calibration_used"] == 3 and c1["calibration_unused"] == 3
    positions = [k["qid"] for k in k1 if k["tag"].startswith("calibration")]
    assert min(positions) <= len(k1) - 6, (
        "calibration items sit at the end — they were appended, not mixed")


def _label(queue, key, items, *, rule):
    by = {(i["lineage_id"], i["n"]): i for i in items}
    kq = {k["qid"]: k for k in key}
    for e in queue:
        e["label"] = rule(kq[e["qid"]], by[(kq[e["qid"]]["lineage_id"],
                                            kq[e["qid"]]["n"])])
    return by


def test_calibration_failure_reports_the_failed_items_and_nothing_else():
    items = _items()
    queue, key, _ = build_queue(items, seed=7)

    def rule(k, it):
        if k["tag"] == "calibration_used":
            return "unsure"                       # a miss, by the rule
        if k["tag"] == "calibration_unused":
            return "unused"
        return "used"
    by = _label(queue, key, items, rule=rule)
    out = evaluate(queue, key, by)
    assert out["calibration"]["passed"] is False
    assert len(out["calibration"]["failed_items"]) == 3
    assert out["result"] is None and "per_stratum" not in out
    assert "shared" in out["calibration"]["second_reading"]


def test_strata_are_counted_apart_and_the_unsure_rule_holds():
    items = _items()
    queue, key, _ = build_queue(items, seed=7)

    def rule(k, it):
        if k["tag"] == "calibration_used":
            return "used"
        if k["tag"] == "calibration_unused":
            return "unused"
        if k["tag"] == "fp_candidate":
            return "unused"        # human sides with MiniCheck on FP items
        return "unsure"            # every FN item unsure -> skipped
    by = _label(queue, key, items, rule=rule)
    out = evaluate(queue, key, by)
    assert out["calibration"]["passed"] is True
    assert out["per_stratum"]["fp_candidate"]["minicheck_correct"] == 13
    assert out["per_stratum"]["fn_candidate"]["n"] == 0
    assert out["total"]["unsure_skipped"] == 13
    assert out["total"]["n"] == 13                # fewer than 20 remain
    assert "proportion" in out["total"]["basis"]  # thresholds as proportions
    assert out["exploratory_nli"]["note"].startswith("exploratory only")


def test_adopt_only_when_minicheck_clears_fourteen_of_twenty():
    items = _items()
    queue, key, _ = build_queue(items, seed=7)

    def rule(k, it):
        if k["tag"].startswith("calibration"):
            return "used" if k["tag"].endswith("used") and \
                not k["tag"].endswith("unused") else "unused"
        # human agrees with MiniCheck on all 26 disagreements
        return "used" if it["minicheck"] else "unused"
    by = _label(queue, key, items, rule=rule)
    out = evaluate(queue, key, by)
    assert out["total"]["n"] == 20 and out["total"]["minicheck_correct"] == 20
    assert out["result"] == "adopt_here"
    assert out["per_stratum"]["fp_candidate"]["n"] \
        + out["per_stratum"]["fn_candidate"]["n"] == 20


# ---------------------------------------------------------------------------
# The scorer never imports its model stack at module level
# ---------------------------------------------------------------------------

def test_the_scorer_imports_torch_lazily():
    tree = ast.parse((ROOT / "scripts" / "aq_score.py").read_text(encoding="utf-8"))
    heavy = {"torch", "transformers", "minicheck"}
    top_level = [n for n in tree.body
                 if isinstance(n, (ast.Import, ast.ImportFrom))]
    offenders = [ast.unparse(n) for n in top_level
                 if any((a.name.split(".")[0] in heavy)
                        for a in getattr(n, "names", []))
                 or (getattr(n, "module", "") or "").split(".")[0] in heavy]
    assert not offenders, (
        f"the scorer imports its model stack at module level: {offenders} — "
        f"the repository's suite must never need torch")
