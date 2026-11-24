"""Package B — per-term quota in search_terms.

Regression guard for the candidate-flooding defect: the old single
OR-of-LIKEs query with one shared LIMIT let a high-frequency generic term
("about") saturate the pool before substantive terms ("cardiovascular")
contributed anything. search_terms now allocates an equal per-term slice
(floor 5), dedups by triple id, and re-sorts tier ASC / weight DESC with
`limit` as the total cap.
"""
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore


@pytest.fixture
def store(tmp_path: Path):
    db = tmp_path / "kb.db"
    s = TripleStore(db)
    yield s
    s.close()


def _seed(store, subject, *, weight=0.6, tier=2, relation="is_a", obj="thing"):
    store.add(subject=subject, relation=relation, object=obj,
              weight=weight, tier=tier, granularity=Granularity.TOPIC, source="t")


def test_generic_term_cannot_saturate_pool(store):
    # 200 high-rank generic matches (tier 0, weight 1.0) — the old combined
    # query returned only these for limit=80.
    for i in range(200):
        _seed(store, f"about_news_{i}", weight=1.0, tier=0)
    # 5 substantive matches at worse rank.
    for i in range(5):
        _seed(store, f"cardiovascular_fact_{i}", weight=0.6, tier=2)

    out = store.search_terms(["about", "cardiovascular"], limit=80)
    cardio = [t for t in out if "cardiovascular" in t.subject]
    assert len(cardio) == 5          # every substantive triple reaches the pool
    assert len(out) <= 80


def test_per_term_floor_applies(store):
    # 10 terms, limit 80 -> per_term = max(5, 8) = 8. Disjoint data per term.
    terms = [f"term{i}" for i in range(10)]
    for term in terms:
        for j in range(20):          # plenty of matches for every term
            _seed(store, f"{term}_subj_{j}")

    out = store.search_terms(terms, limit=80)
    contributed = [t for t in out if t.subject.startswith("term0_")]
    assert len(contributed) == 8     # exactly the per-term quota
    assert len(out) <= 80


def test_dedup_keeps_single_copy(store):
    # One triple matching BOTH query terms appears exactly once.
    _seed(store, "interval_training", weight=0.9, tier=1)
    out = store.search_terms(["interval", "training"], limit=80)
    matches = [t for t in out if t.subject == "interval_training"]
    assert len(matches) == 1


def test_total_cap_respected(store):
    for term in ("alpha", "beta", "gamma"):
        for j in range(100):
            _seed(store, f"{term}_subj_{j}")
    out = store.search_terms(["alpha", "beta", "gamma"], limit=30)
    assert len(out) <= 30


def test_empty_terms_returns_empty(store):
    assert store.search_terms([], limit=80) == []


def test_ordering_tier_then_weight(store):
    _seed(store, "zeta_low",  weight=0.2, tier=2)
    _seed(store, "zeta_high", weight=0.9, tier=2)
    _seed(store, "zeta_top",  weight=0.5, tier=0)
    _seed(store, "zeta_mid",  weight=0.7, tier=1)

    out = store.search_terms(["zeta"], limit=80)
    keys = [(t.tier, -t.weight) for t in out]
    assert keys == sorted(keys)      # tier ASC, then stored weight DESC
    assert out[0].subject == "zeta_top"
