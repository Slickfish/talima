"""v3.1 Phase 1 (1a) — work-unit attribution: junction plus lineage field.

1b (`tests/test_p1b_attribution_as_scope.py`) superseded 1a's B.4 and B.5:
three tests that pinned the lifecycle are GONE from this file and are named
there — the declared header/body conflict, the one-open-unit-per-session
rule, and the restart re-read of the session's open unit. What stays here
is what 1a built and 1b kept.

WHAT IS ASSERTED, in the packet's order:
  establish 1  a lineage record written before the fields existed hashes
               identically after them (the non-default-only rule);
  acceptance 1 a turn naming a unit: the record carries it and every claim
               admitted during the turn has a junction row; a turn naming
               nothing carries the session pseudo-unit, `unattributed`;
  acceptance 2 re-assertion, BOTH source cases pinned (A.2): same source is
               one row upserted with two junction rows; a different source
               is a second row with its own junction row;
  acceptance 3 byte-identical without attribution: the request hash of
               every turn of the fixture conversation equals the hash
               captured on the pre-change tree (tests/_p1_request_hashes.json);
  E.7          a junction row pointing at an archived claim needs nothing:
               archive/unarchive never touch the junction and the unit's
               claim list carries the archived flag.

GUARDS, each with a companion that plants what the guard does not name:
  - the junction is append-only: no SQL statement string in `backend/`
    updates or deletes `triple_work_unit` (AST over string constants);
  - a sealed lineage record is never written in place: an in-row
    attribution of a sealed record is reported by `verify_sealed_lineage`
    at the row layer, and the same write to an unsealed record is not —
    the check is on the seal, not on the write.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
import sys
from pathlib import Path

from backend.kb.granularity import Granularity
from backend.kb.recalibration import DEFAULT_ACTOR
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.kb.work_units import (PSEUDO_UNIT_PREFIX, STATUS_ATTRIBUTED,
                                   STATUS_UNATTRIBUTED, WorkUnitStore, actor_fields,
                                   pseudo_unit_id)
from backend.loop.agentic_loop import run_turn
from backend.loop.state import NexusLoopState
from backend.memory.chain import NexusArchiveChain
from backend.memory.hippocampus import Hippocampus
from backend.memory.lineage import LineageStore, verify_sealed_lineage
from tests._tracked import tracked_backend

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import history_shape_harness as hsh  # noqa: E402
import pytest

JUNCTION_TABLE = "triple_work_unit"
FIXTURE = json.loads((ROOT / "tests" / "_p1_request_hashes.json").read_text(encoding="utf-8"))
ALICE = actor_fields({"id": "alice"})
BOB = actor_fields({"id": "bob"})


def _rig(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    kb = tmp_path / "kb.db"
    hsh._build_store(kb).close()
    store = TripleStore(kb)
    lineage = LineageStore(kb)
    units = WorkUnitStore(store.conn)
    return store, lineage, units


def _turn(store, lineage, units, question, *, work_unit=None, accountable=None, state=None,
          session_id="default", actor=None, decision_boundary=None, provider=None):
    frames = []
    out = state or NexusLoopState()

    async def drive():
        nonlocal out
        async for f, st in run_turn(out, question, retriever=TripleRetriever(store),
                                    hippocampus=Hippocampus(),
                                    provider=provider or hsh._Stub(plant=None),
                                    lineage=lineage, work_units=units, session_id=session_id,
                                    work_unit=work_unit, accountable=accountable, actor=actor,
                                    decision_boundary=decision_boundary):
            frames.append(f)
            out = st
    asyncio.run(drive())
    done = next(json.loads(f.split("data: ", 1)[1].strip())
                for f in reversed(frames) if f.startswith("event: done\n"))
    return out, done


# ---------------------------------------------------------------------------
# establish 1 / acceptance 5 — pre-existing records hash identically
# ---------------------------------------------------------------------------

@pytest.mark.sqlite_only("builds a pre-Phase-1 store by hand with sqlite3 and migrates it")
def test_a_record_without_the_fields_hashes_as_before_and_a_stated_one_differs(tmp_path):
    base = {"lineage_id": "x", "ts": 1.0, "query_hash": "q", "injected": [], "contradictions": [],
            "model_id": "m", "prompt_prefix_hash": "p", "response_hash": "r"}
    old_shape = LineageStore.compute_record_hash(dict(base))
    with_nulls = LineageStore.compute_record_hash({**base, "work_unit_id": None, "accountable_identity": None})
    assert old_shape == with_nulls, "a null field must not enter the hash body"
    assert LineageStore.compute_record_hash({**base, "work_unit_id": "WU-1"}) != old_shape
    assert LineageStore.compute_record_hash({**base, "accountable_identity": "alice"}) != old_shape
    # And a store written by the pre-change schema migrates and re-verifies.
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE lineage_records (
        lineage_id TEXT PRIMARY KEY, ts REAL NOT NULL, query_hash TEXT NOT NULL,
        injected_json TEXT NOT NULL, contradictions_json TEXT NOT NULL, model_id TEXT NOT NULL,
        prompt_prefix_hash TEXT NOT NULL, response_hash TEXT NOT NULL, record_hash TEXT NOT NULL,
        sealed_block INTEGER)""")
    con.execute("INSERT INTO lineage_records VALUES ('old-1', 1.0, 'q', '[]', '[]', 'm', 'p', 'r', ?, NULL)",
                (LineageStore.compute_record_hash({**base, "lineage_id": "old-1"}),))
    con.commit(); con.close()
    st = LineageStore(db)
    assert st.verify_record("old-1")["record_intact"] is True
    assert st.get("old-1").work_unit_id is None and st.get("old-1").accountable_identity is None
    st.close()


