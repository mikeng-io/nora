"""Add graph registration planes to observation and aggregate tables."""

from collections.abc import Iterator

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0156_graph_registration"
down_revision = "0155_cognitive_graph"
branch_labels = None
depends_on = None


_RENAMES = {
    "platform_object_state": (('kind', 'source_kind'), ('state', 'source_state'),),
    "platform_observation": (('kind', 'source_kind'),),
    "presence_corridors": (('lineage', 'build_lineage'),),
    "presence_derived_places": (
        ("kind", "source_kind"), ("confidence", "place_confidence"),
        ("lineage", "build_lineage"),
    ),
    "presence_occupancy": (('lineage', 'build_lineage'),),
    "presence_place_rhythm": (('lineage', 'build_lineage'),),
    "presence_stays": (('lineage', 'build_lineage'),),
    "presence_trips": (('lineage', 'build_lineage'),),
    "rollup_colocation": (('lineage', 'build_lineage'),),
    "rollup_daybook": (('lineage', 'build_lineage'),),
    "rollup_outing": (('lineage', 'build_lineage'),),
    "rollup_response": (('lineage', 'build_lineage'),),
    "rollup_tie": (('lineage', 'build_lineage'),),
    "social_item": (('kind', 'source_kind'),),
    "social_item_state": (('state', 'source_state'),),
}


