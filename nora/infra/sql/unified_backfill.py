"""The unified backfill's reads: what the one backfill enumerates, and the
existing durable state it resumes on.

No new persistence — no queue, no ledger, no migration. Progress is exactly
what the one Cognition session already writes (`cognition_session` rows, and
`comprehend_result` `readings`/`stimulus_failure`/`stimulus_requeue` rows);
29 §5 retired the per-item work-item table as intake and nothing here revives
it.

The enumeration is chronological across every kind by evidence time
(`at_ms`), oldest first (27 §10). Real `stimulus` rows are used where they
exist — they carry ingest's own cues/waits/ref decisions. Where a source row
predates stimulus recording (no row could have been raised), the stimulus the
stage WOULD have written is fabricated from the same stored fields and the
same domain functions (`nora.domain.cues.cues_of`, the ingest stage's ref
rule), marked by a deterministic negative id so `stimuli:<ids>` work ids can
never collide with a real row's.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.cues import cues_of
from nora.domain.social_stream import SOCIAL_READ_REF, read_item
from nora.domain.stimuli import Stimulus
from nora.infra.sql.schema import (
    COGNITION_SESSION,
    COMPREHEND_RESULT as RESULT,
    MESSAGE_DELETE,
    MESSAGE_EDIT,
    MESSAGE_REACTION,
    MESSAGES,
    PLATFORM_OBSERVATION,
    SOCIAL_ITEM,
    STIMULUS,
)

#: The stimulus kinds that are EVIDENCE: something a person or a source did.
#: `time` and `self` are the clock's and her own acts — a historical lane has
#: no clock to fire and no act to take, so they are never enumerated.
EVIDENCE_KINDS: tuple[str, ...] = (
    "message", "social", "play", "rollup", "observation")

#: `SqlComprehendRecords`' parked rule: a stimulus whose sessions failed five
#: times is parked until an operator requeues it (29 §3).
PARK_AFTER = 5


def _ms(value: Any) -> int:
    """A `recorded_at`/datetime or int ms as int ms."""
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    return int(value)


def fabricated_id(kind: str, item: str, ref: str, at_ms: int) -> int:
    """A deterministic id for a stimulus the row predates: NEGATIVE, so a
    fabricated work id can never collide with a real stimulus id, and stable
    across restarts (the same row fabricates the same id every run)."""
    digest = hashlib.sha256(
        f"unified-backfill|{kind}|{item}|{ref}|{at_ms}".encode()
    ).digest()
    return -1 - (int.from_bytes(digest[:6], "big") % (1 << 46))


def _stimulus(row: Any) -> Stimulus:
    return Stimulus(
        id=int(row.id), unit=str(row.unit), kind=str(row.kind), item=str(row.item),
        ref=str(row.ref), at_ms=int(row.at_ms), recorded_at_ms=_ms(row.recorded_at),
        platform=row.platform, conversation_id=row.conversation_id,
        person_id=row.person_id, cues=tuple(row.cues or ()),
        waits=bool(row.waits), due_ms=row.due_ms,
    )


async def evidence_stimuli(
    conn: AsyncConnection, unit: str, *,
    from_ms: int | None = None, until_ms: int | None = None,
) -> list[Stimulus]:
    """Every recorded evidence stimulus of the unit, evidence-time order —
    consumed or not: the backfill comprehends history, so live consumption is
    not coverage."""
    stmt = (
        sa.select(STIMULUS)
        .where(STIMULUS.c.unit == unit, STIMULUS.c.kind.in_(EVIDENCE_KINDS))
        .order_by(STIMULUS.c.at_ms, STIMULUS.c.id)
    )
    if from_ms is not None:
        stmt = stmt.where(STIMULUS.c.at_ms >= from_ms)
    if until_ms is not None:
        stmt = stmt.where(STIMULUS.c.at_ms < until_ms)
    return [_stimulus(r) for r in (await conn.execute(stmt)).all()]


def _fabricate(
    unit: str, *, kind: str, item: str, ref: str, at_ms: int,
    platform: str | None, conversation_id: str | None, person_id: str | None,
    cues: tuple[str, ...] = (),
) -> Stimulus:
    """The stimulus `nora.ingest.stages.stimulus` would have appended had
    stimulus recording existed when the row landed: same kind/item/ref/cues,
    `waits` always False (waits only ever gates WAKE triggering, and nothing
    here triggers a wake). `recorded_at_ms` is the evidence time."""
    return Stimulus(
        id=fabricated_id(kind, item, ref, at_ms), unit=unit, kind=kind,
        item=item, ref=ref, at_ms=at_ms, recorded_at_ms=at_ms,
        platform=platform, conversation_id=conversation_id,
        person_id=person_id, cues=cues, waits=False, due_ms=None,
    )


async def fabricated_message_stimuli(
    conn: AsyncConnection, unit: str,
    rooms: Collection[tuple[str, str]], *,
    self_ids: Collection[str] = (),
    from_ms: int | None = None, until_ms: int | None = None,
) -> list[Stimulus]:
    """Stimulus-equivalents for the message-family rows of the unit's rooms
    that stimulus recording never saw: `messages`, `message_edit`,
    `message_reaction` and `message_delete`, each carrying the item/ref/at
    the ingest stage writes (`_REF_TABLE`: an edit names its target as
    `messages:<id>`). Self-authored rows are skipped by the stage's own rule
    (its own sends and their edits; a reaction or delete the platform
    attributes to her, or to nobody on a row of hers)."""
    if not rooms:
        return []
    in_rooms = sa.or_(*(
        sa.and_(MESSAGES.c.platform == p, MESSAGES.c.conversation_id == cid)
        for p, cid in rooms
    ))
    bounds = lambda col: [  # noqa: E731
        *([] if from_ms is None else [col >= from_ms]),
        *([] if until_ms is None else [col < until_ms]),
    ]
    out: list[Stimulus] = []

    messages = await conn.execute(
        sa.select(
            MESSAGES.c.id, MESSAGES.c.platform, MESSAGES.c.conversation_id,
            MESSAGES.c.person_id, MESSAGES.c.ts, MESSAGES.c.body,
            MESSAGES.c.is_nora,
        ).where(in_rooms, *bounds(MESSAGES.c.ts))
    )
    edits = await conn.execute(
        sa.select(
            MESSAGE_EDIT.c.id, MESSAGE_EDIT.c.message_id, MESSAGE_EDIT.c.ts,
            MESSAGE_EDIT.c.body,
            MESSAGES.c.platform, MESSAGES.c.conversation_id,
            MESSAGES.c.person_id, MESSAGES.c.is_nora,
        ).select_from(MESSAGE_EDIT.join(MESSAGES, MESSAGES.c.id == MESSAGE_EDIT.c.message_id))
        .where(in_rooms, *bounds(MESSAGE_EDIT.c.ts))
    )
    reactions = await conn.execute(
        sa.select(
            MESSAGE_REACTION.c.id, MESSAGE_REACTION.c.message_id,
            MESSAGE_REACTION.c.sender_id, MESSAGE_REACTION.c.person_id,
            MESSAGE_REACTION.c.ts,
            MESSAGES.c.platform, MESSAGES.c.conversation_id,
            MESSAGES.c.person_id.label("target_person_id"), MESSAGES.c.is_nora,
        ).select_from(
            MESSAGE_REACTION.join(MESSAGES, MESSAGES.c.id == MESSAGE_REACTION.c.message_id))
        .where(in_rooms, *bounds(MESSAGE_REACTION.c.ts))
    )
    deletes = await conn.execute(
        sa.select(
            MESSAGE_DELETE.c.id, MESSAGE_DELETE.c.message_id,
            MESSAGE_DELETE.c.sender_id, MESSAGE_DELETE.c.person_id,
            MESSAGE_DELETE.c.ts,
            MESSAGES.c.platform, MESSAGES.c.conversation_id,
            MESSAGES.c.person_id.label("target_person_id"), MESSAGES.c.is_nora,
        ).select_from(
            MESSAGE_DELETE.join(MESSAGES, MESSAGES.c.id == MESSAGE_DELETE.c.message_id))
        .where(in_rooms, *bounds(MESSAGE_DELETE.c.ts))
    )

    mine = set(self_ids)
    for r in messages.all():
        if r.is_nora:
            continue
        out.append(_fabricate(
            unit, kind="message", item="message", ref=f"messages:{r.id}",
            at_ms=int(r.ts), platform=r.platform, conversation_id=r.conversation_id,
            person_id=r.person_id, cues=cues_of(r.body),
        ))
    for r in edits.all():
        if r.is_nora:
            continue
        out.append(_fabricate(
            unit, kind="message", item="edit", ref=f"messages:{r.message_id}",
            at_ms=int(r.ts), platform=r.platform, conversation_id=r.conversation_id,
            person_id=r.person_id, cues=cues_of(r.body),
        ))
    for table, item, rows in (("message_reaction", "reaction", reactions),
                              ("message_delete", "delete", deletes)):
        for r in rows.all():
            sender = r.sender_id or ""
            if sender in mine or (not sender and r.is_nora):
                continue
            out.append(_fabricate(
                unit, kind="message", item=item, ref=f"{table}:{r.id}",
                at_ms=int(r.ts), platform=r.platform,
                conversation_id=r.conversation_id, person_id=r.person_id,
            ))
    return out


async def fabricated_social_stimuli(
    conn: AsyncConnection, *,
    person_rooms: dict[tuple[str, str], tuple[str, str]],
    from_ms: int | None = None, until_ms: int | None = None,
) -> list[Stimulus]:
    """Stimulus-equivalents for `social_item` reads never announced: grouped
    by `read_id` exactly as `social_perception._announce` emits them —
    `social_read:<read_id>`, `read_item` over the read's kinds, `at_ms` the
    newest `taken_at`, one per `(person, unit)` the mapping names (the room
    the stimulus binds; none binds home, as live's `(None, None)` does)."""
    if not person_rooms:
        return []
    stmt = (
        sa.select(
            SOCIAL_ITEM.c.read_id, SOCIAL_ITEM.c.person,
            sa.func.array_agg(SOCIAL_ITEM.c.kind).label("kinds"),
            sa.func.array_agg(SOCIAL_ITEM.c.collab).label("collabs"),
            sa.func.max(SOCIAL_ITEM.c.taken_at).label("taken_at"),
        )
        .where(SOCIAL_ITEM.c.read_id.isnot(None))
        .group_by(SOCIAL_ITEM.c.read_id, SOCIAL_ITEM.c.person)
    )
    if from_ms is not None:
        stmt = stmt.having(sa.func.max(SOCIAL_ITEM.c.taken_at) >= _dt(from_ms))
    if until_ms is not None:
        stmt = stmt.having(sa.func.max(SOCIAL_ITEM.c.taken_at) < _dt(until_ms))
    out: list[Stimulus] = []
    for r in (await conn.execute(stmt)).all():
        at_ms = _ms(r.taken_at)
        item = read_item(list(zip(r.kinds, r.collabs)))
        for (person, unit), (platform, cid) in person_rooms.items():
            if person != r.person:
                continue
            out.append(_fabricate(
                unit, kind="social", item=item,
                ref=f"{SOCIAL_READ_REF}:{r.read_id}", at_ms=at_ms,
                platform=platform, conversation_id=cid, person_id=r.person,
            ))
    return out


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


async def fabricated_observation_stimuli(
    conn: AsyncConnection, *,
    unit_of: Any, from_ms: int | None = None, until_ms: int | None = None,
) -> list[Stimulus]:
    """Stimulus-equivalents for `platform_observation` rows never announced:
    `platform_observation:<id>` and the row's `kind` as `item`, as
    `ingest/stages/stimulus.run_observation` writes them. Person resolution is
    left empty — the stage's `actor_person_id` is a live lookup, and the
    observation line renders its own actor either way. `unit_of` maps the
    event's room onto a unit; an event of a room no unit owns is not
    enumerable (it would never bind a room the session may read)."""
    t = PLATFORM_OBSERVATION
    at = sa.func.coalesce(t.c.occurred_ms, t.c.received_ms)
    stmt = sa.select(
        t.c.id, t.c.kind, t.c.platform, t.c.conversation_id,
        at.label("at_ms"),
    ).order_by(at, t.c.id)
    if from_ms is not None:
        stmt = stmt.where(at >= from_ms)
    if until_ms is not None:
        stmt = stmt.where(at < until_ms)
    rows = (await conn.execute(stmt)).all()
    out: list[Stimulus] = []
    for r in rows:
        platform, cid = r.platform, r.conversation_id or None
        if platform is None or cid is None:
            continue
        unit = unit_of(platform, cid)
        if unit is None:
            continue
        out.append(_fabricate(
            unit, kind="observation", item=str(r.kind),
            ref=f"platform_observation:{r.id}", at_ms=int(r.at_ms),
            platform=platform, conversation_id=cid, person_id=None,
        ))
    return out


async def stimulus_keys(
    conn: AsyncConnection, unit: str,
) -> dict[tuple[str, str, str], set[int]]:
    """`(kind, item, ref) -> every recorded at_ms`, for fabricated-row dedup.
    An edit shares its `messages:<id>` ref with later edits of the same
    message, so its check is the FULL (kind, item, ref, at_ms) tuple; every
    other item is dropped on the (kind, item, ref) prefix alone."""
    rows = (await conn.execute(
        sa.select(STIMULUS.c.kind, STIMULUS.c.item, STIMULUS.c.ref, STIMULUS.c.at_ms)
        .where(STIMULUS.c.unit == unit)
    )).all()
    out: dict[tuple[str, str, str], set[int]] = {}
    for r in rows:
        out.setdefault((str(r.kind), str(r.item), str(r.ref)), set()).add(int(r.at_ms))
    return out


async def readings_done(
    conn: AsyncConnection, work_ids: Collection[str],
) -> dict[str, str]:
    """`work_id` -> the `input_hash` of its newest `readings` receipt: the
    exact dedup key `SqlComprehendRecords.readings` writes and reads, so an
    item comprehended at THIS input is done and an item whose input changed
    (an edit, a landed reading) is not silently skipped."""
    if not work_ids:
        return {}
    latest = (
        sa.select(RESULT.c.work_id, RESULT.c.input_hash)
        .where(RESULT.c.part == "readings", RESULT.c.work_id.in_(list(work_ids)))
        .distinct(RESULT.c.work_id)
        .order_by(RESULT.c.work_id, RESULT.c.id.desc())
    )
    return {str(r.work_id): str(r.input_hash) for r in (await conn.execute(latest)).all()}


async def attempted(
    conn: AsyncConnection, work_ids: Collection[str],
) -> set[str]:
    """The work_ids that have ANY `readings` receipt (any hash): the
    catch-up gate's "was it ever comprehended" — a weaker check than
    `readings_done`, which is the one apply runs."""
    if not work_ids:
        return set()
    rows = (await conn.execute(
        sa.select(RESULT.c.work_id)
        .where(RESULT.c.part == "readings", RESULT.c.work_id.in_(list(work_ids)))
        .distinct()
    )).all()
    return {str(r.work_id) for r in rows}


async def failure_counts(
    conn: AsyncConnection, unit: str, stimulus_ids: Collection[int],
) -> dict[int, int]:
    """Per-stimulus session-failure count since its newest requeue — the same
    rule `SqlComprehendRecords.failures` computes one id at a time, batched
    (`stimulus:{unit}:{sid}` work ids, `stimulus_failure` minus the last
    `stimulus_requeue`)."""
    if not stimulus_ids:
        return {}
    keys = [f"stimulus:{unit}:{sid}" for sid in stimulus_ids]
    requeue = (
        sa.select(RESULT.c.work_id, sa.func.max(RESULT.c.id).label("rid"))
        .where(RESULT.c.part == "stimulus_requeue", RESULT.c.work_id.in_(keys))
        .group_by(RESULT.c.work_id)
        .subquery()
    )
    rows = (await conn.execute(
        sa.select(RESULT.c.work_id, sa.func.count())
        .select_from(RESULT.outerjoin(requeue, RESULT.c.work_id == requeue.c.work_id))
        .where(
            RESULT.c.part == "stimulus_failure", RESULT.c.work_id.in_(keys),
            RESULT.c.id > sa.func.coalesce(requeue.c.rid, 0),
        )
        .group_by(RESULT.c.work_id)
    )).all()
    out = {k: 0 for k in keys}
    out.update({str(r.work_id): int(r[1]) for r in rows})
    return {int(k.rsplit(":", 1)[1]): v for k, v in out.items()}


async def lane_counts(conn: AsyncConnection, unit: str) -> dict[str, Any]:
    """The historical lane's own footprint for the census: sessions recorded
    with the lane marker, and the comprehend-result parts their sessions
    produced. No session row with the marker yet means the lane never ran —
    a zero is reported, never read as coverage."""
    sessions = (
        await conn.execute(
            sa.select(
                sa.func.count().label("n"),
                sa.func.min(COGNITION_SESSION.c.started_at_ms).label("first"),
                sa.func.max(COGNITION_SESSION.c.started_at_ms).label("last"),
            ).where(
                COGNITION_SESSION.c.unit == unit,
                COGNITION_SESSION.c.agenda["lane"].as_string() == "historical",
            )
        )
    ).first()
    parts = (await conn.execute(
        sa.select(RESULT.c.part, sa.func.count())
        .select_from(RESULT.join(
            COGNITION_SESSION, COGNITION_SESSION.c.id == RESULT.c.session_id))
        .where(COGNITION_SESSION.c.unit == unit)
        .group_by(RESULT.c.part)
    )).all()
    return {
        "sessions": int(sessions.n or 0),
        "first_ms": sessions.first,
        "last_ms": sessions.last,
        "parts": {str(r.part): int(r[1]) for r in parts},
    }
