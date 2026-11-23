"""The current and historical row sources of the retrieval queries."""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.sql import FromClause

from nora.infra.sql.cognitive_graph_schema import REGISTRY
from nora.infra.sql.cognitive_graph_store import visible_to_reads
from nora.infra.sql.history import HISTORY_TABLES
from nora.infra.sql.schema import METADATA, ROW_HISTORY
from nora.memory.domain.registration import AGGREGATE_FAMILIES, ITEM_SOURCES

SOURCES = (*ITEM_SOURCES, *AGGREGATE_FAMILIES, "claim", "intention", "entity", "edge")


def _history(name: str, unit: str, at: datetime) -> sa.Select[Any]:
    history = ROW_HISTORY.c
    return sa.select(history.image).where(
        history.table_name == name,
        history.image["unit"].astext == unit,
        history.recorded_at <= at,
        sa.or_(history.superseded_at.is_(None), history.superseded_at > at),
        history.op != "delete",
    )


def _registry_at(unit: str, at: datetime) -> FromClause:
    history = _history("registry", unit, at).subquery()
    image = history.c.image
    return sa.select(
        image["id"].astext.label("id"),
        image["unit"].astext.label("unit"),
        image["state"].astext.label("state"),
    ).subquery()


def _historical_source(name: str, unit: str, at: datetime, id_key: str) -> FromClause:
    history = _history(name, unit, at).subquery()
    image = history.c.image
    return sa.select(
        image[id_key].astext.label("graph_id"),
        image["unit"].astext.label("unit"),
        image,
    ).subquery()


def _image_source(
    name: str, unit: str, as_of: datetime | None, registry_at: FromClause | None,
) -> sa.Select[Any]:
    table = METADATA.tables[name]
    identity = "graph_id" if "graph_id" in table.c else "id"
    if as_of is not None and name in HISTORY_TABLES:
        historical = _historical_source(name, unit, as_of, identity)
        return sa.select(
            historical.c.graph_id, historical.c.image,
        ).where(visible_to_reads(historical, registry_at=registry_at))
    return sa.select(
        table.c[identity].label("graph_id"),
        sa.func.to_jsonb(table.table_valued(), type_=pg.JSONB).label("image"),
    ).where(table.c.unit == unit, visible_to_reads(table, registry_at=registry_at))


def _projection(
    name: str, unit: str, as_of: datetime | None, registry_at: FromClause | None,
) -> sa.Select[Any]:
    rows = _image_source(name, unit, as_of, registry_at).subquery()
    image = rows.c.image
    asserted = image["asserted_at"].astext.cast(sa.DateTime(timezone=True))
    state = image["state"].astext
    valid_to = image["valid_to"].astext.cast(sa.DateTime(timezone=True))
    if name == "claim":
        state = sa.case((
            state.in_(("live", "awaiting_weighing"))
            & (valid_to <= (sa.literal(as_of) if as_of is not None else sa.func.now())),
            "ended",
        ), else_=state)
    return sa.select(
        rows.c.graph_id,
        image["unit"].astext.label("unit"),
        sa.literal(name).label("source"),
        image["kind"].astext.label("kind"),
        state.label("state"),
        image["origin_room"].astext.label("origin_room"),
        image["exposure"].astext.label("exposure"),
        image["lineage"].label("lineage"),
        image["replaces"].astext.label("replaces"),
        asserted.label("asserted_at"),
        sa.func.coalesce(
            image["occurred_at"].astext.cast(sa.DateTime(timezone=True)), asserted,
        ).label("event_time"),
        image,
    )


def graph_rows(
    unit: str, as_of: datetime | None, *, sources: tuple[str, ...] = SOURCES,
) -> FromClause:
    """Graph rows before room and audience restrictions are added."""
    registry = _registry_at(unit, as_of) if as_of is not None else None
    return sa.union_all(*(
        _projection(name, unit, as_of, registry) for name in sources
    )).cte("graph_rows")


def tombstoned_ids(unit: str, *, since: datetime | None = None) -> sa.SelectBase:
    """Only the retained identity is needed for a deleted-source citation."""
    registry = REGISTRY.c
    current = sa.select(registry.id).where(
        registry.unit == unit, registry.state == "tombstoned",
        visible_to_reads(REGISTRY, registry_state="tombstoned"),
    )
    if since is None:
        return current
    history = ROW_HISTORY.c
    return current.union(sa.select(history.image["id"].astext.label("id")).where(
        history.table_name == "registry", history.recorded_at > since,
        history.image["unit"].astext == unit, history.image["state"].astext == "tombstoned",
    ))
