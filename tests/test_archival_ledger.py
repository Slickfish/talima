"""Packet AL — what was set aside, and who said so.

THE DEFECT (AE's finding, fixed here)
    No durable event recorded an archival act. The reason column WAS the
    record, and `unarchive_ids` clears it (tiered_store.py — deliberately:
    a retained reason would state something false about current status), so
    "was this claim ever archived, by whom, why" became unanswerable the
    moment the act was reversed. In a system whose premise is retention,
    the one judgement that sets a claim aside left no trace once withdrawn.

    L.2's recorded argument against a per-change HISTORY COLUMN
    (tiered_store.py:680-684) does not extend here: an append-only event
    ledger is this substrate's standard shape (outcome events, refusal
    ledger, calibration log), and the column's lifecycle is untouched —
    unarchive still clears it, because the row's CURRENT status and the
    ledger's record of ACTS are different claims.

AL.1 — the ledger: one event per belief per act (archived | unarchived),
    with reason, actor, timestamp, a record hash, and a seal ride on the
    existing governance machinery: RecalibrationStore enumerates the table
    in unsealed()/mark_sealed()/verify_governance_records(), so KAIROS
    seals archival acts with zero changes of its own. Written at the
    CHOKEPOINT (inside archive_ids/unarchive_ids), so the dedup endpoint,
    the L.2b script, and any future caller are all recorded — recording at
    call sites is how one forgets.

AL.2 — the caller pin: archive_ids/unarchive_ids (and their (s,r,o)
    wrappers) held to exactly today's callers by AST, both directions —
    the _insert_recalibration seam one table over, same reason: the
    dangerous call is the one nobody had to think about.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tracked import tracked_backend                              # noqa: E402

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore

ROOT = Path(__file__).resolve().parent.parent


def _store(tmp_path) -> tuple[TripleStore, int]:
    store = TripleStore(tmp_path / "kb.db")
    bid = store.add(subject="tak-441", relation="inhibits", object="cyp17",
                    weight=0.8, tier=1, granularity=Granularity.TOPIC,
                    source="wikidata")
    return store, bid


def _events(store, belief_id=None):
    sql = ("SELECT * FROM archival_events" +
           (" WHERE belief_id = ?" if belief_id is not None else "") +
           " ORDER BY ts ASC")
    args = (belief_id,) if belief_id is not None else ()
    return [dict(r) for r in store.conn.execute(sql, args)]


# ---------------------------------------------------------------------------
# THE SENTINEL — the record survives a reversal
# ---------------------------------------------------------------------------

def test_the_record_survives_a_real_archive_unarchive_cycle(tmp_path):
    store, bid = _store(tmp_path)
    n = store.archive("tak-441", "inhibits", "cyp17",
                      reason="dedup", actor_id="alice", actor_role="person")
    # NON-VACUITY: something real was archived, with a real reason and a
    # real actor, before any assertion about what the ledger holds.
    assert n == 1
    row = store.get_by_id(bid)
    assert row.archived is True and row.archive_reason == "dedup"

    store.unarchive("tak-441", "inhibits", "cyp17")
    row = store.get_by_id(bid)
    # L.2's column lifecycle is UNCHANGED — the status is honest about now:
    assert row.archived is False
    assert row.archive_reason is None and row.archived_at is None

    ev = _events(store, bid)
    assert len(ev) == 2, "the acts were not both recorded"
    first, second = ev
    assert first["direction"] == "archived"
    assert first["reason"] == "dedup"
    assert first["actor_id"] == "alice" and first["actor_role"] == "person"
    assert second["direction"] == "unarchived"
    assert second["event_id"] != first["event_id"], (
        "the reversal amended the first act instead of being its own event")
    store.close()


def test_the_first_event_is_untouched_by_the_reversal(tmp_path):
    """Not an amendment: the archived-event row is byte-identical before
    and after the unarchive that reverses the act it records."""
    store, bid = _store(tmp_path)
    store.archive("tak-441", "inhibits", "cyp17", reason="gate:probe",
                  actor_id="alice")
    before = _events(store, bid)[0]
    store.unarchive("tak-441", "inhibits", "cyp17")
    after = [e for e in _events(store, bid)
             if e["event_id"] == before["event_id"]][0]
    assert after == before
    store.close()


def test_archival_behaviour_is_otherwise_unchanged(tmp_path):
    """The accepting direction, pinned: the ledger records an act, it does
    not alter one (I-2/I-9). Tier untouched, row resolvable, retrieval
    exclusion and restoration exactly as before."""
    store, bid = _store(tmp_path)
    tier_before = store.get_by_id(bid).tier
    store.archive("tak-441", "inhibits", "cyp17", reason="dedup")
    row = store.get_by_id(bid)
    assert row is not None, "an archived row must stay resolvable (I-2)"
    assert row.tier == tier_before, "archival moved a tier — it must not"
    assert store.search_terms(["tak-441"]) == [], "archived row still competes"
    store.unarchive("tak-441", "inhibits", "cyp17")
    assert [t.id for t in store.search_terms(["tak-441"])] == [bid]
    store.close()


# ---------------------------------------------------------------------------
# The seal ride
# ---------------------------------------------------------------------------

def test_archival_events_ride_the_governance_seal(tmp_path):
    from backend.kb.recalibration import RecalibrationStore
    store, bid = _store(tmp_path)
    store.archive("tak-441", "inhibits", "cyp17", reason="dedup",
                  actor_id="alice")
    recal = RecalibrationStore(tmp_path / "kb.db")

    unsealed = recal.unsealed()
    ev = _events(store, bid)[0]
    assert any(k == ev["event_id"] for k, _h in unsealed), (
        "the archival event is invisible to the seal enumeration — it would "
        "never be chain-covered")

    recal.mark_sealed([ev["event_id"]], block_index=7)
    assert _events(store, bid)[0]["sealed_block"] == 7

    v = recal.verify_governance_records(None)
    assert v["verdict"] == "intact" and v["row"]["checked"] >= 1

    # Tamper-evidence: a rewritten reason must fail row verification.
    store.conn.execute(
        "UPDATE archival_events SET reason = 'innocuous' WHERE event_id = ?",
        (ev["event_id"],))
    store.conn.commit()
    v2 = recal.verify_governance_records(None)
    assert v2["verdict"] == "row_hash_mismatch"
    assert any(f["table"] == "archival_events" for f in v2["row"]["failures"])
    store.close(); recal.close()


# ---------------------------------------------------------------------------
# The endpoint carries the actor
# ---------------------------------------------------------------------------

def test_the_dedup_confirm_records_its_actor(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", "al-token")
    from fastapi.testclient import TestClient
    from backend.server import create_app, create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    a = svc.store.add(subject="qtc", relation="rises", object="at 600mg",
                      weight=0.8, tier=1, granularity=Granularity.TOPIC,
                      source="extraction")
    svc.store.add(subject="qtc", relation="increases", object="at 600mg",
                  weight=0.7, tier=1, granularity=Granularity.TOPIC,
                  source="extraction")
    client = TestClient(create_app(svc))
    r = client.post("/kb/dedup/confirm", json={
        "subject_a": "qtc", "relation_a": "rises", "object_a": "at 600mg",
        "subject_b": "qtc", "relation_b": "increases", "object_b": "at 600mg",
        "source_a": "extraction", "source_b": "extraction",
        "keep": "b", "confirmed": True, "actor_id": "alice",
        "actor_role": "person"},
        headers={"Authorization": "Bearer al-token"})
    assert r.status_code == 200, r.text
    ev = _events(svc.store, a)
    assert ev and ev[0]["actor_id"] == "alice"
    assert ev[0]["actor_role"] == "person"
    assert ev[0]["reason"] == "dedup"


def test_the_stats_surface_declares_coverage(tmp_path, monkeypatch):
    """Fourth instance of the coverage class: an empty ledger reads as
    'nothing recorded yet', never as 'nothing ever archived'."""
    monkeypatch.setenv("NEXUS_API_TOKEN", "al-token")
    from fastapi.testclient import TestClient
    from backend.server import create_app, create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    client = TestClient(create_app(svc))
    block = client.get("/cortex/stats").json()["archival_ledger"]
    assert block["enabled"] is True
    assert block["events"] == 0
    assert block["records_begin_at"] is None
    assert "nothing recorded yet" in block["note"]
    assert "not nothing ever archived" in block["note"], (
        "the note must name the misreading it exists to prevent")


# ---------------------------------------------------------------------------
# Reducibility, both positions
# ---------------------------------------------------------------------------

def test_off_writes_no_events_and_behaviour_is_exactly_today(
        tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_ARCHIVAL_LEDGER", "off")
    store, bid = _store(tmp_path)
    store.archive("tak-441", "inhibits", "cyp17", reason="dedup")
    assert store.get_by_id(bid).archived is True
    store.unarchive("tak-441", "inhibits", "cyp17")
    assert store.get_by_id(bid).archived is False
    assert _events(store) == [], "off still wrote an event"
    store.close()


def test_the_default_position_is_on(monkeypatch):
    monkeypatch.delenv("NEXUS_ARCHIVAL_LEDGER", raising=False)
    from backend.kb.tiered_store import archival_ledger_enabled
    assert archival_ledger_enabled() is True


# ---------------------------------------------------------------------------
# AL.2 — the caller pin
# ---------------------------------------------------------------------------

_NAMES = frozenset({"archive", "archive_ids", "unarchive", "unarchive_ids"})


def _call_sites() -> set[tuple[str, str, str]]:
    """(module, innermost function, called name) for every archival call in
    tracked backend files."""
    sites: set[tuple[str, str, str]] = set()

    def visit(node, rel, current):
        for child in ast.iter_child_nodes(node):
            name = current
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if isinstance(child, ast.Call):
                called = getattr(child.func, "attr", None) \
                    or getattr(child.func, "id", None)
                if called in _NAMES:
                    sites.add((rel, current, called))
            visit(child, rel, name)

    for path in tracked_backend():
        rel = path.relative_to(ROOT).as_posix()
        visit(ast.parse(path.read_text(encoding="utf-8")), rel, "<module>")
    return sites


def test_archival_has_exactly_todays_callers():
    """AG's seam, one table over: a rule-driven pass calling archive_ids
    directly would be mass invalidation-in-effect with no Law 3 gate. A new
    caller fails until somebody decides it should exist; a vanished one
    fails so the pin cannot rot. The L.2b script (scripts/, not backend/)
    is outside this population and is Law-3 gated on its own."""
    assert _call_sites() == {
        ("backend/kb/tiered_store.py", "archive", "archive_ids"),
        ("backend/kb/tiered_store.py", "unarchive", "unarchive_ids"),
        ("backend/server.py", "dedup_confirm", "archive"),
    }


def test_the_caller_pin_can_actually_fail():
    tree = ast.parse("def rogue_pass(store, ids):\n"
                     "    store.archive_ids(ids, reason='gate:auto')\n")
    hits = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "attr", None) in _NAMES]
    assert len(hits) == 1
