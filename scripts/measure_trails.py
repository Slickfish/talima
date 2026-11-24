#!/usr/bin/env python3
"""What do relation trails find on THIS store — and what do they refuse?

THE MEASUREMENT AB.1 SHIPS WITH (AB.0.8)
    A trace count is not a measurement — it grows whether or not the traces
    are correct. What this prints instead:

      separation   every recorded pair with all three legs shown (events,
                   recurrence, shared_(s,o)), classified. The claim is that
                   frequency alone does NOT separate (AB.0 measured genuine 8
                   vs topic 7/6, PMI inverted) and shared_(s,o) does.
      sensitivity  the declared miss-set: pairs clearing frequency that the
                   structural leg cannot confirm. AB.0's known instance is
                   affinity_for ~ demonstrates_affinity_for — genuine, but
                   its objects differ in spelling, so the bound is TERM
                   identity, which is out of AB's scope on purpose.
      control      the OQ-2 opposition control, ported unchanged: no active
                   trail may cross the configured opposition set, evaluated
                   over the reachable set. This prints the vetoed components
                   and FAILS (exit 3) if an active trail crosses one — the
                   arm that killed OQ-2 applies to its successor verbatim.
      reach        for each active trail, the live rows the channel could
                   admit that lexical search on the other spelling's rows
                   would not carry — what turning NEXUS_TRAIL_SLOTS on would
                   actually change.

WHAT IT DOES NOT DO
    It never opens the live store for writing: the file is COPIED and the
    original's md5 is printed before and after (W.2's harness discipline).
    It does not import backend.server (H.2). No model, no embeddings —
    trails are counted from lineage records, and an embedder here would be
    OQ-2 retried.

USAGE
    python scripts/measure_trails.py --store data/kb.db
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.kb.trails import (MIN_EVENTS, MIN_RECURRENCE,  # noqa: E402
                               TrailStore)
from scripts._console import force_utf8_stdio               # noqa: E402
from scripts._env import load_env                           # noqa: E402


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    force_utf8_stdio()
    load_env()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default="data/kb.db")
    args = ap.parse_args()

    src = Path(args.store)
    if not src.exists():
        print(f"no store at {src}")
        return 2
    md5_before = _md5(src)
    print(f"store: {src}  md5 {md5_before}")

    workdir = Path(tempfile.mkdtemp(prefix="nexus-trails-"))
    copy = workdir / "kb.db"
    shutil.copyfile(src, copy)

    trails = TrailStore(copy)
    trails.scan_new()                    # cursor init on the copy
    filled = trails.backfill()           # Law 3 is the operator running this
    print(f"backfill on the COPY: {filled.get('records', 0)} records, "
          f"{filled.get('events', 0)} co-citation events\n")

    rows = trails.conn.execute(
        "SELECT * FROM relation_trails ORDER BY event_count DESC").fetchall()
    c = trails._classified()
    state_of = {}
    for name in ("active", "structurally_unconfirmed", "vetoed_closure",
                 "below_floor"):
        for pair in c[name]:
            state_of[tuple(pair)] = name

    print(f"{'pair':<64} ev  rec  s(s,o)  state")
    for r in rows[:30]:
        pair = (r["rel_a"], r["rel_b"])
        shared = trails._shared_so(*pair)
        print(f"{(r['rel_a'][:30] + ' ~ ' + r['rel_b'][:30]):<64} "
              f"{r['event_count']:<3} {r['recurrence_count']:<4} "
              f"{shared:<7} {state_of.get(pair, '?')}")
    if len(rows) > 30:
        print(f"… {len(rows) - 30} further pairs not listed "
              f"(all classified in the counts below)")

    print(f"\nfloors (PROVISIONAL): events >= {MIN_EVENTS}, "
          f"recurrence >= {MIN_RECURRENCE}")
    print(f"recorded pairs            : {c['recorded']}")
    print(f"active                    : {len(c['active'])}")
    print(f"below floor               : {len(c['below_floor'])}")
    print(f"structurally unconfirmed  : {len(c['structurally_unconfirmed'])}"
          f"   <- the sensitivity bound, declared")
    for p in c["structurally_unconfirmed"]:
        print(f"    {p[0]} ~ {p[1]}")
    print(f"vetoed (closure)          : {len(c['vetoed_closure'])}")
    for p in c["vetoed_closure"]:
        print(f"    {p[0]} ~ {p[1]}")

    # ---- the OQ-2 control, ported --------------------------------------
    from backend.cortex.contradiction import load_pairs
    oppositions = {frozenset(p) for p in load_pairs()}
    crossing = [p for p in c["active"] if frozenset(p) in oppositions]
    print(f"\nOPPOSITION CONTROL: active trails crossing the opposition "
          f"set: {len(crossing)}")
    if crossing:
        print(f"  *** CONTROL FAILED: {crossing} — the veto is not holding; "
              f"do not enable the channel ***")

    # ---- reach: what would the channel actually add? --------------------
    for a, b in c["active"]:
        n_a = trails.conn.execute(
            "SELECT COUNT(*) FROM triples WHERE relation=? AND archived=0",
            (a,)).fetchone()[0]
        n_b = trails.conn.execute(
            "SELECT COUNT(*) FROM triples WHERE relation=? AND archived=0",
            (b,)).fetchone()[0]
        print(f"\nactive: {a} ~ {b} — {n_a} and {n_b} live rows; a query "
              f"reaching either spelling can now admit same-subject rows "
              f"of the other (capped by NEXUS_TRAIL_SLOTS)")

    trails.close()
    md5_after = _md5(src)
    print(f"\noriginal store md5 after : {md5_after}  "
          f"(unchanged: {md5_after == md5_before})")
    if md5_after != md5_before:
        print("*** THE ORIGINAL MOVED — this run is invalid ***")
        return 4
    return 3 if crossing else 0


if __name__ == "__main__":
    raise SystemExit(main())
