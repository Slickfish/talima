"""External tools available to the agent loop. Each tool is gated by Law 3 —
no external request fires without explicit user confirmation."""
from .search import (
    GAP_DENSITY_THRESHOLD,
    TEMPORAL_KEYWORDS,
    SearchRefused,
    SearchResult,
    WebSearchTool,
    fetch_json,
    should_trigger_search,
)

__all__ = [
    "GAP_DENSITY_THRESHOLD",
    "TEMPORAL_KEYWORDS",
    "SearchRefused",
    "SearchResult",
    "WebSearchTool",
    "fetch_json",
    "should_trigger_search",
]
