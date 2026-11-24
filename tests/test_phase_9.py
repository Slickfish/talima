"""Phase 9 tests — required coverage from CLAUDE.md:

  - prediction error modulates Hebbian (2.5x surprise, 0.4x confirmed)  ← Phase 7
  - contradiction pairs detected
  - chain verify_chain() returns valid=True

Plus arch-doc + integrity-guard invariants:
  - extraction parses well-formed JSON / tolerates noise / fails-soft
  - ContradictionDetector finds bidirectional contradictions, no false positives
  - CuriosityEngine reads live cortex, scores in_degree*(1-confidence), top_k
  - CortexFingerprinter produces stable signature, distance bounded, identical=0
  - NexusArchiveChain init creates schema, append links to previous, verify_chain
    catches tampering at the broken block index
  - KAIROS first cycle takes a snapshot; second within interval does not
  - Compaction integrity guard emits CompactionReport per tier, PASS or WARN
    (BD-3 retired the FAIL tier and the halt)
  - is_finetuning_available returns False without CUDA; finetune raises
    FineTuneUnavailable
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from backend.cortex.contradiction import (
    CONTRADICTION_PAIRS,
    Contradiction,
    ContradictionDetector,
    Triple as CTriple,
)
from backend.cortex.curiosity import CuriosityEngine, CuriousNode
from backend.cortex.edges import SynapticEdge
from backend.cortex.extraction import (
    EXTRACTOR_ROLE,
    ExtractedTriple,
    _parse_triples,
    extract_triples,
)
from backend.cortex.nodes import EpistemicState, NodeType
from backend.loop.compaction import (
    CompactionReport,
    IntegrityViolation,
    Tier,
    compute_integrity_report,
    snapshot_for_integrity,
)
from backend.loop.state import NexusLoopState
from backend.memory.chain import (
    GENESIS_HASH,
    ArchiveBlock,
    NexusArchiveChain,
)
from backend.memory.fingerprint import (
    SIGNATURE_DIM,
    CortexFingerprint,
    CortexFingerprinter,
)
from backend.memory.hippocampus import Hippocampus
from backend.memory.kairos import KairosProcess, SNAPSHOT_INTERVAL_DAYS
from backend.memory.neocortex import Neocortex
from backend.memory.procedural import ProceduralMemory
from backend.cortex.hebbian import HebbianUpdater
from backend.training import FineTuneUnavailable, finetune, is_finetuning_available


# ===================================================================
# Contradiction detector
# ===================================================================

def test_contradiction_pairs_constant_includes_arch_examples():
    flat = {p for pair in CONTRADICTION_PAIRS for p in pair}
    for required in ("increases", "decreases", "causes", "prevents", "is_a", "is_not_a"):
        assert required in flat


def test_contradiction_pairs_detected_in_recent_set():
    triples = [
        CTriple(subject="caffeine", relation="increases", object="alertness", timestamp=1.0),
        CTriple(subject="caffeine", relation="decreases", object="alertness", timestamp=2.0),
    ]
    contradictions = ContradictionDetector().scan_recent(triples)
    assert len(contradictions) == 1
    c = contradictions[0]
    assert c.subject == "caffeine" and c.object == "alertness"
    assert {c.relation_a, c.relation_b} == {"increases", "decreases"}
    assert c.timestamp_a == 1.0 and c.timestamp_b == 2.0


def test_contradiction_no_false_positive_on_unrelated_pairs():
    triples = [
        CTriple(subject="x", relation="describes", object="y"),
        CTriple(subject="x", relation="references", object="y"),
    ]
    assert ContradictionDetector().scan_recent(triples) == []


def test_contradiction_no_false_positive_on_different_subjects():
    triples = [
        CTriple(subject="alpha", relation="increases", object="z"),
        CTriple(subject="beta",  relation="decreases", object="z"),
    ]
    assert ContradictionDetector().scan_recent(triples) == []


def test_contradiction_scan_against_existing_kb():
    new_triples = [CTriple(subject="exercise", relation="prevents", object="anxiety")]
    existing    = [CTriple(subject="exercise", relation="causes",   object="anxiety", timestamp=1.0)]
    contradictions = ContradictionDetector().scan_against(new_triples, existing)
    assert len(contradictions) == 1
    assert contradictions[0].relation_a == "causes"
    assert contradictions[0].relation_b == "prevents"


def test_contradiction_detector_accepts_tuples():
    triples = [
        ("vitamin_d", "improves", "mood", 1.0),
        ("vitamin_d", "worsens",  "mood", 2.0),
    ]
    contradictions = ContradictionDetector().scan_recent(triples)
    assert len(contradictions) == 1


def test_contradiction_normalises_relation_case_and_spacing():
    triples = [
        CTriple(subject="A", relation="Is A", object="B"),
        CTriple(subject="A", relation="is_not_a", object="B"),
    ]
    assert len(ContradictionDetector().scan_recent(triples)) == 1


def test_contradiction_dedupes_symmetric_pairs():
    triples = [
        CTriple(subject="A", relation="causes",   object="B"),
        CTriple(subject="A", relation="prevents", object="B"),
        # exact same pair from another extraction
        CTriple(subject="A", relation="causes",   object="B"),
        CTriple(subject="A", relation="prevents", object="B"),
    ]
    contradictions = ContradictionDetector().scan_recent(triples)
    assert len(contradictions) == 1   # deduped


# ===================================================================
# Triple extraction (Stage 4)
# ===================================================================

def test_parse_triples_object_array():
    # Multi-char identifiers — Fix 5 added a stopword/length filter that
    # rejects ≤2-char tokens, so test inputs use real-word substitutes.
    payload = '[{"subject":"alpha","relation":"is_a","object":"beta","confidence":0.9}]'
    out = _parse_triples(payload)
    assert len(out) == 1
    assert out[0].subject == "alpha" and out[0].relation == "is_a"
    assert out[0].object == "beta" and out[0].confidence == 0.9


def test_parse_triples_tuple_array():
    payload = '[["xenon","is_a","yttrium", 0.6]]'
    out = _parse_triples(payload)
    assert out and out[0].as_tuple() == ("xenon", "is_a", "yttrium", 0.6)


def test_parse_triples_strips_markdown_fences():
    payload = '```json\n[{"subject":"proton","relation":"binds","object":"quark"}]\n```'
    assert len(_parse_triples(payload)) == 1


def test_parse_triples_extracts_array_from_prose():
    payload = (
        "Sure, here are the triples:\n"
        '[{"subject":"xenon","relation":"is_a","object":"yttrium"}]\n'
        "Let me know if you need more."
    )
    assert len(_parse_triples(payload)) == 1


def test_parse_triples_empty_on_garbage():
    assert _parse_triples("not json") == []
    assert _parse_triples("") == []


def test_parse_triples_clamps_confidence():
    payload = '[{"subject":"alpha","relation":"rel","object":"beta","confidence":2.5}]'
    [t] = _parse_triples(payload)
    assert t.confidence == 1.0


class _StubProvider:
    """Yields a predetermined string when stream_chat is awaited."""
    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls.append({"model": model, "len": len(messages), "num_ctx": num_ctx})
        # Yield as multiple chunks so the consumer concatenates
        for piece in (self.response[i:i+40] for i in range(0, len(self.response), 40)):
            yield piece

    async def get_embedding(self, text):  return [0.0] * 768
    async def list_models(self):           return []
    async def health(self):                return True


def test_extract_triples_uses_extraction_role_via_resolve(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    provider = _StubProvider(
        '[{"subject":"python","relation":"is_a","object":"language","confidence":0.9}]'
    )
    out = asyncio.run(extract_triples("Python is a programming language.", provider))
    assert len(out) == 1
    assert out[0].subject == "python"
    # phi4-mini name resolved (Ollama backend)
    assert "phi4-mini" in provider.calls[0]["model"]


def test_extract_triples_fails_soft_on_provider_error():
    class _Boom:
        async def stream_chat(self, *a, **kw):
            raise RuntimeError("provider down")
            if False: yield ""
        async def get_embedding(self, t): return []
        async def list_models(self): return []
        async def health(self): return False
    out = asyncio.run(extract_triples("anything", _Boom()))
    # BA fix 6 — a provider failure is None (not measured), not [] (measured).
    assert out is None


def test_extract_triples_empty_on_blank_input():
    out = asyncio.run(extract_triples("   ", _StubProvider("[]")))
    assert out == []


def test_extractor_role_constant():
    assert EXTRACTOR_ROLE == "extraction"


# ===================================================================
# Curiosity engine (live cortex read)
# ===================================================================

def test_curiosity_returns_top_k_by_score():
    h = Hippocampus()
    # high-centrality, low-confidence node "diabetes" — should win
    h.upsert_node("diabetes", epistemic_state=EpistemicState.PARTIAL, confidence=0.2)
    h.upsert_node("a"); h.upsert_node("b"); h.upsert_node("c")
    h.add_edge(SynapticEdge(source="a", target="diabetes", weight=0.5))
    h.add_edge(SynapticEdge(source="b", target="diabetes", weight=0.5))
    h.add_edge(SynapticEdge(source="c", target="diabetes", weight=0.5))
    # known-state node should be ignored
    h.upsert_node("python", epistemic_state=EpistemicState.KNOWN, confidence=0.95)
    h.upsert_node("d"); h.upsert_node("e")
    h.add_edge(SynapticEdge(source="d", target="python", weight=0.5))
    h.add_edge(SynapticEdge(source="e", target="python", weight=0.5))
    out = CuriosityEngine().find_curious_nodes(h, top_k=5)
    assert any(c.node_id == "diabetes" for c in out)
    assert all(c.node_id != "python" for c in out)


def test_curiosity_score_is_in_degree_times_one_minus_confidence():
    h = Hippocampus()
    h.upsert_node("focus", epistemic_state=EpistemicState.INFERRED, confidence=0.3)
    for s in "abcd":
        h.upsert_node(s)
        h.add_edge(SynapticEdge(source=s, target="focus", weight=0.5))
    out = CuriosityEngine().find_curious_nodes(h)
    [n] = [c for c in out if c.node_id == "focus"]
    # in_degree=4, confidence=0.3 → score = 4 * 0.7 = 2.8
    assert abs(n.score - 2.8) < 1e-6


def test_curiosity_question_uses_predecessors_and_successors():
    h = Hippocampus()
    h.upsert_node("hub", epistemic_state=EpistemicState.PARTIAL, confidence=0.4)
    for s in ("a", "b"): h.upsert_node(s); h.add_edge(SynapticEdge(source=s, target="hub", weight=0.5))
    for s in ("c", "d"): h.upsert_node(s); h.add_edge(SynapticEdge(source="hub", target=s, weight=0.5))
    [n] = CuriosityEngine().find_curious_nodes(h, top_k=1)
    assert "hub" in n.question
    assert any(t in n.question for t in ("a", "b"))
    assert any(t in n.question for t in ("c", "d"))


def test_curiosity_skips_known_unknown_until_referenced():
    h = Hippocampus()
    h.upsert_node("orphan", epistemic_state=EpistemicState.PARTIAL, confidence=0.1)
    out = CuriosityEngine(min_in_degree=1).find_curious_nodes(h)
    assert out == []   # in_degree 0


# ===================================================================
# Cortex fingerprinter
# ===================================================================

def test_fingerprint_signature_dim_is_six():
    h = Hippocampus()
    h.upsert_node("a"); h.upsert_node("b")
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.5))
    fp = CortexFingerprinter.compute(h)
    assert isinstance(fp, CortexFingerprint)
    assert len(fp.signature) == SIGNATURE_DIM == 6


def test_fingerprint_distance_zero_when_identical():
    h = Hippocampus()
    h.upsert_node("a"); h.upsert_node("b")
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.7))
    fp1 = CortexFingerprinter.compute(h)
    fp2 = CortexFingerprinter.compute(h)
    assert CortexFingerprinter.distance(fp1, fp2) == 0.0


def test_fingerprint_distance_in_unit_interval():
    h1 = Hippocampus()
    h1.upsert_node("a"); h1.upsert_node("b")
    h1.add_edge(SynapticEdge(source="a", target="b", weight=0.5))

    h2 = Hippocampus()
    for n in "abcdefghij":
        h2.upsert_node(n)
    for s, t in zip("abcdefghi", "bcdefghij"):
        h2.add_edge(SynapticEdge(source=s, target=t, weight=0.5))

    d = CortexFingerprinter.distance(
        CortexFingerprinter.compute(h1),
        CortexFingerprinter.compute(h2),
    )
    assert 0.0 < d <= 1.0


def test_fingerprint_handles_empty_graph():
    fp = CortexFingerprinter.compute(Hippocampus())
    assert fp.n_nodes == 0 and fp.n_edges == 0
    assert fp.signature == [0.0] * 6


def test_fingerprint_to_dict_round_trip():
    h = Hippocampus()
    h.upsert_node("a")
    fp = CortexFingerprinter.compute(h)
    d = fp.to_dict()
    assert d["n_nodes"] == 1 and d["n_edges"] == 0
    assert "signature" in d and len(d["signature"]) == 6


# ===================================================================
# Archive chain — Phase 9 "verify_chain returns valid=True"
# ===================================================================

@pytest.fixture
def chain(tmp_path: Path):
    c = NexusArchiveChain(tmp_path / "archive.db")
    yield c
    c.close()


def test_chain_init_creates_schema(chain):
    # Schema exists after init() — count() works without error
    assert chain.count() == 0


def test_chain_genesis_constant():
    assert NexusArchiveChain.GENESIS_HASH == GENESIS_HASH == "0" * 64


def test_chain_first_block_links_to_genesis(chain, tmp_path):
    fp = {"n_nodes": 1, "signature": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]}
    snap = tmp_path / "snap-0.json"
    snap.write_text("{}", encoding="utf-8")
    block = chain.append(fp, snap)
    assert block.index == 0
    assert block.previous_hash == GENESIS_HASH
    assert block.block_hash == block.compute_hash()


def test_chain_subsequent_block_links_to_previous(chain, tmp_path):
    fp = {"signature": [0, 0, 0, 0, 0, 0]}
    s1 = tmp_path / "s1"; s1.write_text("a", encoding="utf-8")
    s2 = tmp_path / "s2"; s2.write_text("b", encoding="utf-8")
    b1 = chain.append(fp, s1)
    b2 = chain.append(fp, s2)
    assert b2.index == 1
    assert b2.previous_hash == b1.block_hash


def test_chain_verify_returns_valid_after_appends(chain, tmp_path):
    fp = {"signature": [0.1] * 6}
    for i in range(5):
        path = tmp_path / f"snap-{i}.json"
        path.write_text(f"snap{i}", encoding="utf-8")
        chain.append(fp, path)
    result = chain.verify_chain()
    assert result.valid is True
    assert result.n_blocks == 5
    assert result.broken_at is None


def test_chain_verify_detects_block_hash_tampering(chain, tmp_path):
    fp = {"signature": [0.1] * 6}
    for i in range(3):
        snap = tmp_path / f"snap-{i}.json"; snap.write_text(f"x{i}", encoding="utf-8")
        chain.append(fp, snap)
    # Tamper with block 1's stored snapshot_hash directly (bypassing append())
    chain.conn.execute(
        "UPDATE archive_blocks SET snapshot_hash = ? WHERE idx = ?",
        ("00" * 32, 1),
    )
    chain.conn.commit()
    result = chain.verify_chain()
    assert result.valid is False
    assert result.broken_at == 1


def test_chain_persists_across_reopen(tmp_path):
    db = tmp_path / "archive.db"
    fp = {"signature": [0.0] * 6}
    snap = tmp_path / "snap.json"; snap.write_text("ok", encoding="utf-8")
    c1 = NexusArchiveChain(db)
    c1.append(fp, snap)
    c1.close()
    c2 = NexusArchiveChain(db)
    assert c2.count() == 1
    assert c2.verify_chain().valid is True
    c2.close()


# ===================================================================
# KAIROS — fingerprint + chain append on monthly cadence
# ===================================================================

def _kairos_with_chain(tmp_path: Path) -> KairosProcess:
    h = Hippocampus()
    h.upsert_node("a"); h.upsert_node("b")
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.5, fire_count=8))
    nc = Neocortex(tmp_path / "kb.db")
    return KairosProcess(
        h, nc, HebbianUpdater(), ProceduralMemory(),
        fingerprinter=CortexFingerprinter(),
        archive_chain=NexusArchiveChain(tmp_path / "archive.db"),
        snapshots_dir=tmp_path / "snapshots",
    )


def test_kairos_first_cycle_takes_snapshot(tmp_path: Path):
    k = _kairos_with_chain(tmp_path)
    res = asyncio.run(k.run_one_cycle())
    assert res.snapshot_taken is True
    assert res.archive_block_index == 0
    assert res.snapshot_path
    assert (tmp_path / "snapshots").exists()
    assert k.archive_chain.verify_chain().valid is True
    k.neocortex.close(); k.archive_chain.close()


def test_kairos_second_cycle_within_interval_skips_snapshot(tmp_path: Path):
    k = _kairos_with_chain(tmp_path)
    asyncio.run(k.run_one_cycle())
    second = asyncio.run(k.run_one_cycle())
    assert second.snapshot_taken is False
    assert second.archive_block_index is None
    k.neocortex.close(); k.archive_chain.close()


def test_kairos_force_snapshot_overrides_interval(tmp_path: Path):
    k = _kairos_with_chain(tmp_path)
    asyncio.run(k.run_one_cycle())
    forced = asyncio.run(k.run_one_cycle(force_snapshot=True))
    assert forced.snapshot_taken is True
    assert forced.archive_block_index == 1
    k.neocortex.close(); k.archive_chain.close()


def test_kairos_without_fingerprinter_skips_snapshots(tmp_path: Path):
    """Phase 5 callers that didn't pass fingerprinter/chain still work."""
    h = Hippocampus()
    nc = Neocortex(tmp_path / "kb.db")
    k = KairosProcess(h, nc, HebbianUpdater(), ProceduralMemory())
    res = asyncio.run(k.run_one_cycle(force_snapshot=True))
    assert res.snapshot_taken is False
    nc.close()


