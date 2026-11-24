"""Packet AB — relation trails: sentinels on the three-leg conjunction.

THE TWO NON-NEGOTIABLE PINS (AB.1), first in the file on purpose:
`test_no_relation_is_ever_rewritten` and `test_two_claims_are_never_merged`.
Unmarked, so they pin the change rather than validate it — a trail LINKS;
it never collapses, rewrites, or normalises.

THE MEASURED BASIS these tests encode (AB.0, operator's store):
    co-citation frequency did not separate (genuine 8 vs topic 7 and 6);
    PMI inverted (0.96 genuine vs 1.78/1.56 topic); shared (s,o) separated
    cleanly (1 vs 0 across the top fifteen). Hence the conjunction:
    frequency-with-recurrence AND shared (s,o) AND opposition veto — the
    veto over the REACHABLE SET, because trails are pairwise and A~B, B~C
    quietly yields A-reaches-C.

THE SENSITIVITY BOUND is a surface, not a caveat: pairs clearing frequency
but failing structure land in `structurally_unconfirmed` (AB.0's
affinity_for ~ demonstrates_affinity_for shape — genuine, but its objects
differ in spelling, so the structural leg cannot see it; term identity is
deliberately out of AB's scope).
"""
from __future__ import annotations

import json
import time

import pytest

from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.memory.lineage import LineageStore
from backend.kb.trails import MIN_EVENTS, MIN_RECURRENCE, TrailStore

DAY = 86400.0
BASE = 1_000_000.0


@pytest.fixture
def rig(tmp_path):
    """A store whose claims encode all four AB.0 shapes, and a trail store
    over the same kb.db. The cursor is initialised BEFORE any lineage
    exists, so subsequent records count as steady-state walking."""
    db = tmp_path / "kb.db"
    store = TripleStore(db)
    LineageStore(db)                      # creates lineage_records
    trails = TrailStore(db)
    trails.scan_new()                     # cursor at 0 — everything later is new

    def add(s, r, o):
        return store.add(subject=s, relation=r, object=o, weight=0.8,
                         tier=1, granularity=Granularity.TOPIC,
                         source="human_assertion")

    ids = {
        # the genuine spelling pair — shares (subject, object)
        "gen_a": add("tak441", "is_metabolized_by", "cyp3a4"),
        "gen_b": add("tak441", "primarily_metabolized_by", "cyp3a4"),
        # the topic pair — co-cited, no shared arguments
        "top_a": add("plan", "addressing", "risk"),
        "top_b": add("tool", "can_use_for", "review"),
        # the opposition closure chain — all three share (dose, qtc)
        "inc":   add("dose", "increases", "qtc"),
        "aff":   add("dose", "affects_level_of", "qtc"),
        "dec":   add("dose", "decreases", "qtc"),
        # a below-floor pair
        "one_a": add("alpha", "mentions", "beta"),
        "one_b": add("alpha", "references", "beta"),
    }

    def cite(ts, belief_ids):
        """One lineage record, written the way production writes the fields
        trails read (ts + injected_json with cited flags)."""
        store.conn.execute(
            "INSERT INTO lineage_records (lineage_id, ts, query_hash, "
            "injected_json, contradictions_json, model_id, "
            "prompt_prefix_hash, response_hash, record_hash) "
            "VALUES (?, ?, 'q', ?, '[]', 'm', 'p', 'r', ?)",
            (f"lin-{ts}", ts,
             json.dumps([{"belief_id": b, "cited": True} for b in belief_ids]),
             f"hash-{ts}"))
        store.conn.commit()

    # Three refractory-separated episodes for the qualifying pairs (E.2's
    # distributed-repetition shape), one for the below-floor pair, and a
    # same-hour BURST for a burst-control pair added in the tests that need it.
    for day in range(3):
        ts = BASE + day * DAY
        cite(ts, [ids["gen_a"], ids["gen_b"]])
        cite(ts + 60, [ids["top_a"], ids["top_b"]])
        cite(ts + 120, [ids["inc"], ids["aff"]])
        cite(ts + 180, [ids["aff"], ids["dec"]])
    cite(BASE + 300, [ids["one_a"], ids["one_b"]])
    laid = trails.scan_new()
    assert laid["records"] >= 13, "fixture: the walking was not recorded"
    return store, trails, ids, cite


