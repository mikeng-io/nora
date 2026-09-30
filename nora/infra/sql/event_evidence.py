"""What an event write rests on, resolved against the database in the write's
own transaction (migration `0086_event_citations`; design/cognition-loop/10-tiers.md
§3, 40-register.md §4; the review of 2026-09-30 M2/m6).

`nora.infra.sql.gatherings.SqlGatheringStore.create`/`update` call
`apply_evidence` with the `EventEvidence` a verb handed them. It does the
lookups the pure `nora.domain.gatherings.event_lineage` needs (the room each
cited message was said in, the lineage each cited Rollup row carries, the
event's own room key) and leaves one `citation` edge per `EventCitation`. Both
ride the caller's connection, so the row, its revision, its edges and its
lineage are one fact (the argument `nora.app.withdrawal` makes for the
cascade).

A hidden message (`hidden_at`) is treated as gone: a citation of it resolves
`unresolved`, never to the room it was said in. A Rollup key that matches no
current row, or one whose text is not a key, resolves the same way.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.gatherings import (
    CITED_MESSAGE,
    CITED_ROLLUP,
    EventCitation,
    EventEvidence,
    Gathering,
    event_lineage,
)
from nora.domain.lineage import Lineage
from nora.domain.rollup import room_key
from nora.infra.sql.mobility import current_build_id
from nora.infra.sql.schema import (
    CITATION,
    MESSAGES,
    PRESENCE_STAYS,
    PRESENCE_TRIPS,
    ROLLUP_COLOCATION,
    ROLLUP_DAYBOOK,
    ROLLUP_OUTING,
    ROLLUP_TIE,
)

__all__ = ["apply_evidence", "record_event_citations", "rollup_lineage"]

#: `messages.id` is int4: a cited number past it names no row.
_MESSAGE_ID_MAX = 2**31 - 1


async def record_event_citations(
    conn: AsyncConnection, *, event_id: str, citations: Sequence[EventCitation],
) -> None:
    """One edge per `(part, cited)`, in `conn`'s transaction. `ON CONFLICT DO
    NOTHING`: an event re-citing the same row for the same part on its next
    revision is not a new edge (`nora.infra.sql.citation`'s own rule)."""
    if not citations:
        return
    await conn.execute(
        pg_insert(CITATION)
        .values([
            {
                "citing_store": "event", "citing_key": event_id,
                "cited_store": c.store, "cited_id": c.ref, "part": c.part,
            }
            for c in citations
        ])
        .on_conflict_do_nothing(constraint="citation_edge_unique")
    )


async def _message_rooms(
    conn: AsyncConnection, ids: Iterable[int],
) -> tuple[dict[int, str], int | None]:
    lookup = [i for i in ids if 0 < i <= _MESSAGE_ID_MAX]
    if not lookup:
        return {}, None
    rows = (await conn.execute(
        sa.select(
            MESSAGES.c.id, MESSAGES.c.platform, MESSAGES.c.conversation_id, MESSAGES.c.ts,
        ).where(MESSAGES.c.id.in_(lookup), MESSAGES.c.hidden_at.is_(None))
    )).all()
    return (
        {r.id: room_key(r.platform, r.conversation_id) for r in rows},
        max((r.ts for r in rows), default=None),
    )


async def _room_key_of(conn: AsyncConnection, conversation_id: str) -> str | None:
    platform = (await conn.execute(
        sa.select(MESSAGES.c.platform)
        .where(
            MESSAGES.c.conversation_id == conversation_id,
            MESSAGES.c.hidden_at.is_(None),
        )
        .order_by(MESSAGES.c.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    return None if platform is None else room_key(platform, conversation_id)


def _instant(text: str) -> int | None:
    """An interval end as `rollup_read` printed it (ISO 8601 with an offset, to
    the second) in epoch ms; `None` for anything else."""
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        return None
    return None if at.tzinfo is None else int(at.timestamp() * 1000)


async def rollup_lineage(
    conn: AsyncConnection, *, key: str, unit: str,
) -> Lineage | None:
    """The lineage of the current-build Rollup row `key` names
    (`rollup(kind|subject|from/to)`, `nora.domain.note_body.rollup_key`), the
    union of every row it matches; `None` when it names none, is not a key, or
    is a unit row of another unit than `unit`. `rollup_read` prints an interval
    to the second, so each end is matched within its second."""
    kind, _, rest = key.partition("|")
    subject, _, interval = rest.rpartition("|")
    start_text, _, end_text = interval.partition("/")
    start, end = _instant(start_text), _instant(end_text)
    if not subject or start is None or end is None:
        return None

    def within(column: sa.ColumnElement[int], at: int) -> sa.ColumnElement[bool]:
        return sa.and_(column >= at, column < at + 1000)

    if kind in ("stay", "trip"):
        table = PRESENCE_STAYS if kind == "stay" else PRESENCE_TRIPS
        where: list[sa.ColumnElement[bool]] = [
            table.c.build_id == current_build_id(), table.c.subject_id == subject,
            within(table.c.started_ts, start), within(table.c.ended_ts, end),
        ]
        stmt = sa.select(table.c.lineage).where(*where)
    else:
        unit_part, _, members = subject.partition("|")
        if not unit or unit_part != unit or not members:
            return None
        if kind in ("colocation", "outing"):
            table = ROLLUP_COLOCATION if kind == "colocation" else ROLLUP_OUTING
            who = sa.func.array_to_string(table.c.persons, ",") == members
        elif kind == "daybook":
            table, who = ROLLUP_DAYBOOK, ROLLUP_DAYBOOK.c.person_id == members
        elif kind == "tie":
            a, _, b = members.partition(",")
            table, who = ROLLUP_TIE, sa.and_(
                ROLLUP_TIE.c.person_a == a, ROLLUP_TIE.c.person_b == b,
            )
        else:
            return None
        stmt = sa.select(table.c.lineage).where(
            table.c.build_id == current_build_id(), table.c.unit == unit, who,
            within(table.c.valid_from_ms, start), within(table.c.valid_to_ms, end),
        )
    rows = (await conn.execute(stmt)).all()
    if not rows:
        return None
    merged = Lineage()
    for r in rows:
        merged = merged.union(Lineage.from_json(r.lineage))
    return merged


async def apply_evidence(
    conn: AsyncConnection, *, event: Gathering, held: Lineage,
    evidence: EventEvidence,
) -> tuple[Lineage, int | None]:
    """The lineage and `as_of` of `event` after a write that rests on
    `evidence`, and its edges written. `held` is the lineage the row carries now
    (read under the row lock by `update`; the row's own on `create`). `as_of`
    is the newest cited message's time when that is newer than the row's."""
    ids = {int(c.ref) for c in evidence.citations if c.store == CITED_MESSAGE and c.ref.isdigit()}
    rooms, newest = await _message_rooms(conn, ids)
    rollups: dict[str, Lineage | None] = {}
    for c in evidence.citations:
        if c.store == CITED_ROLLUP and c.ref not in rollups:
            rollups[c.ref] = await rollup_lineage(conn, key=c.ref, unit=event.unit_id)
    origin = evidence.room or await _room_key_of(conn, event.conversation_id) \
        or event.conversation_id
    lineage = event_lineage(
        held, evidence, message_rooms=rooms, rollup_lineages=rollups, origin=origin,
    )
    await record_event_citations(conn, event_id=str(event.id), citations=evidence.citations)
    known = [t for t in (event.as_of, newest) if t is not None]
    return lineage, (max(known) if known else None)
