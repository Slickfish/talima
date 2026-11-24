"""Packet O.2b — a fact the user states becomes a belief they can retrieve.

THE FAILURE, IN THE OPERATOR'S OWN HOUR
=======================================
They wrote, in their own message:

    "At the 600mg supratherapeutic dose, mean placebo-corrected QTc change
     was 11.4ms, exceeding the threshold."

The store ended that turn containing five textbook rows about what a QT
interval is, and NOT ONE was the fact stated. `agentic_loop.py` extracts from
the ASSISTANT'S OWN ANSWER, never from the user's message, so extraction
faithfully harvested the essay explaining the question and never touched the
claim.

Later, asked "what do we know about cardiac safety?", the answer omitted the
threshold-exceeding finding — because it was never a belief, so it could not
be retrieved, so it could not be weighed. A confidently favourable conclusion
with the disqualifying evidence absent, inside the system built to prevent
exactly that.

TWO FAILURES, NOT ONE (measured in O.2a)
========================================
Making the assertion a belief is necessary and NOT sufficient. `search_terms`
matches `LOWER(subject) LIKE ? OR LOWER(object) LIKE ?` — the RELATION column
is never searched. Measured, with the belief stored:

    QTc in subject  -> retrieved
    QTc in relation -> NOT retrieved
    QTc in object   -> retrieved

So a triple shaped the wrong way is stored and still invisible. That is why
O.2b decides the triple shape rather than accepting the user's free text, and
why `test_the_shape_keeps_the_salient_terms_where_retrieval_looks` exists.

DESIGN, AS CLEARED
==================
  recognition  detect candidates, USER CONFIRMS. Law 3, the pattern
               /kb/dedup/confirm and /search/confirm already established.
  class        new `asserted`, FIRST in the ordering, rate below `external`.
  shape        O.2b decides the triple. The gate will not guard a lenient
               class — O.2a measured that it accepts a whole sentence there.
  provenance   no actor column. Recorded as an open question, not invented.

WHY CONFIRMED RATHER THAN INFERRED, in one line: a false negative costs a
restatement, a false positive costs a permanent belief in a substrate whose
founding law is that beliefs are never deleted (Law 4, I-9). O.2a measured
0/20 false positives on a corpus I wrote myself, and said plainly that this
means "the corpus I imagined contains no counterexample".
"""
from __future__ import annotations

import pytest

from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore

# Verbatim. The acceptance test is the operator's two turns, not a proxy.
ASSERTION = ("At the 600mg supratherapeutic dose, mean placebo-corrected QTc "
             "change was 11.4ms, exceeding the threshold.")
LATER_QUESTION = "what do we know about cardiac safety?"


# ---------------------------------------------------------------------------
# THE ACCEPTANCE PROPERTY — the whole packet in one test
# ---------------------------------------------------------------------------

# The operator's five phrasings, verbatim, from the O.2a measurement. Four
# retrieved before Packet W; the fifth did not, and it is the one a person
# would most naturally ask.
PHRASINGS = [
    "what was the QTc change at 600mg?",
    "tell me about the QTc change",
    "supratherapeutic dose findings",
    "what do we know about QT?",
    "what do we know about cardiac safety?",
]


