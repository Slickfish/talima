"""Packet S.3 — why a belief weighs what it weighs.

A QUERY, NOT A RECONSTRUCTION
    `MAX()` made reconstruction impossible anyway: `TEMPORAL_PROBE.md`
    established that stored weight is a monotone high-water mark with no
    recoverable history. So provenance is not "work out what happened" — it is
    "read what was recorded", two indexed reads and a lookup per step.

THE HONESTY REQUIREMENT, WHICH IS THE HARD PART
    The trace answers only for movements recorded after S.2 shipped. An empty
    history therefore means **"nothing was recorded"**, and nothing in the data
    distinguishes that from "this belief never moved" — only the surface can,
    by saying so. A provenance panel rendering an empty list without that
    sentence implies a completeness it does not have.

    That is the same discipline H.2 applied when it made unresolved beliefs
    visible rather than omitting them, and it is why `coverage` is asserted
    present on the EMPTY case first, before any test that has steps to look at.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.kb.recalibration import (
    COVERAGE_NOTE,
    NO_RECORDS_NOTE,
    Envelope,
    EnvelopeViolation,
    apply_outcome,
    provenance_for,
)
from backend.server import create_app, create_services

TOKEN = "packet-s3-token"


def _services(tmp_path):
    return create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots",
    )


def _belief(services, subject="QTc", source="extraction", weight=0.55) -> int:
    return services.store.add(subject=subject, relation="stands_for",
                              object="corrected QT", weight=weight, tier=2,
                              granularity=Granularity.TOPIC, source=source)


def _policy(services, version="v1", **kw):
    env = Envelope(version=version, max_delta=kw.pop("max_delta", 0.05),
                   movable_classes=kw.pop("movable_classes", ("extraction",)),
                   **kw)
    services.recalibration.record_envelope(env, actor="operator",
                                           reason="board approved")
    return env


def _move(services, bid, env, delta, outcome_ref, *, decision="approved",
          direction="confirming"):
    """A recorded outcome and the recalibration it authorised."""
    ev = services.recalibration.record_outcome(
        event_type="review", decision=decision, direction=direction,
        references={"belief_ids": [bid]}, actor="operator",
        workflow_ref="wf-7", notes="", event_id=outcome_ref)
    return apply_outcome(services.store, services.recalibration, belief_id=bid,
                         delta=delta, envelope=env, actor="operator",
                         outcome_ref=ev.event_id)


# ---------------------------------------------------------------------------
# THE HONESTY REQUIREMENT, FIRST — the empty case is the one that can mislead
# ---------------------------------------------------------------------------

def test_a_belief_with_no_recorded_movement_says_so_rather_than_looking_clean(
        tmp_path):
    """THE REQUIREMENT THE PACKET RESTATED, and the one an implementation
    naturally omits: the empty list is the dangerous rendering.

    On a store where nothing has ever been recorded, `steps` is empty and
    `records_begin_at` is None — and the note has to say that nothing was
    recorded, not let the absence read as "this belief never moved".
    """
    services = _services(tmp_path)
    bid = _belief(services)

    trace = provenance_for(services.store, services.recalibration, belief_id=bid)

    assert trace.steps == ()
    assert trace.records_begin_at is None
    assert trace.coverage == NO_RECORDS_NOTE
    # The sentence has to actually distinguish the two readings.
    assert "recorded" in trace.coverage
    assert "TEMPORAL_PROBE" in trace.coverage
    # And it must be in the serialised form, not only the object — the panel
    # renders the dict.
    assert trace.to_dict()["coverage"] == NO_RECORDS_NOTE


def test_the_coverage_note_names_the_date_records_begin(tmp_path):
    """Once anything is recorded, the boundary is a real timestamp and the
    note carries it. "Complete from <date>" is a claim someone can check; "we
    have some records" is not."""
    services = _services(tmp_path)
    bid = _belief(services)
    before = time.time()
    env = _policy(services)

    trace = provenance_for(services.store, services.recalibration, belief_id=bid)
    assert trace.records_begin_at is not None
    assert trace.records_begin_at >= before - 5
    assert trace.coverage == COVERAGE_NOTE.format(
        when=time.strftime("%Y-%m-%d %H:%M:%SZ",
                           time.gmtime(trace.records_begin_at)))
    assert "never 'never moved'" in trace.coverage, (
        "the note no longer distinguishes 'not recorded' from 'never moved'")

    # NON-VACUITY: the boundary is set by the ENVELOPE record, before any
    # recalibration exists — so a belief that never moved still learns from
    # what date it could have been known to.
    assert trace.steps == ()


