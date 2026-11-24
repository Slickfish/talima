"""PullManager — Law IX: NEVER pull autonomously. Always propose first.

When required capabilities exist on no installed model, the kernel surfaces a
non-blocking proposal card with size/time/VRAM honest estimates. The user
decides. This module does not contact Ollama until .confirm() is called.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.router.capabilities import Capability
from backend.router.model_registry import ModelProfile, ModelRegistry, VRAM_CEILING_GB


# Honest assumption: ~6.25 MB/s sustained pull. arch §9.4 estimator.
PULL_MBPS = 6.25
SECS_PER_MIN = 60.0


@dataclass(frozen=True)
class PullProposal:
    candidate: ModelProfile
    required_capabilities: tuple[Capability, ...]
    size_gb: float
    estimated_minutes: float
    vram_impact: str
    fallback: ModelProfile | None
    explanation: str

    def to_dict(self) -> dict:
        return {
            "model":          self.candidate.display_name,
            "name":           self.candidate.name,
            "capabilities":   [c.value for c in self.candidate.capabilities],
            "size_gb":        round(self.size_gb, 2),
            "est_minutes":    round(self.estimated_minutes, 1),
            "vram_impact":    self.vram_impact,
            "fallback":       self.fallback.display_name if self.fallback else None,
            "reason":         self.explanation,
        }


class PullRefused(RuntimeError):
    """Raised by .confirm(False) — caller declined the proposal."""


class PullManager:
    def __init__(
        self,
        registry: ModelRegistry,
        *,
        available_vram_gb: float = VRAM_CEILING_GB,
    ):
        self.registry = registry
        self.available_vram_gb = available_vram_gb

    def evaluate(
        self,
        required_caps: tuple[Capability, ...],
        *,
        candidate: ModelProfile | None,
        context: str = "",
    ) -> PullProposal | None:
        if candidate is None:
            return None
        est = (candidate.pull_size_gb * 1024.0) / (PULL_MBPS * SECS_PER_MIN)
        return PullProposal(
            candidate=candidate,
            required_capabilities=required_caps,
            size_gb=candidate.pull_size_gb,
            estimated_minutes=est,
            vram_impact=(
                f"{candidate.vram_gb}GB — fits the {self.available_vram_gb}GB ceiling"
                if candidate.fits_vram(self.available_vram_gb)
                else f"{candidate.vram_gb}GB — EXCEEDS {self.available_vram_gb}GB ceiling"
            ),
            fallback=self.registry.find_best_for(required_caps, self.available_vram_gb),
            explanation=context or "no_installed_model_meets_required_capabilities",
        )

    async def confirm(
        self,
        proposal: PullProposal,
        *,
        confirmed: bool,
        provider=None,
    ) -> ModelProfile:                                         # pragma: no cover
        if not confirmed:
            raise PullRefused("user did not confirm pull (Law IX)")
        # Real Ollama pull lands here when wired against a running daemon.
        # Fix 4 — if a provider is supplied, refresh is_pulled state so the
        # router immediately sees the new model as installed.
        if provider is not None:
            try:
                await self.registry.sync_pulled_state(provider)
            except Exception:
                pass
        return proposal.candidate
