"""W6 part 3 — a work unit's close is a governance record, and it survives its reversal (run/specs/W6.md, D10 to D12).

WHAT WAS MISSING
    `decision_boundary: close` set `closed_at` and `closed_by` on the unit's row and nothing else: no record of the
    act, nothing listing the unit's lineage records, nothing sealed, and nothing that could reverse it.

WHAT A CLOSE EVENT CLAIMS (registered in the spec before these tests)
    This actor asserted the boundary at this time, and these were the unit's record ids then. Not that the work was
    finished. The actor and the accountable identity are what the request asserted, never verified.

WHAT IS HELD HERE
    C1  A close appends one event that lists the ids of the unit's lineage records at the act, oldest first, and
        not the closing turn's own (that record is written after the act). The event is sealed in a governance
        block on the governance cadence and verified with the other governance records.
    C2  `decision_boundary: reopen` reverses it: the row's `closed_at` is cleared and a `reopen` event names the
        close it reverses. The close event is the same row, byte for byte, before and after, and still verifies
        sealed. A second close lists every record the unit holds by then.
    C3  An edited event is named by the governance verifier. A deleted one is named by the ledger's own check
        where the unit's row or a reopen still refers to it.
    C4  The declarations: a reopen of a unit that is not closed, a close of one that is, and a boundary with no
        unit named are each declared on `done` and write no event; the record carries the boundary as asserted.
    C5  The read: gated and declared per-person, with the ledger's consistency beside it, and not on the
        governance router, so the just-culture guard allows no aggregate by person on it.
    C6  The ledger is append-only: a guard over the tracked backend names any UPDATE or DELETE statement that
        spells the table's name. Companion: a planted one.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb import recalibration
from backend.kb import work_units as wu
from backend.kb.schema import lineage_ids_text, work_unit_event_body, work_unit_event_hash
from backend.store import add_trace, remove_trace
from backend.server import create_app, create_services
from tests._tracked import REPO, tracked_backend
from tests.fixtures.ce_rig import RigClock

sys.path.insert(0, str(REPO / "scripts"))
import history_shape_harness as hsh  # noqa: E402

UNIT = "claim-7"
WHO = {"accountable": "alice", "actor_id": "alice", "actor_role": "person", "actor_source": "local"}


def _frames(resp) -> list[tuple[str, object]]:
    out = []
    for frame in resp.text.split("\n\n"):
        if frame.startswith("event: "):
            head, _, data = frame.partition("\ndata: ")
            out.append((head.removeprefix("event: "), json.loads(data) if data else None))
    return out


def _console(root: Path):
    hsh._build_store(root / "kb.db").close()
    svc = create_services(kb_db_path=root / "kb.db", chroma_dir=root / "chroma", eval_log_path=root / "metrics.jsonl",
                          archive_db_path=root / "archive.db", snapshots_dir=root / "snapshots",
                          provider=hsh._Stub(plant=None))
    return svc, TestClient(create_app(svc)), {"Authorization": f"Bearer {os.environ['NEXUS_API_TOKEN']}"}


def _turn(client, bearer, message: str, **fields) -> dict:
    resp = client.post("/chat", headers=bearer, json={"message": message, **fields})
    assert resp.status_code == 200, resp.text
    frames = _frames(resp)
    assert frames[-1][0] == "done", [e for e, _ in frames]
    return frames[-1][1]


def _seal(svc):
    return asyncio.run(svc.kairos.maybe_seal_governance(force=True))


def _row(svc, event_id: str) -> dict:
    return dict(svc.work_units.conn.execute(
        "SELECT * FROM work_unit_events WHERE event_id = ?", (event_id,)).fetchone())


def _closed_unit(root: Path):
    """Two turns in a unit, then a third that closes it: (services, client, bearer, the three done payloads)."""
    svc, client, bearer = _console(root)
    dones = [_turn(client, bearer, "what does interval training improve?", work_unit=UNIT, **WHO),
             _turn(client, bearer, "and what does sleep improve?", work_unit=UNIT, **WHO),
             _turn(client, bearer, "close this claim", work_unit=UNIT, decision_boundary="close", **WHO)]
    return svc, client, bearer, dones


# ---------------------------------------------------------------------------
# C1 — a close is one sealed governance record
# ---------------------------------------------------------------------------

def test_c1_a_close_is_one_event_listing_the_units_records_and_it_is_sealed(tmp_path):
    with RigClock().installed():
        svc, _client, _bearer, dones = _closed_unit(tmp_path)
        assert dones[2]["unit_closed"] is True
        events = svc.work_units.events(UNIT)
        assert [e["kind"] for e in events] == [wu.KIND_CLOSE]
        close = events[0]
        # the unit's records at the act, oldest first: the act follows the closing turn's own record, so that
        # record is in the list, and the act names it as the record that asserted the boundary
        assert close["lineage_ids"] == [d["lineage_id"] for d in dones]
        assert close["asserted_on"] == dones[2]["lineage_id"]
        assert close["identity_mechanism"] == "asserted"
        assert close["actor"] == {"id": "alice", "role": "person", "source": "local"}
        assert close["accountable"] == "alice" and close["reverses"] is None and close["sealed_block"] is None
        assert svc.lineage.get(dones[2]["lineage_id"]).boundary_asserted == "close"
        assert close["event_id"] in {k for k, _h in svc.recalibration.unsealed()}
        block = _seal(svc)
    assert block is not None
    sealed = svc.work_units.events(UNIT)[0]
    assert sealed["sealed_block"] == block.index and sealed["record_hash"] == close["record_hash"]
    verdict = svc.recalibration.verify_governance_records(svc.archive_chain)
    assert verdict["verdict"] == "intact" and verdict["seal"]["failed"] == 0 and verdict["row"]["failed"] == 0
    assert svc.work_units.verify_events() == {"verdict": "consistent", "events": 1,
                                              "closed_before_ledger": 0, "problems": []}


# ---------------------------------------------------------------------------
# C2 — the close survives its reversal
# ---------------------------------------------------------------------------

def test_c2_a_reopen_reverses_the_close_and_the_close_event_is_untouched(tmp_path):
    with RigClock().installed():
        svc, client, bearer, dones = _closed_unit(tmp_path)
        _seal(svc)
        close = svc.work_units.events(UNIT)[0]
        before = _row(svc, close["event_id"])
        reopened = _turn(client, bearer, "reopen it", work_unit=UNIT, decision_boundary="reopen", **WHO)
        assert reopened["unit_reopened"] is True and reopened["unit_closed_at_time_of_turn"] is True
        assert reopened["unit_closed"] is False and reopened["contract_version"] == 4
        assert svc.work_units.get(UNIT)["closed_at"] is None and svc.work_units.get(UNIT)["closed_by"] is None
        events = svc.work_units.events(UNIT)
        assert [e["kind"] for e in events] == [wu.KIND_CLOSE, wu.KIND_REOPEN]
        assert events[1]["reverses"] == close["event_id"] and events[1]["lineage_ids"] == []
        assert svc.lineage.get(reopened["lineage_id"]).boundary_asserted == "reopen"
        # the close event is the same row, and it still verifies against the block that sealed it
        assert _row(svc, close["event_id"]) == before
        _seal(svc)
        verdict = svc.recalibration.verify_governance_records(svc.archive_chain)
        assert verdict["verdict"] == "intact" and verdict["seal"]["unsealed"] == 0
        assert svc.work_units.verify_events()["verdict"] == "consistent"
        # closed again: the second close lists every record the unit holds by then
        again = _turn(client, bearer, "close it again", work_unit=UNIT, decision_boundary="close", **WHO)
        assert again["unit_closed"] is True
        second = svc.work_units.events(UNIT)[-1]
    assert second["kind"] == wu.KIND_CLOSE and second["asserted_on"] == again["lineage_id"]
    assert second["lineage_ids"] == [d["lineage_id"] for d in (*dones, reopened, again)]
    assert events[1]["asserted_on"] == reopened["lineage_id"]
    assert _row(svc, close["event_id"]) == before


# ---------------------------------------------------------------------------
# C3 — an edited event is named; a deleted one is named where something still refers to it
# ---------------------------------------------------------------------------

def test_c3_an_edited_or_a_deleted_event_is_named(tmp_path):
    with RigClock().installed():
        svc, client, bearer, _dones = _closed_unit(tmp_path)
        close = svc.work_units.events(UNIT)[0]
        conn = svc.work_units.conn
        # edited: the hash no longer matches the row
        conn.execute("UPDATE work_unit_events SET lineage_ids = ? WHERE event_id = ?", ("[]", close["event_id"]))
        conn.commit()
        verdict = svc.recalibration.verify_governance_records(svc.archive_chain)
        assert verdict["verdict"] == "row_hash_mismatch"
        assert {"table": "work_unit_events", "key": close["event_id"], "layer": "row"} in verdict["row"]["failures"]
        conn.execute("UPDATE work_unit_events SET lineage_ids = ? WHERE event_id = ?",
                     (json.dumps(close["lineage_ids"]), close["event_id"]))
        conn.commit()
        assert svc.recalibration.verify_governance_records(svc.archive_chain)["verdict"] == "intact"
        # deleted while the unit's row still says closed: the row has no act behind it... once a ledger exists
        _turn(client, bearer, "reopen it", work_unit=UNIT, decision_boundary="reopen", **WHO)
        reopen = svc.work_units.events(UNIT)[-1]
        conn.execute("DELETE FROM work_unit_events WHERE event_id = ?", (close["event_id"],))
        conn.commit()
        check = svc.work_units.verify_events()
        assert check["verdict"] == "inconsistent"
        assert {"event_id": reopen["event_id"], "problem": "reverses_no_close_of_this_unit"} in check["problems"]
        # deleted reopen: the row is open and the last act left is a close
        conn.execute("DELETE FROM work_unit_events WHERE event_id = ?", (reopen["event_id"],))
        conn.execute("INSERT INTO work_unit_events (event_id, ts, work_unit_id, kind, actor_id, actor_role, "
                     "actor_source, accountable, lineage_ids, reverses, asserted_on, record_hash) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (close["event_id"], close["ts"], UNIT, wu.KIND_CLOSE, "alice", "person", "local", "alice",
                      json.dumps(close["lineage_ids"]), None, close["asserted_on"], close["record_hash"]))
        conn.commit()
        check = svc.work_units.verify_events()
    assert check["verdict"] == "inconsistent"
    assert {"work_unit_id": UNIT, "problem": "row_disagrees_with_last_act", "last_act": wu.KIND_CLOSE} in check["problems"]


def test_c3_every_column_of_an_event_is_in_its_hash_sealed_or_not(tmp_path):
    """An edit to any one column of an act is named by the row layer, before its seal and after it: the actor's
    three fields, the accountable identity, the time, the kind, the unit, the id list, the reversal, the record
    that asserted it and how its identity was established."""
    with RigClock().installed():
        svc, _client, _bearer, _dones = _closed_unit(tmp_path)
        close = svc.work_units.events(UNIT)[0]
        conn = svc.work_units.conn
        original = _row(svc, close["event_id"])
        edits = (("ts", original["ts"] + 1), ("work_unit_id", "claim-8"), ("kind", wu.KIND_REOPEN),
                 ("actor_id", "mallory"), ("actor_role", "source_system"), ("actor_source", "elsewhere"),
                 ("accountable", "mallory"), ("lineage_ids", lineage_ids_text(["x"])), ("reverses", "x"),
                 ("asserted_on", "x"), ("identity_mechanism", "proxy"))
        for sealed in (False, True):
            if sealed:
                _seal(svc)
            for column, value in edits:
                conn.execute(f"UPDATE work_unit_events SET {column} = ? WHERE event_id = ?", (value, close["event_id"]))
                conn.commit()
                verdict = svc.recalibration.verify_governance_records(svc.archive_chain)
                assert {"table": "work_unit_events", "key": close["event_id"], "layer": "row"} \
                    in verdict["row"]["failures"], (column, sealed)
                conn.execute(f"UPDATE work_unit_events SET {column} = ? WHERE event_id = ?",
                             (original[column], close["event_id"]))
                conn.commit()
                assert svc.recalibration.verify_governance_records(svc.archive_chain)["verdict"] == "intact", column


def test_c3_an_id_list_in_any_form_but_the_writers_is_named_and_shown_as_stored(tmp_path):
    """The id list is read only in the form its writer writes. A JSON object keyed by the ids reads as the same
    list under `list()`, so its values were free text no hash covered; it is named now, and so is the right list
    written another way. The read shows the stored text, never a list coerced out of it."""
    with RigClock().installed():
        svc, client, bearer, _dones = _closed_unit(tmp_path)
        _seal(svc)
        close = svc.work_units.events(UNIT)[0]
        conn = svc.work_units.conn
        keyed = json.dumps({i: "REVIEWED AND APPROVED by the board" for i in close["lineage_ids"]})
        spaced = json.dumps(close["lineage_ids"], indent=1)
        for text in (keyed, spaced, "not json"):
            conn.execute("UPDATE work_unit_events SET lineage_ids = ? WHERE event_id = ?", (text, close["event_id"]))
            conn.commit()
            verdict = svc.recalibration.verify_governance_records(svc.archive_chain)
            assert {"table": "work_unit_events", "key": close["event_id"], "layer": "row",
                    "problem": "lineage_ids_not_an_id_list"} in verdict["row"]["failures"], text
            shown = client.get("/work-units/events", headers=bearer, params={"work_unit": UNIT}).json()["events"][0]
            assert shown["lineage_ids"] == text


def test_c3_a_sealed_act_rewritten_and_unsealed_is_named_and_stays_named_after_the_next_seal(tmp_path):
    """Rewritten with a hash recomputed over the new content and its seal cleared, an act read as an unsealed row
    with a consistent hash, and the next seal sealed the forgery. The block that sealed the original still holds
    the original's hash, which no row holds now: that is named, before the next seal and after it."""
    with RigClock().installed():
        svc, _client, _bearer, _dones = _closed_unit(tmp_path)
        block = _seal(svc)
        e = svc.work_units.events(UNIT)[0]
        forged = work_unit_event_body(e["event_id"], e["ts"], UNIT, e["kind"], e["actor"]["id"], e["actor"]["role"],
                                      e["actor"]["source"], "mallory", [], e["reverses"], asserted_on=e["asserted_on"])
        conn = svc.work_units.conn
        conn.execute("UPDATE work_unit_events SET accountable = ?, lineage_ids = ?, record_hash = ?, sealed_block = NULL "
                     "WHERE event_id = ?", ("mallory", lineage_ids_text([]), work_unit_event_hash(forged), e["event_id"]))
        conn.commit()
        for _attempt in range(2):
            verdict = svc.recalibration.verify_governance_records(svc.archive_chain)
            assert verdict["verdict"] == "seal_mismatch" and verdict["row"]["failed"] == 0, verdict
            assert {"sealed_block": block.index, "record_hash": e["record_hash"],
                    "problem": "sealed_hash_without_row"} in verdict["seal"]["failures"]
            assert verdict["seal"]["sealed_without_row"] == 1
            _seal(svc)                                  # the next seal seals the forgery; the first block still names it


