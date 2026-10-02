"""The three write sites that keep `citation` (migration `0075_withdrawal`)
current going forward: `nora.infra.sql.notes`, `.standing` and `.culture`
each call one of these, inside the SAME transaction as the revision they
cite from — exactly `standing_revision`'s own precedent for why an append
rides in the write's transaction rather than a trigger (that module's
docstring; `0075_withdrawal`'s docstring explains why a trigger was not used
here either).

`ON CONFLICT DO NOTHING` throughout: a store re-citing the same message on
its next revision (the same room, the same watermark) is not a NEW edge.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.note_body import cited_ids_in, rollup_keys_in
from nora.infra.sql.schema import CITATION

__all__ = [
    "record_citations",
    "record_message_citation",
]


async def record_citations(
    conn: AsyncConnection, *, citing_store: str, citing_key: str, body: str,
) -> None:
    """Every `[id, id]` group `cited_ids_in(body)` finds (design 42 §4's
    citation grammar, `nora.domain.note_body`) becomes one edge, and every
    `rollup(...)` key `rollup_keys_in(body)` finds becomes one edge to the
    `rollup` store. Used by
    `nora.infra.sql.notes`, whose bodies are already written in this format
    (the only store today whose schema requires it, `12-schemas.md` §1)."""
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
