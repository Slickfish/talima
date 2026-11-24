"""Packet O.2b — the path by which a person's own statement becomes a belief.

WHY THIS MODULE EXISTS
======================
F.0.4 established that there is **no ingest path for user-asserted content
anywhere in the codebase**, and F.4 was blocked on the design decision. An
hour of real use made it the most consequential defect in the system: the
operator stated a threshold-exceeding finding in their own words, the store
learned five textbook facts about what a QT interval is, and the later answer
omitted the finding because it had never become a belief.

`agentic_loop.py` extracts from the ASSISTANT'S OWN ANSWER. The substrate
learned what the model said about the input, not what the user said.

THE THREE DECISIONS, AND THE REASONING
======================================
**Recognition: detected, then CONFIRMED.** Not inferred silently. The error
costs are not comparable — a false negative costs a restatement, a false
positive costs a permanent belief in a substrate whose founding law is that
beliefs are never deleted (Law 4, I-9). O.2a measured 0/20 false positives on
a corpus written for the purpose, and said plainly that this means "the
corpus I imagined contains no counterexample". Detection proposes; the person
decides. That is Law 3, and it reuses the pattern `/kb/dedup/confirm` and
`/search/confirm` already established.

**Class: `asserted`, FIRST in the ordering, slowest decay.** F.3 ranked
evidence by external referent, and a named person stating a fact about their
own trial has the strongest referent the system holds — stronger than
Wikidata, which is a third party that can be wrong. Nothing should out-persist
it. See `tiered_store.CLASS_DECAY_PER_DAY`.

**Shape: THIS MODULE decides the triple, not the user's free text.** Two
reasons, both measured in O.2a:

  1. The gate will not guard it. A lenient class accepts a whole sentence as
     a term — measured: the operator's sentence is refused under `extraction`
     for `copula:was` and ACCEPTED under `external`.
  2. `search_terms` matches subject and object ONLY; the relation column is
     never searched (`tiered_store.py:483`). Measured, with the belief stored:
     QTc in subject -> retrieved, QTc in RELATION -> NOT retrieved. A triple
     shaped the wrong way is stored and invisible.

So the shaping rule is: **the qualifier and the measured quantity both land in
subject/object, never in the relation.** `_shape` is written to that rule and
`test_the_shape_keeps_the_salient_terms_where_retrieval_looks` pins it.

PROVENANCE — AN OPEN QUESTION, RECORDED NOT INVENTED
====================================================
There is no actor column, anywhere. `triples` carries `source` only, which is
class-level rather than actor-level, and `source` is inside the UNIQUE key, so
a per-actor value (`asserted:alice`) would fragment the constraint and
multiply rows per claim. For a single-operator local system that is defensible
and it is what ships. It is NOT defensible for a second user, and the seam
spec's attribution requirement assumes an answer's evidence can be attributed
— so today the strongest evidence class is the least attributable. Logged as
an open question rather than solved by guesswork.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The one source value this path writes. Registered in
# `tiered_store.SOURCE_CLASS` so it cannot decay as an unclassified default.
HUMAN_SOURCE = "human_assertion"

# Arrival prominence. Deliberately tier 1, matching `offline_fixture` rather
# than claiming tier 0: O.4 is investigating tier-on-arrival across the whole
# store and this must not pre-empt it. Tier 1 already outranks extraction's
# tier 2, which is what the acceptance test needs.
ASSERTION_TIER = 1
ASSERTION_WEIGHT = 0.95

# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------
# Measured in O.2a on 12 assertions and 20 non-assertions in the operator's
# register: recall 10/12, false positives 0/20. The two misses are recorded
# honestly in the report — one was a boundary bug in this very regex, found by
# measuring rather than by reading, and fixed below.

_INTERROGATIVE = frozenset({
    "what", "why", "how", "when", "where", "who", "which", "whose", "whom",
    "is", "are", "was", "were", "do", "does", "did", "can", "could",
    "should", "would", "will", "may", "might", "has", "have", "had",
})
_IMPERATIVE = frozenset({
    "summarise", "summarize", "list", "show", "explain", "tell", "give",
    "find", "search", "compare", "write", "draft", "check", "remember",
    "forget", "add", "remove", "update", "run", "open", "describe",
    "analyse", "analyze", "review", "consider", "look", "make", "create",
    "generate", "help", "let", "please", "try", "use", "set", "get", "put",
    "take", "go", "stop", "continue",
})
_FINITE = frozenset({
    "is", "are", "was", "were", "has", "have", "had", "shows", "showed",
    "exceeds", "exceeded", "measured", "reported", "found", "observed",
    "increased", "decreased", "remains", "remained", "equals", "causes",
    "caused", "produces", "produced", "indicates", "indicated",
    "demonstrates", "demonstrated", "occurs", "occurred", "means",
    "withdrew", "accounted", "enrolled", "recorded",
})
_HEDGE = ("might", "may", "could", "possibly", "perhaps", "maybe",
          "i think", "i wonder", "not sure", "presumably", "i guess",
          "suppose")

# A number with a unit. NOTE the trailing `(?![a-z0-9])` and not `\b`: `\b`
# after `%` can never match, because `%` is a non-word character and there is
# no boundary between it and a following space. That bug silently dropped
# "accounted for 18% of total elimination" from the O.2a measurement, and it
# is the same class as K.4's `\w*error` matching `terror` — found by measuring
# the rule, not by reading it.
_MEASUREMENT = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|ms|kg|ml|%|mmhg|hz|nm|mm|cm|units?|"
    r"years?|months?|days?|hours?|minutes?|patients?|subjects?|sites?|fold)"
    r"(?![a-z0-9])", re.I)

_TOKEN = re.compile(r"[a-z0-9.'%-]+")


@dataclass(frozen=True)      # Law 1
class AssertionCandidate:
    """One sentence a person appears to have asserted. A candidate only —
    nothing becomes a belief until it is confirmed."""
    text: str
    reason: str              # why it was proposed, shown to the user


@dataclass(frozen=True)      # Law 1
class AssertedTriple:
    subject: str
    relation: str
    object: str
    weight: float = ASSERTION_WEIGHT
    tier: int = ASSERTION_TIER


def looks_like_assertion(text: str) -> tuple[bool, str]:
    """(is a candidate, why). The `why` is returned for both answers, so a
    refusal can be shown rather than merely applied — I-6."""
    t = (text or "").strip()
    if not t:
        return False, "empty"
    low = t.lower()
    toks = _TOKEN.findall(low)
    if not toks:
        return False, "no tokens"
    if t.rstrip().endswith("?"):
        return False, "question"
    if toks[0] in _INTERROGATIVE:
        return False, f"opens with interrogative or auxiliary '{toks[0]}'"
    if toks[0] in _IMPERATIVE:
        return False, f"opens with imperative '{toks[0]}'"
    if any(h in low for h in _HEDGE):
        return False, "hedged — a maybe is not an assertion"
    has_measure = bool(_MEASUREMENT.search(t))
    has_finite = any(v in toks for v in _FINITE)
    if not (has_finite or has_measure):
        return False, "no finite verb and no measurement"
    if len(toks) < 4:
        return False, "too short to be a claim"
    return True, "measurement" if has_measure else "finite verb"


def detect_assertions(message: str) -> list[AssertionCandidate]:
    """Candidate assertions in one user message, sentence by sentence.

    A message is rarely all one thing — a paragraph may carry one claim and
    three questions — so this splits before judging. O.2a named exactly this
    as untested at the time; splitting is the response.
    """
    out: list[AssertionCandidate] = []
    for raw in re.split(r"(?<=[.!?])\s+", (message or "").strip()):
        sentence = raw.strip()
        if not sentence:
            continue
        ok, why = looks_like_assertion(sentence)
        if ok:
            out.append(AssertionCandidate(text=sentence, reason=why))
    return out


# ---------------------------------------------------------------------------
# Shaping — O.2b decides the triple
# ---------------------------------------------------------------------------

_COPULA = re.compile(
    r"\b(was|were|is|are|measured|showed|shows|exceeded|exceeds|"
    r"reached|reported|remained|equals|equalled)\b", re.I)
# A leading qualifier: "At the 600mg supratherapeutic dose, ..." — the part
# that says under what conditions the claim holds. It must survive into the
# SUBJECT, because it carries the dose and retrieval only searches subject
# and object.
_QUALIFIER = re.compile(r"^\s*(?:at|in|for|under|during|with|after|before)\s+"
                        r"(?:the\s+)?(?P<q>[^,]{2,80}),\s*", re.I)
_TRAILING_CLAUSE = re.compile(r",\s*(?:exceeding|which|thereby|and\s+so)\b.*$",
                              re.I)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" ,.;:").strip()


def assertion_triples(sentence: str) -> list[AssertedTriple]:
    """Shape one asserted sentence into triples, or return [] if it cannot.

    Returning [] is a real answer and the safe one: a confirmed assertion that
    cannot be shaped stores nothing, which is better than storing a triple
    nobody meant. The caller reports the empty result rather than hiding it.

    THE SHAPING RULE, and the reason for it: the qualifier and the measured
    quantity must both end up in SUBJECT or OBJECT, never in the relation,
    because `search_terms` does not search relations. A triple with the only
    distinguishing term in its relation is stored and unfindable.
    """
    text = _clean(sentence)
    if not text:
        return []

    qualifier = ""
    m = _QUALIFIER.match(text)
    if m:
        qualifier = _clean(m.group("q"))
        text = text[m.end():]

    # drop a trailing "..., exceeding the threshold" — it is commentary on the
    # claim rather than part of it, and it would otherwise pad the object.
    text = _TRAILING_CLAUSE.sub("", text)
    text = _clean(text)

    verb = _COPULA.search(text)
    if not verb:
        return []
    subject = _clean(text[:verb.start()])
    object_ = _clean(text[verb.end():])
    if not subject or not object_:
        return []

    if qualifier:
        # Appended, not prefixed: the head of the phrase stays first, which is
        # what a reader scans and what a LIKE query on a leading term matches.
        subject = f"{subject} at {qualifier}"

    relation = re.sub(r"[^a-z0-9]+", "_", verb.group(1).lower()).strip("_")
    return [AssertedTriple(subject=subject, relation=relation, object=object_)]
