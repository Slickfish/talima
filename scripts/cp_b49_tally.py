"""CP S5/S6 — B-49's tally from the operating instance, read-only.

    python scripts/cp_b49_tally.py --db data/kb.db [--capture data/fire_capture.jsonl] [--json]

Claude Code has no instance; the operator runs this on theirs. It opens the
database READ-ONLY (sqlite `mode=ro`, plus an authorizer that refuses every
write), reads the fire capture file, prints both paths on its FIRST lines,
and prints:

  1. the capture's state: the gate as `.env` sets it, the file, the row
     counts (`fire_rows`, `check_rows`, `last_fire_turn`);
  2. the S5 tally so far — `removed_total`, `present_next`, `absent_next`,
     `demoted_next`, `unresolved_ids` — and whether TEN fires of tiers 1 to 3
     exist yet (`fires_tier_1_to_3`, `fires_needed_for_the_rule`);
  3. the S5 conditions as facts: how many removed ids read `present` on the
     next turn, how many demoted ids read `demoted` — printed, not decided:
     the decision is a register line after ten fires exist;
  4. the binding: every row's `record_hash` against `lineage_records` —
     bound / mismatched / unbound (no record), each named;
  5. S1.5's instance state: whether AO (`NEXUS_CITATION_CAPTURE`) is on in
     `.env` today and how many AO rows exist.

Nothing here writes. `--selftest` attempts one UPDATE that matches no row on
`lineage_records`, the table this store must have, and prints the refusal;
the table is resolved before SQLite's authorizer is consulted, so a misopened
file says "no such table" and the selftest FAILS (exit 3) instead of printing
a refusal (W1 part 5f).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402
# W1 — the one read-only open (law 15): percent-encoded mode=ro, query_only,
# and an authorizer that refuses every write and every writing pragma.
from scripts._readonly import (WRITE_ACTIONS, assert_write_refused, open_read_only, read_only_how,  # noqa: E402,F401
                               store_exists, store_label)

TEN = 10


def gate(name: str) -> str:
    raw = os.environ.get(name)
    return "unset (default off)" if raw is None else raw.strip().lower()


def jsonl_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def s5_conditions(rows: list[dict]) -> dict:
    """The rule's two conditions, as counts over resolved checks: removed ids
    by verdict and demoted ids by verdict, each keyed by the kind of id the
    tier emitted (`triple:` for micro's rows, `edge:` for snip's rows and
    collapse's demotions)."""
    removed_verdicts: dict[str, int] = {}
    demoted_verdicts: dict[str, int] = {}
    fires = {r["lineage_id"]: r for r in rows if r.get("kind") == "fire"}
    for r in rows:
        c = r.get("check")
        if not c:
            continue
        prev = fires.get(c.get("of_lineage_id"))
        demoted_ids = set()
        if prev:
            for t in prev.get("tiers", ()):
                demoted_ids.update(t.get("demoted_ids") or ())
        for i, verdict in (c.get("ids") or {}).items():
            bucket = demoted_verdicts if i in demoted_ids else removed_verdicts
            bucket[verdict] = bucket.get(verdict, 0) + 1
    return {"removed_ids_by_next_turn_verdict": removed_verdicts,
            "demoted_ids_by_next_turn_verdict": demoted_verdicts}


def main() -> int:
    force_utf8_stdio()
    load_env()
    from backend.eval import fire_capture as fc                      # noqa: E402  (pure at import)
    from backend.eval.citation_capture import capture_path as ao_path  # noqa: E402

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="the instance's kb.db (lineage lives there); opened read-only")
    ap.add_argument("--capture", default=None,
                    help="the fire capture file; default NEXUS_FIRE_CAPTURE_PATH, else data/fire_capture.jsonl")
    ap.add_argument("--selftest", action="store_true",
                    help="attempt one UPDATE matching no row on lineage_records and print the refusal; "
                         "a file without that table fails the selftest (exit 3)")
    ap.add_argument("--json", action="store_true", help="print the facts as one JSON document after the lines")
    args = ap.parse_args()

    db = args.db
    cap = Path(args.capture) if args.capture else fc.capture_path()
    if not cap.is_absolute():
        cap = ROOT / cap
    print(f"database: {store_label(db)} (opened read-only: {read_only_how(db)})", flush=True)
    print(f"capture:  {cap.resolve().as_posix()} ({'present' if cap.is_file() else 'ABSENT — no fire recorded yet, or the gate is off'})")
    exists, why = store_exists(db)
    if not exists:
        print(f"REFUSED: {why}")
        return 2
    conn = open_read_only(db, url_ok=True)                    # W3b part 3: a database URL is taken too
    if args.selftest:
        # W1 — the refusal itself (scripts/_readonly.py), never any
        # DatabaseError, probed on a table this store must have: a misopened
        # file raises "no such table" and fails the selftest (part 5f).
        try:
            print(f"selftest: write refused ({assert_write_refused(conn, table='lineage_records')})")
        except AssertionError as e:
            print(f"SELFTEST FAILED: {e}")
            return 3

    rows = fc.read_rows(cap)
    t = fc.tally(rows)
    b = t["b49"]

    def lookup(lid):
        r = conn.execute("SELECT record_hash FROM lineage_records WHERE lineage_id = ?", (lid,)).fetchone()
        return r["record_hash"] if r else None
    binding = fc.verify(rows, lookup)
    conn.close()
    conditions = s5_conditions(rows)
    ao_file = ao_path() if Path(ao_path()).is_absolute() else ROOT / ao_path()

    print(f"1. capture gate NEXUS_FIRE_CAPTURE in .env: {gate('NEXUS_FIRE_CAPTURE')}; "
          f"rows: fire {t['fire_rows']}, check {t['check_rows']}, last fire turn {t['last_fire_turn']}")
    print(f"2. S5 tally: removed_total {b['removed_total']}, present_next {b['present_next']}, "
          f"absent_next {b['absent_next']}, demoted_next {b['demoted_next']}, unresolved_ids {b['unresolved_ids']}")
    ten = b["fires_tier_1_to_3"] >= TEN
    print(f"   fires of tiers 1 to 3: {b['fires_tier_1_to_3']} — ten exist: {'YES' if ten else 'NO'} "
          f"({b['fires_needed_for_the_rule']} more needed before the S5 rule applies)")
    print(f"3. S5 conditions (facts, not a decision): removed ids by next-turn verdict "
          f"{json.dumps(conditions['removed_ids_by_next_turn_verdict'])}; demoted ids by next-turn verdict "
          f"{json.dumps(conditions['demoted_ids_by_next_turn_verdict'])}")
    print(f"4. binding to lineage_records: bound {binding['bound']} of {binding['rows']}; "
          f"mismatched {binding['mismatched']}; unbound (no record) {binding['unbound']}")
    print(f"5. AO (S1.5): NEXUS_CITATION_CAPTURE in .env: {gate('NEXUS_CITATION_CAPTURE')}; "
          f"AO rows: {jsonl_rows(Path(ao_file))} at {Path(ao_file).resolve().as_posix()}")
    if args.json:
        print(json.dumps({"db": db.resolve().as_posix(), "capture": cap.resolve().as_posix(),
                          "gate": gate("NEXUS_FIRE_CAPTURE"), "tally": t, "ten_fires_exist": ten,
                          "conditions": conditions, "binding": binding,
                          "ao": {"gate": gate("NEXUS_CITATION_CAPTURE"), "rows": jsonl_rows(Path(ao_file))}},
                         indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
