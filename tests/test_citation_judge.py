"""Packet AP/AP.4 — judging the citation test, adjudication by exception.

THE DESIGN, after the review that reshaped it
    The CITED stratum is exhausted by hand (grouped), so the FP rate is
    exact and judge-independent. The judge is load-bearing only on the
    uncited stratum: the human additionally labels every judge-USED item
    there plus a seeded sample of judge-UNUSED — sized for the
    rule-of-three ceiling on shared error — and consequence-bearing items
    (beliefs cited in SEALED records: the archival exemption rests on
    exactly those flags) enter unconditionally. Everything the human sees
    is ONE blind shuffled queue: no strata, no flags, no judge labels.
    Grouping is by curated alias cluster only (versioned — the list is a
    judgement), never similarity (OQ-2's antonym finding). The report is
    ranges: exact FP with a Wilson interval; FN floor from confirmations
    with a ceiling from the sample; and the free number — within-group
    inconsistency of the lexical test.

CONTAMINATION PINS carried from AP unchanged (the judge is untouched):
    stripped payload, entailment criterion, per-row seeded shuffle, span
    verification, judge-never-answerer, scorer import ban.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.citation_judge import (          # noqa: E402
    JudgeIsAnswerer, build_prompt, check_judge, shuffle_items,
    validate_judgement,
)
from scripts.citation_judge_extract import (  # noqa: E402
    alias_version, group_key, strip_surface,
)
from scripts.citation_queue import build_queue          # noqa: E402
from scripts.citation_judge_score import score, wilson  # noqa: E402

ANSWER = "TAK-441 is metabolized by CYP3A4 in the liver."
QUESTION = "what breaks down tak-441?"

ROW = {
    "lineage_id": "L-1",
    "answer": ANSWER,
    "question": QUESTION,          # present in fixture; must never be sent
    "items": [
        {"n": 1, "claim": "tak-441 is_metabolized_by cyp3a4"},
        {"n": 2, "claim": "safety co_activated qtc"},
    ],
}


# ---------------------------------------------------------------------------
# Judge pins (unchanged mechanism, carried forward)
# ---------------------------------------------------------------------------

def test_the_prompt_contains_no_flag_weight_group_id_or_question():
    prompt, _order = build_prompt(ROW, master_seed=7)
    for leak in ("cited", "[w=", "g=session", "g=topic", "belief_id",
                 QUESTION, "consequence", "stratum"):
        assert leak not in prompt.lower(), f"leak in judge prompt: {leak!r}"
    assert ANSWER in prompt


def test_the_prompt_defines_entailment_not_overlap():
    prompt, _ = build_prompt(ROW, master_seed=7)
    flat = " ".join(prompt.split())
    for required in ("Sharing words does not count",
                     "same subject", "UNSURE", "span"):
        assert required in flat, f"prompt lost its criterion: {required!r}"


def test_shuffle_is_deterministic_per_seed_and_varies_per_row():
    items = [{"n": i, "claim": f"c{i}"} for i in range(1, 13)]
    a = shuffle_items(items, master_seed=7, lineage_id="L-1")
    b = shuffle_items(items, master_seed=7, lineage_id="L-1")
    c = shuffle_items(items, master_seed=7, lineage_id="L-2")
    assert a == b and a != c
    assert [i["n"] for i in a] != [i["n"] for i in items]
    assert sorted(i["n"] for i in a) == list(range(1, 13))


def test_a_used_judgement_without_its_span_is_discarded_and_counted():
    kept, discarded = validate_judgement(
        [{"item": 1, "label": "USED", "span": "metabolized by CYP3A4"},
         {"item": 2, "label": "USED", "span": "cleared renally"},
         {"item": 3, "label": "USED", "span": ""},
         {"item": 4, "label": "UNUSED", "span": ""}], ANSWER)
    assert len(kept) == 2 and discarded == 2


def test_the_judge_refuses_to_mark_its_own_work():
    with pytest.raises(JudgeIsAnswerer):
        check_judge("qwen3.5:9b-q4_K_M", "qwen3.5:9b")
    check_judge("qwen3.5:9b-q4_K_M",
                "deepseek-r1:14b-qwen-distill-q4_K_M")   # must not raise


def test_the_scorer_does_not_import_the_citation_machinery():
    tree = ast.parse((ROOT / "scripts" / "citation_judge_score.py")
                     .read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names
                          if a.name == "backend.kb.citation"]
        elif isinstance(node, ast.ImportFrom):
            if node.module == "backend.kb.citation":
                offenders.append(node.module)
            offenders += [a.name for a in node.names
                          if a.name in ("cited_ids", "is_cited", "_mentions")]
    assert not offenders, f"the scorer imports what it scores: {offenders}"


def test_the_import_ban_can_actually_fail():
    tree = ast.parse("from backend.kb.citation import cited_ids\n")
    assert any(isinstance(n, ast.ImportFrom)
               and n.module == "backend.kb.citation" for n in ast.walk(tree))


# ---------------------------------------------------------------------------
# Grouping: curated clusters only, versioned
# ---------------------------------------------------------------------------

ALIASES = {"is": "is_a", "is_a_type_of": "is_a"}


def test_alias_clusters_merge_spellings_and_never_opposites():
    assert group_key("TAK-441", "is", "inhibitor", ALIASES) \
        == group_key("tak-441", "is_a_type_of", "Inhibitor", ALIASES)
    assert group_key("qtc", "increases", "at 600mg", ALIASES) \
        != group_key("qtc", "decreases", "at 600mg", ALIASES), (
        "opposites merged — one propagated label would be confidently "
        "wrong on half the group (the OQ-2 hazard)")


def test_the_alias_list_is_versioned_and_version_moves_with_the_list():
    v1 = alias_version(ALIASES)
    v2 = alias_version({**ALIASES, "raises": "increases"})
    assert v1 != v2
    assert v1.startswith("k3-2-") and v2.startswith("k3-3-")


def test_strip_surface_removes_metadata_and_outer_parens_only():
    assert strip_surface("(a is_a b) [w=0.97 g=session]") == "a is_a b"
    assert strip_surface("(mercury (planet) is_a planet) [w=0.5 g=topic]") \
        == "mercury (planet) is_a planet"


# ---------------------------------------------------------------------------
# The blind queue
# ---------------------------------------------------------------------------

def _key_doc():
    """Two rows, strata by construction: g1 cited, g2 uncited+judge-used
    (FN candidate), g3 uncited consequence-bearing, g4..g9 uncited
    remainder for the sample."""
    rows = [{"lineage_id": "L-1", "answer_model": "m", "items": [
        {"n": 1, "belief_id": 1, "claim": "c1", "group": "g1",
         "lexical_cited": True, "consequence": False},
        {"n": 2, "belief_id": 2, "claim": "c1 variant", "group": "g1",
         "lexical_cited": False, "consequence": False},   # mixed-flag group
        {"n": 3, "belief_id": 3, "claim": "c2", "group": "g2",
         "lexical_cited": False, "consequence": False},
        {"n": 4, "belief_id": 4, "claim": "c3", "group": "g3",
         "lexical_cited": False, "consequence": True},
    ]}]
    # A remainder large enough that two seeds drawing the same sample is
    # implausible — with six groups, seeds 7 and 8 genuinely collided and
    # made the determinism test's non-vacuity half unprovable.
    rows.append({"lineage_id": "L-2", "answer_model": "m", "items": [
        {"n": i, "belief_id": 10 + i, "claim": f"r{i}", "group": f"g{i}",
         "lexical_cited": False, "consequence": False}
        for i in range(1, 15)]})
    return {"alias_version": "k3-test", "rows": rows}


def _judgements():
    return {"rows": [
        {"lineage_id": "L-1", "items": [
            {"item": 1, "label": "USED", "span": "x", "position": 1},
            {"item": 2, "label": "UNUSED", "span": "", "position": 2},
            {"item": 3, "label": "USED", "span": "y", "position": 3},
            {"item": 4, "label": "UNUSED", "span": "", "position": 4}]},
        # Every L-2 item judged: since W1's close (M8) an item the judge
        # never named is no judgement and goes to a human, so a fixture
        # whose remainder is "judge-UNUSED" names every member UNUSED (it
        # named 1 to 6 before, and 7 to 14 were sampled as if judged).
        {"lineage_id": "L-2", "items": [
            {"item": i, "label": "UNUSED", "span": "", "position": i}
            for i in range(1, 15)]}],
        "discarded": 0}


def test_the_queue_exhausts_cited_includes_fn_and_consequence_and_samples():
    queue, key = build_queue(_key_doc(), _judgements(), seed=7, sample_n=3)
    reasons = {}
    for k in key:
        reasons[k["reason"]] = reasons.get(k["reason"], 0) + 1
    assert reasons["cited_stratum"] == 1          # g1, mixed flags, ANY cited
    assert reasons["fn_candidate"] == 1           # g2, judge said USED
    assert reasons["consequence"] == 1            # g3, unconditional
    assert reasons["agreement_sample"] == 3       # seeded from the remainder


def test_the_queue_is_blind():
    """No stratum marker, flag, judge label, reason, weight, or id reaches
    what the human reads — asserted against the constructed entries."""
    import json as _json
    queue, _key = build_queue(_key_doc(), _judgements(), seed=7, sample_n=3)
    text = _json.dumps(queue).lower()
    for leak in ("cited", "judge", "reason", "stratum", "consequence",
                 "belief_id", "lexical", "sample", "[w="):
        assert leak not in text, f"the queue leaks {leak!r} to the human"


def test_the_queue_is_deterministic_and_the_sample_is_seeded():
    a, ka = build_queue(_key_doc(), _judgements(), seed=7, sample_n=3)
    b, kb = build_queue(_key_doc(), _judgements(), seed=7, sample_n=3)
    c, kc = build_queue(_key_doc(), _judgements(), seed=8, sample_n=3)
    assert [e["claims"] for e in a] == [e["claims"] for e in b]
    assert {(k["lineage_id"], k["group"]) for k in ka
            if k["reason"] == "agreement_sample"} != \
           {(k["lineage_id"], k["group"]) for k in kc
            if k["reason"] == "agreement_sample"}, (
        "a different seed must move the agreement sample")


# ---------------------------------------------------------------------------
# The scorer: ranges, not points
# ---------------------------------------------------------------------------

def _label(queue, key, mapping):
    kq = {k["qid"]: k for k in key}
    for e in queue:
        k = kq[e["qid"]]
        e["label"] = mapping.get((k["lineage_id"], k["group"]), "unused")
    return {"entries": queue, "seed": 7, "sample_n": 3,
            "alias_version": "k3-test"}


def _score_fixture(mapping):
    queue, key = build_queue(_key_doc(), _judgements(), seed=7, sample_n=3)
    labelled = _label(queue, key, mapping)
    return score(_key_doc(), _judgements(), labelled,
                 {"entries": key, "seed": 7})


def test_fp_is_exact_over_the_exhausted_cited_stratum():
    out = _score_fixture({("L-1", "g1"): "unused"})   # the cited group is FP
    fp = out["false_positive"]
    assert fp["cited_items"] == 1 and fp["false_positives"] == 1
    assert fp["rate_exact"] == 1.0
    lo, hi = fp["wilson_95"]
    assert 0.0 <= lo < 1.0 <= hi <= 1.0


def test_fn_reports_a_range_with_the_rule_of_three_ceiling():
    """Non-vacuity both directions: a confirmed FN exists (the judge-used
    uncited group labelled used), the agreement sample shows zero shared
    errors, and the ceiling must exceed the floor by the 3/n allowance
    over the unlabelled remainder."""
    out = _score_fixture({("L-1", "g2"): "used"})
    fn = out["false_negative"]
    assert len(fn["confirmed_cases"]) == 1
    assert fn["agreement_sample"]["n"] == 3
    assert fn["agreement_sample"]["shared_error_rate"] == 0.0
    assert fn["agreement_sample"]["ceiling_rate_rule_of_three"] \
        == pytest.approx(1.0)
    floor, ceiling = fn["range"]
    assert 0 < floor < ceiling <= 1.0
    assert fn["unlabelled_remainder"] > 0, (
        "vacuous: nothing was left unlabelled, so the ceiling bounds "
        "nothing")


def test_within_group_inconsistency_is_measured_for_free():
    out = _score_fixture({})
    wg = out["within_group_inconsistency"]
    assert wg["multi_member_groups"] == 1
    assert wg["mixed_flag_groups"] == 1          # g1: cited + uncited members
    assert wg["rate_among_multi"] == 1.0


def test_unsure_is_excluded_and_counted_and_the_cited_finding_fires():
    out = _score_fixture({("L-1", "g1"): "unsure"})
    assert out["false_positive"]["unsure"] >= 1
    assert out["false_positive"]["rate_exact"] is None   # nothing labelled
    assert any("extraction" in f for f in out["findings"]), (
        "a fully-unsure cited stratum must raise the fragments finding")


def test_the_dependency_names_where_the_judge_is_unmeasured():
    out = _score_fixture({})
    assert "both called unused" in out["dependency"]
    assert out["alias_version"] == "k3-test"


def test_wilson_interval_is_sane():
    lo, hi = wilson(0, 30)
    assert lo == 0.0 and 0.05 < hi < 0.2      # ~11.4% at n=30, k=0
