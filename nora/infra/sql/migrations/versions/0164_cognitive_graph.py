"""The Cognitive Graph's tables; the generic memory-graph stores go legacy_*.

Renames the six generic stores (`entity`, `relation`, `relation_evidence`,
`memory_work_item`, `graph_refusal`, `graph_arrival`) to `legacy_*` — their
triggers, constraints and indexes ride along — and relabels their row_history
rows so an as-of read keeps meaning the legacy table. Creates `registry`,
`entity`, `claim`, `intention` and `edge` (design/cognition): every kind row's
registry row is foreign-keyed on the composite (id, unit, kind), edge
endpoints on (id, unit), so a row without its identity or an edge across
units cannot be written. `replaces` is unique and points back through the
registry, so a revision names a registered base and a stale base takes a
second child. All five keep row history.

The table definitions are duplicated into the live schema in
`nora/infra/sql/cognitive_graph_schema.py`; change one and change the other,
or `test_schema_parity` fails.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0164_cognitive_graph"
down_revision = "0163_managed_history"
branch_labels = None
depends_on = None

METADATA = sa.MetaData()

_LEGACY = (
    "entity", "relation", "relation_evidence",
    "memory_work_item", "graph_refusal", "graph_arrival",
)


def _planes(basis_check: str, state_check: str, kind: str) -> list[sa.schema.SchemaItem]:
    return [
        sa.Column("id", sa.Text, nullable=False),
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
        sa.CheckConstraint(f"kind = '{kind}'", name=f"ck_{kind}_kind"),
        sa.CheckConstraint(basis_check, name=f"ck_{kind}_basis"),
        sa.CheckConstraint(state_check, name=f"ck_{kind}_state"),
        sa.CheckConstraint(
            "exposure IN ('external', 'internal', 'operator')", name=f"ck_{kind}_exposure"
        ),
        sa.CheckConstraint(
            "reasoning IS NULL OR reasoning IN ('told', 'deduced', 'induced', 'abduced')",
            name=f"ck_{kind}_reasoning",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence IN ('low', 'medium', 'high')",
            name=f"ck_{kind}_confidence",
        ),
        sa.CheckConstraint(
            "(basis = 'inferred') = (reasoning IS NOT NULL AND confidence IS NOT NULL)",
            name=f"ck_{kind}_inferred_detail",
        ),
        sa.ForeignKeyConstraint(
            ["id", "unit", "kind"],
            ["registry.id", "registry.unit", "registry.kind"],
            name=f"fk_{kind}_registry",
        ),
        sa.ForeignKeyConstraint(
            ["replaces", "unit", "kind"],
            ["registry.id", "registry.unit", "registry.kind"],
            name=f"fk_{kind}_replaces",
        ),
        sa.UniqueConstraint("replaces", name=f"uq_{kind}_replaces"),
    ]


sa.Table(
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

sa.Table(
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

sa.Table(
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

sa.Table(
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


def upgrade() -> None:
    for name in _LEGACY:
        op.execute(f'ALTER TABLE "{name}" RENAME TO "legacy_{name}"')
    op.execute(
        "UPDATE row_history SET table_name = 'legacy_' || table_name "
        "WHERE table_name IN ('entity', 'relation', 'relation_evidence', "
        "'memory_work_item', 'graph_arrival')"
    )
    METADATA.create_all(op.get_bind())
    for name in ("registry", "entity", "claim", "intention", "edge"):
        op.execute(
            f"CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
            f"ON {name} FOR EACH ROW EXECUTE FUNCTION nora_row_history('id','unit')"
        )
        op.execute(
            f"CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON {name} "
            "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
        )


def _graph_data_held(bind: sa.Connection) -> list[str]:
    """New-graph tables or history a downgrade would drop or mislabel."""
    # An 'entity' row_history row can only be new-graph: the upgrade relabelled
    # every legacy row to legacy_*, so the name now collides with the new table.
    held = [
        name
        for name in ("registry", "entity", "claim", "intention", "edge")
        if bind.execute(
            sa.select(sa.literal(1)).select_from(sa.table(name)).limit(1)
        ).first()
    ]
    if bind.execute(sa.text(
        "SELECT 1 FROM row_history WHERE table_name IN "
        "('registry', 'entity', 'claim', 'intention', 'edge') LIMIT 1"
    )).first():
        held.append("row_history")
    return held


def downgrade() -> None:
    held = _graph_data_held(op.get_bind())
    if held:
        raise RuntimeError(
            "0155 downgrade refuses: the Cognitive Graph still holds data in "
            f"{', '.join(held)} — dropping the tables would lose it and the "
            "restored legacy name would collide with new-graph history"
        )
    METADATA.drop_all(op.get_bind())
    for name in _LEGACY:
        op.execute(f'ALTER TABLE "legacy_{name}" RENAME TO "{name}"')
    op.execute(
        "UPDATE row_history SET table_name = substring(table_name from 8) "
        "WHERE table_name IN ('legacy_entity', 'legacy_relation', "
        "'legacy_relation_evidence', 'legacy_memory_work_item', 'legacy_graph_arrival')"
    )
