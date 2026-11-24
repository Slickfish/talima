"""Adaptive granularity tags — Phase 2 (CLAUDE.md test requirement).

Every triple, every LlamaIndex chunk, and every Hippocampus edge carries one
of these tags so downstream retrieval can rank by appropriate scope.
"""
from __future__ import annotations

from enum import Enum


class Granularity(str, Enum):
    UTTERANCE = "utterance"   # one sentence / one fact
    TURN      = "turn"        # one user+assistant exchange
    SESSION   = "session"     # entire conversation
    TOPIC     = "topic"       # cross-session concept (seeded ontology)

    @classmethod
    def all(cls) -> list[str]:
        return [g.value for g in cls]
