"""`SignalDiscovery` — Signal's collection-roster capability for managed
onboarding (`identity.md` «Platform discovery and membership»).

WHAT SIGNAL CAN PROVE. `listGroups` enumerates a group's member set with
each member's `number`+`uuid` pair as ONE row — the pairing is the
daemon's own, so those two forms bind one account, never two people.
signal-cli streams no membership deltas (`member_events=False`: joins and
leaves reach us through the periodic roster read, never an event). A
group is its own member set — unlike a Discord guild it IS the room's
audience (`room_audience=True`).

Typing is never guessed: a member's kinds come from the daemon's field
names (`number`, `uuid`), and a bare sender value is typed only by shape
— E.164 for `phone`, RFC 4122 for `uuid` — else `None`, because an
untypable sender must not become a person on a hunch.
"""

from __future__ import annotations

import logging
import uuid as uuid_module
from collections.abc import Awaitable, Callable
from typing import Any

from nora.domain.identity import Identifier
from nora.domain.managed import (
    CollectionSnapshot,
    DiscoveryCapabilities,
    ObservedMember,
    ObservedRows,
)

log = logging.getLogger("nora.signal.discovery")


def _typed_ids(row: dict[str, Any]) -> frozenset[Identifier]:
    """The daemon's field names → identifier kinds (`username` is metadata, never matchable)."""
    ids = set()
    number, aci = row.get("number"), row.get("uuid")
    if isinstance(number, str) and number.strip():
        ids.add(Identifier("signal", "phone", number))
    if isinstance(aci, str) and aci.strip():
        ids.add(Identifier("signal", "uuid", aci))
    return frozenset(ids)


def _sender_kind(value: str) -> str | None:
    if value.startswith("+") and value[1:].isdigit():
        return "phone"
    try:
        uuid_module.UUID(value)
    except ValueError:
        return None
    return "uuid"


class SignalDiscovery:
    """`CollectionDiscovery` for Signal groups.

    `fetch` is `async group_id -> ObservedRows | None`: the group's raw
    `members[]` rows plus the instant the upstream read finished, or
    `None` when the read cannot complete — direct mode calls
    `directory.group_members`, bridge mode calls the bridge's
    `roster` query and takes its `observed_at_ms`. `own_account` is
    the service's own number so the roster never onboards Nora herself.
    """

    platform = "signal"

    capabilities = DiscoveryCapabilities(
        roster=True,
        member_events=False,
        sender_typing=True,
        room_audience=True,
    )

    def __init__(
        self,
        *,
        fetch: Callable[[str], Awaitable[ObservedRows | None]],
        own_account: str,
    ) -> None:
        self._fetch = fetch
        self._own = own_account

    def collection_of(self, *, conversation_id: str) -> str | None:
        # A Signal group's conversation_id IS the group's base64 id — a
        # DM's id shares the space but never equals a group id, so it
        # simply matches no rule.
        return conversation_id or None

    def collection_ref(self, *, object_ref: str) -> str | None:
        kind, _, ref = object_ref.partition(":")
        return ref if kind == "group" and ref else None

    async def members(self, *, collection: str) -> CollectionSnapshot:
        result = await self._fetch(collection)
        if result is None:
            return CollectionSnapshot(
                available=False, detail="group-unavailable")
        if not result.rows:
            # signal-cli can report a group it could not refresh with an
            # empty member list; empty is then a broken read, not proof
            # everyone left, and must never stand in as a complete roster.
            return CollectionSnapshot(
                available=False, detail="empty-roster")
        members = []
        for row in result.rows:
            ids = _typed_ids(row)
            if self._own and self._own in {i.value for i in ids}:
                continue  # the service's own account is not a person
            members.append(
                ObservedMember(ids, "person" if ids else "unknown"))
        return CollectionSnapshot(
            available=True,
            members=tuple(members),
            complete=True,
            # The producer's own read instant — `0` when the transport
            # could not establish one, never a re-stamp of delivery time.
            observed_ms=result.observed_ms or 0,
        )

    async def sender_identifiers(
        self, *, collection: str, value: str
    ) -> frozenset[Identifier] | None:
        # Sealed sender means one account reaches us as `number` on one
        # message and `uuid` on the next — a value naming a member row
        # yields that row's whole paired set; else type by shape.
        result = await self._fetch(collection)
        if result is not None:
            for row in result.rows:
                raw = {str(row[k]) for k in ("number", "uuid") if row.get(k)}
                if value in raw:
                    ids = _typed_ids(row)
                    if ids:
                        return ids
        kind = _sender_kind(value)
        return frozenset({Identifier("signal", kind, value)}) if kind else None
