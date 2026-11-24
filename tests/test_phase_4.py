"""Phase 4 tests — required coverage from CLAUDE.md:

  - each tier fires at correct threshold
  - system prompt prefix never modified mid-session

Plus arch-doc invariants:
  - never silent truncate (every reduction emits a CompactionEvent)
  - tier 3 always runs before tier 4 (KEY PRINCIPLE, arch §8.2)
  - lower tiers run before higher tiers (cheapest-first cascade)
  - snip keeps top N session triples
  - microcompact drops only stale triples
  - graph_collapse compresses A->B->C to A->C with averaged weight
  - auto_compact replaces history with [summary, last_user_msg]
"""
from __future__ import annotations

import asyncio

import pytest

from backend.cortex.edges import SynapticEdge
from backend.loop.compaction import (
    CompactionEvent,
    STABLE_PREFIX_BUDGET,
    TIER_1_SNIP_AT,
    TIER_2_MICRO_AT,
    TIER_3_COLLAPSE_AT,
    TIER_4_AUTO_AT,
    TOKEN_BUDGET,
    Tier,
    assert_stable_prefix,
    auto_compact,
    estimate_tokens,
    estimate_tokens_messages,
    estimate_tokens_text,
    graph_collapse,
    microcompact,
    run_cascade,
    snip,
)
from backend.loop.agentic_loop import STABLE_SYSTEM_PREFIX
from backend.loop.state import NexusLoopState, next_state
from backend.memory.hippocampus import Hippocampus


# ---------- threshold constants from arch §8.1 ----------

def test_threshold_constants_are_ordered_and_under_budget():
    assert TOKEN_BUDGET == 8192
    assert TIER_1_SNIP_AT < TIER_2_MICRO_AT < TIER_3_COLLAPSE_AT < TIER_4_AUTO_AT
    assert TIER_4_AUTO_AT < TOKEN_BUDGET


# ---------- helper to fabricate states with a target token count ----------

def _state_with_tokens(target: int, *, n_session_triples: int = 0,
                      n_graph_context: int = 0) -> NexusLoopState:
    """Build a state whose estimate_tokens(...) is >= target.

    session_memory tuples are 4-tuples (source, target, weight, last_fired)
    so the snip tier can recompute current_weight (Fix 3).
    """
    import time as _time
    chars_needed = max(0, target * 4)
    big = "x" * chars_needed
    msg = ({"role": "user", "content": big},)
    now = _time.time()
    sm = tuple(
        (f"s{i}", f"t{i}", round(0.99 - i * 0.01, 3), now)
        for i in range(n_session_triples)
    )
    gc = tuple((f"s{i}", "rel", f"o{i}") for i in range(n_graph_context))
    return NexusLoopState(messages=msg, session_memory=sm, graph_context=gc)


# ---------- under-threshold: cascade is a no-op ----------

def test_no_compaction_when_under_first_threshold():
    s = _state_with_tokens(target=100)
    res = asyncio.run(run_cascade(s, None, prefix=STABLE_SYSTEM_PREFIX))
    assert res.events == []
    assert res.state is s   # no replacement performed


# ---------- token-relief test infrastructure (Fix 1 — cascade feedback) ----------

# Each rendered entry is ~390 chars / 4 ≈ 97 tokens. Padding lets the cascade's
# rebuild_block_fn reflect tier mutations as visible token relief.
PAD = "x" * 360


def _padded_render(state, hippo):
    """Test rebuild_fn — block size depends on session_memory + graph_context
    + hippocampus.edges() so each tier's mutation actually moves the estimator."""
    parts = []
    for t in state.session_memory:
        parts.append(f"[sm] ({t[0]}->{t[1]}) w={t[2]} {PAD}")
    for triple in state.graph_context:
        if len(triple) >= 3:
            parts.append(f"[gc] ({triple[0]} {triple[1]} {triple[2]}) {PAD}")
    if hippo is not None:
        for e in hippo.active_edges():
            parts.append(f"[h] ({e.source}->{e.target}) w={e.weight:.2f} {PAD}")
    return "\n".join(parts)


def _state_with_padded_dynamic(*, n_session=0, n_gc=0, message_tokens=0):
    import time as _time
    msg_chars = max(0, message_tokens * 4)
    msg = ({"role": "user", "content": "x" * msg_chars},) if msg_chars else ()
    now = _time.time()
    sm = tuple(
        (f"s{i}", f"t{i}", round(0.99 - i * 0.02, 3), now)
        for i in range(n_session)
    )
    gc = tuple((f"sg{i}", "rel", f"og{i}") for i in range(n_gc))
    return NexusLoopState(messages=msg, session_memory=sm, graph_context=gc)


