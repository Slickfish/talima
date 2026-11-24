"""Packet P.2 — three rules were refusing content that fed sealed decisions.

§3b measured 34 rejected rows cited by SEALED lineage records, and the
script's own warning applies: *that is a reason to doubt the rule, not the
belief.* The judgement differs per rule, and one of the three overturned the
instinct I started with.

WHY DECOMPOSITION IS NOT SANITISING
    K.5 ruled against sanitising: "storing a belief that differs from what the
    source said — a provenance loss in the layer whose job is faithfulness,
    and a SILENT one." Decomposition is a different act. It does not alter a
    belief; it reads one candidate as the several claims it encodes and offers
    each to the gate separately. `add()` remains the single chokepoint and
    every part is gated on its own merits. The count is DECLARED by the
    caller, so one-in-three-out is visible rather than silent.

    Nothing already stored is touched (I-9). This is a boundary change.

THE THREE, MEASURED
===================
`object:null_literal` — DECOMPOSE, else reject.  (8 of 11 sealed-cited)
    BBC | is_a_reliable_news_source | None

    The instinct "the shape is wrong, not the content" is right, and the
    measurement says something sharper: these rows are retrievable ONLY BY
    SUBJECT. The whole predicate sits in the relation, and `search_terms`
    matches subject and object and never the relation (tiered_store.py:483).
    So ACCEPTING them would store a belief almost nothing can find —
    `BBC | is_a_reliable_news_source | None` is reachable by the token "bbc"
    and by nothing else. Measured, all three of the packet's examples.

    Splitting the relation at its head moves the content into the object,
    where retrieval looks:
        BBC | is_a | reliable news source

`object:structured_literal` — DECOMPOSE.  (7 of 13 sealed-cited)
    Interval_training | includes_examples_of_activities
                      | ['sprinting', 'cycling_at_a_slower_pace']

    A list object is n facts wearing one row. Decomposing keeps every one and
    satisfies the rule, which is the packet's own reading and it is correct.
    The rule stays universal for anything that will not parse.

`object:multi_preposition` — SUBJECT ONLY.  (7 of 15 sealed-cited)
    THIS ONE OVERTURNED THE INSTINCT, including mine. "Borderline" suggests a
    close judgement about which objects to keep. Measured, there is barely a
    judgement left to make: P.1's word cap already refuses almost everything
    this rule uniquely caught.

        alternating periods of intense physical activity with recovery phases
            -> 9 tokens, refused by too_many_tokens whatever this rule does
        the process of converting light into chemical energy   -> 8, ditto
        a measure of the spread of values around the mean      -> 10, ditto
        issue of finding the latest news about Iran online     -> 9, ditto

    The band this rule uniquely governs is objects of SEVEN TOKENS OR FEWER
    carrying two prepositions, and in that band the population is legitimate
    descriptive phrases — `reputable_outlets for real-time updates on Iran`.

    So the asymmetry is the answer, and it is a statement about what the two
    positions ARE: a subject is the thing being described and must be a
    concept; an object is the description and may legitimately be a phrase.
    `theory of relativity` is a subject; "a measure of the spread of values
    around the mean" is what an object is for.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from backend.kb.gate import RELATION_ALIASES, _tokens

# Relational heads a predicate can be split at, longest first so
# `is_an_example_of` wins over `is`. Deliberately small and explicit rather
# than a part-of-speech guess: an unrecognised head means "cannot decompose",
# which is a safe answer, and K.3's argument against putting the gate in the
# ontology business applies here too.
_HEADS = sorted(
    set(RELATION_ALIASES) | set(RELATION_ALIASES.values()) | {
        "is_a", "is_an_example_of", "is_the", "is_part_of", "is_used_for",
        "has", "have", "contains", "includes", "provides", "carries",
        "causes", "produces", "represents", "stands_for", "measures",
        "indicates", "requires", "supports", "enables", "prevents",
        "consists_of", "is_composed_of", "belongs_to", "refers_to",
    }, key=len, reverse=True)

_LIST_RE = re.compile(r"^\s*[\[\{]")


@dataclass(frozen=True)          # Law 1
class Decomposed:
    subject: str
    relation: str
    object: str
    reason: str                  # which decomposition produced it


def _looks_null(value: str) -> bool:
    return (value or "").strip().lower() in {
        "none", "null", "nil", "n/a", "na", "undefined", "", "-"}


def decompose_null_object(subject: str, relation: str) -> list[Decomposed]:
    """`X | is_a_reliable_news_source | None` -> `X | is_a | reliable news source`.

    Returns [] when the relation carries no recognisable head, which is the
    honest answer for a predicate like
    `carries_oxygen_from_your_lungs_to_all_parts_of_your_body`: its tail is
    nine tokens and would be refused by the word cap anyway, so pretending to
    save it would only move where it fails.
    """
    rel = (relation or "").strip().lower()
    for head in _HEADS:
        if rel == head:
            return []                       # nothing to move
        if rel.startswith(head + "_"):
            tail = rel[len(head) + 1:].replace("_", " ").strip()
            if not tail:
                return []
            canon = RELATION_ALIASES.get(head, head)
            return [Decomposed(subject=subject, relation=canon, object=tail,
                               reason="null_object_split")]
    return []


def decompose_list_object(subject: str, relation: str,
                          object_: str) -> list[Decomposed]:
    """`X | includes | ['a', 'b']` -> two rows, one per member.

    Parsed with `ast.literal_eval`, never `eval`: the value is model-derived
    text and this path must not become a way to execute it.
    """
    raw = (object_ or "").strip()
    if not _LIST_RE.match(raw):
        return []
    try:
        value = ast.literal_eval(raw)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return []
    if isinstance(value, dict):
        value = list(value.keys())
    if not isinstance(value, (list, tuple, set)) or not value:
        return []
    out = []
    for member in value:
        text = str(member).replace("_", " ").strip()
        if not text or _looks_null(text):
            continue
        out.append(Decomposed(subject=subject, relation=relation, object=text,
                              reason="list_object_split"))
    return out


def decompose(subject: str, relation: str, object_: str) -> list[Decomposed]:
    """Every candidate this row encodes, or [] if it encodes only itself.

    An empty result means "store the original as it stands and let the gate
    judge it" — decomposition never suppresses a row, it only ever offers
    alternatives the caller can gate.
    """
    if _looks_null(object_):
        return decompose_null_object(subject, relation)
    if _LIST_RE.match((object_ or "").strip()):
        return decompose_list_object(subject, relation, object_)
    return []


def would_decompose(subject: str, relation: str, object_: str) -> bool:
    return bool(decompose(subject, relation, object_))
