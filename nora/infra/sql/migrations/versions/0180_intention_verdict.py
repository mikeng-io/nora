"""Register intention_verdict on the graph."""

# ruff: noqa: E501
from alembic import op

revision = "0180_intention_verdict"
down_revision = "0179_proactive_recheck"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE intention_verdict (
            intention_id TEXT NOT NULL,
            ending TEXT NOT NULL,
            ended_at TIMESTAMP WITH TIME ZONE NOT NULL,
            witness_id TEXT,
            form TEXT NOT NULL,
            owed_to TEXT NOT NULL,
            condition_form TEXT,
            graph_id TEXT NOT NULL,
            kind TEXT DEFAULT 'aggregate' NOT NULL,
            basis TEXT DEFAULT 'computed' NOT NULL,
            reasoning TEXT,
            confidence TEXT,
            unit TEXT NOT NULL,
            origin_room TEXT,
            persons TEXT[] DEFAULT '{}' NOT NULL,
            excluded_persons TEXT[] DEFAULT '{}' NOT NULL,
            lineage JSONB DEFAULT '{}'::jsonb NOT NULL,
            exposure TEXT DEFAULT 'internal' NOT NULL,
            writer TEXT DEFAULT 'SqlIntentionVerification' NOT NULL,
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
            CONSTRAINT pk_intention_verdict PRIMARY KEY (intention_id, unit),
            CONSTRAINT ck_intention_verdict_ending CHECK (ending IN ('achieved', 'dropped', 'leaked', 'unknown')),
            CONSTRAINT ck_intention_verdict_form CHECK (form = 'achieve'),
            CONSTRAINT ck_intention_verdict_condition_form CHECK (condition_form IN ('act_event', 'person_event', 'predicate', 'deadline', 'untyped')),
            CONSTRAINT ck_intention_verdict_graph_kind CHECK (kind = 'aggregate'),
            CONSTRAINT ck_intention_verdict_graph_basis CHECK (basis = 'computed'),
            CONSTRAINT ck_intention_verdict_graph_state CHECK (state IN ('live', 'tombstoned')),
            CONSTRAINT ck_intention_verdict_graph_exposure CHECK (exposure IN ('external', 'internal', 'operator')),
            CONSTRAINT ck_intention_verdict_graph_reasoning CHECK (reasoning IS NULL OR reasoning IN ('told', 'deduced', 'induced', 'abduced')),
            CONSTRAINT ck_intention_verdict_graph_confidence CHECK (confidence IS NULL OR confidence IN ('low', 'medium', 'high')),
            CONSTRAINT ck_intention_verdict_graph_inferred_detail CHECK ((basis = 'inferred') = (reasoning IS NOT NULL AND confidence IS NOT NULL)),
            CONSTRAINT fk_intention_verdict_graph_registry FOREIGN KEY(graph_id, unit, kind) REFERENCES registry (id, unit, kind),
            CONSTRAINT fk_intention_verdict_graph_replaces FOREIGN KEY(replaces, unit, kind) REFERENCES registry (id, unit, kind),
            CONSTRAINT uq_intention_verdict_graph_replaces UNIQUE (replaces),
            CONSTRAINT ck_intention_verdict_graph_pair CHECK ((unit IS NULL) = (graph_id IS NULL)),
            CONSTRAINT fk_intention_verdict_intention FOREIGN KEY(intention_id, unit) REFERENCES intention (id, unit),
            CONSTRAINT fk_intention_verdict_witness FOREIGN KEY(witness_id, unit) REFERENCES registry (id, unit)
        );
        CREATE INDEX idx_intention_verdict_unit_ended ON intention_verdict (unit, ended_at)
    """)
    op.execute("""
        CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE
        ON intention_verdict FOR EACH ROW EXECUTE FUNCTION nora_row_history('intention_id','unit')
    """)
    op.execute("""
        CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON intention_verdict
        FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()
    """)


def downgrade() -> None:
    op.drop_table("intention_verdict")
