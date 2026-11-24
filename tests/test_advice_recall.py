"""AR (advice recall) — the first backward read of the record.

Given a claim, every sealed answer that rested on it. Built from the
INJECTED set, never the cited flag (the flag under-reports by about a
fifth, measured; a recall that misses affected answers is wrong in the
unsafe direction). Read-only; keyed on the claim id (S1.2: ids are stable
across the monotone upsert and across archival); supersede is a reason on
the existing outcome act, no new act kind (S1.3).

T1  recall reads injected, not cited — the fixture returns 7, not 4
T1c two under-inclusive variants fail T1's assertion (the second never
    mentions `cited`)
T2  the route has no write path (derived writer names; call set printed)
T2c a write through a helper the check does not list is caught by the
    behavioural hash check
T3  fifty recalls leave every lineage hash identical
T4  the route is classified with the per-person declaration; a planted
    unclassified route is named by the existing guard
T5  identity is labelled `asserted`, never `verified`
T6  supersede returns and records the count and the route; standing moves
    only within the printed bound; lineage untouched
T6b the read works with the adjustment kill switch off
T6c a claim no answer rested on reports 0, present
T7  the stats block is recomputed from the record
T8  the response shape is a pinned contract at version 2 (W4: the row's
    `identity_label` reads the record), reconciled three ways
T12 the index and the record agree; a planted divergence is named
"""
from __future__ import annotations

import ast
import inspect
import json
import re
import sqlite3
import statistics
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.kb.recalibration import Envelope, RecalibrationStore
from backend.kb.tiered_store import TripleStore, classify
from backend.memory.chain import NexusArchiveChain
from backend.memory.lineage import LineageStore, verify_sealed_lineage
from backend.server import create_app, create_services
from tests._tracked import tracked_backend
from tests.fixtures.advice_recall_fixture import (EXPECTED_CITED_COUNT, EXPECTED_RECALL_COUNT,
                                                  SOURCE, build)
from tests.test_auth_coverage import EXPECTED_WRITERS
from tests.test_bind_conditional_routes import (DELIBERATELY_OPEN, PER_PERSON_ROWS, WRITE_METHODS,
                                                _is_gated_via, derive_routes)

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "advice-recall-token"
H = {"Authorization": f"Bearer {TOKEN}"}
RECALL_PATH = "/belief/{belief_id}/recall"

# T8 — the pinned response shape. A change here is a version bump. Version 1
# (AR); version 2 (W4): each row's `identity_label` reads the record's
# `identity_mechanism` (`asserted` | `proxy`) where version 1 printed the
# literal `asserted` on every row, and `per_person` says what each label
# means. The keys did not change, so the version-1 sets stay defined
# (tests/test_ls_learning_state.py reads the row keys by that name).
RECALL_RESPONSE_KEYS_V1 = frozenset({
    "belief_id", "resolved", "claim", "records", "total", "limit", "offset", "sorted_by",
    "read_from", "per_person", "note", "contract_version"})
RECALL_ROW_KEYS_V1 = frozenset({
    "lineage_id", "ts", "work_unit_id", "accountable", "identity_label", "scope",
    "weight_at_injection", "cited", "tier_at_injection", "response_hash", "sealed_block"})
RECALL_RESPONSE_KEYS_V2 = RECALL_RESPONSE_KEYS_V1
RECALL_ROW_KEYS_V2 = RECALL_ROW_KEYS_V1
RECALL_IDENTITY_LABELS_V2 = frozenset({"asserted", "proxy"})