def test_a_fact_the_user_states_is_retrievable_in_a_later_turn(
        tmp_path, monkeypatch):
    """THE ACCEPTANCE TEST — the operator's two turns, verbatim.

    Turn 1: the operator states the fact and confirms it.
    Turn 2: the operator asks the later question. All five phrasings, and the
    fifth is required to pass.

    WAS `xfail(strict)` FROM PACKET O UNTIL PACKET W.1. What failed was never
    the ingest path — O.2b's half worked, and
    `test_the_ingest_path_works_end_to_end` proved it. What failed was
    matching "cardiac safety" to "QTc change": `search_terms` is substring
    LIKE over subject and object, "cardiac" appears nowhere in the store, and
    retrieval had no other way to relate them. It was kept verbatim and kept
    failing rather than softened to a phrasing that shared a word, because
    that version would have passed and reported the problem solved.

    W.1 gives retrieval a second RECALL channel — cosine over the triple
    surface, unioned into the candidate pool, with the ordering left exactly
    as provenance had it. The claim now enters the pool for the fifth
    phrasing, and `effective_prominence` still decides where it lands.

    IT RUNS AT THE SHIPPED THRESHOLD AND ON A REALISTIC CORPUS (W.2). Both
    were defects in the W.1 version and both were found by the operator's
    harness run rather than by this file:

      - it used to set `NEXUS_SEMANTIC_MIN_SIMILARITY=0.05` while the shipped
        default was 0.55, so it could not have noticed a threshold wrong by a
        factor of five. It now sets nothing, and the embedder below is
        calibrated to the operator's own measured geometry (signal 0.4047 /
        control 0.2570 with nomic-embed-text), so the number that ships is the
        number under test.
      - it used to run on four rows with a five-slot budget, so every row was
        admitted and nothing had to win. It now runs against 320 rows of the
        competition a real store carries — 200 of them `co_activated`
        boilerplate built from this question's own vocabulary, which measured
        109 rows ranking ABOVE the claim before W.2 excluded them.

    WHAT THIS TEST DOES AND DOES NOT PROVE. The embedder is
    `tests/_toy_embed.py`, a bag-of-words vector with a hand-written synonym
    table — it knows `cardiac` and `qtc` name one concept because that file
    says so. So this proves the MECHANISM: a relationship the lexical channel
    cannot see does reach the answer, at the shipped threshold, against real
    competition. It does not prove that a real model sees that relationship.
    `scripts/measure_retrieval.py` is what proves the number, on the real store
    with the real model, and the split is deliberate — the alternative was a
    live daemon in the suite (reacquiring the 143 outbound calls R.4 removed)
    or a weakened question.
    """
    from backend.kb.assertion import assertion_triples, HUMAN_SOURCE
    from backend.kb.semantic_index import SemanticIndex, surface_of
    from tests._realistic_corpus import add_competition
    from tests._toy_embed import toy_embed

    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")

    store = TripleStore(tmp_path / "kb.db")
    # background the session already held, so retrieval has competition
    store.add(subject="QT interval", relation="is_a_segment_of", object="ECG",
              weight=0.7, tier=2, granularity=Granularity.TOPIC,
              source="extraction")
    store.add(subject="QTc", relation="stands_for",
              object="corrected QT adjusted for resting heart rate",
              weight=0.7, tier=2, granularity=Granularity.TOPIC,
              source="extraction")
    added = add_competition(store)
    assert added["co_activated"] >= 200

    # --- turn 1: the user asserts, and confirms (Law 3) ---
    triples = assertion_triples(ASSERTION)
    assert triples, "no triple was derived from the operator's sentence"
    for t in triples:
        store.add(subject=t.subject, relation=t.relation, object=t.object,
                  weight=t.weight, tier=t.tier,
                  granularity=Granularity.TOPIC, source=HUMAN_SOURCE)

    # The index is built from the store itself — every live belief, including
    # the textbook background, so the asserted fact has competition.
    index = SemanticIndex(store)
    for t in store.all():
        index.upsert(t.id, surface_of(t), toy_embed(surface_of(t)), model="toy")

    # --- turn 2: the later question, in each of the five phrasings ---
    retriever = TripleRetriever(store, semantic=index)
    failures = []
    for question in PHRASINGS:
        hits = retriever.retrieve(question, top_k=20,
                                  query_embedding=toy_embed(question))
        surfaces = [f"{h.triple.subject} {h.triple.relation} {h.triple.object}"
                    for h in hits]
        asserted = [h for h in hits if h.triple.source == HUMAN_SOURCE]
        if not asserted:
            failures.append(f"{question!r} -> " + " | ".join(surfaces))
            continue
        joined = " ".join(surfaces)
        if not ("11.4" in joined and "600" in joined):
            failures.append(
                f"{question!r} -> retrieved but without the numbers that make "
                f"it a finding: {surfaces}")
    assert not failures, (
        "the operator's own stated fact was not retrieved for their own later "
        "question:\n  " + "\n  ".join(failures))
    assert LATER_QUESTION in PHRASINGS       # the one that was failing


