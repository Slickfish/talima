"""Model registry / role-map consistency sanity checks.

Guards the invariant the audit surfaced: every tag MODEL_MAP can resolve for
the active backend must have a corresponding ModelProfile in default_registry,
and the previously-broken coder tag must resolve to a real Ollama tag.
"""
from backend.providers.model_map import MODEL_MAP, ROLES, resolve
from backend.router.model_registry import default_registry


def test_every_ollama_role_tag_has_a_registry_profile():
    """Single source of truth: no MODEL_MAP tag without a ModelProfile."""
    for role, tag in MODEL_MAP["ollama"].items():
        assert default_registry.by_name(tag) is not None, (
            f"role {role!r} -> {tag!r} has no ModelProfile in default_registry"
        )


def test_coder_role_uses_valid_ollama_tag():
    """qwen2.5-coder:7b is the real Q4_K_M tag; the -q4_K_M variant 404s."""
    assert resolve("code", backend="ollama") == "qwen2.5-coder:7b"
    names = {m.name for m in default_registry.all()}
    assert "qwen2.5-coder:7b" in names
    assert "qwen2.5-coder:7b-q4_K_M" not in names  # old broken tag gone


def test_reasoning_role_resolves_to_qwen3_8b():
    assert "reasoning" in ROLES
    assert resolve("reasoning", backend="ollama") == "qwen3:8b"
    assert default_registry.by_name("qwen3:8b") is not None


def test_multimodal_profile_backfilled():
    tag = MODEL_MAP["ollama"]["multimodal"]
    profile = default_registry.by_name(tag)
    assert profile is not None
    from backend.router.capabilities import Capability
    assert profile.has(Capability.MULTIMODAL)


# ---------------------------------------------------------------------------
# The `general` role — qwen3.5:9b as an opt-in ANSWERING model
# ---------------------------------------------------------------------------

def test_general_role_resolves_to_qwen35():
    """The `reasoning` precedent: a roster entry, not a special case (Law 2).
    Same tag the multimodal role already registers, so the registry's
    single-source-of-truth check covers it with no second profile."""
    assert "general" in ROLES
    assert resolve("general", backend="ollama") == "qwen3.5:9b-q4_K_M"
    assert default_registry.by_name("qwen3.5:9b-q4_K_M") is not None


def test_general_role_does_not_displace_the_default_answerer():
    """Auto-routing for a general turn must still emit `primary` and the
    best GENERAL_REASONING holder must still be DeepSeek — qwen3.5 answers
    only when the operator explicitly overrides to it."""
    from backend.providers.model_map import capability_to_role
    from backend.router.capabilities import Capability
    assert capability_to_role(Capability.GENERAL_REASONING) == "primary"
    assert resolve("primary", backend="ollama") == \
        "deepseek-r1:14b-qwen-distill-q4_K_M"
    best = default_registry.best_for(Capability.GENERAL_REASONING)
    assert best is not None
    assert best.name == "deepseek-r1:14b-qwen-distill-q4_K_M"


def test_the_general_role_cannot_lift_the_law_v_ceiling():
    """Qwen3.5 advertises a very large native context. Registration is
    metadata: the ceiling lives at the call (`stream_chat` raises above
    NUM_CTX), nothing feeds `context_window` into `num_ctx` (the one live
    call site passes the default), and NUM_CTX is pinned unreadable from the
    environment by UNREAD_BY_BACKEND. The raise precedes any HTTP, so this
    touches no network."""
    import asyncio

    import pytest as _pytest

    from backend.providers.ollama import NUM_CTX, OllamaProvider
    assert NUM_CTX == 8192
    agen = OllamaProvider().stream_chat(resolve("general"), [],
                                        num_ctx=NUM_CTX + 1)
    with _pytest.raises(ValueError):
        asyncio.run(agen.__anext__())


def test_an_override_to_general_records_identity_like_any_model(tmp_path):
    """No special path: overriding to the role must land the resolved tag in
    `model_routed`, in the lineage record's model_id, and in `done` — the
    same three surfaces every other model's identity rides."""
    import asyncio
    import json

    from backend.loop.agentic_loop import run_turn
    from backend.loop.state import NexusLoopState
    from backend.memory.lineage import LineageStore

    class _P:
        def __init__(self): self.calls = 0

        async def stream_chat(self, model, messages, num_ctx=8192):
            self.calls += 1
            yield "an answer" if self.calls == 1 else "[]"

        async def get_embedding(self, text): return [0.0] * 768
        async def list_models(self): return []
        async def health(self): return True

    lineage = LineageStore(tmp_path / "kb.db")
    frames = []

    async def drive():
        async for frame, _s in run_turn(
                NexusLoopState(model_override="general",
                               override_locked=True),
                "hello there", provider=_P(), lineage=lineage):
            frames.append(frame)

    asyncio.run(drive())
    routed = next(json.loads(f.split("data: ", 1)[1].strip())
                  for f in frames if f.startswith("event: model_routed"))
    assert routed["model"] == "qwen3.5:9b-q4_K_M"
    assert routed["override_active"] is True
    rec = lineage.recent(limit=1)[0]
    assert rec.model_id == "qwen3.5:9b-q4_K_M"
    lineage.close()
