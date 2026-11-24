"""Packet F.5 — did the answer actually USE this belief?

WHY THIS MODULE EXISTS
    The packet assumed the lineage record already knew. It does not.
    `agentic_loop.py` builds `injected=[... for rt in hits]` straight from the
    retrieval result, so `injected_json` records **what was placed in front of
    the model**, not what the model did with it. There was no notion of
    citation anywhere in the codebase, so defining one is the actual content
    of F.5 rather than a detail of it.

WHY IT MATTERS
    `retriever.store.fire(...)` reset `last_fired` — and therefore
    `current_weight` — for every retrieved belief, every turn, unconditionally.
    Since retrieval ranks tier ascending, a prominent belief was retrieved
    *because* it was prominent and then strengthened *for having been
    retrieved*. A closed loop with no evidence entering it.

THE DEFINITION, AND ITS BIAS
    A belief is **cited** when both its `subject` and its `object` appear in
    the answer text. Both, not either: an answer that mentions "python" has
    talked about the topic; an answer that mentions "python" and
    "programming_language" has used `(python, is_a, programming_language)`.

    The errors are not symmetric, so the definition leans deliberately:

      false CITED    reopens the feedback loop this packet exists to close,
                     and does so invisibly.
      false UNCITED  lets a belief decay a little that was in fact used. It is
                     self-correcting: the belief is still in the store at full
                     stored weight, and the next turn that genuinely needs it
                     retrieves it and cites it.

    So the test is conservative by construction. It under-credits.

WHAT IT DOES NOT DO
    No paraphrase, no synonymy, no embedding similarity. A belief the answer
    used in its own words is scored uncited. That is a known gap
    (REGROUND_REPORT §3.5's paraphrase case), and it is a gap in the
    *forgiving* direction. Matching against the model's self-extracted triples
    was considered and rejected: extraction is the least reliable signal in
    the system — F.3 gives it the fastest decay for exactly that reason — and
    a hallucinated triple would manufacture a citation out of nothing, which
    is the failure mode with the worse cost.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("nexus.kb.citation")

# ---------------------------------------------------------------------------
# AU — two rules, forward-only, versioned
# ---------------------------------------------------------------------------
# THE DEFECT, measured (the citation false-positive investigation): the
# conjunction below is only as informative as its LESS informative term, so
# `tell | co_activated | know` was "cited" by almost any answer — forty
# gate-refused rows were exempt from archival under I-4 because a sealed
# record cited them, and none had been used. The rules are over sets the
# project already maintains and argued once:
#
#   informative    each term carries at least one token outside the gate's
#                  FILLER_TOKENS and NULL_LITERALS — the gate's judgement
#                  about TERMS, composed; never its verdict on the row,
#                  which would make "cited" mean "well-formed".
#   no_substrate   a substrate row (schema.SUBSTRATE_RELATIONS) is an
#                  association, not a claim an answer can use.
#
# Neither consults the corpus, so both hold at ten times the store and three
# domains; a document-frequency rule was measured and rejected because it
# un-cites the conversation's central entity. FORWARD-ONLY: sealed flags
# and the exemption reader are untouched.
#
# THE FIFTH MEMBER of the F.5 / AK / AM / AN.2 family: a substrate row cited
# by coincidence was fire()d, and fire() resets last_fired — the decay clock
# AN.2 stopped re-promotion from refreshing was still refreshed through
# citation. `no_substrate` closes that path in citation mode.
from backend.kb.gate import FILLER_TOKENS, NULL_LITERALS, _tokens   # noqa: E402
from backend.kb.schema import SUBSTRATE_RELATIONS                     # noqa: E402

# The instrument, named on every surface the series read from (done,
# lineage, capture, forecast context) so the retention-cost and forecast
# series stay readable across the change. The version reflects what RAN.
CITATION_TEST_VERSION = "f5.2-informative-no_substrate"
PRE_RULE_TEST_VERSION = "f5.1-lexical"


def citation_rules_enabled() -> bool:
    """`NEXUS_CITATION_RULES=off` is the F.5 test exactly. Read at call
    time (the E.2 pattern)."""
    return os.environ.get("NEXUS_CITATION_RULES", "on").strip().lower() != "off"


def citation_test_version() -> str:
    return CITATION_TEST_VERSION if citation_rules_enabled() else PRE_RULE_TEST_VERSION


def informative(term: str) -> bool:
    """At least one token outside the gate's filler and null sets."""
    return any(t not in FILLER_TOKENS and t not in NULL_LITERALS
               for t in _tokens((term or "").lower()))


