#!/usr/bin/env python
"""Does the semantic channel actually find things? Measured, not asserted.

WHY A SCRIPT AND NOT A TEST
    The suite's embedder is `_offline_embed`, a SHA-256 hash expanded to 768
    positive floats. W.0 measured what it is worth as a semantic signal: over
    400 unrelated text pairs the cosine ranged 0.766-0.995, median 0.935. It
    cannot tell "cardiac safety" from "banana bread recipe", so nothing built
    on it can measure retrieval quality, and a test that appeared to would be
    measuring its own fixture.

    So the claim is SPLIT, deliberately:

      tests/test_semantic_recall.py   the MECHANISM — a relationship the
                                      lexical channel cannot see reaches the
                                      answer, ranked by provenance, declared
                                      in the result. Deterministic, offline.
      THIS SCRIPT                     the NUMBER — real store, real model,
                                      real questions.

    The alternative was a live daemon inside the suite, which would reacquire
    the 143 outbound calls R.4 removed and put the operator's boot time back.

WHAT IT REPORTS
    Recall@k over a pairs file, with the lexical channel alone and with the
    semantic channel unioned in. Per-question, so a regression is attributable
    rather than a moved average. Plus the questions that still find nothing,
    because those are the next packet's input.

ON SAMPLE SIZE — READ THIS BEFORE BELIEVING A SMALL DIFFERENCE
    V.2 measured the same prompt twice on 20 chunks and got 0.08 then 0.13
    pick rate. An unchanged input moved 0.05. The lesson generalised and it
    applies here: with 10 question/claim pairs, one claim changing side moves
    recall by 0.10, which is inside that kind of noise. **Use 30 pairs or
    more.** The script refuses to print a headline comparison below
    MIN_PAIRS and says why, rather than printing a number that would be
    quoted later without its caveat.

THE THRESHOLD, AND THE THING THAT USUALLY BINDS INSTEAD
    `DEFAULT_MIN_SIMILARITY` is 0.35, fitted to two measurements (signal
    0.4047 / control 0.2570 on the operator's store). n=1; sweeping
    `--thresholds` over 30+ pairs is what turns it into a fitted number.

    But read the BINDING CONSTRAINT column before reaching for the threshold.
    A miss reported as `slots` cannot be fixed by any threshold in any sweep:
    the admission loop walks candidates in DESCENDING similarity and stops at
    the quota, so a lower threshold extends the tail and never reorders the
    head. A flat result across a whole sweep is that signature, and W.1's
    harness printed the sweep without the diagnosis.

USAGE
    python scripts/measure_retrieval.py --db data/kb.db --pairs pairs.json
    python scripts/measure_retrieval.py --db data/kb.db --pairs pairs.json \
        --thresholds 0.3,0.4,0.5,0.6,0.7
    python scripts/measure_retrieval.py --write-template pairs.json

    The pairs file is a JSON array:
        [{"question": "what do we know about cardiac safety?",
          "expect":   ["11.4ms", "QTc"]},
         ...]
    `expect` is a list of substrings; a question COUNTS AS FOUND when every
    one of them appears somewhere in the retrieved surfaces. Substrings rather
    than triple ids so the file survives a re-seeded store, and ALL rather
    than ANY because "retrieved the claim without the number that makes it a
    finding" is the failure O.2b already had once.

    Read-only on the store, and it CHECKS that rather than promising it: the
    file is copied to a temp directory, every read and every embedding lands
    on the copy, and the original's md5 is printed before and after. The copy
    exists so the real `TripleStore` / `SemanticIndex` / `TripleRetriever` can
    be driven — reproducing them in this file is what made the W.1 version
    disagree with production about ranking.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.kb.retriever import extract_terms                    # noqa: E402
from scripts._console import force_utf8_stdio                     # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                 # noqa: E402

MIN_PAIRS = 30

TEMPLATE = [
    {"question": "what do we know about cardiac safety?",
     "expect": ["11.4ms"]},
    {"question": "what was the QTc change at 600mg?",
     "expect": ["11.4ms"]},
]


MATCH_MODES = ("any", "all")


def _load_pairs(path: Path) -> list[dict]:
    """Parse the pairs file, INCLUDING the rule each pair is scored under.

    W.4 — `match` defaults to "any", and that default is the whole fix.
    Every figure this harness printed before W.4 was produced by an
    unconditional ALL: a pair listing five fragments only counted as found if
    all five were retrieved, and `_diagnose` was stricter still, demanding all
    five inside ONE surface. The operator's file states the opposite intent in
    its own header — the fragments are alternatives, "EVERY stored claim that
    would be a correct answer".

    Measured consequence on their file: 32 of 36 pairs carry more than one
    fragment, so under the old rule almost nothing could resolve whatever the
    store held. `"what does interval training improve?"` has all four of its
    fragments in the store, in four different rows, and could never pass.

    `expect_miss` marks a control — a question that SHOULD find nothing. Group
    D of the operator's file is six such pairs, and the old harness filed
    their correct behaviour under "STILL NOT FOUND ... the next packet's
    input", which is a control group's success reported as failure.
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
        match = str(item.get("match", "any")).strip().lower()
        if match not in MATCH_MODES:
            print(f"  WARNING: {q[:40]!r} has match={match!r}; using 'any'")
            match = "any"
        out.append({"question": q, "expect": exp, "match": match,
                    "expect_miss": bool(item.get("expect_miss", False)),
                    "group": group})
    return out


