"""FZ-2 — the freeze switch for association learning (retires T-1).

Frozen means: no automatic, unconfirmed write from the learning layer to
the graph or the store, checked at five store methods. Frozen stops
learning, not answering. THE GUARD READS THE DATABASE: every write a
traced run makes under frozen must be a (table, host, method) pair
answered for below, and the drivers that produce the writes are derived
(boot, turns, cycles, every non-GET route, every store-importing script).

T1   frozen trace == PERMITTED_UNDER_FROZEN, pair-level; learning pairs 0
T1r  the same drivers under live produce all five learning pairs (seen red)
T1c  a sixth writer to `triples` passes a table-level check and fails the
     pair-level one — the reason the check is over pairs
T1d  a helper frame between host and store method is dropped by the
     normalisation, so a rename is not a new writer
T1e  a write route added later is in the derived population and its pair
     is named without an edit here
T2   frozen stops learning, not answering
T3   the learning state is declared on every turn and on stats, three ways
T4   the setting is read through the real dotenv path, fresh interpreter
T5   `done` is a versioned contract (2 since B-43), pinned three ways
T6   the threshold's code default is 3, unchanged
T7   a write the freeze stops is counted: skipped == the live learning total
T8   trails defer and do not lose
T9   what the freeze stops in `rebalance` is a tier move and nothing else
T10  one frozen turn: the reinforcement counter does not move, the record's
     cited flag is written
T11  the trace names a frame by its module, not its file path: a synthetic
     frame whose filename is a Windows path normalises to
     (triples, cycle, tiered_store.rebalance) — the seven chain-dependent
     tests above failed on the operating instance because it did not
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import learning
from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore
from backend.memory.neocortex import DEFAULT_RECURRENCE_THRESHOLD
from tests import _fz_trace
from tests._fz_trace import (LEARNING_PAIRS, ROUTE_EXEMPTIONS, WriteTrace, classify, done_frames,
                             drive_boundary_turns, drive_routes, drive_scripts, drive_turns_and_cycles, plant_counters,
                             store_scripts, write_routes)
from tests.fixtures.advice_recall_fixture import HOUR, PLAN, T0, build
from tests.fixtures.ce_rig import RigClock
from tests.test_ba_fix2_request_hash import DONE_CONTRACT_VERSION, DONE_KEYS_V3, REINFORCEMENT_KEYS_V2
from tests.test_dotenv_settings_take_effect import _resolve_in_child

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import history_shape_harness as hsh  # noqa: E402

TOKEN = "fz-token"
H = {"Authorization": f"Bearer {TOKEN}"}

# THE PERMITTED SET, answered for pair by pair. A frozen run may write
# exactly these and nothing else. A new host or a new method is a new pair
# and fails T1 until someone adds it here with a reason (BD-2's shape).
PERMITTED_UNDER_FROZEN: dict[tuple[str, str, str], tuple[str, str]] = {
    # boot — migrations and the recall index backfill
    ("scope_meta", "boot", "lineage._migrate_scope"):            ("record", "scope's recordable-since, written once at open"),
    ("scope_meta", "boot", "recalibration.__init__"):            ("record", "the same instant, whichever store opens first"),
    ("lineage_injections", "boot", "lineage._index_backfill"):   ("record", "AR's derived index, backfilled once at open"),
    # turn — the record and the claim path
    ("lineage_records", "turn", "lineage.record_turn"):          ("record", "the answer's evidence record"),
    ("lineage_injections", "turn", "lineage.record_turn"):       ("record", "the record's backward index, same transaction"),
    # W0b — the replay capture's companions, same transaction as the record
    # W10c — the one statement that writes request_bodies is `_insert_request_body` (record_turn calls it inside
    # the record's transaction; the interceptor's pre-forward write calls it too): the same write, under its own name.
    ("request_bodies", "turn", "lineage._insert_request_body"):  ("record", "W0b: the exact request bytes, verified by the record's request_hash"),
    ("inference_configs", "turn", "lineage.record_turn"):        ("record", "W0b: the options sent, keyed by a hash the record seals"),
    ("environments", "turn", "lineage.record_turn"):             ("record", "W0b: the runtime facts, keyed by a hash the record seals"),
    ("triple_work_unit", "turn", "work_units.admit"):            ("record", "attribution junction, admission not use"),
    ("work_unit", "turn", "work_units.open"):                    ("record", "the session pseudo-unit, created on first mention"),
    # W6 — the boundary act and its ledger row, on the turn, after its record
    ("work_unit", "turn", "work_units.close"):                   ("record", "W6: closed_at set by a turn's asserted close"),
    ("work_unit", "turn", "work_units.reopen"):                  ("record", "W6: closed_at cleared by a turn's asserted reopen"),
    ("work_unit_events", "turn", "work_units._append_event"):    ("record", "W6: the boundary ledger's one writer"),
    # W8 — a record's salts and commitment root, in the record's own transaction
    ("record_salts", "turn", "lineage._write_commitments"):      ("record", "W8: one salt per hashed field, beside the record"),
    ("record_commitments", "turn", "lineage._write_commitments"): ("record", "W8: the record's commitment root"),
    ("triples", "turn", "tiered_store.add"):                     ("claim path", "extraction through add() and the gate"),
    # cycle — the seals only
    ("archive_blocks", "cycle", "chain.append"):                 ("record", "governance seals and snapshots"),
    ("lineage_records", "cycle", "lineage.mark_sealed"):         ("record", "sealed_block backfill"),
    ("archival_events", "cycle", "recalibration.mark_sealed"):   ("record", "sealed_block backfill"),
    ("envelope_versions", "cycle", "recalibration.mark_sealed"): ("record", "sealed_block backfill"),
    ("outcome_events", "cycle", "recalibration.mark_sealed"):    ("record", "sealed_block backfill"),
    ("recalibration_events", "cycle", "recalibration.mark_sealed"): ("record", "sealed_block backfill"),
    ("refused_requests", "cycle", "recalibration.mark_sealed"):  ("record", "sealed_block backfill"),
    ("work_unit_events", "cycle", "recalibration.mark_sealed"):  ("record", "sealed_block backfill (W6: the boundary ledger)"),
    ("record_tombstones", "cycle", "recalibration.mark_sealed"): ("record", "sealed_block backfill (W8: the tombstone acts)"),
    ("record_commitments", "cycle", "lineage.mark_bound"):  ("record", "W8 (D6): a seal binding a root no seal covered"),
    ("trail_cursor", "cycle", "trails._ensure_cursor"):          ("record", "the trail cursor's one-time initialisation; not advanced under frozen"),
    # routes — confirmed human acts (Law 3) and the semantic index backfill
    ("triples", "route:assert_confirm", "tiered_store.add"):     ("confirmed act", "a human assertion"),
    ("held_commitments", "route:commit_now", "lineage.hold_commitment"): ("confirmed act", "W9 (D8): a person's note, committed"),
    ("held_commitments", "cycle", "lineage.mark_commitments_sealed"): ("record", "W9 (D8): the seal that covers it"),
    ("triples", "route:kb_seed_offline", "tiered_store.add"):    ("confirmed act", "a confirmed seed"),
    ("triples", "route:dedup_confirm", "tiered_store.archive_ids"): ("confirmed act", "a confirmed archival: the archived flag"),
    ("archival_events", "route:dedup_confirm", "tiered_store._record_archival"): ("confirmed act", "the archival act ledger"),
    ("outcome_events", "route:outcome_confirm", "recalibration.record_outcome"): ("confirmed act", "a recorded outcome"),
    ("recalibration_events", "route:outcome_confirm", "recalibration._insert_recalibration"): ("confirmed act", "the governed adjustment it authorised"),
    ("envelope_versions", "route:governance_envelope_confirm", "recalibration.record_envelope"): ("confirmed act", "a policy version"),
    ("triple_embeddings", "route:kb_index_confirm", "semantic_index.upsert"): ("confirmed act", "the Law 3 index backfill; an index of claims, not learning"),
    ("trail_cursor", "route:trails_backfill", "trails.backfill"): ("confirmed act", "the Law 3 trail backfill over history"),
    ("relation_trails", "route:trails_backfill", "trails._lay_range"): ("confirmed act", "the rows that backfill lays; confirmed, not automatic"),
}

SCRIPT_EXEMPTION_REASON = "no dry mode (a probe or harness: reads, or writes only its own scratch store)"


QUESTIONS = ["what does the isolation step require?", "what does sample isolation precede?",
             "and the centrifuge speed?", "does sample isolation require centrifuge at 3000g for 10 minutes?"]


class _Stub(hsh._Stub):
    """Answers in the fixture's own terms (so the answer CITES the injected
    claims and trails have two relations to pair), and extracts one claim,
    so the turn exercises the claim path."""

    async def stream_chat(self, model, messages, num_ctx):
        self.calls += 1
        if self.calls % 2 == 1:
            yield ("Sample isolation requires centrifuge at 3000g for 10 minutes, and sample isolation "
                   "precedes lysis.")
        else:
            yield json.dumps([{"subject": "sample isolation", "relation": "requires",
                               "object": "a calibrated centrifuge", "confidence": 0.8}])

    def request_config(self, model, num_ctx, *, separate):
        """W0b — the options this stub would send, declared the way a real
        provider declares them, so the trace exercises every companion the
        record writes (`inference_configs` beside `environments` and
        `request_bodies`)."""
        return {"provider": "fz-stub", "model": model, "options": {"num_ctx": num_ctx}}


# W1 part 4 — the clock the comparisons run on starts one hour after the
# recall fixture's newest record. The trail cursor's first contact is the
# newest record, so this keeps it behind every turn a test drives, as the
# host's clock does (its turns run a year after the fixture's). At the rig's
# default instant (2023) every turn would predate the fixture, the cursor
# would sit after all of them, and no trail would be laid: T8's arms equal,
# and vacuous; T1r short of its trail pairs.
FIXTURE_NEWEST_TS = T0 + (len(PLAN) - 1) * HOUR
RIG_BASE_AFTER_FIXTURE = FIXTURE_NEWEST_TS + HOUR


def _rig(tmp_path, monkeypatch, mode: str, since: str | None = None):
    """The recall fixture (sealed records, three claims) under a traced
    boot. Returns (trace, fx, svc, client). The trace stays installed for
    the caller's drivers."""
    monkeypatch.setenv("NEXUS_LEARNING", mode)
    if since is None:
        monkeypatch.delenv("NEXUS_LEARNING_FROZEN_SINCE", raising=False)
    else:
        monkeypatch.setenv("NEXUS_LEARNING_FROZEN_SINCE", since)
    monkeypatch.setenv("NEXUS_REFRACTORY_HOURS", "0")
    monkeypatch.delenv("NEXUS_RECURRENCE_THRESHOLD", raising=False)
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    learning.reset_skips()
    fx = build(tmp_path)
    # W3b — a store that reaches boot with one record lacking its recall-index
    # rows (the pre-AR shape the backfill exists for), so the boot driver
    # exercises `lineage._index_backfill`'s write. Before W3b the backfill was
    # one INSERT … SELECT statement that ran at every open, work or none, and
    # the trace saw the statement; in Python it issues a statement only when
    # a record needs indexing, so the fixture must give it one. Done before
    # the trace starts: this is the fixture's shape, not a driver's write.
    from backend.store import connect as _store_connect
    from tests.conftest import store_target_for
    _pre_ar = _store_connect(store_target_for(fx.kb_path))
    _pre_ar.execute("DELETE FROM lineage_injections WHERE lineage_id = "
                    "(SELECT lineage_id FROM lineage_records ORDER BY ts ASC LIMIT 1)")
    _pre_ar.commit()
    _pre_ar.close()
    tr = WriteTrace()
    tr.__enter__()
    from backend.server import create_app, create_services
    svc = create_services(kb_db_path=fx.kb_path, chroma_dir=tmp_path / "chroma",
                          eval_log_path=tmp_path / "m.jsonl", archive_db_path=fx.archive_path,
                          snapshots_dir=tmp_path / "snap", provider=_Stub(plant=None))
    client = TestClient(create_app(svc))
    return tr, fx, svc, client


