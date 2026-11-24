"""AR (advice recall), the readers' check — a supersede is an outcome row,
and every reader of outcome rows was asked whether it counts one.

READERS OF OUTCOME ROWS, AND THEIR SELECTION
  - calibration resolution (`resolve_for_outcome`, reached from
    /outcome/confirm): does not read the table; it acts on the REQUEST
    being recorded, for any direction other than `neutral` when a
    `lineage_id` is referenced. A supersede referencing a lineage_id would
    therefore have resolved the stated confidences of that turn as if the
    world had answered them. FIXED: a supersede resolves none, declared on
    the response like `neutral` (T-A, with a companion that shows the same
    request without the reason resolving rows);
  - forecast scoring (`forecast.py:299`) reads `resolved_outcome` on the
    calibration rows, so it inherits the fix (T-A asserts the rows stay
    unresolved);
  - the refusal readers (`refusals`, `refusal_coverage`) select
    `accepted = 0`; an accepted supersede is outside them by construction,
    and a Law-3-refused supersede attempt is a refusal with no recall
    fields (T-C);
  - `superseded()` — the recall coverage — selected on `notes`. `notes` is
    outside the outcome hash (OQ-5), so a sealed row's notes can be
    rewritten under an intact seal. FIXED: it selects on
    `recall_count IS NOT NULL`, the hashed field (T-B, with the companion
    that edits `notes` on a sealed row and shows the count does not move);
  - `unsealed()` and `verify_governance_records` include the row: a
    supersede is a governance row and is sealed like one (T-D);
  - `provenance_for` resolves a recalibration's `outcome_ref` to its
    outcome: a supersede that moved standing appears as that step's
    outcome, which is correct — it did move it (T-D asserts it resolves).
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.eval import calibration_log as cal
from backend.kb.recalibration import Envelope, RecalibrationStore
from backend.kb.tiered_store import classify
from backend.memory.chain import NexusArchiveChain
from backend.server import create_app, create_services
from tests.fixtures.advice_recall_fixture import SOURCE, build

TOKEN = "advice-recall-readers"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    fx = build(tmp_path)
    rc = RecalibrationStore(fx.kb_path)
    rc.record_envelope(Envelope(version="v1", max_delta=0.1, movable_classes=(classify(SOURCE),)),
                       actor="board", reason="readers fixture")
    rc.close()
    svc = create_services(kb_db_path=fx.kb_path, chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl", archive_db_path=fx.archive_path,
                          snapshots_dir=tmp_path / "snap")
    return fx, svc, TestClient(create_app(svc))


def _outcome(client, references, *, notes, direction="contradicting", confirmed=True):
    return client.post("/outcome/confirm", json={
        "event_type": "review", "decision": "superseded", "direction": direction,
        "references": references, "magnitude": 0.05, "notes": notes,
        "actor_id": "alice", "actor_role": "person", "confirmed": confirmed}, headers=H)


# ---------------------------------------------------------------------------
# T-A — calibration resolution (and through it, forecast scoring)
# ---------------------------------------------------------------------------

def test_a_supersede_resolves_no_calibration_row(rig):
    """A supersede says a newer revision replaced the claim; it is not the world's answer
    to a stated confidence, so no calibration row is resolved and the forecast's
    `resolved_outcome` stays None."""
    fx, _svc, client = rig
    lid = fx.lineage_ids[0]                       # a sealed answer that injected rev3
    eid = cal.log_confidence("triple", 0.8, belief_id=fx.rev3, context={"lineage_id": lid})
    assert eid and cal.latest_by_entry()[eid]["resolved_outcome"] is None
    r = _outcome(client, {"lineage_id": lid}, notes="superseded_by: rev4").json()
    assert r["recall_count"] == 7, r
    assert r["calibration_resolved"] == 0
    assert "supersede" in r["calibration_note"]
    assert cal.latest_by_entry()[eid]["resolved_outcome"] is None, "the forecast's input is untouched"


def test_companion_the_same_request_without_the_reason_resolves_the_row(rig):
    """The exclusion is the reason, not the reference: the identical outcome
    without `superseded_by:` resolves the stated confidence."""
    fx, _svc, client = rig
    lid = fx.lineage_ids[0]
    eid = cal.log_confidence("triple", 0.8, belief_id=fx.rev3, context={"lineage_id": lid})
    r = _outcome(client, {"lineage_id": lid}, notes="reviewed against rev4").json()
    assert "recall_count" not in r
    assert r["calibration_resolved"] == 1
    assert cal.latest_by_entry()[eid]["resolved_outcome"] is False


# ---------------------------------------------------------------------------
# T-B — superseded() selects on the hashed field
# ---------------------------------------------------------------------------

def _seal_governance(svc, fx) -> int:
    chain = NexusArchiveChain(fx.archive_path)
    pending = svc.recalibration.unsealed()
    hashes = [h for _k, h in pending]
    block = chain.append({"kind": "governance", "governance_seal": {
        "count": len(hashes), "record_hashes": hashes}}, "", kind="governance")
    svc.recalibration.mark_sealed([k for k, _h in pending], block.index)
    chain.close()
    return block.index


def test_superseded_selects_on_recall_count_not_notes(rig):
    """`notes` is outside the outcome hash; the coverage count reads the hashed field, so
    rewriting a sealed row's notes moves nothing."""
    fx, svc, client = rig
    sup = _outcome(client, {"belief_ids": [fx.rev3]}, notes="superseded_by: rev4").json()
    plain = _outcome(client, {"belief_ids": [fx.rev4]}, notes="reviewed, kept").json()
    assert svc.recalibration.superseded() == {"events": 1, "claims": [fx.rev3],
                                              "since": svc.recalibration.get_outcome(sup["event_id"]).ts}
    _seal_governance(svc, fx)
    # The write nobody may make, on the sealed row and on its neighbour.
    from backend.store import connect as store_connect
    from tests.conftest import store_target_for
    c = store_connect(store_target_for(fx.kb_path))
    c.execute("UPDATE outcome_events SET notes = 'reviewed, kept' WHERE event_id = ?", (sup["event_id"],))
    c.execute("UPDATE outcome_events SET notes = 'superseded_by: rev9' WHERE event_id = ?", (plain["event_id"],))
    c.commit(); c.close()
    after = svc.recalibration.superseded()
    assert after["events"] == 1 and after["claims"] == [fx.rev3], after
    # And the seal did not notice, which is exactly why notes cannot select.
    assert svc.recalibration.verify_governance_records()["verdict"] == "intact"
    stats = client.get("/cortex/stats").json()["recall"]
    assert (stats["superseded_claims"], stats["records_resting"]) == (1, 7)


