"""W3 — the values `LLM_BACKEND` may take, read one way everywhere.

Three readers used to read the setting apart: the factory lowered it and did
not strip it, `model_map.resolve` the same, and `start.py` stripped it for
its warning; and the factory returned Ollama for ANY value it did not know
(`api`, `nonsense`, `lmstudio ` with a trailing space), named nowhere (W3's
establish, H2; the register line W0b's refutation readers left). Now one
function reads the name, the factory refuses an unknown one by name, and
`start.py` refuses it first, as a usage error (its exit 2).

Part 2 adds the two `api` backends (run/specs/W3.md D3): `openai`, an
OpenAI-compatible chat-completions endpoint (`backend/providers/openai_compat.py`),
and `anthropic`, the Messages API (`backend/providers/anthropic.py`). Each
needs the model it is to ask for, named by the operator (`OPENAI_MODEL`,
`ANTHROPIC_MODEL`: NEXUS never picks a model a bill attaches to), and a
missing one is refused at startup the way an unknown name is
(`MissingSetting`, the same exit 2, nothing opened). The settings themselves
are read by `backend/providers/api_settings.py`.

Standard library only: `start.py` imports this before its dependency check,
so a machine missing httpx must still be told so by the check, never by an
ImportError from here.
"""
from __future__ import annotations

import os

BACKEND_SETTING = "LLM_BACKEND"
OLLAMA, LMSTUDIO, STUB, OPENAI, ANTHROPIC = "ollama", "lmstudio", "stub", "openai", "anthropic"
# In the order the documents list them. `stub` (W3, D2) is the deterministic
# provider the `stub` compose profile and CI run on: no model server. `openai`
# and `anthropic` (W3, D3) are the `api` compose profile's two providers.
KNOWN_BACKENDS = (OLLAMA, LMSTUDIO, STUB, OPENAI, ANTHROPIC)
API_BACKENDS = (OPENAI, ANTHROPIC)
# The one setting each api backend cannot run without: the model it asks for.
REQUIRED_SETTINGS = {OPENAI: ("OPENAI_MODEL", "the chat model the OpenAI-compatible endpoint serves"),
                     ANTHROPIC: ("ANTHROPIC_MODEL", "the Claude model the Messages API is asked for")}
# The embedder the api backends may name (both or neither; api_settings reads them).
EMBED_URL_SETTING, EMBED_MODEL_SETTING = "NEXUS_EMBED_URL", "NEXUS_EMBED_MODEL"
# start.py's EXIT_USAGE, the same number: a wrong setting is a usage error.
EXIT_UNKNOWN_BACKEND = 2


class BackendConfigError(ValueError):
    """The backend settings do not describe a provider NEXUS can construct.
    Raised by the factory; the console's factory call and MCP's main print it
    and leave with `EXIT_UNKNOWN_BACKEND`, having opened nothing."""


class UnknownBackend(BackendConfigError):
    """`LLM_BACKEND` names no backend NEXUS has."""


class MissingSetting(BackendConfigError):
    """`LLM_BACKEND` names an api backend whose required setting is unset, or
    the embedder is named by one of its two settings and not the other."""


def backend_name(environ=None) -> str:
    """The setting, stripped and lowered; unset or empty is `ollama`, as it
    always was."""
    environ = os.environ if environ is None else environ
    return (environ.get(BACKEND_SETTING) or "").strip().lower() or OLLAMA


def unknown_backend_reason(environ=None) -> str | None:
    """The refusal's text when the setting names no known backend, else
    None. Names the value as set and every name NEXUS knows."""
    environ = os.environ if environ is None else environ
    name = backend_name(environ)
    if name in KNOWN_BACKENDS:
        return None
    return (f"{BACKEND_SETTING}={environ.get(BACKEND_SETTING)!r} is not a backend NEXUS "
            f"knows ({', '.join(KNOWN_BACKENDS)})")


def missing_setting_reason(environ=None) -> str | None:
    """The refusal's text when a known api backend lacks its model, or the
    embedder is half named, else None. Nothing is guessed in its place."""
    environ = os.environ if environ is None else environ
    name = backend_name(environ)
    if name not in API_BACKENDS:
        return None
    setting, what = REQUIRED_SETTINGS[name]
    if not (environ.get(setting) or "").strip():
        return (f"{BACKEND_SETTING}={name} needs {setting} ({what}); set it in .env or the "
                f"environment. NEXUS names no model for you.")
    url, model = (environ.get(EMBED_URL_SETTING) or "").strip(), (environ.get(EMBED_MODEL_SETTING) or "").strip()
    if bool(url) != bool(model):
        set_one, unset = ((EMBED_URL_SETTING, EMBED_MODEL_SETTING) if url else (EMBED_MODEL_SETTING, EMBED_URL_SETTING))
        return (f"{set_one} is set without {unset}: the two name the embedder together (an "
                f"OpenAI-compatible /embeddings endpoint and its model), or neither names one")
    return None


def backend_problem(environ=None) -> str | None:
    """Why the factory would refuse these settings, else None: an unknown
    name first, then a missing setting. One text for start.py's checks and
    the factory's exceptions (`config_error`)."""
    return unknown_backend_reason(environ) or missing_setting_reason(environ)


def config_error(environ=None) -> BackendConfigError | None:
    """The exception the factory raises for these settings, else None."""
    reason = unknown_backend_reason(environ)
    if reason is not None:
        return UnknownBackend(reason)
    reason = missing_setting_reason(environ)
    if reason is not None:
        return MissingSetting(reason)
    return None


__all__ = ["ANTHROPIC", "API_BACKENDS", "BACKEND_SETTING", "BackendConfigError", "EMBED_MODEL_SETTING",
           "EMBED_URL_SETTING", "EXIT_UNKNOWN_BACKEND", "KNOWN_BACKENDS", "LMSTUDIO", "MissingSetting",
           "OLLAMA", "OPENAI", "REQUIRED_SETTINGS", "STUB", "UnknownBackend", "backend_name",
           "backend_problem", "config_error", "missing_setting_reason", "unknown_backend_reason"]
