"""CE — the cascade establish (B-28). Characterisation: these pin what was
MEASURED on the rig in tests/fixtures/ce_rig.py, and each names its rig.
Nothing under backend/ changed; if a pin here moves, the cascade, the
estimator or the renderer moved, and that is the finding.

S1.1, THE STOPPING RULE, ONE SENTENCE (backend/loop/compaction.py):
    At entry (:561) and after each of tiers 1–3 (:574, :587, :601) the
    cascade compares the LIVE estimate `t()` (:544, `estimate_tokens` over
    prefix + dynamic block + messages, :89-99) against the NEXT tier's
    threshold (5734 / 6553 / 7208 / 7782, :35-38) and returns if it is below;
    otherwise it fires the next tier — snip :568, microcompact :581,
    graph_collapse :594, auto_compact :608 — re-renders the block (:569,
    :582, :595, :609) and checks again; tier 4 fires whenever the estimate is
    still at or above 7782 after tier 3 and always returns (:623); no tier is
    ever skipped, so reaching tier k means tiers 1..k-1 all fired first, and
    because tiers 1–3 edit nothing the estimator reads (session memory, the
    previous turn's graph_context, hippocampus edges), the tier reached is a
    function of the entry estimate alone: the highest threshold it has
    crossed.

PLATFORM is stamped on every printed table (sys.platform).
"""
from __future__ import annotations

import asyncio
import statistics
import sys
from pathlib import Path

import pytest

from backend.loop import compaction as comp_mod
from backend.loop.compaction import (TIER_1_SNIP_AT, TIER_2_MICRO_AT, TIER_3_COLLAPSE_AT,
                                     TIER_4_AUTO_AT, compaction_block, estimate_tokens,
                                     estimate_tokens_text, run_cascade)
from backend.loop.state import NexusLoopState
from tests.fixtures.ce_rig import (STOP_AFTER_COLLAPSE, STOP_AFTER_MICRO, STOP_AFTER_SNIP,
                                   STOP_BELOW_T1, STOP_TIER4_RAN, CERig, stop_reason_of)

RULE = ("S1.1: at entry (compaction.py:561) and after each of tiers 1-3 (:574, :587, :601) the cascade "
        "compares the live estimate t() (:544) against the NEXT tier's threshold (5734/6553/7208/7782, "
        ":35-38) and returns if below, else fires the next tier (:568/:581/:594/:608) and checks again; "
        "tier 4 fires whenever t() >= 7782 after tier 3 and always returns (:623); no tier is skipped, "
        "and since tiers 1-3 edit nothing the estimator reads, the tier reached is the highest threshold "
        "the ENTRY estimate crossed.")

# ---------------------------------------------------------------------------
# Rig parameters. RECONSTRUCTED where marked: the original harnesses' code
# is not in the repository; the transcript holds their figures.
# ---------------------------------------------------------------------------
RUN1 = dict(pairs=42, answer_chars=800, kb_lines=17, user_chars=76, seed_session_memory=True)
#   pairs=42, answer 800 — recorded (B-28); kb_lines=17 — the 17 matched of 20 (B-28), planted as 17;
#   user_chars=76 — RECONSTRUCTED from 9962 = 89 + 265 + 42·(205 + u) → u = 24 tokens → 76-79 chars;
#   seed_session_memory — RECONSTRUCTED from snip's `removed=7` (17 edges, keep 10).
RUN2_LENGTHS = (200, 400, 800, 1600, 2000)
#   The spec names 200 / 800 / 2000; the transcript of the 28 September run names 400 / 800 / 1600
#   (a premise found different, reported); all five run. Question length RECONSTRUCTED as run 1's.
RUN2_TURNS = 40
B28_RUN1_EST, B28_RUN1_AFTER = 9962, 621          # the recorded figures, for the gap

