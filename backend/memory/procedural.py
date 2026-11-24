"""Procedural memory — behavioural patterns promote at >=5 instances.

Phase 5 minimum: track recurring query-term patterns. When a sorted-tuple
of extracted terms recurs PROCEDURAL_PROMOTION_THRESHOLD times, promote it
into the neocortex as a (procedural, recurring_pattern, term) triple set so
future retrieval surfaces the user's habits even after session reset.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.kb.granularity import Granularity
from backend.kb.retriever import extract_terms

if TYPE_CHECKING:
    from backend.memory.neocortex import Neocortex


PROCEDURAL_PROMOTION_THRESHOLD = 5  # arch §13 Phase 5: ">=5 instances"


@dataclass
class ProceduralMemory:
    threshold: int = PROCEDURAL_PROMOTION_THRESHOLD
    counts: dict[tuple[str, ...], int] = field(default_factory=dict)
    promoted: set[tuple[str, ...]] = field(default_factory=set)

    def observe(self, query: str) -> tuple[str, ...]:
        """Record one observation of a query pattern. Returns the pattern key."""
        terms = tuple(sorted(extract_terms(query)))
        if not terms:
            return ()
        self.counts[terms] = self.counts.get(terms, 0) + 1
        return terms

    def candidates(self) -> list[tuple[str, ...]]:
        return [
            p for p, n in self.counts.items()
            if n >= self.threshold and p not in self.promoted
        ]

    def promote(self, neocortex: "Neocortex") -> int:
        """Write candidate patterns to the neocortex. Returns patterns promoted."""
        n = 0
        for pattern in self.candidates():
            for term in pattern:
                neocortex.store_pattern_triple(
                    subject="procedural",
                    relation="recurring_query_term",
                    object=term,
                    weight=0.7,
                    source="procedural_promotion",
                    granularity=Granularity.TOPIC,
                )
            self.promoted.add(pattern)
            n += 1
        return n
