"""Packet AN.1 — a guard that fails open is not a guard.

THE DEFECT
    I-4's demotion exemption (a belief cited by a sealed lineage record is
    load-bearing and not even demoted) was enforced through two lookups that
    both returned an EMPTY SET on exception, with a console log line nothing
    reads: `_protected()` (hebbian.py) and `cited_pairs()` (graph_store.py).
    A transient database error during a turn or a maintenance cycle silently
    ran the prune with nothing protected — the protection held only when
    nothing went wrong, which is the one condition a guard is not for.

THE FIX, and its deliberate asymmetry
    The lookup now raises `ProtectionUnavailable` (any supplier failure is
    re-raised as that name). The PRUNE step fails closed — nothing is
    demoted when the exemption set is unknown:
      - on the TURN path, `update()` skips the prune, reports
        `pruned=None` (the None discipline: an aborted count is not zero)
        and `protection_state="unavailable"`, declared on the
        `hebbian_update` frame — the turn itself never breaks;
      - on the CYCLE path, `decay_pass()` lets the exception propagate, so
        the cycle aborts through the existing blanket handler and reports
        every count as None — exactly how the cycle already refuses to let
        a default impersonate a measurement.
    Strengthening and LTD weakening are untouched in both positions:
    protection never governed them (the exemption is from DEMOTION), so
    failing closed means exactly "do not demote", not "do nothing".
"""
from __future__ import annotations

import asyncio
import json

import pytest

from backend.cortex.edges import SynapticEdge
from backend.cortex.hebbian import HebbianUpdater, ProtectionUnavailable
from backend.cortex.propagation import ActivationMap
from backend.memory.hippocampus import Hippocampus


def _rig():
    """Two edges below the prune floor: one protected-shaped, one not.

    NON-VACUITY per the packet: the fixture must contain something that
    genuinely NEEDED protection (a sub-floor edge the supplier names) and
    something that genuinely gets demoted when the prune runs (a sub-floor
    edge it does not) — or every assertion here passes on a graph where
    nothing was at stake.
    """
    h = Hippocampus()
    h.add_edge(SynapticEdge(source="qtc", target="dose", weight=0.01))
    h.add_edge(SynapticEdge(source="noise", target="debris", weight=0.01))
    am = ActivationMap(activations={"qtc": 1.0}, fired_sequence=[("qtc", 0)])
    return h, am


def _raising_supplier():
    raise RuntimeError("database is locked")


def test_a_working_supplier_protects_and_prunes():
    """The accepting direction, proving the fixture bites: with the lookup
    healthy, the named pair survives and the unnamed one is demoted."""
    h, am = _rig()
    r = HebbianUpdater(protected_pairs=lambda: {("qtc", "dose")}).update(h, am)
    assert h.get_edge("noise", "debris").demoted is True
    assert h.get_edge("qtc", "dose").demoted is False
    assert r.pruned == 1 and r.protection_state == "ok"


def test_a_failed_lookup_demotes_nothing_and_says_so():
    """THE SENTINEL. Before this packet, a raising supplier demoted BOTH
    edges — the protected one included — and reported the prune count as a
    plain number. Now: nothing demotes, the count is None (not run), and
    the state is declared."""
    h, am = _rig()
    r = HebbianUpdater(protected_pairs=_raising_supplier).update(h, am)
    assert h.get_edge("qtc", "dose").demoted is False, (
        "a failed protection lookup stripped I-4's exemption — the guard "
        "failed open again")
    assert h.get_edge("noise", "debris").demoted is False, (
        "the prune ran without its exemption set — fail-closed means the "
        "PASS aborts, not that it proceeds unprotected")
    assert r.pruned is None, (
        "an aborted prune reported a number — a default impersonating a "
        "measurement, the OQ-1 defect")
    assert r.protection_state == "unavailable"


def test_strengthening_and_ltd_are_untouched_by_the_abort():
    """Fail-closed is scoped to the destructive step protection governs.
    Weakening never consulted the exemption set and must still run."""
    h, am = _rig()
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.5))
    r = HebbianUpdater(protected_pairs=_raising_supplier).update(h, am)
    assert h.get_edge("a", "b").weight < 0.5      # LTD ran (endpoints unfired)
    assert r.weakened >= 1


