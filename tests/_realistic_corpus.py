"""A store shaped like the operator's, for tests that must not pass by luck.

WHY THIS EXISTS (W.2, defect B)
    W.1's acceptance test ran on a FOUR-row corpus with a five-slot semantic
    budget. Every row was therefore admitted regardless of its similarity
    rank, and the test passed by exhaustion rather than by the claim winning
    anything. Measured afterwards: at `slots=1` it failed, because even in
    those four rows the claim was 2nd — behind `QT interval is_a_segment_of
    ECG`. On a 651-row store shaped like the operator's it was **109th**, and
    all 108 rows above it were `co_activated`.

    So the corpus a retrieval test runs on has to be big enough for the slot
    budget to bind, and shaped like the thing it stands in for. The operator's
    store is roughly two thirds substrate-minted association rows, and those
    rows are three tokens long and assembled from query vocabulary — they are
    the competition, and a corpus without them measures nothing.

WHAT IT IS NOT
    It is not the operator's data and it is not a benchmark. It is a shape:
    enough rows that `slots` is a real constraint, and enough `co_activated`
    boilerplate that a claim has to beat it rather than arrive by default.
    The NUMBER still comes from `scripts/measure_retrieval.py`.
"""
from __future__ import annotations

from backend.kb.granularity import Granularity
from backend.kb.tiered_store import TripleStore

# Terms drawn from the operator's own domain, so the co-activation rows are
# built out of the same vocabulary the failing question uses. That is the
# whole point: "safety co_activated qtc" scores 0.61 against "what do we know
# about cardiac safety?" while the claim itself scores 0.40.
TERMS = ["qt", "qtc", "dose", "cohort", "ecg", "interval", "study", "patient",
         "plasma", "auc", "cmax", "enzyme", "cyp3a4", "hepatic", "renal",
         "placebo", "baseline", "arm", "endpoint", "safety"]


def add_competition(store: TripleStore, *, coactivations: int = 200,
                    background: int = 120) -> dict:
    """Add the noise a real store carries. Returns what it added."""
    n_coa = 0
    for a in TERMS:
        for b in TERMS:
            if a == b or n_coa >= coactivations:
                continue
            store.add(subject=a, relation="co_activated", object=b,
                      weight=0.95 - (n_coa % 10) * 0.01,
                      tier=0 if n_coa % 3 else 1,
                      granularity=Granularity.TOPIC, source="consolidation")
            n_coa += 1
    for i in range(background):
        store.add(subject=f"finding {i}", relation="observed_in",
                  object=f"cohort {i % 12}", weight=0.7 - (i % 5) * 0.02,
                  tier=2, granularity=Granularity.TOPIC, source="extraction")
    return {"co_activated": n_coa, "background": background}
