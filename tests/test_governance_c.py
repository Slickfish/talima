"""Pre-Phase-10c Governance Fix C — Execution Isolation + Multi-agent
Forward Compatibility.

Spec contract under test:
  - WorkerContext is frozen (Law 1 — replace returns new object)
  - add_tool_call appends + increments turns_used
  - status becomes "failed" when turns_used reaches turn_limit
  - distill() excludes raw tool_calls log; includes last 3 results +
    task_type header + error count (not content)
  - spawn() with valid task_type creates isolated context
  - spawn() with invalid task_type raises ValueError
  - spawn() generates unique task_id when not provided
  - update() / abandon() / active_count() correctness
  - Two workers with different task_ids are fully independent
  - distill output is plain string (no object refs)
  - spawn_team() / distill_team() / cross_worker_patterns() forward compat
  - /workers/active returns active-only list
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.loop.worker import (
    VALID_TASK_TYPES,
    WorkerContext,
    WorkerContextManager,
)
from backend.server import create_app, create_services


# --------------- WorkerContext frozen + Law 1 ---------------

def test_worker_context_is_frozen():
    w = WorkerContext(task_id="x", task_desc="d")
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.status = "complete"   # type: ignore[misc]


def test_add_tool_call_returns_new_object():
    w = WorkerContext(task_id="x", task_desc="d")
    w2 = w.add_tool_call("file_read", {"path": "x"}, "result")
    assert w2 is not w
    assert w.turns_used == 0   # original unchanged
    assert w2.turns_used == 1
    assert len(w2.tool_calls) == 1


def test_add_tool_call_marks_failed_at_turn_limit():
    w = WorkerContext(task_id="x", task_desc="d", turn_limit=2)
    w = w.add_tool_call("a", {}, "r1")
    assert w.status == "active"
    w = w.add_tool_call("b", {}, "r2")
    assert w.status == "failed"
    assert w.turns_used == 2


def test_complete_returns_status_complete():
    w = WorkerContext(task_id="x", task_desc="d")
    w2 = w.complete("final summary")
    assert w2.status == "complete"
    assert "final summary" in w2.results


def test_abandon_returns_status_abandoned():
    w = WorkerContext(task_id="x", task_desc="d")
    assert w.abandon().status == "abandoned"


# --------------- distill() — isolation boundary ---------------

def test_distill_excludes_tool_calls_log():
    w = WorkerContext(task_id="abc", task_desc="d", task_type="file_op")
    w = w.add_tool_call("file_read", {"path": "/secret"}, "snippet")
    out = w.distill()
    assert "/secret" not in out
    assert "file_read" not in out


def test_distill_includes_last_3_results_only():
    w = WorkerContext(task_id="x", task_desc="d", turn_limit=10)
    for i in range(5):
        w = w.add_tool_call("t", {}, f"result_{i}")
    out = w.distill()
    # Last 3 results: 2, 3, 4
    assert "result_2" in out
    assert "result_3" in out
    assert "result_4" in out
    # First two not shown
    assert "result_0" not in out
    assert "result_1" not in out


def test_distill_header_includes_task_type():
    w = WorkerContext(task_id="abc", task_desc="d", task_type="grader")
    assert "(grader)" in w.distill()


def test_distill_includes_error_count_not_content():
    w = WorkerContext(task_id="x", task_desc="d")
    w = w.add_error("secret stack trace")
    w = w.add_error("another error")
    out = w.distill()
    assert "Errors: 2" in out
    assert "secret stack trace" not in out


def test_distill_returns_plain_string():
    w = WorkerContext(task_id="x", task_desc="d")
    assert isinstance(w.distill(), str)


# --------------- WorkerContextManager basics ---------------

def test_spawn_with_valid_task_type():
    m = WorkerContextManager()
    w = m.spawn("read config.yaml", task_type="file_op")
    assert w.task_type == "file_op"
    assert w.status == "active"


def test_spawn_with_invalid_task_type_raises():
    m = WorkerContextManager()
    with pytest.raises(ValueError, match="Unknown task_type"):
        m.spawn("anything", task_type="exfiltration")


def test_spawn_generates_unique_task_id():
    m = WorkerContextManager()
    a = m.spawn("a"); b = m.spawn("b")
    assert a.task_id != b.task_id
    assert a.task_id.startswith("w_")


def test_update_replaces_atomically():
    m = WorkerContextManager()
    w = m.spawn("d")
    w2 = w.complete("done")
    m.update(w2)
    assert m.get(w.task_id).status == "complete"


def test_abandon_marks_status_abandoned():
    m = WorkerContextManager()
    w = m.spawn("d")
    m.abandon(w.task_id)
    assert m.get(w.task_id).status == "abandoned"


def test_active_count_correct_across_lifecycle():
    m = WorkerContextManager()
    a = m.spawn("a"); b = m.spawn("b"); c = m.spawn("c")
    assert m.active_count() == 3
    m.update(a.complete("done"))
    m.abandon(b.task_id)
    assert m.active_count() == 1


def test_distill_for_unknown_task_id():
    m = WorkerContextManager()
    out = m.distill("does_not_exist")
    assert "NOT FOUND" in out


# --------------- Isolation between workers ---------------

def test_two_workers_are_independent():
    m = WorkerContextManager()
    a = m.spawn("a"); b = m.spawn("b")
    a2 = a.add_tool_call("t1", {}, "r_a")
    m.update(a2)
    # b is unchanged
    assert m.get(b.task_id).turns_used == 0
    assert m.get(b.task_id).tool_calls == ()


def test_worker_state_does_not_appear_in_NexusLoopState_fields():
    """No write path from WorkerContext → NexusLoopState. Pin via
    field-name introspection: NexusLoopState fields are state slots, not
    a worker_id reference."""
    from backend.loop.state import NexusLoopState
    state_fields = {f.name for f in dataclasses.fields(NexusLoopState)}
    # No field name suggests worker writes (e.g. tool_results, worker_log)
    assert not any("tool_call" in n for n in state_fields)
    assert not any("worker_log" in n for n in state_fields)


# --------------- Multi-agent forward compat ---------------

def test_spawn_team_creates_team_and_workers():
    m = WorkerContextManager()
    team_id, workers = m.spawn_team(
        "review pr 42",
        workers=[
            {"task_desc": "read diff", "task_type": "file_op"},
            {"task_desc": "grade quality", "task_type": "grader"},
        ],
    )
    assert team_id.startswith("team_")
    assert len(workers) == 2
    assert workers[0].task_type == "file_op"
    assert workers[1].task_type == "grader"


def test_distill_team_concatenates_workers():
    m = WorkerContextManager()
    team_id, _ = m.spawn_team("t", [
        {"task_desc": "a", "task_type": "general"},
        {"task_desc": "b", "task_type": "general"},
    ])
    out = m.distill_team(team_id)
    assert "TEAM" in out
    assert out.count("[WORKER") == 2


def test_distill_team_unknown_id():
    out = WorkerContextManager().distill_team("missing")
    assert "NOT FOUND" in out


def test_cross_worker_patterns_returns_tool_freq_and_errors():
    m = WorkerContextManager()
    team_id, workers = m.spawn_team("t", [
        {"task_desc": "a"}, {"task_desc": "b"},
    ])
    a = workers[0].add_tool_call("file_read", {}, "ok")
    a = a.add_tool_call("file_read", {}, "ok")
    a = a.add_error("oops")
    m.update(a)
    b = workers[1].add_tool_call("file_read", {}, "ok").add_tool_call("glob", {}, "ok")
    m.update(b)
    p = m.cross_worker_patterns(team_id)
    assert p["worker_count"] == 2
    assert p["total_errors"] == 1
    common = dict(p["common_tools"])
    assert common["file_read"] == 3
    assert common["glob"] == 1


def test_cross_worker_patterns_handles_empty_team():
    p = WorkerContextManager().cross_worker_patterns("does_not_exist")
    assert p["worker_count"] == 0


def test_workers_in_same_team_have_independent_state():
    m = WorkerContextManager()
    _, workers = m.spawn_team("t", [
        {"task_desc": "a"}, {"task_desc": "b"},
    ])
    # Mutate one
    m.update(workers[0].complete("done"))
    assert m.get(workers[0].task_id).status == "complete"
    assert m.get(workers[1].task_id).status == "active"


# --------------- VALID_TASK_TYPES surface ---------------

def test_valid_task_types_includes_phase_10c_set():
    """Phase 10c will spawn file_op + git_op + search workers."""
    for required in ("general", "file_op", "git_op", "search", "grader", "code"):
        assert required in VALID_TASK_TYPES


# --------------- /workers/active endpoint ---------------

@pytest.fixture
def app_services(tmp_path: Path):
    services = create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "ch",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snaps",
    )
    yield services
    services.store.close(); services.neocortex.close()
    services.archive_chain.close()


def test_workers_active_endpoint_lists_active_only(app_services):
    """Spawn 3 workers; complete one; abandon another; endpoint returns 1."""
    wm = app_services.worker_manager
    a = wm.spawn("a")
    b = wm.spawn("b")
    c = wm.spawn("c")
    wm.update(a.complete("done"))
    wm.abandon(b.task_id)
    app = create_app(app_services)
    with TestClient(app) as client:
        r = client.get("/workers/active")
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == 1
    assert len(body["workers"]) == 1
    assert body["workers"][0]["task_id"] == c.task_id