@pytest.fixture
def fx(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    return build(tmp_path)


def _lineage(fx) -> LineageStore:
    return LineageStore(fx.kb_path)


def _client(fx, tmp_path):
    svc = create_services(kb_db_path=fx.kb_path, chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl", archive_db_path=fx.archive_path,
                          snapshots_dir=tmp_path / "snap")
    return svc, TestClient(create_app(svc))


def _hashes(kb_path) -> list[tuple]:
    """Every lineage row, every column — not just `record_hash`. A write to
    an unhashed column (scope on a default row, citation_test, context) is
    still a write, and a check over the hash column alone would miss it."""
    from backend.store import connect as store_connect
    from tests.conftest import store_target_for
    c = store_connect(store_target_for(kb_path))
    try:
        return [tuple(r) for r in c.execute("SELECT * FROM lineage_records ORDER BY lineage_id").fetchall()]
    finally:
        c.close()


# ---------------------------------------------------------------------------
# T1 / T1c — injected, not cited
# ---------------------------------------------------------------------------

def assert_recall_complete(rows: list[dict], expected: tuple) -> None:
    """T1's assertion: every record that INJECTED the claim, in time order,
    with the unit, date, weight and cited value the fixture fixed."""
    assert len(rows) == len(expected) == EXPECTED_RECALL_COUNT, (
        f"recall returned {len(rows)} rows; {len(expected)} records injected the claim")
    for got, exp in zip(rows, expected):
        for k in ("lineage_id", "work_unit_id", "accountable", "ts", "weight_at_injection", "cited"):
            assert got[k] == exp[k], (k, got, exp)


def test_recall_reads_injected_not_cited(fx):
    """The recall is built from the injected set, because the cited flag under-reports by
    about a fifth and a recall that misses affected answers is wrong in the unsafe direction."""
    lineage = _lineage(fx)
    rows, total = lineage.records_injecting(fx.rev3)
    assert_recall_complete(rows, fx.expected)
    assert total == 7 and sum(1 for r in rows if r["cited"]) == EXPECTED_CITED_COUNT == 4
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows), "sorted by time"
    lineage.close()


def test_under_inclusive_recall_fails_T1(fx):
    """A recall that drops any injected record fails T1, whatever the reason it drops it."""
    lineage = _lineage(fx)
    rows, _ = lineage.records_injecting(fx.rev3)
    # Variant one: the flag. Returns the four that cited it.
    by_flag = [r for r in rows if r["cited"]]
    assert len(by_flag) == 4
    with pytest.raises(AssertionError):
        assert_recall_complete(by_flag, fx.expected)
    # Variant two: a weight cut above the fixture median. It never mentions
    # the flag, and it drops rows all the same — the variant the check does
    # not name.
    median = statistics.median(r["weight_at_injection"] for r in rows)
    by_weight = [r for r in rows if r["weight_at_injection"] > median]
    assert 0 < len(by_weight) < len(rows)
    with pytest.raises(AssertionError):
        assert_recall_complete(by_weight, fx.expected)
    lineage.close()


# ---------------------------------------------------------------------------
# T2 / T2c — the route has no write path
# ---------------------------------------------------------------------------

_SQL_WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.I)


def backend_functions() -> dict[str, list[tuple[Path, ast.AST]]]:
    """Every function and method in the tracked backend, by name."""
    out: dict[str, list] = {}
    for f in tracked_backend():
        tree = ast.parse(Path(f).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(node.name, []).append((Path(f), node))
    return out


def writer_names() -> set[str]:
    """DERIVED: the name of every backend function whose body holds a SQL
    write statement or calls executemany/executescript. Not a list."""
    names = set()
    for name, defs in backend_functions().items():
        for _f, node in defs:
            for n in ast.walk(node):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and _SQL_WRITE.match(n.value):
                    names.add(name)
                elif isinstance(n, ast.Call) and getattr(n.func, "attr", None) in ("executemany", "executescript"):
                    names.add(name)
    return names


def _local_containers(node: ast.AST) -> set[str]:
    """Names bound in this function to a list/dict/set literal or
    comprehension: a method called on one of these (`out.append`) is the
    container's, not a backend function's, and resolving it by bare name
    would confuse `list.append` with the archive chain's `append`."""
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                and isinstance(n.value, (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)):
            names.add(n.targets[0].id)
    return names


def called_names(node: ast.AST) -> set[str]:
    out = set()
    locals_ = _local_containers(node)
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) \
                    and n.func.value.id in locals_:
                continue
            nm = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if nm:
                out.add(nm)
    return out


def _endpoint_ast(app, path: str) -> ast.AST:
    for route in app.routes:
        if getattr(route, "path", None) == path:
            src = textwrap.dedent(inspect.getsource(route.endpoint))
            return ast.parse(src).body[0]
    raise AssertionError(f"route {path} not found")


