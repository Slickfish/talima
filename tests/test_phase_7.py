"""Phase 7 tests — required coverage from CLAUDE.md + arch §6.2:

  - prediction error categorises nodes (high_surprise / confirmed / missed)
  - Hebbian uses 2.5x for surprise, 0.4x for confirmed, 0.95x to weaken missed
  - ChromaDB persists across restart
  - 2-stage retrieval (coarse 256 → rerank 768) returns ranked results
  - matryoshka_truncate is dimension-correct
  - offline embedding is deterministic + 768-dim
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.cortex.edges import SynapticEdge
from backend.cortex.hebbian import HebbianUpdater
from backend.cortex.predictive import (
    CONFIRMED_MULTIPLIER,
    MISSED_FACTOR,
    PredictionError,
    PredictiveCortex,
    SURPRISE_MULTIPLIER,
)
from backend.cortex.propagation import ActivationMap, propagate
from backend.memory.hippocampus import Hippocampus
from backend.vector.chroma import VectorStore
from backend.vector.embeddings import COARSE_DIM, FULL_DIM, _offline_embed
from backend.vector.matryoshka import cosine, matryoshka_truncate


# =============== Predictive cortex ===============

def test_prediction_error_multiplier_constants():
    """Arch §6.2 specifies the multipliers exactly."""
    assert SURPRISE_MULTIPLIER == 2.5
    assert CONFIRMED_MULTIPLIER == 0.4
    assert MISSED_FACTOR == 0.95


def _three_node_chain() -> Hippocampus:
    h = Hippocampus()
    for n in "abc":
        h.upsert_node(n)
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.7))
    h.add_edge(SynapticEdge(source="b", target="c", weight=0.6))
    return h


def test_predict_returns_activation_map():
    h = _three_node_chain()
    pc = PredictiveCortex(h)
    am = pc.predict(["a"])
    assert "a" in am.activations and "b" in am.activations and "c" in am.activations


def test_compute_error_categorises_nodes():
    """high_surprise = actual ∖ predicted, confirmed = ∩, missed = predicted ∖ actual."""
    pc = PredictiveCortex(_three_node_chain())
    predicted = ActivationMap(activations={"a": 1.0, "b": 0.5, "c": 0.3})
    actual    = ActivationMap(activations={"b": 0.5, "d": 0.4})  # b shared, d new, a/c missed
    err = pc.compute_error(predicted, actual)
    assert err.high_surprise == frozenset({"d"})
    assert err.confirmed == frozenset({"b"})
    assert err.missed == frozenset({"a", "c"})


def test_surprise_magnitude_in_unit_interval():
    pc = PredictiveCortex(_three_node_chain())
    predicted = ActivationMap(activations={"a": 1.0})
    actual    = ActivationMap(activations={"x": 1.0, "y": 1.0})  # 2/2 surprise
    err = pc.compute_error(predicted, actual)
    assert 0.0 <= err.surprise_magnitude <= 1.0
    assert err.surprise_magnitude == 1.0


def test_surprise_magnitude_zero_when_perfect_prediction():
    pc = PredictiveCortex(_three_node_chain())
    predicted = ActivationMap(activations={"a": 1.0, "b": 0.5})
    actual    = ActivationMap(activations={"a": 1.0, "b": 0.5})
    err = pc.compute_error(predicted, actual)
    assert err.surprise_magnitude == 0.0


# =============== Prediction-error-modulated Hebbian ===============

def test_hebbian_strengthens_25x_for_surprise():
    """Surprise nodes attract a 2.5x multiplier on edges feeding into them."""
    h = Hippocampus()
    for n in "ab":
        h.upsert_node(n)
    edge = SynapticEdge(source="a", target="b", weight=0.5, plasticity=0.1)
    h.add_edge(edge)
    upd = HebbianUpdater()
    activation = ActivationMap(
        activations={"a": 1.0, "b": 0.8},
        fired_sequence=[("a", 0), ("b", 1)],
    )
    error = PredictionError(
        high_surprise=frozenset({"b"}),
        confirmed=frozenset(),
        missed=frozenset(),
        surprise_magnitude=1.0,
    )
    w0 = edge.weight
    upd.update(h, activation, error=error)
    # SURPRISE_MULTIPLIER * plasticity = 2.5 * 0.1 = 0.25 increase
    assert abs((edge.weight - w0) - 0.25) < 1e-6


def test_hebbian_strengthens_04x_for_confirmed():
    """Confirmed nodes attract a 0.4x multiplier — already-known links learn slowly."""
    h = Hippocampus()
    for n in "ab":
        h.upsert_node(n)
    edge = SynapticEdge(source="a", target="b", weight=0.5, plasticity=0.1)
    h.add_edge(edge)
    upd = HebbianUpdater()
    activation = ActivationMap(
        activations={"a": 1.0, "b": 0.8},
        fired_sequence=[("a", 0), ("b", 1)],
    )
    error = PredictionError(
        high_surprise=frozenset(),
        confirmed=frozenset({"b"}),
        missed=frozenset(),
        surprise_magnitude=0.0,
    )
    w0 = edge.weight
    upd.update(h, activation, error=error)
    # CONFIRMED_MULTIPLIER * plasticity = 0.4 * 0.1 = 0.04 increase
    assert abs((edge.weight - w0) - 0.04) < 1e-6


def test_hebbian_weakens_missed_predictions():
    """Predicted-but-missed nodes attract a 0.95 weakening factor on inbound edges."""
    h = Hippocampus()
    for n in "abc":
        h.upsert_node(n)
    edge_to_missed = SynapticEdge(source="a", target="b", weight=0.6)  # b missed
    edge_other     = SynapticEdge(source="a", target="c", weight=0.6)  # c didn't fire
    h.add_edge(edge_to_missed)
    h.add_edge(edge_other)
    upd = HebbianUpdater(ltd_factor=1.0)  # disable normal LTD to isolate missed
    activation = ActivationMap(
        activations={"a": 1.0},
        fired_sequence=[("a", 0)],
    )
    error = PredictionError(
        high_surprise=frozenset(),
        confirmed=frozenset(),
        missed=frozenset({"b"}),
        surprise_magnitude=0.0,
    )
    upd.update(h, activation, error=error)
    assert edge_to_missed.weight < 0.6
    # The 0.95 factor should have been applied
    assert abs(edge_to_missed.weight - 0.6 * MISSED_FACTOR) < 1e-6
    # c's edge: not reinforced (c not in fired set with a) so normal LTD-skipped
    # since ltd_factor=1.0 disables LTD — should still be 0.6
    assert abs(edge_other.weight - 0.6) < 1e-6


def test_hebbian_passes_without_error_kwarg():
    """Backwards compat: Phase 3 callers (no error=) still work."""
    h = _three_node_chain()
    upd = HebbianUpdater()
    am = propagate(h, ["a"])
    result = upd.update(h, am)
    assert result.strengthened > 0


def test_hebbian_result_tracks_error_categories():
    h = Hippocampus()
    for n in "ab":
        h.upsert_node(n)
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.5))
    activation = ActivationMap(
        activations={"a": 1.0, "b": 0.8},
        fired_sequence=[("a", 0), ("b", 1)],
    )
    error = PredictionError(
        high_surprise=frozenset({"b"}),
        confirmed=frozenset(),
        missed=frozenset(),
        surprise_magnitude=1.0,
    )
    result = HebbianUpdater().update(h, activation, error=error)
    assert result.surprise_strengthened == 1
    assert result.confirmed_strengthened == 0


# =============== Matryoshka — embeddings + truncate ===============

def test_offline_embedding_is_768_dim_and_deterministic():
    a = _offline_embed("python is great")
    b = _offline_embed("python is great")
    c = _offline_embed("kafka is different")
    assert len(a) == FULL_DIM == 768
    assert a == b
    assert a != c


def test_offline_embedding_is_l2_normalised():
    v = _offline_embed("hello world")
    norm = float(np.linalg.norm(np.array(v)))
    assert abs(norm - 1.0) < 1e-5


def test_matryoshka_truncate_returns_correct_dim():
    full = _offline_embed("anything", dim=FULL_DIM)
    truncated = matryoshka_truncate(full, COARSE_DIM)
    assert len(truncated) == COARSE_DIM == 256
    # Truncated vector is also L2-normalised
    norm = float(np.linalg.norm(np.array(truncated)))
    assert abs(norm - 1.0) < 1e-5


def test_matryoshka_truncate_rejects_dim_too_large():
    with pytest.raises(ValueError):
        matryoshka_truncate([0.1] * 100, 256)


def test_cosine_self_similarity_is_one():
    v = _offline_embed("self test")
    assert abs(cosine(v, v) - 1.0) < 1e-5


# =============== ChromaDB — 2-stage retrieval ===============

@pytest.fixture
def vector_store(tmp_path: Path):
    vs = VectorStore(persist_directory=tmp_path / "chroma")
    yield vs


def _seed(vs: VectorStore) -> None:
    docs = [
        ("doc-py",     "Python is a programming language used for data science.",
         {"topic": "code"}),
        ("doc-fast",   "FastAPI is a Python web framework for APIs.",
         {"topic": "code"}),
        ("doc-deep",   "DeepSeek R1 is a 14B parameter local model.",
         {"topic": "ai"}),
        ("doc-cortex", "The synthetic cortex consolidates edges via Hebbian.",
         {"topic": "neuro"}),
        ("doc-fit",    "Deadlift loads the posterior chain from the floor.",
         {"topic": "fitness"}),
    ]
    for cid, text, meta in docs:
        vs.add_sync(cid, text, metadata=meta)


def test_two_stage_retrieval_returns_ranked_results(vector_store):
    _seed(vector_store)
    results = vector_store.query_sync("python web framework",
                                       coarse_k=5, final_k=3)
    assert len(results) > 0
    # results are sorted by score descending
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    # 768-dim cosine reranking yields a result for each candidate (max final_k)
    assert len(results) <= 3


def test_two_stage_uses_full_dim_for_rerank(vector_store):
    """Smoke test that the cosine score is computed on the full vector
    (otherwise score range would be different)."""
    _seed(vector_store)
    results = vector_store.query_sync("python", final_k=2)
    assert results
    for r in results:
        assert -1.0 <= r.score <= 1.0


def test_chromadb_persists_across_restart(tmp_path: Path):
    """CLAUDE.md Phase 7 — embeddings persist across PersistentClient restart."""
    persist = tmp_path / "chroma"

    vs1 = VectorStore(persist_directory=persist)
    _seed(vs1)
    expected = vs1.count()
    assert expected >= 5
    # Close the client, so the second open is a new system that reads the
    # files: a reopen without it shares the first client's running system
    # (tests/conftest.py says so) and never reads from disk. ci run 104's
    # windows shard 2 failed here once, Chroma 1.5.9 "Error creating hnsw
    # segment reader: Nothing found on disk"; not reproduced (BACKLOG W6-B13).
    vs1.client.close()
    del vs1

    vs2 = VectorStore(persist_directory=persist)
    assert vs2.count() == expected
    results = vs2.query_sync("python", final_k=2)
    assert results, "expected results from persisted index after restart"


def test_collection_count_matches_inserts(vector_store):
    assert vector_store.count() == 0
    _seed(vector_store)
    assert vector_store.count() == 5


def test_query_with_no_data_returns_empty(vector_store):
    assert vector_store.query_sync("anything") == []


def test_reset_clears_collections(vector_store):
    _seed(vector_store)
    assert vector_store.count() == 5
    vector_store.reset()
    assert vector_store.count() == 0


def test_add_validates_embedding_dimension(vector_store):
    with pytest.raises(ValueError):
        vector_store.add_sync("bad", "txt", embedding=[0.1] * 100)
