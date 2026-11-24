"""Packet O.3 — the gate meets the corpus extraction actually produces.

WHAT AN HOUR OF USE STORED, all accepted by the gate as it stood:

    Study Aim | aimed_to_determine_if | TAK-441_prolonged_the_QT_interval_as_
                                        a_potential_indicator_of_cardiotoxicity
    preclinical_hERG_assay_for_TAK-441 | suggests | reduced_likelihood_of_QT_
                                        prolongation_and_cardiotoxicity_risks
    Clinical_Implications | highlight_the_importance_of_monitoring_levels_and_
                            adjusting_ | TAK-441_when_concomitantly_used_with_
                            CYP3A4 inhibitors

TWO CORRECTIONS TO THE REPORTED DIAGNOSIS, both measured before anything was
changed — because the named instance is not always the whole class, and this
time it was not even the right mechanism.

1. THE TOKENISER WAS NEVER BLIND TO UNDERSCORES. `_tokens()` has always done
   `.replace("_", " ")`. The tokens for the first object above are
   ['tak-441','prolonged','the','qt','interval','as','a','potential',
    'indicator','of','cardiotoxicity'] — correct, and the rules simply had
   nothing to say about them.

   The real reason they passed: `multi_preposition` needs TWO prepositions
   and these clauses carry exactly one (`of`). `as` is not in PREPOSITIONS.
   So this is not a tokenising defect at all — it is the word-count cap K.2
   deliberately declined to ship for want of evidence, and this corpus is the
   evidence K said would decide it.

2. `structured_literal` DOES FIRE. Measured: `"['graph-based systems']"` as an
   object returns `object:structured_literal` today, with the gate enabled by
   default. So that row cannot have entered through `add()` with the gate on
   as shown. `test_the_structured_literal_rule_was_never_broken` pins that,
   and the report names what would settle the row's provenance.

AND ONE DEFECT THE REPORT FOUND THE SYMPTOM OF BUT NOT THE CAUSE: the
truncated relation was truncated BY THE GATE. See
`test_an_over_long_relation_is_refused_not_silently_truncated`.

Nothing already stored is touched (I-9). This is a boundary fix.
"""
from __future__ import annotations

import pytest

from backend.kb.gate import (
    MAX_RELATION_CHARS, MAX_TERM_TOKENS, SUBORDINATORS, _tokens,
    inspect_relation, inspect_term, normalise_relation, normalise_term,
)
from pathlib import Path

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import BeliefRejected, TripleStore
from tests._tracked import tracked_backend

ROOT_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

# Clause-shaped strings from the operator's session, each with the rule that
# must catch it. Measured before and after, so the table records which rules
# O.3 actually made load-bearing rather than claiming credit for all of them:
#
#   6 of these 7 were ACCEPTED by the pre-O.3 gate.
#   The seventh was already caught by `copula:can` and is here as a control.
#
# Two entries are flattened TRIPLES rather than stored terms — my first draft
# put them in as terms and one of them failed for `copula:can`, which was the
# fixture being wrong, not the gate. They are kept, correctly attributed,
# because a rule that fires for the wrong reason is worth knowing about.
CLAUSE_CASES = [
    ("TAK-441_prolonged_the_QT_interval_as_a_potential_indicator_of_cardiotoxicity",
     "too_many_tokens"),
    ("TAK-441_when_concomitantly_used_with_CYP3A4 inhibitors",
     "subordinate_clause"),
    ("reduced_likelihood_of_QT_prolongation_and_cardiotoxicity_risks",
     "too_many_tokens"),
    ("aimed_to_determine_if_TAK-441_prolonged_the_QT_interval",
     "too_many_tokens"),
    ("highlight_the_importance_of_monitoring_levels_and_adjusting_dose",
     "too_many_tokens"),
    ("higher heart rate makes the QT interval appear shorter",
     "too_many_tokens"),
    ("abnormal QT interval can indicate potential heart rhythm disorders",
     "copula"),          # control: already caught before O.3
]
CLAUSES = [c for c, _ in CLAUSE_CASES]

