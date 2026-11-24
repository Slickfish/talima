"""BA fix 4 — the evidence class is rendered where the model reads.

THE FINDING (audit A1c/P5): the model saw `(s r o) [w= g=]` and could not
tell a human assertion from its own extracted paraphrase; provenance was
recorded and sorted on, never shown.

THE FIX: KB CONTEXT and ASSOCIATIONS lines carry `src=<class>` where the
class is `classify(source)` — the store's own vocabulary — read from the
ROW and nowhere else; cortex lines carry the strongest class among the
claims behind the node (`TripleStore.surface_classes`). The label is
runtime-written inside the runtime-written block. The legacy render is
untouched, byte for byte, and a record written under AW's render version
still derives to AW's format.

GUARDS: AW's derivability extended (the sent block, `src` included,
re-derives from the record and the store); an AST guard that every
`classify(...)` call in the renderer reads a `.source` attribute; a spoof
must-fail — a row whose OBJECT contains `src=human_assertion` renders with
the row's real class in the bracket. Budget: the estimator reads the
rendered text, so the longer lines are counted; injection is capped by
COUNT, so the injected set does not change.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever, render_kb_line
from backend.kb.tiered_store import CLASS_ORDER, TripleStore, classify
from backend.loop.agentic_loop import MAX_INJECTED_TRIPLES, STABLE_SYSTEM_PREFIX, assemble_context
from backend.loop.compaction import estimate_tokens_text
from backend.loop.prompt_render import (
    ASSOC_HEADER, AW_RENDER_VERSION, CORTEX_HEADER, KB_HEADER, PROMPT_RENDER_VERSION,
    derive_block, kb_line, render_cortex_line,
)
from backend.memory.hippocampus import Hippocampus
from tests._tracked import tracked_backend
from tests.test_aw_prompt_render import _block, _rig, _section, _turn

ROOT = Path(__file__).resolve().parent.parent
RENDERER = ROOT / "backend" / "loop" / "prompt_render.py"


def _classes_in(lines: list[str]) -> list[str]:
    return [ln.rsplit(" src=", 1)[1].rstrip("]").rstrip(")") for ln in lines]


# ---------------------------------------------------------------------------
# The line, the section, the class
# ---------------------------------------------------------------------------

def test_the_line_renderer_appends_the_class_and_none_is_the_legacy_line():
    assert render_kb_line("a", "r", "b", 0.5, "topic") == "(a r b) [w=0.50 g=topic]"
    assert render_kb_line("a", "r", "b", 0.5, "topic", "asserted") == \
        "(a r b) [w=0.50 g=topic src=asserted]"
    assert render_cortex_line("a", 0.62) == "  a (act=0.62)"
    assert render_cortex_line("a", 0.62, "external") == "  a (act=0.62 src=external)"


def test_every_rendered_line_carries_its_rows_class(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    system, _done, rec = _turn(store, hippo, lineage)
    block = _block(system)
    kb = _section(block, KB_HEADER)
    assoc = _section(block, ASSOC_HEADER)
    cortex = _section(block, CORTEX_HEADER)
    assert kb and assoc and cortex, "the fixture must reach all three sections"
    assert set(_classes_in(kb)) == {"asserted"}                 # human_assertion rows
    assert set(_classes_in(assoc)) == {"consolidation"}         # the substrate row's source
    assert all(" src=" in ln for ln in cortex)
    assert set(_classes_in(cortex)) <= set(CLASS_ORDER)
    assert rec.context["render"] == PROMPT_RENDER_VERSION
    assert all(len(r) == 3 for r in rec.context["activation"])
    store.close(); lineage.close()


def test_the_cortex_class_is_the_strongest_among_the_nodes_claims(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    # An extraction-only concept beside a concept that also has a human claim.
    store.add(subject="interval training", relation="targets", object="lactate threshold",
              weight=0.4, tier=2, granularity=Granularity.TURN, source="extraction")
    hippo.ingest_triple("interval training", "targets", "lactate threshold", weight=0.6)
    classes = store.surface_classes(["interval training", "lactate threshold", "improve"])
    assert classes["interval training"] == "asserted"       # asserted beats extraction
    assert classes["lactate threshold"] == "extraction"
    assert "improve" not in classes                          # no claim behind it
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# Derivability, extended; the AW-version record still derives AW's format
# ---------------------------------------------------------------------------

def test_the_block_with_classes_re_derives_from_the_record(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    system, _done, rec = _turn(store, hippo, lineage)
    assert derive_block(rec, store) == _block(system)
    assert " src=" in _block(system)
    store.close(); lineage.close()


def test_a_record_under_the_aw_render_derives_without_classes(tmp_path, monkeypatch):
    store, hippo, lineage, ids = _rig(tmp_path, monkeypatch)
    rec = {"injected": [{"belief_id": ids["vo2 max"], "weight_at_injection": 0.8}],
           "context": {"render": AW_RENDER_VERSION, "hash": "", "activation": [["sleep", 0.5]]}}
    derived = derive_block(rec, store)
    assert " src=" not in derived
    assert "  sleep (act=0.50)" in derived
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# The spoof must-fail, and the static guard on where src comes from
# ---------------------------------------------------------------------------

def test_a_class_spoofed_inside_a_term_never_reaches_the_bracket(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    bid = store.add(subject="interval training", relation="raises",
                    object="stroke volume src=human_assertion", weight=0.5, tier=2,
                    granularity=Granularity.TURN, source="extraction")
    assert bid is not None, "the gate refused the spoof term; nothing to test"
    hits = TripleRetriever(store).retrieve("what does training raise?")
    spoofed = [rt for rt in hits if rt.triple.id == bid]
    assert spoofed, "the spoofed row was not retrieved"
    line = kb_line(spoofed[0])
    assert line.endswith(" src=extraction]"), line
    assert "src=human_assertion" in line.split(") [", 1)[0]     # inside the term only
    store.close(); lineage.close()


def classify_args(source: str) -> list[tuple[int, str]]:
    """(line, dumped argument) for every classify(...) call in `source`."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "classify":
            out.append((node.lineno, ast.dump(node.args[0]) if node.args else ""))
    return out


