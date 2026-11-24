#!/usr/bin/env python
"""Which expect fragments does the store actually contain?

WHY THIS EXISTS
    `measure_retrieval.py` produces recall figures. A pair whose fragments are
    not in the store cannot be answered by any retrieval, so it drags the
    headline down while telling you nothing about retrieval — and until W.4 it
    reported that as `not_indexed`, a verdict that also fired when every
    fragment WAS present but no single row held them all. Two very different
    problems behind one word.

    This script answers the store half on its own, before any embedding is
    computed: **for each fragment, is that substring anywhere in a live
    surface?** It is pure SQL. No model, no vectors, no provider — which is
    the point. A measurement's own inputs should be checkable by something
    that is not the measurement.

WHY IT IS SEPARATE FROM THE HARNESS
    Same reason `who_outranks.py` is. `measure_retrieval.py` answers "what is
    my recall"; this answers "is my question file even askable". Bundling them
    would mean paying an embedding per belief to learn something a substring
    query already knows, and it would put the check inside the thing being
    checked.

WHAT IT DOES NOT DO
    It does not edit the pairs file and it does not propose fragments. It
    reports what is absent, what is present, and — for pairs scored
    `match="all"` — whether any single surface could satisfy them. Choosing
    replacements needs someone who knows which stored claim actually answers
    the question, and the store contains near-misses that look right in a
    diff. `--suggest` will print candidate surfaces that share a token with
    the question, as a starting point to cut by hand.

READ-ONLY, AND CHECKED RATHER THAN PROMISED
    The store is copied to a temp directory and read there; the original's md5
    is printed before and after. This script never constructs a `TripleStore`,
    so `mode=ro` alone would have been enough to avoid H.2's migration — the
    copy is here anyway for two reasons: one rule for the whole family of
    scripts, and a stable snapshot if a daemon is writing while the audit
    runs.

USAGE
    python scripts/audit_pairs.py --db data/kb.db --pairs pairs.json
    python scripts/audit_pairs.py --pairs pairs.json --suggest

    Exit 0 when every fragment is present, 1 when any is absent, so a
    re-baseline run can gate on it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.kb.retriever import extract_terms                   # noqa: E402
from backend.kb.semantic_index import excluded_relations         # noqa: E402
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _surfaces(db: Path) -> list[tuple[str, str, str]]:
    """(surface, source, relation) for every live belief."""
    conn = open_read_only(db, row_factory=None)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT subject, relation, object, source FROM triples "
        "WHERE archived = 0").fetchall()
    conn.close()
    return [(f"{r['subject']} {r['relation']} {r['object']}",
             r["source"], r["relation"]) for r in rows]


def _load_pairs(path: Path) -> list[dict]:
    """The harness's parser, kept deliberately in step with it.

    Reads `_group`, `match` and `expect_miss` the same way
    `measure_retrieval._load_pairs` does, so the two agree about how many
    pairs a file contains. `tests/test_pairs_audit.py` pins that agreement —
    an audit that parses a different set from the harness is worse than none.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    out, group = [], ""
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        if "_group" in item:
            group = str(item["_group"]).split("—")[0].strip()
            continue
        q = str(item.get("question", "")).strip()
        exp = [str(e) for e in (item.get("expect") or []) if str(e).strip()]
        if not (q and exp):
            continue
        out.append({"question": q, "expect": exp, "group": group,
                    "match": str(item.get("match", "any")).strip().lower(),
                    "expect_miss": bool(item.get("expect_miss", False))})
    return out


def _candidates(surfaces, question: str, limit: int = 5) -> list[str]:
    """Surfaces sharing a term with the question. A STARTING POINT, not a
    proposal — the ranking here is token overlap, which is exactly the lexical
    channel whose blind spot the whole packet is about."""
    terms = set(extract_terms(question))
    scored = []
    for surface, _src, _rel in surfaces:
        low = surface.lower()
        n = sum(1 for t in terms if t in low)
        if n:
            scored.append((n, -len(surface), surface))
    scored.sort(reverse=True)
    return [s for _n, _l, s in scored[:limit]]


