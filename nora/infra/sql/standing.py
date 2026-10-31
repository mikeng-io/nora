"""`SqlStandingStore` — the debrief's per-conversation position, read before
every fold and revised in place after it.

`__init__(self, engine: AsyncEngine)` and nothing else, matching
`SqlTagTraceStore`/`SqlNoteStore`'s own contract-suite
affordance: no config, no clock, no metrics. `updated_at` arrives on the
`Standing` itself, so a fixture in another session can build this class from a
bare `AsyncEngine` and never has to guess what else it needs.

**The write is an UPSERT, not an insert-or-update pair.** R-10 makes two tags
for one conversation impossible to run concurrently, but the fire path reaches
the same conversation through the same slot, and a read-then-insert would race
into a duplicate-key error on exactly the turn that was already slow enough for
the window to matter. `ON CONFLICT` makes the race unrepresentable rather than
unlikely.

**`fold_count` is written, never incremented in SQL.** The caller holds the
prior it just read and computes `prior.fold_count + 1` there. It is a plain
count nothing resets, computed in the caller rather than in SQL. Arithmetic in
the sink, not in the statement.

**The upsert carries an APPEND beside it, in the same transaction.**
`standing_revision` records what the head was set to each time it moved. Both statements are inside
the one
`engine.begin()` block, so «the head moved and nothing recorded it» and «a
revision exists for a position the head never took» are both states this class
cannot produce, rather than states it merely does not intend. That matters
because a refused write must advance NEITHER: the body ceiling is checked on
both tables, so a body over it rolls the pair back together.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.lineage import (
    BODY,
    HOLD_OPEN,
    I_OWE,
    WORKING_SET,
    Lineage,
    own_room_lineage,
    room_key,
)
from nora.domain.standing import (
    Standing,
    StandingHead,
    working_set_from_json,
    working_set_to_json,
)
from nora.infra.sql.schema import MESSAGES, STANDING, STANDING_REVISION

#: A Standing row's sections (`nora.domain.lineage`): `Where it stands` (`BODY`),
#: `I hold open`, `I owe` and `Found, not yet given` (`WORKING_SET`), from
#: `0076_standing_sections`. `HOLD_OPEN`/`I_OWE` carry the SAME lineage as
#: `BODY`/`WORKING_SET`: the fold that writes them reads the same room, so
#: `own_room_lineage` applies to every section a room-Standing row has.
SECTIONS: tuple[str, ...] = (BODY, HOLD_OPEN, I_OWE, WORKING_SET)


def _derived(standing: Standing) -> dict[str, Any]:
    """The columns every derived row carries (migration `0073_lineage`), computed by code from the
    row:
    the fold reads its own room, and is shown a prompt it does not cite
    (`own_room_lineage`); `as_of` is the time of the newest message it read
    (`last_folded_row_id`), else the fold's own instant."""
    as_of: Any = standing.updated_at
    if standing.last_folded_row_id is not None:
        as_of = sa.func.coalesce(
            sa.select(MESSAGES.c.ts)
            .where(
                MESSAGES.c.id == standing.last_folded_row_id,
                MESSAGES.c.hidden_at.is_(None),
            )
            .scalar_subquery(),
            standing.updated_at,
        )
    # The room's own defaults UNION the lineage the write actually carried:
    # `standing.lineage` is where consumed Record requirements (and the
    # prior's inherited gates) live — stamping only `own_room_lineage` would
    # launder them away (design 20 §4, sources derived by code and carried
    # to the write).
    own = own_room_lineage(
        room_key(standing.platform, standing.conversation_id), SECTIONS,
    )
    merged = Lineage.of({
        s: tuple(dict.fromkeys((*own.section(s), *standing.lineage.section(s))))
        for s in SECTIONS
    })
    return {
        "lineage": merged.to_json(),
        "exclude_persons": list(standing.exclude_persons),
        "as_of": as_of,
    }


