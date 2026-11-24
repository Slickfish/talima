"""Packet H.3a — the KAIROS -> client alert bridge honours the event key.

WHY THIS SHIPS ALONE, AHEAD OF ANY NEW EMIT
    KAIROS runs on idle time and has no live SSE channel. It enqueues onto
    `NexusServices.pending_alerts`, and the drain at the top of each /chat
    turn is the ONLY path by which anything KAIROS observes reaches a client.
    Every alert in the system flows through it.

    That drain used to strip the `event` key its producers minted for routing
    and hardcode every frame to `kb_pressure_alert` — a router with exactly
    one destination. This is why the endpoint audit found three declared SSE
    events never emitted: adding an emit call in KAIROS was never going to be
    sufficient, because the bridge would have relabelled it on the way out.

    Changing a shared path is the risk. So the fix ships by itself, and the
    load-bearing assertion is not that new routing works — it is that
    **every existing alert still arrives exactly as it did before.**

THE SIX DECLARED kb_pressure_alert TYPES (docs/CONTRACTS.md)
    gap · staleness · dilution · semantic_duplicates · cortex_drift · web_search

    Only three have a producer today: `semantic_duplicates` and `cortex_drift`
    (KAIROS, via this bridge) and `web_search` (emitted directly in the loop,
    not through the bridge at all). `gap`, `staleness` and `dilution` are
    declared with nothing to emit them — the same dead-contract-member class
    as the three SSE events, one level down. Recorded here, not fixed: this
    packet is the bridge.

    All six are exercised below regardless of who produces them, because the
    property under test is that the bridge does not break the declared
    vocabulary — including the members a future producer will fill in.
"""
import asyncio
import json
import logging

import pytest

from backend.loop.agentic_loop import (
    BRIDGE_EVENTS, DEFAULT_ALERT_EVENT, _bridge_event_name, run_turn,
)
from backend.loop.state import NexusLoopState
from tests._tracked import tracked_backend

# docs/CONTRACTS.md, "`kb_pressure_alert` valid types". Hardcoded deliberately: this
# is the contract the bridge must not break, so it is stated independently of
# whatever the code currently happens to produce.
DECLARED_ALERT_TYPES = (
    "gap", "staleness", "dilution",
    "semantic_duplicates", "cortex_drift", "web_search",
)

# Representative payloads, shaped as their producers mint them.
ALERT_PAYLOADS = {
    "gap": {"event": "kb_pressure_alert", "type": "gap", "domain": "kb",
            "action": "seed_proposed", "density": 0.08},
    "staleness": {"event": "kb_pressure_alert", "type": "staleness",
                  "domain": "medicine", "action": "refresh_proposed",
                  "volatility": 0.7, "age_days": 400},
    "dilution": {"event": "kb_pressure_alert", "type": "dilution",
                 "domain": "kb", "action": "review", "cold_fraction": 0.62},
    "semantic_duplicates": {"event": "kb_pressure_alert",
                            "type": "semantic_duplicates", "domain": "kb",
                            "action": "review_required", "count": 3},
    "cortex_drift": {"event": "kb_pressure_alert", "type": "cortex_drift",
                     "domain": "cortex", "action": "consolidation_recommended",
                     "distance": 0.47, "severity": "alert"},
    "web_search": {"event": "kb_pressure_alert", "type": "web_search",
                   "domain": "kb", "action": "proposed", "query": "latest news"},
}


class _Provider:
    def __init__(self):
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        yield "ok" if self.calls == 1 else "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def drain(pending: list) -> list[tuple[str, dict]]:
    """Run one turn and return (event_name, data) for every frame emitted
    before the turn proper — i.e. what the bridge produced."""
    frames: list[str] = []

    async def go():
        async for frame, _state in run_turn(
            NexusLoopState(), "hello", provider=_Provider(),
            pending_alerts=pending,
        ):
            frames.append(frame)

    asyncio.run(go())
    out = []
    for f in frames:
        lines = f.split("\n")
        ev = next((l[7:] for l in lines if l.startswith("event: ")), "")
        raw = next((l[6:] for l in lines if l.startswith("data: ")), "")
        try:
            data = json.loads(raw)
        except Exception:
            data = raw
        out.append((ev, data))
    return out


# ---------------------------------------------------------------------------
# THE LOAD-BEARING TEST — nothing existing may change
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("alert_type", DECLARED_ALERT_TYPES)
def test_every_declared_alert_type_still_arrives_unchanged(alert_type):
    """Each of the six arrives as `kb_pressure_alert`, with every field intact
    and the routing key stripped — exactly the pre-H.3a contract."""
    payload = dict(ALERT_PAYLOADS[alert_type])
    emitted = drain([dict(payload)])

    matching = [(ev, d) for ev, d in emitted
                if ev == "kb_pressure_alert" and d.get("type") == alert_type]
    assert len(matching) == 1, (
        f"{alert_type} did not arrive exactly once as kb_pressure_alert: "
        f"{[e for e, _ in emitted]}")

    _ev, data = matching[0]
    expected = {k: v for k, v in payload.items() if k != "event"}
    assert data == expected, (
        f"{alert_type} payload changed crossing the bridge:\n"
        f"  expected {expected}\n  got      {data}")
    assert "event" not in data, "routing key leaked into the frame body"


