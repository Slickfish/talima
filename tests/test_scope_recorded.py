"""Packet AD — scope is recorded, not inferred.

WHY A FIELD NOW, HOLDING 'default'
    Everything recorded was unscoped, and scope is the class of thing that
    cannot be added retrospectively — `MAX()` proved that for weight
    history, expensively. A field that exists holding "default" costs almost
    nothing; a field that does not exist costs everything already recorded.

THE FOUR RECORDS THAT CARRY IT CHEAPLY, AND THE EXCLUSIONS, EXPLICIT
    carried:  lineage records, outcome events, recalibration events,
              envelope versions — each has a request in hand at write time.
    excluded: BELIEFS (a scope column on `triples` touches the (s,r,o,
              source) uniqueness key, add()'s chokepoint contract, and by
              implication retrieval partitioning — multi-tenancy inside the
              belief store, not a cheap field; the request field already
              arrives, so when that decision is taken it is a column
              decision only). TRAILS (a projection of lineage records —
              scope on the SOURCE preserves the option to derive scoped
              trails later by re-scan; scoping the projection now fragments
              a signal measured thin at 24 turns). CHAIN BLOCKS and
              SNAPSHOTS (KAIROS-produced, no request exists to carry one).

'default' IS A VALUE, NEVER NULL — a null is ambiguous between "not scoped"
and "scope unknown". And 'default' means two things across the migration
boundary (before: could not be stated; after: the request stated none),
decided by `scope_recordable_since` — records_begin_at's answer, verbatim.

THE SEAL: a stated scope enters the hashed body ONLY when not the default
(ArchiveBlock.compute_hash's `kind` precedent), so every pre-AD record
recomputes unchanged and a stated scope is tamper-evident rather than
rewritable under an intact seal — the notes defect (OQ-5), not repeated on
the one field a partner integration would rely on.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.server import create_app, create_services

TOKEN = "scope-token"
H = {"Authorization": f"Bearer {TOKEN}"}


class _Answering:
    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "The QTc change at 600mg was 11.4ms."

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return False


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap",
                          provider=_Answering())
    bid = svc.store.add(subject="qtc change at 600mg", relation="was",
                        object="11.4ms", weight=0.8, tier=1,
                        granularity=Granularity.TOPIC,
                        source="human_assertion")
    client = TestClient(create_app(svc))
    client.post("/governance/envelope/confirm", json={
        "version": "v1", "max_delta": 0.1, "movable_classes": ["asserted"],
        "reason": "AD fixture", "confirmed": True,
        "scope": "trial-alpha/setup"}, headers=H)
    return svc, client, bid


def _lineage_of_turn(client, svc, message, **extra):
    r = client.post("/chat", json={"message": message, **extra}, headers=H)
    done = [f for f in r.text.split("\n\n") if f.startswith("event: done")]
    assert done, f"turn did not complete: {r.status_code}"
    lid = json.loads(next(l[6:] for l in done[-1].split("\n")
                          if l.startswith("data: ")))["lineage_id"]
    return svc.lineage.get(lid)


def test_a_supplied_scope_and_an_absent_one_in_the_same_fixture(rig):
    """NON-VACUITY per the packet: one record written WITH a scope and one
    WITHOUT, side by side — the default case must not pass on a store where
    nothing was ever scoped."""
    svc, client, _bid = rig
    scoped = _lineage_of_turn(client, svc, "what was the qtc change?",
                              scope="trial-alpha/step-3")
    unscoped = _lineage_of_turn(client, svc, "and at 300mg?")
    assert scoped.scope == "trial-alpha/step-3"
    assert unscoped.scope == "default"
    assert unscoped.scope is not None, "default must be a VALUE, not null"


def test_outcome_scope_reaches_every_record_the_request_produces(rig):
    """One request, three record kinds: the outcome event, each
    recalibration event it authorises — and a Law 3 REFUSED outcome carries
    the stated scope too, or declined attempts become unscoped precisely
    where an auditor reads them."""
    svc, client, bid = rig
    rec = svc.lineage.record_turn(
        query="q", injected=[{"belief_id": bid, "cited": True,
                              "weight_at_injection": 0.8,
                              "tier_at_injection": 1}],
        contradictions=[], model_id="m", response_text="yes",
        prompt_prefix="p", scope="trial-alpha/step-3")
    body = {"event_type": "qc", "decision": "pass", "direction": "confirming",
            "references": {"lineage_id": rec.lineage_id}, "magnitude": 0.05,
            "scope": "trial-alpha/step-3", "confirmed": True}
    r = client.post("/outcome/confirm", json=body, headers=H).json()
    ev = svc.recalibration.get_outcome(r["event_id"])
    assert ev.scope == "trial-alpha/step-3"
    recal = svc.recalibration.events_for(bid)[-1]
    assert recal.scope == "trial-alpha/step-3"

    refused = client.post("/outcome/confirm",
                          json={**body, "confirmed": False}, headers=H)
    assert refused.status_code == 422
    ev2 = svc.recalibration.get_outcome(refused.json()["event_id"])
    assert ev2.accepted is False and ev2.scope == "trial-alpha/step-3"


def test_envelope_records_the_scope_it_was_installed_under(rig):
    svc, _client, _bid = rig
    rec = svc.recalibration.envelope_history()[-1]
    assert rec.scope == "trial-alpha/setup"


def test_nothing_infers_a_scope_from_content(rig):
    """THE ANTI-INFERENCE PIN. A message that NAMES a plausible scope, sent
    without the field, records 'default'. The substrate must not acquire an
    opinion it cannot explain."""
    svc, client, _bid = rig
    rec = _lineage_of_turn(
        client, svc,
        "in programme trial-alpha step 3, what was the qtc change?")
    assert rec.scope == "default", (
        f"scope {rec.scope!r} was INFERRED from message content — recorded, "
        f"never guessed, is the packet's whole contract")


def test_a_stated_scope_is_sealed_and_a_default_hashes_as_before(rig):
    """THE KIND PRECEDENT, both halves. A stated scope is inside the hashed
    body — rewriting it breaks verification (not the notes defect). An
    unstated scope contributes NOTHING to the hash, so every pre-AD record
    recomputes to its stored hash unchanged; proven by writing a legacy-style
    body by hand and comparing."""
    svc, client, bid = rig
    body = {"event_type": "qc", "decision": "pass", "direction": "confirming",
            "references": {"belief_ids": [bid]}, "magnitude": 0.05,
            "scope": "trial-alpha/step-3", "confirmed": True}
    eid = client.post("/outcome/confirm", json=body, headers=H).json()["event_id"]

    v = svc.recalibration.verify_governance_records(svc.archive_chain)
    assert v["verdict"] == "intact"
    svc.recalibration.conn.execute(
        "UPDATE outcome_events SET scope = 'trial-beta/step-9' "
        "WHERE event_id = ?", (eid,))
    svc.recalibration.conn.commit()
    v2 = svc.recalibration.verify_governance_records(svc.archive_chain)
    assert v2["verdict"] == "row_hash_mismatch", (
        "a rewritten scope verified clean — the field is decoration under "
        "an intact seal, the notes defect on the one field a partner "
        "integration would rely on")
    assert any(f["key"] == eid for f in v2["row"]["failures"])

    # The other half: an UNSCOPED record's hash is byte-identical to the
    # pre-AD construction of the same body.
    from backend.kb.recalibration import RecalibrationStore, _canonical_hash
    ev = svc.recalibration.record_outcome(
        event_type="qc", decision="pass", direction="neutral",
        references={"belief_ids": [bid]})
    assert ev.scope == "default"
    legacy_body = {"event_id": ev.event_id, "ts": ev.ts, "event_type": "qc",
                   "decision": "pass", "direction": "neutral",
                   "actor": {"id": "operator", "role": "local_operator",
                             "source": "local"},
                   "references": {"belief_ids": [bid]},
                   "workflow_ref": "", "accepted": True,
                   "refusal_reason": ""}
    assert "scope" not in legacy_body
    assert _canonical_hash(legacy_body) == ev.record_hash, (
        "an unscoped record's hash moved — every pre-AD record would now "
        "fail verification, which is the one change a sealed history must "
        "never suffer")


@pytest.mark.sqlite_only("builds a pre-AD store by hand with sqlite3 and migrates it")
def test_a_pre_ad_store_migrates_and_old_rows_read_default(tmp_path):
    """The additive migration, proven against a genuinely OLD store: tables
    created without the column, rows inserted, then the current stores open
    it — column appears, old rows read 'default', nothing else moves."""
    db = tmp_path / "kb.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE lineage_records (
        lineage_id TEXT PRIMARY KEY, ts REAL NOT NULL,
        query_hash TEXT NOT NULL, injected_json TEXT NOT NULL,
        contradictions_json TEXT NOT NULL, model_id TEXT NOT NULL,
        prompt_prefix_hash TEXT NOT NULL, response_hash TEXT NOT NULL,
        record_hash TEXT NOT NULL, sealed_block INTEGER)""")
    con.execute("INSERT INTO lineage_records VALUES "
                "('old-1', 123.0, 'q', '[]', '[]', 'm', 'p', 'r', 'h', NULL)")
    con.commit(); con.close()

    from backend.memory.lineage import LineageStore
    store = LineageStore(db)
    rec = store.get("old-1")
    assert rec is not None and rec.scope == "default"
    assert store.scope_recordable_since() is not None
    # And it is decidable which side of the boundary the old row is on.
    assert rec.ts < store.scope_recordable_since()