# ---------------------------------------------------------------------------
# acceptance 1 — the record and the junction
# ---------------------------------------------------------------------------

class _Extractor(hsh._Stub):
    """The harness stub, but extraction proposes a claim."""

    async def stream_chat(self, model, messages, num_ctx):
        self.calls += 1
        if self.calls % 2 == 1:
            self.requests.append([dict(m) for m in messages])
            yield "Caffeine improves endurance in most studies."
        else:
            yield json.dumps([{"subject": "caffeine", "relation": "improves",
                               "object": "endurance", "confidence": 0.8}])


def _turn_with_extraction(store, lineage, units, *, work_unit=None, accountable=None, actor=None):
    return _turn(store, lineage, units, "does caffeine improve endurance?", work_unit=work_unit,
                 accountable=accountable, actor=actor, provider=_Extractor(plant=None))


def test_a_named_unit_is_on_the_record_and_every_admitted_claim_has_a_junction_row(tmp_path, monkeypatch):
    store, lineage, units = _rig(tmp_path, monkeypatch)
    state, done = _turn_with_extraction(store, lineage, units, work_unit="WU-1", accountable="alice",
                                        actor={"id": "alice", "role": "person"})
    assert done["work_unit_id"] == "WU-1" and done["attribution_status"] == STATUS_ATTRIBUTED
    assert done["unit_first_seen"] is True and done["unit_closed"] is False
    rec = lineage.get(done["lineage_id"])
    assert rec.work_unit_id == "WU-1" and rec.accountable_identity == "alice"
    assert lineage.verify_record(rec.lineage_id)["record_intact"]
    admitted = [r for r in store.conn.execute(
        "SELECT id FROM triples WHERE source = 'extraction'")]
    assert admitted, "the stub's extraction must admit a claim"
    rows = units.claims_for_unit("WU-1")
    assert {r["triple_id"] for r in rows} == {r["id"] for r in admitted}
    assert all(r["lineage_id"] == rec.lineage_id and r["first_seen_turn"] == 1 for r in rows)
    assert done["admitted_to_unit"] == len(rows)
    # 1b — the row was created with the REQUEST's actor fields, and the
    # state carries no current unit (the tier-4 report list is not one).
    unit = units.get("WU-1")
    assert unit["opened_by"] == {"id": "alice", "role": "person", "source": "local"}
    assert unit["accountable_identity"] == "alice"
    assert not hasattr(state, "work_unit_id") and state.history_work_units == ("WU-1",)
    store.close(); lineage.close()


