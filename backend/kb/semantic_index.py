"""Packet W.1 — a second way for a belief to become a CANDIDATE.

THE FAILURE THIS ANSWERS
    Stored:  mean placebo-corrected QTc change at 600mg supratherapeutic dose
             | was | 11.4ms
    Asked:   "what do we know about cardiac safety?"

    `search_terms` is `LOWER(subject) LIKE ? OR LOWER(object) LIKE ?`. The word
    "cardiac" appears nowhere in the operator's store, so the claim is not
    ranked badly — it never enters the candidate pool at all. Four of five
    plausible phrasings retrieve it; the one a person would most naturally ask
    does not.

RECALL, NOT RANKING — AND WHY THAT IS THE WHOLE DESIGN
    The obvious shape is to score lexical and semantic matches and combine
    them. Three ways to combine, all wrong here:

      - a weighted sum makes the weights arbitrary;
      - reciprocal-rank fusion is scale-free but opaque, and the lexical side
        has no match score to fuse — `search_terms` returns a SET, ordered by
        tier and stored weight, so RRF would be fusing similarity rank with
        prominence rank and calling the result relevance;
      - retrieve-then-rerank costs a pass and still puts similarity in the
        ordering.

    All three answer a ranking question. The measured failure is a RECALL
    failure. So this channel produces candidates and stops:

        candidates = lexical(query) UNION semantic(query_vector)
        order      = unchanged — (tier, -effective_prominence)

    Similarity contributes ZERO to the order. That is not a coincidence to be
    preserved by discipline; it is why Packet Q's inversion cannot return
    through this door. `effective_prominence` carries decay, arrival scale and
    recorded recalibration — provenance. If similarity were a ranking term in
    any amount, a model's own paraphrase of the question would outrank a
    human's assertion, which is precisely what Q was written to fix, and this
    time it would look like an improvement.
    `tests/test_semantic_recall.py` pins it two ways: an AST guard on the sort
    key, and a behavioural one that varies every similarity and asserts the
    order never moves.

WHY THE VECTORS ARE NOT IN CHROMA (W.0.11)
    `backend/vector/chroma.py` exists, works, and has NO production writer —
    measured in W.0 by AST over 67 tracked backend files. Two consumers are
    waiting on it:

      - `assemble_context` would begin injecting chunks into every prompt for
        the first time, and `rag_injected {source: kb}` would fire for the
        first time in a declared SSE event;
      - `SemanticDeduplicator.scan()` would begin returning candidates, and
        its confirm path ARCHIVES BELIEFS.

    Neither has ever run against a populated index. Putting this channel's
    vectors in Chroma would switch both on as a side effect of fixing
    retrieval — one capability's evidence becoming another capability's
    trigger. A feature flag would work and would also be one careless flip
    from the same place, so the separation here is structural instead: these
    vectors live in `kb.db`, in a table nothing else reads, and Chroma stays
    exactly as dark as W.0 found it.

WRITE TOPOLOGY, AND THE ONE RULE THIS DELIBERATELY DOES NOT FOLLOW
    New persistent state goes in `kb.db` — it does, in `triple_embeddings`,
    written through the TripleStore's own guarded connection so there is no
    tenth path for `tests/conftest.py` to guard.

    The rule also says new persistent state is digest-sealed into chain
    blocks. **These vectors are not sealed, deliberately.** They are a derived
    cache: reproducible from `triples` plus a model name, carrying no claim
    and no provenance of their own. Sealing them would make chain verification
    depend on a neural network's floating-point output, so re-embedding the
    same unchanged beliefs with the same model on a different machine could
    fail `verify_chain()`. What IS worth sealing is coverage — how much of the
    store is reachable semantically — and that is a number, not a blob.
    Recorded as a deviation rather than done quietly.

THE THRESHOLD IS UNMEASURED, AND SAYS SO
    W.0.11 found `SIMILARITY_THRESHOLD = 0.92` in the deduplicator, chosen a
    priori and never validated against any embedder — its tests supply
    hardcoded scores through a stub. `DEFAULT_MIN_SIMILARITY` below is in the
    same position and is marked PROVISIONAL for that reason.
    `scripts/measure_retrieval.py` is what turns it into a measured number;
    until it has run on a real store with a real model, it is a guess with a
    comment on it rather than a guess without one.

SCALE
    Search is brute-force cosine over the indexed rows, in numpy. At the
    operator's 867 rows that is a 867x768 matrix product — well under a
    millisecond, and smaller than the SQL query it runs beside. It is O(n) and
    will stop being free somewhere in the high hundreds of thousands of
    beliefs; that is a real ceiling and the right time to reach for an ANN
    index is when a measurement says so, not now.
"""
from __future__ import annotations

