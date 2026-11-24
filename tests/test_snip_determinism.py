"""SNIP-DET — snip's count varied between identical runs on win32; the pins.

THE CAUSE, ESTABLISHED (file and line on the pre-fix code):
    snip's count is `len(session_memory) - 10`, and `session_memory` is every
    hippocampus edge (stage 7). The edge count varied because the Hebbian
    top-20 varied, because the seed order varied, because the retriever's
    hit order varied: `scored.sort(key=(tier, -effective_prominence(...)))`
    (`backend/kb/retriever.py:267`) had no tie-break, and
    `effective_prominence` carries `Triple.current_weight(now)`
    (`backend/kb/tiered_store.py:353`), a per-day decay over the wall clock
    since `last_fired`. Seventeen rows added in one loop are identical in
    every stored respect and differ by microseconds of age, so on a
    microsecond clock (linux) they ranked newest-first, every run; on a
    coarse clock (win32, ~1-16 ms ticks) rows inside one tick tied exactly
    and fell to SQLite's unspecified tie order, rows straddling a tick did
    not, and the tick fell at a different row each run. Hash ordering was
    tested and refuted: six PYTHONHASHSEED values gave identical rows.
    `search_terms` (`tiered_store.py:945`) had the same gap in SQL —
    `ORDER BY tier, weight LIMIT` with no id — and `graph_collapse`
    (`compaction.py`) iterated `hippocampus.nodes`, a set, in hash order.

THE FIX: a total order at each site. The retriever ranks on
`ranking_prominence` — effective prominence rounded to six decimals, then
the id — leaving `rt.score` (the record's `weight_at_injection`) untouched;
`search_terms` orders by id after weight in SQL and in Python; snip's key
is the decayed weight rounded to six decimals, then the edge key;
collapse's candidates are sorted. (A first draft read the clock at whole
seconds; it moved the boundary rather than removing it and was reverted —
the wording here said "whole seconds" until W0 corrected it.) The first
four tests here FAIL on the pre-fix code.

PLATFORM is stamped on every printed line (sys.platform).

W0 (the run to production, 31 October) — CI's first Windows runs found the
rig still read the HOST's clock: two identical rigs differed at turn 9 by
one token, and the real-against-frozen and retrieval-order tests failed in
one or both runs. Not a broken total order: rows written in one loop on a
host with slow writes span long enough that a six-decimal rounding boundary
falls between them at a retrieval, and the tie splits by rule. The rig now
owns a deterministic clock (`tests/fixtures/ce_rig.py`, THE CLOCK IS AN
INPUT TOO); the microsecond arms below are explicit instead of whatever the
host gives; a slow host clock is planted and must not reach the rig; and
the boundary case itself is planted and named, so it is declared rather
than lucky.
"""
from __future__ import annotations

import ast
import json
import os
from tests import _proc
import sys
import tempfile
import time
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever, ranking_prominence
from backend.kb.tiered_store import TripleStore, arrival_scale, decay_rate_for_source
from backend.loop import compaction as comp_mod
from backend.loop.compaction import graph_collapse, snip
from backend.loop.state import NexusLoopState
from backend.memory.hippocampus import Hippocampus
from backend.cortex.edges import SynapticEdge
from tests.fixtures.ce_rig import RIG_CLOCK_BASE, CERig, RigClock

ROOT = Path(__file__).resolve().parent.parent
CHILD = ROOT / "tests" / "_snip_rows.py"
SEEDS = ("0", "1", "2")
TRUE_TIME = time.time          # the host's clock, captured at import; put back after each planted clock


def _rig_rows(root: Path, turns: int = 12, clock="rig") -> list:
    rig = CERig(root, pairs=20, answer_chars=800, kb_lines=17, user_chars=76, clock=clock)
    rows = [r.as_tuple() for r in rig.run(turns)]
    rig.close()
    return rows


# ---------------------------------------------------------------------------
# 1. The operator's requirement: identical rows under ≥3 hash seeds, in
#    subprocesses, two rigs per process
# ---------------------------------------------------------------------------