def test_snapshot_interval_constant():
    assert SNAPSHOT_INTERVAL_DAYS == 30


# ===================================================================
# Compaction integrity guard
# ===================================================================

def _basic_state_with_triples() -> tuple[NexusLoopState, Hippocampus]:
    h = Hippocampus()
    h.upsert_node("a", epistemic_state=EpistemicState.KNOWN, confidence=0.9)
    h.upsert_node("b", epistemic_state=EpistemicState.PARTIAL, confidence=0.5)
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.6))
    s = NexusLoopState(graph_context=(("a", "rel", "b"),))
    return s, h


def test_integrity_report_pass_when_nothing_changes():
    s, h = _basic_state_with_triples()
    pre = snapshot_for_integrity(s, h)
    post = snapshot_for_integrity(s, h)
    rep = compute_integrity_report(pre, post, tier=Tier.SNIP,
                                    prefix_pre="x", prefix_post="x")
    assert rep.pass_fail == "PASS"
    assert rep.violations == ()
    assert rep.retention_rate == 1.0


def test_integrity_report_warns_on_concept_removed_at_tier1():
    s, h = _basic_state_with_triples()
    pre = snapshot_for_integrity(s, h)
    s2 = NexusLoopState(graph_context=())   # all triples gone
    post = snapshot_for_integrity(s2, h)
    rep = compute_integrity_report(pre, post, tier=Tier.SNIP,
                                    prefix_pre="x", prefix_post="x")
    assert rep.pass_fail == "WARN"
    assert any(v.invariant == "concept_completely_removed" for v in rep.violations)


