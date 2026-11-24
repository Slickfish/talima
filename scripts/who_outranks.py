#!/usr/bin/env python
"""A claim lost its semantic slot. What took the slots above it?

WHY THIS EXISTS SEPARATELY FROM measure_retrieval.py
    That script answers WHICH constraint binds — `threshold`, `slots`, `rank`
    or `not_indexed` — and on the operator's store it answered `slots`:

        [slots] cos=0.5613 rank=40 of 288  "what do we know about cardiac safety?"

    Knowing the quota binds is not enough to act, because two different
    findings produce that same line and they have opposite fixes:

      - the 39 rows above are substrate artefacts or generic filler, in which
        case they should not be in the index at all and the quota is fine;
      - they are legitimate claims that genuinely answer the question better,
        in which case the index is right and the question is whether five
        slots is the correct budget.

    Raising the quota because it makes the measurement pass is fitting the
    constant to the one case it was tested against — the mistake the 0.55
    threshold already made in the other direction. So: look at the rows.

WHAT IT ESTABLISHES, AND WHAT IT LEAVES TO YOU
    It prints the claim's rank and cosine, then every row above it with its
    `source` and `relation`, then a histogram of both. It does not propose an
    exclusion, a quota or a threshold — same discipline as
    `propose_contradiction_pairs.py` and `propose_relation_vocabulary.py`, and
    for the same reason: only someone who knows the domain can tell a
    legitimate competitor from an artefact, and the store contains both.

ONE THING ALREADY RULED OUT, SO YOU DO NOT RE-DERIVE IT
    NEXUS's own code mints exactly two relations — `co_activated`
    (`backend/memory/neocortex.py`) and `recurring_query_term`
    (`backend/memory/procedural.py`), established by AST over every tracked
    backend file — and `DEFAULT_EXCLUDED_RELATIONS` already excludes both.
    **There is no third substrate category to find.** Anything above the claim
    therefore came from a seeder, from extraction, or from a person, which is
    why the output groups by `source` and not only by `relation`.

    Worth knowing while reading the output: ConceptNet's `/r/RelatedTo`
    normalises to `related_to` (`backend/kb/seeder.py:29`) and its concepts are
    single words, so those surfaces are three tokens — the same shape that let
    `co_activated` rows outscore an eleven-token claim. Generic seeded
    background is not an artefact; it is knowledge you asked for. Whether it
    should compete with an asserted finding for the same five slots is a
    design question this script exists to inform, not to settle.

READ-ONLY, AND CHECKED RATHER THAN PROMISED
    The store is copied to a temp directory; every read, every embedding and
    the whole index build land on the copy; the original's md5 is printed
    before and after. The copy exists so the REAL `TripleStore`,
    `SemanticIndex` and ranking can be driven — reproducing them here is what
    made an earlier harness disagree with production about what it retrieves
    (W.2, defect C). Constructing a `TripleStore` migrates the file it opens,
    which is why the original is never opened by one (H.2).

USAGE
    python scripts/who_outranks.py --db data/kb.db \
        --question "what do we know about cardiac safety?" --expect 11.4ms

    python scripts/who_outranks.py --expect 11.4ms --expect QTc --top 60

    `--expect` is repeatable and every one must appear in a row's surface for
    it to count as the claim — ALL rather than ANY, because "found the claim
    without the number that makes it a finding" is the failure O.2b had once.

    It costs one embedding call per indexable belief plus one for the
    question, against whatever `LLM_BACKEND` names. Nothing is written to the
    real store and no index is installed.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.kb.semantic_index import (                          # noqa: E402
    SemanticIndex, excluded_relations, semantic_slots, surface_of)
from backend.kb.tiered_store import TripleStore                  # noqa: E402
from backend.providers.factory import get_provider               # noqa: E402
from backend.providers.model_map import embed_prefix, resolve    # noqa: E402
from backend.vector.embeddings import (                          # noqa: E402
    embed_via_provider, embedding_status)
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402


def _md5(path: Path) -> str:
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _matches(surface: str, expect: list[str], match: str = "all") -> bool:
    low = surface.lower()
    test = all if match == "all" else any
    return test(e.lower() in low for e in expect)


async def run(args) -> int:
    print(f"store        : {args.db}")
    print(f"question     : {args.question!r}")
    print(f"expect       : {args.expect}")
    model = resolve("embed")
    print(f"embed model  : {model}")
    print(f"task prefix  : query={embed_prefix(model, 'query')!r} "
          f"document={embed_prefix(model, 'document')!r}")
    print(f"excluded     : {list(excluded_relations())}")
    print(f"slots        : {semantic_slots()} "
          f"(NEXUS_SEMANTIC_SLOTS; the quota this is about)")

    before = _md5(args.db)
    print(f"\nstore md5 before : {before}")
    baseline_fallbacks = embedding_status()["fallbacks"]

    # mkdtemp, not TemporaryDirectory: the connection must be closed BEFORE
    # the directory is removed. On POSIX an open file can be unlinked, so the
    # context-manager version worked here and failed on the operator's Windows
    # box with `PermissionError [WinError 32]` after printing every number.
    tmp = tempfile.mkdtemp(prefix="nexus_who_")
    store = None
    try:
        copy = Path(tmp) / f"who_{args.db.name}"
        shutil.copy2(args.db, copy)
        store = TripleStore(copy)
        idx = SemanticIndex(store)
        provider = get_provider()

        indexable = idx.indexable_count()
        if not indexable:
            print("nothing indexable in this store")
            return 2
        print(f"indexable        : {indexable} belief(s)")
        print("embedding the store...", flush=True)
        pending = idx.unindexed(limit=indexable)
        # W.3 — `strict=True`. A ranking is a measurement, and a measurement
        # may not fall back: the hash vector scores 0.766-0.995 between
        # unrelated texts, so one silent substitution mid-run would reorder
        # this table without changing how it looks. Raises
        # `EmbeddingUnavailable` on the FIRST bad call rather than embedding
        # the rest of the store to reach a verdict it already knows.
        for triple_id, surface in pending:
            vec = await embed_via_provider(provider, surface, kind="document",
                                           strict=True)
            idx.upsert(triple_id, surface, vec, model=model)

        print("embedding the question...", flush=True)
        qv = await embed_via_provider(provider, args.question, kind="query",
                                      strict=True)

        # Belt on the braces, and it reads the MONOTONE counter rather than
        # `degraded`. A transient ReadTimeout sets the flag and the next
        # success clears it, so the old check could not see a fallback that
        # had already happened — which is exactly the silent continue this
        # guard exists to end.
        status = embedding_status()
        if status["fallbacks"] > baseline_fallbacks:
            print(f"\n*** {status['fallbacks'] - baseline_fallbacks} EMBEDDING "
                  f"FALLBACK(S) OCCURRED ({status['first_reason']}). At least "
                  f"one vector above is a hash, which carries no semantic "
                  f"signal. Refusing to rank on it. ***")
            return 3

        ranked = idx.search(qv, k=indexable, min_similarity=-1.0)
        meta = {t.id: t for t in store.all()}

        cut = None
        for pos, (triple_id, sim) in enumerate(ranked, start=1):
            if _matches(surface_of(meta[triple_id]), args.expect, args.match):
                cut = pos
                print(f"\nTHE CLAIM : rank {pos} of {len(ranked)}   "
                      f"cos={sim:.4f}  (match={args.match})")
                print(f"            {surface_of(meta[triple_id])}")
                break
        if cut is None:
            # W.4 — say WHICH fragments are absent, and check whether the
            # match rule is what failed. Pasting a multi-fragment `expect`
            # list straight out of a pairs file into `--match all` reproduces
            # the harness defect this script was used to diagnose, and the old
            # message ("the substrings are wrong") would have sent the reader
            # looking for a missing belief that was half there.
            corpus = " || ".join(surface_of(t) for t in meta.values()).lower()
            missing = [e for e in args.expect if e.lower() not in corpus]
            print(f"\nNO INDEXED BELIEF MATCHES {args.expect} under "
                  f"match={args.match!r}.")
            if missing:
                print(f"  absent from the store entirely: {missing}")
            else:
                print(f"  every fragment IS in the store — just never all in "
                      f"one surface. Re-run with --match any, or name the one "
                      f"claim you mean.")
            if args.match == "all" and len(args.expect) > 1 and not missing:
                for pos, (triple_id, sim) in enumerate(ranked, start=1):
                    if _matches(surface_of(meta[triple_id]), args.expect, "any"):
                        print(f"  under --match any it would be rank {pos} of "
                              f"{len(ranked)}, cos={sim:.4f}: "
                              f"{surface_of(meta[triple_id])[:60]}")
                        break
            print("  Nothing here is about ranking.")
            return 2

        if cut == 1:
            print("\nNothing outranks it — on this store the quota is not the "
                  "binding constraint for this question.")
            return 0

        above = ranked[:cut - 1][:args.top]
        print(f"\nTHE {cut - 1} ROW(S) ABOVE IT"
              + (f" (showing the first {len(above)})" if cut - 1 > len(above)
                 else "") + ":")
        print(f"{'cos':>6}  {'source':<18} {'relation':<26} surface")
        rel_hist: dict[str, int] = {}
        src_hist: dict[str, int] = {}
        for triple_id, sim in ranked[:cut - 1]:
            t = meta[triple_id]
            rel_hist[t.relation] = rel_hist.get(t.relation, 0) + 1
            src_hist[t.source] = src_hist.get(t.source, 0) + 1
        for triple_id, sim in above:
            t = meta[triple_id]
            print(f"{sim:6.4f}  {t.source:<18} {t.relation:<26} "
                  f"{surface_of(t)[:58]}")

        print(f"\nBY SOURCE  — the question this script is really answering:")
        for s, n in sorted(src_hist.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4}  {s}")
        print(f"\nBY RELATION:")
        for r, n in sorted(rel_hist.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>4}  {r}")

        print(f"\nREAD THIS BEFORE CHANGING ANYTHING. If these rows are "
              f"filler, the fix is to stop indexing them and the quota stays "
              f"as it is. If they are claims that genuinely answer the "
              f"question better, the index is right and the open question is "
              f"the budget — a different decision needing a different "
              f"justification. If they are generic seeded background "
              f"competing with an asserted finding, neither exclusion nor a "
              f"bigger quota is the answer: provenance governs the ORDER "
              f"today and has no say in ADMISSION, and that is a design gap "
              f"rather than a constant to tune.")
    finally:
        # Order matters — see the mkdtemp comment above.
        if store is not None:
            store.close()
        shutil.rmtree(tmp, ignore_errors=True)

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
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "kb.db")
    ap.add_argument("--question", type=str,
                    default="what do we know about cardiac safety?")
    ap.add_argument("--expect", action="append", default=None,
                    help="substring the claim must contain; repeatable, ALL "
                         "must match")
    ap.add_argument("--match", choices=("all", "any"), default="all",
                    help="ALL fragments in one surface (default; you are "
                         "naming one claim) or ANY of them — use 'any' when "
                         "pasting an alternatives list from a pairs file")
    ap.add_argument("--top", type=int, default=40,
                    help="how many of the rows above to print in full")
    args = ap.parse_args()
    if not args.expect:
        args.expect = ["11.4ms"]

    print(__doc__.split("USAGE")[0].strip())
    print("=" * 72)
    if not args.db.is_file():
        print(f"no store at {args.db}")
        return 2
    return _abort_on_fallback(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
