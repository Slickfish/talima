"""Packet Z.2 — a confirmed outcome resolves the confidence it was about.

THE DISCONNECTION, MEASURED
    `calibration_log.resolve()` had ZERO production callers —
    `agentic_loop.py` imports only `log_confidence`. The system logged every
    confidence it stated and nothing ever checked whether any of them were
    right: every row read `resolved_outcome: null`, and a perfectly diligent
    operator closing every loop fed the governance record and starved the
    calibration record. Third instance of the plumbed-but-never-connected
    class (P.4's read side, W.0's empty vector store).

WHICH ROWS AN OUTCOME RESOLVES, AND WHY THAT SCOPE
    Rows whose `context.lineage_id` matches the outcome's referenced lineage
    record AND whose `belief_id` is among the beliefs the outcome resolved
    to. The confidences stated in THAT turn about THOSE beliefs are what the
    outcome adjudicates. All-rows-for-the-belief would fabricate outcomes
    for predictions the world never tested; an outcome referencing
    `belief_ids` directly resolves nothing, declared, because no lineage
    ties it to any stated confidence.

DIRECTION
    confirming -> True, contradicting -> False. `neutral` resolves NOTHING —
    `resolved_outcome` is a bool and "the world answered neither way" is not
    a truth value; forcing it would fabricate the very data the calibration
    is for.

REFUSED
    A refused outcome resolves nothing: nothing was adjudicated. But an
    ACCEPTED outcome whose recalibration the envelope refused still
    resolves — the world's answer is a fact regardless of whether governance
    let a weight move. Calibration is epistemics; the envelope is policy.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.eval.calibration_log import latest_by_entry
from backend.kb.granularity import Granularity
from backend.server import create_app, create_services

TOKEN = "outcome-calibration-token"
H = {"Authorization": f"Bearer {TOKEN}"}


class _Answering:
    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "The QTc change at 600mg was 11.4ms."

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return False


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A REAL turn writes the calibration rows — the fixture must prove
    production's write side, or resolving hand-planted rows proves only that
    the test can read its own writing."""
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG",
                       str(tmp_path / "calibration_log.jsonl"))
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap",
                          provider=_Answering())
    # The subject must be a phrase the mock ANSWER actually contains:
    # `_resolve_outcome_references` follows the `cited` flag, and citation
    # (backend/kb/citation.py) demands subject AND object mentioned. The
    # first draft used "mean qtc change at 600mg" — the answer lacks "mean",
    # production marked it cited: False, and every lineage-scoped case 422'd
    # with "0 cited belief(s)". The fixture was refuted by the real citation
    # rule, which is the reason this rig drives a real turn at all.
    bid = svc.store.add(subject="qtc change at 600mg", relation="was",
                        object="11.4ms", weight=0.8, tier=1,
                        granularity=Granularity.TOPIC,
                        source="human_assertion")
    client = TestClient(create_app(svc))
    client.post("/governance/envelope/confirm", json={
        "version": "v1", "max_delta": 0.1, "movable_classes": ["asserted"],
        "reason": "Z.2 fixture", "confirmed": True}, headers=H)
    r = client.post("/chat", json={"message": "what was the qtc change at 600mg?"},
                    headers=H)
    done = [f for f in r.text.split("\n\n") if f.startswith("event: done")]
    assert done, "the turn did not complete"
    lineage_id = json.loads(
        next(l[6:] for l in done[-1].split("\n")
             if l.startswith("data: ")))["lineage_id"]
    assert lineage_id, "no lineage record — the fixture cannot reference one"
    return svc, client, bid, lineage_id


def _unresolved_for(lineage_id):
    return {eid: row for eid, row in latest_by_entry().items()
            if row.get("resolved_outcome") is None
            and (row.get("context") or {}).get("lineage_id") == lineage_id}


def _outcome(client, lineage_id, **extra):
    body = {"event_type": "qc", "decision": "batch_pass",
            "direction": "confirming",
            "references": {"lineage_id": lineage_id},
            "magnitude": 0.05, "confirmed": True, **extra}
    return client.post("/outcome/confirm", json=body, headers=H)


def test_the_fixture_log_actually_contains_unresolved_rows(rig):
    """NON-VACUITY, and the reason this defect was invisible: a log where
    there was nothing to resolve passes every wiring test while proving
    nothing. The turn must have stated confidences, unresolved, tied to the
    lineage record."""
    _svc, _client, bid, lineage_id = rig
    rows = _unresolved_for(lineage_id)
    assert rows, "the turn logged no calibration rows for its lineage record"
    assert any(r.get("belief_id") == bid for r in rows.values()), (
        "no row names the injected belief — the join this packet wires "
        "does not exist in the data")


