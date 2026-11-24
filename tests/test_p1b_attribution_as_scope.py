"""v3.1 Phase 1b — attribution the way scope is done.

Supersedes the Phase 1 packet's B.4 and B.5. Scope is an opaque,
caller-supplied label on every request: never inferred, recorded literally,
`"default"` when absent, sealed, never confirmed. A work unit is scope with
a row and a junction, and 1b treats it the same way: one channel (the
body), nothing inherited between turns, no lifecycle on the server, no
route, no event, no governed act. Close is a boundary assertion on the
request (`decision_boundary: close`) that writes the row once.

TESTS FROM THE DRAFTED 1b THAT WERE NEVER BUILT (named, per the sentinel):
  - `POST /work_unit/open` refused a second open (`unit_already_open`) and
    ledgered it — no route exists, no refusal exists;
  - `POST /work_unit/close` declared `accountability.missing` — no route;
    the flag is declared on `done` and the record instead (acceptance 3);
  - `work_unit_opened` / `work_unit_closed` declared, bridged and bound —
    not declared, not emitted, not in KNOWN_UNHANDLED; the contract test's
    three-way reconciliation is unchanged;
  - the auth ratchet's two additions (`EXPECTED_WRITERS` 15 → 17) — none;
  - the restart re-read of the session's open unit — the test that pinned
    it in 1a is removed, and `test_nothing_inherits_between_turns` is its
    replacement: the second turn is attributed to the pseudo-unit, which is
    what a restart now does too, because nothing is remembered.

GUARDS, each with a companion that plants what the guard does not name:
  - one channel: every header read in the `/chat` route is DERIVED (every
    `.headers` attribute, every `Header(...)` dependency, every
    `Request`-typed parameter, over the route function found by its
    decorator) and must be empty. Companion plants `request.headers.get(
    "Work-Unit")` — a name the old `X-Nexus-*` pattern would not have
    caught; a second companion plants `Header(alias="X-Nexus-Tenant")`, the
    regression witness for the pattern the packet replaced;
  - the row is written at most twice: create, close, close again, mention
    again — only creation and the first close change a column. Companion:
    a planted second `closed_at` write is caught by the same assertion;
  - `opened_by` is never a literal: a bare string cannot be written and a
    request with no actor fields records the vocabulary's defaults.
    Companion plants `system` and an incomplete dict — neither is the
    literal the guard's source names;
  - nothing inherits: WU-1 on turn 1, nothing on turn 2 → pseudo-unit;
  - hash stability: pre-existing records hash identically with the new
    fields null (1a's test, extended); a stated boundary differs.
"""
from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.kb.recalibration import DEFAULT_ACTOR
from backend.kb.work_units import (BOUNDARY_CLOSE, FLAG_ACCOUNTABILITY_MISSING,
                                   STATUS_ATTRIBUTED, STATUS_UNATTRIBUTED, WorkUnitError,
                                   WorkUnitStore, actor_fields, pseudo_unit_id)
