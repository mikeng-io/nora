"""Read legacy commitment heads and import them into the graph atomically."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.lineage import BODY, ROW, Lineage, split_room
from nora.domain.rollup import room_key
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, INTENTION, REGISTRY
from nora.infra.sql.cognitive_graph_store import RowData
from nora.infra.sql.item_registration import epoch_ms
from nora.infra.sql.schema import EVENTS, LEDGER_COMMITMENT, NOTE, REMINDERS
from nora.policy import Policy

_WRITER = "legacy_agenda_migration"
_HEADS = (
    (REMINDERS, REMINDERS.c.state.not_in(("done", "cancelled", "failed"))),
    (EVENTS, EVENTS.c.status.not_in(("happened", "cancelled", "lapsed", "evicted"))),
    (NOTE, NOTE.c.retired_at.is_(None)),
    (LEDGER_COMMITMENT, LEDGER_COMMITMENT.c.status == "open"),
)


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


def _time_trigger(at: datetime) -> dict[str, str]:
    return {"kind": "time", "at": at.isoformat(), "zone": "UTC"}


def migrated_id(table: str, key: object) -> str:
    return f"intention:{uuid5(NAMESPACE_URL, f'nora/legacy-agenda/{table}/{key}').hex}"


class SqlLegacyAgenda:
    def __init__(self, engine: AsyncEngine, policy: Policy) -> None:
        self._engine = engine
        self._policy = policy

    async def import_open(self, *, review_deadline: datetime) -> dict[str, int]:
        added: dict[str, int] = {}
        async with self._engine.begin() as conn:
            await conn.execute(sa.select(sa.func.pg_advisory_xact_lock(
                sa.func.hashtext(_WRITER),
            )))
            for table, open_head in _HEADS:
                name = str(table.name)
                added[name] = 0
                rows = await conn.execute(sa.select(table).where(open_head))
                for row in rows.mappings():
                    graph_id = migrated_id(name, row["id"])
                    existing = sa.select(INTENTION.c.id).where(INTENTION.c.id == graph_id)
                    if await conn.scalar(existing):
                        continue
                    scope = self._scope(name, row)
                    intention, text = _intention(name, row, review_deadline)
                    written = await insert_agenda_intention(
                        conn, graph_id, {**scope, **intention}, text=text, writer=_WRITER,
                    )
                    added[name] += int(written)
        return added

    def _scope(self, source: str, row: RowData) -> dict[str, Any]:
        unit = row.get("unit", row.get("unit_id"))
        if source == "standing_commitment" and row["scope"] == "unit":
            if not unit:
                raise ValueError("Legacy standing commitment has no unit")
            return {"unit": unit, "origin_room": None, "lineage": {}}
        platform = row.get("platform")
        if source == "events" and (not unit or not platform):
            raise ValueError("Legacy event lacks its recorded unit or platform")
        origin = row.get("established_in", row.get("conversation_id"))
        qualified, room = split_room(origin)
        candidates = [
            candidate for candidate in self._policy.rooms()
            if room in candidate.ids and platform in (None, candidate.platform)
            and qualified in (None, candidate.platform) and unit in (None, candidate.unit)
            and candidate.unit
        ]
        if len(candidates) != 1:
            raise ValueError(f"Legacy {source} head has no unique room in its authorized scope")
        [candidate] = candidates
        origin_room = room_key(candidate.platform, candidate.primary_id)
        lineage = Lineage.from_json(row.get("lineage"), origin=origin_room)
        return {
            "unit": candidate.unit,
            "origin_room": origin_room,
            "lineage": Lineage.of({
                BODY: (),
                ROW: (entry for _, entries in lineage.sections for entry in entries),
            }).to_json(),
            "excluded_identities": row.get("exclude_persons") or [],
        }


def _intention(source: str, row: RowData, review_deadline: datetime) -> tuple[dict[str, Any], str]:
    condition, deadline, trigger = None, None, None
    if source == "reminders":
        owed = row["requester_person_id"]
        text, asserted = row["text"], row["created_at"]
        if row["state"] == "firing":
            # An interrupted legacy fire may already have reached the room.
            condition = "Verify whether the interrupted reminder reached its destination."
        else:
            trigger = _time_trigger(row["due_at"])
        identities = [owed]
    elif source == "events":
        owed = row["created_by"]
        text, asserted = row["title"], row["created_at"]
        if row["when"] is not None:
            trigger = _time_trigger(row["when"])
        else:
            condition = "Follow up on this gathering when its time is known."
        identities = [owed]
    elif source == "note":
        owed, identities = "nora", []
        text = "\n".join((row["title"], row["description"], row["uncertainty"]))
        condition, asserted = row["closing_condition"], epoch_ms(row["created_at"])
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
    claim = graph_id.replace("intention:", "claim:", 1)
    await _insert(conn, CLAIM, claim, {
        **shared, "basis": "inferred", "reasoning": "told", "confidence": "high",
        "state": "live", "part": "fact",
        "content": {"text": text},
    })
    await _insert(conn, EDGE, graph_id.replace("intention:", "edge:", 1), {
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
