"""W6 part 2 — two decision fields on the lineage record (run/specs/W6.md, D7 and D8).

WHAT WAS MISSING
    `docs/ARCHITECTURE.md` §10.4 lists the decision fields a record should carry. Three were on it already (the
    flags, the boundary and who asserted it). Two were not: the recalibration envelope in force, and the citation
    forecast's turn-level precision and null, which lived on the `done` frame only.

WHAT EACH FIELD CLAIMS (registered in the spec before these tests)
    envelope_version   this policy version was in force when this record was written. Not that any weight moved.
    forecast           the forecast committed before the model answered scored this precision against this null
                       on this turn's flags. One turn; nothing about the forecast in general.

WHAT IS HELD HERE
    F1  The LS rule for both: absent, None or empty means the old hash; present means it is in the hash.
    F2  The envelope is read when the record is written: no installed policy stores NULL, an installed policy is
        stored and sealed whatever it is called (the compiled default's own name included: the rule reads whether
        a policy row exists, never the name), and both tamper directions are named.
    F3  The forecast on the record is the score `done` carries, rounded to six decimals, without the forecast's
        random id and the ledger's count; scoring for the record writes nothing to the ledger; both tamper
        directions are named; with the forecast off the field is NULL.
    F4  A field that cannot be determined is never a blank: a scoring failure is recorded as `scoring_failed`,
        and an envelope read that raises fails the write, declared (part 1's `record_failed`).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.cortex import forecast as forecast_mod
from backend.kb import recalibration
from backend.memory import lineage as lineage_mod
from backend.server import create_app, create_services
from tests._tracked import REPO
from tests.fixtures.ce_rig import RigClock

sys.path.insert(0, str(REPO / "scripts"))
import history_shape_harness as hsh  # noqa: E402

QUESTIONS = ("what does interval training improve?", "and what does sleep improve?")


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


def _turn(client, bearer, message: str) -> list[tuple[str, object]]:
    resp = client.post("/chat", headers=bearer, json={"message": message})
    assert resp.status_code == 200, resp.text
    return _frames(resp)


def _done(frames) -> dict:
    assert frames[-1][0] == "done", [e for e, _ in frames]
    return frames[-1][1]


def _stored(svc, lineage_id: str, column: str):
    return svc.lineage.conn.execute(
        f"SELECT {column} FROM lineage_records WHERE lineage_id = ?", (lineage_id,)).fetchone()[0]


def _write(svc, lineage_id: str, column: str, value) -> None:
    svc.lineage.conn.execute(f"UPDATE lineage_records SET {column} = ? WHERE lineage_id = ?", (value, lineage_id))
    svc.lineage.conn.commit()


# ---------------------------------------------------------------------------
# F1 — the LS rule, on the hash itself
# ---------------------------------------------------------------------------

def test_f1_absent_or_empty_is_the_old_hash_and_present_is_in_the_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_CITATION_FORECAST", "off")          # a record with neither field
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        lineage_id = _done(_turn(client, bearer, QUESTIONS[0]))["lineage_id"]
    row = svc.lineage.conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (lineage_id,)).fetchone()
    payload = lineage_mod._hash_payload_from_row(row)
    assert payload["envelope_version"] is None and payload["forecast"] is None
    old = svc.lineage.compute_record_hash(payload)
    assert old == row["record_hash"]
    without = {k: v for k, v in payload.items() if k not in ("envelope_version", "forecast")}
    assert svc.lineage.compute_record_hash(without) == old, "a record from before the fields recomputes unchanged"
    # absent is NULL and nothing else: an empty name or an empty block is a value, and is in the hash
    assert svc.lineage.compute_record_hash({**payload, "envelope_version": None}) == old
    assert svc.lineage.compute_record_hash({**payload, "forecast": None}) == old
    for value in ("", " "):
        assert svc.lineage.compute_record_hash({**payload, "envelope_version": value}) != old, repr(value)
    for value in ({}, 0, [], False, ""):
        assert svc.lineage.compute_record_hash({**payload, "forecast": value}) != old, repr(value)
    with_policy = svc.lineage.compute_record_hash({**payload, "envelope_version": "w6-policy-1"})
    under_the_defaults_name = svc.lineage.compute_record_hash(
        {**payload, "envelope_version": recalibration.DEFAULT_ENVELOPE.version})
    with_score = svc.lineage.compute_record_hash({**payload, "forecast": {"k": 3, "precision": 0.5}})
    assert len({old, with_policy, under_the_defaults_name, with_score}) == 4


# ---------------------------------------------------------------------------
# F2 — the envelope in force, read at the write
# ---------------------------------------------------------------------------

def test_f2_the_envelope_in_force_is_read_at_the_write_and_sealed(tmp_path):
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        under_default = _done(_turn(client, bearer, QUESTIONS[0]))["lineage_id"]
        svc.recalibration.record_envelope(recalibration.Envelope(version="w6-policy-1"), actor="w6-test",
                                          reason="W6: a policy other than the compiled default")
        under_policy = _done(_turn(client, bearer, QUESTIONS[1]))["lineage_id"]
    assert _stored(svc, under_default, "envelope_version") is None
    assert svc.lineage.get(under_default).envelope_version is None
    assert _stored(svc, under_policy, "envelope_version") == "w6-policy-1"
    assert svc.lineage.get(under_policy).to_dict()["envelope_version"] == "w6-policy-1"
    assert all(svc.lineage.verify_record(i)["record_intact"] for i in (under_default, under_policy))
    # both tamper directions are named: a version written into a NULL column, and a version taken away
    _write(svc, under_default, "envelope_version", "w6-policy-9")
    _write(svc, under_policy, "envelope_version", None)
    assert svc.lineage.verify_record(under_default)["record_intact"] is False
    assert svc.lineage.verify_record(under_policy)["record_intact"] is False


def test_f2_a_policy_installed_under_the_defaults_own_name_is_still_recorded(tmp_path):
    """Nothing stops an operator installing a policy and calling it `v0-immovable`: the compiled default is no row
    in `envelope_versions`, so the name is free. A rule that read the NAME stored NULL for it, and the record
    said "the default was in force" while a policy with other limits was. The rule reads whether a policy is
    installed, never what it is called."""
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        assert svc.recalibration.installed_envelope_version() is None
        named_like_the_default = recalibration.Envelope(version=recalibration.DEFAULT_ENVELOPE.version, max_delta=0.25,
                                                        movable_classes=("extraction",))
        svc.recalibration.record_envelope(named_like_the_default, actor="w6-test",
                                          reason="W6: a policy that takes the default's name")
        assert svc.recalibration.installed_envelope_version() == recalibration.DEFAULT_ENVELOPE.version
        lineage_id = _done(_turn(client, bearer, QUESTIONS[0]))["lineage_id"]
        shown = client.get("/governance/envelope", headers=bearer).json()
    assert _stored(svc, lineage_id, "envelope_version") == recalibration.DEFAULT_ENVELOPE.version
    assert svc.lineage.verify_record(lineage_id)["record_intact"]
    _write(svc, lineage_id, "envelope_version", None)
    assert svc.lineage.verify_record(lineage_id)["record_intact"] is False
    assert shown["is_default"] is False, "an installed policy is not the default, whatever it is called"


def test_f2_an_installed_policys_name_is_recorded_exactly_as_it_is_written(tmp_path):
    """A version is a name: ` w6-v1` and `w6-v1` are two policies, and a policy named with a blank is a policy.
    The record stores the name in force exactly (it was trimmed, so the first was recorded as the second and the
    blank as no policy at all)."""
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        lineage_ids = []
        for version in ("w6-v1", " w6-v1", " "):
            svc.recalibration.record_envelope(recalibration.Envelope(version=version), actor="w6-test",
                                              reason="W6: a policy name with spaces")
            lineage_ids.append(_done(_turn(client, bearer, QUESTIONS[0]))["lineage_id"])
    assert [_stored(svc, i, "envelope_version") for i in lineage_ids] == ["w6-v1", " w6-v1", " "]
    assert all(svc.lineage.verify_record(i)["record_intact"] for i in lineage_ids)


@pytest.mark.parametrize("column, value", [("envelope_version", ""), ("envelope_version", " "),
                                           ("forecast", "{}"), ("forecast", "0"), ("forecast", "[]"),
                                           ("forecast", "false"), ("forecast", '""')])
def test_f4_a_value_written_into_a_null_column_is_named_whatever_it_is(tmp_path, monkeypatch, column, value):
    """Both tamper directions, for the values that read as nothing: written into a record's NULL column, each one
    is named (the hash rule took an empty name and every falsy forecast for absent, so they verified intact)."""
    monkeypatch.setenv("NEXUS_CITATION_FORECAST", "off")
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        lineage_id = _done(_turn(client, bearer, QUESTIONS[0]))["lineage_id"]
    assert _stored(svc, lineage_id, column) is None
    _write(svc, lineage_id, column, value)
    assert svc.lineage.verify_record(lineage_id)["record_intact"] is False


def test_f4_a_forecast_in_any_form_but_the_writers_is_named_never_raised_on(tmp_path, monkeypatch):
    """The forecast is read only in the form its writer writes. A duplicate key (one reader takes the first, another
    the last), a stray space, or text that is not JSON makes the record's own hash name it; the verifier and its
    route answer, they do not raise."""
    monkeypatch.delenv("NEXUS_CITATION_FORECAST", raising=False)
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        lineage_id = _done(_turn(client, bearer, QUESTIONS[1]))["lineage_id"]
    text = _stored(svc, lineage_id, "forecast")
    assert text and svc.lineage.verify_record(lineage_id)["record_intact"]
    stored = json.loads(text)
    duplicate = text[:-1] + ',"precision":0.99}'
    for planted in (duplicate, json.dumps(stored, sort_keys=True), "not json"):
        _write(svc, lineage_id, "forecast", planted)
        assert svc.lineage.verify_record(lineage_id)["record_intact"] is False, planted
        assert svc.lineage.get(lineage_id).forecast == planted, "shown as stored, never read as a forecast"
        verified = client.get("/lineage/verify", headers=bearer)
        assert verified.status_code == 200 and lineage_id in json.dumps(verified.json()), planted


def test_f2_the_record_surface_carries_both_fields(tmp_path):
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        svc.recalibration.record_envelope(recalibration.Envelope(version="w6-surface"), actor="w6-test",
                                          reason="W6: the surface")
        lineage_id = _done(_turn(client, bearer, QUESTIONS[1]))["lineage_id"]
        body = client.get(f"/lineage/{lineage_id}", headers=bearer).json()
    record = body.get("record", body)
    assert record["envelope_version"] == "w6-surface"
    assert record["forecast"] == svc.lineage.get(lineage_id).forecast and record["forecast"] is not None


# ---------------------------------------------------------------------------
# F3 — the forecast's turn-level score
# ---------------------------------------------------------------------------

def test_f3_the_record_carries_the_score_done_carries_and_scoring_it_writes_no_ledger_row(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXUS_CITATION_FORECAST", raising=False)
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        dones = [_done(_turn(client, bearer, q)) for q in QUESTIONS]
    scorable = [d for d in dones if (d.get("citation_forecast") or {}).get("scorable")]
    assert scorable, "the population: the fixture store's second turn is scorable"
    for done in dones:
        block, rec = done["citation_forecast"], svc.lineage.get(done["lineage_id"])
        expected = {k: block[k] for k in forecast_mod.RECORD_KEYS}
        for rate in ("precision", "null_precision"):
            if expected[rate] is not None:
                expected[rate] = round(expected[rate], 6)
        assert rec.forecast == expected, (rec.forecast, block)
        assert "forecast_id" not in rec.forecast and "resolved" not in rec.forecast
        assert json.loads(_stored(svc, done["lineage_id"], "forecast")) == expected
        assert svc.lineage.verify_record(done["lineage_id"])["record_intact"]
    # scoring for the record resolved no ledger row: the scoring after the record resolved every one of them
    # (a row already resolved is skipped, so a second resolve would have reported fewer)
    done = scorable[0]
    assert done["citation_forecast"]["resolved"] == done["citation_forecast"]["k"] > 0
    # both tamper directions
    stored = json.loads(_stored(svc, done["lineage_id"], "forecast"))
    _write(svc, done["lineage_id"], "forecast", json.dumps({**stored, "precision": 1.0}, sort_keys=True))
    assert svc.lineage.verify_record(done["lineage_id"])["record_intact"] is False
    _write(svc, done["lineage_id"], "forecast", None)
    assert svc.lineage.verify_record(done["lineage_id"])["record_intact"] is False


def test_f3_with_the_forecast_off_the_field_is_null(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_CITATION_FORECAST", "off")
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        done = _done(_turn(client, bearer, QUESTIONS[0]))
    assert "citation_forecast" not in done
    assert _stored(svc, done["lineage_id"], "forecast") is None and svc.lineage.get(done["lineage_id"]).forecast is None


# ---------------------------------------------------------------------------
# F4 — a field that cannot be determined is never a blank
# ---------------------------------------------------------------------------

def test_f4_a_scoring_failure_is_recorded_as_one_never_as_no_forecast(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXUS_CITATION_FORECAST", raising=False)
    real = forecast_mod.score_forecast

    def fails_for_the_record(fc, hits, cited, *, lineage_id=None, resolve_rows=True):
        if not resolve_rows:
            raise ZeroDivisionError("planted")
        return real(fc, hits, cited, lineage_id=lineage_id, resolve_rows=resolve_rows)

    monkeypatch.setattr(forecast_mod, "score_forecast", fails_for_the_record)
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)
        done = _done(_turn(client, bearer, QUESTIONS[0]))
    recorded = svc.lineage.get(done["lineage_id"]).forecast
    assert recorded is not None and recorded["scorable"] is False
    assert recorded["reason"] == forecast_mod.SCORING_FAILED and recorded["precision"] is None


def test_f4_an_envelope_that_cannot_be_read_fails_the_write_declared(tmp_path, monkeypatch):
    with RigClock().installed():
        svc, client, bearer = _console(tmp_path)

        def unreadable():
            raise OSError("planted")

        monkeypatch.setattr(svc.recalibration, "installed_envelope_version", unreadable)
        frames = _turn(client, bearer, QUESTIONS[0])
    names = [e for e, _ in frames]
    assert names[-1] == "record_failed" and "done" not in names and "token" not in names, names
    assert frames[-1][1]["reason"] == "OSError"
    assert svc.lineage.recent(limit=5) == []