# Real technical concepts that must survive. Several are from the operator's
# own domain, and `corrected QT adjusted for resting heart rate` is the object
# of a row their session actually wrote — it is the tightest case in the set,
# at exactly MAX_TERM_TOKENS - 1.
LEGITIMATE = [
    "programming_language", "interval_training", "TAK-441_therapeutic_dose",
    "non-small cell lung carcinoma", "Neisseria gonorrhoeae infection",
    "corrected QT adjusted for resting heart rate",
    "theory of relativity", "war of the roses", "united states of america",
    "preclinical hERG assay", "cytochrome P450 3A4 inhibitor",
    "placebo-corrected QTc change", "single ascending dose study",
    "mean arterial blood pressure", "QT interval prolongation risk",
    "adverse event of special interest",
    "area under the concentration time curve",
    "randomised double blind placebo controlled trial",
    "supratherapeutic dose", "ventricular depolarization",
    "reduced likelihood of QT prolongation",
    "maximum tolerated dose", "time to peak plasma concentration",
]


# ---------------------------------------------------------------------------
# The correction: the tokeniser was never the problem
# ---------------------------------------------------------------------------

def test_the_tokeniser_has_always_split_on_underscores():
    """INVERTED SENTINEL — its passing is the finding.

    The reported diagnosis was that the shape rules "tokenise on whitespace"
    and so never see underscore-joined text. They do see it. Pinned so nobody
    re-fixes a defect that was never there, and so the real mechanism below
    keeps its explanation.
    """
    toks = _tokens("TAK-441_prolonged_the_QT_interval_as_a_potential_indicator")
    assert toks == ["tak-441", "prolonged", "the", "qt", "interval", "as",
                    "a", "potential", "indicator"]
    assert _tokens("a_b c_d") == ["a", "b", "c", "d"]


def test_multi_preposition_is_why_they_passed_not_the_tokeniser():
    """The measured reason: exactly ONE preposition, under the >=2 threshold.

    If this ever reads two, `multi_preposition` would have caught the corpus
    and the cap below would be redundant — which is worth knowing rather than
    assuming.
    """
    from backend.kb.gate import PREPOSITIONS
    toks = _tokens(normalise_term(CLAUSES[0]))
    preps = [t for t in toks if t in PREPOSITIONS]
    assert preps == ["of"], f"expected exactly one preposition, got {preps}"
    assert len(toks) >= MAX_TERM_TOKENS, "non-vacuity: this must be a long term"


# ---------------------------------------------------------------------------
# The clause rules, both directions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("clause,expected", CLAUSE_CASES,
                         ids=lambda v: v[:34] if isinstance(v, str) else v)
def test_every_clause_the_session_stored_is_now_refused(clause, expected):
    """Attributed, not merely refused. Asserting only "rejected" would let a
    new rule take credit for something an old rule was already catching —
    and would hide a rule that had stopped doing anything."""
    reason = inspect_term(normalise_term(clause), evidence_class="extraction")
    assert reason is not None, (
        f"still accepted: {clause!r} — the corpus this packet exists for")
    assert reason.startswith(expected), (
        f"caught by {reason!r}, expected {expected!r} — the rules are not "
        f"doing what the measurement said they do")


def test_the_new_rules_are_load_bearing_for_six_of_the_seven():
    """NON-VACUITY, the direction that matters: neutralise the two new rules
    and the corpus goes back to being accepted.

    Without this, every assertion above would pass on a gate where the new
    rules did nothing and some older rule happened to catch the cases.
    """
    import backend.kb.gate as g
    saved = (g.MAX_TERM_TOKENS, g.SUBORDINATORS)
    try:
        g.MAX_TERM_TOKENS, g.SUBORDINATORS = 10 ** 9, frozenset()
        before = {c: g.inspect_term(g.normalise_term(c),
                                    evidence_class="extraction")
                  for c in CLAUSES}
    finally:
        g.MAX_TERM_TOKENS, g.SUBORDINATORS = saved

    accepted_before = [c for c, r in before.items() if r is None]
    assert len(accepted_before) == 6, (
        f"expected 6 of 7 to have been accepted by the pre-O.3 gate, "
        f"got {len(accepted_before)}")
    for c in accepted_before:
        assert inspect_term(normalise_term(c),
                            evidence_class="extraction") is not None