def test_the_coverage_note_is_present_even_when_the_belief_did_move(tmp_path, recalibration_on):
    """The note is not an empty-state message. A trace with three steps still
    cannot say what happened before the records begin."""
    services = _services(tmp_path)
    bid = _belief(services)
    env = _policy(services)
    _move(services, bid, env, -0.05, "review-1")

    trace = provenance_for(services.store, services.recalibration, belief_id=bid)
    assert trace.steps
    assert trace.coverage and trace.records_begin_at is not None


# ---------------------------------------------------------------------------
# The trace itself
# ---------------------------------------------------------------------------

def test_each_step_resolves_to_its_outcome_actor_and_envelope(tmp_path, recalibration_on):
    """The packet's sentence, asserted: each recalibration to its outcome,
    each outcome to its actor and envelope version."""
    services = _services(tmp_path)
    bid = _belief(services)
    env = _policy(services, version="board-2026-01")
    _move(services, bid, env, -0.05, "review-approved-17")
    _move(services, bid, env, +0.03, "qc-passed-42", decision="passed")

    trace = provenance_for(services.store, services.recalibration, belief_id=bid)

    assert [s.outcome_ref for s in trace.steps] == [
        "review-approved-17", "qc-passed-42"], "steps are out of order"
    for step in trace.steps:
        assert step.outcome is not None, (
            f"step {step.event_id} could not resolve its outcome")
        assert step.outcome.event_type == "review"
        assert step.outcome.workflow_ref == "wf-7"
        assert step.actor.id == "operator"
        assert step.actor.source == "local"
        assert step.envelope_version == "board-2026-01"
        assert step.ts > 0

    # The arithmetic adds up: the steps ARE the adjustment.
    assert sum(s.delta for s in trace.steps) == pytest.approx(trace.adjustment)
    assert trace.adjustment == pytest.approx(-0.02)


def test_the_three_weights_stay_three(tmp_path, recalibration_on):
    """H.2's finding, carried into this surface. The record is not the
    prominence is not the ranking value, and a trace that reported one number
    would be the defect H.2 was written about."""
    services = _services(tmp_path)
    bid = _belief(services, source="extraction", weight=0.55)
    env = _policy(services)
    _move(services, bid, env, -0.05, "review-1")

    trace = provenance_for(services.store, services.recalibration, belief_id=bid)
    assert trace.stored_weight == pytest.approx(0.55), (
        "a recalibration moved the record layer")
    assert trace.arrival_scale == pytest.approx(0.65), (
        "extraction's Q.2 arrival scale is not being reported")
    assert trace.effective_weight == pytest.approx(
        trace.current_weight * trace.arrival_scale + trace.adjustment)
    assert trace.effective_weight < trace.stored_weight

    d = trace.to_dict()
    for key in ("stored_weight", "current_weight", "arrival_scale",
                "recalibration_adjustment", "effective_weight"):
        assert key in d, f"{key} missing from the rendered trace"
    assert set(d["weight_vocabulary"]) == {
        "stored_weight", "current_weight", "arrival_scale",
        "recalibration_adjustment", "effective_weight"}, (
        "the vocabulary and the numbers have drifted apart")


