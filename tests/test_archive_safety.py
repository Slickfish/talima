"""Packet L.1 — archive-safety sentinels, written before anything is archived.

**Guards only. No behaviour changes ship with this file.**

Packet L proposes to remove gate-rejected material from the *retrieval*
surface while leaving it wholly intact on the *record* surface (I-8). It is
the first work in this project that could violate **I-9** — *a gate rejects at
the boundary and never deletes* — because it points the gate's rules at rows
that are already stored. These sentinels exist so that the change is
constrained by them rather than validated by them (§14, and Packet E's
precedent).

WHAT IS UNMARKED, AND WHY THAT MATTERS
======================================
Most of this file is **unmarked** — it passes today and must pass after. That
is deliberate and is the stronger form: an unmarked sentinel pins behaviour
the packet must not disturb, while an xfail merely records a defect. L.0
measured that F.2's `archived` mechanism already works, so the majority of
this packet's safety story is "do not break what is already right".

THE INVERTED SENTINELS
======================
Two tests here pass *because the system is not what one might assume*, and
their passing IS the finding (§15 practice):

  test_decay_alone_never_removes_a_belief_from_retrieval
      A belief decayed to the absorbing floor — current_weight 0.000, tier 3
      — is still returned by every retrieval path. Ranking is by the STORED
      weight (a monotone record field); `current_weight` is applied only as a
      sort key after candidate selection, and there is no prominence floor
      anywhere. This is the measurement that justifies Packet L: F.3's class
      decay did NOT sink the 573 consolidation rows out of retrieval, so
      archiving them is not redundant work.

      If someone later adds a prominence floor to retrieval, this test fails.
      That is correct — it should be a deliberate act, not a silent one, and
      the tier-4 history (F.2) is why: a belief invisible to retrieval for a
      reason no record names is exactly the failure mode that killed the
      KAIROS daemon.

  test_archiving_does_not_move_a_belief_between_tiers
      `archived` is a record-layer column and deliberately NOT a tier. F.2
      migrated away from tier 4 for cause. This pins that separation.

THE XFAILS, AND THE MEASURED VALUE THAT VIOLATES EACH
=====================================================
  archive_does_not_touch_another_sources_copy
      `archive()` matches on (subject, relation, object) and ignores
      `source`, while the store's uniqueness constraint is
      (subject, relation, object, source).
      MEASURED: two rows, sources wikidata + extraction ->
      `archive(...)` reports **2 rows affected**, expected 1.
      Harmless for F.2's use (a human confirming one dedup pair). NOT
      harmless for a rule-driven pass: the gate refuses the extraction copy
      and permits the wikidata copy, and archiving the first silently takes
      the second — a lenient-class belief removed from retrieval by a
      strict-class rule, with nothing in the record saying so.

  unarchive_does_not_touch_another_sources_copy
      The same defect on the reverse path, which makes selective reversal of
      a bulk archive impossible. MEASURED: 2 rows affected, expected 1.

  archive_records_why_a_row_was_archived
      No `archive_reason` column exists. MEASURED: PRAGMA table_info(triples)
      has no such column. Two archival reasons — "superseded by a duplicate"
      (dedup) and "the gate would refuse this" (Packet L) — currently share
      one boolean, which is an I-7 defect: they are different judgements and
      must not share a name.

  rows_archived_by_a_rule_are_selectable_in_bulk
      Follows from the above. Without a reason column there is no selector,
      so L.0.6's bulk reversal cannot be expressed at all.

  dedup_surface_lookup_excludes_an_archived_row
      `deduplicator._lookup_by_surface` (deduplicator.py:99-104) is raw SQL
      with no `archived = 0`. MEASURED: returns the row after archiving.
      Not a retrieval path — it means an already-archived row can be
      re-surfaced as a dedup candidate, i.e. an operator asked to archive
      something twice. Whether to fix it is L.2's call; the sentinel exists so
      the decision is made rather than forgotten.

NON-VACUITY
===========
Every xfail is paired with an unmarked test proving its setup is real — that
two sources genuinely can hold the same triple, and that the dedup lookup
genuinely finds an unarchived row. Without the pair, an xfail that "fails" for
a setup reason would look like the defect it is meant to measure.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.kb.granularity import Granularity
from backend.kb.retriever import TripleRetriever
from backend.kb.tiered_store import (
    DAY_SECONDS, AmbiguousArchiveTarget, TripleStore,
)
from backend.memory.chain import NexusArchiveChain
from backend.memory.graph_store import cited_pairs
from backend.memory.lineage import LineageStore, verify_sealed_lineage

# The record-layer fields. F.1 drew this line: `tier` and `last_fired` are
# PROMINENCE and are excluded, per Packet L's own wording. Everything here is
# monotone and absolute, and archiving must leave all of it byte-identical.
RECORD_FIELDS = ("id", "subject", "relation", "object", "weight",
                 "granularity", "source", "created_at")


def _seed(store: TripleStore) -> dict[str, int]:
    """A small store with one row per evidence class."""
    return {
        "external": store.add(
            subject="ganymede", relation="orbits", object="jupiter",
            weight=0.9, tier=1, granularity=Granularity.TOPIC,
            source="wikidata"),
        "extraction": store.add(
            subject="photosynthesis", relation="produces", object="glucose",
            weight=0.6, tier=2, granularity=Granularity.TOPIC,
            source="extraction"),
        "consolidation": store.add(
            subject="callisto", relation="co_activated", object="jupiter",
            weight=0.8, tier=1, granularity=Granularity.SESSION,
            source="hippocampus_consolidation"),
    }


def _seed_id(store: TripleStore, subject: str) -> int:
    return store.conn.execute(
        "SELECT id FROM triples WHERE subject = ?", (subject,)).fetchone()["id"]


def _record_snapshot(store: TripleStore) -> dict[int, tuple]:
    rows = store.conn.execute(
        f"SELECT {', '.join(RECORD_FIELDS)} FROM triples ORDER BY id"
    ).fetchall()
    return {r["id"]: tuple(r[f] for f in RECORD_FIELDS) for r in rows}


# ---------------------------------------------------------------------------
# I-9 — the archive never deletes and never alters
# ---------------------------------------------------------------------------

def test_archiving_never_deletes_or_alters_a_belief(tmp_path):
    """I-9's enforcement, and the non-negotiable of this packet.

    UNMARKED on purpose: it must pass today *and* after, so it constrains the
    change rather than describing it. This is the exact shape Packet K used
    for `test_the_gate_never_deletes_an_existing_belief`.
    """
    store = TripleStore(tmp_path / "kb.db")
    ids = _seed(store)
    before = _record_snapshot(store)
    n_before = store.count()

    for s, r, o in (("ganymede", "orbits", "jupiter"),
                    ("photosynthesis", "produces", "glucose"),
                    ("callisto", "co_activated", "jupiter")):
        store.archive(s, r, o)

    after = _record_snapshot(store)
    assert store.count() == n_before, "archiving removed a row from the store"
    assert set(after) == set(ids.values()), "an id disappeared"
    assert after == before, (
        "a RECORD-layer field changed during archival; only prominence "
        "(tier, last_fired) and the `archived` flag may move")


def test_archiving_does_not_move_a_belief_between_tiers(tmp_path):
    """INVERTED SENTINEL — its passing is the finding.

    `archived` is a record-layer column, deliberately not a tier (F.2). If a
    future implementation expresses "archived" as a tier again it reopens the
    tier-4 defect: `KeyError: 4` out of `rebalance()`, which killed the KAIROS
    daemon for the life of the process and left a partial transaction open.
    """
    store = TripleStore(tmp_path / "kb.db")
    ids = _seed(store)
    tiers_before = {t.id: t.tier for t in store.all()}

    store.archive("ganymede", "orbits", "jupiter")

    tiers_after = {t.id: t.tier for t in store.all()}
    assert tiers_after == tiers_before
    assert store.get_by_id(ids["external"]).archived is True
    assert all(t.tier in (0, 1, 2, 3) for t in store.all()), \
        "archival must never produce a tier outside the bands"


def test_rebalance_skips_an_archived_row_and_declares_it(tmp_path):
    """Law 8: a skip must be distinguishable from "nothing to do"."""
    store = TripleStore(tmp_path / "kb.db")
    _seed(store)
    assert store.rebalance()["skipped_reasons"] == {}, "non-vacuity"

    store.archive("ganymede", "orbits", "jupiter")
    assert store.rebalance()["skipped_reasons"] == {"archived": 1}


# ---------------------------------------------------------------------------
# I-4 — a cited belief must remain resolvable, archived or not
# ---------------------------------------------------------------------------

@pytest.fixture
def sealed_app(tmp_path):
    """A belief cited by a SEALED lineage record, then archived.

    Sealed byte-for-byte the way KAIROS seals (kairos.py:388-405): the
    lineage digest rides the fingerprint dict, so it is covered by the block
    hash, and `mark_sealed` backfills afterwards.
    """
    import os
    os.environ["NEXUS_API_TOKEN"] = "l1-token"
    from backend.server import create_app, create_services

    store = TripleStore(tmp_path / "kb.db")
    lineage = LineageStore(tmp_path / "kb.db")
    chain = NexusArchiveChain(tmp_path / "archive.db")
    chain.init()

    bid = store.add(subject="ganymede", relation="orbits", object="jupiter",
                    weight=0.9, tier=1, granularity=Granularity.TOPIC,
                    source="wikidata")
    rec = lineage.record_turn(
        query="what orbits jupiter?",
        injected=[{"belief_id": bid, "weight_at_injection": 0.9,
                   "tier_at_injection": 1, "cited": True}],
        contradictions=[], model_id="deepseek-r1:14b",
        response_text="Ganymede orbits Jupiter.", prompt_prefix="STABLE")

    unsealed = lineage.unsealed()
    hashes = sorted(h for _lid, h in unsealed)
    block = chain.append(
        {"signature": [0.1, 0.2],
         "lineage_seal": {"count": len(hashes),
                          "digest": LineageStore.seal_digest(hashes),
                          "record_hashes": hashes}},
        tmp_path / "snap.json")
    lineage.mark_sealed([lid for lid, _h in unsealed], block.index)
    assert lineage.get(rec.lineage_id).sealed_block is not None, "non-vacuity"

    # The archival happens AFTER the seal — the case the packet is about.
    assert store.archive("ganymede", "orbits", "jupiter") == 1
    store.close(); lineage.close(); chain.close()

    services = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots")
    return create_app(services), rec, bid, {"Authorization": "Bearer l1-token"}


def test_an_archived_belief_cited_in_a_sealed_record_still_resolves(sealed_app):
    """The I-4 guarantee, end to end, through the real endpoint.

    If this ever fails, Packet L stops: archiving would turn a provable claim
    into a dangling one, and an archived-but-present belief is NOT unresolved.
    """
    app, rec, bid, auth = sealed_app
    with TestClient(app) as c:
        r = c.get(f"/lineage/{rec.lineage_id}", headers=auth)
    assert r.status_code == 200
    payload = r.json()
    entry = next(i for i in payload["injected"] if i["belief_id"] == bid)

    assert entry["resolved"] is True, "an archived belief must still resolve"
    assert entry["archived"] is True, \
        "an archived belief resolved as though it were live evidence (H.2)"
    assert entry["subject"] == "ganymede" and entry["object"] == "jupiter"
    assert entry["weight_at_injection"] == 0.9, \
        "the sealed weight must never be recomputed"
    assert payload["verify"]["record_intact"] is True
    assert payload["verify"]["sealed"] is True


def test_the_lineage_surface_names_which_archival_judgement_was_made(tmp_path):
    """L.2a — `archived: true` alone is not enough on an evidence surface. A
    reviewer defending a claim needs to know whether a human deduplicated the
    belief or a rule refused its shape; those carry very different weight."""
    import os
    os.environ["NEXUS_API_TOKEN"] = "l2a-token"
    from backend.server import create_app, create_services

    store = TripleStore(tmp_path / "kb.db")
    lineage = LineageStore(tmp_path / "kb.db")
    bid = store.add(subject="ganymede", relation="orbits", object="jupiter",
                    weight=0.9, tier=1, granularity=Granularity.TOPIC,
                    source="wikidata")
    rec = lineage.record_turn(
        query="q", injected=[{"belief_id": bid, "weight_at_injection": 0.9,
                              "tier_at_injection": 1, "cited": True}],
        contradictions=[], model_id="m", response_text="a", prompt_prefix="P")
    store.archive("ganymede", "orbits", "jupiter",
                  reason="gate:subject:multi_preposition")
    store.close(); lineage.close()

    services = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots")
    with TestClient(create_app(services)) as c:
        entry = c.get(f"/lineage/{rec.lineage_id}",
                      headers={"Authorization": "Bearer l2a-token"}
                      ).json()["injected"][0]

    assert entry["archived"] is True
    assert entry["archive_reason"] == "gate:subject:multi_preposition"
    assert entry["archived_at"] is not None


def test_sealed_lineage_verification_survives_archival(sealed_app):
    """All three tamper layers still read `intact` after archiving."""
    app, rec, _bid, auth = sealed_app
    with TestClient(app) as c:
        r = c.get("/lineage/verify", headers=auth)
    assert r.status_code == 200
    v = r.json()
    assert v["verdict"] == "intact", v
    assert v["layers"]["row"]["failed"] == 0
    assert v["layers"]["seal"]["failed"] == 0
    assert v["layers"]["chain"]["ok"] is True


def test_cited_pairs_still_exempts_an_archived_belief(tmp_path):
    """The durability exemption must outlive archival.

    `cited_pairs()` resolves through `get_by_id`, which does not filter
    `archived` — correct, and pinned here so a well-meaning "add the filter
    everywhere" change cannot silently strip the exemption from every belief
    this packet archives.
    """
    store = TripleStore(tmp_path / "kb.db")
    lineage = LineageStore(tmp_path / "kb.db")
    chain = NexusArchiveChain(tmp_path / "archive.db")
    chain.init()

    bid = store.add(subject="ganymede", relation="orbits", object="jupiter",
                    weight=0.9, tier=1, granularity=Granularity.TOPIC,
                    source="wikidata")
    lineage.record_turn(
        query="q", injected=[{"belief_id": bid, "weight_at_injection": 0.9,
                              "tier_at_injection": 1, "cited": True}],
        contradictions=[], model_id="m", response_text="a", prompt_prefix="P")
    unsealed = lineage.unsealed()
    hashes = sorted(h for _l, h in unsealed)
    block = chain.append(
        {"signature": [0.0], "lineage_seal": {
            "count": len(hashes), "digest": LineageStore.seal_digest(hashes),
            "record_hashes": hashes}}, tmp_path / "snap.json")
    lineage.mark_sealed([l for l, _h in unsealed], block.index)

    before = cited_pairs(lineage, store)
    assert before == {("ganymede", "jupiter")}, "non-vacuity"

    store.archive("ganymede", "orbits", "jupiter")
    assert cited_pairs(lineage, store) == before

    v = verify_sealed_lineage(lineage, chain)
    assert v["verdict"] == "intact", v


# ---------------------------------------------------------------------------
# Every retrieval surface, and every record surface — parametrised
# ---------------------------------------------------------------------------

def _present_search_terms(store, tid):
    return any(t.id == tid for t in store.search_terms(["ganymede"], limit=20))


def _present_get_relevant_concept(store, tid):
    return any(d["id"] == tid
               for d in store.get_relevant(concepts=["ganymede"], top_k=20))


def _present_get_relevant_global(store, tid):
    return any(d["id"] == tid for d in store.get_relevant(concepts=[], top_k=200))


def _present_retriever(store, tid):
    return any(rt.triple.id == tid
               for rt in TripleRetriever(store).retrieve(
                   "tell me about ganymede", top_k=10))


# Every consumer L.0.1 enumerated that reads triples for RETRIEVAL. A row that
# is archived must leave all of them; the row must be shown to be present
# first, or the test proves nothing.
RETRIEVAL_SURFACES = {
    "search_terms":                _present_search_terms,
    "get_relevant(concepts=[...])": _present_get_relevant_concept,
    "get_relevant(concepts=[])":   _present_get_relevant_global,
    "TripleRetriever.retrieve":    _present_retriever,
}


@pytest.mark.parametrize("surface", sorted(RETRIEVAL_SURFACES),
                         ids=sorted(RETRIEVAL_SURFACES))
def test_archived_rows_leave_every_retrieval_surface(tmp_path, surface):
    """Non-vacuity built in: present before, absent after.

    If a new retrieval path is added that does not filter `archived`, add it
    here — a path missing from this map is a path that quietly keeps
    returning material the operator believes they removed.
    """
    probe = RETRIEVAL_SURFACES[surface]
    store = TripleStore(tmp_path / "kb.db")
    ids = _seed(store)
    tid = ids["external"]

    assert probe(store, tid) is True, f"non-vacuity: {surface} never had the row"
    store.archive("ganymede", "orbits", "jupiter")
    assert probe(store, tid) is False, \
        f"{surface} still returns an archived belief — archiving will not " \
        f"achieve the packet's purpose"


def _present_get_by_id(store, tid):
    return store.get_by_id(tid) is not None


def _present_all(store, tid):
    return any(t.id == tid for t in store.all())


def _present_count(store, tid):
    return store.count() == 3


# The RECORD surfaces. An archived belief must stay on every one of them —
# this is the other half of I-8, and the half that makes archival not-deletion.
RECORD_SURFACES = {
    "get_by_id": _present_get_by_id,
    "all()":     _present_all,
    "count()":   _present_count,
}


@pytest.mark.parametrize("surface", sorted(RECORD_SURFACES),
                         ids=sorted(RECORD_SURFACES))
def test_archived_rows_remain_on_every_record_surface(tmp_path, surface):
    probe = RECORD_SURFACES[surface]
    store = TripleStore(tmp_path / "kb.db")
    ids = _seed(store)
    tid = ids["external"]

    assert probe(store, tid) is True, "non-vacuity"
    store.archive("ganymede", "orbits", "jupiter")
    assert probe(store, tid) is True, \
        f"{surface} lost an archived belief — that is deletion, not archival"


# ---------------------------------------------------------------------------
# L.0.5 — what actually motivates this packet
# ---------------------------------------------------------------------------

def test_decay_alone_never_removes_a_belief_from_retrieval(tmp_path):
    """INVERTED SENTINEL — its passing is the finding, and it is the reason
    Packet L is not redundant work.

    A consolidation row aged 400 days sits at the absorbing floor: tier 3,
    current_weight 0.000. It is still returned by every retrieval path,
    because `search_terms` and `get_relevant` order by the STORED weight — a
    monotone record field — and no path applies a prominence floor.

    Tier and decay govern RANK. Only `archived` governs PRESENCE.
    """
    store = TripleStore(tmp_path / "kb.db")
    tid = store.add(subject="quantum", relation="co_activated",
                    object="session", weight=0.9, tier=1,
                    granularity=Granularity.SESSION,
                    source="hippocampus_consolidation")
    old = time.time() - 400 * DAY_SECONDS
    store.conn.execute(
        "UPDATE triples SET last_fired = ?, created_at = ? WHERE id = ?",
        (old, old, tid))
    store.conn.commit()
    store.rebalance()

    t = store.get_by_id(tid)
    assert t.tier == 3, "non-vacuity: the row must be at the floor"
    assert t.current_weight() == 0.0, "non-vacuity: prominence must be zero"

    hits = TripleRetriever(store).retrieve("tell me about quantum", top_k=10)
    assert any(rt.triple.id == tid for rt in hits), (
        "a fully-decayed belief is no longer retrieved — if this fails, a "
        "prominence floor was added to retrieval. That is a deliberate design "
        "change, not a refactor: see the tier-4 history in F.2")
    assert any(x.id == tid for x in store.search_terms(["quantum"], limit=20))


def test_only_archiving_removes_the_fully_decayed_belief(tmp_path):
    """The other half of the pair: archival does what decay does not."""
    store = TripleStore(tmp_path / "kb.db")
    tid = store.add(subject="quantum", relation="co_activated",
                    object="session", weight=0.9, tier=1,
                    granularity=Granularity.SESSION,
                    source="hippocampus_consolidation")
    old = time.time() - 400 * DAY_SECONDS
    store.conn.execute(
        "UPDATE triples SET last_fired = ?, created_at = ? WHERE id = ?",
        (old, old, tid))
    store.conn.commit()
    store.rebalance()
    assert TripleRetriever(store).retrieve("quantum", top_k=10), "non-vacuity"

    store.archive("quantum", "co_activated", "session")
    assert TripleRetriever(store).retrieve("quantum", top_k=10) == []
    assert store.get_by_id(tid) is not None, "still on the record surface"


# ---------------------------------------------------------------------------
# L.0.6 — the reversal path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("surface", sorted(RETRIEVAL_SURFACES),
                         ids=sorted(RETRIEVAL_SURFACES))
def test_unarchive_restores_every_retrieval_surface(tmp_path, surface):
    """F.2 shipped `unarchive()` because an archival judgement a human made is
    one a human should be able to withdraw. A rule-driven pass over hundreds
    of rows raises the stakes: reversal is the only thing standing between a
    false positive and permanent invisibility."""
    probe = RETRIEVAL_SURFACES[surface]
    store = TripleStore(tmp_path / "kb.db")
    ids = _seed(store)
    tid = ids["external"]

    store.archive("ganymede", "orbits", "jupiter")
    assert probe(store, tid) is False, "non-vacuity"
    assert store.unarchive("ganymede", "orbits", "jupiter") == 1
    assert probe(store, tid) is True, f"{surface} did not come back"


def test_two_sources_can_hold_the_same_triple(tmp_path):
    """NON-VACUITY for the two source-blindness xfails below.

    The uniqueness constraint is (subject, relation, object, source), so the
    same claim from two sources is two rows with two evidence classes and two
    decay rates. That is by design — it is how a model's self-extraction is
    kept distinguishable from an external referent.
    """
    store = TripleStore(tmp_path / "kb.db")
    a = store.add(subject="mercury", relation="is_a", object="planet",
                  weight=0.9, tier=1, granularity=Granularity.TOPIC,
                  source="wikidata")
    b = store.add(subject="mercury", relation="is_a", object="planet",
                  weight=0.5, tier=2, granularity=Granularity.TOPIC,
                  source="extraction")
    assert a != b and store.count() == 2
    assert {t.source for t in store.all()} == {"wikidata", "extraction"}


def _two_source_store(tmp_path):
    store = TripleStore(tmp_path / "kb.db")
    keep = store.add(subject="mercury", relation="is_a", object="planet",
                     weight=0.9, tier=1, granularity=Granularity.TOPIC,
                     source="wikidata")
    drop = store.add(subject="mercury", relation="is_a", object="planet",
                     weight=0.5, tier=2, granularity=Granularity.TOPIC,
                     source="extraction")
    return store, keep, drop


# FLIPPED BY L.2a — both were xfail(strict), measuring `2 rows affected where
# 1 was intended` on archive() and again on unarchive().
#
# RESTATED, and the restatement is the honest part. As L.1 wrote them, these
# called `archive("mercury", "is_a", "planet")` bare and asserted exactly one
# row moved — which is not a satisfiable requirement, because a bare (s, r, o)
# carries nothing that could name which of the two copies was judged. The
# property the names assert is right; the L.1 bodies asked the store to guess.
# So: naming a source archives only that source (below), and a bare call that
# would have to guess refuses instead (test_archive_refuses_an_ambiguous_target).
# Same correction shape as K.3's — move the case rather than force the evidence
# to fit it.

def test_archive_does_not_touch_another_sources_copy(tmp_path):
    store, keep, drop = _two_source_store(tmp_path)

    n = store.archive("mercury", "is_a", "planet", source="extraction",
                      reason="gate:pronoun")

    assert n == 1, "archived more rows than the one belief that was judged"
    assert store.get_by_id(drop).archived is True
    assert store.get_by_id(keep).archived is False, \
        "a lenient-class belief was archived by a strict-class judgement"


def test_unarchive_does_not_touch_another_sources_copy(tmp_path):
    store, a, b = _two_source_store(tmp_path)
    store.archive_ids([a, b], reason="bulk")
    assert all(t.archived for t in store.all()), "non-vacuity"

    n = store.unarchive("mercury", "is_a", "planet", source="extraction")

    assert n == 1
    assert store.get_by_id(a).archived is True
    assert store.get_by_id(b).archived is False


def test_archive_refuses_an_ambiguous_target(tmp_path):
    """Ambiguity is refused, never resolved by guessing.

    F.2 took this stance on an unreadable tier and K took it on an
    unparseable term. Archiving both copies, or silently picking one, would be
    a judgement nobody made.
    """
    store, keep, drop = _two_source_store(tmp_path)

    with pytest.raises(AmbiguousArchiveTarget) as e:
        store.archive("mercury", "is_a", "planet")
    assert sorted(e.value.sources) == ["extraction", "wikidata"]
    assert [t.archived for t in store.all()] == [False, False], \
        "a refused call must leave the store untouched"

    with pytest.raises(AmbiguousArchiveTarget):
        store.unarchive("mercury", "is_a", "planet")


def test_a_bare_archive_still_works_when_the_claim_exists_once(tmp_path):
    """The F.2 caller — a human confirming one dedup pair — is unaffected."""
    store = TripleStore(tmp_path / "kb.db")
    _seed(store)
    assert store.archive("ganymede", "orbits", "jupiter") == 1
    assert store.get_by_id(_seed_id(store, "ganymede")).archived is True


def test_archive_ids_touches_exactly_the_rows_named(tmp_path):
    """The primitive a bulk pass needs: select rows by a query you can show,
    then archive those ids and nothing else. No predicate is re-evaluated
    between selection and write, so what was reviewed is what is archived."""
    store = TripleStore(tmp_path / "kb.db")
    ids = _seed(store)
    targets = [ids["extraction"], ids["consolidation"]]

    assert store.archive_ids(targets, reason="gate:multi_preposition") == 2

    for t in store.all():
        assert t.archived is (t.id in targets), f"id {t.id} moved unexpectedly"
    assert store.archive_ids([], reason="x") == 0, "empty selection is a no-op"


# ---------------------------------------------------------------------------
# L.0.4 — the mechanism: `archived` is right, the bare boolean is not
# ---------------------------------------------------------------------------

# FLIPPED BY L.2a — was xfail(strict): PRAGMA table_info(triples) had no
# `archive_reason`, and archive() had no `reason` kwarg (TypeError).
def test_archive_records_why_a_row_was_archived(tmp_path):
    store = TripleStore(tmp_path / "kb.db")
    _seed(store)

    cols = {c.name for c in store.conn.columns("triples")}
    assert "archive_reason" in cols

    store.archive("ganymede", "orbits", "jupiter",
                  reason="gate:subject:multi_preposition")
    row = store.conn.execute(
        "SELECT archive_reason FROM triples WHERE subject = 'ganymede'"
    ).fetchone()
    assert row["archive_reason"] == "gate:subject:multi_preposition"


# FLIPPED BY L.2a — was xfail(strict), and it is the selector L.0.6's bulk
# reversal needs: without it, this packet's archives and dedup's are
# indistinguishable forever.
def test_rows_archived_by_a_rule_are_selectable_in_bulk(tmp_path):
    store = TripleStore(tmp_path / "kb.db")
    _seed(store)
    store.archive("ganymede", "orbits", "jupiter", reason="gate:pronoun")
    store.archive("photosynthesis", "produces", "glucose", reason="dedup")

    rows = store.conn.execute(
        "SELECT id FROM triples WHERE archived = 1 "
        "AND LOWER(archive_reason) LIKE 'gate:%'").fetchall()
    assert len(rows) == 1, \
        "a rule-driven archive must be distinguishable from a dedup archive"


def test_unarchiving_clears_the_judgement_it_reverses(tmp_path):
    """A restored row is not archived, so a retained reason would state
    something false about its current status. Nothing here keeps a per-change
    history (TEMPORAL_PROBE.md measured that), and inventing one for a single
    column would be a claim the rest of the store cannot support."""
    store = TripleStore(tmp_path / "kb.db")
    ids = _seed(store)
    store.archive("ganymede", "orbits", "jupiter", reason="gate:pronoun")
    t = store.get_by_id(ids["external"])
    assert t.archive_reason == "gate:pronoun" and t.archived_at is not None

    store.unarchive("ganymede", "orbits", "jupiter")
    t = store.get_by_id(ids["external"])
    assert (t.archived, t.archive_reason, t.archived_at) == (False, None, None)


def test_a_row_archived_before_reasons_existed_keeps_a_null_reason(tmp_path):
    """The migration does not backfill. A row archived before L.2a was
    archived for a reason nobody recorded, and writing "dedup" over it would
    fabricate a record in the layer whose only job is faithfulness. NULL is
    the true value and reads as "pre-L.2a"."""
    store = TripleStore(tmp_path / "kb.db")
    ids = _seed(store)
    store.conn.execute("UPDATE triples SET archived = 1 WHERE id = ?",
                       (ids["external"],))          # the pre-L.2a shape
    store.conn.commit()
    store.close()

    reopened = TripleStore(tmp_path / "kb.db")       # re-runs migrate()
    t = reopened.get_by_id(ids["external"])
    assert t.archived is True
    assert t.archive_reason is None and t.archived_at is None


# ---------------------------------------------------------------------------
# L.0.1 — the one consumer that does not filter, and is not retrieval
# ---------------------------------------------------------------------------

def _dedup_lookup(store, surface):
    """Call the deduplicator's own lookup, not a copy of its SQL.

    Behavioural, not grep: H.4's lesson was that searching for an emit site
    draws the boundary wrong. The same applies here — a test that asserts on
    the shape of a query passes the day someone rewrites the query without
    changing what it returns.
    """
    from backend.kb.deduplicator import SemanticDeduplicator
    return SemanticDeduplicator(None, store)._lookup_by_surface(surface)


def test_dedup_surface_lookup_finds_an_unarchived_row(tmp_path):
    """NON-VACUITY for the xfail below."""
    store = TripleStore(tmp_path / "kb.db")
    _seed(store)
    assert _dedup_lookup(store, "ganymede orbits jupiter") is not None


# FLIPPED BY L.2a — was xfail(strict): the lookup returned the archived row,
# so the operator could be asked to archive something they had already
# archived, and confirming it would write a second reason over the first.
def test_dedup_surface_lookup_excludes_an_archived_row(tmp_path):
    store = TripleStore(tmp_path / "kb.db")
    _seed(store)
    store.archive("ganymede", "orbits", "jupiter")
    assert _dedup_lookup(store, "ganymede orbits jupiter") is None, \
        "an archived row can still be surfaced as a dedup candidate"


# ---------------------------------------------------------------------------
# L.0.10 item 3 — declared loss (I-6). Cleared for L.2a by the operator.
# ---------------------------------------------------------------------------

def test_kb_stats_declares_what_is_retrievable_not_only_what_is_recorded(tmp_path):
    """`total` is the record count and must not move when a belief is
    archived — nothing was deleted. But `total` ALONE is misleading: archive
    419 rows and every counter in the UI holds steady, which is silent loss on
    the surface whose job is declaring it. Both numbers, or the surface lies
    by omission."""
    import os
    os.environ["NEXUS_API_TOKEN"] = "l2a-token"
    from backend.server import create_app, create_services

    store = TripleStore(tmp_path / "kb.db")
    _seed(store)
    store.archive("ganymede", "orbits", "jupiter", reason="gate:pronoun")
    store.close()

    services = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots")
    with TestClient(create_app(services)) as c:
        stats = c.get("/kb/stats").json()

    assert stats["total"] == 3, "the record count must not shrink on archival"
    assert stats["retrievable"] == 2
    assert stats["archived"] == 1
    assert stats["total"] == stats["retrievable"] + stats["archived"]


# ---------------------------------------------------------------------------
# The only production caller of archive() — /kb/dedup/confirm
# ---------------------------------------------------------------------------

def _dedup_app(tmp_path, *, two_sources: bool):
    import os
    os.environ["NEXUS_API_TOKEN"] = "l2a-token"
    from backend.server import create_app, create_services

    store = TripleStore(tmp_path / "kb.db")
    store.add(subject="kept", relation="is_a", object="thing", weight=0.9,
              tier=2, granularity=Granularity.TOPIC, source="wikidata")
    store.add(subject="loser", relation="is_a", object="thing", weight=0.1,
              tier=2, granularity=Granularity.TOPIC, source="extraction")
    if two_sources:
        # The same claim held by a second evidence class — two rows, because
        # the uniqueness constraint includes `source`.
        store.add(subject="loser", relation="is_a", object="thing", weight=0.8,
                  tier=1, granularity=Granularity.TOPIC, source="wikidata")
    store.close()

    services = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "ch",
        eval_log_path=tmp_path / "m.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snaps")
    return create_app(services), services, {"Authorization": "Bearer l2a-token"}


_CONFIRM = {"subject_a": "kept", "relation_a": "is_a", "object_a": "thing",
            "subject_b": "loser", "relation_b": "is_a", "object_b": "thing",
            "keep": "a", "confirmed": True}


def test_dedup_confirm_stamps_its_reason(tmp_path):
    """The endpoint's archives must be distinguishable from a rule-driven
    pass's, or L.0.6's reversal cannot tell them apart."""
    app, services, auth = _dedup_app(tmp_path, two_sources=False)
    with TestClient(app) as c:
        r = c.post("/kb/dedup/confirm", json=_CONFIRM, headers=auth)
    assert r.status_code == 200 and r.json()["rows_archived"] == 1

    loser = next(t for t in services.store.all() if t.subject == "loser")
    assert loser.archived is True
    assert loser.archive_reason == "dedup"
    assert loser.archived_at is not None


def test_dedup_confirm_refuses_an_ambiguous_pair(tmp_path):
    """Before L.2a this archived BOTH `loser` rows — including the wikidata
    one, which no human had judged. It now refuses and says why."""
    app, services, auth = _dedup_app(tmp_path, two_sources=True)
    with TestClient(app) as c:
        r = c.post("/kb/dedup/confirm", json=_CONFIRM, headers=auth)

    assert r.status_code == 409
    body = r.json()
    assert sorted(body["sources"]) == ["extraction", "wikidata"]
    assert not any(t.archived for t in services.store.all()), \
        "a refused confirmation must leave the store untouched"


def test_dedup_confirm_with_a_source_archives_only_that_side(tmp_path):
    app, services, auth = _dedup_app(tmp_path, two_sources=True)
    with TestClient(app) as c:
        r = c.post("/kb/dedup/confirm",
                   json={**_CONFIRM, "source_b": "extraction"}, headers=auth)

    assert r.status_code == 200 and r.json()["rows_archived"] == 1
    archived = [t for t in services.store.all() if t.archived]
    assert [(t.subject, t.source) for t in archived] == [("loser", "extraction")]
