"""Offline test doubles shared across the suite. Grows as the plan progresses."""

from __future__ import annotations


class FakeHoncho:
    """Offline Honcho double. `fail=True` simulates the service being down."""

    def __init__(self, *, cards: dict[str, list[str]] | None = None, fail: bool = False) -> None:
        self.fed: list[tuple[str, str]] = []
        self.peers: list[str] = []
        self.cards = cards or {}
        self.fail = fail

    async def ensure_workspace(self) -> None:
        return None

    async def ensure_peer(self, peer_id: str) -> None:
        if not self.fail:
            self.peers.append(peer_id)

    async def feed(self, peer_id: str, text: str) -> bool:
        if self.fail:
            return False
        self.fed.append((peer_id, text))
        return True

    async def peer_card(self, peer_id: str) -> list[str]:
        if self.fail:
            return []
        return list(self.cards.get(peer_id, []))

    async def health(self) -> dict:
        return {"reachable": not self.fail}