def test_recall_route_has_no_write_path(fx, tmp_path):
    """The recall is a read; nothing it calls writes to any store."""
    _svc, client = _client(fx, tmp_path)
    fn = _endpoint_ast(client.app, RECALL_PATH)
    direct = called_names(fn)
    funcs = backend_functions()
    one_deep = set()
    for name in direct:
        for _f, node in funcs.get(name, []):
            one_deep |= called_names(node)
    call_set = direct | one_deep
    writers = writer_names()
    print(f"\nT2 call set ({len(call_set)}): {sorted(call_set)}")
    print(f"T2 derived writer names ({len(writers)}), e.g. {sorted(writers)[:8]} …")
    assert writers >= {"add", "record_turn", "record_outcome"}, "derivation must see the known writers"
    assert not (call_set & writers), f"the recall route reaches a writer: {sorted(call_set & writers)}"
    assert ("GET", RECALL_PATH) not in EXPECTED_WRITERS


def assert_lineage_untouched(kb_path, action) -> None:
    """The behavioural check: the sum of every lineage record's hash is the
    same before and after `action` runs."""
    before = _hashes(kb_path)
    action()
    after = _hashes(kb_path)
    assert before == after, "a lineage record changed across a read"


def test_write_in_recall_path_is_caught(fx):
    """A write reached through a helper the check does not list is still a write."""
    lineage = _lineage(fx)

    def helper_tidy(conn):                      # the helper nobody listed
        conn.execute("UPDATE lineage_records SET scope = 'tidied' WHERE scope = 'default'")
        conn.commit()

    def planted_recall():                       # a copy of the route that calls it
        helper_tidy(lineage.conn)
        return lineage.records_injecting(fx.rev3)

    with pytest.raises(AssertionError):
        assert_lineage_untouched(fx.kb_path, planted_recall)
    lineage.close()


# ---------------------------------------------------------------------------
# T3 — reading backwards changes nothing
# ---------------------------------------------------------------------------

def test_recall_leaves_lineage_hashes_identical(fx):
    """Reading the record backwards does not change a byte of it."""
    lineage = _lineage(fx)
    chain = NexusArchiveChain(fx.archive_path)
    before = _hashes(fx.kb_path)
    for i in range(50):
        lineage.records_injecting([fx.rev3, fx.rev4, fx.other][i % 3], limit=5, offset=i % 4)
    assert _hashes(fx.kb_path) == before
    assert verify_sealed_lineage(lineage, chain)["verdict"] == "intact"
    lineage.close(); chain.close()


# ---------------------------------------------------------------------------
# T4 — classified, declared; the existing guard names a planted route
# ---------------------------------------------------------------------------

def test_recall_route_is_classified_and_declared(fx, tmp_path):
    """A route exposing records is classified in the ratchet in the same commit as it ships."""
    _svc, client = _client(fx, tmp_path)
    paths = {p for _m, p in derive_routes(client.app)}
    assert RECALL_PATH in paths
    assert RECALL_PATH in PER_PERSON_ROWS and "asserted" in PER_PERSON_ROWS[RECALL_PATH]
    assert RECALL_PATH not in DELIBERATELY_OPEN
    client.headers.pop("Authorization", None)
    assert _is_gated_via(client, "GET", RECALL_PATH), "gated like every evidence read"
    # The existing total-classification guard, exercised: a fixture app with
    # an open, unclassified read is named by the same logic.
    planted = FastAPI()

    @planted.get("/fixture/open-read")
    async def open_read():
        return {"rows": []}

    with TestClient(planted) as pc:
        unclassified = [(m, p) for m, p in derive_routes(planted)
                        if m not in WRITE_METHODS and p not in DELIBERATELY_OPEN
                        and not _is_gated_via(pc, m, p)]
    assert unclassified == [("GET", "/fixture/open-read")]


# ---------------------------------------------------------------------------
# T5 — identity is asserted, never verified
# ---------------------------------------------------------------------------

