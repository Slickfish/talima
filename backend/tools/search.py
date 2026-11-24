"""Phase 10a — SearXNG-backed web search tool (Law 3 gated).

The agent surfaces a search proposal when EITHER:
  - the user query contains a temporal keyword (latest, current, today, …), OR
  - cortex propagation density falls below 0.15 (knowledge-gap pressure,
    arch §10.1 "Knowledge Gap" trigger).

The proposal goes to the UI; the user clicks "search"; the loop calls
WebSearchTool.search(query, confirmed=True). Without confirmed=True the
method raises SearchRefused — no network packet ever leaves the box on a
trigger alone.

Default endpoint is a self-hosted SearXNG at http://localhost:8888 so the
privacy-preserving default doesn't talk to a third party. Override via
SEARXNG_URL env var.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Iterable

from backend.memory import actions as _actions   # W10b: the recorder owns the network

logger = logging.getLogger("nexus.tools.search")


# --------------- triggers ---------------

TEMPORAL_KEYWORDS: frozenset[str] = frozenset({
    "latest", "current", "today", "recent", "news",
})

GAP_DENSITY_THRESHOLD: float = 0.15   # arch §10.1


_WORD_RE = re.compile(r"[a-z][a-z0-9_]+")


def _tokenise(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def should_trigger_search(
    query: str,
    *,
    gap_density: float | None = None,
) -> bool:
    """True iff the agent should propose a web search to the user.

    The function never fires the search itself — it only decides whether to
    surface the proposal. Confirmation gating happens in WebSearchTool.search().
    """
    if TEMPORAL_KEYWORDS & _tokenise(query):
        return True
    if gap_density is not None and gap_density < GAP_DENSITY_THRESHOLD:
        return True
    return False


# --------------- Law 3 gate ---------------

class SearchRefused(RuntimeError):
    """Raised when a search is invoked without explicit confirmation."""


# --------------- result shape ---------------

@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    engine: str = ""

    def to_dict(self) -> dict:
        return {
            "title":   self.title,
            "url":     self.url,
            "snippet": self.snippet,
            "engine":  self.engine,
        }


# --------------- network call (mockable seam for tests) ---------------

async def fetch_json(
    url: str,
    params: dict,
    *,
    timeout: float = 10.0,
    headers: dict | None = None,
    store=None,
    trigger=None,
) -> dict:
    """Single GET → JSON, sent as a recorded runtime action (W10b): its row is
    written before it leaves, or it is not sent (`ActionNotRecorded`).
    Module-level so tests can monkeypatch it without touching the transport."""
    r = await _actions.recorded_get(store, kind="web_search", url=url, params=params, headers=headers,
                                    timeout=timeout, trigger=trigger)
    r.raise_for_status()
    return r.json()


# --------------- tool ---------------

class WebSearchTool:
    """Thin SearXNG wrapper. One method, one contract:
    `search(query, confirmed=True) -> list[SearchResult]`."""

    # The compiled fallback. It used to be `os.getenv("SEARXNG_URL", ...)`
    # evaluated in the class body, which runs at import — before
    # `load_env_files()` — so `SEARXNG_URL` in `.env` was silently ignored and
    # every instance took the default. The env read moved into `__init__`.
    DEFAULT_URL    = "http://localhost:8888"
    DEFAULT_HEADERS = {
        "User-Agent": "NEXUS/0.10 (+local synthetic cortex; SearXNG client)",
    }

    def __init__(self, base_url: str | None = None, *, store=None):
        # Precedence unchanged: an explicit argument still wins over the
        # environment, which now wins over the compiled default.
        self.base_url = (base_url
                         or os.getenv("SEARXNG_URL")
                         or self.DEFAULT_URL).rstrip("/")
        # H.3b — declared loss (I-6), as an ADDITIVE side-channel.
        #
        # `search()` returns [] on HTTPError/OSError, which two Packet 10a
        # sentinels pin as the fails-soft contract
        # (test_search_handles_network_failure_gracefully and
        # ...http_status_failure_gracefully). Making it raise would break
        # both — a real contract change, and one the packet said to report
        # rather than perform. So the failure is recorded here instead: the
        # return type is unchanged, both sentinels stay green, and the
        # endpoint can finally distinguish "the search service is
        # unreachable" from "there were no results".
        #
        # Before this, /search/execute's own error handler (server.py:842)
        # was UNREACHABLE — the tool swallowed the exception first — so the
        # endpoint returned {"count": 0} for both cases identically.
        self.last_error: str = ""
        self.store = store          # W10b — where each search is recorded as an action

    async def search(
        self,
        query: str,
        *,
        confirmed: bool,
        max_results: int = 10,
        categories: Iterable[str] | None = None,
        trigger=None,
    ) -> list[SearchResult]:
        if not confirmed:
            raise SearchRefused(
                "WebSearchTool.search requires confirmed=True (Law 3 — "
                "external request)"
            )
        # Cleared at the top of every call: a stale reason attached to a later
        # successful search would be worse than none.
        self.last_error = ""
        if not query or not query.strip():
            return []
        params: dict[str, object] = {"q": query.strip(), "format": "json"}
        if categories:
            params["categories"] = ",".join(categories)
        try:
            data = await fetch_json(
                f"{self.base_url}/search", params,
                headers=dict(self.DEFAULT_HEADERS),
                store=self.store, trigger=trigger,           # W10b: an unrecorded search is never sent
            )
        except (_actions.HTTPError, OSError) as e:
            self.last_error = f"{type(e).__name__}: {e}"
            logger.warning("web search failed (%s): %s", self.base_url,
                           self.last_error)
            return []
        results = data.get("results") or []
        out: list[SearchResult] = []
        for r in results[:max_results]:
            title = (r.get("title") or "").strip()
            url   = (r.get("url") or "").strip()
            if not (title and url):
                continue
            snippet = (r.get("content") or "").strip()
            engine  = (r.get("engine") or "").strip()
            out.append(SearchResult(title=title, url=url,
                                     snippet=snippet, engine=engine))
        return out

    def proposal(self, query: str, *, gap_density: float | None = None) -> dict:
        """Render the non-blocking proposal payload the UI shows for Law 3
        confirmation. Calling this does NOT contact the network."""
        triggered = should_trigger_search(query, gap_density=gap_density)
        reasons = []
        if TEMPORAL_KEYWORDS & _tokenise(query):
            reasons.append("temporal_keyword")
        if gap_density is not None and gap_density < GAP_DENSITY_THRESHOLD:
            reasons.append(f"low_gap_density:{gap_density:.3f}")
        return {
            "tool":         "web_search",
            "endpoint":     self.base_url,
            "query":        query,
            "triggered":    triggered,
            "reasons":      reasons,
            "requires_confirmation": True,   # Law 3 — always
        }
