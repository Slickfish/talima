"""The stringified null — three layers that work only in concert.

THE INCIDENT
    Ten stored claims carry the literal string 'None' as their object — e.g.
    `Hemoglobin | carries_oxygen_from_your_lungs_to_all_parts_of_your_body |
    None`. The model emitted `"object": null`; the parse stringified it; and
    the rows entered because they PREDATE K.2's gate (2026-08-10) — the
    `null_literal` rule has refused this exact shape on subject and object
    since the day the gate existed. I-9: those ten stay; this file governs
    what enters from today.

THE PIPELINE, named end to end for the first time
    parse       extraction.py:145-147 fabricates the token 'None' from the
                model's null — DELIBERATELY LOAD-BEARING, see the pin below.
    decompose   `_looks_null` recognises it and `null_object_split` rescues
                the claim the predicate swallowed, when a head exists.
    gate        `null_literal` refuses whatever the rescue could not save,
                DECLARED in `done.extraction_gate` — never a silent drop.

    Each layer was tested alone; nothing pinned the chain. This file drives
    real turns through all three.

THE HOLE THIS FILE'S SENTINEL CLOSES
    The null check ran on subject and object and NOT on relation —
    `Hemoglobin | None | protein` was accepted. `inspect_relation` is
    deliberately weaker than `inspect_term` (a predicate legitimately
    contains copulas and articles), but 'None' is not a weaker-rules case:
    no predicate anyone means is the stringified nothing.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.kb.gate import NULL_LITERALS, BeliefRejected, check_triple
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import run_turn
from backend.loop.state import NexusLoopState


class _Provider:
    """First stream_chat call answers; the second returns extraction JSON."""

    def __init__(self, extraction_payload: str):
        self.extraction_payload = extraction_payload
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        yield "An answer about hemoglobin." if self.calls == 1 \
            else self.extraction_payload

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _turn(tmp_path, extraction_payload: str):
    store = TripleStore(tmp_path / "kb.db")
    retriever = TripleRetriever(store)
    frames = []

    async def drive():
        async for frame, _s in run_turn(
                NexusLoopState(), "tell me about hemoglobin",
                retriever=retriever,
                provider=_Provider(extraction_payload)):
            frames.append(frame)

    asyncio.run(drive())
    done = next(json.loads(f.split("data: ", 1)[1].strip())
                for f in reversed(frames) if f.startswith("event: done\n"))
    return store, done


def _stored_null_terms(store) -> list[tuple]:
    return [(t.subject, t.relation, t.object) for t in store.all()
            if t.subject.lower() in NULL_LITERALS
            or t.relation.lower() in NULL_LITERALS
            or t.object.lower() in NULL_LITERALS]


# ---------------------------------------------------------------------------
# THE SENTINEL — the relation field joins the null check
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("literal", ["None", "null", "N/A"])
def test_the_gate_refuses_a_null_relation(literal):
    """`Hemoglobin | None | protein` was ACCEPTED — the null check covered
    subject and object and skipped the field between them."""
    with pytest.raises(BeliefRejected) as e:
        check_triple("Hemoglobin", literal, "protein",
                     evidence_class="extraction", weight=0.5)
    assert e.value.reason == "null_literal"
    assert e.value.field == "relation"


def test_a_legitimate_relation_still_passes():
    """Non-vacuity in the accepting direction: the fix must not make
    `inspect_relation` any stronger than the one literal family —
    its deliberate weakness (copulas, articles) is a recorded decision."""
    check_triple("hemoglobin", "is_a", "protein",
                 evidence_class="extraction", weight=0.5)  # must not raise


# ---------------------------------------------------------------------------
# End to end: no null literal reaches the store by ANY field or form
# ---------------------------------------------------------------------------

def test_no_null_literal_is_stored_and_every_refusal_is_declared(tmp_path):
    """One turn, four malformed triples: null object with a headless
    predicate, null subject, null relation, and the list form. Nothing
    null-shaped may be stored, and the gate's work must be DECLARED in
    `done.extraction_gate` — a silent drop is the K.2 regression."""
    payload = json.dumps([
        {"subject": "Hemoglobin",
         "relation": "carries_oxygen_from_your_lungs_to_all_parts_of_your_body",
         "object": None, "confidence": 0.8},
        {"subject": None, "relation": "is_a", "object": "protein",
         "confidence": 0.8},
        {"subject": "Hemoglobin", "relation": None, "object": "protein",
         "confidence": 0.8},
        ["Myoglobin", "stores_oxygen_in_your_muscles", None, 0.8],
    ])
    store, done = _turn(tmp_path, payload)
    assert _stored_null_terms(store) == [], (
        "a stringified null entered the store — the boundary has a bypass")
    gate = done["extraction_gate"]
    # Extraction saw 4 candidates; whatever was not rescued by decomposition
    # must be visibly rejected, not silently absent.
    assert gate["extracted"] == 4
    assert gate["rejected"] >= 1
    assert "null_literal" in gate["reasons"]
    assert gate["stored"] + gate["rejected"] >= 1
    store.close()


def test_a_rescuable_null_object_becomes_a_real_claim(tmp_path):
    """The rescue half: a swallowed object with a recognisable head and a
    short tail comes back as a true claim — proof the pipeline's answer to
    null is recovery-then-judgement, not blanket refusal."""
    payload = json.dumps([{"subject": "Hemoglobin",
                           "relation": "carries_oxygen",
                           "object": None, "confidence": 0.8}])
    store, done = _turn(tmp_path, payload)
    rows = [(t.subject, t.relation, t.object) for t in store.all()]
    assert ("hemoglobin", "carries", "oxygen") in [
        tuple(x.lower() for x in r) for r in rows], rows
    assert done["extraction_gate"]["decomposed"] >= 1
    assert _stored_null_terms(store) == []
    store.close()


# ---------------------------------------------------------------------------
# THE PIN — the fabricated token is deliberate, and here is why
# ---------------------------------------------------------------------------

def test_the_parse_still_fabricates_the_none_token_on_purpose():
    """DO NOT 'FIX' extraction.py's `str(item.get(...))` WITHOUT READING THIS.

    `str(None)` fabricating the token 'None' looks like a bug. It is the
    routing that makes a model-emitted null end in DECLARED handling:

      - `decompose._looks_null` recognises the token and `null_object_split`
        rescues the claim the predicate swallowed (P.2);
      - what cannot be rescued reaches the gate, which refuses it as
        `null_literal` and the refusal is DECLARED in done.extraction_gate.

    The obvious cleanup — mapping null to "" — fails `if not (s and r and o)`
    at extraction.py:160, so the triple is dropped BEFORE decomposition and
    BEFORE the gate: no rescue, no refusal, no declaration. A silent drop is
    exactly the defect class K.2 exists to end. If you change the parse,
    this test failing is the system telling you where the null now goes —
    make it land somewhere DECLARED, then update this pin.
    """
    from backend.cortex.extraction import _parse_triples
    from backend.kb.decompose import _looks_null, decompose

    # Subject must itself survive Fix 5's meaningfulness filter (a one-char
    # subject is dropped for its own reason and would make this pin vacuous).
    triples = _parse_triples(
        '[{"subject": "wikipedia", "relation": "is_a_reliable_source", '
        '"object": null, "confidence": 0.5}]')
    assert len(triples) == 1 and triples[0].object == "None", (
        "the parse no longer emits the 'None' token for a model-emitted "
        "null. That token was LOAD-BEARING: it is what routes the swallowed-"
        "object shape into decompose's null_object_split rescue and, failing "
        "that, into the gate's DECLARED null_literal refusal. If it became "
        "'' the triple is silently dropped at extraction.py:160 — no rescue, "
        "no refusal, no declaration. Re-route the null into something "
        "declared before changing this pin. See this test's docstring.")

    # And the chain it feeds, so the pin cannot outlive the mechanism:
    assert _looks_null("None") is True
    parts = decompose("wikipedia", "is_a_reliable_source", "None")
    assert parts and parts[0].reason == "null_object_split"