from backend.loop.state import NexusLoopState
from backend.memory.lineage import LineageStore
from backend.server import MAX_SCOPE_CHARS, create_app, create_services
from tests._tracked import tracked_backend
from tests.test_p1_work_unit_attribution import _rig, _turn

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIELDS = {"id": DEFAULT_ACTOR.id, "role": DEFAULT_ACTOR.role, "source": DEFAULT_ACTOR.source}
TOKEN = "p1b-token"
H = {"Authorization": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------------------
# acceptance 1 — first mention with the request's actor fields; nothing → pseudo
# (the record and junction halves are 1a's tests, rerun unchanged)
# ---------------------------------------------------------------------------

def test_first_mention_creates_the_row_with_the_requests_actor_fields(tmp_path, monkeypatch):
    store, lineage, units = _rig(tmp_path, monkeypatch)
    _state, done = _turn(store, lineage, units, "what helps recovery?", work_unit="WU-1",
                         accountable="alice", actor={"id": "alice", "role": "person", "source": "idp"})
    assert done["unit_first_seen"] is True and done["work_unit_id"] == "WU-1"
    unit = units.get("WU-1")
    assert unit["opened_by"] == {"id": "alice", "role": "person", "source": "idp"}
    assert unit["closed_at"] is None and unit["closed_by"] is None
    rec = lineage.get(done["lineage_id"])
    assert (rec.work_unit_id, rec.accountable_identity, rec.boundary_asserted,
            rec.boundary_asserted_by, rec.attribution_flags) == ("WU-1", "alice", None, None, ())
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# acceptance 2 — close as a boundary: once, then declared
# ---------------------------------------------------------------------------

def _row(units, unit_id):
    return dict(units.conn.execute("SELECT * FROM work_unit WHERE work_unit_id = ?", (unit_id,)).fetchone())


def changed_columns(before: dict, after: dict) -> set[str]:
    return {k for k in before if before[k] != after.get(k)}


def assert_row_untouched(before: dict, after: dict, *, step: str) -> None:
    """The row-written-at-most-twice assertion, shared with its companion."""
    changed = changed_columns(before, after)
    assert not changed, f"{step}: the unit row changed {sorted(changed)}; only creation and the first close may write it"


def test_close_writes_once_then_declares_and_a_closed_unit_is_still_attributed(tmp_path, monkeypatch):
    store, lineage, units = _rig(tmp_path, monkeypatch)
    state = NexusLoopState()
    state, d1 = _turn(store, lineage, units, "q1", work_unit="WU-1", accountable="alice",
                      actor={"id": "alice"}, state=state)
    created = _row(units, "WU-1")
    assert d1["unit_first_seen"] and not d1["unit_closed"]

    state, d2 = _turn(store, lineage, units, "q2", work_unit="WU-1", accountable="alice",
                      actor={"id": "alice"}, decision_boundary=BOUNDARY_CLOSE, state=state)
    closed = _row(units, "WU-1")
    assert d2["unit_closed"] is True and "unit_already_closed" not in d2 and "flags" not in d2
    assert changed_columns(created, closed) == {"closed_at", "closed_by"}
    assert closed["closed_at"] is not None
    assert json.loads(closed["closed_by"]) == {"id": "alice", "role": DEFAULT_ACTOR.role, "source": "local"}
    r2 = lineage.get(d2["lineage_id"])
    assert r2.boundary_asserted == "close" and r2.boundary_asserted_by == json.loads(closed["closed_by"])
    assert lineage.verify_record(r2.lineage_id)["record_intact"]

    state, d3 = _turn(store, lineage, units, "q3", work_unit="WU-1", accountable="alice",
                      decision_boundary=BOUNDARY_CLOSE, state=state)
    assert d3["unit_already_closed"] is True and d3["unit_closed"] is False
    assert d3["unit_closed_at_time_of_turn"] is True
    assert_row_untouched(closed, _row(units, "WU-1"), step="second close")
    assert lineage.get(d3["lineage_id"]).boundary_asserted == "close", "recorded as asserted"

    state, d4 = _turn(store, lineage, units, "q4", work_unit="WU-1", state=state)
    assert d4["work_unit_id"] == "WU-1" and d4["attribution_status"] == STATUS_ATTRIBUTED
    assert d4["unit_closed_at_time_of_turn"] is True and d4["unit_closed"] is False
    assert_row_untouched(closed, _row(units, "WU-1"), step="mention after close")
    print("\nacceptance 2 — row after create:", {k: created[k] for k in ("closed_at", "closed_by", "opened_by")},
          "\n               row after close: ", {k: closed[k] for k in ("closed_at", "closed_by")},
          "\n               done keys per turn:", [sorted(k for k in d if k.startswith("unit_") or k == "flags")
                                                   for d in (d1, d2, d3, d4)])
    store.close(); lineage.close()


def test_companion_a_planted_second_closed_at_write_is_caught_by_the_same_assertion(tmp_path, monkeypatch):
    store, lineage, units = _rig(tmp_path, monkeypatch)
    _turn(store, lineage, units, "q1", work_unit="WU-1", accountable="alice")
    _turn(store, lineage, units, "q2", work_unit="WU-1", accountable="alice", decision_boundary=BOUNDARY_CLOSE)
    closed = _row(units, "WU-1")
    # The write the packet forbids, made directly: a re-close with a new time.
    units.conn.execute("UPDATE work_unit SET closed_at = closed_at + 60 WHERE work_unit_id = 'WU-1'")
    units.conn.commit()
    with pytest.raises(AssertionError) as e:
        assert_row_untouched(closed, _row(units, "WU-1"), step="planted re-close")
    assert "closed_at" in str(e.value) and "planted re-close" in str(e.value)
    store.close(); lineage.close()


def test_close_without_a_unit_named_is_recorded_and_declared_not_applied(tmp_path, monkeypatch):
    store, lineage, units = _rig(tmp_path, monkeypatch)
    _state, done = _turn(store, lineage, units, "q", decision_boundary=BOUNDARY_CLOSE)
    assert done["work_unit_id"] == pseudo_unit_id("default") and done["unit_closed"] is False
    assert done["boundary_not_applied"] == "no_unit_named"
    assert units.get(pseudo_unit_id("default"))["closed_at"] is None, "absence cannot be closed"
    assert lineage.get(done["lineage_id"]).boundary_asserted == "close"
    store.close(); lineage.close()


def test_an_unknown_boundary_value_is_recorded_and_has_no_effect(tmp_path, monkeypatch):
    store, lineage, units = _rig(tmp_path, monkeypatch)
    _state, done = _turn(store, lineage, units, "q", work_unit="WU-1", decision_boundary="escalate")
    assert done["unit_closed"] is False and units.get("WU-1")["closed_at"] is None
    rec = lineage.get(done["lineage_id"])
    assert rec.boundary_asserted == "escalate" and rec.boundary_asserted_by == DEFAULT_FIELDS
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# acceptance 3 — close with no accountable: proceeds, flagged
# ---------------------------------------------------------------------------

def test_close_without_accountable_proceeds_and_flags_it_on_done_and_the_record(tmp_path, monkeypatch):
    store, lineage, units = _rig(tmp_path, monkeypatch)
    _turn(store, lineage, units, "q1", work_unit="WU-2")
    _state, done = _turn(store, lineage, units, "q2", work_unit="WU-2", decision_boundary=BOUNDARY_CLOSE)
    assert done["unit_closed"] is True and done["flags"] == [FLAG_ACCOUNTABILITY_MISSING]
    assert units.get("WU-2")["closed_at"] is not None
    rec = lineage.get(done["lineage_id"])
    assert rec.attribution_flags == (FLAG_ACCOUNTABILITY_MISSING,) and rec.accountable_identity is None
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# acceptance 4 — nothing inherits between turns (the scope model)
# ---------------------------------------------------------------------------

def test_nothing_inherits_between_turns(tmp_path, monkeypatch):
    """Turn 1 names WU-1 with alice; turn 2 names nothing and is attributed
    to the session pseudo-unit, `unattributed`, with no accountable — the
    test that proves the scope model and forbids the lifecycle returning."""
    store, lineage, units = _rig(tmp_path, monkeypatch)
    state = NexusLoopState()
    state, d1 = _turn(store, lineage, units, "q1", work_unit="WU-1", accountable="alice", state=state)
    assert d1["work_unit_id"] == "WU-1"
    state, d2 = _turn(store, lineage, units, "q2", state=state)
    assert d2["work_unit_id"] == pseudo_unit_id("default")
    assert d2["attribution_status"] == STATUS_UNATTRIBUTED
    r2 = lineage.get(d2["lineage_id"])
    assert r2.work_unit_id == pseudo_unit_id("default") and r2.accountable_identity is None
    assert units.get("WU-1")["closed_at"] is None, "WU-1 is still open; it was simply not named"
    assert not hasattr(state, "work_unit_id") and not hasattr(state, "accountable_identity")
    store.close(); lineage.close()


# ---------------------------------------------------------------------------
# B.7 — `opened_by` is never a literal
# ---------------------------------------------------------------------------

def test_opened_by_cannot_be_a_bare_string_and_absent_actor_fields_record_the_vocabularys_defaults(tmp_path, monkeypatch):
    store, _lineage, units = _rig(tmp_path, monkeypatch)
    with pytest.raises(WorkUnitError) as e:
        units.open("WU-lit", work_unit_type="claim", opened_by="request")
    assert e.value.rule == "actor_not_fields" and units.get("WU-lit") is None
    unit, declared = units.resolve_for_turn(session_id="default", requested_id="WU-3")
    assert declared["unit_first_seen"] and unit["opened_by"] == DEFAULT_FIELDS
    assert actor_fields({"id": " ", "role": "", "source": None}) == DEFAULT_FIELDS
    store.close()


@pytest.mark.parametrize("planted", ["system", {"id": "alice"}, {"id": "alice", "role": "person", "source": ""}])
def test_companion_other_literals_and_partial_actors_are_refused_too(tmp_path, monkeypatch, planted):
    store, _lineage, units = _rig(tmp_path, monkeypatch)
    with pytest.raises(WorkUnitError) as e:
        units.open("WU-x", work_unit_type="claim", opened_by=planted)
    assert e.value.rule == "actor_not_fields"
    assert units.get("WU-x") is None, "the refusal is a consequence: no row"
    store.close()


# ---------------------------------------------------------------------------
# B.7 — hash stability, extended to the new fields (acceptance 6)
# ---------------------------------------------------------------------------

def test_pre_existing_records_hash_identically_with_the_1b_fields_null():
    base = {"lineage_id": "x", "ts": 1.0, "query_hash": "q", "injected": [], "contradictions": [],
            "model_id": "m", "prompt_prefix_hash": "p", "response_hash": "r"}
    old_shape = LineageStore.compute_record_hash(dict(base))
    assert LineageStore.compute_record_hash({**base, "work_unit_id": None, "accountable_identity": None,
                                             "boundary_asserted": None, "boundary_asserted_by": None}) == old_shape
    assert LineageStore.compute_record_hash({**base, "boundary_asserted": "close"}) != old_shape
    assert LineageStore.compute_record_hash({**base, "boundary_asserted_by": DEFAULT_FIELDS}) != old_shape
    # The 1a-shaped record (unit + identity, no boundary) is unchanged too.
    one_a = {**base, "work_unit_id": "WU-1", "accountable_identity": "alice"}
    assert LineageStore.compute_record_hash(one_a) == LineageStore.compute_record_hash(
        {**one_a, "boundary_asserted": None, "boundary_asserted_by": None})


@pytest.mark.sqlite_only("builds a Phase-1a store by hand with sqlite3 and migrates it")
def test_a_1a_store_migrates_and_its_records_verify(tmp_path):
    """A store written by the 1a schema (16 columns) gains the three 1b
    columns by ALTER and every record still recomputes."""
    db = tmp_path / "one_a.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE lineage_records (
        lineage_id TEXT PRIMARY KEY, ts REAL NOT NULL, query_hash TEXT NOT NULL,
        injected_json TEXT NOT NULL, contradictions_json TEXT NOT NULL, model_id TEXT NOT NULL,
        prompt_prefix_hash TEXT NOT NULL, response_hash TEXT NOT NULL, record_hash TEXT NOT NULL,
        sealed_block INTEGER, scope TEXT NOT NULL DEFAULT 'default', citation_test TEXT,
        context_json TEXT, request_hash TEXT, work_unit_id TEXT, accountable_identity TEXT)""")
    base = {"ts": 1.0, "query_hash": "q", "injected": [], "contradictions": [], "model_id": "m",
            "prompt_prefix_hash": "p", "response_hash": "r"}
    for lid, unit, who in (("a", None, None), ("b", "WU-1", "alice")):
        h = LineageStore.compute_record_hash({**base, "lineage_id": lid, "work_unit_id": unit,
                                              "accountable_identity": who})
        con.execute("INSERT INTO lineage_records VALUES (?, 1.0, 'q', '[]', '[]', 'm', 'p', 'r', ?, NULL, "
                    "'default', NULL, NULL, NULL, ?, ?)", (lid, h, unit, who))
    con.commit(); con.close()
    st = LineageStore(db)
    cols = {r[1] for r in st.conn.execute("PRAGMA table_info(lineage_records)")}
    assert {"boundary_asserted", "boundary_asserted_by", "attribution_flags"} <= cols
    for lid in ("a", "b"):
        v = st.verify_record(lid)
        print(f"1b migration — {lid}: record_intact={v['record_intact']}")
        assert v["record_intact"] is True
        assert st.get(lid).boundary_asserted is None and st.get(lid).attribution_flags == ()
    st.close()


# ---------------------------------------------------------------------------
# B.7 — one channel: the chat route reads no header (derived, not pattern-matched)
# ---------------------------------------------------------------------------

def header_read_sites(source: str, path: str = "<src>", route: str = "/chat") -> tuple[bool, list[str]]:
    """(route_found, [file:line …]) for every header read inside the route
    function registered on `route` — found by its decorator, not its name.

    A header read is any of: an attribute access `.headers` on anything; a
    call to `Header(...)` anywhere in the function, including as a
    parameter default; a parameter annotated `Request` (the object the
    headers hang off — reading it at all is the shape the packet removed).
    The population is every node in the route body and signature, so the
    header's NAME never enters the guard."""
    tree = ast.parse(source)
    found, sites = False, []

    def is_route(fn) -> bool:
        for dec in fn.decorator_list:
            if isinstance(dec, ast.Call) and dec.args and isinstance(dec.args[0], ast.Constant) \
                    and dec.args[0].value == route:
                return True
        return False

    def name_of(node) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not is_route(fn):
            continue
        found = True
        args = fn.args
        for a in args.posonlyargs + args.args + args.kwonlyargs:
            if a.annotation is not None and name_of(a.annotation) == "Request":
                sites.append(f"{path}:{a.lineno}")
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and node.attr == "headers":
                sites.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.Call) and name_of(node.func) == "Header":
                sites.append(f"{path}:{node.lineno}")
    return found, sorted(set(sites), key=lambda s: int(s.rsplit(":", 1)[1]))


