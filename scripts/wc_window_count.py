"""WC S1 item 1 — the extraction window's reach on the operating instance,
read-only, printed as facts.

    python scripts/wc_window_count.py --db data/kb.db [--capture data/citation_capture.jsonl] [--json]

Claude Code has no instance; the operator runs this on theirs. It opens the
database READ-ONLY (sqlite `mode=ro`, plus an authorizer that refuses every
write), prints the path it opened on its FIRST line, and answers WC's four
S1 questions about how far the 4,000-character extraction window reached:

  1. lineage records: the total, how many carry `answer_chars` (C-38 onward),
     and how many of those exceed the window — with the character count
     beyond the window per record and the earliest/latest timestamps of each
     population;
  2. AO capture rows (B-20) that hold the answer text: how many, how many
     exceed the window, the overflow per row, and the population's span;
  3. the unrecoverable gap: records with neither `answer_chars` nor an AO row
     — the honest size of what cannot be measured, never estimated;
  4. the reasoning condition, stated from the code, not the store (printed as
     a fixed line so the operator's run carries it beside the numbers).

`EXTRACTION_MAX_CHARS` is read from `backend.cortex.extraction`, not
hard-coded here, so this script measures the window the code actually uses.
A record's `answer_chars` is the STATED answer's length (C-38); the window
cuts the same text extraction reads, so `answer_chars > EXTRACTION_MAX_CHARS`
is exactly "this answer was silently subtracted from before extraction".

Nothing here writes. `--selftest` attempts one UPDATE that matches no row on
`lineage_records`, the table this store must have, and prints the refusal;
the table is resolved before SQLite's authorizer is consulted, so a misopened
file says "no such table" and the selftest FAILS (exit 3) instead of printing
a refusal (W1 part 5f).
"""
from __future__ import annotations

import argparse
import datetime as _dt
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
from scripts._readonly import WRITE_ACTIONS, assert_write_refused, open_read_only  # noqa: E402,F401

# The reasoning condition, stated from the code (S1 item 3). Printed beside
# the numbers so the operator's run carries it. The commit range is the one
# WC names: the pre-C-38 providers read `content` only and dropped the
# thinking field, so on a SEPARATING daemon reasoning never reached
# extraction; C-38 (merged at b2dab78) made that a boundary in code.
REASONING_LINE = (
    "reasoning condition (from the code, not the store): before C-38 both "
    "providers read message.content only and dropped the separate thinking "
    "field, so for every turn served by a daemon that SEPARATED reasoning, "
    "none of it reached extraction — by construction, nothing to count. "
    "C-38 (merged at b2dab78) made that a boundary in backend/providers. "
    "For a daemon that did NOT separate (older than Ollama v0.12.4), the "
    "store records the daemon version nowhere before 18 October 2026, so "
    "those turns are UNCHECKABLE, not clean — stated, never estimated."
)


def iso(ts: float | None) -> str:
    if ts is None:
        return "null"
    return _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc).isoformat()


def _ao_rows(path: Path) -> list[dict]:
    """AO capture rows that hold answer text. Missing file = none (off means
    nothing); a malformed line is skipped, not fatal."""
    if not path.is_file():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("answer"), str):
                out.append(row)
    return out


