"""Packet V.1 — extraction is offered a relation vocabulary.

MEASURED BEFORE BUILT, which is the point of the packet
    `scripts/measure_vocabulary_compliance.py`, three arms, 20 chunks, a real
    model on the operator's machine:

        arm   triples  distinct  pick  near  other  miss  gate_rej
        A         120       106  0.08  0.12  0.00   0.79      0.38
        B         110        73  0.38  0.09  0.00   0.53      0.51
        C         126        85  0.38  0.12  0.00   0.50      0.32

    Distinct predicates 106 -> 73. Pick rate 0.08 -> 0.38. **Arm B ships**:
    C scored identically, so telling the model that off-list predicates will
    be rejected buys nothing over simply offering the list — and a threat the
    pipeline would then have to carry out is a gate change, not a prompt
    change.

WHAT THIS IS NOT
    Not an admission change. The list is OFFERED. A predicate outside it is
    still normalised and stored exactly as before — `test_an_off_list_
    predicate_is_still_accepted_and_stored` is that claim, and it is the one
    that keeps this a prompt change.

    Not the design, either. OQ-2 (emergent predicate identity) is the
    successor and is blocked on F-04/F-05; the reasoning lives in
    `backend/kb/vocabulary.py`'s docstring so a reader meets it rather than
    assuming a curated list was the intended end state.
"""
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.cortex.extraction import EXTRACTION_PROMPT, extraction_prompt
from backend.kb.granularity import Granularity
from backend.kb.vocabulary import (
    DEFAULT_RELATIONS,
    DEFAULT_VOCABULARY_SOURCE,
    OTHER,
    default_relations,
    load_relations,
    vocabulary_block,
    vocabulary_enabled,
    vocabulary_source,
)
from backend.server import create_app, create_services

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = "Warfarin is a drug. CYP3A4 metabolises it."
TOKEN = "packet-v-token"

# A domain predicate the compiled 33 does not contain — pharmacology, the
# store's actual domain, and exactly the kind of thing V.2 exists to surface.
DOMAIN = ["metabolized_by", "binds_to", "treats"]


def _vocab_file(tmp_path: Path, relations) -> Path:
    p = tmp_path / "relations.json"
    p.write_text(json.dumps(list(relations)), encoding="utf-8")
    return p


def _services(tmp_path):
    return create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots")


# ---------------------------------------------------------------------------
# NON-VACUITY — the fixture vocabulary really differs from the default
# ---------------------------------------------------------------------------

def test_the_domain_predicates_are_not_already_in_the_default():
    """If `metabolized_by` were already compiled in, every assertion about
    loading a file would pass on a list that had not changed."""
    for rel in DOMAIN:
        assert rel not in DEFAULT_RELATIONS, rel
    assert len(DEFAULT_RELATIONS) == 33, (
        f"the compiled default is now {len(DEFAULT_RELATIONS)}; the measured "
        f"0.08 -> 0.38 was taken against 33, so the report must say the "
        f"baseline moved")


# ---------------------------------------------------------------------------
# The prompt carries the list when enabled, and does not when disabled
# ---------------------------------------------------------------------------

def test_the_prompt_carries_the_vocabulary_when_enabled(monkeypatch):
    monkeypatch.delenv("NEXUS_EXTRACTION_VOCABULARY", raising=False)
    monkeypatch.delenv("NEXUS_RELATION_VOCABULARY", raising=False)
    assert vocabulary_enabled() is True

    prompt = extraction_prompt(SAMPLE)
    for rel in DEFAULT_RELATIONS:
        assert rel in prompt, f"{rel} missing from the offered list"
    assert f"`{OTHER}`" in prompt
    assert SAMPLE in prompt


def test_disabling_it_restores_the_pre_v1_prompt_exactly(monkeypatch):
    """REDUCIBILITY (E.2, Q.2, S.2 precedent). Byte-identical, not merely
    similar — a rollback that leaves a trace is not a rollback."""
    monkeypatch.setenv("NEXUS_EXTRACTION_VOCABULARY", "off")
    assert vocabulary_enabled() is False
    assert extraction_prompt(SAMPLE) == EXTRACTION_PROMPT.format(text=SAMPLE)


def test_the_vocabulary_sits_before_the_payload(monkeypatch):
    """Placement is not a preference — it is what the measured arm did. Below
    `TEXT:` a long chunk buries the instruction, and the 0.38 would then be a
    statement about a prompt nobody sent."""
    monkeypatch.delenv("NEXUS_EXTRACTION_VOCABULARY", raising=False)
    prompt = extraction_prompt(SAMPLE)
    assert prompt.index("Use one of these relations") < prompt.index("TEXT:")


def test_the_shipped_prompt_is_arm_b_and_not_arm_c(monkeypatch):
    """C scored identically to B, so the rejection threat buys nothing — and
    the pipeline does not carry it out, so stating it would be false."""
    monkeypatch.delenv("NEXUS_EXTRACTION_VOCABULARY", raising=False)
    prompt = extraction_prompt(SAMPLE).lower()
    assert "rejected" not in prompt, (
        "the shipped prompt threatens rejection; nothing in the pipeline "
        "rejects an off-list predicate, so the prompt would be lying")
    assert "discarded" not in prompt


