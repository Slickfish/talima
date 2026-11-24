"""Packet H.1 — the two-layer tamper guarantee, as a callable and an endpoint.

Packet E.1 proved the guarantee and left it inside a test. NEXUS's strongest
verified property — *the record of what an answer rested on cannot be quietly
rewritten* — was reachable only by reading pytest. These sentinels pin the
lifted version against the same attacks E.1 used, plus the states E.1 never
had to consider because a test fixture never has half-sealed data.

THE LOAD-BEARING CASE is `test_consistent_forgery_passes_row_and_fails_seal`.
Anyone can catch a clumsy edit. The claim that matters is that an attacker who
rewrites the content *and recomputes its hash* — defeating the row-level check
completely — still cannot reach the hash sealed inside a chain block. If that
test ever goes green for the wrong reason, the substrate's central promise is
unverified.
"""
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.cortex.hebbian import HebbianUpdater
from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore
from backend.memory.chain import NexusArchiveChain
from backend.memory.fingerprint import CortexFingerprinter
from backend.memory.hippocampus import Hippocampus
from backend.memory.kairos import KairosProcess
from backend.memory.lineage import LineageStore, verify_sealed_lineage
from backend.memory.neocortex import Neocortex
from backend.memory.procedural import ProceduralMemory


def _stack(tmp_path: Path, *, n_records: int = 3):
    """A store with sealed lineage records and a verifiable chain."""
    store = TripleStore(tmp_path / "kb.db")
    lineage = LineageStore(tmp_path / "kb.db")
    bid = store.add(subject="python", relation="is_a", object="programming_language",
                    weight=0.9, tier=1, granularity=Granularity.TOPIC,
                    source="wikidata")
    recs = [
        lineage.record_turn(
            query=f"q{i}",
            injected=[{"belief_id": bid, "weight_at_injection": 0.9,
                       "tier_at_injection": 1, "cited": True}],
            contradictions=[], model_id="deepseek-r1:14b",
            response_text=f"answer {i}", prompt_prefix="STABLE")
        for i in range(n_records)
    ]
    hippo = Hippocampus()
    hippo.ingest_triple("alpha", "is_a", "thing", weight=0.6)
    kairos = KairosProcess(
        hippo, Neocortex(tmp_path / "kb.db"), HebbianUpdater(), ProceduralMemory(),
        fingerprinter=CortexFingerprinter(),
        archive_chain=NexusArchiveChain(tmp_path / "archive.db"),
        snapshots_dir=tmp_path / "snapshots", lineage=lineage)
    block = asyncio.run(kairos.maybe_snapshot_and_fingerprint(force=True))
    return store, lineage, kairos.archive_chain, recs, block


def _consistent_forgery(lineage, lineage_id: str, new_response_hash: str) -> str:
    """Rewrite content AND recompute record_hash — the attack that defeats
    layer one entirely. Returns the forged hash."""
    import json
    lineage.conn.execute(
        "UPDATE lineage_records SET response_hash = ? WHERE lineage_id = ?",
        (new_response_hash, lineage_id))
    row = lineage.conn.execute(
        "SELECT * FROM lineage_records WHERE lineage_id = ?", (lineage_id,)).fetchone()
    forged = LineageStore.compute_record_hash({
        "lineage_id": row["lineage_id"], "ts": row["ts"],
        "query_hash": row["query_hash"],
        "injected": json.loads(row["injected_json"]),
        "contradictions": json.loads(row["contradictions_json"]),
        "model_id": row["model_id"],
        "prompt_prefix_hash": row["prompt_prefix_hash"],
        "response_hash": row["response_hash"],
    })
    lineage.conn.execute(
        "UPDATE lineage_records SET record_hash = ? WHERE lineage_id = ?",
        (forged, lineage_id))
    lineage.conn.commit()
    return forged


# ---------------------------------------------------------------------------
# A healthy store
# ---------------------------------------------------------------------------

def test_a_clean_store_verifies_at_every_layer(tmp_path):
    store, lineage, chain, recs, block = _stack(tmp_path)

    # NON-VACUITY FIRST (§15.2): a clean verdict over zero sealed records
    # would be a statement about an empty store, not about the guarantee.
    assert len(recs) == 3
    assert all(lineage.get(r.lineage_id).sealed_block is not None for r in recs)
    assert chain.count() >= 1
    assert block is not None and "lineage_seal" in block.fingerprint

    result = verify_sealed_lineage(lineage, chain)

    assert result["records_checked"] == 3
    assert result["layers"]["row"]["ok"] == 3
    assert result["layers"]["row"]["failed"] == 0
    assert result["layers"]["seal"]["ok"] == 3          # actually sealed, not skipped
    assert result["layers"]["seal"]["failed"] == 0
    assert result["layers"]["seal"]["unsealed"] == 0
    assert result["layers"]["chain"]["ok"] is True
    assert result["verdict"] == "intact"
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# Layer 1 — the naive tamper
# ---------------------------------------------------------------------------

