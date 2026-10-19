"""The social stream's Record: `social_item`, `social_poll`, `social_item_state`
(`0107_social_stream`), `social_perception` (`0108_social_perception`) and the
`blob_tombstone` a story frame's deleted bytes leave (`0109_blob_tombstone`),
design/cognition-loop/22-social-stream.md §5.

Shaped like `nora.infra.sql.otp`: the engine comes through `__init__` and each
verb is one transaction. Every table is APPEND-ONLY, so there is no UPDATE and
no DELETE here: an item is written once, and what changes about it is a row in
`social_item_state`, or a tombstone once its bytes are gone.

**An item's tagged place can change** (§5): the author adds, edits or removes
the tag after the item was recorded. `social_item.location` is the place as
first recorded; each later change is a `place` state row carrying the new
place (`place`, NULL when the tag was removed). The current place is the
latest `place` row's, else the recorded one (`current_places`). A `place` row
never hides an item: only `gone` and `hidden` do (`HIDING_STATES`).

**A post's or reel's counts move** (§5, `social_item_count`): every list
read carries each item's likes, comments and reposts, and `record_counts`
appends a row only for a recorded item whose counts differ from its latest
row (`counts_changed`). A read that carries nothing writes nothing, and a
count it does not carry is never compared, so a shape change upstream cannot
write unknowns over real counts. `current_counts` is the latest row, read
under the same rule as every other reader.

**The dedupe is Postgres's.** `insert_item` is `ON CONFLICT (network, item_id)
DO NOTHING` and answers whether the row was new, so two pollers recording the
same frame cannot both think it is theirs, and only a new item goes on to
perception.

**The marker is read, not stored.** A person's read marker is the probe value
of their last completed story read: the latest `story` row of `social_poll`
that advanced it. The last counts follow the same rule: the latest row that
advanced them, which is a probe that made no list read due or a completed
list read carrying its probe's counts. A probe that made a list read due does
not advance them, so a list read that fails leaves the rise still due.

**What is read is filtered here, once** (§2). Every read of items for
perception, for a stimulus, for the Rollup's daybook, for a person's
Character session or for `social_read` (`items_of`) leaves out an item
whose person has `social = false` (`opted_out`, given at construction) and an
item with a `gone` or `hidden` state row. The poller's own dedupe
and marker reads are the writer's state and are not filtered.

**The memory index follows every write** (design/cognition-loop/22 §9):
`insert_item`, `record_state` and `record_perception` each project the item
(`nora.infra.sql.memory_index.project`, kind `social`) inside their own
transaction, so a new item is searchable, a gone or hidden one stops being,
and a perception landing re-reads its media's readings. The opt-out is not
projected: the index's read leaves those items out.

There is ONE implementation and therefore no port and no fake, for the reason
`nora.infra.sql.otp` gives: every test points this class at a real throwaway
Postgres.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.rollup import SocialFact, SocialInput
from nora.domain.social_stream import (
    HIDING_STATES,
    PLACE_STATE,
    SOCIAL_READ_REF,
    BackfillEnd,
    ByteDeletionReason,
    Counts,
    ItemState,
    Origin,
    PerceptionOutcome,
    PersonSocialItem,
    PollStage,
    Reading,
    RecordedCounts,
    RecordedItem,
    SocialItem,
    counts_changed,
    cursor_is_bad,
    from_us,
    to_us,
)
from nora.infra.sql import memory_index
from nora.infra.sql.blob_refs import held_elsewhere
from nora.infra.sql.memory_index import current_place, social_source_id
from nora.infra.sql.schema import (
    BLOB_TOMBSTONE,
    EVENTS,
    MEDIA_OBJECTS,
    MESSAGES,
    ROLLUP_BUILD,
    ROLLUP_COLOCATION,
    ROLLUP_OUTING,
    SOCIAL_ITEM,
    SOCIAL_ITEM_COUNT,
    SOCIAL_ITEM_STATE,
    SOCIAL_PERCEPTION,
    SOCIAL_POLL,
    STIMULUS,
)

Stage = PollStage

#: Probe outcomes that carry no answer from the platform: the call failed, or
#: the login was halted or backed off on it.
_UNANSWERED = ("halted", "backed_off", "error", "abandoned", "unavailable")


#: The stages that read a page of a member's list.
_LIST_STAGES = ("items", "backfill", "relist", "recount")

#: Perception outcomes that settle an item for good.
_SETTLED = ("ok", "nothing_found", "failed")


@dataclass(frozen=True)
class Unperceived:
    """An item whose media is still to be perceived: what the pass needs to
    read it and to tell the describer what it is."""

    network: str
    item_id: str
    person: str
    kind: str
    collab: bool
    taken_at: datetime
    origin: Origin
    media: tuple[str, ...]
    #: Its earlier attempts that ended `unavailable`.
    failures: int = 0
    #: Every one of its media's bytes was deleted after it was recorded (a
    #: tombstone written since names it): there is nothing left to read.
    expired: bool = False


@dataclass(frozen=True)
class ReadItem:
    """One item of a read, as its stimulus needs it."""

    item_id: str
    kind: str
    collab: bool
    taken_at: datetime
    caption: str | None
    media: tuple[str, ...]


@dataclass(frozen=True)
class SettledRead:
    """A read that recorded new polled items and has raised no stimulus yet,
    with its items in the order they were recorded."""

    read_id: str
    network: str
    person: str
    items: tuple[ReadItem, ...]
    #: The units its stimulus is already on record in.
    announced: frozenset[str] = frozenset()


def _media(ref: str | None) -> tuple[str, ...]:
    return tuple(filter(None, (ref or "").split(",")))


def _ms(moment: datetime) -> int:
    return round(moment.timestamp() * 1000)


def _gone(deleted: Mapping[str, datetime], sha: str, recorded_at: datetime) -> bool:
    """Whether `sha`'s bytes were deleted after a row recorded at
    `recorded_at`: a row written after the deletion stored them again."""
    at = deleted.get(sha)
    return at is not None and at > recorded_at


@dataclass(frozen=True)
class ExpiringFrame:
    """A story frame past its first bound whose bytes are still held: its
    media not yet tombstoned, and whether its perception exists."""

    network: str
    item_id: str
    expires_at: datetime
    media: tuple[str, ...]
    perceived: bool


@dataclass(frozen=True)
class ProbeTimes:
    """When a person was last probed at all, and last probed with an answer
    (`recorded_at`, the database's clock). `answered` is None when no probe of
    theirs has been answered."""

    attempted: datetime
    answered: datetime | None


@dataclass(frozen=True)
class Backfill:
    """Where one person's back-fill stands. `cursor` is where the next page
    starts (None: the first page); `items` how many items of theirs are
    recorded `origin = backfill`; `failures` the pages that failed since the
    last page read, `rejected` those of them refused upstream; `last_at` when
    a page was last tried (None: never). `probe_at` is the answered probe preceding
    the last completed first-page read: a conservative publication boundary
    before its fetch, retained across older pages and failed attempts."""

    items: int
    cursor: str | None = None
    done: bool = False
    rejected: int = 0
    failures: int = 0
    last_at: datetime | None = None
    probe_at: datetime | None = None


@dataclass(frozen=True)
class Relist:
    """Where one person's re-walk of their recorded items stands (§5): the
    once-only walk that re-lists what the back-fill recorded, so each item's
    tagged place is compared. `cursor` is where the next page starts (None:
    the first page); `pages` how many were read; `oldest` the taken time of
    their oldest recorded item, which a page reaching past ends the walk;
    `failures` and `rejected` as `Backfill`'s."""

    oldest: datetime | None
    cursor: str | None = None
    pages: int = 0
    done: bool = False
    rejected: int = 0
    failures: int = 0
    last_at: datetime | None = None


class SqlSocialStreamStore:
    """The stream's six tables, as twenty-nine verbs."""

    def __init__(
        self, engine: AsyncEngine, *, opted_out: frozenset[str] = frozenset(),
        metrics: Any = None,
    ) -> None:
        self._engine = engine
        self._opted_out = opted_out
        self._metrics = metrics

    def _readable(self) -> list[Any]:
        """An item a reader may see: its person has not opted out, and it has
        no `gone` or `hidden` state row."""
        i, s = SOCIAL_ITEM, SOCIAL_ITEM_STATE
        stated = sa.exists().where(
            s.c.network == i.c.network, s.c.item_id == i.c.item_id,
            s.c.state.in_(HIDING_STATES),
        )
        where: list[Any] = [~stated]
        if self._opted_out:
            where.append(i.c.person.not_in(sorted(self._opted_out)))
        return where

    async def insert_item(
        self, item: SocialItem, *, person: str, origin: Origin, seen_at: datetime,
        media_ref: str | None = None, location: str | None = None,
        tagged: tuple[str, ...] = (), mentioned: tuple[str, ...] = (),
        read_id: str | None = None,
    ) -> bool:
        """Record one item. True when it is new; False when `(network, item_id)`
        was already recorded, in which case nothing was written and the first
        row stands unchanged."""
        stmt = (
            pg_insert(SOCIAL_ITEM)
            .values(
                network=item.network, item_id=item.item_id, person=person,
                account=item.account, kind=item.kind, taken_at=item.taken_at,
                expires_at=item.expires_at, caption=item.caption, alt_text=item.alt_text,
                location=location, tagged=list(tagged), mentioned=list(mentioned),
                media_ref=media_ref, seen_at=seen_at, origin=origin, collab=item.collab,
                read_id=read_id,
            )
            .on_conflict_do_nothing(index_elements=["network", "item_id"])
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
            if result.rowcount:
                await memory_index.project(
                    conn, "social", social_source_id(item.network, item.item_id),
                    metrics=self._metrics,
                )
        return bool(result.rowcount)

    async def record_poll(
        self, *, network: str, person: str, stage: Stage, outcome: str,
        latest_reel_media: int | None = None, media_count: int | None = None,
        total_clips_count: int | None = None, advanced_marker: bool = False,
        advanced_counts: bool = False, latency_ms: int | None = None,
        id: str | None = None, cursor: str | None = None,
        end_reason: BackfillEnd | None = None,
    ) -> None:
        """Append one probe or read. A read passes its own `id`, the
        `read_id` its items were recorded with; a back-fill page its next
        `cursor`, None when the walk ended on it, and its `end_reason`."""
        extra = {} if id is None else {"id": id}
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(SOCIAL_POLL).values(
                **extra, network=network, person=person, stage=stage, outcome=outcome,
                latest_reel_media=latest_reel_media, media_count=media_count,
                total_clips_count=total_clips_count, advanced_marker=advanced_marker,
                advanced_counts=advanced_counts, latency_ms=latency_ms, cursor=cursor,
                end_reason=end_reason,
            ))

    async def record_state(
        self, *, network: str, item_id: str, state: ItemState, reason: str | None = None,
    ) -> None:
        """Append that an item is gone or hidden."""
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(SOCIAL_ITEM_STATE).values(
                network=network, item_id=item_id, state=state, reason=reason,
            ))
            await memory_index.project(
                conn, "social", social_source_id(network, item_id), metrics=self._metrics,
            )

    async def record_place(self, *, network: str, item_id: str, place: str | None) -> None:
        """Append that an item's tagged place is now `place` (None: the tag
        was removed), and re-project it, so the index searches the current
        place and nothing else."""
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(SOCIAL_ITEM_STATE).values(
                network=network, item_id=item_id, state=PLACE_STATE, place=place,
            ))
            await memory_index.project(
                conn, "social", social_source_id(network, item_id), metrics=self._metrics,
            )

    async def current_places(
        self, *, network: str, item_ids: list[str],
    ) -> dict[str, str | None]:
        """Each recorded item among `item_ids` → its current tagged place: the
        latest `place` row's, else the place it was recorded with. An id not
        recorded is absent."""
        if not item_ids:
            return {}
        i = SOCIAL_ITEM
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(i.c.item_id, current_place(i).label("place"))
                .where(i.c.network == network, i.c.item_id.in_(item_ids))
            )).all()
        return {r.item_id: r.place for r in rows}

    async def record_counts(
        self, *, network: str, items: Sequence[SocialItem], read_id: str | None,
    ) -> int:
        """Append the counts `items` carry, one read's, for each recorded item
        among them whose counts differ from its latest row (`counts_changed`)
        → how many rows were written. An item not recorded, or whose read
        carried nothing, writes none."""
        carried = {i.item_id: i.counts for i in items if i.counts.known}
        if not carried:
            return 0
        i = SOCIAL_ITEM
        async with self._engine.begin() as conn:
            recorded = set((await conn.execute(
                sa.select(i.c.item_id)
                .where(i.c.network == network, i.c.item_id.in_(sorted(carried))),
            )).scalars())
            latest = await _latest_counts(conn, [(network, item) for item in recorded])
            rows = [
                {
                    "network": network, "item_id": item, "likes": counts.likes,
                    "comments": counts.comments, "reposts": counts.reposts,
                    "likes_hidden": counts.likes_hidden is True, "read_id": read_id,
                }
                for item, counts in sorted(carried.items())
                if item in recorded and counts_changed(counts, latest.get((network, item)))
            ]
            if rows:
                await conn.execute(sa.insert(SOCIAL_ITEM_COUNT), rows)
        return len(rows)

    async def current_counts(
        self, *, items: Sequence[tuple[str, str]],
    ) -> dict[tuple[str, str], RecordedCounts]:
        """Each readable item among `items` (`(network, item_id)`) → its
        latest counts. Readable is `_readable`'s: an item gone or hidden, or
        of a person who opted out under any id, has none; nor has one never
        counted."""
        if not items:
            return {}
        i = SOCIAL_ITEM
        async with self._engine.connect() as conn:
            readable = (await conn.execute(
                sa.select(i.c.network, i.c.item_id).where(
                    *self._readable(),
                    sa.tuple_(i.c.network, i.c.item_id).in_(sorted(set(items))),
                ),
            )).tuples().all()
            return await _latest_counts(conn, list(readable))

    async def read_marker(self, *, network: str, person: str) -> int | None:
        """The probe value that led to this person's last completed story read,
        or None when none has completed."""
        p = SOCIAL_POLL
        stmt = (
            sa.select(p.c.latest_reel_media)
            .where(
                p.c.network == network, p.c.person == person, p.c.stage == "story",
                p.c.advanced_marker, p.c.latest_reel_media.is_not(None),
            )
            .order_by(p.c.recorded_at.desc(), p.c.id.desc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            return (await conn.execute(stmt)).scalar_one_or_none()

    async def last_counts(self, *, network: str, person: str) -> Counts | None:
        """The counts of this person's latest row that advanced them, or None
        when none has."""
        p = SOCIAL_POLL
        stmt = (
            sa.select(p.c.media_count, p.c.total_clips_count)
            .where(
                p.c.network == network, p.c.person == person, p.c.advanced_counts,
                p.c.media_count.is_not(None), p.c.total_clips_count.is_not(None),
            )
            .order_by(p.c.recorded_at.desc(), p.c.id.desc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return Counts(row.media_count, row.total_clips_count) if row else None

    async def posted(
        self, *, persons: Collection[str], through_ms: int, network: str | None = None,
    ) -> SocialInput:
        """What the Rollup's daybook counts for `persons`: each readable item
        recorded at or before `through_ms`, whenever it was taken (a back-filled
        item lands in its own day), and per person the first answered poll
        recorded by then. A person who has opted out has neither."""
        at = datetime.fromtimestamp(through_ms / 1000, tz=UTC)
        i, p = SOCIAL_ITEM, SOCIAL_POLL
        who = sorted(set(persons) - self._opted_out)
        items = (
            sa.select(i.c.person, i.c.taken_at)
            .where(*self._readable(), i.c.person.in_(who), i.c.recorded_at <= at)
            .order_by(i.c.person, i.c.taken_at, i.c.id)
        )
        first = (
            sa.select(p.c.person, sa.func.min(p.c.recorded_at))
            .where(p.c.person.in_(who), p.c.outcome.not_in(_UNANSWERED), p.c.recorded_at <= at)
            .group_by(p.c.person)
        )
        if network is not None:
            items = items.where(i.c.network == network)
            first = first.where(p.c.network == network)
        async with self._engine.connect() as conn:
            item_rows = (await conn.execute(items)).all()
            poll_rows = (await conn.execute(first)).all()
        return SocialInput(
            items=tuple(SocialFact(r.person, _ms(r.taken_at)) for r in item_rows),
            observed_from={person: _ms(recorded) for person, recorded in poll_rows},
        )

    async def social_activity(
        self, *, person: str, units: Sequence[str], rooms: Sequence[tuple[str, str]],
        now: datetime, timezone: str,
    ) -> bool:
        """A current activity signal only, never its content or coordinates.

        Unit rows read the current Rollup build. Messages are human-authored,
        visible, in a unit room and in the past hour; events name the person
        and today's local date and remain open. Missing sources add no boost.
        """
        if person in self._opted_out:
            return False
        m, e = MESSAGES, EVENTS
        tz = ZoneInfo(timezone)
        start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        event = sa.exists().where(
            e.c.unit_id.in_(units), e.c.people.any(person),
            e.c.status.in_(("proposed", "confirmed")), e.c.when >= start,
            e.c.when < start + timedelta(days=1),
        )
        message = sa.exists().where(
            m.c.person_id == person, m.c.is_nora == 0, m.c.hidden_at.is_(None),
            sa.tuple_(m.c.platform, m.c.conversation_id).in_(rooms),
            m.c.ts >= _ms(now - timedelta(hours=1)), m.c.ts <= _ms(now),
        )
        current = sa.select(ROLLUP_BUILD.c.id).where(ROLLUP_BUILD.c.status == "current")
        episodes = [sa.exists().where(
            table.c.build_id.in_(current), table.c.unit.in_(units),
            table.c.persons.any(person), table.c.open == 1,
            table.c.valid_from_ms <= _ms(now),
        ) for table in (ROLLUP_OUTING, ROLLUP_COLOCATION)]
        async with self._engine.connect() as conn:
            return bool((await conn.execute(sa.select(sa.or_(message, event, *episodes))))
                        .scalar_one())

    async def item_state(self, *, network: str, item_id: str) -> ItemState | None:
        """The item's current state, the latest row, or None when it has none."""
        s = SOCIAL_ITEM_STATE
        stmt = (
            sa.select(s.c.state)
            .where(
                s.c.network == network, s.c.item_id == item_id,
                s.c.state.in_(HIDING_STATES),
            )
            .order_by(s.c.recorded_at.desc(), s.c.id.desc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            state = (await conn.execute(stmt)).scalar_one_or_none()
        return state if state in ("gone", "hidden") else None

    async def known_items(self, *, network: str, item_ids: list[str]) -> frozenset[str]:
        """The ids among `item_ids` already recorded on `network`."""
        if not item_ids:
            return frozenset()
        i = SOCIAL_ITEM
        stmt = sa.select(i.c.item_id).where(i.c.network == network, i.c.item_id.in_(item_ids))
        async with self._engine.connect() as conn:
            return frozenset((await conn.execute(stmt)).scalars())

    async def live_frames(
        self, *, network: str, person: str, now: datetime,
    ) -> frozenset[str]:
        """This person's recorded story frames that have not expired by `now`
        and are not already recorded gone: the frames a story read should
        still show."""
        i, s = SOCIAL_ITEM, SOCIAL_ITEM_STATE
        gone = sa.exists().where(
            s.c.network == i.c.network, s.c.item_id == i.c.item_id, s.c.state == "gone",
        )
        stmt = sa.select(i.c.item_id).where(
            i.c.network == network, i.c.person == person, i.c.kind == "story_frame",
            i.c.expires_at > now, ~gone,
        )
        async with self._engine.connect() as conn:
            return frozenset((await conn.execute(stmt)).scalars())

    async def last_probes(self, *, network: str) -> dict[str, ProbeTimes]:
        """Per person, when they were last probed and last probed with an
        answer: what a poller that has just started schedules from, so a
        restart keeps the cadence's floor and ceiling."""
        p = SOCIAL_POLL
        answered = sa.func.max(p.c.recorded_at).filter(p.c.outcome.not_in(_UNANSWERED))
        stmt = (
            sa.select(p.c.person, sa.func.max(p.c.recorded_at), answered)
            .where(p.c.network == network, p.c.stage == "probe")
            .group_by(p.c.person)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return {person: ProbeTimes(attempted, ok) for person, attempted, ok in rows}

    async def calls_since(
        self, *, network: str, since: datetime,
    ) -> list[tuple[datetime, PollStage]]:
        """Every probe and read recorded on `network` after `since`, oldest
        first, as `(recorded_at, stage)`: the calls a poller that has just
        started counts against its hourly budget. An `abandoned` row marks a
        back-fill's end beside its call's own row and is not a call."""
        p = SOCIAL_POLL
        stmt = (
            sa.select(p.c.recorded_at, p.c.stage)
            .where(
                p.c.network == network, p.c.recorded_at > since,
                p.c.outcome != "abandoned",
            )
            .order_by(p.c.recorded_at, p.c.id)
        )
        async with self._engine.connect() as conn:
            return [(r.recorded_at, r.stage) for r in (await conn.execute(stmt)).all()]

    async def backfills(self, *, network: str, full_history: bool = False) -> dict[str, Backfill]:
        """Where each person's back-fill stands (§7), for every person with a
        completed list read: read off their `backfill` rows, so a restart
        resumes at the cursor the last page left and reads no page twice.

        The latest row that read a page (`read_ok`) or ended the walk
        (`abandoned`) decides: abandoned, or a page with no next cursor, is
        done; otherwise the walk goes on at that page's cursor. A person with
        a list read and no such row (their first list read was recorded
        before the back-fill existed) starts at the first page. `failures`
        counts the pages that failed since then (`error`, `backed_off`) and
        `rejected` those refused upstream; once they make the cursor bad
        (`cursor_is_bad`) it is done too, read off the failure rows
        themselves, so it stops even when no `abandoned` row was written. A
        halt (`halted`) is the login's or the query's, and an `unavailable`
        page the bridge's while it answered nothing: neither is the
        cursor's. In full-history mode, only a recorded `grid_end` or an
        abandoned/bad cursor is done: bounded or legacy terminal pages reopen
        from the first page, whose known items the poller deduplicates."""
        p, i = SOCIAL_POLL, SOCIAL_ITEM
        # ponytail: every probe, list read and back-fill row of the network, folded
        # here; a list read is rare and a back-fill a handful of pages a
        # person, so a window query is not worth it yet.
        rows_stmt = (
            sa.select(p.c.person, p.c.stage, p.c.outcome, p.c.cursor, p.c.recorded_at,
                      p.c.end_reason, p.c.advanced_counts)
            .where(
                p.c.network == network,
                sa.or_(
                    p.c.stage.in_(("probe", "backfill")),
                    sa.and_(p.c.stage == "items", p.c.outcome == "read_ok"),
                ),
            )
            .order_by(p.c.recorded_at, p.c.id)
        )
        counts_stmt = (
            sa.select(i.c.person, sa.func.count())
            .where(i.c.network == network, i.c.origin == "backfill")
            .group_by(i.c.person)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(rows_stmt)).all()
            counts = dict((await conn.execute(counts_stmt)).tuples().all())
        listed: set[str] = set()
        out: dict[str, Backfill] = {}
        probes: dict[str, datetime] = {}
        for r in rows:
            if r.stage == "probe":
                if r.outcome not in _UNANSWERED:
                    probes[r.person] = r.recorded_at
                continue
            if r.outcome == "read_ok":
                listed.add(r.person)
            if r.stage != "backfill":
                was = out.get(r.person, Backfill(items=0, last_at=r.recorded_at))
                out[r.person] = replace(was, probe_at=probes.get(r.person))
                continue
            was = out.get(r.person, Backfill(items=0))
            if r.outcome == "abandoned":
                out[r.person] = Backfill(items=0, done=True, last_at=r.recorded_at)
            elif r.outcome == "read_ok":
                out[r.person] = Backfill(
                    items=0, cursor=r.cursor,
                    done=r.cursor is None and (not full_history or r.end_reason == "grid_end"),
                    last_at=r.recorded_at,
                    probe_at=probes.get(r.person) if r.advanced_counts else was.probe_at,
                )
            else:
                failed = r.outcome in ("error", "backed_off")
                rejected = was.rejected + (r.outcome == "backed_off")
                failures = was.failures + failed
                out[r.person] = Backfill(
                    items=0, cursor=was.cursor, rejected=rejected, failures=failures,
                    done=was.done or (
                        was.cursor is not None
                        and cursor_is_bad(rejected=rejected, failures=failures)
                    ),
                    last_at=r.recorded_at, probe_at=was.probe_at,
                )
        return {
            person: replace(out.get(person, Backfill(items=0)), items=int(counts.get(person, 0)))
            for person in listed
        }

    async def last_list_reads(self, *, network: str) -> dict[str, datetime]:
        """Per person, when a list page of theirs was last read, at any stage
        (a list read, a back-fill, a re-walk or a recount page): what the
        recount waits `recount_interval_hours` after (§5). A person whose list
        was never read is absent."""
        p = SOCIAL_POLL
        stmt = (
            sa.select(p.c.person, sa.func.max(p.c.recorded_at))
            .where(
                p.c.network == network, p.c.outcome == "read_ok",
                p.c.stage.in_(_LIST_STAGES),
            )
            .group_by(p.c.person)
        )
        async with self._engine.connect() as conn:
            return dict((await conn.execute(stmt)).tuples().all())

    async def relists(self, *, network: str) -> dict[str, Relist]:
        """Where each person with a recorded item stands in the re-walk of
        their items (§5), read off their `relist` rows as `backfills` reads
        the back-fill's: a page read (`read_ok`) moves the cursor and ends the
        walk when it carries none; `abandoned` ends it; failures since the
        last page read count toward `cursor_is_bad`, which ends it too."""
        p, i = SOCIAL_POLL, SOCIAL_ITEM
        rows_stmt = (
            sa.select(p.c.person, p.c.outcome, p.c.cursor, p.c.recorded_at)
            .where(p.c.network == network, p.c.stage == "relist")
            .order_by(p.c.recorded_at, p.c.id)
        )
        oldest_stmt = (
            sa.select(i.c.person, sa.func.min(i.c.taken_at))
            .where(i.c.network == network)
            .group_by(i.c.person)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(rows_stmt)).all()
            oldest = dict((await conn.execute(oldest_stmt)).tuples().all())
        out = {person: Relist(oldest=at) for person, at in oldest.items()}
        for r in rows:
            was = out.get(r.person, Relist(oldest=None))
            if r.outcome == "abandoned":
                out[r.person] = replace(was, done=True, last_at=r.recorded_at)
            elif r.outcome == "read_ok":
                out[r.person] = replace(
                    was, cursor=r.cursor, pages=was.pages + 1, done=r.cursor is None,
                    rejected=0, failures=0, last_at=r.recorded_at,
                )
            elif r.outcome in ("error", "backed_off"):
                rejected = was.rejected + (r.outcome == "backed_off")
                failures = was.failures + 1
                out[r.person] = replace(
                    was, rejected=rejected, failures=failures, last_at=r.recorded_at,
                    done=was.done or cursor_is_bad(rejected=rejected, failures=failures),
                )
            else:
                out[r.person] = replace(was, last_at=r.recorded_at)
        return out

    # -- the readers: perception and the stimulus -----------------------------

    async def unperceived(self, *, since: datetime, limit: int) -> list[Unperceived]:
        """Readable items with media that no attempt has settled (perceived,
        nothing to read, or failed for good) and none has tried since `since`
        (the start of the pass's day): polled items first, then back-filled,
        each newest first, with how many earlier reads of theirs failed. An
        item that failed or was capped today waits for a later day."""
        i, p = SOCIAL_ITEM, SOCIAL_PERCEPTION
        tried = sa.exists().where(
            p.c.network == i.c.network, p.c.item_id == i.c.item_id,
            sa.or_(p.c.outcome.in_(_SETTLED), p.c.at >= since),
        )
        failures = (
            sa.select(sa.func.count()).where(
                p.c.network == i.c.network, p.c.item_id == i.c.item_id,
                p.c.outcome == "unavailable",
            ).scalar_subquery()
        )
        stmt = (
            sa.select(
                i.c.network, i.c.item_id, i.c.person, i.c.kind, i.c.collab,
                i.c.taken_at, i.c.origin, i.c.media_ref, i.c.recorded_at,
                failures.label("failures"),
            )
            .where(*self._readable(), i.c.media_ref.is_not(None), i.c.media_ref != "", ~tried)
            .order_by(sa.case((i.c.origin == "poll", 0), else_=1), i.c.taken_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
            deleted = await self._last_deleted(
                conn, [sha for r in rows for sha in _media(r.media_ref)],
            )
        return [
            Unperceived(
                network=r.network, item_id=r.item_id, person=r.person, kind=r.kind,
                collab=r.collab, taken_at=r.taken_at, origin=r.origin,
                media=_media(r.media_ref), failures=int(r.failures),
                expired=all(
                    _gone(deleted, sha, r.recorded_at) for sha in _media(r.media_ref)
                ),
            )
            for r in rows
        ]

    async def perception_used(self, *, since: datetime) -> dict[str, dict[str, int]]:
        """Vision calls spent since `since`, per unit and per origin
        (`{unit: {"poll": n, "backfill": m}}`)."""
        p = SOCIAL_PERCEPTION
        unit = sa.func.unnest(p.c.units).label("unit")
        inner = sa.select(unit, p.c.origin, p.c.calls).where(p.c.at >= since).subquery()
        stmt = sa.select(
            inner.c.unit, inner.c.origin, sa.func.sum(inner.c.calls),
        ).group_by(inner.c.unit, inner.c.origin)
        out: dict[str, dict[str, int]] = {}
        async with self._engine.connect() as conn:
            for u, origin, calls in (await conn.execute(stmt)).all():
                out.setdefault(u, {})[origin] = int(calls or 0)
        return out

    async def record_perception(
        self, *, network: str, item_id: str, person: str, units: tuple[str, ...],
        origin: Origin, outcome: PerceptionOutcome, calls: int, at: datetime,
        reason: str | None = None,
    ) -> None:
        """Append one attempt to perceive an item's media."""
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(SOCIAL_PERCEPTION).values(
                network=network, item_id=item_id, person=person, units=list(units),
                origin=origin, outcome=outcome, calls=calls, at=at, reason=reason,
            ))
            await memory_index.project(
                conn, "social", social_source_id(network, item_id), metrics=self._metrics,
            )

    async def settled_reads(
        self, *, require_perceived: bool, units: Mapping[str, tuple[str, ...]],
        since: datetime,
    ) -> list[SettledRead]:
        """Every COMPLETED read that recorded readable polled items and has
        not yet raised its `social` stimulus in every unit `units` lists for
        its person, with the units it already has. A read is completed when
        its own `social_poll` row (id = `read_id`) is on record: the poller
        writes that row after the read's last item, so a pass that runs
        mid-read never announces part of it. With `require_perceived`, only a
        read each of whose readable items with media has an attempt on record
        (perceived, failed or capped): its stimulus waits for them.
        Only a read completed after `since` is found: a unit a person joins
        later is owed their next reads, not a wake for each one they had
        before. Back-filled items belong to no stimulus; a person in no unit's
        room has nothing to raise."""
        wanted = {p: u for p, u in units.items() if u}
        if not wanted:
            return []
        i, p, st, poll = SOCIAL_ITEM, SOCIAL_PERCEPTION, STIMULUS, SOCIAL_POLL
        ref = sa.literal(f"{SOCIAL_READ_REF}:") + sa.cast(i.c.read_id, sa.Text)
        expected = sa.case(
            *((i.c.person == person, sa.literal(list(us), type_=pg.ARRAY(sa.Text)))
              for person, us in wanted.items()),
            else_=sa.literal([], type_=pg.ARRAY(sa.Text)),
        )
        announced = (
            sa.select(sa.func.coalesce(
                sa.func.array_agg(sa.distinct(st.c.unit)),
                sa.literal([], type_=pg.ARRAY(sa.Text)),
            ))
            .where(st.c.kind == "social", st.c.ref == ref)
            .scalar_subquery()
        )
        still_due = (
            sa.select(sa.func.count(sa.distinct(st.c.unit)))
            .where(st.c.kind == "social", st.c.ref == ref, st.c.unit == sa.any_(expected))
            .scalar_subquery()
        ) < sa.func.cardinality(expected)
        completed = sa.exists().where(
            poll.c.id == i.c.read_id, poll.c.outcome == "read_ok", poll.c.recorded_at > since,
        )
        where = [
            *self._readable(), i.c.origin == "poll", i.c.read_id.is_not(None),
            i.c.person.in_(sorted(wanted)), completed, still_due,
        ]
        if require_perceived:
            other = SOCIAL_ITEM.alias("other")
            attempted = sa.exists().where(
                p.c.network == other.c.network, p.c.item_id == other.c.item_id,
            )
            waiting = sa.exists().where(
                other.c.read_id == i.c.read_id, other.c.media_ref.is_not(None),
                other.c.media_ref != "", ~attempted,
                ~sa.exists().where(
                    SOCIAL_ITEM_STATE.c.network == other.c.network,
                    SOCIAL_ITEM_STATE.c.item_id == other.c.item_id,
                    SOCIAL_ITEM_STATE.c.state.in_(HIDING_STATES),
                ),
            )
            where.append(~waiting)
        stmt = (
            sa.select(
                i.c.read_id, i.c.network, i.c.person, i.c.item_id, i.c.kind, i.c.collab,
                i.c.taken_at, i.c.caption, i.c.media_ref, announced.label("announced"),
            )
            .where(*where)
            .order_by(i.c.read_id, i.c.recorded_at, i.c.id)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        reads: dict[str, SettledRead] = {}
        for r in rows:
            item = ReadItem(
                item_id=r.item_id, kind=r.kind, collab=r.collab, taken_at=r.taken_at,
                caption=r.caption, media=_media(r.media_ref),
            )
            got = reads.get(r.read_id)
            reads[r.read_id] = SettledRead(
                read_id=r.read_id, network=r.network, person=r.person,
                items=(*(got.items if got else ()), item),
                announced=frozenset(r.announced or ()),
            )
        return list(reads.values())

    # -- the reader: `social_read` (§6) ----------------------------------------

    async def items_of(
        self, *, persons: Sequence[str], start: datetime, end: datetime, limit: int,
    ) -> list[RecordedItem]:
        """One person's readable items posted in `[start, end)`, newest first,
        at most `limit`: polled and back-filled alike, each with the readings
        its media already have (`media_objects` title and description, in the
        item's media order) and its current tagged place (`current_place`).
        `persons` is every id the person's items may be recorded under, their
        current id and each retired one. Readable is `_readable`'s: not opted
        out, and no `gone` or `hidden` state row; a `place` row hides
        nothing. Each carries its latest counts, None while never counted."""
        if not persons:
            return []
        i = SOCIAL_ITEM
        stmt = (
            sa.select(
                i.c.network, i.c.item_id, i.c.kind, i.c.collab, i.c.taken_at, i.c.expires_at,
                # The place as the author has it now: a tag they changed shows
                # the new place, one they removed shows none.
                i.c.caption, current_place(i).label("location"), i.c.media_ref,
            )
            .where(
                *self._readable(), i.c.person.in_(sorted(set(persons))),
                i.c.taken_at >= start, i.c.taken_at < end,
            )
            .order_by(i.c.taken_at.desc(), i.c.id.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
            shas = sorted({sha for r in rows for sha in _media(r.media_ref)})
            m = MEDIA_OBJECTS
            readings = {
                r.sha256: "\n".join(part for part in (r.title, r.description) if part)
                for r in await conn.execute(
                    sa.select(m.c.sha256, m.c.title, m.c.description)
                    .where(m.c.sha256.in_(shas)),
                )
            } if shas else {}
            # The rows are readable already; their latest counts, if any.
            counts = await _latest_counts(conn, [(r.network, r.item_id) for r in rows])
        return [
            RecordedItem(
                network=r.network, kind=r.kind, collab=r.collab, taken_at=r.taken_at,
                expires_at=r.expires_at, caption=r.caption, location=r.location,
                perception=tuple(
                    readings[sha] for sha in _media(r.media_ref) if readings.get(sha)
                ),
                counts=counts.get((r.network, r.item_id)),
            )
            for r in rows
        ]

    # -- the reader: a person's Character session (§6) -------------------------
    #
    # Positioned by `recorded_at`, which follows commit order here: the poller
    # is the one writer and records items one transaction at a time.
    # ponytail: a second concurrent writer could commit an earlier
    # `recorded_at` after a later one was read; a serial column if one appears.

    async def polled_count(self, *, persons: Sequence[str], after_us: int) -> int:
        """How many readable items of one person (recorded under any of
        `persons`: their id and every retired one) a poll recorded after
        `after_us` (microseconds since the epoch, `to_us`): their wake
        pressure on Character. A back-filled item never counts: a back-fill
        of 50 items is history, not 50 items of news (§6)."""
        i = SOCIAL_ITEM
        stmt = sa.select(sa.func.count()).select_from(i).where(
            *self._readable(), i.c.person.in_(list(persons)), i.c.origin == "poll",
            i.c.recorded_at > from_us(after_us),
        )
        async with self._engine.connect() as conn:
            return int((await conn.execute(stmt)).scalar_one())

    async def person_items(
        self, *, persons: Sequence[str], after_us: int, limit: int,
    ) -> list[PersonSocialItem]:
        """The OLDEST `limit` readable items of one person (recorded under any
        of `persons`: their id and every retired one) recorded after
        `after_us`, polled and back-filled alike, in the order recorded, each
        with what perception made of its media in `media_ref`'s order (a
        medium nothing has read yet has an empty reading). The read ends
        before a microsecond it would split, so a cursor advanced to its last
        item skips none. Handed in that order and resumed from the
        last one handed, every item is handed once, however many arrive at
        once (a back-fill records 50)."""
        if limit <= 0:
            return []
        i = SOCIAL_ITEM
        cols = (
            i.c.id, i.c.network, i.c.item_id, i.c.kind, i.c.collab, i.c.taken_at,
            i.c.origin, i.c.caption, i.c.media_ref, i.c.recorded_at,
        )
        mine = [*self._readable(), i.c.person.in_(list(persons))]
        m = MEDIA_OBJECTS
        async with self._engine.connect() as conn:
            rows = list((await conn.execute(
                sa.select(*cols)
                .where(*mine, i.c.recorded_at > from_us(after_us))
                .order_by(i.c.recorded_at, i.c.id)
                .limit(limit + 1)
            )).all())
            if len(rows) > limit:
                # A cursor at the last one handed must skip nothing recorded in
                # the same microsecond: when the next row shares it, stop short
                # of that microsecond, or, when every row is in it, take all of it.
                last = rows[limit - 1].recorded_at
                if rows[limit].recorded_at != last:
                    rows = rows[:limit]
                else:
                    rows = [r for r in rows if r.recorded_at < last] or list(
                        (await conn.execute(
                            sa.select(*cols).where(*mine, i.c.recorded_at == last)
                            .order_by(i.c.id)
                        )).all())
            shas = sorted({sha for r in rows for sha in _media(r.media_ref)})
            read = {
                r.sha256: Reading(r.title, r.description, r.content)
                for r in (await conn.execute(
                    sa.select(m.c.sha256, m.c.title, m.c.description, m.c.content)
                    .where(m.c.sha256.in_(shas))
                )).all()
            } if shas else {}
        return [
            PersonSocialItem(
                network=r.network, item_id=r.item_id, kind=r.kind, collab=r.collab,
                taken_at=r.taken_at, origin=r.origin, caption=r.caption,
                readings=tuple(read.get(sha, Reading()) for sha in _media(r.media_ref)),
                recorded_us=to_us(r.recorded_at),
            )
            for r in rows
        ]

    # -- a story frame's bytes (§5) --------------------------------------------

    @staticmethod
    async def _last_deleted(conn: Any, shas: list[str]) -> dict[str, datetime]:
        """Per address, when its newest deletion was decided (`deleted_at`)."""
        if not shas:
            return {}
        t = BLOB_TOMBSTONE
        rows = await conn.execute(
            sa.select(t.c.sha256, sa.func.max(t.c.deleted_at))
            .where(t.c.sha256.in_(sorted(set(shas))))
            .group_by(t.c.sha256),
        )
        return dict(rows.tuples().all())

    async def frames_past_bound(self, *, before: datetime) -> list[ExpiringFrame]:
        """Every story frame that expired at or before `before` and still has
        bytes held: of every person and in every state, since a frame's bound
        is on its bytes, not on who may read it. Its media lists only the
        addresses not deleted since it was recorded; `perceived` is whether
        an attempt read it (`ok`). Oldest first."""
        i, p = SOCIAL_ITEM, SOCIAL_PERCEPTION
        perceived = sa.exists().where(
            p.c.network == i.c.network, p.c.item_id == i.c.item_id, p.c.outcome == "ok",
        )
        stmt = (
            sa.select(
                i.c.network, i.c.item_id, i.c.expires_at, i.c.media_ref, i.c.recorded_at,
                perceived.label("perceived"),
            )
            .where(
                i.c.kind == "story_frame", i.c.expires_at <= before,
                i.c.media_ref.is_not(None), i.c.media_ref != "",
            )
            .order_by(i.c.expires_at, i.c.id)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
            deleted = await self._last_deleted(
                conn, [sha for r in rows for sha in _media(r.media_ref)],
            )
        out = []
        for r in rows:
            held = tuple(
                sha for sha in _media(r.media_ref) if not _gone(deleted, sha, r.recorded_at)
            )
            if held:
                out.append(ExpiringFrame(
                    network=r.network, item_id=r.item_id, expires_at=r.expires_at,
                    media=held, perceived=bool(r.perceived),
                ))
        return out

    async def held_elsewhere(
        self, *, shas: list[str], letting_go: list[tuple[str, str]],
    ) -> set[str]:
        """The addresses among `shas` another row still names
        (`nora.infra.sql.blob_refs.held_elsewhere`), other than the frames
        `letting_go`."""
        async with self._engine.connect() as conn:
            return await held_elsewhere(conn, shas, letting_go=letting_go)

    async def now(self) -> datetime:
        """The database's clock, the one every row's `recorded_at` is on: a
        tombstone's `deleted_at` is read from it before the deletion, so a
        row written after that instant stored the bytes again."""
        async with self._engine.connect() as conn:
            now: datetime = (
                await conn.execute(sa.select(sa.func.clock_timestamp()))
            ).scalar_one()
        return now

    async def record_tombstone(
        self, *, sha256: str, reason: ByteDeletionReason, deleted_at: datetime,
    ) -> None:
        """Append that a story frame's bytes are deleted: gone for every row
        naming them recorded before `deleted_at` (`now`, read before the
        deletion), never for one recorded after."""
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(BLOB_TOMBSTONE).values(
                sha256=sha256, kind="story_frame", reason=reason, deleted_at=deleted_at,
            ))


async def _latest_counts(
    conn: Any, items: Sequence[tuple[str, str]],
) -> dict[tuple[str, str], RecordedCounts]:
    """Each of `items` (`(network, item_id)`) that has a `social_item_count`
    row → its latest. Unfiltered: a caller passes only items it may show or
    write for."""
    if not items:
        return {}
    c = SOCIAL_ITEM_COUNT
    rows = (await conn.execute(
        sa.select(
            c.c.network, c.c.item_id, c.c.likes, c.c.comments, c.c.reposts,
            c.c.likes_hidden, c.c.recorded_at,
        )
        .where(sa.tuple_(c.c.network, c.c.item_id).in_(sorted(set(items))))
        .distinct(c.c.network, c.c.item_id)
        .order_by(c.c.network, c.c.item_id, c.c.recorded_at.desc(), c.c.id.desc()),
    )).all()
    return {
        (r.network, r.item_id): RecordedCounts(
            likes=r.likes, comments=r.comments, reposts=r.reposts,
            likes_hidden=r.likes_hidden, read_at=r.recorded_at,
        )
        for r in rows
    }


def readable_social_store(
    engine: AsyncEngine, persons: Any, *, metrics: Any = None,
) -> SqlSocialStreamStore:
    """The social stream's store for every reader: the poller's,
    `social_read`'s, a person's Character session's and the Rollup's. It
    leaves out each item of a person whose `identity.toml` block sets
    `social = false`, under their current id and every retired one
    (`Persons.social_off_ids`). The one store built without it is the story
    expiry's, which deletes every person's frames' bytes at their bound."""
    return SqlSocialStreamStore(engine, opted_out=persons.social_off_ids(), metrics=metrics)
