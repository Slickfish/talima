"""ModelProfile + ModelRegistry (arch §9, Law IX).

This file is the single source of truth for which models exist on the box.
Adding a new model is one .register() call. Routing logic NEVER imports the
default_registry instance directly — it receives a registry to query, so
behaviour is entirely data-driven.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from backend.router.capabilities import Capability

VRAM_CEILING_GB = 12.0  # arch §2.1 — RTX 3060 12GB


@dataclass(frozen=True)
class ModelProfile:
    name: str                                      # ollama tag
    display_name: str
    capabilities: tuple[Capability, ...]           # ordered: position 0 = primary
    vram_gb: float
    tokens_per_sec: float
    context_window: int
    pull_size_gb: float
    deprecated: bool = False
    notes: str = ""
    # Fix 4 — tracks whether the local Ollama instance currently has this
    # model pulled. Defaults False; sync_pulled_state() flips it after a
    # provider.list_models() poll. Frozen-safe: mutation goes through
    # dataclasses.replace, never object.__setattr__.
    is_pulled: bool = False
    # Manual-only models are registered (visible, override-selectable by name
    # or role) but excluded from automatic capability routing — best_for/
    # with_capability skip them, so they never displace an auto-routed primary.
    manual_only: bool = False

    def fits_vram(self, available_gb: float = VRAM_CEILING_GB) -> bool:
        return self.vram_gb <= available_gb

    def has(self, cap: Capability) -> bool:
        return cap in self.capabilities

    def primary_index(self, cap: Capability) -> int:
        return self.capabilities.index(cap) if cap in self.capabilities else 1_000


def matches_available(name: str, available: list[str]) -> bool:
    """True iff `name` names a model the backend reported as installed.

    Matching is on the base family (`name.split(':')[0]`), because a registry
    entry and an installed tag routinely disagree about the quantisation
    suffix — registry `qwen2.5-coder:7b` against an installed
    `qwen2.5-coder:7b-q4_K_M` is the roster we ship with.

    R.3 — this rule lived inside `sync_pulled_state` and nowhere else, so
    `/model/override` validated raw model names with a plain `in` instead.
    That difference was invisible while `list_models()` returned an empty list
    on a booted box; the moment it returns the real roster, the strict check
    starts rejecting the operator's own installed models for a suffix. One
    rule, one place.

    Caveat, inherited and still true: base-family matching also accepts a
    SIBLING tag — `qwen3:8b` matches an installed `qwen3.5:9b` because
    "qwen3" is a substring of it. Recognition is coarser than the exact tag
    inference uses. With `/api/ps` this almost never fired because the list
    was almost always empty; with `/api/tags` it fires routinely, and the only
    profile it touches on the shipped roster is `qwen3:8b`, which is
    `manual_only` and never auto-routed.
    """
    base = (name or "").split(":")[0].lower()
    if not base:
        return False
    return any(base in m.lower() for m in available)


class ModelRegistry:
    def __init__(self):
        self._models: dict[str, ModelProfile] = {}

    # ---------- mutation ----------

    def register(self, profile: ModelProfile) -> None:
        if profile.name in self._models:
            raise ValueError(f"model {profile.name!r} already registered")
        self._models[profile.name] = profile

    def copy(self) -> "ModelRegistry":
        """An independent registry holding the same profiles.

        R.3 — `create_services()` handed every app the process-global
        `default_registry`, and `sync_pulled_state()` writes to it. That was
        invisible while the probe read `/api/ps` and got an empty list on any
        box under test: nothing ever flipped, so nothing ever leaked. The
        moment the probe reads installed models, one app's startup flags
        models for every other app in the process — including, under pytest,
        every test that runs afterwards.

        Profiles are frozen (Law 1), so sharing the objects is safe; it is the
        `_models` dict that must not be shared.
        """
        clone = ModelRegistry()
        clone._models = dict(self._models)
        return clone

    def deprecate(self, name: str) -> None:
        old = self._models[name]
        self._models[name] = ModelProfile(
            **{**old.__dict__, "deprecated": True}
        )

    # ---------- read ----------

    def all(self, *, include_deprecated: bool = False) -> list[ModelProfile]:
        return [m for m in self._models.values()
                if include_deprecated or not m.deprecated]

    def by_name(self, name: str) -> ModelProfile:
        return self._models[name]

    def with_capability(
        self, cap: Capability, *, available_vram_gb: float = VRAM_CEILING_GB
    ) -> list[ModelProfile]:
        return [
            m for m in self.all()
            if m.has(cap) and m.fits_vram(available_vram_gb) and not m.manual_only
        ]

    def best_for(
        self, cap: Capability, *, available_vram_gb: float = VRAM_CEILING_GB
    ) -> ModelProfile | None:
        """Sort key: (primary_index asc, tokens_per_sec desc).

        Models that list `cap` as their primary capability win; throughput
        breaks ties. No model name is referenced in the comparison.
        """
        candidates = self.with_capability(cap, available_vram_gb=available_vram_gb)
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda m: (m.primary_index(cap), -m.tokens_per_sec),
        )

    def find_best_for(
        self,
        caps: tuple[Capability, ...],
        available_vram_gb: float = VRAM_CEILING_GB,
    ) -> ModelProfile | None:
        """Best model that satisfies the FIRST capability for which any
        candidate exists. Used by Law IX pull-proposal logic."""
        for cap in caps:
            best = self.best_for(cap, available_vram_gb=available_vram_gb)
            if best is not None:
                return best
        return None

    def recommend_new_model(
        self, caps: tuple[Capability, ...]
    ) -> ModelProfile | None:                                 # pragma: no cover
        """Phase 6: registry only knows what it knows; this hook lands in
        Phase 9 alongside an external catalogue."""
        return None

    # ---------- pull-state sync (Fix 4) ----------

    async def sync_pulled_state(self, provider) -> list[str]:
        """Poll the provider for INSTALLED models and flip is_pulled=True on
        every registered profile whose name matches.

        Matching rule and its caveat live in `matches_available()` above —
        R.3 moved them there because `/model/override` needed the same rule
        and had its own, stricter one.

        R.3 also fixed the poll itself: this docstring has always described
        `ollama list`, and `OllamaProvider.list_models()` called `/api/ps`,
        which lists models resident in VRAM. On a freshly-booted box that is
        empty, so this method returned [] and flagged nothing while every
        model sat installed on disk.

        Returns the list of model names whose is_pulled was newly flipped
        from False → True. Idempotent: re-running returns []."""
        try:
            available = await provider.list_models()
        except Exception:
            return []
        if not available:
            return []
        updated: list[str] = []
        for name, profile in list(self._models.items()):
            if profile.is_pulled:
                continue
            if matches_available(name, available):
                # Frozen-safe mutation: build a new ModelProfile and replace
                # the entry in the registry dict. Law I respected.
                self._models[name] = dataclasses.replace(profile, is_pulled=True)
                updated.append(name)
        return updated


# ---------- default roster (arch §9.2, April 2026) ----------

default_registry = ModelRegistry()

default_registry.register(ModelProfile(
    name="deepseek-r1:14b-qwen-distill-q4_K_M",
    display_name="DeepSeek R1 14B",
    capabilities=(
        Capability.GENERAL_REASONING,
        Capability.ADVANCED_REASONING,
        Capability.GRAPH_SYNTHESIS,
        Capability.CODE_GENERATION,
    ),
    vram_gb=8.7, tokens_per_sec=20.0, context_window=8192, pull_size_gb=8.5,
    notes="Primary model. Non-negotiable footprint.",
))

default_registry.register(ModelProfile(
    name="qwen2.5-coder:7b",
    display_name="Qwen2.5 Coder 7B",
    capabilities=(
        Capability.CODE_GENERATION,
        Capability.CODE_REVIEW,
        Capability.GENERAL_REASONING,
    ),
    vram_gb=4.5, tokens_per_sec=45.0, context_window=8192, pull_size_gb=4.4,
    notes="Code specialist. Faster than R1 for code tasks. Tag is q4_K_M by default.",
))

default_registry.register(ModelProfile(
    name="phi4-mini:3.8b-q4_K_M",
    display_name="Phi 4 Mini 3.8B",
    capabilities=(
        Capability.FAST_EXTRACTION,
        Capability.GENERAL_REASONING,
    ),
    vram_gb=2.5, tokens_per_sec=70.0, context_window=4096, pull_size_gb=2.4,
    notes="Always used for triple extraction (Stage 4).",
))

default_registry.register(ModelProfile(
    name="nomic-embed-text",
    display_name="Nomic Embed",
    capabilities=(Capability.EMBEDDING,),
    vram_gb=0.3, tokens_per_sec=200.0, context_window=8192, pull_size_gb=0.27,
    notes="Always resident. Never swapped.",
))

# qwen3:8b — opt-in reasoning/thinking alternative (override-only via the
# `reasoning` role). context_window records the model's native 40K window;
# runtime num_ctx is still hard-capped at 8192 (Law V). Not pulled by default.
default_registry.register(ModelProfile(
    name="qwen3:8b",
    display_name="Qwen3 8B",
    capabilities=(
        Capability.GENERAL_REASONING,
        Capability.ADVANCED_REASONING,
        Capability.GRAPH_SYNTHESIS,
    ),
    vram_gb=5.5, tokens_per_sec=35.0, context_window=40000, pull_size_gb=5.2,
    manual_only=True,
    notes="Reasoning + thinking mode, tool use. Override-only; never auto-routed.",
))

# Backfill the multimodal role's profile so MODEL_MAP and the registry agree
# (single source of truth). This is the tag model_map resolves for multimodal.
default_registry.register(ModelProfile(
    name="qwen3.5:9b-q4_K_M",
    display_name="Qwen3.5 9B (multimodal)",
    capabilities=(
        Capability.MULTIMODAL,
        Capability.GENERAL_REASONING,
    ),
    vram_gb=6.6, tokens_per_sec=25.0, context_window=8192, pull_size_gb=6.6,
    notes="Vision + tools. Pulled on confirm (Law 3, docs/CONTRACTS.md). num_ctx capped at 8192 (Law 5).",
))
