"""WC S1 item 2 — the extraction window's CONSEQUENCE at the store.

C-38's T4 already shows the INPUT is cut: a 5,000-character answer reaches
extraction as its first 4,000 characters, head first, and the tail marker is
absent from the extraction prompt. WC shows what that costs at the store: a
distinctive claim carried in the answer's last sentence, past the window,
admits NO triple — the claim was silently subtracted before extraction ever
saw it.

The extraction stub here is a faithful model of extraction, not a rubber
stamp: it returns the distinctive triple IFF the claim's own text is in the
prompt it was handed (which is `answer[:EXTRACTION_MAX_CHARS]`). So the
consequence is the window's doing, not the stub's — proven by the companion,
where the SAME stub and the SAME claim, in a SHORT answer, do admit the
triple. Nothing but the answer's length differs between the two.

PLATFORM is stamped on every printed line (sys.platform).
"""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

from backend.cortex.extraction import EXTRACTION_MAX_CHARS
from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.loop.agentic_loop import run_turn
from backend.loop.state import NexusLoopState
from backend.memory.lineage import LineageStore
from backend.kb.tiered_store import TripleStore

# The distinctive claim: a subject that appears nowhere else in the store or
# the question, so a hit on it can only have come from this sentence.
DISTINCT_SUBJECT = "wolffia arrhiza"
DISTINCT_SENTENCE = f"The {DISTINCT_SUBJECT} is the smallest flowering plant on earth."
DISTINCT_TRIPLE = {"subject": DISTINCT_SUBJECT, "relation": "is",
                   "object": "the smallest flowering plant", "confidence": 0.9}


class _Chat:
    """Answer in one piece, then a faithful extraction arm: it yields the
    distinctive triple only when the claim's sentence is in the prompt it was
    handed, and `[]` otherwise. `extraction_prompts` records what extraction
    actually received, so the test can show the window cut the input."""

    def __init__(self, answer: str):
        self.answer = answer
        self.calls = 0
        self.extraction_prompts: list[str] = []

    async def stream_chat(self, model, messages, num_ctx):
        self.calls += 1
        if self.calls % 2 == 1:
            yield self.answer
        else:
            prompt = messages[-1]["content"]
            self.extraction_prompts.append(prompt)
            yield json.dumps([DISTINCT_TRIPLE]) if DISTINCT_SENTENCE in prompt else "[]"

    async def get_embedding(self, t): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def _turn(store, lineage, answer: str):
    provider = _Chat(answer)
    frames, state = [], NexusLoopState()

    async def drive():
        nonlocal state
        async for f, s in run_turn(state, "tell me about small plants",
                                   retriever=TripleRetriever(store), provider=provider,
                                   lineage=lineage):
            frames.append(f)
            state = s
    asyncio.run(drive())
    done = next(json.loads(f.split("data: ", 1)[1].strip())
                for f in reversed(frames) if f.startswith("event: done\n"))
    return done, provider


def _admitted(store) -> bool:
    return bool(store.search_terms([DISTINCT_SUBJECT]))


def test_a_claim_past_the_window_admits_no_triple(tmp_path):
    """The consequence: a 5,000-character answer whose LAST sentence carries
    the distinctive claim admits no triple from it, because extraction read
    only the first 4,000 characters and the sentence was past the cut."""
    pad = "Many plants are small. " * 300
    answer = (pad + " " + DISTINCT_SENTENCE)
    assert len(answer) > 5000 and answer.rfind(DISTINCT_SENTENCE) > EXTRACTION_MAX_CHARS, \
        "the plant must place the claim past the window"
    store = TripleStore(tmp_path / "kb.db")
    lineage = LineageStore(tmp_path / "kb.db")
    done, provider = _turn(store, lineage, answer)
    cut_prompt = provider.extraction_prompts[-1]
    print(f"\nWC-2 ({sys.platform}) answer {len(answer)} chars, claim at "
          f"{answer.rfind(DISTINCT_SENTENCE)} (window {EXTRACTION_MAX_CHARS}); "
          f"extraction_input_truncated={done['extraction_input_truncated']}; "
          f"claim admitted={_admitted(store)}")
    # The input was cut (C-38's fact, restated at the store's doorstep)...
    assert done["extraction_input_truncated"] is True
    assert done["extraction_input_chars"] == EXTRACTION_MAX_CHARS
    assert DISTINCT_SENTENCE not in cut_prompt, "the claim's sentence reached extraction after all"
    # ...and the CONSEQUENCE: nothing from that sentence is in the store.
    assert not _admitted(store), "a claim past the window was admitted — the window did not cut it"
    assert done["extraction_gate"]["extracted"] == 0
    store.close(); lineage.close()


def test_the_same_claim_within_the_window_is_admitted(tmp_path):
    """Non-vacuity / the control: the SAME stub and the SAME claim, in a SHORT
    answer, DO admit the triple — so the miss above is the window's doing and
    nothing else. Only the answer's length differs."""
    answer = f"Here is a fact. {DISTINCT_SENTENCE}"
    assert len(answer) < EXTRACTION_MAX_CHARS
    store = TripleStore(tmp_path / "kb.db")
    lineage = LineageStore(tmp_path / "kb.db")
    done, provider = _turn(store, lineage, answer)
    print(f"\nWC-2-control ({sys.platform}) answer {len(answer)} chars; "
          f"truncated={done['extraction_input_truncated']}; claim admitted={_admitted(store)}")
    assert done["extraction_input_truncated"] is False
    assert DISTINCT_SENTENCE in provider.extraction_prompts[-1]
    assert _admitted(store), "the stub could not extract the claim even within the window — test is vacuous"
    hits = store.search_terms([DISTINCT_SUBJECT])
    assert any(h.subject == DISTINCT_SUBJECT for h in hits)
    store.close(); lineage.close()
