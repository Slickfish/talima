"""BA fix 3 — the cascade's prefix check compared a name with itself.

THE FINDING (audit A3), a defect found in the record: `run_cascade` built
its integrity report with `prefix_pre=prefix, prefix_post=prefix`
(compaction.py:498-500), so the report's "prefix bit-identical" rule had
never been able to fail and had never been evidence. The runtime check at
assembly (`assert_stable_prefix`) was the only real guard.

THE FIX: the constant lives in backend/loop/prefix.py; the cascade
compares the ASSEMBLED system message (its prefix argument plus the
post-tier block) against the constant through `prefix_intact`, the same
predicate `assert_stable_prefix` raises on. One comparison, one source of
truth.

THE GUARD, AST: no `Compare` in `compute_integrity_report` or `run_cascade`
has structurally identical operands, and the prefix rule calls
`prefix_intact`. Its must-fail feeds the checker the old shape. The
behavioural must-fail: a cascade invoked with one byte of the prefix
altered — before the fix every report PASSED; after it every tier reports
`prefix_drift` at WARN and the cascade runs on (BD-3 retired the FAIL
tier and the halt, so the consequence asserted is the WARN line and the
completed cascade, not a flag).
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

import backend.loop.compaction as comp
from backend.loop.agentic_loop import STABLE_SYSTEM_PREFIX as LOOP_PREFIX
from backend.loop.compaction import (
    STABLE_SYSTEM_PREFIX, Tier, assert_stable_prefix, compute_integrity_report,
    prefix_intact, run_cascade, snapshot_for_integrity,
)
from backend.loop.state import NexusLoopState
from backend.memory.hippocampus import Hippocampus

ROOT = Path(__file__).resolve().parent.parent
COMPACTION = ROOT / "backend" / "loop" / "compaction.py"


# ---------------------------------------------------------------------------
# One constant, one predicate
# ---------------------------------------------------------------------------

def test_the_loop_and_the_cascade_read_the_same_constant():
    assert LOOP_PREFIX is STABLE_SYSTEM_PREFIX
    assert LOOP_PREFIX.startswith("You are NEXUS")


def test_the_runtime_check_and_the_rule_share_the_predicate():
    assert prefix_intact(STABLE_SYSTEM_PREFIX + "\n\nblock")
    assert not prefix_intact(STABLE_SYSTEM_PREFIX[:-1] + "!" + "\n\nblock")
    assert_stable_prefix(STABLE_SYSTEM_PREFIX + " x", STABLE_SYSTEM_PREFIX)
    with pytest.raises(RuntimeError):
        assert_stable_prefix("x" + STABLE_SYSTEM_PREFIX, STABLE_SYSTEM_PREFIX)


# ---------------------------------------------------------------------------
# The static guard, and its checker as a function for the must-fail
# ---------------------------------------------------------------------------

def self_comparisons(source: str, functions: tuple[str, ...]) -> list[tuple[str, int]]:
    """(function, line) for every Compare whose left operand is structurally
    identical to its first comparator, inside the named functions."""
    out = []
    tree = ast.parse(source)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name not in functions:
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Compare) and node.comparators \
                    and ast.dump(node.left) == ast.dump(node.comparators[0]):
                out.append((fn.name, node.lineno))
    return out


def keyword_pairs_with_same_value(source: str, function: str, a: str, b: str) -> list[int]:
    """Lines of calls inside `function` whose keywords `a` and `b` carry
    structurally identical values — the vacuous shape `prefix_pre=prefix,
    prefix_post=prefix`."""
    out = []
    tree = ast.parse(source)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name != function:
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                kws = {kw.arg: kw.value for kw in node.keywords}
                if a in kws and b in kws and ast.dump(kws[a]) == ast.dump(kws[b]):
                    out.append(node.lineno)
    return out


def test_no_rule_compares_a_name_with_itself():
    src = COMPACTION.read_text(encoding="utf-8")
    assert self_comparisons(src, ("compute_integrity_report", "run_cascade")) == []
    assert keyword_pairs_with_same_value(src, "run_cascade", "prefix_pre", "prefix_post") == []


def test_the_prefix_rule_calls_the_shared_predicate():
    src = COMPACTION.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "compute_integrity_report")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "prefix_intact"]
    assert len(calls) == 1, "the prefix rule must call prefix_intact exactly once"


def test_the_checker_flags_the_old_vacuous_shape():
    """Must-fail companion: the pre-fix cascade, as it was."""
    old = (
        "async def run_cascade(state, prefix, block):\n"
        "    def integrity(pre, post, *, tier):\n"
        "        return compute_integrity_report(pre, post, tier=tier,\n"
        "                                        prefix_pre=prefix, prefix_post=prefix)\n"
        "    if prefix != prefix:\n"
        "        pass\n")
    assert keyword_pairs_with_same_value(old, "run_cascade", "prefix_pre", "prefix_post") == [3]
    assert self_comparisons(old, ("run_cascade",)) == [("run_cascade", 5)]


# ---------------------------------------------------------------------------
# The behaviour: one altered byte now fails the severe tier
# ---------------------------------------------------------------------------

def _force_all_tiers(monkeypatch):
    for name in ("TIER_1_SNIP_AT", "TIER_2_MICRO_AT", "TIER_3_COLLAPSE_AT", "TIER_4_AUTO_AT"):
        monkeypatch.setattr(comp, name, 0)


def test_an_altered_prefix_is_reported_at_warn_on_every_tier_and_the_cascade_runs_on(monkeypatch):
    """The must-fail's consequence, post BD-3: the WARN line on every tier's
    report and a cascade that completed — not a flag on the state."""
    _force_all_tiers(monkeypatch)
    hippo = Hippocampus()
    state = NexusLoopState(messages=({"role": "user", "content": "x" * 400},))
    res = asyncio.run(run_cascade(state, hippo, prefix=STABLE_SYSTEM_PREFIX[:-1] + "!",
                                  dynamic_block="", rebuild_block_fn=lambda s, h: ""))
    assert [ev.tier for ev in res.events] == [Tier.SNIP, Tier.MICRO, Tier.COLLAPSE, Tier.AUTO], (
        "every tier ran; nothing halted")
    for rep in res.reports:
        assert rep.pass_fail == "WARN"
        assert any(v.invariant == "prefix_drift" and v.severity == "WARN" for v in rep.violations)
    assert res.state.compaction_state == "compacted", "the last tier's name, not a verdict"


def test_the_true_prefix_passes_every_tier(monkeypatch):
    _force_all_tiers(monkeypatch)
    state = NexusLoopState(messages=({"role": "user", "content": "x" * 400},))
    res = asyncio.run(run_cascade(state, Hippocampus(), prefix=STABLE_SYSTEM_PREFIX,
                                  dynamic_block="\n\nblock", rebuild_block_fn=lambda s, h: "\n\nblock"))
    assert not any(v.invariant == "prefix_drift" for r in res.reports for v in r.violations)


def test_the_report_rule_reads_the_assembled_message_not_a_copy_of_the_argument():
    snap = snapshot_for_integrity(NexusLoopState(), Hippocampus())
    ok = compute_integrity_report(snap, snap, tier=Tier.COLLAPSE,
                                  prefix_pre=STABLE_SYSTEM_PREFIX,
                                  prefix_post=STABLE_SYSTEM_PREFIX + "\n\nany block")
    assert not any(v.invariant == "prefix_drift" for v in ok.violations)
    bad = compute_integrity_report(snap, snap, tier=Tier.COLLAPSE,
                                   prefix_pre=STABLE_SYSTEM_PREFIX,
                                   prefix_post="prepended " + STABLE_SYSTEM_PREFIX)
    assert bad.pass_fail == "WARN"
    assert any(v.invariant == "prefix_drift" for v in bad.violations)
