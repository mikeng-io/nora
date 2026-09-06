"""Web search and page extraction — the grounding capability behind the
`web_search`/`web_fetch` tools.

Tavily since 2026-08-08 (DEC-73): SearXNG + Firecrawl and the old `web_fetch` are
retired, and the SSRF apparatus with them (Tavily fetches from its own servers,
never Nora's, and Nora's own network is never handed a model-chosen URL). DEC-116
brings `web_fetch` back on Tavily's separate `/extract` endpoint, which fetches
from the same servers as `/search` — the SSRF argument is unchanged.

Exa joins as a SECOND implementation of the same port on DEC-157, selected by
`[search] provider`. DEC-73 (4) always said another backend would plug in with
no tool change; this is that. Both adapters crawl from their own vendor's
servers, so the no-SSRF property is a property of the PORT here, not of one
vendor — and both are held to one contract suite (`tests/contract/`), which is
what the port's second implementation obliges (CLAUDE.md)."""

from .exa import ExaSearchProvider
from .tavily import TavilySearchProvider

__all__ = ["ExaSearchProvider", "TavilySearchProvider"]
