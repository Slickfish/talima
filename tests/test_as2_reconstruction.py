"""AS-2 — is standing at a past date re-derivable from the record? (tier-1 establish)

THE PRE-REGISTERED EXPECTATION (BUILDSPEC_AS2 S0, written before anything
here was computed): on the rig, for every claim after every turn, the stored
`fire_count` EQUALS the number of lineage records up to that turn in which the
claim is in the injected set with `cited = true` AND `learning_mode` is not
`frozen`. Equality everywhere. One inequality that no named mechanism explains
is a stop: standing at D is then declared *not re-derivable from the record*
and AS-1's fixed sentence stands.

T1   reconstruction on CE's rig — 30 live turns, 10 frozen, 10 live — equals
     the stored counter for every claim after every turn, WITH the
     learning_mode term (the table is printed)
T1c  companion: WITHOUT the term the reconstruction over-counts by exactly the
     frozen block's cited hits, by claim — the term is load-bearing
T2   the call graph of `TripleStore.fire`, derived by AST over the tracked
     backend, equals the answered-for set; a planted second caller that is
     not driven by the cited set is named
T3   the prominence projection's inputs, derived by AST from the two
     functions, equal the answered-for list, each classified as timed-event-
     backed or not; the list fails if an input is gained or lost
T4   item 3's print: the projection's wall-time input, and whether 0.00 after
     17 days frozen follows from it, computed from the shipped constants

Nothing under backend/ changes. Exact pins are allowed: tier 1.
"""
from __future__ import annotations

import ast
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from backend.kb import tiered_store as TS
from tests._tracked import tracked_backend
from tests.fixtures.ce_rig import CERig

ROOT = Path(__file__).resolve().parent.parent
LIVE_A, FROZEN, LIVE_B = 30, 10, 10


# ---------------------------------------------------------------------------
# the reconstruction helper — the S0 rule, from the records alone
# ---------------------------------------------------------------------------

def reconstruct(records, *, learning_term: bool = True) -> dict[int, int]:
    """{belief_id: count of records in which it was injected with cited=true
    (and, with the term, learning_mode != 'frozen')}. Reads the record and
    nothing else: no store, no counter, no clock."""
    counts: dict[int, int] = {}
    for rec in records:
        if learning_term and rec.learning_mode == "frozen":
            continue
        for item in rec.injected:
            if item.get("cited") and item.get("belief_id") is not None:
                counts[int(item["belief_id"])] = counts.get(int(item["belief_id"]), 0) + 1
    return counts


def stored_counts(store) -> dict[int, int]:
    return {t.id: t.fire_count for t in store.all()}


@pytest.fixture(scope="module")
def rig_run(tmp_path_factory):
    """50 turns on CE's rig: 30 live, 10 frozen, 10 live. After every turn the
    stored counter and the record are snapshotted; nothing is adjusted."""
    prev = {k: os.environ.get(k) for k in ("NEXUS_LEARNING", "NEXUS_LEARNING_FROZEN_SINCE", "NEXUS_CALIBRATION_LOG")}
    root = tmp_path_factory.mktemp("as2")
    os.environ["NEXUS_CALIBRATION_LOG"] = str(root / "cal.jsonl")
    os.environ.pop("NEXUS_LEARNING", None)
    rig = CERig(root / "rig", pairs=0, answer_chars=800, kb_lines=17, user_chars=76)
    snapshots = []                      # per turn: (turn, mode, stored, with_term, without_term, record)
    try:
        for turn in range(1, LIVE_A + FROZEN + LIVE_B + 1):
            mode = "frozen" if LIVE_A < turn <= LIVE_A + FROZEN else "live"
            if mode == "frozen":
                os.environ["NEXUS_LEARNING"] = "frozen"
            else:
                os.environ.pop("NEXUS_LEARNING", None)
            row = rig.run_one()
            records = [rig.lineage._row_to_record(r) for r in rig.lineage._all_rows()]
            rec = rig.lineage.get(row.lineage_id)
            snapshots.append({"turn": turn, "mode": mode, "stored": stored_counts(rig.store),
                              "with_term": reconstruct(records), "without_term": reconstruct(records, learning_term=False),
                              "record_mode": rec.learning_mode, "cited_ids": sorted(int(i["belief_id"]) for i in rec.injected if i.get("cited")),
                              "injected": len(rec.injected), "done_cited": row.cited})
        claims = {t.id: f"{t.subject} {t.relation} {t.object}" for t in rig.store.all()}
    finally:
        rig.close()
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return {"snapshots": snapshots, "claims": claims}