def _snapshot(store):
    return [tuple(r) for r in store.conn.execute(
        "SELECT id, subject, relation, object FROM triples ORDER BY id")]


# ---------------------------------------------------------------------------
# THE TWO NON-NEGOTIABLE PINS
# ---------------------------------------------------------------------------

def test_no_relation_is_ever_rewritten(rig, monkeypatch):
    """An aggressive pass — laying, backfill, classification, and trail-
    channel retrieval — leaves every stored relation byte-identical."""
    store, trails, ids, cite = rig
    before = _snapshot(store)
    assert len(before) == 9, "non-vacuity: the store must be populated"

    trails.backfill()
    trails.scan_new()
    trails.active_map()
    trails.status()
    monkeypatch.setenv("NEXUS_TRAIL_SLOTS", "5")
    TripleRetriever(store, trails=trails).retrieve("what about tak441?")

    assert _snapshot(store) == before, (
        "a stored relation moved — a trail LINKS, it never rewrites")


def test_two_claims_are_never_merged(rig, monkeypatch):
    store, trails, ids, cite = rig
    ids_before = [r[0] for r in _snapshot(store)]
    trails.backfill(); trails.scan_new(); trails.active_map()
    monkeypatch.setenv("NEXUS_TRAIL_SLOTS", "5")
    TripleRetriever(store, trails=trails).retrieve("what about tak441?")
    assert [r[0] for r in _snapshot(store)] == ids_before, (
        "row count or ids changed — something merged or removed a claim")


# ---------------------------------------------------------------------------
# the three legs
# ---------------------------------------------------------------------------

def test_the_genuine_pair_is_active_and_the_topic_pair_is_not(rig):
    """AB.0's separation result, encoded. Both pairs clear frequency; only
    the pair sharing (s, o) becomes active. The topic pair lands on the
    sensitivity surface — visibly unconfirmed, never quietly active."""
    _store, trails, ids, _cite = rig
    s = trails.status()
    assert ["is_metabolized_by", "primarily_metabolized_by"] in s["active"]
    assert ["addressing", "can_use_for"] not in s["active"]
    assert ["addressing", "can_use_for"] in s["structurally_unconfirmed"], (
        "the frequency-qualified topic pair is not on the sensitivity "
        "surface — the miss-set has stopped being declared")


def test_below_floor_stays_below_floor(rig):
    _store, trails, ids, _cite = rig
    s = trails.status()
    assert ["mentions", "references"] not in s["active"]
    assert s["below_floor"] >= 1


def test_a_single_burst_is_one_episode(tmp_path):
    """E.2's reasoning, ported: five co-citations in one hour are ONE
    episode of walking, not five. The pair clears event count and fails
    recurrence."""
    db = tmp_path / "kb.db"
    store = TripleStore(db)
    LineageStore(db)
    trails = TrailStore(db)
    trails.scan_new()
    a = store.add(subject="s", relation="rel_x", object="o", weight=0.8,
                  tier=1, granularity=Granularity.TOPIC,
                  source="human_assertion")
    b = store.add(subject="s", relation="rel_y", object="o", weight=0.8,
                  tier=1, granularity=Granularity.TOPIC,
                  source="human_assertion")
    for i in range(5):                     # five records, minutes apart
        store.conn.execute(
            "INSERT INTO lineage_records (lineage_id, ts, query_hash, "
            "injected_json, contradictions_json, model_id, "
            "prompt_prefix_hash, response_hash, record_hash) "
            "VALUES (?, ?, 'q', ?, '[]', 'm', 'p', 'r', ?)",
            (f"burst-{i}", BASE + i * 60,
             json.dumps([{"belief_id": a, "cited": True},
                         {"belief_id": b, "cited": True}]), f"bh-{i}"))
    store.conn.commit()
    trails.scan_new()
    row = trails.conn.execute(
        "SELECT event_count, recurrence_count FROM relation_trails "
        "WHERE rel_a='rel_x' AND rel_b='rel_y'").fetchone()
    assert row["event_count"] == 5 >= MIN_EVENTS, "non-vacuity"
    assert row["recurrence_count"] == 1 < MIN_RECURRENCE
    assert ["rel_x", "rel_y"] not in trails.status()["active"]