def test_recall_identity_is_labelled_asserted(fx):
    """Identity on a recall row is recorded, not authenticated, and the row says so."""
    lineage = _lineage(fx)
    rows, _ = lineage.records_injecting(fx.rev3)
    assert {r["identity_label"] for r in rows} == {"asserted"}
    # The planted record whose actor fields claim an IdP-verified source is
    # in the list, and its label is still `asserted`.
    planted = lineage.get(fx.lineage_ids[3])
    assert planted.boundary_asserted_by == {"id": "bob", "role": "person", "source": "idp"}
    row = next(r for r in rows if r["lineage_id"] == planted.lineage_id)
    assert row["identity_label"] == "asserted" and row["identity_label"] != "verified"
    lineage.close()


# ---------------------------------------------------------------------------
# T6 / T6b / T6c — supersede on the existing act
# ---------------------------------------------------------------------------

def _install_envelope(fx, max_delta=0.1):
    rc = RecalibrationStore(fx.kb_path)
    rc.record_envelope(Envelope(version="v1", max_delta=max_delta, movable_classes=(classify(SOURCE),)),
                       actor="board", reason="AR fixture: permit a bounded move")
    rc.close()


def _supersede(client, belief_id, *, magnitude=0.05, notes="superseded_by: rev4"):
    return client.post("/outcome/confirm", json={
        "event_type": "supersede", "decision": "superseded", "direction": "contradicting",
        "references": {"belief_ids": [belief_id]}, "magnitude": magnitude, "notes": notes,
        "actor_id": "alice", "actor_role": "person", "confirmed": True}, headers=H)


def test_supersede_returns_count_and_route(fx, tmp_path):
    """Superseding a claim reports how many sealed answers rested on it, and changes nothing else."""
    _install_envelope(fx)
    svc, client = _client(fx, tmp_path)
    before = _hashes(fx.kb_path)
    r = _supersede(client, fx.rev3)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recall_count"] == 7 and body["recall_route"] == f"/belief/{fx.rev3}/recall"
    row = svc.recalibration.get_outcome(body["event_id"])
    assert row.recall_count == 7 and row.recall_route == body["recall_route"]
    assert svc.recalibration.verify_governance_records()["verdict"] == "intact", "hashed when present"
    assert _hashes(fx.kb_path) == before, "the record is untouched"
    (applied,) = body["applied"]
    bound = body["envelope"]["max_delta"]
    moved = abs(applied["prominence_after"] - applied["prominence_before"])
    print(f"\nT6 recall_count={body['recall_count']} route={body['recall_route']} "
          f"delta={applied['delta']} moved={moved:.4f} within max_delta={bound}")
    assert abs(applied["delta"]) <= bound and moved <= bound + 1e-9
    # The route the act names returns the same seven.
    got = client.get(body["recall_route"], headers=H).json()
    assert got["total"] == 7 and len(got["records"]) == 7


def test_recall_works_with_adjustment_switch_off(fx, tmp_path, monkeypatch):
    """The read does not depend on the act being enabled."""
    monkeypatch.setenv("NEXUS_RECALIBRATION", "off")
    _svc, client = _client(fx, tmp_path)
    got = client.get(f"/belief/{fx.rev3}/recall", headers=H).json()
    assert got["total"] == 7
    r = _supersede(client, fx.rev3).json()
    assert r["recall_count"] == 7 and r["refused"] and r["refused"][0]["rule"] == "recalibration_disabled"


def test_supersede_on_unused_claim_returns_zero_not_absent(fx, tmp_path):
    """A claim no answer rested on reports a measured zero, not a missing field."""
    store = TripleStore(fx.kb_path)
    unused = store.add(subject="sample isolation", relation="requires", object="a step nobody asked about",
                       weight=0.5, tier=1, granularity=Granularity.TOPIC, source=SOURCE)
    store.close()
    _svc, client = _client(fx, tmp_path)
    body = _supersede(client, unused).json()
    assert "recall_count" in body and body["recall_count"] == 0
    assert body["recall_route"] == f"/belief/{unused}/recall"
    plain = _supersede(client, fx.rev4, notes="reviewed, no supersede").json()
    assert "recall_count" not in plain, "only a supersede carries the fields"


# ---------------------------------------------------------------------------
# T7 — the coverage block is derived, never stored
# ---------------------------------------------------------------------------

