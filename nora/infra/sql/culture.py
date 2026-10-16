"""`SqlCultureStore` — append an account, move the head, walk the chain back.

Three methods and no more: `head`, `append`, `chain`.

**The write is ONE transaction, and that is the whole reason this class is not
two calls from the adaptor.** Appending a revision and moving
`culture.head_revision_id` are a single fact — "this is the current account" —
and splitting them produces the two states that make the chain a lie: a
revision nothing points at, or a head pointing at a row that is not there.
`DreamAdaptor.commit` exists precisely so a tier can sequence its own writes,
and this is what that sequence is.

**The parent is read INSIDE that transaction, never passed in.** The adaptor
does not know what the head is at commit time and must not guess: the Dreamer
dispatches scopes as concurrent tasks, and a parent captured before the model
call is a parent that may have moved. Reading it under `FOR UPDATE` is what
makes "the same thing, earlier" true rather than probable.

`__init__(self, engine: AsyncEngine)` and nothing else, matching
`SqlStandingStore` and `SqlTagTraceStore`: no config, no clock, no metrics. The
clock arrives on the call, so a fixture in another session can build this class
from a bare `AsyncEngine` and never has to guess what else it needs.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.culture import CultureRevision
from nora.domain.lineage import BODY, Lineage, own_room_lineage, room_key
from nora.infra.sql import memory_index
from nora.infra.sql.schema import CULTURE, CULTURE_REVISION, MESSAGES

__all__ = ["SqlCultureStore"]


def _revision(row: sa.Row[Any]) -> CultureRevision:
    return CultureRevision(
        id=row.id,
        culture_key=row.culture_key,
        body=row.body,
        written_at=row.written_at,
        established_by=row.established_by,
        parent_revision_id=row.parent_revision_id,
        instruction_sha256=row.instruction_sha256,
        model=row.model,
        lineage=Lineage.from_json(row.lineage, origin=row.culture_key),
        exclude_persons=tuple(row.exclude_persons or ()),
        citations=tuple(row.citations or ()),
    )


class SqlCultureStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def head(self, *, key: str) -> CultureRevision | None:
        """The current account for `key`, or `None` if this room has none yet.

        Joined through `culture.head_revision_id` rather than "the newest
        revision by `written_at`": the head is a value both sides of a
        compare-and-swap can name, and "whatever is newest right now" is exactly
        what that guards against. Two revisions can also share a millisecond.
        """
        stmt = (
            sa.select(CULTURE_REVISION)
            .select_from(
                CULTURE.join(
                    CULTURE_REVISION,
                    CULTURE.c.head_revision_id == CULTURE_REVISION.c.id,
                )
            )
            .where(CULTURE.c.key == key)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _revision(row)

    async def append(
        self,
        *,
        key: str,
        body: str,
        written_at: int,
        established_by: str,
        instruction_sha256: str | None = None,
        model: str | None = None,
        citations: tuple[str, ...] = (),
    ) -> CultureRevision:
        """Write the new account and make it the head, atomically.

        Returns the revision as stored, INCLUDING the parent the transaction
        chose — the caller cannot know it beforehand and should never be asked
        to supply it.

        `SELECT ... FOR UPDATE` on the `culture` row is what serialises two
        concurrent folds of one key. Without it both read the same parent and
        the chain forks: two revisions claiming the same predecessor, one of
        them silently not in the history any more. The lock is per key, so it
        costs nothing across rooms — which is the only concurrency this loop
        actually has, since `dream_loop` dispatches each scope as its own task.
        """
        async with self._engine.begin() as conn:
            # The room key comes from the Record: a culture key is the room's
            # bare `primary_id` (`nora.domain.lineage`, module docstring).
            newest = (await conn.execute(
                sa.select(MESSAGES.c.platform)
                .where(
                    MESSAGES.c.conversation_id == key,
                    MESSAGES.c.hidden_at.is_(None),
                )
                .order_by(MESSAGES.c.id.desc())
                .limit(1)
            )).one_or_none()
            platform = newest.platform if newest is not None else None
            lineage = (
                own_room_lineage(room_key(platform, key), (BODY,))
                if platform is not None
                else own_room_lineage(key, (BODY,), keyed=False)
            )
            parent = (
                await conn.execute(
                    sa.select(CULTURE.c.head_revision_id)
                    .where(CULTURE.c.key == key)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            revision_id = (
                await conn.execute(
                    sa.insert(CULTURE_REVISION)
                    .values(
                        culture_key=key,
                        body=body,
                        parent_revision_id=parent,
                        written_at=written_at,
                        established_by=established_by,
                        instruction_sha256=instruction_sha256,
                        model=model,
                        # What `character_revise` checked it rests on
                        # (`0098_culture_citations`).
                        citations=list(citations),
                        # Migration `0073_lineage`: the fold reads this room and is
                        # shown the unit's note index, which it does not cite.
                        lineage=lineage.to_json(),
                        as_of=written_at,
                    )
                    .returning(CULTURE_REVISION.c.id)
                )
            ).scalar_one()
            # The `culture` row may not exist yet — the first fold of a room
            # creates it. UPSERT rather than a read-then-branch: the read above
            # already told us, but the branch would be a second statement that
            # can race with another process creating the same key, and
            # `ON CONFLICT` makes that unrepresentable instead of unlikely.
            stmt = pg_insert(CULTURE).values(
                key=key, head_revision_id=revision_id, updated_at=written_at,
            )
            await conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=[CULTURE.c.key],
                    set_={
                        "head_revision_id": stmt.excluded.head_revision_id,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
            )
            # Room and unit-keyed accounts only: the projector never indexes a
            # pre-C2b room-keyed person account.
            await memory_index.project(conn, "character", key)
        return CultureRevision(
            id=revision_id,
            culture_key=key,
            body=body,
            written_at=written_at,
            established_by=established_by,
            parent_revision_id=parent,
            instruction_sha256=instruction_sha256,
            model=model,
            lineage=lineage,
            citations=tuple(citations),
        )

    async def chain(self, *, key: str, limit: int = 10) -> tuple[CultureRevision, ...]:
        """The head and its ancestors, newest first.

        **Walked through `parent_revision_id`, not ordered by `written_at`.**
        The two agree today and would not have to: a row inserted with an older
        clock, or two sharing a millisecond, reorders a timestamp sort and
        produces a history that never happened. The chain IS the parent links —
        that is the whole content of DEC-183 — so reading it any other way reads
        something else.

        `limit` bounds a walk that a cycle would otherwise make infinite. The
        schema forbids the one-step cycle; a longer one would need two bad
        writes, and this bound means the reader survives it rather than hanging.
        """
        out: list[CultureRevision] = []
        current = await self.head(key=key)
        seen: set[str] = set()
        async with self._engine.connect() as conn:
            while current is not None and len(out) < limit:
                out.append(current)
                seen.add(current.id)
                parent_id = current.parent_revision_id
                if parent_id is None or parent_id in seen:
                    break
                row = (
                    await conn.execute(
                        sa.select(CULTURE_REVISION).where(
                            CULTURE_REVISION.c.id == parent_id
                        )
                    )
                ).one_or_none()
                current = None if row is None else _revision(row)
        return tuple(out)
