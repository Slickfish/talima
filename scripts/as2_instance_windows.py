"""AS-2 S1.4 — the operating instance's windows, read-only, printed as facts.

    python scripts/as2_instance_windows.py --db data/kb.db [--frozen-since 2026-10-01]

Claude Code has no instance; the operator runs this on theirs. It opens the
database READ-ONLY (sqlite `mode=ro`, plus an authorizer that refuses every
write), prints the path it opened on its FIRST line, and prints four facts:

  1. the earliest lineage record (MIN(ts)) — reconstruction cannot start
     before it;
  2. the frozen-by-assertion interval — records written at or after the
     operator's frozen-since date whose `learning_mode` is NULL, i.e. frozen
     by declaration and not by the record (C-35; expected exactly two on the
     instance, FOUND BY QUERY, never taken from a prompt);
  3. the LS boundary — the first record carrying `learning_mode`, and the
     first carrying `frozen`;
  4. `fire`'s semantics by code version — the commits that changed
     `TripleStore.fire`, the reinforcement loop, the citation rules, the
     freeze gate and the `learning_mode` column, from `git log` on the
     checkout the script runs in (a shallow clone says so: the sandbox's
     history begins on 2026-08-23 and cannot see F.5).

Reconstruction (AS-2's rule: fire_count at D equals the record's count of
cited injections up to D with learning_mode not frozen) holds only within one
`fire` semantics and only from the record's first date; facts 1–4 are the
windows. Nothing here writes. `--selftest` attempts one UPDATE that matches no
row on `lineage_records`, the table this store must have, and prints the
refusal; the table is resolved before SQLite's authorizer is consulted, so a
misopened file says "no such table" and the selftest FAILS (exit 3) instead of
printing a refusal (W1 part 5f).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402
# W1 — the one read-only open (law 15): percent-encoded mode=ro, query_only,
# and an authorizer that refuses every write and every writing pragma.
from scripts._readonly import (WRITE_ACTIONS, assert_write_refused, column_names, open_read_only,  # noqa: E402,F401
                               read_only_how, store_exists, store_label)

DEFAULT_FROZEN_SINCE = "2026-10-01"      # the operator's declared date (FZ-2, T-1 closed); overridable
# The semantics boundaries: (label, paths, -S pattern or None). Printed from the
# checkout's own history; the sandbox's clone is shallow and sees none before
# 2026-08-23, which the script says rather than inventing a date.
VERSION_PROBES = (
    ("TripleStore.fire body", ("backend/kb/tiered_store.py",), "def fire(self, triple_id"),
    ("the reinforcement loop (F.5 / B-43)", ("backend/loop/agentic_loop.py",), "store.fire("),
    ("the citation rules (F.5 / AU)", ("backend/kb/citation.py",), None),
    ("the freeze gate (FZ-2)", ("backend/learning.py",), None),
    ("learning_mode on the record (LS)", ("backend/memory/lineage.py",), "learning_mode"),
)


def iso(ts: float | None) -> str:
    if ts is None:
        return "null"
    return _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc).isoformat()


def frozen_since_epoch(date: str) -> float:
    """The declared date, read as midnight UTC — the AS-1b convention problem
    in miniature: the record stores epoch seconds and the operator asserts a
    calendar date. Both are printed so the reader can see the eight hours."""
    d = _dt.date.fromisoformat(date)
    return _dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc).timestamp()


def facts(conn: sqlite3.Connection, frozen_since: str) -> dict:
    since = frozen_since_epoch(frozen_since)
    cols = column_names(conn, "lineage_records")
    has_mode = "learning_mode" in cols
    first = conn.execute("SELECT lineage_id, ts FROM lineage_records ORDER BY ts ASC, lineage_id ASC LIMIT 1").fetchone()
    total = conn.execute("SELECT COUNT(*) AS n FROM lineage_records").fetchone()["n"]
    out = {
        "earliest_record": ({"lineage_id": first["lineage_id"], "ts": first["ts"], "iso_utc": iso(first["ts"])}
                            if first else None),
        "records": total,
        "learning_mode_column": has_mode,
        "frozen_since_asserted": frozen_since,
        "frozen_since_epoch_utc_midnight": since,
    }
    if has_mode:
        by_assertion = conn.execute(
            "SELECT lineage_id, ts FROM lineage_records WHERE ts >= ? AND learning_mode IS NULL "
            "AND ts < COALESCE((SELECT MIN(ts) FROM lineage_records WHERE learning_mode IS NOT NULL), 1e18) "
            "ORDER BY ts ASC", (since,)).fetchall()
        out["frozen_by_assertion"] = {
            "rule": "ts >= the asserted frozen-since date, learning_mode NULL, written before the first record that carries the column",
            "count": len(by_assertion),
            "expected_on_the_instance": 2,
            "records": [{"lineage_id": r["lineage_id"], "ts": r["ts"], "iso_utc": iso(r["ts"])} for r in by_assertion],
            "interval": ([iso(by_assertion[0]["ts"]), iso(by_assertion[-1]["ts"])] if by_assertion else None),
        }
        ls_first = conn.execute(
            "SELECT lineage_id, ts, learning_mode FROM lineage_records WHERE learning_mode IS NOT NULL "
            "ORDER BY ts ASC, lineage_id ASC LIMIT 1").fetchone()
        frozen_first = conn.execute(
            "SELECT lineage_id, ts FROM lineage_records WHERE learning_mode = 'frozen' "
            "ORDER BY ts ASC, lineage_id ASC LIMIT 1").fetchone()
        out["ls_boundary"] = {
            "first_record_with_learning_mode": ({"lineage_id": ls_first["lineage_id"], "ts": ls_first["ts"],
                                                 "iso_utc": iso(ls_first["ts"]), "learning_mode": ls_first["learning_mode"]}
                                                if ls_first else None),
            "first_record_frozen": ({"lineage_id": frozen_first["lineage_id"], "ts": frozen_first["ts"],
                                     "iso_utc": iso(frozen_first["ts"])} if frozen_first else None),
            "records_frozen": conn.execute("SELECT COUNT(*) AS n FROM lineage_records WHERE learning_mode = 'frozen'").fetchone()["n"],
            "records_live_or_null": conn.execute("SELECT COUNT(*) AS n FROM lineage_records WHERE learning_mode IS NULL").fetchone()["n"],
        }
    # W3b part 3 — read in Python, where it used SQLite's json_each (no Postgres
    # twin): the same two counts, a record with any cited injection and the
    # distinct beliefs cited, over the parsed `injected_json`.
    cited_records, cited_beliefs = 0, set()
    for lineage_id, injected in conn.execute("SELECT lineage_id, injected_json FROM lineage_records"):
        hits = [i.get("belief_id") for i in (json.loads(injected) if injected else []) if i.get("cited") == 1]
        if hits:
            cited_records += 1
            cited_beliefs.update(h for h in hits if h is not None)
    out["cited"] = {"records_with_a_cited_injection": cited_records, "distinct_cited_beliefs": len(cited_beliefs)}
    return out


def git_history(repo: Path) -> dict:
    """fire's semantics by code version, from the checkout's own history."""
    def run(*args) -> str:
        cp = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=120)
        return cp.stdout.strip() if cp.returncode == 0 else ""
    shallow = (repo / ".git" / "shallow").is_file()
    oldest = run("log", "--reverse", "--format=%h %ad", "--date=short", "--max-count=1") if not shallow else \
        run("log", "--format=%h %ad", "--date=short").splitlines()[-1:] and run("log", "--format=%h %ad", "--date=short").splitlines()[-1]
    out = {"shallow_clone": shallow, "history_begins": oldest or "unknown",
           "note": ("SHALLOW CLONE: commits before the boundary are not visible here; the boundaries below are "
                    "incomplete and the earliest listed is a floor, not the first change") if shallow else
                   "full history: the boundaries below are complete for the named paths", "boundaries": []}
    for label, paths, pattern in VERSION_PROBES:
        args = ["log", "--format=%h %ad %s", "--date=short"]
        if pattern:
            args += ["-S", pattern]
        args += ["--", *paths]
        lines = run(*args).splitlines()
        out["boundaries"].append({"what": label, "paths": list(paths), "search": pattern,
                                  "commits_newest_first": lines})
    return out


def main() -> int:
    force_utf8_stdio()
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="the instance's kb.db (lineage lives there); opened read-only")
    ap.add_argument("--frozen-since", default=os.environ.get("NEXUS_LEARNING_FROZEN_SINCE") or DEFAULT_FROZEN_SINCE,
                    help="the operator's asserted date (YYYY-MM-DD); default from NEXUS_LEARNING_FROZEN_SINCE, else 2026-10-01")
    ap.add_argument("--selftest", action="store_true",
                    help="attempt one UPDATE matching no row on lineage_records and print the refusal; "
                         "a file without that table fails the selftest (exit 3)")
    ap.add_argument("--json", action="store_true", help="print the facts as one JSON document after the lines")
    args = ap.parse_args()
    path = args.db
    print(f"database: {store_label(path)} (opened read-only: {read_only_how(path)})", flush=True)
    exists, why = store_exists(path)
    if not exists:
        print(f"REFUSED: {why}")
        return 2
    conn = open_read_only(path, url_ok=True)                  # W3b part 3: a database URL is taken too
    if args.selftest:
        # W1 — the refusal itself (scripts/_readonly.py), never any
        # DatabaseError, probed on a table this store must have: a misopened
        # file raises "no such table" and fails the selftest (part 5f).
        try:
            print(f"selftest: write refused ({assert_write_refused(conn, table='lineage_records')})")
        except AssertionError as e:
            print(f"SELFTEST FAILED: {e}")
            return 3
    f = facts(conn, args.frozen_since)
    conn.close()
    print(f"1. earliest lineage record: {json.dumps(f['earliest_record'])}  (records: {f['records']})")
    if f["learning_mode_column"]:
        fb = f["frozen_by_assertion"]
        print(f"2. frozen-by-assertion (ts >= {args.frozen_since} = epoch {f['frozen_since_epoch_utc_midnight']:.0f} UTC midnight, "
              f"learning_mode NULL, before the LS boundary): {fb['count']} record(s), expected {fb['expected_on_the_instance']}")
        for r in fb["records"]:
            print(f"     {r['lineage_id']}  {r['iso_utc']}")
        print(f"     interval: {fb['interval']}")
        ls = f["ls_boundary"]
        print(f"3. LS boundary: first record with learning_mode: {json.dumps(ls['first_record_with_learning_mode'])}")
        print(f"   first frozen record: {json.dumps(ls['first_record_frozen'])}; frozen records {ls['records_frozen']}, "
              f"NULL (live or pre-LS) {ls['records_live_or_null']}")
    else:
        print("2./3. the store has no learning_mode column: every record predates LS; frozen-by-assertion is every record "
              f"from {args.frozen_since}, and the boundary is not in this store")
    print(f"   cited: {json.dumps(f['cited'])}")
    h = git_history(ROOT)
    print(f"4. fire semantics by code version ({'SHALLOW clone' if h['shallow_clone'] else 'full history'}; history begins {h['history_begins']}):")
    print(f"   {h['note']}")
    for b in h["boundaries"]:
        print(f"   {b['what']} [{', '.join(b['paths'])}{' -S ' + b['search'] if b['search'] else ''}]:")
        for line in b["commits_newest_first"] or ["(no commit visible)"]:
            print(f"     {line}")
    print("   reconstruction holds only within one fire semantics (F.5 citation-driven fire; B-43 counts writes; FZ-2 frozen "
          "skips the write; LS records the mode) and only from the earliest record above.")
    if args.json:
        print(json.dumps({**f, "git": h}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
