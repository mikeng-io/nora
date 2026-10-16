"""Web search and page extraction — the grounding capability behind the
`web_search`/`web_fetch` tools.

Two implementations of one port, selected by `[search] provider`: Tavily
(DEC-73; `web_fetch` on its `/extract` endpoint, DEC-116) and Exa (DEC-157).
Both crawl from their own vendor's servers and Nora's network is never handed a
model-chosen URL, so having no SSRF surface is a property of the port, not of
one vendor. Both are held to one contract suite (`tests/contract/`)."""

from .exa import ExaSearchProvider
from .tavily import TavilySearchProvider

__all__ = ["ExaSearchProvider", "TavilySearchProvider"]
