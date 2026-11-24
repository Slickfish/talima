"""Packet AK — the weight that comes back.

THE LEAK (I-8, violated in the direction nothing guarded)
    `_ingest_hits_into_hippocampus` folds every retrieved hit into the
    session graph with `weight=rt.triple.weight`, and `ingest_triple` sets
    `edge.weight = max(edge.weight, weight)`. For a substrate row
    (`co_activated` / `recurring_query_term`) that store weight is a
    MONOTONE HIGH-WATER MARK — `add()` MAX-upserts it on every promotion —
    so one retrieval restores the live edge to its historical maximum,
    silently undoing Hebbian weakening (x0.95 per missed prediction) and
    the KAIROS LTD pass (0.02/day, applied as 1/24 day per cycle: ~0.24/day
    of decay at a five-minute poll, all of it reversible by one retrieval).
    The record layer was writing the prominence layer, and every guard
    watched writes TO the store, never reads FROM it.

THE LEGITIMATE CASE, WHICH MUST SURVIVE
    For a CLAIM row the same `max()` is how a retrieved belief warms the
    session graph — the row's weight is the claim's evidential weight, not
    the graph's own recycled output. The distinguisher at the call site is
    the relation: the retriever's 20% cap (retriever.py:254) and the
    semantic index's exclusion already partition on exactly it.

THE SENTINELS, per the packet:
    - a genuinely decayed edge stays decayed when its consolidated row is
      retrieved (real decay pass, not a mocked weight);
    - a claim triple still warms the graph — pinned in the accepting
      direction;
    - the RECORD is untouched: the row's stored weight is identical before
      and after a retrieval that no longer restores;
    - reducibility both ways: NEXUS_SUBSTRATE_WEIGHT_RESTORE=on is the
      pre-AK `max()` exactly;
    - suppressions are DECLARED (I-6) — counted where an operator can see
      them, because a silent behaviour change in the graph is how this got
      here;
    - the store's weight has exactly the graph-writing readers it has
      today, pinned in both directions by AST;
    - non-vacuity: the fixture edge really decayed and the row really was
      higher, or the test passes with nothing to restore.
"""
from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tracked import tracked_backend                              # noqa: E402

from backend.cortex.edges import SynapticEdge
from backend.cortex.hebbian import HebbianUpdater
from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import run_turn
from backend.loop.state import NexusLoopState
from backend.memory.hippocampus import Hippocampus

ROOT = Path(__file__).resolve().parent.parent


class _Provider:
    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "Nothing that cites anything."

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _rig(tmp_path, *, relation, source):
    """One retrievable row whose live edge has GENUINELY decayed below it."""
    store = TripleStore(tmp_path / "kb.db")
    store.add(subject="safety", relation=relation, object="qtc",
              weight=0.9, tier=1, granularity=Granularity.SESSION,
              source=source)
    hippo = Hippocampus()
    hippo.add_edge(SynapticEdge(source="safety", target="qtc", weight=0.9))
    # REAL decay, the packet insists — the KAIROS LTD path, not a mocked
    # weight: five days at 0.02/day.
    HebbianUpdater().decay_pass(hippo, days=5.0)
    edge = hippo.get_edge("safety", "qtc")
    # NON-VACUITY: the edge really decayed, and the row really is higher.
    assert edge.weight == pytest.approx(0.8)
    assert store.get_by_id(1).weight > edge.weight
    return store, hippo


def _turn(store, hippo, query="tell me about safety"):
    frames = []

    async def drive():
        async for frame, _s in run_turn(
                NexusLoopState(), query,
                retriever=TripleRetriever(store), hippocampus=hippo,
                provider=_Provider()):
            frames.append(frame)

    asyncio.run(drive())
    done = next(json.loads(f.split("data: ", 1)[1].strip())
                for f in reversed(frames) if f.startswith("event: done\n"))
    return done


# ---------------------------------------------------------------------------
# THE SENTINEL
# ---------------------------------------------------------------------------

def test_a_decayed_edge_stays_decayed_when_its_row_is_retrieved(tmp_path):
    store, hippo = _rig(tmp_path, relation="co_activated",
                        source="hippocampus_consolidation")
    done = _turn(store, hippo)
    assert done["reinforcement"]["injected"] >= 1, (
        "vacuous: the substrate row was never retrieved")
    assert hippo.get_edge("safety", "qtc").weight == pytest.approx(0.8), (
        "the record layer wrote the prominence layer again — five days of "
        "LTD were undone by one retrieval of the mechanism's own output")
    store.close()