def _rows(db: Path) -> list[dict]:
    conn = open_read_only(db, row_factory=None)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, subject, relation, object, weight, tier, source "
        "FROM triples WHERE archived = 0").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _surface(r: dict) -> str:
    return f"{r['subject']} {r['relation']} {r['object']}"


def _copy_store(db: Path, into: Path) -> Path:
    """Work on a COPY, and prove the original was not touched.

    W.1's harness reproduced `search_terms`' selection rule in Python because
    constructing a `TripleStore` migrates the database it opens (H.2), and a
    read-only measurement must not rewrite the store it measures.

    **That reproduction was the wrong answer and it was measured wrong.** The
    harness sorted the merged pool by `(tier, stored weight)` while production
    sorts by `(tier, effective_prominence)` and then caps co-activations at 20%
    of the output. On a store two thirds full of high-weight tier-0
    association rows those two orderings disagree completely: the harness
    reported a claim NOT FOUND that production retrieves.

    Copying the file costs a few megabytes and removes the whole class. The
    original's md5 is printed before and after so "read-only" is a checked
    claim rather than a promise.
    """
    target = into / f"measure_{db.name}"
    shutil.copy2(db, target)
    return target


def _md5(path: Path) -> str:
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


async def _embed_all(texts: list[str], provider, *, kind: str) -> list[list[float]]:
    """W.3 — `strict=True`. A measurement may not fall back.

    The hash fallback scores 0.766-0.995 between unrelated texts, so a single
    silent substitution turns every number below into noise wearing the shape
    of a measurement. `EmbeddingUnavailable` propagates to `main()`, which
    names the failure and exits non-zero.
    """
    from backend.vector.embeddings import embed_via_provider
    return [await embed_via_provider(provider, t, kind=kind, strict=True)
            for t in texts]


def _found(surfaces: list[str], expect: list[str],
           match: str = "any") -> bool:
    """Does this set of surfaces satisfy the expectation?

    `any` — one fragment is enough. This is the default and it is what a list
    of alternative correct answers means.
    `all` — every fragment must appear. Correct for a pair like
    `["11.4ms", "supratherapeutic"]`, where the second is what makes the first
    the right row rather than a coincidence, and O.2b's failure — retrieving
    the claim without the number that makes it a finding — is the reason the
    mode exists at all.
    """
    joined = " ".join(surfaces).lower()
    test = all if match == "all" else any
    return test(e.lower() in joined for e in expect)