def facts(conn: sqlite3.Connection, ao_path: Path, window: int) -> dict:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(lineage_records)")}
    has_answer_chars = "answer_chars" in cols
    total = conn.execute("SELECT COUNT(*) AS n FROM lineage_records").fetchone()["n"]

    # 1. Records with answer_chars, and those over the window.
    lineage: dict = {"records_total": total, "has_answer_chars_column": has_answer_chars}
    if has_answer_chars:
        with_chars = conn.execute(
            "SELECT lineage_id, ts, answer_chars FROM lineage_records "
            "WHERE answer_chars IS NOT NULL ORDER BY ts ASC").fetchall()
        over = [r for r in with_chars if r["answer_chars"] > window]
        lineage.update({
            "with_answer_chars": len(with_chars),
            "with_answer_chars_span": ([iso(with_chars[0]["ts"]), iso(with_chars[-1]["ts"])]
                                       if with_chars else None),
            "over_window": len(over),
            "over_window_span": ([iso(over[0]["ts"]), iso(over[-1]["ts"])] if over else None),
            "overflow_per_record": [
                {"lineage_id": r["lineage_id"], "ts": r["ts"], "iso_utc": iso(r["ts"]),
                 "answer_chars": r["answer_chars"], "beyond_window": r["answer_chars"] - window}
                for r in over],
        })
    else:
        lineage.update({"with_answer_chars": 0, "over_window": 0,
                        "note": "no answer_chars column: every record predates C-38 (unmeasurable here)"})

    # 2. AO capture rows with answer text, and those over the window.
    ao = _ao_rows(ao_path)
    ao_over = [r for r in ao if len(r["answer"]) > window]
    ao_ts = sorted(float(r["ts"]) for r in ao if isinstance(r.get("ts"), (int, float)))
    capture = {
        "capture_file": ao_path.resolve().as_posix(),
        "present": ao_path.is_file(),
        "rows_with_answer": len(ao),
        "over_window": len(ao_over),
        "span": ([iso(ao_ts[0]), iso(ao_ts[-1])] if ao_ts else None),
        "overflow_per_row": [
            {"lineage_id": r.get("lineage_id"), "ts": r.get("ts"),
             "answer_len": len(r["answer"]), "beyond_window": len(r["answer"]) - window}
            for r in ao_over],
    }

    # 3. The unrecoverable gap: records with neither answer_chars nor an AO row.
    ao_ids = {r.get("lineage_id") for r in ao if r.get("lineage_id")}
    if has_answer_chars:
        rows = conn.execute("SELECT lineage_id, answer_chars FROM lineage_records").fetchall()
        unrecoverable = [r["lineage_id"] for r in rows
                         if r["answer_chars"] is None and r["lineage_id"] not in ao_ids]
    else:
        rows = conn.execute("SELECT lineage_id FROM lineage_records").fetchall()
        unrecoverable = [r["lineage_id"] for r in rows if r["lineage_id"] not in ao_ids]
    gap = {
        "count": len(unrecoverable),
        "of_total": total,
        "note": ("records with neither answer_chars (pre-C-38) nor an AO capture row: "
                 "the answer's length is not recoverable, so whether the window cut it "
                 "is UNMEASURABLE — reported, never estimated"),
    }

    return {"window": window, "lineage": lineage, "capture": capture,
            "unrecoverable": gap, "reasoning": REASONING_LINE}


def main() -> int:
    force_utf8_stdio()
    load_env()
    from backend.cortex.extraction import EXTRACTION_MAX_CHARS           # noqa: E402
    from backend.eval.citation_capture import capture_path              # noqa: E402

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="the instance's kb.db (lineage lives there); opened read-only")
    ap.add_argument("--capture", default=None,
                    help="the AO capture file; default NEXUS_CITATION_CAPTURE_PATH, else data/citation_capture.jsonl")
    ap.add_argument("--selftest", action="store_true",
                    help="attempt one UPDATE matching no row on lineage_records and print the refusal; "
                         "a file without that table fails the selftest (exit 3)")
    ap.add_argument("--json", action="store_true", help="print the facts as one JSON document after the lines")
    args = ap.parse_args()

    db = Path(args.db)
    ao = Path(args.capture) if args.capture else capture_path()
    if not ao.is_absolute():
        ao = ROOT / ao
    print(f"database: {db.resolve().as_posix()} (opened read-only: mode=ro + write authorizer)", flush=True)
    if not db.is_file():
        print(f"REFUSED: {db} is not a file")
        return 2
    conn = open_read_only(db)
    if args.selftest:
        # W1 — the refusal itself (scripts/_readonly.py), never any
        # DatabaseError, probed on a table this store must have: a misopened
        # file raises "no such table" and fails the selftest (part 5f).
        try:
            print(f"selftest: write refused ({assert_write_refused(conn, table='lineage_records')})")
        except AssertionError as e:
            print(f"SELFTEST FAILED: {e}")
            return 3

    f = facts(conn, ao, EXTRACTION_MAX_CHARS)
    conn.close()
    lg, cap, gap = f["lineage"], f["capture"], f["unrecoverable"]
    print(f"window: EXTRACTION_MAX_CHARS = {f['window']} (read from backend.cortex.extraction)")
    print(f"1. lineage records: {lg['records_total']} total; with answer_chars (C-38 onward): "
          f"{lg['with_answer_chars']} (span {lg.get('with_answer_chars_span')}); "
          f"of those OVER {f['window']}: {lg['over_window']} (span {lg.get('over_window_span')})")
    for r in lg.get("overflow_per_record", []):
        print(f"   - {r['lineage_id']} {r['iso_utc']}: {r['answer_chars']} chars, {r['beyond_window']} beyond the window")
    print(f"2. AO capture rows with answer text: {cap['rows_with_answer']} "
          f"({'file present' if cap['present'] else 'no capture file'}, span {cap['span']}); "
          f"of those OVER {f['window']}: {cap['over_window']}")
    for r in cap.get("overflow_per_row", []):
        print(f"   - {r['lineage_id']}: {r['answer_len']} chars, {r['beyond_window']} beyond the window")
    print(f"3. UNRECOVERABLE (neither answer_chars nor an AO row): {gap['count']} of {gap['of_total']} "
          f"— the honest size of the gap; whether the window cut these is unmeasurable, not estimated")
    print(f"4. {f['reasoning']}")
    if args.json:
        print(json.dumps(f, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
