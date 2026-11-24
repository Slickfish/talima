"""Packet Q.1 — provenance must govern how prominent evidence ARRIVES.

THE GAP, IN ONE LINE
====================
    F.3 governs how fast evidence fades. Nothing governs how prominent it
    arrives.

That is a hole in the design rather than a bad constant, and it is why the
inversion survived F.3 entirely: class-aware decay was built and class-aware
entry was never asked about.

MEASURED, END TO END, IN P.3
============================
`agentic_loop.py:542` writes `tier = 2`. `rebalance()` is an idempotent
projection of prominence and `band_for(1.0, 2) = 0`, because
`TIER_ENTRY[0] = 0.75`. KAIROS rebalances every cycle at 300s. Nothing filters
confidence — `min_confidence()` is 0.0.

    extraction conf=1.0   tier 2 -> 0
    extraction conf=0.6   tier 2 -> 1
    wikidata   w=0.7      tier 2 -> 1
    fixture    w=0.8      tier 1 -> 0

Retrieval orders `tier ASC`, so a model marking its own homework at confidence
1.0 outranks Wikidata within five minutes of being written. Decay
self-corrects in ~7 days, but every turn writes fresh rows at 1.0, so the
window never closes while the system is in use.

WHY THE BAND AND NOT THE WEIGHT
===============================
Weight is the RECORD layer — F.1 pins it as a monotone high-water mark of
what the extractor actually reported. Scaling it on arrival would write a
number nobody claimed: sanitising, which K.5 ruled against by name. The band
is PROMINENCE, the lossy layer (I-8), and a provenance judgement belongs
there. Same reasoning that made P.2's decomposition acceptable where
sanitising was not.

WHAT THESE SENTINELS ASSERT, AND ON WHAT
========================================
On the RETRIEVER'S OWN ORDERING, never on tier numbers. The property is
"which belief comes back first"; a tier assertion would keep passing while
ranking changed underneath it. P.3's sentinel already pins today's ordering
and will fail when Q.2 lands — that is the design, and it gets updated with
the argument rather than weakened.
"""
from __future__ import annotations

import pytest

from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import TripleStore

# One shared object so every class competes for the same query terms. Without
# this the ordering assertions would be comparing beliefs that never meet.
SHARED = "cardiac safety review"
QUERY = "cardiac safety review"

RECORD_FIELDS = ("id", "subject", "relation", "object", "weight",
                 "granularity", "source", "created_at")


def _competing_store(tmp_path) -> tuple[TripleStore, dict]:
    """Evidence of four classes, all matching one query.

    Weights are chosen so the ordering CANNOT come out right by accident: the
    model's self-report carries the HIGHEST stored weight in the store, and
    the external source the lowest of the three real ones. If provenance does
    not govern arrival, extraction wins on prominence alone.
    """
    store = TripleStore(tmp_path / "kb.db")
    ids = {
        "extraction": store.add(
            subject="model self report", relation="concerns", object=SHARED,
            weight=1.0, tier=2, granularity=Granularity.TURN,
            source="extraction"),
        "external": store.add(
            subject="wikidata entry", relation="concerns", object=SHARED,
            weight=0.7, tier=2, granularity=Granularity.TOPIC,
            source="wikidata"),
        "fixture": store.add(
            subject="seeded fixture", relation="concerns", object=SHARED,
            weight=0.8, tier=1, granularity=Granularity.TOPIC,
            source="offline_fixture"),
        "asserted": store.add(
            subject="operator statement", relation="concerns", object=SHARED,
            weight=0.95, tier=1, granularity=Granularity.TOPIC,
            source="human_assertion"),
    }
    return store, ids


def _order(store) -> list[str]:
    return [h.triple.source
            for h in TripleRetriever(store).retrieve(QUERY, top_k=20)]


def _snapshot(store) -> dict:
    rows = store.conn.execute(
        f"SELECT {', '.join(RECORD_FIELDS)} FROM triples ORDER BY id"
    ).fetchall()
    return {r["id"]: tuple(r[f] for f in RECORD_FIELDS) for r in rows}


# ---------------------------------------------------------------------------
# Non-vacuity first — the fixture must actually pose the question
# ---------------------------------------------------------------------------

