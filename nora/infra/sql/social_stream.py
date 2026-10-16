"""The social stream's Record: `social_item`, `social_poll`, `social_item_state`
(`0107_social_stream`, design/cognition-loop/22-social-stream.md §5).

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

There is ONE implementation and therefore no port and no fake, for the reason
`nora.infra.sql.otp` gives: every test points this class at a real throwaway
Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.social_stream import Counts, PollStage, SocialItem
from nora.infra.sql.schema import SOCIAL_ITEM, SOCIAL_ITEM_STATE, SOCIAL_POLL

Origin = Literal["poll", "backfill"]
Stage = PollStage
ItemState = Literal["gone", "hidden"]

#: Probe outcomes that carry no answer from the platform: the call failed, or
#: the login was halted or backed off on it.
_UNANSWERED = ("halted", "backed_off", "error")


@dataclass(frozen=True)
class ProbeTimes:
    """When a person was last probed at all, and last probed with an answer
    (`recorded_at`, the database's clock). `answered` is None when no probe of
    theirs has been answered."""

    attempted: datetime
    answered: datetime | None


class SqlSocialStreamStore:
    """The stream's three tables, as ten verbs."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def insert_item(
        self, item: SocialItem, *, person: str, origin: Origin, seen_at: datetime,
        media_ref: str | None = None, location: str | None = None,
        tagged: tuple[str, ...] = (), mentioned: tuple[str, ...] = (),
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
    ) -> None:
        """Append one probe or read."""
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(SOCIAL_POLL).values(
                network=network, person=person, stage=stage, outcome=outcome,
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
