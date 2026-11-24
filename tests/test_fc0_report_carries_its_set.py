"""FC-0 — the compaction report carries its set.

`concept_completely_removed` computed `removed_concepts` as a set and wrote
it only as a detail string sliced to five names, with no count. Six planted
orphans read as five on the stream and `orphan_f` existed nowhere. The set
is now structural on the violation (`removed`, `removed_count`) and mirrored
at the top of the report payload on every fire, `[]`/`0` on PASS, derived
from the one stored copy at render.

TESTS
  - six planted orphans: `removed` lists six, `removed_count == 6`, the
    rendered `detail` still shows five — display truncation and record are
    pinned as different things;
  - PASS: `removed == []` and `removed_count == 0` are PRESENT on the
    payload (an empty set that declares itself, never an omitted field);
  - other rules carry no `removed` (a `[]` there would be a false blank);
  - the payload note in docs/CONTRACTS.md names exactly the keys the emitter sends.

GUARD AND COMPANION
  `assert_report_carries_its_set` holds count == len(removed) == the size
  of the set the rule was given, and set equality. The companion plants a
  rule that builds `removed` by parsing the rendered detail (five names)
  while counting the real set (six) — the truncation returning by the back
  door — and asserts the check fails naming the mismatch. A second companion
  plants a rule that builds BOTH from the string, so count and length agree
  at five, and asserts the check still fails against the set it was given.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path

import pytest

import backend.loop.compaction as comp
from backend.kb.retriever import TripleRetriever
from backend.loop.agentic_loop import run_turn
from backend.loop.compaction import (CompactionReport, IntegrityViolation, REMOVED_INVARIANT,
                                     Tier, compute_integrity_report)
from backend.loop.prefix import STABLE_SYSTEM_PREFIX
from backend.loop.state import NexusLoopState
from tests.test_bd3_halt_retired import _Provider, _rig

ROOT = Path(__file__).resolve().parent.parent
ORPHANS = (("orphan_a", "rel", "orphan_b"), ("orphan_c", "rel", "orphan_d"), ("orphan_e", "rel", "orphan_f"))
ORPHAN_IDS = {c for t in ORPHANS for c in (t[0], t[2])}


def assert_report_carries_its_set(payload: dict, expected: set) -> None:
    """The check, on the ONE stored copy: the violation's `removed` is the
    whole set the rule computed and its `removed_count` is that list's
    length; the report's top-level pair mirrors it. Used by the tests and by
    the companions. (The top level derives its count from its list, so a
    slice can only show on the violation — which is why this reads it.)"""
    stored = [v for v in payload["violations"] if v["invariant"] == REMOVED_INVARIANT]
    if stored:
        (v,) = stored
        removed, count = v["removed"], v["removed_count"]
        assert count == len(removed), f"removed_count={count} but len(removed)={len(removed)}: a slice was recorded"
        assert payload["removed"] == list(removed) and payload["removed_count"] == count, "top level must mirror the violation"
    else:
        removed, count = payload["removed"], payload["removed_count"]
        assert removed == [] and count == 0, "no violation, so the declared set must be empty"
    assert count == len(expected), f"removed_count={count} but the rule was given {len(expected)} concepts"
    assert set(removed) == set(expected), f"removed={sorted(removed)} != {sorted(expected)}"


def _snapshots(pre_triples, post_triples):
    pre = comp.snapshot_for_integrity(NexusLoopState(graph_context=tuple(pre_triples)), None)
    post = comp.snapshot_for_integrity(NexusLoopState(graph_context=tuple(post_triples)), None)
    return pre, post


# ---------------------------------------------------------------------------
# acceptance 1 — six orphans, on the live turn
# ---------------------------------------------------------------------------

def test_six_planted_orphans_are_all_on_the_record_and_five_on_the_display(tmp_path, monkeypatch):
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    answer = ("Interval training improves several markers of fitness over weeks. " * 12)[:800]
    msgs = []
    for i in range(32):
        msgs += [{"role": "user", "content": f"question {i} about training and recovery"},
                 {"role": "assistant", "content": answer}]
    state = NexusLoopState(messages=tuple(msgs), graph_context=ORPHANS, turn_count=32)
    assert comp.estimate_tokens(state) > comp.TIER_2_MICRO_AT, "the fixture must reach tier 2"
    frames = []

    async def drive():
        async for f, _s in run_turn(state, "what does training improve?", retriever=TripleRetriever(store),
                                    hippocampus=hippo, provider=_Provider(), lineage=lineage):
            frames.append(f)
    asyncio.run(drive())
    fired = [json.loads(f.split("data: ", 1)[1].strip())["tier"]
             for f in frames if f.startswith("event: compaction_fired\n")]
    reports = [json.loads(f.split("data: ", 1)[1].strip())
               for f in frames if f.startswith("event: compaction_report\n")]
    micro = reports[fired.index("microcompact")]
    print("\nFC-0 micro payload:", json.dumps(micro, sort_keys=True))
    assert_report_carries_its_set(micro, ORPHAN_IDS)
    assert micro["removed"] == sorted(ORPHAN_IDS) and micro["removed_count"] == 6
    v = next(v for v in micro["violations"] if v["invariant"] == REMOVED_INVARIANT)
    assert v["removed"] == sorted(ORPHAN_IDS) and v["removed_count"] == 6
    shown = re.findall(r"'([a-z_]+)'", v["detail"])
    assert len(shown) == 5 and "orphan_f" not in shown, "the display slice is unchanged: five names"
    assert "orphan_f" in v["removed"], "and the record has the sixth"
    # The other rules carry no set.
    for other in micro["violations"]:
        if other["invariant"] != REMOVED_INVARIANT:
            assert "removed" not in other and "removed_count" not in other
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# the PASS case — present, not absent
# ---------------------------------------------------------------------------

def test_a_pass_report_declares_an_empty_set_rather_than_omitting_it():
    pre, post = _snapshots([("a", "r", "b")], [("a", "r", "b")])
    report = compute_integrity_report(pre, post, tier=Tier.MICRO, prefix_pre=STABLE_SYSTEM_PREFIX,
                                      prefix_post=STABLE_SYSTEM_PREFIX)
    payload = report.to_dict()
    assert payload["pass_fail"] == "PASS" and payload["violations"] == []
    assert "removed" in payload and "removed_count" in payload
    assert payload["removed"] == [] and payload["removed_count"] == 0
    assert_report_carries_its_set(payload, set())


def test_a_prefix_drift_violation_carries_no_set():
    pre, post = _snapshots([("a", "r", "b")], [("a", "r", "b")])
    report = compute_integrity_report(pre, post, tier=Tier.MICRO, prefix_pre=STABLE_SYSTEM_PREFIX,
                                      prefix_post="something else")
    (v,) = report.violations
    assert v.invariant == "prefix_drift" and v.removed is None and v.removed_count is None
    assert "removed" not in v.to_dict()
    assert report.to_dict()["removed"] == [] and report.to_dict()["removed_count"] == 0


# ---------------------------------------------------------------------------
# companions — the truncation returning by the back door
# ---------------------------------------------------------------------------

def _planted_rule(pre, post, *, list_from_string: bool, count_from_string: bool) -> dict:
    """A fixture rule that derives its structural fields from the rendered
    detail instead of the set — the shape FC-0 exists to forbid."""
    removed_concepts = pre["concepts"] - post["concepts"]
    detail = f"concepts vanished: {sorted(removed_concepts)[:5]}"
    parsed = re.findall(r"'([a-z_]+)'", detail)
    v = IntegrityViolation(
        invariant=REMOVED_INVARIANT, severity="WARN", detail=detail,
        removed=tuple(parsed) if list_from_string else tuple(sorted(removed_concepts)),
        removed_count=len(parsed) if count_from_string else len(removed_concepts))
    return CompactionReport(pass_fail="WARN", violations=(v,), retention_rate=0.0,
                            minority_loss_rate=1.0, structural_summary={}).to_dict()


def test_companion_a_list_built_from_the_rendered_string_fails_count_equals_length():
    pre, post = _snapshots(ORPHANS, [])
    payload = _planted_rule(pre, post, list_from_string=True, count_from_string=False)
    (v,) = payload["violations"]
    assert v["removed_count"] == 6 and len(v["removed"]) == 5, "the planted mismatch, on the stored copy"
    with pytest.raises(AssertionError) as e:
        assert_report_carries_its_set(payload, ORPHAN_IDS)
    assert "a slice was recorded" in str(e.value), str(e.value)


def test_companion_both_fields_built_from_the_string_agree_with_each_other_and_still_fail():
    pre, post = _snapshots(ORPHANS, [])
    payload = _planted_rule(pre, post, list_from_string=True, count_from_string=True)
    (v,) = payload["violations"]
    assert v["removed_count"] == len(v["removed"]) == 5, "self-consistent, and wrong"
    with pytest.raises(AssertionError) as e:
        assert_report_carries_its_set(payload, ORPHAN_IDS)
    assert "was given 6 concepts" in str(e.value), str(e.value)


def test_the_real_rule_passes_the_same_check():
    pre, post = _snapshots(ORPHANS, [])
    report = compute_integrity_report(pre, post, tier=Tier.MICRO, prefix_pre=STABLE_SYSTEM_PREFIX,
                                      prefix_post=STABLE_SYSTEM_PREFIX)
    assert_report_carries_its_set(report.to_dict(), ORPHAN_IDS)


# ---------------------------------------------------------------------------
# B.2 — the payload note names exactly what the emitter sends
# ---------------------------------------------------------------------------

def _noted_keys(event: str) -> set[str]:
    block = (ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8").split("## SSE Events")[1].split("```")[1]
    text = block[block.index(event):]
    inner = text[text.index("{") + 1:text.index("}")]
    return {k.strip() for k in inner.replace("\n", " ").split(",") if k.strip()}


def test_the_compaction_report_note_matches_the_emitted_keys():
    pre, post = _snapshots(ORPHANS, [])
    report = compute_integrity_report(pre, post, tier=Tier.MICRO, prefix_pre=STABLE_SYSTEM_PREFIX,
                                      prefix_post=STABLE_SYSTEM_PREFIX)
    assert set(report.to_dict()) == _noted_keys("compaction_report")
    # And the derivation is not vacuous: the note has the two new keys.
    assert {"removed", "removed_count"} <= _noted_keys("compaction_report")


def test_the_note_reader_is_not_a_text_search_over_the_emitter():
    """The emitter side is the dataclass's own `to_dict`, executed — the
    note is the only thing parsed as text, and it is the constitution."""
    src = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(src):
        if isinstance(node, ast.FunctionDef) and node.name == "test_the_compaction_report_note_matches_the_emitted_keys":
            calls = {getattr(c.func, "attr", getattr(c.func, "id", None)) for c in ast.walk(node)
                     if isinstance(c, ast.Call)}
            assert "to_dict" in calls and "read_text" not in calls