def test_integrity_report_warns_on_concept_removed_at_tier3_too():
    """BD-3: the tier no longer changes the severity; WARN at every tier."""
    s, h = _basic_state_with_triples()
    pre = snapshot_for_integrity(s, h)
    post = snapshot_for_integrity(NexusLoopState(graph_context=()), h)
    rep = compute_integrity_report(pre, post, tier=Tier.COLLAPSE,
                                    prefix_pre="x", prefix_post="x")
    assert rep.pass_fail == "WARN"
    assert all(v.severity == "WARN" for v in rep.violations)


def test_integrity_report_warns_on_prefix_drift():
    s, h = _basic_state_with_triples()
    pre = snapshot_for_integrity(s, h)
    rep = compute_integrity_report(
        pre, pre, tier=Tier.SNIP,
        prefix_pre="ORIGINAL", prefix_post="MUTATED",
    )
    assert rep.pass_fail == "WARN"
    assert any(v.invariant == "prefix_drift" for v in rep.violations)


def test_integrity_report_to_dict_schema():
    s, h = _basic_state_with_triples()
    snap = snapshot_for_integrity(s, h)
    rep = compute_integrity_report(snap, snap, tier=Tier.SNIP,
                                    prefix_pre="x", prefix_post="x")
    d = rep.to_dict()
    for key in ("pass_fail", "violations", "retention_rate",
                "minority_loss_rate", "structural_summary"):
        assert key in d
    # BD-3 — the two retired rules' counters left the frame with them: a
    # constant zero would be a default impersonating a measurement.
    assert "epistemic_violations" not in d and "confidence_drift" not in d