def test_a_confirmed_outcome_resolves_its_turns_rows_and_no_others(rig):
    svc, client, bid, lineage_id = rig
    # A SECOND turn whose rows must stay untouched.
    r2 = client.post("/chat", json={"message": "what was the qtc change at 600mg?"},
                     headers=H)
    done = [f for f in r2.text.split("\n\n") if f.startswith("event: done")]
    other_lineage = json.loads(
        next(l[6:] for l in done[-1].split("\n")
             if l.startswith("data: ")))["lineage_id"]
    before_mine = _unresolved_for(lineage_id)
    before_other = _unresolved_for(other_lineage)
    assert before_mine and before_other, "fixture must have rows on both sides"

    resp = _outcome(client, lineage_id).json()
    assert resp["calibration_resolved"] == len(before_mine), resp

    merged = latest_by_entry()
    for eid in before_mine:
        assert merged[eid]["resolved_outcome"] is True, (
            f"{eid} not resolved True on a confirming outcome")
    for eid in before_other:
        assert merged[eid]["resolved_outcome"] is None, (
            "an outcome about one turn resolved another turn's rows — that "
            "fabricates outcomes for predictions the world never tested")


def test_a_contradicting_outcome_resolves_false(rig):
    _svc, client, _bid, lineage_id = rig
    before = _unresolved_for(lineage_id)
    resp = _outcome(client, lineage_id, direction="contradicting",
                    decision="batch_fail").json()
    assert resp["calibration_resolved"] == len(before)
    merged = latest_by_entry()
    assert all(merged[eid]["resolved_outcome"] is False for eid in before)


def test_a_neutral_outcome_resolves_nothing_and_says_why(rig):
    """bool cannot carry 'neither'. Forcing a truth value out of a neutral
    outcome would fabricate the very data the calibration exists to check."""
    _svc, client, _bid, lineage_id = rig
    resp = _outcome(client, lineage_id, direction="neutral").json()
    assert resp["calibration_resolved"] == 0
    assert "neutral" in resp["calibration_note"]
    assert _unresolved_for(lineage_id), "neutral resolved rows anyway"


def test_a_refused_outcome_resolves_nothing(rig):
    _svc, client, _bid, lineage_id = rig
    r = _outcome(client, lineage_id, confirmed=False)
    assert r.status_code == 422
    assert _unresolved_for(lineage_id), (
        "a Law 3 refusal resolved calibration rows — nothing was adjudicated")


def test_a_belief_ids_outcome_resolves_nothing_declared(rig):
    """No lineage reference means no tie to any stated confidence. Resolving
    all unresolved rows for the belief would adjudicate statements from
    turns this outcome knows nothing about."""
    _svc, client, bid, lineage_id = rig
    resp = _outcome(client, lineage_id,
                    references={"belief_ids": [bid]}).json()
    assert resp["calibration_resolved"] == 0
    assert "lineage" in resp["calibration_note"]
    assert _unresolved_for(lineage_id)


def test_resolving_twice_does_not_corrupt(rig):
    """`resolve()` appends; a second resolution row for the same entry wins
    the merge, so an unguarded retry could silently rewrite an outcome. The
    wiring must skip rows already resolved — the retry reports zero and the
    stored values do not move."""
    _svc, client, _bid, lineage_id = rig
    first = _outcome(client, lineage_id).json()
    assert first["calibration_resolved"] > 0
    again = _outcome(client, lineage_id,
                     direction="contradicting", decision="oops").json()
    assert again["calibration_resolved"] == 0, (
        "the retry re-resolved already-resolved rows — a later outcome can "
        "silently rewrite an earlier adjudication")
    merged = latest_by_entry()
    resolved = [r for r in merged.values()
                if (r.get("context") or {}).get("lineage_id") == lineage_id
                and r.get("resolved_outcome") is not None]
    assert resolved and all(r["resolved_outcome"] is True for r in resolved), (
        "the first adjudication was rewritten")


def test_the_count_is_on_the_response(rig):
    """I-6 — a wiring that silently resolves nothing is exactly how this
    went unnoticed. The response must carry the number either way."""
    _svc, client, _bid, lineage_id = rig
    resp = _outcome(client, lineage_id).json()
    assert "calibration_resolved" in resp
    assert isinstance(resp["calibration_resolved"], int)
