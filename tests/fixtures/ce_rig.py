"""CE — the cascade rig (B-28). A tracked asset; BB Part 3 reuses it.

Parameterised: `pairs` (user/assistant pairs seeded into the history before
the first turn), `answer_chars` (the stub model's answer length), `kb_lines`
(claims in the store, all retrievable by the fixed question), `turns`, and
`user_chars` (the question's length). The rig drives the production loop
(`run_turn`) with a stub provider and records, per turn, what the cascade
did: the estimate at entry, every tier attempted with the estimate before
and after it, the tier reached, the stop reason (S1.1's condition), the
final estimate, and the injected set — read from the lineage record.

DETERMINISM, STATED (amended C5): the rig is deterministic ONLY because the
stub model's answers are fixed in length and content per turn — `answer(t)`
is a pure function of the turn number and `answer_chars`, and extraction
always returns nothing — and because the question is a pure function of the
turn number and `user_chars`. Two runs with the same parameters produce the
same rows; `tests/test_ce_cascade.py::test_ce_rig_is_deterministic` asserts
it on that basis.

THE CLOCK IS AN INPUT TOO (W0, the run to production, 31 October). The
statement above held on Linux and was false on a Windows CI runner: two
identical rigs in one process differed at turn 9 by one token of estimate,
in two CI runs of two. The rig seeded its seventeen claims and ran its turns
on the host's wall clock, and prominence decays on that clock. The claims'
source, `asserted`, is unregistered and decays at the extraction rate
(0.05 a day, arrival 0.65), so their ranking value — prominence at the
record's six decimals (SNIP-DET) — steps down by 1e-6 every 2.66 seconds of
age, the first step at 1.33 seconds. Rows written in one loop tie and fall
to the id; but a retrieval made while that boundary sits inside the rows'
write spread splits them by 1e-6, by rule. On a fast host the spread is
milliseconds and the split is rare; on a host with slow writes it lands in
most runs. Measured on Linux with the clock replaced (`RUN_LOG.md`, W0):
1 µs per read and frozen give identical rows; 20 ms per read differs from
turn 10 by one token, 35 ms from turn 2. So the rig now owns its clock:
`RigClock`, installed as `time.time` around its own seeding and every
turn it runs (and around any direct call a test makes inside
`rig.clocked()`), one microsecond per read from a fixed instant — what a
fast host gives, on every host. `clock="host"` gives the host's clock back
for a test that wants it. The product's order rule is unchanged (law 7);
what changed is that the instrument no longer reads the machine's speed.
Timestamps appear in no row.

WHAT THE RIG DOES NOT TOUCH: nothing under `backend/`. The capture wraps
`backend.loop.agentic_loop.run_cascade` by name for the duration of a turn
and restores it; arm B of S1.4 replaces `snip`, `microcompact` and
`graph_collapse` in `backend.loop.compaction` with no-ops for one turn and
restores them — the cascade's control flow (which tiers are attempted, which
thresholds are checked) is unchanged, only each tier's effect is removed.

RECONSTRUCTED PARAMETERS (run 1, B-28; run 2, 28 September) are marked where
they are set in `tests/test_ce_cascade.py`, not here.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.cortex.hebbian import HebbianUpdater
from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.loop import agentic_loop as loop_mod
from backend.loop import compaction as comp_mod
from backend.loop.agentic_loop import assemble_context, run_turn
from backend.loop.compaction import (TIER_1_SNIP_AT, TIER_2_MICRO_AT, TIER_3_COLLAPSE_AT,
                                     TIER_4_AUTO_AT, Tier)
from backend.loop.state import NexusLoopState
from backend.memory.hippocampus import Hippocampus
from backend.memory.lineage import LineageStore
from scripts.history_shape_harness import production_wrap_off

# Three substantive terms. `search_terms` gives each query term an equal
# slice of the candidate limit (80 / terms, floor 5): a longer question
# would cap retrieval below the planted `kb_lines` — eight terms reach ten
# rows whatever the store holds — so the question is short and padded with
# spaces to `user_chars`, which the term extractor ignores.
QUESTION_STEM = "concept improves outcome"

# The S1.1 stop reasons, one per exit of `run_cascade` (compaction.py lines
# 561, 574, 587, 601 and the fall-through after tier 4).
STOP_BELOW_T1 = "below_tier1_at_entry"          # :561  no tier fires
STOP_AFTER_SNIP = "below_tier2_after_snip"      # :574
STOP_AFTER_MICRO = "below_tier3_after_micro"    # :587
STOP_AFTER_COLLAPSE = "below_tier4_after_collapse"  # :601
STOP_TIER4_RAN = "tier4_ran"                    # :623  tier 4 always returns
TIER_ORDER = (Tier.SNIP, Tier.MICRO, Tier.COLLAPSE, Tier.AUTO)
THRESHOLDS = {Tier.SNIP: TIER_1_SNIP_AT, Tier.MICRO: TIER_2_MICRO_AT,
              Tier.COLLAPSE: TIER_3_COLLAPSE_AT, Tier.AUTO: TIER_4_AUTO_AT}

# The rig's clock (see THE CLOCK IS AN INPUT TOO, above). The instant is
# SNIP-DET's frozen one; the step is one microsecond per read. A 12-turn run
# makes about 425 reads, so its virtual span is under a millisecond, three
# orders of magnitude inside the first rounding step (1.33 seconds) — and a
# read more or fewer (the once-per-process unregistered-source warning is
# one) moves every later timestamp by a microsecond and nothing else.
RIG_CLOCK_BASE = 1_700_000_000.0        # 14 November 2023, 22:13:20 UTC
RIG_CLOCK_STEP = 1e-6                   # one microsecond per read
HOST_CLOCK = "host"


class RigClock:
    """A deterministic `time.time`: `base + n * step` on the n-th read.
    `step=0.0` is a frozen clock. Computed from the read count, never
    accumulated, so every platform's IEEE doubles give the same values."""

    def __init__(self, base: float = RIG_CLOCK_BASE, step: float = RIG_CLOCK_STEP):
        self.base, self.step, self.reads = float(base), float(step), 0

    def __call__(self) -> float:
        self.reads += 1
        return self.base + self.reads * self.step

    @contextlib.contextmanager
    def installed(self):
        """`time.time` is this clock for the block, restored after it, even on
        an exception. Nested installs restore in order."""
        real = time.time
        time.time = self
        try:
            yield self
        finally:
            time.time = real