def _all_drivers(tr, fx, svc, client) -> dict:
    plant_counters(svc)
    promoted = drive_turns_and_cycles(client, H, turns=60, every=10, questions=QUESTIONS)
    boundary = drive_boundary_turns(client, H)               # W6 — a close and a reopen, on turns
    assert boundary[1]["unit_closed"] and boundary[2]["unit_reopened"], boundary
    routes = drive_routes(client, fx, H)
    scripts = drive_scripts(fx.kb_path)
    tr.__exit__(None, None, None)
    return {"promoted": promoted, "routes": routes, "scripts": scripts}


def _traced_run(tmp_path, monkeypatch, mode: str, since: str | None = None) -> tuple:
    """T1's boot and drivers on the rig's clock, from `RIG_BASE_AFTER_FIXTURE`
    (W1 part 4; T8 below says why the clock is an input and why the base sits
    after the fixture). Returns (trace, client, drivers)."""
    with RigClock(base=RIG_BASE_AFTER_FIXTURE).installed():
        tr, fx, svc, client = _rig(tmp_path, monkeypatch, mode, since)
        d = _all_drivers(tr, fx, svc, client)
    return tr, client, d


def _print_pairs(label: str, pairs) -> None:
    print(f"\n{label}: {len(pairs)} pairs")
    for (t, h, m), n in sorted(pairs.items(), key=lambda kv: (str(classify(kv[0])), kv[0])):
        print(f"   {str(classify((t, h, m))):<14}{t:<22}{h:<34}{m:<38}{n:>7}")