def test_refused_attempts_appear_beside_the_applied_ones(tmp_path, recalibration_on):
    """A trace showing only what succeeded describes a system that never
    declines anything — the same objection S.2 answered inside the store, and
    it has to survive the trip to the surface."""
    services = _services(tmp_path)
    bid = _belief(services)
    env = _policy(services)
    _move(services, bid, env, -0.05, "review-1")
    with pytest.raises(EnvelopeViolation):
        apply_outcome(services.store, services.recalibration, belief_id=bid,
                      delta=-0.40, envelope=env, actor="operator",
                      outcome_ref="review-2")

    trace = provenance_for(services.store, services.recalibration, belief_id=bid)
    assert len(trace.steps) == 1
    assert len(trace.refused) == 1
    assert trace.refused[0].refusal_rule == "max_delta"
    assert trace.refused[0].applied is False
    assert "max_delta" in trace.refused[0].refusal_detail
    # A refusal moved nothing, and must not be counted in the adjustment.
    assert trace.adjustment == pytest.approx(-0.05)


def test_a_step_whose_outcome_does_not_resolve_is_reported(tmp_path, recalibration_on):
    """I-6. A recalibration citing an outcome nobody can find is a finding,
    not a field to leave null and move past."""
    services = _services(tmp_path)
    bid = _belief(services)
    env = _policy(services)
    apply_outcome(services.store, services.recalibration, belief_id=bid,
                  delta=-0.05, envelope=env, actor="operator",
                  outcome_ref="an-outcome-nobody-recorded")

    step = provenance_for(services.store, services.recalibration,
                          belief_id=bid).steps[0]
    assert step.outcome is None
    assert step.to_dict()["outcome_resolved"] is False, (
        "an unresolvable outcome reference is indistinguishable from a step "
        "that never had one")


def test_seal_status_travels_with_each_step(tmp_path, monkeypatch, recalibration_on):
    """Whether a movement is chain-covered is part of what it is worth. An
    unsealed recalibration proves nothing yet, and a trace that did not say so
    would let a reader take it as settled."""
    import asyncio

    monkeypatch.setenv("NEXUS_GOVERNANCE_SEAL", "prompt")
    services = _services(tmp_path)
    bid = _belief(services)
    env = _policy(services)
    _move(services, bid, env, -0.05, "review-1")

    before = provenance_for(services.store, services.recalibration,
                            belief_id=bid)
    assert before.steps[0].sealed_block is None
    assert before.steps[0].to_dict()["sealed"] is False

    asyncio.run(services.kairos.maybe_seal_governance(force=True))

    after = provenance_for(services.store, services.recalibration, belief_id=bid)
    assert after.steps[0].sealed_block is not None
    assert after.steps[0].to_dict()["sealed"] is True


def test_an_unresolved_belief_is_reported_not_hidden(tmp_path):
    """H.2's discipline. A belief id that no longer resolves gets a trace
    saying so rather than an exception or an empty success."""
    services = _services(tmp_path)
    trace = provenance_for(services.store, services.recalibration,
                           belief_id=999_999)
    assert trace.resolved is False
    assert trace.to_dict()["resolved"] is False
    assert trace.coverage, "even an unresolved belief carries the coverage note"


def test_the_kill_switch_is_visible_in_the_trace(tmp_path, monkeypatch, recalibration_on):
    """With `NEXUS_RECALIBRATION=off` the recorded steps still exist but no
    longer affect the weight. A trace that showed the steps without saying
    they are inert would be describing a system that is not running."""
    services = _services(tmp_path)
    bid = _belief(services)
    env = _policy(services)
    _move(services, bid, env, -0.05, "review-1")

    monkeypatch.setenv("NEXUS_RECALIBRATION", "off")
    trace = provenance_for(services.store, services.recalibration, belief_id=bid)
    assert trace.enabled is False
    assert trace.to_dict()["recalibration_enabled"] is False
    assert trace.adjustment == 0.0, "the switch is off but the delta applied"
    assert trace.steps, "the record was erased rather than made inert"


# ---------------------------------------------------------------------------
# The surfaces
# ---------------------------------------------------------------------------

