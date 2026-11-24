"""W6 part 1 — the recorder fails closed (run/specs/W6.md, D1 to D6).

WHAT WAS WRONG
    The console released each answer frame while the model wrote it and wrote the record afterwards, and a
    `record_turn` that raised was logged while the turn went on with `lineage_id: null`. Under MCP the same failure
    was counted `recorded` and returned as a success. So an answer could leave the process with no record of it,
    and no surface said so.

WHAT IS HELD HERE
    R1  A record write planted to raise: under the default release the console receives no frame built from the
        answer, one `record_failed`, no `done`; the session's state is what it was; the event ring holds no frame
        of that answer; the error's text is not on the wire. Fails on the code before W6.
    R2  When the write succeeds the frames are the same held or live, in the same order.
    R3  `live` (the rollback) releases the answer as it arrives and still declares a failed write.
    R4  The setting: only `live` is live; any other value is the closed position, and an unknown one is named.
    R5  A loop composed with no recorder is not a failed recorder; the server and MCP always compose one.
    R6  MCP: a failed write is a failure by name, counted `record_failed` and never `recorded`, and the retrieval
        stays open so the same id records on a second attempt.
    R7  A guard over `complete_turn`'s source: it yields nothing above the record step (the frames it used to
        yield there go out right after the write). Companion: a planted frame above the record step is named.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import dataclasses
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.loop import agentic_loop as loop
from backend.loop.state import NexusLoopState
from backend.mcp import coverage as cov
from backend.mcp.mcp_server import MCP_CONTRACT_VERSION, NexusRuntime, build_server
from backend.server import create_app, create_services
from tests._tracked import REPO
from tests.fixtures.ce_rig import RigClock

sys.path.insert(0, str(REPO / "scripts"))
import history_shape_harness as hsh  # noqa: E402

LOOP_SRC = REPO / "backend" / "loop" / "agentic_loop.py"
QUESTION = "what does interval training improve?"
EXTRACTED = json.dumps([{"subject": "interval training", "relation": "improves", "object": "aerobic capacity",
                         "confidence": 0.9}])
# The frames built from the answer before the record step: the answer itself and what extraction read out of it.
ANSWER_FRAMES = {"token", "triples_extracted", "contradiction_found"}
# Every frame a turn whose record failed must not have put through the event ring: the answer's, and the ones
# built after the record step (the evaluator's, the prediction error, the Hebbian update, done).
AFTER_THE_RECORD = ANSWER_FRAMES | {"eval_metric", "prediction_error", "hebbian_update", "done"}
PLANTED_TEXT = "PLANTED-SECRET-TEXT: the disk is full"


def _frames(resp) -> list[tuple[str, object]]:
    out = []
    for frame in resp.text.split("\n\n"):
        if frame.startswith("event: "):
            head, _, data = frame.partition("\ndata: ")
            out.append((head.removeprefix("event: "), json.loads(data) if data else None))
    return out


def _console(root: Path):
    """A console on the harness's fixture store, with its stub answering and extracting one claim per turn."""
    hsh._build_store(root / "kb.db").close()
    svc = create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma", eval_log_path=root / "metrics.jsonl",
                          archive_db_path=root / "archive.db", snapshots_dir=root / "snapshots",
                          provider=hsh._Stub(plant=None, extract=lambda _prompt: EXTRACTED))
    return svc, TestClient(create_app(svc)), {"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"}


def _turn(client, bearer, message: str = QUESTION):
    resp = client.post("/chat", headers=bearer, json={"message": message})
    assert resp.status_code == 200, resp.text
    return _frames(resp), resp.text


def _break(recorder, monkeypatch) -> None:
    def refuse(**_kw):
        raise OSError(PLANTED_TEXT)
    monkeypatch.setattr(recorder, "record_turn", refuse)


def _ring(client, bearer) -> list[str]:
    events = client.get("/events/recent", headers=bearer).json()
    events = events.get("events", events) if isinstance(events, dict) else events
    return [e.get("event") or e.get("name") for e in events]


def _ring_tokens(client, bearer) -> int:
    return sum(1 for name in _ring(client, bearer) if name == "token")


def _calibration_rows(mechanism: str) -> int:
    from backend.eval.calibration_log import read_entries
    return sum(1 for e in read_entries() if e.get("mechanism") == mechanism)