def test_stats_recall_block(fx, tmp_path):
    """The coverage block is a count derived from the record, never a stored number."""
    _install_envelope(fx)
    _svc, client = _client(fx, tmp_path)
    before = client.get("/cortex/stats").json()["recall"]
    assert (before["superseded_claims"], before["records_resting"], before["since"]) == (0, 0, None)
    ev = _supersede(client, fx.rev3).json()
    after = client.get("/cortex/stats").json()["recall"]
    assert (after["superseded_claims"], after["records_resting"]) == (1, 7)
    assert after["since"] is not None and after["supersede_events"] == 1
    again = client.get("/cortex/stats").json()["recall"]
    assert again == after, "recomputed from the record: two reads agree with no cache to delete"
    assert after["index"]["records"] == 12 and after["read"] == RECALL_PATH
    assert ev["event_id"]


# ---------------------------------------------------------------------------
# T8 — the contract, pinned and reconciled three ways
# ---------------------------------------------------------------------------

def test_recall_response_shape_pinned(fx, tmp_path):
    """The recall response is a contract, and a change to it is a version bump."""
    _svc, client = _client(fx, tmp_path)
    got = client.get(f"/belief/{fx.rev3}/recall?limit=3&offset=2", headers=H).json()
    assert set(got) == RECALL_RESPONSE_KEYS_V2 and got["contract_version"] == 2
    assert all(set(r) == RECALL_ROW_KEYS_V2 for r in got["records"])
    assert {r["identity_label"] for r in got["records"]} <= RECALL_IDENTITY_LABELS_V2
    assert set(got["per_person"]["labels"]) == RECALL_IDENTITY_LABELS_V2
    assert got["per_person"]["declared"] is True and got["per_person"]["field"] == "accountable"
    assert got["total"] == 7 and len(got["records"]) == 3 and got["offset"] == 2
    assert got["read_from"] == "injected" and got["resolved"] is True
    for doc in ("docs/CONTRACTS.md", "docs/INTEGRATION_REFERENCE.md", "docs/INTEGRATION_REFERENCE.html"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert RECALL_PATH in text, f"{doc} does not name the recall route"
        assert "per-person" in text, f"{doc} does not carry the per-person declaration"


# ---------------------------------------------------------------------------
# T12 — the index agrees with the record
# ---------------------------------------------------------------------------

def test_index_consistent_with_record(fx):
    """Only if S1.5 decided an index: the index and the record agree, and a divergence is named."""
    lineage = _lineage(fx)
    assert lineage.verify_recall_index()["consistent"] is True
    # Plant: an index row for a record that did not inject that claim.
    lineage.conn.execute("INSERT INTO lineage_injections (belief_id, lineage_id) VALUES (?, ?)",
                         (fx.rev4, fx.lineage_ids[0]))
    # And the reverse: a record whose index row is missing.
    lineage.conn.execute("DELETE FROM lineage_injections WHERE belief_id = ? AND lineage_id = ?",
                         (fx.rev3, fx.lineage_ids[1]))
    lineage.conn.commit()
    v = lineage.verify_recall_index()
    assert v["consistent"] is False
    assert v["index_without_record"] == [{"belief_id": fx.rev4, "lineage_id": fx.lineage_ids[0]}]
    assert v["record_without_index"] == [{"belief_id": fx.rev3, "lineage_id": fx.lineage_ids[1]}]
    lineage.close()


def test_the_index_is_backfilled_for_records_that_predate_it(fx):
    """A store written before the index gains its rows at open, once, and the
    coverage block says how many."""
    from backend.store import connect as store_connect
    from tests.conftest import store_target_for
    c = store_connect(store_target_for(fx.kb_path))
    c.execute("DELETE FROM lineage_injections")
    c.commit(); c.close()
    lineage = _lineage(fx)
    assert lineage.recall_index_backfilled == 14
    assert lineage.recall_index_coverage()["backfilled_at_open"] == 14
    assert lineage.records_injecting(fx.rev3)[1] == 7
    lineage.close()
    again = _lineage(fx)
    assert again.recall_index_backfilled == 0, "idempotent"
    again.close()