def test_the_extractor_sends_the_prompt_this_module_builds():
    """AST. A second prompt construction inside `extract_triples` would make
    every assertion here about a function nobody calls."""
    import backend.cortex.extraction as ext

    tree = ast.parse(Path(ext.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "extract_triples")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "extraction_prompt" in called, (
        "extract_triples() builds its prompt some other way")
    formats = [n for n in ast.walk(fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "format"]
    assert not formats, (
        f"extract_triples() formats a prompt directly at line "
        f"{formats[0].lineno}; the vocabulary switch would not apply to it")


# ---------------------------------------------------------------------------
# The vocabulary is configuration — T.2's pattern
# ---------------------------------------------------------------------------

def test_the_default_is_unchanged_when_nothing_is_installed(monkeypatch):
    monkeypatch.delenv("NEXUS_RELATION_VOCABULARY", raising=False)
    assert load_relations() == DEFAULT_RELATIONS
    assert vocabulary_source()["source"] == DEFAULT_VOCABULARY_SOURCE
    assert vocabulary_source()["error"] == ""


def test_a_file_replaces_the_default_and_reaches_the_prompt(tmp_path, monkeypatch):
    """BOTH DIRECTIONS. The installed list appears; a default the file omits
    does not — otherwise the two are being merged rather than replaced."""
    monkeypatch.delenv("NEXUS_EXTRACTION_VOCABULARY", raising=False)
    monkeypatch.setenv("NEXUS_RELATION_VOCABULARY",
                       str(_vocab_file(tmp_path, DOMAIN)))

    assert load_relations() == tuple(DOMAIN)
    prompt = extraction_prompt(SAMPLE)
    for rel in DOMAIN:
        assert rel in prompt
    assert "is_composed_of" not in prompt, (
        "a compiled default survived an installed file; the lists are being "
        "merged, not replaced")


def test_an_installed_file_is_normalised_and_deduped(tmp_path, monkeypatch):
    """The file is written by a human. `Metabolized By` must match stored
    relations, which are slugged on ingest by the same normaliser."""
    monkeypatch.setenv("NEXUS_RELATION_VOCABULARY", str(_vocab_file(
        tmp_path, ["Metabolized By", "metabolized_by", "BINDS TO"])))
    assert load_relations() == ("metabolized_by", "binds_to")


def test_the_escape_hatch_cannot_be_installed_as_a_relation(tmp_path, monkeypatch):
    """`other` is the hatch, not a predicate. A file listing it would offer it
    twice and make the miss/other split unreadable — which is the one number
    that says whether the model knows it is off the list."""
    monkeypatch.setenv("NEXUS_RELATION_VOCABULARY",
                       str(_vocab_file(tmp_path, ["treats", OTHER])))
    assert load_relations() == ("treats",)
    assert vocabulary_block().count(f"`{OTHER}`") == 1


@pytest.mark.parametrize("content,why", [
    ("not json at all",            "unparseable"),
    ('{"a": "b"}',                 "an object, not an array"),
    ("[]",                         "empty"),
    ("[1, 2, 3]",                  "not strings"),
    ('[""]',                       "empty string"),
])
def test_a_bad_vocabulary_file_warns_and_falls_back(
        tmp_path, monkeypatch, caplog, content, why):
    """DECLARED, NOT SILENT (I-6) — T.2's handling, unchanged. A malformed
    file must not leave the extractor with an empty list and must not stop the
    app, but it must not pass unremarked either."""
    p = tmp_path / "bad.json"
    p.write_text(content, encoding="utf-8")
    monkeypatch.setenv("NEXUS_RELATION_VOCABULARY", str(p))
    with caplog.at_level(logging.WARNING, logger="nexus.vocabulary"):
        loaded = load_relations()
    assert loaded == DEFAULT_RELATIONS, f"{why}: did not fall back"
    assert vocabulary_source()["source"] == DEFAULT_VOCABULARY_SOURCE
    assert vocabulary_source()["error"], f"{why}: fell back silently"
    assert caplog.records, f"{why}: no warning logged"


def test_a_partly_malformed_file_keeps_the_good_entries_and_says_so(
        tmp_path, monkeypatch):
    p = tmp_path / "mixed.json"
    p.write_text(json.dumps(["treats", 7, ""]), encoding="utf-8")
    monkeypatch.setenv("NEXUS_RELATION_VOCABULARY", str(p))
    assert load_relations() == ("treats",)
    assert "2 malformed" in vocabulary_source()["error"]


def test_cortex_stats_reports_the_vocabulary_in_force(tmp_path, monkeypatch):
    """I-6. An operator who installed sixty predicates and is silently running
    on the compiled thirty-three has no other way to find out."""
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_RELATION_VOCABULARY",
                       str(_vocab_file(tmp_path, DOMAIN)))
    with TestClient(create_app(_services(tmp_path))) as client:
        body = client.get("/cortex/stats").json()["relation_vocabulary"]

    assert body["enabled"] is True
    assert body["count"] == 3
    assert body["relations"] == DOMAIN
    assert body["source"].endswith("relations.json")
    assert body["error"] == ""


def test_cortex_stats_shows_the_fallback_after_a_bad_file(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    monkeypatch.setenv("NEXUS_RELATION_VOCABULARY", str(bad))
    with TestClient(create_app(_services(tmp_path))) as client:
        body = client.get("/cortex/stats").json()["relation_vocabulary"]

    assert body["count"] == len(DEFAULT_RELATIONS)
    assert body["source"] == DEFAULT_VOCABULARY_SOURCE
    assert body["error"], "the fallback is not declared"


# ---------------------------------------------------------------------------
# It is an OFFER, not an admission rule
# ---------------------------------------------------------------------------

def test_an_off_list_predicate_is_still_accepted_and_stored(tmp_path):
    """THE CLAIM THAT KEEPS THIS A PROMPT CHANGE.

    NON-VACUITY: the predicate is asserted absent from the vocabulary first,
    so this cannot pass on a relation that happens to be on the list.
    """
    assert "primarily_metabolized_by" not in load_relations()

    services = _services(tmp_path)
    bid = services.store.add(
        subject="TAK-441", relation="primarily_metabolized_by",
        object="CYP3A4", weight=0.6, tier=2,
        granularity=Granularity.TOPIC, source="extraction")
    stored = services.store.get_by_id(bid)
    assert stored is not None, "an off-list predicate was refused"
    assert stored.relation == "primarily_metabolized_by", (
        "an off-list predicate was rewritten to something on the list — that "
        "would be sanitising, which K.5 ruled against")


def test_the_gate_has_no_vocabulary_rule():
    """AST. The offer must not become an admission rule by accident: a gate
    that imported the vocabulary would turn V.1 into a change of what may be
    stored, which is a different packet and a different argument."""
    import backend.kb.gate as gate

    tree = ast.parse(Path(gate.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "vocabulary" not in (node.module or ""), (
                f"gate.py imports the vocabulary at line {node.lineno}; the "
                f"list is offered, never enforced")


# ---------------------------------------------------------------------------
# One definition, three sources, and the one thing that must NOT be tunable
# ---------------------------------------------------------------------------

def test_the_default_is_derived_from_all_three_existing_vocabularies():
    from backend.cortex.contradiction import CONTRADICTION_PAIRS
    from backend.kb.decompose import _HEADS
    from backend.kb.gate import RELATION_ALIASES, normalise_relation

    expected = {normalise_relation(r) for r in (
        set(_HEADS) | set(RELATION_ALIASES.values())
        | {r for p in CONTRADICTION_PAIRS for r in p}) if r}
    assert set(default_relations()) == expected
    # NON-VACUITY: all three genuinely contribute.
    assert "is_part_of" in expected, "_HEADS contributed nothing"
    assert "is_a" in expected, "RELATION_ALIASES contributed nothing"
    assert "inhibits" in expected, "CONTRADICTION_PAIRS contributed nothing"


def test_installing_a_vocabulary_does_not_change_how_sentences_are_split(
        tmp_path, monkeypatch):
    """THE SEPARATION THE PACKET ASKED FOR, asserted rather than commented.

    `decompose._HEADS` is parsing machinery about English sentence structure.
    A customer editing their domain ontology must not change how a predicate
    is split — so the tunable list and the heads are different things, even
    though the DEFAULT list is derived from the heads.
    """
    from backend.kb.decompose import _HEADS, decompose

    before_heads = list(_HEADS)
    before = decompose("BBC", "is_a_reliable_news_source", None)
    assert before, "the fixture no longer decomposes; pick another example"

    monkeypatch.setenv("NEXUS_RELATION_VOCABULARY",
                       str(_vocab_file(tmp_path, ["treats"])))
    assert load_relations() == ("treats",)

    assert list(_HEADS) == before_heads, "installing a vocabulary edited _HEADS"
    after = decompose("BBC", "is_a_reliable_news_source", None)
    assert [(d.subject, d.relation, d.object) for d in after] == \
           [(d.subject, d.relation, d.object) for d in before], (
        "installing a domain vocabulary changed how a sentence is split")


def test_the_module_records_why_a_list_is_scaffolding():
    """OQ-2, in the code rather than only in a packet. A future reader must
    meet the reasoning — a curated list is not the intended end state, and
    emergent predicate identity is blocked on F-04/F-05."""
    import backend.kb.vocabulary as vocab

    doc = (vocab.__doc__ or "")
    assert "OQ-2" in doc
    assert "F-04" in doc or "F-05" in doc
    assert "scaffolding" in doc.lower() or "not the intended end state" in doc.lower()