@pytest.mark.parametrize("term", LEGITIMATE, ids=lambda t: t[:34])
def test_no_legitimate_concept_is_refused(term):
    """THE MEASUREMENT THE PACKET ASKED FOR: 0/23 false positives.

    K.0.9 is the warning this exists to heed — rules calibrated on one corpus
    met another and refused 40 of 66 legitimate concepts, `WHO` and `1984`
    among them. The same risk runs in reverse here.
    """
    assert inspect_term(normalise_term(term),
                        evidence_class="extraction") is None, (
        f"legitimate concept refused: {term!r}")


def test_the_boundary_is_where_the_measurement_put_it():
    """The tightest case, named. The longest legitimate concept in the corpus
    is 7 tokens and the shortest clause is also 7 — they OVERLAP, so the cap
    alone cannot separate them and the subordinator rule carries the last one.

    One more legitimate 8-token concept and the cap is wrong. That is not a
    hypothetical: `verify_packet_k.py` §4 prints the store's 7+ word terms and
    is the population that should confirm this threshold.
    """
    longest_ok = max(LEGITIMATE, key=lambda t: len(_tokens(normalise_term(t))))
    assert len(_tokens(normalise_term(longest_ok))) == MAX_TERM_TOKENS - 1
    assert longest_ok == "corrected QT adjusted for resting heart rate"

    shortest_clause = min(CLAUSES, key=lambda t: len(_tokens(normalise_term(t))))
    assert len(_tokens(normalise_term(shortest_clause))) == MAX_TERM_TOKENS - 1, (
        "the corpus no longer overlaps at the boundary, so the subordinator "
        "rule may have become redundant — check before removing it")
    assert any(t in SUBORDINATORS for t in _tokens(normalise_term(shortest_clause)))


def test_the_new_rules_are_strict_class_only():
    """A lenient class is a person's or an institution's own words and may
    legitimately be a long sentence. O.2b adds exactly such a class, so this
    is the guard that keeps the cap off it."""
    long_clause = CLAUSES[0]
    assert inspect_term(normalise_term(long_clause),
                        evidence_class="extraction") is not None
    for lenient in ("external", "fixture"):
        assert inspect_term(normalise_term(long_clause),
                            evidence_class=lenient) is None, (
            f"the clause cap fired on {lenient}, which the gate treats as "
            f"someone else's words")


# ---------------------------------------------------------------------------
# structured_literal — established, not assumed
# ---------------------------------------------------------------------------

def test_the_structured_literal_rule_was_never_broken():
    """The report asked why this did not fire. Measured: it does.

    So the stored row cannot have passed through `add()` with the gate
    enabled, in the form reported. What would settle it is the operator's
    store: whether NEXUS_INGEST_GATE was set, or the row predates Packet K.
    """
    for surface in ('"[\'graph-based systems\']"', "['graph-based systems']",
                    "{'a': 1}", "['a']"):
        assert inspect_term(surface, evidence_class="extraction") == \
            "structured_literal", f"{surface!r} slipped through"
        # universal, not strict-only — a stringified list is not a belief from
        # ANY source
        assert inspect_term(surface, evidence_class="external") == \
            "structured_literal"


def test_a_stringified_list_is_refused_as_a_relation_too():
    assert inspect_relation("['a','b']", evidence_class="external") == \
        "structured_literal"


def test_python_structure_siblings_are_caught_but_only_under_strict():
    """O.1's lesson applied: the named instance is not the whole class.

    Swept the neighbouring surfaces. Every one is refused under a strict
    class, but by OTHER rules — `('a','b')` by `parenthetical`, `<a,b>` by
    `markup`, `None` by `null_literal`. Under a LENIENT class only
    `_SQUARE_CURLY`'s square/curly forms are refused.

    Recorded rather than widened: O.2b introduces a lenient class, and this is
    the honest statement of what the gate will and will not do for it.
    """
    for surface in ("('a', 'b')", "<a, b>", "set()"):
        assert inspect_term(surface, evidence_class="extraction") is not None
    # ...and the lenient gap, stated as a fact rather than fixed by guesswork
    assert inspect_term("('a', 'b')", evidence_class="external") is None
    assert inspect_term("a=1, b=2", evidence_class="external") is None


# ---------------------------------------------------------------------------
# The truncated relation — the gate did it
# ---------------------------------------------------------------------------

