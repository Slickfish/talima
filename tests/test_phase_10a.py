"""Phase 10a — web search tool (CLAUDE.md scope, single component).

Required behaviours per spec:
  - Trigger on temporal keywords ("latest","current","today","recent","news")
    OR kb gap density < 0.15
  - Confirm before any external request (Law 3)

Tests assert both the trigger detector and the Law 3 gate, plus the network
shape (mocked — no test ever hits a real SearXNG instance).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from backend.tools.search import (
    GAP_DENSITY_THRESHOLD,
    TEMPORAL_KEYWORDS,
    SearchRefused,
    SearchResult,
    WebSearchTool,
    should_trigger_search,
)
from backend.tools import search as search_mod


# --------------- triggers ---------------

def test_temporal_keywords_constants():
    """The five keywords from the spec are present, lowercase, set-typed."""
    expected = {"latest", "current", "today", "recent", "news"}
    assert expected <= set(TEMPORAL_KEYWORDS)
    for kw in TEMPORAL_KEYWORDS:
        assert kw == kw.lower()


def test_gap_density_threshold_constant():
    """arch §10.1 sets the knowledge-gap threshold at 0.15."""
    assert GAP_DENSITY_THRESHOLD == 0.15


@pytest.mark.parametrize("query", [
    "what is the latest python release",
    "current stock price for AAPL",
    "today's weather in London",
    "any recent news on llm benchmarks",
    "news from Anthropic this week",
])
def test_should_trigger_on_temporal_keyword(query):
    assert should_trigger_search(query) is True


@pytest.mark.parametrize("query", [
    "explain monads",
    "how does the GIL work",
    "implement quicksort in rust",
])
def test_should_not_trigger_on_evergreen_query(query):
    assert should_trigger_search(query) is False
    assert should_trigger_search(query, gap_density=0.5) is False


def test_should_trigger_on_low_gap_density():
    """Sub-0.15 propagation density flags a knowledge gap → search proposal."""
    assert should_trigger_search("anything", gap_density=0.10) is True
    assert should_trigger_search("anything", gap_density=0.0)  is True


def test_should_not_trigger_at_or_above_threshold():
    assert should_trigger_search("anything", gap_density=0.15) is False
    assert should_trigger_search("anything", gap_density=0.30) is False


def test_should_trigger_keyword_overrides_high_density():
    """Temporal keyword fires even when the cortex is densely populated."""
    assert should_trigger_search("latest python", gap_density=0.99) is True


def test_temporal_keyword_match_is_case_insensitive():
    assert should_trigger_search("LATEST python release") is True
    assert should_trigger_search("Today is wednesday") is True


def test_temporal_keyword_match_requires_word_boundary():
    """'currently' contains 'current' as a substring but the trigger should
    fire on whole words only — token-level match prevents false positives."""
    # 'currently' is one token — the substring 'current' is NOT in TEMPORAL_KEYWORDS
    # because tokenisation produces ['currently'].
    assert should_trigger_search("currently working on this") is False
    # But 'current' as a whole word DOES fire.
    assert should_trigger_search("the current build") is True


# --------------- Law 3 gate ---------------

def test_search_refuses_without_explicit_confirmation():
    tool = WebSearchTool(base_url="http://example.invalid")
    with pytest.raises(SearchRefused, match="Law 3"):
        asyncio.run(tool.search("anything", confirmed=False))


def test_search_refuses_when_confirmed_omitted():
    """`confirmed` is keyword-only with no default — calling search() without
    it must be a TypeError, not a silent network call."""
    tool = WebSearchTool(base_url="http://example.invalid")
    with pytest.raises(TypeError):
        asyncio.run(tool.search("anything"))   # type: ignore[call-arg]


def test_search_refusal_does_not_touch_network(monkeypatch):
    """Confirm that the refused path never reaches fetch_json."""
    called = {"n": 0}
    async def boom(*a, **kw):
        called["n"] += 1
        return {}
    monkeypatch.setattr(search_mod, "fetch_json", boom)
    tool = WebSearchTool(base_url="http://example.invalid")
    with pytest.raises(SearchRefused):
        asyncio.run(tool.search("anything", confirmed=False))
    assert called["n"] == 0, "fetch_json was called despite refusal"


# --------------- network path (mocked) ---------------

def test_search_returns_results_when_confirmed(monkeypatch):
    async def fake_fetch(url, params, *, timeout=10.0, headers=None, store=None, trigger=None):   # W10b's seam
        assert url.endswith("/search")
        assert params["format"] == "json"
        assert params["q"] == "python release notes"
        return {"results": [
            {"title": "Python 3.13 release notes",
             "url":   "https://docs.python.org/3.13/whatsnew",
             "content": "What's new in Python 3.13",
             "engine":  "duckduckgo"},
            {"title": "Python 3.13 announcement",
             "url":   "https://python.org/announce/3.13",
             "content": "Latest stable release",
             "engine":  "google"},
        ]}
    monkeypatch.setattr(search_mod, "fetch_json", fake_fetch)
    tool = WebSearchTool(base_url="http://example.invalid")
    out = asyncio.run(tool.search("python release notes", confirmed=True))
    assert len(out) == 2
    assert all(isinstance(r, SearchResult) for r in out)
    assert out[0].title == "Python 3.13 release notes"
    assert out[0].url.startswith("https://docs.python.org")
    assert out[0].snippet == "What's new in Python 3.13"
    assert out[0].engine == "duckduckgo"


def test_search_max_results_truncates(monkeypatch):
    async def fake_fetch(url, params, *, timeout=10.0, headers=None, store=None, trigger=None):   # W10b's seam
        return {"results": [
            {"title": f"r{i}", "url": f"http://x/{i}", "content": "", "engine": "e"}
            for i in range(20)
        ]}
    monkeypatch.setattr(search_mod, "fetch_json", fake_fetch)
    tool = WebSearchTool(base_url="http://example.invalid")
    out = asyncio.run(tool.search("x", confirmed=True, max_results=5))
    assert len(out) == 5


def test_search_drops_results_missing_title_or_url(monkeypatch):
    async def fake_fetch(url, params, *, timeout=10.0, headers=None, store=None, trigger=None):   # W10b's seam
        return {"results": [
            {"title": "", "url": "http://x", "content": "no title"},
            {"title": "no url", "url": "", "content": "x"},
            {"title": "good", "url": "http://y", "content": "ok"},
        ]}
    monkeypatch.setattr(search_mod, "fetch_json", fake_fetch)
    tool = WebSearchTool(base_url="http://example.invalid")
    out = asyncio.run(tool.search("x", confirmed=True))
    assert len(out) == 1 and out[0].title == "good"


def test_search_returns_empty_on_blank_query():
    tool = WebSearchTool(base_url="http://example.invalid")
    assert asyncio.run(tool.search("   ", confirmed=True)) == []


def test_search_handles_network_failure_gracefully(monkeypatch):
    async def boom(*a, **kw):
        raise httpx.ConnectError("backend unreachable")
    monkeypatch.setattr(search_mod, "fetch_json", boom)
    tool = WebSearchTool(base_url="http://example.invalid")
    out = asyncio.run(tool.search("anything", confirmed=True))
    assert out == []


def test_search_handles_http_status_failure_gracefully(monkeypatch):
    async def fail_status(*a, **kw):
        raise httpx.HTTPStatusError(
            "500", request=httpx.Request("GET", "http://x"),
            response=httpx.Response(500),
        )
    monkeypatch.setattr(search_mod, "fetch_json", fail_status)
    tool = WebSearchTool(base_url="http://example.invalid")
    assert asyncio.run(tool.search("anything", confirmed=True)) == []


def test_search_url_via_env(monkeypatch):
    """CORRECTED — this test used to prove the opposite of its name.

    It set SEARXNG_URL and then passed the same URL explicitly, so it
    asserted the explicit argument and said nothing about the environment.
    Its own comment recorded why — "WebSearchTool reads DEFAULT_URL at class
    definition" — which is the defect, written down inside the test that was
    supposed to catch it.

    The read moved into `__init__`, so the environment can now be asserted
    with no argument at all.
    """
    monkeypatch.setenv("SEARXNG_URL", "https://my-searxng.example/")
    tool = WebSearchTool()
    assert tool.base_url == "https://my-searxng.example"   # trailing slash trimmed


def test_search_explicit_url_still_beats_the_environment(monkeypatch):
    """Precedence, unchanged by the move: an explicit argument wins."""
    monkeypatch.setenv("SEARXNG_URL", "https://from-env.example")
    assert WebSearchTool(base_url="https://explicit.example").base_url == \
        "https://explicit.example"


def test_search_falls_back_to_the_compiled_default(monkeypatch):
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    assert WebSearchTool().base_url == WebSearchTool.DEFAULT_URL


def test_search_explicit_base_url_overrides_default():
    tool = WebSearchTool(base_url="http://localhost:1234")
    assert tool.base_url == "http://localhost:1234"


# --------------- proposal payload ---------------

def test_proposal_marks_temporal_trigger_with_reason():
    p = WebSearchTool().proposal("latest news on llm benchmarks")
    assert p["tool"] == "web_search"
    assert p["triggered"] is True
    assert "temporal_keyword" in p["reasons"]
    assert p["requires_confirmation"] is True


def test_proposal_marks_density_trigger_with_reason():
    p = WebSearchTool().proposal("explain X", gap_density=0.05)
    assert p["triggered"] is True
    assert any(r.startswith("low_gap_density:") for r in p["reasons"])


def test_proposal_when_no_trigger_still_returns_payload():
    p = WebSearchTool().proposal("explain monads")
    assert p["triggered"] is False
    assert p["reasons"] == []
    assert p["requires_confirmation"] is True


def test_proposal_does_not_contact_network(monkeypatch):
    """Calling .proposal() must never fire fetch_json — it's a UI helper."""
    called = {"n": 0}
    async def fake(*a, **kw):
        called["n"] += 1
        return {}
    monkeypatch.setattr(search_mod, "fetch_json", fake)
    WebSearchTool().proposal("latest python")
    assert called["n"] == 0


# --------------- module surface ---------------

def test_module_exports_required_names():
    """Phase 10b wiring will import these — pin the public surface."""
    from backend.tools import (
        GAP_DENSITY_THRESHOLD as gd,
        TEMPORAL_KEYWORDS as kw,
        SearchRefused as sr,
        SearchResult as srx,
        WebSearchTool as wst,
        should_trigger_search as sts,
    )
    assert gd == GAP_DENSITY_THRESHOLD
    assert kw is TEMPORAL_KEYWORDS
    assert sr is SearchRefused
    assert srx is SearchResult
    assert wst is WebSearchTool
    assert sts is should_trigger_search
