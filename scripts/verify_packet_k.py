#!/usr/bin/env python3
"""Packet K verification — read-only measurement of the real store.

READ-ONLY, TWICE OVER
    1. The real database is opened `mode=ro` and is never written.
    2. Anything that would mutate runs on a COPY in a temp directory.

    It does NOT import `backend.server`. Packet J made that import
    side-effect-free, but the path defaults are still relative, so an explicit
    `create_app()` from the wrong working directory still creates a `data/`
    tree. This script imports `backend.kb` only.

USAGE
    python scripts/verify_packet_k.py                    # defaults to data/kb.db
    python scripts/verify_packet_k.py --kb path/to/kb.db
    python scripts/verify_packet_k.py --json report.json
    python scripts/verify_packet_k.py --examples 20      # show more replay hits
    python scripts/verify_packet_k.py --surfaces-class consolidation

SECTION [3b] — THE LIST L.2c IS GATED ON (added by Packet L)
    Prints EVERY rejected surface of one evidence class (default `extraction`,
    Packet L.2c's population), in FULL, grouped by rule, with a per-rule count
    of how many of those rows are cited by a SEALED lineage record.

    Full surfaces because truncation is where a legitimate concept hides, and
    this list exists to be read by eye. Grouped by rule because 71
    `multi_preposition` rows are one decision about a rule, not 71 decisions
    about rows.

    The sealed-cited column is the alarm. If a rule is refusing beliefs that
    participated in recorded decisions, that shows up here — before anyone
    confirms anything — and it is a reason to doubt the rule, not the belief.

    **THIS LIST IS THE INPUT TO A DECISION, NOT THE DECISION.** A long list is
    not a licence to archive.

THE NUMBER THAT MATTERS
    Section [3] replays the ingest gate against every row already in the
    store and reports how many it WOULD reject, by rule. **Nothing is
    changed.** That number is:

      * the measure of the problem the gate was built for;
      * the evidence that decides whether a word-count cap is needed — K.2
        shipped without one deliberately, because the candidate rules were
        validated against concepts topping out at four words and the store's
        7+ word terms had never been looked at;
      * the input to a future cleanup decision, WHICH THIS PACKET DOES NOT
        TAKE. Existing rows are beliefs with provenance, some cited in sealed
        lineage records. Removing any of them is a separate decision with its
        own record, as Packet B's purge and E.4's declined write both were.

    A high number is not a licence to delete. It is a licence to have the
    conversation.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import statistics
import sys
import tempfile
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
from backend.kb.gate import (  # noqa: E402
    MAX_TERM_TOKENS, RELATION_ALIASES, _tokens, gate_enabled,
    inspect_triple, min_confidence, normalise_relation, normalise_term,
)
from backend.kb.tiered_store import classify  # noqa: E402
# ONE definition of the sealed-cited rule, shared with Packet L's
# archival pass, so the list read here and the set exempted there cannot
# drift apart (the hebbian/hebbian_update lesson, H.4).
from backend.memory.lineage import sealed_cited_belief_ids  # noqa: E402

out: dict = {}


def h(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def ro(db: Path) -> sqlite3.Connection:
    conn = open_read_only(db, row_factory=None)
    conn.row_factory = sqlite3.Row
    return conn


def dist(vals) -> str:
    if not vals:
        return "n=0"
    sv = sorted(vals)
    return (f"n={len(sv)} min={sv[0]:.3f} p50={statistics.median(sv):.3f} "
            f"mean={statistics.fmean(sv):.3f} max={sv[-1]:.3f}")


# ---------------------------------------------------------------------------

def before_state(conn) -> dict:
    h("[1] THE STORE AS IT STANDS")
    total = conn.execute("SELECT COUNT(*) n FROM triples").fetchone()["n"]
    by_source = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, COUNT(*) n FROM triples GROUP BY source ORDER BY n DESC")}
    print(f"triples: {total}\n")
    print("by source, with the evidence class and gate strictness each gets:")
    for src, n in by_source.items():
        cls = classify(src)
        strict = "STRICT" if cls in ("extraction", "consolidation", "procedural") else "lenient"
        print(f"  {src:<28} {n:>5}   {cls:<14} {strict}")

    lineage = sealed = None
    try:
        lineage = conn.execute("SELECT COUNT(*) n FROM lineage_records").fetchone()["n"]
        sealed = conn.execute("SELECT COUNT(*) n FROM lineage_records "
                              "WHERE sealed_block IS NOT NULL").fetchone()["n"]
        print(f"\nlineage records: {lineage} ({sealed} sealed)")
    except sqlite3.Error:
        pass
    return {"total": total, "by_source": by_source,
            "lineage": lineage, "sealed": sealed}


def relations(conn) -> dict:
    h("[2] RELATIONS — every distinct spelling, and what K.3 would make of it")
    rows = [(r["relation"], r["n"]) for r in conn.execute(
        "SELECT relation, COUNT(*) n FROM triples GROUP BY relation "
        "ORDER BY n DESC")]
    print(f"distinct relations stored: {len(rows)}\n")
    print(f"  {'stored':<34} {'count':>6}   {'normalises to':<28} note")
    collapses: Counter = Counter()
    for rel, n in rows:
        norm = normalise_relation(rel)
        note = ""
        if norm != rel:
            note = "ALIASED" if rel.lower().replace(" ", "_") in RELATION_ALIASES \
                   or norm in RELATION_ALIASES.values() and norm != rel else "slugged"
        collapses[norm] += n
        print(f"  {rel[:34]:<34} {n:>6}   {norm[:28]:<28} {note}")

    merged = {k: v for k, v in collapses.items()
              if sum(1 for r, _n in rows if normalise_relation(r) == k) > 1}
    print(f"\ndistinct AFTER normalisation: {len(collapses)}  "
          f"(from {len(rows)})")
    if merged:
        print("\nspellings that would collapse together (NEW writes only — "
              "existing rows keep their spelling):")
        for canon, total in sorted(merged.items(), key=lambda kv: -kv[1]):
            spellings = [r for r, _n in rows if normalise_relation(r) == canon]
            print(f"  {canon:<28} <- {spellings}")
    return {"distinct_before": len(rows), "distinct_after": len(collapses),
            "would_merge": {k: sorted(r for r, _ in rows
                                      if normalise_relation(r) == k)
                            for k in merged}}


def gate_replay(conn, n_examples: int) -> dict:
    h("[3] GATE REPLAY — how many STORED rows would the gate reject?")
    print("READ-ONLY. Nothing is changed. This is a measurement, not a cleanup.\n")
    rows = conn.execute(
        "SELECT id, subject, relation, object, source, weight FROM triples"
    ).fetchall()
    if not rows:
        print("empty store")
        return {}

    by_rule: Counter = Counter()
    by_source_rejected: Counter = Counter()
    by_source_total: Counter = Counter()
    examples: list[dict] = []

    for r in rows:
        src = r["source"]
        by_source_total[src] += 1
        reason = inspect_triple(
            normalise_term(r["subject"]),
            normalise_relation(r["relation"]),
            normalise_term(r["object"]),
            evidence_class=classify(src),
            weight=r["weight"],
        )
        if reason is None:
            continue
        rule = reason.split(":", 2)
        by_rule[f"{rule[0]}:{rule[1]}" if len(rule) > 1 else reason] += 1
        by_source_rejected[src] += 1
        if len(examples) < n_examples:
            examples.append({"id": r["id"], "reason": reason,
                             "triple": f"{r['subject']} | {r['relation']} | {r['object']}",
                             "source": src})

    rejected = sum(by_rule.values())
    pct = 100.0 * rejected / len(rows)
    print(f"stored rows            : {len(rows)}")
    print(f"the gate WOULD reject  : {rejected}  ({pct:.1f}%)")
    print(f"the gate would accept  : {len(rows) - rejected}\n")

    print("by rule — this is what decides whether a word-count cap is needed:")
    for rule, n in by_rule.most_common():
        print(f"  {rule:<40} {n:>5}")

    print("\nby source (rejected / total):")
    for src, tot in by_source_total.most_common():
        rej = by_source_rejected.get(src, 0)
        share = f"{100.0*rej/tot:5.1f}%" if tot else "    -"
        print(f"  {src:<28} {rej:>5} / {tot:<5} {share}")

    if examples:
        print(f"\nfirst {len(examples)} examples:")
        for e in examples:
            print(f"  [{e['reason']}] {e['triple'][:72]}")

    print()
    print("  A HIGH NUMBER IS NOT A LICENCE TO DELETE. Every one of these rows")
    print("  is a belief with provenance, and some may be cited in sealed")
    print("  lineage records. Packet K rejects at the boundary and never")
    print("  reaches backwards. Retroactive cleanup is a separate decision")
    print("  with its own record — see reports/ for how Packet B's purge and")
    print("  E.4's declined write were each documented before acting.")
    return {"rows": len(rows), "would_reject": rejected, "pct": round(pct, 2),
            "by_rule": dict(by_rule), "examples": examples}


def rejected_surfaces(conn, evidence_class: str = "extraction") -> dict:
    """[3b] Every rejected surface of one evidence class, grouped by rule.

    Added for Packet L.2c, and the reason it exists is worth stating rather
    than assuming: **L.2c would point the gate's rules at rows that are
    already stored, and those rules were calibrated for a decision an order of
    magnitude cheaper.** At the door, a false positive costs one un-stored
    belief the user can restate in the next sentence, and the gate is loud
    about it. Applied backwards, the same false positive removes an existing
    belief — possibly one that fed a recorded decision — from every retrieval
    surface at once.

    L.0.9 measured that risk directly: **40 of 66 legitimate concepts** are
    refused under strict classes, including `WHO`, `US`, `IT`, `O'Brien`,
    `Achilles' heel`, `1984`, `747`, `C`, `R`,
    `Master of Science in Computer Science` and `To Kill a Mockingbird`.
    Whether that risk is real *in this store* cannot be settled by a count —
    only by reading the surfaces.

    So: **full surfaces, never truncated.** A legitimate concept hides in the
    part a `[:72]` slice cuts off, and this list exists to be read by eye.
    Grouped by rule so a cluster can be judged as a class — a `multi_preposition`
    group of institution names is a different decision from one of sentence
    fragments, and 71 rows are not 71 decisions.
    """
    h(f"[3b] REJECTED SURFACES — evidence class '{evidence_class}', "
      f"grouped by rule")
    print("READ-ONLY. NOTHING IS ARCHIVED BY THIS SCRIPT, EVER.")
    print()
    print("  THIS LIST IS THE INPUT TO A DECISION, NOT THE DECISION.")
    print("  A long list is not a licence to archive. Each of these rows is a")
    print("  belief with provenance that some path once judged worth storing;")
    print("  the gate's rules never saw it and were never validated against")
    print("  it. Read them. The question a rule has to survive is not 'does")
    print("  this catch junk' but 'what legitimate belief does it also take'.")
    print()

    # Sealed-cited ids come from backend.memory.lineage — the SAME function
    # Packet L's archival pass uses to decide what is exempt, so the number
    # read here and the set exempted there cannot drift apart.
    cited_ids, lineage_stats = sealed_cited_belief_ids(conn)

    rows = conn.execute(
        "SELECT id, subject, relation, object, source, weight, archived "
        "FROM triples ORDER BY id").fetchall()

    groups: dict[str, list[dict]] = {}
    n_class = 0
    for r in rows:
        src = r["source"]
        if classify(src) != evidence_class:
            continue
        n_class += 1
        reason = inspect_triple(
            normalise_term(r["subject"]),
            normalise_relation(r["relation"]),
            normalise_term(r["object"]),
            evidence_class=evidence_class,
            weight=r["weight"],
        )
        if reason is None:
            continue
        parts = reason.split(":", 2)
        rule = f"{parts[0]}:{parts[1]}" if len(parts) > 1 else reason
        groups.setdefault(rule, []).append({
            "id":     r["id"],
            "reason": reason,
            "source": src,
            "sealed_cited": r["id"] in cited_ids,
            "archived":     bool(r["archived"]),
            "subject":  r["subject"],
            "relation": r["relation"],
            "object":   r["object"],
        })

    total_rejected = sum(len(v) for v in groups.values())
    if not n_class:
        print(f"  no rows of evidence class '{evidence_class}' in this store.")
        return {"evidence_class": evidence_class, "class_rows": 0,
                "rejected": 0, "by_rule": {}, "groups": {}}

    print(f"  rows of this class     : {n_class}")
    print(f"  the gate would reject  : {total_rejected}"
          f"  ({100.0 * total_rejected / n_class:.1f}%)")
    if "error" in lineage_stats:
        print(f"  sealed-cited lookup    : {lineage_stats['error']}")
    else:
        print(f"  lineage records        : {lineage_stats['records_total']}"
              f"  ({lineage_stats['records_sealed']} sealed)")
        print(f"  sealed-cited beliefs   : {len(cited_ids)}  (all sources; "
              f"cited=true {lineage_stats['cited_true']}, "
              f"NULL {lineage_stats['cited_null']} counted as cited, "
              f"cited=false {lineage_stats['cited_false']} not)")

    # ---- the summary that decides which clusters get read first ----
    print()
    print(f"  {'rule':<34} {'rows':>6} {'sealed-cited':>14}")
    print(f"  {'-' * 34} {'-' * 6} {'-' * 14}")
    for rule, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        n_cited = sum(1 for i in items if i["sealed_cited"])
        flag = "   <<< REVIEW" if n_cited else ""
        print(f"  {rule:<34} {len(items):>6} {n_cited:>14}{flag}")
    any_cited = sum(1 for v in groups.values() for i in v if i["sealed_cited"])
    print()
    if any_cited:
        print(f"  *** {any_cited} rejected row(s) participated in a RECORDED,")
        print("      SEALED decision. A rule that refuses those is refusing")
        print("      beliefs someone may have to defend, and that is a reason")
        print("      to doubt the rule, not the belief. Marked [SEALED-CITED]")
        print("      below.")
    else:
        print("  No rejected row of this class is cited by a sealed record.")
        print("  That is a fact about coverage as much as about safety — see")
        print("  TEMPORAL_PROBE.md: reconstruction coverage equals retrieval")
        print("  history, and a store with few sealed records exempts few rows")
        print("  because few were ever recorded, not because none mattered.")

    # ---- the full surfaces, untruncated ----
    for rule, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        n_cited = sum(1 for i in items if i["sealed_cited"])
        print()
        print("-" * 72)
        print(f"  {rule}   —   {len(items)} row(s), {n_cited} sealed-cited")
        print("-" * 72)
        for it in items:
            marks = []
            if it["sealed_cited"]:
                marks.append("SEALED-CITED")
            if it["archived"]:
                marks.append("already archived")
            if it["source"] != "extraction":
                marks.append(f"source={it['source']}")
            mark = ("  [" + "; ".join(marks) + "]") if marks else ""
            # FULL surface. Never sliced — a legitimate concept hides in the
            # part a truncation cuts off, and this list is read by eye.
            print(f"  {it['id']:>6}  {it['subject']} | {it['relation']} | "
                  f"{it['object']}{mark}")
            if it["reason"] != rule:
                print(f"          rule detail: {it['reason']}")

    print()
    print("=" * 72)
    print("  NOTHING WAS CHANGED. If a rule's cluster reads as legitimate")
    print("  concepts, the finding is that the RULE is wrong for existing")
    print("  rows — which is what K.2 concluded twice already, loosening")
    print("  `too_short` and widening STRICT_CLASSES when the sentinels")
    print("  disagreed with the gate.")
    print("=" * 72)

    return {
        "evidence_class": evidence_class,
        "class_rows":     n_class,
        "rejected":       total_rejected,
        "sealed_cited_total": any_cited,
        "lineage":        lineage_stats,
        "by_rule": {rule: {"rows": len(items),
                           "sealed_cited": sum(1 for i in items
                                               if i["sealed_cited"])}
                    for rule, items in groups.items()},
        "groups": groups,
    }


def length_evidence(conn) -> dict:
    h("[4] TERM LENGTH — the cap, and the boundary it sits on")
    print("K.2 shipped NO word-count cap on purpose, and said what would decide")
    print("it. O.3 measured the threshold (7/7 recall, 0/23 false positives on")
    print(f"a hand-built corpus) and shipped MAX_TERM_TOKENS = {MAX_TERM_TOKENS},")
    print("strict classes only. This section is now the check on that number")
    print("against the store's OWN terms rather than a synthetic corpus.\n")
    print("  P.1 CORRECTION — THE WORD COUNTS BELOW USED TO BE WRONG.")
    print("  This section counted with `len(t.split())`, which does not split")
    print("  underscores, while extraction's prompt asks for snake_case. So")
    print("  every snake_cased clause counted as ONE word:")
    print("      TAK-441_prolonged_the_QT_interval_as_a_potential_indicator...")
    print("      split() -> 1     the gate's own _tokens() -> 11")
    print("  The 1-word bucket therefore CONTAINED CLAUSES, and any '7+ words'")
    print("  figure taken from it was an undercount. It now counts with the")
    print("  gate's tokeniser, so this distribution and the rule that reads it")
    print("  finally agree.\n")

    terms: Counter = Counter()
    for r in conn.execute("SELECT subject, object FROM triples"):
        for t in (r["subject"], r["object"]):
            terms[normalise_term(t)] += 1
    # The gate's own tokeniser — not `split()`. See the correction above.
    words = {t: len(_tokens(t)) for t in terms}
    buckets = Counter(min(w, 10) for w in words.values())
    print("word-count distribution of distinct terms (gate tokeniser):")
    for w in sorted(buckets):
        label = f"{w}+" if w == 10 else str(w)
        flag = "   <- refused by the cap" if w >= MAX_TERM_TOKENS else ""
        print(f"  {label:>3} word(s): {buckets[w]:>5}{flag}")

    refused = [t for t, w in words.items() if w >= MAX_TERM_TOKENS]
    print(f"\n  the cap refuses : {len(refused)} distinct term(s)")
    print(f"  the cap declines: {sum(n for w, n in buckets.items() if w < MAX_TERM_TOKENS)} "
          f"term(s) below the threshold")

    # ---- THE BOUNDARY, PRINTED IN FULL --------------------------------------
    # This is the false-positive measurement the synthetic corpus cannot give.
    # A cap is only as good as the longest LEGITIMATE term it lets through, so
    # the population that decides it is the one just below the line. Printed
    # untruncated and read by eye, exactly as §3b's surfaces are.
    print()
    print("  " + "-" * 70)
    print(f"  THE BOUNDARY — every term of {MAX_TERM_TOKENS - 3} to "
          f"{MAX_TERM_TOKENS - 1} words, in full.")
    print("  These are the terms the cap DECLINES to refuse. If any of them is")
    print("  a clause, the cap is too loose. If any term just above the line is")
    print("  a legitimate concept, the cap is too tight. Nothing else decides")
    print("  this — a synthetic corpus cannot, and O.3's said so.")
    print("  " + "-" * 70)
    for w in range(MAX_TERM_TOKENS - 3, MAX_TERM_TOKENS):
        at = sorted(t for t, n in words.items() if n == w)
        print(f"\n  [{w} words] {len(at)} term(s)")
        for t in at:
            verdict = inspect_triple(t, "is_a", "thing",
                                     evidence_class="extraction")
            mark = f"   [already refused: {verdict}]" if verdict else ""
            print(f"      {t}{mark}")

    long_terms = sorted(refused, key=lambda t: -words[t])
    print(f"\n  terms AT OR ABOVE the cap: {len(long_terms)}")
    for t in long_terms[:20]:
        other = inspect_triple(t, "is_a", "thing", evidence_class="extraction")
        # what would have caught it BEFORE the cap existed
        mark = "also caught by another rule" if other and "too_many_tokens" not in str(other) \
               else "** the cap is the only thing catching this **"
        print(f"  [{words[t]:>2}w] {t[:56]:<56} {mark}")

    print("\n  A cap that refuses a legitimate concept is worse than no cap:")
    print("  the belief is lost silently and the operator never learns of it.")
    print("  Read the boundary block above before trusting this threshold on")
    print("  any corpus other than the one it was measured on.")
    return {"distinct_terms": len(terms), "long_terms": len(long_terms),
            "cap": MAX_TERM_TOKENS,
            "refused_by_cap": len(refused),
            "boundary": {str(w): sorted(t for t, n in words.items() if n == w)
                         for w in range(MAX_TERM_TOKENS - 3, MAX_TERM_TOKENS)},
            "buckets": {str(k): v for k, v in buckets.items()}}


def hostile_set() -> dict:
    h("[5] THE AUDIT'S HOSTILE SET, against the live gate")
    cases = [
        ("To address", "addressing", "issue of finding the latest news about Iran"),
        ("activation nodes related to 'routes'", "partial or inactive", "this session's context"),
        ("My understanding", "derived_from", "conversation_history we've shared"),
        ("x" * 500, "is_a", "y" * 500),
        ("['a', 'b', 'c']", "is_a", "['d']"),
        ("Traceback (most recent call last): ValueError", "raised", "line 42"),
        ("<script>alert(1)</script>", "is_a", "xss"),
        ("null", "is_a", "None"),
        ("cat", "", "mammal"),
        ("Connection refused", "is_a", "thing"),
    ]
    results = {}
    for s, r, o in cases:
        reason = inspect_triple(normalise_term(s), normalise_relation(r),
                                normalise_term(o), evidence_class="extraction")
        results[s[:40]] = reason
        print(f"  {'REJECT' if reason else '** ACCEPTED **':<16} {reason or '':<34} {s[:34]}")
    return results


def integrity(src: Path) -> dict:
    h("[6] INTEGRITY — the record is untouched by all of the above")
    conn = ro(src)
    fields = ("subject", "relation", "object", "weight", "source", "created_at")
    fp = {r["id"]: tuple(r[f] for f in fields)
          for r in conn.execute("SELECT id, " + ", ".join(fields) + " FROM triples")}
    counts = {"triples": len(fp)}
    for table in ("lineage_records",):
        try:
            counts[table] = conn.execute(
                f"SELECT COUNT(*) n FROM {table}").fetchone()["n"]
        except sqlite3.Error:
            pass
    conn.close()
    print(f"  triples          : {counts['triples']}")
    if "lineage_records" in counts:
        print(f"  lineage records  : {counts['lineage_records']}")
    print("  This script opened the database mode=ro throughout. Compare these")
    print("  numbers, and the file's md5, before and after — they must match.")
    return counts


def main() -> int:
    # M.4 — the first statement, before anything is printed. Scripts
    # print em dashes; a piped stdout would otherwise carry cp1252
    # bytes that a UTF-8 reader cannot decode. See scripts/_console.py.
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kb", default="data/kb.db")
    ap.add_argument("--json", help="also write measurements to this file")
    ap.add_argument("--examples", type=int, default=10)
    ap.add_argument("--surfaces-class", default="extraction",
                    help="evidence class whose rejected surfaces §3b prints in full (default: extraction — Packet L.2c's population)")
    args = ap.parse_args()

    src = Path(args.kb).resolve()
    if not src.exists():
        print(f"no database at {src}")
        return 2

    # Law 15's first line through the one formatter (W4's verifier, V3).
    print(store_line(src), flush=True)
    print(f"Packet K verification — {src}")
    print(f"gate enabled: {gate_enabled()} | confidence floor: {min_confidence()}")
    print("The store is opened read-only and is never written.")

    conn = ro(src)
    if not conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                        "AND name='triples'").fetchone()[0]:
        print("no `triples` table — is this a NEXUS kb.db?")
        return 2

    out["before"] = before_state(conn)
    out["relations"] = relations(conn)
    out["gate_replay"] = gate_replay(conn, args.examples)
    out["rejected_surfaces"] = rejected_surfaces(conn, args.surfaces_class)
    out["length_evidence"] = length_evidence(conn)
    out["hostile_set"] = hostile_set()
    conn.close()
    out["integrity"] = integrity(src)

    h("SUMMARY")
    gr = out["gate_replay"]
    rel = out["relations"]
    if gr:
        print(f"  stored rows the gate would reject : {gr['would_reject']} / "
              f"{gr['rows']}  ({gr['pct']}%)")
        top = sorted(gr["by_rule"].items(), key=lambda kv: -kv[1])[:3]
        print(f"  top rules                         : "
              f"{', '.join(f'{k}={v}' for k, v in top)}")
    print(f"  distinct relations                : {rel['distinct_before']} -> "
          f"{rel['distinct_after']} after normalisation (new writes only)")
    print(f"  terms of 7+ words                 : {out['length_evidence']['long_terms']}")
    rs = out.get("rejected_surfaces") or {}
    if rs.get("class_rows"):
        print(f"  '{rs['evidence_class']}' rows rejected           : "
              f"{rs['rejected']} / {rs['class_rows']}  "
              f"(§3b lists every surface in full)")
        print(f"  ...of which sealed-cited          : "
              f"{rs['sealed_cited_total']}"
              f"{'   <<< a rule is refusing recorded evidence' if rs['sealed_cited_total'] else ''}")
    print()
    print("  Nothing was written. No row was deleted, altered or normalised.")
    print("  The replay number is the input to a cleanup decision this packet")
    print("  does not take.")

    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2, default=str),
                                   encoding="utf-8")
        print(f"  measurements written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
