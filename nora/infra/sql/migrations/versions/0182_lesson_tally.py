"""Register lesson_tally on the graph."""

# ruff: noqa: E501
from alembic import op

revision = "0182_lesson_tally"
down_revision = "0181_wake_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE lesson_tally (
            claim_id TEXT NOT NULL,
            support INTEGER NOT NULL,
            contradiction INTEGER NOT NULL,
            graph_id TEXT NOT NULL,
            kind TEXT DEFAULT 'aggregate' NOT NULL,
            basis TEXT DEFAULT 'computed' NOT NULL,
            reasoning TEXT,
            confidence TEXT,
            unit TEXT NOT NULL,
            origin_room TEXT,
            identities TEXT[] DEFAULT '{}' NOT NULL,
            excluded_identities TEXT[] DEFAULT '{}' NOT NULL,
            lineage JSONB DEFAULT '{}'::jsonb NOT NULL,
            exposure TEXT DEFAULT 'internal' NOT NULL,
            writer TEXT DEFAULT 'SqlLessonTally' NOT NULL,
            occasion TEXT,
            pass TEXT,
            trace TEXT,
            model TEXT,
            rev_code TEXT,
            rev_instruction TEXT,
            rev_skill TEXT,
            rev_config TEXT,
            rev_identity TEXT,
            rev_policy TEXT,
            occurred_at TIMESTAMP WITH TIME ZONE,
            asserted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            valid_from TIMESTAMP WITH TIME ZONE,
            valid_to TIMESTAMP WITH TIME ZONE,
            replaces TEXT,
            state TEXT DEFAULT 'live' NOT NULL,
            CONSTRAINT pk_lesson_tally PRIMARY KEY (claim_id, unit),
            CONSTRAINT ck_lesson_tally_counts CHECK (support >= 0 AND contradiction >= 0),
            CONSTRAINT ck_lesson_tally_graph_kind CHECK (kind = 'aggregate'),
            CONSTRAINT ck_lesson_tally_graph_basis CHECK (basis = 'computed'),
            CONSTRAINT ck_lesson_tally_graph_state CHECK (state IN ('live', 'tombstoned')),
            CONSTRAINT ck_lesson_tally_graph_exposure CHECK (exposure IN ('external', 'internal', 'operator')),
            CONSTRAINT ck_lesson_tally_graph_reasoning CHECK (reasoning IS NULL OR reasoning IN ('told', 'deduced', 'induced', 'abduced')),
            CONSTRAINT ck_lesson_tally_graph_confidence CHECK (confidence IS NULL OR confidence IN ('low', 'medium', 'high')),
            CONSTRAINT ck_lesson_tally_graph_inferred_detail CHECK ((basis = 'inferred') = (reasoning IS NOT NULL AND confidence IS NOT NULL)),
            CONSTRAINT fk_lesson_tally_graph_registry FOREIGN KEY(graph_id, unit, kind) REFERENCES registry (id, unit, kind),
            CONSTRAINT fk_lesson_tally_graph_replaces FOREIGN KEY(replaces, unit, kind) REFERENCES registry (id, unit, kind),
            CONSTRAINT uq_lesson_tally_graph_replaces UNIQUE (replaces),
            CONSTRAINT ck_lesson_tally_graph_pair CHECK ((unit IS NULL) = (graph_id IS NULL)),
            CONSTRAINT fk_lesson_tally_claim FOREIGN KEY(claim_id, unit) REFERENCES claim (id, unit)
        )
    """)
    op.execute("""
        CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE
        ON lesson_tally FOR EACH ROW EXECUTE FUNCTION nora_row_history('claim_id','unit')
    """)
    op.execute("""
        CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON lesson_tally
        FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()
    """)


def downgrade() -> None:
    op.drop_table("lesson_tally")