import logging
import os
import struct
from typing import Sequence

import numpy as np

from backend.kb.tiered_store import Triple, TripleStore

logger = logging.getLogger("nexus.semantic")

DIM = 768

# Slots default to 0 so installing this changes no behaviour at all — the same
# promise `DEFAULT_ENVELOPE` makes for S.2 and `NEXUS_EXTRACTION_VOCABULARY`
# makes for V.1. Retrieval with 0 slots is byte-identical to pre-W.1
# retrieval, and `tests/test_semantic_recall.py` asserts exactly that.
DEFAULT_SLOTS = 0

# W.2 — MEASURED, no longer chosen. On the operator's store with
# nomic-embed-text:
#
#     bare      signal 0.4047   control 0.2570
#     prefixed  signal 0.5251   control 0.2570
#
# 0.35 sits above the control by 0.093 and below the lower (bare) signal by
# 0.055, so it separates both arms. W.1 shipped 0.55, which is ABOVE both
# measured signals — it would have rejected the operator's own claim outright,
# and no test caught it because every test set its own threshold to 0.0 or
# 0.05. That is the defect this constant's history exists to record.
#
# **STILL WEAK EVIDENCE, AND SAYING SO IS THE POINT.** One signal pair and one
# control pair is n=1; V.2 measured an unchanged prompt moving 0.05 on its own.
# `scripts/measure_retrieval.py --thresholds` over 30+ pairs is what turns this
# into a fitted number rather than a midpoint between two observations.
#
# One number is MISSING and would change this: the control was measured bare in
# both arms (identical to four decimal places), so the PREFIXED control is
# unknown. Prefixes add shared tokens to both sides, which lifts every cosine
# including unrelated ones — if the prefixed control also rose, the prefixed
# arm's separation is smaller than the signal alone suggests.
DEFAULT_MIN_SIMILARITY = 0.35

# W.2 — relations the substrate mints for itself, excluded from the index.
#
# MEASURED, and it is the binding constraint. On a 651-row store shaped like
# the operator's, the claim scored 0.4048 against the failing question and
# ranked **109th** — and every one of the 108 rows above it was a
# `co_activated` row. Those surfaces are three tokens long and assembled from
# the query's own vocabulary ("safety co_activated qtc" scores 0.61), so a
# specific eleven-token claim cannot outscore them. Excluding them moves the
# claim from 109th to **1st of 271**.
#
# This is not a hack to make a test pass; it is the same category argument
# V.2's `propose_relation_vocabulary.py` already makes when it excludes these
# from the vocabulary proposal: **they are associations, not claims.** The
# semantic channel's budget exists to reach claims the lexical channel cannot,
# and the lexical channel already reaches co-activations — which the retriever
# then caps at 20% of the output precisely because they crowd context.
#
# Configuration, not a constant (T.2/V.1 pattern): `NEXUS_SEMANTIC_INDEX_EXCLUDE`
# is a comma-separated relation list, and setting it EMPTY indexes everything,
# reproducing W.1 behaviour exactly.
# AN.2 — the tuple itself now lives in schema.py (the store needs the same
# partition and cannot import this module without a cycle). Same name, same
# value, same env-override semantics here; identity pinned by test.
from backend.kb.schema import SUBSTRATE_RELATIONS as DEFAULT_EXCLUDED_RELATIONS  # noqa: E402,E501

