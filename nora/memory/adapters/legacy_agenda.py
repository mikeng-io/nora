"""Read legacy commitment heads and import them into the graph atomically."""

from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.lineage import (
    BODY,
    FACE,
    MESSAGE,
    PERCEPTION,
    PLAY,
    PRESENCE,
    ROW,
    SOCIAL,
    Lineage,
    LineageEntry,
    split_room,
)
from nora.domain.note_body import split_sections
from nora.domain.rollup import room_key
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, INTENTION, REGISTRY
from nora.infra.sql.cognitive_graph_store import RowData
from nora.infra.sql.item_registration import epoch_ms
from nora.infra.sql.schema import (
    EVENT_REVISION,
    EVENTS,
    LEDGER_COMMITMENT,
    LEDGER_DECISION,
    NOTE,
    REMINDERS,
)
from nora.memory.domain.time_trigger import decode_time_trigger
from nora.persons import Persons
from nora.policy import Conversation, Policy

_WRITER = "legacy_agenda_migration"
_HEADS = (
    (REMINDERS, REMINDERS.c.state.not_in(("done", "cancelled", "failed"))),
    (EVENTS, EVENTS.c.status.not_in(("happened", "cancelled", "lapsed", "evicted"))),
    (NOTE, NOTE.c.retired_at.is_(None)),
    (LEDGER_COMMITMENT, LEDGER_COMMITMENT.c.status == "open"),
    (LEDGER_DECISION, LEDGER_DECISION.c.closed_at.is_(None)),
)
_DECISION_SECTIONS = ("Options", "Criteria", "Leaning", "Blocked on", "Reopen if", "Closed as")


async def seed_scheduled_intention(
    conn: AsyncConnection, *, unit: str, room: str, requester: str, text: str,
    created_at: datetime, due_at: datetime,
) -> str:
    graph_id = f"intention:{uuid4().hex}"
    await insert_agenda_intention(conn, graph_id, {
        "unit": unit, "origin_room": room,
        "lineage": Lineage.of({BODY: ()}).to_json(),
        "identities": [] if requester == "nora" else [requester],
        "owner": "nora", "owed_to": requester, "asserted_at": created_at,
        "done_when_form": "act_event", "done_when": {"own_delivery": True},
        "delivery_mode": "choose_moment",
        "trigger": _time_trigger(due_at),
    }, text=text, writer="case-seed")
    return graph_id


def _time_trigger(at: datetime) -> dict[str, Any]:
    return {"kind": "time", "at": at.isoformat(), "zone": "UTC"}


def _reminder_trigger(row: RowData) -> dict[str, Any]:
    trigger = _time_trigger(row["due_at"])
    if row["recurrence"] == "none":
        return trigger
    trigger["zone"] = row["recurrence_tz"]
    remaining: dict[str, Any] = {
        "every": row["recurrence"],
        "interval": row["recurrence_interval"],
        "anchor": row["series_anchor_at"].isoformat(),
        "offset": row["occurrence"],
    }
    if row["recurrence_count"] is not None:
        remaining["count"] = row["recurrence_count"] - row["occurrence"]
    if row["recurrence_until"] is not None:
        remaining["until"] = row["recurrence_until"].isoformat()
    trigger["recurrence"] = remaining
    schedule = decode_time_trigger(trigger)
    if schedule is None or schedule.occurrence(0) is None:
        raise ValueError("Legacy reminder has an invalid recurrence")
    return trigger


def _open_rows(table: sa.Table, predicate: sa.ColumnElement[bool]) -> sa.Select:
    if table is not EVENTS:
        return sa.select(table).where(predicate)
    notes = (
        sa.select(EVENT_REVISION.c.body, EVENT_REVISION.c.lineage)
        .where(EVENT_REVISION.c.event_id == EVENTS.c.id, EVENT_REVISION.c.body.is_not(None))
        .order_by(EVENT_REVISION.c.recorded_at.desc(), EVENT_REVISION.c.id.desc())
        .limit(1).lateral()
    )
    return sa.select(
        EVENTS, notes.c.body.label("event_notes"), notes.c.lineage.label("event_notes_lineage"),
    ).outerjoin(notes, sa.true()).where(predicate)


def _event_text(row: RowData) -> str:
    lines = [row["title"]]
    for key, label in (
        ("venue_text", "Venue"), ("venue_name", "Venue name"), ("venue_address", "Venue address"),
        ("venue_district", "Venue district"), ("venue_city", "Venue city"),
        ("venue_place_id", "Venue ID"), ("venue_rating", "Venue rating"),
        ("venue_price_level", "Venue price level"), ("venue_maps_url", "Venue map"),
    ):
        if row[key] is not None and row[key] != "":
            lines.append(f"{label}: {row[key]}")
    if row["people"]:
        lines.append("People: " + ", ".join(row["people"]))
    if row["end"] is not None:
        lines.append(f'End: {row["end"].isoformat()}')
    if row.get("event_notes"):
        lines.append(row["event_notes"])
    return "\n".join(lines)