def test_naive_tamper_is_caught_at_the_row_layer(tmp_path):
    """Content edited, hash left behind. Caught by recomputation."""
    store, lineage, chain, recs, _ = _stack(tmp_path)
    victim = recs[1].lineage_id

    lineage.conn.execute(
        "UPDATE lineage_records SET response_hash = ? WHERE lineage_id = ?",
        ("f" * 64, victim))
    lineage.conn.commit()

    result = verify_sealed_lineage(lineage, chain)
    assert result["layers"]["row"]["failed"] == 1
    assert result["layers"]["row"]["failures"] == [victim]
    assert result["layers"]["chain"]["ok"] is True      # the chain is untouched
    assert result["verdict"] in ("record_tampered", "record_and_seal_broken")
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# Layer 2 — THE LOAD-BEARING CASE
# ---------------------------------------------------------------------------

def test_consistent_forgery_passes_row_and_fails_seal(tmp_path):
    """The attack that defeats row-level checking, and the seal that stops it.

    An attacker with write access to kb.db rewrites a record's content and
    recomputes its `record_hash` so the two agree. Layer one is satisfied —
    it has nothing left to detect. The forgery still fails, because the hash
    that was sealed into the archive block is not the forged one, and the
    block carrying it is chain-verified.

    **This is why the layers are reported separately.** A single boolean would
    say "invalid" for both this and a typo. `seal_broken` says "the content
    hashes to itself but is not the content that was sealed" — which names a
    forgery.
    """
    store, lineage, chain, recs, block = _stack(tmp_path)
    victim = recs[1].lineage_id
    original_hash = recs[1].record_hash
    sealed_hashes = block.fingerprint["lineage_seal"]["record_hashes"]
    assert original_hash in sealed_hashes               # non-vacuity

    forged = _consistent_forgery(lineage, victim, "d" * 64)

    # Layer one is fooled — completely, and that is the premise.
    assert lineage.verify_record(victim)["record_intact"] is True
    assert forged != original_hash

    result = verify_sealed_lineage(lineage, chain)

    assert result["layers"]["row"]["failed"] == 0, (
        "the forgery should defeat the row layer — if it does not, this test "
        "is no longer exercising the attack it was written for")
    assert result["layers"]["seal"]["failed"] == 1
    assert result["layers"]["seal"]["failures"] == [victim]
    assert result["layers"]["chain"]["ok"] is True
    assert result["verdict"] == "seal_broken"
    # The other two records are unaffected — the failure is attributed, not
    # smeared across the store.
    assert result["layers"]["seal"]["ok"] == 2
    store.close(); lineage.close()


def test_forged_hash_is_not_among_the_sealed_hashes(tmp_path):
    """The mechanism behind the case above, asserted directly."""
    store, lineage, chain, recs, block = _stack(tmp_path)
    forged = _consistent_forgery(lineage, recs[0].lineage_id, "e" * 64)
    sealed = chain.get_block(0).fingerprint["lineage_seal"]["record_hashes"]
    assert forged not in sealed
    assert chain.verify_chain().valid is True, (
        "the block carrying the seal must remain verifiable, or layer two is "
        "checking against evidence the attacker could also have rewritten")
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# Layer 3 — the chain itself
# ---------------------------------------------------------------------------

def test_chain_tamper_is_reported_at_the_chain_layer(tmp_path):
    store, lineage, chain, recs, _ = _stack(tmp_path)
    chain.conn.execute(
        "UPDATE archive_blocks SET fingerprint_json = ? WHERE idx = 0",
        ('{"tampered": true}',))
    chain.conn.commit()

    result = verify_sealed_lineage(lineage, chain)
    assert result["layers"]["chain"]["ok"] is False
    assert result["layers"]["chain"]["broken_at"] == 0
    assert result["verdict"] == "chain_broken", (
        "a broken chain outranks every other finding — the evidence the other "
        "layers check against is itself in question")
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# Unsealed is a state, not a failure (I-6)
# ---------------------------------------------------------------------------

def test_unsealed_records_are_declared_not_failed(tmp_path):
    """A record written since the last KAIROS cycle is awaiting its seal.

    Reporting that as invalid would fire a false alarm on every healthy
    store, and an operator who learns to ignore one alarm ignores the next.
    """
    store, lineage, chain, recs, _ = _stack(tmp_path)
    fresh = lineage.record_turn(
        query="after the cycle", injected=[], contradictions=[],
        model_id="m", response_text="r", prompt_prefix="STABLE")
    assert lineage.get(fresh.lineage_id).sealed_block is None   # non-vacuity

    result = verify_sealed_lineage(lineage, chain)

    assert result["layers"]["seal"]["unsealed"] == 1
    assert result["layers"]["seal"]["unsealed_ids"] == [fresh.lineage_id]
    assert result["layers"]["seal"]["failed"] == 0
    assert fresh.lineage_id not in result["layers"]["seal"]["failures"]
    assert result["verdict"] == "intact", (
        "awaiting a seal is normal operation, not a tamper finding")
    # ...and it is still row-checked, so an unsealed record is not unchecked.
    assert result["layers"]["row"]["ok"] == 4
    store.close(); lineage.close()