_DDL = """
CREATE TABLE IF NOT EXISTS triple_embeddings (
    triple_id  INTEGER PRIMARY KEY,
    dim        INTEGER NOT NULL,
    model      TEXT    NOT NULL,
    surface    TEXT    NOT NULL,
    vec        BLOB    NOT NULL,
    created_at REAL    NOT NULL
);
"""


def semantic_slots() -> int:
    """How many candidates the semantic channel may ADD. 0 disables it.

    Read at call time so a `.env` loaded after import is honoured (U.2), and
    so a test can move it without reimporting anything.
    """
    raw = os.getenv("NEXUS_SEMANTIC_SLOTS", "").strip()
    if not raw:
        return DEFAULT_SLOTS
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("NEXUS_SEMANTIC_SLOTS=%r is not an integer; using %d",
                       raw, DEFAULT_SLOTS)
        return DEFAULT_SLOTS


def semantic_min_similarity() -> float:
    raw = os.getenv("NEXUS_SEMANTIC_MIN_SIMILARITY", "").strip()
    if not raw:
        return DEFAULT_MIN_SIMILARITY
    try:
        return float(raw)
    except ValueError:
        logger.warning("NEXUS_SEMANTIC_MIN_SIMILARITY=%r is not a number; "
                       "using %.2f", raw, DEFAULT_MIN_SIMILARITY)
        return DEFAULT_MIN_SIMILARITY


def excluded_relations() -> tuple[str, ...]:
    """Relations kept OUT of the semantic index. Empty = index everything.

    `NEXUS_SEMANTIC_INDEX_EXCLUDE` unset -> the compiled default; set to the
    empty string -> nothing excluded, which is W.1's behaviour byte for byte.
    """
    raw = os.getenv("NEXUS_SEMANTIC_INDEX_EXCLUDE")
    if raw is None:
        return DEFAULT_EXCLUDED_RELATIONS
    return tuple(r.strip() for r in raw.split(",") if r.strip())


def surface_of(t: "Triple | dict") -> str:
    """The text that stands for a belief when it is embedded.

    The whole triple as one sentence, which is what W.0.5 recommended: the
    query is a question and the index is a claim, so they need to occupy the
    same space. Subject-and-object-separately would discard the relation, and
    `assertion.py` deliberately keeps salient terms OUT of the relation, so
    there is nothing to gain there. Predicates embedded on their own are what
    OQ-2 will need — a separate collection, a separate decision, not this one.
    """
    if isinstance(t, dict):
        return f"{t['subject']} {t['relation']} {t['object']}"
    return f"{t.subject} {t.relation} {t.object}"


def _pack(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *[float(v) for v in vec])


