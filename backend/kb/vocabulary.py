"""Packet V.1 — the relation vocabulary offered to extraction.

WHY THIS EXISTS, AND WHY IT IS SCAFFOLDING (OQ-2)
    Extraction invented a new predicate for nearly every triple: on the
    operator's store, ~206 distinct predicates over ~271 non-co-activation
    rows — roughly 1.3 rows per predicate. Nothing composes, nothing
    traverses by predicate, and contradiction detection has almost no surface
    to act on.

    **A curated list is not the intended end state.** OQ-2 is emergent
    predicate identity: predicates should be associated by proximity and
    co-occurrence rather than declared equal by a table, with the canonical
    form emerging as the most reinforced member of a cluster. That is blocked
    on F-04/F-05 (semantic retrieval), because it needs predicates embedded in
    a space where proximity means something. This module is what ships in the
    meantime — a reader meeting it should meet the reasoning, not assume a
    list was the design.

MEASURED BEFORE BUILT
    `scripts/measure_vocabulary_compliance.py`, three arms, 20 chunks, a real
    model on the operator's machine:

        arm   triples  distinct  pick  near  other  miss  gate_rej
        A         120       106  0.08  0.12  0.00   0.79      0.38
        B         110        73  0.38  0.09  0.00   0.53      0.51
        C         126        85  0.38  0.12  0.00   0.50      0.32

    Distinct predicates 106 -> 73; pick rate 0.08 -> 0.38. **Arm B is what
    ships**: C (telling the model off-list predicates will be rejected) scored
    identically, and a threat the pipeline would then have to carry out is a
    gate change rather than a prompt change.

THE VOCABULARY IS CONFIGURATION, NOT CODE
    Same pattern as T.2's opposition set, and the same argument — the seam
    spec's own: the mechanism is ours, the vocabulary is the customer's.
    Pharma needs `metabolized_by` and `binds_to`; credit needs `secured_by`;
    mining needs `within_tolerance`. Compiling any of that in means every
    domain forks the code. `NEXUS_RELATION_VOCABULARY` names a JSON file of
    strings; the default is the compiled 33, so nothing changes until a file
    is named.

WHAT IS DELIBERATELY *NOT* TUNABLE
    `decompose._HEADS`. Those are the relational heads P.2 splits a predicate
    at — `X | is_a_reliable_news_source | None` becomes
    `X | is_a | reliable news source`. That is parsing machinery about English
    sentence structure, not domain vocabulary. A customer editing their
    ontology must not change how sentences are split, so the two are separate
    even though the default vocabulary is DERIVED from the heads.

    Likewise `RELATION_ALIASES` (ingest normalisation, `gate.py`) and
    `CONTRADICTION_PAIRS` (opposition, `contradiction.py`) keep their own
    roles. This module is the single definition of *the list offered to the
    extractor*, derived from all three so a repo that already believes
    something about relations does not get contradicted by a fourth list.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from backend.cortex.contradiction import CONTRADICTION_PAIRS
from backend.kb.decompose import _HEADS
from backend.kb.gate import RELATION_ALIASES, normalise_relation

logger = logging.getLogger("nexus.vocabulary")

# The escape hatch offered in the prompt. Measured at 0.00 in all three arms —
# the model never used it, so the 53% miss is invention rather than honest
# abstention. It stays because arm B is what was MEASURED and V.1 ships the
# measured thing; whether it earns its tokens is a fourth arm for a later
# run, not a change to make on a hunch.
OTHER = "other"

DEFAULT_VOCABULARY_SOURCE = "compiled_default"
_SOURCE = {"source": DEFAULT_VOCABULARY_SOURCE, "error": ""}


def default_relations() -> tuple[str, ...]:
    """The compiled default: what this repo already believes a relation is.

    Union of the three existing vocabularies, put through the shipped
    normaliser so the eight alias spellings collapse the way ingest already
    collapses them — 41 raw, 33 after.

    DERIVED, never hand-written. If the union is deficient for a corpus that
    is a FINDING about the repo, not a gap to quietly paper over; widening it
    is `scripts/propose_relation_vocabulary.py`'s job and the operator's
    decision.
    """
    raw = (set(_HEADS)
           | set(RELATION_ALIASES.values())
           | {r for pair in CONTRADICTION_PAIRS for r in pair})
    return tuple(sorted({normalise_relation(r) for r in raw if r}))


DEFAULT_RELATIONS = default_relations()


def vocabulary_enabled() -> bool:
    """`NEXUS_EXTRACTION_VOCABULARY=off` restores the pre-V.1 prompt EXACTLY.

    The rollback (E.2, Q.2, S.2 precedent). Read at call time so a `.env`
    loaded after import is honoured.
    """
    return os.getenv("NEXUS_EXTRACTION_VOCABULARY", "on").strip().lower() != "off"


def load_relations(path: "str | Path | None" = None) -> tuple[str, ...]:
    """The vocabulary in force. Falls back to the compiled default.

    DECLARED, NOT SILENT (I-6): a file that cannot be read or parsed WARNS and
    falls back; it never leaves the extractor with an empty list and never
    stops the app. `vocabulary_source()` reports which happened, and
    `/cortex/stats` carries it — an operator who installed sixty predicates
    and is silently running on thirty-three has no other way to tell.
    """
    raw = str(path) if path else os.environ.get("NEXUS_RELATION_VOCABULARY", "")
    raw = raw.strip()
    if not raw:
        _SOURCE.update(source=DEFAULT_VOCABULARY_SOURCE, error="")
        return DEFAULT_RELATIONS

    p = Path(raw)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(
            "NEXUS_RELATION_VOCABULARY=%s could not be read as JSON (%s: %s); "
            "falling back to the %d compiled relations. Extraction is being "
            "offered the default list.",
            raw, type(e).__name__, e, len(DEFAULT_RELATIONS))
        _SOURCE.update(source=DEFAULT_VOCABULARY_SOURCE,
                       error=f"{type(e).__name__}: {e}")
        return DEFAULT_RELATIONS

    relations: list[str] = []
    dropped = 0
    for item in (data if isinstance(data, list) else []):
        if not isinstance(item, str):
            dropped += 1
            continue
        canon = normalise_relation(item)
        # `other` is the escape hatch, not a relation; a file listing it would
        # offer it twice and make the miss/other measurement unreadable.
        if not canon or canon == OTHER:
            dropped += 1
            continue
        relations.append(canon)

    if not relations:
        logger.warning(
            "NEXUS_RELATION_VOCABULARY=%s yielded no usable relations (%d "
            "malformed); falling back to the %d compiled ones.",
            raw, dropped, len(DEFAULT_RELATIONS))
        _SOURCE.update(source=DEFAULT_VOCABULARY_SOURCE,
                       error=f"no usable relations ({dropped} malformed)")
        return DEFAULT_RELATIONS

    if dropped:
        logger.warning("%s: %d malformed entr(ies) skipped", raw, dropped)
    logger.info("relation vocabulary loaded from %s: %d relation(s)",
                raw, len(relations))
    _SOURCE.update(source=str(p),
                   error=f"{dropped} malformed entr(ies) skipped" if dropped else "")
    return tuple(dict.fromkeys(relations))          # dedupe, order preserved


def vocabulary_source() -> dict:
    """Where the list in force came from, and what went wrong if anything."""
    return dict(_SOURCE)


def vocabulary_block(relations: "tuple[str, ...] | None" = None, *,
                     escape_hatch: bool = True) -> str:
    """Arm B's instruction, verbatim — the thing that was measured.

    Kept as one function so the shipped extractor and
    `scripts/measure_vocabulary_compliance.py` cannot drift apart: a harness
    measuring a prompt nobody ships is the defect that arm A's import-rather-
    than-restate rule already guards against, one level up.

    `escape_hatch=False` is V.2's arm D — the same block minus the `other`
    line. It exists because the hatch measured 0.00 in all three arms and the
    honest way to decide whether it earns its tokens is a fourth arm on the
    same corpus, not a removal on a hunch. The SHIPPED prompt keeps it until
    that measurement says otherwise.
    """
    rels = load_relations() if relations is None else relations
    block = ("Use one of these relations, exactly as written:\n"
             + ", ".join(rels) + "\n")
    if escape_hatch:
        block += f"If none of them fits, use `{OTHER}`.\n"
    return block


__all__ = ["DEFAULT_RELATIONS", "DEFAULT_VOCABULARY_SOURCE", "OTHER",
           "default_relations", "load_relations", "vocabulary_block",
           "vocabulary_enabled", "vocabulary_source"]