def test_c3_a_sealed_act_rewritten_with_its_seal_kept_is_named_by_both_seal_checks(tmp_path):
    """The same forgery with `sealed_block` left in place: the row's new hash is in no block (row to block), and the
    block's hash is in no row (block to row)."""
    with RigClock().installed():
        svc, _client, _bearer, _dones = _closed_unit(tmp_path)
        block = _seal(svc)
        e = svc.work_units.events(UNIT)[0]
        forged = work_unit_event_body(e["event_id"], e["ts"], UNIT, e["kind"], e["actor"]["id"], e["actor"]["role"],
                                      e["actor"]["source"], "mallory", e["lineage_ids"], e["reverses"],
                                      asserted_on=e["asserted_on"])
        svc.work_units.conn.execute("UPDATE work_unit_events SET accountable = ?, record_hash = ? WHERE event_id = ?",
                                    ("mallory", work_unit_event_hash(forged), e["event_id"]))
        svc.work_units.conn.commit()
        verdict = svc.recalibration.verify_governance_records(svc.archive_chain)
    assert verdict["verdict"] == "seal_mismatch" and verdict["row"]["failed"] == 0, verdict
    assert {"table": "work_unit_events", "key": e["event_id"], "sealed_block": block.index} in verdict["seal"]["failures"]
    assert {"sealed_block": block.index, "record_hash": e["record_hash"],
            "problem": "sealed_hash_without_row"} in verdict["seal"]["failures"]


