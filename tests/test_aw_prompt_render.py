"""AW — the prompt render: sentinel first, then narrow, then record.

THE DEFECT, read verbatim from a real turn's system message:

    [CORTEX ACTIVATION — top firing nodes with epistemic state]
      strength training [partial knowledge — use with caution] (act=1.00, col=fitness)
      improve [partial knowledge — use with caution] (act=1.00, col=general)
    (training co_activated recovery) [w=0.36 g=topic]        <- in the KB list

  - the tag and the weight are artefacts (never written after creation,
    never persisted), and they contradict the stable prefix's "use it as
    what you know" on every turn;
  - "improve" is the question's own verb, rendered as a half-known concept
    because every query token becomes a node and seeds fire at 1.00;
  - the substrate row sits in the ranked KB list as if it were a claim;
  - and the lineage record hashed the STABLE prefix only, so the cortex
    half of what the model was told was derivable from nothing recorded.

THE SENTINEL, written before the change: re-derive every line of the
dynamic block from the lineage record and the store, and compare it with
the block the model was sent. It failed on the cortex section before the
narrowing and the recording — the right reason — and is green after.

ORDER MATTERS, and the packet set it: narrowing before recording, because
recording the old block would have sealed an artefact into the series.
`NEXUS_PROMPT_RENDER=legacy` is the rollback and produces the pre-AW block
byte for byte; the record is written in both positions and names which.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from backend.cortex.edges import SynapticEdge
from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.schema import SUBSTRATE_RELATIONS
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import STABLE_SYSTEM_PREFIX, assemble_context, run_turn
from backend.loop.prompt_render import (
    ASSOC_HEADER, CORTEX_HEADER, KB_HEADER, LEGACY_CORTEX_HEADER, LEGACY_RENDER,
    PROMPT_RENDER_VERSION, context_hash, derive_block, prompt_render_mode,
)
from backend.loop.state import NexusLoopState
from backend.memory.hippocampus import Hippocampus
from backend.memory.lineage import LineageStore
from tests._tracked import tracked_backend

ROOT = Path(__file__).resolve().parent.parent
QUERY = "what does training improve?"
ANSWER = "Interval training improves vo2 max."
RENDER_PHRASES = ("partial knowledge", "use with caution", "evidential weight",
                  "knowledge gap", "inferred by association", "contradictory evidence")


class _Capture:
    """Answers once, extracts nothing, and keeps the messages it was sent."""

    def __init__(self, answer: str = ANSWER):
        self.answer, self.calls, self.messages = answer, 0, None

    async def stream_chat(self, model, messages, num_ctx):
        self.calls += 1
        if self.calls == 1:
            self.messages = messages
            for tok in self.answer.split(" "):
                yield tok + " "
        else:
            yield "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _rig(tmp_path, monkeypatch):
    """A store and graph where every defect the packet names is REACHABLE:

    - a substrate row retrievable by the query term ("training"), so it
      lands in the hits beside the claims;
    - a query token ("improve") that is not a claim concept, so it becomes
      a node and fires at 1.00;
    - an association edge to a claim concept the query does not retrieve
      ("sleep"), so the cortex section carries something the KB half does
      not — the derivation cannot pass by re-reading `injected` alone.
    """
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "calibration.jsonl"))
    monkeypatch.delenv("NEXUS_PROMPT_RENDER", raising=False)
    kb = tmp_path / "kb.db"
    store = TripleStore(kb)
    ids = {}
    for s, r, o, w, t, src in [
        ("interval training", "improves", "vo2 max", 0.8, 1, "human_assertion"),
        ("interval training", "reduces", "resting heart rate", 0.7, 1, "human_assertion"),
        ("sleep", "improves", "recovery", 0.9, 1, "human_assertion"),
    ]:
        ids[o] = store.add(subject=s, relation=r, object=o, weight=w, tier=t,
                           granularity=Granularity.TOPIC, source=src)
    monkeypatch.setenv("NEXUS_INGEST_GATE", "off")
    ids["substrate"] = store.add(subject="training", relation="co_activated",
                                 object="recovery", weight=0.4, tier=3,
                                 granularity=Granularity.TOPIC,
                                 source="hippocampus_consolidation")
    monkeypatch.delenv("NEXUS_INGEST_GATE", raising=False)
    hippo = Hippocampus()
    hippo.ingest_triple("interval training", "improves", "vo2 max", weight=0.9)
    hippo.ingest_triple("interval training", "reduces", "resting heart rate", weight=0.6)
    hippo.ingest_triple("sleep", "improves", "recovery", weight=0.8)
    hippo.add_edge(SynapticEdge(source="interval training", target="sleep", weight=0.9))
    lineage = LineageStore(kb)
    return store, hippo, lineage, ids


def _turn(store, hippo, lineage, query=QUERY):
    """One real turn. Returns (system message, done payload, lineage record)."""
    prov = _Capture()
    frames = []

    async def drive():
        async for frame, _state in run_turn(
                NexusLoopState(), query, retriever=TripleRetriever(store),
                hippocampus=hippo, provider=prov, lineage=lineage):
            frames.append(frame)

    asyncio.run(drive())
    done = next(json.loads(f.split("data: ", 1)[1].strip())
                for f in reversed(frames) if f.startswith("event: done\n"))
    rec = lineage.get(done["lineage_id"])
    assert rec is not None, "no lineage record for the turn"
    return prov.messages[0]["content"], done, rec


def _block(system: str) -> str:
    assert system.startswith(STABLE_SYSTEM_PREFIX), "Law VII: prefix not stable"
    return system[len(STABLE_SYSTEM_PREFIX):]


def _section(block: str, header: str) -> list[str]:
    """Lines of one section, or [] when the header is absent."""
    for part in block.split("\n\n"):
        if part.startswith(header + "\n"):
            return part.split("\n")[1:]
    return []


# ---------------------------------------------------------------------------
# THE SENTINEL — written first, failed on the cortex section, green after
# ---------------------------------------------------------------------------

def test_sentinel_every_line_of_the_block_derives_from_the_record(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    system, _done, rec = _turn(store, hippo, lineage)
    block = _block(system)
    assert block, "the fixture produced an empty block — nothing was tested"
    derived = derive_block(rec, store)
    assert derived == block, (
        "the model was sent something the record cannot re-derive:\n"
        f"--- sent ---\n{block}\n--- derived ---\n{derived}")
    assert rec.context is not None and rec.context["hash"] == context_hash(block)
    store.close(); lineage.close()


def test_non_vacuity_the_legacy_block_carried_every_defect_and_did_not_derive(
        tmp_path, monkeypatch):
    """Proves the fixture reaches all three defects — the sentinel would have
    failed before the change on THIS block, not on an empty one."""
    store, hippo, lineage, ids = _rig(tmp_path, monkeypatch)
    monkeypatch.setenv("NEXUS_PROMPT_RENDER", "legacy")
    system, _done, rec = _turn(store, hippo, lineage)
    block = _block(system)
    kb = _section(block, KB_HEADER)
    cortex = _section(block, LEGACY_CORTEX_HEADER)
    assert any(" co_activated " in ln for ln in kb), "no substrate row in the KB list"
    assert any(ln.startswith("  improve ") for ln in cortex), "query token not rendered"
    assert any(ph in block for ph in RENDER_PHRASES), "no epistemic tag rendered"
    assert "col=" in block
    assert derive_block(rec, store) != block, (
        "the legacy block derived from the record — the sentinel proves nothing")
    assert rec.context["render"] == LEGACY_RENDER
    assert rec.context["hash"] == context_hash(block), "legacy turns are recorded too"
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# The narrowing, each defect pinned by the fixture that reaches it
# ---------------------------------------------------------------------------

def test_no_substrate_row_in_the_rendered_kb_half(tmp_path, monkeypatch):
    store, hippo, lineage, ids = _rig(tmp_path, monkeypatch)
    system, _done, rec = _turn(store, hippo, lineage)
    block = _block(system)
    kb = _section(block, KB_HEADER)
    assoc = _section(block, ASSOC_HEADER)
    assert kb, "the KB half is empty"
    assert not any(f" {rel} " in ln for ln in kb for rel in SUBSTRATE_RELATIONS)
    assert any(" co_activated " in ln for ln in assoc), (
        "the substrate row vanished instead of moving under its own header")
    # Still INJECTED: the record's list is the hits, not the claims subset.
    assert ids["substrate"] in {it["belief_id"] for it in rec.injected}
    store.close(); lineage.close()


def test_no_query_only_token_in_the_cortex_half(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    system, _done, rec = _turn(store, hippo, lineage)
    cortex = _section(_block(system), CORTEX_HEADER)
    assert cortex, "the cortex half is empty"
    nodes = [ln.strip().rsplit(" (act=", 1)[0] for ln in cortex]
    assert "improve" in hippo.nodes, "the query token never became a node"
    assert "improve" not in nodes
    with_claims = store.surfaces_with_claims(nodes, exclude_relations=SUBSTRATE_RELATIONS)
    assert set(nodes) <= with_claims, f"rendered without a claim: {set(nodes) - with_claims}"
    # Claim-based, not hit-based: reached by association, never retrieved.
    assert "sleep" in nodes
    assert "sleep" not in {ln.split(" ")[0].lstrip("(") for ln in _section(_block(system), KB_HEADER)}
    store.close(); lineage.close()


def test_no_tag_weight_or_column_in_the_narrow_block(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    system, _done, _rec = _turn(store, hippo, lineage)
    block = _block(system)
    assert not any(ph in block for ph in RENDER_PHRASES)
    assert "col=" not in block and "evidential" not in block
    assert LEGACY_CORTEX_HEADER not in block
    store.close(); lineage.close()


def test_the_block_is_absent_when_retrieval_missed_and_the_miss_is_recorded(
        tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    system, _done, rec = _turn(store, hippo, lineage, query="tell me about jupiter")
    assert _block(system) == ""
    assert rec.context["hash"] == context_hash("")
    assert rec.context["activation"] == []
    assert derive_block(rec, store) == ""
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

def test_system_message_is_prefix_plus_block_and_the_block_is_recorded(
        tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    system, done, rec = _turn(store, hippo, lineage)
    assert system == STABLE_SYSTEM_PREFIX + _block(system)
    assert rec.context["render"] == PROMPT_RENDER_VERSION
    assert rec.context["hash"] == context_hash(_block(system))
    assert rec.context["activation"], "nothing recorded for a non-empty cortex half"
    # BA fix 4 widened each entry to [concept, activation, class].
    assert all(isinstance(r[0], str) and isinstance(r[1], float)
               for r in rec.context["activation"])
    # Outside the hashed body: the sealed claim is unchanged by the addition.
    assert lineage.verify_record(done["lineage_id"])["record_intact"] is True
    assert rec.to_dict()["context"] == rec.context
    store.close(); lineage.close()


def test_migration_is_additive_on_a_used_store(tmp_path, monkeypatch):
    """`_used_store()` — a store an hour of use left behind, with a SEALED
    pre-AW record. The column is added, the old record reads no context,
    its hash still verifies, and a new turn on the same store derives."""
    from backend.memory.graph_store import HebbianGraphStore
    from tests.test_restart_with_state import _used_store
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "calibration.jsonl"))
    monkeypatch.delenv("NEXUS_PROMPT_RENDER", raising=False)
    used = _used_store(tmp_path)
    lineage = LineageStore(used["kb"])
    old = lineage.get(used["lineage_id"])
    assert old.context is None
    assert lineage.verify_record(used["lineage_id"])["record_intact"] is True
    assert old.sealed_block is not None
    store = TripleStore(used["kb"])
    # A pre-AW record derives its KB half only — exactly what was derivable then.
    assert derive_block(old, store).startswith("\n\n" + KB_HEADER)
    assert CORTEX_HEADER not in derive_block(old, store)

    hippo = Hippocampus()
    assert HebbianGraphStore(used["kb"]).load_into(hippo) > 0
    system, _done, rec = _turn(store, hippo, lineage, query="what is the QT interval?")
    assert _block(system), "the used store produced no block"
    assert derive_block(rec, store) == _block(system)
    store.close(); lineage.close()


def test_a_record_without_context_derives_its_kb_half_only(tmp_path, monkeypatch):
    store, hippo, lineage, ids = _rig(tmp_path, monkeypatch)
    rec = {"injected": [{"belief_id": ids["vo2 max"], "weight_at_injection": 0.8},
                        {"belief_id": ids["substrate"], "weight_at_injection": 0.4}]}
    derived = derive_block(rec, store)
    assert KB_HEADER in derived and ASSOC_HEADER in derived and CORTEX_HEADER not in derived
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# Reducibility — legacy is the pre-AW block byte for byte
# ---------------------------------------------------------------------------

def test_legacy_is_the_pre_aw_block_byte_for_byte(tmp_path, monkeypatch):
    """The reference is the OLD f-string, kept here rather than imported, so
    the rollback is compared against what shipped and not against itself."""
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    monkeypatch.setenv("NEXUS_PROMPT_RENDER", "legacy")
    assert prompt_render_mode() == "legacy"
    ctx = assemble_context(QUERY, TripleRetriever(store), hippo)
    tagged = [f"  {nid} {hippo.get_node(nid).epistemic_state.render(confidence=hippo.get_node(nid).confidence)} "
              f"(act={act:.2f}, col={hippo.get_node(nid).column})"
              for nid, act in ctx.activation.top_k(10)]
    expected = ("\n\n" + KB_HEADER + "\n" + "\n".join(rt.render() for rt in ctx.hits)
                + "\n\n" + LEGACY_CORTEX_HEADER + "\n" + "\n".join(tagged))
    assert ctx.block == expected
    assert ctx.rendered_activation == ()
    store.close(); lineage.close()


def test_default_is_narrow_and_unknown_values_are_not_legacy(monkeypatch):
    monkeypatch.delenv("NEXUS_PROMPT_RENDER", raising=False)
    assert prompt_render_mode() == "narrow"
    monkeypatch.setenv("NEXUS_PROMPT_RENDER", "LEGACY ")
    assert prompt_render_mode() == "legacy"
    monkeypatch.setenv("NEXUS_PROMPT_RENDER", "wide")
    assert prompt_render_mode() == "narrow"


# ---------------------------------------------------------------------------
# The guard — derived from the call sites, AST, tracked files
# ---------------------------------------------------------------------------

def _record_turn_calls() -> list[tuple[str, int, bool]]:
    out = []
    for path in tracked_backend():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "record_turn":
                out.append((path.relative_to(ROOT).as_posix(), node.lineno,
                            any(kw.arg == "context" for kw in node.keywords)))
    return out


def test_every_record_turn_call_site_records_the_context():
    calls = _record_turn_calls()
    assert calls, "derivation found no record_turn call site"
    missing = [(f, ln) for f, ln, ok in calls if not ok]
    assert not missing, f"record_turn called without context= at {missing}"


def test_the_guard_population_is_not_empty():
    assert any(f.endswith("agentic_loop.py") for f, _ln, _ok in _record_turn_calls())