def test_dangling_seal_is_distinct_from_unsealed(tmp_path):
    """A record naming a block that is not in the chain is neither unsealed
    nor content-tampered. Conflating it with either would misdescribe it."""
    store, lineage, chain, recs, _ = _stack(tmp_path)
    lineage.conn.execute(
        "UPDATE lineage_records SET sealed_block = 99 WHERE lineage_id = ?",
        (recs[0].lineage_id,))
    lineage.conn.commit()

    result = verify_sealed_lineage(lineage, chain)
    seal = result["layers"]["seal"]
    assert seal["missing_block"] == [
        {"lineage_id": recs[0].lineage_id, "sealed_block": 99}]
    assert recs[0].lineage_id in seal["failures"]
    assert recs[0].lineage_id not in seal["unsealed_ids"]
    assert result["layers"]["row"]["failed"] == 0
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# Scoping and edge cases
# ---------------------------------------------------------------------------

def test_single_record_scope(tmp_path):
    store, lineage, chain, recs, _ = _stack(tmp_path)
    _consistent_forgery(lineage, recs[2].lineage_id, "a" * 64)

    clean = verify_sealed_lineage(lineage, chain, lineage_id=recs[0].lineage_id)
    assert clean["records_checked"] == 1 and clean["verdict"] == "intact"

    dirty = verify_sealed_lineage(lineage, chain, lineage_id=recs[2].lineage_id)
    assert dirty["records_checked"] == 1 and dirty["verdict"] == "seal_broken"

    assert verify_sealed_lineage(lineage, chain,
                                 lineage_id="nope")["exists"] is False
    store.close(); lineage.close()


def test_no_chain_available_is_declared_not_assumed_valid(tmp_path):
    """Without the chain, layer three cannot be evaluated. It reports `None`
    — not `True`. An unevaluated check must never read as a passed one."""
    store, lineage, chain, recs, _ = _stack(tmp_path)
    result = verify_sealed_lineage(lineage, None)
    assert result["layers"]["chain"]["ok"] is None
    assert result["layers"]["chain"]["reason"] == "no_chain_available"
    store.close(); lineage.close()


def test_empty_store_does_not_claim_a_guarantee(tmp_path):
    """Zero records verify as `intact` with `records_checked == 0`.

    The count is what stops that being a false assurance — the same non-vacuity
    problem as /chain/status reporting `valid: true` for an empty chain
    (ENDPOINT_AUDIT.md P3). Here the caller can tell.
    """
    lineage = LineageStore(tmp_path / "kb.db")
    chain = NexusArchiveChain(tmp_path / "archive.db")
    result = verify_sealed_lineage(lineage, chain)
    assert result["records_checked"] == 0
    assert result["verdict"] == "intact"
    lineage.close()


# ---------------------------------------------------------------------------
# The endpoints
# ---------------------------------------------------------------------------

@pytest.fixture
def app_with_lineage(tmp_path):
    import os
    os.environ["NEXUS_API_TOKEN"] = "h1-token"
    from backend.server import create_app, create_services
    store, lineage, chain, recs, block = _stack(tmp_path)
    store.close(); lineage.close(); chain.close()
    services = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots")
    return create_app(services), recs, {"Authorization": "Bearer h1-token"}


def test_verify_endpoints_report_all_three_layers(app_with_lineage):
    app, recs, auth = app_with_lineage
    with TestClient(app) as c:
        body = c.get("/lineage/verify", headers=auth).json()
        assert body["records_checked"] >= 3          # non-vacuity
        assert set(body["layers"]) == {"row", "seal", "chain"}
        assert body["layers"]["seal"]["ok"] >= 3
        assert body["verdict"] == "intact"

        one = c.get(f"/lineage/{recs[0].lineage_id}/verify", headers=auth).json()
        assert one["records_checked"] == 1
        assert one["verdict"] == "intact"

        assert c.get("/lineage/nope/verify", headers=auth).status_code == 404


def test_verify_route_is_not_swallowed_by_the_id_route(app_with_lineage):
    """`/lineage/verify` is declared BEFORE `/lineage/{lineage_id}`.

    FastAPI matches in declaration order, so the reverse would send `verify`
    to the id handler and 404 forever — a route that exists and is
    unreachable, which is the audit's whole theme.
    """
    app, _recs, auth = app_with_lineage
    with TestClient(app) as c:
        r = c.get("/lineage/verify", headers=auth)
        assert r.status_code == 200
        assert "layers" in r.json(), "matched the {lineage_id} route instead"


def test_verify_endpoints_are_gated(app_with_lineage):
    """Consistent with `/lineage/recent`, which Packet G pinned as the
    deliberately-gated read: evidence records are more sensitive than the
    open-GET posture."""
    app, recs, _auth = app_with_lineage
    with TestClient(app) as c:
        c.headers.pop("Authorization", None)
        assert c.get("/lineage/verify").status_code == 401
        assert c.get(f"/lineage/{recs[0].lineage_id}/verify").status_code == 401
        assert c.get("/lineage/verify",
                     headers={"Authorization": "Bearer wrong"}).status_code == 401
