"""The Cognitive Graph's tables (design/cognition): registry, entity, claim,
intention and edge.

Registered on the shared `schema.METADATA`; kept in their own module because
`schema.py` is at its file-size baseline. The same plane columns are declared
once and shared across the four row tables so a plane cannot drift per kind.
The CHECK value sets here are declared again as `Literal`s in
`nora/memory/domain/` — the unit tests compare them without a database, so a
vocabulary change lands in both places or fails.
"""

from __future__ import annotations

from collections.abc import Iterator

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from nora.infra.sql.schema import METADATA


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


REGISTRY = sa.Table(
    "registry",
    METADATA,
    sa.Column("id", sa.Text, nullable=False),
    sa.Column("unit", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False, server_default="live"),
    sa.Column(
        "registered_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("content_hash", sa.Text),
    sa.Column("tombstoned_at", sa.DateTime(timezone=True)),
    sa.PrimaryKeyConstraint("id", "unit", name="pk_registry"),
    sa.UniqueConstraint("id", "unit", "kind", name="uq_registry_id_unit_kind"),
    sa.CheckConstraint(
        "kind IN ('entity', 'item', 'aggregate', 'claim', 'intention', 'edge')",
        name="ck_registry_kind",
    ),
    sa.CheckConstraint("state IN ('live', 'tombstoned')", name="ck_registry_state"),
    sa.CheckConstraint(
        "state <> 'tombstoned' OR content_hash IS NOT NULL",
        name="ck_registry_tombstone_hash",
    ),
    sa.CheckConstraint(
        "(state = 'tombstoned') = (tombstoned_at IS NOT NULL)",
        name="ck_registry_tombstone_at",
    ),
)

ENTITY = sa.Table(
    "entity",
    METADATA,
    *_planes(
        "basis IN ('computed', 'inferred')",
        "state IN ('admitted', 'merged', 'retired', 'tombstoned')",
        "entity",
    ),
    sa.Column("name", sa.Text),
    sa.Column("aliases", pg.ARRAY(sa.Text)),
    sa.Column("pointer_kind", sa.Text),
    sa.Column("pointer_ref", sa.Text),
    sa.PrimaryKeyConstraint("id", "unit", name="pk_entity"),
    sa.CheckConstraint(
        "state = 'tombstoned' OR name IS NOT NULL", name="ck_entity_name"
    ),
    sa.CheckConstraint(
        "pointer_kind IS NULL OR pointer_kind IN ('person', 'room', 'unit', 'nora')",
        name="ck_entity_pointer_kind",
    ),
    sa.CheckConstraint(
        "(pointer_kind IS NULL) = (pointer_ref IS NULL)", name="ck_entity_pointer_pair"
    ),
    sa.CheckConstraint(
        "pointer_kind IS NULL OR basis = 'computed'", name="ck_entity_pointer_basis"
    ),
)

CLAIM = sa.Table(
    "claim",
    METADATA,
    *_planes(
        "basis IN ('inferred')",
        "state IN ('live', 'superseded', 'hidden', 'awaiting_weighing', 'retired',"
        " 'tombstoned')",
        "claim",
    ),
    sa.Column("part", sa.Text, nullable=False),
    sa.Column("content", pg.JSONB(none_as_null=True)),
    sa.Column("prediction", sa.Text),
    sa.Column("window_end", sa.DateTime(timezone=True)),
    sa.PrimaryKeyConstraint("id", "unit", name="pk_claim"),
    sa.CheckConstraint("part IN ('fact', 'trait', 'guess')", name="ck_claim_part"),
    sa.CheckConstraint(
        "state = 'tombstoned' OR content IS NOT NULL", name="ck_claim_content"
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' OR part <> 'guess' "
        "OR (prediction IS NOT NULL AND window_end IS NOT NULL)",
        name="ck_claim_guess",
    ),
    sa.CheckConstraint(
        "part = 'guess' OR (prediction IS NULL AND window_end IS NULL)",
        name="ck_claim_non_guess",
    ),
)

INTENTION = sa.Table(
    "intention",
    METADATA,
    *_planes(
        "basis IN ('committed')",
        "state IN ('open', 'ended', 'superseded', 'tombstoned')",
        "intention",
    ),
    sa.Column("owner", sa.Text, nullable=False),
    sa.Column("owed_to", sa.Text, nullable=False),
    sa.Column("form", sa.Text, nullable=False),
    sa.Column("done_when_form", sa.Text),
    sa.Column("done_when", pg.JSONB(none_as_null=True)),
    sa.Column("holds_while_form", sa.Text),
    sa.Column("holds_while", pg.JSONB(none_as_null=True)),
    sa.Column("drop_when_form", sa.Text),
    sa.Column("drop_when", pg.JSONB(none_as_null=True)),
    sa.Column("deadline", sa.DateTime(timezone=True)),
    sa.Column("review_time", sa.DateTime(timezone=True)),
    sa.Column("delivery_mode", sa.Text, nullable=False),
    sa.Column("trigger", pg.JSONB(none_as_null=True)),
    sa.Column("ending", sa.Text),
    sa.Column("ended_at", sa.DateTime(timezone=True)),
    sa.PrimaryKeyConstraint("id", "unit", name="pk_intention"),
    sa.CheckConstraint("form IN ('achieve', 'maintain')", name="ck_intention_form"),
    sa.CheckConstraint(
        "done_when_form IS NULL OR done_when_form IN "
        "('act_event', 'person_event', 'predicate', 'untyped')",
        name="ck_intention_done_when_form",
    ),
    sa.CheckConstraint(
        "holds_while_form IS NULL OR holds_while_form IN "
        "('act_event', 'person_event', 'predicate', 'deadline', 'untyped')",
        name="ck_intention_holds_while_form",
    ),
    sa.CheckConstraint(
        "drop_when_form IS NULL OR drop_when_form IN "
        "('act_event', 'person_event', 'predicate', 'deadline', 'untyped')",
        name="ck_intention_drop_when_form",
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' OR (form = 'achieve') = (done_when_form IS NOT NULL)",
        name="ck_intention_form_pair",
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' "
        "OR (done_when_form IS NULL) <> (holds_while_form IS NULL)",
        name="ck_intention_condition_pair",
    ),
    sa.CheckConstraint(
        "(done_when_form IS NULL) = (done_when IS NULL)", name="ck_intention_done_pair"
    ),
    sa.CheckConstraint(
        "(holds_while_form IS NULL) = (holds_while IS NULL)",
        name="ck_intention_holds_pair",
    ),
    sa.CheckConstraint(
        "(drop_when_form IS NULL) = (drop_when IS NULL)", name="ck_intention_drop_pair"
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' OR done_when_form <> 'untyped' OR deadline IS NOT NULL",
        name="ck_intention_untyped_deadline",
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' OR holds_while_form <> 'untyped' "
        "OR (drop_when_form IS NOT NULL AND drop_when_form = 'deadline')",
        name="ck_intention_untyped_drop",
    ),
    sa.CheckConstraint(
        "delivery_mode IN ('choose_moment', 'when_it_comes_up')",
        name="ck_intention_delivery_mode",
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' "
        "OR (delivery_mode = 'choose_moment') = (trigger IS NOT NULL)",
        name="ck_intention_trigger",
    ),
    sa.CheckConstraint(
        "form <> 'achieve' OR ending IS NULL "
        "OR ending IN ('achieved', 'dropped', 'leaked', 'unknown')",
        name="ck_intention_achieve_ending",
    ),
    sa.CheckConstraint(
        "form <> 'maintain' OR ending IS NULL OR ending IN ('ceased', 'dropped')",
        name="ck_intention_maintain_ending",
    ),
    sa.CheckConstraint(
        "(ending IS NULL) = (ended_at IS NULL)", name="ck_intention_ended_pair"
    ),
    sa.CheckConstraint(
        "state = 'tombstoned' "
        "OR (state = 'ended') = (ending IS NOT NULL AND ended_at IS NOT NULL)",
        name="ck_intention_ended",
    ),
)

EDGE = sa.Table(
    "edge",
    METADATA,
    *_planes(
        "basis IN ('observed', 'computed', 'inferred', 'committed')",
        "state IN ('live', 'superseded', 'retired', 'tombstoned')",
        "edge",
    ),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("src_id", sa.Text, nullable=False),
    sa.Column("dst_id", sa.Text, nullable=False),
    sa.PrimaryKeyConstraint("id", "unit", name="pk_edge"),
    sa.CheckConstraint(
        "type IN ('cites', 'about', 'draws_on', 'rests_on', 'serves', 'watches',"
        " 'closed_by', 'interested_in')",
        name="ck_edge_type",
    ),
    sa.ForeignKeyConstraint(
        ["src_id", "unit"], ["registry.id", "registry.unit"], name="fk_edge_src"
    ),
    sa.ForeignKeyConstraint(
        ["dst_id", "unit"], ["registry.id", "registry.unit"], name="fk_edge_dst"
    ),
)
sa.Index("idx_edge_src", EDGE.c.src_id, EDGE.c.type)
sa.Index("idx_edge_dst", EDGE.c.dst_id, EDGE.c.type)


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

def _attach_registration_planes() -> None:
    for name, kind, basis, writer, exposure in _observed_tables():
        table = METADATA.tables[name]
        for field in _registration_planes(name, kind, basis, writer, exposure):
            if isinstance(field, sa.Column):
                table.append_column(field)
            else:
                table.append_constraint(field)


_attach_registration_planes()

sa.Index(
    "uq_social_item_key", METADATA.tables["social_item"].c.network,
    METADATA.tables["social_item"].c.item_id, METADATA.tables["social_item"].c.unit,
    unique=True, postgresql_nulls_not_distinct=True,
)