def _linear_chain_hippo(n: int) -> Hippocampus:
    h = Hippocampus()
    for i in range(n):
        h.upsert_node(f"n{i}")
    for i in range(n - 1):
        h.add_edge(SynapticEdge(source=f"n{i}", target=f"n{i+1}", weight=0.5))
    return h


# ---------- TIER 1: snip frees tokens via rebuild + does not escalate ----------

def test_tier_1_fires_with_relief_and_no_escalation():
    """T1 snip on a state padded with session_memory frees tokens visible to
    the cascade's rebuild_fn; cascade does NOT escalate to T2."""
    s = _state_with_padded_dynamic(n_session=25, n_gc=0, message_tokens=3500)
    initial_block = _padded_render(s, None)
    res = asyncio.run(run_cascade(
        s, None,
        prefix=STABLE_SYSTEM_PREFIX,
        dynamic_block=initial_block,
        rebuild_block_fn=_padded_render,
    ))
    fired = {ev.tier for ev in res.events}
    assert fired == {Tier.SNIP}, f"expected only T1, got {fired}"
    snip_ev = next(ev for ev in res.events if ev.tier == Tier.SNIP)
    assert snip_ev.tokens_freed > 0, "T1 must free tokens via rebuild_fn"
    assert snip_ev.tokens_after < snip_ev.tokens_before
    assert res.final_tokens < TIER_2_MICRO_AT
    assert len(res.state.session_memory) == 10


def test_snip_keeps_top_n_by_weight():
    import time as _time
    now = _time.time()
    s = NexusLoopState(session_memory=tuple(
        (f"s{i}", f"t{i}", round(i * 0.05, 3), now) for i in range(20)
    ))
    new_s, removed = snip(s, keep_top_n=10)
    assert removed == 10
    assert len(new_s.session_memory) == 10
    weights = [t[2] for t in new_s.session_memory]
    assert min(weights) >= 0.5   # the bottom 10 (weights 0.0–0.45) are dropped
    assert new_s.compaction_state == "snipped"


def test_snip_no_op_below_keep_n():
    import time as _time
    now = _time.time()
    s = NexusLoopState(session_memory=(
        ("a", "b", 0.5, now),
        ("c", "d", 0.6, now),
    ))
    new_s, removed = snip(s, keep_top_n=10)
    assert removed == 0
    assert len(new_s.session_memory) == 2


def test_snip_uses_current_weight_not_stored_weight():
    """Two triples with identical stored weight but different last_fired —
    snip must rank by current_weight (decay since last_fired) and drop the
    older one (Fix 3, arch §8.2)."""
    import time as _time
    now = _time.time()
    fresh    = ("fresh_src", "tgt", 0.6, now)               # decayed by ~0
    stale    = ("stale_src", "tgt", 0.6, now - 24 * 3600)   # 24h decay → ~0.30
    # Pad to force snip to drop one of these two: keep_top_n = 1
    s = NexusLoopState(session_memory=(fresh, stale))
    new_s, removed = snip(s, keep_top_n=1)
    assert removed == 1
    [kept] = new_s.session_memory
    assert kept[0] == "fresh_src", (
        f"expected fresh triple to win on current_weight; got {kept!r}"
    )


# ---------- TIER 2: micro frees tokens via rebuild + does not escalate ----------

def test_tier_2_fires_with_relief_and_no_escalation():
    """T2 micro clears stale graph_context; rebuild reflects the drop;
    cascade does NOT escalate to T3."""
    s = _state_with_padded_dynamic(n_session=0, n_gc=20, message_tokens=4700)
    h = Hippocampus()  # empty — every gc triple is stale
    initial_block = _padded_render(s, h)
    res = asyncio.run(run_cascade(
        s, h,
        prefix=STABLE_SYSTEM_PREFIX,
        dynamic_block=initial_block,
        rebuild_block_fn=_padded_render,
    ))
    fired = {ev.tier for ev in res.events}
    # T1 fires (cheapest-first) but with 0 items removed; T2 does the work.
    assert Tier.MICRO in fired
    assert Tier.COLLAPSE not in fired and Tier.AUTO not in fired
    micro_ev = next(ev for ev in res.events if ev.tier == Tier.MICRO)
    assert micro_ev.tokens_freed > 0, "T2 must free tokens via rebuild_fn"
    assert micro_ev.tokens_after < micro_ev.tokens_before
    assert res.state.graph_context == ()
    assert res.final_tokens < TIER_3_COLLAPSE_AT