def test_the_chat_route_reads_no_header():
    server = next(Path(f) for f in tracked_backend() if Path(f).name == "server.py")
    found, sites = header_read_sites(server.read_text(encoding="utf-8"), server.relative_to(ROOT).as_posix())
    assert found, "the /chat route must be found by its decorator — an empty population proves nothing"
    assert sites == [], f"attribution has one channel, the body; header reads in /chat: {sites}"


def test_companion_a_header_read_the_old_pattern_would_not_have_caught_is_named(tmp_path):
    planted = (
        "@app.post('/chat')\n"
        "async def chat(req: ChatRequest, request: Request):\n"
        "    unit = request.headers.get('Work-Unit')\n"
        "    who = request.headers['Accountable']\n"
        "    return unit, who\n")
    found, sites = header_read_sites(planted, "planted.py")
    assert found and sites == ["planted.py:2", "planted.py:3", "planted.py:4"], sites
    # A route that is not /chat is outside the population, by the decorator.
    other = planted.replace("'/chat'", "'/other'")
    assert header_read_sites(other, "other.py") == (False, [])


def test_companion_the_x_nexus_shape_is_still_caught_as_a_regression_witness():
    planted = (
        "@app.post('/chat')\n"
        "async def chat(req: ChatRequest, tenant: str = Header(default='', alias='X-Nexus-Tenant')):\n"
        "    return tenant\n")
    found, sites = header_read_sites(planted, "planted.py")
    assert found and sites == ["planted.py:2"], sites


