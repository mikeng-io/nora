"""`nora events backfill-units [--apply]` (B3(b), design/cognition-loop/70-
migration.md §8, DEC-237).

WHY IT EXISTS. Migration `0074_event_revision` backfilled every
pre-existing `events` row to `unit_id=""` — units live in `policy.toml`,
not the database, so the migration had no way to fill the column for an
event that predates it. `GatheringStore.search` (B3(a)) already widens a
unit-wide `event_search` to also match a legacy `unit_id=""` row whose
`conversation_id` is one of the unit's own rooms, but that is a READ-side
patch: the row itself still carries no unit, so it is invisible to
anything that filters on `unit_id` directly (a future report, an export, a
`WHERE` clause someone writes by hand). This command WRITES the column,
once, so the read-side patch stops being load-bearing.

WHAT IT DOES. For every room `policy.toml` names (`Policy.conversations`),
list that room's events (`GatheringStore.list_for_conversation`, every
status) and set `unit_id` on any row that still carries the pre-migration
`unit_id=""` to that room's `conv.unit` (`"nora"` when `policy.toml` names
no `unit =` for it — the ordinary default, DEC-214). A room `policy.toml`
does not name AT ALL has no unit that could be resolved for it, so its
events — pre-migration or not — are never read at all: SKIPPED BY
CONSTRUCTION, not by a check this command could get wrong.

Dry run by default: it reads everything and reports what `--apply` would
change, writing nothing. `--apply` writes through `GatheringStore.update`
— the SAME method `update_event`/`set_event_status` write through — so the
write is a normal revision (a new `event_revision` row, the head moved)
and `row_history`'s trigger (migration `0068_system_time_history`,
`nora.infra.sql.history`) captures it exactly like any other write to
`events`: this command adds no new write path, and nothing about it is
invisible to the audit trail the rest of the table already has.

REFUSED: nothing. A room with an empty roster of events, or none at all in
`policy.toml`, is an empty report, not an error — `policy.toml` with no
`[units]` at all just means every room maps to no unit, so nothing is
touched and the report says so."""

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
    (`nora.domain.gatherings`) — the two methods `nora.app.gatherings.
    GatheringService` itself calls, seen structurally so this module does
    not need to import the concrete store."""

    async def list_for_conversation(
        self, *, conversation_id: str, status: GatheringStatus | None = None,
        include_unsupported: bool = False,
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
    ever read — a room policy.toml does not name at all is never a source
    of a "skipped" count, it is simply never visited."""
    report = EventsBackfillReport(applied=apply)
    for convo in policy.conversations:
        if not convo.unit or not convo.primary_id:
            continue
        # `include_unsupported`: a writer that re-homes every row needs the
        # ones the withdrawal cascade marked too. Left out, a marked legacy
        # event would keep no unit for good, and clearing its mark later would
        # return it unreachable by the unit-scoped search. The update does not
        # touch the mark (`_event_values` never writes it).
        events = await store.list_for_conversation(
            conversation_id=convo.primary_id, include_unsupported=True,
        )
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
    """`updated` is only counted on `--apply` (`backfill_unit_ids` never
    attempts a write on a dry run, so it never has an outcome to count); on a
    dry run the number this prints is `legacy` — the count of what WOULD be
    set, which is what a dry run exists to report. Confusing the two used to
    make every dry run print "would set 0" regardless of how many legacy
    events there were."""
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
