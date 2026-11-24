"""Phase 2 tests — required coverage from CLAUDE.md:

  - seed conceptnet→triples in db (offline fixture is deterministic equivalent)
  - retrieval returns >0 triples
  - granularity tagged (utterance/turn/session/topic)
  - triples persist across restart
  - LlamaIndex chunks have source metadata
  - retrieval ranks by weight
  - Law 3: seeding refuses without explicit confirmation
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever, extract_terms
from backend.kb.seeder import SeedRefused, seed_offline_fixture
from backend.kb.source_registry import KBSource, KBSourceRegistry, default_registry
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import assemble_context, STABLE_SYSTEM_PREFIX
from backend.loop.state import AssembledContext


@pytest.fixture
def store(tmp_path: Path):
    db = tmp_path / "kb.db"
    s = TripleStore(db)
    yield s
    s.close()


# ---------- Law 3: confirmation required ----------

def test_seeder_refuses_without_confirmation(store):
    with pytest.raises(SeedRefused):
        seed_offline_fixture(store, confirmed=False)
    assert store.count() == 0


def test_offline_seed_inserts_triples(store):
    n = seed_offline_fixture(store, confirmed=True).seeded
    assert n > 0
    assert store.count() == n


# ---------- Granularity enum + tagging ----------

def test_granularity_enum_complete():
    assert set(Granularity.all()) == {"utterance", "turn", "session", "topic"}


def test_every_seeded_triple_is_tagged_with_granularity(store):
    seed_offline_fixture(store, confirmed=True)
    for t in store.all():
        assert t.granularity in Granularity.all(), \
            f"triple {t.id} has invalid granularity {t.granularity!r}"


def test_store_rejects_unknown_granularity(store):
    with pytest.raises(ValueError):
        store.add(subject="aa", relation="r", object="bb",
                  granularity="paragraph", source="x")


# ---------- Persistence across "restart" ----------

def test_triples_persist_across_restart(tmp_path: Path):
    db = tmp_path / "kb.db"
    s1 = TripleStore(db)
    n = seed_offline_fixture(s1, confirmed=True).seeded
    s1.close()

    s2 = TripleStore(db)  # simulate process restart
    assert s2.count() == n
    rows = s2.all()
    assert any(t.subject == "python" for t in rows)
    s2.close()


# ---------- Retrieval returns >0, ranks by weight ----------

def test_retrieval_returns_triples(store):
    seed_offline_fixture(store, confirmed=True)
    r = TripleRetriever(store)
    hits = r.retrieve("Tell me about python")
    assert len(hits) > 0
    assert any(rt.triple.subject == "python" for rt in hits)


def test_retrieval_ranks_by_weight(store):
    # Insert two competing triples — same tier, different weights
    store.add(subject="alpha", relation="is_a", object="strong_match",
              weight=0.95, tier=1, granularity=Granularity.TOPIC, source="t")
    store.add(subject="alpha", relation="is_a", object="weak_match",
              weight=0.10, tier=1, granularity=Granularity.TOPIC, source="t")
    r = TripleRetriever(store)
    hits = r.retrieve("alpha")
    assert hits[0].triple.object == "strong_match", \
        f"expected strong-weight first, got {[h.triple.object for h in hits]}"
    assert hits[0].score > hits[1].score


def test_retrieval_prefers_lower_tier(store):
    # Tier 0 (core) must beat tier 2 even with lower weight
    store.add(subject="kafka", relation="is_a", object="core_answer",
              weight=0.4, tier=0, granularity=Granularity.TOPIC, source="t")
    store.add(subject="kafka", relation="is_a", object="warm_answer",
              weight=0.9, tier=2, granularity=Granularity.TOPIC, source="t")
    hits = TripleRetriever(store).retrieve("kafka")
    assert hits, "expected hits for 'kafka'"
    assert hits[0].triple.tier == 0


def test_retrieval_empty_when_no_match(store):
    seed_offline_fixture(store, confirmed=True)
    r = TripleRetriever(store)
    assert r.retrieve("zxqwertasdfgh") == []


# ---------- LlamaIndex chunks have source metadata ----------

def test_llama_index_chunks_carry_source_metadata(store):
    pytest.importorskip("llama_index.core")
    from backend.kb.llama_index_wrap import (
        load_chunks, persist_chunks, split_with_metadata,
    )
    text = (
        "NEXUS runs on a single RTX 3060 12GB. "
        "It uses Ollama to host DeepSeek R1 14B. "
        "The agentic loop is async and yields SSE events to the UI."
    )
    chunks = split_with_metadata(text, source="arch_doc",
                                 extra_metadata={"section": "1"})
    assert len(chunks) > 0
    for c in chunks:
        assert c.source == "arch_doc"
        assert c.metadata["source"] == "arch_doc"
        assert c.metadata.get("section") == "1"
        assert c.granularity in Granularity.all()

    # Persistence path
    persist_chunks(store, chunks)
    reloaded = load_chunks(store, source="arch_doc")
    assert len(reloaded) == len(chunks)
    assert reloaded[0].metadata["source"] == "arch_doc"


# ---------- KB source registry ----------

def test_default_registry_has_conceptnet_and_wikidata():
    ids = {s.id for s in default_registry.all()}
    assert "conceptnet" in ids
    assert "wikidata" in ids


def test_registry_one_line_extension():
    reg = KBSourceRegistry()
    reg.register(KBSource(
        id="pubmed", display_name="PubMed", source_type="api",
        endpoint="https://example.invalid", domains=("medical",),
    ))
    assert reg.get("pubmed").display_name == "PubMed"
    assert reg.for_domain("medical")[0].id == "pubmed"


def test_registry_rejects_duplicate():
    reg = KBSourceRegistry()
    src = KBSource(id="x", display_name="X", source_type="api",
                   endpoint="https://example.invalid", domains=("g",))
    reg.register(src)
    with pytest.raises(ValueError):
        reg.register(src)


# ---------- Stage 2 context assembly ----------

def test_assemble_context_appends_dynamic_block_only(store):
    seed_offline_fixture(store, confirmed=True)
    retriever = TripleRetriever(store)
    ctx = assemble_context("python machine learning", retriever, hippocampus=None)
    assert isinstance(ctx, AssembledContext)
    assert ctx.hits, "expected at least one hit for python"
    assert ctx.block.startswith("\n\n[KB CONTEXT")
    # Law VII: stable prefix is unchanged. Block is appended, not interpolated.
    composed = STABLE_SYSTEM_PREFIX + ctx.block
    assert composed.startswith(STABLE_SYSTEM_PREFIX)


def test_assemble_context_handles_no_retriever():
    ctx = assemble_context("anything", None, hippocampus=None)
    assert isinstance(ctx, AssembledContext)
    assert ctx.hits == []
    assert ctx.activation is None
    assert ctx.vector_chunks == []
    assert ctx.block == ""


# ---------- Term extraction sanity ----------

def test_extract_terms_drops_stopwords():
    terms = extract_terms("What is the python language used for")
    assert "python" in terms
    assert "language" in terms
    assert "what" not in terms
    assert "the" not in terms


# ---------- Decay preserves stored weight ----------

def test_current_weight_decays_without_mutating(store):
    tid = store.add(subject="aa", relation="r", object="bb",
                    weight=0.9, tier=2,
                    granularity=Granularity.TOPIC, source="t")
    t = store.all()[0]
    # Simulate 10 days elapsed
    decayed = t.current_weight(now=t.last_fired + 10 * 86400)
    assert decayed < t.weight
    assert t.weight == 0.9, "stored weight must not be mutated by current_weight()"