def test_scope_too_long_is_refused_by_name(rig):
    _svc, client, bid = rig
    huge = "x" * 5000
    r = client.post("/chat", json={"message": "hi", "scope": huge}, headers=H)
    assert r.status_code == 422 and r.json()["error"] == "scope_too_long"
    r2 = client.post("/outcome/confirm", json={
        "event_type": "qc", "decision": "p", "direction": "confirming",
        "references": {"belief_ids": [bid]}, "scope": huge,
        "confirmed": True}, headers=H)
    assert r2.status_code == 422 and r2.json()["error"] == "scope_too_long"


def test_coverage_is_declared_on_the_surface(rig):
    _svc, client, _bid = rig
    block = client.get("/cortex/stats").json()["scope"]
    assert block["recordable_since"] is not None
    assert block["default_value"] == "default"
    assert "never inferred" in block["note"]


def test_the_exclusions_are_explicit_not_implied(rig):
    """The amended sentinel: every record that can carry one CHEAPLY does;
    the ones that cannot are pinned as exclusions, so a scope column
    appearing on `triples` or `relation_trails` is a deliberate future
    decision that must come here and say so — not a drive-by."""
    svc, _client, _bid = rig
    # BD-2 — the `triples` half of this pin asserted that one literal name was
    # absent, and any other name walked through (BC, item 11). It is now the
    # two-layer guard below, which fails on any
    # column a human has not answered for. The `relation_trails` half is
    # unchanged; Phase 1 extends the pattern to the lineage tables.
    trail_cols = {c.name for c in svc.store.conn.columns("relation_trails")}
    assert "scope" not in trail_cols, (
        "a scope column appeared on `relation_trails` — trails are a "
        "projection of lineage records, which now carry scope; scoped "
        "trails are derivable by re-scan and scoping the projection was "
        "deliberately declined (AD Q3)")