# ---------------------------------------------------------------------------
# R1 — a failed write releases no answer, and is declared
# ---------------------------------------------------------------------------

def test_r1_a_failed_write_releases_no_frame_of_the_answer_and_is_declared(tmp_path, monkeypatch):
    monkeypatch.delenv(loop.ANSWER_RELEASE_SETTING, raising=False)
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        good, _ = _turn(client, bearer)
        names = [e for e, _d in good]
        assert "token" in names and "triples_extracted" in names and names[-1] == "done", names
        assert good[-1][1]["lineage_id"], "the population: a turn whose record is written ends in done with its id"
        state_before, ring_before = svc.state, _ring_tokens(client, bearer)
        ring_names_before = _ring(client, bearer)
        failures_before = loop.recorder_declaration()["record_failures_since_start"]

        _break(svc.lineage, monkeypatch)
        failed, wire = _turn(client, bearer, "and what does sleep improve?")
        names = [e for e, _d in failed]
        assert not (set(names) & ANSWER_FRAMES), f"a frame of an answer with no record was released: {names}"
        assert names.count("record_failed") == 1 and "done" not in names and names[-1] == "record_failed", names
        declared = failed[-1][1]
        assert declared == {"turn": 2, "reason": "OSError", "answer_released": False,
                            "claims_admitted": declared["claims_admitted"],
                            "contract_version": loop.RECORD_FAILED_CONTRACT_VERSION}, declared
        # the stub extracts the same one claim each turn: re-asserted on the failed turn, one claim admitted
        assert declared["claims_admitted"] == 1
        assert "PLANTED-SECRET-TEXT" not in wire, "the error's text stays in the log"
        # the conversation is what it was: no turn counted, no message added
        assert svc.state.turn_count == state_before.turn_count == 1
        assert svc.state.messages == state_before.messages
        # the event ring is another door out of the process: no frame of the held answer went through it, and
        # no frame built after the record step did either
        assert _ring_tokens(client, bearer) == ring_before
        ring_after = _ring(client, bearer)
        assert len(ring_after) > len(ring_names_before), "the population: the failed turn's early frames are in the ring"
        assert not set(ring_after[len(ring_names_before):]) & AFTER_THE_RECORD, ring_after[len(ring_names_before):]
        stats = client.get("/cortex/stats", headers=bearer).json()["recorder"]
        assert stats["answer_release"] == loop.AFTER_RECORD
        assert stats["record_failures_since_start"] == failures_before + 1 and stats["last_failure_reason"] == "OSError"


# ---------------------------------------------------------------------------
# R2 — the same frames, held or live, when the write succeeds
# ---------------------------------------------------------------------------

def _comparable(frames) -> list:
    """The frames with the two values that differ between two runs by construction taken out: the record's id and
    the forecast's id, each a random UUID."""
    out = []
    for event, data in frames:
        if event == "done":
            data = {k: v for k, v in data.items() if k != "lineage_id"}
            if isinstance(data.get("citation_forecast"), dict):
                data["citation_forecast"] = {k: v for k, v in data["citation_forecast"].items() if k != "forecast_id"}
        out.append((event, data))
    return out


def test_r2_the_frames_are_the_same_held_or_live_when_the_record_is_written(tmp_path, monkeypatch):
    runs = {}
    for position in (loop.AFTER_RECORD, loop.LIVE):
        if position == loop.LIVE:
            monkeypatch.setenv("NEXUS_ANSWER_RELEASE", "live")
        else:
            monkeypatch.delenv("NEXUS_ANSWER_RELEASE", raising=False)
        root = tmp_path / position
        root.mkdir()
        with RigClock().installed():
            _svc, client, bearer = _console(root)
            runs[position] = [_comparable(_turn(client, bearer, q)[0])
                              for q in (QUESTION, "and what does sleep improve?")]
    held, live = runs[loop.AFTER_RECORD], runs[loop.LIVE]
    assert [[e for e, _ in t] for t in held] == [[e for e, _ in t] for t in live]
    assert held == live
    assert any(e == "token" for e, _ in held[0]) and any(e == "triples_extracted" for e, _ in held[0])


# ---------------------------------------------------------------------------
# R3 — live releases first, and a failed write is declared all the same
# ---------------------------------------------------------------------------

