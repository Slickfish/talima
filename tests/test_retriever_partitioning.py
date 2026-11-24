"""Retrieval partitioning — grounded facts prioritised, co-activations capped.

Regression guard for the candidate-pool-domination bug: hippocampus
consolidation writes many `co_activated` meta-edges (promoted to tier 0,
weight ~1.0) that swamp `search_terms` results and crowd grounded facts out
of the LLM context. `retrieve()` now partitions by relation kind, keeps all
grounded hits first, and caps co-activations at 20% of `top_k`.
"""
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore


@pytest.fixture
def store(tmp_path: Path):
    db = tmp_path / "kb.db"
    s = TripleStore(db)
    yield s
    s.close()


def _add_grounded(store, n: int, term: str = "alpha") -> None:
    for i in range(n):
        store.add(
            subject=f"{term}_fact_{i}", relation="is_a", object=f"thing_{i}",
            weight=0.6, tier=2, granularity=Granularity.TOPIC, source="extraction",
        )


def _add_coactivations(store, n: int, term: str = "alpha") -> None:
    for i in range(n):
        store.add(
            subject=term, relation="co_activated", object=f"partner_{i}",
            weight=1.0, tier=0, granularity=Granularity.SESSION,
            source="hippocampus_consolidation",
        )


def _counts(hits):
    grounded = sum(1 for h in hits if h.triple.relation != "co_activated")
    coa = sum(1 for h in hits if h.triple.relation == "co_activated")
    return grounded, coa


def test_grounded_facts_prioritised_over_coactivations(store):
    _add_grounded(store, 5)
    _add_coactivations(store, 50)
    hits = TripleRetriever(store).retrieve("alpha", top_k=20)
    grounded, coa = _counts(hits)
    assert grounded == 5          # every grounded fact survives
    assert coa <= 4               # co-activations capped at 20% of top_k


def test_coactivations_fill_when_grounded_sparse(store):
    _add_grounded(store, 2)
    _add_coactivations(store, 50)
    hits = TripleRetriever(store).retrieve("alpha", top_k=20)
    grounded, coa = _counts(hits)
    assert grounded == 2          # both grounded facts survive
    assert 0 < coa <= 4           # co-activations fill remaining slots, still capped


def test_no_coactivations_returned_when_grounded_sufficient(store):
    _add_grounded(store, 25)
    _add_coactivations(store, 50)
    hits = TripleRetriever(store).retrieve("alpha", top_k=20)
    grounded, coa = _counts(hits)
    assert len(hits) == 20
    assert grounded == 20         # grounded fills the whole budget
    assert coa == 0               # no co-activation pollution
