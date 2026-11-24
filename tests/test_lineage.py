"""P3 — answer lineage (L2; closes REVIEW_REPORT F-22's per-answer scope gap).

Pins: one record per grounded turn with the exact injected set; canonical
record hashing; KAIROS digest-sealing + backfill (archive.db keeps exactly
one writer); tamper detectability; auth'd read endpoints with honest belief
resolution; unsealed records surviving restart.
"""
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.cortex.hebbian import HebbianUpdater
from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import run_turn
from backend.loop.state import NexusLoopState
from backend.memory.chain import NexusArchiveChain
from backend.memory.fingerprint import CortexFingerprinter
from backend.memory.hippocampus import Hippocampus
from backend.memory.kairos import KairosProcess
from backend.memory.lineage import LineageStore
from backend.memory.neocortex import Neocortex
from backend.memory.procedural import ProceduralMemory
from backend.server import create_app


class _StubProvider:
    async def stream_chat(self, model, messages, num_ctx=8192, **kw):
        yield "grounded "
        yield "response"

    async def get_embedding(self, text):  return [0.0] * 768
    async def list_models(self):          return []
    async def health(self):               return True


def _drain(gen):
    """Collect (frames, final_state) from the async run_turn generator."""
    async def _run():
        frames, state = [], None
        async for frame, st in gen:
            frames.append(frame)
            state = st
        return frames, state
    return asyncio.run(_run())


@pytest.fixture
def kb(tmp_path: Path):
    store = TripleStore(tmp_path / "kb.db")
    lineage = LineageStore(tmp_path / "kb.db")
    yield store, lineage, tmp_path
    store.close(); lineage.close()


# ---------- 1. record written per turn with the correct injected set ----------

def test_record_written_per_turn_with_injected_set(kb):
    store, lineage, _ = kb
    bid = store.add(subject="interval_training", relation="improves",
                    object="cardiovascular_health", weight=0.7, tier=2,
                    granularity=Granularity.TOPIC, source="extraction")

    frames, _ = _drain(run_turn(
        NexusLoopState(), "tell me about interval training",
        retriever=TripleRetriever(store),
        provider=_StubProvider(),
        lineage=lineage,
    ))

    assert lineage.count() == 1
    rec = lineage.recent(1)[0]
    assert [i["belief_id"] for i in rec.injected] == [bid]
    assert rec.injected[0]["tier_at_injection"] == 2
    assert rec.injected[0]["weight_at_injection"] > 0
    assert rec.model_id                       # the model that streamed
    assert rec.sealed_block is None           # unsealed until a KAIROS block
    # done frame carries the lineage handle (seam Flow 2 needs it)
    done = next(f for f in frames if '"done"' in f or "event: done" in f)
    assert rec.lineage_id in done


# ---------- 2. record_hash is canonical ----------

def test_record_hash_stable_and_canonical():
    payload = {
        "lineage_id": "L1", "ts": 111.5, "query_hash": "q" * 64,
        "injected": [{"belief_id": 1, "weight_at_injection": 0.5,
                      "tier_at_injection": 2}],
        "contradictions": [], "model_id": "m",
        "prompt_prefix_hash": "p" * 64, "response_hash": "r" * 64,
    }
    shuffled = dict(reversed(list(payload.items())))
    assert (LineageStore.compute_record_hash(payload)
            == LineageStore.compute_record_hash(shuffled))
    tampered = dict(payload, response_hash="x" * 64)
    assert (LineageStore.compute_record_hash(tampered)
            != LineageStore.compute_record_hash(payload))


# ---------- 3. KAIROS seals + backfills; single archive writer ----------

def _mini_kairos(tmp_path, lineage):
    return KairosProcess(
        Hippocampus(), Neocortex(tmp_path / "kb.db"), HebbianUpdater(),
        ProceduralMemory(),
        fingerprinter=CortexFingerprinter(),
        archive_chain=NexusArchiveChain(tmp_path / "archive.db"),
        snapshots_dir=tmp_path / "snapshots",
        lineage=lineage,
    )