def test_c3_a_sealed_row_deleted_from_any_governance_ledger_is_named(tmp_path):
    """A sealed act deleted read as nothing at all (BACKLOG W6-B4, for all six ledgers). The block still holds its
    hash; the check is block to row and does not depend on the table, shown here on the boundary ledger and on
    the envelope history."""
    with RigClock().installed():
        svc, _client, _bearer, _dones = _closed_unit(tmp_path)
        svc.recalibration.record_envelope(recalibration.Envelope(version="w6-delete-me"), actor="w6-test",
                                          reason="W6: a sealed row to delete")
        _seal(svc)
        close = svc.work_units.events(UNIT)[0]
        env_hash = svc.recalibration.conn.execute(
            "SELECT record_hash FROM envelope_versions WHERE version = ?", ("w6-delete-me",)).fetchone()[0]
        assert svc.recalibration.verify_governance_records(svc.archive_chain)["verdict"] == "intact"
        svc.work_units.conn.execute("DELETE FROM work_unit_events WHERE event_id = ?", (close["event_id"],))
        svc.work_units.conn.commit()
        svc.recalibration.conn.execute("DELETE FROM envelope_versions WHERE version = ?", ("w6-delete-me",))
        svc.recalibration.conn.commit()
        verdict = svc.recalibration.verify_governance_records(svc.archive_chain)
    named = {f["record_hash"] for f in verdict["seal"]["failures"] if f.get("problem") == "sealed_hash_without_row"}
    assert verdict["verdict"] == "seal_mismatch" and named == {close["record_hash"], env_hash}, verdict