@dataclass
class TierStep:
    tier: str
    before: int
    after: int
    removed: int


@dataclass
class TurnRow:
    turn: int
    est_before: int                 # the estimate at the cascade's entry
    steps: list = field(default_factory=list)   # TierStep per tier attempted, in order
    tier_reached: str | None = None             # None when nothing fired
    stop_reason: str = STOP_BELOW_T1
    final_tokens: int = 0
    messages_after: int = 0         # len(state.messages) after the turn
    injected_ids: tuple = ()
    cited: int = 0
    lineage_id: str | None = None

    @property
    def fired(self) -> bool:
        return bool(self.steps)

    def as_tuple(self) -> tuple:
        """Every value a determinism check compares; no timestamp in it."""
        return (self.turn, self.est_before,
                tuple((s.tier, s.before, s.after, s.removed) for s in self.steps),
                self.tier_reached, self.stop_reason, self.final_tokens,
                self.messages_after, self.injected_ids, self.cited)

    def line(self) -> str:
        tiers = " ".join(f"{s.tier}:{s.before}->{s.after}(-{s.removed})" for s in self.steps) or "-"
        return (f"turn {self.turn:>3}  est {self.est_before:>5}  {tiers:<70} reached={self.tier_reached or '-':<14} "
                f"stop={self.stop_reason:<26} final={self.final_tokens:>5} msgs={self.messages_after}")


def stop_reason_of(result) -> tuple[str | None, str]:
    """(tier reached, stop reason) read from a CompactionResult by S1.1's rule."""
    if not result.events:
        return None, STOP_BELOW_T1
    last = result.events[-1].tier
    return last.value, {Tier.SNIP: STOP_AFTER_SNIP, Tier.MICRO: STOP_AFTER_MICRO,
                        Tier.COLLAPSE: STOP_AFTER_COLLAPSE, Tier.AUTO: STOP_TIER4_RAN}[last]


class _Provider:
    """The stub model: the answer for turn t, then an empty extraction."""

    def __init__(self, rig: "CERig"):
        self.rig = rig
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls += 1
        if self.calls % 2 == 1:
            text = self.rig.answer(self.rig.turn + 1)
            for i in range(0, len(text), 50):
                yield text[i:i + 50]
        else:
            yield "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