def test_the_veto_holds_over_the_reachable_set(rig):
    """THE CLOSURE CASE nobody gets by default. increases~affects_level_of
    and affects_level_of~decreases both qualify on all three edge-local
    legs — no trailed EDGE is an opposition — but the component contains
    increases/decreases, so the whole component is inactive. An edge-only
    veto would have let the path wear around it."""
    _store, trails, ids, _cite = rig
    from backend.cortex.contradiction import load_pairs
    assert ("increases", "decreases") in [tuple(p) for p in load_pairs()], (
        "fixture premise: the compiled opposition set no longer holds "
        "increases/decreases — re-derive this test's chain")
    s = trails.status()
    for pair in (["affects_level_of", "increases"],
                 ["affects_level_of", "decreases"]):
        assert pair not in s["active"], (
            f"{pair} is active inside a component containing an opposition "
            f"— the veto is running on edges, not the reachable set")
        assert pair in s["vetoed_closure"]
    # And the genuine pair, in its own component, is untouched by the veto.
    assert ["is_metabolized_by", "primarily_metabolized_by"] in s["active"]


# ---------------------------------------------------------------------------
# the channel — admission only, reducible to silence
# ---------------------------------------------------------------------------

def test_the_channel_admits_the_sibling_and_labels_it(rig, monkeypatch):
    store, trails, ids, _cite = rig
    monkeypatch.setenv("NEXUS_TRAIL_SLOTS", "3")
    r = TripleRetriever(store, trails=trails)
    # A query whose terms reach ONLY the primarily_* spelling: the sibling
    # under is_metabolized_by shares no query term surface... both share
    # subject/object here, so probe the map directly AND the pool.
    got = r.retrieve("what about tak441?")
    channels = {rt.triple.id: rt.channel for rt in got}
    assert ids["gen_a"] in channels and ids["gen_b"] in channels
    # Both spellings match lexically in this fixture; the channel's ADD path
    # is proven on a sibling that lexical search cannot reach:
    extra = store.add(subject="zk-902", relation="is_metabolized_by",
                      object="cyp2d6", weight=0.8, tier=1,
                      granularity=Granularity.TOPIC, source="human_assertion")
    sib = store.add(subject="zk-902", relation="primarily_metabolized_by",
                    object="hepatic route", weight=0.8, tier=1,
                    granularity=Granularity.TOPIC, source="human_assertion")
    # The query reaches the anchor by OBJECT only — the sibling shares
    # neither query term, so lexical search cannot admit it and the trail's
    # ADD path is the only route in. (First draft queried the subject too,
    # and the sibling arrived labelled "lexical": the test was passing on
    # the wrong mechanism.)
    got2 = r.retrieve("tell me about cyp2d6")
    ch2 = {rt.triple.id: rt.channel for rt in got2}
    assert extra in ch2, "precondition: the lexical channel finds the anchor"
    assert sib in ch2, (
        "the trail-linked sibling was not admitted — the channel added "
        "nothing")
    assert ch2[sib] == "trail", (
        f"admitted but labelled {ch2[sib]!r} — I-6, the pool must say WHY "
        f"each row is in it")


def test_slots_zero_is_byte_identical_to_no_trails(rig, monkeypatch):
    """REDUCIBILITY of the channel, at the documented off position."""
    store, trails, ids, _cite = rig
    monkeypatch.setenv("NEXUS_TRAIL_SLOTS", "0")
    with_trails = TripleRetriever(store, trails=trails).retrieve(
        "what about tak441?")
    without = TripleRetriever(store, trails=None).retrieve(
        "what about tak441?")
    assert [(rt.triple.id, rt.channel) for rt in with_trails] == \
           [(rt.triple.id, rt.channel) for rt in without]


def test_laying_off_lays_nothing(tmp_path, monkeypatch):
    """REDUCIBILITY of the laying, at the documented off position."""
    monkeypatch.setenv("NEXUS_RELATION_TRAILS", "off")
    db = tmp_path / "kb.db"
    TripleStore(db)
    trails = TrailStore(db)
    assert trails.scan_new() == {"skipped": "disabled"}
    assert trails.conn.execute(
        "SELECT COUNT(*) FROM relation_trails").fetchone()[0] == 0