def test_r3_live_releases_the_answer_first_and_still_declares_a_failed_write(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_ANSWER_RELEASE", "live")
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        _break(svc.lineage, monkeypatch)
        frames, _wire = _turn(client, bearer)
    names = [e for e, _d in frames]
    assert "token" in names and names[-1] == "record_failed" and "done" not in names, names
    assert frames[-1][1]["answer_released"] is True and frames[-1][1]["reason"] == "OSError"
    assert svc.state.turn_count == 0 and svc.state.messages == ()


# ---------------------------------------------------------------------------
# R4 — the setting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, position, named", [
    (None, "after_record", False), ("", "after_record", False), ("after_record", "after_record", False),
    ("live", "live", False), (" LIVE ", "live", False),
    ("on", "after_record", True), ("stream", "after_record", True), ("0", "after_record", True),
])
def test_r4_only_live_is_live_and_any_other_value_is_the_closed_position(monkeypatch, value, position, named):
    if value is None:
        monkeypatch.delenv(loop.ANSWER_RELEASE_SETTING, raising=False)
    else:
        monkeypatch.setenv(loop.ANSWER_RELEASE_SETTING, value)
    declared = loop.recorder_declaration()
    assert loop.answer_release() == declared["answer_release"] == position
    assert ("setting_not_recognised" in declared) is named
    if named:
        assert declared["setting_not_recognised"] == value.strip().lower() and declared["treated_as"] == "after_record"


# ---------------------------------------------------------------------------
# R5 — no recorder is not a failed recorder; the server and MCP always compose one
# ---------------------------------------------------------------------------

def test_r5_a_loop_with_no_recorder_ends_in_done_as_before(monkeypatch):
    monkeypatch.delenv(loop.ANSWER_RELEASE_SETTING, raising=False)

    async def drive():
        return [frame async for frame, _state in loop.run_turn(NexusLoopState(), QUESTION, provider=hsh._Stub())]

    names = [f.split("\n", 1)[0].removeprefix("event: ") for f in asyncio.run(drive())]
    assert "token" in names and names[-1] == "done" and "record_failed" not in names, names


def test_r8_run_turn_hands_back_the_state_it_was_given_when_the_record_fails(monkeypatch):
    """For a caller of the loop itself (not the console): the record_failed frame's state is the state the turn was
    given, never the first half's, whose routed model and pinned attachments are a turn that did not happen."""
    monkeypatch.delenv(loop.ANSWER_RELEASE_SETTING, raising=False)

    class Refusing:
        def record_turn(self, **_kw):
            raise OSError("planted")

    given = NexusLoopState(attachments=({"name": "x.png", "type": "image/png", "data": "QUJD"},))

    async def drive():
        return [(frame, st) async for frame, st in loop.run_turn(given, QUESTION, provider=hsh._Stub(),
                                                                 lineage=Refusing())]

    pairs = asyncio.run(drive())
    assert pairs[-1][0].startswith("event: record_failed\n"), [f.split("\n", 1)[0] for f, _ in pairs]
    assert pairs[-1][1] == given and pairs[-1][1] is given
    assert any(st != given for _f, st in pairs[:-1]), "the population: the first half's state differs from the given"


def _keyword(source: str, callee: str, keyword: str) -> list[str]:
    """What each call to `callee` in `source` passes as `keyword`."""
    return [ast.unparse(k.value) for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == callee
            for k in n.keywords if k.arg == keyword]


def test_r5_the_server_and_mcp_always_compose_a_recorder(tmp_path):
    svc, _client, _bearer = _console(tmp_path)
    assert svc.lineage is not None
    assert _keyword((REPO / "backend" / "server.py").read_text(encoding="utf-8"), "run_turn", "lineage") == \
        ["services.lineage"]
    assert _keyword((REPO / "backend" / "mcp" / "mcp_server.py").read_text(encoding="utf-8"),
                    "complete_turn", "lineage") == ["s.lineage"]


# ---------------------------------------------------------------------------
# R6 — MCP: a failure by name, a counted gap, a retrieval that stays open
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def restored_mcp_environment():
    """`NexusRuntime.open` writes MCP mode's defaults into the process environment on purpose: learning frozen, its
    from-date, and the three logs under its root. A `frozen` left behind starves every later test's graph persist
    (two graph parity tests failed that way, in an order that ran them after this file), so every setting it may
    write is put back by hand: monkeypatch records nothing for a key that was absent."""
    from backend.mcp import config as mcp_config
    keys = (mcp_config.LEARNING_SETTING, mcp_config.SINCE_SETTING, mcp_config.OWNER_SETTING,
            mcp_config.DATA_ROOT_SETTING, *mcp_config.LOG_SETTINGS)
    before = {k: os.environ.get(k) for k in keys}
    try:
        yield before
    finally:
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def mcp_environment():
    with restored_mcp_environment() as before:
        yield before


