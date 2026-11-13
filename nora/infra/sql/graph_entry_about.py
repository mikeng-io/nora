"""Entries are reachable from their subjects: profile lines, Characters and
events project notebook-layer `about` relations from the same index edges the
search pointers use (`profile_line.profile_key`/relationship `fact_value`,
`culture.key`, `events.people`/`event_rsvps`/`venue_place_id`), admitted like
any relation — evidence is the entry's citations resolved to the Record/Rollup
nodes beneath them, never the entry itself, so an entry whose citations
resolve to nothing projects nothing. Persons are the union the entry and its
evidence both permit; an `authored_by`/`mentions` edge carries a message's
person scope because its node names none.

`project_about` runs inside `project_source`, after the owner's previous
relations are retired, so every caller of the one projection path — a live
write, a rebuild, a queued retry, the backfill — retires and recreates in the
same savepoint; nothing can bypass the pair.

Evidence eligibility is resolved the way the graph resolves a node's scope:
a Record node by its unit or origin room, a person-scoped Rollup (a stay, a
trip) into the member units of the persons it names. A citation that
establishes no eligibility — its node is gone, excluded, or scoped nowhere —
refuses the whole relation fail-closed; the evidence is never dropped to
keep it.
"""

from __future__ import annotations

from collections.abc import Mapping
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
from nora.infra.sql.graph_projection import (
    ProjectionContext,
    _endpoint,
    _scopes,
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


async def project_about(
    conn: AsyncConnection, kind: str, sid: str, *, owner: str, state: str,
    scopes: Mapping[str, tuple[str, ...]], at: datetime, cfg: ProjectionContext,
) -> None:
    """The entry's code-owned `about` relations, one per unit its evidence is
    eligible for. Called by `project_source` with the owner's scopes already
    resolved; a hidden or retired entry keeps nothing (its relations were
    retired upstream)."""
    if kind not in _ENTRY_ABOUT or state != "live" or not scopes:
        return
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
    resolved: dict[Key, dict[str, Source]] = {}
    refused: str | None = None
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
        if node is None or node["exclude_persons"]:
            refused = "ungrounded" if node is None else "excluded"
            break
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
        node_scopes = await _scopes(conn, key, cfg)
        if not node_scopes:
            refused = "ungrounded"
            break
        resolved[key] = {
            unit: Source(
                key, "rollup" if key[0] in ROLLUP else "record", unit,
                tuple(sorted(audience | set(persons))), node["text"] or "",
            )
            for unit, persons in node_scopes.items()
        }
    if refused is not None or not resolved:
        if cfg.metrics is not None:
            cfg.metrics.inc(
                "nora_memory_graph_projection_total", outcome=refused or "ungrounded"
            )
        return
    for unit, persons in scopes.items():
        unit_sources = {
            key: per[unit] for key, per in resolved.items() if unit in per
        }
        if len(unit_sources) != len(resolved):
            # Every citation must be eligible here; dropping one would lose its restriction.
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