def test_trails_do_not_touch_the_ordering(rig, monkeypatch):
    """W's law, held by the third channel too: the rows both configurations
    retrieve appear in the SAME relative order — trails add, never rank."""
    store, trails, ids, _cite = rig
    monkeypatch.setenv("NEXUS_TRAIL_SLOTS", "5")
    with_t = TripleRetriever(store, trails=trails).retrieve("what about tak441?")
    monkeypatch.setenv("NEXUS_TRAIL_SLOTS", "0")
    without = TripleRetriever(store, trails=trails).retrieve("what about tak441?")
    common = {rt.triple.id for rt in without}
    order_with = [rt.triple.id for rt in with_t if rt.triple.id in common]
    order_without = [rt.triple.id for rt in without]
    assert order_with == order_without, (
        "turning the trail channel on changed the relative order of rows "
        "it did not add — the ordering door W closed has been reopened")


# ---------------------------------------------------------------------------
# coverage, cursor, Law 3
# ---------------------------------------------------------------------------

def test_a_fresh_install_does_not_consume_history(tmp_path):
    """The cursor starts at the newest existing record: months of lineage
    are NOT silently processed on first contact. History is entered only
    via the Law 3 backfill — and a second backfill does not double-count."""
    db = tmp_path / "kb.db"
    store = TripleStore(db)
    LineageStore(db)
    a = store.add(subject="s", relation="old_rel_a", object="o", weight=0.8,
                  tier=1, granularity=Granularity.TOPIC,
                  source="human_assertion")
    b = store.add(subject="s", relation="old_rel_b", object="o", weight=0.8,
                  tier=1, granularity=Granularity.TOPIC,
                  source="human_assertion")
    for day in range(3):
        store.conn.execute(
            "INSERT INTO lineage_records (lineage_id, ts, query_hash, "
            "injected_json, contradictions_json, model_id, "
            "prompt_prefix_hash, response_hash, record_hash) "
            "VALUES (?, ?, 'q', ?, '[]', 'm', 'p', 'r', ?)",
            (f"old-{day}", BASE + day * DAY,
             json.dumps([{"belief_id": a, "cited": True},
                         {"belief_id": b, "cited": True}]), f"oh-{day}"))
    store.conn.commit()

    trails = TrailStore(db)                # first contact AFTER the history
    assert trails.scan_new().get("records", 0) == 0, (
        "a fresh install consumed pre-existing history without Law 3")
    assert trails.status()["coverage"]["backfilled"] is False

    first = trails.backfill()
    assert first["records"] == 3, "the confirmed backfill missed history"
    assert trails.status()["coverage"]["backfilled"] is True
    again = trails.backfill()
    assert again.get("already_backfilled") is True and again["records"] == 0, (
        "a second backfill re-processed the same records — double-counted "
        "walking")
    row = trails.conn.execute(
        "SELECT event_count FROM relation_trails WHERE rel_a='old_rel_a'"
    ).fetchone()
    assert row["event_count"] == 3


def test_the_backfill_route_is_law3(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", "trails-token")
    from fastapi.testclient import TestClient

    from backend.server import create_app, create_services
    svc = create_services(kb_db_path=tmp_path / "kb.db",
                          chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl",
                          archive_db_path=tmp_path / "a.db",
                          snapshots_dir=tmp_path / "snap")
    client = TestClient(create_app(svc))
    H = {"Authorization": "Bearer trails-token"}
    r = client.post("/kb/trails/backfill/confirm", headers=H)
    # AT.2 — 422 like every other gate (was 400), and recorded.
    assert r.status_code == 422 and "Law 3" in r.json()["error"]
    assert r.json()["recorded"] is True
    r2 = client.post("/kb/trails/backfill/confirm?confirmed=true", headers=H)
    assert r2.status_code == 200
    assert r2.json()["status"]["coverage"]["backfilled"] is True
    # And the stats surface declares the whole state, confirm route included.
    stats = client.get("/cortex/stats").json()["relation_trails"]
    assert stats["coverage"]["backfilled"] is True
    assert stats["confirm_backfill"]["endpoint"] == "/kb/trails/backfill/confirm"
    assert "structurally_unconfirmed" in stats, (
        "the sensitivity bound fell off the surface")
