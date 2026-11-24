"""Phase 6 tests — required coverage from CLAUDE.md:

  6.A — DeepEval RAGAS score logged per turn
  6.B — router selects Qwen on code keywords
        router never references model name in branching

Plus arch-doc invariants:
  - registry one-line extension still works
  - VRAM ceiling enforced
  - fallback to GENERAL_REASONING when no model has the inferred capability
  - FAST_EXTRACTION (Stage 4) selected by .fast_extractor()
  - PullProposal honest size/time/VRAM (Law IX)
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from backend.cortex.columns import Column, ColumnState
from backend.eval.ragas import (
    TurnEvaluator,
    TurnMetrics,
    answer_relevancy_score,
    context_precision_score,
    faithfulness_score,
)
from backend.kb.granularity import Granularity
from backend.kb.retriever import RetrievedTriple, TripleRetriever
from backend.kb.tiered_store import TripleStore
from backend.router.capabilities import Capability
from backend.router.model_kernel import ModelKernel
from backend.router.model_registry import (
    ModelProfile,
    ModelRegistry,
    VRAM_CEILING_GB,
    default_registry,
)
from backend.router.pull_manager import PullManager, PullProposal


# =================== Phase 6.A: DeepEval / RAGAS ===================

def test_evaluator_logs_one_record_per_turn(tmp_path: Path):
    log = tmp_path / "metrics.jsonl"
    ev = TurnEvaluator(log_path=log)
    ev.evaluate(turn=1, query="what is python", response="python is a language",
                hits=[], fired_node_ids=set())
    ev.evaluate(turn=2, query="and javascript", response="javascript also is",
                hits=[], fired_node_ids=set())
    assert len(ev.records) == 2
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["turn"] == 1
    assert "answer_relevancy" in rec
    assert "faithfulness" in rec
    assert "context_precision" in rec


def test_metric_record_is_in_unit_interval_or_not_computed():
    ev = TurnEvaluator()
    m = ev.evaluate(
        turn=1, query="python machine learning",
        response="python is great for machine learning",
    )
    assert 0.0 <= m.answer_relevancy <= 1.0
    # BB Part 1 — nothing injected: the two fractions have no denominator
    # and are not computed, and so is the composite.
    assert m.faithfulness is None and m.context_precision is None and m.composite() is None
    assert m.n_injected_triples == 0


def test_answer_relevancy_jaccard_overlap():
    # identical content: 1.0
    assert answer_relevancy_score("python ml", "python ml") == 1.0
    # disjoint: 0.0
    assert answer_relevancy_score("python", "kafka") == 0.0


def test_faithfulness_is_not_computed_when_no_triples_injected():
    # Was `== 1.0` — "nothing to be unfaithful to" — until BB Part 1.
    assert faithfulness_score("anything", []) is None
    assert context_precision_score([], set()) is None


def test_faithfulness_credits_surfaced_triples(tmp_path: Path):
    store = TripleStore(tmp_path / "kb.db")
    tid = store.add(subject="python", relation="is_a", object="language",
                    weight=0.9, tier=1, granularity=Granularity.TOPIC, source="t")
    hits = TripleRetriever(store).retrieve("python language")
    assert hits
    score = faithfulness_score("python is a programming language", hits)
    assert score == 1.0
    score = faithfulness_score("kafka is a streaming platform", hits)
    assert score == 0.0
    store.close()


def test_context_precision_credits_fired_nodes(tmp_path: Path):
    store = TripleStore(tmp_path / "kb.db")
    store.add(subject="python", relation="is_a", object="language",
              weight=0.9, tier=1, granularity=Granularity.TOPIC, source="t")
    hits = TripleRetriever(store).retrieve("python language")
    assert context_precision_score(hits, fired_node_ids={"python"}) == 1.0
    assert context_precision_score(hits, fired_node_ids=set()) == 0.0
    store.close()


def test_evaluator_in_memory_records_persist_without_log_path():
    ev = TurnEvaluator()  # no log path
    ev.evaluate(turn=1, query="x", response="y")
    assert ev.latest() is not None and ev.latest().turn == 1


# =================== Phase 6.B: router selects on capability ===================

def test_router_selects_qwen_for_code_keywords():
    """Code keywords -> CODE_GENERATION -> Qwen (highest tok/s with primary cap)."""
    kernel = ModelKernel(default_registry)
    decision = kernel.route(query="please fix this python function and add type hints")
    assert decision.capability == Capability.CODE_GENERATION
    assert decision.profile is not None
    # The TEST may compare model names; the kernel itself must not.
    assert "qwen" in decision.profile.name.lower()


def test_router_selects_code_review_for_review_hint():
    kernel = ModelKernel(default_registry)
    decision = kernel.route(query="please review this typescript class for safety")
    assert decision.capability == Capability.CODE_REVIEW
    assert decision.profile is not None
    assert Capability.CODE_REVIEW in decision.profile.capabilities


def test_router_falls_back_to_general_for_health_query():
    """Health prompt infers MEDICAL_REASONING; default roster has none, so the
    kernel must fall back to GENERAL_REASONING (DeepSeek R1 in the default roster)."""
    kernel = ModelKernel(default_registry)
    decision = kernel.route(query="what causes high blood pressure")
    assert decision.capability == Capability.MEDICAL_REASONING
    assert decision.fallback_used is True
    assert decision.profile is not None
    assert Capability.GENERAL_REASONING in decision.profile.capabilities


def test_router_picks_general_for_generic_question():
    kernel = ModelKernel(default_registry)
    decision = kernel.route(query="what is philosophy about")
    assert decision.capability == Capability.GENERAL_REASONING
    assert decision.profile is not None


def test_router_promotes_advanced_when_association_column_wins():
    kernel = ModelKernel(default_registry)
    cs = ColumnState()
    cs.add(Column.ASSOCIATION, 0.9)
    decision = kernel.route(query="how do these things connect", column_state=cs)
    assert decision.capability == Capability.ADVANCED_REASONING


def test_router_uses_column_winner_when_no_keyword_signal():
    kernel = ModelKernel(default_registry)
    cs = ColumnState()
    cs.add(Column.HEALTH, 0.8)
    decision = kernel.route(query="explain this please", column_state=cs)
    assert decision.capability == Capability.MEDICAL_REASONING


def test_fast_extractor_capability_selected_for_stage_4():
    kernel = ModelKernel(default_registry)
    p = kernel.fast_extractor()
    assert p is not None
    assert Capability.FAST_EXTRACTION in p.capabilities


def test_embedding_model_resolved_via_capability():
    kernel = ModelKernel(default_registry)
    p = kernel.embedding_model()
    assert p is not None
    assert Capability.EMBEDDING in p.capabilities


# =================== Law IX: no model names in routing logic ===================

# Forbidden literal substrings — case-insensitive. Tests the source of the
# kernel module (functions + class) does not branch on any of these.
FORBIDDEN_LITERALS = ("deepseek", "qwen", "phi4", "phi-4", "phi_4", "nomic")


def test_kernel_source_contains_no_model_name_literals():
    src = Path("backend/router/model_kernel.py").read_text(encoding="utf-8").lower()
    # Strip lines that are pure comments / docstrings — we only care about
    # branching logic. (A docstring example would still count, so play it safe
    # by checking a stripped-no-comments view too.)
    code_lines = [
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)
    for forbidden in FORBIDDEN_LITERALS:
        assert forbidden not in code, (
            f"model name {forbidden!r} found in routing logic — Law IX violated"
        )


def test_kernel_class_source_contains_no_model_names():
    src = inspect.getsource(ModelKernel).lower()
    for forbidden in FORBIDDEN_LITERALS:
        assert forbidden not in src, (
            f"model name {forbidden!r} found in ModelKernel — Law IX violated"
        )


# =================== ModelRegistry semantics ===================

def test_registry_best_for_picks_highest_throughput_with_primary_cap():
    """Among models with the requested cap, primary placement wins; tok/s breaks ties."""
    best = default_registry.best_for(Capability.CODE_GENERATION)
    # Qwen lists CODE_GENERATION at position 0 → outranks DeepSeek (position 3).
    assert best is not None and "qwen" in best.name.lower()


def test_registry_best_for_general_reasoning_picks_primary_holder():
    best = default_registry.best_for(Capability.GENERAL_REASONING)
    # DeepSeek lists GENERAL_REASONING at position 0; others list it at >=1.
    assert best is not None and "deepseek" in best.name.lower()


def test_registry_best_for_returns_none_when_no_match():
    # MULTIMODAL now has a registered holder (qwen3.5:9b-q4_K_M backfill), so
    # use MEDICAL_REASONING — a capability no roster model claims — to exercise
    # the empty-candidates path.
    assert default_registry.best_for(Capability.MEDICAL_REASONING) is None


def test_registry_one_line_extension():
    reg = ModelRegistry()
    reg.register(ModelProfile(
        name="hypothetical:1b", display_name="Hypothetical 1B",
        capabilities=(Capability.CODE_GENERATION,),
        vram_gb=1.0, tokens_per_sec=100.0, context_window=4096, pull_size_gb=1.0,
    ))
    best = reg.best_for(Capability.CODE_GENERATION)
    assert best is not None and best.name == "hypothetical:1b"


def test_registry_rejects_duplicate_registration():
    reg = ModelRegistry()
    p = ModelProfile(
        name="x", display_name="X", capabilities=(Capability.GENERAL_REASONING,),
        vram_gb=1.0, tokens_per_sec=10.0, context_window=4096, pull_size_gb=1.0,
    )
    reg.register(p)
    with pytest.raises(ValueError):
        reg.register(p)


def test_registry_filters_oversized_models_by_vram():
    reg = ModelRegistry()
    reg.register(ModelProfile(
        name="huge:70b", display_name="Huge",
        capabilities=(Capability.GENERAL_REASONING,),
        vram_gb=40.0, tokens_per_sec=8.0, context_window=8192, pull_size_gb=40.0,
    ))
    assert reg.best_for(Capability.GENERAL_REASONING, available_vram_gb=12.0) is None


def test_default_roster_fits_3060_ceiling():
    for m in default_registry.all():
        assert m.vram_gb <= VRAM_CEILING_GB, f"{m.name} oversized for the 3060"


def test_registry_deprecate_excludes_from_default_listing():
    reg = ModelRegistry()
    reg.register(ModelProfile(
        name="old:1", display_name="Old", capabilities=(Capability.GENERAL_REASONING,),
        vram_gb=1.0, tokens_per_sec=10.0, context_window=4096, pull_size_gb=1.0,
    ))
    reg.deprecate("old:1")
    assert reg.all() == []
    assert reg.all(include_deprecated=True) != []


# =================== PullManager (Law IX) ===================

def test_pull_proposal_estimates_size_time_vram_honestly():
    pm = PullManager(default_registry)
    candidate = default_registry.by_name("qwen2.5-coder:7b")
    proposal = pm.evaluate(
        (Capability.CODE_GENERATION,), candidate=candidate, context="test",
    )
    assert isinstance(proposal, PullProposal)
    assert proposal.size_gb == candidate.pull_size_gb
    assert proposal.estimated_minutes > 0
    assert "GB" in proposal.vram_impact
    d = proposal.to_dict()
    assert d["size_gb"] == round(candidate.pull_size_gb, 2)


def test_pull_evaluate_returns_none_without_candidate():
    pm = PullManager(default_registry)
    assert pm.evaluate((Capability.MULTIMODAL,), candidate=None) is None


# ===================================================================
# Fix 0 / Part A — Provider abstraction
# ===================================================================

import asyncio as _asyncio
import os as _os

from backend.providers import LLMProvider, get_provider, PROVIDER
from backend.providers.lmstudio import LMStudioProvider
from backend.providers.model_map import (
    MODEL_MAP,
    ROLES as MODEL_ROLES,
    capability_to_role,
    resolve,
)
from backend.providers.ollama import NUM_CTX, OllamaProvider


class _MockProvider(LLMProvider):
    """Tiny in-memory provider — exercises the LLMProvider contract without
    talking to a real backend."""

    def __init__(self, *, healthy: bool = True, models: list[str] | None = None,
                 tokens: list[str] | None = None,
                 embedding: list[float] | None = None):
        self._healthy = healthy
        # Use `is None` so callers can explicitly pass [] to mean "no models".
        self._models = ["mock-7b"] if models is None else list(models)
        self._tokens = tokens if tokens is not None else ["hello", " ", "world"]
        self._embedding = embedding if embedding is not None else [0.0] * 768
        self.calls: list[dict] = []

    async def stream_chat(self, model, messages, num_ctx=8192):
        self.calls.append({"kind": "stream", "model": model, "n_msgs": len(messages),
                            "num_ctx": num_ctx})
        for t in self._tokens:
            yield t

    async def get_embedding(self, text):
        self.calls.append({"kind": "embed", "text": text})
        return list(self._embedding)

    async def list_models(self):
        return list(self._models)

    async def health(self):
        return self._healthy


def test_provider_stream_chat_yields_strings():
    p = _MockProvider(tokens=["a", "b", "c"])

    async def go():
        out = []
        async for tok in p.stream_chat("mock-7b", [{"role": "user", "content": "hi"}]):
            out.append(tok)
        return out

    assert _asyncio.run(go()) == ["a", "b", "c"]


def test_provider_health_returns_bool():
    assert _asyncio.run(_MockProvider(healthy=True).health()) is True
    assert _asyncio.run(_MockProvider(healthy=False).health()) is False


def test_get_provider_returns_ollama_by_default(monkeypatch):
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    assert isinstance(get_provider(), OllamaProvider)


def test_get_provider_returns_ollama_when_env_says_ollama(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    assert isinstance(get_provider(), OllamaProvider)


def test_get_provider_returns_lmstudio_when_env_says_lmstudio(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "lmstudio")
    assert isinstance(get_provider(), LMStudioProvider)


def test_get_provider_refuses_an_unknown_backend_by_name(monkeypatch):
    """W3 — before it, any value but exactly `lmstudio` selected Ollama,
    named nowhere (the register line W0b's refutation readers left; H2).
    Asserted on what is selected, so the pre-fix factory fails on that."""
    import backend.providers as providers
    monkeypatch.setenv("LLM_BACKEND", "no-such-backend")
    try:
        selected = get_provider()
    except Exception as refused:
        assert isinstance(refused, getattr(providers, "UnknownBackend", ())), refused
        assert "'no-such-backend'" in str(refused) and "ollama, lmstudio, stub, openai, anthropic" in str(refused)
    else:
        pytest.fail(f"an unknown LLM_BACKEND selected {type(selected).__name__} instead of being refused")


def test_module_level_PROVIDER_implements_protocol():
    assert isinstance(PROVIDER, LLMProvider)


def test_resolve_returns_provider_specific_name_for_each_role():
    for role in ("primary", "code", "extraction", "multimodal", "embed"):
        assert resolve(role, backend="ollama") == MODEL_MAP["ollama"][role]
        assert resolve(role, backend="lmstudio") == MODEL_MAP["lmstudio"][role]


def test_resolve_falls_back_to_primary_for_unknown_role():
    assert resolve("not-a-role", backend="ollama") == MODEL_MAP["ollama"]["primary"]


def test_resolve_falls_back_to_ollama_table_for_unknown_backend():
    assert resolve("primary", backend="weird") == MODEL_MAP["ollama"]["primary"]


def test_capability_to_role_maps_correctly():
    assert capability_to_role(Capability.CODE_GENERATION) == "code"
    assert capability_to_role(Capability.CODE_REVIEW) == "code"
    assert capability_to_role(Capability.FAST_EXTRACTION) == "extraction"
    assert capability_to_role(Capability.MULTIMODAL) == "multimodal"
    assert capability_to_role(Capability.EMBEDDING) == "embed"
    assert capability_to_role(Capability.GENERAL_REASONING) == "primary"
    assert capability_to_role(Capability.ADVANCED_REASONING) == "primary"
    assert capability_to_role(Capability.MEDICAL_REASONING) == "primary"


# W1 — `test_num_ctx_constant_still_resolvable_via_legacy_shim` stood here:
# it kept the Fix 0 shim's import path alive for tests/test_phase_1.py. That
# test now imports from backend.providers.ollama, the shim is retired, and the
# retirement is asserted by tests/test_w1_shim_retired.py. NUM_CTX == 8192
# stays pinned by test_phase_1.py::test_num_ctx_ceiling and
# tests/test_model_registry_tags.py.


# ===================================================================
# Fix 0 / Part B — User model override
# ===================================================================

from fastapi.testclient import TestClient

from backend.loop.state import NexusLoopState
from backend.loop.state import next_state as _next_state
from backend.server import create_app, create_services


def _override_app(tmp_path, *, mock_provider: _MockProvider | None = None):
    services = create_services(
        kb_db_path=tmp_path / "kb.db",
        chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "metrics.jsonl",
        snapshots_dir=tmp_path / "snapshots",      # W11c: never the repository's data/snapshots
    )
    if mock_provider is not None:
        services.provider = mock_provider
    return create_app(services), services


def test_override_endpoint_sets_role_and_marks_active(tmp_path):
    app, services = _override_app(tmp_path, mock_provider=_MockProvider(models=[]))
    with TestClient(app) as c:
        r = c.post("/model/override", json={"model": "code", "lock": False})
    assert r.status_code == 200
    body = r.json()
    assert body["override_active"] is True
    assert body["locked"] is False
    assert body["resolved_model"] == MODEL_MAP["ollama"]["code"]
    assert services.state.model_override == "code"
    assert services.state.override_locked is False


def test_override_locked_persists_across_state_advances(tmp_path):
    """lock=True survives an end-of-turn next_state pattern that the loop uses."""
    app, services = _override_app(tmp_path, mock_provider=_MockProvider(models=[]))
    with TestClient(app) as c:
        c.post("/model/override", json={"model": "primary", "lock": True})
    # Simulate end of turn: locked → preserve, unlocked → clear
    next_override = services.state.model_override if services.state.override_locked else None
    services.state = _next_state(
        services.state,
        model_override=next_override,
        override_locked=services.state.override_locked,
    )
    assert services.state.model_override == "primary"
    assert services.state.override_locked is True


def test_override_unlocked_clears_after_one_turn(tmp_path):
    app, services = _override_app(tmp_path, mock_provider=_MockProvider(models=[]))
    with TestClient(app) as c:
        c.post("/model/override", json={"model": "code", "lock": False})
    # Simulate end-of-turn next_state
    next_override = services.state.model_override if services.state.override_locked else None
    services.state = _next_state(
        services.state,
        model_override=next_override,
        override_locked=services.state.override_locked,
    )
    assert services.state.model_override is None


def test_override_clear_endpoint(tmp_path):
    app, services = _override_app(tmp_path, mock_provider=_MockProvider(models=[]))
    with TestClient(app) as c:
        c.post("/model/override", json={"model": "code", "lock": True})
        assert services.state.model_override == "code"
        r = c.delete("/model/override")
    assert r.status_code == 200
    assert r.json() == {"override_active": False}
    assert services.state.model_override is None
    assert services.state.override_locked is False


def test_override_status_endpoint_returns_full_payload(tmp_path):
    mp = _MockProvider(models=["mock-7b", "mock-3b"])
    app, _services = _override_app(tmp_path, mock_provider=mp)
    with TestClient(app) as c:
        c.post("/model/override", json={"model": "primary", "lock": True})
        r = c.get("/model/status")
    assert r.status_code == 200
    body = r.json()
    assert body["override"] == "primary"
    assert body["locked"] is True
    assert body["available"] == ["mock-7b", "mock-3b"]
    assert "active_model" in body and "backend" in body


def test_override_rejects_unknown_raw_name_when_backend_lists_models(tmp_path):
    """Raw provider name not in /list_models → 422, state unchanged."""
    mp = _MockProvider(models=["mock-7b"])
    app, services = _override_app(tmp_path, mock_provider=mp)
    with TestClient(app) as c:
        r = c.post("/model/override", json={"model": "ghost-99b"})
    assert r.status_code == 422
    assert services.state.model_override is None


def test_override_accepts_raw_name_when_backend_unreachable(tmp_path):
    """If /list_models returns empty (offline), accept raw names — Law 3 still
    holds because it's an explicit user action."""
    mp = _MockProvider(models=[])
    app, services = _override_app(tmp_path, mock_provider=mp)
    with TestClient(app) as c:
        r = c.post("/model/override", json={"model": "experimental:x"})
    assert r.status_code == 200, f"unexpected {r.status_code}: {r.text}"
    assert services.state.model_override == "experimental:x"


def test_override_drift_anchor_prefix_unchanged(tmp_path):
    """STABLE_SYSTEM_PREFIX is identical before and after override is set."""
    from backend.loop.agentic_loop import STABLE_SYSTEM_PREFIX
    snapshot = STABLE_SYSTEM_PREFIX
    app, _services = _override_app(tmp_path, mock_provider=_MockProvider(models=[]))
    with TestClient(app) as c:
        c.post("/model/override", json={"model": "code"})
        c.post("/model/override", json={"model": "primary", "lock": True})
        c.delete("/model/override")
    from backend.loop.agentic_loop import STABLE_SYSTEM_PREFIX as after
    assert after is snapshot


# ===================================================================
# Pre-Phase-9 regression spot-checks
# ===================================================================

def test_assemble_context_still_returns_AssembledContext():
    """Fix 2 intact — return is the dataclass, not a tuple."""
    from backend.loop.agentic_loop import assemble_context
    from backend.loop.state import AssembledContext
    ctx = assemble_context("hello", None, hippocampus=None)
    assert isinstance(ctx, AssembledContext)
    assert ctx.block == ""


def test_session_memory_writes_4_tuples():
    """Fix 3 intact — current_session_weight() expects 4-tuples."""
    from backend.loop.compaction import _current_session_weight
    import time as _time
    now = _time.time()
    triple = ("a", "b", 0.5, now)
    assert _current_session_weight(triple, now=now) == 0.5


def test_run_cascade_still_takes_rebuild_block_fn():
    """Fix 1 intact — run_cascade signature carries rebuild_block_fn kwarg."""
    import inspect as _inspect
    from backend.loop.compaction import run_cascade
    sig = _inspect.signature(run_cascade)
    assert "rebuild_block_fn" in sig.parameters


def test_create_app_still_isolates_per_call(tmp_path):
    """Fix 4 intact — two create_app() calls yield independent services."""
    a = create_app(create_services(kb_db_path=tmp_path / "a.db",
                                    chroma_dir=tmp_path / "ca", snapshots_dir=tmp_path / "sa"))
    b = create_app(create_services(kb_db_path=tmp_path / "b.db",
                                    chroma_dir=tmp_path / "cb", snapshots_dir=tmp_path / "sb"))
    assert a.state.services is not b.state.services
    assert a.state.services.provider is not b.state.services.provider


# ===================================================================
# Fix 4 — is_pulled field on ModelProfile + sync_pulled_state
# ===================================================================

import asyncio as _asyncio_fix4
import dataclasses as _dc_fix4

from backend.router.model_registry import ModelProfile as _MP_fix4
from backend.router.model_registry import ModelRegistry as _MR_fix4
from backend.router.capabilities import Capability as _Cap_fix4


def _profile(name: str, *, is_pulled: bool = False) -> _MP_fix4:
    return _MP_fix4(
        name=name, display_name=name,
        capabilities=(_Cap_fix4.GENERAL_REASONING,),
        vram_gb=1.0, tokens_per_sec=10.0, context_window=4096,
        pull_size_gb=1.0, is_pulled=is_pulled,
    )


def test_model_profile_has_is_pulled_default_false():
    p = _profile("foo:1b")
    assert hasattr(p, "is_pulled")
    assert p.is_pulled is False


def test_model_profile_is_frozen_so_is_pulled_must_be_replaced(monkeypatch):
    p = _profile("foo:1b")
    with pytest.raises(_dc_fix4.FrozenInstanceError):
        p.is_pulled = True   # type: ignore[misc]


def test_sync_pulled_state_flips_matching_models_via_replace():
    reg = _MR_fix4()
    reg.register(_profile("qwen2.5-coder:7b-q4_K_M"))
    reg.register(_profile("phi4-mini:3.8b-q4_K_M"))
    reg.register(_profile("hypothetical:99b"))   # NOT in available list

    class _Stub:
        async def list_models(self):
            return ["qwen2.5-coder:7b-q4_K_M", "phi4-mini:3.8b-q4_K_M"]

    updated = _asyncio_fix4.run(reg.sync_pulled_state(_Stub()))
    assert set(updated) == {"qwen2.5-coder:7b-q4_K_M", "phi4-mini:3.8b-q4_K_M"}
    assert reg.by_name("qwen2.5-coder:7b-q4_K_M").is_pulled is True
    assert reg.by_name("phi4-mini:3.8b-q4_K_M").is_pulled is True
    assert reg.by_name("hypothetical:99b").is_pulled is False


def test_sync_pulled_state_is_idempotent():
    reg = _MR_fix4()
    reg.register(_profile("m:1b"))

    class _Stub:
        async def list_models(self): return ["m:1b"]

    first  = _asyncio_fix4.run(reg.sync_pulled_state(_Stub()))
    second = _asyncio_fix4.run(reg.sync_pulled_state(_Stub()))
    assert first == ["m:1b"]
    assert second == []   # already pulled — no new flips


def test_sync_pulled_state_returns_empty_on_provider_failure():
    reg = _MR_fix4()
    reg.register(_profile("m:1b"))

    class _BoomProvider:
        async def list_models(self):
            raise RuntimeError("Ollama down")

    out = _asyncio_fix4.run(reg.sync_pulled_state(_BoomProvider()))
    assert out == []
    assert reg.by_name("m:1b").is_pulled is False   # untouched on failure
