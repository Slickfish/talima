"""AX.1 — where curiosity runs: the idle cycle, not the turn.

THE DEFECT. `find_curious_nodes` was called on every turn (agentic_loop's
Stage 7-aux) while `NEXUS_KNOWLEDGE_PACK.md` §5 had already recorded the
decision that *"contradiction is reactive (write-time); curiosity is
proactive (idle/reflective pass)"*. The placement contradicted a written
decision, and the record was checked for a purpose the pack did not
anticipate before assuming drift: there is none. Phase 9 introduced the
call as one of four per-turn Stage-7 additions with no packet naming the
turn as its host, the only consumer is a side panel that appends cards,
and the per-turn position sat inside the unguarded window between `token`
and `done` that `tests/test_stream_truncation.py` recorded — one of the
two suspects named there, still unestablished.

COST is the second reason. Today's pass is O(nodes) over a graph already
in memory, sub-millisecond; but the claim-graph investigation measured
the shape a rebuilt pass would take — the sum over concepts of
degree-choose-two, 4.5 million pair checks at a hub of degree 3,000 — and
per turn does not survive that scale. The idle cycle already owns a
per-cycle budget and runs only when no turn is in flight.

WHAT SHIPS (`NEXUS_CURIOSITY_PLACEMENT=idle`, the default): the KAIROS
cycle runs the pass after decay and demotion have settled the graph it
reads, enqueues each question through the same `notify_ui` queue every
other idle-time signal uses, and the next `/chat` bridges it to the client
as `curiosity_surfaced` — the same frame, the same payload, arriving at
the next turn instead of during this one. The cycle result carries
`curiosity_surfaced` (a count) and `curiosity_top` (the node ids), `None`
on an aborted cycle, so a pass that produced nothing is distinguishable
from one that did not run (I-6).

`NEXUS_CURIOSITY_PLACEMENT=turn` is the rollback: the per-turn call and
frame exactly as before, and the idle cycle surfaces nothing.

THE GUARD is derived, not listed: no file under `backend/loop/` may call
`find_curious_nodes`. The two functions below are the only callers on
either path; each is a no-op in the other mode, so the mode decides which
host runs the pass and the loop itself never does.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.cortex.curiosity import CuriosityEngine, CuriousNode
    from backend.memory.hippocampus import Hippocampus

PLACEMENTS = ("idle", "turn")
DEFAULT_TOP_K = 3


def curiosity_placement() -> str:
    """`idle` (default) or `turn`. Read at call time (the E.2 pattern)."""
    raw = os.environ.get("NEXUS_CURIOSITY_PLACEMENT", "idle").strip().lower()
    return "turn" if raw == "turn" else "idle"


def turn_curiosity(curiosity: "CuriosityEngine | None",
                   hippocampus: "Hippocampus | None",
                   *, top_k: int = DEFAULT_TOP_K) -> "list[CuriousNode]":
    """The per-turn pass — the rollback host. Empty unless placement is `turn`."""
    if curiosity is None or hippocampus is None or curiosity_placement() != "turn":
        return []
    return curiosity.find_curious_nodes(hippocampus, top_k=top_k)


def idle_curiosity(curiosity: "CuriosityEngine | None",
                   hippocampus: "Hippocampus | None",
                   *, top_k: int = DEFAULT_TOP_K) -> "list[CuriousNode]":
    """The idle-cycle pass — the shipped host. Empty unless placement is `idle`."""
    if curiosity is None or hippocampus is None or curiosity_placement() != "idle":
        return []
    return curiosity.find_curious_nodes(hippocampus, top_k=top_k)