def _reopen_condition(row: RowData) -> str | None:
    sections = dict(split_sections(row["body"], schema=_DECISION_SECTIONS))
    lines = sections.get("Reopen if")
    return "Reopen if: " + "\n".join(lines) if lines else None


def migrated_id(table: str, key: object) -> str:
    return f"intention:{uuid5(NAMESPACE_URL, f'nora/legacy-agenda/{table}/{key}').hex}"


class SqlLegacyAgenda:
    def __init__(self, engine: AsyncEngine, policy: Policy, persons: Persons) -> None:
        self._engine = engine
        self._policy = policy
        self._persons = persons

    async def import_open(self, *, review_deadline: datetime) -> dict[str, int]:
        added: dict[str, int] = {}
        async with self._engine.begin() as conn:
            await conn.execute(sa.select(sa.func.pg_advisory_xact_lock(
                sa.func.hashtext(_WRITER),
            )))
            for table, open_head in _HEADS:
                name = str(table.name)
                added[name] = 0
                rows = await conn.execute(_open_rows(table, open_head))
                for row in rows.mappings():
                    graph_id = migrated_id(name, row["id"])
                    existing = sa.select(INTENTION.c.id).where(INTENTION.c.id == graph_id)
                    if await conn.scalar(existing):
                        continue
                    scope = self._scope(name, row)
                    intention, text = _intention(name, row, review_deadline)
                    reopen = _reopen_condition(row) if table is LEDGER_DECISION else None
                    written = await insert_agenda_intention(
                        conn, graph_id, {**scope, **intention}, text=text, writer=_WRITER,
                        reopen_condition=reopen,
                    )
                    added[name] += int(written)
        return added

    def _scope(self, source: str, row: RowData) -> dict[str, Any]:
        unit = row.get("unit", row.get("unit_id"))
        exclusions = row.get("exclude_persons") or []
        if row.get("scope") == "unit":
            return {
                "unit": unit, "origin_room": None, "lineage": self._unit_lineage(row, unit),
                "excluded_identities": exclusions,
            }
        platform = row.get("platform")
        if source == "events" and (not unit or not platform):
            raise ValueError("Legacy event lacks its recorded unit or platform")
        origin = row.get("established_in", row.get("conversation_id"))
        candidates = self._rooms(origin, unit, platform)
        if len(candidates) != 1:
            raise ValueError(f"Legacy {source} head has no unique room in its authorized scope")
        [candidate] = candidates
        origin_room = room_key(candidate.platform, candidate.primary_id)
        lineage = Lineage.from_json(row.get("lineage"), origin=origin_room)
        if row.get("event_notes_lineage") is not None:
            lineage = lineage.union(Lineage.from_json(
                row["event_notes_lineage"], origin=origin_room,
            ))
        return {
            "unit": candidate.unit,
            "origin_room": origin_room,
            "lineage": Lineage.of({
                BODY: (),
                ROW: (entry for _, entries in lineage.sections for entry in entries),
            }).to_json(),
            "excluded_identities": exclusions,
        }

    def _rooms(self, ref: str, unit: str | None, platform: str | None = None) -> list[Conversation]:
        qualified, room = split_room(ref)
        return [
            candidate for candidate in self._policy.rooms()
            if room in candidate.ids and platform in (None, candidate.platform)
            and qualified in (None, candidate.platform) and unit in (None, candidate.unit)
            and candidate.unit
        ]

    def _unit_lineage(self, row: RowData, unit: str) -> list[Any] | dict[str, Any]:
        lineage = Lineage.from_json(row.get("lineage"))
        entries = tuple(entry for _, entries in lineage.sections for entry in entries)
        if not unit or not entries or not all(
            self._entry_resolves(entry, unit) for entry in entries
        ):
            raise ValueError("Legacy unit-scoped head has empty or unresolved lineage")
        sections = {BODY: entries}
        if limits := tuple(self._direct_room_limits(entries, unit)):
            sections[ROW] = limits
        return Lineage.of(sections).to_json()

    def _direct_room_limits(
        self, entries: tuple[LineageEntry, ...], unit: str,
    ) -> Iterator[LineageEntry]:
        for entry in entries:
            if entry.origin is not None:
                [room] = self._rooms(entry.origin, unit)
                if room.ids & self._persons.direct_addresses(room.platform):
                    # A bare perception is readable only in the room it came from.
                    yield LineageEntry(PERCEPTION, origin=room_key(room.platform, room.primary_id))
            yield from self._direct_room_limits(entry.reads, unit)

    def _entry_resolves(self, entry: LineageEntry, unit: str) -> bool:
        if entry.kind not in (FACE, MESSAGE, PERCEPTION, PLAY, PRESENCE, SOCIAL):
            return False
        if entry.origin is not None:
            if len(self._rooms(entry.origin, unit)) != 1:
                return False
        elif entry.kind in (FACE, MESSAGE, PERCEPTION):
            return False
        if entry.kind in (FACE, PLAY, PRESENCE, SOCIAL) and not entry.subject:
            return False
        return all(self._entry_resolves(child, unit) for child in entry.reads)


