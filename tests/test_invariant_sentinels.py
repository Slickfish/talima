"""Packet E.1 — the three sentinels pack §14 lists as required-and-unwritten.

These are written BEFORE persistence lands so that persistence is constrained
by them rather than validated after the fact. A failing sentinel here is
"philosophy breached", not "test needs updating" (pack §14).

Invariants pinned:
  I-2  decay/prune may reduce PROMINENCE, never RECOVERABILITY
  I-3  sealed records are unreachable by decay, BY CONSTRUCTION
  I-4  citation confers durability

I-2 is asserted across ALL THREE edge-deletion paths, not the one the packet
originally named. E.0 found the other two:

  path 1  hebbian.py:135  prune_below inside decay_pass   (per KAIROS cycle)
  path 2  hebbian.py:114  prune_below inside update       (EVERY turn)
  path 3  compaction.py:203-204  graph_collapse remove_edge x2  (tier-3)

On today's volatile graph all three are harmless. Once the graph is
persisted each is destruction of association structure — and of the monotone
recurrence_count that E.2 adds. The xfails below are the correct result for
current behaviour; E.3 converts deletion to demotion and they flip to pass.
"""
import asyncio
from pathlib import Path

import pytest

from backend.cortex.edges import SynapticEdge
from backend.cortex.hebbian import HebbianUpdater
from backend.cortex.propagation import ActivationMap
from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore
from backend.loop.compaction import graph_collapse
from backend.memory.chain import NexusArchiveChain
from backend.memory.fingerprint import CortexFingerprinter
from backend.memory.hippocampus import Hippocampus
from backend.memory.kairos import KairosProcess
from backend.memory.lineage import LineageStore
from backend.memory.neocortex import Neocortex
from backend.memory.procedural import ProceduralMemory

WEAK = 0.001          # far below PRUNE_THRESHOLD (0.05)


def _edge_count(h: Hippocampus) -> int:
    return h.graph.number_of_edges()


def _weak_graph() -> Hippocampus:
    """Two nodes, one edge whose weight is under the prune floor."""
    h = Hippocampus()
    h.add_edge(SynapticEdge(source="alpha", target="beta", weight=WEAK))
    return h


# ---------------------------------------------------------------------------
# I-2 — prune reduces prominence, not count. One test per deletion path.
# ---------------------------------------------------------------------------

def test_prune_reduces_prominence_not_count_decay_pass():
    h = _weak_graph()
    before = _edge_count(h)
    HebbianUpdater().decay_pass(h, days=1.0)
    assert _edge_count(h) == before, "decay_pass deleted a persisted edge"


def test_prune_reduces_prominence_not_count_hebbian_update():
    h = _weak_graph()
    # Fire an unrelated pair so the weak edge is weakened (LTD) and pruned.
    am = ActivationMap(activations={"gamma": 1.0, "delta": 1.0},
                       fired_sequence=[("gamma", 0), ("delta", 0)])
    HebbianUpdater().update(h, am)
    # NB: assert the specific edge, not the count — update() also FORMS edges
    # (gamma->delta here), which masks a deletion in a count comparison.
    assert h.get_edge("alpha", "beta") is not None, \
        "update() deleted a persisted edge"


def test_prune_reduces_prominence_not_count_graph_collapse():
    h = Hippocampus()
    # a -> b -> c, with b a simple linear node (in_degree 1, out_degree 1)
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.6))
    h.add_edge(SynapticEdge(source="b", target="c", weight=0.6))
    before = _edge_count(h)

    collapsed = graph_collapse(h)

    assert collapsed >= 1, "fixture did not exercise the collapse path"
    assert _edge_count(h) >= before, "graph_collapse deleted persisted edges"


def test_prune_never_deletes_a_stored_belief():
    """The store is not reachable from any prune path — true today and it
    must stay true. Beliefs are never deleted (Law 4); only association
    prominence may fall."""
    import tempfile
    store = TripleStore(Path(tempfile.mkdtemp()) / "kb.db")
    bid = store.add(subject="alpha", relation="is_a", object="beta",
                    weight=WEAK, tier=3, granularity=Granularity.TOPIC,
                    source="extraction")

    h = _weak_graph()
    HebbianUpdater().decay_pass(h, days=365.0)      # aggressive
    graph_collapse(h)

    assert store.get_by_id(bid) is not None, "a prune path deleted a belief"
    store.close()


# ---------------------------------------------------------------------------
# I-4 — citation confers durability
# ---------------------------------------------------------------------------