# What the rig measured (this environment, linux; pinned as measured).
# SNIP-DET moved two figures by two tokens each (10091 -> 10089, 740 -> 738): the
# hit order is now total (id among equals) instead of newest-by-microsecond,
# so the cortex section renders different equal-activation nodes. A
# consequence of the fix, not a regression; the shape and `removed` held.
RUN1_MEASURED = {"est": 10089, "after": 738, "removed": (7, 0, 0, 81)}
RUN2_MEASURED = {   # answer_chars: (fires, first fire turn, {tier: count})
    200:  (0, None, {}),
    400:  (0, None, {}),
    800:  (10, 24, {"snip": 4, "microcompact": 3, "graph_collapse": 2, "auto_compact": 1}),
    1600: (11, 14, {"snip": 4, "microcompact": 2, "graph_collapse": 3, "auto_compact": 2}),
    2000: (14, 11, {"snip": 6, "microcompact": 3, "graph_collapse": 3, "auto_compact": 2}),
}
S14_LENGTH = 2000      # the answer length with the most tier 1-3 fires (12; 800 has 9) — S1.4's rig
S14_MEASURED = (11, 0, 0)   # (pairs, median |diff|, pairs with a content difference), as measured; 12 tier 1-3
                            # fires at 2000, one on turn 40 with no N+1 to measure


# ---------------------------------------------------------------------------
# shared rigs (module scope: the rows are deterministic, C5)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def ce_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("ce")


@pytest.fixture(scope="module")
def run2(ce_root) -> dict:
    out = {}
    for L in RUN2_LENGTHS:
        rig = CERig(ce_root / f"run2_{L}", answer_chars=L, kb_lines=17, user_chars=76)
        out[L] = rig.run(RUN2_TURNS)
        rig.close()
    return out


def _fires(rows):
    return [r for r in rows if r.fired]


def _dist(rows) -> dict:
    d: dict = {}
    for r in _fires(rows):
        d[r.tier_reached] = d.get(r.tier_reached, 0) + 1
    return d


def _check_distribution(rows, expected) -> None:
    fires, first, dist = expected
    got = (len(_fires(rows)), _fires(rows)[0].turn if _fires(rows) else None, _dist(rows))
    assert got == (fires, first, dist), f"measured {got}, pinned {expected}"


# ---------------------------------------------------------------------------
# C1 — the stopping rule
# ---------------------------------------------------------------------------
def _state_at(tokens: int) -> NexusLoopState:
    """One assistant message sized so the estimate (prefix '', block '') is exactly `tokens`."""
    # estimate_tokens_text = len//4 + 1 (so the empty prefix and block cost 1 each); +4 per message
    chars = (tokens - 1 - 1 - 4 - 1) * 4
    return NexusLoopState(messages=({"role": "assistant", "content": "x" * chars},))


@pytest.mark.parametrize("tokens, reached, reason", [
    (TIER_1_SNIP_AT - 1, None, STOP_BELOW_T1),
    (TIER_1_SNIP_AT, "snip", STOP_AFTER_SNIP),
    (TIER_2_MICRO_AT - 1, "snip", STOP_AFTER_SNIP),
    (TIER_2_MICRO_AT, "microcompact", STOP_AFTER_MICRO),
    (TIER_3_COLLAPSE_AT - 1, "microcompact", STOP_AFTER_MICRO),
    (TIER_3_COLLAPSE_AT, "graph_collapse", STOP_AFTER_COLLAPSE),
    (TIER_4_AUTO_AT - 1, "graph_collapse", STOP_AFTER_COLLAPSE),
    (TIER_4_AUTO_AT, "auto_compact", STOP_TIER4_RAN),
    (TIER_4_AUTO_AT + 2000, "auto_compact", STOP_TIER4_RAN),
])
def test_stopping_rule_as_stated(tokens, reached, reason):
    """The cascade's stopping rule is the one S1.1 states, with line numbers; this test fails if
    the code changes it. Rig: `run_cascade` directly on one synthetic message sized to the
    estimate named, prefix and block empty, no hippocampus."""
    state = _state_at(tokens)
    assert estimate_tokens(state) == tokens
    res = asyncio.run(run_cascade(state))
    got_reached, got_reason = stop_reason_of(res)
    print(f"\n{RULE if tokens == TIER_1_SNIP_AT - 1 else ''}\nC1 ({sys.platform}) est={tokens}: "
          f"reached={got_reached} stop={got_reason} steps={[(e.tier.value, e.tokens_before, e.tokens_after) for e in res.events]}")
    assert (got_reached, got_reason) == (reached, reason)
    # The consequence, not the flag: tiers 1-3 leave the estimate where it was; tier 4 drops it
    # below tier 1's threshold.
    for e in res.events:
        if e.tier.value != "auto_compact":
            assert e.tokens_after == e.tokens_before == tokens
        else:
            assert e.tokens_after < TIER_1_SNIP_AT


