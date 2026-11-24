"""Packet AG — what the budget was spent on.

WHY A REPORT, AND WHY ONLY A REPORT
    The contamination investigation established that every defence here is
    per-event: nothing can be written downward, evidence class caps reach,
    contradiction is retained — so the remaining route is slow accumulation,
    where every individual event passes every check and the attack lives in
    the aggregate. Nothing looked at aggregates. Of the three candidate
    measures, budget consumption is the one whose reference is a POLICY
    SOMEONE INSTALLED rather than a normal someone inferred — a new user, a
    bulk seed, a growing domain perform no recalibrations and move it not at
    all — so it is the one that escapes the false-positive problem.

WHAT IT IS NOT (I-7)
    A report about GOVERNED MOVEMENT. Not a contamination detector: it
    watches the layer where an attacker must use the governed door, and the
    belief layer's door is ungoverned by identity entirely. The payload
    carries that limit on its surface, and these tests fail if it stops.

THE SENTINELS, per the packet:
    - the report reads and writes nothing (md5 across a call; AST proves the
      report function reaches no writer);
    - the four self-declarations ride the PAYLOAD, not documentation;
    - consumption is anchored to the envelope version each event itself
      cites, and a belief whose movement spans versions is reported as
      spanning rather than summed per-version;
    - a person's movement and a source system's are never summed into one
      row;
    - `_insert_recalibration` has exactly today's callers, pinned in both
      directions — it performs no envelope checks, so a new caller is an
      ungoverned write path until someone decides otherwise;
    - token-gated, with the gated-GET pin updated;
    - non-vacuity: the fixture holds applied AND refused events, two actor
      roles, and movement under two envelope versions.
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tracked import tracked_backend                              # noqa: E402

from backend.kb.granularity import Granularity
from backend.kb.recalibration import (
    Actor, Envelope, EnvelopeViolation, RecalibrationStore, apply_outcome,
)
from backend.kb.tiered_store import TripleStore

ROOT = Path(__file__).resolve().parent.parent

ALICE = Actor(id="alice", role="person", source="local")
LIMS = Actor(id="lims", role="source_system", source="local")


@pytest.fixture
def rig(tmp_path):
    """Two beliefs, two envelope versions, two actor roles, one refusal.

    Belief A moves under BOTH versions (the spanning case); belief B under
    v1 only. Alice is a person, lims a source system. The refusal is a
    max_delta violation under v1. Everything the sentinels below assert on
    is present, which the non-vacuity test pins so the rest cannot pass on
    an empty aggregate.
    """
    store = TripleStore(tmp_path / "kb.db")
    recal = RecalibrationStore(tmp_path / "kb.db")
    a = store.add(subject="qtc change at 600mg", relation="was",
                  object="11.4ms", weight=0.8, tier=1,
                  granularity=Granularity.TOPIC, source="human_assertion")
    b = store.add(subject="interval training", relation="improves",
                  object="vo2 max", weight=0.7, tier=1,
                  granularity=Granularity.TOPIC, source="human_assertion")
    recal.record_envelope(
        Envelope(version="v1", max_delta=0.1, movable_classes=("asserted",),
                 cumulative_max=0.5),
        actor=ALICE, reason="AG fixture v1")
    apply_outcome(store, recal, belief_id=a, delta=0.05, actor=ALICE,
                  outcome_ref="o1")
    apply_outcome(store, recal, belief_id=b, delta=-0.05, actor=ALICE,
                  outcome_ref="o2")
    with pytest.raises(EnvelopeViolation):
        apply_outcome(store, recal, belief_id=a, delta=0.5, actor=ALICE,
                      outcome_ref="o3")
    recal.record_envelope(
        Envelope(version="v2", max_delta=0.1, movable_classes=("asserted",),
                 cumulative_max=0.4),
        actor=LIMS, reason="AG fixture v2")
    apply_outcome(store, recal, belief_id=a, delta=0.05, actor=LIMS,
                  outcome_ref="o4")
    return store, recal, a, b


def test_the_fixture_is_not_vacuous(rig):
    """Applied AND refused, two roles, two versions — or every sentinel
    below passes on a store with nothing to aggregate, which is exactly how
    the null script's sandbox run had to be reported as meaningless."""
    _store, recal, _a, _b = rig
    rep = recal.budget_report()
    sw = rep["store_wide"]
    assert sw["applied"] >= 3 and sw["refused"] >= 1
    assert len(rep["envelope_versions"]) >= 2
    roles = {(e["actor_id"], e["actor_role"]) for e in rep["per_actor"]}
    assert ("alice", "person") in roles and ("lims", "source_system") in roles


# ---------------------------------------------------------------------------
# Reads and writes nothing
# ---------------------------------------------------------------------------

