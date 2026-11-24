"""Pre-Phase-10c Governance Fix A — Semantic Deduplication.

Spec contract under test:
  - SemanticDeduplicator.scan() flags near-duplicates above threshold,
    returns [] when none exist
  - Symmetric pair guard — (a,b) and (b,a) only flagged once
  - MAX_CANDIDATES_PER_CYCLE truncation
  - suggested_keep prefers higher-weight triple
  - /kb/dedup/pending returns candidate list
  - /kb/dedup/confirm — Law 3: confirmed=False → 422
                       Law 3: invalid `keep` → 422
                       Law 4: archives to tier 4 (never deletes)
                       Removed from pending list after confirmation
  - KAIROS run_one_cycle() populates last_dedup_candidates and emits
    kb_pressure_alert via the notify_ui hook
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.deduplicator import (
    DuplicateCandidate,
    MAX_CANDIDATES_PER_CYCLE,
    SIMILARITY_THRESHOLD,
    SemanticDeduplicator,
)
from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore
from backend.server import create_app, create_services


# --------------- minimal in-memory vector stub ---------------

class _StubVector:
    """In-memory vector store stub. The real ChromaDB-backed VectorStore
    lives in backend/vector/chroma.py; this tiny stub returns prearranged
    matches so the dedup scan exercises only its own logic."""
    def __init__(self, surface_to_matches: dict):
        self.surface_to_matches = surface_to_matches

    async def query(self, text: str, *, final_k: int = 10):
        return self.surface_to_matches.get(text, [])


def _seed_pair(store: TripleStore, *, subject_a, subject_b, weight_a, weight_b):
    """Two triples that share (relation, object) but have different subjects;
    surface forms differ enough that an embedding model would call them
    near-duplicates."""
    store.add(
        subject=subject_a, relation="is_a", object="programming_language",
        weight=weight_a, tier=2, granularity=Granularity.TOPIC, source="t",
    )
    store.add(
        subject=subject_b, relation="is_a", object="programming_language",
        weight=weight_b, tier=2, granularity=Granularity.TOPIC, source="t",
    )


# --------------- direct deduplicator tests ---------------

def test_scan_returns_candidates_above_threshold(tmp_path: Path):
    store = TripleStore(tmp_path / "kb.db")
    _seed_pair(store, subject_a="python", subject_b="py3", weight_a=0.9, weight_b=0.4)
    surface_a = "python is_a programming_language"
    surface_b = "py3 is_a programming_language"
    vec = _StubVector({
        surface_a: [{"score": 0.95, "doc": surface_b}],
        surface_b: [{"score": 0.95, "doc": surface_a}],
    })
    dedup = SemanticDeduplicator(vec, store)
    out = asyncio.run(dedup.scan())
    assert len(out) == 1
    cand = out[0]
    assert isinstance(cand, DuplicateCandidate)
    assert cand.similarity >= SIMILARITY_THRESHOLD
    store.close()


def test_scan_returns_empty_when_no_near_duplicates(tmp_path: Path):
    store = TripleStore(tmp_path / "kb.db")
    _seed_pair(store, subject_a="python", subject_b="kafka", weight_a=0.5, weight_b=0.5)
    # Vector returns sub-threshold scores
    surface_a = "python is_a programming_language"
    surface_b = "kafka is_a programming_language"
    vec = _StubVector({
        surface_a: [{"score": 0.30, "doc": surface_b}],
        surface_b: [{"score": 0.30, "doc": surface_a}],
    })
    out = asyncio.run(SemanticDeduplicator(vec, store).scan())
    assert out == []
    store.close()


def test_scan_does_not_surface_symmetric_pair_twice(tmp_path: Path):
    store = TripleStore(tmp_path / "kb.db")
    _seed_pair(store, subject_a="alpha", subject_b="beta", weight_a=0.7, weight_b=0.6)
    sa = "alpha is_a programming_language"
    sb = "beta is_a programming_language"
    vec = _StubVector({
        sa: [{"score": 0.95, "doc": sb}],
        sb: [{"score": 0.95, "doc": sa}],
    })
    out = asyncio.run(SemanticDeduplicator(vec, store).scan())
    assert len(out) == 1, "symmetric pair must only fire once"
    store.close()


def test_scan_respects_max_candidates_per_cycle(tmp_path: Path):
    store = TripleStore(tmp_path / "kb.db")
    # Seed N+5 distinct near-duplicate pairs that all match each other
    n = MAX_CANDIDATES_PER_CYCLE + 5
    surfaces = []
    for i in range(n):
        store.add(
            subject=f"concept_{i}", relation="is_a", object="thing",
            weight=0.5, tier=2, granularity=Granularity.TOPIC, source="t",
        )
        surfaces.append(f"concept_{i} is_a thing")
    matches = {s: [{"score": 0.95, "doc": surfaces[(i + 1) % n]}]
               for i, s in enumerate(surfaces)}
    out = asyncio.run(SemanticDeduplicator(_StubVector(matches), store).scan())
    assert len(out) <= MAX_CANDIDATES_PER_CYCLE
    store.close()


def test_suggested_keep_prefers_higher_weight_triple(tmp_path: Path):
    store = TripleStore(tmp_path / "kb.db")
    _seed_pair(store, subject_a="strong", subject_b="weak", weight_a=0.9, weight_b=0.1)
    sa = "strong is_a programming_language"
    sb = "weak is_a programming_language"
    vec = _StubVector({
        sa: [{"score": 0.95, "doc": sb}],
        sb: [{"score": 0.95, "doc": sa}],
    })
    [cand] = asyncio.run(SemanticDeduplicator(vec, store).scan())
    # The higher-weight side ('strong', weight 0.9) is "a" in candidate
    if cand.triple_a["subject"] == "strong":
        assert cand.suggested_keep == "a"
    else:
        assert cand.suggested_keep == "b"
    store.close()


# --------------- /kb/dedup/pending + /kb/dedup/confirm endpoints ---------------

@pytest.fixture
def app_services(tmp_path: Path):
    services = create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "ch",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snaps",
    )
    # Seed a dedup candidate manually so endpoint tests don't need a vector run
    services.store.add(
        subject="kept", relation="is_a", object="thing",
        weight=0.9, tier=2, granularity=Granularity.TOPIC, source="t",
    )
    services.store.add(
        subject="loser", relation="is_a", object="thing",
        weight=0.1, tier=2, granularity=Granularity.TOPIC, source="t",
    )
    a = services.store.get_relevant(top_k=10)
    a_dict = next(t for t in a if t["subject"] == "kept")
    b_dict = next(t for t in a if t["subject"] == "loser")
    services.last_dedup_candidates.append(DuplicateCandidate(
        triple_a=a_dict, triple_b=b_dict, similarity=0.95,
        suggested_keep="a", reason="Higher weight (0.90 vs 0.10)",
    ))
    app = create_app(services)
    yield app, services


def test_pending_endpoint_returns_candidate_list(app_services):
    app, services = app_services
    with TestClient(app) as c:
        r = c.get("/kb/dedup/pending")
    assert r.status_code == 200
    body = r.json()
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["similarity"] == 0.95


def test_confirm_without_confirmed_returns_422(app_services):
    """Law 3 — confirmed=False is a hard refusal."""
    app, _ = app_services
    with TestClient(app) as c:
        r = c.post("/kb/dedup/confirm", json={
            "subject_a": "kept", "relation_a": "is_a", "object_a": "thing",
            "subject_b": "loser", "relation_b": "is_a", "object_b": "thing",
            "keep": "a", "confirmed": False,
        })
    assert r.status_code == 422
    assert "Law 3" in r.json().get("error", "")


def test_confirm_with_invalid_keep_returns_422(app_services):
    app, _ = app_services
    with TestClient(app) as c:
        r = c.post("/kb/dedup/confirm", json={
            "subject_a": "kept", "relation_a": "is_a", "object_a": "thing",
            "subject_b": "loser", "relation_b": "is_a", "object_b": "thing",
            "keep": "neither", "confirmed": True,
        })
    assert r.status_code == 422


def test_confirm_archives_loser(app_services):
    """Law 4 — archived, never deleted.

    F.2 changed the mechanism, not the contract: the loser used to be marked
    by `tier = 4`, which was not a tier (it raised `KeyError: 4` out of
    `rebalance()` and `current_weight()`). It is now marked by the record-layer
    `archived` column. This test asserts the contract on both layers, which the
    tier-4 version could not: the belief is intact in the RECORD, and gone from
    active RETRIEVAL. Before F.2 a tier-4 row was still returned by
    `get_relevant`/`search_terms` — it merely sorted last — so "archived" never
    actually removed it from the candidate pool.
    """
    app, services = app_services
    with TestClient(app) as c:
        r = c.post("/kb/dedup/confirm", json={
            "subject_a": "kept", "relation_a": "is_a", "object_a": "thing",
            "subject_b": "loser", "relation_b": "is_a", "object_b": "thing",
            "keep": "a", "confirmed": True,
        })
    assert r.status_code == 200
    body = r.json()
    assert body["kept"] == "a"
    assert body["rows_archived"] == 1
    assert body["tier"] == 4 and body["tier_deprecated"] is True  # P2 alias

    # RECORD layer — the belief survives intact, with every field it had.
    loser = next(t for t in services.store.all() if t.subject == "loser")
    assert loser.archived is True
    assert loser.tier in (0, 1, 2, 3)          # a real tier, not the sentinel
    assert loser.weight > 0 and loser.source and loser.created_at > 0

    # PROMINENCE layer — excluded from retrieval, both surfaces.
    assert not [t for t in services.store.get_relevant(top_k=10)
                if t["subject"] == "loser"]
    assert not [t for t in services.store.search_terms(["loser"], limit=10)]

    # The winner is untouched on both layers.
    kept = next(t for t in services.store.all() if t.subject == "kept")
    assert kept.archived is False
    assert [t for t in services.store.get_relevant(top_k=10)
            if t["subject"] == "kept"]


def test_confirmed_pair_removed_from_pending_list(app_services):
    """Cannot be re-confirmed — pending list drops the entry."""
    app, services = app_services
    with TestClient(app) as c:
        c.post("/kb/dedup/confirm", json={
            "subject_a": "kept", "relation_a": "is_a", "object_a": "thing",
            "subject_b": "loser", "relation_b": "is_a", "object_b": "thing",
            "keep": "a", "confirmed": True,
        })
    assert services.last_dedup_candidates == []


# --------------- KAIROS scan + notify_ui hook ---------------

class _StubProcedural:
    def promote(self, *a, **kw): return 0


def test_kairos_run_one_cycle_populates_candidates_and_emits_alert(tmp_path: Path):
    """End-to-end: KAIROS idle cycle runs the deduplicator scan,
    populates services.last_dedup_candidates, and pushes a
    kb_pressure_alert payload through notify_ui."""
    services = create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "ch",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snaps",
    )
    _seed_pair(services.store, subject_a="alpha", subject_b="beta",
               weight_a=0.7, weight_b=0.6)
    # Replace deduplicator's vector with a deterministic stub
    surface_a = "alpha is_a programming_language"
    surface_b = "beta is_a programming_language"
    services.deduplicator.vector = _StubVector({
        surface_a: [{"score": 0.95, "doc": surface_b}],
        surface_b: [{"score": 0.95, "doc": surface_a}],
    })

    captured: list[dict] = []
    async def capture(payload: dict):
        captured.append(payload)
    services.kairos.notify_ui = capture

    res = asyncio.run(services.kairos.run_one_cycle())
    assert res.skipped is False
    assert res.dedup_candidates >= 1
    assert len(services.last_dedup_candidates) >= 1
    assert any(
        p.get("type") == "semantic_duplicates" for p in captured
    ), f"expected semantic_duplicates alert, got {captured}"
    services.store.close()
    services.neocortex.close()
    services.archive_chain.close()
