"""The history path's wrapper — shape A in production (CHANGE_A_SHIP_SHAPE_A, tier 3).

Every prior assistant answer the next prompt carries is wrapped as

    <nexus-block source="prior_answer" turn=N truncated=0>
    <the answer>
    </nexus-block>

or, when the answer was cut,

    <nexus-block source="prior_answer" turn=N truncated=1 kept=<n> dropped=<m>>

with `kept` and `dropped` in characters and `kept + dropped == len(answer)`.
The two BB3a rules the harness established hold here unchanged:

- **the runtime's compaction block is exempt**, keyed on its delimiter
  (`is_runtime_block`): the tier-4 summary is a code-written block inside
  `COMPACTION_BLOCK_OPEN`/`_CLOSE` (BA fix 1), never a prior answer; a prose
  answer that merely mentions the delimiter is still wrapped;
- **the turn label is carried, never recomputed.** `N` is the loop's turn
  count when the answer was produced, kept in a carrier beside the messages
  (`NexusLoopState.history_carrier`), never on the message dicts, which go
  to the provider verbatim (BB Part 2). At every assembly `align_carrier`
  requires the carrier and the history to agree — one entry per answer,
  hashes matching — and raises `CarrierMismatch` otherwise. Nothing is
  relabelled: recomputing from position is how a post-reset answer was
  once labelled turn=2.

**The cut is at a sentence boundary, never mid-word** (correcting the defect
BB Part 2 recorded, where the cut was by character). The cap is
`HISTORY_ANSWER_CAP_CHARS`, in characters — see its comment for the source.

Moved here from `scripts/history_shape_harness.py` (BB Part 3a-2/3a-3), which
now imports these functions, so one implementation remains. The harness's
own shape A — a hard cut at its pre-registered `HISTORY_MAX_CHARS = 600`
with `truncated=K` — stays in the harness as the instrument that produced
the BB3 data; it passes its own cut rule to `carrier_entry`.

`NEXUS_HISTORY_WRAP=off` is the rollback: the pre-A history path byte for
byte (the carrier is still maintained, so turning it back on is safe).
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Callable

from backend.loop.compaction import COMPACTION_BLOCK_CLOSE, COMPACTION_BLOCK_OPEN

# PROVISIONAL — review when a week of real `answer_chars` exists (C-38 records
# that per turn). Source: the 480 production-model answers of the BB3
# overnight run (`reports/bb3_overnight.json`, 4–5 October 2026, 20
# conversations × 12 turns × both arms), measured 18 October 2026: pooled p50
# 241, p90 1,304, p95 1,772, p99 2,268, max 3,044 characters (control p95
# 1,794, A p95 1,713). The smallest round number at or above the pooled 95th
# percentile; it would have cut about 5% of them. In CHARACTERS, not tokens:
# the run saved no token counts, the cut is counted at assembly without a
# tokenizer, and `answer_chars` is the unit the review data will carry.
HISTORY_ANSWER_CAP_CHARS = 1800

PRIOR_ANSWER_OPEN = '<nexus-block source="prior_answer"'
PRIOR_ANSWER_CLOSE = "</nexus-block>"

# The attribute grammar, read back. Accepts both forms (truncated=0, and
# truncated=1 kept= dropped=) and the harness's pre-registered truncated=K.
WRAPPER_RE = re.compile(
    r'<nexus-block source="prior_answer" turn=(\d+) truncated=(\d+)'
    r'(?: kept=(\d+) dropped=(\d+))?>\n(.*)\n</nexus-block>\Z', re.S)

# A sentence ends at ., ! or ? (optionally followed by a closing quote or
# bracket) that is followed by whitespace or the end of the text.
_SENTENCE_END = re.compile(r'[.!?]["\'”’)\]]*(?=\s|$)')
_LAST_SPACE = re.compile(r"\s(?=\S*$)")


class CarrierMismatch(RuntimeError):
    """The carrier and the history disagree. Raised at assembly with the
    first disagreement named; never relabelled — a label the carrier cannot
    vouch for is an error, not a guess (BB Part 3a-3, Fable's deviation 1)."""


def history_wrap_enabled() -> bool:
    """`NEXUS_HISTORY_WRAP=off` restores the pre-A history path byte for byte."""
    return os.environ.get("NEXUS_HISTORY_WRAP", "on").strip().lower() != "off"


def is_runtime_block(content: str) -> bool:
    """True iff the message IS the runtime's compaction block — the delimiter
    at its head and its close at its tail. Keyed on the delimiter, never on
    position or on content."""
    c = (content or "").strip()
    return c.startswith(COMPACTION_BLOCK_OPEN) and c.endswith(COMPACTION_BLOCK_CLOSE)


def is_wrapped(content: str) -> bool:
    return (content or "").startswith(PRIOR_ANSWER_OPEN)


def answer_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def cut_at_sentence(text: str, cap: int = HISTORY_ANSWER_CAP_CHARS) -> tuple[str, int]:
    """The kept text and the number of characters dropped. Nothing is cut at
    or under the cap. Over it, the cut falls at the last sentence end inside
    the cap; with no sentence end inside the cap, at the last whitespace
    (never mid-word); and only for a single unbroken token longer than the
    cap, at the cap itself. `len(kept) + dropped == len(text)` always."""
    if len(text) <= cap:
        return text, 0
    end = None
    for m in _SENTENCE_END.finditer(text):
        if m.end() > cap:
            break
        end = m.end()
    if end is None:
        ws = _LAST_SPACE.search(text[:cap])
        end = ws.start() if ws and ws.start() > 0 else cap
    kept = text[:end].rstrip()
    return kept, len(text) - len(kept)


def wrapper_open(turn: int, *, truncated: bool, kept: int = 0, dropped: int = 0) -> str:
    """The opening tag: `turn=N truncated=0`, or `turn=N truncated=1 kept=<n>
    dropped=<m>` when the answer was cut, both counts in characters."""
    if not truncated:
        return f'{PRIOR_ANSWER_OPEN} turn={turn} truncated=0>'
    return f'{PRIOR_ANSWER_OPEN} turn={turn} truncated=1 kept={kept} dropped={dropped}>'


def wrap_answer(answer: str, turn: int, *, cap: int = HISTORY_ANSWER_CAP_CHARS) -> str:
    kept, dropped = cut_at_sentence(answer, cap)
    head = wrapper_open(turn, truncated=dropped > 0, kept=len(kept), dropped=dropped)
    return head + "\n" + kept + "\n" + PRIOR_ANSWER_CLOSE


def _production_cut(answer: str) -> str:
    return cut_at_sentence(answer)[0]


def carrier_entry(answer: str, turn: int, cut: Callable[[str], str] = _production_cut) -> dict:
    """One carrier entry: the loop's turn count when the answer was produced,
    the hash of the answer as produced, and the hash of the body the wrapper
    keeps under `cut` (production's sentence-boundary cut by default; the
    harness passes its pre-registered hard cut). Kept beside the messages,
    never on them."""
    kept = cut(answer)
    return {"turn": turn, "hash": answer_hash(answer), "wrapped_hash": answer_hash(kept),
            "truncated": len(answer) - len(kept)}


def align_carrier(messages: tuple, carrier, *, strict: bool = True) -> list:
    """The carrier's entry for each assistant message in the history that is
    not the runtime's block, in order — and the assembly assertion: the two
    must agree, one entry per answer, hashes matching, or this raises
    `CarrierMismatch`. The history's answers are the carrier's TAIL, because
    compaction only ever drops a prefix of the history (tier 4 keeps the last
    pair) and nothing else; if that ever stops being true the hashes
    disagree and this raises rather than relabels. Returned pruned, so an
    entry whose message the reset dropped does not outlive the message. A
    wrapped answer is checked on its label and its body; an unwrapped one on
    its hash.

    `strict=False` is production's reading of a history the carrier does
    not reach the start of: answers BEFORE the carrier's first entry are
    pre-carrier history (a planted fixture, or a session that ran under
    `NEXUS_HISTORY_WRAP=off` and was switched on). They are left exactly as
    they are — never wrapped, never relabelled — and the returned list
    covers the tail only; every covered answer is still checked, and any
    disagreement still raises. The harness keeps the strict reading, where
    an entry short IS a disagreement."""
    carrier = list(carrier)
    answers = [m for m in messages if m.get("role") == "assistant" and not is_runtime_block(m.get("content", ""))]
    if len(carrier) < len(answers):
        if strict:
            raise CarrierMismatch(f"history holds {len(answers)} answers, carrier holds {len(carrier)} entries")
        answers = answers[len(answers) - len(carrier):]
    aligned = carrier[len(carrier) - len(answers):]
    for i, (m, e) in enumerate(zip(answers, aligned)):
        body = m["content"]
        blk = WRAPPER_RE.match(body)
        if blk:
            label, inner = int(blk.group(1)), blk.group(5)
            if label != e["turn"]:
                raise CarrierMismatch(f"answer {i}: wrapper label turn={label}, carrier entry says turn {e['turn']}")
            if answer_hash(inner) != e["wrapped_hash"]:
                raise CarrierMismatch(f"answer {i}: wrapped body is not the carrier's answer for turn {e['turn']}")
        elif is_wrapped(body):
            raise CarrierMismatch(f"answer {i}: a prior_answer wrapper that is not a complete block")
        elif answer_hash(body) != e["hash"]:
            raise CarrierMismatch(f"answer {i}: body hash {answer_hash(body)[:12]}… is not the carrier's for turn {e['turn']}")
    return aligned


def wrap_prior_answers(messages: tuple, carrier, *, cap: int = HISTORY_ANSWER_CAP_CHARS) -> tuple:
    """The production shape: every assistant message that is neither the
    runtime's block nor already wrapped is wrapped with its carrier entry's
    turn, cut at a sentence boundary at `cap`. The carrier is asserted
    against the history first (`align_carrier`, production's reading: a
    pre-carrier prefix stays bare). Every message returned carries `role`
    and `content` only."""
    aligned = align_carrier(messages, carrier, strict=False)
    answers = [i for i, m in enumerate(messages)
               if m.get("role") == "assistant" and not is_runtime_block(m.get("content", ""))]
    covered = dict(zip(answers[len(answers) - len(aligned):], aligned))
    out = []
    for i, m in enumerate(messages):
        entry = covered.get(i)
        if entry is None or is_wrapped(m["content"]):
            out.append(dict(m))
            continue
        out.append({"role": "assistant", "content": wrap_answer(m["content"], entry["turn"], cap=cap)})
    return tuple(out)
