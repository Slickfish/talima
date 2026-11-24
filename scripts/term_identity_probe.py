"""AT (term identity) — read-only probe: when are two terms the same thing?

    python scripts/term_identity_probe.py [--kb data/kb.db]
        [--capture data/citation_capture.jsonl] [--out term_identity.json]
        [--top 25]

Measures, on the store as it is, what a term-identity mechanism would have
to catch — and prints the actual term sets so a human can judge what each
candidate would WRONGLY merge. Nothing is written to the store: it is
opened read-only and its md5 is printed before and after.

  §1  the term distribution — OQ-2's 194-of-229 question, for terms
  §2  textual-difference classes, each a DERIVED key, no list:
        case        differ only by case
        punct       differ only by punctuation / underscore / whitespace
        plural      differ by a trailing s / es
        headword    one term is the other plus leading or trailing words
                    ("CYP3A4" / "CYP3A4 enzymes")
        bag         same words in a different order
      Every group is printed. A group is only as good as a human's reading
      of it; the counts say how much each rule would touch.
  §3  structural (AB's method): two terms share (relation, partner)
      signature elements. Computed over RAW relations, so it does not rest
      on relation identity — that is the circularity question, answered by
      running both ways and reporting the difference.
  §4  co-citation: terms whose beliefs are cited together across turns.
  §5  the captured turns: per turn, the injected terms grouped by §2's
      classes — the duplicate characterisation, in full.

Substrate rows (co_activated / recurring_query_term) are excluded from every
count and reported separately: they are associations, not claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "at", "for", "with",
         "by", "and", "or", "is", "are"}


def _md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else "-"


# ---------------------------------------------------------------------------
# Keys — each a derived function of the string, no table anywhere
# ---------------------------------------------------------------------------

def k_case(t: str) -> str:
    return " ".join(t.split()).casefold()


def k_punct(t: str) -> str:
    return _NON_ALNUM.sub("", t.casefold())


def k_plural(t: str) -> str:
    w = k_case(t)
    if len(w) > 4 and w.endswith("es"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def words(t: str) -> list[str]:
    return [w for w in _NON_ALNUM.sub(" ", t.casefold()).split() if w]


def k_bag(t: str) -> str:
    return " ".join(sorted(w for w in words(t) if w not in _STOP))


def groups_by(terms, key) -> list[list[str]]:
    g: dict[str, list[str]] = defaultdict(list)
    for t in terms:
        k = key(t)
        if k:
            g[k].append(t)
    return sorted((v for v in g.values() if len(v) > 1), key=lambda v: -len(v))


def headword_pairs(terms) -> list[tuple[str, str]]:
    """(short, long) where long == short plus leading and/or trailing words,
    compared on case-folded words. O(n·L): every proper sub-run of a long
    term's words is looked up, never every pair."""
    by_words = {}
    for t in terms:
        by_words.setdefault(tuple(words(t)), t)
    out = []
    for t in terms:
        ws = tuple(words(t))
        if len(ws) < 2:
            continue
        for i in range(len(ws)):
            for j in range(i + 1, len(ws) + 1):
                if (i, j) == (0, len(ws)):
                    continue
                sub = ws[i:j]
                short = by_words.get(sub)
                if short is not None and short != t:
                    out.append((short, t))
    return sorted(set(out))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--capture", default="data/citation_capture.jsonl")
    ap.add_argument("--out", default="term_identity.json")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()
    print(store_line(args.kb), flush=True)              # law 15: the store first (W4 part 4)

    from backend.kb.gate import RELATION_ALIASES, normalise_relation
    from backend.kb.schema import SUBSTRATE_RELATIONS

    kb = Path(args.kb)
    if not kb.is_file():
        print(f"MISSING: {kb}")
        return 2
    md5_before = _md5(kb)
    con = open_read_only(kb, row_factory=None)
    con.row_factory = sqlite3.Row
    tables = {r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "triples" not in tables:
        print("NO RECORDS: no triples table")
        return 2
    ph = ",".join("?" * len(SUBSTRATE_RELATIONS))
    rows = con.execute(
        f"SELECT id, subject, relation, object FROM triples WHERE archived = 0 "
        f"AND relation NOT IN ({ph})", SUBSTRATE_RELATIONS).fetchall()
    n_substrate = con.execute(
        f"SELECT COUNT(*) AS n FROM triples WHERE archived = 0 "
        f"AND relation IN ({ph})", SUBSTRATE_RELATIONS).fetchone()["n"]
    report: dict = {"claim_rows": len(rows), "substrate_rows": n_substrate}
    print(f"claim rows {len(rows)}   substrate rows {n_substrate} (excluded)")

    # ---- §1 distribution ---------------------------------------------------
    freq: Counter = Counter()
    for r in rows:
        freq[r["subject"]] += 1
        freq[r["object"]] += 1
    terms = sorted(freq)
    once = sum(1 for t in terms if freq[t] == 1)
    subj_only = {r["subject"] for r in rows} - {r["object"] for r in rows}
    obj_only = {r["object"] for r in rows} - {r["subject"] for r in rows}
    dist = Counter(min(freq[t], 10) for t in terms)
    report["distribution"] = {
        "distinct_terms": len(terms), "appear_once": once,
        "appear_once_share": (once / len(terms)) if terms else None,
        "subject_only": len(subj_only), "object_only": len(obj_only),
        "by_frequency_capped_at_10": {str(k): v for k, v in sorted(dist.items())},
        "top": freq.most_common(args.top),
        "word_count": dict(Counter(min(len(words(t)), 8) for t in terms)),
    }
    print(f"\n§1 distinct terms {len(terms)}, appearing once {once} "
          f"({(once / len(terms)) if terms else 0:.1%}); subject-only {len(subj_only)}, "
          f"object-only {len(obj_only)}")
    print("   words/term:", dict(sorted(report["distribution"]["word_count"].items())))
    print("   top:", freq.most_common(10))

    # ---- §2 textual classes ------------------------------------------------
    classes = {"case": groups_by(terms, k_case),
               "punct": groups_by(terms, k_punct),
               "plural": groups_by(terms, k_plural),
               "bag": groups_by(terms, k_bag)}
    # each class reported NET of the weaker ones it contains, so counts add up
    seen_keys = set()
    net: dict[str, list[list[str]]] = {}
    for name in ("case", "punct", "plural", "bag"):
        fresh = []
        for g in classes[name]:
            key = tuple(sorted(g))
            if key not in seen_keys:
                seen_keys.add(key)
                fresh.append(g)
        net[name] = fresh
    hw = headword_pairs(terms)
    report["textual"] = {
        **{name: {"groups": len(g), "terms": sum(len(x) for x in g),
                  "samples": g[:args.top]} for name, g in net.items()},
        "headword": {"pairs": len(hw), "samples": hw[:args.top]},
    }
    print("\n§2 textual-difference classes (each group printed up to --top):")
    for name, g in net.items():
        print(f"   {name:9} {len(g):5d} groups, {sum(len(x) for x in g):5d} terms")
        for x in g[:args.top]:
            print(f"      {x}")
    print(f"   headword  {len(hw):5d} pairs (short ⊂ long)")
    for a, b in hw[:args.top]:
        print(f"      {a!r} ⊂ {b!r}")

    # ---- §3 structural ---------------------------------------------------
    def signatures(canon: bool) -> dict[str, set]:
        sig: dict[str, set] = defaultdict(set)
        for r in rows:
            rel = normalise_relation(r["relation"]) if canon else r["relation"]
            rel = RELATION_ALIASES.get(rel, rel) if canon else rel
            sig[r["subject"]].add(("s", rel, k_case(r["object"])))
            sig[r["object"]].add(("o", rel, k_case(r["subject"])))
        return sig

    def shared_pairs(sig: dict[str, set], min_shared: int = 2):
        idx: dict[tuple, list[str]] = defaultdict(list)
        for t, s in sig.items():
            for el in s:
                idx[el].append(t)
        shared: Counter = Counter()
        for el, ts in idx.items():
            if len(ts) < 2 or len(ts) > 50:      # a hub element says nothing
                continue
            for a, b in combinations(sorted(ts), 2):
                shared[(a, b)] += 1
        out = []
        for (a, b), n in shared.most_common():
            if n < min_shared:
                break
            jac = n / len(sig[a] | sig[b])
            out.append({"a": a, "b": b, "shared": n, "jaccard": round(jac, 3),
                        "textual": k_punct(a) == k_punct(b) or k_bag(a) == k_bag(b)
                        or k_plural(a) == k_plural(b)})
        return out

    raw = shared_pairs(signatures(canon=False))
    can = shared_pairs(signatures(canon=True))
    report["structural"] = {
        "raw_relations": {"pairs": len(raw), "top": raw[:args.top]},
        "alias_canonical": {"pairs": len(can), "top": can[:args.top]},
        "circularity_delta": len(can) - len(raw),
        "structural_only": [p for p in raw if not p["textual"]][:args.top],
    }
    print(f"\n§3 structural: {len(raw)} pairs share >=2 (relation, partner) "
          f"elements over raw relations; {len(can)} over alias-canonical "
          f"(delta {len(can) - len(raw)} = what relation identity adds)")
    for p in raw[:args.top]:
        print(f"      {p['a']!r} ~ {p['b']!r}  shared={p['shared']} "
              f"jaccard={p['jaccard']} textual={p['textual']}")

    # ---- §4 co-citation ----------------------------------------------------
    cocit = {"available": "lineage_records" in tables, "pairs": 0, "top": []}
    if cocit["available"]:
        by_id = {r["id"]: r for r in rows}
        term_turns: dict[str, set] = defaultdict(set)
        for rec in con.execute("SELECT lineage_id, injected_json FROM lineage_records"):
            for it in json.loads(rec["injected_json"]):
                if it.get("cited") is True and it.get("belief_id") in by_id:
                    r = by_id[it["belief_id"]]
                    term_turns[r["subject"]].add(rec["lineage_id"])
                    term_turns[r["object"]].add(rec["lineage_id"])
        pairs = []
        turn_terms: dict[str, list[str]] = defaultdict(list)
        for t, lids in term_turns.items():
            for lid in lids:
                turn_terms[lid].append(t)
        # A subject and its own object are cited together by construction;
        # only pairs that never share a row can carry a co-citation signal.
        same_row = {tuple(sorted((r["subject"], r["object"]))) for r in rows}
        co: Counter = Counter()
        for lid, ts in turn_terms.items():
            for a, b in combinations(sorted(set(ts)), 2):
                if (a, b) not in same_row:
                    co[(a, b)] += 1
        for (a, b), n in co.most_common():
            if n < 3:
                break
            jac = n / len(term_turns[a] | term_turns[b])
            if jac >= 0.5:
                pairs.append({"a": a, "b": b, "co_cited": n, "jaccard": round(jac, 3),
                              "textual": k_punct(a) == k_punct(b)
                              or k_bag(a) == k_bag(b)})
        cocit.update(pairs=len(pairs), top=pairs[:args.top],
                     cited_terms=len(term_turns))
        print(f"\n§4 co-citation: {len(term_turns)} terms in cited beliefs; "
              f"{len(pairs)} pairs co-cited >=3 times with Jaccard >= 0.5")
        for p in pairs[:args.top]:
            print(f"      {p['a']!r} & {p['b']!r}  co_cited={p['co_cited']} "
                  f"jaccard={p['jaccard']} textual={p['textual']}")
    report["co_citation"] = cocit

    # ---- §5 the captured turns ---------------------------------------------
    cap = Path(args.capture)
    turns_out = []
    if cap.is_file():
        for line in cap.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            ids = [it["belief_id"] for it in row["injected"]]
            tset = set()
            for bid in ids:
                r = con.execute("SELECT subject, object FROM triples WHERE id = ?",
                                (bid,)).fetchone()
                if r:
                    tset.update((r["subject"], r["object"]))
            tl = sorted(tset)
            per = {name: groups_by(tl, key) for name, key in
                   (("case", k_case), ("punct", k_punct), ("plural", k_plural),
                    ("bag", k_bag))}
            hwp = headword_pairs(tl)
            turns_out.append({"lineage_id": row["lineage_id"], "terms": len(tl),
                              "injected": len(ids),
                              **{k: v for k, v in per.items() if v},
                              **({"headword": hwp} if hwp else {})})
        dup_turns = [t for t in turns_out
                     if any(k in t for k in ("case", "punct", "plural", "bag", "headword"))]
        print(f"\n§5 captured turns {len(turns_out)}; turns with a textual-class "
              f"group {len(dup_turns)}")
        for t in dup_turns[:args.top]:
            print(f"   {t['lineage_id'][:8]} injected={t['injected']} terms={t['terms']}")
            for k in ("case", "punct", "plural", "bag", "headword"):
                if k in t:
                    print(f"      {k}: {t[k]}")
    else:
        print(f"\n§5 no capture at {cap} — the per-turn characterisation needs it")
    report["captured_turns"] = turns_out
    con.close()

    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    print(f"\nwrote {args.out}")
    md5_after = _md5(kb)
    print(f"store md5 {md5_before} -> {md5_after} "
          f"{'UNCHANGED' if md5_after == md5_before else '*** CHANGED ***'}")
    return 0 if md5_after == md5_before else 3


if __name__ == "__main__":
    sys.exit(main())