# ---------------------------------------------------------------------------
# C2 — run 1
# ---------------------------------------------------------------------------
def test_run1_escalates_from_above_tier4(ce_root):
    """From a start above the tier-4 threshold, tiers 1–3 do not move the estimator, and tier 4
    fires. Rig: RUN1 (42 pairs × 800-char answers, 17 KB lines, 76-char question, seeded session
    memory). Shape is the pin; the absolute figure is reported with its gap from B-28's 9962."""
    rig = CERig(ce_root / "run1", **RUN1)
    row = rig.run_one()
    rig.close()
    gap = row.est_before - B28_RUN1_EST
    print(f"\nC2 ({sys.platform}) run 1: {row.line()}\n   gap from B-28: entry {gap:+d} "
          f"({gap / B28_RUN1_EST:+.1%}), after tier 4 {row.final_tokens - B28_RUN1_AFTER:+d}; "
          f"params {rig.params}")
    tiers = [s.tier for s in row.steps]
    assert tiers == ["snip", "microcompact", "graph_collapse", "auto_compact"]
    assert row.est_before >= TIER_4_AUTO_AT, "the start is above tier 4's threshold"
    for s in row.steps[:3]:
        assert s.after == s.before == row.est_before, f"{s.tier} moved the estimator"
    assert row.steps[3].after < TIER_1_SNIP_AT and row.stop_reason == STOP_TIER4_RAN
    # Pinned as measured on this rig (not as a reproduction of 9962).
    assert (row.est_before, row.final_tokens) == (RUN1_MEASURED["est"], RUN1_MEASURED["after"])
    assert tuple(s.removed for s in row.steps) == RUN1_MEASURED["removed"]


# ---------------------------------------------------------------------------
# C3 — run 2, and its companion
# ---------------------------------------------------------------------------
def _prediction(rig: CERig, base: int) -> dict:
    """The hypothesis's numeric prediction from the estimator alone, BEFORE any fire is read:
    `base` is turn 1's entry estimate (prefix + block, no history) and growth is what one
    user/assistant pair adds by the estimator's formula (len//4 + 1 + 4 per message), both
    known from the rig's fixed text. Tiers 1-3 free nothing (B-28), so once the growing
    estimate crosses 5734 every turn fires until it crosses 7782 and tier 4 resets it."""
    growth = (estimate_tokens_text(rig.question(1)) + 4) + (estimate_tokens_text(rig.answer(1)) + 4)
    # The predicted trajectory, turn by turn: the entry estimate is base + the history's
    # messages; a tier-4 fire replaces the history with the stub summary and the last pair
    # (compaction.py:317-324), and stage 7 then appends this turn's pair.
    history_tokens, n_pairs, traj, tiers = 0, 0, [], {}
    for n in range(1, RUN2_TURNS + 1):
        est = base + history_tokens
        tier = ("auto_compact" if est >= TIER_4_AUTO_AT else "graph_collapse" if est >= TIER_3_COLLAPSE_AT
                else "microcompact" if est >= TIER_2_MICRO_AT else "snip" if est >= TIER_1_SNIP_AT else None)
        traj.append((n, est, tier))
        if tier:
            tiers[tier] = tiers.get(tier, 0) + 1
        if tier == "auto_compact":
            summary = estimate_tokens_text(compaction_block(
                f"[Auto-summary stub: {n_pairs} user / {n_pairs} assistant turns compacted.]")) + 4
            history_tokens, n_pairs = summary + growth, 1     # the summary and the last pair
        history_tokens += growth
        n_pairs += 1
    fires = [n for n, _e, t in traj if t]
    return {"base": base, "growth": growth, "first_fire": fires[0] if fires else None,
            "tier4_at": [n for n, _e, t in traj if t == "auto_compact"], "fires": len(fires),
            "dist": tiers, "trajectory": traj}


