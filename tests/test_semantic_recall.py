"""Packet W.1 — the semantic recall channel, and the four things it must not do.

WHAT W.1 ADDS, IN ONE SENTENCE
    A second way for a belief to become a CANDIDATE. Not a second way for it
    to be RANKED.

    W.0 measured why that distinction is the whole design. The operator's
    failing question does not retrieve the wrong thing — it retrieves nothing,
    because `search_terms(['know','about','cardiac','safety'])` returns zero
    rows and the claim never enters the pool. Fusing scores would be answering
    a ranking question that was never asked, and it would put similarity into
    an ordering that Packet Q spent a whole packet making provenance-governed.

    So: union the candidates, keep the ordering exactly as it was.

THE FOUR SENTINELS, AND WHY EACH ONE
    reducible      `NEXUS_SEMANTIC_SLOTS=0` must reproduce today's retrieval
                   exactly — same ids, same order. The rollback (E.2, Q.2,
                   S.2, V.1 precedent).
    provenance     similarity must contribute NOTHING to the order. If it can,
                   a model's paraphrase outranks a human's assertion and
                   Packet Q's inversion is back under a new name — and this
                   time it would look like an improvement.
    chroma stays dark
                   W.0.11. Populating a vector index switches on TWO consumers
                   that have never run against a populated one, and one of
                   them proposes archiving beliefs. This channel therefore
                   does not live in Chroma at all: the separation is
                   structural, not a feature flag someone can flip by
                   accident.
    offline        the suite must not reacquire the 143 outbound calls R.4
                   removed. Retrieval takes a query vector as an argument; it
                   never embeds anything itself.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.semantic_index import (
    DEFAULT_MIN_SIMILARITY, SemanticIndex, surface_of)
from backend.kb.tiered_store import TripleStore

from tests._toy_embed import DIM, toy_embed

REPO = Path(__file__).resolve().parent.parent

HUMAN = "human_assertion"


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

def _corpus(tmp_path) -> TripleStore:
    """The operator's hour, reproduced: one asserted finding, and the textbook
    background the same session accumulated around it."""
    store = TripleStore(tmp_path / "kb.db")
    store.add(subject="QT interval", relation="is_a_segment_of", object="ECG",
              weight=0.7, tier=2, granularity=Granularity.TOPIC,
              source="extraction")
    store.add(subject="QRS complex", relation="represents",
              object="ventricular depolarization", weight=0.7, tier=2,
              granularity=Granularity.TOPIC, source="extraction")
    store.add(subject="TAK-441", relation="primarily_metabolized_by",
              object="CYP3A4", weight=0.7, tier=2,
              granularity=Granularity.TOPIC, source="extraction")
    store.add(subject="mean placebo-corrected QTc change at 600mg "
                      "supratherapeutic dose",
              relation="was", object="11.4ms",
              weight=0.8, tier=1, granularity=Granularity.TOPIC,
              source=HUMAN)
    return store


def _index_everything(store: TripleStore) -> SemanticIndex:
    idx = SemanticIndex(store)
    for t in store.all():
        idx.upsert(t.id, surface_of(t), toy_embed(surface_of(t)), model="toy")
    return idx


# ---------------------------------------------------------------------------
# NON-VACUITY — the failure this packet exists for, still failing
# ---------------------------------------------------------------------------

def test_the_lexical_channel_alone_still_cannot_find_it(tmp_path):
    """If this ever passes on its own, every test below is passing for a
    reason that has nothing to do with semantic recall."""
    store = _corpus(tmp_path)
    hits = TripleRetriever(store).retrieve(
        "what do we know about cardiac safety?", top_k=20)
    assert not any(h.triple.source == HUMAN for h in hits), (
        "the lexical channel found the asserted fact without help — the "
        "premise of Packet W has changed and the design needs re-deriving")


def test_the_two_texts_genuinely_share_no_token(tmp_path):
    """NON-VACUITY for the toy embedder: it must not be smuggling in a lexical
    match. If these overlapped, the acceptance test would prove nothing."""
    from backend.kb.retriever import extract_terms
    store = _corpus(tmp_path)
    claim = [t for t in store.all() if t.source == HUMAN][0]
    claim_words = set(surface_of(claim).lower().replace("-", " ").split())
    question_terms = set(extract_terms("what do we know about cardiac safety?"))
    assert not (claim_words & question_terms), (
        f"question and claim share {claim_words & question_terms}")
    # ...and the toy embedder nonetheless relates them.
    from backend.vector.matryoshka import cosine
    sim = cosine(toy_embed("what do we know about cardiac safety?"),
                 toy_embed(surface_of(claim)))
    assert sim > 0.0, "the toy embedder sees no relationship either"


# ---------------------------------------------------------------------------
# SENTINEL 1 — reducible
# ---------------------------------------------------------------------------

def test_zero_slots_reproduces_todays_retrieval_exactly(tmp_path, monkeypatch):
    """The rollback. A fully populated index and a slots budget of 0 must be
    indistinguishable from not having built any of this."""
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "0")
    store = _corpus(tmp_path)
    idx = _index_everything(store)
    q = "what do we know about cardiac safety?"
    qv = toy_embed(q)

    baseline = TripleRetriever(store).retrieve(q, top_k=20)
    with_index = TripleRetriever(store, semantic=idx).retrieve(
        q, top_k=20, query_embedding=qv)

    assert [h.triple.id for h in baseline] == [h.triple.id for h in with_index]
    assert [h.score for h in baseline] == [h.score for h in with_index]
    assert all(h.channel == "lexical" for h in with_index)
    # NON-VACUITY: identical-to-baseline is only meaningful if the baseline is
    # the FAILING one. If the claim were retrievable here, this test would be
    # asserting that two working retrievals agree.
    assert not any(h.triple.source == HUMAN for h in with_index)


def test_zero_slots_is_the_default(monkeypatch):
    """Installing W.1 changes no behaviour until someone opts in — the same
    promise DEFAULT_ENVELOPE makes for S.2."""
    monkeypatch.delenv("NEXUS_SEMANTIC_SLOTS", raising=False)
    from backend.kb.semantic_index import semantic_slots
    assert semantic_slots() == 0


# ---------------------------------------------------------------------------
# THE ACCEPTANCE PROPERTY
# ---------------------------------------------------------------------------

def test_the_question_with_no_shared_token_now_retrieves_the_claim(
        tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
    store = _corpus(tmp_path)
    idx = _index_everything(store)
    q = "what do we know about cardiac safety?"

    hits = TripleRetriever(store, semantic=idx).retrieve(
        q, top_k=20, query_embedding=toy_embed(q))

    asserted = [h for h in hits if h.triple.source == HUMAN]
    assert asserted, (
        "the operator's own stated fact is still not retrievable for their "
        "own later question. Retrieved instead: "
        + str([surface_of(h.triple) for h in hits]))
    assert "11.4ms" in surface_of(asserted[0].triple)


def test_a_semantically_retrieved_belief_says_so(tmp_path, monkeypatch):
    """DECLARED, NOT SILENT. An answer's evidence must be able to say WHY each
    claim was considered relevant — otherwise "it was in the context" covers
    two different things and neither is auditable."""
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
    store = _corpus(tmp_path)
    idx = _index_everything(store)
    q = "what do we know about cardiac safety?"
    hits = TripleRetriever(store, semantic=idx).retrieve(
        q, top_k=20, query_embedding=toy_embed(q))

    by_channel = {h.channel for h in hits}
    assert "semantic" in by_channel, f"nothing declared semantic: {by_channel}"
    asserted = [h for h in hits if h.triple.source == HUMAN][0]
    assert asserted.channel in ("semantic", "both")
    assert asserted.similarity is not None and asserted.similarity > 0.0


# ---------------------------------------------------------------------------
# SENTINEL 2 — provenance still governs the order
# ---------------------------------------------------------------------------

def test_similarity_cannot_lift_a_paraphrase_over_a_human_assertion(
        tmp_path, monkeypatch):
    """Packet Q's inversion, attempted through the new door.

    The extraction row is a PERFECT semantic match for the query (cosine 1.0)
    and the human assertion is a weaker one. If similarity were a ranking term
    in any amount, the paraphrase would win. It must not.
    """
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
    store = TripleStore(tmp_path / "kb.db")
    store.add(subject="cardiac safety", relation="was", object="acceptable",
              weight=0.8, tier=1, granularity=Granularity.TOPIC,
              source="extraction")
    store.add(subject="QTc change at 600mg", relation="was", object="11.4ms",
              weight=0.8, tier=1, granularity=Granularity.TOPIC, source=HUMAN)
    idx = _index_everything(store)

    q = "cardiac safety was acceptable"          # verbatim the extraction row
    hits = TripleRetriever(store, semantic=idx).retrieve(
        q, top_k=20, query_embedding=toy_embed(q))

    sims = {h.triple.source: h.similarity for h in hits}
    assert sims.get("extraction") is not None
    assert sims["extraction"] > (sims.get(HUMAN) or 0.0), (
        "the setup is wrong — the paraphrase must be the BETTER semantic match "
        "for this test to mean anything")
    assert hits[0].triple.source == HUMAN, (
        "a perfect semantic match outranked a human assertion. Packet Q's "
        "inversion is back: " + str([(h.triple.source, h.similarity) for h in hits]))


def test_order_is_invariant_to_similarity(tmp_path, monkeypatch):
    """The strong form, and the one an AST guard cannot fake.

    Re-index the same corpus with the query vector aimed at a different row
    each time. The similarity values change completely; the returned ORDER
    must not change at all.
    """
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "10")
    store = _corpus(tmp_path)
    idx = _index_everything(store)
    triples = store.all()

    orders, sim_sets = [], []
    for t in triples:
        # A query vector that is a perfect match for THIS row and nothing else.
        qv = toy_embed(surface_of(t))
        hits = TripleRetriever(store, semantic=idx).retrieve(
            "cardiac", top_k=20, query_embedding=qv)
        orders.append([h.triple.id for h in hits])
        sim_sets.append(tuple(sorted(
            round(h.similarity, 4) for h in hits if h.similarity is not None)))

    # W.2 — compare RELATIVE ORDER, not the returned set. Different query
    # vectors legitimately ADMIT different rows; that is the channel doing its
    # job. What must never change is where two rows sit relative to each other,
    # because that is decided by provenance alone. The earlier version compared
    # whole lists and only passed because an uncalibrated embedder admitted
    # everything every time — a fixture-shaped pass.
    for i in range(len(orders)):
        for j in range(i + 1, len(orders)):
            common = [x for x in orders[i] if x in set(orders[j])]
            other = [x for x in orders[j] if x in set(orders[i])]
            assert common == other, (
                f"two runs disagreed on the relative order of the same "
                f"beliefs — similarity reached the ranking: {common} vs {other}")
    assert len(set(sim_sets)) > 1, (
        "NON-VACUITY: the similarities never actually differed, so the "
        "invariance above proves nothing")
    assert any(len(o) > 1 for o in orders), (
        "NON-VACUITY: no run returned two comparable beliefs, so nothing was "
        "ordered at all")


def test_effective_prominence_is_the_only_sort_key(tmp_path):
    """AST, not text search. The sort key in `retrieve` must rank on
    `effective_prominence` and must not mention similarity.

    SNIP-DET — the key now ranks on `ranking_prominence`, the effective
    prominence rounded to the record's precision with the id as the tie-
    break. The guard follows the wrapper: its body must call
    `effective_prominence` and may name nothing from the forbidden set, so
    the wrapper cannot become a different ranking under the same name."""
    src = (REPO / "backend" / "kb" / "retriever.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "retrieve")
    sorts = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "sort"]
    assert sorts, "retrieve() no longer sorts — re-derive this guard"
    forbidden = {"similarity", "sim", "score_semantic", "cosine"}

    def names_in(node) -> set:
        out = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        out |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        return out

    for call in sorts:
        key = next((kw.value for kw in call.keywords if kw.arg == "key"), None)
        assert key is not None, "sort without an explicit key= — unreadable"
        names = names_in(key)
        assert names & {"effective_prominence", "ranking_prominence"}, (
            f"the ranking sort no longer uses effective_prominence: {names}")
        assert not (names & forbidden), (
            f"similarity entered the ranking sort: {names & forbidden}")
        if "ranking_prominence" in names:
            wrapper = next(n for n in ast.walk(tree)
                           if isinstance(n, ast.FunctionDef) and n.name == "ranking_prominence")
            body = names_in(wrapper)
            assert "effective_prominence" in body, (
                "ranking_prominence no longer ranks on effective_prominence")
            assert not (body & forbidden), (
                f"similarity entered ranking_prominence: {body & forbidden}")


# ---------------------------------------------------------------------------
# SENTINEL 3 — Chroma stays dark (W.0.11)
# ---------------------------------------------------------------------------

def test_the_semantic_index_does_not_touch_chroma():
    """W.0.11's answer, enforced structurally.

    Populating `backend/vector/chroma.py` would switch on the RAG branch in
    `assemble_context` AND `SemanticDeduplicator.scan()`, whose confirm path
    archives beliefs — neither of which has ever run against a populated
    index. This channel keeps its vectors in `kb.db`, so those two stay
    exactly as dark as they were.
    """
    tree = ast.parse(
        (REPO / "backend" / "kb" / "semantic_index.py").read_text(encoding="utf-8"))
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module)
    assert not any("vector.chroma" in m for m in imported), (
        f"the semantic index reaches into Chroma: {imported}")


def test_indexing_and_retrieval_leave_the_vector_store_empty(tmp_path, monkeypatch):
    """The behavioural half — an import guard alone would miss an indirect
    route. Measured, not reasoned: count before, count after."""
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
    from backend.vector.chroma import VectorStore
    vec = VectorStore(tmp_path / "chroma")
    before = vec.count()

    store = _corpus(tmp_path)
    idx = _index_everything(store)
    q = "what do we know about cardiac safety?"
    TripleRetriever(store, semantic=idx).retrieve(
        q, top_k=20, query_embedding=toy_embed(q))

    assert vec.count() == before == 0


# ---------------------------------------------------------------------------
# SENTINEL 4 — offline, and no new outbound calls
# ---------------------------------------------------------------------------

def test_retrieval_never_embeds_anything_itself(tmp_path, monkeypatch):
    """The query vector is an ARGUMENT. `assemble_context` is synchronous and
    is called from inside a running event loop, so an embed call there would
    have to be sync-in-async; making the caller pass the vector is what keeps
    the one provider call per turn in the async layer where it belongs."""
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
    store = _corpus(tmp_path)
    idx = _index_everything(store)

    import backend.kb.retriever as retr
    tree = ast.parse(Path(retr.__file__).read_text(encoding="utf-8"))
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "get_embedding" not in called, "retrieval acquired a provider call"

    # And with no query vector supplied, the semantic channel simply does not run.
    hits = TripleRetriever(store, semantic=idx).retrieve(
        "what do we know about cardiac safety?", top_k=20)
    assert not any(h.triple.source == HUMAN for h in hits)


# ---------------------------------------------------------------------------
# the index itself
# ---------------------------------------------------------------------------

def test_archived_beliefs_are_not_semantically_retrievable(tmp_path, monkeypatch):
    """F.2's rule holds on the new channel too: a superseded belief keeps every
    record field and stays resolvable by id, and must not compete for slots."""
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
    store = _corpus(tmp_path)
    idx = _index_everything(store)
    claim = [t for t in store.all() if t.source == HUMAN][0]
    store.archive_ids([claim.id], reason="test")

    q = "what do we know about cardiac safety?"
    hits = TripleRetriever(store, semantic=idx).retrieve(
        q, top_k=20, query_embedding=toy_embed(q))
    assert not any(h.triple.id == claim.id for h in hits)


def test_the_index_writes_into_the_stores_own_database(tmp_path):
    """Write topology: no new file, no tenth path to guard in conftest. The
    vectors live in `kb.db` beside the beliefs they describe."""
    store = _corpus(tmp_path)
    idx = _index_everything(store)
    assert idx.coverage()["indexed"] == len(store.all())
    assert not list(tmp_path.glob("*.vec"))
    tables = set(store.conn.tables())
    assert "triple_embeddings" in tables


def test_the_table_is_not_created_until_something_is_indexed(tmp_path):
    """With the feature off, the operator's kb.db is not touched at all —
    not even by an empty table appearing in it."""
    store = TripleStore(tmp_path / "kb.db")
    idx = SemanticIndex(store)
    cov = idx.coverage()
    assert (cov["indexed"], cov["models"], cov["dim"]) == (0, [], DIM)
    tables = set(store.conn.tables())
    assert "triple_embeddings" not in tables
    assert idx.search(toy_embed("anything"), k=5) == []


def test_a_wrong_dimension_vector_is_refused(tmp_path):
    store = _corpus(tmp_path)
    idx = SemanticIndex(store)
    t = store.all()[0]
    with pytest.raises(ValueError):
        idx.upsert(t.id, surface_of(t), [0.1, 0.2, 0.3], model="toy")


def test_reindexing_replaces_rather_than_accumulates(tmp_path):
    store = _corpus(tmp_path)
    idx = _index_everything(store)
    n = idx.coverage()["indexed"]
    _index_everything(store)
    assert idx.coverage()["indexed"] == n


def test_unindexed_reports_what_is_missing(tmp_path):
    store = _corpus(tmp_path)
    idx = SemanticIndex(store)
    assert len(idx.unindexed(limit=100)) == len(store.all())
    t = store.all()[0]
    idx.upsert(t.id, surface_of(t), toy_embed(surface_of(t)), model="toy")
    remaining = {tid for tid, _ in idx.unindexed(limit=100)}
    assert t.id not in remaining
    assert len(remaining) == len(store.all()) - 1


# ---------------------------------------------------------------------------
# W.2 — the three defects the operator's harness run exposed
# ---------------------------------------------------------------------------

def test_the_toy_embedder_matches_the_measured_geometry():
    """DEFECT A's root cause. A threshold can only be tested by an embedder
    that lives in the same numeric range as the one that meets it in
    production. W.1's toy scored unrelated pairs at exactly 0.0 and the
    acceptance pair at 0.1091, so every test had to lower the threshold to
    0.05 and the shipped 0.55 went unexercised.

    Fitted to the operator's own measurements with nomic-embed-text,
    un-prefixed. If either number drifts, the tests below stop meaning what
    they say and this fails first.
    """
    from backend.vector.matryoshka import cosine

    from tests._toy_embed import _MEASURED_CONTROL, _MEASURED_SIGNAL
    q = "what do we know about cardiac safety?"
    claim = ("mean placebo-corrected QTc change at 600mg supratherapeutic "
             "dose was 11.4ms")
    control = "the deployment pipeline rebuilt the container image last tuesday"
    signal = cosine(toy_embed(q), toy_embed(claim))
    floor = cosine(toy_embed(q), toy_embed(control))
    assert abs(signal - _MEASURED_SIGNAL) < 0.01, f"signal drifted to {signal}"
    assert abs(floor - _MEASURED_CONTROL) < 0.01, f"control drifted to {floor}"
    assert floor < DEFAULT_MIN_SIMILARITY < signal, (
        f"the shipped threshold {DEFAULT_MIN_SIMILARITY} does not separate "
        f"control {floor:.4f} from signal {signal:.4f}")


def test_no_test_in_this_file_overrides_the_shipped_threshold():
    """DEFECT A, as a ratchet. Every semantic test in the suite used to set
    its own `NEXUS_SEMANTIC_MIN_SIMILARITY`, which is why a default five times
    too high survived. A test that needs to move the threshold is a test that
    is no longer measuring what ships."""
    src = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("setenv", "delenv")):
            continue
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and \
                first.value == "NEXUS_SEMANTIC_MIN_SIMILARITY":
            offenders.append(node.lineno)
    assert not offenders, (
        f"threshold overridden at line(s) {offenders} — run at the shipped "
        f"default or change the default")


def test_the_claim_must_win_a_slot_against_a_realistic_store(
        tmp_path, monkeypatch):
    """DEFECT B. Four rows and five slots meant every row was admitted and
    nothing had to rank. With the competition a real store carries, the claim
    only arrives if it beats it."""
    from tests._realistic_corpus import add_competition
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
    store = _corpus(tmp_path)
    add_competition(store)
    assert len(store.all()) > 300, "the corpus is not large enough to bind"
    idx = _index_everything(store)
    q = "what do we know about cardiac safety?"
    hits = TripleRetriever(store, semantic=idx).retrieve(
        q, top_k=20, query_embedding=toy_embed(q))
    assert any(h.triple.source == HUMAN for h in hits), (
        "the claim did not win a semantic slot on a realistic corpus")


def test_without_the_exclusion_the_claim_loses_its_slot(tmp_path, monkeypatch):
    """NON-VACUITY for the test above, and the measurement that decided W.2.

    Substrate-minted `co_activated` surfaces are three tokens assembled from
    the query's own vocabulary, so they outscore a specific eleven-token
    claim. Measured on a 651-row store: the claim ranked 109th and all 108
    rows above it were co-activations. With them excluded it ranks 1st.

    If this ever starts passing, the exclusion has stopped being load-bearing
    and the test above is passing for some other reason.
    """
    from tests._realistic_corpus import add_competition
    monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
    monkeypatch.setenv("NEXUS_SEMANTIC_INDEX_EXCLUDE", "")   # index everything
    store = _corpus(tmp_path)
    add_competition(store)
    idx = _index_everything(store)
    q = "what do we know about cardiac safety?"
    hits = TripleRetriever(store, semantic=idx).retrieve(
        q, top_k=20, query_embedding=toy_embed(q))
    assert not any(h.triple.source == HUMAN for h in hits), (
        "the claim won a slot even with co-activations competing — re-derive "
        "the exclusion, it may no longer be needed")


def test_the_slot_quota_binds_before_the_threshold_does(tmp_path, monkeypatch):
    """DEFECT B's real lesson, stated as a property.

    The admission loop walks candidates in DESCENDING similarity and stops at
    `added >= slots`, so lowering the threshold extends the tail and never
    reorders the head. A claim ranked below the quota is unreachable at EVERY
    threshold — which is exactly the invariant 0.30-0.70 result the operator's
    harness produced, and why a threshold sweep could not have diagnosed it.
    """
    from tests._realistic_corpus import add_competition
    monkeypatch.setenv("NEXUS_SEMANTIC_INDEX_EXCLUDE", "")
    store = _corpus(tmp_path)
    add_competition(store)
    idx = _index_everything(store)
    q = "what do we know about cardiac safety?"
    qv = toy_embed(q)
    claim = [t for t in store.all() if t.source == HUMAN][0]

    for threshold in (0.0, 0.1, 0.2, 0.3, 0.4):
        monkeypatch.setenv("NEXUS_SEMANTIC_SLOTS", "5")
        hits = idx.search(qv, k=5, min_similarity=threshold)
        assert claim.id not in {i for i, _ in hits}, (
            f"admitted at threshold {threshold} — the quota no longer binds")
    # ...and raising the QUOTA is what actually reaches it.
    deep = idx.search(qv, k=400, min_similarity=0.0)
    ranked = [i for i, _ in deep]
    assert claim.id in ranked
    assert ranked.index(claim.id) > 5, (
        "the claim is inside the default quota, so this test proves nothing")


def test_the_exclusion_is_reversible_and_declared(tmp_path, monkeypatch):
    """T.2/V.1's rule: a set that governs behaviour must be reportable and
    must have an off switch, or an operator cannot tell what is in force."""
    from backend.kb.semantic_index import excluded_relations
    monkeypatch.delenv("NEXUS_SEMANTIC_INDEX_EXCLUDE", raising=False)
    assert "co_activated" in excluded_relations()
    monkeypatch.setenv("NEXUS_SEMANTIC_INDEX_EXCLUDE", "")
    assert excluded_relations() == ()
    monkeypatch.setenv("NEXUS_SEMANTIC_INDEX_EXCLUDE", "foo, bar")
    assert excluded_relations() == ("foo", "bar")

    monkeypatch.delenv("NEXUS_SEMANTIC_INDEX_EXCLUDE", raising=False)
    store = _corpus(tmp_path)
    idx = _index_everything(store)
    assert idx.coverage()["excluded_relations"] == list(excluded_relations())


def test_excluded_relations_are_never_indexed(tmp_path, monkeypatch):
    from tests._realistic_corpus import add_competition
    monkeypatch.delenv("NEXUS_SEMANTIC_INDEX_EXCLUDE", raising=False)
    store = _corpus(tmp_path)
    add_competition(store)
    idx = SemanticIndex(store)
    pending = idx.unindexed(limit=10_000)
    assert pending, "nothing pending — the corpus is wrong"
    assert not any(" co_activated " in s for _i, s in pending)
    assert idx.indexable_count() == len(pending)
    assert idx.indexable_count() < len(store.all())


# ---------------------------------------------------------------------------
# the degradation flag W.0 found unreachable
# ---------------------------------------------------------------------------

def test_a_failing_provider_now_flips_embedding_degraded():
    """W.0.2 — `/healthz.embedding_degraded` could never become true: the only
    writer was `embed_text()`, which has no callers, and `chroma.py` swallowed
    the exception and fell back without marking anything. A degradation surface
    that cannot fire is worse than none (I-6)."""
    import asyncio

    from backend.vector import embeddings as emb

    class _Down:
        async def get_embedding(self, text):
            raise ConnectionRefusedError("no daemon")

    emb._mark_healthy()
    assert emb.embedding_status()["degraded"] is False
    vec = asyncio.run(emb.embed_via_provider(_Down(), "anything"))
    assert len(vec) == emb.FULL_DIM, "the fallback vector must still be usable"
    status = emb.embedding_status()
    assert status["degraded"] is True
    assert "ConnectionRefused" in status["reason"]
    emb._mark_healthy()


def test_a_working_provider_clears_it():
    import asyncio

    from backend.vector import embeddings as emb
    from tests._toy_embed import toy_embed as _te

    class _Up:
        async def get_embedding(self, text):
            return _te(text)

    emb._mark_degraded("pretend")
    assert emb.embedding_status()["degraded"] is True
    asyncio.run(emb.embed_via_provider(_Up(), "anything"))
    assert emb.embedding_status()["degraded"] is False


def test_chroma_marks_degradation_rather_than_swallowing_it(tmp_path):
    """The site W.0 found: `chroma.py` caught the exception and called
    `_offline_embed` directly, so the flag stayed clean while every embedding
    in the system was a hash."""
    import asyncio

    from backend.vector import embeddings as emb
    from backend.vector.chroma import VectorStore

    class _Down:
        async def get_embedding(self, text):
            raise ConnectionRefusedError("no daemon")

    emb._mark_healthy()
    vec = VectorStore(tmp_path / "chroma", provider=_Down())
    asyncio.run(vec.add("c1", "some text"))
    assert emb.embedding_status()["degraded"] is True
    emb._mark_healthy()