# ===========================================================================
# BD-2 — the `triples` column guard: derived, two layers, both directions
# ===========================================================================
#
# WHAT IT PROTECTS. A column on `triples` takes OQ-12 — whether every claim
# carries the name of whoever contributed it — by accident, and touches the
# `(subject, relation, object, source)` key, `add()`'s contract and retrieval
# partitioning. The old pin asserted that one literal name was absent and let
# every other name through; the register claimed for 24 days that it
# produced a message naming the decision, and it produced none.
#
# TWO LAYERS, both set equalities over the FULL `PRAGMA table_info` tuple
# (cid, name, type, notnull, dflt_value, pk), so a change to a default or a
# key flag fails exactly as an added or removed column does:
#
#   layer 1  store == DDL        drift: a store a migration or direct SQL moved
#                                away from `schema.py`. The database
#                                introspects itself; no SQL text is parsed.
#   layer 2  DDL == answered-for  deliberateness: the DDL and the store move
#                                together in one commit (the case Phase 2 will
#                                produce), so a test-side set that DUPLICATES
#                                the DDL is what forces the second, recorded
#                                edit. The duplication is the point.
#
# The DDL is read at CALL time through the module attribute
# (`backend.kb.schema.SCHEMA_SQL`), never bound at import, so a companion can
# monkeypatch a copy and the guard sees it. Every companion below plants a
# name this section does not contain, and asserts the MESSAGE, not only the
# failure.

import backend.kb.schema as _schema

OQ12_SENTENCE = ("A column on `triples` is a deferred decision (OQ-12: whether every "
                 "claim carries the name of whoever contributed it).")

# The answered-for set. One tuple per column, the full table_info row, with
# the packet or decision that answered for it. Adding a column to `triples`
# means adding it HERE, with a reason, in the same commit as the DDL.
ANSWERED_TRIPLES_COLUMNS = frozenset({
    (0,  "id",             "INTEGER", 0, None,  1),   # Phase 2 — the row key
    (1,  "subject",        "TEXT",    1, None,  0),   # Phase 2 — the claim
    (2,  "relation",       "TEXT",    1, None,  0),   # Phase 2
    (3,  "object",         "TEXT",    1, None,  0),   # Phase 2
    (4,  "weight",         "REAL",    1, "0.5", 0),   # Phase 2 — written by add() alone (S.1)
    (5,  "tier",           "INTEGER", 1, "2",   0),   # Phase 2 — Law 4 tiers, never delete
    (6,  "granularity",    "TEXT",    1, None,  0),   # Phase 2 — adaptive granularity
    (7,  "source",         "TEXT",    1, None,  0),   # Phase 2 — inside the uniqueness key (F.3 classes)
    (8,  "fire_count",     "INTEGER", 1, "0",   0),   # Phase 3 — citation reinforcement counter (F.5)
    (9,  "created_at",     "REAL",    1, None,  0),   # Phase 2
    (10, "last_fired",     "REAL",    1, None,  0),   # Phase 2 — the decay clock (AN.2)
    (11, "archived",       "INTEGER", 1, "0",   0),   # F.2 — record-layer status, not prominence
    (12, "archive_reason", "TEXT",    0, None,  0),   # L.2a — why a row was archived
    (13, "archived_at",    "REAL",    0, None,  0),   # L.2a — when
})


def table_columns(conn, table: str = "triples") -> frozenset:
    """The full `PRAGMA table_info` rows of `table`, as tuples."""
    return frozenset(tuple(r) for r in conn.execute(f"PRAGMA table_info({table})"))


