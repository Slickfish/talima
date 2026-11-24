"""W3 part 2 — the settings of the two `api` backends, read in one place
(run/specs/W3.md D3; RUN_WORKLIST W3: "the key from the environment").

The names are the SDKs' own where one exists, so an operator's existing
environment works unchanged: `OPENAI_BASE_URL` and `OPENAI_API_KEY`,
`ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY`. The model each backend asks
for is the operator's choice and has no default (`OPENAI_MODEL`,
`ANTHROPIC_MODEL`: the factory refuses a backend whose model is unset,
`backends.MissingSetting`); extraction may name a cheaper model
(`*_EXTRACTION_MODEL`, default the answering model). The embedder is
separate, because Anthropic offers none and an OpenAI-compatible endpoint
may not serve one: `NEXUS_EMBED_URL` (an OpenAI-compatible base URL;
`/embeddings` is appended) and `NEXUS_EMBED_MODEL`, both or neither, with
`NEXUS_EMBED_API_KEY` when that endpoint wants its own key. With neither,
the backend declares `has_embedder` False and the idle scans do not call it
(H5; `backend/memory/kairos.py`).

Every value is read at call time, never at import (the dotenv sentinel,
`tests/test_dotenv_settings_take_effect.py`, probes each one). The three
keys are read here and nowhere else in `backend/`, by AST guard
(`tests/test_w3_api_providers.py`): a key that appears in one function
cannot leak into a record, a log or a request body by accident. Standard
library only: `start.py` reads the URLs for its model-server check before
its dependency check.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from backend.providers.backends import (
    ANTHROPIC, API_BACKENDS, EMBED_MODEL_SETTING, EMBED_URL_SETTING, OPENAI, REQUIRED_SETTINGS,
    backend_name)

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
# The Messages API version header every request carries (start.py's model
# check sends it too, so it lives in this standard-library module).
ANTHROPIC_VERSION = "2023-06-01"
EMBED_KEY_SETTING = "NEXUS_EMBED_API_KEY"
# What `resolve()` returns for a role whose model nothing names: a string no
# endpoint serves, so a record or a route that shows it cannot be misread as
# a model. The factory refuses the backend before any request carries it.
UNSET_MODEL = "<{setting} unset>"
NO_EMBED_MODEL = f"<no embedder: {EMBED_MODEL_SETTING} unset>"


def _read(environ, key: str, default: str = "") -> str:
    return (environ.get(key) or "").strip() or default


@dataclass(frozen=True)
class ApiSettings:
    backend: str
    base_url: str
    api_key: str              # "" when none is set (a local endpoint may need none)
    model: str                # UNSET_MODEL's text when nothing names it
    extraction_model: str     # the answering model unless named apart

    @property
    def model_setting(self) -> str:
        return REQUIRED_SETTINGS[self.backend][0]

    @property
    def configured(self) -> bool:
        return self.model != UNSET_MODEL.format(setting=self.model_setting)

    @property
    def host(self) -> str:
        """The endpoint's host, for a record: never the URL as set, which
        could carry credentials."""
        return urlsplit(self.base_url).hostname or self.base_url


@dataclass(frozen=True)
class EmbedSettings:
    url: str
    model: str
    api_key: str

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or self.url


def openai_settings(environ=None) -> ApiSettings:
    environ = os.environ if environ is None else environ
    model = _read(environ, "OPENAI_MODEL", UNSET_MODEL.format(setting="OPENAI_MODEL"))
    return ApiSettings(backend=OPENAI,
                       base_url=_read(environ, "OPENAI_BASE_URL", OPENAI_DEFAULT_BASE_URL).rstrip("/"),
                       api_key=_read(environ, "OPENAI_API_KEY"),
                       model=model,
                       extraction_model=_read(environ, "OPENAI_EXTRACTION_MODEL", model))


def anthropic_settings(environ=None) -> ApiSettings:
    environ = os.environ if environ is None else environ
    model = _read(environ, "ANTHROPIC_MODEL", UNSET_MODEL.format(setting="ANTHROPIC_MODEL"))
    return ApiSettings(backend=ANTHROPIC,
                       base_url=_read(environ, "ANTHROPIC_BASE_URL", ANTHROPIC_DEFAULT_BASE_URL).rstrip("/"),
                       api_key=_read(environ, "ANTHROPIC_API_KEY"),
                       model=model,
                       extraction_model=_read(environ, "ANTHROPIC_EXTRACTION_MODEL", model))


def api_settings(backend: str, environ=None) -> ApiSettings:
    if backend == OPENAI:
        return openai_settings(environ)
    if backend == ANTHROPIC:
        return anthropic_settings(environ)
    raise ValueError(f"{backend!r} is not an api backend: {API_BACKENDS}")


def embed_url(environ=None) -> str:
    environ = os.environ if environ is None else environ
    return _read(environ, EMBED_URL_SETTING).rstrip("/")


def embed_model(environ=None) -> str:
    environ = os.environ if environ is None else environ
    return _read(environ, EMBED_MODEL_SETTING)


def embed_api_key(environ=None) -> str:
    environ = os.environ if environ is None else environ
    return _read(environ, EMBED_KEY_SETTING)


def embed_settings(environ=None) -> EmbedSettings | None:
    """The embedder the api backends share, or None when neither setting
    names one. One without the other is the factory's refusal
    (`backends.missing_setting_reason`), never a half-built embedder."""
    url, model = embed_url(environ), embed_model(environ)
    if url and model:
        return EmbedSettings(url=url, model=model, api_key=embed_api_key(environ))
    return None


def api_model_table(backend: str | None = None, environ=None) -> dict[str, str] | None:
    """`model_map.MODEL_MAP`'s shape for an api backend, from the environment
    at call time: every answering role is the one model the operator named,
    `extraction` its extraction model, `embed` the embedder's model or a
    sentinel no endpoint serves. None for a backend that is not an api one,
    so `resolve()` keeps its own tables and its own fallback for those."""
    name = (backend or backend_name(environ)).strip().lower()
    if name not in API_BACKENDS:
        return None
    s = api_settings(name, environ)
    embed = embed_settings(environ)
    return {"primary": s.model, "code": s.model, "reasoning": s.model, "general": s.model,
            "multimodal": s.model, "extraction": s.extraction_model,
            "embed": embed.model if embed else NO_EMBED_MODEL}


__all__ = ["ANTHROPIC_DEFAULT_BASE_URL", "ApiSettings", "EMBED_KEY_SETTING", "EmbedSettings",
           "NO_EMBED_MODEL", "OPENAI_DEFAULT_BASE_URL", "UNSET_MODEL", "anthropic_settings",
           "api_model_table", "api_settings", "embed_api_key", "embed_model", "embed_settings",
           "embed_url", "openai_settings"]
