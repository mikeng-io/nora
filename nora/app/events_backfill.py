"""`nora events backfill-units [--apply]`: set `unit_id` on legacy events.

Units live in `policy.toml`, not the database, so events written before the
`unit_id` column existed carry `unit_id=""`. `GatheringStore.search` widens a
unit-wide `event_search` to match such a row when its `conversation_id` is one
of the unit's rooms, but that is a read-side patch: the row still carries no
unit, so anything filtering on `unit_id` directly misses it. This command
writes the column so the patch is not load-bearing (DEC-237).

For every room `policy.toml` names (`Policy.conversations`), list that room's
events (`GatheringStore.list_for_conversation`, every status) and set
`unit_id` on any row still carrying `""` to that room's `conv.unit` (`"nora"`
when `policy.toml` names no `unit =`, DEC-214). A room `policy.toml` does not
name has no resolvable unit, so its events are never read: skipped by
construction.

Dry run by default: reports what `--apply` would change, writing nothing.
`--apply` writes through `GatheringStore.update`, the same method
`update_event`/`set_event_status` use, so the write is a normal revision (a new
`event_revision` row, the head moved) and the `row_history` trigger
(`nora.infra.sql.history`) captures it. No new write path.

A room with no events, or none named in `policy.toml`, is an empty report, not
an error.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol
from uuid import UUID

from nora.domain.gatherings import Gathering, GatheringStatus
from nora.policy import Policy

log = logging.getLogger("nora.app.events_backfill")


class BackfillStore(Protocol):
    """What this command needs of `GatheringStore`
    (`nora.domain.gatherings`): the two methods `nora.app.gatherings.
    GatheringService` calls, seen structurally so this module does not import
    the concrete store."""

    async def list_for_conversation(
        self, *, conversation_id: str, status: GatheringStatus | None = None,
        unit_id: str = "",
    ) -> Sequence[Gathering]: ...

    async def update(
        self, event: Gathering, *, base_revision_id: UUID | None = None,
    ) -> Gathering: ...


@dataclass
class RoomCounts:
    conversation_id: str
    label: str
    unit: str
    total: int = 0
    legacy: int = 0
    updated: int = 0
    failed: int = 0


@dataclass
class EventsBackfillReport:
    applied: bool
    rooms: list[RoomCounts] = field(default_factory=list)

    @property
    def updated(self) -> int:
        return sum(r.updated for r in self.rooms)

    @property
    def legacy(self) -> int:
        return sum(r.legacy for r in self.rooms)

    @property
    def failed(self) -> int:
        return sum(r.failed for r in self.rooms)


async def backfill_unit_ids(
    *, policy: Policy, store: BackfillStore, apply: bool = False,
) -> EventsBackfillReport:
    """See the module docstring. Only rooms `policy.conversations` names are
    read; an unnamed room is never visited, so it never counts as skipped."""
    report = EventsBackfillReport(applied=apply)
    for convo in policy.conversations:
        if not convo.unit or not convo.primary_id:
            continue
        events = await store.list_for_conversation(conversation_id=convo.primary_id)
        counts = RoomCounts(
            conversation_id=convo.primary_id, label=convo.label or convo.primary_id,
            unit=convo.unit, total=len(events),
        )
        report.rooms.append(counts)
        for event in events:
            if event.unit_id:
                continue
            counts.legacy += 1
            if not apply:
                continue
            try:
                await store.update(replace(event, unit_id=convo.unit))
            except Exception:
                log.exception(
                    "events backfill-units: update failed for event %s (room %s)",
                    event.id, convo.primary_id,
                )
                counts.failed += 1
                continue
            counts.updated += 1
    return report


def render(report: EventsBackfillReport) -> list[str]:
    """`updated` is only counted on `--apply` (a dry run attempts no write); on a
    dry run the printed number is `legacy`, the count of what WOULD be set."""
    verb = "set" if report.applied else "would set"
    lines = [
        f"events backfill-units ({'APPLIED' if report.applied else 'dry run: nothing written'})",
    ]
    for room in report.rooms:
        if room.legacy == 0 and room.total == 0:
            continue
        would_set = room.updated if report.applied else room.legacy
        lines.append(
            f"  {room.label} [{room.conversation_id[:12]}…] -> unit {room.unit!r}: "
            f"{room.total} event(s), {room.legacy} legacy (unit_id=''), "
            f"{verb} {would_set}"
            + (f", {room.failed} FAILED" if room.failed else "")
        )
    total_set = report.updated if report.applied else report.legacy
    lines.append(
        f"total: {report.legacy} legacy event(s) across {len(report.rooms)} room(s) "
        f"policy.toml names, {verb} {total_set}"
        + (f", {report.failed} FAILED" if report.failed else "")
    )
    if not report.applied:
        lines.append("re-run with --apply to write")
    return lines