def test_rows_identical_under_three_hash_seeds_in_subprocesses():
    """Three interpreters, each under its own PYTHONHASHSEED, each running two
    identical rigs (pairs=20, answer 800, 17 KB lines, 76-char question, 12
    turns): every run's rows equal, within each process and across the
    three. The seeds also cover collapse's candidate order, which was hash
    order before the fix."""
    results = {}
    for seed in SEEDS:
        env = {**os.environ, "PYTHONHASHSEED": seed}
        cp = _proc.run([sys.executable, str(CHILD), "12"], cwd=str(ROOT), env=env,
                            capture_output=True, encoding="utf-8", errors="replace", timeout=900)
        assert cp.returncode == 0, f"seed {seed}: child failed:\n{cp.stderr[-2000:]}"
        results[seed] = json.loads(cp.stdout.strip().splitlines()[-1])
    for seed, r in results.items():
        assert r["runs"][0] == r["runs"][1], f"seed {seed}: two identical rigs in one process differ"
    first = results[SEEDS[0]]["runs"][0]
    for seed in SEEDS[1:]:
        assert results[seed]["runs"][0] == first, f"seed {seed} differs from seed {SEEDS[0]}"
    fires = [(i + 1, [(s[0], s[3]) for s in row[2]]) for i, row in enumerate(first) if row[2]]
    print(f"\nSNIP-DET-1 ({sys.platform}) seeds {SEEDS}: identical; fires {fires}")
    assert fires, "non-vacuity: the rig fired"


# ---------------------------------------------------------------------------
# 2. The cause, as a consequence: the rows do not depend on the clock
# ---------------------------------------------------------------------------

def test_rows_do_not_depend_on_the_clock(tmp_path, monkeypatch):
    """FAILS PRE-FIX. One rig on a clock that advances one microsecond a read
    (rows written microseconds apart, as a fast host writes them), one on a
    frozen clock (every row's `created_at`/`last_fired` identical, so nothing
    can rank by age): the same rows, turn for turn. Pre-fix: 277 edges and
    snip 267 under the real clock against 207 and 197 under the frozen one.

    W0: the first arm was the HOST's clock, and on a Windows CI runner that
    clock's spread across one loop of writes crossed a rounding boundary, so
    the test failed for a reason that is the rule working (the planted case
    is `test_a_write_spread_across_a_rounding_boundary_splits_the_tie_by_rule`).
    The microsecond arm is explicit now, so the claim — microseconds of age
    decide nothing the model sees — is tested on every host."""
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    microseconds = _rig_rows(tmp_path / "us", clock=RigClock())
    frozen = _rig_rows(tmp_path / "frozen", clock=RigClock(step=0.0))
    r_fires = [(i + 1, [(s[0], s[3]) for s in row[2]]) for i, row in enumerate(microseconds) if row[2]]
    f_fires = [(i + 1, [(s[0], s[3]) for s in row[2]]) for i, row in enumerate(frozen) if row[2]]
    print(f"\nSNIP-DET-2 ({sys.platform}) microsecond clock fires {r_fires[:3]}...; frozen clock fires {f_fires[:3]}...")
    assert microseconds == frozen, "the rows depend on microseconds of age"


def test_companion_a_slow_host_clock_does_not_reach_the_rig(tmp_path, monkeypatch):
    """The rig owns its clock. With the HOST's `time.time` replaced by one
    that advances 35 ms a read — a host on which the pre-W0 rig, which read
    the host clock, differed from the frozen rows from turn 2 (snip 224
    against 197; `RUN_LOG.md`, W0) — the rig's rows are the frozen rows, and
    the slow clock is never read while the rig works."""
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    frozen = _rig_rows(tmp_path / "frozen", clock=RigClock(step=0.0))
    slow_host = RigClock(step=0.035)
    monkeypatch.setattr(time, "time", slow_host)
    rows = _rig_rows(tmp_path / "slow_host")              # the rig's default clock
    monkeypatch.setattr(time, "time", TRUE_TIME)
    print(f"\nSNIP-DET-2c ({sys.platform}) slow host clock read {slow_host.reads} times while the rig ran; "
          f"rows equal to frozen: {rows == frozen}")
    assert slow_host.reads == 0, "the rig read the host's clock"
    assert rows == frozen, "the host's clock reached the rig's rows"


