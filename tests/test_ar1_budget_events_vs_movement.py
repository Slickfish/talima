"""Packet AR.1 — the budget report counts events and movement apart.

THE DEFECT, measured while re-deriving the integration reference: a
`neutral` outcome under a permitting envelope writes an APPLIED
recalibration with `delta: 0.0`, and the AG report counted it under
`applied` and `movements`. I-7 — a counter may not overstate what it
counts — and "twelve movements" where three moved nothing is a wrong number
wearing a right name.

THE DECISION (AR.1, argued in the report): the zero-delta event STAYS on the
record — "someone looked and concluded nothing" is an adjudication, it is
what provenance shows as a step, and deleting the path would change what
the verifier counts. The COUNTER changes: `applied` names recorded events,
`movements` names events with a non-zero delta, and `zero_delta` names the
rest, at every level of the report. Consumed figures already summed |delta|
and were never wrong.

Sentinels:
  - a store holding ONLY neutral outcomes reports zero movements and zero
    consumed against a non-zero event count;
  - non-vacuity: the mixed fixture holds a zero-delta AND a non-zero
    applied event, and the three counts reconcile at every level;
  - the store is byte-identical across the report (AG's pin, re-asserted
    on the new keys).
"""
from __future__ import annotations

import hashlib

import pytest

from backend.kb.recalibration import (
    Actor, Envelope, RecalibrationStore, apply_outcome,
)
from tests.test_governed_adaptation import _used_store

ALICE = Actor(id="alice", role="person", source="local")


def _rig(tmp_path, *, zero_deltas: int, moves: int):
    store, ids = _used_store(tmp_path)
    recal = RecalibrationStore(tmp_path / "kb.db")
    recal.record_envelope(
        Envelope(version="v1", max_delta=0.1, movable_classes=("asserted",),
                 cumulative_max=0.5),
        actor=ALICE, reason="AR.1 fixture")
    bid = ids["QT interval"]                      # class `asserted`
    for i in range(zero_deltas):
        apply_outcome(store, recal, belief_id=bid, delta=0.0, actor=ALICE,
                      outcome_ref=f"neutral-{i}")
    for i in range(moves):
        apply_outcome(store, recal, belief_id=bid, delta=0.05, actor=ALICE,
                      outcome_ref=f"move-{i}")
    return store, recal, bid


def test_only_neutral_outcomes_report_events_but_no_movement(tmp_path):
    """THE SENTINEL. Three adjudications, nothing moved."""
    _store, recal, bid = _rig(tmp_path, zero_deltas=3, moves=0)
    rep = recal.budget_report()
    sw = rep["store_wide"]
    assert sw["applied"] == 3, "the events are on the record and stay visible"
    assert sw["zero_delta"] == 3
    assert sw["movements"] == 0, "nothing moved; the counter said otherwise"
    assert sw["total_movement"] == 0.0
    assert sw["by_version"]["v1"] == {
        "applied": 3, "refused": 0, "consumed": 0.0,
        "movements": 0, "zero_delta": 3}
    actor = rep["per_actor"][0]
    assert (actor["events"], actor["movements"], actor["zero_delta"]) == (3, 0, 3)
    assert actor["consumed"] == 0.0
    belief = rep["per_belief"][0]
    assert belief["belief_id"] == bid
    assert (belief["events"], belief["movements"], belief["zero_delta"]) == (3, 0, 3)
    assert belief["consumed_lifetime"] == 0.0


def test_non_vacuity_the_mixed_fixture_holds_both_kinds_and_reconciles(tmp_path):
    """Both a zero-delta and a non-zero applied event, or the sentinel above
    passes on a store where the distinction has nothing to distinguish."""
    _store, recal, _bid = _rig(tmp_path, zero_deltas=2, moves=3)
    rep = recal.budget_report()
    sw = rep["store_wide"]
    assert sw["zero_delta"] == 2 and sw["movements"] == 3
    assert sw["applied"] == sw["movements"] + sw["zero_delta"]
    assert sw["total_movement"] == pytest.approx(0.15)
    for level in (rep["per_actor"][0], rep["per_belief"][0],
                  sw["by_version"]["v1"]):
        events = level.get("events", level.get("applied"))
        assert events == level["movements"] + level["zero_delta"], level


def test_the_counting_rule_is_declared_on_the_payload(tmp_path):
    """I-6 — the distinction rides the surface, not a docstring. A reader of
    `applied` must be told it counts events, and where movements are."""
    _store, recal, _bid = _rig(tmp_path, zero_deltas=1, moves=1)
    sw = recal.budget_report()["store_wide"]
    note = sw["counting"]
    assert "applied" in note and "movements" in note and "zero_delta" in note
    assert "non-zero" in note


@pytest.mark.sqlite_only("hashes the SQLite file's bytes before and after: the claim is about the file")
def test_the_new_keys_change_no_byte_of_the_store(tmp_path):
    _store, recal, _bid = _rig(tmp_path, zero_deltas=1, moves=1)
    db = tmp_path / "kb.db"
    before = hashlib.md5(db.read_bytes()).hexdigest()
    recal.budget_report()
    assert hashlib.md5(db.read_bytes()).hexdigest() == before