@pytest.mark.sqlite_only("hashes the SQLite file's bytes before and after: the claim is about the file")
def test_the_report_changes_no_byte_of_the_store(rig, tmp_path):
    store, recal, _a, _b = rig
    before = hashlib.md5((tmp_path / "kb.db").read_bytes()).hexdigest()
    recal.budget_report()
    recal.budget_report()
    after = hashlib.md5((tmp_path / "kb.db").read_bytes()).hexdigest()
    assert before == after, "a REPORT moved the store"


def test_no_path_from_the_report_reaches_a_writer():
    """AST over the report function: it may call no writing method and may
    hold no writing SQL. The md5 check above proves behaviour on one store;
    this pins the shape so a helpful cache column cannot arrive later."""
    import inspect
    from backend.kb import recalibration as mod
    src = inspect.getsource(mod.RecalibrationStore.budget_report)
    tree = ast.parse("class _C:\n" + "\n".join(
        "    " + line for line in src.splitlines()) + "\n")
    writers = {"add", "fire", "archive", "archive_ids", "unarchive",
               "record_outcome", "record_envelope", "_insert_recalibration",
               "mark_sealed", "executemany", "executescript", "commit"}
    offenders = [getattr(n.func, "attr", getattr(n.func, "id", ""))
                 for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", getattr(n.func, "id", "")) in writers]
    assert not offenders, f"the report calls a writer: {offenders}"
    # STATEMENTS, not substrings: writing SQL STARTS with its verb, while
    # prose merely contains it — this guard's first draft flagged the
    # method's own docstring because `_insert_recalibration` uppercases to
    # contain INSERT, the third-recurrence defect (guards firing on their
    # own prose) the AST discipline exists to end.
    sql_words = ("INSERT", "UPDATE", "DELETE", "ALTER", "DROP")
    bad = [c.value for c in ast.walk(tree)
           if isinstance(c, ast.Constant) and isinstance(c.value, str)
           and c.value.strip().upper().startswith(sql_words)]
    assert not bad, f"the report holds writing SQL: {bad}"
    # Non-vacuity for the refined predicate: a real writing statement is
    # still caught, and prose naming _insert_recalibration is not.
    assert "update triples set x = 1".strip().upper().startswith(sql_words)
    assert not ("holds _insert_recalibration to its callers"
                .strip().upper().startswith(sql_words))


# ---------------------------------------------------------------------------
# The four declarations ride the payload
# ---------------------------------------------------------------------------

def test_the_four_declarations_are_in_the_payload(rig):
    """A governance report that looks complete is worse than one that names
    what it omits. The limits are payload, not documentation — and they must
    survive an empty store too (I-6), which the next test covers."""
    _store, recal, _a, _b = rig
    d = recal.budget_report()["declarations"]
    assert "per belief only" in d["cap_scope"], d["cap_scope"]
    assert "spreading" in d["cap_scope"]
    assert "recalibration" in d["governed_movement_only"]
    assert "evidence class" in d["attribution_limit"]
    assert "identity" in d["attribution_limit"]
    assert d["coverage"]["records_begin_at"] is not None
    assert "invisible" in d["coverage"]["note"]


def test_the_note_is_the_investigation_sentence(rig):
    _store, recal, _a, _b = rig
    note = recal.budget_report()["note"]
    for phrase in ("attributable and findable afterwards",
                   "evidence class",
                   "notice it accumulating"):
        assert phrase in note, f"the endpoint's note lost: {phrase!r}"


def test_an_empty_store_still_declares_its_limits(tmp_path):
    recal = RecalibrationStore(tmp_path / "kb.db")
    rep = recal.budget_report()
    assert rep["store_wide"]["applied"] == 0
    assert set(rep["declarations"]) == {
        "cap_scope", "governed_movement_only", "attribution_limit", "coverage"}
    assert rep["declarations"]["coverage"]["records_begin_at"] is None
    assert "recorded" in rep["declarations"]["coverage"]["note"].lower()


# ---------------------------------------------------------------------------
# Version anchoring
# ---------------------------------------------------------------------------

def test_consumption_is_anchored_to_the_version_each_event_cites(rig):
    """Not a time join: every recalibration row already records the
    envelope_version that permitted it, and that signed fact is what the
    report groups by."""
    _store, recal, a, b = rig
    rep = recal.budget_report()
    row_a = next(r for r in rep["per_belief"] if r["belief_id"] == a)
    assert row_a["by_version"] == {"v1": pytest.approx(0.05),
                                   "v2": pytest.approx(0.05)}
    row_b = next(r for r in rep["per_belief"] if r["belief_id"] == b)
    assert row_b["by_version"] == {"v1": pytest.approx(0.05)}


def test_a_span_across_versions_is_reported_as_spanning_not_summed(rig):
    """The per-version numbers stay separate; the one summed figure is the
    LIFETIME total, present because it is the exact quantity apply_outcome's
    cumulative_max check compares — the policy's own construction, labeled
    as such, not this report's invention."""
    _store, recal, a, _b = rig
    rep = recal.budget_report()
    row = next(r for r in rep["per_belief"] if r["belief_id"] == a)
    assert row["spans_versions"] is True
    assert len(row["by_version"]) == 2
    assert row["consumed_lifetime"] == pytest.approx(0.10)
    assert row["consumed_lifetime"] == recal.cumulative_movement(a), (
        "the lifetime figure must be the admission check's own quantity")
    # Against the ACTIVE cap, because that is what the next apply_outcome
    # will compare against.
    assert row["active_cumulative_max"] == pytest.approx(0.4)
    assert row["remaining_under_active_cap"] == pytest.approx(0.3)
    # And the store-wide by_version block keeps the versions apart too.
    bv = rep["store_wide"]["by_version"]
    assert bv["v1"]["consumed"] == pytest.approx(0.10)
    assert bv["v2"]["consumed"] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Roles are not summed
# ---------------------------------------------------------------------------

def test_a_person_and_a_source_system_are_separate_rows(rig):
    _store, recal, _a, _b = rig
    per_actor = recal.budget_report()["per_actor"]
    alice = [r for r in per_actor if r["actor_id"] == "alice"]
    lims = [r for r in per_actor if r["actor_id"] == "lims"]
    assert len(alice) == 1 and len(lims) == 1
    assert alice[0]["actor_role"] == "person"
    assert lims[0]["actor_role"] == "source_system"
    assert alice[0]["consumed"] == pytest.approx(0.10)
    assert alice[0]["movements"] == 2
    assert alice[0]["refused"] == 1
    assert lims[0]["consumed"] == pytest.approx(0.05)
    assert alice[0]["first_ts"] <= alice[0]["last_ts"]


def test_refusals_are_counted_with_their_rules(rig):
    _store, recal, _a, _b = rig
    sw = recal.budget_report()["store_wide"]
    assert sw["refusal_reasons"] == {"max_delta": 1}
    assert sw["total_movement"] == pytest.approx(0.15), (
        "refused deltas must not count as movement — nothing moved")


# ---------------------------------------------------------------------------
# The seam: _insert_recalibration's callers, pinned in both directions
# ---------------------------------------------------------------------------

def _insert_call_sites() -> set[tuple[str, str]]:
    """(module, innermost enclosing function) for every call of
    `_insert_recalibration` across tracked backend files."""
    sites: set[tuple[str, str]] = set()

    def visit(node, rel, current):
        for child in ast.iter_child_nodes(node):
            name = current
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if isinstance(child, ast.Call) and (
                    getattr(child.func, "attr", None) == "_insert_recalibration"
                    or getattr(child.func, "id", None) == "_insert_recalibration"):
                sites.add((rel, current))
            visit(child, rel, name)

    for path in tracked_backend():
        rel = path.relative_to(ROOT).as_posix()
        visit(ast.parse(path.read_text(encoding="utf-8")), rel, "<module>")
    return sites


def test_insert_recalibration_has_exactly_todays_callers():
    """`_insert_recalibration` performs no envelope checks — all policy
    lives in apply_outcome, so a new caller is an ungoverned write path that
    would produce applied events that verify clean and were never
    policy-checked. AG builds the first surface that would make automating
    such a writer tempting, so the seam is pinned NOW, in both directions:
    a new caller fails until somebody decides it should exist, and a
    vanished one fails so the pin cannot rot into reassurance."""
    expected = {
        ("backend/kb/recalibration.py", "_refuse"),
        ("backend/kb/recalibration.py", "apply_outcome"),
    }
    assert _insert_call_sites() == expected


def test_the_caller_pin_can_actually_fail():
    """NON-VACUITY: the detector sees the exact shape a new caller takes,
    and the legitimate callers were genuinely found (the pin above is an
    equality, so an empty sweep would already have failed it — this proves
    the DETECTOR, not the population)."""
    tree = ast.parse("def rogue(recal, ev):\n"
                     "    recal._insert_recalibration(ev)\n")
    hits = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "attr", None) == "_insert_recalibration"]
    assert len(hits) == 1


# ---------------------------------------------------------------------------
# The endpoint: token-gated, in the governance family
# ---------------------------------------------------------------------------

TOKEN = "ag-budget-token"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    from fastapi.testclient import TestClient
    from backend.server import create_app, create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    return TestClient(create_app(svc)), tmp_path


def test_the_endpoint_is_token_gated(client):
    c, _tmp = client
    c.headers.pop("Authorization", None)
    assert c.get("/governance/budget").status_code == 401
    r = c.get("/governance/budget",
              headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    body = r.json()
    assert "declarations" in body and "note" in body


@pytest.mark.sqlite_only("hashes the SQLite file's bytes before and after: the claim is about the file")
def test_the_endpoint_call_changes_no_byte(client):
    c, tmp = client
    before = hashlib.md5((tmp / "kb.db").read_bytes()).hexdigest()
    c.get("/governance/budget", headers={"Authorization": f"Bearer {TOKEN}"})
    assert hashlib.md5((tmp / "kb.db").read_bytes()).hexdigest() == before