# ---------------------------------------------------------------------------
# 3. snip's own order: planted ties and planted microsecond ages
# ---------------------------------------------------------------------------

def _state(rows) -> NexusLoopState:
    return NexusLoopState(session_memory=tuple(rows))


def test_snip_keeps_the_same_rows_in_any_input_order():
    """FAILS PRE-FIX. Thirty session rows, every weight 0.5, every
    `last_fired` identical: an exact tie. Offered in two orders, snip keeps
    the same ten rows — by the edge key, not by where they sat."""
    now = time.time()
    rows = [(f"n{i:02d}", f"m{i:02d}", 0.5, now) for i in range(30)]
    a, _ = snip(_state(rows))
    b, _ = snip(_state(list(reversed(rows))))
    kept_a, kept_b = sorted(a.session_memory), sorted(b.session_memory)
    print(f"\nSNIP-DET-3a ({sys.platform}) kept {[r[0] for r in kept_a]}")
    assert kept_a == kept_b, "snip's kept set followed the input order"
    assert [r[0] for r in kept_a] == [f"n{i:02d}" for i in range(10)]


def test_snip_ignores_microseconds_of_age(monkeypatch):
    """FAILS PRE-FIX. The same thirty rows, `last_fired` stepped by one
    microsecond in name order, so that a microsecond clock ranks n29 the
    newest and a pre-fix snip keeps n20..n29. snip's order rounds the decayed
    weight to six decimals: the ten kept are the ten lowest edge keys,
    n00..n09, as in the exact-tie case.

    W0: the instant snip reads is planted, one second after the rows. It
    used to be the host's clock, and at about one instant in 8,600 (the
    verifier's sweep over 450,001 planted instants: 58 kept the wrong set)
    a six-decimal boundary falls inside the thirty microseconds and the
    tie splits by rule, on any platform. At the planted instant the thirty
    decayed weights span 1.2e-10 and sit 2.7e-7 from the nearest boundary."""
    base = RIG_CLOCK_BASE + 0.1
    rows = [(f"n{i:02d}", f"m{i:02d}", 0.5, base + i * 1e-6) for i in range(30)]
    monkeypatch.setattr(time, "time", RigClock(base=base + 1.0, step=0.0))
    s, removed = snip(_state(rows))
    monkeypatch.setattr(time, "time", TRUE_TIME)
    kept = sorted(r[0] for r in s.session_memory)
    print(f"\nSNIP-DET-3b ({sys.platform}) removed {removed}, kept {kept}")
    assert removed == 20
    assert kept == [f"n{i:02d}" for i in range(10)], "microseconds of age decided what the model keeps"


# ---------------------------------------------------------------------------
# 4. The retriever's order: planted microsecond ages
# ---------------------------------------------------------------------------

def _seventeen(root: Path) -> TripleStore:
    store = TripleStore(root / "kb.db")
    for i in range(1, 18):
        store.add(subject=f"concept{i}", relation="improves", object=f"outcome{i}", weight=0.88,
                  tier=1, granularity=Granularity.TOPIC, source="asserted")
    return store


def test_retrieval_order_ignores_microseconds_of_age(tmp_path, monkeypatch):
    """FAILS PRE-FIX. Seventeen rows, identical in every stored respect, added
    in one loop on a clock advancing one microsecond a read (rows microseconds
    apart, as a fast host writes them) and, separately, under a frozen clock
    (identical timestamps), each retrieved on its own clock. The retriever
    returns the same order for both: by id, never by which was newest by a
    microsecond. `rt.score` still carries each row's real prominence.

    W0: the first arm was the host's clock; a Windows CI runner wrote the
    seventeen rows far enough apart that a six-decimal boundary fell between
    them before the retrieval, and the test failed in one CI run of two
    (`concept4`..`concept17` before `concept1`..`concept3`). The microsecond
    clock is explicit now; the boundary case is planted in the next test."""
    q = "concept improves outcome"
    monkeypatch.setattr(time, "time", RigClock())
    live = _seventeen(tmp_path / "live")
    live_hits = TripleRetriever(live).retrieve(q, top_k=20)
    monkeypatch.setattr(time, "time", RigClock(step=0.0))
    frozen = _seventeen(tmp_path / "frozen")
    frozen_hits = TripleRetriever(frozen).retrieve(q, top_k=20)
    monkeypatch.setattr(time, "time", TRUE_TIME)
    live_order = [h.triple.subject for h in live_hits]
    print(f"\nSNIP-DET-4 ({sys.platform}) microsecond-clock order {live_order}")
    assert live_order == [h.triple.subject for h in frozen_hits]
    assert live_order == [f"concept{i}" for i in range(1, 18)], "the order is by id among equals"
    assert all(abs(h.score - 0.88) < 1e-3 for h in live_hits), "rt.score must stay the real prominence"
    live.close(); frozen.close()


