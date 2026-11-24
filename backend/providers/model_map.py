"""Canonical NEXUS roles → provider-specific model names (Fix 0 / Part B).

The kernel selects a Capability; the loop maps Capability → role string;
resolve(role) returns the provider-specific name. Model names never appear
in routing logic (Law 2) — they live only here, as data.
"""
from __future__ import annotations

from backend.providers.backends import backend_name
from backend.router.capabilities import Capability


MODEL_MAP: dict[str, dict[str, str]] = {
    "ollama": {
        "primary":    "deepseek-r1:14b-qwen-distill-q4_K_M",
        "code":       "qwen2.5-coder:7b",
        "extraction": "phi4-mini:3.8b-q4_K_M",
        "multimodal": "qwen3.5:9b-q4_K_M",
        "reasoning":  "qwen3:8b",
        # The same tag as `multimodal`, reachable as an ANSWERING model via
        # explicit override — the `reasoning` precedent. Auto-routing for a
        # general turn still emits `primary` (capability_to_role below), so
        # the default answerer is never displaced; Law V's ceiling is
        # enforced at stream_chat regardless of what the model advertises.
        "general":    "qwen3.5:9b-q4_K_M",
        "embed":      "nomic-embed-text",
    },
    "lmstudio": {
        "primary":    "DeepSeek-R1-Distill-Qwen-14B-Q4_K_M",
        "code":       "Qwen2.5-Coder-7B-Instruct-Q4_K_M",
        "extraction": "Phi-4-Mini-Instruct-Q4_K_M",
        "multimodal": "Qwen2.5-VL-7B-Instruct-Q4_K_M",
        "embed":      "nomic-embed-text-v1.5-Q4_0",
    },
    # W3 — the stub provider's roster (backend/providers/stub.py, STUB_ROLES:
    # the two tables are held equal by test). Names of a provider that runs no
    # model, so a record made under it reads `stub:<role>` and nothing else.
    "stub": {
        "primary":    "stub:primary",
        "code":       "stub:code",
        "extraction": "stub:extraction",
        "multimodal": "stub:multimodal",
        "reasoning":  "stub:primary",
        "general":    "stub:primary",
        "embed":      "stub:embed",
    },
}

# Canonical roles the override endpoint accepts. `reasoning` (qwen3:8b) and
# `general` (qwen3.5:9b) are opt-in alternatives reachable only via explicit
# override — auto-routing never emits either, so the default primary
# (DeepSeek R1) is never displaced.
ROLES: frozenset[str] = frozenset({"primary", "code", "extraction",
                                   "multimodal", "reasoning", "general",
                                   "embed"})


# --------------------------------------------------------------------------
# W.2 — embedding task prefixes, per model, as data
# --------------------------------------------------------------------------
# MEASURED ON THE OPERATOR'S MACHINE, nomic-embed-text:
#
#     bare      signal 0.4047   control 0.2570   gap +0.1477
#     prefixed  signal 0.5251   control 0.2570   gap +0.2681
#
# The nomic family is trained with mandatory task prefixes and its query and
# document sides are DIFFERENT — embedding both with the same un-prefixed call
# throws away the asymmetry the model was trained for. We had been doing
# exactly that since Phase 7.
#
# **A PREFIX IS NOT A GENERAL IMPROVEMENT, IT IS A PER-MODEL CONTRACT.**
# Every family disagrees about what to prepend, and prepending the wrong thing
# is worse than prepending nothing — the tokens become content the model tries
# to represent:
#
#     nomic-embed-text*        search_query: / search_document:   (used here)
#     intfloat/e5-*            query: / passage:                  (different)
#     BAAI/bge-*               an instruction on the QUERY only   (different)
#     all-MiniLM / gte / mxbai none — prefixes are noise          (harmful)
#
# So the table is keyed by model name and **the default is no prefix**, which
# is byte-identical to pre-W.2 behaviour. A model nobody has entered here is
# not silently given nomic's contract; it is left alone. Adding a family is a
# data change, next to the names it belongs with (Law 2: model names live only
# in this module).
#
# Keys are matched as a case-insensitive PREFIX of the resolved model name, so
# `nomic-embed-text`, `nomic-embed-text-v1.5-Q4_0` and a future
# `nomic-embed-text:latest` all match one entry.
EMBED_PREFIXES: dict[str, dict[str, str]] = {
    "nomic-embed-text": {"query":    "search_query: ",
                         "document": "search_document: "},
}

EMBED_KINDS: frozenset[str] = frozenset({"query", "document"})