def test_c3_a_close_whose_row_write_fails_leaves_no_act(tmp_path, monkeypatch):
    """The act and the row's change are one transaction: when the row's write fails after the act's, neither is
    kept, and the next unrelated commit does not write the act alone."""
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        _turn(client, bearer, "what does interval training improve?", work_unit=UNIT, **WHO)
    conn = svc.work_units.conn
    real_execute = conn.execute

    def failing(sql, *args, **kwargs):
        if sql.startswith("UPDATE work_unit SET closed_at"):
            raise OSError("planted: the row's write fails after the act's")
        return real_execute(sql, *args, **kwargs)

    monkeypatch.setattr(conn, "execute", failing)
    with pytest.raises(OSError, match="planted"):
        svc.work_units.close(UNIT, closed_by=wu.actor_fields({"id": "alice"}), accountable="alice")
    monkeypatch.setattr(conn, "execute", real_execute)
    svc.work_units.open("claim-8", work_unit_type="unspecified", opened_by=wu.actor_fields(None))   # another commit
    assert svc.work_units.events(UNIT) == [] and svc.work_units.get(UNIT)["closed_at"] is None
    assert svc.work_units.verify_events()["verdict"] == "consistent"


def test_c3_acts_in_one_clock_tick_read_in_the_order_they_were_made(tmp_path):
    """Forty units closed and reopened at one instant: the ledger reads each unit's acts in the order they were
    appended (its insertion ordinal), never by the act's time and then a random id."""
    with RigClock().installed():
        svc, _client, _bearer = _console(tmp_path)
    who = wu.actor_fields({"id": "alice"})
    for n in range(40):
        uid = f"tick-{n}"
        svc.work_units.open(uid, work_unit_type="t", opened_by=who, now=1000.0)
        svc.work_units.close(uid, closed_by=who, now=1000.0)
        svc.work_units.reopen(uid, reopened_by=who, now=1000.0)
    check = svc.work_units.verify_events()
    assert check["verdict"] == "consistent", check["problems"][:3]
    assert all([e["kind"] for e in svc.work_units.events(f"tick-{n}")] == [wu.KIND_CLOSE, wu.KIND_REOPEN]
               for n in range(40))


def test_c3_a_closed_unit_whose_close_was_deleted_after_the_ledger_began_is_named(tmp_path):
    """A unit closed with no act at all is counted as closed before the ledger only when its close predates the
    ledger's first act; one closed after that and left with no act is named."""
    with RigClock().installed():
        svc, client, bearer, _dones = _closed_unit(tmp_path)
        _turn(client, bearer, "what does sleep improve?", work_unit="claim-9", **WHO)
        _turn(client, bearer, "close it", work_unit="claim-9", decision_boundary="close", **WHO)
        later = svc.work_units.events("claim-9")[0]
        svc.work_units.conn.execute("DELETE FROM work_unit_events WHERE event_id = ?", (later["event_id"],))
        svc.work_units.conn.commit()
        check = svc.work_units.verify_events()
    assert check["verdict"] == "inconsistent" and check["closed_before_ledger"] == 0
    assert {"work_unit_id": "claim-9", "problem": "closed_with_no_act"} in check["problems"]


# ---------------------------------------------------------------------------
# C4 — the declarations
# ---------------------------------------------------------------------------

def test_c4_what_is_declared_and_writes_no_event(tmp_path):
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        opened = _turn(client, bearer, "what does interval training improve?", work_unit=UNIT, **WHO)
        assert opened["unit_first_seen"] is True
        not_closed = _turn(client, bearer, "reopen it", work_unit=UNIT, decision_boundary="reopen", **WHO)
        assert not_closed["unit_not_closed"] is True and "unit_reopened" not in not_closed
        assert svc.work_units.events(UNIT) == []
        assert svc.lineage.get(not_closed["lineage_id"]).boundary_asserted == "reopen"
        _turn(client, bearer, "close it", work_unit=UNIT, decision_boundary="close", **WHO)
        twice = _turn(client, bearer, "close it", work_unit=UNIT, decision_boundary="close", **WHO)
        assert twice["unit_already_closed"] is True and twice["unit_closed"] is False
        assert [e["kind"] for e in svc.work_units.events(UNIT)] == [wu.KIND_CLOSE]
        no_unit = _turn(client, bearer, "reopen", decision_boundary="reopen")
        assert no_unit["boundary_not_applied"] == "no_unit_named"
        # a reopen with no accountable on the request proceeds and is flagged, as a close is
        flagged = _turn(client, bearer, "reopen it", work_unit=UNIT, decision_boundary="reopen", actor_id="bob")
    assert flagged["unit_reopened"] is True and wu.FLAG_ACCOUNTABILITY_MISSING in flagged["flags"]
    assert svc.work_units.events(UNIT)[-1]["accountable"] is None


# ---------------------------------------------------------------------------
# C5 — the read
# ---------------------------------------------------------------------------

def test_c5_the_read_is_gated_declared_per_person_and_carries_the_consistency(tmp_path):
    with RigClock().installed():
        _svc, client, bearer, dones = _closed_unit(tmp_path)
    # the suite's conftest sends the bearer by default, so the request with no credential says so
    assert client.get("/work-units/events", headers={"Authorization": ""}).status_code == 401
    body = client.get("/work-units/events", headers=bearer, params={"work_unit": UNIT}).json()
    assert body["total"] == 1 and body["events"][0]["kind"] == wu.KIND_CLOSE
    assert body["events"][0]["lineage_ids"] == [d["lineage_id"] for d in dones]
    assert body["events"][0]["identity_mechanism"] == "asserted"
    assert body["consistency"]["verdict"] == "consistent"
    assert body["per_person"]["declared"] is True and body["per_person"]["label"] == "per row: identity_mechanism"
    # not an authority surface: the guard derives those from the governance router, and this is not on it
    router = client.app.state.governance_router
    served = {r.path for r in client.app.routes if getattr(r, "endpoint", None) in {x.endpoint for x in router.routes}}
    assert "/work-units/events" not in served and served, served
    assert client.get("/work-units/events", headers=bearer).json()["total"] == 1
    # the consistency is the ledger's own check, read through the route: an inconsistency planted under it shows
    _svc.work_units.conn.execute("UPDATE work_unit SET closed_at = NULL WHERE work_unit_id = ?", (UNIT,))
    _svc.work_units.conn.commit()
    planted = client.get("/work-units/events", headers=bearer).json()["consistency"]
    assert planted["verdict"] == "inconsistent" and planted["problems"], planted


