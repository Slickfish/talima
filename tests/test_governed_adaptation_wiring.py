"""Packet S.2 — the parts of governed adaptation that live outside the module.

`test_governed_adaptation.py` holds S.1's guards: the record layer does not
move, the envelope bounds the move, the bound is itself an event. This file
covers what S.2 had to wire for those to mean anything in a running system —
the read path, the seal, the endpoints, and the reversal.

WHY THE SEAL IS HERE AND NOT AN AFTERTHOUGHT
    An envelope change that is not chain-covered is exactly as forgeable as a
    recalibration that is not, and the envelope is the thing that says the
    recalibration was in bounds. Sealing one without the other would produce a
    record that proves a weight moved and cannot prove it was allowed to.

WHAT THESE TESTS DO NOT ASSERT
    That the seal is timely. It rides the KAIROS snapshot cadence, default 30
    days, and `kairos.py:33-40` already flags that as a known unseparated
    design question. S.0.7 raised it; S.2 inherits it rather than fixing it,
    and saying so here is better than a test that quietly implies otherwise.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.kb.recalibration import (
    DEFAULT_ENVELOPE,
    Actor,
    Envelope,
    EnvelopeViolation,
    RecalibrationStore,
    apply_outcome,
    recalibration_enabled,
)
from backend.kb.tiered_store import TIER_ENTRY, TripleStore, effective_prominence
from backend.server import create_app, create_services

TOKEN = "packet-s2-token"


def _services(tmp_path):
    return create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots",
    )


def _seed(services) -> dict:
    ids = {}
    for s, r, o, w, tier, src in [
        ("QTc",      "stands_for",  "corrected QT", 0.55, 2, "extraction"),
        ("ganymede", "orbits",      "jupiter",      0.90, 1, "wikidata"),
        ("dose",     "was",         "600mg",        0.95, 1, "human_assertion"),
    ]:
        ids[s] = services.store.add(subject=s, relation=r, object=o, weight=w,
                                    tier=tier, granularity=Granularity.TOPIC,
                                    source=src)
    return ids


def _install(services, **kw) -> Envelope:
    env = Envelope(version=kw.pop("version", "v1"),
                   max_delta=kw.pop("max_delta", 0.05),
                   movable_classes=kw.pop("movable_classes", ("extraction",)),
                   **kw)
    services.recalibration.record_envelope(
        env, actor="operator", reason="sentinel policy")
    return env


# ---------------------------------------------------------------------------
# The read path — the third factor actually reaches ranking and banding
# ---------------------------------------------------------------------------

def test_the_store_is_wired_to_its_own_recalibrations(tmp_path):
    """NON-VACUITY for everything below. `TripleStore.recalibration` is an
    injected hook defaulting to None; if `create_services` stopped binding it,
    every adjustment would silently read as zero and each test here would pass
    while measuring a system with no governance in it."""
    services = _services(tmp_path)
    assert services.store.recalibration is services.recalibration is not None
    assert services.store.recalibration.db_path == services.store.db_path, (
        "the governance record is in a different database from the beliefs it "
        "governs")


def test_a_recalibration_moves_effective_weight_and_not_the_record(tmp_path, recalibration_on):
    services = _services(tmp_path)
    ids = _seed(services)
    env = _install(services)
    bid = ids["QTc"]

    before_effective = services.store.effective_weight(bid)
    before_stored = services.store.get_by_id(bid).weight

    apply_outcome(services.store, services.recalibration, belief_id=bid,
                  delta=-0.05, envelope=env, actor="operator",
                  outcome_ref="o1")

    assert services.store.effective_weight(bid) == pytest.approx(
        before_effective - 0.05)
    assert services.store.get_by_id(bid).weight == pytest.approx(before_stored)


def test_recalibration_changes_retrieval_order(tmp_path, recalibration_on):
    """S.0.8 named this as the interaction that matters: a governance record
    nothing ranks by is a record of a decision that had no effect.

    Two beliefs in the SAME tier so the tier key cannot decide it, and the
    assertion is on the order before and after — not on a threshold, which
    would pass on a system where nothing was ranked at all.
    """
    from backend.kb.retriever import TripleRetriever

    services = _services(tmp_path)
    a = services.store.add(subject="alpha", relation="is_a", object="thing",
                           weight=0.60, tier=2, granularity=Granularity.TOPIC,
                           source="extraction")
    b = services.store.add(subject="alpha", relation="is_a", object="widget",
                           weight=0.58, tier=2, granularity=Granularity.TOPIC,
                           source="extraction")
    retriever = TripleRetriever(services.store)

    order_before = [rt.triple.id for rt in retriever.retrieve("alpha", top_k=5)]
    assert order_before.index(a) < order_before.index(b), (
        "the fixture does not start in the order this test assumes")

    env = _install(services, max_delta=0.10)
    apply_outcome(services.store, services.recalibration, belief_id=a,
                  delta=-0.10, envelope=env, actor="operator", outcome_ref="o1")

    order_after = [rt.triple.id for rt in retriever.retrieve("alpha", top_k=5)]
    assert order_after.index(b) < order_after.index(a), (
        f"a recorded recalibration did not change retrieval order: "
        f"{order_before} -> {order_after}")


def test_recalibration_governs_the_tier_band_and_rebalance_stays_idempotent(tmp_path, recalibration_on):
    """F.3's requirement, carried into the third factor.

    `adjustments()` is a pure sum over stored rows, so a projection run twice
    reads the same number. If it ever became "as of now", `rebalance()` would
    cycle and F.3's whole argument would be undone.
    """
    services = _services(tmp_path)
    bid = services.store.add(subject="beta", relation="is_a", object="thing",
                             weight=0.95, tier=0, granularity=Granularity.TOPIC,
                             source="wikidata")
    assert services.store.effective_weight(bid) >= TIER_ENTRY[0]

    env = _install(services, max_delta=0.40, movable_classes=("external",))
    apply_outcome(services.store, services.recalibration, belief_id=bid,
                  delta=-0.40, envelope=env, actor="operator", outcome_ref="o1")

    first = services.store.rebalance()
    tier_after = services.store.get_by_id(bid).tier
    assert tier_after > 0, (
        "a 0.40 downward recalibration left the belief in tier 0")

    second = services.store.rebalance()
    assert services.store.get_by_id(bid).tier == tier_after, (
        "rebalance is no longer idempotent under recalibration")
    assert (second["promoted"], second["demoted"]) == (0, 0), (
        f"the second pass still moved rows: {second}")
    assert first["demoted"] >= 1


# ---------------------------------------------------------------------------
# Refusals are recorded (I-6)
# ---------------------------------------------------------------------------

def test_a_refused_recalibration_is_recorded_before_it_raises(tmp_path, recalibration_on):
    """A caller cannot make a declined attempt disappear by catching the
    exception. A governance record holding only what succeeded describes a
    system that never declines anything."""
    services = _services(tmp_path)
    ids = _seed(services)
    env = _install(services)
    bid = ids["QTc"]

    with pytest.raises(EnvelopeViolation):
        apply_outcome(services.store, services.recalibration, belief_id=bid,
                      delta=-0.40, envelope=env, actor="operator",
                      outcome_ref="o1")

    assert services.recalibration.events_for(bid) == []
    attempts = services.recalibration.all_events_for(bid)
    assert len(attempts) == 1, "the refused attempt was not recorded"
    assert attempts[0].applied is False
    assert attempts[0].refusal_rule == "max_delta"
    assert "max_delta" in attempts[0].refusal_detail
    assert attempts[0].actor.id == "operator"
    assert attempts[0].envelope_version == env.version, (
        "the refusal does not name the policy that refused it")


@pytest.mark.parametrize("case,delta,classes,frozen,rule", [
    ("over the delta",   -0.40, ("extraction",), (),      "max_delta"),
    ("immovable class",  -0.01, ("external",),   (),      "class_not_movable"),
    ("frozen belief",    -0.01, ("extraction",), "self",  "frozen_belief"),
])
def test_every_envelope_rule_refuses_and_names_itself(tmp_path, case, delta, classes, frozen, rule, recalibration_on):
    """Each rule reachable, each naming itself. A rule nothing can trigger
    reads as satisfied on inspection — the C10 shape."""
    services = _services(tmp_path)
    ids = _seed(services)
    bid = ids["QTc"]
    env = _install(services, max_delta=0.05, movable_classes=classes,
                   frozen_belief_ids=(bid,) if frozen else ())

    with pytest.raises(EnvelopeViolation) as ex:
        apply_outcome(services.store, services.recalibration, belief_id=bid,
                      delta=delta, envelope=env, actor="operator")
    assert ex.value.rule == rule, f"{case}: refused by {ex.value.rule!r}"


def test_the_cumulative_cap_counts_magnitude_not_net(tmp_path, recalibration_on):
    """A belief walked up and down ten times has moved, whatever the net says.
    Summing signed deltas would let an unbounded number of moves hide behind a
    net of zero."""
    services = _services(tmp_path)
    ids = _seed(services)
    env = _install(services, max_delta=0.05, cumulative_max=0.08)
    bid = ids["QTc"]

    apply_outcome(services.store, services.recalibration, belief_id=bid,
                  delta=-0.05, envelope=env, actor="operator")
    apply_outcome(services.store, services.recalibration, belief_id=bid,
                  delta=+0.03, envelope=env, actor="operator")
    assert services.recalibration.cumulative_movement(bid) == pytest.approx(0.08)

    with pytest.raises(EnvelopeViolation) as ex:
        apply_outcome(services.store, services.recalibration, belief_id=bid,
                      delta=+0.01, envelope=env, actor="operator")
    assert ex.value.rule == "cumulative_max"


def test_a_policy_version_cannot_be_reused(tmp_path):
    """A version is a name for a decision. Reusing one to mean something else
    makes every recalibration citing it ambiguous — the `MAX()` defect in
    different clothing."""
    services = _services(tmp_path)
    _install(services, version="v1")
    with pytest.raises(EnvelopeViolation) as ex:
        _install(services, version="v1", max_delta=0.99)
    assert ex.value.rule == "version_reused"


# ---------------------------------------------------------------------------
# Reversibility (E.2, Q.2 precedent)
# ---------------------------------------------------------------------------

def test_the_default_policy_moves_nothing(tmp_path, recalibration_on):
    """A deployment that installs S.2 and sets no policy behaves exactly as it
    did before S.2 — and the reason is auditable: "nothing may move" is a
    policy someone can be shown, not a constant someone compiled."""
    services = _services(tmp_path)
    ids = _seed(services)
    assert services.recalibration.active_envelope() == DEFAULT_ENVELOPE
    with pytest.raises(EnvelopeViolation) as ex:
        apply_outcome(services.store, services.recalibration,
                      belief_id=ids["QTc"], delta=0.01, actor="operator")
    assert ex.value.rule in {"class_not_movable", "max_delta"}


def test_the_kill_switch_stops_every_move(tmp_path, monkeypatch, recalibration_on):
    """`NEXUS_RECALIBRATION=off` — for when the question is not "what is
    permitted" but "stop". Both halves: nothing new applies, and nothing
    already recorded still counts."""
    services = _services(tmp_path)
    ids = _seed(services)
    env = _install(services)
    bid = ids["QTc"]

    apply_outcome(services.store, services.recalibration, belief_id=bid,
                  delta=-0.05, envelope=env, actor="operator")
    moved = services.store.effective_weight(bid)

    monkeypatch.setenv("NEXUS_RECALIBRATION", "off")
    assert recalibration_enabled() is False
    assert services.store.adjustment_for(bid) == 0.0, (
        "an existing adjustment still applies with the switch off")
    assert services.store.effective_weight(bid) == pytest.approx(moved + 0.05)

    with pytest.raises(EnvelopeViolation) as ex:
        apply_outcome(services.store, services.recalibration, belief_id=bid,
                      delta=-0.01, envelope=env, actor="operator")
    assert ex.value.rule == "recalibration_disabled"


# ---------------------------------------------------------------------------
# Sealing (S.0.7) — the lineage path, reused
# ---------------------------------------------------------------------------

def test_governance_records_are_sealed_into_the_chain(tmp_path, recalibration_on):
    services = _services(tmp_path)
    ids = _seed(services)
    env = _install(services)
    apply_outcome(services.store, services.recalibration, belief_id=ids["QTc"],
                  delta=-0.05, envelope=env, actor="operator", outcome_ref="o1")

    unsealed_before = services.recalibration.unsealed()
    assert unsealed_before, "nothing was pending a seal; the fixture is empty"

    services.hippocampus.ingest_triple("QTc", "stands_for", "corrected QT")
    block = asyncio.run(services.kairos.maybe_snapshot_and_fingerprint(force=True))
    assert block is not None, (
        f"no block was appended: {services.kairos.last_snapshot_skip_reason}")

    seal = block.fingerprint.get("recalibration_seal")
    assert seal is not None, (
        "the block carries no recalibration_seal; the governance record is "
        "not chain-covered")
    assert seal["count"] == len(unsealed_before)
    assert seal["digest"] == RecalibrationStore.seal_digest(
        [h for _k, h in unsealed_before])
    assert services.recalibration.unsealed() == [], "nothing was marked sealed"

    # ...and the envelope rode the same block as the recalibration it permitted
    history = services.recalibration.envelope_history()
    assert history[0].sealed_block == block.index, (
        "the policy was not sealed alongside the move it authorised")


def test_the_seal_survives_when_nothing_is_pending(tmp_path):
    """The other direction: a cycle with no governance activity must not mint
    an empty seal key, or `count: 0` starts appearing in blocks and a verifier
    cannot tell "nothing happened" from "the seal was dropped"."""
    services = _services(tmp_path)
    services.hippocampus.ingest_triple("a", "is_a", "b")
    block = asyncio.run(services.kairos.maybe_snapshot_and_fingerprint(force=True))
    assert block is not None
    assert "recalibration_seal" not in block.fingerprint


# ---------------------------------------------------------------------------
# The endpoints (Law 3)
# ---------------------------------------------------------------------------

def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    services = _services(tmp_path)
    return TestClient(create_app(services)), services


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_outcome_without_confirmation_is_refused_and_recorded(
        tmp_path, monkeypatch):
    """Law 3, and I-6 on top of it: the refused attempt is part of the story."""
    client, services = _client(tmp_path, monkeypatch)
    ids = _seed(services)
    with client:
        r = client.post("/outcome/confirm", headers=_auth(), json={
            "event_type": "review", "decision": "approved",
            "direction": "confirming", "references": {"belief_ids": [ids["QTc"]]},
            "magnitude": 0.05, "confirmed": False})
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["recorded"] is True
    ev = services.recalibration.get_outcome(body["event_id"])
    assert ev is not None and ev.accepted is False
    assert "confirmed=true" in ev.refusal_reason


def test_a_confirmed_outcome_reports_what_moved_and_what_did_not(tmp_path, monkeypatch, recalibration_on):
    """DECLARED, NOT SILENT. One belief in a movable class and one not, in the
    same request, so the response has to carry both halves."""
    client, services = _client(tmp_path, monkeypatch)
    ids = _seed(services)
    _install(services, max_delta=0.05, movable_classes=("extraction",))
    with client:
        r = client.post("/outcome/confirm", headers=_auth(), json={
            "event_type": "review", "decision": "rejected",
            "direction": "contradicting",
            "references": {"belief_ids": [ids["QTc"], ids["ganymede"]]},
            "magnitude": 0.05, "confirmed": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [a["belief_id"] for a in body["applied"]] == [ids["QTc"]]
    assert [d["belief_id"] for d in body["refused"]] == [ids["ganymede"]]
    assert body["refused"][0]["rule"] == "class_not_movable"
    assert body["applied"][0]["delta"] == pytest.approx(-0.05), (
        "direction 'contradicting' did not produce a negative delta")
    assert body["envelope"]["version"] == "v1"


def test_claim_id_is_refused_rather_than_ignored(tmp_path, monkeypatch):
    """S.0.2 measured that `claim_id` appears nowhere in NEXUS. Accepting it
    silently would surface as a 500 at the first integration; refusing it says
    so at the boundary, where a partner can read it."""
    client, _services_ = _client(tmp_path, monkeypatch)
    with client:
        r = client.post("/outcome/confirm", headers=_auth(), json={
            "event_type": "review", "decision": "approved",
            "direction": "confirming", "references": {"claim_id": "c-1"},
            "magnitude": 0.05, "confirmed": True})
    assert r.status_code == 422
    assert r.json()["error"] == "claim_id_unresolvable"


def test_an_outcome_can_reference_a_lineage_record(tmp_path, monkeypatch, recalibration_on):
    """The seam's primary reference: a reviewer approves an ANSWER, and the
    answer already names its beliefs. The fan-out rule follows the `cited`
    flag's three states, which `sealed_cited_belief_ids` defines once —
    explicit false is excluded, absent counts as cited."""
    client, services = _client(tmp_path, monkeypatch)
    ids = _seed(services)
    _install(services, max_delta=0.05, movable_classes=("extraction",))
    rec = services.lineage.record_turn(
        query="what is QTc?",
        injected=[
            {"belief_id": ids["QTc"], "weight_at_injection": 0.55,
             "tier_at_injection": 2, "cited": True},
            {"belief_id": ids["ganymede"], "weight_at_injection": 0.90,
             "tier_at_injection": 1, "cited": False},
        ],
        contradictions=[], model_id="test", response_text="QTc is corrected QT.",
        prompt_prefix="STABLE")

    with client:
        r = client.post("/outcome/confirm", headers=_auth(), json={
            "event_type": "qc", "decision": "approved",
            "direction": "confirming",
            "references": {"lineage_id": rec.lineage_id},
            "magnitude": 0.05, "confirmed": True})
    assert r.status_code == 200, r.text
    body = r.json()
    moved = [a["belief_id"] for a in body["applied"]]
    assert moved == [ids["QTc"]], (
        f"the explicitly-uncited belief was moved too: {body}")


def test_the_envelope_endpoint_reports_enabled_and_active_separately(tmp_path, monkeypatch, recalibration_on):
    """I-6. "The kill switch is off" and "the policy permits nothing" are
    different states, and an operator debugging why nothing moved needs to
    know which one they are in."""
    client, services = _client(tmp_path, monkeypatch)
    with client:
        first = client.get("/governance/envelope").json()
        assert first["enabled"] is True
        assert first["is_default"] is True
        assert first["active"]["max_delta"] == 0.0
        assert first["history"] == []

        r = client.post("/governance/envelope/confirm", headers=_auth(), json={
            "version": "board-2026-01", "max_delta": 0.05,
            "movable_classes": ["extraction"],
            "reason": "approved at change board", "confirmed": True})
        assert r.status_code == 200, r.text

        after = client.get("/governance/envelope").json()
    assert after["is_default"] is False
    assert after["active"]["version"] == "board-2026-01"
    assert [h["version"] for h in after["history"]] == ["board-2026-01"]
    assert after["history"][0]["reason"] == "approved at change board"
    assert after["history"][0]["actor"]["source"] == "local", (
        "actor_source is missing — the field exists so IdP propagation is a "
        "value change rather than a schema change")


def test_an_envelope_change_needs_a_reason(tmp_path, monkeypatch):
    client, _s = _client(tmp_path, monkeypatch)
    with client:
        r = client.post("/governance/envelope/confirm", headers=_auth(), json={
            "version": "v9", "max_delta": 0.05, "reason": "   ",
            "confirmed": True})
    assert r.status_code == 422
    assert r.json()["error"] == "reason_required"
