"""Web search — the grounding capability behind the one `web_search` tool.

Tavily since 2026-08-08 (DEC-73): SearXNG + Firecrawl and the `web_fetch` verb are
retired, and the SSRF apparatus with them (Tavily searches from its own servers,
never Nora's, and is never handed a model-chosen URL)."""

from .tavily import TavilySearchProvider

__all__ = ["TavilySearchProvider"]
