"""Guards on the measurement harness, not on a build.

WHY A MEASUREMENT NEEDS SENTINELS
    `scripts/measure_vocabulary_compliance.py` produces the number that
    decides whether a relation vocabulary is worth building at all. A harness
    that measures the wrong thing is worse than none, because the number comes
    back looking like an answer.

    Three ways it could measure the wrong thing, all guarded here:

    1. **Arm A drifts from the shipped prompt.** A control that carries its
       own copy of `EXTRACTION_PROMPT` measures a prompt nobody ships, and the
       drift would be invisible the moment `extraction.py` changed. Checked by
       AST: the harness must IMPORT the constant and must not define a literal
       that looks like a prompt.
    2. **The vocabulary is hand-written.** The candidate set has to be the
       union of what the repo already believes a relation is. If someone
       "improved" it, the measurement becomes about their judgement rather
       than the model's behaviour.
    3. **The near-miss rule is too generous.** Near-miss is the number that
       decides between "build the vocabulary" and "build a post-extraction
       mapper". A rule that counted `increases` as a near miss for `decreases`
       on their shared tail would invert the recommendation.

    Plus the standing ones: nothing imports `backend.server` (H.2), and
    nothing writes to `data/`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.cortex.extraction import EXTRACTION_PROMPT
from scripts.measure_vocabulary_compliance import (
    CONSTRAINT_LINE,
    OTHER,
    VOCAB,
    arm_prompt,
    candidate_vocabulary,
    classify_predicate,
    quality_metrics,
)

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "scripts" / "measure_vocabulary_compliance.py"
TREE = ast.parse(HARNESS.read_text(encoding="utf-8"), filename=str(HARNESS))

SAMPLE = "Warfarin is a drug. CYP3A4 metabolises it."


# ---------------------------------------------------------------------------
# 1. The control arm is the shipped prompt
# ---------------------------------------------------------------------------

def test_the_control_arm_is_the_shipped_prompt():
    """Behavioural: arm A renders exactly what the extractor renders."""
    assert arm_prompt("A", SAMPLE) == EXTRACTION_PROMPT.format(text=SAMPLE)


def test_the_harness_imports_the_prompt_rather_than_restating_it():
    """AST, not a text search — this file quotes the prompt's own words in
    prose, and a grep-based guard would fire on its own documentation for the
    sixth packet running.
    """
    imported = any(
        isinstance(n, ast.ImportFrom)
        and (n.module or "").startswith("backend.cortex.extraction")
        and any(a.name == "EXTRACTION_PROMPT" for a in n.names)
        for n in ast.walk(TREE))
    assert imported, "the harness does not import the shipped prompt"

    # ...and does not carry a copy. A string literal long enough to be a
    # prompt AND containing the prompt's instruction words is a copy.
    for node in ast.walk(TREE):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if len(v) > 120 and "triples" in v and "subject" in v and "TEXT:" in v:
                pytest.fail(f"a prompt copy is embedded at line {node.lineno}")


def test_the_offered_arms_add_to_the_prompt_without_replacing_it(
        ):
    """B and C must be A plus an instruction. If they diverged elsewhere the
    measurement would be comparing two unrelated prompts."""
    a = arm_prompt("A", SAMPLE)
    for arm in ("B", "C"):
        p = arm_prompt(arm, SAMPLE)
        assert p != a
        # every line of the shipped prompt survives, in order
        for line in [l for l in a.split("\n") if l.strip()]:
            assert line in p, f"arm {arm} dropped {line!r}"
        assert p.index("TEXT:") > p.index(VOCAB[0]), (
            f"arm {arm} puts the vocabulary after the payload, where a long "
            f"chunk buries it")
    assert CONSTRAINT_LINE in arm_prompt("C", SAMPLE)
    assert CONSTRAINT_LINE not in arm_prompt("B", SAMPLE), (
        "arm B is stating the constraint; B and C would measure the same thing")


# ---------------------------------------------------------------------------
# 2. The vocabulary is derived, not invented
# ---------------------------------------------------------------------------

def test_the_vocabulary_is_the_union_of_what_already_exists():
    from backend.cortex.contradiction import CONTRADICTION_PAIRS
    from backend.kb.decompose import _HEADS
    from backend.kb.gate import RELATION_ALIASES, normalise_relation

    expected = {normalise_relation(r) for r in (
        set(_HEADS) | set(RELATION_ALIASES.values())
        | {r for p in CONTRADICTION_PAIRS for r in p}) if r}
    assert set(candidate_vocabulary()) == expected
    assert len(VOCAB) == 33, (
        f"the candidate set is now {len(VOCAB)}; if a source vocabulary grew "
        f"that is fine, but the measurement's baseline moved and the report "
        f"must say so")

    # NON-VACUITY: the three sources genuinely contribute, so this is a union
    # and not one list wearing three names.
    assert "is_part_of" in expected, "decompose._HEADS contributed nothing"
    assert "is_a" in expected, "RELATION_ALIASES contributed nothing"
    assert "inhibits" in expected, "CONTRADICTION_PAIRS contributed nothing"


def test_the_vocabulary_is_built_by_derivation_in_the_source():
    """AST — the set must come from the three sources at runtime. A literal
    tuple of 33 strings would pass the test above on the day it was written
    and silently stop tracking the moment a source vocabulary changed.

    V.1 — the derivation MOVED to `backend/kb/vocabulary.py` when the list
    started shipping, so this follows it there rather than asserting about a
    harness that now delegates. Derived from the function's own module, so it
    follows another move too.
    """
    import backend.kb.vocabulary as vocab

    tree = ast.parse(Path(vocab.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "default_relations")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert {"_HEADS", "RELATION_ALIASES", "CONTRADICTION_PAIRS"} <= names, (
        f"default_relations() no longer derives from all three sources; "
        f"it references {sorted(names)}")


def test_the_harness_and_the_extractor_share_one_definition():
    """V.1 — two copies of the candidate set is how a harness comes to measure
    a prompt nobody sends. Arm A already refuses to restate the shipped
    prompt; this is the same rule for the vocabulary."""
    import backend.kb.vocabulary as vocab
    import scripts.measure_vocabulary_compliance as harness

    assert tuple(harness.VOCAB) == tuple(vocab.load_relations())
    assert harness.VOCAB_BLOCK == vocab.vocabulary_block(harness.VOCAB)
    assert harness.OTHER is vocab.OTHER


# ---------------------------------------------------------------------------
# 3. The near-miss rule is the one that was argued for
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pred,verdict,member", [
    ("is_a",                          "exact", "is_a"),
    ("is a type of",                  "exact", "is_a"),   # alias, via ingest
    ("IS_AN_EXAMPLE_OF",              "exact", "is_a"),
    (OTHER,                           "other", OTHER),
    ("causes_of",                     "near",  "causes"),
    ("is_a_segment_of",               "near",  "is_a"),
    ("primarily_metabolized_by",      "miss",  ""),
    ("is_metabolized_by_primary_enzyme_of", "miss", ""),
    ("",                              "miss",  ""),
])
def test_the_predicate_classifier(pred, verdict, member):
    assert classify_predicate(pred) == (verdict, member)


def test_a_shared_tail_is_not_a_near_miss():
    """THE RULE THAT DECIDES THE RECOMMENDATION.

    `increases` and `decreases` share seven characters. A substring rule would
    call one a near miss for the other — inflating the number that chooses
    "post-extraction mapping" over "state the scope and stop", in the
    direction of doing more work. Token runs, not characters.
    """
    assert classify_predicate("decreases") == ("exact", "decreases")
    verdict, member = classify_predicate("creases")
    assert verdict == "miss", f"'creases' matched {member!r} on a shared tail"


def test_the_two_operator_predicates_are_both_misses():
    """The pair from the investigation — the whole reason this is being
    measured. If either scored as a near miss the harness would be reporting
    that the problem is already half-solved."""
    for pred in ("primarily_metabolized_by",
                 "is_metabolized_by_primary_enzyme_of"):
        assert classify_predicate(pred)[0] == "miss"


# ---------------------------------------------------------------------------
# 4. Quality proxies are the shipped gate, not a private opinion
# ---------------------------------------------------------------------------

def test_quality_metrics_use_the_shipped_gate():
    from backend.cortex.extraction import ExtractedTriple

    good = ExtractedTriple("warfarin", "is_a", "anticoagulant", 0.9)
    bad = ExtractedTriple(
        "the process of converting light into chemical energy in plants",
        "is_a", "photosynthesis", 0.9)
    assert quality_metrics([good])["gate_rejected"] == 0
    assert quality_metrics([bad])["gate_rejected"] == 1, (
        "the gate no longer refuses a sentence-fragment subject; the quality "
        "proxy has stopped proxying anything")
    assert quality_metrics([])["n"] == 0


# ---------------------------------------------------------------------------
# 5. Standing constraints
# ---------------------------------------------------------------------------

def test_the_harness_never_imports_the_server():
    """H.2 — a scratchpad script imported `backend.server` from the repo root
    and migrated the operator's live kb.db. AST, so the sentence above does
    not trip its own guard."""
    for node in ast.walk(TREE):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("backend.server"), (
                f"line {node.lineno} imports backend.server")
        if isinstance(node, ast.Import):
            for a in node.names:
                assert not a.name.startswith("backend.server"), (
                    f"line {node.lineno} imports backend.server")


def test_the_harness_opens_no_store_and_writes_only_a_report():
    """Nothing enters the store — the measurement counts triples, it does not
    persist them. Checked structurally: no store class is imported, and the
    only path it writes is under reports/."""
    imported = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    for forbidden in ("backend.kb.tiered_store", "backend.kb.recalibration",
                      "backend.memory.lineage", "backend.memory.chain",
                      "backend.vector.chroma"):
        assert forbidden not in imported, (
            f"the harness imports {forbidden}; a measurement must not be able "
            f"to write to the substrate it is measuring")

    writes = [n for n in ast.walk(TREE)
              if isinstance(n, ast.Attribute) and n.attr in {"write_text",
                                                             "write_bytes"}]
    assert len(writes) == 1, (
        f"the harness has {len(writes)} write sites; it should have exactly "
        f"one, the report")