def _table(snapshots, claims, key) -> str:
    ids = sorted(claims)
    head = "turn mode   " + " ".join(f"{i:>3d}" for i in ids)
    lines = [head]
    for s in snapshots:
        lines.append(f"{s['turn']:>4d} {s['mode']:<6s} " + " ".join(f"{s[key].get(i, 0):>3d}" for i in ids))
    return "\n".join(lines)


def test_t1_reconstruction_equals_the_stored_counter_on_every_claim_after_every_turn(rig_run):
    snaps, claims = rig_run["snapshots"], rig_run["claims"]
    print(f"\nT1 ({sys.platform}) stored fire_count per claim id, {len(snaps)} turns "
          f"(live {LIVE_A}, frozen {FROZEN}, live {LIVE_B}); 17 claims injected, "
          f"{snaps[0]['done_cited']} cited per turn:\n" + _table(snaps, claims, "stored"))
    inequalities = []
    for s in snaps:
        for cid in claims:
            if s["stored"].get(cid, 0) != s["with_term"].get(cid, 0):
                inequalities.append((s["turn"], cid, s["stored"].get(cid, 0), s["with_term"].get(cid, 0)))
    print(f"T1 inequalities WITH the learning_mode term: {inequalities}")
    # Non-vacuity: the rig cites, the counter moves, the frozen block is recorded as frozen.
    assert all(s["done_cited"] > 0 for s in snaps), "the rig cites nothing; the comparison is vacuous"
    assert all(s["injected"] == 17 for s in snaps)
    assert [s["record_mode"] for s in snaps] == [None] * LIVE_A + ["frozen"] * FROZEN + [None] * LIVE_B
    assert max(snaps[-1]["stored"].values()) == LIVE_A + LIVE_B, "the cited claims fired once per live turn"
    assert inequalities == [], "NOT RE-DERIVABLE: an inequality the named mechanisms do not explain"


def test_t1c_without_the_learning_term_the_overcount_is_exactly_the_frozen_blocks_cited_hits(rig_run):
    snaps, claims = rig_run["snapshots"], rig_run["claims"]
    frozen_hits: dict[int, int] = {}
    for s in snaps:
        if s["mode"] == "frozen":
            for cid in s["cited_ids"]:
                frozen_hits[cid] = frozen_hits.get(cid, 0) + 1
    print(f"\nT1c ({sys.platform}) frozen block's cited hits by claim: {frozen_hits}")
    print("T1c reconstruction WITHOUT the term:\n" + _table(snaps, claims, "without_term"))
    over = {}
    for s in snaps:
        for cid in claims:
            d = s["without_term"].get(cid, 0) - s["stored"].get(cid, 0)
            if d:
                over[(s["turn"], cid)] = d
    # Before the frozen block nothing differs; during it the difference grows by one per cited hit;
    # after it the difference is exactly the frozen block's hits and stays.
    assert all(d == 0 for s in snaps if s["turn"] <= LIVE_A for d in [s["without_term"].get(c, 0) - s["stored"].get(c, 0) for c in claims])
    last = snaps[-1]
    final_over = {cid: last["without_term"].get(cid, 0) - last["stored"].get(cid, 0) for cid in claims
                  if last["without_term"].get(cid, 0) != last["stored"].get(cid, 0)}
    assert final_over == frozen_hits and frozen_hits, (final_over, frozen_hits)
    assert sum(frozen_hits.values()) == sum(len(s["cited_ids"]) for s in snaps if s["mode"] == "frozen")
    assert over, "the term never mattered; the companion proves nothing"


# ---------------------------------------------------------------------------
# T2 — the call graph of TripleStore.fire, derived by AST
# ---------------------------------------------------------------------------

