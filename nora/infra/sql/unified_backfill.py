"""The unified backfill's reads: what the one backfill enumerates, and the
existing durable state it resumes on.

No new persistence — no queue, no ledger, no migration. Progress is exactly
what the one Cognition session already writes (`cognition_session` rows, and
`comprehend_result` normal-completion, legacy reading, failure and requeue receipts);
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
import json
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.cues import cues_of
from nora.domain.memory_graph import CATCH_UP_PREFIX
from nora.domain.social_stream import SOCIAL_READ_REF, read_item
from nora.domain.stimuli import Stimulus
from nora.infra.sql.schema import (
    COGNITION_SESSION,
    ENTITY,
    EVENTS,
    LEDGER_COMMITMENT,
    MESSAGE_DELETE,
    MESSAGE_EDIT,
    MESSAGE_REACTION,
    MESSAGES,
    PLATFORM_OBSERVATION,
    RELATION,
    SOCIAL_ITEM,
    STIMULUS,
)
from nora.infra.sql.schema import (
    COMPREHEND_RESULT as RESULT,
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
    def bounds(col: Any) -> list[Any]:
        return [
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
    for e in edits.all():
        if e.is_nora:
            continue
        out.append(_fabricate(
            unit, kind="message", item="edit", ref=f"messages:{e.message_id}",
            at_ms=int(e.ts), platform=e.platform, conversation_id=e.conversation_id,
            person_id=e.person_id, cues=cues_of(e.body),
        ))
    for table, item, rows in (("message_reaction", "reaction", reactions),
                              ("message_delete", "delete", deletes)):
        for x in rows.all():
            sender = x.sender_id or ""
            if sender in mine or (not sender and x.is_nora):
                continue
            out.append(_fabricate(
                unit, kind="message", item=item, ref=f"{table}:{x.id}",
                at_ms=int(x.ts), platform=x.platform,
                conversation_id=x.conversation_id, person_id=x.person_id,
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
        item = read_item(list(zip(r.kinds, r.collabs, strict=True)))
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


async def session_inputs_done(
    conn: AsyncConnection, work_ids: Collection[str],
) -> set[tuple[str, str]]:
    """Read exact input hashes from normal completions and legacy reading receipts."""
    wanted = set(work_ids)
    by_unit: dict[str, list[str]] = {}
    for key in wanted:
        unit, sid = key.removeprefix("input:").rsplit(":", 1)
        by_unit.setdefault(unit, []).append(sid)
    if not by_unit:
        return set()
    rows = (await conn.execute(sa.select(RESULT.c.payload).where(
        RESULT.c.part == "session_complete",
        sa.or_(*(sa.and_(RESULT.c.work_id == f"session:{unit}",
                        RESULT.c.payload["inputs"].has_any(sa.cast(ids, sa.ARRAY(sa.Text))))
                 for unit, ids in by_unit.items())),
    ))).scalars()
    current = {(key, digest) for row in rows for sid, digest in row["inputs"].items()
               if (key := f"input:{row['unit']}:{sid}") in wanted}
    legacy = (await conn.execute(
        sa.select(RESULT.c.work_id, RESULT.c.input_hash)
        .where(RESULT.c.part.in_(("readings", "readings_input")),
               RESULT.c.work_id.in_(list(wanted)))
        .distinct()
    )).all()
    return current | {(str(r.work_id), str(r.input_hash)) for r in legacy}


async def completed_sessions(conn: AsyncConnection, unit: str) -> list[dict[str, str]]:
    """Inputs of normally ended sessions and legacy completed readings."""
    rows = (await conn.execute(sa.select(RESULT.c.payload).where(
        RESULT.c.part == "session_complete", RESULT.c.work_id == f"session:{unit}",
    ))).scalars()
    legacy = (await conn.execute(
        sa.select(RESULT.c.work_id, RESULT.c.input_hash)
        .select_from(RESULT.join(
            COGNITION_SESSION, COGNITION_SESSION.c.id == RESULT.c.session_id))
        .where(COGNITION_SESSION.c.unit == unit,
               RESULT.c.part.in_(("readings", "readings_input")))
        .distinct()
    )).all()
    completed = [row["inputs"] for row in rows]
    for work_id, digest in legacy:
        if work_id.startswith("stimuli:"):
            ids = work_id.removeprefix("stimuli:").split(",")
        elif work_id.startswith(f"input:{unit}:"):
            ids = [work_id.rsplit(":", 1)[1]]
        else:
            continue
        completed.append({sid: digest for sid in ids if sid})
    return completed


#: The isolated identity historical attempts write under — the SAME
#: `comprehend_result` receipt mechanism `SqlComprehendRecords` owns, but a
#: distinct `work_id` prefix so a historical failure can never count toward
#: the live wake's parked rule (`stimulus:{unit}:{sid}`). An operator
#: requeues by writing `stimulus_requeue` under the same namespaced key.
FAILURE_KEY = "backfill-stimulus:{unit}:{sid}"


async def fail_historical(
    engine: AsyncEngine, unit: str, stimulus_id: int, session: int | None,
) -> None:
    """One historical-lane failure receipt — same row shape as
    `SqlComprehendRecords.fail_stimulus`, isolated key (see `FAILURE_KEY`).
    A live stimulus's own failure counter is untouched by definition."""
    async with engine.begin() as conn:
        await conn.execute(sa.insert(RESULT).values(
            work_id=FAILURE_KEY.format(unit=unit, sid=stimulus_id),
            input_hash="", session_id=session or 0,
            part="stimulus_failure", payload={"state": "failed"}))


