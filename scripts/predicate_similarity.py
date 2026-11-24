#!/usr/bin/env python
"""Does predicate similarity separate SAME from OPPOSITE on this corpus?

THE QUESTION THAT DECIDES PACKET X
    OQ-2 wants `primarily_metabolized_by` and
    `is_metabolized_by_primary_enzyme_of` recognised as one relation. The
    cheap way to try is embedding predicates and calling the close ones the
    same.

    **The reason that might not work is the control arm below.** Embeddings
    place antonyms close: `causes` and `prevents` occupy the same
    distributional slot, take the same subjects and objects, and differ only
    in polarity. If the measure that pulls the two metabolism spellings
    together also pulls `causes` to `prevents`, it is not usable — that one
    distinction is what every contradiction pair in the system rests on, and
    mapping a novel predicate to the wrong pole is worse than not mapping it.

    So this prints target, antonym, paraphrase and unrelated groups, and
    states plainly whether any threshold divides target from antonym. **If
    none does, that is the finding and Packet X is that finding rather than a
    build.**

A LEXICAL BASELINE RUNS BESIDE IT, AND IT IS NOT DECORATION
    Measured before any model was involved: the target pair scores jaccard
    0.29 on shared tokens while all six antonym pairs score 0.00. **Token
    overlap separates the motivating case perfectly and at zero cost.** The
    risk of pulling antonyms together belongs to the embedding, not to the
    lexicon — which inverts the usual reading, so both columns print and the
    comparison is the reader's to make.

    Where the lexical baseline genuinely fails is a paraphrase with no shared
    token (`has_low_likelihood_of` / `reduces_risk_of`). That group is here so
    the trade is visible rather than asserted.

MAP-TO-KNOWN, WHICH IS THE DESIGN THE DISTRIBUTION SUPPORTS
    With `--store`, every distinct predicate in the store is embedded and
    matched against the ANCHORS — the offered vocabulary plus the relations
    named in the contradiction pairs. That is the measurement for the design
    that works on the single-row bulk: a predicate used once has no usage to
    cluster, but it can still be mapped onto a relation the system already
    understands. Cluster-by-usage needs members; this does not.

    It reports, per predicate, the nearest anchor and its cosine, so the
    operator can see where a threshold would sit and what it would claim.
    **It proposes nothing.** Reading the list is the job — the same discipline
    as `propose_relation_vocabulary.py`, and for the same reason: only
    someone who knows the domain can tell a real mapping from a confident
    wrong one.

READ-ONLY, AND A MEASUREMENT MAY NOT FALL BACK
    With `--store` the file is copied to a temp directory and read there; the
    original's md5 prints before and after. Every embedding uses
    `strict=True`, so a provider failure aborts with `EmbeddingUnavailable`
    and exit 5 rather than substituting hash vectors whose cosine runs
    0.766-0.995 between unrelated texts (W.0/W.3).

USAGE
    python scripts/predicate_similarity.py
    python scripts/predicate_similarity.py --store --db data/kb.db

    Without `--store` it costs about 30 embedding calls and answers the
    go/no-go. With it, one call per distinct predicate plus one per anchor —
    on a store with ~200 predicates, roughly 250. The count prints before any
    of them are made.

    Exit 0 when target and antonym separate, 1 when they do not, 5 when the
    provider failed.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import hashlib
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.cortex.contradiction import CONTRADICTION_PAIRS     # noqa: E402
from backend.kb.semantic_index import excluded_relations         # noqa: E402
from backend.kb.vocabulary import load_relations                 # noqa: E402
from backend.providers.factory import get_provider               # noqa: E402
from backend.providers.model_map import resolve                  # noqa: E402
from backend.vector.embeddings import (                          # noqa: E402
    EmbeddingUnavailable, embed_via_provider)
from backend.vector.matryoshka import cosine                     # noqa: E402
from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._readonly import open_read_only, store_line  # noqa: E402
from scripts._env import load_env                                # noqa: E402

# THE CONTROL ARM. Every one of these must stay FAR apart, and each is a live
# `CONTRADICTION_PAIRS` member — so a measure that fails here does not merely
# score badly, it breaks contradiction detection.
ANTONYM = tuple(CONTRADICTION_PAIRS)

# The pair OQ-2 was written about, plus the simplest spelling variant.
TARGET = (("primarily_metabolized_by", "is_metabolized_by_primary_enzyme_of"),
          ("metabolized_by", "is_metabolized_by"))

# Genuine synonymy with no shared content token — where a lexical baseline
# must fail and an embedding might not. The trade, made visible.
PARAPHRASE = (("has_low_likelihood_of", "reduces_risk_of"),
              ("observed_in", "measured_in"))

# Must stay far apart under any usable measure.
UNRELATED = (("binds_to", "is_metabolized_by"), ("is_a", "measures"),
             ("stands_for", "reduces_risk_of"))

_WORD = re.compile(r"[a-z0-9]+")


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def jaccard(a: str, b: str) -> float:
    """The lexical baseline. Deliberately the crudest thing that could work,
    so that beating it has to mean something."""
    A, B = set(_WORD.findall(a.lower())), set(_WORD.findall(b.lower()))
    return len(A & B) / max(1, len(A | B))


def anchors() -> tuple[str, ...]:
    """What a novel predicate would be mapped ONTO.

    The offered vocabulary plus the contradiction relations — the two sets the
    system already understands. Derived, never hand-written, so it moves with
    `NEXUS_RELATION_VOCABULARY` and `NEXUS_CONTRADICTION_PAIRS` rather than
    drifting from them.
    """
    rels = set(load_relations()) | {r for pair in CONTRADICTION_PAIRS
                                    for r in pair}
    return tuple(sorted(rels))


def _store_predicates(db: Path) -> collections.Counter:
    conn = open_read_only(db, row_factory=None)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT relation, COUNT(*) AS n FROM triples WHERE archived = 0 "
        "GROUP BY relation").fetchall()
    conn.close()
    excl = set(excluded_relations())
    return collections.Counter({r["relation"]: int(r["n"]) for r in rows
                                if r["relation"] not in excl})


async def run(args) -> int:
    provider = get_provider()
    model = resolve("embed")
    print(f"embed model : {model}")

    predicates: collections.Counter = collections.Counter()
    before = after = ""
    if args.store:
        before = _md5(args.db)
        print(f"store md5 before : {before}")
        tmp = tempfile.mkdtemp(prefix="nexus_predsim_")
        try:
            copy = Path(tmp) / f"sim_{args.db.name}"
            shutil.copy2(args.db, copy)
            predicates = _store_predicates(copy)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        after = _md5(args.db)
        print(f"store md5 after  : {after}  "
              f"{'UNCHANGED' if after == before else '*** MUTATED ***'}")
        if after != before:
            return 4

    anc = anchors()
    control_terms = {p for group in (TARGET, ANTONYM, PARAPHRASE, UNRELATED)
                     for pair in group for p in pair}
    n_calls = len(control_terms) + (len(anc) + len(predicates)
                                    if args.store else 0)
    print(f"anchors     : {len(anc)}")
    if args.store:
        print(f"predicates  : {len(predicates)} distinct in the store")
    print(f"embedding calls this run : {n_calls}\n")

    cache: dict[str, list[float]] = {}

    async def vec(text: str) -> list[float]:
        if text not in cache:
            cache[text] = await embed_via_provider(
                provider, text, kind="document", strict=True)
        return cache[text]

    scores: dict[str, list[float]] = {}
    for title, group in (("TARGET — must be CLOSE", TARGET),
                         ("ANTONYM — must be FAR (the control arm)", ANTONYM),
                         ("PARAPHRASE — close, and no shared token", PARAPHRASE),
                         ("UNRELATED — must be FAR", UNRELATED)):
        print(title)
        print(f"  {'cosine':>7} {'jaccard':>8}  pair")
        vals = []
        for a, b in group:
            c = cosine(await vec(a), await vec(b))
            vals.append(c)
            print(f"  {c:7.4f} {jaccard(a, b):8.2f}  {a} / {b}")
        scores[title] = vals
        print()

    tgt = scores["TARGET — must be CLOSE"]
    ant = scores["ANTONYM — must be FAR (the control arm)"]
    print("=" * 72)
    print(f"target  min {min(tgt):.4f}      antonym max {max(ant):.4f}")
    separated = min(tgt) > max(ant)
    if separated:
        print(f"SEPARATED. Any threshold in ({max(ant):.4f}, {min(tgt):.4f}) "
              f"divides them — a margin of {min(tgt) - max(ant):.4f}. Read the "
              f"margin, not just the verdict: a narrow one is a threshold "
              f"fitted to four pairs, which is the sample size V.2 warned "
              f"about.")
    else:
        print("NOT SEPARATED. No threshold divides the motivating pair from "
              "the antonyms. Predicate similarity over surface strings cannot "
              "carry this on this model, and Packet X is that finding — "
              "record it in OQ-2 beside V.2's widening negative.")
    print(f"lexical baseline: target min {min(jaccard(a, b) for a, b in TARGET):.2f}"
          f"      antonym max {max(jaccard(a, b) for a, b in ANTONYM):.2f}")

    if not args.store:
        print("\nRe-run with --store for the map-to-known table, which is the "
              "design the distribution supports.")
        return 0 if separated else 1

    print("\n" + "=" * 72)
    print("MAP-TO-KNOWN — nearest anchor for each predicate in the store.")
    print("Sorted by cosine descending. A HIGH score is not a correct mapping; "
          "it is a candidate. Read the pairs, especially near the antonym "
          "band above.")
    anchor_vecs = {a: await vec(a) for a in anc}
    table = []
    for pred, n in predicates.items():
        if pred in anchor_vecs:
            continue                      # already a known relation
        pv = await vec(pred)
        best, best_c = "", -1.0
        for a, av in anchor_vecs.items():
            c = cosine(pv, av)
            if c > best_c:
                best, best_c = a, c
        table.append((best_c, n, pred, best))
    table.sort(reverse=True)
    print(f"\n  {'cosine':>7} {'rows':>5}  predicate -> nearest anchor")
    for c, n, pred, best in table[:args.top]:
        print(f"  {c:7.4f} {n:>5}  {pred} -> {best}")
    if len(table) > args.top:
        print(f"  ... {len(table) - args.top} more (raise --top)")
    if table:
        above = sum(1 for c, _n, _p, _a in table if c > max(ant))
        print(f"\n  {above} of {len(table)} off-vocabulary predicates score "
              f"above the antonym ceiling ({max(ant):.4f}). Those are where a "
              f"mapping would fire; the rest would be left alone.")
    return 0 if separated else 1


def main() -> int:
    force_utf8_stdio()
    load_env()      # U.2 — the same .env the server reads
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "kb.db")
    ap.add_argument("--store", action="store_true",
                    help="also embed the store's predicates and report the "
                         "nearest anchor for each")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()
    print(store_line(args.db), flush=True)              # law 15: the store first (W4 part 4)

    print(__doc__.split("USAGE")[0].strip())
    print("=" * 72)
    if args.store and not args.db.is_file():
        print(f"no store at {args.db}")
        return 2
    try:
        return asyncio.run(run(args))
    except EmbeddingUnavailable as e:
        print(f"\n*** ABORTED — EmbeddingUnavailable ***\n{e}\n\n"
              f"Nothing was measured. A similarity table built on hash "
              f"vectors would separate nothing and look exactly like one that "
              f"did.")
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
