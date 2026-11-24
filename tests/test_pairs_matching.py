"""Packet W.4 — the harness was measuring its own matching rule.

WHAT WENT WRONG
    `expect` is a list of alternative correct answers — the operator's pairs
    file says so in its own header. The harness read it as a conjunction:

      - the scorer required EVERY fragment to appear across the retrieved set;
      - `_diagnose` required EVERY fragment inside ONE surface.

    32 of the operator's 36 pairs carry more than one fragment, so under that
    rule almost nothing could resolve whatever the store held. Measured
    against the eight surfaces recorded verbatim in `OQ-3_RETRIEVAL_CONTEXT.md`:
    **exactly one pair of thirty-six could resolve.** `not_indexed` fired for
    14 of 15 misses and said nothing at all about the store.

    The clearest case is a GROUP A pair — the control group that is supposed
    to work on the lexical channel alone. `"what does interval training
    improve?"` expects four fragments; all four are in the store, in four
    different rows; it could never pass, and it would have read as the lexical
    channel failing.

**EVERY FIGURE THE HARNESS PRINTED BEFORE THIS IS VOID**, including the
0.36 -> 0.58 headline. Those numbers measured the matching rule, not
retrieval, and there is nothing in them worth carrying forward.

HOW IT WAS FOUND, WHICH IS THE PART WORTH KEEPING
    Not by the harness. By replaying its scorer against surfaces recorded
    independently in OQ-3 and noticing that only one pair could ever pass.
    Second time in this session: the vocabulary tests ran at a 0.05 threshold
    while the shipped default was 0.55, and no test could see it because every
    test set its own. **A measurement's own correctness needs a check that
    does not use the measurement.** These tests are that check for this one.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Verbatim from OQ-3_RETRIEVAL_CONTEXT.md and the O.2b acceptance test. Real
# surfaces from the operator's store, recorded by something other than the
# harness — which is the only reason they can be used to check it.
OQ3_SURFACES = [
    "mean placebo-corrected QTc change at 600mg supratherapeutic dose was 11.4ms",
    "Interval training has been shown to improve cardiovascular health",
    "regular_interval_training reduces_risk_of heart_disease",
    "Interval training enhances cardiac_output",
    "regular_interval_training strengthens heart_muscles",
    "Aerobic exercise enhances cardiovascular endurance",
    "TAK-441 has_low_likelihood_of QT_prolongation",
    "preclinical_hERG_assay suggests reduced_likelihood",
]


def _load(name):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name[:-3]] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mr():
    return _load("measure_retrieval.py")


@pytest.fixture(scope="module")
def audit():
    return _load("audit_pairs.py")


# ---------------------------------------------------------------------------
# the matching rule
# ---------------------------------------------------------------------------

def test_any_is_the_default(mr, tmp_path):
    p = tmp_path / "pairs.json"
    p.write_text(json.dumps([{"question": "q", "expect": ["a", "b"]}]),
                 encoding="utf-8")
    assert mr._load_pairs(p)[0]["match"] == "any"


def test_any_needs_one_fragment_all_needs_every_one(mr):
    surfaces = ["QTc change at 600mg was measured"]
    assert mr._found(surfaces, ["QTc", "11.4ms"], "any") is True
    assert mr._found(surfaces, ["QTc", "11.4ms"], "all") is False
    assert mr._found(surfaces, ["nothing", "here"], "any") is False


def test_all_survives_because_o2b_needs_it(mr):
    """`["11.4ms", "supratherapeutic"]` genuinely wants both — the second is
    what makes the first the right row rather than a coincidence. Removing
    the mode entirely would trade one wrong default for another."""
    assert mr._found([OQ3_SURFACES[0]], ["11.4ms", "supratherapeutic"], "all")
    assert not mr._found(["QTc change was 11.4ms"],
                         ["11.4ms", "supratherapeutic"], "all")


def test_an_unknown_match_mode_falls_back_to_any(mr, tmp_path, capsys):
    p = tmp_path / "pairs.json"
    p.write_text(json.dumps([{"question": "q", "expect": ["a"],
                              "match": "MOST"}]), encoding="utf-8")
    pairs = mr._load_pairs(p)
    assert pairs[0]["match"] == "any"
    assert "WARNING" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# THE CHECK THAT DOES NOT USE THE MEASUREMENT
# ---------------------------------------------------------------------------

def test_the_old_conjunction_rule_could_not_have_worked(mr):
    """Replays the defect against surfaces recorded outside the harness.

    Under ALL-in-one-row, one of these eight surfaces has to contain every
    fragment of a pair. Against the operator's own file that was true for
    exactly one pair in thirty-six — which is the 14-of-15 `not_indexed`
    report, reproduced.
    """
    pairs = json.loads(
        (ROOT / "tests" / "fixtures" / "oq3_pairs_excerpt.json")
        .read_text(encoding="utf-8"))
    resolvable_all = sum(
        1 for p in pairs
        if any(mr._found([s], p["expect"], "all") for s in OQ3_SURFACES))
    resolvable_any = sum(
        1 for p in pairs
        if any(mr._found([s], p["expect"], "any") for s in OQ3_SURFACES))
    assert resolvable_all == 1, (
        f"the historical defect no longer reproduces ({resolvable_all}) — if "
        f"the excerpt changed, this test stops standing for anything")
    assert resolvable_any > resolvable_all * 5, (
        f"switching to 'any' barely moved the needle ({resolvable_any}); the "
        f"diagnosis behind W.4 needs re-deriving")


def test_a_pair_whose_fragments_span_rows_now_resolves(mr):
    """`"what does interval training improve?"` — group A, all four fragments
    in the store, four different rows. The single clearest instance."""
    expect = ["endurance", "cardiac_output", "heart_muscle", "cardiovascular"]
    assert not any(mr._found([s], expect, "all") for s in OQ3_SURFACES)
    assert mr._found(OQ3_SURFACES, expect, "any")
    # ...and each fragment really is somewhere, so this is a rule defect and
    # not an absent-fragment problem.
    blob = " ".join(OQ3_SURFACES).lower()
    assert all(e.lower() in blob for e in expect)


# ---------------------------------------------------------------------------
# _diagnose
# ---------------------------------------------------------------------------

class _Idx:
    def __init__(self, ranked, surfaces):
        self._r, self._s = ranked, surfaces

    def search(self, qv, *, k, min_similarity=None):
        return self._r[:k]

    def surface_for(self, tid):
        return self._s.get(tid, "")


def test_not_indexed_now_means_the_store_lacks_every_fragment(mr):
    idx = _Idx([(1, 0.9), (2, 0.8)],
               {1: "aspirin is_a analgesic", 2: "python is_a language"})
    d = mr._diagnose(idx, [0.0], ["Lima", "Peru"], slots=5, threshold=0.3,
                     n_rows=2)
    assert d["binding"] == "not_indexed"
    assert d["missing"] == ["Lima", "Peru"]


def test_partially_present_fragments_are_unsatisfiable_not_not_indexed(mr):
    """The verdict that used to be conflated. `hERG` is in the store; `18.4`
    is not; under match='all' no row can satisfy the pair. Calling that
    `not_indexed` told the operator to go looking for a missing belief that
    was half there."""
    idx = _Idx([(1, 0.9)], {1: "preclinical_hERG_assay suggests reduced_likelihood"})
    d = mr._diagnose(idx, [0.0], ["hERG", "18.4"], slots=5, threshold=0.3,
                     n_rows=1, match="all")
    assert d["binding"] == "unsatisfiable"
    assert d["missing"] == ["18.4"]


def test_missing_is_reported_even_when_the_pair_resolves(mr):
    """A pair can pass on one fragment while another is absent — worth knowing
    before the absent one is quoted as evidence of anything."""
    idx = _Idx([(1, 0.9)], {1: "TAK-441 has_low_likelihood_of QT_prolongation"})
    d = mr._diagnose(idx, [0.0], ["QT_prolongation", "cardiotoxicity"],
                     slots=5, threshold=0.3, n_rows=1)
    assert d["binding"] == "rank" and d["found_in_index"] is True
    assert d["missing"] == ["cardiotoxicity"]


def test_the_three_retrieval_verdicts_still_work(mr):
    idx = _Idx([(1, 0.90), (2, 0.80), (7, 0.42)], {7: "QTc change was 11.4ms"})
    assert mr._diagnose(idx, [0.0], ["11.4ms"], slots=2, threshold=0.30,
                        n_rows=3)["binding"] == "slots"
    assert mr._diagnose(idx, [0.0], ["11.4ms"], slots=10, threshold=0.50,
                        n_rows=3)["binding"] == "threshold"
    assert mr._diagnose(idx, [0.0], ["11.4ms"], slots=10, threshold=0.30,
                        n_rows=3)["binding"] == "rank"


# ---------------------------------------------------------------------------
# controls
# ---------------------------------------------------------------------------

def test_expect_miss_is_parsed_and_defaults_false(mr, tmp_path):
    p = tmp_path / "pairs.json"
    p.write_text(json.dumps([
        {"question": "a", "expect": ["x"]},
        {"question": "b", "expect": ["y"], "expect_miss": True},
    ]), encoding="utf-8")
    pairs = mr._load_pairs(p)
    assert [q["expect_miss"] for q in pairs] == [False, True]


def test_controls_are_excluded_from_the_headline(mr):
    """Six expected-miss pairs used to be filed under "STILL NOT FOUND — the
    next packet's input", so a control group behaving correctly was reported
    as six failures and dragged the denominator with it."""
    src = (ROOT / "scripts" / "measure_retrieval.py").read_text(encoding="utf-8")
    assert 'if pair["expect_miss"]:' in src
    assert "scored += 1" in src
    assert "of {n} scored pairs" in src


def test_group_comment_rows_are_not_counted_as_pairs(mr, tmp_path):
    p = tmp_path / "pairs.json"
    p.write_text(json.dumps([
        {"_comment": "notes"},
        {"_group": "A — word overlap"},
        {"question": "a", "expect": ["x"]},
    ]), encoding="utf-8")
    pairs = mr._load_pairs(p)
    assert len(pairs) == 1 and pairs[0]["group"] == "A"


# ---------------------------------------------------------------------------
# audit_pairs.py agrees with the harness about what a pair is
# ---------------------------------------------------------------------------

def test_the_audit_parses_the_same_pairs_as_the_harness(mr, audit, tmp_path):
    """An audit that sees a different set from the harness is worse than no
    audit — it would clear fragments for pairs the harness never ran."""
    p = tmp_path / "pairs.json"
    p.write_text(json.dumps([
        {"_comment": "x"},
        {"_group": "B — no overlap"},
        {"question": "a", "expect": ["x", "y"], "match": "all"},
        {"question": "b", "expect": ["z"], "expect_miss": True},
        {"question": "", "expect": ["nope"]},
    ]), encoding="utf-8")
    a = audit._load_pairs(p)
    m = mr._load_pairs(p)
    assert [x["question"] for x in a] == [x["question"] for x in m]
    assert [x["match"] for x in a] == [x["match"] for x in m]
    assert [x["expect_miss"] for x in a] == [x["expect_miss"] for x in m]


def test_the_audit_needs_no_provider(audit):
    """It is a substring query. If it ever grows an embedding call it stops
    being an independent check and becomes part of the thing it checks."""
    import ast
    tree = ast.parse((ROOT / "scripts" / "audit_pairs.py")
                     .read_text(encoding="utf-8"))
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for forbidden in ("get_embedding", "embed_via_provider", "get_provider",
                      "SemanticIndex", "TripleRetriever"):
        assert forbidden not in names, (
            f"audit_pairs.py reaches for {forbidden} — it must stay a check "
            f"that does not use the measurement")