def test_kairos_seals_and_backfills(kb):
    store, lineage, tmp = kb
    r1 = lineage.record_turn(query="q1", injected=[], contradictions=[],
                             model_id="m", response_text="a1", prompt_prefix="p")
    r2 = lineage.record_turn(query="q2", injected=[], contradictions=[],
                             model_id="m", response_text="a2", prompt_prefix="p")

    kairos = _mini_kairos(tmp, lineage)
    block = asyncio.run(kairos.maybe_snapshot_and_fingerprint(force=True))
    assert block is not None

    seal = block.fingerprint["lineage_seal"]
    assert seal["count"] == 2
    assert sorted([r1.record_hash, r2.record_hash]) == seal["record_hashes"]
    assert seal["digest"] == LineageStore.seal_digest(
        [r1.record_hash, r2.record_hash])

    # backfilled, and the chain (now carrying the seal) still verifies
    assert lineage.get(r1.lineage_id).sealed_block == block.index
    assert lineage.get(r2.lineage_id).sealed_block == block.index
    assert lineage.unsealed() == []
    assert kairos.archive_chain.verify_chain().valid is True


# ---------- 4. tampering with a sealed record is detectable ----------

def test_tampered_sealed_record_detectable(kb):
    store, lineage, tmp = kb
    rec = lineage.record_turn(query="q", injected=[{"belief_id": 1,
                              "weight_at_injection": 0.5, "tier_at_injection": 2}],
                              contradictions=[], model_id="m",
                              response_text="a", prompt_prefix="p")
    kairos = _mini_kairos(tmp, lineage)
    block = asyncio.run(kairos.maybe_snapshot_and_fingerprint(force=True))

    assert lineage.verify_record(rec.lineage_id)["record_intact"] is True

    lineage.conn.execute(
        "UPDATE lineage_records SET injected_json = '[]' WHERE lineage_id = ?",
        (rec.lineage_id,))
    lineage.conn.commit()

    v = lineage.verify_record(rec.lineage_id)
    assert v["record_intact"] is False        # row no longer matches its hash
    # and the original hash is still pinned inside the sealed chain block
    assert rec.record_hash in block.fingerprint["lineage_seal"]["record_hashes"]


# ---------- 5. endpoints: auth + honest belief resolution ----------

def test_lineage_endpoints_auth_and_resolution():
    app = create_app()
    client = TestClient(app)
    services = app.state.services

    bid = services.store.add(subject="alpha", relation="is_a", object="thing",
                             weight=0.6, tier=2, granularity=Granularity.TOPIC,
                             source="t")
    rec = services.lineage.record_turn(
        query="q",
        injected=[{"belief_id": bid, "weight_at_injection": 0.6,
                   "tier_at_injection": 2},
                  {"belief_id": 99999999, "weight_at_injection": 0.1,
                   "tier_at_injection": 2}],
        contradictions=[], model_id="m", response_text="a", prompt_prefix="p")

    # auth: pack mandates token on lineage reads
    unauth = TestClient(app)
    unauth.headers.pop("Authorization", None)
    assert unauth.get(f"/lineage/{rec.lineage_id}").status_code == 401
    assert unauth.get("/lineage/recent").status_code == 401

    r = client.get(f"/lineage/{rec.lineage_id}")
    assert r.status_code == 200
    body = r.json()
    by_id = {i["belief_id"]: i for i in body["injected"]}
    assert by_id[bid]["resolved"] is True
    assert by_id[bid]["subject"] == "alpha"
    assert by_id[99999999]["resolved"] is False    # honest, not papered over
    assert body["verify"]["record_intact"] is True

    recent = client.get("/lineage/recent").json()
    assert any(x["lineage_id"] == rec.lineage_id for x in recent["records"])

    assert client.get("/lineage/does-not-exist").status_code == 404


# ---------- 6. unsealed records survive restart, then seal ----------

def test_unsealed_survive_restart_and_seal_next_cycle(kb):
    store, lineage, tmp = kb
    rec = lineage.record_turn(query="q", injected=[], contradictions=[],
                              model_id="m", response_text="a", prompt_prefix="p")
    lineage.close()

    reopened = LineageStore(tmp / "kb.db")               # process restart
    assert [lid for lid, _ in reopened.unsealed()] == [rec.lineage_id]

    kairos = _mini_kairos(tmp, reopened)
    block = asyncio.run(kairos.maybe_snapshot_and_fingerprint(force=True))
    assert reopened.get(rec.lineage_id).sealed_block == block.index
    reopened.close()
