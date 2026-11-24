"""W0b — a zero's sign is not stored, and the governance verifier read that
as a content edit.

FOUND BY THE PRE-RUN DATA FIXTURE (W0b): on a store written by the product's
own routes, `verify_governance_records` reported `row_hash_mismatch` for one
recalibration event, with nothing tampered. The event belonged to a
contradicting outcome with the default zero magnitude, whose delta is
`DIRECTION_SIGN["contradicting"] * abs(0.0)` = -0.0. The writer hashed the
body with "-0.0" in it; SQLite stores an integral REAL as an integer and
reads it back +0.0; the verifier rebuilt the body from the row with "0.0"
and the hashes differed. Every refused recalibration of a contradicting
outcome at zero magnitude has failed verification since Z.1.

THE FIX (backend/kb/recalibration.py, RECAL_REAL_FIELDS): forward, the
event's four REAL fields are written with an unsigned zero, so the hash is
the one the row reads back; backward, the verifier accepts a stored zero
under either sign — the one bit a row cannot carry — and counts each row it
resolved that way (`row.zero_sign_legacy`), so a reader sees how many rows
rest on the rule. Sealed hashes are not rewritten (law 3).

PRE-FIX (law 16): the first two tests fail on the code before this change,
both with `row_hash_mismatch` (measured in a worktree at `f45a18f`). The
companions pin that the rule is no wider than a zero's sign; their verdict
assertions hold before the change too, and there they fail only on the new
count's key.
"""
from __future__ import annotations

import sys
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.kb.recalibration import Actor, RecalibrationStore, _canonical_hash
from backend.server import create_app, create_services

TOKEN = "w0b-zero-sign"
H = {"Authorization": f"Bearer {TOKEN}"}


class _Quiet:
    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "ok"

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return False


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl", archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap", provider=_Quiet())
    bid = svc.store.add(subject="QT interval", relation="is_a_segment_of", object="ECG", weight=0.9,
                        tier=1, granularity=Granularity.TOPIC, source="extraction")
    return svc, TestClient(create_app(svc)), bid


def _plant_legacy_row(rec: RecalibrationStore, belief_id: int, *, delta_hashed: float,
                      delta_stored: float, stored_weight_stored: float = 0.9) -> str:
    """A recalibration row as the pre-W0b writer left it: the hash computed
    over `delta_hashed`, the row holding `delta_stored` (SQLite drops a
    zero's sign either way). Built with the store's own body function."""
    eid, ts, before = str(uuid.uuid4()), time.time(), 0.585
    who = Actor("operator", "local_operator", "local")
    body = RecalibrationStore._recal_body(eid, ts, belief_id, delta_hashed, before, before, 0.9,
                                          "v0-immovable", who, "outcome-legacy", False,
                                          "class_not_movable", "default")
    rec.conn.execute(
        "INSERT INTO recalibration_events (event_id, ts, belief_id, delta, prominence_before, "
        "prominence_after, stored_weight, envelope_version, actor_id, actor_role, actor_source, "
        "outcome_ref, applied, refusal_rule, refusal_detail, record_hash, scope) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (eid, ts, belief_id, delta_stored, before, before, stored_weight_stored, "v0-immovable",
         who.id, who.role, who.source, "outcome-legacy", 0, "class_not_movable", "planted",
         _canonical_hash(body), "default"))
    rec.conn.commit()
    return eid


@pytest.mark.sqlite_only("SQLite's own behaviour is the premise: it stores -0.0 as 0.0 (W3b D6's declared difference; Postgres keeps the sign)")
def test_a_contradicting_outcome_at_zero_magnitude_writes_a_row_that_verifies(rig):
    """FAILS PRE-FIX (row_hash_mismatch). The route's own path: a
    contradicting outcome, magnitude left at its default, on the shipped
    immovable envelope (the recalibration is refused and recorded)."""
    svc, client, bid = rig
    r = client.post("/outcome/confirm", headers=H, json={
        "event_type": "review", "decision": "rejected", "direction": "contradicting",
        "references": {"belief_ids": [bid]}, "confirmed": True})
    assert r.status_code == 200, r.text
    rows = svc.recalibration.conn.execute(
        "SELECT delta, typeof(delta) FROM recalibration_events").fetchall()
    g = svc.recalibration.verify_governance_records(svc.archive_chain)
    print(f"\nW0b-Z1 ({sys.platform}) recalibration rows {[tuple(x) for x in rows]}; verdict {g['verdict']}, "
          f"row failures {g['row']['failed']}, zero_sign_legacy {g['row'].get('zero_sign_legacy')}")
    assert rows, "non-vacuity: the outcome wrote no recalibration event"
    assert g["verdict"] == "intact" and g["row"]["failed"] == 0
    assert g["row"]["zero_sign_legacy"] == 0, "a row written after the fix needed the legacy rule"


@pytest.mark.sqlite_only("SQLite's own behaviour is the premise: it stores -0.0 as 0.0 (W3b D6's declared difference; Postgres keeps the sign)")
def test_a_legacy_row_hashed_over_negative_zero_verifies_and_is_counted(rig):
    """FAILS PRE-FIX (row_hash_mismatch). A row exactly as the pre-W0b writer
    left one on every instance: hashed over -0.0, read back as 0.0."""
    svc, _client, bid = rig
    _plant_legacy_row(svc.recalibration, bid, delta_hashed=-0.0, delta_stored=-0.0)
    stored = svc.recalibration.conn.execute("SELECT delta FROM recalibration_events").fetchone()[0]
    g = svc.recalibration.verify_governance_records(svc.archive_chain)
    print(f"\nW0b-Z2 ({sys.platform}) legacy row reads back delta {stored!r}; verdict {g['verdict']}, "
          f"zero_sign_legacy {g['row'].get('zero_sign_legacy')}")
    assert repr(stored) == "0.0", "SQLite kept the sign: the premise of this fix does not hold here"
    assert g["verdict"] == "intact" and g["row"]["zero_sign_legacy"] == 1


def test_companion_a_legacy_zero_row_with_another_field_changed_still_fails(rig):
    """The rule restores a zero's sign and nothing else: a legacy -0.0 row
    whose stored weight was edited is named a content edit."""
    svc, _client, bid = rig
    _plant_legacy_row(svc.recalibration, bid, delta_hashed=-0.0, delta_stored=0.0,
                      stored_weight_stored=0.8)
    g = svc.recalibration.verify_governance_records(svc.archive_chain)
    assert g["verdict"] == "row_hash_mismatch" and g["row"]["failed"] == 1
    assert g["row"]["zero_sign_legacy"] == 0


def test_companion_a_nonzero_sign_flip_still_fails(rig):
    """A nonzero delta keeps its sign in SQLite, so a flipped sign is an edit
    the rule must not excuse: hashed over -0.5, stored +0.5, named."""
    svc, _client, bid = rig
    _plant_legacy_row(svc.recalibration, bid, delta_hashed=-0.5, delta_stored=0.5)
    g = svc.recalibration.verify_governance_records(svc.archive_chain)
    assert g["verdict"] == "row_hash_mismatch" and g["row"]["zero_sign_legacy"] == 0