def test_microcompact_drops_only_stale_triples():
    h = Hippocampus()
    # Live, recent, strong: keep
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.8,
                            last_fired=__import__("time").time()))
    # Live, old, weak: drop
    h.add_edge(SynapticEdge(source="x", target="y", weight=0.1,
                            last_fired=1.0))
    s = NexusLoopState(graph_context=(("a", "rel", "b"), ("x", "rel", "y")))
    new_s, removed = microcompact(s, h, age_seconds=60, weight_threshold=0.3)
    assert removed == 1
    assert ("a", "rel", "b") in new_s.graph_context
    assert ("x", "rel", "y") not in new_s.graph_context
    assert new_s.compaction_state == "microcompacted"


def test_microcompact_drops_triples_with_no_live_edge():
    s = NexusLoopState(graph_context=(("ghost", "rel", "phantom"),))
    new_s, removed = microcompact(s, Hippocampus())
    assert removed == 1
    assert new_s.graph_context == ()


# ---------- TIER 3: collapse frees tokens via rebuild + does not escalate ----------

def test_tier_3_fires_with_relief_and_no_escalation():
    """T3 collapse compresses linear hippo chain; rebuild reflects the drop;
    cascade does NOT escalate to T4."""
    s = _state_with_padded_dynamic(n_session=0, n_gc=0, message_tokens=5000)
    h = _linear_chain_hippo(25)
    initial_block = _padded_render(s, h)
    res = asyncio.run(run_cascade(
        s, h,
        prefix=STABLE_SYSTEM_PREFIX,
        dynamic_block=initial_block,
        rebuild_block_fn=_padded_render,
    ))
    fired = {ev.tier for ev in res.events}
    assert Tier.COLLAPSE in fired
    assert Tier.AUTO not in fired
    collapse_ev = next(ev for ev in res.events if ev.tier == Tier.COLLAPSE)
    assert collapse_ev.tokens_freed > 0, "T3 must free tokens via rebuild_fn"
    assert collapse_ev.tokens_after < collapse_ev.tokens_before
    assert res.final_tokens < TIER_4_AUTO_AT


def test_graph_collapse_compresses_linear_path():
    h = Hippocampus()
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.6))
    h.add_edge(SynapticEdge(source="b", target="c", weight=0.4))
    n = graph_collapse(h)
    assert n == 1
    assert h.get_edge("a", "c") is not None
    assert abs(h.get_edge("a", "c").weight - 0.5) < 1e-6
    # E.3/I-2: source edges are demoted, never removed — deleting them
    # would destroy their accumulated recurrence_count.
    assert h.get_edge("a", "b").demoted is True
    assert h.get_edge("b", "c").demoted is True


def test_graph_collapse_skips_branching_nodes():
    h = Hippocampus()
    h.add_edge(SynapticEdge(source="a", target="b", weight=0.6))
    h.add_edge(SynapticEdge(source="b", target="c", weight=0.4))
    h.add_edge(SynapticEdge(source="b", target="d", weight=0.4))   # b branches
    n = graph_collapse(h)
    assert n == 0   # b has out-degree 2, not collapsible


# ---------- TIER 4 + cascade short-circuit ----------

def test_cascade_short_circuits_when_tier_3_clears_threshold():
    """If T3 brings tokens under T4 threshold, T4 must NOT fire even though
    initial tokens were over T4 — KEY PRINCIPLE, arch §8.2."""
    s = _state_with_padded_dynamic(
        n_session=25, n_gc=5, message_tokens=5000,
    )
    h = _linear_chain_hippo(25)
    initial_block = _padded_render(s, h)
    res = asyncio.run(run_cascade(
        s, h,
        prefix=STABLE_SYSTEM_PREFIX,
        dynamic_block=initial_block,
        rebuild_block_fn=_padded_render,
    ))
    fired = [ev.tier for ev in res.events]
    assert Tier.SNIP in fired and Tier.MICRO in fired and Tier.COLLAPSE in fired
    assert Tier.AUTO not in fired, (
        f"T4 must not fire when T3 cleared threshold; events={fired}"
    )
    assert res.final_tokens < TIER_4_AUTO_AT
    # Token relief monotonic across all firing tiers
    for ev in res.events:
        assert ev.tokens_after <= ev.tokens_before, (
            f"tier {ev.tier} regressed tokens: {ev.tokens_before}→{ev.tokens_after}"
        )