def _diagnose(idx, qv, expect: list[str], *, slots: int, threshold: int,
              n_rows: int, match: str = "any") -> dict:
    """WHY a question missed, and WHICH fragments the store does not hold.

    A threshold sweep can only move the tail of the candidate list. Three
    failure modes are invariant to it, and the operator's uniform 0.30-0.70
    result was consistent with all three:

        threshold     the best matching row's cosine is below the whole sweep
        slots         it is not in the top-`slots` by cosine, so the budget is
                      spent before the walk reaches it
        rank          admitted, then cut by top_k / prominence

    W.4 — plus two that are about the EXPECTATION rather than retrieval:

        not_indexed   no fragment appears in any indexed surface. This is now
                      a statement about the store, which is what the name
                      always implied.
        unsatisfiable `match="all"` and no single surface holds every
                      fragment, though some fragments do exist. Reported
                      separately because the fix is to the pairs file, not to
                      retrieval.

    **`not_indexed` used to mean neither of those.** It fired whenever no ONE
    surface contained EVERY fragment, so on the operator's file it fired for
    14 of 15 misses and said nothing about the store at all. `missing` now
    carries the fragments genuinely absent, because "your expectation is
    unmeetable" and "these three words are nowhere in your data" need
    different actions and the old verdict covered both.
    """
    ranked = idx.search(qv, k=n_rows, min_similarity=-1.0)
    surfaces = {tid: (idx.surface_for(tid) or "") for tid, _s in ranked}
    corpus = " || ".join(surfaces.values()).lower()
    missing = [e for e in expect if e.lower() not in corpus]

    for pos, (triple_id, sim) in enumerate(ranked, start=1):
        if not _found([surfaces[triple_id]], expect, match):
            continue
        binding = ("threshold" if sim < threshold
                   else "slots" if pos > slots
                   else "rank")
        return {"found_in_index": True, "cosine": sim, "rank": pos,
                "of": len(ranked), "binding": binding, "missing": missing}

    binding = "not_indexed" if len(missing) == len(expect) else "unsatisfiable"
    return {"found_in_index": False, "cosine": None, "rank": None,
            "of": len(ranked), "binding": binding, "missing": missing}


