"""Entries are reachable from their subjects: profile lines, Characters and
events project notebook-layer `about` relations from the same index edges the
search pointers use (`profile_line.profile_key`/relationship `fact_value`,
`culture.key`, `events.people`/`event_rsvps`/`venue_place_id`), admitted like
any relation — evidence is the entry's citations resolved to the Record/Rollup
nodes beneath them, never the entry itself, so an entry whose citations
resolve to nothing projects nothing. Persons are the union the entry and its
evidence both permit; an `authored_by`/`mentions` edge carries a message's
person scope because its node names none.

`record_event_cites` persists an event write's `(part, ref)` citations as
`cites` edges — the event row keeps only the lineage they resolve to — and the
index's `_entry_edges` keeps them across rewrites because no row can
re-derive them. A revision replaces only the parts it restates, matching the
lineage rule.

`project` wraps `memory_index.project` for the same callers so the relation
step rides the write's own transaction and the backfill takes the same path.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.domain.memory_graph import (
    RECORD,
    ROLLUP,
    Evidence,
    Key,
    Relation,
    Source,
    validate_relation,
)
from nora.infra.sql import memory_index
from nora.infra.sql.graph_projection import (
    ProjectionContext,
    _endpoint,
    _scopes,
    context,
    instant,
    stable_id,
)
from nora.infra.sql.memory_graph import relation_values
from nora.infra.sql.schema import (
    MEMORY_EDGE,
    MEMORY_NODE,
    RELATION,
    RELATION_EVIDENCE,
)

_ENTRY_ABOUT = ("event", "character", "profile_line")


async def project(
    conn: AsyncConnection, kind: str, sid: str, *, metrics: object = None
) -> None:
    """The index projection, then the entry's `about` relations on top of it:
    the index's edge rewrite has run and the node's state is final, so a
    superseded or retired entry keeps none."""
    await memory_index.project(conn, kind, sid, metrics=metrics)
    if kind not in _ENTRY_ABOUT:
        return
    cfg = context(conn)
    if cfg is None:
        return
    root = (
        await conn.execute(
            sa.select(MEMORY_NODE).where(
                MEMORY_NODE.c.kind == kind, MEMORY_NODE.c.source_id == sid
            )
        )
    ).mappings().first()
    if root is None or root["state"] != "live":
        return
    await _about(
        conn, kind, sid, f"project:{kind}:{sid}",
        await _scopes(conn, (kind, sid), cfg),
        instant(root["at_ms"]) if root["at_ms"] is not None else root["projected_at"],
        cfg,
    )


async def _about(
    conn: AsyncConnection, kind: str, sid: str, owner: str,
    scopes: Mapping[str, tuple[str, ...]], at: datetime, cfg: ProjectionContext,
) -> None:
    """The entry's code-owned `about` relations per unit evidence scopes to."""
    about = (
        await conn.execute(
            sa.select(MEMORY_EDGE.c.dst_kind, MEMORY_EDGE.c.dst_id).where(
                MEMORY_EDGE.c.src_kind == kind,
                MEMORY_EDGE.c.src_id == sid,
                MEMORY_EDGE.c.rel == "about",
            )
        )
    ).all()
    if not about:
        return
    cites = (
        await conn.execute(
            sa.select(MEMORY_EDGE.c.dst_kind, MEMORY_EDGE.c.dst_id).where(
                MEMORY_EDGE.c.src_kind == kind,
                MEMORY_EDGE.c.src_id == sid,
                MEMORY_EDGE.c.rel == "cites",
            )
        )
    ).all()
    sources: dict[Key, Source] = {}
    for key in {(r.dst_kind, r.dst_id) for r in cites}:
        if key[0] not in (*RECORD, *ROLLUP):
            continue
        node = (
            await conn.execute(
                sa.select(MEMORY_NODE).where(
                    MEMORY_NODE.c.kind == key[0], MEMORY_NODE.c.source_id == key[1]
                )
            )
        ).mappings().first()
        if node is None:
            continue
        audience = set(node["persons"] or ())
        audience |= {
            r.dst_id for r in (
                await conn.execute(
                    sa.select(MEMORY_EDGE.c.dst_kind, MEMORY_EDGE.c.dst_id).where(
                        MEMORY_EDGE.c.src_kind == key[0],
                        MEMORY_EDGE.c.src_id == key[1],
                        MEMORY_EDGE.c.rel.in_(("authored_by", "mentions")),
                        MEMORY_EDGE.c.dst_kind == "person",
                    )
                )
            )
        }
        sources[key] = Source(
            key, "rollup" if key[0] in ROLLUP else "record",
            node["unit"] or cfg.rooms.get(node["origin_room"], ""),
            tuple(sorted(audience)), node["text"] or "",
        )
    if not sources:
        if cfg.metrics is not None:
            cfg.metrics.inc("nora_memory_graph_projection_total", outcome="ungrounded")
        return
    for unit, persons in scopes.items():
        unit_sources = {k: s for k, s in sources.items() if s.unit == unit}
        if not unit_sources:
            continue
        restricted = tuple(sorted(
            set(persons) | {p for s in unit_sources.values() for p in s.persons}
        ))
        for dst in {(r.dst_kind, r.dst_id) for r in about}:
            await _about_relation(
                conn, owner=owner, unit=unit, src=(kind, sid), dst=dst,
                persons=restricted, at=at, sources=unit_sources,
            )