# ---------------------------------------------------------------------------
# T1 / T1r — the guard, and the run that turns it red first
# ---------------------------------------------------------------------------

def test_frozen_trace_equals_permitted_pairs(tmp_path, monkeypatch):
    """Under frozen, every write the database saw is a pair someone answered for; the drivers
    are derived, so a new route or script is exercised without an edit here."""
    tr, client, d = _traced_run(tmp_path, monkeypatch, "frozen", "2026-09-05")
    pairs = tr.pairs()
    n_routes = len(write_routes(client.app))
    print(f"\nT1 drivers: boot, 60 turns, 6 cycles (promoted {d['promoted']}), routes {n_routes} "
          f"(driven {len(d['routes']['driven'])}, exempt {len(d['routes']['exempt'])}: "
          f"{[e[0] for e in d['routes']['exempt']]}, undriven {d['routes']['undriven']}), "
          f"scripts {len(store_scripts())} (driven {[s for s, _ in d['scripts']['driven']]}, "
          f"exempt {len(d['scripts']['exempt'])})")
    assert not d["routes"]["undriven"], "every derived write route needs a fixture payload or an exemption"
    assert len(d["routes"]["exempt"]) * 4 <= n_routes, "more than a quarter of the write routes exempt"
    assert all(reason == SCRIPT_EXEMPTION_REASON or "no store-path" in reason for _n, reason in d["scripts"]["exempt"])
    _print_pairs("T1 frozen pairs", pairs)
    learning_pairs = {p: n for p, n in pairs.items() if classify(p) == "learning"}
    print(f"T1 learning pairs under frozen: {len(learning_pairs)} (statements {sum(learning_pairs.values())})")
    assert learning_pairs == {}
    unanswered = [p for p in pairs if classify(p) is None]
    assert not unanswered, f"pairs none of record, claim path, confirmed act or learning: {unanswered}"
    assert set(pairs) == set(PERMITTED_UNDER_FROZEN), (
        f"unexpected: {sorted(set(pairs) - set(PERMITTED_UNDER_FROZEN))}; "
        f"not seen: {sorted(set(PERMITTED_UNDER_FROZEN) - set(pairs))}")
    for p, (cls, _reason) in PERMITTED_UNDER_FROZEN.items():
        assert classify(p) == cls, (p, classify(p), cls)


def test_live_trace_contains_learning_pairs(tmp_path, monkeypatch):
    """Seen red first: the same drivers under live produce all five learning pairs.

    W1 part 4. The drivers run on the rig's clock, inside a planted slow host
    that they must read zero times: this test is its own companion. On the
    host's clock, a host slow between turns let the edges the last turn
    fired transmit outside their 0.5 s refractory, and more edges reached
    the promotion count, so the first cycle promoted 8 where the planted
    counters are 5 (measured on Linux at x20; T8 below has the mechanism).
    The planted host is deterministic since part 5i (`_slow_host`): 50 ms a
    read from the rig's instant, the shape that splits the refractory class
    on every host, where a scaled real clock split by the host's own speed
    (the pre-fix count moved: 79, 24, 8). Pre-fix (law 16): with the drivers
    unclocked as T1r ran them at 2d52f02, this test fails on the count,
    deterministically; the run is in b0ad516's commit body (part 5i) and in
    RUN_LOG.md's W1 entry."""
    slow = _slow_host()
    with slow.installed():
        tr, client, d = _traced_run(tmp_path, monkeypatch, "live")
    pairs = tr.pairs()
    _print_pairs("T1r live pairs", pairs)
    seen = {p: n for p, n in pairs.items() if p in LEARNING_PAIRS}
    print(f"T1r learning pairs under live: {len(seen)} of {len(LEARNING_PAIRS)}; statements {sum(seen.values())}; "
          f"promoted {d['promoted']}; slow host read {slow.reads} times")
    assert set(seen) == set(LEARNING_PAIRS), sorted(set(LEARNING_PAIRS) - set(seen))
    assert all(n > 0 for n in seen.values())
    assert d["promoted"][0] == 5, "the planted counters were promoted at the shipped threshold"
    assert slow.reads == 0, "the host's clock reached the drivers"


# ---------------------------------------------------------------------------
# T1c / T1d / T1e — companions that plant what the guard does not name
# ---------------------------------------------------------------------------

def test_sixth_writer_to_permitted_table_is_named(tmp_path, monkeypatch):
    """A new automatic writer to `triples` passes a table-level check and fails the pair-level
    one, which is why the check is over pairs."""
    from tests.fixtures import fz_plant_writer as plant
    monkeypatch.setenv("NEXUS_LEARNING", "frozen")
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    kb = tmp_path / "kb.db"
    with WriteTrace(extra_modules=("tests.fixtures.",)) as tr:
        store = TripleStore(kb)
        store.add(subject="sample isolation", relation="requires", object="a calibrated centrifuge",
                  weight=0.5, tier=1, granularity=Granularity.TOPIC, source="extraction")   # the claim path
        plant._run_cycle_body(lambda: plant.plant_writer(store.conn))
        store.close()
    pairs = tr.pairs()
    permitted_tables = {t for t, _h, _m in PERMITTED_UNDER_FROZEN}
    # Table-level: everything written is a permitted table — PASSES.
    assert set(tr.tables()) <= permitted_tables, tr.tables()
    # Pair-level: the planted chain is a pair nobody answered for — FAILS, by name.
    unanswered = sorted(set(pairs) - set(PERMITTED_UNDER_FROZEN))
    print(f"\nT1c table-level check passed over {sorted(tr.tables())}; pair-level names {unanswered}")
    assert ("triples", "cycle", "fz_plant_writer.plant_writer") in unanswered
    assert classify(("triples", "cycle", "fz_plant_writer.plant_writer")) is None