def test_run2_gradual_growth_tier_distribution(ce_root, run2):
    """A gradually growing context stops at the tier whose threshold it crosses. Rig: RUN2 — 40
    turns from an empty history, 17 KB lines, 76-char question, at each answer length; the
    hypothesis's prediction is printed from the estimator before the trajectory it is compared
    against."""
    for L, rows in run2.items():
        probe = CERig(ce_root / f"pred_{L}", answer_chars=L, kb_lines=17, user_chars=76)
        pred = _prediction(probe, rows[0].est_before)
        probe.close()
        shown = {k: v for k, v in pred.items() if k != "trajectory"}
        print(f"\nC3 ({sys.platform}) answer={L}: PREDICTED (before reading a fire) {shown}; "
              f"MEASURED fires={len(_fires(rows))} first={_fires(rows)[0].turn if _fires(rows) else None} dist={_dist(rows)}")
        if L == 800:
            # The whole trajectory, fire or not, with the prediction beside each turn and, for a
            # turn after the first crossing that did not fire, the reason from S1.1's rule.
            first = pred["first_fire"]
            for r, (_n, p_est, p_tier) in zip(rows, pred["trajectory"]):
                why = ""
                if first and r.turn > first and not r.fired:
                    last_t4 = max(t for t in pred["tier4_at"] if t < r.turn)
                    why = (f"  <- no fire: entry {r.est_before} < {TIER_1_SNIP_AT} after the tier-4 reset at "
                           f"turn {last_t4}; no cooldown exists in the rule, nothing else moved the estimate")
                print(f"   {r.line()}  | predicted est {p_est} tier {p_tier or '-'}{why}")
        _check_distribution(rows, RUN2_MEASURED[L])
        # The prediction, compared: the same fire turns and the same tier per fire. Entry
        # estimates may differ by the block's token-level drift (the cortex section re-renders
        # each turn); the fire turns and tiers may not.
        measured = [(r.turn, r.tier_reached) for r in rows if r.fired]
        predicted = [(n, t) for n, _e, t in pred["trajectory"] if t]
        assert measured == predicted, f"answer={L}: measured {measured} != predicted {predicted}"


def test_c3_fails_on_forced_escalation(ce_root, monkeypatch):
    """Companion: if every fire escalated to tier 4, C3 would fail. Planted: tiers 2–4's
    thresholds lowered to tier 1's in `backend.loop.compaction` (monkeypatched), so the first
    crossing runs every tier; the pinned 800-character distribution then fails by name."""
    for name in ("TIER_2_MICRO_AT", "TIER_3_COLLAPSE_AT", "TIER_4_AUTO_AT"):
        monkeypatch.setattr(comp_mod, name, TIER_1_SNIP_AT)
    rig = CERig(ce_root / "forced", answer_chars=800, kb_lines=17, user_chars=76)
    rows = rig.run(26)
    rig.close()
    print(f"\nC3c ({sys.platform}) forced: {_dist(rows)}")
    # Non-vacuity: every fire escalated (one fire: the first crossing runs to tier 4 and resets).
    assert set(_dist(rows)) == {"auto_compact"} and _dist(rows)["auto_compact"] >= 1
    with pytest.raises(AssertionError):
        _check_distribution(rows, RUN2_MEASURED[800])


