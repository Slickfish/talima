"""B-43 — `done.reinforcement` counts what was written, not what was called.

Under FZ-2 the loop counted its calls to `fire()`, so a frozen turn reported
`reinforced: 2` beside `learning: frozen` while no row moved. Now `fire()`
says whether it wrote: `reinforced` is the writes made (0 under frozen) and
`skipped` is the attempts the freeze refused. `done` moves to contract
version 2 for the new key (pinned in tests/test_ba_fix2_request_hash.py).

THE CLAIMS ARE PLANTED WITH PLAIN-WORD OBJECTS, DELIBERATELY. The citation
test is whole-token containment of the subject and the object in the answer,
and a store written by extraction holds underscore-joined objects the model's
prose never reproduces (the register carries the screenshot: 3 of 3 missed).
A fixture that planted those would read `cited: 0`, so `reinforced` and
`skipped` would both be 0 and the test would pass without testing anything.
`cited == 2` is asserted first, as non-vacuity.

THE COUNTER EQUALITY IS A DELTA. `skipped_writes_since_start` is process-wide
and cumulative; "`skipped` equals the counter's `fire` entry" holds on the
first frozen turn of a fresh process by accident and fails on the second. The
frozen arm runs two turns and compares the second turn's `skipped` to the
difference the counter moved by.
"""
from __future__ import annotations

import asyncio
import json
import sys

from backend import learning
from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import run_turn
from backend.loop.state import NexusLoopState

QUESTION = "does creatine improve strength and does caffeine improve endurance?"
ANSWER = "Creatine improves strength and caffeine improves endurance."
CLAIMS = [("creatine", "improves", "strength"), ("caffeine", "improves", "endurance")]


class _Provider:
    """The answer, then an empty extraction — two stream_chat calls per turn."""

    def __init__(self):
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        if self.calls % 2 == 1:
            for tok in ANSWER.split(" "):
                yield tok + " "
        else:
            yield "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _plant(tmp_path) -> TripleStore:
    store = TripleStore(tmp_path / "kb.db")
    for s, r, o in CLAIMS:
        store.add(subject=s, relation=r, object=o, weight=0.9, tier=1,
                  granularity=Granularity.TOPIC, source="wikidata")
    return store


def _turn(store, provider, state=None):
    """One turn through the production loop; the final `done` payload and the new state."""
    frames, out = [], {}

    async def drive():
        async for frame, st in run_turn(state or NexusLoopState(), QUESTION,
                                        retriever=TripleRetriever(store), provider=provider):
            frames.append(frame)
            out["state"] = st

    asyncio.run(drive())
    done = next(json.loads(f.split("data: ", 1)[1].strip()) for f in reversed(frames)
                if f.startswith("event: done\n"))
    return done, out["state"]


def _fire_skips() -> int:
    return learning.skipped_writes()["by_method"].get("TripleStore.fire", 0)


def test_frozen_turn_counts_writes_made_and_attempts_refused(tmp_path, monkeypatch):
    """Frozen, two claims cited: reinforced 0, skipped 2, and skipped equals the counter's
    movement over the turn — on the SECOND turn, so a cumulative counter cannot pass by
    coincidence."""
    monkeypatch.setenv("NEXUS_LEARNING", "frozen")
    store = _plant(tmp_path)
    provider = _Provider()
    before_rows = {t.id: (t.fire_count, t.last_fired) for t in store.all()}
    first, state = _turn(store, provider)
    assert first["reinforcement"]["cited"] == 2, f"non-vacuity: {first['reinforcement']}"
    counter_before = _fire_skips()
    done, _ = _turn(store, provider, state)
    counter_after = _fire_skips()
    r = done["reinforcement"]
    print(f"\nB-43 frozen (turn 2, {sys.platform}): {r}; counter fire {counter_before} -> {counter_after}; "
          f"learning {done['learning']['display']}")
    assert r["cited"] == 2, "non-vacuity: both planted claims were cited"
    assert r["reinforced"] == 0
    assert r["skipped"] == 2
    assert r["skipped"] == counter_after - counter_before, "the delta, not the absolute"
    assert r["injected"] == r["reinforced"] + r["skipped"] + r["suppressed"]
    assert done["contract_version"] == 4 and done["learning"]["state"] == "frozen"
    after_rows = {t.id: (t.fire_count, t.last_fired) for t in store.all()}
    assert {k: after_rows[k] for k in before_rows} == before_rows, "no planted row moved"
    store.close()


def test_live_turn_counts_the_same_two_as_written(tmp_path, monkeypatch):
    """Companion: the same turn live reads reinforced 2, skipped 0, and both rows moved."""
    monkeypatch.setenv("NEXUS_LEARNING", "live")
    store = _plant(tmp_path)
    before = {t.id: t.fire_count for t in store.all()}
    counter_before = _fire_skips()
    done, _ = _turn(store, _Provider())
    r = done["reinforcement"]
    print(f"\nB-43 live ({sys.platform}): {r}")
    assert r["cited"] == 2, "non-vacuity"
    assert r["reinforced"] == 2 and r["skipped"] == 0
    assert _fire_skips() == counter_before, "live moved the skip counter"
    after = {t.id: t.fire_count for t in store.all()}
    assert all(after[k] == before[k] + 1 for k in before), (before, after)
    assert done["contract_version"] == 4
    store.close()


def test_fire_reports_what_it_did(tmp_path, monkeypatch):
    """The writer says whether it wrote: True live, False frozen, and the frozen call is counted."""
    store = _plant(tmp_path)
    tid = store.all()[0].id
    monkeypatch.setenv("NEXUS_LEARNING", "live")
    assert store.fire(tid) is True
    monkeypatch.setenv("NEXUS_LEARNING", "frozen")
    before = _fire_skips()
    assert store.fire(tid) is False
    assert _fire_skips() == before + 1
    assert store.get_by_id(tid).fire_count == 1
    store.close()
