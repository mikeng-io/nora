"""The Rollup's builds and its unit rollups over SQLAlchemy Core
(migration 0071).

- **Builds.** `rollup_build` holds one row per build; exactly one is
  `current` and that row is the pointer every reader resolves. `create`
  starts a build, `advance` records the watermark it has reached, `switch`
  makes it current and supersedes the one that was, in ONE transaction, so a
  reader sees either the old build whole or the new one whole. A superseded
  build's rows are never touched again, never deleted.
- **Inputs.** The Record the unit rollups read, bounded by a `RecordMark`
  (the newest row id of each table recorded at or before the watermark):
  person-authored messages of a unit's rooms, reactions to them, Nora's
  sends and what answered them, and source health.
- **Rows.** Each unit rollup is written DIFFED against what the build holds,
  by its natural key, so a run that computes the same rows writes nothing
  (and no version in `row_history`).
- **Citation by overlap.** `resolve` answers a `(kind, subject, interval)`
  citation against the current build.

Not a port with a second implementation: the unit rollups are exercised
against Postgres (`tests/integration/test_rollup_determinism.py`), and their
arithmetic is pure (`nora.domain.rollup`).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.cognition_remind import INTENTS
from nora.domain.models import parse_mentions_json
from nora.domain.rollup import (
    FOLLOW_NAMES,
    FOLLOW_WINDOW_SECONDS,
    Citation,
    Colocation,
    Daybook,
    FollowCandidate,
    HealthRow,
    Home,
    MessageFact,
    Outing,
    ReactionFact,
    Resolution,
    Response,
    ResponseEvidence,
    SendFact,
    StaySpan,
    Tie,
    episode_key,
    lineage_json,
    resolve_by_overlap,
    room_key,
    stay_from_json,
    unquoted_follows,
)
from nora.infra.sql import memory_index
from nora.infra.sql.mobility import current_build_id
from nora.infra.sql.schema import (
    MESSAGE_EDIT,
    MESSAGE_REACTION,
    MESSAGES,
    PRESENCE_DERIVED_PLACES,
    PRESENCE_STAYS,
    PRESENCE_TRIPS,
    ROLLUP_BUILD,
    ROLLUP_COLOCATION,
    ROLLUP_DAYBOOK,
    ROLLUP_FOLD_STATE,
    ROLLUP_OUTING,
    ROLLUP_RESPONSE,
    ROLLUP_TIE,
    SOURCE_HEALTH,
)
from nora.tag import is_tagged


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


@dataclass(frozen=True)
class BuildRow:
    id: int
    code_version: str
    params_sha256: str
    status: str
    through_ms: int


@dataclass(frozen=True)
class RecordMark:
    """The newest id of each message-side Record table recorded at or before
    a watermark: the unit rollups read ids at or below these, so a run and a
    rebuild at the same watermark read the same rows."""

    through_ms: int
    message_id: int
    reaction_id: int
    edit_id: int


@dataclass(frozen=True)
class UnitRooms:
    """What the unit rollups need to know about one unit from `policy.toml`
    and `identity.toml`, as plain values (`nora.infra` never imports the
    policy): its rooms, its members (by subject), and how a platform account
    resolves to a member."""

    unit: str
    rooms: tuple[tuple[str, str], ...]
    members: tuple[str, ...]
    #: `(platform, account id)` -> member subject, for mentions.
    accounts: dict[tuple[str, str], str]


class SqlRollupStore:
    def __init__(self, engine: AsyncEngine, *, metrics: Any = None) -> None:
        """`metrics` is injected, never imported (`SqlMobilityStore`'s
        reason); it counts the memory index's projections."""
        self._engine = engine
        self._metrics = metrics

    # -- builds --------------------------------------------------------------

    async def current_build(self) -> BuildRow | None:
        stmt = sa.select(ROLLUP_BUILD).where(ROLLUP_BUILD.c.status == "current")
        async with self._engine.connect() as conn:
            r = (await conn.execute(stmt)).first()
        return None if r is None else BuildRow(
            id=r.id, code_version=r.code_version, params_sha256=r.params_sha256,
            status=r.status, through_ms=r.through_ms,
        )

    async def create_build(self, *, code_version: str, params_sha256: str) -> int:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.insert(ROLLUP_BUILD).values(
                    code_version=code_version, params_sha256=params_sha256,
                    status="building",
                ).returning(ROLLUP_BUILD.c.id)
            )
            return int(result.scalar_one())

    async def advance_build(self, *, build_id: int, through_ms: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(ROLLUP_BUILD)
                .where(ROLLUP_BUILD.c.id == build_id, ROLLUP_BUILD.c.through_ms < through_ms)
                .values(through_ms=through_ms)
            )

    async def switch_build(self, *, build_id: int) -> None:
        """Make `build_id` current and supersede the build that was, in one
        transaction. The old build's rows stay; the memory index's episodes
        move to the new build in the same transaction, so a reader of the
        index never sees two builds' outings at once."""
        now = sa.func.clock_timestamp()
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(ROLLUP_BUILD)
                .where(ROLLUP_BUILD.c.status == "current", ROLLUP_BUILD.c.id != build_id)
                .values(status="superseded", superseded_at=now, superseded_by=build_id)
            )
            await conn.execute(
                sa.update(ROLLUP_BUILD).where(ROLLUP_BUILD.c.id == build_id)
                .values(status="current", switched_at=now)
            )
            await memory_index.reproject_episodes(conn, metrics=self._metrics)

    async def drain_index_queue(self, *, limit: int = 500) -> int:
        """Re-project what the memory index's projections queued (the
        analytics process drains once per tick). Returns the rows tried."""
        async with self._engine.begin() as conn:
            return await memory_index.drain_queue(conn, limit=limit, metrics=self._metrics)

    async def abandon_build(self, *, build_id: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.update(ROLLUP_BUILD)
                .where(ROLLUP_BUILD.c.id == build_id, ROLLUP_BUILD.c.status == "building")
                .values(status="abandoned")
            )

    # -- the Record, bounded ------------------------------------------------

    async def mark(self, *, through_ms: int) -> RecordMark:
        """The newest ids recorded at or before `through_ms`. Each read walks
        its primary key down from the top and stops at the first row old
        enough, which is a handful of rows."""
        at = _at(through_ms)

        async def newest(table: sa.Table, recorded: Any) -> int:
            stmt = (
                sa.select(table.c.id).where(recorded <= at)
                .order_by(table.c.id.desc()).limit(1)
            )
            async with self._engine.connect() as conn:
                value = (await conn.execute(stmt)).scalar_one_or_none()
            return int(value or 0)

        return RecordMark(
            through_ms=through_ms,
            message_id=await newest(
                MESSAGES, sa.cast(MESSAGES.c.created_at, sa.DateTime(timezone=True)),
            ),
            reaction_id=await newest(
                MESSAGE_REACTION,
                sa.cast(MESSAGE_REACTION.c.created_at, sa.DateTime(timezone=True)),
            ),
            edit_id=await newest(MESSAGE_EDIT, MESSAGE_EDIT.c.recorded_at),
        )

    async def stay_spans(self, *, build_id: int, subjects: Sequence[str]) -> list[StaySpan]:
        """Every closed stay of `subjects` in the build with the place it is
        linked to, and each one's stay in progress from its fold state."""
        if not subjects:
            return []
        stmt = (
            sa.select(
                PRESENCE_STAYS.c.subject_id, PRESENCE_STAYS.c.started_ts,
                PRESENCE_STAYS.c.ended_ts, PRESENCE_STAYS.c.lat, PRESENCE_STAYS.c.lng,
                PRESENCE_STAYS.c.radius_m, PRESENCE_STAYS.c.place_id,
                PRESENCE_DERIVED_PLACES.c.kind, PRESENCE_DERIVED_PLACES.c.label,
                PRESENCE_DERIVED_PLACES.c.saved_place_id,
            )
            .select_from(PRESENCE_STAYS.outerjoin(
                PRESENCE_DERIVED_PLACES,
                PRESENCE_STAYS.c.place_id == PRESENCE_DERIVED_PLACES.c.id,
            ))
            .where(
                PRESENCE_STAYS.c.build_id == build_id,
                PRESENCE_STAYS.c.subject_id.in_(list(subjects)),
            )
            .order_by(PRESENCE_STAYS.c.subject_id, PRESENCE_STAYS.c.started_ts)
        )
        opens = sa.select(ROLLUP_FOLD_STATE.c.subject_id, ROLLUP_FOLD_STATE.c.open_stay).where(
            ROLLUP_FOLD_STATE.c.build_id == build_id,
            ROLLUP_FOLD_STATE.c.subject_id.in_(list(subjects)),
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
            open_rows = (await conn.execute(opens)).all()
        out = [
            StaySpan(
                person_id=r.subject_id, started_ts=r.started_ts, ended_ts=r.ended_ts,
                lat=r.lat, lng=r.lng, radius_m=r.radius_m, place_id=r.place_id,
                place_kind=r.kind, place_label=r.label, saved_place_id=r.saved_place_id,
            )
            for r in rows
        ]
        for r in open_rows:
            stay = stay_from_json(r.open_stay)
            if stay is not None:
                out.append(StaySpan(
                    person_id=r.subject_id, started_ts=stay.started_ts,
                    ended_ts=stay.ended_ts, lat=stay.lat, lng=stay.lng,
                    radius_m=stay.radius_m, open=True,
                ))
        return out

    async def homes(self, *, build_id: int, subjects: Sequence[str]) -> dict[str, list[Home]]:
        stmt = sa.select(
            PRESENCE_DERIVED_PLACES.c.subject_id, PRESENCE_DERIVED_PLACES.c.lat,
            PRESENCE_DERIVED_PLACES.c.lng, PRESENCE_DERIVED_PLACES.c.radius_m,
        ).where(
            PRESENCE_DERIVED_PLACES.c.build_id == build_id,
            PRESENCE_DERIVED_PLACES.c.subject_id.in_(list(subjects)),
            PRESENCE_DERIVED_PLACES.c.kind == "home",
        ).order_by(PRESENCE_DERIVED_PLACES.c.subject_id, PRESENCE_DERIVED_PLACES.c.id)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        out: dict[str, list[Home]] = {}
        for r in rows:
            out.setdefault(r.subject_id, []).append(
                Home(person_id=r.subject_id, lat=r.lat, lng=r.lng, radius_m=r.radius_m)
            )
        return out

    def _in_rooms(self, rooms: Sequence[tuple[str, str]]) -> Any:
        if not rooms:
            return sa.false()
        return sa.or_(*(
            sa.and_(MESSAGES.c.platform == p, MESSAGES.c.conversation_id == c)
            for p, c in rooms
        ))

    async def message_facts(
        self, *, unit: UnitRooms, mark: RecordMark, subject_of: dict[str, str],
        follow_window_ms: int = int(FOLLOW_WINDOW_SECONDS * 1000),
        nora_names: Sequence[str] = FOLLOW_NAMES,
    ) -> tuple[list[MessageFact], list[ReactionFact], list[SendFact], list[ResponseEvidence]]:
        """The unit's person-authored messages (with the person each one
        replies to and the members it mentions), the reactions of members
        to members' messages, Nora's sends, and every reply, reaction or edit
        of a reply that answered a send, and the unquoted messages read as
        answering one (`unquoted_follows`) — all at or below `mark`.

        Her own account ids are the `sender_id` of her rows in the unit's
        rooms, per platform: what her sends were written under, which is the
        platform's own answer to who she is (`PlatformIdentity.primary`). The
        analytics process has no platform registry, so this is the one place
        it can read them from; a mention of her under an id form that never
        wrote a row (a Signal ACI alone) does not resolve here, and the
        message is then read by what it says."""
        quoted = MESSAGES.alias("quoted")
        stmt = (
            sa.select(
                MESSAGES.c.id, MESSAGES.c.platform, MESSAGES.c.conversation_id,
                MESSAGES.c.ts, MESSAGES.c.person_id, MESSAGES.c.is_nora,
                MESSAGES.c.mentions_json, MESSAGES.c.quoted_ts,
                quoted.c.person_id.label("quoted_person"),
                quoted.c.is_nora.label("quoted_is_nora"),
                quoted.c.id.label("quoted_id"),
                MESSAGES.c.sender_id, MESSAGES.c.body,
                # Only Nora's own rows: the raw envelope of a person's message is not read here.
                sa.case((MESSAGES.c.is_nora == 1, MESSAGES.c.raw_json)).label("send_raw"),
            )
            .select_from(MESSAGES.outerjoin(quoted, sa.and_(
                quoted.c.conversation_id == MESSAGES.c.conversation_id,
                quoted.c.platform == MESSAGES.c.platform,
                quoted.c.ts == MESSAGES.c.quoted_ts,
                quoted.c.id <= mark.message_id,
                quoted.c.hidden_at.is_(None),
            )))
            .where(
                self._in_rooms(unit.rooms),
                MESSAGES.c.id <= mark.message_id,
                MESSAGES.c.hidden_at.is_(None),
            )
            .order_by(MESSAGES.c.id, quoted.c.id)
        )
        target = MESSAGES.alias("target")
        reactions_stmt = (
            sa.select(
                MESSAGE_REACTION.c.id, MESSAGE_REACTION.c.person_id,
                MESSAGE_REACTION.c.ts, MESSAGE_REACTION.c.message_id,
                target.c.person_id.label("target_person"),
                target.c.is_nora.label("target_is_nora"),
                target.c.platform, target.c.conversation_id,
            )
            .select_from(
                MESSAGE_REACTION.join(target, target.c.id == MESSAGE_REACTION.c.message_id)
            )
            .where(
                MESSAGE_REACTION.c.id <= mark.reaction_id,
                MESSAGE_REACTION.c.removed == 0,
                MESSAGE_REACTION.c.person_id.is_not(None),
                target.c.hidden_at.is_(None),
                sa.or_(*(
                    sa.and_(target.c.platform == p, target.c.conversation_id == c)
                    for p, c in unit.rooms
                )) if unit.rooms else sa.false(),
            )
            .order_by(MESSAGE_REACTION.c.id)
        )
        edits_stmt = (
            sa.select(MESSAGE_EDIT.c.message_id, MESSAGE_EDIT.c.ts)
            .where(MESSAGE_EDIT.c.id <= mark.edit_id)
            .order_by(MESSAGE_EDIT.c.id)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
            reaction_rows = (await conn.execute(reactions_stmt)).all()
            edit_rows = (await conn.execute(edits_stmt)).all() if unit.rooms else []

        members = set(unit.members)
        messages: list[MessageFact] = []
        sends: dict[int, SendFact] = {}
        #: Sends grouped by the turn (`tag_id`) that delivered them.
        turn_sends: dict[str, list[int]] = {}
        evidence: list[ResponseEvidence] = []
        reply_to_send: dict[int, int] = {}
        #: Her own account ids per platform, and the members' messages that
        #: quote none of her sends (what `unquoted_follows` reads).
        nora_ids: dict[str, set[str]] = {}
        unquoted: list[tuple[Any, str, frozenset[str]]] = []  # (row, person, mentions)
        seen: set[int] = set()
        for r in rows:
            if r.id in seen:
                continue  # two rows share the quoted ts: the lowest id answers
            seen.add(r.id)
            room = room_key(r.platform, r.conversation_id)
            mentioned = tuple(sorted({
                subject for account in (parse_mentions_json(r.mentions_json) or ())
                if (subject := unit.accounts.get((r.platform, account))) is not None
            }))
            if r.is_nora:
                sends[r.id] = SendFact(
                    id=r.id, room=room, platform=r.platform,
                    conversation_id=r.conversation_id, ts=r.ts, named=mentioned,
                    intent=_send_intent(r.send_raw), turn=_send_tag(r.send_raw),
                )
                if r.sender_id:
                    nora_ids.setdefault(r.platform, set()).add(r.sender_id)
                turn = _send_tag(r.send_raw)
                if turn is not None:
                    turn_sends.setdefault(turn, []).append(r.id)
                continue
            person = subject_of.get(r.person_id) if r.person_id else None
            if person is None or person not in members:
                continue
            quoted_person = (
                subject_of.get(r.quoted_person) if r.quoted_person and not r.quoted_is_nora
                else None
            )
            messages.append(MessageFact(
                id=r.id, person_id=person, room=room, ts=r.ts,
                quoted_person=quoted_person, mentioned=mentioned,
            ))
            if r.quoted_is_nora and r.quoted_id is not None:
                reply_to_send[r.id] = r.quoted_id
                evidence.append(ResponseEvidence(
                    send_id=r.quoted_id, person_id=person, ts=r.ts, kind="reply",
                ))
            else:
                unquoted.append((r, person, parse_mentions_json(r.mentions_json) or frozenset()))
        reactions: list[ReactionFact] = []
        for r in reaction_rows:
            person = subject_of.get(r.person_id)
            if person is None or person not in members:
                continue
            if r.target_is_nora:
                evidence.append(ResponseEvidence(
                    send_id=r.message_id, person_id=person, ts=r.ts, kind="reaction",
                ))
                continue
            target_person = subject_of.get(r.target_person) if r.target_person else None
            if target_person is None:
                continue
            reactions.append(ReactionFact(
                id=r.id, person_id=person, target_person=target_person,
                room=room_key(r.platform, r.conversation_id), ts=r.ts,
            ))
        by_reply = {m.id: m for m in messages}
        for r in edit_rows:
            send_id = reply_to_send.get(r.message_id)
            if send_id is not None:
                evidence.append(ResponseEvidence(
                    send_id=send_id, person_id=by_reply[r.message_id].person_id,
                    ts=r.ts, kind="edit",
                ))
        candidates = [
            FollowCandidate(
                id=r.id, person_id=person, room=room_key(r.platform, r.conversation_id),
                ts=r.ts,
                names_nora=bool(ids & nora_ids.get(r.platform, set()))
                or any(is_tagged(r.body or "", word) for word in nora_names),
                # A quote of anyone else's message is a reply to THEM, like a
                # mention of someone else: never a follow inside the window.
                mentions_other=r.quoted_ts is not None
                or (bool(ids) and not ids & nora_ids.get(r.platform, set())),
            )
            for r, person, ids in unquoted
        ]
        evidence.extend(unquoted_follows(
            list(sends.values()), candidates, follow_window_ms=follow_window_ms,
        ))
        # ONE expectation per turn: a multi-part fire turn writes its brief's
        # intent on every part, but only its LAST send carries it here, so
        # the earlier parts of one `ask` are not each counted `silent`.
        for ids in turn_sends.values():
            for earlier in sorted(ids)[:-1]:
                if sends[earlier].intent is not None:
                    sends[earlier] = replace(sends[earlier], intent=None)
        return messages, reactions, list(sends.values()), evidence

    async def health_rows(
        self, *, sources: Sequence[str], start_ms: int, through_ms: int,
    ) -> list[HealthRow]:
        """Source health for `sources`, from the newest row at or before
        `start_ms` of each (the state a span begins in) to `through_ms`,
        recorded at or before the watermark."""
        if not sources:
            return []
        recorded = SOURCE_HEALTH.c.recorded_at <= _at(through_ms)
        inside = (
            sa.select(SOURCE_HEALTH)
            .where(
                SOURCE_HEALTH.c.source.in_(list(sources)),
                SOURCE_HEALTH.c.at_ms > start_ms, SOURCE_HEALTH.c.at_ms <= through_ms,
                recorded,
            )
            .order_by(SOURCE_HEALTH.c.at_ms, SOURCE_HEALTH.c.id)
        )
        async with self._engine.connect() as conn:
            rows = list((await conn.execute(inside)).all())
            for source in sorted(set(sources)):
                before = (
                    sa.select(SOURCE_HEALTH)
                    .where(
                        SOURCE_HEALTH.c.source == source, SOURCE_HEALTH.c.at_ms <= start_ms,
                        recorded,
                    )
                    .order_by(SOURCE_HEALTH.c.at_ms.desc(), SOURCE_HEALTH.c.id.desc())
                )
                # The newest row per scope at or before the span's start.
                seen: set[str] = set()
                for r in (await conn.execute(before.limit(200))).all():
                    if r.scope not in seen:
                        seen.add(r.scope)
                        rows.append(r)
        return sorted(
            (HealthRow(source=r.source, scope=r.scope, event=r.event,
                       outcome=r.outcome, at_ms=r.at_ms) for r in rows),
            key=lambda h: (h.at_ms, h.source, h.scope),
        )

    # -- rows ----------------------------------------------------------------

    async def apply(
        self, *, build_id: int, unit: str, colocations: Sequence[Colocation],
        outings: Sequence[Outing], daybooks: Sequence[Daybook], ties: Sequence[Tie],
        responses: Sequence[Response],
    ) -> dict[str, int]:
        """Make the build's rows for `unit` exactly these, DIFFED by natural
        key: an unchanged row is not written. Returns how many rows of each
        kind were inserted, updated or deleted."""
        changes: dict[str, int] = {}
        touched: list[dict[str, Any]] = []
        async with self._engine.begin() as conn:
            for table, key, rows in (
                (ROLLUP_COLOCATION, ("persons", "valid_from_ms"),
                 [_colocation_values(c) for c in colocations]),
                (ROLLUP_OUTING, ("persons", "valid_from_ms"),
                 [_outing_values(o) for o in outings]),
                (ROLLUP_DAYBOOK, ("person_id", "local_date"),
                 [_daybook_values(d) for d in daybooks]),
                (ROLLUP_TIE, ("person_a", "person_b", "iso_week"),
                 [_tie_values(t) for t in ties]),
                (ROLLUP_RESPONSE, ("message_id",),
                 [_response_values(r) for r in responses]),
            ):
                changes[table.name] = await _diff_apply(
                    conn, table, build_id=build_id, unit=unit, key=key, rows=rows,
                    touched=touched if table is ROLLUP_OUTING else None,
                )
            # Every outing this diff inserted, updated or deleted, by its key
            # before and after: a key the build no longer holds is dropped.
            await memory_index.project_episodes(
                conn, build_id, "outing",
                [_outing_key(r) for r in touched], metrics=self._metrics,
            )
        return changes

    async def rows(self, *, table: sa.Table, build_id: int) -> list[dict[str, Any]]:
        """Every row of one unit rollup table in a build, as plain dicts
        without their surrogate id or system time — what the determinism
        gate compares."""
        stmt = sa.select(table).where(table.c.build_id == build_id)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [
            {k: _plain(v) for k, v in r.items() if k not in ("id", "build_id", "recorded_at")}
            for r in rows
        ]

    async def resolve(self, citation: Citation) -> Resolution:
        """A citation resolved against the CURRENT build: the rows of its
        kind and subject whose valid interval overlaps its interval (25 §2).
        A person-scoped kind (`stay`, `trip`) names the person as its
        subject; a unit kind names `<unit>|<key>` as `Citation` documents."""
        if citation.kind in _PERSON_TABLES:
            ptable = _PERSON_TABLES[citation.kind]
            stmt = sa.select(
                ptable.c.id, ptable.c.started_ts.label("valid_from_ms"),
                ptable.c.ended_ts.label("valid_to_ms"),
            ).where(
                ptable.c.build_id == current_build_id(),
                ptable.c.subject_id == citation.subject,
                ptable.c.started_ts <= citation.end_ms,
                ptable.c.ended_ts >= citation.start_ms,
            )
        else:
            table, where = _citation_scope(citation)
            stmt = sa.select(table.c.id, table.c.valid_from_ms, table.c.valid_to_ms).where(
                table.c.build_id == current_build_id(), *where,
            )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return resolve_by_overlap(
            citation, [(r.id, r.valid_from_ms, r.valid_to_ms) for r in rows],
        )


_PERSON_TABLES: dict[str, sa.Table] = {"stay": PRESENCE_STAYS, "trip": PRESENCE_TRIPS}

_TABLES: dict[str, sa.Table] = {
    "colocation": ROLLUP_COLOCATION, "outing": ROLLUP_OUTING,
    "daybook": ROLLUP_DAYBOOK, "tie": ROLLUP_TIE, "response": ROLLUP_RESPONSE,
}


def _citation_scope(citation: Citation) -> tuple[sa.Table, list[Any]]:
    table = _TABLES.get(citation.kind)
    if table is None:
        raise ValueError(f"not a unit rollup kind: {citation.kind!r}")
    unit, _, key = citation.subject.partition("|")
    where: list[Any] = [table.c.unit == unit]
    if citation.kind in ("colocation", "outing"):
        where.append(table.c.persons == sorted(key.split(",")))
    elif citation.kind == "daybook":
        where.append(table.c.person_id == key)
    elif citation.kind == "tie":
        a, b = sorted(key.split(","))
        where += [table.c.person_a == a, table.c.person_b == b]
    else:
        where.append(table.c.message_id == int(key))
    return table, where


def _plain(value: Any) -> Any:
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, float):
        return round(value, 9)
    return value


def _common(row: Any) -> dict[str, Any]:
    return {
        "unit": row.unit, "lineage": lineage_json(row.lineage),
        "valid_from_ms": row.valid_from_ms, "valid_to_ms": row.valid_to_ms,
        "as_of_ms": row.as_of_ms,
    }


def _colocation_values(c: Colocation) -> dict[str, Any]:
    return {
        **_common(c), "persons": list(c.persons), "lat": c.lat, "lng": c.lng,
        "radius_m": c.radius_m, "place_label": c.place_label,
        "saved_place_id": c.saved_place_id, "at_home_of": list(c.at_home_of),
        "homes_unknown": list(c.homes_unknown), "stay_count": c.stay_count,
        "open": int(c.open),
    }


def _outing_values(o: Outing) -> dict[str, Any]:
    return {
        **_common(o), "persons": list(o.persons), "lat": o.lat, "lng": o.lng,
        "place_label": o.place_label, "saved_place_id": o.saved_place_id,
        "open": int(o.open), "district": o.district, "city": o.city,
    }


def _daybook_values(d: Daybook) -> dict[str, Any]:
    return {
        **_common(d), "person_id": d.person_id, "local_date": d.local_date,
        "stays": list(d.stays), "stay_ms": d.stay_ms, "home_ms": d.home_ms,
        "colocation_ms": d.colocation_ms, "with_persons": list(d.with_persons),
        "outings": d.outings, "rooms": dict(d.rooms), "messages": d.messages,
        "reactions": d.reactions, "items_posted": d.items_posted,
    }


def _tie_values(t: Tie) -> dict[str, Any]:
    return {
        **_common(t), "person_a": t.person_a, "person_b": t.person_b,
        "iso_week": t.iso_week, "replies_ab": t.replies_ab, "replies_ba": t.replies_ba,
        "mentions_ab": t.mentions_ab, "mentions_ba": t.mentions_ba,
        "reactions_ab": t.reactions_ab, "reactions_ba": t.reactions_ba,
        "colocation_ms": t.colocation_ms,
    }


def _response_values(r: Response) -> dict[str, Any]:
    return {
        **_common(r), "message_id": r.message_id, "platform": r.platform,
        "conversation_id": r.conversation_id, "sent_ts": r.sent_ts,
        "replies": r.replies, "reactions": r.reactions, "edits": r.edits,
        "follows": r.follows, "responders": list(r.responders),
        "named_persons": list(r.named_persons),
        "intent": r.intent, "outcome": r.outcome,
    }


def _key_of(row: dict[str, Any], key: Sequence[str]) -> tuple[Any, ...]:
    return tuple(tuple(row[k]) if isinstance(row[k], list) else row[k] for k in key)


def _outing_key(row: dict[str, Any]) -> str:
    """An outing row's memory-index key: its citation, `unit|persons` and its
    interval (`rollup_read`'s subject for it)."""
    subject = f"{row['unit']}|{','.join(row['persons'])}"
    return episode_key(subject, row["valid_from_ms"], row["valid_to_ms"])


async def _diff_apply(
    conn: Any, table: sa.Table, *, build_id: int, unit: str, key: Sequence[str],
    rows: Sequence[dict[str, Any]], touched: list[dict[str, Any]] | None = None,
) -> int:
    """`touched`, when given, collects every row the diff changed, as it was
    and as it is now."""
    columns = list(rows[0]) if rows else []
    stored = {
        _key_of(dict(r), key): dict(r)
        for r in (await conn.execute(
            sa.select(table).where(table.c.build_id == build_id, table.c.unit == unit)
        )).mappings().all()
    }
    wanted = {_key_of(r, key): r for r in rows}
    changed = 0
    doomed = [stored[k]["id"] for k in stored if k not in wanted]
    if doomed:
        await conn.execute(sa.delete(table).where(table.c.id.in_(doomed)))
        changed += len(doomed)
        if touched is not None:
            touched += [stored[k] for k in stored if k not in wanted]
    for k, row in wanted.items():
        prior = stored.get(k)
        if prior is None:
            await conn.execute(sa.insert(table).values(build_id=build_id, **row))
            changed += 1
        elif any(_plain(prior[c]) != _plain(row[c]) for c in columns):
            await conn.execute(
                sa.update(table).where(table.c.id == prior["id"]).values(**row)
            )
            changed += 1
            if touched is not None:
                touched.append(prior)
        else:
            continue
        if touched is not None:
            touched.append(row)
    return changed


def _send_tag(raw_json: str | None) -> str | None:
    """The `tag_id` of the turn that delivered a send, from its raw_json, or None."""
    try:
        tag = json.loads(raw_json).get("tag_id") if raw_json else None
    except (ValueError, AttributeError):
        return None
    return tag if isinstance(tag, str) and tag else None


def _send_intent(raw_json: str | None) -> str | None:
    """The brief's intent a fire turn's Deliverer wrote into the send's raw_json, or None.

    Exactly `tell` or `ask`; a tag reply, an older row or anything else is None, never `tell`."""
    try:
        intent = json.loads(raw_json).get("intent") if raw_json else None
    except (ValueError, AttributeError):
        return None
    return intent if intent in INTENTS else None