def ddl_columns(ddl: str | None = None) -> frozenset:
    """The columns the DDL declares, read by executing it into `:memory:` and
    asking the database — never by parsing SQL text. `ddl` defaults to the
    module attribute AT CALL TIME so a patched copy is what the guard reads."""
    if ddl is None:
        ddl = _schema.SCHEMA_SQL
    mem = sqlite3.connect(":memory:")
    try:
        mem.executescript(ddl)
        return table_columns(mem)
    finally:
        mem.close()


def _column_message(column: str, present_in: str, absent_from: str, detail: str = "") -> str:
    return (f"{OQ12_SENTENCE} This column is present in {present_in} and absent from "
            f"{absent_from}: `{column}`{detail}. Add it with a recorded reason, or remove it.")


def _compare_column_sets(left: frozenset, right: frozenset, *, left_name: str,
                         right_name: str, layer: str) -> None:
    """Both directions, and shape: raise AssertionError naming the column, the
    side and the decision whenever `left` and `right` differ."""
    if left == right:
        return
    by_name_l = {c[1]: c for c in left}
    by_name_r = {c[1]: c for c in right}
    problems = []
    for name in sorted(set(by_name_l) - set(by_name_r)):
        problems.append(_column_message(name, left_name, right_name))
    for name in sorted(set(by_name_r) - set(by_name_l)):
        problems.append(_column_message(name, right_name, left_name))
    for name in sorted(set(by_name_l) & set(by_name_r)):
        if by_name_l[name] != by_name_r[name]:
            problems.append(_column_message(
                name, f"{left_name} as {by_name_l[name]}", f"{right_name} as {by_name_r[name]}",
                detail=" (shape differs)"))
    raise AssertionError(f"[{layer}] " + "\n".join(problems))


def check_layer1_store_matches_ddl(store_conn, ddl: str | None = None) -> None:
    _compare_column_sets(table_columns(store_conn), ddl_columns(ddl),
                         left_name="the store", right_name="the DDL", layer="layer 1: store vs DDL")


def check_layer2_ddl_matches_answered(answered: frozenset = ANSWERED_TRIPLES_COLUMNS,
                                      ddl: str | None = None) -> None:
    _compare_column_sets(ddl_columns(ddl), answered,
                         left_name="the DDL", right_name="the answered-for set",
                         layer="layer 2: DDL vs answered-for set")


# ---- the guard, both layers --------------------------------------------------

@pytest.mark.sqlite_only("the SQLite DDL pin (PRAGMA table_info against the DDL run into :memory:); its Postgres twin is tests/test_w3b_dialect.py D")
def test_layer1_the_fixture_store_matches_the_ddl(rig):
    svc, _client, _bid = rig
    check_layer1_store_matches_ddl(svc.store.conn)


def test_layer2_the_ddl_matches_the_answered_for_set():
    check_layer2_ddl_matches_answered()


@pytest.mark.sqlite_only("the SQLite DDL pin (PRAGMA table_info against the DDL run into :memory:); its Postgres twin is tests/test_w3b_dialect.py D")
def test_the_establish_agrees_and_is_not_vacuous(rig):
    """Non-vacuity for both layers: the population is fourteen columns read
    from two places that agree, not an empty set equal to an empty set."""
    svc, _client, _bid = rig
    assert len(table_columns(svc.store.conn)) == len(ddl_columns()) == len(ANSWERED_TRIPLES_COLUMNS) == 14


# ---- companions: layer 1, the store side -------------------------------------

def _temp_store(tmp_path):
    from backend.kb.tiered_store import TripleStore
    return TripleStore(tmp_path / "planted.db")


def _assert_fires(exc, column: str, present_in: str, absent_from: str, layer: str):
    msg = str(exc.value)
    assert f"[{layer}]" in msg
    assert f"`{column}`" in msg, msg
    assert f"present in {present_in}" in msg and f"absent from {absent_from}" in msg, msg
    assert "OQ-12" in msg and "recorded reason" in msg, msg


@pytest.mark.sqlite_only("the SQLite DDL pin (PRAGMA table_info against the DDL run into :memory:); its Postgres twin is tests/test_w3b_dialect.py D")
def test_companion_1_a_planted_text_column_fails_layer1_naming_it(tmp_path):
    store = _temp_store(tmp_path)
    store.conn.execute("ALTER TABLE triples ADD COLUMN tenant_id TEXT")
    with pytest.raises(AssertionError) as e:
        check_layer1_store_matches_ddl(store.conn)
    store.close()
    _assert_fires(e, "tenant_id", "the store", "the DDL", "layer 1: store vs DDL")


@pytest.mark.sqlite_only("the SQLite DDL pin (PRAGMA table_info against the DDL run into :memory:); its Postgres twin is tests/test_w3b_dialect.py D")
def test_companion_2_a_nullable_defaulted_column_fails_layer1_the_same_way(tmp_path):
    store = _temp_store(tmp_path)
    store.conn.execute("ALTER TABLE triples ADD COLUMN region TEXT DEFAULT 'au'")
    with pytest.raises(AssertionError) as e:
        check_layer1_store_matches_ddl(store.conn)
    store.close()
    _assert_fires(e, "region", "the store", "the DDL", "layer 1: store vs DDL")