def test_helper_rename_does_not_fail(tmp_path, monkeypatch):
    """Normalisation drops helpers, so a rename is not a new writer."""
    from tests.fixtures import fz_plant_writer as plant
    from backend.memory.graph_store import HebbianGraphStore
    from backend.memory.hippocampus import Hippocampus
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    hippo = Hippocampus()
    hippo.ingest_triple("a", "co_activated", "b", weight=0.5)
    for mode, expect_pair in (("live", True), ("frozen", False)):
        monkeypatch.setenv("NEXUS_LEARNING", mode)
        with WriteTrace(extra_modules=("tests.fixtures.",)) as tr:
            gs = HebbianGraphStore(tmp_path / f"{mode}.db")
            plant._run_cycle_body(lambda: plant.persist_via_helper(gs, hippo))
            gs.close()
        pairs = tr.pairs()
        if expect_pair:
            assert set(pairs) == {("hebbian_edges", "cycle", "graph_store.save")}, pairs
            assert classify(("hebbian_edges", "cycle", "graph_store.save")) == "learning"
        else:
            assert pairs == {}, pairs     # frozen: the helper changed nothing; T1's set holds


def test_new_route_is_driven_without_edit(tmp_path, monkeypatch):
    """A write route added later is in the traced population the day it exists."""
    from tests.fixtures import fz_plant_writer as plant
    monkeypatch.setenv("NEXUS_LEARNING", "frozen")
    db = tmp_path / "planted.db"
    planted = FastAPI()

    @planted.post("/planted/write")
    async def planted_write():
        plant.planted_route_write(db)
        return {"ok": True}

    routes = write_routes(planted)
    assert ("POST", "/planted/write") in routes, routes
    with WriteTrace(extra_modules=("tests.fixtures.",)) as tr:
        with TestClient(planted) as c:
            assert c.post("/planted/write").status_code == 200
    pairs = tr.pairs()
    named = [p for p in pairs if p[0] == "planted_rows"]
    print(f"\nT1e derived routes: {routes}; planted pair: {named}")
    assert named == [("planted_rows", "other:fz_plant_writer.planted_route_write", "fz_plant_writer.planted_route_write")]
    assert named[0] not in PERMITTED_UNDER_FROZEN and classify(named[0]) is None


# ---------------------------------------------------------------------------
# T2 — frozen stops learning, not answering
# ---------------------------------------------------------------------------

def _injected_sets(svc, n_last: int) -> list[set]:
    recs = svc.lineage.recent(limit=n_last)
    return [frozenset(i["belief_id"] for i in r.injected) for r in reversed(recs)]


def test_frozen_stops_learning_not_answering(tmp_path, monkeypatch):
    """Frozen stops learning, not answering. The two arms run on the rig's
    clock (W1 part 4: every comparison of arms in this file does, because the
    clock is an input)."""
    sets = {}
    for mode in ("live", "frozen"):
        sub = tmp_path / mode
        sub.mkdir()
        with RigClock(base=RIG_BASE_AFTER_FIXTURE).installed():
            tr, fx, svc, client = _rig(sub, monkeypatch, mode)
            for q in ("what does the isolation step require?", "and the centrifuge speed?", "what about lysis?"):
                r = client.post("/chat", json={"message": q}, headers=H)
                assert r.status_code == 200 and done_frames(r.text)[-1]["turn"] > 0
            sets[mode] = _injected_sets(svc, 3)
            before = svc.store.count()
            a = client.post("/kb/assert/confirm", json={"sentence": "The dose was 600mg.", "confirmed": True}, headers=H)
            assert a.status_code == 200 and a.json()["stored"] == 1 and svc.store.count() == before + 1
            tr.__exit__(None, None, None)
    assert sets["frozen"] == sets["live"], "retrieval injected the same beliefs under both"
    assert any(sets["live"]), "non-vacuity: something was injected"


# ---------------------------------------------------------------------------
# T3 / T4 / T5 / T6 — declarations and contracts
# ---------------------------------------------------------------------------

def test_learning_state_declared_every_turn(tmp_path, monkeypatch):
    """A system that is not learning says so on every turn."""
    expect = [("frozen", "2026-09-05", "frozen since 2026-09-05"),
              ("frozen", None, "frozen since: undeclared"),
              ("live", None, "live")]
    for i, (mode, since, display) in enumerate(expect):
        sub = tmp_path / f"case{i}"
        sub.mkdir()
        tr, fx, svc, client = _rig(sub, monkeypatch, mode, since)
        done = done_frames(client.post("/chat", json={"message": "q"}, headers=H).text)[-1]
        stats = client.get("/cortex/stats").json()["learning"]
        tr.__exit__(None, None, None)
        assert done["learning"]["display"] == display and done["learning"]["state"] == mode
        assert done["learning"]["since"] == since and done["learning"]["since_source"] == "asserted"
        assert stats["display"] == display and stats["since"] == since
        assert "skipped_writes_since_start" in stats and stats["checked_at"] == list(learning.LEARNING_METHODS)
    monkeypatch.setenv("NEXUS_LEARNING", "live")          # the rollback position, exercised by name


def test_learning_setting_fresh_interpreter(tmp_path):
    """The setting is read through the real dotenv path, both positions."""
    for value in ("frozen", "live"):
        got = _resolve_in_child(tmp_path, "NEXUS_LEARNING", value,
                                "__import__('backend.learning', fromlist=['x']).learning_mode()")
        assert got == repr(value), got
    got = _resolve_in_child(tmp_path, "NEXUS_LEARNING_FROZEN_SINCE", "2026-09-05",
                            "__import__('backend.learning', fromlist=['x']).frozen_since()")
    assert got == repr("2026-09-05")
    print(f"\nT4 rollback literal: {learning.ROLLBACK_LITERAL}")
    assert learning.ROLLBACK_LITERAL == "NEXUS_LEARNING=live"


