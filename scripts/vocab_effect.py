#!/usr/bin/env python
"""Did V.1's vocabulary change what extraction writes? Partition by created_at.

THE QUESTION
    Distinct non-substrate predicates grew ~206 -> 229 while a curated
    vocabulary was in force. Three explanations, needing three different
    responses:

      a) the vocabulary is not working in production;
      b) the growth is on paths the vocabulary does not govern;
      c) the growth is in rows that predate V.1 taking effect.

    A single distinct-predicate count cannot tell them apart, and the count is
    what raised the question. This script separates them.

THE VOCABULARY GOVERNS EXACTLY ONE SOURCE
    `extraction_prompt()` is reached only from `extract_triples`, and those
    rows are written with `source="extraction"` (`agentic_loop.py:693, 710`).
    Seeder rows, `human_assertion`, `consolidation` and `procedural` were
    never touched by it, so growth there is outside its reach entirely. The
    per-source table prints first for that reason: a reader who stops after
    it may already have their answer.

WHY created_at CAN CARRY THIS
    `add()`'s upsert is `ON CONFLICT ... DO UPDATE SET weight, tier,
    last_fired` (`tiered_store.py:508-510`) — it does NOT touch `created_at`,
    and no other statement in the codebase writes it. So a row's `created_at`
    is the wall-clock moment that exact `(subject, relation, object, source)`
    first entered the store, and nothing later moves it.

THE ONE THING IT CANNOT ANSWER, STATED UP FRONT
    V.1 was committed `5b21539`, 2026-08-17 12:30:23 UTC. **That is when the
    CODE landed, not when the operator's running server began sending the new
    prompt** — a daemon picks it up only on restart, and no restart time is
    recorded anywhere in the store. The boundary is therefore a LOWER BOUND:
    rows created shortly after it may still have been extracted under the old
    prompt.

    Rather than assume, `--daily` prints the on-list rate per day so the step
    can be LOCATED in the data. If there is no step, that is the answer. Pass
    `--since` to move the boundary once you know when the restart was.

A CONFOUND THIS CANNOT SEPARATE
    P.2's `decompose()` rewrites a compound predicate into a head (`is_a`,
    `has`, …) BEFORE storing, and those heads are on the offered list. A
    post-boundary rise in on-list rate is the prompt AND decomposition
    together. Neither this script nor the store can attribute between them,
    and a report that implied otherwise would be claiming more than the data
    holds.

READ-ONLY, AND CHECKED RATHER THAN PROMISED
    The store is copied to a temp directory and read there; the original's md5
    prints before and after. No `TripleStore` is constructed against it (H.2),
    and no embedder is involved — this is a question about what was written,
    not about what anything means.

USAGE
    python scripts/vocab_effect.py --db data/kb.db
    python scripts/vocab_effect.py --db data/kb.db --daily
    python scripts/vocab_effect.py --db data/kb.db --since 2026-08-18T09:00:00

    Exit 0 when the partition was computed, 1 when the store holds no
    extraction rows to partition, 4 if the original changed under it.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.kb.semantic_index import excluded_relations         # noqa: E402
from backend.kb.vocabulary import load_relations                 # noqa: E402
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402

# `git log 5b21539` — Packet V.1, the commit that made extraction offer the
# vocabulary. Hardcoded with its hash rather than derived from git at run
# time, because a shallow clone or an export has no history and a probe that
# silently loses its boundary is worse than one that states it. A LOWER BOUND
# on when it took effect — see the docstring, and `--since`.
V1_COMMIT = "5b21539"
V1_COMMITTED = dt.datetime(2026, 8, 17, 12, 30, 23,
                           tzinfo=dt.timezone.utc).timestamp()

# The only source the vocabulary has ever governed.
GOVERNED_SOURCE = "extraction"


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _rows(db: Path) -> list[dict]:
    conn = open_read_only(db, row_factory=None)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT relation, source, created_at FROM triples WHERE archived = 0")]
    conn.close()
    return rows


def report(rows: list[dict], *, boundary: float, label: str,
           daily: bool) -> int:
    excl = set(excluded_relations())
    vocab = set(load_relations())
    claims = [r for r in rows if r["relation"] not in excl]

    print(f"boundary  : "
          f"{dt.datetime.fromtimestamp(boundary, dt.timezone.utc)}  [{label}]")
    print(f"live rows : {len(rows)}   claim rows (substrate excluded): "
          f"{len(claims)}")
    print(f"offered vocabulary: {len(vocab)} relation(s)\n")

    print("BY SOURCE — the vocabulary governs only 'extraction'.")
    print(f"  {'rows':>6} {'distinct':>9} {'on-list':>8}  source")
    by_src: dict[str, list[dict]] = collections.defaultdict(list)
    for r in claims:
        by_src[r["source"]].append(r)
    for src, rs in sorted(by_src.items(), key=lambda kv: -len(kv[1])):
        preds = {r["relation"] for r in rs}
        print(f"  {len(rs):>6} {len(preds):>9} "
              f"{len(preds & vocab)/max(1,len(preds)):>7.0%}   {src}"
              f"{'   <-- governed' if src == GOVERNED_SOURCE else ''}")

    gov = [r for r in claims if r["source"] == GOVERNED_SOURCE]
    if not gov:
        print(f"\nNo rows with source='{GOVERNED_SOURCE}'. The vocabulary has "
              f"never governed anything in this store, so the growth is "
              f"entirely on paths it does not touch and V.1's production "
              f"effect cannot be assessed from these rows at all.")
        return 1

    pre = {r["relation"] for r in gov if r["created_at"] < boundary}
    post = {r["relation"] for r in gov if r["created_at"] >= boundary}
    pre_rows = [r for r in gov if r["created_at"] < boundary]
    post_rows = [r for r in gov if r["created_at"] >= boundary]

    print("\nEXTRACTION PREDICATES, partitioned by created_at:")
    print(f"  rows before boundary : {len(pre_rows)}")
    print(f"  rows after  boundary : {len(post_rows)}")
    print(f"  distinct, BEFORE only: {len(pre - post)}")
    print(f"  distinct, AFTER only : {len(post - pre)}")
    print(f"  distinct, BOTH sides : {len(pre & post)}")

    def rate(preds: set) -> float:
        return len(preds & vocab) / max(1, len(preds))

    print("\nON-LIST RATE — the number that decides whether V.1's claim holds "
          "in production:")
    print(f"  {'distinct':>9} {'on-list':>8}  window")
    print(f"  {len(pre):>9} {rate(pre):>7.0%}   before the boundary")
    print(f"  {len(post):>9} {rate(post):>7.0%}   after the boundary")
    print(f"  {len(post - pre):>9} {rate(post - pre):>7.0%}   NEW after the "
          f"boundary — predicates the store had never seen, the strictest read")
    for name, rs in (("before", pre_rows), ("after", post_rows)):
        if rs:
            on = sum(1 for r in rs if r["relation"] in vocab)
            print(f"  by ROW, {name:>6}: {on}/{len(rs)} = {on/len(rs):.0%}")
    if not post_rows:
        print("\n  NOTHING was extracted after the boundary. The store cannot "
              "speak to V.1's production effect — the growth predates it, "
              "which is explanation (c).")

    if daily:
        print("\nDAILY — locate the step rather than assuming the boundary.")
        print(f"  {'date':>10} {'rows':>5} {'on-list':>8}  new-predicates")
        seen: set[str] = set()
        buckets: dict[str, list[dict]] = collections.defaultdict(list)
        for r in sorted(gov, key=lambda r: r["created_at"]):
            d = dt.datetime.fromtimestamp(
                r["created_at"], dt.timezone.utc).date().isoformat()
            buckets[d].append(r)
        for d in sorted(buckets):
            rs = buckets[d]
            on = sum(1 for r in rs if r["relation"] in vocab)
            fresh = {r["relation"] for r in rs} - seen
            seen |= {r["relation"] for r in rs}
            print(f"  {d:>10} {len(rs):>5} {on/len(rs):>7.0%}  {len(fresh)}")

    print(f"\nREAD BEFORE QUOTING ANY OF THIS. The boundary is V.1's COMMIT "
          f"({V1_COMMIT}), not the restart that put it in production — rows "
          f"just after it may still be pre-vocabulary, so use --daily to find "
          f"the step and --since to move the boundary once you know it. And "
          f"`decompose()` rewrites compound predicates into on-list heads "
          f"before storing, so a post-boundary rise is the prompt AND "
          f"decomposition; nothing here separates them.")
    return 0


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "kb.db")
    ap.add_argument("--since", type=str, default="",
                    help="ISO timestamp to use as the boundary instead of "
                         "V.1's commit — use it once the restart time is known")
    ap.add_argument("--daily", action="store_true",
                    help="on-list rate per day, to locate the step in the "
                         "data rather than assume it")
    args = ap.parse_args()
    print(store_line(args.db), flush=True)              # law 15: the store first (W4 part 4)

    print(__doc__.split("USAGE")[0].strip())
    print("=" * 72)
    if not args.db.is_file():
        print(f"no store at {args.db}")
        return 2

    boundary, label = V1_COMMITTED, f"V.1 commit {V1_COMMIT} — LOWER BOUND"
    if args.since:
        try:
            parsed = dt.datetime.fromisoformat(args.since)
        except ValueError:
            print(f"--since {args.since!r} is not an ISO timestamp")
            return 2
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        boundary, label = parsed.timestamp(), f"--since {args.since}"

    before = _md5(args.db)
    print(f"store md5 before : {before}")
    tmp = tempfile.mkdtemp(prefix="nexus_vocabeffect_")
    try:
        copy = Path(tmp) / f"effect_{args.db.name}"
        shutil.copy2(args.db, copy)
        rows = _rows(copy)
    finally:
        # The connection is closed inside `_rows`; the family's rule is that
        # nothing holds the file when the directory goes.
        shutil.rmtree(tmp, ignore_errors=True)
    after = _md5(args.db)
    print(f"store md5 after  : {after}  "
          f"{'UNCHANGED' if after == before else '*** MUTATED ***'}\n")
    if after != before:
        return 4
    return report(rows, boundary=boundary, label=label, daily=args.daily)


if __name__ == "__main__":
    raise SystemExit(main())
