"""One-shot diagnostic — read-only analysis of:
1. What code paths call retrieve() or search_terms()
2. Tier distribution across sources in current KB
3. How often co-activations would win ranking vs grounded facts

No mutations. No commits to the database. No file edits.
Run: python scripts/diag_downstream.py [--db data/kb.db]   (from the repository's root; W11c moved it here)

The store is opened through `scripts/_readonly.py` (law 15: mode=ro plus a
write-refusing authorizer), and the first line printed is its path. Until
W1's close this script opened `data/kb.db` through `TripleStore`, a
read-write open, under the sentence above: a store older than the code was
migrated (the class recreates any table or index its DDL names), and where
no `data/kb.db` existed one was created, with its folder, and reported as
"Total triples: 0" (the third verifier's lens 3, F3; must-fix M6).

Exit codes: 0 the store was analysed; 1 it could not be (named on stderr);
2 there is no store at the path (nothing is created).
"""
import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402
from scripts._readonly import open_read_only, read_only_how, store_exists, store_label  # noqa: E402


def main(argv: "list[str] | None" = None) -> int:
    force_utf8_stdio()
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/kb.db", help="the store to analyse; opened read-only")
    args = ap.parse_args(argv)
    db = args.db
    print(f"database: {store_label(db)} (opened read-only: {read_only_how(db)})", flush=True)
    exists, why = store_exists(db)
    if not exists:
        print(f"no such store: {why} (nothing is created)", file=sys.stderr)
        return 2

    # ── Caller inventory ─────────────────────────────────────────
    print("=" * 70)
    print("DOWNSTREAM ANALYSIS — who calls retrieval primitives?")
    print("=" * 70)

    call_patterns = [
        (r"\.retrieve\(", "retriever.retrieve()"),
        (r"\.search_terms\(", "store.search_terms()"),
        (r"\.get_relevant\(", "store.get_relevant()"),
        (r"TripleRetriever\(", "TripleRetriever instantiation"),
        (r"extract_terms\(", "retriever.extract_terms()"),
    ]

    backend = Path("backend")
    for pattern, name in call_patterns:
        rx = re.compile(pattern)
        matches = []
        for py in backend.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line) and not line.strip().startswith("#"):
                    matches.append(f"  {py}:{i}  {line.strip()[:80]}")
        print(f"\n[{name}] — {len(matches)} call site(s):")
        for m in matches:
            print(m)

    # ── Tier × source distribution in current KB ──────────────────
    print("\n" + "=" * 70)
    print("TIER DISTRIBUTION — what's in the KB right now")
    print("=" * 70)

    try:
        conn = open_read_only(db, row_factory=None, url_ok=True)
        try:
            # Every row, archived included: what `TripleStore.all()` returned.
            all_triples = conn.execute("SELECT tier, source, relation FROM triples").fetchall()
        finally:
            conn.close()
    except Exception as e:
        print(f"\n[error] could not analyse: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"\nTotal triples: {len(all_triples)}")

    by_tier_source = Counter((tier, source) for tier, source, _relation in all_triples)
    by_tier_relation = Counter(
        (tier, "co_activated" if relation == "co_activated" else "grounded")
        for tier, _source, relation in all_triples
    )

    print("\nTier × Source breakdown:")
    print(f"  {'Tier':<6} {'Source':<25} {'Count':>8}")
    for (tier, src), count in sorted(by_tier_source.items()):
        print(f"  {tier:<6} {src:<25} {count:>8}")

    print("\nTier × Relation kind:")
    print(f"  {'Tier':<6} {'Kind':<15} {'Count':>8}")
    for (tier, kind), count in sorted(by_tier_relation.items()):
        print(f"  {tier:<6} {kind:<15} {count:>8}")

    # ── Quantify the bug: how often do co-activations crowd out grounded? ──
    print("\n" + "=" * 70)
    print("RANKING IMPACT — co-activations vs grounded by tier")
    print("=" * 70)
    by_tier = {}
    for tier, _source, relation in all_triples:
        kind = "co_activated" if relation == "co_activated" else "grounded"
        by_tier.setdefault(tier, Counter())[kind] += 1
    for tier in sorted(by_tier):
        c = by_tier[tier]
        total = sum(c.values())
        coa = c.get("co_activated", 0)
        gnd = c.get("grounded", 0)
        if total > 0:
            print(f"  Tier {tier}: {coa:>5} co-activations, "
                  f"{gnd:>5} grounded ({coa/total*100:.1f}% co-activations)")

    # Lowest tier that contains grounded facts
    grounded_tiers = sorted({tier for tier, _s, relation in all_triples if relation != "co_activated"})
    coa_tiers = sorted({tier for tier, _s, relation in all_triples if relation == "co_activated"})
    print(f"\nGrounded facts live in tiers: {grounded_tiers}")
    print(f"Co-activations live in tiers: {coa_tiers}")

    if grounded_tiers and coa_tiers and min(coa_tiers) < min(grounded_tiers):
        print("\n  ⚠ CONFIRMED: co-activations occupy lower tiers (= higher priority)")
        print("    than any grounded facts. The bug is reproducible from data alone.")
    elif grounded_tiers and coa_tiers and min(coa_tiers) == min(grounded_tiers):
        print("\n  ⚠ MIXED: co-activations and grounded share lowest tier")
        print("    Within same tier, ranking depends purely on weight.")
    else:
        print("\n  → Grounded facts have priority access. No tier inversion observed.")

    print("\n" + "=" * 70)
    print("END DIAGNOSTIC")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