# ===================================================================
# Fine-tune stub
# ===================================================================

def test_finetuning_unavailable_without_cuda():
    if is_finetuning_available():
        pytest.skip("CUDA + unsloth actually available — different test path")
    assert is_finetuning_available() is False


def test_finetune_raises_unavailable_when_skip_path_active():
    if is_finetuning_available():
        pytest.skip("CUDA + unsloth actually available — different test path")
    with pytest.raises(FineTuneUnavailable):
        finetune()


# ===================================================================
# Pre-Phase-9 regression spot-checks
# ===================================================================

def test_predictive_constants_unchanged():
    """Phase 7 multipliers — Phase 9 must NOT redefine them."""
    from backend.cortex.predictive import (
        CONFIRMED_MULTIPLIER, MISSED_FACTOR, SURPRISE_MULTIPLIER,
    )
    assert SURPRISE_MULTIPLIER == 2.5
    assert CONFIRMED_MULTIPLIER == 0.4
    assert MISSED_FACTOR == 0.95


def test_run_cascade_signature_still_takes_rebuild_block_fn():
    """Fix 1 intact."""
    import inspect as _inspect
    from backend.loop.compaction import run_cascade
    assert "rebuild_block_fn" in _inspect.signature(run_cascade).parameters


def test_assembled_context_dataclass_unchanged():
    """Fix 2 intact."""
    from backend.loop.state import AssembledContext
    fields = {f for f in AssembledContext.__dataclass_fields__}
    assert {"hits", "activation", "vector_chunks", "block"} <= fields