def test_a_turn_naming_nothing_carries_the_session_pseudo_unit(tmp_path, monkeypatch):
    store, lineage, units = _rig(tmp_path, monkeypatch)
    state, done = _turn(store, lineage, units, "what does interval training improve?")
    assert done["work_unit_id"] == pseudo_unit_id("default") == f"{PSEUDO_UNIT_PREFIX}default"
    assert done["attribution_status"] == STATUS_UNATTRIBUTED
    assert done["unit_first_seen"] is False, "the pseudo-unit is absence, not a first mention"
    assert "flags" not in done and "unit_already_closed" not in done
    rec = lineage.get(done["lineage_id"])
    assert rec.work_unit_id == pseudo_unit_id("default") and rec.accountable_identity is None
    unit = units.get(pseudo_unit_id("default"))
    # 1b — created by the same rule as any unit: the request's actor fields,
    # the vocabulary's defaults when the request said nothing. Not `system`.
    assert unit["opened_by"] == {"id": DEFAULT_ACTOR.id, "role": DEFAULT_ACTOR.role,
                                 "source": DEFAULT_ACTOR.source}
    assert unit["pseudo"] is True
    assert units.counts()["unattributed"]["open"] == 1
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# acceptance 2 — re-assertion, both source cases (A.2)
# ---------------------------------------------------------------------------

def _rows(store):
    # The fixture store already holds `creatine | increases | strength` from
    # Wikidata; the cases below are about the two rows this test adds.
    return [dict(r) for r in store.conn.execute(
        "SELECT id, subject, relation, object, weight, tier, source FROM triples "
        "WHERE subject = 'creatine' AND source != 'wikidata' ORDER BY id")]


def test_reassertion_same_source_is_one_row_two_junction_rows(tmp_path, monkeypatch):
    store, _lineage, units = _rig(tmp_path, monkeypatch)
    units.open("WU-1", work_unit_type="claim", opened_by=ALICE)
    units.open("WU-7", work_unit_type="claim", opened_by=BOB)
    with store.admissions() as admitted:
        first = store.add(subject="creatine", relation="increases", object="strength", weight=0.6,
                          tier=2, granularity=Granularity.TURN, source="extraction")
    units.admit(admitted, "WU-1", turn=1, lineage_id="L1")
    before = _rows(store)
    with store.admissions() as admitted:
        second = store.add(subject="creatine", relation="increases", object="strength", weight=0.9,
                           tier=2, granularity=Granularity.TURN, source="extraction")
    units.admit(admitted, "WU-7", turn=7, lineage_id="L7")
    after = _rows(store)
    print("\nA.2 same source — before:", before, "\n                 after: ", after)
    assert first == second and len(after) == 1
    assert before[0]["weight"] == 0.6 and after[0]["weight"] == 0.9, "the monotone upsert raised it"
    assert {k: v for k, v in after[0].items() if k != "weight"} == {k: v for k, v in before[0].items() if k != "weight"}
    junction = units.units_for_triple(first)
    print("junction rows:", junction)
    assert [(j["work_unit_id"], j["first_seen_turn"], j["lineage_id"]) for j in junction] == \
        [("WU-1", 1, "L1"), ("WU-7", 7, "L7")]
    store.close()


def test_reassertion_different_source_is_a_second_row_with_its_own_junction_row(tmp_path, monkeypatch):
    store, _lineage, units = _rig(tmp_path, monkeypatch)
    units.open("WU-1", work_unit_type="claim", opened_by=ALICE)
    units.open("WU-7", work_unit_type="claim", opened_by=BOB)
    with store.admissions() as admitted:
        human = store.add(subject="creatine", relation="increases", object="strength", weight=0.6,
                          tier=1, granularity=Granularity.TOPIC, source="human_assertion")
    units.admit(admitted, "WU-1", turn=1)
    with store.admissions() as admitted:
        model = store.add(subject="creatine", relation="increases", object="strength", weight=0.9,
                          tier=2, granularity=Granularity.TURN, source="extraction")
    units.admit(admitted, "WU-7", turn=7)
    rows = _rows(store)
    print("\nA.2 different source — rows:", rows)
    assert human != model and len(rows) == 2
    assert rows[0]["weight"] == 0.6 and rows[0]["source"] == "human_assertion", "the human row is untouched"
    assert units.units_for_triple(human)[0]["work_unit_id"] == "WU-1"
    assert units.units_for_triple(model)[0]["work_unit_id"] == "WU-7"
    store.close()