def test_the_fixture_really_contains_competing_classes(tmp_path):
    """Without this, every ordering assertion below could pass on a store
    where nothing competes, or where extraction simply happened to be weaker.
    """
    store, _ids = _competing_store(tmp_path)
    order = _order(store)

    assert len(set(order)) >= 3, (
        f"fewer than three evidence classes reached retrieval: {order}")
    for cls in ("extraction", "wikidata"):
        assert cls in order, f"{cls} did not match the shared query: {order}"

    weights = {t.source: t.weight for t in store.all()}
    assert weights["extraction"] == max(weights.values()), (
        "the model's self-report is not the highest-weighted row, so the "
        "ordering test would not be exercising the hard case")
    assert weights["extraction"] > weights["wikidata"], weights


# ---------------------------------------------------------------------------
# 1. The property Q.2 must deliver
# ---------------------------------------------------------------------------

# FLIPPED BY Q.2 — was xfail(strict), failing `assert 3 < 0`:
#   before  ['extraction', 'human_assertion', 'offline_fixture', 'wikidata']
#   after   ['human_assertion', 'offline_fixture', 'wikidata', 'extraction']
def test_provenance_governs_arrival_prominence(tmp_path):
    """External evidence at a LOWER weight must not be out-ranked by a model's
    self-report at confidence 1.0.

    Asserted on the retriever's ordering, not on tier numbers: the property is
    which belief comes back first, and a tier assertion would pass while the
    ranking changed underneath it.
    """
    store, _ids = _competing_store(tmp_path)
    store.rebalance()                      # the KAIROS cycle, once
    order = _order(store)

    assert order.index("wikidata") < order.index("extraction"), (
        f"the model's self-report outranks the external source: {order}")


# ---------------------------------------------------------------------------
# 2. I-8 — this is a PROMINENCE change and must touch no record field
# ---------------------------------------------------------------------------

def test_the_record_layer_is_untouched(tmp_path):
    """UNMARKED: must pass today AND after Q.2, so it constrains the change
    rather than describing it.

    The whole argument for changing the band rather than the weight is that
    weight is the record layer. If Q.2 moves a stored field, it has become
    the sanitising K.5 ruled against, wearing a different name.
    """
    store, _ids = _competing_store(tmp_path)
    before = _snapshot(store)

    store.rebalance()
    store.rebalance()

    assert _snapshot(store) == before, (
        "a RECORD-layer field moved during rebalance; only tier, which is "
        "prominence, may change")
    assert len(before) == 4, "non-vacuity: the fixture must hold four rows"


# ---------------------------------------------------------------------------
# 3. F.3 — the projection must stay idempotent
# ---------------------------------------------------------------------------

def test_rebalance_is_still_idempotent(tmp_path):
    """UNMARKED. F.3's central property: `band_for` is a pure function of
    prominence with hysteresis, so running it ten times leaves the store where
    running it once did. Before F.3 a fully-decayed belief cycled
    [2,3,2,3,...] across ten passes.

    A class argument must not reintroduce that. It is the single most likely
    way to break F.3 while appearing to satisfy Q.2.
    """
    store, _ids = _competing_store(tmp_path)
    store.rebalance()
    settled = {t.id: t.tier for t in store.all()}

    seen = []
    for _ in range(10):
        store.rebalance()
        seen.append({t.id: t.tier for t in store.all()})

    assert all(s == settled for s in seen), (
        f"rebalance oscillates: {settled} then {seen}")
    # ...and the floor still absorbs: nothing may sit outside the bands
    assert all(0 <= t.tier <= 3 for t in store.all())


# ---------------------------------------------------------------------------
# 4. The second consumer — P.2's decomposition feeds this path
# ---------------------------------------------------------------------------

# FLIPPED BY Q.2 — the second consumer is covered by the same discount,
# because the scale is keyed on `source` and decomposition writes
# `extraction`. No separate rule was needed, which is why the sentinel was
# written before the change rather than after.
def test_a_decomposed_row_does_not_inherit_an_unearned_band(tmp_path):
    """The adjacent consumer, pinned before it can surprise anyone.

    `BBC | is_a_reliable_news_source | None` decomposes to
    `BBC | is_a | reliable news source` and is stored as `extraction` — so it
    arrives as prominent as any other model output, and competes with
    Wikidata's own statement about BBC.
    """
    from backend.kb.decompose import decompose

    store = TripleStore(tmp_path / "kb.db")
    parts = decompose("BBC", "is_a_reliable_news_source", "None")
    assert parts, "non-vacuity: the row must actually decompose"

    for d in parts:
        store.add(subject=d.subject, relation=d.relation, object=d.object,
                  weight=1.0, tier=2, granularity=Granularity.TURN,
                  source="extraction")
    store.add(subject="BBC", relation="is_a", object="british broadcaster",
              weight=0.7, tier=2, granularity=Granularity.TOPIC,
              source="wikidata")
    store.rebalance()

    order = [h.triple.source
             for h in TripleRetriever(store).retrieve("BBC", top_k=20)]
    assert "extraction" in order and "wikidata" in order, (
        f"non-vacuity: both classes must reach retrieval, got {order}")
    assert order.index("wikidata") < order.index("extraction"), (
        f"a decomposed model output outranks Wikidata on the same entity: "
        f"{order}")


