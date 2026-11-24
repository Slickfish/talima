#!/usr/bin/env python3
"""RETIRED by Packet F.3. Read-only forecasting still works; writing does not.

WHY IT IS RETIRED
    This existed to move the 555 co-activation rows out of tier 0, because
    tier 0 never decayed and nothing could leave it. F.3 removed that
    immunity: the decay rate is now a property of the belief's evidence class
    (`hippocampus_consolidation`), not of its tier, so those rows decay where
    they are and rest at the floor with **no write at all**. The operation
    this script performs is no longer necessary, and performing it would be a
    manual nudge to a store that now reaches the same state by policy.

    Its value was the measurement that stopped us firing it: the forecast
    showed `rebalance()` would have promoted the rows straight back, so the
    committed record would have claimed an effect that did not occur. See
    `reports/coactivation_eligibility_2026-08-08.md`, which stays.

    The `--confirm` path is disabled. The forecast is left runnable because
    it is still a useful read-only view of what decay is doing to a real
    store, and because deleting the instrument would delete the evidence.

--- original header follows -------------------------------------------------

Packet E.4 — make the co-activation rows decay-eligible.

WHAT THIS DOES
    Writes the `tier` column, and nothing else, on rows matching
        relation = 'co_activated' AND source = 'hippocampus_consolidation'
    Weights are not touched. Nothing is deleted. Law 4 holds: these are
    beliefs and they remain beliefs, with full provenance, retrievable.

WHY A SCOPED WRITE RATHER THAN A CONSTANTS CHANGE
    Tier 0 never decays (TIER_DECAY_PER_DAY[0] = 0.0) and can never demote
    (TIER_DEMOTE_WEIGHT[0] = 0.0 makes `cw < 0.0` unsatisfiable, since
    current_weight clamps at 0). Making the 555 decay-eligible by editing
    those constants would be global: per measurement, tier 0 holds ~602 rows
    of which 555 are co-activation, so ~47 unrelated beliefs would be
    re-fated by a change no record covers. The scoped write touches exactly
    the intended population.

THE RATCHET — READ THIS BEFORE RUNNING
    Measured on a synthetic row shaped like the real ones (weight 0.971,
    recently fired): a tier write does NOT necessarily stick. `rebalance()`
    promotes whenever `current_weight >= TIER_PROMOTE_WEIGHT[tier]`, so a
    high-weight row climbs back:
        from tier 1 -> tier 0 in 1 cycle   (0.971 >= 0.70)
        from tier 2 -> tier 0 in 2 cycles  (0.971 >= 0.40)
        from tier 3 -> tier 0 in 3 cycles  (0.971 >= 0.00)
    and tier 0 is absorbing. Whether the write sticks therefore depends
    entirely on how stale `last_fired` is on the real rows: once out of tier
    0 they decay, and if they have been idle long enough their current_weight
    falls below the promote threshold before the next rebalance and they sink
    instead of climbing. This script computes that forecast per row from the
    real data and refuses to guess.

LAW 3 — this is a bulk write to the belief store, so it is confirm-gated.
Dry run is the default; `--confirm` is required to write.

    python scripts/coactivation_eligibility.py --kb data/kb.db            # forecast
    python scripts/coactivation_eligibility.py --kb data/kb.db --target-tier 3 --confirm
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# M.4 — scripts print em dashes and box rules; a piped stdout would
# otherwise be encoded with the platform default (cp1252 on Windows) and
# the byte 0x97 kills a UTF-8 reader. Fixed at the writer, not left to
# the environment. See scripts/_console.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8_stdio  # noqa: E402
from _env import load_env  # noqa: E402
from _readonly import open_read_only, store_line  # noqa: E402

from backend.kb.tiered_store import (  # noqa: E402
    TIER_DECAY_PER_DAY, TIER_DEMOTE_WEIGHT, TIER_PROMOTE_WEIGHT,
)

SELECTOR = "relation = 'co_activated' AND source = 'hippocampus_consolidation'"
DAY = 86400.0


def _rows(conn) -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT id, subject, object, weight, tier, last_fired FROM triples "
        f"WHERE {SELECTOR}").fetchall()


def _dist(vals) -> str:
    if not vals:
        return "n=0"
    sv = sorted(vals)
    return (f"n={len(sv)} min={sv[0]:.3f} p50={statistics.median(sv):.3f} "
            f"mean={statistics.fmean(sv):.3f} max={sv[-1]:.3f}")


def _tier_counts(conn, where: str = "1=1") -> dict:
    return {int(r["tier"]): int(r["n"]) for r in conn.execute(
        f"SELECT tier, COUNT(*) AS n FROM triples WHERE {where} "
        f"GROUP BY tier ORDER BY tier")}


def forecast(rows, target: int, now: float) -> dict:
    """Would rebalance() ratchet each row back toward tier 0?"""
    climbs = sinks = holds = 0
    for r in rows:
        days = max(0.0, (now - float(r["last_fired"])) / DAY)
        cw = max(0.0, float(r["weight"]) - TIER_DECAY_PER_DAY[target] * days)
        if target > 0 and cw >= TIER_PROMOTE_WEIGHT[target]:
            climbs += 1
        elif target < 3 and cw < TIER_DEMOTE_WEIGHT[target]:
            sinks += 1
        else:
            holds += 1
    return {"climbs_back": climbs, "sinks_further": sinks, "holds": holds}


def break_even_days(weight: float, target: int) -> float:
    """Idle days a row of this weight needs before the demotion HOLDS.

    Solve `weight - decay*days < promote_threshold`. Below this, the next
    rebalance() promotes it straight back and the write achieved nothing.
    """
    return (weight - TIER_PROMOTE_WEIGHT[target]) / TIER_DECAY_PER_DAY[target]


def main() -> int:
    # M.4 — the first statement, before anything is printed. Scripts
    # print em dashes; a piped stdout would otherwise carry cp1252
    # bytes that a UTF-8 reader cannot decode. See scripts/_console.py.
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--target-tier", type=int, default=2,
                    help="tier to write (default 2 — the store's ordinary-belief tier)")
    ap.add_argument("--confirm", action="store_true",
                    help="Law 3 — required to actually write")
    args = ap.parse_args()

    if args.confirm:
        print(__doc__.split("--- original header follows")[0].rstrip())
        print("\nRefusing to write. Re-run without --confirm for the forecast.")
        return 2
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    if args.target_tier not in (1, 2, 3):
        print("--target-tier must be 1, 2 or 3 (0 is the tier we are leaving; "
              "4 is the archive tier and would be an archival act, not decay "
              "eligibility)")
        return 2

    db = Path(args.kb)
    if not db.exists():
        print(f"no database at {db}")
        return 2

    # W1 — read-only (law 15): the docstring's "read-only forecasting" was a
    # plain read-write connect, and --confirm's write path is unreachable.
    conn = open_read_only(db)
    now = time.time()

    rows = _rows(conn)
    if not rows:
        print(f"No rows match: {SELECTOR}\nNothing to do.")
        return 0

    weights = [float(r["weight"]) for r in rows]
    ages = [(now - float(r["last_fired"])) / DAY for r in rows]

    print("=" * 68)
    print("CO-ACTIVATION ELIGIBILITY — before")
    print("=" * 68)
    print(f"selector          : {SELECTOR}")
    print(f"matched rows      : {len(rows)}")
    print(f"total triples     : {conn.execute('SELECT COUNT(*) n FROM triples').fetchone()['n']}")
    print(f"weight            : {_dist(weights)}")
    print(f"days since fired  : {_dist(ages)}")
    print(f"tier (matched)    : {_tier_counts(conn, SELECTOR)}")
    print(f"tier (whole store): {_tier_counts(conn)}")
    print(f"tier 0 NOT matched: "
          f"{conn.execute(f'SELECT COUNT(*) n FROM triples WHERE tier=0 AND NOT ({SELECTOR})').fetchone()['n']}"
          "   <- rows a constants change would have re-fated")

    print()
    print("=" * 68)
    print("RATCHET FORECAST (rebalance() behaviour at each candidate tier)")
    print("=" * 68)
    median_w = statistics.median(weights)
    for t in (1, 2, 3):
        f = forecast(rows, t, now)
        mark = "  <-- selected" if t == args.target_tier else ""
        print(f"  tier {t}: climbs_back={f['climbs_back']:<6} "
              f"sinks_further={f['sinks_further']:<6} holds={f['holds']:<6}{mark}")
    print("  climbs_back > 0 means those rows return toward tier 0, which is")
    print("  absorbing — the write would not achieve decay eligibility for them.")
    print()
    print(f"  break-even idle days at the median weight ({median_w:.3f}) — a row")
    print("  must be at least this stale for the demotion to hold:")
    for t in (1, 2, 3):
        print(f"    tier {t}: {break_even_days(median_w, t):>6.1f} days idle")
    print(f"  observed median idle: {statistics.median(ages):.1f} days")
    print()
    print("  NOTE — tier 3 never holds anything. TIER_PROMOTE_WEIGHT[3] = 0.0 and")
    print("  current_weight() clamps at 0, so `cw >= 0.0` is always true and every")
    print("  tier-3 row promotes to tier 2 on every rebalance(). A fully-decayed")
    print("  belief therefore oscillates 2<->3 forever instead of resting at the")
    print("  bottom (measured). Tier 1 is the only tier that retains a demoted row")
    print("  over a real weight band (0.40 <= cw < 0.70); tier 2 retains over")
    print("  (0.15 <= cw < 0.40).")

    if not args.confirm:
        print()
        print("DRY RUN — nothing written. Re-run with --confirm to apply "
              f"tier={args.target_tier} (Law 3).")
        return 0

    conn.execute(f"UPDATE triples SET tier = ? WHERE {SELECTOR}",
                 (args.target_tier,))
    conn.commit()

    after = _rows(conn)
    print()
    print("=" * 68)
    print("AFTER")
    print("=" * 68)
    print(f"rows written      : {len(after)}  (tier column only)")
    print(f"tier (matched)    : {_tier_counts(conn, SELECTOR)}")
    print(f"weight            : {_dist([float(r['weight']) for r in after])}"
          "   <- unchanged, by construction")
    print(f"tier (whole store): {_tier_counts(conn)}")
    print("\nRecord this run in reports/coactivation_eligibility_2026-08-08.md")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
