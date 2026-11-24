"""Semantic near-duplicate detection for the triple store.

Runs as a KAIROS idle-cycle job, never inline during inference.
Flags candidates — never collapses without explicit user confirmation (Law 3).
Archives to tier 4, never deletes (Law 4).

Anthropic's Dreaming feature merges duplicate information as a core function;
NEXUS reaches the same need from below — duplicate accumulation degrades
retrieval precision over time and can outpace the compaction cascade's
token-relief tracking.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from backend.kb.tiered_store import TripleStore
    from backend.vector.chroma import VectorStore


SIMILARITY_THRESHOLD     = 0.92      # cosine similarity = near-duplicate candidate
MAX_CANDIDATES_PER_CYCLE = 20        # prevent runaway scan on large KBs
MIN_TRIPLE_AGE_HOURS     = 1         # don't flag triples written this session


@dataclass
class DuplicateCandidate:
    triple_a: dict
    triple_b: dict
    similarity: float
    suggested_keep: str        # "a" | "b" — based on weight + recency
    reason: str                # human-readable rationale for UI


class SemanticDeduplicator:
    """Idle-time scanner. Surfaces candidates; archival is gated by Law 3."""

    def __init__(
        self,
        vector_store: "VectorStore",
        triple_store: "TripleStore",
    ):
        self.vector = vector_store
        self.store  = triple_store

    async def scan(self) -> List[DuplicateCandidate]:
        candidates: list[DuplicateCandidate] = []
        triples = self.store.get_relevant(concepts=[], top_k=200)
        seen_pairs: set[tuple[str, str]] = set()

        for t in triples:
            surface = self._surface(t)
            results = await self.vector.query(surface, final_k=5)
            for r in results:
                # Dict-style access via the prep-shim RetrievedChunk
                if r["score"] < SIMILARITY_THRESHOLD:
                    continue
                if r["doc"] == surface:
                    continue

                pair = tuple(sorted([surface, r["doc"]]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                other = self._lookup_by_surface(r["doc"])
                if not other:
                    continue
                # Don't flag a triple against itself with metadata only
                if (
                    other["subject"]  == t["subject"]
                    and other["relation"] == t["relation"]
                    and other["object"] == t["object"]
                ):
                    continue

                candidates.append(DuplicateCandidate(
                    triple_a       = t,
                    triple_b       = other,
                    similarity     = float(r["score"]),
                    suggested_keep = self._suggest(t, other),
                    reason         = self._explain(t, other),
                ))
                if len(candidates) >= MAX_CANDIDATES_PER_CYCLE:
                    return candidates
        return candidates

    # ---------- surface form helpers ----------

    @staticmethod
    def _surface(t: dict) -> str:
        return f"{t['subject']} {t['relation']} {t['object']}"

    def _lookup_by_surface(self, surface: str) -> Optional[dict]:
        parts = surface.split(" ", 2)
        if len(parts) < 3:
            return None
        with self.store.conn() as c:
            # L.2a — `archived = 0`. This lookup had no archive clause, so an
            # already-archived row could be re-surfaced as a duplicate
            # candidate: the operator would be asked to archive something they
            # had already archived, and confirming it would write a second
            # `archive_reason` over the first. `scan()`'s own candidate list
            # comes from `get_relevant`, which has always filtered — this was
            # the one path in the dedup cycle that did not.
            row = c.execute(
                "SELECT * FROM triples "
                "WHERE archived = 0 AND subject=? AND relation=? AND object=? "
                "LIMIT 1",
                (parts[0], parts[1], parts[2]),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _suggest(a: dict, b: dict) -> str:
        wa = float(a.get("weight", 0.0))
        wb = float(b.get("weight", 0.0))
        return "a" if wa >= wb else "b"

    @staticmethod
    def _explain(a: dict, b: dict) -> str:
        wa = float(a.get("weight", 0.0))
        wb = float(b.get("weight", 0.0))
        return f"Higher weight ({wa:.2f} vs {wb:.2f})"