async def main_async(args) -> int:
    import os
    import tempfile

    from backend.kb.retriever import TripleRetriever
    from backend.kb.semantic_index import (
        SemanticIndex, excluded_relations, surface_of)
    from backend.kb.tiered_store import TripleStore
    from backend.providers.factory import get_provider
    from backend.providers.model_map import embed_prefix, resolve

    rows = _rows(args.db)
    if not rows:
        print(f"no live beliefs in {args.db}")
        return 2
    pairs = _load_pairs(args.pairs)
    if not pairs:
        print(f"no usable pairs in {args.pairs}")
        return 2

    model = resolve("embed")
    print(f"store        : {args.db}  ({len(rows)} live belief(s))")
    print(f"pairs        : {args.pairs}  ({len(pairs)} question(s))")
    print(f"top_k        : {args.top_k}   slots: {args.slots}")
    print(f"embed model  : {model}")
    print(f"task prefix  : query={embed_prefix(model, 'query')!r} "
          f"document={embed_prefix(model, 'document')!r}")
    print(f"excluded     : {list(excluded_relations())}")
    if len(pairs) < MIN_PAIRS:
        print(f"\n*** {len(pairs)} pairs is below MIN_PAIRS={MIN_PAIRS}. The "
              f"per-question table below is still worth reading; the headline "
              f"recall comparison is NOT printed, because one question "
              f"changing side would move it by "
              f"{1 / len(pairs):.2f} and V.2 measured an unchanged input "
              f"moving 0.05 on its own. ***")

    before = _md5(args.db)
    print(f"\nstore md5 before : {before}")
    from backend.vector.embeddings import embedding_status as _es
    baseline_fallbacks = _es()["fallbacks"]

    # W.2 — the store is closed in a `finally` INSIDE the temp directory's
    # scope, not left to garbage collection.
    #
    # On POSIX an open file handle does not stop the directory being removed,
    # so the first version cleaned up fine here and crashed on the operator's
    # Windows box with `PermissionError [WinError 32]` — the file is in use by
    # another process — as `TemporaryDirectory.__exit__` ran with SQLite still
    # holding `measure_kb.db`. Results had already printed; the failure was
    # cleanup only, and a script that reports correctly and then exits non-zero
    # is one a CI job would call a failure.
    tmpdir = tempfile.mkdtemp(prefix="nexus_measure_")
    store = None
    try:
        copy = _copy_store(args.db, Path(tmpdir))
        # Everything below runs against the COPY, through the SAME classes
        # production uses — so what this prints is what a turn would retrieve.
        store = TripleStore(copy)
        idx = SemanticIndex(store)
        provider = get_provider()

        indexable = idx.indexable_count()
        print(f"indexable        : {indexable} of {len(rows)} live "
              f"(substrate relations excluded)")
        print("embedding the store...", flush=True)
        pending = idx.unindexed(limit=indexable or 1)
        vecs = await _embed_all([s for _i, s in pending], provider,
                                kind="document")
        for (triple_id, surface), vec in zip(pending, vecs):
            idx.upsert(triple_id, surface, vec, model=model)

        print("embedding the questions...", flush=True)
        qvecs = await _embed_all([p["question"] for p in pairs], provider,
                                 kind="query")

        # Belt on the braces. `strict=True` above raises on the first bad
        # call, so this should be unreachable — but `fallbacks` is monotone
        # and a recovery cannot clear it, so it also catches a fallback that
        # happened through some path this function does not own.
        from backend.vector.embeddings import embedding_status
        status = embedding_status()
        if status["fallbacks"] > baseline_fallbacks:
            print(f"\n*** {status['fallbacks'] - baseline_fallbacks} EMBEDDING "
                  f"FALLBACK(S) OCCURRED ({status['first_reason']}). Some "
                  f"vector above is a hash, which carries no semantic signal. "
                  f"Refusing to print numbers built on it. ***")
            return 3

        thresholds = [float(t) for t in args.thresholds.split(",") if t.strip()]
        lexical_only = TripleRetriever(store)
        union_arm = TripleRetriever(store, semantic=idx)

        for threshold in thresholds:
            os.environ["NEXUS_SEMANTIC_SLOTS"] = str(args.slots)
            os.environ["NEXUS_SEMANTIC_MIN_SIMILARITY"] = str(threshold)
            lex_found = sem_found = scored = 0
            misses: list[tuple[str, dict]] = []
            rescued: list[str] = []
            leaks: list[str] = []            # controls that WERE retrieved
            controls_held = 0
            print(f"\n{'=' * 72}\nmin_similarity = {threshold:.2f}")
            print(f"{'lex':>4} {'sem':>4}  {'cos':>6} {'rank':>6}  "
                  f"{'match':>5}  question")
            for pair, qv in zip(pairs, qvecs):
                lex = lexical_only.retrieve(pair["question"], top_k=args.top_k)
                lex_ok = _found([surface_of(h.triple) for h in lex],
                                pair["expect"], pair["match"])
                hits = union_arm.retrieve(pair["question"], top_k=args.top_k,
                                          query_embedding=qv)
                sem_ok = _found([surface_of(h.triple) for h in hits],
                                pair["expect"], pair["match"])
                diag = _diagnose(idx, qv, pair["expect"], slots=args.slots,
                                 threshold=threshold, n_rows=max(indexable, 1),
                                 match=pair["match"])

                cos = ("%.4f" % diag["cosine"]) if diag["cosine"] is not None else "--"
                rank = str(diag["rank"]) if diag["rank"] else "--"
                # A control is scored on the OPPOSITE outcome, so its column
                # shows whether it BEHAVED, not whether it was found. Printing
                # a correct control as "." next to a real miss is how six
                # working controls got filed as failures.
                if pair["expect_miss"]:
                    held = not sem_ok
                    controls_held += int(held)
                    if not held:
                        leaks.append(pair["question"])
                    print(f"{'ctl':>4} {'ok' if held else 'LEAK':>4}  "
                          f"{cos:>6} {rank:>6}  {pair['match']:>5}  "
                          f"{pair['question'][:48]}")
                    continue

                scored += 1
                lex_found += int(lex_ok)
                sem_found += int(sem_ok)
                print(f"{'Y' if lex_ok else '.':>4} "
                      f"{'Y' if sem_ok else '.':>4}  {cos:>6} {rank:>6}  "
                      f"{pair['match']:>5}  {pair['question'][:48]}")
                if sem_ok and not lex_ok:
                    rescued.append(pair["question"])
                if not sem_ok:
                    misses.append((pair["question"], diag))

            n = scored
            n_controls = len(pairs) - scored
            if n >= MIN_PAIRS:
                print(f"\nrecall@{args.top_k}  lexical {lex_found / n:.2f}  "
                      f"union {sem_found / n:.2f}  "
                      f"({lex_found} -> {sem_found} of {n} scored pairs)")
            else:
                print(f"\n{lex_found} -> {sem_found} of {n} scored pairs found. "
                      f"Headline recall withheld: fewer than {MIN_PAIRS} "
                      f"scored pairs.")
            if n_controls:
                # Half a measurement without this: a channel that admits
                # everything scores perfect recall and is useless.
                print(f"control   {controls_held} of {n_controls} expected-miss "
                      f"pair(s) correctly found nothing"
                      + ("" if not leaks else f"; {len(leaks)} LEAKED"))
                for q in leaks:
                    print(f"  LEAK: {q}")
            else:
                # Declared, not inferred. A `_group` comment saying "expected
                # to MISS" is prose; only `"expect_miss": true` makes a pair a
                # control, and guessing from the prose would be inferring what
                # the file could simply state. Said out loud because a recall
                # figure with no control arm is half a measurement, and its
                # absence is otherwise invisible.
                print("control   none declared. Add \"expect_miss\": true to "
                      "the pairs that should find nothing — without them a "
                      "channel that admits everything scores perfect recall.")
            if rescued:
                print(f"\nrescued by the semantic channel ({len(rescued)}):")
                for q in rescued:
                    print(f"  {q}")
            if misses:
                # WHY, not just WHICH. A miss whose binding constraint is
                # `slots` cannot be fixed by any threshold in the sweep, and
                # printing the threshold column next to it without saying so is
                # how a whole sweep gets misread as evidence about similarity.
                print(f"\nSTILL NOT FOUND ({len(misses)}), with the binding "
                      f"constraint for each:")
                for q, d in misses:
                    print(f"  [{d['binding']:>13}] cos="
                          f"{d['cosine'] if d['cosine'] is None else round(d['cosine'], 4)}"
                          f" rank={d['rank']} of {d['of']}  {q}")
                    if d["missing"]:
                        print(f"      absent from the store: {d['missing']}")
                kinds = {}
                for _q, d in misses:
                    kinds[d["binding"]] = kinds.get(d["binding"], 0) + 1
                for kind, note in (
                    ("not_indexed",
                     "no fragment appears anywhere in the store. That is the "
                     "PAIRS FILE, not retrieval — fix the fragments (see "
                     "scripts/audit_pairs.py) before reading these as recall."),
                    ("unsatisfiable",
                     "match='all' and no single surface holds every fragment, "
                     "though some fragments do exist. Also the pairs file: "
                     "either split the pair or set match='any'."),
                ):
                    if kinds.get(kind):
                        print(f"\n  {kinds[kind]} miss(es) bind on "
                              f"{kind.upper()}. {note}")
                if kinds.get("slots"):
                    print(f"\n  {kinds['slots']} miss(es) bind on SLOTS. No "
                          f"threshold in this sweep can reach them — the "
                          f"admission loop walks descending similarity and "
                          f"stops at the quota, so a lower threshold extends "
                          f"the tail and never reorders the head. Raise "
                          f"--slots, or narrow what is indexed.")
    finally:
        # Order matters: the connection must be gone before the directory is,
        # or Windows refuses to remove the file underneath it.
        if store is not None:
            store.close()
        shutil.rmtree(tmpdir, ignore_errors=True)

    after = _md5(args.db)
    print(f"\nstore md5 after  : {after}  "
          f"{'UNCHANGED' if after == before else '*** MUTATED ***'}")
    return 0 if after == before else 4


