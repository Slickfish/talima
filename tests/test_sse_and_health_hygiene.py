"""Packet H.3b — the three dead contract members, /health, and declared search failure.

Each item here was found by the endpoint audit and each has the same shape:
something present, reviewed, and never verified to actually happen.

  hebbian_update        declared in CLAUDE.md, emitted as `hebbian` — seven
                        characters of naming drift on a path that runs every
                        turn.
  chain_appended        declared since Phase 9, no emit call, and BOTH UI
                        files carry a handler waiting for it.
  kairos_consolidation  same.
  GET /health           polled by the UI every refresh, 404 every time — and
                        because `fetch` resolves on a 404 and nothing checked
                        `r.ok`, the status light read "online" regardless.
  /search/execute       declared an error key its own handler could never
                        reach, because the tool swallowed the exception first.
"""
import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import backend.tools.search as search_mod
from backend.cortex.hebbian import HebbianUpdater
from backend.loop.agentic_loop import BRIDGE_EVENTS, run_turn
from backend.loop.state import NexusLoopState
from backend.memory.chain import NexusArchiveChain
from backend.memory.fingerprint import CortexFingerprinter
from backend.memory.hippocampus import Hippocampus
from backend.memory.kairos import KairosProcess
from backend.memory.neocortex import Neocortex
from backend.memory.procedural import ProceduralMemory
from backend.tools.search import WebSearchTool
from tests._tracked import tracked, tracked_backend

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


class _Provider:
    def __init__(self): self.calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        yield "ok" if self.calls == 1 else "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _events(pending=None, *, full_stack=False):
    """Drive one turn. `full_stack` wires the hippocampus and Hebbian updater,
    without which the Hebbian stage is skipped entirely — the reason the first
    draft of the rename test failed for a reason unrelated to the rename."""
    frames = []
    kwargs = {"provider": _Provider(), "pending_alerts": pending}
    if full_stack:
        # The Hebbian stage needs hippocampus AND hebbian AND a non-None
        # `activation`, and activation only exists when retrieval returned
        # hits — so a retriever with a matching belief is required, not
        # optional scaffolding.
        import tempfile
        from backend.kb.granularity import Granularity
        from backend.kb.retriever import TripleRetriever
        from backend.kb.tiered_store import TripleStore
        store = TripleStore(Path(tempfile.mkdtemp()) / "kb.db")
        store.add(subject="alpha", relation="is_a", object="thing",
                  weight=0.8, tier=1, granularity=Granularity.TOPIC,
                  source="wikidata")
        hippo = Hippocampus()
        kwargs["retriever"] = TripleRetriever(store)
        kwargs["hippocampus"] = hippo
        kwargs["hebbian"] = HebbianUpdater()

    async def go():
        async for f, _s in run_turn(NexusLoopState(), "tell me about alpha",
                                    **kwargs):
            frames.append(f)
    asyncio.run(go())
    out = []
    for f in frames:
        lines = f.split("\n")
        ev = next((l[7:] for l in lines if l.startswith("event: ")), "")
        raw = next((l[6:] for l in lines if l.startswith("data: ")), "")
        try: data = json.loads(raw)
        except Exception: data = raw
        out.append((ev, data))
    return out


# ---------------------------------------------------------------------------
# hebbian_update — naming drift only
# ---------------------------------------------------------------------------

def test_hebbian_update_is_emitted_under_its_declared_name():
    """The payload was always right; only the label was wrong."""
    evs = _events(full_stack=True)
    names = [e for e, _ in evs]
    assert "hebbian_update" in names, (
        "the declared contract member is still not emitted")
    assert "hebbian" not in names, (
        "the old name is still being emitted — renaming means renaming, not "
        "emitting both: every UI handler would fire twice")

    payload = next(d for e, d in evs if e == "hebbian_update")
    # Same fields as before the rename — this was drift, not a redesign.
    for key in ("strengthened", "weakened", "formed", "pruned"):
        assert key in payload, f"payload lost {key} in the rename"


# ---------------------------------------------------------------------------
# chain_appended / kairos_consolidation — real emits through the real bridge
# ---------------------------------------------------------------------------

def _kairos(tmp_path, hippo=None, services_ref=None):
    hippo = hippo if hippo is not None else Hippocampus()
    k = KairosProcess(
        hippo, Neocortex(tmp_path / "kb.db"), HebbianUpdater(), ProceduralMemory(),
        fingerprinter=CortexFingerprinter(),
        archive_chain=NexusArchiveChain(tmp_path / "archive.db"),
        snapshots_dir=tmp_path / "snapshots")
    return k