def test_done_contract_version(tmp_path, monkeypatch):
    """The `done` frame is a contract; `learning` was its first versioned bump (1) and
    `reinforcement.skipped` its second (2, B-43). The version literal is pinned three ways —
    the test-side constant, the CLAUDE.md payload note and the INTEGRATION_REFERENCE row — and
    all three must say the same number."""
    tr, fx, svc, client = _rig(tmp_path, monkeypatch, "live")
    done = done_frames(client.post("/chat", json={"message": "q"}, headers=H).text)[-1]
    tr.__exit__(None, None, None)
    assert set(done) == DONE_KEYS_V3 and done["contract_version"] == DONE_CONTRACT_VERSION == 4
    assert set(done["reinforcement"]) == REINFORCEMENT_KEYS_V2
    claude = (ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    block = claude.split("## SSE Events")[1].split("```")[1]
    done_note = block[block.index("done "):]
    assert "learning" in done_note and f"contract_version = {DONE_CONTRACT_VERSION}" in done_note
    assert "skipped" in done_note
    for doc in ("docs/INTEGRATION_REFERENCE.md", "docs/INTEGRATION_REFERENCE.html"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert f"contract_version: {DONE_CONTRACT_VERSION}" in text and "learning" in text, doc
        assert '"skipped"' in text, doc


def test_threshold_default_unchanged():
    """The threshold's code default was never changed; T-1 lives in the operator's `.env`."""
    print(f"\nT6 DEFAULT_RECURRENCE_THRESHOLD = {DEFAULT_RECURRENCE_THRESHOLD}")
    assert DEFAULT_RECURRENCE_THRESHOLD == 3


# ---------------------------------------------------------------------------
# T7 — skipped writes are counted, and they equal the live count
# ---------------------------------------------------------------------------

SLOW_HOST_STEP = 0.05          # seconds a read: the deterministic slow host (part 5i)


def _slow_host() -> RigClock:
    """A slow host, deterministic: the rig's clock from
    `RIG_BASE_AFTER_FIXTURE` at 50 ms a read, so every read a turn makes
    moves the clock and turns land outside the edges' 0.5 s refractory
    (`SynapticEdge.in_refractory`) on every host alike. A scaled real clock
    (the host's elapsed time multiplied, the plant of part 4 and of W0b part
    2b) split the same class only as fast as the host ran, so its pre-fix
    failure moved with the machine; one stall of two seconds does not split
    T8 or T1r at all (W1's third verifier, F2). Counts its reads, like every
    RigClock."""
    return RigClock(base=RIG_BASE_AFTER_FIXTURE, step=SLOW_HOST_STEP)


T7_STALL_AT = 100              # the rig clock's read from which T7's stall applies (part 5m)
T7_STALL_BY = 0.6              # seconds: past the edges' 0.5 s refractory


class _StallingHost(RigClock):
    """A slow host for T7, deterministic: the rig's clock, stalled by
    `T7_STALL_BY` seconds from its `T7_STALL_AT`-th read on. One instance is
    shared by the two arms, as a real host is, so the stall falls inside one
    arm's run and moves that arm's turns against the other's. Measured on
    part 5m's code with the arms unclocked, twice identically: a 0.6 s stall
    at read 20, 50, 100, 150, 200, 300, 400 or 600 splits them (at 100, 517
    saved against 506 skipped), one at read 10 or 500 does not. A fixed step
    (1 ms to 50 ms a read) never splits T7, because it moves both arms'
    turns alike. Because a code change can move the reads, T7c checks this
    premise on every run before it checks the fix. Part 5m replaced with it
    the scaled real clock W0b part 2b planted (the host's elapsed time x5),
    whose split came and went with the host's speed: 547 against 530 in one
    run and 530 against 530 in another, on one machine."""

    def __init__(self, at: int = T7_STALL_AT, by: float = T7_STALL_BY):
        super().__init__()
        self.at, self.by = at, by

    def __call__(self) -> float:
        t = super().__call__()
        return t + (self.by if self.reads >= self.at else 0.0)


def _t7_arms(tmp_path, monkeypatch, *, clocked: bool = True) -> dict:
    """T7's two arms with identical inputs, the clock included. Each arm
    runs on the rig's deterministic clock (tests/fixtures/ce_rig.RigClock, one
    microsecond a read), from `RIG_BASE_AFTER_FIXTURE`, installed around its
    fixture, boot, turns and cycle, and creates the trail cursor before its
    first turn, as T8's arms do. Both since part 5n (the third verifier's
    critic, C9): from the default instant the turns' records predate the
    fixture's, the cursor the cycle creates sits after all of them, and the
    trail method wrote and skipped nothing in either arm, so T7's equality
    held vacuously for the fifth learning method. `clocked=False` runs the
    arms as T7 ran before W0b part 2b, on whatever `time.time` is: T7c's
    premise check, and nothing else."""
    totals = {}
    for mode in ("live", "frozen"):
        sub = tmp_path / mode
        sub.mkdir(parents=True)
        with (RigClock(base=RIG_BASE_AFTER_FIXTURE).installed() if clocked else contextlib.nullcontext()):
            tr, fx, svc, client = _rig(sub, monkeypatch, mode)
            plant_counters(svc)
            svc.trails._ensure_cursor()                 # the cursor exists before any turn, as in T8
            drive_turns_and_cycles(client, H, turns=5, every=5, questions=QUESTIONS)   # five turns, one cycle: identical inputs both arms
            tr.__exit__(None, None, None)
        totals[mode] = {"learning_statements": sum(n for p, n in tr.pairs().items() if classify(p) == "learning"),
                        "by_pair": {f"{p[0]}/{p[1]}/{p[2]}": n for p, n in tr.pairs().items() if classify(p) == "learning"},
                        **learning.skipped_writes()}
    return totals


def _t7_saves(totals: dict) -> tuple[int, int]:
    """The graph saves the live arm made, and the ones the frozen arm's freeze
    refused: the first-order pair T7 compares."""
    live, skipped = totals["live"]["by_pair"], totals["frozen"]["by_method"]
    return (live.get("hebbian_edges/turn/graph_store.save", 0) + live.get("hebbian_edges/cycle/graph_store.save", 0),
            skipped.get("HebbianGraphStore.save", 0))


def test_skipped_writes_counted(tmp_path, monkeypatch):
    """A write the freeze stops is counted, so frozen and broken look different.

    W0b part 2b. The clock is an input too. The live arm persists the graph
    and fires the claims it cites; the frozen arm's writes are refused, so
    the live arm reads the clock more often and, on a real host, spends
    longer between turns. On a host slow between turns the two arms' turns
    therefore land at different instants, a different set of edges is past
    its 0.5 s transmission refractory (`SynapticEdge.in_refractory`) when a
    later turn propagates, and the graphs the arms save differ. CI runs 5 and
    6 on windows showed 403 + 127 saved against 506 skipped, identically both
    times. W0b's record gave the order key's rounding step as the input
    ("turn 5 then retrieves in a different order"); W1's third verifier (R6)
    and part 5m measured otherwise: in the split, both arms inject the same
    claims in the same order in all 17 records, and with the refractory
    forced off (559 against 559) or on (153 against 153) the stalled arms
    agree. Both arms run on the rig's clock (`_t7_arms`). The comparison
    itself is unchanged.
    """
    totals = _t7_arms(tmp_path, monkeypatch)
    print(f"\nT7 live: {totals['live']}\nT7 frozen: {totals['frozen']}")
    assert totals["live"]["skipped_writes_since_start"] == 0
    assert totals["frozen"]["learning_statements"] == 0
    live, skipped = totals["live"]["by_pair"], totals["frozen"]["by_method"]
    # First-order writes agree method by method: the persist, the counter,
    # the promotions the gate admitted.
    assert skipped["HebbianGraphStore.save"] == (live.get("hebbian_edges/turn/graph_store.save", 0)
                                                 + live.get("hebbian_edges/cycle/graph_store.save", 0)) > 0
    assert skipped["TripleStore.fire"] == live.get("triples/turn/tiered_store.fire", 0) > 0
    promotions = live.get("triples/cycle/tiered_store.add", 0)
    assert skipped["Neocortex.consolidate_from_hippocampus"] + skipped.get("Neocortex.store_pattern_triple", 0) \
        == promotions > 0
    # The fifth method, the trails (part 5n, C9): the live cycle lays the
    # turns' trails and advances the cursor; the frozen cycle refuses the
    # same writes, deferred and counted.
    trails = live.get("relation_trails/cycle/trails._lay_range", 0) + live.get("trail_cursor/cycle/trails.scan_new", 0)
    assert skipped["TrailStore.scan_new"] == trails > 0, "the trail method was not exercised"
    # The one second-order effect, measured: in the same cycle, `rebalance`
    # re-tiers the rows promotion has just written, so the live pass moves
    # more rows than the frozen plan by at most the promoted rows. The skip
    # counter counts first-order writes; the cascade is declared here.
    cascade = live.get("triples/cycle/tiered_store.rebalance", 0) - skipped["TripleStore.rebalance"]
    print(f"T7 rebalance cascade from promoted rows: {cascade} (promotions {promotions})")
    assert 0 <= cascade <= promotions
    assert totals["frozen"]["skipped_writes_since_start"] + cascade == totals["live"]["learning_statements"] > 0


def test_companion_a_slow_host_clock_does_not_reach_t7(tmp_path, monkeypatch):
    """COMPANION (OQ-16 rules 1 and 3). A slow host that splits T7's arms,
    `_StallingHost` (the rig's clock, stalled 0.6 s from its 100th read), is
    installed around the whole of T7. Its premise first, because the plant is
    narrow: arms that read it, unclocked as T7 ran before W0b part 2b,
    disagree, and a code change that moves the reads so that it no longer
    splits them fails here by name rather than leaving a companion that
    cannot fail. Then the consequence: T7's own arms' first-order sums agree.
    Then the mechanism: they read the planted host zero times.

    W0b part 2b planted the host clock scaled x5 here, whose split moved with
    the host (on one machine, 547 against 530 in one run and 530 against 530
    in the next, where only the read count would have failed): the
    intermediate flag OQ-16 rule 3 forbids, replaced in part 5m.

    Pre-fix (law 16): with `_t7_arms`'s rig clock removed (the arms
    unclocked, as before W0b part 2b), this test fails on the consequence,
    `assert 506 == 517` (517 saved against 506 skipped, the stalled host read
    1001 times), the same in two runs; the run is in part 5m's commit body
    and RUN_LOG.md's W1 entry."""
    premise = _StallingHost()
    with premise.installed():
        unclocked = _t7_arms(tmp_path / "premise", monkeypatch, clocked=False)
    p_saved, p_skipped = _t7_saves(unclocked)
    print(f"\nT7c premise ({sys.platform}): arms reading the stalled host: saved {p_saved}, "
          f"skipped {p_skipped}, read {premise.reads} times")
    assert premise.reads > 0 and p_saved != p_skipped, (
        f"the planted host no longer splits arms that read it (saved {p_saved}, skipped {p_skipped}, "
        f"read {premise.reads} times): move T7_STALL_AT to a read that splits them before trusting this companion")
    slow = _StallingHost()
    with slow.installed():
        totals = _t7_arms(tmp_path / "fixed", monkeypatch)
    saved, skipped = _t7_saves(totals)
    live, by_method = totals["live"]["by_pair"], totals["frozen"]["by_method"]
    print(f"T7c ({sys.platform}) stalled host: read {slow.reads} times by T7's arms; saved {saved}, skipped {skipped}")
    assert skipped == saved > 0, "the arms' first-order sums disagree on the stalled host"
    assert by_method["TripleStore.fire"] == live.get("triples/turn/tiered_store.fire", 0) > 0
    assert slow.reads == 0, "the host's clock reached the comparison"


# ---------------------------------------------------------------------------
# T8 — trails defer, they do not lose
# ---------------------------------------------------------------------------

T8_TURNS = 30


def _trails(kb) -> list[tuple]:
    from backend.store import connect as store_connect
    from tests.conftest import store_target_for
    c = store_connect(store_target_for(kb))
    try:
        return c.execute("SELECT rel_a, rel_b, event_count, recurrence_count FROM relation_trails "
                         "ORDER BY rel_a, rel_b").fetchall()
    finally:
        c.close()


def _cited_by_turn(kb) -> list[tuple]:
    """For each record after the fixture's, in order, the relations it cites:
    the input trails are laid from (`TrailStore._cited_relations`'s rule)."""
    from backend.store import connect as store_connect
    from tests.conftest import store_target_for
    c = store_connect(store_target_for(kb))
    try:
        relation = dict(tuple(r) for r in c.execute("SELECT id, relation FROM triples"))
        out = []
        for (injected,) in c.execute("SELECT injected_json FROM lineage_records WHERE ts > ? "
                                     "ORDER BY ts, lineage_id", (FIXTURE_NEWEST_TS,)):
            out.append(tuple(sorted({relation[i["belief_id"]] for i in json.loads(injected)
                                     if i.get("cited") is not False and i.get("belief_id") is not None})))
        return out
    finally:
        c.close()


def _t8_arms(tmp_path, monkeypatch) -> dict:
    """T8's two arms with identical inputs, the clock included: each runs on
    the rig's deterministic clock (one microsecond a read, from
    `RIG_BASE_AFTER_FIXTURE`), installed around its fixture, boot, turns and
    cycles."""
    monkeypatch.setenv("NEXUS_RELATION_TRAILS", "on")
    out = {}
    for arm in ("never_frozen", "frozen_then_thawed"):
        sub = tmp_path / arm
        sub.mkdir()
        while_frozen = None
        with RigClock(base=RIG_BASE_AFTER_FIXTURE).installed():
            tr, fx, svc, client = _rig(sub, monkeypatch, "live")
            svc.trails._ensure_cursor()                 # the cursor exists before any turn, both arms
            if arm == "frozen_then_thawed":
                monkeypatch.setenv("NEXUS_LEARNING", "frozen")
            drive_turns_and_cycles(client, H, turns=T8_TURNS, every=10, questions=QUESTIONS)
            if arm == "frozen_then_thawed":
                while_frozen = _trails(fx.kb_path)
                monkeypatch.setenv("NEXUS_LEARNING", "live")
            client.post("/kairos/cycle?force_snapshot=false", headers=H)
            tr.__exit__(None, None, None)
        out[arm] = {"trails": _trails(fx.kb_path), "while_frozen": while_frozen,
                    "cited": _cited_by_turn(fx.kb_path)}
    return out


def _assert_t8(arms: dict) -> None:
    never, thawed = arms["never_frozen"], arms["frozen_then_thawed"]
    assert thawed["while_frozen"] == [], "nothing laid while frozen"
    assert len(never["cited"]) == len(thawed["cited"]) == T8_TURNS, "non-vacuity: one record per turn, after the cursor"
    # The premise, named apart from the claim: trails are laid from the
    # records, so the comparison means something only while the arms' records
    # cite the same relations. Learning that changes what a turn cites is the
    # learning the switch stops, not a lost trail.
    differ = [t + 1 for t, (a, b) in enumerate(zip(never["cited"], thawed["cited"])) if a != b]
    assert not differ, f"the arms' records cite different relations at turns {differ}: the premise failed"
    assert never["trails"] and thawed["trails"] == never["trails"]


def test_trails_defer_not_lose(tmp_path, monkeypatch):
    """Freezing trails delays them; it does not drop them.

    W1 (CI run 17, windows): the arms ran on the host's clock, and the
    clock is an input (W0; T7 in W0b part 2b). An edge does not transmit
    within 0.5 s of its last firing (`SynapticEdge.in_refractory`). On a fast
    host the turns land inside that window; on a slow one they do not, so
    the edges the last turn fired transmit, more edges fire, and they reach
    the promotion count. The never-frozen arm's cycle then promoted
    co-activation rows, which took injection slots in the turns after, so
    claims the frozen arm cited went uncited and the records differed:
    windows laid 21 events against 23. Emulated on Linux, the host clock
    scaled x20 gives 18 against 23 (x5 and a 15.625 ms tick do not split the
    arms), and the refractory disabled on the unscaled clock gives 15
    against 23; a deterministic host at 50 ms a read gives 18 against 23 on
    every host, the arms' records first differing at turn 21, after the
    second cycle (part 5i). Both arms now run on the rig's clock
    (`_t8_arms`), and the premise is asserted before the comparison."""
    arms = _t8_arms(tmp_path, monkeypatch)
    print(f"\nT8 trails never frozen: {arms['never_frozen']['trails']}; "
          f"frozen then thawed: {arms['frozen_then_thawed']['trails']}")
    _assert_t8(arms)


def test_companion_a_slow_host_clock_does_not_reach_t8(tmp_path, monkeypatch):
    """COMPANION (OQ-16 rules 1 and 3). A slow host that splits T8's arms,
    deterministic at 50 ms a read (`_slow_host`; part 4 planted the host
    clock scaled x20, whose pre-fix failure moved with the machine), is
    installed around the whole of T8. The consequence first: the arms'
    records cite the same relations and the thawed arm lays exactly the
    never-frozen arm's trails; then the mechanism: the arms read the planted
    host zero times.

    Pre-fix (law 16): with the arms unclocked as T8 ran them at 2d52f02,
    this test fails on the premise, deterministically — the arms' records
    cite different relations at turns [21, 24, 25, 28, 29], 18 trails against
    23; the run is in b0ad516's commit body (part 5i) and in RUN_LOG.md's W1
    entry."""
    slow = _slow_host()
    with slow.installed():
        arms = _t8_arms(tmp_path, monkeypatch)
    print(f"\nT8c ({sys.platform}) slow host at {SLOW_HOST_STEP} s a read: read {slow.reads} times by the arms; "
          f"never frozen {arms['never_frozen']['trails']}, thawed {arms['frozen_then_thawed']['trails']}")
    _assert_t8(arms)                                    # the consequence: T8's own assertions hold
    assert slow.reads == 0, "the host's clock reached the comparison"


# ---------------------------------------------------------------------------
# T9 — rebalance's effects are tier moves only
# ---------------------------------------------------------------------------

def test_rebalance_effects_are_tier_moves(tmp_path, monkeypatch):
    """What the freeze stops in `rebalance` is listed, not assumed."""
    monkeypatch.setenv("NEXUS_LEARNING", "live")
    kb = tmp_path / "kb.db"
    with WriteTrace() as tr:
        store = TripleStore(kb)
        bid = store.add(subject="old", relation="claim", object="cold", weight=0.05, tier=0,
                        granularity=Granularity.TOPIC, source="extraction")
        res = store.rebalance(now=__import__("time").time() + 400 * 86400)   # far enough for the band to move
        store.close()
    cols = tr.set_columns.get(("triples", "tiered_store.rebalance"), set())
    print(f"\nT9 rebalance result {res}; columns written: {sorted(cols)}")
    assert res["promoted"] + res["demoted"] >= 1, "non-vacuity: a tier moved"
    assert cols == {"tier"}
    assert bid


# ---------------------------------------------------------------------------
# T10 — one frozen turn keeps the record and stops the learning
# ---------------------------------------------------------------------------

def test_frozen_turn_keeps_record_stops_learning(tmp_path, monkeypatch):
    """On one frozen turn, the reinforcement counter does not move and the record's cited flags
    are still written; a freeze that stopped the record would pass T1 with fewer writes."""
    tr, fx, svc, client = _rig(tmp_path, monkeypatch, "frozen", "2026-09-05")
    before = {r["id"]: r["fire_count"] for r in svc.store.conn.execute("SELECT id, fire_count FROM triples")}
    r = client.post("/chat", json={"message": "what does the isolation step require?"}, headers=H)
    done = done_frames(r.text)[-1]
    tr.__exit__(None, None, None)
    after = {r["id"]: r["fire_count"] for r in svc.store.conn.execute("SELECT id, fire_count FROM triples")}
    rec = svc.lineage.get(done["lineage_id"])
    cited = [i for i in rec.injected if i.get("cited") is True]
    moved = [k for k in before if after.get(k) != before[k]]
    new_rows = sorted(set(after) - set(before))
    print(f"\nT10 injected {len(rec.injected)}, cited {len(cited)}, reinforcement {done['reinforcement']}, "
          f"fire_count moved: {moved}, rows the claim path added this turn: {new_rows}")
    assert cited, "non-vacuity: the answer cited an injected claim"
    assert all(i.get("cited") is not None for i in rec.injected), "the record carries the flag"
    assert moved == [], "the reinforcement counter did not move under frozen"
    assert all(after[k] == 0 for k in new_rows), "extraction's own rows arrive at fire_count 0 (the claim path stays live)"
    # B-43 — `reinforced` counts writes made (none under frozen); `skipped` the attempts the
    # freeze refused, equal to the counter's `fire` entry here because `_rig` reset it.
    assert done["reinforcement"]["reinforced"] == 0
    assert learning.skipped_writes()["by_method"].get("TripleStore.fire", 0) == done["reinforcement"]["skipped"] > 0


# ---------------------------------------------------------------------------
# T11 — a frame is named by its module, not by its file path
# ---------------------------------------------------------------------------

def _frame(module: str, file: str, func: str):
    """A synthetic frame: what a live one carries and nothing else."""
    import types
    return types.SimpleNamespace(f_globals={"__name__": module, "__file__": file},
                                 f_code=types.SimpleNamespace(co_name=func, co_filename=file),
                                 f_lineno=1, f_back=None)


# The operator's stack as Windows renders it. Nothing in tests/_fz_trace.py
# names a backslash, a drive letter, or any of these paths.
WINDOWS_STACK = [
    _frame("_pytest.python", r"C:\Users\op\Nexus\.venv\Lib\site-packages\_pytest\python.py", "pytest_pyfunc_call"),
    _frame("tests.test_fz_freeze", r"C:\Users\op\Nexus\tests\test_fz_freeze.py", "test_rebalance_effects_are_tier_moves"),
    _frame("backend.loop.kairos", r"C:\Users\op\Nexus\backend\loop\kairos.py", "_run_cycle_body"),
    _frame("backend.kb.tiered_store", r"C:\Users\op\Nexus\backend\kb\tiered_store.py", "rebalance"),
]


def test_frame_named_by_module_not_path(monkeypatch, tmp_path):
    """T9's read, with the stack the operating instance produces. Before the fix the chain was
    built from `/backend/` in the filename, so every Windows frame was dropped and the UPDATE
    read ("triples", "other:?", "?") with no set_columns entry — the exact symptom reported.
    (W3b: the statements go through the store interface, which is what the trace listens
    to; a raw driver connection is outside it by design.)"""
    from backend.store import connect as store_connect
    with WriteTrace() as tr:
        # `raising=False`: on the pre-fix helper there is no `_stack` to replace, the real
        # (Linux, test-module) stack is read, and the assertion below fails on the symptom
        # rather than on a missing seam.
        monkeypatch.setattr(tr, "_stack", lambda: list(WINDOWS_STACK), raising=False)
        from tests.conftest import store_target_for
        conn = store_connect(store_target_for(tmp_path / "t11.db"))
        conn.execute("CREATE TABLE triples (id INTEGER, tier INTEGER)")
        conn.execute("INSERT INTO triples VALUES (1, 1)")
        tr.clear()
        conn.execute("UPDATE triples SET tier = 2 WHERE id = 1")
        conn.close()
    pairs = tr.pairs()
    print(f"\nT11 pairs from a Windows stack: {dict(pairs)}; set_columns: {dict(tr.set_columns)}")
    assert pairs == {("triples", "cycle", "tiered_store.rebalance"): 1}, pairs
    assert tr.set_columns.get(("triples", "tiered_store.rebalance")) == {"tier"}
    # The normaliser alone, fed the synthetic frames directly: the pytest and test frames are
    # dropped by name, the backend frames are kept by name, and the path is never read.
    chain = _fz_trace.chain_of(WINDOWS_STACK)
    assert chain == (("backend", "kairos", "_run_cycle_body"), ("backend", "tiered_store", "rebalance")), chain
    assert _fz_trace.host_of(chain) == "cycle" and _fz_trace.method_of(chain) == "tiered_store.rebalance"
    # The one path the normaliser still reads — a script run as `__main__` — is parsed in the
    # flavour it was written in, on either host.
    for file in (r"C:\Users\op\Nexus\scripts\archive_pregate.py", "/home/op/Nexus/scripts/archive_pregate.py"):
        assert _fz_trace.frame_tag(_frame("__main__", file, "main")) == ("scripts", "archive_pregate", "main"), file
    assert _fz_trace.frame_tag(_frame("__main__", r"C:\Users\op\Nexus\.venv\Lib\site-packages\pytest\__main__.py", "<module>")) is None
    # A fixture module counts as a writer only when asked for, by name.
    fx = _frame("tests.fixtures.fz_plant_writer", r"C:\Users\op\Nexus\tests\fixtures\fz_plant_writer.py", "plant_writer")
    assert _fz_trace.frame_tag(fx) is None
    assert _fz_trace.frame_tag(fx, ("tests.fixtures.",)) == ("backend", "fz_plant_writer", "plant_writer")