def test_without_the_assertion_path_the_question_finds_only_textbook(tmp_path):
    """NON-VACUITY for the acceptance test, and the operator's hour reproduced.

    The same two turns with extraction alone: the answer's own harvest, and
    nothing the user said. If this ever starts finding the fact, the
    acceptance test above is passing for a reason unrelated to this packet.
    """
    store = TripleStore(tmp_path / "kb.db")
    for s, r, o in [
        ("QT interval", "is_a_segment_of", "ECG"),
        ("QRS complex", "represents", "ventricular depolarization"),
        ("QTc", "stands_for", "corrected QT adjusted for resting heart rate"),
        ("higher heart rate", "makes", "the QT interval appear shorter"),
    ]:
        store.add(subject=s, relation=r, object=o, weight=0.7, tier=2,
                  granularity=Granularity.TOPIC, source="extraction")

    hits = TripleRetriever(store).retrieve(LATER_QUESTION, top_k=20)
    joined = " ".join(f"{h.triple.subject} {h.triple.object}" for h in hits)
    assert "11.4" not in joined, (
        "the fact is present without the assertion path — the fixture no "
        "longer reproduces the operator's failure")


def test_the_shape_keeps_the_salient_terms_where_retrieval_looks(tmp_path):
    """O.2a's second failure, guarded.

    `search_terms` never searches the relation column. A triple whose only
    distinguishing term sits in the relation is stored and unfindable, so the
    shaping function must not put one there.
    """
    from backend.kb.assertion import assertion_triples

    for t in assertion_triples(ASSERTION):
        searchable = f"{t.subject} {t.object}".lower()
        assert "qtc" in searchable or "qt" in searchable, (
            f"no QT/QTc term in subject or object — retrieval cannot reach "
            f"this triple: {t.subject} | {t.relation} | {t.object}")


def test_the_ingest_path_works_end_to_end(tmp_path):
    """O.2b'S OWN HALF, isolated from F-04/F-05.

    The same store and the same assertion, asked with a question that shares
    a term. If this fails, the ingest path is broken. If only the verbatim
    test above fails, retrieval is the remaining gap — and that distinction
    is the whole reason both tests exist.
    """
    from backend.kb.assertion import assertion_triples, HUMAN_SOURCE

    store = TripleStore(tmp_path / "kb.db")
    store.add(subject="QT interval", relation="is_a_segment_of", object="ECG",
              weight=0.7, tier=2, granularity=Granularity.TOPIC,
              source="extraction")
    for t in assertion_triples(ASSERTION):
        store.add(subject=t.subject, relation=t.relation, object=t.object,
                  weight=t.weight, tier=t.tier,
                  granularity=Granularity.TOPIC, source=HUMAN_SOURCE)

    # Four questions a person might actually ask about their own statement.
    for question in ("what was the QTc change at 600mg?",
                     "tell me about the QTc change",
                     "supratherapeutic dose findings",
                     "what do we know about QT?"):
        hits = TripleRetriever(store).retrieve(question, top_k=20)
        asserted = [h for h in hits if h.triple.source == HUMAN_SOURCE]
        assert asserted, f"the assertion was not retrieved for {question!r}"
        joined = f"{asserted[0].triple.subject} {asserted[0].triple.object}"
        assert "11.4" in joined and "600" in joined, (
            f"retrieved, but without the numbers that make it a finding: "
            f"{joined!r}")


