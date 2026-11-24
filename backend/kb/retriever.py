"""Tiered context-time retrieval. Phase 2: keyword extraction + weight ranking.

Phase 7 swaps the keyword search for vector + rerank, but the ranking surface
(`current_weight()` first, tier as tie-breaker) stays identical.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from backend.kb.tiered_store import (
    Triple, TripleStore, effective_prominence)

# P1/F-06 — retrieval diagnostics (D8: capability retained) run at DEBUG so
# the hot path is quiet by default; enable with
# logging.getLogger("nexus.retrieval").setLevel(logging.DEBUG).
logger = logging.getLogger("nexus.retrieval")

_WORD_RE = re.compile(r"[a-z][a-z0-9_]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
    "of", "to", "in", "on", "at", "for", "with", "from", "by", "as",
    "what", "why", "how", "when", "where", "who", "do", "does", "did",
    "i", "you", "we", "they", "it", "this", "that", "these", "those",
    "be", "been", "have", "has", "had", "can", "could", "would", "should",
}


# The precision the record carries: `weight_at_injection` is `round(score, 6)`.
# A prominence difference the record cannot show is not an ordering signal.
RANK_PRECISION = 6


def ranking_prominence(triple: Triple, now: float, adjustment: float = 0.0) -> float:
    """The ORDER key's value, distinct from `rt.score` (the evidence value
    the record carries unrounded). Effective prominence rounded to the
    record's own precision, so two rows the record could not tell apart tie
    exactly and the order among them falls to the id tie-break — never to
    the microseconds of age that a per-day decay turned into noise in the
    last bits. A difference the record CAN show (a row fired an hour
    earlier) still orders. Rounding, not flooring the clock: a floor on the
    timestamps put a wall-clock second boundary between rows added
    microseconds apart (found on the rig, 1 run in 5)."""
    return round(effective_prominence(triple, now, adjustment), RANK_PRECISION)


def extract_terms(query: str, *, max_terms: int = 8) -> list[str]:
    seen, out = set(), []
    for w in _WORD_RE.findall(query.lower()):
        if w in _STOPWORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= max_terms:
            break
    return out


@dataclass(frozen=True)
class RetrievedTriple:
    triple: Triple
    score: float
    # W.1 — DECLARED, NOT SILENT. Which channel put this belief in the pool:
    # "lexical" (substring match on subject/object), "semantic" (cosine over
    # the triple surface), or "both". An answer's evidence should be able to
    # say WHY each claim was considered relevant; without this, "it was in the
    # context" covers two different things and neither is auditable.
    #
    # Defaults keep every existing construction site and every positional
    # caller working unchanged, which is what makes the 0-slot path identical.
    channel: str = "lexical"
    similarity: float | None = None

    def render(self) -> str:
        t = self.triple
        return render_kb_line(t.subject, t.relation, t.object, self.score, t.granularity)


def render_kb_line(subject: str, relation: str, object: str,
                   weight: float, granularity: str,
                   src: str | None = None, via: str | None = None) -> str:
    """One KB CONTEXT line, as a pure function of what the lineage record
    keeps (`belief_id` -> the row; `weight_at_injection` -> `w=`).

    AW — this is the single implementation both the prompt and the
    derivability check use, so the record can re-derive the line rather
    than a second renderer approximating it (H.4: two implementations of
    one string is how they drift apart).

    BA fix 4 — `src` is the row's evidence class (`classify(source)`, the
    store's own vocabulary), rendered so the model can tell a human
    assertion from its own extracted paraphrase at the point of reading.
    The label is written by the runtime inside the runtime-written block;
    nothing the model authors can set it. `None` (the legacy render) is the
    pre-fix line, byte for byte.

    W5 — `via` is the row's provenance, the source the reference policy
    declares (`wikidata`, `human_assertion`), written after the class when
    the reference gateway is on. `None`, the default and the off position,
    is the line as BA fix 4 left it, byte for byte.
    """
    tail = (f" src={src}" if src else "") + (f" via={via}" if via else "")
    return f"({subject} {relation} {object}) [w={weight:.2f} g={granularity}{tail}]"


class TripleRetriever:
    def __init__(self, store: TripleStore, *, semantic=None, trails=None):
        """`semantic` is an optional `SemanticIndex` (W.1); `trails` an
        optional `TrailStore` (Packet AB).

        Optional rather than required because retrieval must keep working on a
        store whose index has never been built — and because with
        `NEXUS_SEMANTIC_SLOTS=0` / `NEXUS_TRAIL_SLOTS=0`, which are the
        defaults, a channel that IS present contributes nothing.
        """
        self.store = store
        self.semantic = semantic
        self.trails = trails

    def _semantic_candidates(self, candidates: list, lexical_ids: set,
                             query_embedding, top_k: int) -> dict:
        """Append semantically-similar live beliefs to `candidates` in place.

        Returns {triple_id: cosine} for everything the channel touched,
        INCLUDING rows the lexical channel had already found — a belief that
        both channels agree on should say "both", not be silently relabelled.

        The quota is the point. W.0 measured that co-activation rows already
        needed a 20% cap to stop them crowding the injected context; an
        unbounded second channel would do the same thing with weak-but-similar
        rows, and the resulting dilution would look like the retrieval getting
        worse rather than like a missing cap.
        """
        if self.semantic is None or query_embedding is None:
            return {}
        from backend.kb.semantic_index import semantic_slots
        slots = semantic_slots()
        if slots <= 0:
            return {}
        try:
            # Ask for the quota PLUS what the lexical channel already has, so a
            # pool dominated by rows both channels agree on still yields new ones.
            hits = self.semantic.search(query_embedding,
                                        k=slots + len(lexical_ids))
        except Exception:
            logger.debug("[retrieve-diag] semantic channel failed", exc_info=True)
            return {}

        sims: dict = {}
        added = 0
        for triple_id, sim in hits:
            sims[triple_id] = sim
            if triple_id in lexical_ids:
                continue
            if added >= slots:
                continue
            t = self.store.get_by_id(triple_id)
            # F.2 — `search()` already excludes archived rows; this is the
            # belt on the braces, because a candidate list is exactly where an
            # archived belief re-entering would be hardest to notice.
            if t is None or t.archived:
                continue
            candidates.append(t)
            added += 1
        logger.debug("[retrieve-diag] semantic: %d hit(s), %d added (slots=%d)",
                     len(hits), added, slots)
        return sims

    def _trail_candidates(self, candidates: list, existing_ids: set) -> set:
        """Packet AB — the third recall channel: relation trails.

        For each candidate already in the pool whose relation has an ACTIVE
        trail (three-leg conjunction: co-citation recurrence, shared (s,o),
        closure veto — trails.py), admit sibling claims on the SAME SUBJECT
        under the trail-linked relation. Adds candidates and does nothing
        else — same law as the semantic channel: admission, never rank. The
        quota is `NEXUS_TRAIL_SLOTS`, default 0, at which this method is a
        no-op and retrieval is byte-identical to pre-AB behaviour.

        Same-subject, deliberately: a trail says the RELATIONS are one worn
        path, not that every claim under them is relevant; the subject
        anchor keeps the admission about the entity the query already
        reached. Returns the ids it added, for channel labelling (I-6).
        """
        if self.trails is None:
            return set()
        from backend.kb.trails import trail_slots
        slots = trail_slots()
        if slots <= 0:
            return set()
        try:
            active = self.trails.active_map()
        except Exception:
            logger.debug("[retrieve-diag] trail channel failed", exc_info=True)
            return set()
        if not active:
            return set()
        added: set = set()
        for t in list(candidates):
            linked = active.get(t.relation)
            if not linked:
                continue
            for rel_b in sorted(linked):
                if len(added) >= slots:
                    break
                for sib in self.store.search_subject_relation(
                        t.subject, rel_b):
                    if sib.id in existing_ids or sib.id in added:
                        continue
                    if len(added) >= slots:
                        break
                    candidates.append(sib)
                    added.add(sib.id)
        logger.debug("[retrieve-diag] trails: %d added (slots=%d)",
                     len(added), slots)
        return added

    def retrieve(self, query: str, *, top_k: int = 20,
                 query_embedding=None) -> list[RetrievedTriple]:
        """Union recall, unchanged ranking.

        `query_embedding` is passed IN rather than computed here. Embedding is
        a provider call and therefore async; `assemble_context` is synchronous
        and runs inside a live event loop, so computing it here would mean
        sync-in-async. Making it an argument keeps the one call per turn in the
        async layer where it belongs, and keeps this function — and the whole
        test suite — free of outbound traffic (R.4).
        """
        terms = extract_terms(query)
        logger.debug("[retrieve-diag] query=%r extracted_terms=%s", query, terms)
        candidates = self.store.search_terms(terms, limit=top_k * 4) if terms else []
        lexical_ids = {t.id for t in candidates}
        logger.debug("[retrieve-diag] candidates_count=%d (asked for top_k*4=%d)",
                     len(candidates), top_k * 4)

        # ---- the second recall channel (W.1) -------------------------------
        # It ADDS candidates and does nothing else. Everything below this block
        # — the sort, the co-activation cap, the diagnostics — is untouched.
        semantic_sim = self._semantic_candidates(
            candidates, lexical_ids, query_embedding, top_k)

        # ---- the third recall channel (Packet AB) --------------------------
        # Adds candidates and does nothing else, like the block above. At the
        # default NEXUS_TRAIL_SLOTS=0 it adds nothing and this line is free.
        trail_ids = self._trail_candidates(
            candidates, lexical_ids | set(semantic_sim))

        if not candidates:
            logger.debug("[retrieve-diag] no candidates — returning empty")
            return []

        # Source breakdown of candidates
        from collections import Counter
        src_breakdown = Counter(c.source for c in candidates)
        tier_breakdown = Counter(c.tier for c in candidates)
        rel_breakdown = Counter(
            "co_activated" if c.relation == "co_activated" else "grounded"
            for c in candidates
        )
        logger.debug("[retrieve-diag] candidate_sources=%s", dict(src_breakdown))
        logger.debug("[retrieve-diag] candidate_tiers=%s", dict(tier_breakdown))
        logger.debug("[retrieve-diag] candidate_relation_kind=%s", dict(rel_breakdown))

        now = time.time()
        scored = [
            RetrievedTriple(
                triple=t, score=t.current_weight(now),
                channel=("trail" if t.id in trail_ids
                         else "both" if t.id in semantic_sim and t.id in lexical_ids
                         else "semantic" if t.id in semantic_sim
                         else "lexical"),
                similarity=semantic_sim.get(t.id),
            )
            for t in candidates
        ]
        # Tier ascending (0 best), score descending — Tier 0 always wins ties
        # Q.2 — ranked by EFFECTIVE prominence, not by raw current_weight.
        # `rt.score` stays the belief's real prominence because the lineage
        # record carries it as `weight_at_injection` and that number must not
        # acquire a ranking adjustment. Provenance decides the ORDER; the
        # record keeps saying what the belief was actually worth.
        #
        # Both halves are needed: scaling only the band would hold a class out
        # of a tier and then let it win the tie inside the one below.
        # S.2 — and by the recorded recalibrations, the third factor. Loaded
        # once for the candidate set, not per comparison.
        adjustments = self.store.adjustments()
        # SNIP-DET — a TOTAL order. The ranking value is the prominence at the
        # record's own precision, so two rows identical in every stored
        # respect but fired microseconds apart tie EXACTLY instead of ranking
        # by how much decay those microseconds bought; the tie falls to the
        # id. Before this the order of such rows was the creation order
        # reversed on a microsecond clock and tick-dependent on a coarse one
        # (win32), and because the hit order seeds propagation, the Hebbian
        # top-k, the edge count and snip's count all moved with it.
        # `rt.score` is untouched: `weight_at_injection` keeps the real value.
        scored.sort(key=lambda rt: (rt.triple.tier,
                                    -ranking_prominence(
                                        rt.triple, now,
                                        adjustments.get(rt.triple.id, 0.0)),
                                    rt.triple.id if rt.triple.id is not None else -1))

        # Partition: grounded facts vs co-activation meta-edges.
        # Co-activations represent "these terms have appeared together"
        # — useful for hippocampal recall but pollution in LLM context
        # when they dominate the candidate pool. Cap them at 20% of top_k.
        grounded = [rt for rt in scored if rt.triple.relation != "co_activated"]
        coactivated = [rt for rt in scored if rt.triple.relation == "co_activated"]

        coa_cap = max(1, top_k // 5)  # 20% cap, minimum 1 slot
        if len(grounded) < top_k - coa_cap:
            final = grounded + coactivated[:coa_cap]
        else:
            final = grounded[:top_k]

        logger.debug(
            "[retrieve-diag] partition: grounded=%d coa=%d final_grounded=%d final_coa=%d",
            len(grounded), len(coactivated),
            sum(1 for rt in final if rt.triple.relation != "co_activated"),
            sum(1 for rt in final if rt.triple.relation == "co_activated"),
        )
        # Show what actually made it into the top_k
        logger.debug("[retrieve-diag] returning %d of %d candidates", len(final), len(scored))
        final_src = Counter(rt.triple.source for rt in final)
        final_rel = Counter(
            "co_activated" if rt.triple.relation == "co_activated" else "grounded"
            for rt in final
        )
        logger.debug("[retrieve-diag] final_sources=%s", dict(final_src))
        logger.debug("[retrieve-diag] final_relation_kind=%s", dict(final_rel))

        # Show top 5 in detail
        for i, rt in enumerate(final[:5]):
            t = rt.triple
            logger.debug("[retrieve-diag] #%d: tier=%d score=%.3f src=%s (%s %s %s)",
                         i, t.tier, rt.score, t.source, t.subject, t.relation, t.object)

        # Show how many grounded facts were dropped
        dropped = [rt for rt in scored[top_k:] if rt.triple.relation != "co_activated"]
        if dropped:
            logger.debug("[retrieve-diag] DROPPED %d grounded facts that matched "
                         "but ranked below top_%d:", len(dropped), top_k)
            for rt in dropped[:5]:
                t = rt.triple
                logger.debug("[retrieve-diag]   dropped: tier=%d score=%.3f src=%s (%s %s %s)",
                             t.tier, rt.score, t.source, t.subject, t.relation, t.object)

        return final
