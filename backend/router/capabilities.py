"""Capability taxonomy (arch §9.1, Law IX).

The router speaks ONLY in capabilities. Model names live in the registry as
data; routing logic never branches on them.
"""
from __future__ import annotations

from enum import Enum


class Capability(str, Enum):
    GENERAL_REASONING   = "general_reasoning"
    ADVANCED_REASONING  = "advanced_reasoning"
    GRAPH_SYNTHESIS     = "graph_synthesis"
    CODE_GENERATION     = "code_generation"
    CODE_REVIEW         = "code_review"
    FAST_EXTRACTION     = "fast_extraction"
    EMBEDDING           = "embedding"
    MEDICAL_REASONING   = "medical_reasoning"
    MULTIMODAL          = "multimodal"

    @classmethod
    def all(cls) -> list["Capability"]:
        return list(cls)


# AR.4 — the capabilities that are NOT answering capabilities. A model whose
# profile declares only these cannot take a chat turn, so `/model/override`
# refuses it by name. The ONE stated fact in that derivation — everything
# else is read from the registry's declarations — and it is checked against
# the kernel by AST (tests/test_ar4_override_answering.py): these are exactly
# the capabilities the kernel reaches through a dedicated accessor
# (`fast_extractor`, `embedding_model`) rather than through `route()`, and
# `infer_capability` never returns one. Add a capability here only when the
# kernel gains a non-chat accessor for it; the test says so if the two drift.
NON_ANSWERING: frozenset[Capability] = frozenset({
    Capability.FAST_EXTRACTION,
    Capability.EMBEDDING,
})


def can_answer(capabilities) -> bool:
    """True iff at least one declared capability is an answering one."""
    return any(c not in NON_ANSWERING for c in capabilities)