def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    services = _services(tmp_path)
    return TestClient(create_app(services)), services


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_the_provenance_endpoint_answers(tmp_path, monkeypatch, recalibration_on):
    client, services = _client(tmp_path, monkeypatch)
    bid = _belief(services)
    env = _policy(services)
    _move(services, bid, env, -0.05, "review-approved-17")

    with client:
        r = client.get(f"/belief/{bid}/provenance", headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["belief_id"] == bid
    assert len(body["steps"]) == 1
    assert body["steps"][0]["outcome"]["event_id"] == "review-approved-17"
    assert body["steps"][0]["actor"]["id"] == "operator"
    assert body["coverage"]


def test_the_provenance_endpoint_is_gated(tmp_path, monkeypatch):
    """Same posture as every `/lineage/*` route, and for the same reason:
    this is evidence about decisions and it names who made them."""
    client, services = _client(tmp_path, monkeypatch)
    bid = _belief(services)
    with client:
        r = client.get(f"/belief/{bid}/provenance",
                       headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_the_lineage_view_shows_that_the_ground_moved(tmp_path, monkeypatch, recalibration_on):
    """SURFACED WHERE H.2 ALREADY SHOWS THE BELIEF.

    The lineage view is the one place that says what an answer rested on. If
    a belief's weight has moved since, that view has to show it — otherwise
    the answer looks as well-supported today as it was then, which is exactly
    the reading the record exists to prevent.
    """
    client, services = _client(tmp_path, monkeypatch)
    bid = _belief(services)
    env = _policy(services)
    rec = services.lineage.record_turn(
        query="what is QTc?",
        injected=[{"belief_id": bid, "weight_at_injection": 0.55,
                   "tier_at_injection": 2, "cited": True}],
        contradictions=[], model_id="test", response_text="QTc.",
        prompt_prefix="STABLE")
    _move(services, bid, env, -0.05, "review-approved-17")

    with client:
        body = client.get(f"/lineage/{rec.lineage_id}", headers=_auth()).json()

    entry = body["injected"][0]
    prov = entry["provenance"]
    assert prov["recalibrations"] == 1
    assert prov["recalibration_adjustment"] == pytest.approx(-0.05)
    assert prov["detail"] == f"/belief/{bid}/provenance"
    # The recorded value is untouched — that is the whole of H.2's point.
    assert entry["weight_at_injection"] == 0.55
    assert entry["high_water_weight"] == 0.55
    assert prov["effective_weight"] < entry["weight_at_injection"]


def test_the_lineage_view_carries_the_coverage_note_on_every_response(
        tmp_path, monkeypatch):
    """ON ITS FACE, including when nothing moved.

    This is the case the requirement is really about: a lineage record whose
    beliefs show `recalibrations: 0`. Without the note that reads as "these
    beliefs are unchanged since the answer", and it means "nothing was
    recorded".
    """
    client, services = _client(tmp_path, monkeypatch)
    bid = _belief(services)
    rec = services.lineage.record_turn(
        query="q", injected=[{"belief_id": bid, "weight_at_injection": 0.55,
                              "tier_at_injection": 2, "cited": True}],
        contradictions=[], model_id="test", response_text="r",
        prompt_prefix="STABLE")

    with client:
        body = client.get(f"/lineage/{rec.lineage_id}", headers=_auth()).json()

    assert body["injected"][0]["provenance"]["recalibrations"] == 0
    coverage = body["provenance_coverage"]
    assert coverage["records_begin_at"] is None
    assert coverage["note"] == NO_RECORDS_NOTE
    assert "TEMPORAL_PROBE" in coverage["note"]


def test_the_lineage_view_still_reports_an_unresolved_belief(
        tmp_path, monkeypatch):
    """H.2's original finding must survive S.3's addition — a belief that no
    longer resolves gets no provenance block and is still flagged, rather than
    crashing the view or being quietly dropped."""
    client, services = _client(tmp_path, monkeypatch)
    rec = services.lineage.record_turn(
        query="q", injected=[{"belief_id": 999_999, "weight_at_injection": 0.9,
                              "tier_at_injection": 1, "cited": True}],
        contradictions=[], model_id="test", response_text="r",
        prompt_prefix="STABLE")
    with client:
        body = client.get(f"/lineage/{rec.lineage_id}", headers=_auth()).json()

    entry = body["injected"][0]
    assert entry["resolved"] is False
    assert "provenance" not in entry
    assert body["provenance_coverage"]["note"]