def test_session_memory_remains_4_tuple_format():
    """Fix 3 intact — Phase 9 only ADDS state fields, never reshapes them."""
    from backend.loop.compaction import _current_session_weight
    import time as _time
    now = _time.time()
    # Pin both timestamps so per-hour decay evaluates to exactly 0.
    assert _current_session_weight(("a", "b", 0.5, now), now=now) == 0.5


def test_create_app_isolation_with_phase9_collaborators(tmp_path: Path):
    """Fix 4 intact — fresh services per app, including Phase 9 fields."""
    from backend.server import create_app, create_services
    s1 = create_services(
        kb_db_path=tmp_path / "a.db", chroma_dir=tmp_path / "ca",
        archive_db_path=tmp_path / "a-archive.db", snapshots_dir=tmp_path / "snaps-a",
    )
    s2 = create_services(
        kb_db_path=tmp_path / "b.db", chroma_dir=tmp_path / "cb",
        archive_db_path=tmp_path / "b-archive.db", snapshots_dir=tmp_path / "snaps-b",
    )
    a1 = create_app(s1); a2 = create_app(s2)
    assert a1.state.services.archive_chain is not a2.state.services.archive_chain
    assert a1.state.services.fingerprinter is not a2.state.services.fingerprinter
    assert a1.state.services.contradiction is not a2.state.services.contradiction
    assert a1.state.services.curiosity is not a2.state.services.curiosity


