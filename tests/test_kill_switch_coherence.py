"""The whole system with `NEXUS_RECALIBRATION=off` — the cautious deployment.

WHY THIS EXISTS
    The kill switch is documented, supported, and is the configuration a first
    deployment would sensibly choose: install NEXUS, leave the thing that moves
    belief weights turned off, decide later.

    **Measured: with it engaged, 39 tests failed.** Every one of them was about
    the feature working, and every one silently assumed the switch was on. The
    off position had two tests — both of which flip the switch mid-body after
    an on-position setup — and nothing at all asserted that the SYSTEM hangs
    together in that state.

    Three surfaces have to agree, and they are the three an operator would
    consult in that order:

      /governance/envelope        says the machinery is disabled
      /belief/{id}/provenance     says the ground did not move, rather than
                                  rendering an empty trace that reads as
                                  "nothing was ever attempted"
      KAIROS                      seals nothing AND declares why, rather than
                                  silently doing nothing

    The middle one is the reason this is an integration test rather than three
    unit tests. An empty trace and an inert-but-populated trace are the same
    JSON length and completely different claims.

NON-VACUITY IS THE POINT HERE, NOT A FORMALITY
    A test that turns the switch off and finds nothing moved proves nothing on
    a system where nothing was tried. So the fixture MOVES A WEIGHT FIRST, with
    the switch on, and asserts it moved; then flips the switch and asserts a
    second attempt is REFUSED BY NAME (`recalibration_disabled`, not
    `max_delta` or `class_not_movable`, which would mean the envelope refused
    it and the switch was never consulted).
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.kb.recalibration import (
    Envelope, EnvelopeViolation, apply_outcome, provenance_for,
    recalibration_enabled)
from backend.server import create_app, create_services

TOKEN = "kill-switch-token"
BELIEF = ("mean placebo-corrected QTc change at 600mg supratherapeutic dose",
          "was", "11.4ms")


@pytest.fixture
def deployment(tmp_path, monkeypatch, recalibration_on):
    """A store with one belief, a policy that permits movement, and one
    movement already applied — the state a real deployment reaches before
    anyone reaches for the switch."""
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    services = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots")
    subject, relation, obj = BELIEF
    belief_id = services.store.add(
        subject=subject, relation=relation, object=obj, weight=0.8, tier=1,
        granularity=Granularity.TOPIC, source="human_assertion")
    envelope = Envelope(version="kill-switch-probe", max_delta=0.10,
                        movable_classes=("asserted",))
    services.recalibration.record_envelope(envelope, actor="operator",
                                          reason="integration fixture")
    return services, belief_id, envelope


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_the_system_is_coherent_with_recalibration_off(
        deployment, monkeypatch):
    """One test, three surfaces, in the order an operator would consult them."""
    services, belief_id, envelope = deployment

    # --- NON-VACUITY, part one: a movement really happened ------------------
    before = services.store.effective_weight(belief_id)
    apply_outcome(services.store, services.recalibration,
                  belief_id=belief_id, delta=-0.05, envelope=envelope,
                  actor="operator")
    moved = services.store.effective_weight(belief_id)
    assert moved == pytest.approx(before - 0.05), (
        "the fixture never moved the weight, so everything below would pass "
        "on a system where nothing was tried")
    assert services.recalibration.events_for(belief_id), (
        "no recalibration event was recorded — the trace assertions below "
        "would be asserting an empty trace is empty")

    # --- the switch --------------------------------------------------------
    monkeypatch.setenv("NEXUS_RECALIBRATION", "off")
    assert recalibration_enabled() is False

    # --- NON-VACUITY, part two: refused BY THE SWITCH, by name -------------
    with pytest.raises(EnvelopeViolation) as ex:
        apply_outcome(services.store, services.recalibration,
                      belief_id=belief_id, delta=-0.01, envelope=envelope,
                      actor="operator")
    assert ex.value.rule == "recalibration_disabled", (
        f"the attempt was refused as {ex.value.rule!r}. The envelope refused "
        f"it, not the kill switch, so this test would pass with the switch "
        f"doing nothing at all — the delta and class must be within the "
        f"policy for the refusal to be attributable.")

    client = TestClient(create_app(services))

    # --- surface 1: the policy says the machinery is disabled --------------
    env_body = client.get("/governance/envelope", headers=_auth()).json()
    assert env_body["enabled"] is False, (
        "the policy surface still reports recalibration enabled; an operator "
        "reading it would think the switch had not taken")
    # `enabled` and `active` are separate fields on purpose (S.2): the switch
    # DISABLES the machinery, it does not UNINSTALL the policy, and an
    # operator turning it back on must get their own envelope rather than the
    # default that permits nothing.
    assert env_body["active"]["version"] == "kill-switch-probe", (
        "the installed policy vanished with the switch — it is disabled, not "
        "uninstalled, and the distinction is what makes it reversible")

    # --- surface 2: inert, and SAYING SO -----------------------------------
    prov = client.get(f"/belief/{belief_id}/provenance", headers=_auth()).json()
    assert prov["recalibration_enabled"] is False
    assert prov["steps"], (
        "the trace is empty. The record was not erased — the switch makes it "
        "INERT — and an empty trace reads as 'nothing was ever attempted', "
        "which is a different and untrue claim.")
    assert prov["recalibration_adjustment"] == 0.0, (
        "the switch is off and the recorded delta is still being applied")
    # `coverage` is present even when `steps` is empty, by design — it is the
    # field that says what the trace can and cannot answer. With the switch
    # off it is the difference between "no decisions were made" and "decisions
    # were made and are currently inert".
    assert prov["coverage"], "the trace does not say what it covers"
    assert prov["refused"], (
        "the refused attempt is missing from the trace. An operator looking "
        "at a belief that did not move needs to see that something TRIED — "
        "S.2's refusals are recorded before they raise for exactly this")
    # The weight is back where it started: the record survives, its effect
    # does not. That pair is the whole meaning of "reversible".
    assert services.store.effective_weight(belief_id) == pytest.approx(before)

    # --- surface 3: KAIROS seals the RECORDS and moves NOTHING --------------
    #
    # THIS IS NOT WHAT I EXPECTED, AND THE SYSTEM IS RIGHT. The proposal for
    # this test said KAIROS should "seal nothing and declare why". It seals
    # three records, and it should: `maybe_seal_governance` gates on the seal
    # MODE and on whether anything is pending, never on
    # `recalibration_enabled()`. The kill switch suspends MOVEMENT; the chain
    # seals RECORDS — and the refusal above is a record. An operator who
    # turned the switch off and later has to show what the system did while it
    # was off is exactly who needs that sealed.
    #
    # Asserting the expectation instead of the behaviour would have written a
    # worse system into a test.
    services.kairos.session_ended()
    result = asyncio.run(services.kairos.run_one_cycle(force_snapshot=False))
    assert result.skipped is False, "the cycle did not run at all"
    assert result.cycle_error == "", f"the cycle failed: {result.cycle_error}"
    assert result.governance_sealed_records >= 1, (
        "the audit trail was not sealed. The switch stops movement, not "
        "record-keeping — a refusal nobody can prove happened is not a "
        "refusal anyone can rely on.")
    assert result.governance_skipped_reason == "", (
        "KAIROS reported a skip reason while also sealing — the two must not "
        "both be set, or U.1's contradiction is back")
    # And the seal did not resurrect the adjustment.
    assert services.store.effective_weight(belief_id) == pytest.approx(before)

    # --- and the trace still resolves through the API, not only in-process --
    trace = provenance_for(services.store, services.recalibration,
                           belief_id=belief_id)
    assert trace.enabled is False and trace.steps


def test_turning_it_back_on_restores_the_movement(deployment, monkeypatch):
    """The other half of reversible, and the guard against a switch that
    quietly discards rather than suspends.

    If `off` deleted the events instead of ignoring them, every assertion
    above would still pass and this one would not.
    """
    services, belief_id, envelope = deployment
    before = services.store.effective_weight(belief_id)
    apply_outcome(services.store, services.recalibration,
                  belief_id=belief_id, delta=-0.05, envelope=envelope,
                  actor="operator")

    monkeypatch.setenv("NEXUS_RECALIBRATION", "off")
    assert services.store.effective_weight(belief_id) == pytest.approx(before)

    monkeypatch.setenv("NEXUS_RECALIBRATION", "on")
    assert services.store.effective_weight(belief_id) == pytest.approx(
        before - 0.05), (
        "the adjustment did not come back, so `off` discarded rather than "
        "suspended — the switch is not reversible and the envelope's audit "
        "trail has a hole in it")
