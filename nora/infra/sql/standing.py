"""`SqlStandingStore` — the debrief's per-conversation position, read before
every fold and revised in place after it
(`design/dreamer-agent-loop/26-standing.md` §4).

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
prior it just read and computes `prior.fold_count + 1` there. Until
2026-09-24 this was also how the re-derivation path could RESET it to zero —
a store that only knows how to increment cannot express that — but that path
is deleted (design/standing-compact/); `fold_count` is now a plain count
nothing resets, still computed in the caller rather than in SQL. Arithmetic in
the sink, not in the statement.

**The upsert now carries an APPEND beside it, in the same transaction.** The
head keeps its shape, its semantics and its statement exactly — nothing about
`read`/`write` changed for any existing caller — and `standing_revision` records
what the head was set to each time it moved. Both statements are inside the one
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

from nora.domain.lineage import BODY, WORKING_SET, Lineage, own_room_lineage
from nora.domain.standing import Standing, working_set_from_json, working_set_to_json
from nora.infra.sql.schema import MESSAGES, STANDING, STANDING_REVISION

#: A Standing row's sections today (`nora.domain.lineage`): the position and
#: the working set. The sectioned Standing schema arrives at migration step 3.
SECTIONS: tuple[str, ...] = (BODY, WORKING_SET)


def _derived(standing: Standing) -> dict[str, Any]:
    """The columns every derived row carries (design/cognition-loop/
    10-tiers.md §3, migration `0070_lineage`), computed by code from the row:
    the fold reads its own room, and is shown a prompt it does not cite
    (`own_room_lineage`); `as_of` is the time of the newest message it read
    (`last_folded_row_id`), else the fold's own instant."""
    as_of: Any = standing.updated_at
    if standing.last_folded_row_id is not None:
        as_of = sa.func.coalesce(
            sa.select(MESSAGES.c.ts)
            .where(MESSAGES.c.id == standing.last_folded_row_id)
            .scalar_subquery(),
            standing.updated_at,
        )
    return {
        "lineage": own_room_lineage(standing.conversation_id, SECTIONS).to_json(),
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
        lineage=Lineage.from_json(row.lineage, origin=row.conversation_id),
        exclude_persons=tuple(row.exclude_persons or ()),
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

        **`fold_count DESC` is the tie-break, NOT `id DESC`** — measured, and
        the first draft had it the other way round. `id` is
        `gen_random_uuid()`: it carries no relationship to which fold was
        written first, so two folds of one room sharing a millisecond resolved
        to whichever uuid happened to sort higher. Over twelve trials that
        returned the EARLIER of the two positions seven times — deterministic
        for a fixed set of rows, and uncorrelated with the question being asked.
        `fold_count` is the fold path's own counter (`prior.fold_count + 1`), so
        ordering on it picks the later fold rather than the luckier uuid.

        **Where `fold_count` is not enough, stated rather than left to be
        found.** It is not strictly increasing: a rollup fold carries the
        prior's value forward unchanged (`standing_event`), rather than
        incrementing it. (Before 2026-09-24 a rebuild also reset it to 1
        — deleted with the ceiling trigger, design/standing-compact/.) So two
        revisions that share BOTH a millisecond and a `fold_count` are still a
        tie. `id DESC` stays on as the final key purely to make the ordering
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
        """The fold's write — an unconditional upsert, exactly as before this
        argument existed. `source` is recorded on the `standing_revision`
        append ONLY (design/dreamer-agent-loop/26-standing.md, dated section
        2026-09-25): the head carries no writer-provenance column, because the
        property worth recording is who wrote each POSITION the room held,
        which is what the chain behind the head is for. All three callers —
        `debrief.py`, `standing_event.py`, and the eval sandbox's
        `standing_fold.py` (which stamps its seeded prior `'fold'` too, since
        it is reproducing a fold that already happened) — stay on the
        default; nothing about this method's shape or its transaction
        changed for any of them.

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
            **_derived(standing),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[STANDING.c.platform, STANDING.c.conversation_id],
            set_={
                "body": stmt.excluded.body,
                "fold_count": stmt.excluded.fold_count,
                "updated_at": stmt.excluded.updated_at,
                # `consumed_verdict_tag` is NOT written: it was the watermark
                # of the ungrounded re-derivation trigger, deleted 2026-09-24
                # with the verdict (design/standing-compact/). The column
                # stays, as dropped tiers' columns do; nothing reads it.
                #
                # The row-id watermark: written on every upsert, never
                # omitted — an omitted key would keep its old value by
                # accident rather than by decision — so a revise
                # that carries the prior's watermark forward does so by
                # decision and a fold that advanced it writes the advance in
                # the SAME statement as the position it belongs to
                # (design/standing-watermark/20 — the fold's write is one
                # statement, never a position and a watermark that can drift
                # apart).
                "last_folded_row_id": stmt.excluded.last_folded_row_id,
                # Written on every upsert, same reasoning as the watermark
                # above: the caller (the fold, or the rollup carrying the
                # prior's set forward unchanged) decided the value, and an
                # omitted key here would silently keep whatever the row held
                # instead of the decision the caller made.
                "working_set": stmt.excluded.working_set,
                # The derived-row columns (`0070_lineage`), recomputed by
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
            **_derived(standing),
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
            await conn.execute(revision)

    async def write_if_unchanged(
        self, *, expected: Standing, standing: Standing
    ) -> bool:
        """The operator's write: a manual edit of `body`, refused unless the
        row is EXACTLY what was read (design/dreamer-agent-loop/26-standing.md,
        dated section 2026-09-25).

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
        than silently accepting them. **An earlier version of this docstring
        claimed the WHERE clause already made that impossible; it did not.**
        The WHERE clause matches the row's CURRENT (pre-write) values against
        `expected`, which pins the row being updated, but it says nothing
        about what the caller's `standing` argument then WRITES into
        `fold_count` or `last_folded_row_id` — those come from `.values()`,
        entirely uncontrolled by the WHERE — so a caller that (by a bug)
        passed a `standing` with a different `fold_count` would have
        silently written it, exactly the claim the operator edit exists to
        rule out. `platform`/`conversation_id` mismatches were worse: the
        WHERE still finds the right row (keyed on `expected`'s), but the
        revision insert below takes ITS values from `standing`, so a caller
        passing the wrong key would have appended a revision under a
        different room's identity while updating this one's head. Both are
        now closed by the checks that follow, not by an argument about the
        SQL.

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
        ):
            raise ValueError(
                "write_if_unchanged only changes body and updated_at — "
                "platform, conversation_id, fold_count, last_folded_row_id "
                "and working_set must match `expected` exactly"
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
            **_derived(standing),
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(update_stmt)
            if result.rowcount == 0:
                return False
            await conn.execute(revision)
            return True