# ---------------------------------------------------------------------------
# the route — one channel end to end, validation in scope's position
# ---------------------------------------------------------------------------

class _Answering:
    async def stream_chat(self, model, messages, num_ctx=8192):
        yield "Interval training improves VO2 max."

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return False


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    svc = create_services(kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl", archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap", provider=_Answering())
    svc.store.add(subject="interval training", relation="improves", object="vo2 max", weight=0.8,
                  tier=1, granularity=Granularity.TOPIC, source="human_assertion")
    return svc, TestClient(create_app(svc))


def _done(resp) -> dict:
    frame = [f for f in resp.text.split("\n\n") if f.startswith("event: done")][-1]
    return json.loads(frame.split("data: ", 1)[1])


def test_the_route_takes_the_body_and_ignores_a_header(rig):
    svc, client = rig
    r = client.post("/chat", json={"message": "what does interval training improve?", "work_unit": " WU-r ",
                                   "accountable": "alice", "actor_id": "alice", "actor_role": "person"},
                    headers={**H, "X-Nexus-Work-Unit": "WU-header", "X-Nexus-Accountable": "mallory"})
    assert r.status_code == 200
    done = _done(r)
    assert done["work_unit_id"] == "WU-r" and done["unit_first_seen"] is True, "stripped, body only"
    assert svc.work_units.get("WU-header") is None, "the header named nothing"
    rec = svc.lineage.get(done["lineage_id"])
    assert rec.accountable_identity == "alice"
    assert svc.work_units.get("WU-r")["opened_by"] == {"id": "alice", "role": "person", "source": "local"}
    # A second request with only the header: the pseudo-unit, not WU-header.
    r2 = client.post("/chat", json={"message": "again?"}, headers={**H, "X-Nexus-Work-Unit": "WU-header"})
    assert _done(r2)["work_unit_id"] == pseudo_unit_id("default")


