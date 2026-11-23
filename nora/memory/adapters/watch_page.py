"""The closed registry of watch adapters."""

from types import MappingProxyType

from nora.domain.ports import SearchProvider


class PageWatch:
    def __init__(self, provider: SearchProvider) -> None:
        self._provider = provider

    async def __call__(self, url: str) -> str | None:
        result = await self._provider.fetch(url=url)
        text = result.get("text")
        return text if result.get("status") == "ok" and isinstance(text, str) else None


WATCH_ADAPTERS = MappingProxyType({"page": PageWatch})