def test_decay_pass_propagates_so_the_cycle_aborts():
    """The cycle path: `decay_pass` raises the named exception, which the
    KAIROS blanket handler turns into skipped=True with every count None —
    the machinery OQ-1's correction built, reused rather than duplicated."""
    h, _am = _rig()
    with pytest.raises(ProtectionUnavailable):
        HebbianUpdater(protected_pairs=_raising_supplier).decay_pass(
            h, days=1.0)
    # And nothing was demoted on the way out.
    assert h.get_edge("qtc", "dose").demoted is False
    assert h.get_edge("noise", "debris").demoted is False


def test_cited_pairs_raises_by_name_on_a_broken_store(tmp_path):
    """The supplier itself: a db-level failure is no longer an empty set.
    A single unresolvable belief id is still skipped (accepting direction —
    protection for a row that does not resolve is not constructible, and
    one bad id must not strip protection from every good one)."""
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    from backend.memory.graph_store import cited_pairs
    from backend.memory.lineage import LineageStore

    store = TripleStore(tmp_path / "kb.db")
    lineage = LineageStore(tmp_path / "kb.db")
    bid = store.add(subject="qtc", relation="was", object="11.4ms",
                    weight=0.8, tier=1, granularity=Granularity.TOPIC,
                    source="human_assertion")
    rec = lineage.record_turn(
        query="q", injected=[
            {"belief_id": bid, "cited": True,
             "weight_at_injection": 0.8, "tier_at_injection": 1},
            {"belief_id": 999999, "cited": True,
             "weight_at_injection": 0.5, "tier_at_injection": 1}],
        contradictions=[], model_id="m", response_text="r",
        prompt_prefix="p")
    lineage.conn.execute("UPDATE lineage_records SET sealed_block = 1 "
                         "WHERE lineage_id = ?", (rec.lineage_id,))
    lineage.conn.commit()

    pairs = cited_pairs(lineage, store)
    assert ("qtc", "11.4ms") in pairs             # resolvable row protected
    # NON-VACUITY for the failure case: break the store underneath it.
    store.conn.close()
    with pytest.raises(ProtectionUnavailable):
        cited_pairs(lineage, store)
    lineage.close()


def test_the_turn_survives_and_the_frame_declares(tmp_path):
    """End to end: a raising supplier must not break the turn (the original
    comment's concern, kept), and the hebbian_update frame carries the
    declaration an operator can actually see (I-6) — which the console log
    line never was."""
    from backend.kb.granularity import Granularity
    from backend.kb.retriever import TripleRetriever
    from backend.kb.tiered_store import TripleStore
    from backend.loop.agentic_loop import run_turn
    from backend.loop.state import NexusLoopState

    class _P:
        def __init__(self): self.calls = 0

        async def stream_chat(self, model, messages, num_ctx=8192):
            self.calls += 1
            yield "an answer" if self.calls == 1 else "[]"

        async def get_embedding(self, t): return [0.0] * 768
        async def list_models(self): return []
        async def health(self): return True

    store = TripleStore(tmp_path / "kb.db")
    store.add(subject="qtc", relation="was", object="11.4ms", weight=0.8,
              tier=1, granularity=Granularity.TOPIC, source="human_assertion")
    hippo = Hippocampus()
    heb = HebbianUpdater(protected_pairs=_raising_supplier)
    frames = []

    async def drive():
        async for f, _s in run_turn(NexusLoopState(), "what was the qtc?",
                                    retriever=TripleRetriever(store),
                                    hippocampus=hippo, hebbian=heb,
                                    provider=_P()):
            frames.append(f)

    asyncio.run(drive())
    assert frames[-1].startswith("event: done"), "the turn broke"
    hb = next(json.loads(f.split("data: ", 1)[1].strip())
              for f in frames if f.startswith("event: hebbian_update"))
    assert hb["protection"] == "unavailable"
    assert hb["pruned"] is None
    store.close()
