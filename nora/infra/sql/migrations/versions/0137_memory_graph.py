"""Shared memory graph stores; no readers or runtime wiring."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0137_memory_graph"
down_revision = "0136_extraction_drops"
branch_labels = None
depends_on = None

METADATA = sa.MetaData()

# Generic memory graph stores (design/unified-memory/27). Vocabularies live in code.
ENTITY = sa.Table(
    "entity",
    METADATA,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("unit", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("aliases", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
    # ponytail: JSONB attributes; promote one to a column only when a query plan needs it
    sa.Column("attributes", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("state", sa.Text, nullable=False, server_default="live"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("merged_into", sa.Text, sa.ForeignKey("entity.id")),
)
# pg_trgm is installed by 0026; array aliases need a separate search
# representation for trigram indexing, deferred to the SQL candidate query.
sa.Index("idx_entity_name_trgm", ENTITY.c.name,
         postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"})
RELATION = sa.Table(
    "relation",
    METADATA,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("src_kind", sa.Text, nullable=False),
    sa.Column("src_id", sa.Text, nullable=False),
    sa.Column("rel", sa.Text, nullable=False),
    sa.Column("dst_kind", sa.Text, nullable=False),
    sa.Column("dst_id", sa.Text, nullable=False),
    sa.Column("layer", sa.Text, nullable=False),
    sa.Column("valid_from", sa.DateTime(timezone=True)),
    sa.Column("valid_to", sa.DateTime(timezone=True)),
    sa.Column("asserted_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="current"),
    sa.Column("written_by", sa.Text, nullable=False),
    sa.Column("superseded_by", sa.Text, sa.ForeignKey("relation.id")),
    sa.Column("unit", sa.Text, nullable=False),
    sa.Column("persons", pg.ARRAY(sa.Text), nullable=False, server_default="{}"),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint("layer IN ('record', 'rollup', 'notebook')", name="relation_layer_check"),
    sa.CheckConstraint(
        "status IN ('current', 'superseded', 'disputed', 'retired')", name="relation_status_check"
    ),
)
sa.Index(
    "idx_relation_src_walk",
    RELATION.c.src_kind,
    RELATION.c.src_id,
    RELATION.c.rel,
    postgresql_include=["unit"],
)
sa.Index(
    "idx_relation_dst_walk",
    RELATION.c.dst_kind,
    RELATION.c.dst_id,
    RELATION.c.rel,
    postgresql_include=["unit"],
)
RELATION_EVIDENCE = sa.Table(
    "relation_evidence",
    METADATA,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("relation_id", sa.Text, sa.ForeignKey("relation.id")),
    sa.Column("entity_id", sa.Text, sa.ForeignKey("entity.id")),
    sa.Column("attribute", sa.Text),
    sa.Column("source_kind", sa.Text, nullable=False),
    sa.Column("source_id", sa.Text, nullable=False),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("span", sa.Text),
    sa.CheckConstraint(
        "(relation_id IS NOT NULL AND entity_id IS NULL AND attribute IS NULL) OR "
        "(relation_id IS NULL AND entity_id IS NOT NULL AND attribute IS NOT NULL)",
        name="relation_evidence_target_check",
    ),
    sa.CheckConstraint("role IN ('supports', 'contradicts')", name="relation_evidence_role_check"),
)
sa.Index("idx_relation_evidence_relation", RELATION_EVIDENCE.c.relation_id)
sa.Index(
    "idx_relation_evidence_entity", RELATION_EVIDENCE.c.entity_id, RELATION_EVIDENCE.c.attribute
)
MEMORY_WORK_ITEM = sa.Table(
    "memory_work_item",
    METADATA,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("source_kind", sa.Text, nullable=False),
    sa.Column("source_id", sa.Text, nullable=False),
    sa.Column("unit", sa.Text, nullable=False),
    sa.Column("input_hash", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False, server_default="pending"),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("last_error", sa.Text),
    sa.Column("historical", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("due_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("settled_at", sa.DateTime(timezone=True)),
    sa.UniqueConstraint(
        "source_kind", "source_id", "unit", "input_hash", name="memory_work_item_input_key"
    ),
    sa.CheckConstraint(
        "state IN ('pending', 'running', 'settled', 'failed')", name="memory_work_item_state_check"
    ),
    sa.CheckConstraint("attempts BETWEEN 0 AND 5", name="memory_work_item_attempts_check"),
)
sa.Index(
    "idx_memory_work_item_claim",
    MEMORY_WORK_ITEM.c.historical,
    MEMORY_WORK_ITEM.c.state,
    MEMORY_WORK_ITEM.c.due_at,
    MEMORY_WORK_ITEM.c.as_of,
)


def upgrade() -> None:
    METADATA.create_all(op.get_bind())
    for name in ("entity", "relation", "relation_evidence", "memory_work_item"):
        op.execute(
            f"CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
            f"ON {name} FOR EACH ROW EXECUTE FUNCTION nora_row_history('id')"
        )
        op.execute(
            f"CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON {name} "
            "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
        )


def downgrade() -> None:
    METADATA.drop_all(op.get_bind())