def test_a_repeat_admission_in_the_same_unit_is_a_no_op(tmp_path, monkeypatch):
    store, _lineage, units = _rig(tmp_path, monkeypatch)
    units.open("WU-1", work_unit_type="claim", opened_by=ALICE)
    assert units.admit([1, 2], "WU-1", turn=1) == 2
    assert units.admit([1, 2], "WU-1", turn=3) == 0
    assert [j["first_seen_turn"] for j in units.units_for_triple(1)] == [1]
    store.close()


# ---------------------------------------------------------------------------
# acceptance 3 — byte-identical without attribution
# ---------------------------------------------------------------------------

def test_the_request_hash_of_every_fixture_turn_equals_the_pre_change_capture(tmp_path, monkeypatch):
    """The prompt did not change. `tests/_p1_request_hashes.json` was
    captured on the tree BEFORE Phase 1 (85c0b54) by the same fixture;
    a differing hash on any turn means attribution reached the request.
    Rerun unchanged for 1b (acceptance 6).

    CHANGE A (18 October) wraps every prior answer in the history, which
    changes turns 2–5's request bytes by design; that is the wrapper's
    change, not attribution's. The capture keeps its pre-Phase-1 meaning by
    running this fixture under the documented rollback, the pre-A history
    path byte for byte, rather than being re-taken."""
    monkeypatch.setenv("NEXUS_HISTORY_WRAP", "off")
    store, lineage, units = _rig(tmp_path, monkeypatch)
    state = NexusLoopState()
    got = []
    for q in FIXTURE["questions"]:
        state, done = _turn(store, lineage, units, q, state=state)
        got.append(lineage.get(done["lineage_id"]).request_hash)
    for i, (q, exp, act) in enumerate(zip(FIXTURE["questions"], FIXTURE["request_hashes"], got), 1):
        print(f"acceptance 3 turn {i}: {act[:16]}… {'==' if act == exp else '!='} {exp[:16]}…  {q}")
    assert got == FIXTURE["request_hashes"]
    store.close(); lineage.close()


def test_the_capture_is_not_vacuous():
    assert len(FIXTURE["request_hashes"]) == 5
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in FIXTURE["request_hashes"])
    assert len(set(FIXTURE["request_hashes"])) == 5, "five distinct requests"


# ---------------------------------------------------------------------------
# E.7 — archival and the junction
# ---------------------------------------------------------------------------

def test_archiving_an_admitted_claim_leaves_the_junction_and_declares_the_flag(tmp_path, monkeypatch):
    store, _lineage, units = _rig(tmp_path, monkeypatch)
    units.open("WU-1", work_unit_type="claim", opened_by=ALICE)
    with store.admissions() as admitted:
        bid = store.add(subject="creatine", relation="increases", object="strength", weight=0.6,
                        tier=2, granularity=Granularity.TURN, source="extraction")
    units.admit(admitted, "WU-1", turn=1)
    store.archive_ids([bid], reason="gate:test")
    rows = units.claims_for_unit("WU-1")
    assert [r["triple_id"] for r in rows] == [bid] and rows[0]["archived"] == 1
    store.unarchive_ids([bid], reason="reversal:test")
    assert units.claims_for_unit("WU-1")[0]["archived"] == 0
    assert units.units_for_triple(bid)[0]["work_unit_id"] == "WU-1"
    store.close()


# ---------------------------------------------------------------------------
# guard 1 — the junction is append-only
# ---------------------------------------------------------------------------

_MUTATION = re.compile(r"\b(UPDATE|DELETE\s+FROM)\s+" + JUNCTION_TABLE + r"\b", re.I)


def junction_mutation_sites(files=None) -> list[str]:
    """file:line of every string constant in `files` (default: the tracked
    backend) that is an UPDATE or DELETE statement over the junction."""
    out = []
    for f in (files if files is not None else tracked_backend()):
        f = Path(f)
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _MUTATION.search(node.value):
                rel = f.relative_to(ROOT).as_posix() if f.is_relative_to(ROOT) else f.as_posix()
                out.append(f"{rel}:{node.lineno}")
    return out


def test_no_backend_statement_updates_or_deletes_a_junction_row():
    assert junction_mutation_sites() == [], (
        "the work-unit junction is append-only; a row records an admission that happened")


