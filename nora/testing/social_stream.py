"""`FakeSocialSource`: an in-memory `SocialSource`, held to the same answers as
`nora.infra.social.instagram_source.InstagramSocialSource` by
`tests/contract/test_social_source_contract.py`.

It answers the way the bridge does rather than the same way every time: an
account it holds nothing for is `not_found` on every read, a checkpointed
session is `halt` on every read and unhealthy, and each scripted answer
(a value or a `Failed`) is returned for its own account or item only. `calls`
records `(read, id)` for every call, so a test can count what reached the
network.
"""

from __future__ import annotations

from nora.domain.social_stream import (
    Account,
    Cap,
    Failed,
    FailureKind,
    ItemRef,
    Page,
    Probe,
    SocialItem,
    StorySnapshot,
)

_NOT_FOUND = Failed(FailureKind.NOT_FOUND, "not_found")
_HALT = Failed(FailureKind.HALT, "needs_human")


class FakeSocialSource:
    """Scripted per account (`probes`, `stories`, `pages`) and per item
    (`items`, keyed by item id). `checkpointed` is the session needing a
    human: every read halts and `healthy` is False."""

    def __init__(
        self, *, network: str = "instagram", probe_cost: int = 2,
        capabilities: frozenset[Cap] = frozenset(Cap),
    ) -> None:
        self.network = network
        self.probe_cost = probe_cost
        self.capabilities = capabilities
        self.probes: dict[str, Probe | Failed] = {}
        self.stories: dict[str, StorySnapshot | Failed] = {}
        self.pages: dict[str, Page | Failed] = {}
        self.items: dict[str, SocialItem | Failed] = {}
        self.checkpointed = False
        self.calls: list[tuple[str, str]] = []

    def reads(self, read: str) -> list[str]:
        """The ids `read` was called with, in order."""
        return [target for name, target in self.calls if name == read]

    async def probe(self, *, account: Account) -> Probe | Failed:
        self.calls.append(("probe", account.id))
        return _HALT if self.checkpointed else self.probes.get(account.id, _NOT_FOUND)

    async def story(self, *, account: Account) -> StorySnapshot | Failed:
        self.calls.append(("story", account.id))
        return _HALT if self.checkpointed else self.stories.get(account.id, _NOT_FOUND)

    async def list_items(self, *, account: Account, after: str | None) -> Page | Failed:
        self.calls.append(("items", account.id))
        return _HALT if self.checkpointed else self.pages.get(account.id, _NOT_FOUND)

    async def read_item(self, *, ref: ItemRef) -> SocialItem | Failed:
        self.calls.append(("item", ref.item_id))
        return _HALT if self.checkpointed else self.items.get(ref.item_id, _NOT_FOUND)

    async def healthy(self) -> bool:
        self.calls.append(("health", ""))
        return not self.checkpointed


__all__ = ["FakeSocialSource"]
