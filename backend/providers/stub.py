"""W3 — the stub provider: `LLM_BACKEND=stub`, a deterministic provider for
the `stub` compose profile, CI's deploy smoke and any run with no model
server (run/specs/W3.md D2; the charter's decision 10, "model for CI: the
stub provider").

Until W3 no stub existed in `backend/`: the name appeared in one docstring
(`create_services`), the real stubs lived in tests and scripts, and the
factory gave Ollama for every `LLM_BACKEND` but exactly `lmstudio`, so the
profile the charter named had nothing to run (W3's establish, H1).

What it is, and says it is:

- **One fixed answer**, the same bytes every call, naming itself and the
  model it was asked for, so a record made under it can never be read as a
  model's. Asked for its extraction model it answers `[]`: a valid empty
  extraction, which the loop records as zero triples measured, never as a
  failure. The console's turn therefore completes and seals a record whose
  `model_id` is `stub:<role>`.
- **No seed** (`takes_seed` False): the record's `inference_seed` is NULL
  with W0b's reason, "provider takes no seed". Its request options are
  recorded (`request_config`), its digest and model-server facts are absent
  with their reasons, never guessed.
- **No embedder** (`has_embedder` False): `get_embedding` raises, so the
  embedding chokepoint (`backend/vector/embeddings.py`) substitutes its hash
  vector and DECLARES the degradation on `/healthz` (`embedding_degraded`,
  the monotone `embedding_fallbacks`). A stub that returned hash vectors as
  embeddings would report a healthy embedder over noise (law 5). Part 2 reads
  `has_embedder` to stop the dedup scan calling an embedder a provider says
  it has not (H5).
- **Its own roster**: `model_registry()` is the registry the kernel routes
  on under this provider (`create_services` asks the provider for one and
  falls back to the default roster), one profile per role named
  `stub:<role>`, every one installed, so routing, extraction and the record
  name the stub and nothing else. `model_map.MODEL_MAP["stub"]` maps the
  roles to the same names.

Every network-free answer here is in-process: the smoke's network check
(W2's `scripts/bundle_smoke.py` S3) and W3's `verify_deploy.py` see no call
from it.
"""
from __future__ import annotations

from typing import AsyncGenerator

from backend.providers.base import LLMProvider, reply_separation_enabled
from backend.providers.ollama import NUM_CTX

STUB = "stub"
# The roster: role -> (name, capabilities). `primary` carries every answering
# capability the default roster's primary carries, so the kernel's best_for
# lands on it for a general turn, and `code` wins code turns as the default
# roster's coder does (position 0 is the primary capability).
STUB_PRIMARY, STUB_CODE, STUB_EXTRACTION, STUB_MULTIMODAL, STUB_EMBED = (
    "stub:primary", "stub:code", "stub:extraction", "stub:multimodal", "stub:embed")
STUB_ROLES = {"primary": STUB_PRIMARY, "code": STUB_CODE, "extraction": STUB_EXTRACTION,
              "multimodal": STUB_MULTIMODAL, "reasoning": STUB_PRIMARY, "general": STUB_PRIMARY,
              "embed": STUB_EMBED}
EMPTY_EXTRACTION = "[]"


def stub_answer(model: str) -> str:
    """The one fixed answer: for the extraction model a valid empty
    extraction; for every other model a sentence naming the stub and the
    model asked for, the same bytes every call."""
    if model == STUB_EXTRACTION:
        return EMPTY_EXTRACTION
    return (f"This is the fixed answer of NEXUS's stub provider (LLM_BACKEND=stub), "
            f"asked for model {model}. No language model ran: the stub answers in-process, "
            f"deterministically, so this record is a record of the stub.")


class StubProvider(LLMProvider):
    takes_seed = False
    has_embedder = False
    # W3 part 2 reads this beside `has_embedder` (H5): the reason the idle
    # scans give for not asking, on the cycle result and /cortex/stats.embedder.
    no_embedder_reason = ("LLM_BACKEND=stub: the stub provider has no embedder; the embedding chokepoint "
                          "declares the fallback on /healthz when anything asks it to embed")

    def _answer(self, model: str, num_ctx: int) -> str:
        # Law 5's ceiling refuses here as Ollama's does; it supplies nothing.
        if num_ctx > NUM_CTX:
            raise ValueError(f"num_ctx {num_ctx} exceeds Law V ceiling {NUM_CTX}")
        return stub_answer(model)

    def request_config(self, model: str, num_ctx: int, *, separate: bool) -> dict:
        """W0b — the options a record keeps: no endpoint, no seed, the model
        and the context size asked for."""
        return {"provider": STUB, "endpoint": None, "model": model, "num_ctx": int(num_ctx)}

    async def served_model_digest(self, model: str) -> tuple[str | None, str]:
        return None, "the stub provider has no model weights (LLM_BACKEND=stub)"

    async def inference_environment(self) -> dict:
        return {"model_server_absent": "LLM_BACKEND=stub: no model server; the stub answers in-process"}

    async def stream_chat(
        self, model: str, messages: list[dict], num_ctx: int,
    ) -> AsyncGenerator[str, None]:
        yield self._answer(model, num_ctx)

    async def stream_pieces(
        self, model: str, messages: list[dict], num_ctx: int, *,
        separate: bool | None = None, seed: int | None = None,
    ) -> AsyncGenerator[tuple[str, str], None]:
        """C-38's shape: the answer as one content piece; the stub has no
        reasoning channel. `separate` changes nothing here (there is nothing
        to separate), and `seed` is never sent (`takes_seed` is False)."""
        if separate is None:
            separate = reply_separation_enabled()
        yield "content", self._answer(model, num_ctx)

    async def get_embedding(self, text: str) -> list[float]:
        raise NotImplementedError(
            "the stub provider has no embedder (LLM_BACKEND=stub); the embedding chokepoint "
            "declares the fallback on /healthz")

    async def list_models(self) -> list[str]:
        return [p.name for p in self.model_registry().all()]

    async def health(self) -> bool:
        return True

    @staticmethod
    def model_registry():
        """The roster the kernel routes on under this provider: five profiles,
        every one installed, no VRAM, named `stub:<role>`."""
        from backend.router.capabilities import Capability as C
        from backend.router.model_registry import ModelProfile, ModelRegistry
        registry = ModelRegistry()
        for name, caps, note in (
            (STUB_PRIMARY, (C.GENERAL_REASONING, C.ADVANCED_REASONING, C.GRAPH_SYNTHESIS,
                            C.CODE_GENERATION), "the stub's answering model"),
            (STUB_CODE, (C.CODE_GENERATION, C.CODE_REVIEW, C.GENERAL_REASONING), "the stub's code model"),
            (STUB_EXTRACTION, (C.FAST_EXTRACTION,), "the stub's extractor: answers [] (zero triples, measured)"),
            (STUB_MULTIMODAL, (C.MULTIMODAL, C.GENERAL_REASONING), "the stub's multimodal model"),
            (STUB_EMBED, (C.EMBEDDING,), "the stub's embedder: has none; the chokepoint declares the fallback"),
        ):
            registry.register(ModelProfile(
                name=name, display_name=name, capabilities=caps, vram_gb=0.0, tokens_per_sec=0.0,
                context_window=NUM_CTX, pull_size_gb=0.0, is_pulled=True, notes=note))
        return registry


__all__ = ["EMPTY_EXTRACTION", "STUB", "STUB_CODE", "STUB_EMBED", "STUB_EXTRACTION", "STUB_MULTIMODAL",
           "STUB_PRIMARY", "STUB_ROLES", "StubProvider", "stub_answer"]