def _qualifies(triple) -> bool:
    """AU — may this row be cited at all? True under `off`."""
    if not citation_rules_enabled():
        return True
    if triple.relation in SUBSTRATE_RELATIONS:
        return False
    return informative(triple.subject) and informative(triple.object)

# ---------------------------------------------------------------------------
# Reinforcement policy
# ---------------------------------------------------------------------------
# "citation" (default) — full reinforcement for beliefs the answer used, none
#                        for beliefs merely placed in front of it.
# "retrieval"          — the pre-F.5 behaviour: everything retrieved is
#                        reinforced. Kept so the change is reversible without
#                        a code edit, and so a before/after can be measured on
#                        one store.
# "off"                — nothing is reinforced by the turn at all. Diagnostic:
#                        it makes decay observable without retrieval
#                        interference.
REINFORCE_MODES = ("citation", "retrieval", "off")
DEFAULT_REINFORCE_MODE = "citation"


def reinforce_mode() -> str:
    """Read at call time (the E.2 pattern) so it is switchable per test."""
    mode = os.environ.get("NEXUS_REINFORCE_MODE", DEFAULT_REINFORCE_MODE).strip().lower()
    if mode not in REINFORCE_MODES:
        logger.warning("NEXUS_REINFORCE_MODE=%r is not one of %s; using %r",
                       mode, REINFORCE_MODES, DEFAULT_REINFORCE_MODE)
        return DEFAULT_REINFORCE_MODE
    return mode

# Matches how the store writes multi-word concepts: `programming_language`,
# `machine_learning`. Both the underscored and the spaced form are accepted,
# because the model writes prose and the store writes identifiers.
_NON_WORD = re.compile(r"[^a-z0-9]+")


def _variants(term: str) -> list[str]:
    """Surface forms a model might plausibly have written for a concept."""
    t = (term or "").strip().lower()
    if not t:
        return []
    spaced = _NON_WORD.sub(" ", t).strip()
    if not spaced:
        return []
    out = {spaced}
    if "_" in t:
        out.add(t)
    return [v for v in out if v]


def _mentions(haystack: str, term: str) -> bool:
    """Whole-token containment, so `cat` does not match `catastrophe`."""
    for variant in _variants(term):
        pattern = r"(?<![a-z0-9])" + re.escape(variant) + r"(?![a-z0-9])"
        if re.search(pattern, haystack):
            return True
    return False


def normalise(text: str) -> str:
    return _NON_WORD.sub(" ", (text or "").lower())


def is_cited(triple, response_text: str) -> bool:
    """True when the answer used this belief, on the conservative reading."""
    hay = normalise(response_text)
    if not hay.strip():
        return False
    return (_qualifies(triple)
            and _mentions(hay, triple.subject) and _mentions(hay, triple.object))


def cited_ids(hits, response_text: str) -> set[int]:
    """Ids of the retrieved beliefs the answer actually used."""
    hay = normalise(response_text)
    if not hay.strip():
        return set()
    return {
        rt.triple.id for rt in hits
        if rt.triple.id is not None
        and _qualifies(rt.triple)
        and _mentions(hay, rt.triple.subject)
        and _mentions(hay, rt.triple.object)
    }