# ---------------------------------------------------------------------------
# C7 — a turn whose record fails makes no act; C8 — the act's identity mechanism
# ---------------------------------------------------------------------------

def test_c7_a_turn_whose_record_fails_makes_no_act_and_its_retry_does(tmp_path, monkeypatch):
    """The act is made after the turn's record: a closing turn whose record cannot be written leaves the unit
    open and the ledger empty, and the retry, recorded, closes it and names its own record."""
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        _turn(client, bearer, "what does interval training improve?", work_unit=UNIT, **WHO)
        real = svc.lineage.record_turn

        def refuse(**_kw):
            raise OSError("planted")

        monkeypatch.setattr(svc.lineage, "record_turn", refuse)
        resp = client.post("/chat", headers=bearer, json={"message": "close this claim", "work_unit": UNIT,
                                                          "decision_boundary": "close", **WHO})
        assert _frames(resp)[-1][0] == "record_failed"
        assert svc.work_units.get(UNIT)["closed_at"] is None and svc.work_units.events(UNIT) == []
        monkeypatch.setattr(svc.lineage, "record_turn", real)
        retry = _turn(client, bearer, "close this claim", work_unit=UNIT, decision_boundary="close", **WHO)
        close = svc.work_units.events(UNIT)[0]
    assert retry["unit_closed"] is True and close["asserted_on"] == retry["lineage_id"]
    assert retry["lineage_id"] in close["lineage_ids"]


def test_c7_an_act_that_cannot_be_written_after_its_record_is_declared(tmp_path, monkeypatch):
    """The record exists and the answer has gone; an act that then cannot be written is declared on done
    (`boundary_not_applied: write_failed`), never raised, and leaves no act."""
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        _turn(client, bearer, "what does interval training improve?", work_unit=UNIT, **WHO)

        def refuse(*_a, **_kw):
            raise OSError("planted")

        monkeypatch.setattr(svc.work_units, "close", refuse)
        done = _turn(client, bearer, "close this claim", work_unit=UNIT, decision_boundary="close", **WHO)
    assert done["lineage_id"] and done["unit_closed"] is False and done["boundary_not_applied"] == "write_failed"
    assert svc.work_units.events(UNIT) == [] and svc.lineage.get(done["lineage_id"]).boundary_asserted == "close"


def test_c8_an_act_carries_how_its_identity_was_established_and_its_hash_covers_it(tmp_path):
    """An act asserted on a proxy-mode turn says so (`identity_mechanism: proxy`); the read labels that row by it;
    the default (`asserted`) is stored NULL and kept out of the hash, as on the lineage record."""
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        unit, decl = svc.work_units.attribute_for_turn(session_id="s", requested_id=UNIT, accountable="alice",
                                                       actor={"id": "alice"}, boundary="close")
        svc.work_units.apply_boundary_for_turn(UNIT, decl, boundary="close", actor={"id": "alice"},
                                               accountable="alice", asserted_on="lineage-1", identity_mechanism="proxy")
        e = svc.work_units.events(UNIT)[0]
        assert e["identity_mechanism"] == "proxy" and e["asserted_on"] == "lineage-1"
        row = client.get("/work-units/events", headers=bearer, params={"work_unit": UNIT}).json()["events"][0]
        assert row["identity_mechanism"] == "proxy"
        assert svc.recalibration.verify_governance_records(svc.archive_chain)["verdict"] == "intact"
        svc.work_units.conn.execute("UPDATE work_unit_events SET identity_mechanism = NULL WHERE event_id = ?",
                                    (e["event_id"],))
        svc.work_units.conn.commit()
        assert svc.recalibration.verify_governance_records(svc.archive_chain)["verdict"] == "row_hash_mismatch"


def test_c8_a_turn_hands_its_identity_mechanism_to_the_act(tmp_path, monkeypatch):
    """The loop passes the turn's mechanism to the act, the same word the turn's record carries (here the chat
    route's mapping is planted to say `proxy`, as it does for a request the identity proxy vouched for)."""
    import backend.server as server_mod
    monkeypatch.setattr(server_mod, "_identity_mechanism_of", lambda _identity: "proxy")
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        _turn(client, bearer, "what does interval training improve?", work_unit=UNIT, **WHO)
        done = _turn(client, bearer, "close this claim", work_unit=UNIT, decision_boundary="close", **WHO)
        e = svc.work_units.events(UNIT)[0]
    assert svc.lineage.get(done["lineage_id"]).identity_mechanism == "proxy"
    assert e["identity_mechanism"] == "proxy" and e["asserted_on"] == done["lineage_id"]


# ---------------------------------------------------------------------------
# C6 — the ledger is append-only
# ---------------------------------------------------------------------------

_WRITE = re.compile(r"\b(?:UPDATE|DELETE)\b[^;]*\bwork_unit_events\b", re.I)


def ledger_rewrites(sources: dict[str, str]) -> list[str]:
    """Every string constant in the tracked backend that spells an UPDATE or a DELETE and the table's name after
    it. It reads spellings in single string constants and nothing else: the seal's own `UPDATE {table} SET
    sealed_block` is built from a tuple of table names and holds no such string, and a statement that takes the
    table's name from a variable, builds it from parts, or upserts (`INSERT ... ON CONFLICT ... DO UPDATE`,
    `REPLACE INTO`) is not named by this. `ledger_statement_problems`, below, reads the statements as they run
    on the paths it drives, with every name resolved; neither sees a path no test drives."""
    out = []
    for where, text in sorted(sources.items()):
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _WRITE.search(node.value):
                out.append(f"{where}:{node.lineno}")
    return out


