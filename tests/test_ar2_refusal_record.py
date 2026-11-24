"""Packet AR.2, re-drawn by AT.1 — which refusals on /outcome/confirm record.

AR.2 recorded every named refusal on this route. AT argued against that
build and the operator adopted the line: a refusal is RECORDED when a
well-formed, in-vocabulary request asked for a governed act and the system
declined ("would not" — a governance act); a request that was never valid
(schema, closed vocabulary, an unresolvable reference, an over-long scope)
is a CLIENT ERROR, declared on the response and recorded nowhere; a system
that could not is state, declared elsewhere. Recording client errors made
the ledger's scope unstatable — the same misconfigured caller's schema
error leaves no trace, so completeness over attempts was unreachable.

Rows AR.2 already wrote stay (forward-only). This file pins the line on
this route: the two "would not" paths record, the five "never valid" paths
do not, and the schema boundary is stated.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.server import create_app, create_services

TOKEN = "ar2-token"
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
    return svc, TestClient(create_app(svc)), rec.lineage_id


def _body(lineage_id, **extra):
    return {"event_type": "qc", "decision": "batch_pass",
            "direction": "confirming",
            "references": {"lineage_id": lineage_id},
            "magnitude": 0.05, "actor_id": "lims", "confirmed": True, **extra}


def _recorded(svc):
    return [o for o in svc.recalibration.outcomes(limit=100) if not o.accepted]


NEVER_VALID = [
    ("unknown_direction",     {"direction": "sideways"}),
    ("unknown_actor_role",    {"actor_role": "robot"}),
    ("unknown_actor_source",  {"actor_source": "ldap"}),
    ("claim_id_unresolvable", {"references": {"claim_id": "c-1"}}),
    ("scope_too_long",        {"scope": "s" * 300}),
]


@pytest.mark.parametrize("error,extra", NEVER_VALID, ids=[r[0] for r in NEVER_VALID])
def test_a_never_valid_request_is_declared_and_not_recorded(rig, error, extra):
    """THE LINE, on the client-error side: named on the response, nothing
    on the ledger, and the response does not claim otherwise."""
    svc, client, lid = rig
    before = len(_recorded(svc))
    r = client.post("/outcome/confirm", json=_body(lid, **extra), headers=H)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"] == error
    assert "recorded" not in body and "event_id" not in body, body
    assert len(_recorded(svc)) == before, "a client error was ledgered"


def test_the_two_would_not_paths_record(rig):
    """The governance side: the Law 3 gate and no_beliefs_resolved are
    well-formed requests the system declined, and both write a row."""
    svc, client, lid = rig
    r = client.post("/outcome/confirm", json=_body(lid, confirmed=False), headers=H)
    assert r.status_code == 422 and r.json()["recorded"] is True
    ev = svc.recalibration.get_outcome(r.json()["event_id"])
    assert ev.accepted is False and "confirmed=true" in ev.refusal_reason
    assert ev.actor.id == "lims"

    r2 = client.post("/outcome/confirm",
                     json=_body("no-such-lineage"), headers=H)
    assert r2.status_code == 422 and r2.json()["error"] == "no_beliefs_resolved"
    assert r2.json()["recorded"] is True
    assert len(_recorded(svc)) == 2


def test_a_schema_refusal_is_the_declared_boundary_and_records_nothing(rig):
    """A body the parser rejects never reaches the handler; nothing can be
    recorded because nothing is known."""
    svc, client, lid = rig
    before = len(_recorded(svc))
    body = _body(lid)
    del body["confirmed"]
    r = client.post("/outcome/confirm", json=body, headers=H)
    assert r.status_code == 422
    assert "detail" in r.json() and "error" not in r.json()
    assert len(_recorded(svc)) == before


def test_the_read_surface_lists_the_recorded_refusals_with_their_ledger(rig):
    svc, client, lid = rig
    client.post("/outcome/confirm", json=_body(lid, confirmed=False), headers=H)
    client.post("/outcome/confirm", json=_body(lid, actor_role="robot"), headers=H)
    rows = client.get("/governance/refusals", headers=H).json()["refusals"]
    assert len(rows) == 1, "the client error must not appear on the read surface"
    assert rows[0]["ledger"] == "outcome_events"
    assert rows[0]["route"] == "/outcome/confirm"
    assert rows[0]["rule"].startswith("Law 3")