LONG_RELATION = ("highlight_the_importance_of_monitoring_levels_and_"
                 "adjusting_dose_for_CYP3A4_inhibitors")


def test_normalise_relation_no_longer_truncates():
    """REGRESSION. This slice produced the operator's truncated relation
    character for character, trailing underscore and all."""
    out = normalise_relation(LONG_RELATION)
    assert len(out) == len(LONG_RELATION), (
        f"the relation was silently shortened to {len(out)} chars: {out!r}")
    assert not out.endswith("_"), "a trailing underscore is a truncation scar"
    assert out != "highlight_the_importance_of_monitoring_levels_and_adjusting_", (
        "this is the exact string the operator found in their store")


def test_an_over_long_relation_is_refused_not_silently_truncated():
    """`relation_too_long` was UNREACHABLE through add(): normalisation
    truncated to exactly MAX_RELATION_CHARS before the length check ran, so a
    200-character relation returned ACCEPTED and was stored at 60.

    That is the C10 / H.4 defect class — a rule naming a state the code cannot
    reach — and it is also a SANITISE, which K.5 ruled against explicitly:
    rejecting is faithful and loud, sanitising is a silent provenance loss.
    """
    assert len(LONG_RELATION) > MAX_RELATION_CHARS, "non-vacuity"
    assert inspect_relation(normalise_relation(LONG_RELATION),
                            evidence_class="extraction") == "relation_too_long"


def test_the_over_long_relation_is_refused_at_the_real_boundary(tmp_path):
    """Through `add()`, not through the rule in isolation — the ordering IS
    the defect, so testing the rule alone would have passed all along."""
    store = TripleStore(tmp_path / "kb.db")
    with pytest.raises(BeliefRejected) as e:
        store.add(subject="Clinical Implications", relation=LONG_RELATION,
                  object="CYP3A4 inhibitors", weight=0.6, tier=2,
                  granularity=Granularity.TOPIC, source="extraction")
    assert "relation_too_long" in str(e.value)
    assert store.count() == 0, "a refused belief was stored anyway"


def test_a_relation_at_the_limit_still_passes(tmp_path):
    """Non-vacuity for the above: the boundary must admit as well as refuse,
    or the fix is just a stricter cap wearing a bug report's clothes."""
    ok = "a" * MAX_RELATION_CHARS
    assert inspect_relation(ok, evidence_class="extraction") is None
    store = TripleStore(tmp_path / "kb.db")
    bid = store.add(subject="alpha", relation=ok, object="beta", weight=0.6,
                    tier=2, granularity=Granularity.TOPIC, source="extraction")
    assert store.get_by_id(bid).relation == ok, "stored relation was altered"


# ---------------------------------------------------------------------------
# I-9 — a boundary fix touches nothing already stored
# ---------------------------------------------------------------------------

def test_rows_already_stored_are_untouched_by_the_new_rules(tmp_path):
    """The clauses are refused at the DOOR. Rows written before O.3 keep
    every field, stay retrievable, and are not re-judged."""
    import os
    os.environ["NEXUS_INGEST_GATE"] = "off"
    try:
        store = TripleStore(tmp_path / "kb.db")
        bid = store.add(subject="Study Aim", relation="aimed_to_determine_if",
                        object=CLAUSES[0], weight=0.6, tier=2,
                        granularity=Granularity.TOPIC, source="extraction")
    finally:
        os.environ.pop("NEXUS_INGEST_GATE", None)

    reopened = TripleStore(tmp_path / "kb.db")
    t = reopened.get_by_id(bid)
    assert t is not None and t.object == CLAUSES[0], "a stored row was altered"
    assert any(x.id == bid for x in reopened.search_terms(["study"], limit=10)), \
        "a pre-O.3 row stopped being retrievable"

    with pytest.raises(BeliefRejected):
        reopened.add(subject="Study Aim 2", relation="aimed_to_determine_if",
                     object=CLAUSES[0], weight=0.6, tier=2,
                     granularity=Granularity.TOPIC, source="extraction")


# ---------------------------------------------------------------------------
# Packet P.1 — the cap, and the metric that measures it
# ---------------------------------------------------------------------------

