"""NexusLoopState — frozen dataclass per Law VI (immutable state).

State transitions replace the entire object atomically via `next_state()`.
Future phases extend the field set; the immutability contract never changes.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AssembledContext:
    """Stage-2 context-assembly result. Frozen so future phases (contradictions,
    curiosity, …) extend it via .replace() without breaking older test access
    patterns."""
    hits: list                       # list[RetrievedTriple]
    activation: object               # ActivationMap | None
    vector_chunks: list              # list[RetrievedChunk]
    block: str
    # AW — the (concept, activation) pairs the cortex section RENDERED, in
    # order, so the lineage record can carry what the model was told and the
    # derivation can rebuild that section from the record alone. Empty under
    # the legacy render, which records its block by hash only.
    rendered_activation: tuple = ()

    def has_dynamic_content(self) -> bool:
        return bool(self.block)


@dataclass(frozen=True)
class NexusLoopState:
    messages: tuple = field(default_factory=tuple)
    graph_context: tuple = field(default_factory=tuple)
    session_memory: tuple = field(default_factory=tuple)
    predicted_nodes: tuple = field(default_factory=tuple)
    prediction_error: float = 0.0
    active_model: str = "deepseek-r1:14b-qwen-distill-q4_K_M"
    turn_count: int = 0
    token_count: int = 0
    compaction_state: str = "none"
    last_extraction: tuple = field(default_factory=tuple)
    transition_reason: str = "init"
    # Fix 0 / Part B — user model override (session-level pin).
    # model_override: canonical role ("primary"|"code"|...) OR raw provider name.
    # override_locked: True = persists across turns; False = clears after turn.
    model_override: str | None = None
    override_locked: bool = False
    # Fix 9 — multimodal attachments for THIS turn only (single-use; clears
    # at end-of-turn atomic transition). Each element is a small dict-like
    # snapshot: {"name": str, "type": str (mime), "data": str (base64)}.
    attachments: tuple = field(default_factory=tuple)
    # v3.1 Phase 1b — NO current unit on the state. 1a carried
    # `work_unit_id` / `accountable_identity` here so a later turn could
    # inherit them; 1b removed both: attribution is asserted by the caller
    # on each request as scope is, and the lineage record holds it. The one
    # unit-shaped field that remains is a report list, not an inheritance:
    # the units whose turns sit in `messages` since the last tier-4 fire, so
    # the tier-4 report can say which units it summarised. Cleared by tier 4.
    history_work_units: tuple = field(default_factory=tuple)
    # CHANGE A (shape A in production) — the history wrapper's carrier: one
    # entry per assistant answer still in `messages`, in production order
    # (`backend.loop.history.carrier_entry`: the loop's turn count when the
    # answer was produced and the hashes assembly checks it against). Kept
    # beside the messages, never on them; pruned to the history at every
    # assembly; a disagreement raises rather than relabels.
    history_carrier: tuple = field(default_factory=tuple)


def next_state(state: NexusLoopState, **updates) -> NexusLoopState:
    return dataclasses.replace(state, **updates)
