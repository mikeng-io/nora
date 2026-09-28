"""The withdrawal cascade (design/cognition-loop/10-tiers.md §2 rule 7, §8;
70-migration.md §2 row 2c; migration `0075_withdrawal`, DEC-238).

**What it does.** Given one or more withdrawn Record item ids (a message
deleted or unsent, the PII scrub, a person leaving a unit — 70 §9), it walks
the `citation` edge table (`nora.infra.sql.schema.CITATION`) to find every
`note`, `standing` and `culture` HEAD row whose citation chain reaches one of
them, and marks it `unsupported_at` (epoch ms) — **it never parses a body**
(10 §2 rule 7's own words). Marking is the only effect: the row is neither
deleted nor rewritten, "stops rendering" is the readers' job
(`nora.infra.sql.notes.SqlNoteStore.standing`/`.search`,
`nora.engagement.context`'s culture/standing render reads — see each site's
own comment for why it filters there and not in the store's general `read`),
and re-deriving it is the writer's next session's job (10 §2 rule 7's last
clause) — a job the Cognition and Character adaptors do not exist to do yet
(step 3+), so an unsupported row today simply stays unsupported until an
operator or a later step revises it.

**Idempotent.** A row already `unsupported_at` keeps its ORIGINAL timestamp:
`WHERE unsupported_at IS NULL` on every UPDATE means a repeat cascade over
the same withdrawn id marks nothing twice, and `Report.marked` only ever
lists what THIS call newly marked — the property a caller needs to log "N
rows went unsupported just now" rather than "N rows are unsupported in
total".

**Must run in the caller's own transaction.** Every function here takes an
`AsyncConnection`, never an `AsyncEngine`: the row that withdrew the message
(a hide, a delete) and the cascade that follows from it are one fact, the
same argument `nora.infra.sql.culture`'s docstring makes for the append and
the head-move riding in one transaction. A caller that ran this in a second
transaction could withdraw a message and crash before the cascade, leaving a
row that renders on a citation that no longer exists — silently, since
nothing would ever re-check it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.schema import CITATION, CULTURE, NOTE, STANDING

__all__ = ["WithdrawalReport", "withdraw_message", "withdraw_messages"]


@dataclass
class WithdrawalReport:
    """`marked[store]` is the list of citing keys THIS call newly marked
    `unsupported_at` — never a row that was already unsupported (see the
    module docstring's idempotence note)."""

    withdrawn: tuple[str, ...]
    marked: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total_marked(self) -> int:
        return sum(len(v) for v in self.marked.values())


async def withdraw_messages(
    conn: AsyncConnection, *, message_ids: Sequence[int], now_ms: int,
) -> WithdrawalReport:
    """The cascade over several withdrawn message ids at once — one lookup
    against `citation` rather than one per id, for `forget_person`'s case
    (a person's whole message history withdrawn together)."""
    ids = [str(i) for i in message_ids]
    report = WithdrawalReport(withdrawn=tuple(ids))
    if not ids:
        return report
    edges = (await conn.execute(
        sa.select(CITATION.c.citing_store, CITATION.c.citing_key)
        .where(CITATION.c.cited_store == "message", CITATION.c.cited_id.in_(ids))
        .distinct()
    )).all()
    by_store: dict[str, list[str]] = {}
    for row in edges:
        by_store.setdefault(row.citing_store, []).append(row.citing_key)
    if by_store.get("note"):
        marked = await _mark_note(conn, by_store["note"], now_ms)
        if marked:
            report.marked["note"] = marked
    if by_store.get("standing"):
        marked = await _mark_standing(conn, by_store["standing"], now_ms)
        if marked:
            report.marked["standing"] = marked
    if by_store.get("culture"):
        marked = await _mark_culture(conn, by_store["culture"], now_ms)
        if marked:
            report.marked["culture"] = marked
    return report


async def withdraw_message(
    conn: AsyncConnection, *, message_id: int, now_ms: int,
) -> WithdrawalReport:
    """One message withdrawn — a delete, an unsend, one row the PII scrub
    took. See `withdraw_messages` for the batch form and the module
    docstring for what "withdrawn" triggers this."""
    return await withdraw_messages(conn, message_ids=[message_id], now_ms=now_ms)


async def _mark_note(
    conn: AsyncConnection, keys: Sequence[str], now_ms: int,
) -> list[str]:
    result = await conn.execute(
        sa.update(NOTE)
        .where(NOTE.c.id.in_(keys), NOTE.c.unsupported_at.is_(None))
        .values(unsupported_at=now_ms)
        .returning(NOTE.c.id)
    )
    return [str(r.id) for r in result.all()]


async def _mark_standing(
    conn: AsyncConnection, keys: Sequence[str], now_ms: int,
) -> list[str]:
    """`keys` are `<platform>:<conversation_id>` (`citation.citing_key`'s
    shape for this store, `nora.domain.lineage.room_key`'s own format); the
    composite primary key means one UPDATE per room rather than one `IN`."""
    marked: list[str] = []
    for key in keys:
        platform, sep, conversation_id = key.partition(":")
        if not sep or not platform or not conversation_id:
            continue
        result = await conn.execute(
            sa.update(STANDING)
            .where(
                STANDING.c.platform == platform,
                STANDING.c.conversation_id == conversation_id,
                STANDING.c.unsupported_at.is_(None),
            )
            .values(unsupported_at=now_ms)
            .returning(STANDING.c.platform, STANDING.c.conversation_id)
        )
        marked.extend(f"{r.platform}:{r.conversation_id}" for r in result.all())
    return marked


async def _mark_culture(
    conn: AsyncConnection, keys: Sequence[str], now_ms: int,
) -> list[str]:
    result = await conn.execute(
        sa.update(CULTURE)
        .where(CULTURE.c.key.in_(keys), CULTURE.c.unsupported_at.is_(None))
        .values(unsupported_at=now_ms)
        .returning(CULTURE.c.key)
    )
    return [r.key for r in result.all()]