def audit(args) -> int:
    before = _md5(args.db)
    print(f"store        : {args.db}")
    print(f"pairs        : {args.pairs}")
    print(f"store md5 before : {before}")

    tmp = tempfile.mkdtemp(prefix="nexus_audit_")
    try:
        copy = Path(tmp) / f"audit_{args.db.name}"
        shutil.copy2(args.db, copy)
        surfaces = _surfaces(copy)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    after = _md5(args.db)
    print(f"store md5 after  : {after}  "
          f"{'UNCHANGED' if after == before else '*** MUTATED ***'}")
    if after != before:
        return 4

    pairs = _load_pairs(args.pairs)
    if not pairs:
        print("no usable pairs")
        return 2
    excluded = set(excluded_relations())
    blob = " || ".join(s for s, _src, _rel in surfaces).lower()
    indexed_blob = " || ".join(s for s, _src, rel in surfaces
                               if rel not in excluded).lower()

    print(f"\n{len(surfaces)} live surface(s), {len(pairs)} pair(s), "
          f"excluded relations {sorted(excluded)}")
    print("=" * 72)

    absent_all: dict[str, int] = {}
    n_absent_pairs = n_unsatisfiable = n_control_present = 0
    for p in pairs:
        missing = [e for e in p["expect"] if e.lower() not in blob]
        present = [e for e in p["expect"] if e.lower() in blob]
        # A fragment present in the store but only on an EXCLUDED relation is
        # invisible to the semantic channel — the third state, and the one a
        # bare present/absent split would hide.
        unindexed = [e for e in present if e.lower() not in indexed_blob]
        one_row = any(all(e.lower() in s.lower() for e in p["expect"])
                      for s, _src, _rel in surfaces)

        for e in missing:
            absent_all[e] = absent_all.get(e, 0) + 1

        tag = "ok "
        if p["expect_miss"]:
            if present:
                tag, n_control_present = "CTL", n_control_present + 1
            else:
                tag = "ctl"
        elif missing:
            tag, n_absent_pairs = "!! ", n_absent_pairs + 1
        elif p["match"] == "all" and not one_row:
            tag, n_unsatisfiable = "ALL", n_unsatisfiable + 1

        print(f"{tag} [{p['group'] or '-'}] {p['question']}")
        if missing:
            print(f"      ABSENT      : {missing}")
        if unindexed:
            print(f"      NOT INDEXED : {unindexed}  (present, but only on an "
                  f"excluded relation)")
        if present and (missing or args.verbose):
            print(f"      present     : {present}")
        if p["match"] == "all" and not one_row and not missing:
            print(f"      match='all' but no single surface holds all of "
                  f"{p['expect']} — split the pair or use match='any'")

    print("\n" + "=" * 72)
    if absent_all:
        print(f"{len(absent_all)} distinct fragment(s) absent from the store:")
        for frag, n in sorted(absent_all.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {n:>3} pair(s)  {frag}")
    else:
        print("every fragment in every pair is present in the store.")
    print(f"\n{n_absent_pairs} scored pair(s) unanswerable — at least one "
          f"fragment absent")
    print(f"{n_unsatisfiable} scored pair(s) use match='all' with no single "
          f"surface able to satisfy them")
    if n_control_present:
        print(f"{n_control_present} CONTROL pair(s) have fragments that ARE in "
              f"the store. A control is only a control while its answer is "
              f"genuinely absent — these test nothing.")

    if args.suggest:
        print("\n" + "=" * 72)
        print("CANDIDATE SURFACES for pairs with absent fragments. Token "
              "overlap only — cut by hand, and prefer a surface that answers "
              "the question over one that merely shares a word:")
        for p in pairs:
            if p["expect_miss"]:
                continue
            if not [e for e in p["expect"] if e.lower() not in blob]:
                continue
            print(f"\n  {p['question']}")
            for cand in _candidates(surfaces, p["question"]):
                print(f"     {cand[:88]}")

    return 1 if absent_all else 0


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "kb.db")
    ap.add_argument("--pairs", type=Path, default=ROOT / "retrieval_pairs.json")
    ap.add_argument("--suggest", action="store_true",
                    help="print candidate surfaces for pairs with absent "
                         "fragments (token overlap; a starting point only)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    print(store_line(args.db), flush=True)              # law 15: the store first (W4 part 4)

    print(__doc__.split("USAGE")[0].strip())
    print("=" * 72)
    if not args.db.is_file():
        print(f"no store at {args.db}")
        return 2
    if not args.pairs.is_file():
        print(f"no pairs file at {args.pairs}")
        return 2
    return audit(args)


if __name__ == "__main__":
    raise SystemExit(main())