class CERig:
    def __init__(self, root: Path, *, pairs: int = 0, answer_chars: int = 800, kb_lines: int = 17,
                 user_chars: int = 76, seed_session_memory: bool = False, hebbian: bool = True,
                 clock="rig", store=None):
        """`clock`: "rig" (the default, a fresh `RigClock`), "host" (the
        machine's own clock, the pre-W0 behaviour), or any `RigClock`.
        `store` (W3b part 4): where the claims and the records live, a path
        or a `StoreTarget`; the default, kb.db under `root`, is every pin's,
        and the parity rig passes one target per backend."""
        self.root = Path(root)
        self.kb_target = store if store is not None else self.root / "kb.db"
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = (RigClock() if clock == "rig" else None if clock == HOST_CLOCK else clock)
        self.params = {"pairs": pairs, "answer_chars": answer_chars, "kb_lines": kb_lines,
                       "user_chars": user_chars, "seed_session_memory": seed_session_memory}
        self.answer_chars, self.user_chars = answer_chars, user_chars
        with self.clocked():
            self._seed(pairs, kb_lines, seed_session_memory, hebbian)

    def clocked(self):
        """The rig's clock installed as `time.time` for a block — for a test
        that drives the loop directly with the rig's objects. A no-op under
        the host clock."""
        return self.clock.installed() if self.clock is not None else contextlib.nullcontext()

    def _seed(self, pairs: int, kb_lines: int, seed_session_memory: bool, hebbian: bool) -> None:
        self.store = TripleStore(self.kb_target)
        for i in range(1, kb_lines + 1):
            self.store.add(subject=f"concept{i}", relation="improves", object=f"outcome{i}",
                           weight=0.88, tier=1, granularity=Granularity.TOPIC, source="asserted")
        self.retriever = TripleRetriever(self.store)
        self.hippocampus = Hippocampus()
        self.hebbian = HebbianUpdater() if hebbian else None
        self.lineage = LineageStore(self.kb_target)
        self.provider = _Provider(self)
        self.turn = 0
        self.rows: list[TurnRow] = []
        msgs = []
        for p in range(1, pairs + 1):
            msgs.append({"role": "user", "content": self.question(-p)})
            msgs.append({"role": "assistant", "content": self.answer(-p)})
        self.state = NexusLoopState(messages=tuple(msgs))
        if seed_session_memory:
            # Run 1's "ingested into a hippocampus": one priming retrieval so the
            # graph holds the claims' edges, and the state's session memory
            # carries them the way stage 7 of a prior turn would have written it.
            assemble_context(self.question(0), self.retriever, self.hippocampus)
            self.state = NexusLoopState(messages=tuple(msgs), session_memory=tuple(
                (e.source, e.target, round(e.weight, 3), e.last_fired or time.time())
                for e in self.hippocampus.edges()))

    # -- the fixed text, pure functions of the turn number -------------------
    def question(self, t: int) -> str:
        base = f"turn {t}: {QUESTION_STEM}"
        return (base + " " * self.user_chars)[:self.user_chars]

    def answer(self, t: int) -> str:
        unit = " ".join(f"concept{i} improves outcome{i}." for i in range(1, 6)) + f" (answer {t}) "
        text = unit
        while len(text) < self.answer_chars:
            text += unit
        return text[:self.answer_chars]

    # -- one turn, captured ---------------------------------------------------
    def run_one(self, *, noop_tiers_1_to_3: bool = False) -> TurnRow:
        self.turn += 1
        captured: list = []
        real_cascade = loop_mod.run_cascade

        async def capturing_cascade(*a, **kw):
            res = await real_cascade(*a, **kw)
            captured.append(res)
            return res

        saved = (comp_mod.snip, comp_mod.microcompact, comp_mod.graph_collapse)
        loop_mod.run_cascade = capturing_cascade
        if noop_tiers_1_to_3:
            comp_mod.snip = lambda state, **kw: (state, 0)
            comp_mod.microcompact = lambda state, hippo, **kw: (state, 0)
            comp_mod.graph_collapse = lambda hippo: 0
        frames: list[str] = []
        try:
            async def drive():
                async for frame, st in run_turn(
                        self.state, self.question(self.turn), retriever=self.retriever,
                        hippocampus=self.hippocampus, hebbian=self.hebbian,
                        provider=self.provider, lineage=self.lineage):
                    frames.append(frame)
                    self.state = st
            # CHANGE A put shape A on the production history path. This rig
            # characterises the cascade over the history AS PLANTED (bare
            # answers of a chosen length; CE, 3 October), so it runs the loop
            # with the production wrapper OFF — the documented rollback — and
            # its pins stay what they measured. With the wrapper live every
            # assistant message costs the wrapper's bytes in the estimator and
            # an answer over HISTORY_ANSWER_CAP_CHARS is cut in history, so the
            # fire turns move; that is the wrapper's doing, not the cascade's.
            with production_wrap_off(), self.clocked():
                asyncio.run(drive())
        finally:
            loop_mod.run_cascade = real_cascade
            comp_mod.snip, comp_mod.microcompact, comp_mod.graph_collapse = saved
        done = next(json.loads(f.split("data: ", 1)[1].strip()) for f in reversed(frames)
                    if f.startswith("event: done\n"))
        assert len(captured) == 1, f"the cascade ran {len(captured)} times on one turn"
        res = captured[0]
        reached, reason = stop_reason_of(res)
        row = TurnRow(turn=self.turn,
                      est_before=res.events[0].tokens_before if res.events else res.final_tokens,
                      steps=[TierStep(e.tier.value, e.tokens_before, e.tokens_after, e.items_removed)
                             for e in res.events],
                      tier_reached=reached, stop_reason=reason, final_tokens=res.final_tokens,
                      messages_after=len(self.state.messages), lineage_id=done["lineage_id"],
                      cited=done["reinforcement"]["cited"])
        rec = self.lineage.get(done["lineage_id"]) if done["lineage_id"] else None
        row.injected_ids = tuple(sorted(i["belief_id"] for i in rec.injected)) if rec else ()
        self.rows.append(row)
        return row

    def run(self, turns: int, *, noop_at: int | None = None) -> list[TurnRow]:
        """`noop_at`: the one turn whose tiers 1–3 are no-op'd (S1.4 arm B)."""
        for _ in range(turns):
            self.run_one(noop_tiers_1_to_3=(self.turn + 1 == noop_at))
        return list(self.rows)

    def close(self) -> None:
        self.store.close()
        self.lineage.close()

    # -- S1.3: one traced turn ------------------------------------------------
    STAGE_FUNCS = {"assemble_context", "retrieve", "run_cascade", "assert_single_system_message",
                   "request_hash_of", "stream_chat", "extract_triples", "record_turn", "fire",
                   "update", "next_state", "sse_event", "snip", "microcompact", "graph_collapse",
                   "auto_compact"}

    def trace_turn(self) -> list[dict]:
        """Every call to a stage function during one turn, in execution order,
        with the function's defining module and line and the line it was called
        from. Collected with `sys.setprofile`; a resumed coroutine is one entry."""
        events: list[dict] = []

        def prof(frame, event, arg):
            if event != "call":
                return
            code = frame.f_code
            if code.co_name not in self.STAGE_FUNCS:
                return
            # A coroutine or generator raises a 'call' event on every resume;
            # only a fresh call starts on the function's own first line. (A
            # frame-id set is not safe here: ids are reused once a frame is
            # freed, and a later tier's call was dropped as a duplicate.)
            if frame.f_lineno != code.co_firstlineno:
                return
            mod = frame.f_globals.get("__name__", "")
            if not (mod.startswith("backend.") or code.co_name == "stream_chat"):
                return
            if code.co_name == "sse_event" and frame.f_locals.get("event") != "done":
                return
            if code.co_name == "next_state" and not (
                    "graph_context" in frame.f_locals.get("updates", {})
                    and frame.f_back.f_globals.get("__name__") == "backend.loop.agentic_loop"):
                return                      # stage 7's replacement, not a tier's own next_state
            if code.co_name == "update" and mod != "backend.cortex.hebbian":
                return
            if code.co_name == "fire" and mod != "backend.kb.tiered_store":
                return                      # node.fire() inside propagation is not reinforcement
            back = frame.f_back
            # The capture wrapper sits between the loop and `run_cascade`;
            # the call site reported is the production one, not the rig's.
            while back is not None and back.f_globals.get("__name__", "") == __name__:
                back = back.f_back
            events.append({"func": f"{mod}.{code.co_name}", "line": code.co_firstlineno,
                           "called_from": f"{back.f_globals.get('__name__', '?')}:{back.f_lineno}"})

        sys.setprofile(prof)
        try:
            self.run_one()
        finally:
            sys.setprofile(None)
        return events


__all__ = ["CERig", "RigClock", "RIG_CLOCK_BASE", "RIG_CLOCK_STEP", "HOST_CLOCK",
           "TurnRow", "TierStep", "stop_reason_of", "THRESHOLDS", "TIER_ORDER",
           "STOP_BELOW_T1", "STOP_AFTER_SNIP", "STOP_AFTER_MICRO", "STOP_AFTER_COLLAPSE", "STOP_TIER4_RAN"]
