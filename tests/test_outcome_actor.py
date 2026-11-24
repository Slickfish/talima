"""Packet Z.1 — an outcome states who, or what, supplied it.

THE FALSE RECORD, MEASURED
    `POST /outcome/confirm  actor_id="lims-system"` stored
    `role='local_operator' source='local'` — a QC system recorded as a
    person, sealed into the chain with the false role inside the hashed
    body. `OutcomeConfirmReq` carried only `actor_id`; `_as_actor(str)`
    defaulted the rest. Not a gap: a record stating something untrue, and
    every outcome added another.

THE VOCABULARY, AND WHY NOT FREE TEXT
    `event_type` documents its intended values in a comment and constrains
    nothing — that is what free text is. `actor_role` is validated against a
    closed set (`person | source_system`) at the boundary, and it already
    rides the hashed body (`asdict(who)` at recalibration.py:402), so unlike
    a flag it cannot be lost without breaking the record hash. Proven below
    by rewriting one and watching verification fail.

    `actor_source` stays `{local, idp}` — it answers "how was this identity
    established", not "what kind of agent is this". The axes are orthogonal
    (a source system's identity can itself be IdP-attested), so it is
    validated, never overloaded.

THE DEFAULT
    An unstated role still stores `local_operator`, byte-identical to every
    row written before Z.1 — an existing caller keeps meaning what it meant:
    "the local human operator". The vocabulary constrains what can be
    STATED, not what silence means.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.server import create_app, create_services

TOKEN = "outcome-actor-token"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    bid = svc.store.add(subject="batch 47", relation="passed", object="qc",
                        weight=0.6, tier=1, granularity=Granularity.TOPIC,
                        source="human_assertion")
    rec = svc.lineage.record_turn(
        query="did batch 47 pass?",
        injected=[{"belief_id": bid, "weight_at_injection": 0.6,
                   "tier_at_injection": 1, "cited": True}],
        contradictions=[], model_id="m", response_text="yes",
        prompt_prefix="p")
    client = TestClient(create_app(svc))
    client.post("/governance/envelope/confirm", json={
        "version": "v1", "max_delta": 0.1, "movable_classes": ["asserted"],
        "reason": "Z.1 fixture", "confirmed": True}, headers=H)
    return svc, client, bid, rec.lineage_id


def _outcome(client, lineage_id, **extra):
    body = {"event_type": "qc", "decision": "batch_pass",
            "direction": "confirming",
            "references": {"lineage_id": lineage_id},
            "magnitude": 0.05, "workflow_ref": "LIMS/batch-47",
            "confirmed": True, **extra}
    return client.post("/outcome/confirm", json=body, headers=H)


def test_a_system_actor_is_recorded_as_source_system(rig):
    svc, client, bid, lid = rig
    r = _outcome(client, lid, actor_id="lims", actor_role="source_system")
    assert r.status_code == 200, r.text
    ev = svc.recalibration.get_outcome(r.json()["event_id"])
    assert (ev.actor.id, ev.actor.role, ev.actor.source) == \
        ("lims", "source_system", "local")
    # The recalibration event the outcome authorised carries the SAME actor —
    # the false record was in both tables, so the fix must be too.
    recal = svc.recalibration.events_for(bid)[-1]
    assert recal.actor.role == "source_system"


def test_an_unstated_role_defaults_and_the_default_is_honest(rig):
    """Byte-identical to pre-Z.1 rows: `local_operator`, meaning the local
    human operator. Silence keeps meaning what it meant."""
    svc, client, _bid, lid = rig
    r = _outcome(client, lid, actor_id="operator")
    ev = svc.recalibration.get_outcome(r.json()["event_id"])
    assert ev.actor.role == "local_operator"
    assert ev.actor.source == "local"


def test_a_role_outside_the_vocabulary_is_refused_by_name(rig):
    _svc, client, _bid, lid = rig
    r = _outcome(client, lid, actor_id="x", actor_role="robot")
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "unknown_actor_role"
    assert body["role"] == "robot"
    assert sorted(body["valid"]) == ["person", "source_system"], (
        "the refusal must name the vocabulary, or the caller learns the "
        "valid set by bisection")


def test_actor_source_is_validated_not_overloaded(rig):
    """`system` in actor_source is the overload Q4 argued against — refuse it
    by name; `idp` is the value the field exists for — accept it."""
    svc, client, _bid, lid = rig
    r = _outcome(client, lid, actor_id="x", actor_source="system")
    assert r.status_code == 422
    assert r.json()["error"] == "unknown_actor_source"
    assert sorted(r.json()["valid"]) == ["idp", "local"]

    r2 = _outcome(client, lid, actor_id="jane@corp",
                  actor_role="person", actor_source="idp")
    assert r2.status_code == 200
    ev = svc.recalibration.get_outcome(r2.json()["event_id"])
    assert (ev.actor.role, ev.actor.source) == ("person", "idp")


def test_a_law3_refusal_still_records_the_stated_actor(rig):
    """The refused-attempt record must carry the same actor the attempt
    stated — a refusal logged under a defaulted identity would be the Z.1
    defect surviving on exactly the rows that describe declined writes."""
    svc, client, _bid, lid = rig
    r = _outcome(client, lid, actor_id="lims", actor_role="source_system",
                 confirmed=False)
    assert r.status_code == 422
    ev = svc.recalibration.get_outcome(r.json()["event_id"])
    assert ev.accepted is False
    assert ev.actor.role == "source_system"


def test_system_and_person_render_differently_on_the_trace(rig):
    """NON-VACUITY for the vocabulary: recorded-but-invisible would be a
    distinction nobody can act on. Two outcomes, one belief, and the
    provenance trace must show which step was observed and which judged."""
    _svc, client, bid, lid = rig
    _outcome(client, lid, actor_id="lims", actor_role="source_system")
    _outcome(client, lid, actor_id="jane", actor_role="person")
    steps = client.get(f"/belief/{bid}/provenance", headers=H).json()["steps"]
    roles = [s["actor"]["role"] for s in steps]
    assert "source_system" in roles and "person" in roles
    assert roles.count("source_system") == 1, roles
    # And the outcome nested in each step carries it too.
    outcome_roles = {s["outcome"]["actor"]["role"] for s in steps
                     if s.get("outcome")}
    assert {"source_system", "person"} <= outcome_roles


def test_the_verifier_is_reachable_over_http(rig):
    """A verifier callable only from Python is the envelope-write-route
    defect (T.1) one surface over: the guarantee exists on inspection only.
    `/governance/verify` mirrors `/lineage/verify` — gated, layered, and it
    must catch the same rewrite the Python call catches."""
    svc, client, _bid, lid = rig
    r = _outcome(client, lid, actor_id="lims", actor_role="source_system")
    eid = r.json()["event_id"]
    svc.kairos.session_ended()
    asyncio.run(svc.kairos.run_one_cycle(force_snapshot=True))

    # A WRONG token, not a missing one: conftest patches TestClient to send
    # the valid token by default, so a bare request here is authenticated and
    # would prove nothing about the gate.
    assert client.get("/governance/verify",
                      headers={"Authorization": "Bearer wrong"}
                      ).status_code == 401, "the verify surface is not gated"
    v = client.get("/governance/verify", headers=H).json()
    assert v["verdict"] == "intact"
    assert {"row", "seal"} <= set(v), "layers are not reported separately"

    svc.recalibration.conn.execute(
        "UPDATE outcome_events SET actor_role = 'person' WHERE event_id = ?",
        (eid,))
    svc.recalibration.conn.commit()
    v2 = client.get("/governance/verify", headers=H).json()
    assert v2["verdict"] == "row_hash_mismatch"
    assert any(f["key"] == eid for f in v2["row"]["failures"])


def test_a_rewritten_role_breaks_the_seal(rig):
    """THE ONE THAT MATTERS MOST. The role is inside the hashed body, the
    hash is sealed into a governance block, so rewriting the role must fail
    verification — otherwise the vocabulary is advisory, not evidential.

    `verify_governance_records()` exists because of this test: the seal
    covered these records and NOTHING recomputed them — the same
    reachable-only-by-reading-pytest defect verify_sealed_lineage was built
    to end (E.1), one table over.
    """
    svc, client, _bid, lid = rig
    r = _outcome(client, lid, actor_id="lims", actor_role="source_system")
    eid = r.json()["event_id"]
    svc.kairos.session_ended()
    asyncio.run(svc.kairos.run_one_cycle(force_snapshot=True))

    v = svc.recalibration.verify_governance_records(svc.archive_chain)
    assert v["verdict"] == "intact", f"clean store fails: {v}"
    assert v["row"]["checked"] >= 2, "vacuous — nothing was checked"
    assert v["seal"]["sealed"] >= 1, "vacuous — nothing was sealed"

    svc.recalibration.conn.execute(
        "UPDATE outcome_events SET actor_role = 'person' WHERE event_id = ?",
        (eid,))
    svc.recalibration.conn.commit()

    v2 = svc.recalibration.verify_governance_records(svc.archive_chain)
    assert v2["verdict"] != "intact"
    assert any(f["key"] == eid and f["table"] == "outcome_events"
               for f in v2["row"]["failures"]), (
        f"the rewritten role was not caught by name: {v2['row']['failures']}")