def test_the_append_only_guard_names_a_planted_delete(tmp_path):
    planted = tmp_path / "tidy_fixture.py"
    planted.write_text("def tidy(conn, unit):\n"
                       "    conn.execute('DELETE FROM triple_work_unit WHERE work_unit_id = ?', (unit,))\n"
                       "    conn.execute(\"UPDATE triple_work_unit SET lineage_id = NULL\")\n",
                       encoding="utf-8")
    assert junction_mutation_sites([planted]) == [f"{planted.as_posix()}:2", f"{planted.as_posix()}:3"]
    clean = tmp_path / "clean_fixture.py"
    clean.write_text("SQL = 'INSERT OR IGNORE INTO triple_work_unit VALUES (?, ?, ?, ?)'\n"
                     "NOTE = 'delete from triple_work_unit is forbidden'\n", encoding="utf-8")
    # The prose line matches the pattern deliberately: the guard is over
    # statement-shaped strings, and this shows it sees them wherever they are.
    assert junction_mutation_sites([clean]) == [f"{clean.as_posix()}:2"]


# ---------------------------------------------------------------------------
# guard 2 — a sealed lineage record is never written in place
# ---------------------------------------------------------------------------

def _sealed_store(tmp_path):
    lineage = LineageStore(tmp_path / "kb.db")
    chain = NexusArchiveChain(tmp_path / "archive.db")
    a = lineage.record_turn(query="q1", injected=[], contradictions=[], model_id="m",
                            response_text="a1", prompt_prefix="P")
    b = lineage.record_turn(query="q2", injected=[], contradictions=[], model_id="m",
                            response_text="a2", prompt_prefix="P")
    # Seal `a` only, the way KAIROS does: the hashes into a governance block.
    hashes = [a.record_hash]
    block = chain.append({"kind": "governance", "lineage_seal": {
        "count": 1, "digest": LineageStore.seal_digest(hashes), "record_hashes": hashes}},
        "", kind="governance")
    lineage.mark_sealed([a.lineage_id], block.index)
    return lineage, chain, a, b


def test_an_in_row_attribution_of_a_sealed_record_is_reported_by_id(tmp_path):
    lineage, chain, sealed, _unsealed = _sealed_store(tmp_path)
    # The write nobody may make: attribute history in place. The hashed
    # field changes; the row no longer recomputes; the seal names it.
    lineage.conn.execute("UPDATE lineage_records SET work_unit_id = 'WU-hist', "
                         "record_hash = ? WHERE lineage_id = ?",
                         (LineageStore.compute_record_hash({
                             "lineage_id": sealed.lineage_id, "ts": sealed.ts, "query_hash": sealed.query_hash,
                             "injected": [], "contradictions": [], "model_id": "m",
                             "prompt_prefix_hash": sealed.prompt_prefix_hash,
                             "response_hash": sealed.response_hash, "work_unit_id": "WU-hist"}),
                          sealed.lineage_id))
    lineage.conn.commit()
    report = verify_sealed_lineage(lineage, chain, lineage_id=sealed.lineage_id)
    assert report["verdict"] == "seal_broken", report
    assert report["layers"]["seal"]["failures"] == [sealed.lineage_id]
    lineage.close(); chain.close()


def test_the_same_write_to_an_unsealed_record_is_not_reported(tmp_path):
    """Companion: the check is on the seal, not on the write."""
    lineage, chain, _sealed, unsealed = _sealed_store(tmp_path)
    lineage.conn.execute("UPDATE lineage_records SET work_unit_id = 'WU-hist', record_hash = ? "
                         "WHERE lineage_id = ?",
                         (LineageStore.compute_record_hash({
                             "lineage_id": unsealed.lineage_id, "ts": unsealed.ts,
                             "query_hash": unsealed.query_hash, "injected": [], "contradictions": [],
                             "model_id": "m", "prompt_prefix_hash": unsealed.prompt_prefix_hash,
                             "response_hash": unsealed.response_hash, "work_unit_id": "WU-hist"}),
                          unsealed.lineage_id))
    lineage.conn.commit()
    report = verify_sealed_lineage(lineage, chain, lineage_id=unsealed.lineage_id)
    assert report["verdict"] == "intact" and report["layers"]["seal"]["unsealed"] == 1, report
    lineage.close(); chain.close()