def _planes(
    basis_check: str, state_check: str, kind: str, *,
    table: str | None = None, row_id: str = "id",
) -> list[sa.schema.SchemaItem]:
    """The columns and rules every row and edge carries under one shape."""
    table = f"{table}_graph" if table else kind
    return [
        sa.Column(row_id, sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("basis", sa.Text, nullable=False),
        sa.Column("reasoning", sa.Text),
        sa.Column("confidence", sa.Text),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("origin_room", sa.Text),
        sa.Column("persons", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("excluded_persons", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("lineage", pg.JSONB(none_as_null=True), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("exposure", sa.Text, nullable=False),
        sa.Column("writer", sa.Text, nullable=False),
        sa.Column("occasion", sa.Text),
        sa.Column("pass", sa.Text),
        sa.Column("trace", sa.Text),
        sa.Column("model", sa.Text),
        sa.Column("rev_code", sa.Text),
        sa.Column("rev_instruction", sa.Text),
        sa.Column("rev_skill", sa.Text),
        sa.Column("rev_config", sa.Text),
        sa.Column("rev_identity", sa.Text),
        sa.Column("rev_policy", sa.Text),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("asserted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("replaces", sa.Text),
        sa.Column("state", sa.Text, nullable=False),
        sa.CheckConstraint(f"kind = '{kind}'", name=f"ck_{table}_kind"),
        sa.CheckConstraint(basis_check, name=f"ck_{table}_basis"),
        sa.CheckConstraint(state_check, name=f"ck_{table}_state"),
        sa.CheckConstraint(
            "exposure IN ('external', 'internal', 'operator')", name=f"ck_{table}_exposure"
        ),
        sa.CheckConstraint(
            "reasoning IS NULL OR reasoning IN ('told', 'deduced', 'induced', 'abduced')",
            name=f"ck_{table}_reasoning",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence IN ('low', 'medium', 'high')",
            name=f"ck_{table}_confidence",
        ),
        sa.CheckConstraint(
            "(basis = 'inferred') = (reasoning IS NOT NULL AND confidence IS NOT NULL)",
            name=f"ck_{table}_inferred_detail",
        ),
        sa.ForeignKeyConstraint(
            [row_id, "unit", "kind"],
            ["registry.id", "registry.unit", "registry.kind"],
            name=f"fk_{table}_registry",
        ),
        sa.ForeignKeyConstraint(
            ["replaces", "unit", "kind"],
            ["registry.id", "registry.unit", "registry.kind"],
            name=f"fk_{table}_replaces",
        ),
        sa.UniqueConstraint("replaces", name=f"uq_{table}_replaces"),
    ]


_ITEM_WRITERS = {
    "messages": ("SqlRecordStore", "external"),
    "message_edit": ("SqlRecordStore", "external"),
    "message_reaction": ("SqlRecordStore", "external"),
    "message_delete": ("SqlRecordStore", "external"),
    "presence_fixes": ("PresenceStore", "internal"),
    "fetched_pages": ("SqlFetchedPageStore", "external"),
    "social_item": ("SqlSocialStreamStore", "external"),
    "social_item_count": ("SqlSocialStreamStore", "external"),
    "social_item_state": ("SqlSocialStreamStore", "external"),
    "social_poll": ("SqlSocialStreamStore", "external"),
    "platform_observation": ("SqlObservationStore", "external"),
    "source_health": ("SqlSourceHealthLog", "internal"),
}

_AGGREGATE_WRITERS = {
    "presence_stays": ("SqlMobilityStore", "internal"),
    "presence_trips": ("SqlMobilityStore", "internal"),
    "presence_derived_places": ("SqlMobilityStore", "internal"),
    "presence_occupancy": ("SqlMobilityStore", "internal"),
    "presence_place_rhythm": ("SqlMobilityStore", "internal"),
    "presence_corridors": ("SqlMobilityStore", "internal"),
    "rollup_colocation": ("SqlRollupStore", "internal"),
    "rollup_outing": ("SqlRollupStore", "internal"),
    "rollup_response": ("SqlRollupStore", "external"),
    "rollup_tie": ("SqlRollupStore", "internal"),
    "rollup_daybook": ("SqlRollupStore", "internal"),
    "presence_places": ("PresenceStore", "internal"),
    "platform_object_state": ("SqlObservationStore", "external"),
}


def _observed_tables() -> Iterator[tuple[str, str, str, str, str]]:
    for kind, basis, writers in (
        ("item", "observed", _ITEM_WRITERS),
        ("aggregate", "computed", _AGGREGATE_WRITERS),
    ):
        for name, (writer, exposure) in writers.items():
            yield name, kind, basis, writer, exposure


def _registration_planes(
    table: str, kind: str, basis: str, writer: str, exposure: str,
) -> Iterator[sa.schema.SchemaItem]:
    for field in _planes(
        f"basis = '{basis}'", "state IN ('live', 'tombstoned')", kind,
        table=table, row_id="graph_id",
    ):
        if isinstance(field, sa.Column):
            if field.name == "unit" and table.startswith("rollup_"):
                continue
            if field.name == "persons" and table in {"rollup_colocation", "rollup_outing"}:
                continue
            if field.name in {"unit", "graph_id"}:
                field.nullable = True
            defaults = {
                "kind": kind, "basis": basis, "writer": writer,
                "exposure": exposure, "state": "live",
            }
            if field.name in defaults:
                field.server_default = sa.DefaultClause(defaults[field.name])
        yield field
    yield sa.CheckConstraint(
        "(unit IS NULL) = (graph_id IS NULL)",
        name=f"ck_{table}_graph_pair", postgresql_not_valid=True,
    )


def _rename_source_column(table: str, old: str, new: str) -> None:
    op.alter_column(table, old, new_column_name=new)
    # The as-of reader decodes earlier row images under the live table's shape.
    op.get_bind().execute(sa.text(
        "UPDATE row_history "
        "SET image = (image - :old) || jsonb_build_object(CAST(:new AS text), image -> :old) "
        "WHERE table_name = :table AND jsonb_exists(image, :old)"
    ), {"table": table, "old": old, "new": new})


def upgrade() -> None:
    for table, fields in _RENAMES.items():
        for old, new in fields:
            _rename_source_column(table, old, new)
    metadata = sa.MetaData()
    sa.Table("registry", metadata, *(sa.Column(x, sa.Text) for x in ("id", "unit", "kind")))
    for name, kind, basis, writer, exposure in _observed_tables():
        table = sa.Table(
            name, metadata,
            *([sa.Column("unit", sa.Text)] if name.startswith("rollup_") else []),
            *_registration_planes(name, kind, basis, writer, exposure),
        )
        for column in table.columns:
            if column.name == "unit" and name.startswith("rollup_"):
                continue
            op.add_column(name, column)
        for constraint in sorted(table.constraints, key=lambda c: c.name or ""):
            if constraint.name is not None:
                op.execute(sa.schema.AddConstraint(constraint))


def downgrade() -> None:
    for name, kind, basis, writer, exposure in _observed_tables():
        for field in reversed(list(_registration_planes(name, kind, basis, writer, exposure))):
            if isinstance(field, sa.Column):
                op.drop_column(name, field.name)
            else:
                op.drop_constraint(field.name, name)
    for table, fields in _RENAMES.items():
        for old, new in fields:
            _rename_source_column(table, new, old)
