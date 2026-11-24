"""Packet BC — v3.1 Phase 0, the parts that need the operator's store. Read-only.

    python scripts/v31_phase0_probe.py [--kb data/kb.db] [--archive data/archive.db]
        [--calibration data/calibration_log.jsonl] [--out v31_phase0.json] [--fixture]

Three counts the packet wants from the store rather than from a document, and
one fixture that needs no store:

  [1.5] THE MASK. `NEXUS_RECURRENCE_THRESHOLD` as `.env` supplies it and the
        value the consolidation gate would use now (`_default_recurrence_
        threshold()`), beside the rows currently archived, by reason, read
        from `triples.archived` / `archive_reason`.
  [12]  THE EXHAUST, DERIVED. `archival_events` grouped by direction and
        reason, and per reason the number of beliefs whose LATEST event is
        `archived` — the ledger-derived figure, to set beside the review's
        21,929 and the register's 21,915. Neither is quoted here.
  [13]  THE BUDGET. The injected set is capped at `MAX_INJECTED_TRIPLES`
        (assemble_context's `top_k`). What a "work unit" would hold today is
        measured two ways, because no work-unit exists to count: (a) distinct
        extracted beliefs per session, sessions delimited by the `turn`
        counter resetting in the calibration log's `triple` rows; (b) rows
        with source='extraction' clustered by a 30-minute gap in
        `created_at`. Also the tier-0 count (the `shared` analogue: what an
        always-first non-decaying partition would be if it were tier 0
        today) and the lineage record's `scope` usage.

  --fixture  ITEM 2 and ITEM 11, on a temporary store, printed in full: a
        `work_unit_id` column is added to `triples`; the same claim is
        `add()`ed under WU-1 (column set as the spec's INSERT would) and again
        under WU-7; the row is printed; then the deferred-decision guard's
        own predicate (`tests/test_scope_recorded.py`, `"scope" not in cols`)
        is evaluated against the altered table.

Stores are opened `mode=ro`; md5 before and after. Nothing is written but the
report. `backend.server` is never imported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402

SESSION_GAP_SECONDS = 1800.0


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "ABSENT"


def _ro(p: Path) -> sqlite3.Connection:
    con = open_read_only(p, row_factory=None)
    con.row_factory = sqlite3.Row
    return con


def _tables(con) -> set[str]:
    return {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


# ---------------------------------------------------------------------------
# [1.5] the mask
# ---------------------------------------------------------------------------

def mask_state(con) -> dict:
    from backend.memory.neocortex import (DEFAULT_RECURRENCE_THRESHOLD,
                                          _default_recurrence_threshold)
    out = {
        "env_NEXUS_RECURRENCE_THRESHOLD": os.environ.get("NEXUS_RECURRENCE_THRESHOLD"),
        "effective_recurrence_threshold": _default_recurrence_threshold(),
        "default_recurrence_threshold": DEFAULT_RECURRENCE_THRESHOLD,
        "env_files_read": [],
    }
    out["muted"] = (out["effective_recurrence_threshold"] > 100)   # 999 is the T-1 value
    if con is None:
        out["archived_rows_by_reason"] = "store ABSENT"
        return out
    rows = con.execute(
        "SELECT COALESCE(archive_reason, '<NULL>') AS reason, COUNT(*) AS n "
        "FROM triples WHERE archived = 1 GROUP BY reason ORDER BY n DESC").fetchall()
    out["archived_rows_by_reason"] = {r["reason"]: r["n"] for r in rows}
    out["archived_rows_total"] = sum(r["n"] for r in rows)
    out["live_rows_total"] = con.execute(
        "SELECT COUNT(*) AS n FROM triples WHERE archived = 0").fetchone()["n"]
    return out


# ---------------------------------------------------------------------------
# [12] the exhaust, derived from the ledger
# ---------------------------------------------------------------------------

def exhaust_from_ledger(con) -> dict:
    if con is None:
        return {"status": "store ABSENT"}
    if "archival_events" not in _tables(con):
        return {"status": "no archival_events table (pre-AL store): ledger cannot derive it"}
    events = con.execute(
        "SELECT belief_id, direction, reason, ts FROM archival_events ORDER BY ts ASC").fetchall()
    by_dir_reason = Counter((e["direction"], e["reason"]) for e in events)
    latest: dict[int, tuple[str, str]] = {}
    for e in events:
        latest[e["belief_id"]] = (e["direction"], e["reason"])
    currently_archived_by_reason = Counter(r for d, r in latest.values() if d == "archived")
    return {
        "status": "derived",
        "events_total": len(events),
        "events_by_direction_reason": {f"{d}|{r}": n for (d, r), n in by_dir_reason.most_common()},
        "beliefs_with_events": len(latest),
        "beliefs_currently_archived_per_ledger": sum(currently_archived_by_reason.values()),
        "currently_archived_by_reason": dict(currently_archived_by_reason.most_common()),
        "note": ("the exhaust figure is the `currently_archived_by_reason` entry for the "
                 "reason the B-17 pass used; every reason is printed so the operator "
                 "picks it by name rather than by expectation"),
    }


# ---------------------------------------------------------------------------
# [13] the budget
# ---------------------------------------------------------------------------

def budget(con, calibration: Path) -> dict:
    from backend.loop.agentic_loop import MAX_INJECTED_TRIPLES
    out = {"injected_cap_MAX_INJECTED_TRIPLES": MAX_INJECTED_TRIPLES}

    # (a) sessions from the calibration log's `triple` rows
    sessions_a: list[set] = []
    if calibration.is_file():
        cur: set = set()
        last_turn = None
        with calibration.open(encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("mechanism") != "triple" or row.get("resolved_outcome") is not None:
                    continue
                turn = (row.get("context") or {}).get("turn")
                if turn is None:
                    continue
                if last_turn is not None and turn <= last_turn and turn == 1:
                    if cur:
                        sessions_a.append(cur)
                    cur = set()
                cur.add(row.get("belief_id"))
                last_turn = turn
        if cur:
            sessions_a.append(cur)
        sizes_a = [len(s) for s in sessions_a]
        out["a_calibration_sessions"] = {
            "sessions": len(sizes_a), "max": max(sizes_a) if sizes_a else None,
            "p50": _pct(sizes_a, 0.5), "p90": _pct(sizes_a, 0.9),
            "exceeding_cap": sum(1 for n in sizes_a if n > MAX_INJECTED_TRIPLES),
            "max_share_of_cap": (round(max(sizes_a) / MAX_INJECTED_TRIPLES, 2)
                                 if sizes_a else None),
        }
    else:
        out["a_calibration_sessions"] = "calibration log ABSENT"

    if con is None:
        out["b_extraction_clusters"] = "store ABSENT"
        return out

    # (b) extraction rows clustered by created_at gap
    rows = con.execute(
        "SELECT id, created_at FROM triples WHERE source = 'extraction' "
        "ORDER BY created_at ASC").fetchall()
    clusters: list[int] = []
    n = 0
    prev = None
    for r in rows:
        if prev is not None and (r["created_at"] - prev) > SESSION_GAP_SECONDS:
            clusters.append(n)
            n = 0
        n += 1
        prev = r["created_at"]
    if n:
        clusters.append(n)
    out["b_extraction_clusters"] = {
        "extraction_rows": len(rows), "clusters": len(clusters),
        "gap_seconds": SESSION_GAP_SECONDS,
        "max": max(clusters) if clusters else None,
        "p50": _pct(clusters, 0.5), "p90": _pct(clusters, 0.9),
        "exceeding_cap": sum(1 for c in clusters if c > MAX_INJECTED_TRIPLES),
        "max_share_of_cap": (round(max(clusters) / MAX_INJECTED_TRIPLES, 2)
                             if clusters else None),
    }
    tiers = con.execute(
        "SELECT tier, COUNT(*) AS n FROM triples WHERE archived = 0 GROUP BY tier").fetchall()
    out["live_rows_by_tier"] = {int(r["tier"]): r["n"] for r in tiers}
    out["tier0_live_rows_the_shared_analogue"] = out["live_rows_by_tier"].get(0, 0)
    if "lineage_records" in _tables(con):
        out["lineage"] = {
            "records": con.execute("SELECT COUNT(*) AS n FROM lineage_records").fetchone()["n"],
            "sealed": con.execute(
                "SELECT COUNT(*) AS n FROM lineage_records WHERE sealed_block IS NOT NULL"
            ).fetchone()["n"],
            "scope_values": {r["scope"]: r["n"] for r in con.execute(
                "SELECT scope, COUNT(*) AS n FROM lineage_records GROUP BY scope "
                "ORDER BY n DESC LIMIT 10")},
        }
    return out


# ---------------------------------------------------------------------------
# --fixture: items 2 and 11 on a temporary store
# ---------------------------------------------------------------------------

def fixture() -> dict:
    from backend.kb.granularity import Granularity
    from backend.kb.tiered_store import TripleStore
    tmp = Path(tempfile.mkdtemp(prefix="nexus_v31_fixture_"))
    store = TripleStore(tmp / "kb.db")
    # The spec's §2.2.6 column, on a store nothing else uses.
    store.conn.execute("ALTER TABLE triples ADD COLUMN work_unit_id TEXT")
    store.conn.commit()

    def row():
        r = store.conn.execute(
            "SELECT id, subject, relation, object, weight, tier, source, fire_count, "
            "work_unit_id FROM triples WHERE subject = 'interval training'").fetchall()
        return [dict(x) for x in r]

    # WU-1: first extraction of the claim. The spec's INSERT sets the column.
    id_wu1 = store.add(subject="interval training", relation="improves", object="vo2 max",
                       weight=0.6, tier=2, granularity=Granularity.TURN, source="extraction")
    store.conn.execute("UPDATE triples SET work_unit_id = 'WU-1' WHERE id = ?", (id_wu1,))
    store.conn.commit()
    after_wu1 = row()
    # WU-7: the same claim re-asserted, higher confidence. add() is the only
    # path, and its ON CONFLICT clause names weight, tier and last_fired.
    id_wu7 = store.add(subject="interval training", relation="improves", object="vo2 max",
                       weight=0.9, tier=2, granularity=Granularity.TURN, source="extraction")
    after_wu7 = row()

    # Item 11: the guard's own predicate against the altered table.
    cols = {r["name"] for r in store.conn.execute("PRAGMA table_info(triples)")}
    guard_passes = "scope" not in cols
    store.close()
    return {
        "same_id": id_wu1 == id_wu7,
        "row_count": len(after_wu7),
        "after_WU1": after_wu1,
        "after_WU7": after_wu7,
        "item11_columns_on_triples": sorted(cols),
        "item11_guard_predicate": "'scope' not in PRAGMA table_info(triples)",
        "item11_guard_passes_with_work_unit_id_present": guard_passes,
    }


def main() -> int:
    force_utf8_stdio()
    env_files = load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--archive", default="data/archive.db")
    ap.add_argument("--calibration", default="data/calibration_log.jsonl")
    ap.add_argument("--out", default="v31_phase0.json")
    ap.add_argument("--fixture", action="store_true",
                    help="run items 2 and 11 on a temporary store and exit")
    args = ap.parse_args()

    if args.fixture:
        fx = fixture()
        print(json.dumps(fx, indent=2, ensure_ascii=False))
        return 0
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)
    print(store_line(args.archive), flush=True)              # law 15: the store first (W4 part 4)

    kb, archive, cal = Path(args.kb), Path(args.archive), Path(args.calibration)
    before = {"kb": _md5(kb), "archive": _md5(archive), "calibration": _md5(cal)}
    print(f"md5 before: {before}")
    con = _ro(kb) if kb.is_file() else None
    acon = _ro(archive) if archive.is_file() else None

    report = {"paths": {"kb": str(kb), "archive": str(archive), "calibration": str(cal)},
              "md5_before": before}
    report["item_1_5_mask"] = mask_state(con)
    report["item_1_5_mask"]["env_files_read"] = [str(p) for p in env_files]
    report["item_12_exhaust"] = exhaust_from_ledger(con)
    report["item_13_budget"] = budget(con, cal)
    if acon is not None:
        kinds = acon.execute("SELECT kind, COUNT(*) AS n FROM archive_blocks GROUP BY kind").fetchall()
        report["archive_blocks_by_kind"] = {r["kind"]: r["n"] for r in kinds}
        acon.close()
    else:
        report["archive_blocks_by_kind"] = "archive ABSENT"
    if con is not None:
        con.close()
    report["md5_after"] = {"kb": _md5(kb), "archive": _md5(archive), "calibration": _md5(cal)}
    report["unchanged"] = report["md5_after"] == before

    print(json.dumps(report, indent=2, ensure_ascii=False))
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"md5 after:  {report['md5_after']}  unchanged={report['unchanged']}")
    print(f"wrote {args.out}")
    return 0 if con is not None else 2


if __name__ == "__main__":
    sys.exit(main())