async def _about_relation(
    conn: AsyncConnection, *, owner: str, unit: str, src: Key, dst: Key,
    persons: tuple[str, ...], at: datetime,
    sources: Mapping[Key, Source],
) -> str | None:
    """One `about` relation, admitted like any other; refuse, write nothing."""
    src = await _endpoint(conn, src, unit)
    dst = await _endpoint(conn, dst, unit)
    r = Relation(
        stable_id(owner, unit, src, "about", dst),
        src, "about", dst, "notebook", unit, persons,
        asserted_at=at, written_by=owner,
    )
    evidence = tuple(Evidence(key, relation_id=r.id) for key in sources)
    if validate_relation(r, evidence, sources) is not None:
        return None
    stmt = insert(RELATION).values(**relation_values(r))
    await conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[RELATION.c.id],
            set_={
                "status": "current",
                "persons": stmt.excluded.persons,
                "asserted_at": stmt.excluded.asserted_at,
            },
        )
    )
    await conn.execute(
        sa.delete(RELATION_EVIDENCE).where(RELATION_EVIDENCE.c.relation_id == r.id)
    )
    await conn.execute(
        insert(RELATION_EVIDENCE).values([
            {
                "relation_id": r.id,
                "source_kind": s.key[0],
                "source_id": s.key[1],
                "role": "supports",
            }
            for s in sources.values()
        ])
    )
    return r.id


async def record_event_cites(
    conn: AsyncConnection, source_id: str, citations: Iterable[tuple[str, str]],
) -> None:
    """An event write's `(part, ref)` citations as `cites` edges, written by
    the store in the write's own transaction because nothing else keeps them:
    the row carries only the lineage they resolve to. A revision replaces
    only the parts it restates — the lineage rule — so edges carry the part
    in their provenance and a re-stated part drops its old edges first."""
    cited = list(citations)
    if cited:
        await conn.execute(
            sa.delete(MEMORY_EDGE).where(
                MEMORY_EDGE.c.src_kind == "event",
                MEMORY_EDGE.c.src_id == source_id,
                MEMORY_EDGE.c.rel == "cites",
                MEMORY_EDGE.c.provenance.in_(
                    [f"event_citations.{part}" for part, _ in cited]
                ),
            )
        )
    await memory_index._insert_edges(
        conn,
        [
            edge
            for part, ref in cited
            for edge in memory_index._cites(
                "event", source_id, (ref,), f"event_citations.{part}"
            )
        ],
    )
