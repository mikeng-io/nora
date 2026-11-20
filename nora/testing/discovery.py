"""Scripted CollectionDiscovery; shared contract in tests/contract/."""

from nora.domain.identity import Identifier
from nora.domain.managed import CollectionSnapshot, DiscoveryCapabilities


class ScriptedDiscovery:
    """A `CollectionDiscovery` whose `members` answer a test drives —
    for the flows the platform normalizers cannot produce (a PARTIAL
    snapshot; Discord/Signal reads are all-or-unavailable)."""

    platform = "discord"
    capabilities = DiscoveryCapabilities(
        roster=True, member_events=True, sender_typing=True,
        room_audience=False)

    def __init__(self, snapshot: CollectionSnapshot, *, collection: str) -> None:
        self.collection = collection
        self.snapshot = snapshot
        self.reads = 0

    def collection_of(self, *, conversation_id: str) -> str | None:
        guild = conversation_id.partition("/")[0]
        return guild if guild.isascii() and guild.isdigit() else None

    def collection_ref(self, *, object_ref: str) -> str | None:
        kind, _, ref = object_ref.partition(":")
        return ref if kind == "guild" and ref.isascii() and ref.isdigit() else None

    async def members(self, *, collection: str) -> CollectionSnapshot:
        self.reads += 1
        if collection != self.collection:
            return CollectionSnapshot(available=False, detail="unknown-collection")
        return self.snapshot

    async def sender_identifiers(
        self, *, collection: str, value: str,
    ) -> frozenset[Identifier] | None:
        if not value.isascii() or not value.isdigit():
            return None
        return frozenset({Identifier("discord", "user_id", value)})
