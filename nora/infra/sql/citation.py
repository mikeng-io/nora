"""The three write sites that keep `citation` (migration `0075_withdrawal`)
current going forward: `nora.infra.sql.notes`, `.standing` and `.culture`
each call one of these, inside the SAME transaction as the revision they
cite from — exactly `standing_revision`'s own precedent for why an append
rides in the write's transaction rather than a trigger (that module's
docstring; `0075_withdrawal`'s docstring explains why a trigger was not used
here either).

`ON CONFLICT DO NOTHING` throughout: a store re-citing the same message on
its next revision (the same room, the same watermark) is not a NEW edge (10
tiers.md §2 rule 7 — the edge does not change; the row it cites is what gets
marked, not the edge itself).
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.note_body import cited_ids_in, rollup_keys_in
from nora.infra.sql.schema import CITATION, EVENTS, MESSAGES, NOTE

__all__ = [
    "lock_cited_messages",
    "mark_if_cites_hidden",
    "record_citations",
    "record_message_citation",
]

_MESSAGE_ID_MAX = 2**31 - 1


async def lock_cited_messages(conn: AsyncConnection, message_ids: Collection[int]) -> None:
    """Take `FOR SHARE` on the messages a write is about to cite, at the START of the
    writer's transaction, BEFORE it locks its own note or event row. ONE lock order
    everywhere: message rows first, then the row that cites them. A delete hides the
    message (the row lock) and its cascade then updates the citing rows; a writer that
    locked its citing row first and the message second is the opposite order, and the
    two deadlock (the delete ends `cascade_failed` and its hide is rolled back). The
    write-time check (`mark_if_cites_hidden`) re-reads the same rows under the lock this
    already holds. Ordered by id so two writers never wait on each other's rows."""
    lookup = sorted({i for i in message_ids if 0 < i <= _MESSAGE_ID_MAX})
    if not lookup:
        return
    await conn.execute(
        sa.select(MESSAGES.c.id).where(MESSAGES.c.id.in_(lookup))
        .order_by(MESSAGES.c.id).with_for_update(read=True)
    )


async def mark_if_cites_hidden(
    conn: AsyncConnection, *, citing_store: str, citing_key: str,
    message_ids: Collection[int], now_ms: int,
) -> bool:
    """A row written AFTER the message it cites was withdrawn is `unsupported` at
    birth. The cascade (`nora.app.withdrawal`) runs once, when the message is
    withdrawn, and marks the rows that cite it THEN; a note or an event a Dreamer
    session writes afterwards, read off a transcript it was handed before the
    delete, writes its edge with `unsupported_at` NULL and nothing would ever
    revisit it. So the one choke point where the edges are written asks the one
    question: is any message this row cites hidden? If so the row is marked, in
    the writer's own transaction, exactly as the cascade would have marked it.

    Only `note` and `event` (the stores whose citations are evidence a delete
    withdraws, `nora.app.withdrawal.EVIDENCE_STORES`); Standing and culture cite a
    watermark and are never marked by a write. The row must already exist in
    `conn`'s transaction (an UPDATE of nothing is not a mark). Returns whether the
    row was marked."""
    lookup = [i for i in message_ids if 0 < i <= _MESSAGE_ID_MAX]
    if not lookup:
        return False
    # FOR SHARE, not FOR KEY SHARE, and BEFORE the answer is read: under READ
    # COMMITTED the writer's edge is invisible to a delete that hides the message
    # and scans the edges at the same moment, and the writer's own check would see
    # the message not hidden yet; both would commit and nothing would mark the row,
    # for good (the sweep only looks at messages still visible). FOR SHARE conflicts
    # with the hide's UPDATE, so the hide WAITS until this transaction commits and its
    # cascade then sees the edge, or it has committed first and this read sees it
    # hidden. One of the two marks the row. Ordered by id so two writers do not
    # deadlock on the same rows.
    rows = (await conn.execute(
        sa.select(MESSAGES.c.hidden_at)
        .where(MESSAGES.c.id.in_(lookup))
        .order_by(MESSAGES.c.id)
        .with_for_update(read=True)
    )).all()
    if not any(r.hidden_at is not None for r in rows):
        return False
    if citing_store == "note":
        stmt = (
            sa.update(NOTE)
            .where(NOTE.c.id == citing_key, NOTE.c.unsupported_at.is_(None))
            .values(unsupported_at=now_ms)
        )
    elif citing_store == "event":
        stmt = (
            sa.update(EVENTS)
            .where(EVENTS.c.id == UUID(citing_key), EVENTS.c.unsupported_at.is_(None))
            .values(unsupported_at=now_ms)
        )
    else:
        return False
    await conn.execute(stmt)
    return True


async def record_citations(
    conn: AsyncConnection, *, citing_store: str, citing_key: str, body: str,
    now_ms: int | None = None,
) -> None:
    """Every `[id, id]` group `cited_ids_in(body)` finds (design 42 §4's
    citation grammar, `nora.domain.note_body`) becomes one edge, and every
    `rollup(...)` key `rollup_keys_in(body)` finds becomes one edge to the
    `rollup` store. Used by
    `nora.infra.sql.notes`, whose bodies are already written in this format
    (the only store today whose schema requires it, `12-schemas.md` §1).

    With `now_ms`, the row is marked `unsupported` when a message it cites is
    already hidden (`mark_if_cites_hidden`); the citing row must exist by now."""
    ids = cited_ids_in(body)
    keys = rollup_keys_in(body)
    edges = [
        {
            "citing_store": citing_store, "citing_key": citing_key,
            "cited_store": "message", "cited_id": str(i),
        }
        for i in sorted(ids)
    ] + [
        # A Rollup row the body rests on (`rollup(kind|subject|from/to)`, #663):
        # its edge is the row's key text, `cited_store = 'rollup'`, the same
        # vocabulary an event's edge uses (migration `0086_event_citations`).
        {
            "citing_store": citing_store, "citing_key": citing_key,
            "cited_store": "rollup", "cited_id": key,
        }
        for key in sorted(keys)
    ]
    if not edges:
        return
    await conn.execute(
        pg_insert(CITATION).values(edges)
        .on_conflict_do_nothing(constraint="citation_edge_unique")
    )
    if now_ms is not None and ids:
        await mark_if_cites_hidden(
            conn, citing_store=citing_store, citing_key=citing_key,
            message_ids=ids, now_ms=now_ms,
        )


async def record_message_citation(
    conn: AsyncConnection, *, citing_store: str, citing_key: str,
    message_id: int | None,
) -> None:
    """One edge to one message id, or nothing when there is none to cite.
    Used by `nora.infra.sql.standing` (`last_folded_row_id`, the one message
    id the fold already tracks) and `nora.infra.sql.culture` (the newest
    message on record in its room, resolved the same way migration
    `0075_withdrawal`'s backfill resolved it) — neither store's body is
    schema'd with citations yet (`12-schemas.md`'s Standing/Character
    sections arrive at step 3+), so this is the one message id each can name
    today rather than a set parsed out of free prose."""
    if message_id is None:
        return
    await conn.execute(
        pg_insert(CITATION)
        .values(
            citing_store=citing_store, citing_key=citing_key,
            cited_store="message", cited_id=str(message_id),
        )
        .on_conflict_do_nothing(constraint="citation_edge_unique")
    )
