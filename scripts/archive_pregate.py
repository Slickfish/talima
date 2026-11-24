#!/usr/bin/env python3
"""Packet BA fix 8 (backlog B-21, L.2c) — archive the pre-gate rows.

WHAT THIS DOES, AND WHAT IT DELIBERATELY DOES NOT
=================================================
Removes from the retrieval surface every LIVE row the ingest gate would
refuse TODAY, and nothing else. Nothing is deleted (Law 4). Every archived
row keeps its id, text, weight, tier, source and created_at, stays
resolvable by `get_by_id`, stays citable in lineage, and can be restored in
one call with the id list this script writes. Since AL every act writes a
sealed, actor-stamped `archival_events` row, and so does the reversal.

**The selector is the gate and nothing else.** The population is DERIVED:
`inspect_triple(subject, relation, object, evidence_class, weight)` over
every live row, exactly as `add()` judges a row now. There is no hand list
of bad shapes in this file and a sentinel asserts that none appears; when
a rule changes, the population moves with it (tests/test_ba_fix8_pregate_archive.py
proves that by changing one). L.2b, the sibling instrument, refuses to
import the gate for the opposite reason — its rows are a provenance
judgement — and the two stay apart.

THE FINDING THIS ANSWERS
========================
On the operator's store 107 of 447 live rows (23.9%) were refused by the
gate today, all written before 16 August, zero after — I-9 working. They
are 15.2% of injected prompt tokens against 3.4% for duplication. I-9
governs admission, not retrievability: these rows are live, reach prompts
and assert nothing (`Hemoglobin | carries_oxygen... | None`). Re-measure
with `scripts/gate_population_probe.py` after fix 5; the number after is
the one to act on.

THE EXEMPTION
=============
A belief cited by a SEALED lineage record is exempt and is never archived
by this pass (I-4), whatever the gate says of it. Forty of the operator's
107 are exempt, and they were cited by lexical coincidence under the pre-AU
test; what to do about them is the operator's decision (three options are
in the packet) and this script takes none of them. **A NULL or missing
`cited` flag counts as CITED**, as in L.2b.

LAW 3
=====
Dry run is the default and writes nothing. `--confirm` is required to
archive. Both modes write a report carrying every candidate id; the
confirmed report is the committed record and is what reverses the pass.

USAGE
    python scripts/archive_pregate.py                       # dry run
    python scripts/archive_pregate.py --kb data/kb.db       # dry run
    python scripts/archive_pregate.py --confirm             # archives
    python scripts/archive_pregate.py --report out.json

REVERSAL
    python - <<'PY'
    import json
    from backend.kb.tiered_store import TripleStore
    rec = json.load(open("reports/l2c_archive_<ts>.json", encoding="utf-8"))
    print(TripleStore("data/kb.db").unarchive_ids(rec["archived_ids"],
                                                   reason="reversal:l2c"))
    PY

This script imports `backend.kb` and `backend.memory.lineage` only — never
`backend.server`.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import force_utf8_stdio  # noqa: E402
from _readonly import is_url, open_read_only, redacted, store_exists, store_line, store_target  # noqa: E402
from _env import load_env  # noqa: E402
from backend.kb.gate import inspect_triple  # noqa: E402
from backend.kb.tiered_store import TripleStore, classify  # noqa: E402
from backend.memory.lineage import sealed_cited_belief_ids  # noqa: E402

PACKET = "L.2c"
REASON_PREFIX = "gate:"


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def select(conn: sqlite3.Connection) -> list[dict]:
    """Every live row and the gate's verdict on it, as `add()` would judge it
    now. The population is this list filtered on `reason is not None`."""
    out = []
    for r in conn.execute("SELECT id, subject, relation, object, source, weight, tier, "
                          "created_at FROM triples WHERE archived = 0 ORDER BY id"):
        cls = classify(r["source"])
        reason = inspect_triple(r["subject"], r["relation"], r["object"],
                                evidence_class=cls, weight=r["weight"])
        out.append({"id": r["id"], "reason": reason, "class": cls, "tier": r["tier"],
                    "created_at": r["created_at"],
                    "surface": f"{r['subject']} | {r['relation']} | {r['object']}"})
    return out


def plan(conn: sqlite3.Connection) -> dict:
    """Everything the pass would do, computed without writing anything."""
    total = conn.execute("SELECT COUNT(*) AS n FROM triples").fetchone()["n"]
    retrievable_before = conn.execute(
        "SELECT COUNT(*) AS n FROM triples WHERE archived = 0").fetchone()["n"]
    judged = select(conn)
    refused = [j for j in judged if j["reason"] is not None]
    exempt_ids, lineage_stats = sealed_cited_belief_ids(conn)
    exempt = [j for j in refused if j["id"] in exempt_ids]
    targets = [j for j in refused if j["id"] not in exempt_ids]
    return {
        "packet":              PACKET,
        "selector":            "backend.kb.gate.inspect_triple over every live row",
        "triples_total":       total,
        "retrievable_before":  retrievable_before,
        "live_rows_judged":    len(judged),
        "admitted":            len(judged) - len(refused),
        "refused":             len(refused),
        "exempt_sealed_cited": len(exempt),
        "exempt_ids":          [j["id"] for j in exempt],
        "candidates":          len(targets),
        "candidate_ids":       [j["id"] for j in targets],
        "candidate_reasons":   {str(j["id"]): j["reason"] for j in targets},
        "by_rule":             dict(Counter(j["reason"] for j in targets).most_common()),
        "by_class":            dict(Counter(j["class"] for j in targets)),
        "by_tier":             {str(k): v for k, v in sorted(Counter(j["tier"] for j in targets).items())},
        "latest_candidate_created_at": max((j["created_at"] for j in targets), default=None),
        "examples":            [j["surface"] for j in targets[:12]],
        "lineage":             lineage_stats,
        "sealed_cited_universe": len(exempt_ids),
    }


def render(p: dict, *, confirmed: bool, kb: Path, md5_before: str,
           md5_after: str | None, archived: int | None,
           retrievable_after: int | None) -> str:
    L = []
    a = L.append
    a("=" * 74)
    a(f"  Packet {PACKET} — archive the rows the gate refuses today")
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
    a(f"  live rows judged by the gate     : {p['live_rows_judged']}")
    a(f"    admitted                       : {p['admitted']}")
    a(f"    refused                        : {p['refused']}")
    a(f"    EXEMPT — sealed-record cited   : {p['exempt_sealed_cited']}  (untouched)")
    a(f"    candidates                     : {p['candidates']}")
    if archived is not None:
        a(f"    ARCHIVED                       : {archived}")
    a("")
    a("  -- candidates by rule --")
    for rule, n in p["by_rule"].items():
        a(f"    {rule:<36} {n}")
    a("  -- candidates by class --")
    for cls, n in p["by_class"].items():
        a(f"    {cls:<36} {n}")
    a("  -- examples --")
    for s in p["examples"]:
        a(f"    {s}")
    a("")
    if not confirmed:
        a("  Law 3 — nothing was archived. Re-run with --confirm to apply.")
    else:
        a("  Reversal: unarchive_ids(record['archived_ids']) restores every row.")
    a("=" * 74)
    return "\n".join(L)


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--confirm", action="store_true",
                    help="Law 3 — required to archive. Default is a dry run.")
    ap.add_argument("--report", help="write the JSON record here "
                                     "(default: reports/l2c_archive_<ts>.json)")
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    kb = args.kb                                   # a path, or (W3b part 3) a database URL
    exists, why = store_exists(kb)
    if not exists:
        print(f"no such store: {why}", file=sys.stderr)
        return 2
    md5_before = md5(Path(kb)) if not is_url(kb) else None   # a file's bytes; a database has no such figure

    if not args.confirm:
        conn = open_read_only(kb, row_factory=None, url_ok=True)
        conn.row_factory = sqlite3.Row
        p = plan(conn)
        conn.close()
        archived = retrievable_after = None
        md5_after = md5(Path(kb)) if not is_url(kb) else None
        assert md5_after == md5_before, "dry run modified the store"
    else:
        # W2 — the single-writer lock, before the store opens: a NEXUS
        # process serving this store refuses the run by name (exit 7).
        from backend.store_lock import EXIT_STORE_LOCKED, StoreLocked, StoreLockUnavailable
        from backend.store_lock import acquire as acquire_store_lock
        try:
            acquire_store_lock(store_target(kb), mode="script:archive_pregate")
        except (StoreLocked, StoreLockUnavailable) as e:
            print(f"REFUSED: {e}. Nothing was archived.", file=sys.stderr)
            return EXIT_STORE_LOCKED
        store = TripleStore(store_target(kb))
        p = plan(store.conn)
        archived = 0
        # One act per rule, so every ledger row names the rule that refused
        # the belief rather than a bulk label (`gate:<rule>` is the schema's
        # own convention for archive_reason).
        by_rule: dict[str, list[int]] = {}
        for bid, rule in p["candidate_reasons"].items():
            by_rule.setdefault(rule, []).append(int(bid))
        for rule, ids in by_rule.items():
            archived += store.archive_ids(ids, reason=REASON_PREFIX + rule)
        assert archived == p["candidates"], (
            f"archived {archived} rows, planned {p['candidates']}")
        for eid in p["exempt_ids"]:
            t = store.get_by_id(eid)
            assert t is not None and not t.archived, f"exempt belief {eid} was archived"
        assert store.count() == p["triples_total"], \
            "the record count changed — that is deletion, not archival"
        retrievable_after = store.count(archived=False)
        assert retrievable_after == p["retrievable_before"] - archived
        store.close()
        md5_after = md5(Path(kb)) if not is_url(kb) else None

    record = {
        "packet":       PACKET,
        "ts":           time.time(),
        "kb":           redacted(kb) if is_url(kb) else str(kb),
        "confirmed":    args.confirm,
        "md5_before":   md5_before,
        "md5_after":    md5_after,
        "archived":     archived,
        "archived_ids": p["candidate_ids"] if args.confirm else [],
        "plan":         p,
    }
    out = Path(args.report) if args.report else \
        Path("reports") / f"l2c_archive_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print(render(p, confirmed=args.confirm, kb=(redacted(kb) if is_url(kb) else kb), md5_before=md5_before,
                 md5_after=md5_after, archived=archived,
                 retrievable_after=retrievable_after))
    print(f"\n  record written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
