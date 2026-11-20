"""CollectionDiscovery preserves typed accounts and the producer's evidence."""

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from nora.domain.identity import Identifier
from nora.domain.managed import CollectionSnapshot, ObservedMember, ObservedRows
from nora.domain.managed_ports import CollectionDiscovery
from nora.platform.discord.discovery import DiscordDiscovery
from nora.platform.signal.discovery import SignalDiscovery
from nora.testing.discovery import ScriptedDiscovery

STAMP = 1_750_000_000_000
COLLECTION = "100000000000000001"
PHONE = "+85290000101"
ACI = "a1000001-0000-4000-8000-000000000001"


@dataclass
class DiscoveryCase:
    source: CollectionDiscovery
    set_snapshot: Callable[[str], None]
    identifiers: frozenset[Identifier]
    value: str
    conversation: str
    object_ref: str
    room_audience: bool


@pytest.fixture(params=["fake", "discord", "signal"])
def discovery(request) -> DiscoveryCase:
    signal = request.param == "signal"
    identifiers = frozenset({Identifier("signal", "phone", PHONE),
                             Identifier("signal", "uuid", ACI)}) if signal else frozenset({
        Identifier("discord", "user_id", "10001")})
    value = PHONE if signal else "10001"
    snapshot = CollectionSnapshot(available=True, complete=True, observed_ms=STAMP,
                                  members=(ObservedMember(identifiers),))
    if request.param == "fake":
        fake = ScriptedDiscovery(snapshot, collection=COLLECTION)

        def change(mode: str) -> None:
            if mode == "healthy":
                fake.snapshot = snapshot
            elif mode == "unknown":
                fake.snapshot = CollectionSnapshot(available=True, complete=True,
                    observed_ms=STAMP, members=(ObservedMember(frozenset(), "unknown"),))
            else:
                fake.snapshot = CollectionSnapshot(available=False, detail=mode)

        source: CollectionDiscovery = fake
    else:
        state = {"mode": "healthy"}

        def change(mode: str) -> None:
            state["mode"] = mode

        async def fetch(collection: str) -> ObservedRows | None:
            if collection != COLLECTION or state["mode"] == "unavailable":
                return None
            if state["mode"] == "empty":
                return ObservedRows(rows=(), observed_ms=STAMP)
            row = {"number": PHONE, "uuid": ACI, "username": "descriptive-only"} if signal else {
                "user": {"id": "10001", "username": "descriptive-only"}}
            if state["mode"] == "unknown":
                row = {"username": "descriptive-only"}
            return ObservedRows(rows=(row,), observed_ms=STAMP)

        source = (SignalDiscovery(fetch=fetch, own_account="")
                  if signal else DiscordDiscovery(fetch=fetch))
    return DiscoveryCase(source, change, identifiers, value,
        COLLECTION if signal else f"{COLLECTION}/100000000000000002",
        f"group:{COLLECTION}" if signal else f"guild:{COLLECTION}", signal)


async def test_member_groups_preserve_exact_identifiers_and_source_time(discovery):
    snapshot = await discovery.source.members(collection=COLLECTION)
    assert snapshot.available and snapshot.complete
    assert snapshot.members == (ObservedMember(discovery.identifiers),)
    assert snapshot.observed_ms == STAMP


@pytest.mark.parametrize("mode", ["unavailable", "empty"])
async def test_failed_or_empty_reads_do_not_establish_departure(discovery, mode):
    healthy = await discovery.source.members(collection=COLLECTION)
    assert healthy.available and healthy.members
    discovery.set_snapshot(mode)
    failed = await discovery.source.members(collection=COLLECTION)
    assert not failed.available and not failed.complete and not failed.members
    discovery.set_snapshot("healthy")
    recovered = await discovery.source.members(collection=COLLECTION)
    assert recovered.available and recovered.members == healthy.members


async def test_unknown_collection_is_unavailable_not_a_complete_empty_roster(discovery):
    assert (await discovery.source.members(collection=COLLECTION)).available
    unknown = await discovery.source.members(collection="999999999999999999")
    assert not unknown.available and not unknown.complete and not unknown.members


async def test_metadata_cannot_turn_an_unknown_row_into_an_account(discovery):
    discovery.set_snapshot("unknown")
    snapshot = await discovery.source.members(collection=COLLECTION)
    assert snapshot.available
    assert snapshot.members == (ObservedMember(frozenset(), "unknown"),)
    discovery.set_snapshot("healthy")
    assert (await discovery.source.members(collection=COLLECTION)).members[0].identifiers


def test_collection_objects_and_capabilities_preserve_platform_scope(discovery):
    source = discovery.source
    assert source.collection_of(conversation_id=discovery.conversation) == COLLECTION
    assert source.collection_ref(object_ref=discovery.object_ref) == COLLECTION
    assert source.collection_ref(object_ref=f"channel:{COLLECTION}") is None
    assert source.collection_ref(object_ref="") is None
    assert source.capabilities.roster and source.capabilities.sender_typing
    assert source.capabilities.room_audience is discovery.room_audience
    assert source.capabilities.member_events is (not discovery.room_audience)


async def test_sender_typing_returns_one_account_and_refuses_unprovable_values(discovery):
    source = discovery.source
    typed = await source.sender_identifiers(collection=COLLECTION, value=discovery.value)
    assert typed == discovery.identifiers
    assert await source.sender_identifiers(collection=COLLECTION, value="descriptive-only") is None