async def failure_counts(
    conn: AsyncConnection, unit: str, stimulus_ids: Collection[int],
) -> dict[int, int]:
    """Per-stimulus HISTORICAL failure count since its newest requeue — the
    same rule `SqlComprehendRecords.failures` computes one id at a time,
    batched over this lane's own namespaced keys (never the live
    `stimulus:` ones)."""
    if not stimulus_ids:
        return {}
    keys = [FAILURE_KEY.format(unit=unit, sid=sid) for sid in stimulus_ids]
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
    out = dict.fromkeys(keys, 0)
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


async def still_open(
    conn: AsyncConnection, unit: str, now_ms: int, *,
    remind_subjects: Sequence[str] = (),
) -> dict[str, Any]:
    """The deterministic still-open selection a catch-up wake reviews,
    from existing tables only — no engine, ledger or block of its own:

    - `commitments`: ledger `standing_commitment` rows not closed
      (`closed_at IS NULL`), each with its own `due_at` — the actual due
      field, so expired-looking entries that were never closed still
      surface (an overdue commitment is still open).
    - `events`: `events` rows of the unit still proposed and not past
      (`status='proposed'` and `when` unset or not before now), the same
      rule `still_open_gatherings` applies to a room's list.
    - `reminds`: the caller's pending-reminder subjects (the live
      `pending_reminds` read the session itself grounds on).

    `closed_at`/`due_at`/`when` are the row's own event-time fields, so the
    set is provably still-open AT `now_ms` — never "anything current".
    """
    commits = (await conn.execute(
        sa.select(LEDGER_COMMITMENT.c.subject, LEDGER_COMMITMENT.c.due_at)
        .where(
            LEDGER_COMMITMENT.c.unit == unit,
            LEDGER_COMMITMENT.c.closed_at.is_(None),
        ).order_by(LEDGER_COMMITMENT.c.due_at.asc().nulls_last()))
    ).all()
    events = (await conn.execute(
        sa.select(EVENTS.c.title, EVENTS.c.when)
        .where(
            EVENTS.c.unit_id == unit,
            EVENTS.c.status == "proposed",
            sa.or_(EVENTS.c.when.is_(None),
                   EVENTS.c.when >= datetime.fromtimestamp(now_ms / 1000, tz=UTC)),
        ).order_by(EVENTS.c.when.asc().nulls_last()))
    ).all()
    src, dst = ENTITY.alias("src"), ENTITY.alias("dst")
    relations = (await conn.execute(sa.select(
        RELATION, src.c.name.label("src_name"), dst.c.name.label("dst_name"),
    ).select_from(RELATION.outerjoin(src, sa.and_(
        src.c.id == RELATION.c.src_id, src.c.kind == RELATION.c.src_kind,
        src.c.unit == unit,
    )).outerjoin(dst, sa.and_(
        dst.c.id == RELATION.c.dst_id, dst.c.kind == RELATION.c.dst_kind,
        dst.c.unit == unit,
    ))).where(
        RELATION.c.unit == unit, RELATION.c.status == "current",
        RELATION.c.valid_to.is_(None),
        RELATION.c.asserted_at <= datetime.fromtimestamp(now_ms / 1000, tz=UTC),
        sa.or_(RELATION.c.rel == "plans", RELATION.c.src_kind == "commitment",
               RELATION.c.dst_kind == "commitment"),
    ).order_by(RELATION.c.id))).mappings().all()
    return {
        "unit": unit, "as_of_ms": now_ms,
        "relations": [
            {"id": row["id"], "rel": row["rel"],
             "src": [row["src_kind"], row["src_id"]],
             "dst": [row["dst_kind"], row["dst_id"]],
             "src_name": row["src_name"], "dst_name": row["dst_name"],
             "valid_from": None if row["valid_from"] is None
             else row["valid_from"].isoformat(),
             "persons": list(row["persons"])}
            for row in relations],
        "commitments": [
            {"subject": str(r.subject),
             "due_ms": None if r.due_at is None else int(r.due_at)}
            for r in commits],
        "events": [
            {"title": str(r.title),
             "when": None if r.when is None else _ms(r.when)}
            for r in events],
        "reminds": list(remind_subjects),
    }


def still_open_item(open_work: Mapping[str, Any]) -> str:
    """The `time` stimulus's `item` for a catch-up: a compact, deterministic
    line of what is still open. `item` is the stimulus's own field rendered
    verbatim by the agenda's declared `_summary` ("a time stimulus is due:
    …") AND matched against `graph.aliases` by `preview_seeds`, so the named
    open work both appears in the session's input AND seeds its graph
    preview — the selection reaches the model through the ordinary assembly,
    not a bespoke prompt."""
    return CATCH_UP_PREFIX + json.dumps(open_work, sort_keys=True)