def _sealed_lineage(tmp_path: Path, belief_id: int):
    """Seed a lineage record citing `belief_id` and seal it into the chain."""
    lineage = LineageStore(tmp_path / "kb.db")
    rec = lineage.record_turn(
        query="what about alpha?",
        injected=[{"belief_id": belief_id, "weight_at_injection": 0.6,
                   "tier_at_injection": 2}],
        contradictions=[], model_id="test-model",
        response_text="alpha is a beta", prompt_prefix="prefix",
    )
    hippo = Hippocampus()
    hippo.ingest_triple("alpha", "is_a", "beta", weight=0.6)
    kairos = KairosProcess(
        hippo, Neocortex(tmp_path / "kb.db"), HebbianUpdater(), ProceduralMemory(),
        fingerprinter=CortexFingerprinter(),
        archive_chain=NexusArchiveChain(tmp_path / "archive.db"),
        snapshots_dir=tmp_path / "snaps", lineage=lineage,
    )
    block = asyncio.run(kairos.maybe_snapshot_and_fingerprint(force=True))
    return lineage, rec, block, kairos


def test_cited_belief_triple_survives_prune(tmp_path):
    """A belief cited by a SEALED lineage record is still retrievable after an
    aggressive prune. Passes today (prune cannot reach the store) — pinned so
    persistence cannot quietly change it."""
    store = TripleStore(tmp_path / "kb.db")
    bid = store.add(subject="alpha", relation="is_a", object="beta",
                    weight=WEAK, tier=3, granularity=Granularity.TOPIC,
                    source="extraction")
    lineage, rec, block, kairos = _sealed_lineage(tmp_path, bid)
    assert lineage.get(rec.lineage_id).sealed_block == block.index

    h = _weak_graph()
    HebbianUpdater().decay_pass(h, days=365.0)
    graph_collapse(h)

    assert store.get_by_id(bid) is not None
    store.close(); lineage.close()


def test_cited_belief_edge_survives_prune(tmp_path):
    store = TripleStore(tmp_path / "kb.db")
    bid = store.add(subject="alpha", relation="is_a", object="beta",
                    weight=WEAK, tier=3, granularity=Granularity.TOPIC,
                    source="extraction")
    _sealed_lineage(tmp_path, bid)

    h = _weak_graph()                       # the alpha->beta association
    HebbianUpdater().decay_pass(h, days=365.0)

    assert h.get_edge("alpha", "beta") is not None, \
        "prune destroyed the association of a cited belief"
    store.close()


# ---------------------------------------------------------------------------
# I-3 — sealed records unreachable by decay, by construction
# ---------------------------------------------------------------------------

def test_sealed_lineage_tamper_detected(tmp_path):
    """Tampering a SEALED lineage record is detectable against the chain.

    Two layers, both asserted:
      1. row-level  — recomputing record_hash from stored fields no longer
         matches (LineageStore.verify_record).
      2. chain-level — even a consistent forgery (content AND record_hash
         rewritten together) fails against the record_hashes sealed inside
         the block, whose own hash is covered by chain verification.
    """
    store = TripleStore(tmp_path / "kb.db")
    bid = store.add(subject="alpha", relation="is_a", object="beta",
                    weight=0.6, tier=2, granularity=Granularity.TOPIC,
                    source="extraction")
    lineage, rec, block, kairos = _sealed_lineage(tmp_path, bid)

    sealed_hashes = block.fingerprint["lineage_seal"]["record_hashes"]
    assert rec.record_hash in sealed_hashes
    assert kairos.archive_chain.verify_chain().valid is True

    # --- layer 1: naive tamper (content only) -----------------------------
    lineage.conn.execute(
        "UPDATE lineage_records SET response_hash = ? WHERE lineage_id = ?",
        ("f" * 64, rec.lineage_id))
    lineage.conn.commit()
    assert lineage.verify_record(rec.lineage_id)["record_intact"] is False

    # --- layer 2: consistent forgery (content AND its hash) ---------------
    row = lineage.conn.execute(
        "SELECT * FROM lineage_records WHERE lineage_id = ?",
        (rec.lineage_id,)).fetchone()
    import json as _json
    forged_hash = LineageStore.compute_record_hash({
        "lineage_id": row["lineage_id"], "ts": row["ts"],
        "query_hash": row["query_hash"],
        "injected": _json.loads(row["injected_json"]),
        "contradictions": _json.loads(row["contradictions_json"]),
        "model_id": row["model_id"],
        "prompt_prefix_hash": row["prompt_prefix_hash"],
        "response_hash": row["response_hash"],
    })
    lineage.conn.execute(
        "UPDATE lineage_records SET record_hash = ? WHERE lineage_id = ?",
        (forged_hash, rec.lineage_id))
    lineage.conn.commit()

    # Row-level check is now fooled — that is the point of layer 2.
    assert lineage.verify_record(rec.lineage_id)["record_intact"] is True
    # But the seal is not: the forged hash is not among the sealed hashes,
    # and the block carrying them is itself chain-verified.
    assert forged_hash not in sealed_hashes, \
        "a consistent forgery matched the sealed digest"
    assert kairos.archive_chain.verify_chain().valid is True, \
        "the block carrying the seal must remain verifiable"

    store.close(); lineage.close()