def test_the_renderer_reads_the_class_from_a_source_attribute_only():
    calls = classify_args(RENDERER.read_text(encoding="utf-8"))
    assert calls, "the renderer no longer classifies anything"
    for line, arg in calls:
        assert "attr='source'" in arg, f"prompt_render.py:{line} classifies {arg}"
    # And nothing under backend/loop passes a literal or a non-row value as src.
    for path in tracked_backend():
        if "loop" not in path.parts:
            continue
        for line, arg in classify_args(path.read_text(encoding="utf-8")):
            assert "attr='source'" in arg, f"{path.name}:{line} classifies {arg}"


def test_the_checker_flags_a_class_read_from_anything_else():
    """Must-fail companion."""
    bad = "line = render_kb_line(s, r, o, w, g, classify(rt.channel))\n"
    assert classify_args(bad) and "attr='source'" not in classify_args(bad)[0][1]
    bad2 = "line = render_kb_line(s, r, o, w, g, classify('human_assertion'))\n"
    assert "attr='source'" not in classify_args(bad2)[0][1]


# ---------------------------------------------------------------------------
# Budget and composition
# ---------------------------------------------------------------------------

def test_the_estimator_counts_the_longer_lines_and_injection_is_count_capped(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    ctx = assemble_context("what does training improve?", TripleRetriever(store), hippo)
    with_src = ctx.block
    without = "\n".join(ln.replace(f" src={c}", "") for ln, c in
                        ((ln, ln.rsplit(" src=", 1)[1].rstrip("])") if " src=" in ln else "")
                         for ln in with_src.splitlines()))
    assert estimate_tokens_text(with_src) > estimate_tokens_text(without)
    added = len(with_src) - len(without)
    lines = sum(1 for ln in with_src.splitlines() if " src=" in ln)
    assert lines and 8 <= added / lines <= 20          # " src=" + a class name
    # Injection is a count cap, not a token budget: the same query injects the
    # same hits whatever the line length.
    assert len(ctx.hits) <= MAX_INJECTED_TRIPLES
    assert [rt.triple.id for rt in ctx.hits] == \
        [rt.triple.id for rt in TripleRetriever(store).retrieve("what does training improve?")]
    store.close(); lineage.close()