def test_the_route_closes_on_the_boundary_field_and_declares_the_second(rig):
    svc, client = rig
    client.post("/chat", json={"message": "q1", "work_unit": "WU-c", "accountable": "alice"}, headers=H)
    d2 = _done(client.post("/chat", json={"message": "q2", "work_unit": "WU-c", "accountable": "alice",
                                          "decision_boundary": "close", "actor_id": "alice"}, headers=H))
    assert d2["unit_closed"] is True
    assert svc.work_units.get("WU-c")["closed_by"] == {"id": "alice", "role": DEFAULT_ACTOR.role, "source": "local"}
    d3 = _done(client.post("/chat", json={"message": "q3", "work_unit": "WU-c", "decision_boundary": "close"},
                           headers=H))
    assert d3["unit_already_closed"] is True and d3["unit_closed_at_time_of_turn"] is True
    assert "flags" not in d3, "no close happened on this request, so nothing about its identity is flagged"


@pytest.mark.parametrize("field", ["work_unit", "work_unit_type", "accountable", "decision_boundary"])
def test_the_attribution_fields_copy_scopes_cap_and_error_shape(rig, field):
    _svc, client = rig
    r = client.post("/chat", json={"message": "q", field: "x" * (MAX_SCOPE_CHARS + 1)}, headers=H)
    assert r.status_code == 422 and r.json() == {"error": f"{field}_too_long", "max_chars": MAX_SCOPE_CHARS,
                                                 "got": MAX_SCOPE_CHARS + 1}
    ok = client.post("/chat", json={"message": "q", field: "x" * MAX_SCOPE_CHARS}, headers=H)
    assert ok.status_code == 200
    ws = client.post("/chat", json={"message": "q", "work_unit": "   "}, headers=H)
    assert _done(ws)["work_unit_id"] == pseudo_unit_id("default"), "whitespace only is unsupplied"


