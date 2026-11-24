"""BD-3 — the FAIL tier and the marked-state flag are retired; what survives
is a WARN-only report, live at tier 2.

THE GUARD. Nothing under `backend/` may assign to, read, compare or branch
on a state attribute named `corrupted` — the flag the constitution once
promised would refuse the next turn, which E.5 found no rule could set in
production and B-24 found nothing read. This is the guard against the halt
being rebuilt half-way by someone who remembers the old sentence: trigger
and consequence come back together in one commit, or not at all.

THE COMPANION plants a module that branches on `state.corrupted` in an `if`,
under a path the checker is given explicitly (the tracked-backend default is
what the guard enumerates; a planted fixture is invisible to it BY
CONSTRUCTION, so the checker takes a file list, the derived-writer guard's
shape). The guard must name the file and the line.

THE BEHAVIOUR THAT SURVIVES. With micro forced on the synthetic rig, the
integrity report carries `concept_completely_removed` and
`minority_loss_excess` at WARN, the turn continues, and `done` is emitted —
the consequence (a completed turn), not a flag.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import backend.loop.compaction as comp
from backend.kb.retriever import TripleRetriever
from backend.loop.agentic_loop import run_turn
from backend.loop.state import NexusLoopState
from tests._tracked import tracked_backend
from tests.test_aw_prompt_render import _rig

ROOT = Path(__file__).resolve().parent.parent
RETIRED_ATTRIBUTE = "corrupted"


def retired_flag_sites(files: "list[Path] | None" = None) -> list[str]:
    """`file:line` of every Attribute named after the retired flag that is
    assigned, compared, branched on or returned, in `files` (default: the
    tracked backend)."""
    out = []
    for f in (files if files is not None else tracked_backend()):
        f = Path(f)
        tree = ast.parse(f.read_text(encoding="utf-8"))
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and node.attr == RETIRED_ATTRIBUTE):
                continue
            n = node
            while n in parents:
                n = parents[n]
                if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Compare,
                                  ast.If, ast.IfExp, ast.While, ast.Return, ast.BoolOp)):
                    rel = f.relative_to(ROOT).as_posix() if f.is_relative_to(ROOT) else f.as_posix()
                    out.append(f"{rel}:{node.lineno}")
                    break
                if isinstance(n, ast.stmt):
                    break
    return out


def test_no_backend_code_reads_or_writes_the_retired_flag():
    sites = retired_flag_sites()
    assert not sites, (
        "the retired halt flag is back in control flow at:\n  " + "\n  ".join(sites)
        + "\n\nBD-3 retired the FAIL tier and the marked state because no rule could set "
          "it and nothing read it. A halt returns only with a live severe-tier rule in "
          "the same commit — trigger and consequence together.")


def test_the_guard_names_a_planted_branch_on_the_flag(tmp_path):
    """Must-fail companion. The planted module is under a path the checker
    is handed, not under the tracked backend; the name it branches on is the
    retired attribute, reached through a state object the guard does not
    enumerate."""
    planted = tmp_path / "loop_fixture.py"
    planted.write_text(
        "def next_turn(session):\n"
        "    verdict = session.report\n"
        "    if session.corrupted:\n"
        "        return None\n"
        "    return verdict\n", encoding="utf-8")
    sites = retired_flag_sites([planted])
    assert sites == [f"{planted.as_posix()}:3"], sites
    # And the detector is not fooled by the word in prose or in a string.
    clean = tmp_path / "clean_fixture.py"
    clean.write_text('NOTE = "the corrupted state was retired"\n'
                     "def f(x):\n    return x.report\n", encoding="utf-8")
    assert retired_flag_sites([clean]) == []


class _Provider:
    def __init__(self):
        self.calls = 0

    async def stream_chat(self, model, messages, num_ctx):
        self.calls += 1
        yield "Training improves vo2 max." if self.calls % 2 == 1 else "[]"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def test_a_warn_at_tier_two_is_reported_and_the_turn_completes(tmp_path, monkeypatch):
    """The WARN half is live, and its consequence is nothing: the report says
    WARN, the turn runs to `done`."""
    store, hippo, lineage, _ids = _rig(tmp_path, monkeypatch)
    # Force micro to fire on this turn: enough history to pass the tier-2
    # threshold, and graph_context rows with no live edge behind them.
    answer = ("Interval training improves several markers of fitness over weeks. " * 12)[:800]
    msgs = []
    for i in range(32):
        msgs += [{"role": "user", "content": f"question {i} about training and recovery"},
                 {"role": "assistant", "content": answer}]
    state = NexusLoopState(messages=tuple(msgs),
                           graph_context=(("orphan_a", "rel", "orphan_b"),
                                          ("orphan_c", "rel", "orphan_d")),
                           turn_count=32)
    assert comp.estimate_tokens(state) > comp.TIER_2_MICRO_AT, "the fixture must reach tier 2"
    frames = []

    async def drive():
        async for f, _s in run_turn(state, "what does training improve?",
                                    retriever=TripleRetriever(store), hippocampus=hippo,
                                    provider=_Provider(), lineage=lineage):
            frames.append(f)
    asyncio.run(drive())
    reports = [json.loads(f.split("data: ", 1)[1].strip())
               for f in frames if f.startswith("event: compaction_report\n")]
    fired = [json.loads(f.split("data: ", 1)[1].strip())["tier"]
             for f in frames if f.startswith("event: compaction_fired\n")]
    assert "microcompact" in fired, fired
    micro = reports[fired.index("microcompact")]
    assert micro["pass_fail"] == "WARN"
    names = {v["invariant"] for v in micro["violations"]}
    assert {"concept_completely_removed", "minority_loss_excess"} <= names, names
    assert all(v["severity"] == "WARN" for r in reports for v in r["violations"])
    assert not any(r.get("halted") for r in reports), "no halted frame exists any more"
    # THE CONSEQUENCE: the turn completed.
    assert frames[-1].startswith("event: done\n"), frames[-1][:60]
    store.close(); lineage.close()