def test_r6_mcp_a_failed_record_is_a_named_failure_and_a_counted_gap(tmp_path, monkeypatch, mcp_environment):
    rt = NexusRuntime.open(root=tmp_path)
    srv = build_server(rt)

    def call(name, **args):
        return asyncio.run(srv.call_tool(name, args)).model_dump()["structured_content"]

    retrieved = call("nexus_retrieve", question="tell me about python")
    real = rt.services.lineage.record_turn
    _break(rt.services.lineage, monkeypatch)
    with pytest.raises(Exception, match="record_not_written") as failure:
        call("nexus_record", retrieval_id=retrieved["retrieval_id"], answer="Python is a programming language.")
    assert "PLANTED-SECRET-TEXT" not in str(failure.value)
    counted = rt.coverage.counters()
    assert (counted[cov.RECORD_FAILED], counted[cov.RECORDED]) == (1, 0), counted
    assert rt.services.lineage.recent(limit=5) == []
    assert call("nexus_status")["handles_open"] == 1, "the retrieval stays open"
    assert "could not write" in counted["sentence"] and MCP_CONTRACT_VERSION == 5   # W9: 3 -> 4; W10b: 4 -> 5
    # the same retrieval records on a second attempt, once the recorder works
    monkeypatch.setattr(rt.services.lineage, "record_turn", real)
    rec = call("nexus_record", retrieval_id=retrieved["retrieval_id"], answer="Python is a programming language.")
    counted = rt.coverage.counters()
    assert rec["lineage_id"] and (counted[cov.RECORD_FAILED], counted[cov.RECORDED]) == (1, 1)
    assert call("nexus_status")["handles_open"] == 0


# ---------------------------------------------------------------------------
# R8 — what a failed write leaves: the session as it was, no stated confidence, the admitted claims counted
# ---------------------------------------------------------------------------

def test_r8_a_failed_write_leaves_the_session_as_it_was_before_the_request(tmp_path, monkeypatch):
    """The state a failed turn hands back is the session's before the request, field for field: not the first
    half's (the routed model) and not the request's attachments, which the next turn would be routed on. A
    one-turn override set before the request is still pending: the turn that would have used it did not happen."""
    monkeypatch.delenv(loop.ANSWER_RELEASE_SETTING, raising=False)
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        _turn(client, bearer)
        set_override = client.post("/model/override", headers=bearer, json={"model": "code", "lock": False})
        assert set_override.status_code == 200, set_override.text
        before = svc.state
        assert before.model_override == "code" and before.attachments == ()
        real = svc.lineage.record_turn
        _break(svc.lineage, monkeypatch)
        resp = client.post("/chat", headers=bearer, json={
            "message": "what is in this picture?",
            "attachments": [{"name": "x.png", "type": "image/png", "data": "QUJD"}]})
        assert _frames(resp)[-1][0] == "record_failed"
        changed = [f.name for f in dataclasses.fields(before) if getattr(before, f.name) != getattr(svc.state, f.name)]
        assert svc.state == before, f"fields the failed turn left changed: {changed}"
        monkeypatch.setattr(svc.lineage, "record_turn", real)
        routed = [d for e, d in _turn(client, bearer, "and what does sleep improve?")[0] if e == "model_routed"]
    assert routed and routed[0]["capability"] != "multimodal", routed