def fire_call_sites(sources: dict[str, str]) -> list[tuple[str, int, str]]:
    """Every `<something>.fire(<arg>)` call in the given {path: source} map,
    excluding the graph's own node/edge `.fire()` (no argument: nodes.py's
    `fire(self)` takes none; the store's takes a triple id)."""
    out = []
    for path, src in sources.items():
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "fire" and node.args):
                out.append((path, node.lineno, ast.unparse(node)))
    return sorted(out)


ANSWERED_FIRE_CALLERS = {
    # the one production caller: the F.5 reinforcement loop, over `hits`, gated
    # on membership in `reinforce_ids` — the cited set under the shipped mode
    ("backend/loop/agentic_loop.py", "retriever.store.fire(rt.triple.id)"),
}


def test_t2_the_fire_call_graph_equals_the_answered_for_set():
    sources = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in tracked_backend()}
    sites = fire_call_sites(sources)
    print(f"\nT2 ({sys.platform}) TripleStore.fire call sites in the tracked backend: {sites}")
    assert {(p, call) for p, _ln, call in sites} == ANSWERED_FIRE_CALLERS, sites
    # And the one caller is driven by the cited set: the loop's gate names `reinforce_ids`,
    # which is `cited_belief_ids` under the shipped mode, computed by `cited_ids(hits, …)`.
    loop = sources["backend/loop/agentic_loop.py"]
    assert "if rt.triple.id in reinforce_ids:" in loop
    assert 'cited_belief_ids if reinforce_mode_used == "citation"' in loop
    assert '"cited":               rt.triple.id in cited_belief_ids,' in loop, "the record's flag is the same set"


def test_t2c_a_planted_second_caller_not_driven_by_the_cited_set_is_named():
    planted = {"backend/kb/booster.py": "def boost(store, ids):\n    for i in ids:\n        store.fire(i)\n"}
    sources = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in tracked_backend()}
    sites = fire_call_sites({**sources, **planted})
    assert ("backend/kb/booster.py", 3, "store.fire(i)") in sites
    assert {(p, call) for p, _ln, call in sites} != ANSWERED_FIRE_CALLERS, "the plant was not named"


# ---------------------------------------------------------------------------
# T3 — prominence as a function: its inputs, derived by AST
# ---------------------------------------------------------------------------

