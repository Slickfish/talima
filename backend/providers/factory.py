"""Provider factory — single source of truth for backend selection.

`get_provider()` reads `LLM_BACKEND` (`backend/providers/backends.py`: one
reader, stripped and lowered) and returns the one provider that name means:
`ollama` (the default), `lmstudio`, `stub` (W3: the deterministic provider
for the `stub` compose profile and CI), `openai` (an OpenAI-compatible
chat-completions endpoint) or `anthropic` (the Messages API), the last two
W3 part 2's `api` providers. Any other value is refused by name,
`UnknownBackend`, at startup: before W3 every unknown value silently
selected Ollama (the register line; W3's establish, H2). An api backend
whose model is unset (`OPENAI_MODEL`, `ANTHROPIC_MODEL`), or an embedder
named by one of its two settings, is refused the same way (`MissingSetting`;
both are `BackendConfigError`): NEXUS names no model for the operator.

`PROVIDER`, the module-level default for callers that carry no
NexusServices container (legacy paths: `agentic_loop`'s and `chroma`'s
fallbacks), is resolved on its first read (`default_provider`), not at
import: an import-time read would make an unknown setting a traceback from
whatever imported the package first, where the console's factory call and
MCP's main print the refusal and leave with its exit. New code resolves
providers through `services.provider`, so create_app() can give each test
its own.
"""
from __future__ import annotations

from backend.providers.backends import (ANTHROPIC, LMSTUDIO, OLLAMA, OPENAI, STUB, UnknownBackend, backend_name,
                                        config_error, unknown_backend_reason)
from backend.providers.base import LLMProvider
from backend.providers.lmstudio import LMStudioProvider
from backend.providers.ollama import OllamaProvider


def get_provider() -> LLMProvider:
    error = config_error()
    if error is not None:
        raise error
    name = backend_name()
    if name == LMSTUDIO:
        return LMStudioProvider()
    if name == STUB:
        from backend.providers.stub import StubProvider
        return StubProvider()
    if name == OPENAI:
        from backend.providers.openai_compat import OpenAICompatProvider
        return OpenAICompatProvider()
    if name == ANTHROPIC:
        from backend.providers.anthropic import AnthropicProvider
        return AnthropicProvider()
    if name == OLLAMA:
        return OllamaProvider()
    raise UnknownBackend(unknown_backend_reason())                 # pragma: no cover — config_error refused it


_default: LLMProvider | None = None


def default_provider() -> LLMProvider:
    """The legacy module-level default (`PROVIDER`), resolved once, on first
    use."""
    global _default
    if _default is None:
        _default = get_provider()
    return _default


def __getattr__(name: str):
    if name == "PROVIDER":
        return default_provider()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
