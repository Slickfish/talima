"""Provider abstraction (Fix 0).

Every backend-touching call in NEXUS goes through a single LLMProvider
implementation. Switch backends with the LLM_BACKEND env var (`ollama`,
`lmstudio`, `stub`, W3's deterministic provider for CI and the `stub`
compose profile, `openai`, an OpenAI-compatible endpoint, or `anthropic`,
the Messages API, W3 part 2's `api` providers; any other value is refused
by name at startup, and so is an api backend whose model is unset); never
branch on backend name in business logic.

`PROVIDER` is the legacy module-level default, resolved on its first read
(W3): `from backend.providers import PROVIDER` still works, and reads the
provider the environment names at that moment.
"""
from .backends import BackendConfigError, KNOWN_BACKENDS, MissingSetting, UnknownBackend, backend_name
from .base import LLMProvider
from .factory import default_provider, get_provider


def __getattr__(name: str):
    if name == "PROVIDER":
        return default_provider()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["BackendConfigError", "KNOWN_BACKENDS", "LLMProvider", "MissingSetting", "PROVIDER", "UnknownBackend",
           "backend_name", "default_provider", "get_provider"]
