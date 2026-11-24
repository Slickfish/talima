"""ModelKernel — capability-based router (Law IX, arch §9).

This module is forbidden from referencing any specific model name in its
branching logic. It analyses query signals + activated cortical columns,
picks a Capability, and asks the registry for the best fit. The registry
returns a ModelProfile; the kernel passes through the .name attribute as an
opaque string. Routing is entirely data-driven.

Phase 6 test 'router never branches on model name' source-greps this file.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.cortex.columns import Column, ColumnState, infer_column
from backend.router.capabilities import Capability
from backend.router.model_registry import (
    ModelProfile,
    ModelRegistry,
    VRAM_CEILING_GB,
    default_registry,
)


# ---------- intent signals (capability-only; no model names) ----------

# Each token below maps a query keyword to a capability. The keywords are
# derived from the query domain ("code", "blood pressure", etc.), never from a
# model identifier. Adding a new capability/signal does not require touching
# the registry.
CODE_SIGNALS    = (
    "code", "function", "class", "compile", "debug", "syntax",
    "framework", "library", "package", "exception", "stack trace",
    "python", "javascript", "typescript", "rust", "java", "c++",
    "regex", "git",
)
CODE_REVIEW_HINTS = ("review", "audit", "explain", "refactor")
MEDICAL_SIGNALS = (
    "blood", "pressure", "heart", "disease", "symptom", "medication",
    "dose", "diagnosis", "patient", "clinical", "doctor", "therapy",
)


# ---------- decision record ----------

@dataclass(frozen=True)
class RoutingDecision:
    capability: Capability
    profile: ModelProfile | None      # None when no installed model satisfies
    reason: str
    fallback_used: bool = False

    @property
    def model_name(self) -> str:
        return self.profile.name if self.profile else ""


# ---------- the kernel ----------

class ModelKernel:
    """Selects a Capability from query signals, then asks the registry for
    the best ModelProfile that satisfies it. Falls back to GENERAL_REASONING
    when no installed model matches the inferred capability."""

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        *,
        available_vram_gb: float = VRAM_CEILING_GB,
    ):
        self.registry = registry or default_registry
        self.available_vram_gb = available_vram_gb

    # ---------- intent inference ----------

    def infer_capability(
        self,
        query: str,
        *,
        column_state: ColumnState | None = None,
    ) -> Capability:
        q = (query or "").lower()

        if any(s in q for s in CODE_SIGNALS):
            if any(h in q for h in CODE_REVIEW_HINTS):
                return Capability.CODE_REVIEW
            return Capability.CODE_GENERATION

        if any(s in q for s in MEDICAL_SIGNALS):
            return Capability.MEDICAL_REASONING

        # Cortical-column signal: association cortex firing → multi-hop synthesis.
        if column_state is not None:
            assoc = column_state.activations.get(Column.ASSOCIATION, 0.0)
            domain_max = max(
                (column_state.activations.get(c, 0.0)
                 for c in Column.domain_columns()),
                default=0.0,
            )
            if assoc > 0.0 and assoc >= domain_max:
                return Capability.ADVANCED_REASONING

            # Domain column hints (no model names involved)
            winner = column_state.winner()
            if winner is Column.CODING:
                return Capability.CODE_GENERATION
            if winner is Column.HEALTH:
                return Capability.MEDICAL_REASONING

        return Capability.GENERAL_REASONING

    # ---------- top-level routing ----------

    def route(
        self,
        *,
        query: str,
        column_state: ColumnState | None = None,
        required_capability: Capability | None = None,
    ) -> RoutingDecision:
        cap = required_capability or self.infer_capability(query, column_state=column_state)

        profile = self.registry.best_for(
            cap, available_vram_gb=self.available_vram_gb
        )
        if profile is not None:
            return RoutingDecision(
                capability=cap, profile=profile,
                reason=f"matched_capability:{cap.value}",
            )

        # Fallback: GENERAL_REASONING is a near-universal capability.
        general = self.registry.best_for(
            Capability.GENERAL_REASONING, available_vram_gb=self.available_vram_gb
        )
        return RoutingDecision(
            capability=cap, profile=general,
            reason=f"no_installed_model_for:{cap.value};fallback:general_reasoning",
            fallback_used=True,
        )

    # ---------- always-on capability (Stage 4 extraction) ----------

    def fast_extractor(self) -> ModelProfile | None:
        return self.registry.best_for(
            Capability.FAST_EXTRACTION, available_vram_gb=self.available_vram_gb
        )

    def embedding_model(self) -> ModelProfile | None:
        return self.registry.best_for(
            Capability.EMBEDDING, available_vram_gb=self.available_vram_gb
        )