# --------------------------------------------------------------------------
# C-38 — reasoning separation, per model, as data
# --------------------------------------------------------------------------
# A thinking model's reply has two parts: the reasoning it produced for
# itself and the answer it stated. Where the daemon separates them (Ollama's
# `think` option returns `message.thinking` beside `message.content`) the
# provider reads the separate field; where the model's tag pair arrives
# inline in `content` anyway, the provider parses THAT MODEL'S pair out of
# the stream. Both are keyed here, by model name, as data — never a global
# regex: a model with no entry has its content left exactly as it arrived,
# even when it contains the literal text `<think>` in prose. Keys are matched
# as a case-insensitive PREFIX of the resolved model name, like
# EMBED_PREFIXES, so the Ollama tag and the LM Studio file name share one
# entry where their names share a stem.
THINK_TAGS: dict[str, tuple[str, str]] = {
    "deepseek-r1": ("<think>", "</think>"),
    "qwen3":       ("<think>", "</think>"),
}
# The models the Ollama provider would ask for separation from (`think: true`).
# EMPTY, deliberately, and the reason is a fact found while establishing
# C-38 (the daemon's source, bisected by tag): since v0.12.4 (3 October 2025)
# the daemon defaults the separation ON for every model whose template
# carries the thinking capability, which is why the operator's 4 October
# check saw a separate field without asking; and since v0.12.5 the daemon
# answers `think: true` for a model WITHOUT that capability — an old
# deepseek-r1 manifest pulled before the capability existed — with HTTP 400
# "does not support thinking", which the provider would deliver as the
# answer. So NEXUS reads `message.thinking` whenever the daemon sends it
# and asks for nothing; the inline tag pair above is the second line of
# defence for a daemon older than v0.12.4, which puts the tags in content.
# Naming a model here sends the option for it; nothing is named.
THINK_OPTION: frozenset[str] = frozenset()


def think_tags(model: str) -> tuple[str, str] | None:
    """The (open, close) tag pair this model emits around its reasoning, or
    None — in which case nothing in the content is ever treated as reasoning."""
    name = (model or "").strip().lower()
    for key, pair in THINK_TAGS.items():
        if name.startswith(key.lower()):
            return pair
    return None


def think_option(model: str) -> bool:
    """Whether the Ollama request carries `think: true` for this model.
    False for every model today; see THINK_OPTION."""
    name = (model or "").strip().lower()
    return any(name.startswith(key.lower()) for key in THINK_OPTION)


def embed_prefix(model: str, kind: str) -> str:
    """The prefix this model expects for this side of the pair, or "".

    Unknown model, unknown kind, or a model with no entry -> "", which is
    exactly what every call did before W.2.
    """
    if kind not in EMBED_KINDS:
        return ""
    name = (model or "").strip().lower()
    for key, table in EMBED_PREFIXES.items():
        if name.startswith(key.lower()):
            return table.get(kind, "")
    return ""


def resolve(role: str, backend: str | None = None) -> str:
    """Return the provider-specific model name for a canonical role.

    Falls back to the ollama mapping when `backend` is unknown, and to
    "primary" when the role is unknown — both are honest defaults that
    keep the system functional rather than crashing.
    """
    # W3 — the environment's name is read as the factory reads it (stripped,
    # lowered); an explicit `backend` argument is taken as given.
    b = (backend or backend_name()).lower()
    table = MODEL_MAP.get(b)
    if table is None:
        # W3 part 2 — an api backend's names are the environment's
        # (`OPENAI_MODEL`, `ANTHROPIC_MODEL`, `NEXUS_EMBED_MODEL`: the operator
        # names the model a bill attaches to), read at call time, never a
        # compiled table. None for any other name, which keeps the fallback.
        from backend.providers.api_settings import api_model_table
        table = api_model_table(b)
    if table is None:
        table = MODEL_MAP["ollama"]
    return table.get(role, table.get("primary", MODEL_MAP["ollama"]["primary"]))


def answering_roles(registry, backend: str | None = None) -> frozenset[str]:
    """AR.4 — the roles `/model/override` may pin as the ANSWERING model,
    derived from the registry's capability declarations.

    For each role, resolve its provider name and ask the registry what that
    model declares: a profile with any answering capability (see
    `capabilities.NON_ANSWERING` for the two that are not) makes the role
    pinnable. The registry is the single source of truth for what a model
    can do, so a tenth model needs no list edit here — an embed-only
    profile is refused the moment it is registered, and a role whose tag is
    re-registered with an answering capability is accepted the same way.

    FALLBACK, declared: when the registry holds no profile for the resolved
    name (the lmstudio table has none in the default registry), the only
    fact left is which capability the role is the image of under
    `capability_to_role`, so roles for NON_ANSWERING capabilities are
    refused and the rest accepted. Registering profiles for that backend
    is what removes the fallback.

    Consequence stated on purpose: `extraction` resolves (ollama) to a
    profile that also declares GENERAL_REASONING, so it stays pinnable — a
    small chat model answering is a choice, not a category error.
    """
    from backend.router.capabilities import NON_ANSWERING, can_answer
    fallback_refused = {capability_to_role(c) for c in NON_ANSWERING}
    out = set()
    for role in ROLES:
        name = resolve(role, backend)
        profile = registry._models.get(name) if hasattr(registry, "_models") else None
        if profile is None:
            if role not in fallback_refused:
                out.add(role)
        elif can_answer(profile.capabilities):
            out.add(role)
    return frozenset(out)


def capability_to_role(cap: Capability) -> str:
    """Translate a kernel-emitted Capability into the role string used by
    the model map. Keeps Law 2 intact — kernel speaks capabilities, the
    map speaks roles, and the provider speaks names."""
    if cap in (Capability.CODE_GENERATION, Capability.CODE_REVIEW):
        return "code"
    if cap is Capability.FAST_EXTRACTION:
        return "extraction"
    if cap is Capability.MULTIMODAL:
        return "multimodal"
    if cap is Capability.EMBEDDING:
        return "embed"
    return "primary"
