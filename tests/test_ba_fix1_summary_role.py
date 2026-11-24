"""BA fix 1 — the compaction summary leaves system role.

THE FINDING (compaction boundary audit, A1b): tier 4 wrote the summary as
`{"role": "system", ...}` at the head of history. With no summariser wired
it was the count stub; with one wired, model prose would carry system
authority — the September 2026 shape, built in by design.

THE FIX: the summary is an `assistant`-role message inside a runtime-written
delimiter carrying `source="compaction"`; the delimiter is a markup tag the
gate refuses in any term, so no claim can carry it into a prompt. The role
is argued in compaction.py beside SUMMARY_ROLE.

THE GUARD, static and derived: over every tracked file under backend/loop,
every dict literal with role == "system" is enumerated; exactly one may
exist, at prompt assembly, and its content must be the assembled
`system_content` name — nothing that flows from a Summariser. The runtime
companion: the request carries exactly one system message, first,
prefix-led. Each has a must-fail: the checker flags a hand-built
system-role construction fed from a summariser, and the assertion refuses
a request with the BREACH ALERT item planted as a second system message.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

import backend.loop.compaction as comp
from backend.kb.gate import inspect_term
from backend.kb.retriever import TripleRetriever
from backend.loop.agentic_loop import STABLE_SYSTEM_PREFIX, run_turn
from backend.loop.compaction import (
    COMPACTION_BLOCK_CLOSE, COMPACTION_BLOCK_OPEN, SUMMARY_ROLE, auto_compact,
    assert_single_system_message, compaction_block, run_cascade,
)
from backend.loop.state import NexusLoopState
from backend.memory.hippocampus import Hippocampus
from tests._corpus import CORPUS, CORPUS_SHA256
from tests._tracked import tracked_backend
from tests.test_aw_prompt_render import _rig

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# The static guard, and its checker as a function so the must-fail can use it
# ---------------------------------------------------------------------------

def system_role_dicts(source: str) -> list[tuple[int, str]]:
    """(line, content-expression) for every dict literal with role == 'system'."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
        if "role" not in keys:
            continue
        role = node.values[keys.index("role")]
        if isinstance(role, ast.Constant) and role.value == "system":
            content = node.values[keys.index("content")] if "content" in keys else None
            out.append((node.lineno, ast.dump(content) if content is not None else ""))
    return out


def test_no_system_role_message_is_built_from_a_summary():
    sites = {}
    for path in tracked_backend():
        if "loop" not in path.parts:
            continue
        found = system_role_dicts(path.read_text(encoding="utf-8"))
        if found:
            sites[path.name] = found
    assert list(sites) == ["agentic_loop.py"], (
        f"system-role messages are constructed in {sorted(sites)}; only prompt "
        f"assembly in agentic_loop.py may build one")
    assert len(sites["agentic_loop.py"]) == 1
    _line, content = sites["agentic_loop.py"][0]
    assert content == ast.dump(ast.Name(id="system_content", ctx=ast.Load())), (
        "the one system-role message must carry the assembled system_content, "
        f"not {content}")


def test_the_static_checker_catches_a_summary_placed_in_system_role():
    """Must-fail companion: a hand-built compaction that puts the summariser's
    text in a system-role dict is exactly what the checker exists to flag."""
    snippet = (
        "async def bad_compact(state, summariser):\n"
        "    summary_text = await summariser(state.messages)\n"
        "    return {'role': 'system', 'content': '[CONVERSATION SUMMARY]\\n' + summary_text}\n")
    found = system_role_dicts(snippet)
    assert len(found) == 1 and "summary_text" in found[0][1]


# ---------------------------------------------------------------------------
# The runtime assertion, and its must-fail with the corpus item
# ---------------------------------------------------------------------------

def test_the_request_carries_exactly_one_prefix_led_system_message():
    good = [{"role": "system", "content": STABLE_SYSTEM_PREFIX + "\n\nblock"},
            {"role": "user", "content": "q"}]
    assert_single_system_message(good, STABLE_SYSTEM_PREFIX)


