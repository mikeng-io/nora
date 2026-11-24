"""Append a Culture account and move its head in one transaction.

The writer reads the parent under the head row lock before inserting the
revision. Seed callers supply the account's time and provenance.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.culture import CultureRevision
from nora.domain.lineage import BODY, own_room_lineage, room_key
from nora.infra.sql import memory_index
from nora.infra.sql.schema import CULTURE, CULTURE_REVISION, MESSAGES

__all__ = ["SqlCultureStore"]


class SqlCultureStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

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
        the chain forks: two revisions claim the same predecessor. The lock
        is per key.
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
                        citations=list(citations),
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
