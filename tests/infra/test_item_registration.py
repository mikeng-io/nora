from collections.abc import Iterator

import pytest
import sqlalchemy as sa

from nora.infra.sql.migrations_runner import sync_url_for
from nora.memory.domain.registration import AGGREGATE_FAMILIES, ITEM_SOURCES


@pytest.fixture
def graph_schema(clean_pg_url: str) -> Iterator[sa.Inspector]:
    engine = sa.create_engine(sync_url_for(clean_pg_url))
    try:
        with engine.connect() as connection:
            yield sa.inspect(connection)
    finally:
        engine.dispose()


@pytest.mark.parametrize("name", (*ITEM_SOURCES, *AGGREGATE_FAMILIES))
def test_every_item_and_aggregate_carries_the_planes(
    graph_schema: sa.Inspector, name: str,
) -> None:
    entity_fields = {field["name"]: field["type"] for field in graph_schema.get_columns("entity")}
    for payload in ("name", "aliases", "pointer_kind", "pointer_ref"):
        del entity_fields[payload]
    entity_fields["graph_id"] = entity_fields.pop("id")

    actual = {field["name"]: field["type"] for field in graph_schema.get_columns(name)}
    assert {field: str(actual[field]) for field in actual.keys() & entity_fields.keys()} == {
        field: str(field_type) for field, field_type in entity_fields.items()
    }

    assert any(
        fk["constrained_columns"] == ["graph_id", "unit", "kind"]
        and fk["referred_table"] == "registry"
        and fk["referred_columns"] == ["id", "unit", "kind"]
        for fk in graph_schema.get_foreign_keys(name)
    )