# ---------------------------------------------------------------------------
# The conservative reduction — a stronger claim than "behaviour unchanged"
# ---------------------------------------------------------------------------

PRE_Q_ORDER = ["extraction", "human_assertion", "offline_fixture", "wikidata"]
POST_Q_ORDER = ["human_assertion", "offline_fixture", "wikidata", "extraction"]


def test_the_reducing_configuration_reproduces_the_measured_pre_q_ordering(
        tmp_path, monkeypatch):
    """ROLLBACK PROOF, and it is exact rather than approximate.

    Setting every class scale to 1.0 makes `effective_prominence` identical to
    `current_weight`, so the band and the sort are bit-identical to pre-Q
    behaviour. Asserted against the ORDER MEASURED IN Q.1, not against a
    vaguer claim that nothing changed — E.2's precedent for shipping a
    provisional default with a way back.
    """
    from backend.kb.tiered_store import CLASS_ARRIVAL_SCALE
    for cls in CLASS_ARRIVAL_SCALE:
        monkeypatch.setenv(f"NEXUS_ARRIVAL_{cls.upper()}", "1.0")

    store, _ids = _competing_store(tmp_path)
    store.rebalance()
    assert _order(store) == PRE_Q_ORDER, (
        "the reducing configuration does not reproduce pre-Q behaviour, so "
        "the change has no exact rollback")


def test_the_shipped_configuration_produces_the_intended_ordering(tmp_path):
    """The before/after in one assertion, on the retriever's own output."""
    store, _ids = _competing_store(tmp_path)
    store.rebalance()
    assert _order(store) == POST_Q_ORDER
    assert PRE_Q_ORDER != POST_Q_ORDER, "non-vacuity: the orders must differ"


def test_the_scale_is_a_discount_and_never_a_boost():
    """Provenance may lower how prominent a claim arrives; it may never
    inflate it above what its own evidence supports. A factor above 1.0 would
    let a class outrank a belief with strictly more evidence behind it."""
    from backend.kb.tiered_store import CLASS_ARRIVAL_SCALE, arrival_scale
    assert all(0.0 < v <= 1.0 for v in CLASS_ARRIVAL_SCALE.values())
    assert arrival_scale("wikidata") == 1.0
    assert arrival_scale("extraction") < 1.0


def test_the_arrival_order_matches_the_decay_order():
    """The one-sentence defence, asserted rather than only written down:
    provenance governs both how prominent evidence arrives and how long it
    stays, so the two orderings are the same ordering.

    If a later packet finds a reason arrival should rank differently from
    persistence, this fails and the divergence has to be argued.
    """
    from backend.kb.tiered_store import CLASS_ARRIVAL_SCALE, CLASS_ORDER
    scales = [CLASS_ARRIVAL_SCALE[c] for c in CLASS_ORDER]
    assert scales == sorted(scales, reverse=True), (
        f"arrival order diverges from CLASS_ORDER: {list(zip(CLASS_ORDER, scales))}")


def test_no_class_is_immune_and_the_floor_still_absorbs(tmp_path):
    """F.3's constraints under the new band. A discount must not create a
    class that cannot fall, and must not stop the floor absorbing."""
    from backend.kb.tiered_store import DAY_SECONDS, LOWEST_TIER
    import time as _time
    store = TripleStore(tmp_path / "kb.db")
    tid = store.add(subject="external claim", relation="concerns", object=SHARED,
                    weight=0.95, tier=0, granularity=Granularity.TOPIC,
                    source="wikidata")
    store.conn.execute("UPDATE triples SET last_fired = ? WHERE id = ?",
                       (_time.time() - 5000 * DAY_SECONDS, tid))
    store.conn.commit()
    store.rebalance()
    t = store.get_by_id(tid)
    assert t.tier == LOWEST_TIER, "an undiscounted class did not reach the floor"
    assert t.current_weight() == 0.0
