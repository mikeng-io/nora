"""The social stream's Record: `social_item`, `social_poll`, `social_item_state`
(`0107_social_stream`) and `social_perception` (`0108_social_perception`),
design/cognition-loop/22-social-stream.md §5.

Shaped like `nora.infra.sql.otp`: the engine comes through `__init__` and each
verb is one transaction. All three tables are APPEND-ONLY, so there is no
UPDATE and no DELETE here: an item is written once, and what changes about it
is a row in `social_item_state`.

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
perception or for a stimulus leaves out an item
whose person has `social = false` (`opted_out`, given at construction) and an
item with a `social_item_state` row (gone or hidden). The poller's own dedupe
and marker reads are the writer's state and are not filtered.

There is ONE implementation and therefore no port and no fake, for the reason
`nora.infra.sql.otp` gives: every test points this class at a real throwaway
Postgres.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.social_stream import (
    SOCIAL_READ_REF,
    Counts,
    Origin,
    PerceptionOutcome,
    PollStage,
    SocialItem,
)
from nora.infra.sql.schema import (
    SOCIAL_ITEM,
    SOCIAL_ITEM_STATE,
    SOCIAL_PERCEPTION,
    SOCIAL_POLL,
    STIMULUS,
)

Stage = PollStage
ItemState = Literal["gone", "hidden"]

#: Probe outcomes that carry no answer from the platform: the call failed, or
#: the login was halted or backed off on it.
_UNANSWERED = ("halted", "backed_off", "error")


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


@dataclass(frozen=True)
class ProbeTimes:
    """When a person was last probed at all, and last probed with an answer
    (`recorded_at`, the database's clock). `answered` is None when no probe of
    theirs has been answered."""

    attempted: datetime
    answered: datetime | None


class SqlSocialStreamStore:
    """The stream's four tables, as fourteen verbs."""

    def __init__(self, engine: AsyncEngine, *, opted_out: frozenset[str] = frozenset()) -> None:
        self._engine = engine
        self._opted_out = opted_out

    def _readable(self) -> list[Any]:
        """An item a reader may see: its person has not opted out, and it has
        no `social_item_state` row (gone or hidden)."""
        i, s = SOCIAL_ITEM, SOCIAL_ITEM_STATE
        stated = sa.exists().where(s.c.network == i.c.network, s.c.item_id == i.c.item_id)
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
        return bool(result.rowcount)

    async def record_poll(
        self, *, network: str, person: str, stage: Stage, outcome: str,
        latest_reel_media: int | None = None, media_count: int | None = None,
        total_clips_count: int | None = None, advanced_marker: bool = False,
        advanced_counts: bool = False, latency_ms: int | None = None,
        id: str | None = None,
    ) -> None:
        """Append one probe or read. A read passes its own `id`, the
        `read_id` its items were recorded with."""
        extra = {} if id is None else {"id": id}
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(SOCIAL_POLL).values(
                **extra, network=network, person=person, stage=stage, outcome=outcome,
                latest_reel_media=latest_reel_media, media_count=media_count,
                total_clips_count=total_clips_count, advanced_marker=advanced_marker,
                advanced_counts=advanced_counts, latency_ms=latency_ms,
            ))

    async def record_state(
        self, *, network: str, item_id: str, state: ItemState, reason: str | None = None,
    ) -> None:
        """Append that an item is gone or hidden."""
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(SOCIAL_ITEM_STATE).values(
                network=network, item_id=item_id, state=state, reason=reason,
            ))

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

    async def item_state(self, *, network: str, item_id: str) -> ItemState | None:
        """The item's current state, the latest row, or None when it has none."""
        s = SOCIAL_ITEM_STATE
        stmt = (
            sa.select(s.c.state)
            .where(s.c.network == network, s.c.item_id == item_id)
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
        started counts against its hourly budget."""
        p = SOCIAL_POLL
        stmt = (
            sa.select(p.c.recorded_at, p.c.stage)
            .where(p.c.network == network, p.c.recorded_at > since)
            .order_by(p.c.recorded_at, p.c.id)
        )
        async with self._engine.connect() as conn:
            return [(r.recorded_at, r.stage) for r in (await conn.execute(stmt)).all()]

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
                i.c.taken_at, i.c.origin, i.c.media_ref, failures.label("failures"),
            )
            .where(*self._readable(), i.c.media_ref.is_not(None), i.c.media_ref != "", ~tried)
            .order_by(sa.case((i.c.origin == "poll", 0), else_=1), i.c.taken_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            Unperceived(
                network=r.network, item_id=r.item_id, person=r.person, kind=r.kind,
                collab=r.collab, taken_at=r.taken_at, origin=r.origin,
                media=_media(r.media_ref), failures=int(r.failures),
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