def _backend_sources() -> dict[str, str]:
    return {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8") for p in tracked_backend()}


def test_c6_no_statement_in_the_backend_rewrites_the_ledger():
    sources = _backend_sources()
    assert ledger_rewrites(sources) == []
    inserts = [w for w, t in sources.items() if "INSERT INTO work_unit_events" in t]
    assert len(inserts) == 1, f"one writer, WorkUnitStore._append_event: {inserts}"


def test_c6_companion_a_planted_rewrite_is_named():
    sources = _backend_sources()
    name = next(w for w in sources if w.endswith("work_units.py"))
    sources[name] += ('\ndef _amend(conn, event_id):\n'
                      '    conn.execute("UPDATE work_unit_events SET lineage_ids = ? WHERE event_id = ?", ("[]", event_id))\n')
    other = next(w for w in sources if w.endswith("recalibration.py"))
    sources[other] += '\ndef _forget(conn):\n    conn.execute("delete from work_unit_events")\n'
    assert [f.split(":")[0] for f in ledger_rewrites(sources)] == [other, name]


_SEAL_UPDATE = re.compile(r"^UPDATE work_unit_events SET sealed_block = \? WHERE event_id = \? AND sealed_block IS NULL$")
_INSERT = re.compile(r"^INSERT INTO work_unit_events \(")


def ledger_statement_problems(statements: list[str]) -> list[str]:
    """Each statement that names the ledger, as it ran, against the only three things it may be: the writer's
    plain INSERT, the seal's UPDATE of `sealed_block` on a row not yet sealed, or a read (and the table's own DDL)."""
    problems = []
    for sql in (" ".join(st.split()) for st in statements):
        upper = sql.upper()
        if upper.startswith(("SELECT", "CREATE TABLE IF NOT EXISTS", "CREATE INDEX IF NOT EXISTS")):
            continue
        if _INSERT.match(sql) and "ON CONFLICT" not in upper and "OR REPLACE" not in upper:
            continue
        if _SEAL_UPDATE.match(sql):
            continue
        problems.append(sql)
    return problems


def _drive_the_ledger(root: Path, plant=None) -> list[str]:
    seen: list[str] = []

    def cb(sql: str) -> None:
        if "work_unit_events" in sql:
            seen.append(sql)

    add_trace(cb)
    try:
        with RigClock().installed():
            svc, client, bearer, _dones = _closed_unit(root)
            if plant is not None:
                plant(svc)
            _turn(client, bearer, "reopen it", work_unit=UNIT, decision_boundary="reopen", **WHO)
            _seal(svc)
            svc.recalibration.verify_governance_records(svc.archive_chain)
            client.get("/work-units/events", headers=bearer)
    finally:
        remove_trace(cb)
    return seen


def test_c6_the_statements_the_ledger_sees_as_they_run_are_its_insert_its_seal_and_reads(tmp_path):
    """Through the store interface's trace hook (every statement any `Database` runs, its table name resolved):
    a close and a reopen on turns, a seal, the verifier and the read touch the ledger only by the writer's two
    INSERTs and the seal's UPDATE. It sees the paths it drives and no others."""
    seen = _drive_the_ledger(tmp_path)
    assert sum(1 for st in seen if _INSERT.match(" ".join(st.split()))) == 2
    assert any(_SEAL_UPDATE.match(" ".join(st.split())) for st in seen)
    assert ledger_statement_problems(seen) == [], ledger_statement_problems(seen)


def test_c6_companion_a_rewrite_built_from_a_variable_on_a_driven_path_is_named(tmp_path):
    """The source scan cannot see a table name held in a variable; the statements as they ran can."""
    def plant(svc):
        real = svc.work_units._append_event

        def appending_then_amending(*args, **kwargs):
            event_id = real(*args, **kwargs)
            table = "work_unit" + "_events"
            svc.work_units.conn.execute(f"UPDATE {table} SET accountable = ? WHERE event_id = ?", ("mallory", event_id))
            return event_id

        svc.work_units._append_event = appending_then_amending

    problems = ledger_statement_problems(_drive_the_ledger(tmp_path, plant))
    assert problems == ["UPDATE work_unit_events SET accountable = ? WHERE event_id = ?"], problems


# ---------------------------------------------------------------------------
# Fix round 2 (W6's delta check 1): a backup across a seal, a cut stream, interleaved acts, the rollbacks
# ---------------------------------------------------------------------------

def _copy_sqlite(src: Path, dst: Path) -> None:
    import sqlite3
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()


def _restore_verify(folder: Path, scratch: Path):
    from tests import _proc
    return _proc.run([sys.executable, str(REPO / "scripts" / "restore_verify.py"), str(folder), "--into", str(scratch)],
                     cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                     env={**os.environ, "NEXUS_ENV_ROOT": str(REPO / "tests" / "fixtures")})


def _straddle(tmp_path, monkeypatch, *, straddles: int = 1, prepare=None, seal_first: bool = True):
    """A console whose `copy_database` makes and seals a governance act right after the first of the two store
    copies of each of the first `straddles` attempts (whichever store is copied first, so an order that copies
    the archive first is caught by what it leaves, not by a count). Returns (services, data root, out folder)."""
    from scripts import backup
    root = tmp_path / "data"
    root.mkdir(parents=True)
    clock = RigClock()
    with clock.installed():
        svc, client, bearer, _dones = _closed_unit(root)
        if seal_first:
            _seal(svc)
        if prepare is not None:
            prepare(svc, client, bearer)
    real, pair_calls, fired = backup.copy_database, [], []

    def straddle(src, dst):
        took = real(src, dst)
        if Path(src).name in ("kb.db", "archive.db"):
            pair_calls.append(Path(src).name)
            if len(pair_calls) % 2 == 1 and len(fired) < straddles:
                fired.append(1)
                with clock.installed():
                    svc.recalibration.record_refusal(route="probe", rule="law3_unconfirmed",
                                                     detail="between the copies", scope="default",
                                                     request={"probe": len(fired)})
                    _seal(svc)
        return took

    monkeypatch.setattr(backup, "copy_database", straddle)
    return svc, root, tmp_path / "backups"


def _straddled_backup(tmp_path, monkeypatch, *, straddles: int = 1, prepare=None, seal_first: bool = True):
    from scripts import backup
    svc, root, out = _straddle(tmp_path, monkeypatch, straddles=straddles, prepare=prepare, seal_first=seal_first)
    with RigClock().installed():
        folder, manifest, failures = backup.take_backup(root, out)
    return svc, folder, manifest, failures


def _restored(folder: Path, root: Path):
    """The backup's two stores copied into a fresh data root, as the runbook restores them; the governance check
    that root's own `/governance/verify` runs (no cut: the live check never has one)."""
    from backend.kb.recalibration import RecalibrationStore
    from backend.memory.chain import NexusArchiveChain
    root.mkdir()
    for name in ("kb.db", "archive.db"):
        _copy_sqlite(folder / name, root / name)
    return RecalibrationStore(root / "kb.db").verify_governance_records(NexusArchiveChain(root / "archive.db"))


@pytest.mark.sqlite_only("scripts/backup.py copies the SQLite files; a hosted backup is one pg_dump snapshot, "
                         "so no sealed act can be missing from it")
def test_d2_a_backup_taken_across_a_seal_is_copied_again_and_restores_intact(tmp_path, monkeypatch):
    """scripts/backup.py copies kb.db before archive.db, so a seal between the two copies leaves a block sealing an
    act the copy of kb.db lacks, and a store restored from that pair names the block for good (W6's second delta
    check). The backup copies the pair again when the archive's head moved across it: R4 passes, and so does the
    restored store's own check."""
    svc, folder, manifest, failures = _straddled_backup(tmp_path, monkeypatch, straddles=1)
    proc = _restore_verify(folder, tmp_path / "scratch")
    assert proc.returncode == 0, proc.stdout[-2000:]
    live = _restored(folder, tmp_path / "restored")
    assert live["verdict"] == "intact" and live["seal"]["sealed_without_row"] == 0, live["seal"]
    assert failures == [] and manifest["pair_attempts"] == 2, (failures, manifest.get("pair_attempts"))


@pytest.mark.sqlite_only("scripts/backup.py copies the SQLite files; a hosted backup is one pg_dump snapshot")
def test_d2_b_an_archive_that_moves_across_every_attempt_fails_the_backup_by_name(tmp_path, monkeypatch, capsys):
    """Five attempts (the documents say five); the last one still counts; a head that moves during every one fails
    the backup with exit 6 and its own line, and leaves no folder, so neither restore verification nor `--keep`
    can take it (W6's third delta check)."""
    from scripts import backup
    assert backup.PAIR_ATTEMPTS == 5, "docs/CONTRACTS.md, the runbook and the CHANGELOG say five"
    _svc, _folder, manifest, failures = _straddled_backup(tmp_path / "last", monkeypatch,
                                                          straddles=backup.PAIR_ATTEMPTS - 1)
    assert failures == [] and manifest["pair_attempts"] == backup.PAIR_ATTEMPTS, "the last attempt still counts"
    _svc, root, out = _straddle(tmp_path / "every", monkeypatch, straddles=backup.PAIR_ATTEMPTS)
    monkeypatch.setenv("NEXUS_ENV_ROOT", str(REPO / "tests" / "fixtures"))
    rc = backup.main(["--data-root", str(root), "--out", str(out)])
    printed = capsys.readouterr().out
    assert rc == backup.EXIT_NO_PAIR == 6, printed[-1500:]
    assert "FAIL: no consistent pair" in printed and "integrity_check" not in printed, printed[-1500:]
    assert not [p for p in out.iterdir() if p.is_dir()], "a failed pair leaves no folder"


@pytest.mark.sqlite_only("scripts/backup.py copies the SQLite files; a hosted backup is one pg_dump snapshot")
def test_d2_d_a_store_whose_first_seal_lands_between_the_copies_is_copied_again(tmp_path, monkeypatch):
    """A store with no block yet (no seal before the backup): its head reads none, and the first block landing
    between the two copies still moves it (W6's third delta check: a head of none is not block 0)."""
    _svc, folder, manifest, failures = _straddled_backup(tmp_path, monkeypatch, straddles=1, seal_first=False)
    proc = _restore_verify(folder, tmp_path / "scratch")
    assert proc.returncode == 0, proc.stdout[-2000:]
    live = _restored(folder, tmp_path / "restored")
    assert live["verdict"] == "intact" and live["seal"]["sealed_without_row"] == 0, live["seal"]
    assert failures == [] and manifest["pair_attempts"] == 2, (failures, manifest.get("pair_attempts"))


@pytest.mark.sqlite_only("scripts/backup.py copies the SQLite files; a hosted backup is one pg_dump snapshot")
def test_d2_c_a_sealed_act_deleted_from_an_earlier_block_is_named_in_a_restore(tmp_path, monkeypatch):
    """The unit's close is sealed in the first block and its reopen in a later one; the close deleted from the
    backup's copy of kb.db before the restore is named by R4 (only R4: the manifest is made to agree)."""
    import shutil
    import sqlite3
    from scripts.backup import sha256

    def reopen(svc, client, bearer):
        _turn(client, bearer, "reopen it", work_unit=UNIT, decision_boundary="reopen", **WHO)
        _seal(svc)

    svc, folder, manifest, failures = _straddled_backup(tmp_path, monkeypatch, straddles=0, prepare=reopen)
    assert failures == [] and manifest["pair_attempts"] == 1
    close, reopened = svc.work_units.events(UNIT)[:2]
    assert int(close["sealed_block"]) < int(reopened["sealed_block"]), "the population: two blocks, close first"
    altered = tmp_path / "altered"
    shutil.copytree(folder, altered)
    conn = sqlite3.connect(altered / "kb.db")
    conn.execute("DELETE FROM work_unit_events WHERE event_id = ?", (close["event_id"],))
    conn.commit()
    conn.close()
    counts = json.loads(json.dumps(manifest["counts"]))
    counts["kb.db"]["work_unit_events"] -= 1
    files = {**manifest["files"], "kb.db": sha256(altered / "kb.db")}
    (altered / "MANIFEST.json").write_text(json.dumps({**manifest, "counts": counts, "files": files}, indent=1,
                                                      sort_keys=True), encoding="utf-8")
    proc = _restore_verify(altered, tmp_path / "scratch")
    failed = [line for line in proc.stdout.splitlines() if line.startswith("FAIL")]
    assert proc.returncode != 0 and [line.split()[1] for line in failed] == ["R4"], proc.stdout[-2000:]
    assert "verify_governance_records: seal_mismatch" in failed[0], failed


def test_d2_a_sealed_act_deleted_from_the_middle_is_still_named(tmp_path):
    with RigClock().installed():
        svc, client, bearer, _dones = _closed_unit(tmp_path)
        _seal(svc)
        close = svc.work_units.events(UNIT)[0]
        _turn(client, bearer, "reopen it", work_unit=UNIT, decision_boundary="reopen", **WHO)
        _seal(svc)
    svc.work_units.conn.execute("DELETE FROM work_unit_events WHERE event_id = ?", (close["event_id"],))
    svc.work_units.conn.commit()
    verdict = svc.recalibration.verify_governance_records(svc.archive_chain)
    assert verdict["verdict"] == "seal_mismatch" and verdict["seal"]["sealed_without_row"] == 1, verdict["seal"]


def test_d4_the_act_is_made_before_the_first_frame_is_released(tmp_path):
    """A client that stops reading while the held answer is released closes the generator at that frame. The
    act (and the extractor's stated confidences) are made right after the record, before any frame, so a cut
    stream does not leave a record whose act was never made."""
    from backend.loop import agentic_loop as loop
    from backend.loop.state import NexusLoopState
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        _turn(client, bearer, "what does interval training improve?", work_unit=UNIT, **WHO)

        async def cut() -> list[str]:
            seen = []
            gen = loop.run_turn(NexusLoopState(), "close this claim", retriever=svc.retriever,
                                provider=svc.provider, lineage=svc.lineage, work_units=svc.work_units,
                                work_unit=UNIT, decision_boundary="close", accountable="alice",
                                actor={"id": "alice", "role": "person", "source": "local"})
            async for frame, _st in gen:
                seen.append(frame.split("\n", 1)[0])
                if frame.startswith("event: token"):
                    break
            await gen.aclose()
            return seen

        seen = asyncio.run(cut())
    assert seen[-1] == "event: token" and "event: done" not in seen, seen
    events = svc.work_units.events(UNIT)
    assert [e["kind"] for e in events] == [wu.KIND_CLOSE] and svc.work_units.get(UNIT)["closed_at"] is not None


def test_d5_an_act_re_decides_on_the_row_as_it_stands_when_another_request_moved_it(tmp_path):
    """The first half decides a close on an open unit; before the act, another request closes it. The act reads
    the row again and declares `unit_already_closed`, and appends no second close."""
    with RigClock().installed():
        svc, _client, _bearer = _console(tmp_path)
    who = {"id": "alice"}
    _unit, plan = svc.work_units.attribute_for_turn(session_id="s", requested_id=UNIT, accountable="alice",
                                                    actor=who, boundary="close")
    assert plan["unit_closed"] is True
    svc.work_units.resolve_for_turn(session_id="s2", requested_id=UNIT, accountable="bob", actor={"id": "bob"},
                                    boundary="close")
    _row_after, outcome = svc.work_units.apply_boundary_for_turn(UNIT, plan, boundary="close", actor=who,
                                                                 accountable="alice", asserted_on="L")
    assert outcome.get("unit_already_closed") is True and outcome.get("unit_closed") is False
    assert [e["accountable"] for e in svc.work_units.events(UNIT)] == ["bob"]


def test_d5_a_closed_unit_whose_middle_act_was_deleted_is_named(tmp_path):
    """Three units closed in turn; the middle one's act deleted. Its close is after the ledger's first act, so it
    is named; reading the ledger's start as its last act would have counted it as closed before the ledger."""
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        for uid in ("u-first", "u-middle", "u-last"):
            _turn(client, bearer, "what does sleep improve?", work_unit=uid, **WHO)
            _turn(client, bearer, "close it", work_unit=uid, decision_boundary="close", **WHO)
    middle = svc.work_units.events("u-middle")[0]
    svc.work_units.conn.execute("DELETE FROM work_unit_events WHERE event_id = ?", (middle["event_id"],))
    svc.work_units.conn.commit()
    check = svc.work_units.verify_events()
    assert {"work_unit_id": "u-middle", "problem": "closed_with_no_act"} in check["problems"], check


def test_d5_a_reopen_whose_row_write_fails_leaves_no_act(tmp_path, monkeypatch):
    with RigClock().installed():
        svc, _client, _bearer, _dones = _closed_unit(tmp_path)
    conn = svc.work_units.conn
    real_execute = conn.execute

    def failing(sql, *args, **kwargs):
        if sql.startswith("UPDATE work_unit SET closed_at = NULL"):
            raise OSError("planted: the row's write fails after the act's")
        return real_execute(sql, *args, **kwargs)

    monkeypatch.setattr(conn, "execute", failing)
    with pytest.raises(OSError, match="planted"):
        svc.work_units.reopen(UNIT, reopened_by=wu.actor_fields({"id": "alice"}), accountable="alice")
    monkeypatch.setattr(conn, "execute", real_execute)
    svc.work_units.open("claim-8", work_unit_type="unspecified", opened_by=wu.actor_fields(None))   # another commit
    assert [e["kind"] for e in svc.work_units.events(UNIT)] == [wu.KIND_CLOSE]
    assert svc.work_units.get(UNIT)["closed_at"] is not None