def _intention(source: str, row: RowData, review_deadline: datetime) -> tuple[dict[str, Any], str]:
    condition, deadline, trigger = None, None, None
    if source == "reminders":
        owed = row["requester_person_id"]
        text, asserted = row["text"], row["created_at"]
        if row["state"] == "firing":
            # An interrupted legacy fire may already have reached the room.
            condition = "Verify whether the interrupted reminder reached its destination."
        else:
            trigger = _reminder_trigger(row)
        identities = [owed]
    elif source == "events":
        owed = row["created_by"]
        text, asserted = _event_text(row), row["created_at"]
        if row["when"] is not None:
            trigger = _time_trigger(row["when"])
        else:
            condition = "Follow up on this gathering when its time is known."
        identities = [owed]
    elif source == "note":
        owed, identities = "nora", []
        text = "\n".join((row["title"], row["description"], row["uncertainty"]))
        condition, asserted = row["closing_condition"], epoch_ms(row["created_at"])
    elif source == "standing_decision":
        owed, identities = "nora", []
        text, asserted = f'{row["subject"]}\n{row["body"]}', epoch_ms(row["opened_at"])
        condition = "Settle the outstanding decision."
    else:
        identities = row["owed_to"]
        owed = identities[0] if len(identities) == 1 else "nora"
        text, asserted = f'{row["subject"]}\n{row["body"]}', epoch_ms(row["made_at"])
        condition, deadline = row["body"], epoch_ms(row["due_at"])
    return {
        "asserted_at": asserted,
        "owner": "nora",
        "owed_to": owed,
        "identities": [person for person in identities if person != "nora"],
        "done_when_form": "act_event" if condition is None else "untyped",
        "done_when": {"own_delivery": True} if condition is None else condition,
        "deadline": (deadline or review_deadline) if condition is not None else None,
        "delivery_mode": "when_it_comes_up" if trigger is None else "choose_moment",
        "trigger": trigger,
    }, text


async def insert_agenda_intention(
    conn: AsyncConnection, graph_id: str, values: Mapping[str, Any], *, text: str, writer: str,
    reopen_condition: str | None = None,
) -> bool:
    """Keep the source wording as the intention's scoped basis."""
    shared = {
        key: values[key] for key in
        ("unit", "origin_room", "identities", "excluded_identities", "lineage", "asserted_at")
        if key in values
    }
    shared.update(writer=writer, exposure="internal")
    if not await _insert(conn, INTENTION, graph_id, {
        **values, **shared, "basis": "committed", "state": "open", "form": "achieve",
    }):
        return False
    bases = [("", text)]
    if reopen_condition is not None:
        bases.append((":reopen", reopen_condition))
    for suffix, basis in bases:
        claim = graph_id.replace("intention:", "claim:", 1) + suffix
        await _insert(conn, CLAIM, claim, {
            **shared, "basis": "inferred", "reasoning": "told", "confidence": "high",
            "state": "live", "part": "fact",
            "content": {"text": basis},
        })
        await _insert(conn, EDGE, graph_id.replace("intention:", "edge:", 1) + suffix, {
            **shared, "basis": "committed", "state": "live", "type": "rests_on",
            "src_id": graph_id, "dst_id": claim,
        })
    return True


async def _insert(
    conn: AsyncConnection, table: sa.Table, graph_id: str, values: Mapping[str, Any],
) -> bool:
    kind = str(table.name)
    register = insert(REGISTRY).values(
        id=graph_id, unit=values["unit"], kind=kind,
    ).on_conflict_do_nothing(index_elements=[REGISTRY.c.id, REGISTRY.c.unit])
    if await conn.scalar(register.returning(REGISTRY.c.id)) is None:
        return False
    await conn.execute(sa.insert(table).values(id=graph_id, kind=kind, **values))
    return True