def test_chain_appended_is_emitted_when_a_block_is_sealed(tmp_path):
    """Sealing is the event this substrate exists to demonstrate. An operator
    watching a seal happen previously saw nothing at all."""
    hippo = Hippocampus()
    hippo.ingest_triple("alpha", "is_a", "thing", weight=0.6)
    k = _kairos(tmp_path, hippo)

    queued: list = []

    async def notify(payload): queued.append(payload)
    k.notify_ui = notify

    block = asyncio.run(k.maybe_snapshot_and_fingerprint(force=True))
    assert block is not None                                  # non-vacuity

    minted = [p for p in queued if p.get("event") == "chain_appended"]
    assert len(minted) == 1
    assert minted[0]["index"] == block.index
    assert minted[0]["hash"] == block.block_hash

    # ...and it survives the bridge under its own name (H.3a).
    evs = _events(pending=list(queued))
    frames = [(e, d) for e, d in evs if e == "chain_appended"]
    assert len(frames) == 1, "sealed, queued, and then lost crossing the bridge"
    assert frames[0][1]["index"] == block.index
    assert "event" not in frames[0][1]


def test_kairos_consolidation_fires_every_cycle_including_zero(tmp_path):
    """Zero is a measurement, not an absence.

    After E.2/E.3 promotion needs three refractory-separated recurrences, so
    `edges_promoted` reads 0 on a real store for a long time. An emit gated on
    a non-zero would therefore never fire — leaving the contract member as
    dead as it was before, which is the defect this packet exists to close.
    "The cycle ran and promoted nothing" is what a reviewer needs to see, and
    it is different from silence.
    """
    from backend.cortex.edges import SynapticEdge

    # Nothing to promote -> the frame STILL fires, carrying zero.
    (tmp_path / "quiet").mkdir(exist_ok=True)
    quiet = _kairos(tmp_path / "quiet")
    queued: list = []
    async def notify(p): queued.append(p)
    quiet.notify_ui = notify
    res = asyncio.run(quiet.run_one_cycle())
    assert res.edges_promoted == 0                            # non-vacuity
    zeros = [p for p in queued if p.get("event") == "kairos_consolidation"]
    assert len(zeros) == 1, "an idle cycle emitted nothing — the reviewer sees silence"
    assert zeros[0]["promoted"] == 0

    # ...and it reaches a client under its own name, carrying the zero.
    evs = _events(pending=[p for p in queued
                           if p.get("event") == "kairos_consolidation"])
    frames = [d for e, d in evs if e == "kairos_consolidation"]
    assert len(frames) == 1 and frames[0]["promoted"] == 0

    # Something to promote -> exactly one frame carrying the count.
    hippo = Hippocampus()
    hippo.ingest_triple("alpha", "is_a", "thing", weight=0.8)
    edge = hippo.get_edge("alpha", "thing")
    edge.fire_count, edge.recurrence_count = 12, 5
    hippo.add_edge(SynapticEdge(source="beta", target="alpha", weight=0.7,
                                fire_count=9, recurrence_count=4))
    (tmp_path / "busy").mkdir(exist_ok=True)
    busy = _kairos(tmp_path / "busy", hippo)
    queued2: list = []
    async def notify2(p): queued2.append(p)
    busy.notify_ui = notify2
    result = asyncio.run(busy.run_one_cycle())
    assert result.edges_promoted > 0                          # non-vacuity

    minted = [p for p in queued2 if p.get("event") == "kairos_consolidation"]
    assert len(minted) == 1
    assert minted[0]["promoted"] == result.edges_promoted

    evs = _events(pending=list(queued2))
    assert [d for e, d in evs if e == "kairos_consolidation"], (
        "promoted, queued, then lost crossing the bridge")