def test_r8_a_failed_write_accrues_no_stated_confidence_and_counts_distinct_claims(tmp_path, monkeypatch):
    """The extractor's stated confidence names a claim read out of the answer (its subject and relation), so it
    waits for the record: a failed write accrues none. The claims extraction admitted stay (Law 4) and are counted
    on the frame as distinct claims, not as calls: here three candidates, one of them twice, are two claims. The
    two contradict each other, so the recorded turn carries a contradiction_found frame and the failed turn must
    not have built one (the event ring would hold it), nor written the evaluator's line."""
    monkeypatch.delenv(loop.ANSWER_RELEASE_SETTING, raising=False)
    twice = json.dumps([{"subject": "sleep", "relation": "increases", "object": "recovery", "confidence": 0.9},
                        {"subject": "sleep", "relation": "increases", "object": "recovery", "confidence": 0.9},
                        {"subject": "sleep", "relation": "decreases", "object": "recovery", "confidence": 0.8}])
    with RigClock().installed():
        hsh._build_store(tmp_path / "kb.db").close()
        svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                              eval_log_path=tmp_path / "metrics.jsonl", archive_db_path=tmp_path / "archive.db",
                              snapshots_dir=tmp_path / "snapshots",
                              provider=hsh._Stub(plant=None, extract=lambda _prompt: twice))
        client = TestClient(create_app(svc))
        bearer = {"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"}
        recorded, _ = _turn(client, bearer)
        assert "contradiction_found" in [e for e, _d in recorded], "the population: the pair contradicts"
        triple_rows = _calibration_rows("triple")
        assert triple_rows >= 2, "the population: a recorded turn accrues each extracted claim's stated confidence"
        metrics = tmp_path / "metrics.jsonl"
        assert metrics.exists(), "the population: the evaluator writes a line for a recorded turn"
        metrics_before = metrics.read_bytes()
        ring_before = _ring(client, bearer)
        _break(svc.lineage, monkeypatch)
        failed, _wire = _turn(client, bearer, "and what does sleep improve?")
        ring_after = _ring(client, bearer)
    assert not set(ring_after[len(ring_before):]) & AFTER_THE_RECORD, ring_after[len(ring_before):]
    assert failed[-1][0] == "record_failed" and failed[-1][1]["claims_admitted"] == 2, failed[-1]
    assert _calibration_rows("triple") == triple_rows, "a stated confidence was accrued for a turn with no record"
    assert metrics.read_bytes() == metrics_before, "the evaluator wrote a line for a turn with no record"


def test_r6_mcp_a_failed_record_whose_count_the_store_also_refuses_says_so(tmp_path, monkeypatch, mcp_environment):
    """The coverage counter lives in the store that refused the record. When it refuses the count too, the failure
    says so and is counted in this process (`nexus_status.record_failed_uncounted`), never "counted as
    record_failed" over a count that was not written."""
    rt = NexusRuntime.open(root=tmp_path)
    srv = build_server(rt)

    def call(name, **args):
        return asyncio.run(srv.call_tool(name, args)).model_dump()["structured_content"]

    retrieved = call("nexus_retrieve", question="tell me about python")
    _break(rt.services.lineage, monkeypatch)
    real_event = rt.coverage.record_event

    def refuse(event, **kw):
        if event == cov.RECORD_FAILED:
            raise OSError("the store refuses writes")
        return real_event(event, **kw)

    monkeypatch.setattr(rt.coverage, "record_event", refuse)
    with pytest.raises(Exception, match="record_not_written") as failure:
        call("nexus_record", retrieval_id=retrieved["retrieval_id"], answer="Python is a programming language.")
    text = str(failure.value)
    assert "counted as record_failed" not in text and "counted in this process only" in text, text
    status = call("nexus_status")
    assert status["record_failed_uncounted"] == 1 and status["coverage"][cov.RECORD_FAILED] == 0, status


# ---------------------------------------------------------------------------
# R7 — every frame before the record step goes through the hold
# ---------------------------------------------------------------------------

def frames_before_the_record(source: str) -> list[str]:
    """Every `yield` in `complete_turn` on a line above the `record_turn` call. It reads spellings: a frame
    yielded there leaves while the answer is held. A frame released before the record step by another spelling (a
    helper generator, `yield from`) is not named by this; R1 is the behaviour test for those."""
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "complete_turn")
    record_line = min(n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Attribute) and n.func.attr == "record_turn")
    return [f"line {n.lineno}: {ast.unparse(n)[:70]}" for n in ast.walk(fn)
            if isinstance(n, ast.Yield) and n.lineno < record_line]


def test_r7_complete_turn_yields_nothing_before_the_record_step():
    source = LOOP_SRC.read_text(encoding="utf-8")
    assert frames_before_the_record(source) == []
    fn = next(n for n in ast.walk(ast.parse(source))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "complete_turn")
    assert sum(1 for n in ast.walk(fn) if isinstance(n, ast.Yield)) >= 8, "the population: the frames it does yield"