@pytest.mark.sqlite_only("the SQLite DDL pin (PRAGMA table_info against the DDL run into :memory:); its Postgres twin is tests/test_w3b_dialect.py D")
def test_companion_3_a_missing_column_fails_layer1_the_other_way(tmp_path):
    store = _temp_store(tmp_path)
    store.conn.execute("ALTER TABLE triples DROP COLUMN archived_at")
    with pytest.raises(AssertionError) as e:
        check_layer1_store_matches_ddl(store.conn)
    store.close()
    _assert_fires(e, "archived_at", "the DDL", "the store", "layer 1: store vs DDL")


def test_companion_4_a_shape_change_alone_fails_layer1(tmp_path):
    """Same name, nullability flipped, nothing else: the full tuple catches it."""
    reshaped = _schema.SCHEMA_SQL.replace("archive_reason TEXT,",
                                          "archive_reason TEXT NOT NULL DEFAULT '',")
    assert reshaped != _schema.SCHEMA_SQL, "the fixture did not change the DDL copy"
    conn = sqlite3.connect(tmp_path / "reshaped.db")
    conn.executescript(reshaped)
    with pytest.raises(AssertionError) as e:
        check_layer1_store_matches_ddl(conn)
    conn.close()
    msg = str(e.value)
    assert "`archive_reason`" in msg and "shape differs" in msg and "OQ-12" in msg, msg


# ---- companions: layer 2, the DDL side ---------------------------------------

def _ddl_with(extra_decl: str) -> str:
    """A copy of the DDL with one more column at the end of `triples`."""
    patched = _schema.SCHEMA_SQL.replace("    archived_at    REAL,\n",
                                         f"    archived_at    REAL,\n    {extra_decl},\n")
    assert patched != _schema.SCHEMA_SQL, "the DDL anchor moved — re-derive this fixture"
    return patched


def test_companion_5_the_column_phase_2_will_add_fails_layer2_until_answered_for(monkeypatch):
    """THE REASON THE PACKET EXISTS. DDL and store move together; only the
    answered-for set stands between the column and the build."""
    monkeypatch.setattr(_schema, "SCHEMA_SQL", _ddl_with("work_unit_id TEXT"))
    with pytest.raises(AssertionError) as e:
        check_layer2_ddl_matches_answered()
    _assert_fires(e, "work_unit_id", "the DDL", "the answered-for set",
                  "layer 2: DDL vs answered-for set")
    # And layer 1 alone would NOT have caught it: a store built from the
    # patched DDL matches the patched DDL.
    mem = sqlite3.connect(":memory:")
    mem.executescript(_schema.SCHEMA_SQL)
    check_layer1_store_matches_ddl(mem)
    mem.close()


def test_companion_6_a_column_removed_from_the_ddl_fails_layer2_the_other_way(monkeypatch):
    removed = _schema.SCHEMA_SQL.replace("    archived_at    REAL,\n", "")
    assert removed != _schema.SCHEMA_SQL
    monkeypatch.setattr(_schema, "SCHEMA_SQL", removed)
    with pytest.raises(AssertionError) as e:
        check_layer2_ddl_matches_answered()
    _assert_fires(e, "archived_at", "the answered-for set", "the DDL",
                  "layer 2: DDL vs answered-for set")


def test_non_vacuity_the_ratchet_admits_a_column_a_human_answered_for(monkeypatch):
    """A ratchet that cannot be satisfied deliberately is not a ratchet."""
    monkeypatch.setattr(_schema, "SCHEMA_SQL", _ddl_with("work_unit_id TEXT"))
    answered = ANSWERED_TRIPLES_COLUMNS | {(14, "work_unit_id", "TEXT", 0, None, 0)}
    check_layer2_ddl_matches_answered(answered)          # passes


@pytest.mark.sqlite_only("the SQLite DDL pin (PRAGMA table_info against the DDL run into :memory:); its Postgres twin is tests/test_w3b_dialect.py D")
def test_regression_witness_scope_is_still_refused(tmp_path):
    """Continuity with the old pin; proves nothing the six above do not."""
    store = _temp_store(tmp_path)
    store.conn.execute("ALTER TABLE triples ADD COLUMN scope TEXT")
    with pytest.raises(AssertionError) as e:
        check_layer1_store_matches_ddl(store.conn)
    store.close()
    assert "`scope`" in str(e.value)