def test_the_assertion_outranks_the_model_self_report(tmp_path):
    """The ordering that made the operator's answer wrong.

    Extraction rows arrive at tier 2; an assertion arrives at tier 1, and
    retrieval sorts tier ASC. So when both match, what the person said is
    weighed before what the model said about it — which is the inversion the
    hour exposed.
    """
    from backend.kb.assertion import assertion_triples, HUMAN_SOURCE

    store = TripleStore(tmp_path / "kb.db")
    store.add(subject="QTc change", relation="is", object="normal",
              weight=1.0, tier=2, granularity=Granularity.TOPIC,
              source="extraction")
    for t in assertion_triples(ASSERTION):
        store.add(subject=t.subject, relation=t.relation, object=t.object,
                  weight=t.weight, tier=t.tier,
                  granularity=Granularity.TOPIC, source=HUMAN_SOURCE)

    hits = TripleRetriever(store).retrieve("QTc change", top_k=20)
    assert hits, "non-vacuity: neither belief was retrieved"
    assert hits[0].triple.source == HUMAN_SOURCE, (
        f"the model's self-report outranked the operator's own statement: "
        f"{[(h.triple.source, h.triple.tier) for h in hits]}")


# ---------------------------------------------------------------------------
# Recognition — measured, and its cost stated
# ---------------------------------------------------------------------------

ASSERTIONS = [
    "At the 600mg supratherapeutic dose, mean placebo-corrected QTc change was 11.4ms, exceeding the threshold.",
    "TAK-441 showed no hERG inhibition at therapeutic concentrations.",
    "The trial enrolled 248 patients across 14 sites.",
    "Baseline QTc was 412ms in the placebo arm.",
    "The compound is metabolised primarily by CYP3A4.",
    "Renal clearance accounted for 18% of total elimination.",
    "The threshold for regulatory concern is 10ms.",
]
NOT_ASSERTIONS = [
    "What was the QTc change at the supratherapeutic dose?",
    "Summarise the cardiac safety findings.",
    "Can you explain what a QT interval is?",
    "How does CYP3A4 inhibition affect exposure?",
    "Tell me what we know about TAK-441.",
    "list the protocol deviations",
    "thanks, that's helpful",
    "hi",
    "I think the dose might be too high.",
    "Could the QTc signal be an artefact?",
    "Remember that for later.",
    "Is 11.4ms above the threshold?",
    "please check the 600mg arm again",
    "who signed off on the protocol?",
]


@pytest.mark.parametrize("text", ASSERTIONS, ids=lambda t: t[:36])
def test_a_statement_of_fact_is_proposed(text):
    from backend.kb.assertion import looks_like_assertion
    ok, why = looks_like_assertion(text)
    assert ok, f"not proposed ({why}): {text!r}"


@pytest.mark.parametrize("text", NOT_ASSERTIONS, ids=lambda t: t[:36])
def test_a_question_or_instruction_is_never_proposed(text):
    """THE ERROR THAT MATTERS. A false negative costs a restatement; a false
    positive costs a permanent belief in a substrate whose founding law is
    that beliefs are never deleted. O.2a measured 0/20 here and said plainly
    that this means "the corpus I imagined contains no counterexample" — which
    is why detection only ever PROPOSES."""
    from backend.kb.assertion import looks_like_assertion
    ok, why = looks_like_assertion(text)
    assert not ok, f"proposed as an assertion ({why}): {text!r}"


def test_the_percent_measurement_bug_stays_fixed():
    """REGRESSION for a bug in this module's own regex, found by measuring it.

    `\\b` after `%` can never match — `%` is a non-word character, so there is
    no boundary between it and a following space. It silently dropped
    "accounted for 18% of total elimination" from the O.2a measurement. Same
    class as K.4's `\\w*error` matching `terror`.
    """
    from backend.kb.assertion import looks_like_assertion
    ok, why = looks_like_assertion(
        "Renal clearance accounted for 18% of total elimination.")
    assert ok and why == "measurement", (why,)


def test_a_message_is_split_before_it_is_judged():
    """A paragraph is rarely all one thing. O.2a named multi-sentence messages
    as untested; splitting is the answer, and this is the case that proves it
    — one claim among three questions."""
    from backend.kb.assertion import detect_assertions
    msg = ("What did the study find? Baseline QTc was 412ms in the placebo "
           "arm. Can you summarise that? Should we be worried?")
    got = [c.text for c in detect_assertions(msg)]
    assert got == ["Baseline QTc was 412ms in the placebo arm."], got


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------

