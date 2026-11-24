#!/usr/bin/env python3
"""Packet F verification — read-only measurement of the real store.

Packet F changed how prominence works. This measures the change on YOUR
store, because the development sandbox holds one triple and can prove nothing
about a store with 787.

READ-ONLY, TWICE OVER
    1. The real database is opened `mode=ro` and is never written.
    2. Everything that mutates — the F.2 migration, ten rebalance passes —
       runs on a COPY in a temp directory, which is deleted afterwards.

    An audit that mutates is not an audit. If this script crashes halfway,
    your store is untouched.

    It deliberately does NOT import `backend.server`: that module runs
    `app = create_app()` at import scope (server.py:992), which builds a full
    production stack against the real `data/` tree. Importing it to measure
    the store would be the exact failure this script exists to avoid.

USAGE
    python scripts/verify_packet_f.py                     # defaults to data/kb.db
    python scripts/verify_packet_f.py --kb path/to/kb.db
    python scripts/verify_packet_f.py --json report.json  # also machine-readable

WHAT TO LOOK FOR, in order of importance
    [1] RECORD INTEGRITY must read UNCHANGED on every line. That is the claim
        Packet F rests on: prominence is lossy, the record is not.
    [2] CONVERGENCE must say "converged after pass 1". If tiers are still
        moving at pass 10, `rebalance()` is not the projection F.3 claims.
    [3] THE FEEDBACK LOOP section settles the E.4 question that has been open
        since Packet E: are the co-activation rows re-firing themselves?
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# M.4 — scripts print em dashes and box rules; a piped stdout would
# otherwise be encoded with the platform default (cp1252 on Windows) and
# the byte 0x97 kills a UTF-8 reader. Fixed at the writer, not left to
# the environment. See scripts/_console.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import force_utf8_stdio  # noqa: E402
from _readonly import open_read_only, store_line  # noqa: E402
from _env import load_env  # noqa: E402

# backend.kb only — never backend.server (see module docstring).
from backend.kb.citation import is_cited                       # noqa: E402
from backend.kb.retriever import TripleRetriever, extract_terms  # noqa: E402
from backend.kb.schema import migrate                          # noqa: E402
from backend.kb.tiered_store import (                          # noqa: E402
    CLASS_DECAY_PER_DAY, LOWEST_TIER, TripleStore, band_for, classify,
)

DAY = 86400.0
RECORD_FIELDS = ("subject", "relation", "object", "weight", "source", "created_at")

# AUDIT_READINESS_AND_VALIDATION.md §3.5 — the fixed retrieval probe set.
# (PACKET_F_REVISED cites this as REGROUND §3.5; REGROUND's §3.5 is the
# prediction-row provenance section. Same number, different document.)
PROBES = [
    ("exact-term", "what is python"),
    ("paraphrase", "which coding languages do you know about"),
    ("greeting",   "hi"),
]

out: dict = {}


def h(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def ro_connect(db: Path) -> sqlite3.Connection:
    """Genuinely read-only: SQLite refuses writes on this handle."""
    conn = open_read_only(db, row_factory=None)
    conn.row_factory = sqlite3.Row
    return conn


def scalar(conn, sql, args=()):
    row = conn.execute(sql, args).fetchone()
    return row[0] if row else None


def table_exists(conn, name) -> bool:
    return scalar(conn,
                  "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                  (name,)) > 0


def histogram(values, edges=(0.0, 0.2, 0.4, 0.6, 0.8, 1.01)) -> str:
    if not values:
        return "  (none)"
    lines = []
    for lo, hi in zip(edges, edges[1:]):
        n = sum(1 for v in values if lo <= v < hi)
        bar = "#" * min(40, round(40 * n / max(1, len(values))))
        lines.append(f"  [{lo:.1f},{hi:.1f})  {n:>5}  {bar}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------

def before_state(conn) -> dict:
    h("[0] BEFORE — the store as it stands")
    total = scalar(conn, "SELECT COUNT(*) FROM triples")
    tiers = {int(r["tier"]): int(r["n"]) for r in conn.execute(
        "SELECT tier, COUNT(*) n FROM triples GROUP BY tier ORDER BY tier")}
    sources = {r["source"]: int(r["n"]) for r in conn.execute(
        "SELECT source, COUNT(*) n FROM triples GROUP BY source ORDER BY n DESC")}
    weights = [float(r["weight"]) for r in conn.execute("SELECT weight FROM triples")]
    legacy4 = scalar(conn, "SELECT COUNT(*) FROM triples WHERE tier >= 4")

    print(f"triples                : {total}")
    print(f"tier distribution      : {tiers}")
    print(f"rows at tier >= 4      : {legacy4}   <- F.2 will migrate these to archived")
    print("\nsources, and the evidence class each now decays at:")
    for src, n in sources.items():
        cls = classify(src)
        print(f"  {src:<28} {n:>5}   {cls:<14} {CLASS_DECAY_PER_DAY[cls]}/day")
    print("\nstored weight histogram (the RECORD — must not move):")
    print(histogram(weights))

    lineage = sealed = None
    if table_exists(conn, "lineage_records"):
        lineage = scalar(conn, "SELECT COUNT(*) FROM lineage_records")
        sealed = scalar(conn,
                        "SELECT COUNT(*) FROM lineage_records WHERE sealed_block IS NOT NULL")
        print(f"\nlineage records        : {lineage} ({sealed} sealed)")

    return {"total": total, "tiers": tiers, "sources": sources,
            "legacy_tier4": legacy4, "lineage": lineage, "sealed": sealed,
            "weight_mean": statistics.fmean(weights) if weights else 0.0}


def feedback_loop(conn) -> dict:
    """The E.4 question, open since Packet E: are the 555 re-firing themselves?"""
    h("[3] THE RETRIEVAL FEEDBACK LOOP — settles the E.4 question")
    now = time.time()
    row = conn.execute(
        "SELECT COUNT(*) n, MAX(last_fired) mx, MIN(last_fired) mn "
        "FROM triples WHERE relation = 'co_activated'").fetchone()
    n, mx, mn = int(row["n"]), row["mx"], row["mn"]
    if not n:
        print("no co_activated rows in this store — nothing to settle")
        return {"co_activated": 0}

    fired_24h = scalar(conn,
                       "SELECT COUNT(*) FROM triples WHERE relation='co_activated' "
                       "AND last_fired > ?", (now - DAY,))
    fired_7d = scalar(conn,
                      "SELECT COUNT(*) FROM triples WHERE relation='co_activated' "
                      "AND last_fired > ?", (now - 7 * DAY,))
    print(f"co_activated rows      : {n}")
    print(f"most recently fired    : {(now - mx) / DAY:.1f} days ago")
    print(f"least recently fired   : {(now - mn) / DAY:.1f} days ago")
    print(f"fired in last 24h      : {fired_24h}")
    print(f"fired in last 7 days   : {fired_7d}")
    print()
    if fired_24h:
        print(f"  >>> CONFIRMED on real data: {fired_24h} co-activation rows were")
        print("      re-fired within 24 hours. Before F.5 every retrieval reset the")
        print("      decay clock, so these rows were re-selecting themselves:")
        print("      retrieved BECAUSE prominent, strengthened FOR being retrieved.")
        print("      F.5 breaks that loop — they are only reinforced when the answer")
        print("      actually uses them. This is why E.4's tier write would not have")
        print("      stuck, and why not firing it was correct.")
    else:
        print("  >>> Not re-fired in the last 24h. Either the server has not run")
        print("      recently, or these rows are not being retrieved. Check the")
        print("      'most recently fired' figure above before drawing a conclusion.")
    return {"co_activated": n, "fired_24h": fired_24h, "fired_7d": fired_7d,
            "days_since_most_recent": (now - mx) / DAY}


def coactivation_forecast(conn) -> dict:
    """Do the 555 now sink and rest, on their real last_fired values?"""
    h("[4] THE 555 UNDER F.3 SEMANTICS — do they sink and rest?")
    rows = conn.execute(
        "SELECT weight, last_fired, tier FROM triples "
        "WHERE relation='co_activated' AND source='hippocampus_consolidation'"
    ).fetchall()
    if not rows:
        print("no matching rows")
        return {}
    now = time.time()
    rate = CLASS_DECAY_PER_DAY[classify("hippocampus_consolidation")]
    bands = Counter()
    for r in rows:
        idle = max(0.0, (now - float(r["last_fired"])) / DAY)
        cw = max(0.0, float(r["weight"]) - rate * idle)
        bands[band_for(cw)] += 1
    print(f"rows                   : {len(rows)}   decaying at {rate}/day")
    print(f"tier they land in now  : {dict(sorted(bands.items()))}")
    at_floor = bands[LOWEST_TIER]
    print(f"at the floor           : {at_floor} / {len(rows)} "
          f"({100 * at_floor / len(rows):.0f}%)")
    print()
    print("  Rows not yet at the floor are simply not stale enough yet — with")
    print("  F.5 suppressing uncited reinforcement they will get there. Re-run")
    print("  this in a week: `at the floor` should rise monotonically.")
    return {"rows": len(rows), "bands": dict(bands), "at_floor": at_floor}


def convergence_and_integrity(src: Path) -> dict:
    """Migrate + ten rebalance passes, on a COPY."""
    h("[1] RECORD INTEGRITY and [2] CONVERGENCE  (on a disposable copy)")
    tmp = Path(tempfile.mkdtemp(prefix="nexus_verify_"))
    copy = tmp / "kb.db"
    shutil.copy2(src, copy)
    for suffix in ("-wal", "-shm"):
        side = src.with_name(src.name + suffix)
        if side.exists():
            shutil.copy2(side, copy.with_name(copy.name + suffix))

    raw = sqlite3.connect(copy)
    raw.row_factory = sqlite3.Row
    summary = migrate(raw)
    raw.close()
    print(f"F.2 migration          : {summary['legacy_tier4_migrated']} legacy "
          f"tier-4 row(s) -> archived=1, tier=3; columns added "
          f"{summary['columns_added'] or 'none'}")

    store = TripleStore(copy)
    fingerprint = lambda: {t.id: tuple(getattr(t, f) for f in RECORD_FIELDS)
                           for t in store.all()}
    before_fp = fingerprint()
    before_ids = set(before_fp)
    lin_before = None
    if table_exists(store.conn, "lineage_records"):
        lin_before = (scalar(store.conn, "SELECT COUNT(*) FROM lineage_records"),
                      scalar(store.conn, "SELECT COUNT(*) FROM lineage_records "
                                         "WHERE sealed_block IS NOT NULL"))

    tiers_by_pass = []
    moves = []
    for _ in range(10):
        result = store.rebalance()
        tiers_by_pass.append({t.id: t.tier for t in store.all()})
        moves.append(result["promoted"] + result["demoted"])

    print()
    print("[2] CONVERGENCE — tier moves per rebalance pass")
    print(f"  pass:   " + "  ".join(f"{i + 1:>4}" for i in range(10)))
    print(f"  moves:  " + "  ".join(f"{m:>4}" for m in moves))
    converged = all(t == tiers_by_pass[0] for t in tiers_by_pass[1:])
    if converged and moves[1:] == [0] * 9:
        print("  >>> CONVERGED after pass 1, zero movement thereafter.")
        print("      rebalance() is an idempotent projection: the floor absorbs")
        print("      by construction, not by threshold engineering.")
    elif converged:
        print("  >>> Tiers stable after pass 1 (movement counts above are the "
              "first pass only).")
    else:
        print("  >>> *** NOT CONVERGED — tiers still moving at pass 10. ***")
        print("      This contradicts F.3's central claim. Report it.")

    after_fp = fingerprint()
    print()
    print("[1] RECORD INTEGRITY — after migration + 10 aggressive passes")
    checks = [
        ("triple count",       len(before_fp) == len(after_fp),
         f"{len(before_fp)} -> {len(after_fp)}"),
        ("triple ids",         before_ids == set(after_fp),
         "identical set" if before_ids == set(after_fp) else "CHANGED"),
        ("subject/relation/object/weight/source/created_at",
         before_fp == after_fp, "identical" if before_fp == after_fp else "CHANGED"),
    ]
    if lin_before is not None:
        lin_after = (scalar(store.conn, "SELECT COUNT(*) FROM lineage_records"),
                     scalar(store.conn, "SELECT COUNT(*) FROM lineage_records "
                                        "WHERE sealed_block IS NOT NULL"))
        checks.append(("lineage records / sealed", lin_before == lin_after,
                       f"{lin_before} -> {lin_after}"))

    all_ok = True
    for name, ok, detail in checks:
        all_ok &= ok
        print(f"  {'UNCHANGED' if ok else '*** CHANGED ***':<16} {name:<52} {detail}")
    if all_ok:
        print("\n  >>> The record is untouched. Prominence moved; nothing else did.")
    else:
        print("\n  >>> *** RECORD ALTERED — this must not happen. Report it. ***")

    start = tiers_by_pass[0]
    settled = Counter(start.values())
    print()
    print(f"tier distribution after: {dict(sorted(settled.items()))}")

    store.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return {"converged": converged, "moves_per_pass": moves,
            "record_intact": all_ok, "migrated": summary["legacy_tier4_migrated"],
            "tiers_after": dict(settled)}


def retrieval_probe(src: Path) -> dict:
    """AUDIT_READINESS_AND_VALIDATION.md §3.5, on a copy."""
    h("[5] RETRIEVAL PROBE — exact term / paraphrase / greeting")
    tmp = Path(tempfile.mkdtemp(prefix="nexus_probe_"))
    copy = tmp / "kb.db"
    shutil.copy2(src, copy)
    store = TripleStore(copy)
    retriever = TripleRetriever(store)

    results = {}
    for label, query in PROBES:
        hits = retriever.retrieve(query, top_k=10)
        results[label] = len(hits)
        print(f"\n  {label:<11} {query!r}")
        print(f"  {'':<11} terms={extract_terms(query)} -> {len(hits)} hit(s)")
        for rt in hits[:5]:
            t = rt.triple
            print(f"  {'':<13} tier={t.tier} cw={rt.score:.3f} src={t.source:<24} "
                  f"({t.subject} {t.relation} {t.object})")

    print()
    print("  NOTE — the paraphrase case is EXPECTED to under-retrieve. Retrieval")
    print("  is still lexical LIKE-matching (Phase 7's vector path is not wired")
    print("  into this query surface), so a paraphrase that shares no surface")
    print("  term with the stored triple cannot match. That is F-05/F-04's")
    print("  scope, NOT a Packet F regression — recorded here so a weak result")
    print("  is not misattributed to the decay change.")

    store.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return results


def citation_dry_run(src: Path) -> dict:
    """What would F.5 have suppressed, on beliefs this store actually holds?"""
    h("[6] CITATION DRY RUN — how selective is the F.5 gate here?")
    tmp = Path(tempfile.mkdtemp(prefix="nexus_cite_"))
    copy = tmp / "kb.db"
    shutil.copy2(src, copy)
    store = TripleStore(copy)
    retriever = TripleRetriever(store)

    # A plausible answer that names one concept but not the pairs around it —
    # the common case the gate has to judge.
    sample = "Python is a programming language often used for machine learning."
    hits = retriever.retrieve("tell me about python", top_k=20)
    cited = [rt for rt in hits if is_cited(rt.triple, sample)]
    print(f"  sample answer   : {sample!r}")
    print(f"  retrieved       : {len(hits)}")
    print(f"  would be cited  : {len(cited)}")
    print(f"  would be suppressed: {len(hits) - len(cited)}")
    for rt in cited[:5]:
        t = rt.triple
        print(f"    cited: ({t.subject} {t.relation} {t.object})")
    print()
    print("  A low cited count is the gate working as designed: it under-credits")
    print("  deliberately. A false 'cited' reopens the feedback loop invisibly;")
    print("  a false 'uncited' only lets a belief decay slightly, and the next")
    print("  turn that genuinely needs it will retrieve and cite it.")

    store.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return {"retrieved": len(hits), "cited": len(cited)}


def main() -> int:
    # M.4 — the first statement, before anything is printed. Scripts
    # print em dashes; a piped stdout would otherwise carry cp1252
    # bytes that a UTF-8 reader cannot decode. See scripts/_console.py.
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--json", help="also write the measurements to this file")
    args = ap.parse_args()

    src = Path(args.kb).resolve()
    if not src.exists():
        print(f"no database at {src}")
        return 2

    # Law 15's first line through the one formatter (W4's verifier, V3).
    print(store_line(src), flush=True)
    print(f"Packet F verification — {src}")
    print("The real store is opened read-only; all mutation runs on temp copies.")

    conn = ro_connect(src)
    if not table_exists(conn, "triples"):
        print("no `triples` table — is this a NEXUS kb.db?")
        return 2

    out["before"] = before_state(conn)
    out["integrity_and_convergence"] = convergence_and_integrity(src)
    out["feedback_loop"] = feedback_loop(conn)
    out["coactivation"] = coactivation_forecast(conn)
    out["retrieval"] = retrieval_probe(src)
    out["citation"] = citation_dry_run(src)
    conn.close()

    h("SUMMARY")
    ic = out["integrity_and_convergence"]
    print(f"  record intact          : {'YES' if ic['record_intact'] else '*** NO ***'}")
    print(f"  rebalance converged    : {'YES' if ic['converged'] else '*** NO ***'}")
    print(f"  legacy tier-4 migrated : {ic['migrated']} (none deleted)")
    fl = out["feedback_loop"]
    if fl.get("co_activated"):
        print(f"  co-activation re-fired in 24h: {fl['fired_24h']} "
              f"({'feedback loop CONFIRMED on real data' if fl['fired_24h'] else 'not observed'})")
    print()
    print("  Nothing was written to your store. Re-run any time.")

    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2, default=str),
                                   encoding="utf-8")
        print(f"  measurements written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
