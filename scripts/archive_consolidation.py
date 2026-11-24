#!/usr/bin/env python3
"""Packet L.2b — archive the hippocampus-consolidation artefacts.

WHAT THIS DOES, AND WHAT IT DELIBERATELY DOES NOT
=================================================
Removes rows written by ONE source from the retrieval surface, leaving them
wholly intact on the record surface. Nothing is deleted (Law 4). Every
archived row keeps its id, text, weight, tier, source and created_at, stays
resolvable by `get_by_id`, stays citable in lineage, and can be restored in
one call with the id list this script writes.

**The selector is the source column and nothing else.** No gate rule enters
into this pass — this file does not import `backend.kb.gate` and a sentinel
asserts that it never will. That is the whole reason L.2b runs before L.2c:
the consolidation rows are artefacts of a defect Packet E fixed, so removing
them from retrieval is a provenance judgement, not a shape judgement, and it
carries none of the false-positive risk that L.0.9 measured against the gate's
rules (40 of 66 legitimate concepts refused under strict classes — `WHO`,
`O'Brien`, `1984`, `To Kill a Mockingbird`). Those rules were calibrated for
the door; pointing them at the furniture is L.2c's problem, gated on a
human reading the surfaces first.

WHY THESE ROWS AND NOT DECAY
============================
L.0.5 measured that decay does NOT remove a belief from retrieval. A
consolidation row aged past the floor sits at tier 3 with `current_weight`
0.000 and is still returned by `search_terms`, `get_relevant` and
`TripleRetriever.retrieve`, because ranking is by the STORED weight — a
monotone record field — and no retrieval path applies a prominence floor.
Tier and decay govern rank. Only `archived` governs presence.

THE EXEMPTION
=============
A belief cited by a SEALED lineage record is load-bearing: it fed a decision
someone may have to defend. Those rows are exempt and are never archived by
this pass, even though L.1 measured that archiving them would be safe (they
stay resolvable, flagged `archived: true`, with all three tamper layers
`intact`). Safe is not the same as free: the cost of keeping a handful of
artefacts in retrieval is far below the cost of one argument about whether an
answer's evidence was tampered with.

**A NULL or missing `cited` flag counts as CITED.** F.5 added that flag; every
record written before it lacks the key. Reading a missing key as "not cited"
would silently strip the exemption from exactly the oldest records — the ones
most likely to matter and least able to defend themselves.

LAW 3
=====
Dry run is the default and writes nothing to the store. `--confirm` is
required to archive. Both modes write a report; the confirmed report is the
committed record of what happened, and carries every archived id so the pass
is reversible in one call.

USAGE
    python scripts/archive_consolidation.py                     # dry run
    python scripts/archive_consolidation.py --kb data/kb.db     # dry run
    python scripts/archive_consolidation.py --confirm           # archives
    python scripts/archive_consolidation.py --report out.json

REVERSAL
    python - <<'PY'
    import json
    from backend.kb.tiered_store import TripleStore
    rec = json.load(open("reports/l2b_archive_<ts>.json", encoding="utf-8"))
    print(TripleStore("data/kb.db").unarchive_ids(rec["archived_ids"]))
    PY

This script imports `backend.kb` and `backend.memory.lineage` only — never
`backend.server`. Packet J made that import side-effect-free, but the path
defaults are still relative, so an explicit `create_app()` from the wrong
working directory still creates a `data/` tree (H.2 measured it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
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
from _readonly import is_url, open_read_only, redacted, store_exists, store_line, store_target  # noqa: E402
from _env import load_env  # noqa: E402

from backend.kb.tiered_store import TripleStore  # noqa: E402
# The exemption rule lives in ONE place. `verify_packet_k.py` §3b imports the
# same function to report which gate-rejected rows are sealed-cited, so the
# list an operator reads and the set this pass exempts cannot drift apart.
from backend.memory.lineage import sealed_cited_belief_ids  # noqa: E402

# Retained as the historical name this script used; it is the same object.
sealed_cited_ids = sealed_cited_belief_ids

# The source whose rows this pass removes from retrieval. A source value, not
# a rule — see the module docstring.
DEFAULT_SOURCE = "hippocampus_consolidation"

# The archival judgement, stamped on every row (L.2a's `archive_reason`).
# A THIRD reason class beside `dedup` and `gate:<rule>`, and deliberately not
# spelled `gate:` — calling a provenance judgement a gate rule would
# misdescribe it in the one field whose whole purpose is saying which
# judgement was made.
REASON_PREFIX = "bulk:source="


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# The plan — pure read. Runs unchanged against a read-only connection.
# --------------------------------------------------------------------------

def plan(conn: sqlite3.Connection, source: str) -> dict:
    """Everything the pass would do, computed without writing anything."""
    total = conn.execute("SELECT COUNT(*) AS n FROM triples").fetchone()["n"]
    retrievable_before = conn.execute(
        "SELECT COUNT(*) AS n FROM triples WHERE archived = 0").fetchone()["n"]

    rows = conn.execute(
        "SELECT id, subject, relation, object, tier, weight, archived "
        "FROM triples WHERE source = ? ORDER BY id", (source,)).fetchall()

    exempt_ids, lineage_stats = sealed_cited_ids(conn)

    already = [r["id"] for r in rows if r["archived"]]
    live = [r for r in rows if not r["archived"]]
    exempt = [r["id"] for r in live if r["id"] in exempt_ids]
    targets = [r["id"] for r in live if r["id"] not in exempt_ids]

    by_relation = Counter(r["relation"] for r in live if r["id"] in targets)
    by_tier = Counter(r["tier"] for r in live if r["id"] in targets)

    return {
        "source":              source,
        "reason":              REASON_PREFIX + source,
        "triples_total":       total,
        "retrievable_before":  retrievable_before,
        "source_rows":         len(rows),
        "already_archived":    len(already),
        "already_archived_ids": already,
        "exempt_sealed_cited": len(exempt),
        "exempt_ids":          exempt,
        "candidates":          len(targets),
        "candidate_ids":       targets,
        "by_relation":         dict(by_relation.most_common()),
        "by_tier":             {str(k): v for k, v in sorted(by_tier.items())},
        "lineage":             lineage_stats,
        "sealed_cited_universe": len(exempt_ids),
    }


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def render(p: dict, *, confirmed: bool, kb: Path,
           md5_before: str, md5_after: str | None,
           archived: int | None, retrievable_after: int | None) -> str:
    L = []
    a = L.append
    a("=" * 74)
    a(f"  Packet L.2b — archive source={p['source']}")
    a(f"  {'CONFIRMED — rows were archived' if confirmed else 'DRY RUN — nothing was written'}")
    a("=" * 74)
    a(f"  store              : {kb}")
    a(f"  md5 before         : {md5_before}")
    if md5_after is not None:
        a(f"  md5 after          : {md5_after}"
          f"{'   (UNCHANGED)' if md5_after == md5_before else ''}")
    a("")
    a(f"  triples (record)   : {p['triples_total']}")
    a(f"  retrievable before : {p['retrievable_before']}")
    if retrievable_after is not None:
        a(f"  retrievable after  : {retrievable_after}")
    a("")
    a(f"  rows with this source          : {p['source_rows']}")
    a(f"    already archived (untouched) : {p['already_archived']}")
    a(f"    EXEMPT — sealed-record cited : {p['exempt_sealed_cited']}")
    a(f"    candidates                   : {p['candidates']}")
    if archived is not None:
        a(f"    ARCHIVED                     : {archived}")
    a("")
    a("  exempt ids : " + (", ".join(str(i) for i in p["exempt_ids"])
                           if p["exempt_ids"] else "(none)"))
    a("")
    a("  -- the exemption, measured --")
    ls = p["lineage"]
    if "error" in ls:
        a(f"    {ls['error']}")
    else:
        a(f"    lineage records         : {ls['records_total']}")
        a(f"    sealed                  : {ls['records_sealed']}")
        a(f"    injected items (sealed) : {ls['items']}")
        a(f"      cited = true          : {ls['cited_true']}")
        a(f"      cited = NULL/missing  : {ls['cited_null']}  (counts as CITED)")
        a(f"      cited = false         : {ls['cited_false']}  (no exemption)")
        a(f"    distinct exempt beliefs : {p['sealed_cited_universe']}  "
          f"(across ALL sources)")
    a("")
    a("  -- candidates by relation --")
    for rel, n in list(p["by_relation"].items())[:12]:
        a(f"    {rel:<28} {n}")
    a("  -- candidates by tier --")
    for tier, n in p["by_tier"].items():
        a(f"    tier {tier}                      {n}")
    a("")
    if not confirmed:
        a("  Law 3 — nothing was archived. Re-run with --confirm to apply.")
    else:
        a("  Reversal: unarchive_ids(record['archived_ids']) restores every row.")
    a("=" * 74)
    return "\n".join(L)


# --------------------------------------------------------------------------

def main() -> int:
    # M.4 — the first statement, before anything is printed. Scripts
    # print em dashes; a piped stdout would otherwise carry cp1252
    # bytes that a UTF-8 reader cannot decode. See scripts/_console.py.
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--source", default=DEFAULT_SOURCE,
                    help="the ONLY selector. A source value, never a rule.")
    ap.add_argument("--confirm", action="store_true",
                    help="Law 3 — required to archive. Default is a dry run.")
    ap.add_argument("--report", help="write the JSON record here "
                                     "(default: reports/l2b_archive_<ts>.json)")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    kb = args.kb                                   # a path, or (W3b part 3) a database URL
    exists, why = store_exists(kb)
    if not exists:
        print(f"no such store: {why}", file=sys.stderr)
        return 2
    md5_before = md5(Path(kb)) if not is_url(kb) else None   # a file's bytes; a database has no such figure

    if not args.confirm:
        # DRY RUN — read-only, twice over: mode=ro, and no write path is taken.
        conn = open_read_only(kb, row_factory=None, url_ok=True)
        conn.row_factory = sqlite3.Row
        p = plan(conn, args.source)
        conn.close()
        archived = retrievable_after = None
        md5_after = md5(Path(kb)) if not is_url(kb) else None
        assert md5_after == md5_before, "dry run modified the store"
    else:
        # CONFIRMED. TripleStore's constructor runs the additive L.2a
        # migration; that is a schema write and the only one before the
        # archive itself.
        # W2 — the single-writer lock, before the store opens: a NEXUS
        # process serving this store refuses the run by name (exit 7).
        from backend.store_lock import EXIT_STORE_LOCKED, StoreLocked, StoreLockUnavailable
        from backend.store_lock import acquire as acquire_store_lock
        try:
            acquire_store_lock(store_target(kb), mode="script:archive_consolidation")
        except (StoreLocked, StoreLockUnavailable) as e:
            print(f"REFUSED: {e}. Nothing was archived.", file=sys.stderr)
            return EXIT_STORE_LOCKED
        store = TripleStore(store_target(kb))
        p = plan(store.conn, args.source)
        archived = store.archive_ids(p["candidate_ids"], reason=p["reason"])

        # Post-conditions, asserted rather than assumed.
        assert archived == p["candidates"], (
            f"archived {archived} rows, planned {p['candidates']}")
        for eid in p["exempt_ids"]:
            t = store.get_by_id(eid)
            assert t is not None and not t.archived, \
                f"exempt belief {eid} was archived"
        assert store.count() == p["triples_total"], \
            "the record count changed — that is deletion, not archival"
        retrievable_after = store.count(archived=False)
        assert retrievable_after == p["retrievable_before"] - archived
        store.close()
        md5_after = md5(Path(kb)) if not is_url(kb) else None

    record = {
        "packet":       "L.2b",
        "timestamp":    time.time(),
        "confirmed":    bool(args.confirm),
        "kb":           redacted(kb) if is_url(kb) else str(kb),
        "md5_before":   md5_before,
        "md5_after":    md5_after,
        "archived":     archived,
        "archived_ids": p["candidate_ids"] if args.confirm else [],
        "retrievable_after": retrievable_after,
        **p,
    }
    out = Path(args.report) if args.report else Path(
        "reports") / f"l2b_archive_{int(record['timestamp'])}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True),
                   encoding="utf-8")

    print(render(p, confirmed=bool(args.confirm), kb=(redacted(kb) if is_url(kb) else kb),
                 md5_before=md5_before, md5_after=md5_after,
                 archived=archived, retrievable_after=retrievable_after))
    print(f"\n  record written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