def test_the_shape_puts_the_qualifier_and_the_quantity_where_retrieval_looks():
    from backend.kb.assertion import assertion_triples
    t = assertion_triples(ASSERTION)[0]
    assert "600mg" in t.subject, "the dose left subject/object"
    assert "QTc" in t.subject
    assert "11.4ms" in t.object
    assert "600" not in t.relation and "11.4" not in t.relation, (
        f"a number landed in the relation, which search_terms never searches: "
        f"{t.relation!r}")


def test_an_unshapeable_sentence_stores_nothing_rather_than_guessing():
    """Returning [] is a real answer and the safe one."""
    from backend.kb.assertion import assertion_triples
    assert assertion_triples("Cardiotoxicity.") == []
    assert assertion_triples("") == []


def test_the_asserted_class_outlives_every_other():
    """The ordering argument, asserted where it is used rather than only in
    the decay table: what the operator told the system must not fade before
    what a third party published."""
    from backend.kb.tiered_store import decay_rate_for_source
    from backend.kb.assertion import HUMAN_SOURCE
    assert decay_rate_for_source(HUMAN_SOURCE) < decay_rate_for_source("wikidata")
    assert decay_rate_for_source(HUMAN_SOURCE) < decay_rate_for_source("extraction")


# ---------------------------------------------------------------------------
# Law 3 — the confirm gate
# ---------------------------------------------------------------------------

@pytest.fixture
def assert_app(tmp_path):
    import os
    os.environ["NEXUS_API_TOKEN"] = "o2b-token"
    from backend.server import create_app, create_services
    services = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots")
    return create_app(services), services, {"Authorization": "Bearer o2b-token"}


def test_nothing_is_stored_without_explicit_confirmation(assert_app):
    """Law 3. The same gate /kb/dedup/confirm and /search/confirm use, and for
    the same reason — an irreversible write."""
    from fastapi.testclient import TestClient
    app, services, auth = assert_app
    with TestClient(app) as c:
        r = c.post("/kb/assert/confirm",
                   json={"sentence": ASSERTION, "confirmed": False},
                   headers=auth)
    assert r.status_code == 422
    assert "Law 3" in r.json().get("error", "")
    assert services.store.count() == 0, "a belief was stored without confirm"


def test_a_confirmed_assertion_becomes_a_belief(assert_app):
    from fastapi.testclient import TestClient
    from backend.kb.assertion import HUMAN_SOURCE
    app, services, auth = assert_app
    with TestClient(app) as c:
        r = c.post("/kb/assert/confirm",
                   json={"sentence": ASSERTION, "confirmed": True},
                   headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stored"] == 1 and body["shaped"] is True
    assert body["source"] == HUMAN_SOURCE

    stored = [t for t in services.store.all() if t.source == HUMAN_SOURCE]
    assert len(stored) == 1
    assert "11.4ms" in stored[0].object


def test_an_unshapeable_confirmation_declares_that_it_stored_nothing(assert_app):
    """I-6. A confirmed sentence that cannot be shaped must say so, not
    succeed silently with an empty result."""
    from fastapi.testclient import TestClient
    app, services, auth = assert_app
    with TestClient(app) as c:
        r = c.post("/kb/assert/confirm",
                   json={"sentence": "Cardiotoxicity.", "confirmed": True},
                   headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["stored"] == 0 and body["shaped"] is False
    assert body["hint"], "no guidance on why nothing was stored"
    assert services.store.count() == 0


def test_the_confirm_gate_requires_a_token(assert_app):
    """It writes the slowest-decaying class in the system — the last endpoint
    that should be open."""
    from fastapi.testclient import TestClient
    app, services, _auth = assert_app
    with TestClient(app) as c:
        # conftest authenticates every TestClient by default; the auth tests
        # strip the header to exercise the 401 path and so does this one.
        c.headers.pop("Authorization", None)
        r = c.post("/kb/assert/confirm",
                   json={"sentence": ASSERTION, "confirmed": True})
    assert r.status_code == 401
    assert services.store.count() == 0, "an unauthenticated write landed"
