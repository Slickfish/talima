#!/usr/bin/env python3
"""What does one KAIROS consolidation pass cost, at a given scale?

WHY THIS EXISTS
    The operator's console filled with refusal lines and the natural reading
    was "the system attempts 5,235 writes per cycle that cannot succeed".
    Measured, that is not what happens: `check_triple()` runs BEFORE the
    INSERT in `TripleStore.add()`, so a refused belief costs one normalisation
    and one regex pass and touches no database at all. The store's row count
    is bit-identical across a refused pass.

    The cost is the SUCCESSES. `add()` commits per row, `journal_mode` is
    `delete` and `synchronous` is FULL, so a steady-state pass is one fsync
    per promoted edge — and every one of them is a no-op `MAX` upsert on a row
    that is already there. On the operator's numbers that is 97% of the held
    write lock. OQ-4 records the conclusion.

WHAT IT DOES NOT DO
    It never opens `data/`. Every run builds a fresh store under a temp
    directory and drops it; the point is to measure the write path's cost per
    call, which does not depend on the operator's content.

    It also does not import `backend.server` — H.2 established that doing so
    migrates the live database as a side effect.

USAGE
    python scripts/measure_cycle_cost.py                    # operator's scale
    python scripts/measure_cycle_cost.py --promoted 2000 --refused 800
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contextlib import nullcontext                  # noqa: E402

from backend.kb.gate import BeliefRejected          # noqa: E402
from backend.kb.granularity import Granularity      # noqa: E402
from backend.kb.tiered_store import TripleStore     # noqa: E402
from scripts._console import force_utf8_stdio       # noqa: E402
from scripts._env import load_env                   # noqa: E402

# The operator's cycle result, 2026-08-20:
#   hebbian_edges_visited 39962 | edges_promoted 13183 | consolidation_refused 5235
DEFAULT_PROMOTED = 13183
DEFAULT_REFUSED = 5235

# 12 tokens against MAX_TERM_TOKENS = 8. The operator's own row.
REFUSED_OBJECT = ("TAK-441 prolonged the QT interval as a potential indicator "
                  "of cardiac risk in the supratherapeutic arm")


def _offer(store: TripleStore, i: int, object: str) -> bool:
    """One consolidation offer, exactly as `consolidate_from_hippocampus`
    makes it. Returns True if it was written."""
    try:
        store.add(subject=f"concept {i % 400}", relation="co_activated",
                  object=object, weight=0.6, tier=1,
                  granularity=Granularity.SESSION,
                  source="hippocampus_consolidation")
        return True
    except BeliefRejected:
        return False


def main() -> int:
    force_utf8_stdio()
    # U.2 — the same .env the server reads. It matters here: NEXUS_GATE_* and
    # NEXUS_RECURRENCE_THRESHOLD change which offers are refused, and a cost
    # measured under different rules than the daemon runs is not a measurement
    # of the daemon.
    load_env()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--promoted", type=int, default=DEFAULT_PROMOTED)
    ap.add_argument("--refused", type=int, default=DEFAULT_REFUSED)
    # Packet AA — measure the batched pass beside the per-row one, so the
    # packet's claim ships as a number this script can reproduce anywhere.
    ap.add_argument("--batch", action="store_true",
                    help="wrap each pass in TripleStore.deferred_pass() "
                         "(one commit per pass, Packet AA)")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="nexus-cycle-cost-"))
    store = TripleStore(tmp / "kb.db")

    print("sqlite settings that govern the cost:")
    for pragma in ("journal_mode", "synchronous", "busy_timeout"):
        val = store.conn.execute(f"PRAGMA {pragma}").fetchone()[0]
        print(f"  {pragma:<14} = {val}")
    print()

    def pass_ctx():
        return store.deferred_pass() if args.batch else nullcontext()

    mode = "one commit per pass" if args.batch else "commit per row"
    print(f"mode: {mode}\n")
    t0 = time.perf_counter()
    with pass_ctx():
        for i in range(args.promoted):
            _offer(store, i, f"target {i}")
    first = time.perf_counter() - t0
    print(f"first pass   ({args.promoted} new rows)        : {first:6.2f}s")

    # THE STEADY STATE: the identical edges, re-offered. This is what every
    # cycle after the first one does, forever.
    t0 = time.perf_counter()
    with pass_ctx():
        for i in range(args.promoted):
            _offer(store, i, f"target {i}")
    repeat = time.perf_counter() - t0
    rows_after_repeat = store.count()
    print(f"repeat pass  ({args.promoted} no-op upserts)   : {repeat:6.2f}s")

    t0 = time.perf_counter()
    with pass_ctx():
        refused = sum(0 if _offer(store, i, REFUSED_OBJECT) else 1
                      for i in range(args.refused))
    refused_t = time.perf_counter() - t0
    print(f"refused pass ({args.refused} rejections)       : {refused_t:6.2f}s"
          f"  (n={refused})")
    print()

    print(f"rows after the repeat pass  : {rows_after_repeat}")
    print(f"rows after the refused pass : {store.count()}"
          f"   <- refusals write NOTHING")
    print()
    print(f"per successful upsert : {repeat / args.promoted * 1000:.3f} ms")
    print(f"per refusal           : {refused_t / args.refused * 1000:.3f} ms")
    total = repeat + refused_t
    print()
    print(f"ONE STEADY-STATE CYCLE = {total:.2f}s of held write lock:")
    print(f"  {repeat:6.2f}s ({repeat / total * 100:3.0f}%) successful re-upserts")
    print(f"  {refused_t:6.2f}s ({refused_t / total * 100:3.0f}%) refusals")
    print()
    print("Measured on THIS host. R.2 established that the operator's Windows")
    print("machine is far slower on syscall-bound work, so treat these as a")
    print("floor rather than an estimate for their deployment.")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