def test_the_guard_names_no_planted_column():
    """The companions prove the class only if their names are absent from the
    guard: every string constant in the guard's helpers and the answered-for
    set, by AST, against the names the companions plant."""
    import ast
    from pathlib import Path as _P
    tree = ast.parse(_P(__file__).read_text(encoding="utf-8"))
    guard_names = {"ANSWERED_TRIPLES_COLUMNS", "table_columns", "ddl_columns",
                   "_column_message", "_compare_column_sets",
                   "check_layer1_store_matches_ddl", "check_layer2_ddl_matches_answered"}
    literals: set[str] = set()
    for node in tree.body:
        name = getattr(node, "name", None) or (
            node.targets[0].id if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name) else None)
        if name in guard_names:
            literals |= {c.value for c in ast.walk(node)
                         if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    planted = {"tenant_id", "region", "work_unit_id", "scope"}
    assert not (planted & literals), f"the guard names a planted column: {planted & literals}"


# ===========================================================================
# v3.1 Phase 1 — the same two-layer guard over the two new tables and the
# lineage table (the extension BD-2 named as Phase 1's job)
# ===========================================================================

import backend.memory.lineage as _lineage_mod

# The DDL each table is declared in, read at CALL time (a companion patches
# a copy). `work_unit` and `triple_work_unit` live in schema.WORK_UNIT_DDL;
# `lineage_records` in lineage.SCHEMA_SQL.
def _ddl_for(table: str) -> str:
    if table in LINEAGE_DDL_TABLES:
        return _lineage_mod.SCHEMA_SQL
    if table == "work_unit_events":
        return _schema.WORK_UNIT_EVENTS_DDL
    return _schema.WORK_UNIT_DDL


ANSWERED_COLUMNS = {
    # v3.1 Phase 1 — every column, with the packet that answered for it.
    "work_unit": frozenset({
        (0,  "work_unit_id",         "TEXT",    0, None,   1),   # Phase 1 — application-supplied id
        (1,  "work_unit_type",       "TEXT",    1, None,   0),   # Phase 1
        (2,  "accountable_identity", "TEXT",    0, None,   0),   # Phase 1 — recorded, not authenticated
        (3,  "opened_at",            "REAL",    1, None,   0),   # Phase 1
        (4,  "closed_at",            "REAL",    0, None,   0),   # Phase 1; 1b — set once by `decision_boundary: close`, the row's second and last write
        (5,  "opened_by",            "TEXT",    1, None,   0),   # Phase 1; 1b — actor fields as canonical JSON (pre-1b rows: a bare literal)
        (6,  "closed_by",            "TEXT",    0, None,   0),   # Phase 1; 1b — actor fields as canonical JSON
        (7,  "parent_work_unit_id",  "TEXT",    0, None,   0),   # Phase 1 — optional nesting
        (8,  "attributes_json",      "TEXT",    1, "'{}'", 0),   # Phase 1 — never interpreted
        (9,  "attribution_status",   "TEXT",    1, None,   0),   # Phase 1 — attributed | unattributed (no 'inferred')
        (10, "session_id",           "TEXT",    0, None,   0),   # Phase 1; 1b — the session that first named the unit (nothing re-reads an open unit by it any more)
    }),
    "triple_work_unit": frozenset({
        (0, "triple_id",       "INTEGER", 1, None, 1),           # Phase 1 — the row add() returned
        (1, "work_unit_id",    "TEXT",    1, None, 2),           # Phase 1
        (2, "first_seen_turn", "INTEGER", 1, None, 0),           # Phase 1 — admission, not use
        (3, "lineage_id",      "TEXT",    0, None, 0),           # Phase 1 — the answer it was admitted during
    }),
    # W6 — the boundary ledger: one row per close or reopen, append-only, sealed with the governance records.
    "work_unit_events": frozenset({
        (0,  "event_id",           "TEXT",    0, None,   1),   # W6 — the act's id
        (1,  "ts",                 "REAL",    1, None,   0),   # W6 — when the act was made (after the turn's record)
        (2,  "work_unit_id",       "TEXT",    1, None,   0),   # W6
        (3,  "kind",               "TEXT",    1, None,   0),   # W6 — close | reopen
        (4,  "actor_id",           "TEXT",    1, None,   0),   # W6 — the request's actor fields, asserted
        (5,  "actor_role",         "TEXT",    1, None,   0),   # W6
        (6,  "actor_source",       "TEXT",    1, None,   0),   # W6
        (7,  "accountable",        "TEXT",    0, None,   0),   # W6 — the request's accountable identity
        (8,  "lineage_ids",        "TEXT",    1, "'[]'", 0),   # W6 — a close: the unit's record ids at the act, the asserting record's included
        (9,  "reverses",           "TEXT",    0, None,   0),   # W6 — a reopen: the close it reverses
        (10, "asserted_on",        "TEXT",    0, None,   0),   # W6 fix round — the record of the turn that asserted it
        (11, "identity_mechanism", "TEXT",    0, None,   0),   # W6 fix round — asserted (NULL) | proxy, as on the record
        (12, "record_hash",        "TEXT",    1, None,   0),   # W6 — the act's hash, sealed into a governance block
        (13, "sealed_block",       "INTEGER", 0, None,   0),   # W6 — the block that sealed it
    }),
    "lineage_records": frozenset({
        (0,  "lineage_id",          "TEXT",    0, None,        1),   # P3/L2
        (1,  "ts",                  "REAL",    1, None,        0),   # P3/L2
        (2,  "query_hash",          "TEXT",    1, None,        0),   # P3/L2
        (3,  "injected_json",       "TEXT",    1, None,        0),   # P3/L2
        (4,  "contradictions_json", "TEXT",    1, None,        0),   # P3/L2
        (5,  "model_id",            "TEXT",    1, None,        0),   # P3/L2
        (6,  "prompt_prefix_hash",  "TEXT",    1, None,        0),   # P3/L2
        (7,  "response_hash",       "TEXT",    1, None,        0),   # P3/L2
        (8,  "record_hash",         "TEXT",    1, None,        0),   # P3/L2
        (9,  "sealed_block",        "INTEGER", 0, None,        0),   # P3/L2 — KAIROS backfills
        (10, "scope",               "TEXT",    1, "'default'", 0),   # AD
        (11, "citation_test",       "TEXT",    0, None,        0),   # AU — unhashed
        (12, "context_json",        "TEXT",    0, None,        0),   # AW — unhashed
        (13, "request_hash",        "TEXT",    0, None,        0),   # BA fix 2
        (14, "work_unit_id",        "TEXT",    0, None,        0),   # v3.1 Phase 1 — hashed when stated
        (15, "accountable_identity", "TEXT",   0, None,        0),   # v3.1 Phase 1 — hashed when stated
        (16, "boundary_asserted",   "TEXT",    0, None,        0),   # v3.1 Phase 1b — the request's boundary; hashed when stated
        (17, "boundary_asserted_by", "TEXT",   0, None,        0),   # v3.1 Phase 1b — actor fields, canonical JSON; hashed when stated
        (18, "attribution_flags",   "TEXT",    0, None,        0),   # v3.1 Phase 1b — JSON list; UNHASHED, re-derivable from 15+16
        (19, "learning_mode",       "TEXT",    0, None,        0),   # LS — learning state at write; sealed when non-default; LS
        (20, "answer_hash",         "TEXT",    0, None,        0),   # C-38 — sha256 of the stated answer; sealed when present
        (21, "reasoning_hash",      "TEXT",    0, None,        0),   # C-38 — sha256 of the reasoning; sealed when present; NULL = none
        (22, "answer_chars",        "INTEGER", 0, None,        0),   # C-38 — UNHASHED length; the data A's cap review waits for
        (23, "reasoning_chars",     "INTEGER", 0, None,        0),   # C-38 — UNHASHED length; NULL when the reply carried no reasoning
        (24, "answer_empty_reason", "TEXT",    0, None,        0),   # C-38 — UNHASHED provenance, like citation_test; NULL unless the answer was empty
        (25, "record_scope",        "TEXT",    0, None,        0),   # MCP-2 — full_turn (NULL, the console) | mcp_block; sealed when non-default
        (26, "inference_seed",      "INTEGER", 0, None,        0),   # W0b — the seed sent with the answering request; sealed when present
        (27, "model_digest",        "TEXT",    0, None,        0),   # W0b — the model's content digest as served; sealed when present
        (28, "inference_config_hash", "TEXT",  0, None,        0),   # W0b — keys inference_configs (the options sent); sealed when present
        (29, "environment_hash",    "TEXT",    0, None,        0),   # W0b — keys environments (the runtime facts); sealed when present
        (30, "capture_notes",       "TEXT",    0, None,        0),   # W0b — canonical JSON: the reason for each absent capture field; sealed when present
        (31, "identity_mechanism",  "TEXT",    0, None,        0),   # W4 — how accountable_identity was established: asserted (NULL, the default) | proxy; sealed when non-default; W4
        (32, "envelope_version",    "TEXT",    0, None,        0),   # W6 — the recalibration envelope in force at the write; NULL under the compiled default and on every record before W6; sealed when present; W6
        (33, "forecast",            "TEXT",    0, None,        0),   # W6 — the citation forecast's turn-level score as canonical JSON; NULL when no forecast ran; sealed when present; W6
    }),
    # W0b — the replay capture's companions, kept beside the record and
    # verified by it (or keyed by a hash it seals). Never in the record's hash.
    "request_bodies": frozenset({
        (0, "request_hash",  "TEXT",    0, None, 1),             # W0b — the record's sealed request_hash; the bytes must hash to it
        (1, "encoding",      "TEXT",    1, None, 0),             # W0b — a declared encoding (lineage.REQUEST_ENCODINGS)
        (2, "body",          "BLOB",    1, None, 0),             # W0b — zlib over the exact bytes request_hash covers
        (3, "raw_bytes",     "INTEGER", 1, None, 0),             # W0b — the uncompressed length, checked on verify
        (4, "first_seen_ts", "REAL",    1, None, 0),             # W0b — the first record that kept these bytes
    }),
    "inference_configs": frozenset({
        (0, "config_hash",   "TEXT",    0, None, 1),             # W0b — sha256 of the canonical options
        (1, "config_json",   "TEXT",    1, None, 0),             # W0b — the request body minus messages and seed, as sent
        (2, "first_seen_ts", "REAL",    1, None, 0),             # W0b
    }),
    "environments": frozenset({
        (0, "environment_hash", "TEXT", 0, None, 1),             # W0b — sha256 of the canonical descriptor
        (1, "descriptor_json",  "TEXT", 1, None, 0),             # W0b — observable runtime facts; absent ones with reasons
        (2, "first_seen_ts",    "REAL", 1, None, 0),             # W0b
    }),
}

# W0b — every table declared in lineage.SCHEMA_SQL that this guard answers for.
LINEAGE_DDL_TABLES = ("lineage_records", "request_bodies", "inference_configs", "environments")

# LS — the reason the column arrived, as a value a test can read (the comment
# beside the entry is for the reader; this is for tests/test_ls_learning_state.py).
LS_REASON = "learning state at write; sealed when non-default; LS"
# W4 — the same for the record's identity mechanism (tests/test_w4_identity_record.py R6).
W4_REASON = "how accountable_identity was established: asserted (NULL, the default) | proxy; sealed when non-default; W4"


def ddl_columns_of(table: str, ddl: str | None = None) -> frozenset:
    mem = sqlite3.connect(":memory:")
    try:
        mem.executescript(ddl if ddl is not None else _ddl_for(table))
        return table_columns(mem, table)
    finally:
        mem.close()


def check_table_layer1(store_conn, table: str, ddl: str | None = None) -> None:
    _compare_column_sets(table_columns(store_conn, table), ddl_columns_of(table, ddl),
                         left_name="the store", right_name="the DDL",
                         layer=f"layer 1: store vs DDL ({table})")


def check_table_layer2(table: str, answered: frozenset | None = None, ddl: str | None = None) -> None:
    _compare_column_sets(ddl_columns_of(table, ddl),
                         ANSWERED_COLUMNS[table] if answered is None else answered,
                         left_name="the DDL", right_name="the answered-for set",
                         layer=f"layer 2: DDL vs answered-for set ({table})")


@pytest.mark.parametrize("table", sorted(ANSWERED_COLUMNS))
@pytest.mark.sqlite_only("the SQLite DDL pin (PRAGMA table_info against the DDL run into :memory:); its Postgres twin is tests/test_w3b_dialect.py D")
def test_phase1_layer1_the_fixture_store_matches_the_ddl(rig, table):
    svc, _client, _bid = rig
    conn = svc.lineage.conn if table in LINEAGE_DDL_TABLES else svc.store.conn
    check_table_layer1(conn, table)


@pytest.mark.parametrize("table", sorted(ANSWERED_COLUMNS))
def test_phase1_layer2_the_ddl_matches_the_answered_for_set(table):
    check_table_layer2(table)
    assert len(ddl_columns_of(table)) == len(ANSWERED_COLUMNS[table]) > 0


def test_phase1_companion_tenant_on_work_unit_fails_layer2_until_answered_for(monkeypatch):
    patched = _schema.WORK_UNIT_DDL.replace("    session_id            TEXT\n",
                                            "    session_id            TEXT,\n    tenant                TEXT\n")
    assert patched != _schema.WORK_UNIT_DDL
    monkeypatch.setattr(_schema, "WORK_UNIT_DDL", patched)
    with pytest.raises(AssertionError) as e:
        check_table_layer2("work_unit")
    msg = str(e.value)
    assert "`tenant`" in msg and "present in the DDL" in msg and "OQ-12" in msg, msg


def test_phase1_companion_a_column_on_lineage_records_fails_layer2_until_answered_for(monkeypatch):
    # Planted after the table's last column (W0b moved the last column from
    # `record_scope` to `capture_notes`, W4 from `capture_notes` to
    # `identity_mechanism`, W6 from `identity_mechanism` to `forecast`, taking
    # the name this companion used to plant, `envelope_version`, for a real
    # column; the anchor moved with it each time).
    patched = _lineage_mod.SCHEMA_SQL.replace("    forecast            TEXT\n",
                                              "    forecast            TEXT,\n    confirmation_ref TEXT\n")
    assert patched != _lineage_mod.SCHEMA_SQL
    monkeypatch.setattr(_lineage_mod, "SCHEMA_SQL", patched)
    with pytest.raises(AssertionError) as e:
        check_table_layer2("lineage_records")
    msg = str(e.value)
    assert "`confirmation_ref`" in msg and "present in the DDL" in msg and "OQ-12" in msg, msg


def test_phase1_the_guards_name_no_planted_column():
    import ast as _ast
    from pathlib import Path as _P
    tree = _ast.parse(_P(__file__).read_text(encoding="utf-8"))
    names = {"ANSWERED_COLUMNS", "ddl_columns_of", "check_table_layer1", "check_table_layer2", "_ddl_for"}
    literals: set[str] = set()
    for node in tree.body:
        name = getattr(node, "name", None) or (
            node.targets[0].id if isinstance(node, _ast.Assign)
            and isinstance(node.targets[0], _ast.Name) else None)
        if name in names:
            literals |= {c.value for c in _ast.walk(node)
                         if isinstance(c, _ast.Constant) and isinstance(c.value, str)}
    assert not ({"tenant", "confirmation_ref"} & literals)