def test_a_claim_triple_still_warms_the_graph(tmp_path):
    """The accepting direction, pinned — or the fix reads as a regression
    the day someone notices retrieved claims no longer refresh the graph."""
    store, hippo = _rig(tmp_path, relation="raises", source="extraction")
    done = _turn(store, hippo)
    assert done["reinforcement"]["injected"] >= 1
    assert hippo.get_edge("safety", "qtc").weight == pytest.approx(0.9), (
        "a CLAIM row no longer warms the session graph — the fix "
        "overshot the substrate and hit the legitimate case")
    store.close()


def test_the_record_is_untouched_by_the_suppression(tmp_path):
    store, hippo = _rig(tmp_path, relation="co_activated",
                        source="hippocampus_consolidation")
    before = store.get_by_id(1).weight
    _turn(store, hippo)
    assert store.get_by_id(1).weight == before == pytest.approx(0.9), (
        "this is a read-path change; the row's stored weight moved")
    store.close()


def test_suppressions_are_declared_not_silent(tmp_path):
    """I-6 — a silent behaviour change in the graph is how this got here.
    The count lives on the hippocampus and is surfaced on /cortex/stats."""
    store, hippo = _rig(tmp_path, relation="co_activated",
                        source="hippocampus_consolidation")
    assert hippo.restorations_suppressed == 0
    _turn(store, hippo)
    assert hippo.restorations_suppressed == 1
    # A second retrieval of the same row suppresses again — still higher.
    _turn(store, hippo)
    assert hippo.restorations_suppressed == 2
    store.close()


def test_the_stats_surface_carries_the_count(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", "ak-token")
    from fastapi.testclient import TestClient
    from backend.server import create_app, create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    client = TestClient(create_app(svc))
    block = client.get("/cortex/stats").json()["weight_restoration"]
    assert block["substrate_restore_enabled"] is False
    assert block["suppressed_total"] == 0
    assert "record layer" in block["note"]


# ---------------------------------------------------------------------------
# Reducibility, both positions
# ---------------------------------------------------------------------------

def test_the_rollback_is_the_pre_ak_max_exactly(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_SUBSTRATE_WEIGHT_RESTORE", "on")
    store, hippo = _rig(tmp_path, relation="co_activated",
                        source="hippocampus_consolidation")
    _turn(store, hippo)
    assert hippo.get_edge("safety", "qtc").weight == pytest.approx(0.9), (
        "the rollback position does not reproduce the pre-AK restoration")
    assert hippo.restorations_suppressed == 0, (
        "the rollback restored AND counted a suppression — the counter is "
        "overstating (I-7)")
    store.close()


def test_the_default_position_is_off(monkeypatch):
    monkeypatch.delenv("NEXUS_SUBSTRATE_WEIGHT_RESTORE", raising=False)
    from backend.loop.agentic_loop import substrate_weight_restore_enabled
    assert substrate_weight_restore_enabled() is False


# ---------------------------------------------------------------------------
# The read-pin: store weight's graph-writing readers, both directions
# ---------------------------------------------------------------------------

def _ingest_call_sites() -> set[tuple[str, str]]:
    """(module, innermost enclosing function) of every `ingest_triple` call
    in tracked backend files — the only path that copies a store row's
    weight into a live edge."""
    sites: set[tuple[str, str]] = set()

    def visit(node, rel, current):
        for child in ast.iter_child_nodes(node):
            name = current
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if isinstance(child, ast.Call) and (
                    getattr(child.func, "attr", None) == "ingest_triple"
                    or getattr(child.func, "id", None) == "ingest_triple"):
                sites.add((rel, current))
            visit(child, rel, name)

    for path in tracked_backend():
        rel = path.relative_to(ROOT).as_posix()
        visit(ast.parse(path.read_text(encoding="utf-8")), rel, "<module>")
    return sites


def test_store_weight_reaches_the_graph_through_exactly_one_site():
    """This leak existed because nothing watched READS of the store's
    weight. Pinned in both directions: a new ingest site fails the build
    until someone decides it should exist, and a vanished one fails so the
    pin cannot rot into reassurance."""
    assert _ingest_call_sites() == {
        ("backend/loop/agentic_loop.py", "_ingest_hits_into_hippocampus")}


def test_the_read_pin_can_actually_fail():
    """NON-VACUITY: the detector sees the exact shape a new reader takes."""
    tree = ast.parse("def rogue(h, t):\n"
                     "    h.ingest_triple(t.subject, t.relation, t.object,\n"
                     "                    weight=t.weight)\n")
    hits = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "attr", None) == "ingest_triple"]
    assert len(hits) == 1
