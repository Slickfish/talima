"""CP — the durable fire capture (CE's named successor). The S3 pins.

THE PIN THAT MAKES "ONLY TO EMIT" A TESTED STATEMENT
    CE's rig under capture on and off gives the same tier reached, the same
    estimates and the same request — the whole message list the provider
    was handed, byte for byte — turn for turn. The capture observes; it
    must not participate.

PLANTED INPUTS, ONE PER TEST, named in each docstring. The planted
scenarios are the packet's: a tier-2 fire removing three ids; a fire
removing A and B and demoting C followed by a turn whose rebuilt block
holds A and C (C demoted) and not B; a row whose hash does not match its
record and a row with no record; the gate off; a planted `answer_text`
key — and one the guard's source does not name.

PLATFORM is stamped on every printed line (sys.platform).
"""
from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path

import pytest

from backend.eval import fire_capture as fc
from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.loop.agentic_loop import run_turn
from backend.loop.compaction import (STABLE_SYSTEM_PREFIX, TIER_2_MICRO_AT, TIER_3_COLLAPSE_AT,
                                     TIER_4_AUTO_AT, CompactionEvent, CompactionResult, Tier,
                                     estimate_tokens)
from backend.loop.state import NexusLoopState
from backend.memory.hippocampus import Hippocampus
from backend.memory.lineage import LineageStore
from backend.cortex.edges import SynapticEdge
from tests.fixtures.ce_rig import (STOP_AFTER_COLLAPSE, STOP_AFTER_MICRO, STOP_AFTER_SNIP,
                                   STOP_TIER4_RAN, CERig)

ROOT = Path(__file__).resolve().parent.parent
RARE = "zqxvtoken"          # planted into the answer, the question and a claim; must reach no row


@pytest.fixture(autouse=True)
def _fresh_pending():
    fc.reset_pending()
    yield
    fc.reset_pending()


def _on(monkeypatch, path: Path) -> None:
    monkeypatch.setenv("NEXUS_FIRE_CAPTURE", "on")
    monkeypatch.setenv("NEXUS_FIRE_CAPTURE_PATH", str(path))


class _P:
    """The stub model: a fixed answer carrying the rare token, then an empty
    extraction."""

    def __init__(self, answer: str = f"Python is a programming_language. {RARE}"):
        self.answer = answer
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        if self.calls == 1:
            for tok in self.answer.split(" "):
                yield tok + " "
        else:
            yield "[]"

    async def get_embedding(self, t): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _store(root: Path):
    store = TripleStore(root / "kb.db")
    lineage = LineageStore(root / "kb.db")
    store.add(subject="python", relation="is_a", object="programming_language",
              weight=0.8, tier=1, granularity=Granularity.TOPIC, source="human_assertion")
    store.add(subject="python", relation="is_a", object="snake_genus",
              weight=0.7, tier=1, granularity=Granularity.TOPIC, source="wikidata")
    return store, lineage