def test_a_second_system_message_is_refused_at_assembly():
    """Must-fail: the pre-fix shape, with the BREACH ALERT item as the summary
    (corpus sha256 %s)."""
    bad = [{"role": "system", "content": STABLE_SYSTEM_PREFIX},
           {"role": "system", "content": "[CONVERSATION SUMMARY]\n" + CORPUS["authority"]},
           {"role": "user", "content": "q"}]
    with pytest.raises(RuntimeError, match="system-role messages at \\[0, 1\\]"):
        assert_single_system_message(bad, STABLE_SYSTEM_PREFIX)
    with pytest.raises(RuntimeError):
        assert_single_system_message([{"role": "user", "content": "q"}], STABLE_SYSTEM_PREFIX)
    with pytest.raises(RuntimeError):
        assert_single_system_message([{"role": "system", "content": "not the prefix"}],
                                     STABLE_SYSTEM_PREFIX)


test_a_second_system_message_is_refused_at_assembly.__doc__ %= CORPUS_SHA256


# ---------------------------------------------------------------------------
# The container: role, delimiter, position; the delimiter is gate-refused
# ---------------------------------------------------------------------------

def test_the_summary_is_an_assistant_block_at_the_head_of_history():
    async def summariser(messages):
        return CORPUS["authority"]
    state = NexusLoopState(messages=(
        {"role": "user", "content": "first"}, {"role": "assistant", "content": "one"},
        {"role": "user", "content": "second"}, {"role": "assistant", "content": "two"}))
    new, removed = asyncio.run(auto_compact(state, summariser=summariser))
    assert removed == 1                     # 4 -> [summary, user, assistant]
    assert [m["role"] for m in new.messages] == [SUMMARY_ROLE, "user", "assistant"]
    assert SUMMARY_ROLE == "assistant"
    block = new.messages[0]["content"]
    assert block.startswith(COMPACTION_BLOCK_OPEN) and block.endswith(COMPACTION_BLOCK_CLOSE)
    assert CORPUS["authority"] in block
    assert not any(m["role"] == "system" for m in new.messages)


def test_the_delimiter_is_refused_by_the_gate_in_every_evidence_class():
    """No claim term can carry the opening or closing tag into a prompt."""
    for tag in (COMPACTION_BLOCK_OPEN, COMPACTION_BLOCK_CLOSE, f"x {COMPACTION_BLOCK_OPEN} y"):
        for cls in ("asserted", "external", "extraction"):
            assert inspect_term(tag, evidence_class=cls) == "markup", (tag, cls)


def test_the_stub_text_is_unchanged_only_its_container():
    state = NexusLoopState(messages=({"role": "user", "content": "a"},
                                     {"role": "assistant", "content": "b"},
                                     {"role": "user", "content": "c"}))
    new, _ = asyncio.run(auto_compact(state))
    assert "[Auto-summary stub: 2 user / 1 assistant turns compacted.]" in new.messages[0]["content"]
    assert new.messages[0]["content"] == compaction_block(
        "[Auto-summary stub: 2 user / 1 assistant turns compacted.]")


# ---------------------------------------------------------------------------
# End to end: tier 4 forced on a real turn, the next request inspected
# ---------------------------------------------------------------------------

class _Capture:
    def __init__(self, answer):
        self.answer, self.calls, self.requests = answer, 0, []

    async def stream_chat(self, model, messages, num_ctx):
        self.calls += 1
        if self.calls % 2 == 1:
            self.requests.append([dict(m) for m in messages])
            yield self.answer
        else:
            yield "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def test_with_tier_four_forced_the_next_request_has_one_system_message(tmp_path, monkeypatch):
    for name in ("TIER_1_SNIP_AT", "TIER_2_MICRO_AT", "TIER_3_COLLAPSE_AT", "TIER_4_AUTO_AT"):
        monkeypatch.setattr(comp, name, 0)
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)

    async def summariser(messages):
        return CORPUS["authority"]
    prov = _Capture("Interval training improves vo2 max.")
    state = NexusLoopState()

    async def turn(q):
        nonlocal state
        async for _f, st in run_turn(state, q, retriever=TripleRetriever(store),
                                     hippocampus=hippo, provider=prov, summariser=summariser):
            state = st
    asyncio.run(turn("what does training improve?"))
    asyncio.run(turn("and what about recovery?"))
    req = prov.requests[1]
    roles = [m["role"] for m in req]
    assert roles.count("system") == 1 and roles[0] == "system"
    assert req[0]["content"].startswith(STABLE_SYSTEM_PREFIX)
    summary = [m for m in req if COMPACTION_BLOCK_OPEN in m["content"]]
    assert summary and summary[0]["role"] == "assistant"
    assert CORPUS["authority"] in summary[0]["content"]
    assert CORPUS["authority"] not in req[0]["content"]
    store.close(); lineage.close()