def _standing(row: sa.Row[Any]) -> Standing:
    """The shared row -> `Standing` mapping for `read` and `read_as_of`.

    One mapping rather than two, because the two tables carry the same six
    values by construction — `write` copies them across in one transaction — and
    a second mapping is a second place for a column to be dropped. `audit.py`'s
    `_row` is the same argument for the same reason.
    """
    return Standing(
        platform=row.platform,
        conversation_id=row.conversation_id,
        body=row.body,
        fold_count=row.fold_count,
        updated_at=row.updated_at,
        last_folded_row_id=row.last_folded_row_id,
        working_set=working_set_from_json(row.working_set),
        lineage=Lineage.from_json(
            row.lineage, origin=room_key(row.platform, row.conversation_id),
        ),
        exclude_persons=tuple(row.exclude_persons or ()),
        # `hold_open` / `i_owe` (`0076_standing_sections`).
        hold_open=row.hold_open,
        i_owe=row.i_owe,
    )


class SqlStandingStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def read(
        self, *, platform: str, conversation_id: str
    ) -> Standing | None:
        stmt = sa.select(STANDING).where(
            STANDING.c.platform == platform,
            STANDING.c.conversation_id == conversation_id,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        if row is None:
            return None
        return _standing(row)

    async def read_head(
        self, *, platform: str, conversation_id: str
    ) -> StandingHead | None:
        """The head and the id of the `standing_revision` row that IS it, or
        `None` when the room has no head.

        The head and its revision are written in ONE transaction (`write`), so
        the head's revision is the chain's newest by the same order
        `read_as_of` uses. It is returned only when that row matches the head
        on `updated_at`, `fold_count` and `body`; a chain that does not end at
        the head (a room that predates the chain, a head edited outside
        `write`) gives `revision_id=None` rather than another position's id.
        SELECT only: reading a head must not fold, revise or migrate it."""
        head = await self.read(platform=platform, conversation_id=conversation_id)
        if head is None:
            return None
        stmt = (
            sa.select(
                STANDING_REVISION.c.id, STANDING_REVISION.c.updated_at,
                STANDING_REVISION.c.fold_count, STANDING_REVISION.c.body,
            )
            .where(
                STANDING_REVISION.c.platform == platform,
                STANDING_REVISION.c.conversation_id == conversation_id,
            )
            .order_by(
                STANDING_REVISION.c.updated_at.desc(),
                STANDING_REVISION.c.fold_count.desc(),
                STANDING_REVISION.c.id.desc(),
            )
            .limit(1)
        )
        async with self._engine.connect() as conn:
            newest = (await conn.execute(stmt)).one_or_none()
        matches = (
            newest is not None
            and newest.updated_at == head.updated_at
            and newest.fold_count == head.fold_count
            and newest.body == head.body
        )
        return StandingHead(
            standing=head, revision_id=str(newest.id) if matches and newest else None,
        )

    async def read_as_of(
        self, *, platform: str, conversation_id: str, before_ms: int
    ) -> Standing | None:
        """The position this room held at `before_ms` — the newest revision
        whose own `updated_at` is at or before it, or `None`.

        **`None` is the room's cold start, and it is the SAME meaning
        `last_folded_row_id is null` already carries on the head**: at that
        instant no fold had happened here yet, so there was no position. It is
        never «the history is missing» — this table is append-only and written
        in the fold's own transaction, so an absent revision at time T means a
        fold had not run by T. (The one case where those two come apart is a
        room whose chain predates this table, which `0059_standing_revision`
        deliberately did not backfill and says why.)

        **At or before, not strictly before.** A fold that landed exactly at
        `before_ms` is part of what she knew at `before_ms`: the timestamp is
        the instant the position was TAKEN, so excluding it would return the
        position it replaced and call that the state at a moment it was no
        longer the state. The boundary is worth stating because both readings
        look reasonable and only one of them reconstructs anything.

        **`fold_count DESC` is the tie-break, NOT `id DESC`.** `id` is
        `gen_random_uuid()`: it carries no relationship to which fold was
        written first, so two folds of one room sharing a millisecond would
        resolve to whichever uuid sorts higher, uncorrelated with the question
        being asked. `fold_count` is the fold path's own counter (`prior.fold_count + 1`), so
        ordering on it picks the later fold rather than the luckier uuid.

        **Where `fold_count` is not enough.** It is not guaranteed strictly
        increasing: a writer may carry the prior's value forward unchanged
        (DEC-277). So two revisions that share BOTH a millisecond and a
        `fold_count` are still a tie. `id DESC` stays on as the final key purely to make the
        ordering
        TOTAL — with two keys the planner would be free to return either row
        of such a tie, and a total order at least answers one way.
        Sub-millisecond ties are not reachable on the fold path in practice
        (one fold per turn, per room); the ordering is written for the case
        that is, and says what it does not cover.

        The `(platform, conversation_id, updated_at DESC)` prefix of
        `idx_standing_revision_room_updated` still serves the filter and the
        descending scan; only the tie-break now sorts, over the handful of rows
        that share the bound millisecond.
        """
        stmt = (
            sa.select(STANDING_REVISION)
            .where(
                STANDING_REVISION.c.platform == platform,
                STANDING_REVISION.c.conversation_id == conversation_id,
                STANDING_REVISION.c.updated_at <= before_ms,
            )
            .order_by(
                STANDING_REVISION.c.updated_at.desc(),
                STANDING_REVISION.c.fold_count.desc(),
                STANDING_REVISION.c.id.desc(),
            )
            .limit(1)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        if row is None:
            return None
        return _standing(row)

    async def write(self, *, standing: Standing, source: str = "fold") -> None:
        """The fold's write — an unconditional upsert. `source` is recorded on
        the `standing_revision` append ONLY: the head carries no writer-provenance column, because
        the
        property worth recording is who wrote each POSITION the room held,
        which is what the chain behind the head is for. Fold callers stay on the
        default `'fold'`.

        `'operator'` is written by `write_if_unchanged` below, never by this
        method directly — a manual edit also needs the compare-and-set this
        method does not do, so giving `write` itself an `'operator'` caller
        would be a second, weaker path to the same effect.
        """
        stmt = pg_insert(STANDING).values(
            platform=standing.platform,
            conversation_id=standing.conversation_id,
            body=standing.body,
            fold_count=standing.fold_count,
            updated_at=standing.updated_at,
            last_folded_row_id=standing.last_folded_row_id,
            working_set=working_set_to_json(standing.working_set),
            hold_open=standing.hold_open,
            i_owe=standing.i_owe,
            **_derived(standing),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[STANDING.c.platform, STANDING.c.conversation_id],
            set_={
                "body": stmt.excluded.body,
                "fold_count": stmt.excluded.fold_count,
                "updated_at": stmt.excluded.updated_at,
                # `hold_open` / `i_owe` (`0076_standing_sections`): written on
                # every upsert, the same "never by accident" argument
                # `working_set` makes below — an omitted key would keep the
                # row's old value instead of the caller's decision.
                "hold_open": stmt.excluded.hold_open,
                "i_owe": stmt.excluded.i_owe,
                # `consumed_verdict_tag` is NOT written: a vestigial column
                # nothing reads.
                #
                # The row-id watermark: written on every upsert, never
                # omitted — an omitted key would keep its old value by
                # accident rather than by decision — so a revise
                # that carries the prior's watermark forward does so by
                # decision and a fold that advanced it writes the advance in
                # the SAME statement as the position it belongs to, so a
                # position and its watermark cannot drift apart.
                "last_folded_row_id": stmt.excluded.last_folded_row_id,
                # Written on every upsert, same reasoning as the watermark
                # above: the caller decided the value, and an
                # omitted key here would silently keep whatever the row held
                # instead of the decision the caller made.
                "working_set": stmt.excluded.working_set,
                # The derived-row columns (`0073_lineage`), recomputed by
                # `_derived` on every write and never kept from the old row.
                "lineage": stmt.excluded.lineage,
                "exclude_persons": stmt.excluded.exclude_persons,
                "as_of": stmt.excluded.as_of,
            },
        )
        # The APPEND, beside the upsert and inside the same transaction. Same
        # values, no derivation: whatever the head was set to is what history
        # records, so there is no arithmetic here that could disagree with the
        # statement above. It is a plain `INSERT` with no `ON CONFLICT` for
        # an append-only log's reason — there is no update path to take,
        # and `id` defaults to a fresh uuid, so two folds that agree in every
        # other column are two rows rather than one silently replaced.
        #
        # NOT a trigger on `standing`. A trigger would be invisible from this
        # module and from the store contract, and the property worth holding is
        # that the reader and the writer of this chain are one class somebody
        # can read end to end.
        revision = sa.insert(STANDING_REVISION).values(
            platform=standing.platform,
            conversation_id=standing.conversation_id,
            body=standing.body,
            fold_count=standing.fold_count,
            updated_at=standing.updated_at,
            last_folded_row_id=standing.last_folded_row_id,
            source=source,
            working_set=working_set_to_json(standing.working_set),
            hold_open=standing.hold_open,
            i_owe=standing.i_owe,
            **_derived(standing),
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
            await conn.execute(revision)

    async def write_if_unchanged(
        self, *, expected: Standing, standing: Standing
    ) -> bool:
        """The operator's write: a manual edit of `body`, refused unless the
        row is EXACTLY what was read.

        **Why this is a second method rather than a flag on `write`.** The
        fold's write is a blind upsert — it must never refuse on a stale read,
        because the fold has no "someone else changed it, please retry"
        recovery and was never designed to need one. An operator's edit is the
        opposite: it is the ONE write against this table with a human reading
        the before-value first, so the same guarantee `standing_revision`
        gives the room (a position that moved and nothing recorded it cannot
        happen) has to hold in the other direction too — a position that moved
        BETWEEN the operator's read and their write must not be silently
        overwritten by what they read.

        Refuses (returns `False`, writes nothing) unless `body`, `fold_count`,
        `updated_at` and `last_folded_row_id` all still match `expected` —
        i.e. nothing folded, and nobody else edited, since `expected` was
        read.

        `standing.platform`, `standing.conversation_id`, `standing.fold_count`
        and `standing.last_folded_row_id` are NOT part of what an operator
        edit changes — only `body` and `updated_at` are — and this method
        RAISES `ValueError` if any of the four differ from `expected` rather
        than silently accepting them. The WHERE clause only pins the row
        being updated against `expected`; the values written come from
        `standing` via `.values()`, uncontrolled by the WHERE, and the revision
        insert takes its platform/conversation_id from `standing` too, so a
        mismatched key would append a revision under a different room's
        identity. The explicit checks close both.

        Always writes `source='operator'` on the revision it appends — that is
        the entire reason this method exists rather than a caller doing its
        own read-compare-write around `write(source="operator")`, which would
        still race the fold between the compare and the write.
        """
        if (
            standing.platform != expected.platform
            or standing.conversation_id != expected.conversation_id
            or standing.fold_count != expected.fold_count
            or standing.last_folded_row_id != expected.last_folded_row_id
            or standing.working_set != expected.working_set
            or standing.hold_open != expected.hold_open
            or standing.i_owe != expected.i_owe
        ):
            raise ValueError(
                "write_if_unchanged only changes body and updated_at — "
                "platform, conversation_id, fold_count, last_folded_row_id, "
                "working_set, hold_open and i_owe must match `expected` "
                "exactly"
            )
        last_folded_row_id_matches = (
            STANDING.c.last_folded_row_id == expected.last_folded_row_id
            if expected.last_folded_row_id is not None
            else STANDING.c.last_folded_row_id.is_(None)
        )
        update_stmt = (
            sa.update(STANDING)
            .where(
                STANDING.c.platform == expected.platform,
                STANDING.c.conversation_id == expected.conversation_id,
                STANDING.c.body == expected.body,
                STANDING.c.fold_count == expected.fold_count,
                STANDING.c.updated_at == expected.updated_at,
                last_folded_row_id_matches,
            )
            .values(
                body=standing.body,
                fold_count=standing.fold_count,
                updated_at=standing.updated_at,
                last_folded_row_id=standing.last_folded_row_id,
                # UNCHANGED by an operator edit (the raise above already
                # proved `standing.working_set == expected.working_set`),
                # written explicitly rather than omitted — the same
                # "never by accident" argument `write`'s upsert makes for
                # this column.
                working_set=working_set_to_json(standing.working_set),
                # UNCHANGED, same reason as `working_set` above (the raise
                # already proved both match `expected`).
                hold_open=standing.hold_open,
                i_owe=standing.i_owe,
                **_derived(standing),
            )
        )
        revision = sa.insert(STANDING_REVISION).values(
            platform=standing.platform,
            conversation_id=standing.conversation_id,
            body=standing.body,
            fold_count=standing.fold_count,
            updated_at=standing.updated_at,
            last_folded_row_id=standing.last_folded_row_id,
            source="operator",
            working_set=working_set_to_json(standing.working_set),
            hold_open=standing.hold_open,
            i_owe=standing.i_owe,
            **_derived(standing),
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(update_stmt)
            if result.rowcount == 0:
                return False
            await conn.execute(revision)
            return True