# ---------------------------------------------------------------------------
# T-C — the refusal readers
# ---------------------------------------------------------------------------

def test_refusal_readers_exclude_an_accepted_supersede(rig):
    fx, svc, client = rig
    before = svc.recalibration.refusal_coverage()["by_ledger"]["outcome_events"]
    sup = _outcome(client, {"belief_ids": [fx.rev3]}, notes="superseded_by: rev4").json()
    assert sup["recall_count"] == 7
    assert svc.recalibration.refusal_coverage()["by_ledger"]["outcome_events"] == before
    assert sup["event_id"] not in {r["event_id"] for r in svc.recalibration.refusals(limit=100)}
    # A Law-3-refused supersede attempt is a refusal, and carries no recall fields.
    refused = _outcome(client, {"belief_ids": [fx.rev3]}, notes="superseded_by: rev4", confirmed=False)
    assert refused.status_code == 422 and refused.json()["recorded"] is True
    row = svc.recalibration.get_outcome(refused.json()["event_id"])
    assert row.accepted is False and row.recall_count is None and row.recall_route is None
    assert svc.recalibration.refusal_coverage()["by_ledger"]["outcome_events"] == before + 1
    assert svc.recalibration.superseded()["events"] == 1, "the refused attempt is not a supersede"


# ---------------------------------------------------------------------------
# T-D — sealed like any governance row; resolved by provenance
# ---------------------------------------------------------------------------

def test_a_supersede_is_sealed_and_resolves_in_provenance(rig):
    fx, svc, client = rig
    sup = _outcome(client, {"belief_ids": [fx.rev3]}, notes="superseded_by: rev4").json()
    assert sup["event_id"] in {k for k, _h in svc.recalibration.unsealed()}
    _seal_governance(svc, fx)
    assert svc.recalibration.get_outcome(sup["event_id"]).sealed_block is not None
    assert svc.recalibration.verify_governance_records()["verdict"] == "intact"
    prov = client.get(f"/belief/{fx.rev3}/provenance", headers=H).json()
    step = next(s for s in prov["steps"] if s["outcome_ref"] == sup["event_id"])
    assert step["outcome_resolved"] is True and step["outcome"]["notes"] == "superseded_by: rev4"
