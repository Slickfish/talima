"""KBSource registry — Law X: new sources are registered in one place.

Adding a source is a single .register(KBSource(...)) call (arch §10.4).
Phase 2 ships ConceptNet + Wikidata; Phase 5+ adds PubMed, UMLS, etc.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class KBSource:
    id: str
    display_name: str
    source_type: str          # "api" | "sparql" | "static"
    endpoint: str
    domains: tuple[str, ...]
    is_live: bool = True
    rate_limit_per_min: int = 30
    notes: str = ""


class KBSourceRegistry:
    def __init__(self):
        self._sources: dict[str, KBSource] = {}

    def register(self, source: KBSource) -> None:
        if source.id in self._sources:
            raise ValueError(f"source {source.id!r} already registered")
        self._sources[source.id] = source

    def get(self, source_id: str) -> KBSource:
        return self._sources[source_id]

    def all(self) -> list[KBSource]:
        return list(self._sources.values())

    def for_domain(self, domain: str) -> list[KBSource]:
        return [s for s in self._sources.values() if domain in s.domains]


# Default registry — Phase 2 sources
default_registry = KBSourceRegistry()
default_registry.register(KBSource(
    id="conceptnet",
    display_name="ConceptNet 5",
    source_type="api",
    endpoint="https://api.conceptnet.io",
    domains=("general", "language", "commonsense"),
    rate_limit_per_min=30,
    notes="Multilingual commonsense graph. Free, no auth.",
))
default_registry.register(KBSource(
    id="wikidata",
    display_name="Wikidata",
    source_type="sparql",
    endpoint="https://query.wikidata.org/sparql",
    domains=("general", "encyclopedic", "entities"),
    rate_limit_per_min=20,
    notes="Universal entity graph. SPARQL endpoint.",
))