def test_a_write_spread_across_a_rounding_boundary_splits_the_tie_by_rule(tmp_path, monkeypatch):
    """The case W0 found on Windows, planted and named. Seventeen rows written
    a quarter-second apart (a four-second spread, as slow writes give),
    retrieved four seconds after the first. Their ranking value — prominence
    at six decimals; the unregistered `asserted` source decays at the
    extraction rate, 0.05 a day, at arrival 0.65 — steps down at 1.33 s and
    at 3.99 s of age, so the seventeen sit in three bands and the tie splits:
    the order is the rule's (tier, value, id) and it is not the id order.
    The order rule working, not failing: a total order over rounded values
    has boundaries, and the rig's own clock (W0) is what keeps the rig's rows
    off them.

    Recorded beside it, not changed (law 7): the record's
    `weight_at_injection` rounds the UNSCALED prominence, so it sits on a
    different grid from the order's — rows 1 and 2 here carry the same
    `weight_at_injection` and are ordered apart. The order is re-derived
    from the record together with the store's class constants, not from the
    record alone.

    Planted for the SHIPPED extraction constants, which `tiered_store.py`
    calls provisional: the two overrides are scrubbed for the test, and the
    constants are asserted first, so a recalibration fails here by name and
    the case is re-planted rather than read as a behaviour defect."""
    monkeypatch.delenv("NEXUS_DECAY_EXTRACTION", raising=False)
    monkeypatch.delenv("NEXUS_ARRIVAL_EXTRACTION", raising=False)
    assert (decay_rate_for_source("asserted"), arrival_scale("asserted")) == (0.05, 0.65), \
        "the extraction constants moved: re-plant this case for the new grid"
    store = TripleStore(tmp_path / "kb.db")
    for i in range(17):
        monkeypatch.setattr(time, "time", RigClock(base=RIG_CLOCK_BASE + 0.25 * i, step=0.0))
        store.add(subject=f"concept{i + 1}", relation="improves", object=f"outcome{i + 1}", weight=0.88,
                  tier=1, granularity=Granularity.TOPIC, source="asserted")
    now = RIG_CLOCK_BASE + 4.0
    monkeypatch.setattr(time, "time", RigClock(base=now, step=0.0))
    hits = TripleRetriever(store).retrieve("concept improves outcome", top_k=20)
    monkeypatch.setattr(time, "time", TRUE_TIME)
    order = [h.triple.subject for h in hits]
    value = {h.triple.subject: ranking_prominence(h.triple, now) for h in hits}
    record = {h.triple.subject: round(h.score, 6) for h in hits}
    print(f"\nSNIP-DET-6 ({sys.platform}) order {order}; ranking values {sorted(set(value.values()))}; "
          f"concept1/concept2: ranking {value['concept1']}/{value['concept2']}, "
          f"weight_at_injection {record['concept1']}/{record['concept2']}")
    expected = sorted(hits, key=lambda h: (h.triple.tier, -value[h.triple.subject], h.triple.id))
    assert order == [h.triple.subject for h in expected], "the order is not the rule's"
    assert sorted(set(value.values())) == [0.571998, 0.571999, 0.572]
    assert order == ([f"concept{i}" for i in range(12, 18)] + [f"concept{i}" for i in range(2, 12)]
                     + ["concept1"])
    assert order != [f"concept{i}" for i in range(1, 18)], "non-vacuity: the boundary split the tie"
    assert record["concept1"] == record["concept2"] and value["concept1"] != value["concept2"]
    store.close()