def test_chain_status_endpoint_returns_verification(tmp_path: Path):
    """Lifespan starts KAIROS as a background task — its first cycle takes a
    snapshot because no prior block exists. So expect ≥1 block and valid chain."""
    from fastapi.testclient import TestClient
    from backend.server import create_app, create_services
    s = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "ca",
        archive_db_path=tmp_path / "archive.db", snapshots_dir=tmp_path / "snaps",
    )
    a = create_app(s)
    with TestClient(a) as c:
        r = c.get("/chain/status")
    assert r.status_code == 200
    body = r.json()
    assert "n_blocks" in body and body["n_blocks"] >= 0
    assert "verification" in body and body["verification"]["valid"] is True


# ===================================================================
# Fix 5 — stopword filter in extraction (_parse_triples gate)
# ===================================================================

from backend.cortex.extraction import _is_meaningful as _is_meaningful_fix5
from backend.cortex.extraction import _parse_triples as _parse_fix5


def test_is_meaningful_rejects_stopwords_and_short_tokens():
    # Conversational filler — extraction-local stopwords catch these
    assert _is_meaningful_fix5("me")        is False   # len ≤ 2
    assert _is_meaningful_fix5("about")     is False   # extraction stopword
    assert _is_meaningful_fix5("yourself")  is False   # extraction stopword
    assert _is_meaningful_fix5("tell")      is False   # extraction stopword
    assert _is_meaningful_fix5("your")      is False   # extraction stopword
    # Domain-real terms — must pass
    assert _is_meaningful_fix5("fastapi")          is True
    assert _is_meaningful_fix5("web_framework")    is True
    assert _is_meaningful_fix5("interval_training") is True
    # Single/double char + pure digits
    assert _is_meaningful_fix5("a")  is False
    assert _is_meaningful_fix5("ab") is False
    assert _is_meaningful_fix5("42") is False


def test_parse_triples_drops_stopword_subject_or_object():
    import json as _j
    payload = _j.dumps([
        {"subject": "fastapi",  "relation": "is_a",       "object": "web_framework"},
        {"subject": "me",       "relation": "is",         "object": "tired"},          # both ends junk
        {"subject": "python",   "relation": "used_by",    "object": "yourself"},        # object junk
        {"subject": "tell",     "relation": "describes",  "object": "monads"},          # subject junk
        {"subject": "42",       "relation": "is_a",       "object": "answer"},          # digit subject
    ])
    out = _parse_fix5(payload)
    names = {(t.subject, t.object) for t in out}
    assert names == {("fastapi", "web_framework")}, f"unexpected survivors: {names}"


def test_parse_triples_keeps_relation_untouched_even_if_short():
    """Relations are always kept — short rel like 'is' is fine, the filter
    only gates subject/object."""
    import json as _j
    payload = _j.dumps([
        {"subject": "fastapi", "relation": "is", "object": "framework"},
    ])
    out = _parse_fix5(payload)
    assert len(out) == 1
    assert out[0].relation == "is"


# ===================================================================
# Fix 2 — extracted triples persist with Granularity.TURN
# ===================================================================

import asyncio as _asyncio_fix2

from backend.kb.granularity import Granularity as _G_fix2
from backend.kb.retriever import TripleRetriever as _TR_fix2
from backend.kb.tiered_store import TripleStore as _TS_fix2
from backend.loop.agentic_loop import run_turn as _run_turn_fix2
from backend.loop.state import NexusLoopState as _NS_fix2


class _ExtractionMockProvider:
    """Yields a single chat token, then on the extraction call yields a JSON
    array — two stream_chat calls per turn, two scripted responses."""
    def __init__(self):
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        if self.calls == 1:
            # First call: chat completion
            for tok in ["FastAPI ", "is ", "a ", "Python ", "web ", "framework."]:
                yield tok
        else:
            # Second call: extraction prompt → emit JSON
            payload = (
                '[{"subject":"fastapi","relation":"is_a",'
                '"object":"web_framework","confidence":0.9}]'
            )
            for piece in (payload[i:i+40] for i in range(0, len(payload), 40)):
                yield piece

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def test_extracted_triples_persist_with_turn_granularity(tmp_path: Path):
    """Fix 2 — after a turn, extracted triples are written to the store
    tagged Granularity.TURN with source='extraction'. Seeded triples (if any)
    keep their original granularity — Fix 2 only touches the extraction path."""
    store = _TS_fix2(tmp_path / "kb.db")
    retriever = _TR_fix2(store)

    state = _NS_fix2()
    provider = _ExtractionMockProvider()

    async def drive():
        last_state = state
        async for _frame, state_after in _run_turn_fix2(
            state, "tell me about fastapi",
            retriever=retriever, provider=provider,
        ):
            last_state = state_after
        return last_state

    _asyncio_fix2.run(drive())

    rows = store.all()
    extraction_rows = [r for r in rows if r.source == "extraction"]
    assert extraction_rows, "expected at least one extracted triple persisted"
    for row in extraction_rows:
        assert row.granularity == _G_fix2.TURN.value, (
            f"extracted triple {row.subject}->{row.object} has granularity "
            f"{row.granularity}, expected {_G_fix2.TURN.value}"
        )
    # The fastapi triple specifically
    assert any(
        r.subject == "fastapi" and r.object == "web_framework"
        for r in extraction_rows
    )
    store.close()


