"""P1 sentinel tests — pin the probe-verified differentiators and the P1
demo-integrity fixes so they can never silently regress.

- Contradiction retention + detection (READINESS_REPORT probe 3.3)
- Chain tamper-evidence (probe 3.2)
- Chain honesty for missing snapshots (F-16)
- Non-silent embedding degradation (F-10)
- Wikidata seeder warning parity (F-10)
- Retrieval diagnostics off stdout (F-06)
- Vendored UI assets served locally, no external URLs in pages (F-07)
- Substrate voice in the stable prefix (F-03)
"""
import asyncio
import hashlib
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.cortex.contradiction import ContradictionDetector
from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.memory.chain import GENESIS_HASH, ArchiveBlock, NexusArchiveChain


@pytest.fixture
def store(tmp_path: Path):
    s = TripleStore(tmp_path / "kb.db")
    yield s
    s.close()


@pytest.fixture
def chain(tmp_path: Path):
    c = NexusArchiveChain(tmp_path / "archive.db")
    yield c
    c.close()


# ---------- probe 3.3 — contradiction retention ----------

def test_contradiction_retention(store):
    """Conflicting beliefs on the same (subject, object) are BOTH retained
    as distinct rows and related by the detector — never overwritten."""
    store.add(subject="exercise", relation="improves", object="health",
              weight=0.7, tier=2, granularity=Granularity.TOPIC, source="src_a")
    store.add(subject="exercise", relation="worsens", object="health",
              weight=0.7, tier=2, granularity=Granularity.TOPIC, source="src_b")

    rows = [t for t in store.all()
            if t.subject == "exercise" and t.object == "health"]
    assert sorted(t.relation for t in rows) == ["improves", "worsens"]

    contras = ContradictionDetector().scan_recent(store.all())
    assert len(contras) == 1
    assert {contras[0].relation_a, contras[0].relation_b} == {"improves", "worsens"}


# ---------- probe 3.2 — chain tamper-evidence ----------

def test_chain_tamper(chain, tmp_path):
    """Mutating a sealed block breaks verification at exactly that block."""
    for i in range(3):
        chain.append({"sig": [i, i + 1]}, tmp_path / f"snap_{i}.json")
    assert chain.verify_chain().valid is True

    chain.conn.execute(
        "UPDATE archive_blocks SET fingerprint_json='{\"sig\": [99]}' WHERE idx=1")
    chain.conn.commit()

    result = chain.verify_chain()
    assert result.valid is False
    assert result.broken_at == 1
    # W8 — a block appended now is layout 2: its fingerprint is held in its
    # header by its hash, and an edited fingerprint is named as one.
    assert result.reason == "fingerprint_mismatch"


# ---------- F-16 — chain honesty for missing snapshots ----------

def test_chain_records_missing_snapshot(chain, tmp_path):
    real = tmp_path / "real_snap.json"
    real.write_text("{}", encoding="utf-8")

    b_missing = chain.append({"sig": [1]}, tmp_path / "never_written.json")
    b_present = chain.append({"sig": [2]}, real)

    assert b_missing.snapshot_missing is True
    assert b_present.snapshot_missing is False

    result = chain.verify_chain()
    assert result.valid is True
    assert result.missing_snapshots == (0,)
    assert result.to_dict()["missing_snapshots"] == [0]


def test_chain_hash_backcompat_for_present_snapshots():
    """Blocks with snapshot_missing=False hash exactly as the legacy formula —
    pre-F-16 chains keep verifying unchanged."""
    fields = dict(index=0, timestamp=1234.5, fingerprint={"sig": [1]},
                  snapshot_path="/x/s.json", snapshot_hash="ab" * 32,
                  previous_hash=GENESIS_HASH)
    # The pre-F-16 hash body — exactly these six keys.
    legacy = hashlib.sha256(json.dumps(
        fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    blk = ArchiveBlock(**fields, snapshot_missing=False)
    assert blk.compute_hash() == legacy
    # And the flag IS sealed when True (different hash)
    assert ArchiveBlock(**fields, snapshot_missing=True).compute_hash() != legacy


# ---------- F-10 — embedding degradation is never silent ----------

def test_embedding_fallback_flags_degraded_and_warns(monkeypatch, caplog):
    from backend.vector import embeddings as emb

    emb._DEGRADED.update(degraded=False, reason="", ts=0.0)

    class BoomClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): raise httpx.ConnectError("boom")

    monkeypatch.setattr(emb.httpx, "AsyncClient", BoomClient)
    monkeypatch.setattr(emb, "_resolve_embedding_model", lambda: "nomic-embed-text")

    with caplog.at_level(logging.WARNING, logger="nexus.embeddings"):
        vec = asyncio.run(emb.embed_text("hello"))

    assert len(vec) == emb.FULL_DIM                      # fallback still serves
    status = emb.embedding_status()
    assert status["degraded"] is True
    assert "ConnectError" in status["reason"]
    assert any("fallback" in r.message for r in caplog.records)


# ---------- F-10 — Wikidata seeder warning parity ----------

def test_wikidata_seeder_surfaces_warnings(monkeypatch, store):
    from backend.kb import seeder

    class BoomClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise httpx.ConnectError("boom")

    from backend.memory import actions as recorder     # W10b: the recorder owns the seeder's transport
    monkeypatch.setattr(recorder.httpx, "AsyncClient", BoomClient)

    from backend.memory.actions import Trigger
    from backend.memory.lineage import LineageStore
    actions = LineageStore(store.target)
    res = asyncio.run(seeder.seed_wikidata(           # W10b: a seed's requests are recorded actions, in kb.db
        ["python", "exercise"], confirmed=True, store=store,
        action_store=actions, trigger=Trigger(route="/kb/seed/wikidata")))
    actions.close()
    assert res.seeded == 0
    assert len(res.warnings) == 2                        # one per concept, not silent
    assert all("failed" in w for w in res.warnings)
    assert res.refusals == []   # the gate never saw a row; a network failure
                                # is not a refusal and must not be filed as one


# ---------- F-06 — retrieval diagnostics quiet on stdout ----------

def test_retrieval_diag_not_on_stdout(store, capsys):
    store.add(subject="alpha", relation="is_a", object="thing",
              weight=0.6, tier=2, granularity=Granularity.TOPIC, source="t")
    hits = TripleRetriever(store).retrieve("alpha")
    assert len(hits) == 1
    out = capsys.readouterr()
    assert out.out == ""                                 # hot path is quiet


# ---------- F-07 — vendored assets served, pages reference nothing external ----------

def test_vendor_assets_served():
    from backend.server import create_app
    client = TestClient(create_app())
    for path in ("/vendor/d3.v7.min.js", "/vendor/gsap.min.js",
                 "/vendor/three/three.module.js", "/vendor/fonts/fonts.css"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert len(r.content) > 0, path


def test_frontend_pages_have_no_external_urls():
    root = Path(__file__).resolve().parent.parent / "frontend"
    import re
    for page in ("nexus_ui.html", "nexus_memory.html"):
        text = (root / page).read_text(encoding="utf-8")
        external = [u for u in re.findall(r"https?://[^\s\"'<>)]+", text)
                    if "localhost" not in u and "127.0.0.1" not in u]
        assert external == [], f"{page} still references: {external}"


# ---------- F-03 — substrate voice in the stable prefix ----------

def test_prefix_names_the_substrate():
    from backend.loop.agentic_loop import STABLE_SYSTEM_PREFIX as p
    assert "NEXUS" in p
    assert "memory" in p.lower()
    assert "never claim to lack memory" in p.lower()