def test_the_actor_fields_are_validated_against_the_closed_vocabularies(rig):
    _svc, client = rig
    r = client.post("/chat", json={"message": "q", "actor_role": "robot"}, headers=H)
    assert r.status_code == 422 and r.json()["error"] == "unknown_actor_role"
    r = client.post("/chat", json={"message": "q", "actor_source": "ldap"}, headers=H)
    assert r.status_code == 422 and r.json()["error"] == "unknown_actor_source"


def test_the_assertion_and_seed_routes_take_the_unit_on_the_request(rig):
    svc, client = rig
    r = client.post("/kb/assert/confirm", json={"sentence": "The dose was 600mg.", "confirmed": True,
                                                "work_unit": "WU-a"}, headers=H)
    assert r.status_code == 200 and r.json()["shaped"] is True, r.text
    att = r.json()["attribution"]
    assert att["work_unit_id"] == "WU-a" and att["unit_first_seen"] is True and att["admitted"] >= 1
    assert svc.work_units.get("WU-a")["opened_by"] == DEFAULT_FIELDS, "the route carries no actor fields"
    none = client.post("/kb/assert/confirm", json={"sentence": "The QTc change was 11.4ms.", "confirmed": True},
                       headers=H).json()["attribution"]
    assert none == {"work_unit_id": None, "admitted": 0, "reason": "no unit named"}
    seed = client.post("/kb/seed/offline?confirmed=true&work_unit=WU-s", headers=H).json()["attribution"]
    assert seed["work_unit_id"] == "WU-s" and seed["unit_first_seen"] is True
    long = client.post("/kb/seed/offline?confirmed=true&work_unit=" + "x" * (MAX_SCOPE_CHARS + 1), headers=H)
    assert long.status_code == 422 and long.json()["error"] == "work_unit_too_long"


# ---------------------------------------------------------------------------
# E.6 — the pseudo-unit against the scope precedent (measured, not changed)
# ---------------------------------------------------------------------------

def test_e6_one_pseudo_unit_row_per_session_id(tmp_path, monkeypatch):
    store, _lineage, units = _rig(tmp_path, monkeypatch)
    n = 5
    for i in range(n):
        units.resolve_for_turn(session_id=f"s{i}")
    rows = units.conn.execute("SELECT COUNT(*) AS n FROM work_unit WHERE attribution_status = ?",
                              (STATUS_UNATTRIBUTED,)).fetchone()["n"]
    print(f"\nE.6 — sessions={n} pseudo-unit rows={rows} (scope's absence is one literal; this is one row per session)")
    assert rows == n
    store.close()