def test_all_six_types_in_one_drain_keep_order_and_content():
    """The queue is drained FIFO and nothing is merged, dropped or reordered."""
    queued = [dict(ALERT_PAYLOADS[t]) for t in DECLARED_ALERT_TYPES]
    emitted = drain(queued)

    alerts = [(ev, d) for ev, d in emitted if ev == "kb_pressure_alert"]
    # NON-VACUITY: the turn must actually have drained six, or the ordering
    # claim below is about an empty list.
    assert len(alerts) >= 6
    got_types = [d.get("type") for _e, d in alerts][:6]
    assert got_types == list(DECLARED_ALERT_TYPES), (
        f"order or content changed: {got_types}")


def test_queue_is_emptied_by_the_drain():
    pending = [dict(ALERT_PAYLOADS["cortex_drift"])]
    drain(pending)
    assert pending == [], "alerts were left queued and would re-fire next turn"


# ---------------------------------------------------------------------------
# The new capability
# ---------------------------------------------------------------------------

def test_bridge_routes_a_declared_event_name():
    """The point of the change: an allowed `event` reaches the client under
    its own name instead of being relabelled."""
    assert _bridge_event_name({"event": "kb_pressure_alert"}) == "kb_pressure_alert"
    for name in BRIDGE_EVENTS:
        assert _bridge_event_name({"event": name}) == name


def test_a_newly_allowed_event_reaches_the_client_under_its_own_name(monkeypatch):
    """The capability H.3b depends on, proven now rather than assumed.

    `BRIDGE_EVENTS` currently holds one member, so without this the routing
    fix would ship with its actual purpose untested — "presence verified,
    behaviour unverified" (pack §15.4), which is the defect class this whole
    sequence of packets exists to stop. Here the allowed set is widened for
    one test and a `chain_appended` payload is followed all the way to the
    frame a client would receive.
    """
    import backend.loop.agentic_loop as loop
    monkeypatch.setattr(
        loop, "BRIDGE_EVENTS",
        frozenset({"kb_pressure_alert", "chain_appended"}))

    emitted = drain([
        {"event": "chain_appended", "index": 7, "hash": "abc123"},
        {"event": "kb_pressure_alert", "type": "cortex_drift", "domain": "cortex"},
    ])

    chain = [(e, d) for e, d in emitted if e == "chain_appended"]
    assert len(chain) == 1, (
        "a routable event still did not reach the client under its own name")
    assert chain[0][1] == {"index": 7, "hash": "abc123"}
    # ...and the existing alert alongside it is untouched.
    alerts = [(e, d) for e, d in emitted if e == "kb_pressure_alert"]
    assert len(alerts) == 1 and alerts[0][1]["type"] == "cortex_drift"


def test_missing_event_key_falls_back_to_historical_behaviour():
    """Pre-H.3a producers minted no routing key. They must be unaffected."""
    assert _bridge_event_name({}) == DEFAULT_ALERT_EVENT
    assert _bridge_event_name({"type": "gap"}) == DEFAULT_ALERT_EVENT
    assert _bridge_event_name({"event": None}) == DEFAULT_ALERT_EVENT

    emitted = drain([{"type": "gap", "domain": "kb", "action": "seed_proposed"}])
    alerts = [(e, d) for e, d in emitted if e == "kb_pressure_alert"]
    assert len(alerts) == 1
    assert alerts[0][1]["type"] == "gap"


def test_unrecognised_event_is_delivered_not_dropped_and_declared(caplog):
    """A name the bridge does not route must not vanish and must not become a
    new event type by accident.

    Both failure modes are worse than the fallback: a dropped alert is silent
    loss, and an unbounded router would let a typo mint an event no client
    knows and no contract declares — which is how the SSE contract drifted in
    the first place.
    """
    payload = {"event": "chian_appended",       # deliberate typo
               "type": "gap", "domain": "kb", "action": "x"}
    with caplog.at_level(logging.WARNING, logger="nexus.loop"):
        emitted = drain([dict(payload)])

    alerts = [(e, d) for e, d in emitted if e == DEFAULT_ALERT_EVENT]
    assert len(alerts) == 1, "the alert was dropped"
    assert alerts[0][1]["type"] == "gap"        # content survived intact
    assert not [e for e, _ in emitted if e == "chian_appended"]
    msg = "\n".join(r.getMessage() for r in caplog.records)
    assert "chian_appended" in msg and "BRIDGE_EVENTS" in msg