def _sized(target_tokens: int, **fields) -> NexusLoopState:
    """One planted assistant message sized so prefix + messages estimates to
    `target_tokens` (the block, unknown until retrieval, adds its own ~90)."""
    chars = (target_tokens - (len(STABLE_SYSTEM_PREFIX) // 4 + 1) - 1 - 4 - 1) * 4
    return NexusLoopState(messages=({"role": "assistant", "content": "x" * chars},), **fields)


def _turn(state, store, lineage, hippocampus, question=f"tell me about python {RARE}"):
    frames, out = [], {}

    async def drive():
        async for f, st in run_turn(state, question, retriever=TripleRetriever(store),
                                    hippocampus=hippocampus, provider=_P(), lineage=lineage):
            frames.append(f)
            out["state"] = st

    asyncio.run(drive())
    done = next(json.loads(f.split("data: ", 1)[1].strip()) for f in reversed(frames)
                if f.startswith("event: done\n"))
    return done, out["state"]


# ---------------------------------------------------------------------------
# 1. The cascade is unchanged with the capture on
# ---------------------------------------------------------------------------

def test_cascade_unchanged_with_capture_on(tmp_path, monkeypatch):
    """CE's rig (2000-character answers, 17 KB lines, 76-character question,
    16 turns: fires at 11–15, every tier) under capture off and on: the same
    tier reached, the same estimates (`TurnRow.as_tuple`) and the same request
    — every message list the provider was handed, byte for byte — turn for
    turn. Non-vacuity: the on arm wrote fire rows at tiers 1–3 and a resolved
    next-turn check; the off arm wrote no file."""
    arms, requests = {}, {}
    for mode in ("off", "on"):
        monkeypatch.setenv("NEXUS_FIRE_CAPTURE", mode)
        monkeypatch.setenv("NEXUS_FIRE_CAPTURE_PATH", str(tmp_path / mode / "fire.jsonl"))
        fc.reset_pending()
        rig = CERig(tmp_path / f"rig_{mode}", answer_chars=2000, kb_lines=17, user_chars=76)
        seen: list[str] = []
        orig = rig.provider.stream_chat

        async def rec(model, messages, num_ctx=8192, _o=orig, _seen=seen):
            _seen.append(json.dumps(messages, sort_keys=True))
            async for c in _o(model, messages, num_ctx=num_ctx):
                yield c
        rig.provider.stream_chat = rec
        rows = rig.run(16)
        rig.close()
        arms[mode] = [r.as_tuple() for r in rows]
        requests[mode] = seen
        print(f"\nCP-1 ({sys.platform}) capture={mode}: fires "
              f"{[(r.turn, r.tier_reached) for r in rows if r.fired]}, requests {len(seen)}")
    assert arms["on"] == arms["off"], "the capture moved a tier or an estimate"
    assert requests["on"] == requests["off"], "the capture changed what the model was sent"
    assert len(requests["on"]) == 32            # 16 turns × (answer + extraction)
    assert any(t[2] for t in arms["on"]), "non-vacuity: the rig fired"
    assert not (tmp_path / "off" / "fire.jsonl").exists()
    cap = fc.read_rows(tmp_path / "on" / "fire.jsonl")
    fires = [r for r in cap if r["kind"] == "fire"]
    assert {r["tier_reached"] for r in fires} >= {"snip", "microcompact", "graph_collapse"}
    checks = [r for r in cap if r.get("check")]
    assert checks, "no next-turn check was resolved"
    by_turn = {r["turn"]: r for r in fires}
    for r in checks:
        prev = by_turn[r["check"]["of_turn"]]
        assert r["turn"] == prev["turn"] + 1
        assert r["check"]["of_lineage_id"] == prev["lineage_id"]
        assert sum(r["check"]["counts"].values()) == prev["removed_total"]
    print(f"CP-1 ({sys.platform}) tally: {json.dumps(fc.tally(cap)['b49'])}")


def test_stop_reasons_agree_with_ce_rig():
    """The capture's S1.1 names are CE's, so the series reads across the rig
    and the instance."""
    assert fc.STOP_REASONS == {"snip": STOP_AFTER_SNIP, "microcompact": STOP_AFTER_MICRO,
                               "graph_collapse": STOP_AFTER_COLLAPSE, "auto_compact": STOP_TIER4_RAN}


# ---------------------------------------------------------------------------
# 2. The row shape
# ---------------------------------------------------------------------------

def test_row_shape_planted_tier2_fire(tmp_path, monkeypatch):
    """PLANTED: a history sized to enter between tier 2's and tier 3's
    thresholds, and three orphan rows in `graph_context` (no hippocampus edge
    behind them, so micro drops all three). One row: those three ids, the
    estimate before and after each tier, the stop reason by name, bound to
    the record by `record_hash`; and the rare token planted in the answer and
    the question reaches no value of the row."""
    cap = tmp_path / "fire.jsonl"
    _on(monkeypatch, cap)
    store, lineage = _store(tmp_path)
    state = _sized(TIER_2_MICRO_AT + 150, graph_context=(
        ("orphan1", "r", "o1"), ("orphan2", "r", "o2"), ("orphan3", "r", "o3")))
    done, _ = _turn(state, store, lineage, Hippocampus())
    rows = fc.read_rows(cap)
    assert len(rows) == 1
    row = rows[0]
    print(f"\nCP-2 ({sys.platform}) planted tier-2 fire: entry {row['entry_estimate']} "
          f"reached {row['tier_reached']} stop {row['stop_reason']} "
          f"tiers {[(t['tier'], t['before'], t['after'], t['removed_count']) for t in row['tiers']]}")
    assert TIER_2_MICRO_AT <= row["entry_estimate"] < TIER_3_COLLAPSE_AT, "the plant missed its range"
    assert row["kind"] == "fire" and row["turn"] == 1
    assert row["tier_reached"] == "microcompact" and row["stop_reason"] == STOP_AFTER_MICRO
    assert [t["tier"] for t in row["tiers"]] == ["snip", "microcompact"]
    micro = row["tiers"][1]
    assert micro["removed_ids"] == [fc.triple_id("orphan1", "r", "o1"),
                                    fc.triple_id("orphan2", "r", "o2"),
                                    fc.triple_id("orphan3", "r", "o3")]
    assert micro["removed_count"] == micro["items_removed"] == 3 and micro["integrity"] == "WARN"
    assert micro["concepts_count"] == 6                 # FC-0's set, beside the ids, outside the tally
    assert row["removed_total"] == 3
    for t in row["tiers"]:
        assert isinstance(t["before"], int) and isinstance(t["after"], int)
    assert row["thresholds"] == fc.THRESHOLDS and row["check"] is None
    assert row["sizes"]["graph_context_entry"] == 3 and row["sizes"]["graph_context_after_rebuild"] == 2
    assert row["block"]["kb_lines"] == 2 and row["block"]["tokens_sent"] > 0
    # Bound to the record.
    rec = lineage.get(row["lineage_id"])
    assert rec is not None and rec.record_hash == row["record_hash"]
    assert row["lineage_id"] == done["lineage_id"]
    # Ids and numbers, never text.
    assert RARE not in cap.read_text(encoding="utf-8")
    assert set(row) <= fc.ROW_KEYS
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# 3. The next-turn check
# ---------------------------------------------------------------------------

def _result(events, state=None, final=0):
    return CompactionResult(state=state or NexusLoopState(), events=list(events),
                            final_tokens=final, reports=[])


def test_next_turn_check_present_absent_demoted(tmp_path, monkeypatch):
    """PLANTED, at the capture's own seam: a fire removing A (a graph_context
    row) and B (a session-memory row) and demoting C (a hippocampus edge),
    then a next turn whose rebuilt block holds A and C (C demoted) and not B.
    The next row records `A: present, B: absent, C: demoted`, written on the
    next row and keyed to the fire's turn and lineage_id."""
    cap = tmp_path / "fire.jsonl"
    _on(monkeypatch, cap)
    A, B, C = fc.triple_id("a", "r", "a2"), fc.edge_id("b", "b2"), fc.edge_id("c", "c2")
    fire = CompactionEvent(Tier.COLLAPSE, 7300, 7300, 1, "linear_paths_collapsed",
                           removed_edges=(("b", "b2"),), removed_triples=(("a", "r", "a2"),),
                           demoted_edges=(("c", "c2"),))
    observed_n = {"triples": [], "concepts": [], "edges_live": [B, C], "edges_demoted": [], "injected_ids": []}
    kind = fc.capture_fire(turn=4, lineage_id="lin-4", record_hash="h4", result=_result([fire], final=7300),
                           observed=observed_n, sizes_entry=(1, 1), sizes_after=(1, 1))
    assert kind == "fire" and fc.pending()["ids"] == sorted([A, B, C])
    # The next turn: A re-retrieved into the block, C held demoted, B gone.
    hippo = Hippocampus()
    hippo.add_edge(SynapticEdge(source="c", target="c2", weight=0.5))
    hippo.get_edge("c", "c2").demoted = True

    class _RT:
        class triple:
            id, subject, relation, object = 7, "a", "r", "a2"
    observed_n1 = fc.observe(hits=[_RT()], rendered_activation=(), hippocampus=hippo)
    kind = fc.capture_fire(turn=5, lineage_id="lin-5", record_hash="h5", result=_result([], final=6000),
                           observed=observed_n1, sizes_entry=(0, 1), sizes_after=(1, 1))
    assert kind == "check" and fc.pending() is None
    rows = fc.read_rows(cap)
    assert len(rows) == 2 and rows[1]["kind"] == "check" and rows[1]["turn"] == 5
    check = rows[1]["check"]
    print(f"\nCP-3 ({sys.platform}) next-turn check: {check['ids']}")
    assert check["ids"] == {A: fc.PRESENT, B: fc.ABSENT, C: fc.DEMOTED}
    assert check["counts"] == {"present": 1, "absent": 1, "demoted": 1, "unresolved": 0}
    assert check["reason"] is None
    assert check["of_turn"] == 4 and check["of_lineage_id"] == "lin-4"
    assert check["entry_estimate"] == 6000 and check["injected_ids"] == [7]
    t = fc.tally(rows)
    assert (t["b49"]["removed_total"], t["b49"]["present_next"], t["b49"]["absent_next"],
            t["b49"]["demoted_next"], t["b49"]["unresolved_ids"]) == (3, 1, 1, 1, 0)


def test_a_planted_collapse_reads_demoted_through_the_loop(tmp_path, monkeypatch):
    """PLANTED, through the production loop: a history sized to enter between
    tier 3's and tier 4's thresholds and a linear chain p→q→r in the
    hippocampus that nothing in the question reaches. Collapse demotes p→q
    and q→r on turn 1; on turn 2 nothing re-fires them, and the next row
    reads both `demoted` — the one survival path CE named, exercised. The
    observation is taken at turn 2's assembly, before turn 2's own cascade."""
    cap = tmp_path / "fire.jsonl"
    _on(monkeypatch, cap)
    store, lineage = _store(tmp_path)
    hippo = Hippocampus()
    hippo.add_edge(SynapticEdge(source="p", target="q", weight=0.6))
    hippo.add_edge(SynapticEdge(source="q", target="r", weight=0.6))
    state = _sized(TIER_3_COLLAPSE_AT + 150)
    _, state = _turn(state, store, lineage, hippo)
    rows = fc.read_rows(cap)
    assert len(rows) == 1 and rows[0]["tier_reached"] == "graph_collapse"
    assert TIER_3_COLLAPSE_AT <= rows[0]["entry_estimate"] < TIER_4_AUTO_AT
    t3 = rows[0]["tiers"][2]
    assert t3["demoted_ids"] == [fc.edge_id("p", "q"), fc.edge_id("q", "r")] and t3["items_removed"] == 1
    assert hippo.get_edge("p", "r") is not None, "collapse synthesised p→r"
    # Turn 2 on the state turn 1 left: the answer is short, nothing fires.
    _, _ = _turn(state, store, lineage, hippo)
    rows = fc.read_rows(cap)
    assert len(rows) == 2
    check = rows[1]["check"]
    print(f"\nCP-3b ({sys.platform}) planted collapse, next turn: {check['ids']}")
    assert check["ids"] == {fc.edge_id("p", "q"): fc.DEMOTED, fc.edge_id("q", "r"): fc.DEMOTED}
    assert check["of_turn"] == 1 and rows[1]["turn"] == 2
    store.close(); lineage.close()


def test_an_observe_that_raises_leaves_the_turn_intact_and_the_ids_unresolved(
        tmp_path, monkeypatch, caplog):
    """PLANTED: turn 1 fires at tier 2 (three orphan rows) and is observed
    normally, so three ids await their check; on turn 2 the observation's
    body (`_observe`, which `observe` guards) raises. The consequence, not a
    flag: the turn completes — the done frame arrives with its answer, the
    lineage record is written and the row binds to it — the warning is
    logged, and the row carries the three ids `unresolved` with
    `reason: no_observation`, counted by the tally as unresolved and never
    as present; nothing is left pending for a later turn to answer from the
    wrong block. The guard is inside `observe`, so the loop's bound name
    reaches it."""
    cap = tmp_path / "fire.jsonl"
    _on(monkeypatch, cap)
    store, lineage = _store(tmp_path)
    state = _sized(TIER_2_MICRO_AT + 150, graph_context=(
        ("orphan1", "r", "o1"), ("orphan2", "r", "o2"), ("orphan3", "r", "o3")))
    _, state = _turn(state, store, lineage, Hippocampus())
    assert fc.pending() is not None and len(fc.pending()["ids"]) == 3

    def boom(**_kw):
        raise RuntimeError("planted: the observation body failed")
    monkeypatch.setattr(fc, "_observe", boom)
    with caplog.at_level("WARNING", logger="nexus.capture"):
        done, _ = _turn(state, store, lineage, Hippocampus())
    assert done["answer_chars"] > 0 and done["lineage_id"], "the turn did not complete intact"
    rec = lineage.get(done["lineage_id"])
    assert rec is not None
    assert any("observation failed" in r.getMessage() for r in caplog.records), "no warning logged"
    rows = fc.read_rows(cap)
    assert len(rows) == 2 and rows[1]["lineage_id"] == done["lineage_id"]
    assert rows[1]["record_hash"] == rec.record_hash
    check = rows[1]["check"]
    print(f"\nCP-3c ({sys.platform}) observe raised on turn 2: row kind={rows[1]['kind']} "
          f"check ids={check['ids']} reason={check['reason']}")
    assert check["of_turn"] == 1 and check["reason"] == fc.NO_OBSERVATION
    assert len(check["ids"]) == 3 and set(check["ids"].values()) == {fc.UNRESOLVED}
    assert check["counts"] == {"present": 0, "absent": 0, "demoted": 0, "unresolved": 3}
    assert check["injected_ids"] == []
    b49 = fc.tally(rows)["b49"]
    assert (b49["present_next"], b49["absent_next"], b49["demoted_next"]) == (0, 0, 0)
    own = rows[1]["removed_total"] if rows[1]["kind"] == "fire" else 0
    assert b49["unresolved_ids"] == rows[0]["removed_total"] + own == b49["removed_total"]
    # Turn 1's question is closed; only turn 2's own fire, if any, may wait.
    if own:
        assert fc.pending()["turn"] == 2
    else:
        assert fc.pending() is None
    assert fc.observe(hits=[], rendered_activation=(), hippocampus=None) is None
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# 4. The binding
# ---------------------------------------------------------------------------

def test_binding_names_a_mismatched_row_and_a_row_with_no_record(tmp_path, monkeypatch):
    """PLANTED: after one real fire row, a copy of it with its `record_hash`
    altered, and a row whose `lineage_id` no record carries. `verify` names
    the first as mismatched and the second as unbound; the real row is bound.
    """
    cap = tmp_path / "fire.jsonl"
    _on(monkeypatch, cap)
    store, lineage = _store(tmp_path)
    _turn(_sized(TIER_2_MICRO_AT + 150, graph_context=(("orphan1", "r", "o1"),)),
          store, lineage, Hippocampus())
    rows = fc.read_rows(cap)
    assert len(rows) == 1
    bad = dict(rows[0], record_hash="0" * 64)
    none = dict(rows[0], lineage_id="no-such-record", record_hash=rows[0]["record_hash"])
    with cap.open("a", encoding="utf-8") as f:
        f.write(json.dumps(bad) + "\n" + json.dumps(none) + "\n")

    def lookup(lid):
        rec = lineage.get(lid)
        return rec.record_hash if rec is not None else None
    v = fc.verify(fc.read_rows(cap), lookup)
    print(f"\nCP-4 ({sys.platform}) binding: {v}")
    assert v["rows"] == 3 and v["bound"] == 1
    assert v["mismatched"] == [rows[0]["lineage_id"]]
    assert v["unbound"] == ["no-such-record"]
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# 5. The off position
# ---------------------------------------------------------------------------

def test_off_position_writes_nothing_and_stats_read_zero(tmp_path, monkeypatch):
    """With the gate off (the explicit literal, which is also what satisfies the
    dotenv rollback scan), a fire that would have written a row writes no row,
    creates no directory, and `/cortex/stats.fire_capture` reads zero with the
    gate declared off."""
    cap = tmp_path / "capdir" / "fire.jsonl"
    monkeypatch.setenv("NEXUS_FIRE_CAPTURE", "off")
    monkeypatch.setenv("NEXUS_FIRE_CAPTURE_PATH", str(cap))
    store, lineage = _store(tmp_path)
    done, _ = _turn(_sized(TIER_2_MICRO_AT + 150, graph_context=(("orphan1", "r", "o1"),)),
                    store, lineage, Hippocampus())
    assert done["reinforcement"]["injected"] >= 2        # the turn itself ran
    assert not cap.exists() and not cap.parent.exists(), "off must mean NOTHING"
    assert fc.pending() is None
    store.close(); lineage.close()

    monkeypatch.setenv("NEXUS_API_TOKEN", "cp-token")
    from fastapi.testclient import TestClient
    from backend.server import create_app, create_services
    svc = create_services(kb_db_path=tmp_path / "s" / "kb.db", chroma_dir=tmp_path / "s" / "chroma",
                          eval_log_path=tmp_path / "s" / "m.jsonl", archive_db_path=tmp_path / "s" / "a.db",
                          snapshots_dir=tmp_path / "s" / "snap")
    block = TestClient(create_app(svc)).get("/cortex/stats").json()["fire_capture"]
    print(f"\nCP-5 ({sys.platform}) stats: {json.dumps(block)[:300]}")
    assert block["enabled"] is False
    assert (block["fire_rows"], block["last_fire_turn"], block["check_rows"]) == (0, None, 0)
    assert block["b49"] == {"removed_total": 0, "present_next": 0, "absent_next": 0, "demoted_next": 0,
                            "unresolved_ids": 0, "fires_tier_1_to_3": 0, "fires_needed_for_the_rule": 10}
    assert "derived, not record" in block["note"] and "ids and numbers only" in block["note"].lower()


def test_the_default_position_is_off(monkeypatch):
    monkeypatch.delenv("NEXUS_FIRE_CAPTURE", raising=False)
    assert fc.capture_enabled() is False


def test_the_suite_redirect_is_live():
    """conftest's env line lands every enabled test in the sandbox, not data/."""
    from tests.conftest import SESSION_TMP
    assert fc.capture_path() != Path(fc.DEFAULT_FIRE_CAPTURE_PATH)
    assert str(fc.capture_path()).startswith(str(SESSION_TMP))


def test_capture_is_acknowledged_to_the_writer_ratchet():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _writers import ACKNOWLEDGED_DATA_CONSTANTS
    key = ("backend.eval.fire_capture", "DEFAULT_FIRE_CAPTURE_PATH")
    assert key in ACKNOWLEDGED_DATA_CONSTANTS
    assert "NEXUS_FIRE_CAPTURE_PATH" in ACKNOWLEDGED_DATA_CONSTANTS[key]


# ---------------------------------------------------------------------------
# 6. The guard: no text column
# ---------------------------------------------------------------------------

def _keys(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{prefix}{k}"
            yield from _keys(v, f"{prefix}{k}.")
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v, prefix)


def _values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _values(v)
    elif isinstance(obj, str):
        yield obj


def _assert_no_text(row: dict, planted_texts) -> None:
    """The guard, two rules. (1) Schema: the row's top-level keys are the
    declared set and no key at any depth is text-shaped. (2) Value: no string
    value anywhere in the row contains any of the turn's texts."""
    assert set(row) <= fc.ROW_KEYS, f"undeclared key(s): {set(row) - fc.ROW_KEYS}"
    for k in _keys(row):
        tokens = set(k.rsplit(".", 1)[-1].lower().split("_"))   # token-wise: `graph_context` is not `text`
        assert not (tokens & set(fc.TEXT_KEY_MARKERS)), f"text-shaped key: {k}"
    for v in _values(row):
        for text in planted_texts:
            assert text not in v, f"a row value carries the turn's text: {v[:60]!r}"


def test_no_text_column_by_introspection(tmp_path, monkeypatch):
    """The guard over a written row, then three companions: the packet's
    planted `answer_text` column fails by name; a planted `user_said` column
    carrying the question — a name the guard's marker list does not contain
    (OQ-16 rule 1: a member the guard's source does not name) — fails by
    value; a nested `tiers[].summary_text` fails by name at depth."""
    cap = tmp_path / "fire.jsonl"
    _on(monkeypatch, cap)
    store, lineage = _store(tmp_path)
    question = f"tell me about python {RARE}"
    _turn(_sized(TIER_2_MICRO_AT + 150, graph_context=(("orphan1", "r", "o1"),)),
          store, lineage, Hippocampus(), question=question)
    row = fc.read_rows(cap)[0]
    planted = [RARE, question, "Python is a programming_language.", STABLE_SYSTEM_PREFIX[:40]]
    _assert_no_text(row, planted)                      # the real row passes
    with pytest.raises(AssertionError, match="undeclared key"):
        _assert_no_text(dict(row, answer_text="Python is a programming_language."), planted)
    # Companion 2 (OQ-16 rule 1 — a member the guard's source does not name):
    # the question smuggled as a VALUE under a declared, id-shaped key, where
    # no key rule can see it. Only the value rule catches it.
    smuggled = json.loads(json.dumps(row))
    smuggled["tiers"][0]["removed_ids"].append(question)
    with pytest.raises(AssertionError, match="carries the turn's text"):
        _assert_no_text(smuggled, planted)
    # Companion 3: an undeclared top-level key fails by schema whatever it is called.
    with pytest.raises(AssertionError, match="undeclared key"):
        _assert_no_text(dict(row, user_said=question), planted)
    nested = json.loads(json.dumps(row))
    nested["tiers"][0]["summary_text"] = "x"
    with pytest.raises(AssertionError, match="text-shaped key"):
        _assert_no_text(nested, planted)
    store.close(); lineage.close()


def test_the_module_reads_no_text_attribute():
    """AST over the capture module: it never reads `.content`, `.answer`,
    `.reasoning`, `.raw`, `.render` or `.surface` — the attributes every
    text-carrying object in the loop exposes — so the only way text could
    enter a row is through an argument the loop does not pass."""
    tree = ast.parse((ROOT / "backend" / "eval" / "fire_capture.py").read_text(encoding="utf-8"))
    forbidden = {"answer", "reasoning", "raw", "render", "surface", "query"}
    hits = sorted({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in forbidden})
    assert not hits, f"the capture reads a text attribute: {hits}"
    # `.content` is read exactly once: the head of the history, for its LENGTH.
    content_reads = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
                     and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "content"]
    assert len(content_reads) == 1


def test_compaction_emits_and_decides_nothing_by_the_cascade_pins():
    """`compaction.py` was touched only to emit. The CE pins in
    tests/test_ce_cascade.py (estimates, removed counts, tier distributions,
    stopping rule) are the decision pins and are unchanged; this test holds
    the shape: the four emit fields default empty, so every pre-CP
    construction of the event is unchanged, and no threshold constant moved."""
    ev = CompactionEvent(Tier.SNIP, 1, 1, 0, "r")
    assert (ev.removed_edges, ev.removed_triples, ev.removed_concepts, ev.demoted_edges) == ((), (), (), ())
    assert fc.THRESHOLDS == {"snip": 5734, "microcompact": 6553, "graph_collapse": 7208, "auto_compact": 7782}