def _abort_on_fallback(fn):
    """W.3 — a measurement may not fall back, so it exits instead.

    Named, not swallowed: the operator's report was that both scripts "silently
    continue after embedding fallback engaged", and the fix has to be visible
    at the exit code as well as in the text. 5 is this failure and nothing
    else, so a wrapper script can branch on it.
    """
    from backend.vector.embeddings import EmbeddingUnavailable
    try:
        return asyncio.run(fn)
    except EmbeddingUnavailable as e:
        print(f"\n*** ABORTED — EmbeddingUnavailable ***\n{e}\n\n"
              f"Nothing was measured and nothing was written. Fix the "
              f"provider (a ReadTimeout usually means the model is not "
              f"resident yet — embed once by hand first) and re-run.")
        return 5


def main() -> int:
    force_utf8_stdio()
    load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "kb.db")
    ap.add_argument("--pairs", type=Path, default=ROOT / "retrieval_pairs.json")
    ap.add_argument("--top-k", type=int, default=20, dest="top_k")
    ap.add_argument("--slots", type=int, default=5)
    ap.add_argument("--thresholds", type=str, default="0.55")
    ap.add_argument("--write-template", type=Path, default=None)
    args = ap.parse_args()
    print(store_line(args.db), flush=True)              # law 15: the store first (W4 part 4)

    print(__doc__.split("USAGE")[0].strip())
    print("=" * 72)

    if args.write_template:
        args.write_template.write_text(json.dumps(TEMPLATE, indent=1),
                                       encoding="utf-8")
        print(f"\nwrote a {len(TEMPLATE)}-pair TEMPLATE to "
              f"{args.write_template}. It is a shape, not a corpus — write at "
              f"least {MIN_PAIRS} questions you would actually ask, including "
              f"ones you expect to fail, before quoting any number from this "
              f"script.")
        return 0

    if not args.db.is_file():
        print(f"no store at {args.db}")
        return 2
    if not args.pairs.is_file():
        print(f"no pairs file at {args.pairs} — start with --write-template")
        return 2
    return _abort_on_fallback(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