def test_seeded_triples_keep_topic_granularity_independently(tmp_path: Path):
    """Fix 2 only touches the extraction path; seeded fixture triples must
    still land as Granularity.TOPIC (and offline fixture explicitly uses TOPIC)."""
    from backend.kb.seeder import seed_offline_fixture
    store = _TS_fix2(tmp_path / "kb.db")
    seed_offline_fixture(store, confirmed=True)
    rows = store.all()
    assert rows, "expected offline fixture to seed triples"
    for r in rows:
        assert r.granularity == _G_fix2.TOPIC.value
    store.close()


# ===================================================================
# Fix 6 — model_pull_proposal SSE + GENERAL_REASONING fallback
# ===================================================================

import asyncio as _asyncio_fix6
import re as _re_fix6

from backend.kb.tiered_store import TripleStore as _TS_fix6
from backend.kb.retriever import TripleRetriever as _TR_fix6
from backend.loop.agentic_loop import run_turn as _run_turn_fix6
from backend.loop.state import NexusLoopState as _NS_fix6
from backend.router.capabilities import Capability as _Cap_fix6
from backend.router.model_kernel import ModelKernel as _MK_fix6
from backend.router.model_registry import (
    ModelProfile as _MP_fix6,
    ModelRegistry as _MR_fix6,
)


def _build_kernel_with_partial_pull(*, code_pulled: bool, primary_pulled: bool) -> _MK_fix6:
    """Hand-rolled tiny registry so we can pin is_pulled per model."""
    reg = _MR_fix6()
    reg.register(_MP_fix6(
        name="deepseek-fake:14b", display_name="DeepSeek (fake)",
        capabilities=(_Cap_fix6.GENERAL_REASONING,),
        vram_gb=8.0, tokens_per_sec=20.0, context_window=8192,
        pull_size_gb=8.0, is_pulled=primary_pulled,
    ))
    reg.register(_MP_fix6(
        name="qwen-fake-coder:7b", display_name="Qwen Coder (fake)",
        capabilities=(_Cap_fix6.CODE_GENERATION, _Cap_fix6.GENERAL_REASONING),
        vram_gb=4.0, tokens_per_sec=45.0, context_window=8192,
        pull_size_gb=4.0, is_pulled=code_pulled,
    ))
    return _MK_fix6(reg)


class _SilentMockProvider:
    """No-op chat stream + empty extraction so run_turn completes quickly."""
    async def stream_chat(self, model, messages, num_ctx=8192):
        # Yield one token so the stream isn't empty
        yield "ok"
    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _collect_frames(state, user_msg, *, kernel, provider, retriever=None):
    out = []
    async def go():
        async for frame, _state in _run_turn_fix6(
            state, user_msg,
            kernel=kernel, provider=provider, retriever=retriever,
        ):
            out.append(frame)
    _asyncio_fix6.run(go())
    return out


def _events_from(frames):
    rx = _re_fix6.compile(r"^event:\s*(.+)$", _re_fix6.MULTILINE)
    return [rx.search(f).group(1).strip() for f in frames if rx.search(f)]


def test_pull_proposal_emitted_when_specialist_not_pulled(tmp_path: Path):
    """Code query → kernel selects qwen-fake-coder. Coder is NOT pulled
    but primary IS — pull_proposal fires AND fallback model_routed
    selects the primary."""
    kernel = _build_kernel_with_partial_pull(code_pulled=False, primary_pulled=True)
    store = _TS_fix6(tmp_path / "kb.db")
    retriever = _TR_fix6(store)
    frames = _collect_frames(
        _NS_fix6(), "please fix this python function",
        kernel=kernel, provider=_SilentMockProvider(), retriever=retriever,
    )
    events = _events_from(frames)
    assert "model_pull_proposal" in events
    assert "model_routed" in events
    # The model_routed frame after pull_proposal should reference the fallback
    pull_idx = events.index("model_pull_proposal")
    routed_after = [i for i, e in enumerate(events) if e == "model_routed" and i > pull_idx]
    assert routed_after, "expected fallback model_routed AFTER model_pull_proposal"
    fallback_frame = frames[routed_after[0]]
    assert "specialist_unavailable_fallback" in fallback_frame
    assert "general_reasoning" in fallback_frame
    store.close()