def test_the_cap_is_at_the_threshold_the_measurement_chose():
    """P.1 asked for the cap O.3 measured. It shipped in O.3; this pins the
    number so P.1 is a check rather than a re-ship.

    8, not 7. The store's own evidence is what makes that the right call:
    `corrected QT adjusted for resting heart rate` is SEVEN tokens, is
    legitimate, and is the object of `QTc | stands_for | ...` — a row the O.2
    findings list as stored. A cap at 7 would refuse it silently.
    """
    assert MAX_TERM_TOKENS == 8
    legit_7 = "corrected QT adjusted for resting heart rate"
    assert len(_tokens(normalise_term(legit_7))) == 7
    assert inspect_term(normalise_term(legit_7),
                        evidence_class="extraction") is None, (
        "a legitimate 7-token concept from the operator's own store is "
        "refused — the cap is one token too tight")


def test_the_length_metric_uses_the_same_tokeniser_as_the_rule():
    """P.1 ADJACENT FINDING, and it changes what the evidence says.

    `verify_packet_k.py` §4 counted words with `len(t.split())`, which does
    not split underscores — while extraction's prompt asks for snake_case.
    Measured:

        TAK-441_prolonged_the_QT_interval_as_a_potential_indicator_of_...
            split()    -> 1 word
            _tokens()  -> 11 words

    So every snake_cased clause landed in the ONE-WORD bucket, the quoted
    distribution's `1w:166` contained clauses, and "43 terms of 7+ words" was
    an undercount. The claim it was used to support — "no legitimate concept
    above 6 words exists" — rested on a metric that could not see the
    population it was describing.

    A measurement and the rule it justifies must count the same way, or the
    evidence is about a different quantity than the decision.
    """
    # AST, not text. The first draft searched the function's SOURCE for
    # "t.split()" and fired on the print statement that documents the defect —
    # the third time this class has bitten in three packets (M.5's regex hit a
    # docstring, O.3's guard hit a comment, this one hit a string literal).
    # An AST sees calls; strings are Constants and are invisible to it.
    import ast
    tree = ast.parse((ROOT_SCRIPTS / "verify_packet_k.py").read_text(
        encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "length_evidence"),
              None)
    assert fn is not None, "length_evidence() is gone from verify_packet_k.py"

    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    methods = {n.func.attr for n in ast.walk(fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "_tokens" in called, (
        "§4 no longer counts with the gate's tokeniser, so its distribution "
        "and the cap that reads it have drifted apart again")
    assert "split" not in methods, (
        "§4 calls .split() again — snake_cased clauses will collapse into the "
        "one-word bucket and the distribution will understate the population")

    # ...and the difference is real, not theoretical
    clause = "TAK-441_prolonged_the_QT_interval_as_a_potential_indicator"
    assert len(clause.split()) == 1
    assert len(_tokens(normalise_term(clause))) == 9


def test_the_cap_cannot_fire_below_its_threshold():
    """The false-positive answer for the store's 5- and 6-word population,
    which P.1 asked for: the cap cannot reach them by construction.

    That is worth asserting rather than assuming, because a rule stated as
    ">= N" and implemented as "> N" would silently take a whole bucket.
    """
    for term in ("adverse event of special interest",            # 5
                 "area under the concentration time curve",      # 6
                 "corrected QT adjusted for resting heart rate"):  # 7
        toks = len(_tokens(normalise_term(term)))
        assert toks < MAX_TERM_TOKENS
        reason = inspect_term(normalise_term(term), evidence_class="extraction")
        assert reason != "too_many_tokens", (
            f"the cap fired on a {toks}-token term: {term!r}")


# ---------------------------------------------------------------------------
# Packet P.2 — three rules, three different judgements
# ---------------------------------------------------------------------------
# §3b measured 34 rejected rows cited by SEALED lineage records. Per rule:
#   null_literal        DECOMPOSE, else reject   (8 of 11 sealed-cited)
#   structured_literal  DECOMPOSE                (7 of 13)
#   multi_preposition   SUBJECT ONLY             (7 of 15)

from backend.kb.decompose import decompose, decompose_list_object  # noqa: E402


def _verdict(s, r, o):
    from backend.kb.gate import inspect_triple
    return inspect_triple(normalise_term(s), normalise_relation(r),
                          normalise_term(o), evidence_class="extraction")


@pytest.mark.parametrize("subject,relation,expected_object", [
    ("BBC", "is_a_reliable_news_source", "reliable news source"),
    ("Sprinting", "is_an_example_of_interval_training_activity",
     "interval training activity"),
])
def test_a_null_object_row_is_decomposed_into_a_findable_one(
        subject, relation, expected_object):
    """The measured reason this is a shape problem and not a content problem:
    these rows are retrievable ONLY BY SUBJECT, because the whole predicate
    sits in the relation and `search_terms` never searches relations. Moving
    the tail into the object is what makes the belief findable at all."""
    assert _verdict(subject, relation, "None") == "object:null_literal"
    parts = decompose(subject, relation, "None")
    assert len(parts) == 1
    assert parts[0].object == expected_object
    assert parts[0].relation == "is_a"
    assert _verdict(parts[0].subject, parts[0].relation, parts[0].object) is None


def test_a_null_object_row_with_no_recognisable_head_is_not_rescued():
    """HONEST LIMIT, and it is the third of the packet's three examples.

    `carries_oxygen_from_your_lungs_to_all_parts_of_your_body` decomposes,
    but its tail is nine tokens carrying `your`, so the parts are refused too.
    Pretending to save it would only move where it fails.
    """
    parts = decompose("Hemoglobin",
                      "carries_oxygen_from_your_lungs_to_all_parts_of_your_body",
                      "None")
    assert len(parts) == 1
    assert _verdict(parts[0].subject, parts[0].relation,
                    parts[0].object) is not None


def test_a_list_object_becomes_one_row_per_member():
    """A list object is n facts wearing one row."""
    s, r = "Interval_training", "includes_examples_of_activities"
    o = "['sprinting', 'cycling_at_a_slower_pace']"
    assert _verdict(s, r, o) == "object:structured_literal"
    parts = decompose(s, r, o)
    assert [p.object for p in parts] == ["sprinting", "cycling at a slower pace"]
    for p in parts:
        assert _verdict(p.subject, p.relation, p.object) is None


def test_list_decomposition_never_evaluates_the_text():
    """`ast.literal_eval`, never `eval`. The value is model-derived text and
    this path must not become a way to execute it."""
    assert decompose_list_object("x", "y", "[__import__('os').system('true')]") == []
    assert decompose_list_object("x", "y", "not a list") == []
    assert decompose_list_object("x", "y", "[]") == []


def test_multi_preposition_no_longer_refuses_a_descriptive_object():
    """P.2 OVERTURNED THE INSTINCT — including mine — that this was a close
    judgement about which objects to keep.

    Measured: since P.1's word cap, almost everything this rule uniquely
    caught is refused by `too_many_tokens` regardless. The band it uniquely
    governs is objects of <=7 tokens with two prepositions, and there the
    population is legitimate descriptive phrases.
    """
    assert _verdict("BBC", "is_an_example_of",
                    "reputable_outlets for real-time updates on Iran") is None


def test_multi_preposition_still_refuses_a_clause_as_a_SUBJECT():
    """NON-VACUITY. The rule is narrowed, not removed: a subject is the thing
    being described and must still be a concept."""
    from backend.kb.gate import inspect_term
    clause = "issue of finding the latest news about Iran"
    assert inspect_term(normalise_term(clause), evidence_class="extraction",
                        position="subject") == "multi_preposition"
    assert inspect_term(normalise_term(clause), evidence_class="extraction",
                        position="object") != "multi_preposition"


def test_the_long_objects_are_still_refused_by_the_cap():
    """The other half of the narrowing's safety: relaxing this rule for
    objects gives nothing back to the clauses, because the cap has them."""
    for obj in ("alternating periods of intense physical activity with recovery phases",
                "the process of converting light into chemical energy",
                "issue of finding the latest news about Iran online"):
        # subject must be a real term — "x" trips too_short first
        assert _verdict("training", "involves", obj) == "object:too_many_tokens"


@pytest.mark.parametrize("s,r,o", [
    ("error", "is", "done"),
    ("My understanding", "derived_from", "conversation_history"),
    ("To address", "addressing", "issue of finding the latest news about Iran"),
])
def test_the_correctly_refused_rows_are_still_refused(s, r, o):
    """The packet's own control set. Every one falls on a SUBJECT rule, which
    P.2 did not touch — and decomposition offers nothing, so none of them
    slips through the new path."""
    assert _verdict(s, r, o) is not None
    assert decompose(s, r, o) == []


def test_decomposition_never_suppresses_a_row():
    """An empty result means "store the original and let the gate judge it".
    Decomposition only ever OFFERS alternatives; it can never be the reason a
    belief goes missing."""
    assert decompose("photosynthesis", "produces", "glucose") == []
    assert _verdict("photosynthesis", "produces", "glucose") is None


# ---------------------------------------------------------------------------
# Packet P.3 — tier on arrival. PINS CURRENT BEHAVIOUR. Changes nothing.
# ---------------------------------------------------------------------------

def test_the_written_arrival_tier_is_not_the_tier_a_row_ends_up_in(tmp_path):
    """P.3, report-only: `agentic_loop.py:542` writes tier=2 and the operator
    observed tier 0. Both are true. `rebalance()` is an idempotent PROJECTION
    of prominence (F.3), so a row arriving at weight 1.0 is promoted to tier 0
    on the next KAIROS cycle — TIER_ENTRY[0] is 0.75.

    Pinned so that a later change to arrival tier, TIER_ENTRY, or the
    projection is a DELIBERATE act rather than something that quietly moves
    what retrieval ranks first. Nothing here recommends; the recommendation is
    in the report and the decision is the operator's.
    """
    from backend.kb.tiered_store import TIER_ENTRY, band_for

    store = TripleStore(tmp_path / "kb.db")
    high = store.add(subject="high confidence topic", relation="relates_to",
                     object="cardiac safety review", weight=1.0, tier=2,
                     granularity=Granularity.TOPIC, source="extraction")
    external = store.add(subject="external topic", relation="relates_to",
                         object="cardiac safety review", weight=0.7, tier=2,
                         granularity=Granularity.TOPIC, source="wikidata")

    assert store.get_by_id(high).tier == 2, "non-vacuity: written at tier 2"
    store.rebalance()

    # UPDATED BY Q.2, WITH THE ARGUMENT — not weakened.
    #
    # P.3 pinned the old outcome deliberately so that changing it would be a
    # deliberate act. Q.2 is that act. The MECHANISM P.3 established is
    # unchanged and still asserted: `band_for` is a pure projection and
    # TIER_ENTRY[0] is still 0.75, so raw weight 1.0 still bands to 0. What
    # changed is the INPUT — the band now projects effective prominence
    # (current_weight x the class arrival discount), so an extraction row at
    # 1.0 presents 0.65 and lands at tier 1 instead.
    assert band_for(1.0, 2) == 0 and TIER_ENTRY[0] == 0.75, (
        "the projection itself changed; Q.2 changed only what is fed to it")

    assert store.get_by_id(high).tier == 1, (
        "a weight-1.0 extraction row no longer lands where Q.2 put it")
    assert store.get_by_id(external).tier == 1

    # The consequence retrieval sees — the actual finding, and now inverted.
    from backend.kb.retriever import TripleRetriever
    order = [h.triple.source for h in
             TripleRetriever(store).retrieve("cardiac safety review", top_k=10)]
    assert order[0] == "wikidata", (
        f"the model's self-report outranks the external source again: {order}")


# ---------------------------------------------------------------------------
# Packet P.4 — the calibration channel
# ---------------------------------------------------------------------------

def test_no_path_emits_the_reserved_prediction_mechanism():
    """`prediction` was RESERVED by P.4 for a real predictor, and AF.1 built
    it: the citation forecast. The reservation is now an EXCLUSIVE GRANT —
    exactly one module may write the mechanism, so the channel keeps holding
    rows from one computation and stays calibratable against its own
    history. A second writer appearing anywhere else is the P.4 defect
    returning with a different byline.

    AST, not grep — the third recurrence of guards firing on their own prose
    taught that. A string constant is invisible to a parser looking at calls.
    """
    import ast
    from backend.eval.calibration_log import MECHANISMS, RESERVED_MECHANISMS

    assert RESERVED_MECHANISMS <= MECHANISMS
    root = Path(__file__).resolve().parent.parent
    allowed = {"backend/cortex/forecast.py"}
    offenders, exercised = [], []
    for path in tracked_backend():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in ("log_confidence", "_log_confidence", "log_many"):
                continue
            if node.args and isinstance(node.args[0], ast.Constant) \
                    and node.args[0].value in RESERVED_MECHANISMS:
                (exercised if rel in allowed else offenders).append(
                    f"{rel}:{node.lineno}")
    assert not offenders, (
        f"the reserved mechanism is written outside its one permitted path "
        f"{sorted(allowed)}: {offenders}")
    # The grant must be USED, or this test has quietly become the old
    # nothing-writes-it rule with a stale allow-list nobody notices.
    assert exercised, (
        f"nothing in {sorted(allowed)} writes the reserved mechanism — "
        f"either the forecast stopped landing there (a defect) or the "
        f"allowance is stale (remove it)")


def test_the_reserved_check_can_actually_fail():
    """Non-vacuity: every module is clean, so the guard above passes whether
    or not it works."""
    import ast
    from backend.eval.calibration_log import RESERVED_MECHANISMS
    tree = ast.parse('_log_confidence("prediction", 0.5)\n')
    hits = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and n.args
            and isinstance(n.args[0], ast.Constant)
            and n.args[0].value in RESERVED_MECHANISMS]
    assert len(hits) == 1, "the detector does not see the exact P.4 violation"


def test_router_is_gone_because_nothing_ever_emitted_it():
    """A member no code can reach reads as satisfied on inspection — the
    C10 / H.4 defect. `router` was in MECHANISMS and was never written."""
    from backend.eval.calibration_log import MECHANISMS
    assert "router" not in MECHANISMS
    assert MECHANISMS == {"observation", "propagation", "prediction",
                          "triple", "resolution"}


def test_observation_and_propagation_are_distinguished_by_hop():
    """The split that makes the channel usable. Before P.4 all 297 rows said
    `retrieval` at confidence 1.0 — the given and the estimated recorded
    identically, so nothing could be calibrated."""
    import inspect as _inspect
    from backend.loop import agentic_loop
    src = _inspect.getsource(agentic_loop._accrue_calibration)
    assert '"observation" if not hop else "propagation"' in src
    for field in ("hop", "rank", "saturated", "model_id", "lineage_id"):
        assert f'"{field}"' in src, f"{field} is not recorded"


def test_the_accrual_runs_after_the_lineage_record_exists():
    """`lineage_id` is the join key between the calibration channel and the
    evidence channel and cannot be backfilled — so the accrual must fire
    after the lineage record, not before it."""
    # AST, and the first draft got this wrong in an instructive way: a text
    # search for "_accrue_calibration(hits" found the DEFINITION, which sits
    # above run_turn, and reported the call as too early. Line numbers from a
    # parser cannot make that mistake.
    import ast
    path = (Path(__file__).resolve().parent.parent
            / "backend" / "loop" / "agentic_loop.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # MCP-2: the stages after the model call live in `complete_turn` (the
    # console's `run_turn` composes it), so that is the body read.
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "complete_turn")
    call_lines = [n.lineno for n in ast.walk(fn)
                  if isinstance(n, ast.Call)
                  and getattr(n.func, "id", None) == "_accrue_calibration"]
    assign_lines = [n.lineno for n in ast.walk(fn)
                    if isinstance(n, ast.Assign)
                    and any(getattr(t, "id", None) == "lineage_id"
                            for t in n.targets)]
    assert call_lines, "the calibration accrual is not called from complete_turn"
    assert assign_lines, "lineage_id is not assigned in complete_turn"
    assert min(call_lines) > max(assign_lines), (
        "the accrual fires before lineage_id exists, so every row would carry "
        "null and the two channels could never be joined")


def test_predict_is_quarantined_not_deleted():
    """The seam is the point. Deleting would lose the record of a decision
    not taken — E.4's declined write and Packet B's purge are the precedents.
    """
    import inspect as _inspect
    from backend.cortex.predictive import PredictiveCortex
    assert callable(PredictiveCortex.predict)
    src = _inspect.getsource(PredictiveCortex.predict)
    assert "QUARANTINED" in src and "RESERVED" in src