def test_r7_companion_a_frame_yielded_before_the_record_step_is_named():
    source = LOOP_SRC.read_text(encoding="utf-8")
    anchor = "    cited_belief_ids = cited_ids(hits, assistant_msg[\"content\"])\n"
    assert source.count(anchor) == 1
    planted = source.replace(anchor, anchor + "    yield sse_event(\"graph_activating\", []), state\n")
    assert len(frames_before_the_record(planted)) == 1 and "graph_activating" in frames_before_the_record(planted)[0]


def test_r6_companion_what_an_mcp_runtime_writes_to_the_environment_is_put_back(tmp_path, monkeypatch):
    """The order dependency this file once had: R6 opened an MCP runtime and left `NEXUS_LEARNING=frozen` in the
    process, and tests that ran after it saved no graph. Here the runtime's write is seen inside the block, and
    the setting is what it was once the block ends."""
    from backend.mcp import config as mcp_config
    monkeypatch.delenv(mcp_config.LEARNING_SETTING, raising=False)
    monkeypatch.delenv(mcp_config.SINCE_SETTING, raising=False)
    with restored_mcp_environment():
        NexusRuntime.open(root=tmp_path)
        assert os.environ.get(mcp_config.LEARNING_SETTING) == mcp_config.FROZEN, "the runtime applies its default"
    assert mcp_config.LEARNING_SETTING not in os.environ and mcp_config.SINCE_SETTING not in os.environ


def test_r9_a_failed_turn_spends_a_consented_search_and_the_retry_runs_none(tmp_path, monkeypatch):
    """Declared, not restored (W6's delta check, D3): a web search consented to for the turn runs in the first half
    and its single-use consent is spent there, so a turn whose record fails leaves no consent, and the retry runs
    no search until the operator consents again (Law 3). MCP runs no search, so this is not an MCP retrieval's."""
    from backend.tools.search import SearchResult
    svc, client, bearer = _console(tmp_path)
    calls: list[str] = []

    async def fake_search(query, confirmed=False, **_kw):
        calls.append(query)
        return [SearchResult(title="a result", url="https://example.org/a", snippet="interval training improves "
                             "VO2 max", engine="probe")]

    monkeypatch.setattr(svc.search_tool, "search", fake_search)
    question = "what is the latest news on interval training?"
    consent = client.post("/search/confirm", headers=bearer, json={"query": question, "confirmed": True})
    assert consent.status_code == 200, consent.text
    real = svc.lineage.record_turn
    _break(svc.lineage, monkeypatch)
    failed, _text = _turn(client, bearer, question)
    assert [e for e, _ in failed if e in ("rag_injected", "record_failed", "done")] == ["rag_injected", "record_failed"]
    assert len(calls) == 1 and not svc.search_consents, "the consent was spent on the failed turn"
    monkeypatch.setattr(svc.lineage, "record_turn", real)
    retry, _text = _turn(client, bearer, question)
    assert [e for e, _ in retry if e in ("rag_injected", "record_failed", "done")] == ["done"]
    assert len(calls) == 1, "the retry ran no search: the consent is asked for again"


def test_r10_the_stated_confidences_are_logged_before_the_first_frame_is_released(tmp_path, monkeypatch):
    """W6's second delta check, F9: a client that stops reading at the first released frame (Starlette closes the
    generator there) still leaves the extractor's stated confidence in the calibration log, because it is logged
    right after the record and before any frame of the held answer."""
    monkeypatch.delenv(loop.ANSWER_RELEASE_SETTING, raising=False)
    svc, _client, _bearer = _console(tmp_path)           # its stub extracts one claim at confidence 0.9
    before = _calibration_rows("triple")

    async def cut() -> list[str]:
        seen = []
        gen = loop.run_turn(NexusLoopState(), QUESTION, retriever=svc.retriever, provider=svc.provider,
                            lineage=svc.lineage, work_units=svc.work_units)
        async for frame, _st in gen:
            seen.append(frame.split("\n", 1)[0])
            if frame.startswith("event: token"):
                break
        await gen.aclose()
        return seen

    seen = asyncio.run(cut())
    assert seen[-1] == "event: token" and "event: done" not in seen, seen
    assert _calibration_rows("triple") == before + 1, "the stated confidence was not logged before the release"
