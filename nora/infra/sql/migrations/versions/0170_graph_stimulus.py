"""Durable per-unit Cognitive Graph stimuli."""

from alembic import op

revision = "0170_graph_stimulus"
down_revision = "0169_act_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE graph_stimulus (
            unit TEXT NOT NULL,
            kind TEXT NOT NULL,
            graph_id TEXT NOT NULL,
            occurrence TEXT DEFAULT '' NOT NULL,
            eligible_at TIMESTAMP WITH TIME ZONE NOT NULL,
            recorded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            source TEXT NOT NULL,
            room TEXT,
            identity TEXT,
            cues TEXT[] DEFAULT '{}' NOT NULL,
            revision_id TEXT,
            dependents TEXT[] DEFAULT '{}' NOT NULL,
            CONSTRAINT pk_graph_stimulus PRIMARY KEY (unit, kind, graph_id, occurrence),
            CONSTRAINT ck_graph_stimulus_kind CHECK (kind IN (
                'new_item', 'strong_cue', 'failed_act', 'dependency', 'aggregate_crossing',
                'intention_due', 'review_time', 'valid_to', 'deadline',
                'watched_source_changed', 'absence', 'verdict', 'presence_change'
            )),
            CONSTRAINT fk_graph_stimulus_row FOREIGN KEY (graph_id, unit)
                REFERENCES registry (id, unit),
            CONSTRAINT fk_graph_stimulus_revision FOREIGN KEY (revision_id, unit)
                REFERENCES registry (id, unit)
        )
    """)
    op.execute("CREATE INDEX idx_graph_stimulus_eligible ON graph_stimulus (unit, eligible_at)")


def downgrade() -> None:
    op.drop_table("graph_stimulus")