# ---------------------------------------------------------------------------
# C4 — the next-turn question (S1.4, amended)
# ---------------------------------------------------------------------------
def test_next_turn_effect_measured(ce_root, run2):
    """Whether tiers 1–3 reduce the next turn's estimate is measured on this rig, both arms, and
    the median difference is pinned. Rig: RUN2 at 2000 characters, the answer length with the most
    tier 1–3 fires (12; 800 has 9). DECISION RULE, STATED FIRST: if the median |B − A| on
    turn N+1's entry estimate is under 1% of A's estimate, tiers 1–3 do not reduce the next
    turn's context on this rig. Arm B replays the same deterministic run with tiers 1–3 replaced
    by no-ops at turn N only; its tier reached at N must equal A's (identical control flow), and
    the injected sets at N+1 are compared beside the estimate."""
    A = run2[S14_LENGTH]
    pairs = [r.turn for r in A if r.fired and r.tier_reached != "auto_compact" and r.turn < RUN2_TURNS]
    last_turn_fire = [r.turn for r in A if r.fired and r.tier_reached != "auto_compact" and r.turn == RUN2_TURNS]
    assert pairs, "non-vacuity: run 2 has turns that stopped at tiers 1-3"
    table, diffs, content_diff_pairs, confounded = [], [], 0, []
    for N in pairs:
        rig = CERig(ce_root / f"armB_{N}", answer_chars=S14_LENGTH, kb_lines=17, user_chars=76)
        B = rig.run(N + 1, noop_at=N)
        rig.close()
        aN, bN, a1, b1 = A[N - 1], B[N - 1], A[N], B[N]
        assert bN.turn == N and b1.turn == N + 1
        assert all(s.removed == 0 for s in bN.steps), "arm B's tiers were not no-ops"
        if aN.tier_reached != bN.tier_reached:
            confounded.append(N)
        only_a = sorted(set(a1.injected_ids) - set(b1.injected_ids))
        only_b = sorted(set(b1.injected_ids) - set(a1.injected_ids))
        if only_a or only_b:
            content_diff_pairs += 1
        diff = b1.est_before - a1.est_before
        diffs.append(diff)
        table.append(f"   N={N:>2} tier@N A={aN.tier_reached:<14} B={bN.tier_reached:<14} "
                     f"N+1 est A={a1.est_before:>5} B={b1.est_before:>5} diff={diff:+d} "
                     f"injected only-A={only_a} only-B={only_b}")
    median = statistics.median(abs(d) for d in diffs)
    pct = median / statistics.median(A[N].est_before for N in pairs)
    print(f"\nC4 ({sys.platform}) rig RUN2@{S14_LENGTH}: {len(pairs)} pairs\n" + "\n".join(table) +
          f"\n   median |diff| = {median} ({pct:.2%} of the estimate), range {min(diffs)}..{max(diffs)}, "
          f"pairs with a content difference: {content_diff_pairs}, confounded pairs: {confounded}, "
          f"tier 1-3 fires on the last turn (no N+1 to measure): {last_turn_fire}")
    assert not confounded, f"arm B reached a different tier at N: {confounded}"
    assert pct < 0.01, (f"tiers 1-3 DO reduce the next turn's context on rig RUN2@{S14_LENGTH}: "
                        f"median {median}")
    # Pinned as measured.
    assert (len(pairs), median, content_diff_pairs) == S14_MEASURED, (
        f"rig RUN2@{S14_LENGTH} (40 turns, 17 KB lines, 76-char question): "
        f"count={len(pairs)} median={median} content_diff_pairs={content_diff_pairs}")


# ---------------------------------------------------------------------------
# C5 — determinism
# ---------------------------------------------------------------------------
def test_ce_rig_is_deterministic(ce_root):
    """The rig gives the same numbers twice, or none of the above pins mean anything. On the basis
    the fixture's docstring states — the stub's answers and the question are fixed in length and
    content per turn — two runs with the same parameters produce identical rows."""
    rows = []
    for i in (1, 2):
        rig = CERig(ce_root / f"det_{i}", pairs=20, answer_chars=800, kb_lines=17, user_chars=76)
        rows.append([r.as_tuple() for r in rig.run(12)])
        rig.close()
    assert any(t[2] for t in rows[0]), "non-vacuity: the run fired"
    assert rows[0] == rows[1]


# ---------------------------------------------------------------------------
# S1.3 — execution order, printed once; S1.5 — the successor's fields
# ---------------------------------------------------------------------------
# The loop's own labels, read from the comments at the lines named; printed
# beside the measured order so the two numberings reconcile on the order.
LOOP_LABELS = {      # keyed by the production call site
    "backend.loop.agentic_loop:469": "retrieval — loop 'Stage 2 (preview)' :467",
    "backend.loop.agentic_loop:222": "retrieval, inside assemble_context",
    "backend.loop.agentic_loop:695": "the cascade — loop 'Stage 1' :686 — the architecture document's 'stage 5'",
    "backend.loop.compaction:568": "tier 1", "backend.loop.compaction:581": "tier 2",
    "backend.loop.compaction:594": "tier 3", "backend.loop.compaction:608": "tier 4",
    "backend.loop.agentic_loop:691": "the block re-rendered inside the cascade (rebuild_block_fn)",
    "backend.loop.agentic_loop:721": "the block re-rendered after tier 4",
    "backend.loop.agentic_loop:737": "prompt assembly :729-740",
    "backend.loop.agentic_loop:770": "the model call — loop 'Stage 3' :761",
    "backend.loop.agentic_loop:807": "extraction — loop 'Stage 4' :790",
    "backend.cortex.extraction:211": "extraction's own model call",
    "backend.loop.agentic_loop:951": "the lineage write — loop 'Stage 6.5' :930",
    "backend.loop.agentic_loop:1116": "Hebbian — loop 'Stage 5' :1114",
    "backend.loop.agentic_loop:1149": "graph_context replacement — loop 'Stage 7' :1144",
    "backend.loop.agentic_loop:1200": "reinforcement :1176",
    "backend.loop.agentic_loop:1215": "the done frame :1208",
}

