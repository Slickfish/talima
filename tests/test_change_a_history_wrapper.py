"""CHANGE A — shape A on the production history path: the declared behaviour.

Tier 3 (CHANGE_A_SHIP_SHAPE_A.md): one declared-behaviour test, never a value
test. A planted long answer is cut at a sentence boundary, never mid-word,
and the wrapper reports `truncated=1 kept=<n> dropped=<m>` with the counts
adding up; a short answer is untouched and reports `truncated=0`. Through the
production loop: the new answer is wrapped with its own turn, the runtime's
compaction block is exempt, and `NEXUS_HISTORY_WRAP=off` is the pre-A path.
The cap's VALUE is asserted nowhere here — the planted input is built from
the constant so that a review of the value does not touch this file.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from backend.loop import history as H

SENTENCES = ("The first point is that sleep improves recovery. "
             "The second point is that caffeine improves endurance! "
             "Does strength training require rest days? It does. ")


def _long_answer() -> str:
    """Sentences past the cap, ending mid-sentence so a character cut would land mid-word."""
    text = ""
    while len(text) <= 2 * H.HISTORY_ANSWER_CAP_CHARS:
        text += SENTENCES
    return text + "And one more clause with no period at the end"


def test_a_long_answer_is_cut_at_a_sentence_boundary_and_the_counts_add_up():
    answer = _long_answer()
    assert len(answer) > H.HISTORY_ANSWER_CAP_CHARS, "the plant is over the cap"
    wrapped = H.wrap_answer(answer, 5)
    head, body, close = wrapped.split("\n", 1)[0], wrapped.split("\n", 1)[1].rsplit("\n", 1)[0], wrapped.rsplit("\n", 1)[1]
    m = re.fullmatch(r'<nexus-block source="prior_answer" turn=5 truncated=1 kept=(\d+) dropped=(\d+)>', head)
    assert m, head
    kept, dropped = int(m.group(1)), int(m.group(2))
    print(f"\nplanted {len(answer)} chars -> kept {kept} dropped {dropped}; kept text ends {body[-40:]!r}")
    assert kept == len(body) and kept + dropped == len(answer), "the counts add up, in characters"
    assert kept <= H.HISTORY_ANSWER_CAP_CHARS
    assert body[-1] in ".!?", "the kept text ends with sentence punctuation"
    assert answer.startswith(body) and answer[len(body)] in " \n", "the cut falls after a whole sentence, never mid-word"
    assert close == H.PRIOR_ANSWER_CLOSE
    # The cut does not fall where a character cut would: the character cut lands mid-word here.
    assert answer[H.HISTORY_ANSWER_CAP_CHARS - 1].isalpha() or answer[H.HISTORY_ANSWER_CAP_CHARS].isalpha()


def test_a_short_answer_is_untouched_and_reports_truncated_0():
    short = "Sleep improves recovery."
    wrapped = H.wrap_answer(short, 2)
    assert wrapped == f'<nexus-block source="prior_answer" turn=2 truncated=0>\n{short}\n{H.PRIOR_ANSWER_CLOSE}'
    assert H.cut_at_sentence(short) == (short, 0)
    # A single unbroken token longer than the cap is the only input cut at the cap itself.
    blob = "x" * (H.HISTORY_ANSWER_CAP_CHARS + 7)
    kept, dropped = H.cut_at_sentence(blob)
    assert len(kept) == H.HISTORY_ANSWER_CAP_CHARS and dropped == 7
    # Words but no sentence end: the cut falls at the last whitespace, never mid-word.
    words = ("word " * (H.HISTORY_ANSWER_CAP_CHARS // 2)).strip()
    kept, dropped = H.cut_at_sentence(words)
    assert kept.endswith("word") and not kept.endswith(" ") and words[len(kept)] == " " and len(kept) + dropped == len(words)


def _turn(state, question, answer, *, monkeypatch=None):
    """One turn of the production loop with a stub provider answering `answer`."""
    from backend.loop.agentic_loop import run_turn

    class _Stub:
        async def stream_chat(self, model, messages, num_ctx):
            yield answer if messages[-1]["content"] != "__extract__" else "[]"

    class _Prov(_Stub):
        calls = 0

        async def stream_chat(self, model, messages, num_ctx):
            _Prov.calls += 1
            yield answer if _Prov.calls % 2 == 1 else "[]"

    out = state

    async def drive():
        nonlocal out
        async for _f, s in run_turn(out, question, provider=_Prov()):
            out = s
    asyncio.run(drive())
    return out


def test_through_the_loop_the_prior_answer_is_wrapped_with_its_turn_and_the_block_is_exempt(monkeypatch, tmp_path):
    from backend.loop.compaction import compaction_block
    from backend.loop.state import NexusLoopState
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    monkeypatch.delenv("NEXUS_HISTORY_WRAP", raising=False)
    s1 = _turn(NexusLoopState(), "what helps recovery?", "Sleep improves recovery.")
    assert s1.messages[-1]["content"] == H.wrap_answer("Sleep improves recovery.", 1)
    assert [e["turn"] for e in s1.history_carrier] == [1]
    s2 = _turn(s1, "and endurance?", _long_answer())
    tags = [m["content"].split("\n", 1)[0] for m in s2.messages if m["role"] == "assistant"]
    print(f"\nafter two turns: {tags}")
    assert tags[0] == '<nexus-block source="prior_answer" turn=1 truncated=0>'
    assert tags[1].startswith('<nexus-block source="prior_answer" turn=2 truncated=1 kept=')
    assert [e["turn"] for e in s2.history_carrier] == [1, 2]
    # The runtime's compaction block, planted as tier 4 leaves it, is never wrapped; a bare
    # pre-carrier answer (history the carrier does not reach) is left exactly as it is.
    planted = NexusLoopState(messages=({"role": "assistant", "content": compaction_block("[stub]")},
                                       {"role": "user", "content": "q0"},
                                       {"role": "assistant", "content": "A bare pre-carrier answer."}),
                             turn_count=6)
    s7 = _turn(planted, "repeat what you said", "I said sleep improves recovery.")
    contents = [m["content"] for m in s7.messages if m["role"] == "assistant"]
    assert contents[0] == compaction_block("[stub]")
    assert contents[1] == "A bare pre-carrier answer."
    assert contents[2] == H.wrap_answer("I said sleep improves recovery.", 7)
    # A disagreement is refused, never relabelled: a wrapper whose label the carrier contradicts.
    bad = NexusLoopState(messages=({"role": "user", "content": "q"},
                                   {"role": "assistant", "content": H.wrap_answer("x.", 9)}),
                         history_carrier=(H.carrier_entry("x.", 3),), turn_count=3)
    with pytest.raises(H.CarrierMismatch, match="turn=9, carrier entry says turn 3"):
        _turn(bad, "q2", "y.")
    # The rollback: the pre-A path, bare history; the carrier is still kept, so switching on is safe.
    monkeypatch.setenv("NEXUS_HISTORY_WRAP", "off")
    s_off = _turn(NexusLoopState(), "q", "Bare answer.")
    assert s_off.messages[-1]["content"] == "Bare answer." and [e["turn"] for e in s_off.history_carrier] == [1]
    monkeypatch.setenv("NEXUS_HISTORY_WRAP", "on")
    s_on = _turn(s_off, "q2", "Second answer.")
    assert [m["content"] for m in s_on.messages if m["role"] == "assistant"] == [
        H.wrap_answer("Bare answer.", 1), H.wrap_answer("Second answer.", 2)]