def test_the_three_dead_contract_members_are_all_reachable_now():
    """The audit's headline finding, closed. Asserted against the emit sites
    rather than a memory of what was changed."""
    src = (Path(__file__).resolve().parent.parent / "backend")
    text = "\n".join(p.read_text(encoding="utf-8") for p in tracked_backend())
    assert 'sse_event("hebbian_update"' in text
    assert '"event":        "chain_appended"' in text
    assert '"event":    "kairos_consolidation"' in text
    # The two KAIROS ones only reach a client if the bridge routes them.
    assert {"chain_appended", "kairos_consolidation"} <= BRIDGE_EVENTS


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    import os
    os.environ["NEXUS_API_TOKEN"] = "h3b"
    from backend.server import create_app, create_services
    return create_app(create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots"))


def test_health_and_healthz_both_serve_the_same_payload(app):
    with TestClient(app) as c:
        a, b = c.get("/health"), c.get("/healthz")
    assert a.status_code == 200 and b.status_code == 200
    assert set(a.json()) == set(b.json())
    for k in ("ollama", "kb_triples", "embedding_degraded",
              "embedding_degraded_reason"):
        assert k in a.json(), f"/health alias is missing {k}"


def test_ui_checks_response_ok_and_reads_the_body():
    """The three bugs in three lines, pinned.

    `fetch` resolves on a 404, so without an `r.ok` check the light read
    "online" whether the server was healthy, unhealthy, or serving a route
    that did not exist — falsely reassuring, which is worse than broken.
    """
    body = (FRONTEND / "nexus_ui.html").read_text(encoding="utf-8")
    assert "fetch('/health')" not in body, "still polling the 404 path"
    assert "fetch('/healthz')" in body
    assert "r.ok" in body, "no status check — a 404 would still read as online"
    assert "embedding_degraded" in body, (
        "P1/F-10 shipped this flag specifically to make degradation visible "
        "and no UI has ever read it")
    assert "degraded" in body


def test_no_source_documents_health_as_the_canonical_path():
    """`embeddings.py:31` documented `GET /health` as the status path, which
    is how the UI came to poll it. The alias makes it work; the docs should
    still name the canonical route.

    The alias's own registration is excluded — that line is the fix, not the
    bug (a first draft of this test flagged it, which was the test being
    wrong, not the code).
    """
    root = Path(__file__).resolve().parent.parent
    for f in tracked_backend() + tracked("frontend/*.html"):
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if '@app.get("/health")' in line:
                continue                      # the alias declaration itself
            assert "GET /health " not in line and "/health'" not in line, (
                f"{f.name}:{i} names /health where /healthz is canonical: "
                f"{line.strip()}")


# ---------------------------------------------------------------------------
# /search/execute — declared failure, additively
# ---------------------------------------------------------------------------

def test_tool_still_fails_soft_and_now_records_why(monkeypatch):
    """Both Packet 10a sentinels pin `== []`. The return type is unchanged;
    the reason rides a side-channel."""
    async def boom(*a, **kw):
        raise httpx.ConnectError("backend unreachable")
    monkeypatch.setattr(search_mod, "fetch_json", boom)
    tool = WebSearchTool(base_url="http://example.invalid")

    out = asyncio.run(tool.search("anything", confirmed=True))
    assert out == []                                   # the 10a contract
    assert "ConnectError" in tool.last_error
    assert "unreachable" in tool.last_error


def test_last_error_clears_on_the_next_successful_search(monkeypatch):
    """A stale reason attached to a later successful search would be worse
    than no reason at all."""
    tool = WebSearchTool(base_url="http://example.invalid")

    async def boom(*a, **kw): raise httpx.ConnectError("down")
    monkeypatch.setattr(search_mod, "fetch_json", boom)
    asyncio.run(tool.search("q", confirmed=True))
    assert tool.last_error

    async def fine(*a, **kw): return {"results": []}
    monkeypatch.setattr(search_mod, "fetch_json", fine)
    assert asyncio.run(tool.search("q", confirmed=True)) == []
    assert tool.last_error == "", "a stale failure reason survived a good call"


def test_endpoint_distinguishes_failure_from_no_results(app, monkeypatch):
    """The finding itself: `count: 0` meant both, identically."""
    async def boom(*a, **kw): raise httpx.ConnectError("searxng down")
    monkeypatch.setattr(search_mod, "fetch_json", boom)
    with TestClient(app) as c:
        failed = c.post("/search/execute",
                        json={"query": "latest news", "confirmed": True},
                        headers={"Authorization": "Bearer h3b"}).json()

    async def empty(*a, **kw): return {"results": []}
    monkeypatch.setattr(search_mod, "fetch_json", empty)
    with TestClient(app) as c:
        none = c.post("/search/execute",
                      json={"query": "latest news", "confirmed": True},
                      headers={"Authorization": "Bearer h3b"}).json()

    assert failed["count"] == none["count"] == 0        # indistinguishable before
    assert failed["search_failed"] is True
    assert "ConnectError" in failed["error"]
    assert none["search_failed"] is False
    assert "error" not in none

    # Additive: every pre-existing key keeps its meaning.
    for key in ("query", "results", "count"):
        assert key in failed and key in none
