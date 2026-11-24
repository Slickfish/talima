"""Packet W.1 — the channel is REACHABLE, not merely present.

WHY THIS FILE IS SEPARATE FROM test_semantic_recall.py
    That file proves the mechanism works when you hand it an index and a query
    vector. This one proves that a running NEXUS ever does.

    S.4 is the precedent and the warning. O.2b built assertion detection AND
    `POST /kb/assert/confirm`, and built no offer — so the feature existed,
    passed its tests, and was reachable only by someone who had read the
    source. "Present" and "reachable" are different claims and only one of
    them is worth anything to an operator.

    So: does `create_services` give the retriever an index; does a turn embed
    the query (exactly once, and only when the channel is on); does KAIROS
    fill the index; does the Law 3 backfill work; and does anything report
    coverage.

THE COST CLAIM IS ASSERTED HERE, NOT DESCRIBED
    W.0.8 promised one provider call per turn and none when the channel is
    off. That is a number, so it is counted rather than reasoned about — the
    same discipline R.4 applied to the 145 outbound calls it removed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.kb.semantic_index import SemanticIndex, surface_of
from backend.server import create_app, create_services

from tests._toy_embed import toy_embed

TOKEN = "packet-w-token"


class _CountingProvider:
    """Answers, embeds meaningfully, and counts every embedding it is asked for."""

    def __init__(self):
        self.embeddings = 0
        self.embedded_texts: list[str] = []

    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "noted."

    async def get_embedding(self, text):
        self.embeddings += 1
        self.embedded_texts.append(text)
        return toy_embed(text)

    async def list_models(self): return []
    async def health(self): return False


def _client(tmp_path, monkeypatch, provider=None):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    services = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots",
        provider=provider or _CountingProvider())
    return TestClient(create_app(services)), services


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def _seed(services, n=4):
    for i in range(n):
        services.store.add(subject=f"subject {i}", relation="is_a",
                           object=f"object {i}", weight=0.6, tier=2,
                           granularity=Granularity.TOPIC, source="extraction")


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------

def test_create_services_gives_the_retriever_an_index(tmp_path, monkeypatch):
    _c, services = _client(tmp_path, monkeypatch)
    assert services.semantic is not None
    assert services.retriever.semantic is services.semantic


def test_constructing_services_does_not_create_the_table(tmp_path, monkeypatch):
    """Installing W.1 must not change an operator's `kb.db` at all — not even
    by an empty table appearing in it for a capability nobody turned on."""
    _c, services = _client(tmp_path, monkeypatch)
    tables = set(services.store.conn.tables())
    assert "triple_embeddings" not in tables


# ---------------------------------------------------------------------------
# the per-turn cost, counted
# ---------------------------------------------------------------------------

def test_a_turn_costs_no_embedding_when_the_channel_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXUS_SEMANTIC_SLOTS", raising=False)
    provider = _CountingProvider()
    client, _s = _client(tmp_path, monkeypatch, provider)
    with client:
        r = client.post("/chat", json={"message": "what about cardiac safety?"},
                        headers=_auth())
    assert r.status_code == 200, r.text
    assert provider.embeddings == 0, (
        f"the default configuration paid for {provider.embeddings} embedding(s)")


def test_a_turn_costs_exactly_one_embedding_when_the_channel_is_on(
        tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
    provider = _CountingProvider()
    client, _s = _client(tmp_path, monkeypatch, provider)
    with client:
        r = client.post("/chat", json={"message": "what about cardiac safety?"},
                        headers=_auth())
    assert r.status_code == 200, r.text
    assert provider.embeddings == 1, (
        f"expected one query embedding per turn, got {provider.embeddings}: "
        f"{provider.embedded_texts}")
    # W.2 — and it goes out as a QUERY, not a document. The asymmetry is the
    # measured half of the prefix change (signal 0.4047 -> 0.5251), so the
    # side it is applied to is worth asserting rather than assuming.
    assert provider.embedded_texts == ["search_query: what about cardiac safety?"]


# ---------------------------------------------------------------------------
# KAIROS fills the index
# ---------------------------------------------------------------------------

def _cycle(services):
    """Drive one cycle directly.

    NOT through `POST /kairos/cycle` inside `with client:` — entering the
    lifespan starts the background KAIROS daemon, which runs a cycle of its
    own and indexes everything before the forced cycle is reached. The first
    draft of these tests measured 0 indexed for exactly that reason, which is
    the daemon working rather than the job failing.
    """
    import asyncio
    services.kairos.session_ended()
    return asyncio.run(services.kairos.run_one_cycle(force_snapshot=False))


def test_kairos_indexes_on_the_idle_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
    _c, services = _client(tmp_path, monkeypatch)
    _seed(services, 4)
    result = _cycle(services)
    assert result.semantic_indexed == 4, result
    assert result.semantic_index_error == ""
    assert services.semantic.coverage()["indexed"] == 4


def test_kairos_indexes_nothing_when_the_channel_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXUS_SEMANTIC_SLOTS", raising=False)
    _c, services = _client(tmp_path, monkeypatch)
    _seed(services, 4)
    result = _cycle(services)
    assert result.semantic_indexed == 0
    assert services.semantic.coverage()["indexed"] == 0


def test_a_down_provider_degrades_the_index_rather_than_the_cycle(
        tmp_path, monkeypatch):
    """D.1's rule on the new job, and the honest version of what happens.

    `embed_via_provider` catches the provider failure and returns the offline
    hash vector, so indexing SUCCEEDS with a vector that carries no meaning
    (W.0 measured that). The cycle survives — and the degradation is DECLARED
    on `/healthz`, which before W.1 it could not be.
    """
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")

    class _Broken(_CountingProvider):
        async def get_embedding(self, text):
            raise RuntimeError("embedder exploded")

    from backend.vector import embeddings as emb
    emb._mark_healthy()
    client, services = _client(tmp_path, monkeypatch, _Broken())
    _seed(services, 2)
    result = _cycle(services)
    assert result.skipped is False
    assert result.semantic_indexed == 2
    health = client.get("/healthz", headers=_auth()).json()
    assert health["embedding_degraded"] is True, (
        "the index was filled with hash vectors and nothing said so")
    emb._mark_healthy()


# ---------------------------------------------------------------------------
# Law 3 backfill, and the surfaces that report coverage
# ---------------------------------------------------------------------------

def test_backfill_requires_confirmation(tmp_path, monkeypatch):
    client, services = _client(tmp_path, monkeypatch)
    _seed(services, 3)
    with client:
        r = client.post("/kb/index/confirm", json={"confirmed": False},
                        headers=_auth())
    assert r.status_code == 422, r.text
    assert services.semantic.coverage()["indexed"] == 0


def test_backfill_indexes_when_confirmed(tmp_path, monkeypatch):
    client, services = _client(tmp_path, monkeypatch)
    _seed(services, 3)
    with client:
        r = client.post("/kb/index/confirm", json={"confirmed": True},
                        headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["indexed"] == 3
    assert body["coverage"]["indexed"] == 3
    assert body["coverage"]["pending"] == 0


def test_backfill_works_even_with_the_channel_off(tmp_path, monkeypatch):
    """Deliberate: an operator should be able to build the index BEFORE
    turning the channel on, so the first question after switching it on is
    answered from a full index rather than an empty one."""
    monkeypatch.delenv("NEXUS_SEMANTIC_SLOTS", raising=False)
    client, services = _client(tmp_path, monkeypatch)
    _seed(services, 3)
    with client:
        r = client.post("/kb/index/confirm", json={"confirmed": True},
                        headers=_auth())
    assert r.json()["indexed"] == 3


def test_the_pending_surface_reports_coverage_and_names_its_confirm_route(
        tmp_path, monkeypatch):
    client, services = _client(tmp_path, monkeypatch)
    _seed(services, 3)
    with client:
        body = client.get("/kb/index/pending", headers=_auth()).json()
    assert body["live_beliefs"] == 3
    assert body["indexed"] == 0
    assert body["pending"] == 3
    assert len(body["sample_unindexed"]) == 3
    assert body["confirm"]["endpoint"] == "/kb/index/confirm"


def test_cortex_stats_reports_the_channel_and_its_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
    client, services = _client(tmp_path, monkeypatch)
    _seed(services, 2)
    with client:
        client.post("/kb/index/confirm", json={"confirmed": True}, headers=_auth())
        stats = client.get("/cortex/stats", headers=_auth()).json()["semantic_index"]
    assert stats["enabled"] is True
    assert stats["slots"] == 5
    assert stats["indexed"] == 2
    assert stats["pending"] == 0


def test_coverage_distinguishes_off_from_empty(tmp_path, monkeypatch):
    """I-6. A store with 867 beliefs and 0 embeddings answers every question
    lexically and reports no error; so does one with the channel switched off.
    They are different problems with different fixes."""
    monkeypatch.delenv("NEXUS_SEMANTIC_SLOTS", raising=False)
    client, services = _client(tmp_path, monkeypatch)
    _seed(services, 2)
    # No lifespan here: starting it would start KAIROS, which would index the
    # store and make "on but empty" unreachable.
    off = client.get("/cortex/stats", headers=_auth()).json()["semantic_index"]
    assert off["enabled"] is False and off["indexed"] == 0

    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
    on_empty = client.get("/cortex/stats", headers=_auth()).json()["semantic_index"]
    assert on_empty["enabled"] is True and on_empty["indexed"] == 0
    assert on_empty["pending"] == 2


# ---------------------------------------------------------------------------
# end to end, through the running app
# ---------------------------------------------------------------------------

def test_the_operators_question_reaches_the_claim_through_a_live_turn(
        tmp_path, monkeypatch):
    """The whole packet, through /chat rather than through the library.

    The toy embedder is the one carried by the provider here, so this proves
    plumbing end to end — the claim about a REAL model's judgement is
    `scripts/measure_retrieval.py`'s, not this test's.
    """
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
    client, services = _client(tmp_path, monkeypatch)
    services.store.add(
        subject="mean placebo-corrected QTc change at 600mg supratherapeutic dose",
        relation="was", object="11.4ms", weight=0.8, tier=1,
        granularity=Granularity.TOPIC, source="human_assertion")
    services.store.add(subject="QT interval", relation="is_a_segment_of",
                       object="ECG", weight=0.7, tier=2,
                       granularity=Granularity.TOPIC, source="extraction")
    with client:
        client.post("/kb/index/confirm", json={"confirmed": True}, headers=_auth())
        body = client.post("/chat",
                           json={"message": "what do we know about cardiac safety?"},
                           headers=_auth()).text

    # `triples_injected` carries a LIST of triples. CLAUDE.md declared
    # {count, concepts} until W1, which aligned every declaration to
    # emission (tests/test_w1_sse_payloads.py reconciles the payloads).
    injected = []
    for frame in body.split("\n\n"):
        if "event: triples_injected" not in frame:
            continue
        raw = next((l[6:] for l in frame.split("\n") if l.startswith("data: ")), "")
        injected.extend(json.loads(raw))
    assert injected, (
        "the turn injected nothing for the question this packet exists to fix")
    assert any("11.4ms" in t["o"] for t in injected), (
        "the turn injected triples, but not the asserted finding: " + str(injected))
