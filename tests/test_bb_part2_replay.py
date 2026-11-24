"""BB Part 2 — placement by replay, not by sampling.

Placement depends only on the prior answer's text and the shape's wrapping,
so `scripts/history_shape_replay.py` records turn-1 answers once, plants
every corpus item into each, and drives turn 2 through the real assembly
(`run_turn`) with a stub provider under every shape. What this file holds:

  - the locator's four outcomes, each reached by a constructed request, and
    a must-fail: an item that sits AFTER the block's close tag is bare, not
    labelled, and an item in the system message is the instruction region
    whatever else carries it;
  - the wrapper's byte cost is the arithmetic the script prints: measured
    utf-8 bytes of the wrapped message minus the original, equal to the
    formula, at lengths under and over HISTORY_MAX_CHARS;
  - the replay itself on one recorded conversation: the placement matrix is
    the expected one (control bare, A labelled, B absent — for the control
    item too), the truncation plant prints `truncated=K` with K exactly the
    overrun, and the tail of a truncated answer never reaches turn 2 under A
    while its head does;
  - the shapes send `role` and `content` only.
"""
from __future__ import annotations

import sys
from pathlib import Path

from tests._corpus import CORPUS, CORPUS_SHA256

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import history_shape_harness as hsh      # noqa: E402
import history_shape_replay as rp        # noqa: E402
import pytest

GUARDS = [("test_the_locator_reaches_all_four_regions", "test_the_locator_is_not_fooled_by_position")]


def _sys(text):
    return {"role": "system", "content": hsh.__dict__.get("STABLE_SYSTEM_PREFIX", "You are NEXUS.") + text}


def test_the_locator_reaches_all_four_regions():
    item = CORPUS["authority"]
    wrapped = hsh.PRIOR_OPEN.format(turn=1, truncated=0) + "\nanswer " + item + "\n" + hsh.PRIOR_CLOSE
    assert rp.locate([_sys(" KB CONTEXT\n" + item), {"role": "user", "content": "q"}], item)["region"] \
        == rp.IN_INSTRUCTION_REGION
    assert rp.locate([_sys(""), {"role": "assistant", "content": wrapped}], item)["region"] == rp.IN_LABELLED_BLOCK
    assert rp.locate([_sys(""), {"role": "assistant", "content": "answer " + item}], item)["region"] == rp.BARE_IN_HISTORY
    assert rp.locate([_sys(""), {"role": "user", "content": "q"}], item)["region"] == rp.ABSENT


def test_the_locator_is_not_fooled_by_position():
    """Must-fail companion: the label must ENCLOSE the item."""
    item = CORPUS["authority"]
    after_close = hsh.PRIOR_OPEN.format(turn=1, truncated=0) + "\nanswer\n" + hsh.PRIOR_CLOSE + " " + item
    assert rp.locate([_sys(""), {"role": "assistant", "content": after_close}], item)["region"] == rp.BARE_IN_HISTORY
    wrapped = hsh.PRIOR_OPEN.format(turn=1, truncated=0) + "\n" + item + "\n" + hsh.PRIOR_CLOSE
    both = [_sys(" " + item), {"role": "assistant", "content": wrapped}]
    assert rp.locate(both, item)["region"] == rp.IN_INSTRUCTION_REGION, "the worst case wins"


def test_the_byte_cost_is_arithmetic():
    for n in (10, 599, 600, 601, 2000):
        original = "x" * n
        wrapped = hsh.shape_messages(({"role": "user", "content": "q"},
                                      {"role": "assistant", "content": original}), "A")[1]["content"]
        measured = len(wrapped.encode("utf-8")) - len(original.encode("utf-8"))
        assert measured == rp.wrapper_cost_bytes(original, turn=1), n
    assert rp.wrapper_cost_bytes("x" * 10, turn=1) == rp.wrapper_cost_bytes("x" * 600, turn=1), \
        "constant under the bound"
    assert rp.wrapper_cost_bytes("x" * 601, turn=1) == rp.wrapper_cost_bytes("x" * 600, turn=1) - 1, \
        "one byte saved per character cut, minus nothing else"
    assert rp.wrapper_cost_bytes("x" * 10, turn=10) == rp.wrapper_cost_bytes("x" * 10, turn=1) + 1, \
        "one byte per extra digit of turn"


@pytest.mark.sqlite_only("the replay harness reads the SQLite files its rig builds (pre-registered on the pre-A path)")
def test_the_replay_on_one_conversation(tmp_path):
    kb = tmp_path / "fixture.db"
    hsh._build_store(kb).close()
    rec = rp.record("stub", hsh.CONVERSATIONS[:1], tmp_path / "rec")
    assert len(rec) == 1 and rec[0]["answer"], "turn 1 was recorded"
    rows = rp.replay(kb, rec, tmp_path / "rep")
    regions = {(r["item"], r["shape"]): r["region"] for r in rows}
    for name in CORPUS:
        assert regions[(name, "control")] == rp.BARE_IN_HISTORY, name
        assert regions[(name, "A")] == rp.IN_LABELLED_BLOCK, name
        assert regions[(name, "B")] == rp.ABSENT, name
    # Truncation, exercised: the plant at the tail of an over-long answer
    # is cut under A and the delimiter says by how much; the head survives.
    tail = next(r for r in rows if r["item"] == rp.TRUNCATION_TAIL and r["shape"] == "A")
    head = next(r for r in rows if r["item"] == rp.TRUNCATION_HEAD and r["shape"] == "A")
    assert tail["region"] == rp.ABSENT and tail["truncated"] > 0
    assert f"truncated={tail['truncated']}" in tail["excerpt"]
    assert head["region"] == rp.IN_LABELLED_BLOCK and head["truncated"] == tail["truncated"]
    bare_tail = next(r for r in rows if r["item"] == rp.TRUNCATION_TAIL and r["shape"] == "control")
    assert bare_tail["region"] == rp.BARE_IN_HISTORY, "control carries the tail; A does not"
    # Every request was assembled by the real loop: one prefix-led system message first.
    assert all(r["system_starts_with_prefix"] for r in rows)
    assert all(set(m) == {"role", "content"} for r in rows for m in r["request"])
    assert rows[0]["corpus_sha256"] == CORPUS_SHA256


def test_the_script_pre_registers_the_shapes_it_replays():
    assert rp.SHAPES == hsh.SHAPES == ("control", "A", "B")
    assert set(rp.REPLAY_ITEMS) == set(CORPUS) | {rp.TRUNCATION_HEAD, rp.TRUNCATION_TAIL}
    assert len(rp.LONG_FILLER) > hsh.HISTORY_MAX_CHARS