# ---------------------------------------------------------------------------
# 5. Collapse's candidates are a total order
# ---------------------------------------------------------------------------

def test_collapse_candidates_are_sorted_by_ast_and_by_consequence():
    """The candidates comprehension in `graph_collapse` iterates `sorted(...)`
    (AST); and two graphs holding the same two chains built in opposite
    insertion orders collapse the same nodes and demote the same edges."""
    src = (ROOT / "backend" / "loop" / "compaction.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "graph_collapse")
    comps = [n for n in ast.walk(fn) if isinstance(n, ast.ListComp)]
    assert comps, "no candidates comprehension"
    iters = [c.generators[0].iter for c in comps]
    assert any(isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "sorted" for it in iters), \
        "graph_collapse iterates the node set unsorted"

    def graph(order):
        h = Hippocampus()
        for s, t in order:
            h.add_edge(SynapticEdge(source=s, target=t, weight=0.6))
        return h
    chain = [("a", "b"), ("b", "c"), ("c", "d"), ("x", "y"), ("y", "z")]
    g1, g2 = graph(chain), graph(list(reversed(chain)))
    n1, n2 = graph_collapse(g1), graph_collapse(g2)
    dem1 = sorted((e.source, e.target) for e in g1.edges() if e.demoted)
    dem2 = sorted((e.source, e.target) for e in g2.edges() if e.demoted)
    print(f"\nSNIP-DET-5 ({sys.platform}) collapsed {n1}/{n2}; demoted {dem1}")
    assert (n1, dem1) == (n2, dem2)
    assert dem1 == [("a", "b"), ("b", "c"), ("x", "y"), ("y", "z")]


def test_the_rig_clock_restores_and_its_run_stays_inside_the_first_step(tmp_path, monkeypatch):
    """The two properties the rig's clock rests on, pinned (the W0 verifier
    found both asserted in comments only). (1) `installed()` puts the host's
    clock back after an exception and after nested installs, in order — a
    leaked clock would make every later test read 2023. (2) A 12-turn run of
    the SNIP-DET configuration reads the clock about 425 times; at one
    microsecond a read its virtual span must stay three orders of magnitude
    inside the first rounding step (1.33 s of age for the rig's claims), or
    the rig's own clock would put rows back on a boundary.
    COMPANION: a clock whose `installed()` forgets the restore is caught."""
    host = time.time
    outer, inner = RigClock(), RigClock(step=0.0)
    with pytest.raises(RuntimeError):
        with outer.installed():
            with inner.installed():
                assert time.time is inner
            assert time.time is outer
            raise RuntimeError("a turn that raised inside the rig's clock")
    assert time.time is host, "the rig's clock leaked past an exception"

    class Leaky(RigClock):
        def installed(self):                       # the planted defect: no finally
            import contextlib as _c

            @_c.contextmanager
            def cm():
                real = time.time
                time.time = self
                yield self
                time.time = real
            return cm()
    leaky = Leaky()
    try:
        with pytest.raises(RuntimeError):
            with leaky.installed():
                raise RuntimeError("boom")
        leaked = time.time is leaky
    finally:
        # A plain assignment, never monkeypatch: monkeypatch would record the
        # LEAKED clock as the value to restore and put it back at teardown
        # (found by the guard below on this test's first draft).
        time.time = host
    assert leaked, "companion: the planted leak was not observable"

    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    rig = CERig(tmp_path / "span", pairs=20, answer_chars=800, kb_lines=17, user_chars=76)
    rig.run(12)
    rig.close()
    span = rig.clock.reads * rig.clock.step
    print(f"\nSNIP-DET-7 ({sys.platform}) 12 turns: {rig.clock.reads} clock reads, virtual span {span * 1e3:.3f} ms")
    assert span < 1.329e-3, "the rig's own clock now spans a thousandth of the first rounding step"


def test_the_suite_still_runs_with_the_real_clock():
    """A guard on the guard: no test above leaves `time.time` patched."""
    assert abs(time.time() - time.time()) < 1.0 and time.time() > 1_750_000_000.0
