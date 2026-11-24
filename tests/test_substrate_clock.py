"""Packet AN.2 — re-promotion is not evidence, so it may not reset the clock.

THE MECHANISM, confirmed live before this was built
    `add()`'s upsert sets `last_fired = excluded.last_fired` on every
    conflict (tiered_store.py), and consolidation re-offers every
    gate-passing edge every cycle — so each re-promotion reset that row's
    decay clock. Demonstrated on a fixture: a substrate row idle 40 days
    (current weight 0.000) returned to 0.900 from ONE re-add. This is the
    store-side sibling of the leak AK closed on the graph side, and the
    strong inference for why the operator's substrate rows sat at
    weight 1.00, tier 1, regardless of age.

THE DISTINCTION, same as AK's
    A CLAIM re-asserted is fresh evidence — someone said it again — and
    keeps refreshing the clock. A SUBSTRATE row re-promoted is the
    mechanism re-offering its own output, and now leaves `last_fired`
    alone. The partition is the one spelling the retriever's cap, the
    semantic index's exclusion and AK's ingest guard already use, now
    homed in `backend/kb/schema.py` (the store cannot import
    semantic_index without a cycle) and re-exported unchanged.

    The record layer is untouched in both positions: weight stays a
    monotone MAX, created_at never moves, tier's MIN is out of scope here.
    `store.fire()` — the citation-driven reset — is untouched: an answer
    USING a substrate row is still evidence about it.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore

OLD = time.time() - 40 * 86400


def _aged(tmp_path, relation, source) -> tuple[TripleStore, int, dict]:
    store = TripleStore(tmp_path / "kb.db")
    kw = dict(subject="safety", relation=relation, object="qtc",
              granularity=Granularity.SESSION, source=source)
    bid = store.add(weight=0.9, tier=1, **kw)
    store.conn.execute(
        "UPDATE triples SET last_fired = ?, created_at = ? WHERE id = ?",
        (OLD, OLD, bid))
    store.conn.commit()
    # NON-VACUITY: the row is genuinely idle and genuinely decayed — a
    # fresh fixture would make "the clock did not move" vacuously true.
    t = store.get_by_id(bid)
    assert t.last_fired == OLD and t.current_weight() == pytest.approx(0.0)
    return store, bid, kw


def test_substrate_repromotion_does_not_reset_the_clock(tmp_path):
    """THE SENTINEL. Before AN.2 this re-add restored a 40-day-idle row to
    full prominence in one statement."""
    store, bid, kw = _aged(tmp_path, "co_activated",
                           "hippocampus_consolidation")
    store.add(weight=0.9, tier=1, **kw)
    t = store.get_by_id(bid)
    assert t.last_fired == OLD, (
        "a re-promotion reset the decay clock — the mechanism is refreshing "
        "its own output's prominence again")
    assert t.current_weight() == pytest.approx(0.0)
    assert t.created_at == OLD
    store.close()


def test_the_record_layer_is_untouched_by_the_fix(tmp_path):
    """Weight stays add()'s monotone MAX in both positions — this packet
    touches the CLOCK, not the record (I-8's record half is not in play)."""
    store, bid, kw = _aged(tmp_path, "co_activated",
                           "hippocampus_consolidation")
    store.add(weight=0.95, tier=1, **kw)
    assert store.get_by_id(bid).weight == pytest.approx(0.95)
    store.add(weight=0.2, tier=1, **kw)
    assert store.get_by_id(bid).weight == pytest.approx(0.95)
    store.close()


def test_a_reasserted_claim_still_refreshes(tmp_path):
    """The accepting direction: a claim re-asserted IS fresh evidence and
    keeps the pre-AN.2 behaviour exactly."""
    store, bid, kw = _aged(tmp_path, "raises", "extraction")
    store.add(weight=0.9, tier=1, **kw)
    t = store.get_by_id(bid)
    assert t.last_fired > OLD, "a re-asserted claim no longer refreshes"
    assert t.current_weight() == pytest.approx(0.9)
    store.close()


def test_fire_still_refreshes_a_substrate_row(tmp_path):
    """Citation-driven reinforcement (F.5) is evidence about the row and is
    deliberately untouched — the fix is scoped to re-PROMOTION."""
    store, bid, _kw = _aged(tmp_path, "co_activated",
                            "hippocampus_consolidation")
    store.fire(bid)
    assert store.get_by_id(bid).last_fired > OLD
    store.close()


def test_the_rollback_is_the_pre_an2_upsert_exactly(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_SUBSTRATE_CLOCK_REFRESH", "on")
    store, bid, kw = _aged(tmp_path, "co_activated",
                           "hippocampus_consolidation")
    store.add(weight=0.9, tier=1, **kw)
    t = store.get_by_id(bid)
    assert t.last_fired > OLD, "rollback did not restore the refresh"
    assert t.current_weight() == pytest.approx(0.9)
    store.close()


def test_the_partition_has_one_spelling(tmp_path):
    """The pin the packet asked for, AK's way: the substrate partition used
    by the store IS the object the semantic index exports — identity, not
    equality, so a second spelling cannot drift."""
    from backend.kb import schema, semantic_index, tiered_store
    assert semantic_index.DEFAULT_EXCLUDED_RELATIONS \
        is schema.SUBSTRATE_RELATIONS
    assert tiered_store.SUBSTRATE_RELATIONS is schema.SUBSTRATE_RELATIONS
    # And the loop's AK guard still reads the same object.
    from backend.loop.agentic_loop import _SUBSTRATE_RELATIONS
    assert _SUBSTRATE_RELATIONS is schema.SUBSTRATE_RELATIONS
