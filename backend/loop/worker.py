"""Isolated worker context for tool execution and multi-agent orchestration.

Architecture:
  Planner spawns WorkerContext(s).
  Workers execute in isolation — they cannot read or write NexusLoopState.
  Only distill() output enters main context as a compact summary block.

Forward compatibility:
  - task_type field supports specialised workers (grader, file_op, code, search)
  - WorkerContextManager.spawn_team() enables multi-agent orchestration
  - cross_worker_patterns() surfaces recurring patterns across workers
    (matches Anthropic Dreaming's cross-session insight extraction)
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Optional


VALID_TASK_TYPES = frozenset({
    "general",     # default tool execution
    "file_op",     # Phase 10c: read/write/glob within sandbox
    "git_op",      # Phase 10d: clone/diff/commit
    "search",      # Phase 10b extension: web search worker
    "grader",      # Future: outcomes-style independent grader
    "code",        # Future: code generation/review worker
})


@dataclass(frozen=True)
class WorkerContext:
    task_id:    str
    task_desc:  str
    task_type:  str = "general"
    tool_calls: tuple = ()
    results:    tuple = ()
    errors:     tuple = ()
    turn_limit: int = 10
    turns_used: int = 0
    status:     str = "active"
    started_at: float = field(default_factory=time.time)
    parent_id:  Optional[str] = None

    def add_tool_call(self, tool: str, args: dict, result: Any) -> "WorkerContext":
        new_turns = self.turns_used + 1
        return replace(
            self,
            tool_calls=self.tool_calls + ((tool, args),),
            results=self.results + (result,),
            turns_used=new_turns,
            status="failed" if new_turns >= self.turn_limit else "active",
        )

    def add_error(self, error: str) -> "WorkerContext":
        return replace(self, errors=self.errors + (error,))

    def complete(self, summary: str) -> "WorkerContext":
        return replace(self, status="complete", results=self.results + (summary,))

    def abandon(self) -> "WorkerContext":
        return replace(self, status="abandoned")

    def distill(self) -> str:
        """Compact context block safe to inject into main state.

        Tool call logs and intermediate errors NOT included — only final
        results + status. This is the isolation boundary."""
        lines = [
            f"[WORKER {self.task_id} ({self.task_type}): {self.status.upper()}]"
        ]
        for r in self.results[-3:]:
            lines.append(f"  {r}")
        if self.errors:
            lines.append(f"  Errors: {len(self.errors)} (not shown)")
        return "\n".join(lines)


class WorkerContextManager:
    """Manages isolated worker contexts.

    Main loop interacts only via spawn() / spawn_team() / distill().
    Workers cannot read or write NexusLoopState directly — Law 1 boundary."""

    def __init__(self):
        self._workers: dict[str, WorkerContext] = {}
        self._teams: dict[str, list[str]] = {}

    # ---------- spawn / update ----------

    def spawn(
        self,
        task_desc: str,
        task_type: str = "general",
        turn_limit: int = 10,
        parent_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> WorkerContext:
        if task_type not in VALID_TASK_TYPES:
            raise ValueError(f"Unknown task_type: {task_type}")
        tid = task_id or f"w_{uuid.uuid4().hex[:8]}"
        w = WorkerContext(
            task_id=tid, task_desc=task_desc,
            task_type=task_type, turn_limit=turn_limit,
            parent_id=parent_id,
        )
        self._workers[tid] = w
        return w

    def spawn_team(
        self,
        team_desc: str,
        workers: list[dict],
        turn_limit: int = 10,
    ) -> tuple[str, list[WorkerContext]]:
        """Multi-agent orchestration entry point. Spawns multiple workers
        sharing a team_id. Each worker is independently isolated; the team
        groups them only for cross-worker pattern detection later."""
        team_id = f"team_{uuid.uuid4().hex[:8]}"
        spawned: list[WorkerContext] = []
        for w in workers:
            ctx = self.spawn(
                task_desc=w["task_desc"],
                task_type=w.get("task_type", "general"),
                turn_limit=turn_limit,
            )
            spawned.append(ctx)
        self._teams[team_id] = [c.task_id for c in spawned]
        return team_id, spawned

    def update(self, ctx: WorkerContext) -> WorkerContext:
        self._workers[ctx.task_id] = ctx
        return ctx

    def abandon(self, task_id: str) -> None:
        if task_id in self._workers:
            self._workers[task_id] = self._workers[task_id].abandon()

    # ---------- read ----------

    def get(self, task_id: str) -> Optional[WorkerContext]:
        return self._workers.get(task_id)

    def active_count(self) -> int:
        return sum(1 for w in self._workers.values() if w.status == "active")

    def distill(self, task_id: str) -> str:
        w = self._workers.get(task_id)
        return w.distill() if w else f"[WORKER {task_id}: NOT FOUND]"

    def distill_team(self, team_id: str) -> str:
        task_ids = self._teams.get(team_id, [])
        if not task_ids:
            return f"[TEAM {team_id}: NOT FOUND]"
        lines = [f"[TEAM {team_id}: {len(task_ids)} workers]"]
        for tid in task_ids:
            lines.append(self.distill(tid))
        return "\n".join(lines)

    def cross_worker_patterns(self, team_id: str) -> dict:
        """Forward-compat surface for multi-agent pattern detection.

        Returns recurring tool calls, common errors, status distribution
        across workers in a team. Used by future dreaming-style cross-
        session analysis."""
        task_ids = self._teams.get(team_id, [])
        workers = [
            self._workers[tid] for tid in task_ids if tid in self._workers
        ]
        if not workers:
            return {"team_id": team_id, "patterns": [], "worker_count": 0}

        tool_freq: dict[str, int] = {}
        error_count = 0
        statuses: list[str] = []
        for w in workers:
            for (tool, _args) in w.tool_calls:
                tool_freq[tool] = tool_freq.get(tool, 0) + 1
            error_count += len(w.errors)
            statuses.append(w.status)

        return {
            "team_id":             team_id,
            "worker_count":        len(workers),
            "common_tools":        sorted(tool_freq.items(),
                                          key=lambda kv: -kv[1])[:5],
            "total_errors":        error_count,
            "status_distribution": {s: statuses.count(s) for s in set(statuses)},
        }
