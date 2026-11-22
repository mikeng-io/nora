"""Register claim_support on the graph."""

# ruff: noqa: E501
from alembic import op

revision = "0174_claim_support"
down_revision = "0173_forget_dispute"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE claim_support (
            claim_id TEXT NOT NULL,
            support_count INTEGER NOT NULL,
            last_evidenced_at TIMESTAMP WITH TIME ZONE,
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
            writer TEXT DEFAULT 'SqlClaimSupport' NOT NULL,
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
            PRIMARY KEY (claim_id, unit),
            CONSTRAINT ck_claim_support_tally CHECK (support_count >= 0 AND ((support_count = 0) = (last_evidenced_at IS NULL))),
            CONSTRAINT ck_claim_support_graph_kind CHECK (kind = 'aggregate'),
            CONSTRAINT ck_claim_support_graph_basis CHECK (basis = 'computed'),
            CONSTRAINT ck_claim_support_graph_state CHECK (state IN ('live', 'tombstoned')),
            CONSTRAINT ck_claim_support_graph_exposure CHECK (exposure IN ('external', 'internal', 'operator')),
            CONSTRAINT ck_claim_support_graph_reasoning CHECK (reasoning IS NULL OR reasoning IN ('told', 'deduced', 'induced', 'abduced')),
            CONSTRAINT ck_claim_support_graph_confidence CHECK (confidence IS NULL OR confidence IN ('low', 'medium', 'high')),
            CONSTRAINT ck_claim_support_graph_inferred_detail CHECK ((basis = 'inferred') = (reasoning IS NOT NULL AND confidence IS NOT NULL)),
            CONSTRAINT fk_claim_support_graph_registry FOREIGN KEY(graph_id, unit, kind) REFERENCES registry (id, unit, kind),
            CONSTRAINT fk_claim_support_graph_replaces FOREIGN KEY(replaces, unit, kind) REFERENCES registry (id, unit, kind),
            CONSTRAINT uq_claim_support_graph_replaces UNIQUE (replaces),
            CONSTRAINT ck_claim_support_graph_pair CHECK ((unit IS NULL) = (graph_id IS NULL)),
            CONSTRAINT fk_claim_support_claim FOREIGN KEY(claim_id, unit) REFERENCES claim (id, unit)
        )
    """)
    op.execute("""
        CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE
        ON claim_support FOR EACH ROW EXECUTE FUNCTION nora_row_history('claim_id','unit')
    """)
    op.execute("""
        CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON claim_support
        FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()
    """)



def downgrade() -> None:
    op.drop_table("claim_support")