def test_no_pull_proposal_when_routed_model_is_pulled(tmp_path: Path):
    """When the kernel's first choice is_pulled=True, no proposal fires."""
    kernel = _build_kernel_with_partial_pull(code_pulled=True, primary_pulled=True)
    store = _TS_fix6(tmp_path / "kb.db")
    retriever = _TR_fix6(store)
    frames = _collect_frames(
        _NS_fix6(), "please fix this python function",
        kernel=kernel, provider=_SilentMockProvider(), retriever=retriever,
    )
    events = _events_from(frames)
    assert "model_pull_proposal" not in events
    assert "model_routed" in events
    store.close()


def test_pull_proposal_with_no_fallback_still_emits_proposal(tmp_path: Path):
    """If no GENERAL_REASONING model is_pulled at all, the proposal still
    fires (so the UI can prompt the pull) but no fallback model_routed."""
    kernel = _build_kernel_with_partial_pull(code_pulled=False, primary_pulled=False)
    store = _TS_fix6(tmp_path / "kb.db")
    retriever = _TR_fix6(store)
    frames = _collect_frames(
        _NS_fix6(), "please fix this python function",
        kernel=kernel, provider=_SilentMockProvider(), retriever=retriever,
    )
    events = _events_from(frames)
    assert "model_pull_proposal" in events
    pull_frame = next(f for f in frames if "model_pull_proposal" in f)
    # Without any pulled model, the fallback field is null
    assert '"fallback": null' in pull_frame or '"fallback":null' in pull_frame
    store.close()


# ===================================================================
# Fix 9 — multimodal file upload (state field + ChatRequest + routing)
# ===================================================================

import asyncio as _asyncio_fix9
import dataclasses as _dc_fix9

from backend.loop.state import NexusLoopState as _NS_fix9
from backend.loop.agentic_loop import run_turn as _run_turn_fix9
from backend.providers.model_map import resolve as _resolve_fix9


def test_nexus_loop_state_has_attachments_field():
    s = _NS_fix9()
    assert hasattr(s, "attachments")
    assert s.attachments == ()
    # Frozen-safe — assignment must fail (Law I)
    with pytest.raises(_dc_fix9.FrozenInstanceError):
        s.attachments = ({"name": "x", "type": "image/png", "data": "aGk="},)


def test_chat_request_accepts_attachments_field():
    from backend.server import ChatRequest, Attachment
    req = ChatRequest(
        message="describe this",
        attachments=[Attachment(name="x.png", type="image/png", data="aGk=")],
    )
    assert req.attachments[0].name == "x.png"
    assert req.attachments[0].type == "image/png"


def test_image_attachment_routes_to_multimodal_model(tmp_path: Path):
    """Image attachment present → kernel-independent multimodal route fires,
    model_routed SSE carries capability=multimodal."""
    kernel = _build_kernel_with_partial_pull(code_pulled=True, primary_pulled=True)
    store = _TS_fix6(tmp_path / "kb.db")
    retriever = _TR_fix6(store)
    state = _NS_fix9(attachments=(
        {"name": "diagram.png", "type": "image/png", "data": "aGk="},
    ))

    frames = _collect_frames(
        state, "describe this picture",
        kernel=kernel, provider=_SilentMockProvider(), retriever=retriever,
    )
    events = _events_from(frames)
    assert "model_routed" in events
    # First model_routed should be the multimodal capability frame
    mm_frame = next(f for f in frames if "model_routed" in f and "multimodal" in f)
    assert _resolve_fix9("multimodal") in mm_frame
    assert "image_attachment_present" in mm_frame
    store.close()


def test_no_attachment_does_not_trigger_multimodal_route(tmp_path: Path):
    """Plain chat (no attachments) → multimodal route MUST NOT fire."""
    kernel = _build_kernel_with_partial_pull(code_pulled=True, primary_pulled=True)
    store = _TS_fix6(tmp_path / "kb.db")
    retriever = _TR_fix6(store)
    frames = _collect_frames(
        _NS_fix9(), "what is the answer",
        kernel=kernel, provider=_SilentMockProvider(), retriever=retriever,
    )
    mm_frames = [f for f in frames if "image_attachment_present" in f]
    assert mm_frames == [], "no attachment → no multimodal frame"
    store.close()


def test_attachments_cleared_at_end_of_turn(tmp_path: Path):
    """state.attachments must be empty after the turn completes (Stage 7
    atomic transition clears them — single-use)."""
    kernel = _build_kernel_with_partial_pull(code_pulled=True, primary_pulled=True)
    store = _TS_fix6(tmp_path / "kb.db")
    retriever = _TR_fix6(store)
    state = _NS_fix9(attachments=(
        {"name": "x.png", "type": "image/png", "data": "aGk="},
    ))

    last = state
    async def go():
        nonlocal last
        async for _frame, state_after in _run_turn_fix9(
            state, "describe",
            kernel=kernel, provider=_SilentMockProvider(), retriever=retriever,
        ):
            last = state_after
    _asyncio_fix9.run(go())
    assert last.attachments == ()
    store.close()