def _unpack(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4", count=dim)


class SemanticIndex:
    """Triple-surface embeddings, stored beside the beliefs they describe.

    Takes the TripleStore rather than a path — the vectors belong to that
    store's database, so there is no second file to open and nothing new for
    the conftest path guard to catch. (E.3 and S.2 both introduced a writer
    that escaped that guard and wrote into the operator's real `data/kb.db`;
    the cheapest way not to be the third is not to be a writer.)
    """

    def __init__(self, store: TripleStore, *, dim: int = DIM):
        self.store = store
        self.dim = dim
        self._table_ready = False

    # ---------- schema, created lazily ----------

    def _ensure(self) -> None:
        """Create the table on first WRITE, never on construction.

        With the feature off, the operator's `kb.db` is not touched at all —
        not even by an empty table appearing in it, which would change its
        md5 for a capability nobody switched on.
        """
        if self._table_ready:
            return
        self.store.conn.executescript(_DDL)
        self.store.conn.commit()
        self._table_ready = True

    def _has_table(self) -> bool:
        return self.store.conn.has_table("triple_embeddings")

    # ---------- write ----------

    def upsert(self, triple_id: int, surface: str, vector: Sequence[float],
               *, model: str) -> None:
        vec = list(vector)
        if len(vec) != self.dim:
            raise ValueError(
                f"embedding has {len(vec)} dims; this index expects {self.dim}")
        self._ensure()
        import time as _t
        self.store.conn.execute(
            "INSERT INTO triple_embeddings "
            "(triple_id, dim, model, surface, vec, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(triple_id) DO UPDATE SET "
            "dim=excluded.dim, model=excluded.model, surface=excluded.surface, "
            "vec=excluded.vec, created_at=excluded.created_at",
            (int(triple_id), self.dim, model, surface, _pack(vec), _t.time()),
        )
        self.store.conn.commit()

    # ---------- read ----------

    def coverage(self) -> dict:
        """How much of the store is reachable semantically, and by which model.

        The number worth sealing, per the write-topology note in the module
        docstring — and the number an operator needs to tell "retrieval found
        nothing" from "retrieval could not look".
        """
        excluded = list(excluded_relations())
        if not self._has_table():
            return {"indexed": 0, "models": [], "dim": self.dim,
                    "excluded_relations": excluded}
        rows = self.store.conn.execute(
            "SELECT model, COUNT(*) AS n FROM triple_embeddings "
            "GROUP BY model").fetchall()
        return {
            "indexed": sum(int(r["n"]) for r in rows),
            "models": sorted(str(r["model"]) for r in rows),
            "dim": self.dim,
            "excluded_relations": excluded,
        }

    @staticmethod
    def _exclusion_clause(alias: str = "") -> tuple[str, list]:
        rels = excluded_relations()
        if not rels:
            return "", []
        col = f"{alias}.relation" if alias else "relation"
        return (f" AND {col} NOT IN ({','.join('?' * len(rels))})", list(rels))

    def indexable_count(self) -> int:
        """Live beliefs this index is willing to hold — the denominator.

        Not `store.count(archived=False)`: with substrate relations excluded,
        that denominator makes `pending` bottom out at ~2/3 of the store
        forever, which reads as a permanently broken backfill (I-6).
        """
        clause, params = self._exclusion_clause()
        return int(self.store.conn.execute(
            "SELECT COUNT(*) AS n FROM triples WHERE archived = 0" + clause,
            params).fetchone()["n"])

    def unindexed(self, *, limit: int = 100) -> list[tuple[int, str]]:
        """Live, indexable beliefs with no embedding yet, oldest first.

        `archived = 0`: a superseded belief keeps every record field and stays
        resolvable by id, and must not consume indexing budget (F.2).
        Substrate-minted relations are excluded for the reason recorded at
        `DEFAULT_EXCLUDED_RELATIONS` — they are associations, not claims, and
        they were measured crowding the whole slot budget.
        """
        if not self._has_table():
            clause, params = self._exclusion_clause()
            rows = self.store.conn.execute(
                "SELECT id, subject, relation, object FROM triples "
                "WHERE archived = 0" + clause + " ORDER BY id LIMIT ?",
                (*params, limit)).fetchall()
        else:
            clause, params = self._exclusion_clause("t")
            rows = self.store.conn.execute(
                "SELECT t.id, t.subject, t.relation, t.object FROM triples t "
                "LEFT JOIN triple_embeddings e ON e.triple_id = t.id "
                "WHERE t.archived = 0 AND e.triple_id IS NULL" + clause +
                " ORDER BY t.id LIMIT ?", (*params, limit)).fetchall()
        return [(int(r["id"]),
                 f"{r['subject']} {r['relation']} {r['object']}") for r in rows]

    def surface_for(self, triple_id: int) -> str:
        """The text this belief was embedded AS. For diagnostics — knowing a
        claim ranked 109th is only useful next to what was embedded."""
        if not self._has_table():
            return ""
        row = self.store.conn.execute(
            "SELECT surface FROM triple_embeddings WHERE triple_id = ?",
            (int(triple_id),)).fetchone()
        return str(row["surface"]) if row else ""

    def search(self, query_vector: Sequence[float], *, k: int,
               min_similarity: float | None = None) -> list[tuple[int, float]]:
        """The `k` most similar LIVE beliefs, as (triple_id, cosine).

        Returns [] rather than raising when the index is empty or absent — a
        retrieval path must never fail because a cache has not been built.
        """
        if k <= 0 or not self._has_table():
            return []
        q = np.asarray(list(query_vector), dtype="f4")
        if q.shape[0] != self.dim:
            logger.warning("query vector has %d dims; index expects %d — "
                           "semantic channel skipped", q.shape[0], self.dim)
            return []
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []

        # The exclusion is applied on READ as well as on write, because an
        # index built before W.2 already holds substrate rows and re-indexing
        # is the operator's call, not a side effect of upgrading.
        clause, params = self._exclusion_clause("t")
        rows = self.store.conn.execute(
            "SELECT e.triple_id AS id, e.vec AS vec FROM triple_embeddings e "
            "JOIN triples t ON t.id = e.triple_id "
            # W3b D14 — a total order on the candidates: `np.argsort` below is
            # not stable, so a similarity tie resolved by the backend's row
            # order; on SQLite this is the join's scan order (by triple_id).
            "WHERE t.archived = 0 AND e.dim = ?" + clause + " ORDER BY t.id",
            (self.dim, *params)).fetchall()
        if not rows:
            return []

        mat = np.vstack([_unpack(r["vec"], self.dim) for r in rows])
        norms = np.linalg.norm(mat, axis=1)
        norms[norms == 0.0] = 1.0
        sims = (mat @ q) / (norms * qn)

        floor = semantic_min_similarity() if min_similarity is None else min_similarity
        order = np.argsort(-sims)
        out: list[tuple[int, float]] = []
        for i in order[:max(k, 0)]:
            s = float(sims[int(i)])
            if s < floor:
                break
            out.append((int(rows[int(i)]["id"]), s))
        return out


DEFAULT_INDEX_BATCH = 32


def index_batch_size() -> int:
    raw = os.getenv("NEXUS_SEMANTIC_INDEX_BATCH", "").strip()
    if not raw:
        return DEFAULT_INDEX_BATCH
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_INDEX_BATCH


async def index_pending(index: SemanticIndex, provider, *,
                        limit: int | None = None, model: str = "") -> dict:
    """Embed up to `limit` un-indexed live beliefs. The one indexing path.

    Shared by the KAIROS idle job and the Law 3 backfill endpoint so there is
    exactly one place where a belief acquires a vector — the same argument
    that keeps `add()` the single write chokepoint.

    ON THE IDLE CYCLE, NOT THE TURN. Embedding is a provider call per belief
    and extraction produces several beliefs per turn; paying that inline would
    put a variable number of round trips on the latency path for a benefit
    that is not needed until the NEXT question. KAIROS already owns a
    per-cycle budget and only runs when the session is idle.

    Returns a declared result (I-6) — an indexer that quietly indexed nothing
    is indistinguishable from one that had nothing to do.
    """
    # Imported here, not at module scope: `backend.vector.embeddings` is the
    # health-flag path, and this module must stay importable by anything that
    # only wants to read the index.
    from backend.vector.embeddings import FULL_DIM, embed_via_provider

    n = index_batch_size() if limit is None else max(0, int(limit))
    pending = index.unindexed(limit=n) if n else []
    indexed, failed, error = 0, 0, ""
    for triple_id, surface in pending:
        try:
            vec = await embed_via_provider(provider, surface, dim=FULL_DIM,
                                           kind="document")
            index.upsert(triple_id, surface, vec, model=model or "provider")
        except Exception as e:                        # noqa: BLE001
            failed += 1
            if not error:
                error = f"{type(e).__name__}: {e}"
            continue
        indexed += 1
    return {"indexed": indexed, "failed": failed, "error": error,
            "pending_before": len(pending)}


__all__ = ["DEFAULT_INDEX_BATCH", "DEFAULT_MIN_SIMILARITY", "DEFAULT_SLOTS",
           "DIM", "SemanticIndex", "index_batch_size", "index_pending",
           "semantic_min_similarity", "semantic_slots", "surface_of"]