def projection_inputs() -> dict[str, set[str]]:
    """The names each projection function reads: attributes of its triple
    argument (`self` / `triple`), its parameters, and the functions it calls.
    Derived from the source of `Triple.current_weight` and
    `effective_prominence`, so a new input fails the pin."""
    tree = ast.parse(Path(TS.__file__).read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("current_weight", "effective_prominence"):
            params = {a.arg for a in node.args.args}
            attrs = {f"{n.value.id}.{n.attr}" for n in ast.walk(node)
                     if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in ("self", "triple")}
            calls = {ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}
            out[node.name] = params | attrs | calls
    return out


# Every input, answered for. `timed` says whether a timed event history stands
# behind it in the store (one row per change with a timestamp) — AS-1's A-1.
PROJECTION_INPUTS = {
    "current_weight": {
        "self":                       "the row",
        "now":                        "WALL TIME: defaults to time.time() at the call; the one input that is not stored at all",
        "self.weight":                "NOT TIMED: a monotone MAX written by add()'s upsert; no history (AS-1 A-1)",
        "self.last_fired":            "NOT TIMED as a column; fire() and add()'s re-assertion both overwrite it. fire's writes are re-derivable "
                                      "from the record (the ts of the last live cited record); add()'s refresh on re-assertion is not recorded",
        "self.source":                "immutable: part of the row key",
        "time.time":                  "the wall clock",
        "decay_rate_for_source":      "a constant per evidence class, env-overridable at call time; versioned by code, not by the store",
        "max":                        "builtin",
    },
    "effective_prominence": {
        "triple":                     "the row",
        "now":                        "WALL TIME, passed through to current_weight",
        "adjustment":                 "TIMED: the summed recalibration_events for the belief, one sealed row per act",
        "triple.current_weight":      "the projection above",
        "triple.source":              "immutable: part of the row key",
        "arrival_scale":              "a constant per evidence class, env-overridable at call time",
        "min":                        "builtin",
        "max":                        "builtin",
    },
}
NOT_TIMED = ("self.weight", "self.last_fired", "now")


def test_t3_the_projections_inputs_are_the_answered_for_list():
    got = projection_inputs()
    print(f"\nT3 ({sys.platform}) prominence inputs, derived: {json.dumps({k: sorted(v) for k, v in got.items()})}")
    assert set(got) == set(PROJECTION_INPUTS)
    for fn, inputs in got.items():
        assert inputs == set(PROJECTION_INPUTS[fn]), (fn, inputs ^ set(PROJECTION_INPUTS[fn]))
    assert "self.fire_count" not in got["current_weight"] and "triple.fire_count" not in got["effective_prominence"], \
        "the projection does not read fire_count at all"
    assert "self.tier" not in got["current_weight"] and "triple.tier" not in got["effective_prominence"], \
        "tier is not an input of prominence (it orders retrieval, by rebalance's band)"
    for name in NOT_TIMED:
        assert "NOT TIMED" in PROJECTION_INPUTS["current_weight"][name] or "WALL TIME" in PROJECTION_INPUTS["current_weight"][name]


def test_t4_the_wall_time_input_and_what_0_00_after_17_days_frozen_follows_from():
    """Item 3's extra print. On the operator's instance (frozen since 2026-10-01)
    every injected claim displays prominence 0.00 after 17 days. The projection's
    wall-time input is `now` (time.time() at the call); frozen, fire() writes
    nothing, so `last_fired` never refreshes and `days` grows with the clock.
    The premise "0.00 follows from 17 frozen days" was checked, not assumed:
    at the shipped rates a claim at high-water 1.00 is NOT at 0.00 after 17
    days in any class — the display follows from the clock only together with
    a `last_fired` that predates the freeze, or a high-water at or under
    0.85 (extraction, consolidation). B-46 saw 0.00 on the FIRST frozen turn."""
    rates = {cls: TS.decay_rate_for_source(src) for cls, src in
             (("extraction", "extraction"), ("asserted", "human_assertion"), ("external", "wikidata"),
              ("consolidation", "consolidation"), ("procedural", "procedural"))}
    lines = []
    for cls, rate in rates.items():
        days_to_zero_from_1 = (1.0 / rate) if rate else float("inf")
        after_17 = max(0.0, 1.0 - rate * 17)
        lines.append(f"  {cls:<14s} rate {rate:.3f}/day  high-water 1.00 reaches 0.00 after {days_to_zero_from_1:6.1f} days; "
                     f"at 17 days: {after_17:.2f}; highest high-water that reads 0.00 at 17 days: {min(1.0, rate * 17):.2f}")
    print(f"\nT4 ({sys.platform}) wall-time input: current_weight(now) with now = time.time() at the call "
          f"(tiered_store.py, `now = now or time.time()`), days = (now - last_fired) / DAY_SECONDS; fire() is the only "
          f"refresh of last_fired besides add()'s re-assertion, and frozen writes neither\n" + "\n".join(lines))
    seventeen = 17 * TS.DAY_SECONDS

    def row(source, weight):
        return TS.Triple(id=1, subject="s", relation="r", object="o", weight=weight, tier=3, granularity="topic",
                         source=source, fire_count=0, created_at=0.0, last_fired=0.0)
    # The premise, found wrong: the fastest class at 1.00 is 0.15 at 17 days, 0.00 at 20.
    assert round(row("extraction", 1.0).current_weight(now=seventeen), 2) == 0.15
    assert row("extraction", 1.0).current_weight(now=20 * TS.DAY_SECONDS) == 0.0
    # What the display does follow from: an extraction or consolidation row at high-water <= 0.85
    # reads 0.00 at 17 days; anything slower needs a last_fired older than the freeze.
    assert row("extraction", 0.85).current_weight(now=seventeen) == 0.0
    assert row("consolidation", 0.85).current_weight(now=seventeen) == 0.0
    assert row("human_assertion", 1.0).current_weight(now=seventeen) > 0.9
    # The same row, fired 20 days before the freeze began, is 0.00 on the freeze's first day.
    assert row("extraction", 1.0).current_weight(now=20 * TS.DAY_SECONDS) == 0.0
    assert rates["extraction"] * 17 == pytest.approx(0.85)


def _plant_lineage(path: Path, rows: list[tuple[float, str | None, bool]]) -> list[str]:
    """A lineage fixture with planted timestamps: (ts, learning_mode, cited)
    per record, written through LineageStore and then re-timed by SQL — a
    fixture, so its hashes are not verified."""
    from backend.memory.lineage import LineageStore
    store = LineageStore(path)
    ids = []
    for i, (ts, mode, cited) in enumerate(rows):
        rec = store.record_turn(query=f"q{i}", injected=[{"belief_id": 1, "weight_at_injection": 0.5,
                                                         "tier_at_injection": 1, "cited": cited}],
                                contradictions=[], model_id="m", response_text=f"a{i}", prompt_prefix="p",
                                learning_mode=mode)
        store.conn.execute("UPDATE lineage_records SET ts = ? WHERE lineage_id = ?", (ts, rec.lineage_id))
        ids.append(rec.lineage_id)
    store.conn.commit()
    store.close()
    return ids


@pytest.mark.sqlite_only("scripts/as2_instance_windows.py reads the SQLite file through the read-only open (law 15); the URL form is W3b part 3 (D9, T8)")
def test_t5_the_instance_script_opens_read_only_prints_the_path_first_and_finds_the_windows_by_query(tmp_path):
    """The read-only instance query, on a planted copy (never data/): the first
    line is the path; a write is refused; the frozen-by-assertion records and
    the LS boundary are found by query, with their ids printed."""
    from tests import _proc
    from scripts import as2_instance_windows as W
    day = 86400.0
    since = W.frozen_since_epoch("2026-10-01")
    ids = _plant_lineage(tmp_path / "kb.db", [
        (since - 30 * day, None, True),          # pre-freeze, pre-LS (the earliest record)
        (since - 2 * day, None, True),
        (since + 0.5 * day, None, True),         # frozen by assertion: after the date, before LS
        (since + 1.0 * day, None, False),        # frozen by assertion
        (since + 2.0 * day, "frozen", True),     # the LS boundary: first record carrying the column
        (since + 3.0 * day, "frozen", True),
        (since + 4.0 * day, None, True),         # live again (NULL = live), AFTER the LS boundary: not by-assertion
    ])
    conn = W.open_read_only(tmp_path / "kb.db")
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|readonly database"):
        conn.execute("UPDATE lineage_records SET ts = 0")
    f = W.facts(conn, "2026-10-01")
    conn.close()
    assert f["earliest_record"]["lineage_id"] == ids[0] and f["records"] == 7
    assert [r["lineage_id"] for r in f["frozen_by_assertion"]["records"]] == [ids[2], ids[3]]
    assert f["frozen_by_assertion"]["count"] == 2
    assert f["ls_boundary"]["first_record_with_learning_mode"]["lineage_id"] == ids[4]
    assert f["ls_boundary"]["first_record_frozen"]["lineage_id"] == ids[4]
    assert f["ls_boundary"]["records_frozen"] == 2 and f["cited"]["records_with_a_cited_injection"] == 6
    cp = _proc.run([sys.executable, "scripts/as2_instance_windows.py", "--db", str(tmp_path / "kb.db"), "--selftest"],
                        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    out = cp.stdout.splitlines()
    print(f"\nT5 ({sys.platform}) script output:\n" + "\n".join("   " + ln for ln in out))
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert out[0].startswith("database: ") and (tmp_path / "kb.db").resolve().as_posix() in out[0]
    # W1 part 5f (SF4): the exact line, not its prefix — a selftest that
    # printed "write refused" without attempting a write passed the prefix.
    assert out[1] == "selftest: write refused (DatabaseError: not authorized)", out[1]
    assert any(ids[2] in ln for ln in out) and any(ids[3] in ln for ln in out) and any(ids[4] in ln for ln in out)
    assert any(ln.startswith("4. fire semantics by code version") for ln in out)
    # Nothing was written: the planted file's bytes are unchanged by the run.
    assert not any("SELFTEST FAILED" in ln for ln in out)