def test_kairos_producers_only_mint_event_names_the_bridge_routes():
    """The coupling that would otherwise fail silently.

    KAIROS mints the routing key; the bridge decides what it means. If a
    producer names an event the bridge does not route, the alert still
    arrives (as `kb_pressure_alert`) but under the wrong name — the exact
    defect H.3a fixes, reintroduced from the other end. Asserted against the
    producer source so the two vocabularies cannot drift apart.
    """
    import pathlib
    import re
    kairos = (pathlib.Path(__file__).resolve().parent.parent
              / "backend" / "memory" / "kairos.py").read_text(encoding="utf-8")
    # Every "event": "..." inside a notify_ui payload.
    minted = set(re.findall(r'"event":\s*"([a-z_]+)"', kairos))
    assert minted, "no notify_ui payloads found — has the producer moved?"
    unroutable = minted - BRIDGE_EVENTS
    assert not unroutable, (
        f"KAIROS mints {sorted(unroutable)}, which the bridge does not route; "
        f"those alerts would arrive relabelled as {DEFAULT_ALERT_EVENT}")


def test_enqueued_payload_keeps_its_routing_key_end_to_end():
    """`_enqueue_alert` is a one-line append, and it is the only link between
    a KAIROS producer and this bridge. Pinned so a future 'tidy-up' cannot
    strip the key on the way into the queue instead of on the way out."""
    pending: list = []

    async def enqueue(payload: dict) -> None:      # mirrors server.py
        pending.append(payload)

    asyncio.run(enqueue({"event": "kb_pressure_alert", "type": "cortex_drift",
                         "domain": "cortex", "severity": "warn"}))
    assert pending[0]["event"] == "kb_pressure_alert"

    emitted = drain(pending)
    alerts = [(e, d) for e, d in emitted if e == "kb_pressure_alert"]
    assert len(alerts) == 1
    assert alerts[0][1] == {"type": "cortex_drift", "domain": "cortex",
                            "severity": "warn"}


def test_bridge_events_is_a_ratchet_not_an_open_door():
    """Adding a routable event is a deliberate act, not an emergent one.

    If this fails because a member was added, that is the review step: confirm
    the event is in docs/CONTRACTS.md's SSE contract, then update this list.
    """
    # H.3b widened this by two. The ratchet fired on the change, which is the
    # review step: both were confirmed present in docs/CONTRACTS.md's SSE contract
    # before being acknowledged here.
    # AX.1 widened it by one: `curiosity_surfaced` moved from a per-turn
    # `sse_event` call to the KAIROS queue, and is bridged under its own
    # name. Declared in docs/CONTRACTS.md's SSE contract since Phase 9; the ratchet
    # fired on the change and this is its acknowledgement.
    assert BRIDGE_EVENTS == frozenset({
        "kb_pressure_alert", "chain_appended", "kairos_consolidation",
        "curiosity_surfaced",
    }), (f"BRIDGE_EVENTS changed to {sorted(BRIDGE_EVENTS)} — is each new "
         f"member declared in docs/CONTRACTS.md's SSE contract?")


def test_every_bridge_event_is_declared_in_the_contract():
    """The ratchet's question, asked mechanically rather than in a comment.

    A member added here that docs/CONTRACTS.md does not declare would let the bridge
    mint an undeclared event — the drift H.3a bounded it against.
    """
    import pathlib
    import re
    claude = (pathlib.Path(__file__).resolve().parent.parent
              / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    block = claude.split("## SSE Events")[1].split("**`kb_pressure_alert`")[0]
    declared = set(re.findall(r"^([a-z_]+)\s", block, re.M))
    undeclared = BRIDGE_EVENTS - declared
    assert not undeclared, (
        f"the bridge routes {sorted(undeclared)}, which docs/CONTRACTS.md's SSE "
        f"contract does not declare")


# ---------------------------------------------------------------------------
# Recorded, not fixed
# ---------------------------------------------------------------------------

def test_three_declared_alert_types_have_no_producer():
    """`gap`, `staleness` and `dilution` are declared in docs/CONTRACTS.md and nothing
    emits them — dead contract members one level below the three dead SSE
    events. Pinned so the count cannot quietly grow while nobody is looking.

    This test passing is a FINDING, not a success. When a producer is added,
    this fails and the entry moves out of the list.
    """
    import pathlib
    import re
    backend = pathlib.Path(__file__).resolve().parent.parent / "backend"
    src = "\n".join(p.read_text(encoding="utf-8") for p in tracked_backend())
    producerless = [
        t for t in DECLARED_ALERT_TYPES
        if not re.search(r'"type":\s*"' + t + r'"', src)
    ]
    assert producerless == ["gap", "staleness", "dilution"], (
        f"the set of producerless alert types changed: {producerless}")