CAPTURE_FIELDS = [
    "turn number and lineage_id of the turn (the cascade runs before the record exists: carry forward)",
    "estimate at entry, and the four thresholds in force",
    "per tier attempted: tier, estimate before, estimate after, items removed, integrity pass_fail",
    "tier reached and the stop reason (S1.1's condition that ended it, by name)",
    "what tier 4 removed: messages dropped (count), summary length, work_unit_ids_summarised",
    "what micro removed: the concept ids (FC-0's `removed`), count; snip: session-memory rows dropped (count)",
    "the dynamic block's token count before and after the cascade, and the KB-line count in it",
    "the next turn's entry estimate (for the next-turn question: needs turn N+1 joined to turn N)",
    "the next turn's injected belief ids (the content-difference column: S1.4 needed the lineage record for it)",
    "session-memory and graph_context sizes at entry and after stage 7's rebuild (both are overwritten each turn)",
]


def test_execution_order_printed_once(ce_root):
    """S1.3: one traced turn on the run-1 rig (so every tier appears), every stage in execution
    order with function and line, and the two stage numberings printed beside it."""
    rig = CERig(ce_root / "trace", **RUN1)
    events = rig.trace_turn()
    rig.close()
    order = [e["func"] for e in events]
    print(f"\nS1.3 ({sys.platform}) execution order, one turn on rig RUN1:")
    for i, e in enumerate(events, 1):
        print(f"   {i:>2}. {e['func']} (defined :{e['line']}) called from {e['called_from']}  "
              f"— {LOOP_LABELS.get(e['called_from'], '')}")
    print("   Reconciliation: compaction runs SECOND, after the retrieval preview and before the model call; "
          "the loop's comment calls it 'Stage 1' (:686) and the architecture document 'stage 5'; the loop's "
          "'Stage 5' label is the Hebbian update, which runs after the lineage write. The order is the fact.")
    print("S1.5 fields a durable fire capture must record:")
    for f in CAPTURE_FIELDS:
        print(f"   - {f}")
    first = lambda name: next(i for i, f in enumerate(order) if f == name)   # noqa: E731
    assert first("backend.loop.agentic_loop.assemble_context") < first("backend.loop.compaction.run_cascade")
    assert first("backend.loop.compaction.run_cascade") < first("backend.loop.compaction.assert_single_system_message")
    assert first("backend.loop.compaction.assert_single_system_message") < first("tests.fixtures.ce_rig.stream_chat")
    assert first("tests.fixtures.ce_rig.stream_chat") < first("backend.cortex.extraction.extract_triples")
    assert first("backend.cortex.extraction.extract_triples") < first("backend.memory.lineage.record_turn")
    assert first("backend.memory.lineage.record_turn") < first("backend.cortex.hebbian.update")
    assert first("backend.cortex.hebbian.update") < first("backend.loop.state.next_state")
    assert first("backend.loop.state.next_state") < first("backend.loop.agentic_loop.sse_event")
    assert order[-1] == "backend.loop.agentic_loop.sse_event"
    # Every tier ran inside the cascade on this rig, between the cascade's entry and prompt assembly.
    for tier in ("backend.loop.compaction.snip", "backend.loop.compaction.microcompact",
                 "backend.loop.compaction.graph_collapse", "backend.loop.compaction.auto_compact"):
        assert first("backend.loop.compaction.run_cascade") < first(tier) < first("backend.loop.compaction.assert_single_system_message")
