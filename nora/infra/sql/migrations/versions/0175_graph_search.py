"""Project searchable graph rows into one derived lexical and vector index."""

import sqlalchemy as sa
from alembic import op

revision = "0175_graph_search"
down_revision = "0174_claim_support"
branch_labels = None
depends_on = None

_SOURCES = {
    "messages": ("graph_id", "body"),
    "fetched_pages": ("graph_id", "url"),
    "social_item": ("graph_id", "caption", "alt_text"),
    "nora_pass_output": ("graph_id", "content"),
    "claim": ("id", "content"),
    "intention": ("id", "done_when", "holds_while", "drop_when"),
    "entity": ("id", "name", "aliases"),
}


def upgrade() -> None:
    op.execute("""
        CREATE TABLE graph_search (
            graph_id text NOT NULL,
            unit text NOT NULL,
            text text NOT NULL,
            embedding halfvec(2048),
            embedding_model text,
            embedded_at bigint,
            embed_attempts integer NOT NULL DEFAULT 0,
            embed_error text,
            embed_input text,
            CONSTRAINT pk_graph_search PRIMARY KEY (graph_id, unit),
            CONSTRAINT fk_graph_search_registry FOREIGN KEY (graph_id, unit)
                REFERENCES registry (id, unit),
            CONSTRAINT ck_graph_search_embed_input CHECK (embed_input = 'text')
        )
        """)
    op.execute(
        "CREATE INDEX idx_graph_search_text_trgm ON graph_search USING gin (text gin_trgm_ops)",
    )
    op.execute("""
        CREATE INDEX idx_graph_search_embedding_256 ON graph_search USING hnsw
            ((subvector(embedding, 1, 256)::halfvec(256)) halfvec_cosine_ops)
        """)
    op.execute("""
        CREATE FUNCTION nora_graph_search_text(image jsonb, fields text[]) RETURNS text
        LANGUAGE sql IMMUTABLE AS $$
            SELECT string_agg(leaf #>> '{}', ' ' ORDER BY f.ord, l.ord)
            FROM unnest(fields) WITH ORDINALITY AS f(field, ord)
            CROSS JOIN LATERAL jsonb_path_query(
                image -> f.field, 'strict $.** ? (@.type() == "string")'
            ) WITH ORDINALITY AS l(leaf, ord)
            WHERE btrim(leaf #>> '{}') <> ''
              AND image ->> 'state' NOT IN ('tombstoned', 'hidden')
              AND image ->> 'exposure' <> 'operator'
        $$
        """)
    op.execute("""
        CREATE FUNCTION nora_graph_search_sync() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            old_id text;
            new_id text;
            new_unit text;
            new_text text;
        BEGIN
            IF current_setting('nora.row_history', true) = 'off' THEN
                RETURN NULL;
            END IF;
            IF TG_OP <> 'DELETE' THEN
                new_id := to_jsonb(NEW) ->> TG_ARGV[0];
                new_unit := NEW.unit;
            END IF;
            IF TG_OP <> 'INSERT' THEN
                old_id := to_jsonb(OLD) ->> TG_ARGV[0];
                IF old_id IS NOT NULL AND (old_id, OLD.unit) IS DISTINCT FROM (new_id, new_unit)
                THEN
                    DELETE FROM graph_search WHERE graph_id = old_id AND unit = OLD.unit;
                END IF;
            END IF;
            IF new_id IS NULL THEN
                RETURN NULL;
            END IF;
            new_text := nora_graph_search_text(to_jsonb(NEW), TG_ARGV[1:TG_NARGS-1]);
            IF new_text IS NULL THEN
                DELETE FROM graph_search WHERE graph_id = new_id AND unit = NEW.unit;
            ELSE
                INSERT INTO graph_search (graph_id, unit, text) VALUES (new_id, NEW.unit, new_text)
                ON CONFLICT (graph_id, unit) DO UPDATE
                    SET text = EXCLUDED.text, embedding = NULL, embedding_model = NULL,
                        embedded_at = NULL, embed_attempts = 0,
                        embed_error = NULL, embed_input = NULL
                    WHERE graph_search.text IS DISTINCT FROM EXCLUDED.text;
            END IF;
            RETURN NULL;
        END
        $$
        """)
    for table, (identity, *fields) in _SOURCES.items():
        columns = ", ".join(f"'{field}'" for field in fields)
        op.execute(
            f"CREATE TRIGGER graph_search_sync AFTER INSERT OR UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION nora_graph_search_sync('{identity}', {columns})",
        )
        lexical = f"nora_graph_search_text(to_jsonb(source), ARRAY[{columns}])"
        op.execute(sa.text(
            "INSERT INTO graph_search (graph_id, unit, text) "  # noqa: S608 — migration constants
            f"SELECT {identity}, unit, lexical.text FROM {table} AS source "
            f"CROSS JOIN LATERAL (SELECT {lexical} AS text) AS lexical "
            f"WHERE {identity} IS NOT NULL AND lexical.text IS NOT NULL"
        ))


def downgrade() -> None:
    for table in _SOURCES:
        op.execute(f"DROP TRIGGER graph_search_sync ON {table}")
    op.execute("DROP FUNCTION nora_graph_search_sync()")
    op.execute("DROP FUNCTION nora_graph_search_text(jsonb, text[])")
    op.drop_table("graph_search")