def test_tier_4_fires_when_lower_tiers_cannot_relieve_below_threshold():
    """Messages dominate the prompt; lower tiers cannot free enough; T4 fires
    and KEY PRINCIPLE (T3 always before T4) holds."""
    s = NexusLoopState(messages=(
        {"role": "user", "content": "x" * (8500 * 4)},
    ))
    initial_block = _padded_render(s, None)
    res = asyncio.run(run_cascade(
        s, None,
        prefix=STABLE_SYSTEM_PREFIX,
        dynamic_block=initial_block,
        rebuild_block_fn=_padded_render,
    ))
    fired = [ev.tier for ev in res.events]
    assert Tier.AUTO in fired
    # KEY PRINCIPLE: T3 always before T4
    assert fired.index(Tier.COLLAPSE) < fired.index(Tier.AUTO)
    # AutoCompact replaced messages with [summary, last_user]. BA fix 1: the
    # summary is an assistant-role message inside a runtime delimiter, never
    # a system message (tests/test_ba_fix1_summary_role.py holds the rest).
    assert len(res.state.messages) == 2
    assert res.state.messages[0]["role"] == "assistant"
    assert "[CONVERSATION SUMMARY]" in res.state.messages[0]["content"]
    assert res.state.messages[0]["content"].startswith('<nexus-block source="compaction">')
    assert res.state.messages[1]["role"] == "user"


def test_auto_compact_uses_injected_summariser():
    captured = {}

    async def fake_summariser(messages):
        captured["calls"] = captured.get("calls", 0) + 1
        captured["msg_count"] = len(messages)
        return "explicit summary"

    s = NexusLoopState(messages=(
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "what is python"},
    ))
    new_s, removed = asyncio.run(auto_compact(s, summariser=fake_summariser))
    assert captured["calls"] == 1
    assert captured["msg_count"] == 3
    assert "explicit summary" in new_s.messages[0]["content"]
    assert removed == 1   # 3 messages -> [summary, last_user]


# ---------- never silent truncate ----------

def test_never_silent_truncate_every_reduction_emits_event():
    target = TIER_4_AUTO_AT + 500
    s = _state_with_tokens(target=target, n_session_triples=15, n_graph_context=5)
    h = Hippocampus()
    res = asyncio.run(run_cascade(s, h, prefix=STABLE_SYSTEM_PREFIX))

    # 4 events, 4 distinct tiers, every event has populated bookkeeping.
    assert len(res.events) == 4
    assert {e.tier for e in res.events} == {
        Tier.SNIP, Tier.MICRO, Tier.COLLAPSE, Tier.AUTO,
    }
    for ev in res.events:
        assert isinstance(ev, CompactionEvent)
        assert ev.tokens_before >= 0
        assert ev.tokens_after >= 0
        assert ev.reason   # non-empty reason string


# ---------- Law VII: stable system prefix invariant ----------

def test_stable_prefix_unchanged_after_compaction():
    """Compaction must NEVER mutate STABLE_SYSTEM_PREFIX (Law VII)."""
    snapshot = STABLE_SYSTEM_PREFIX
    target = TIER_4_AUTO_AT + 500
    s = _state_with_tokens(target=target, n_session_triples=15, n_graph_context=5)
    asyncio.run(run_cascade(s, Hippocampus(), prefix=STABLE_SYSTEM_PREFIX))
    # The module-level constant is identical bytes-for-bytes.
    from backend.loop.agentic_loop import STABLE_SYSTEM_PREFIX as after
    assert after == snapshot
    assert after is snapshot   # same string object — no rebinding


def test_assert_stable_prefix_passes_when_block_appended():
    composed = STABLE_SYSTEM_PREFIX + "\n\n[KB CONTEXT — fresh block]"
    assert_stable_prefix(composed, STABLE_SYSTEM_PREFIX)  # no raise


def test_assert_stable_prefix_raises_when_prefix_modified():
    tampered = "MUTATED\n" + STABLE_SYSTEM_PREFIX
    with pytest.raises(RuntimeError, match="Law VII"):
        assert_stable_prefix(tampered, STABLE_SYSTEM_PREFIX)


def test_assert_stable_prefix_raises_when_prefix_truncated():
    truncated = STABLE_SYSTEM_PREFIX[:30]
    with pytest.raises(RuntimeError, match="Law VII"):
        assert_stable_prefix(truncated, STABLE_SYSTEM_PREFIX)


# ---------- token estimator sanity ----------

def test_estimate_tokens_text_grows_with_length():
    a = estimate_tokens_text("hello")
    b = estimate_tokens_text("hello " * 100)
    assert b > a


def test_estimate_tokens_messages_includes_role_overhead():
    msgs = ({"role": "user", "content": ""},)
    # empty content still costs the per-message overhead (~4 tokens) + 1 from text estimator
    assert estimate_tokens_messages(msgs) >= 4
