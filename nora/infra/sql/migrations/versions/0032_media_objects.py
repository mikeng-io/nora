"""media_objects — the unified media index (DEC-138).

One content-addressed index for every byte Nora holds: inbound photos,
generated images, fetched attachments. Folds `media_descriptions` (the vision
description cache) and `generated_images` (the generated-image library,
migration 0031) into a SINGLE table where `source` is an attribute — never
one table + one tool family per acquisition path.

Data moves:

- `media_descriptions` rows become `source='inbound'` objects whose
  description came from a vision model (`describe_model` = the model that
  wrote it). `media_type`/`size` are unknown at that layer (the descriptions
  table was keyed on content only), so they default — the transcript's
  `media_json` is still the authoritative per-conversation record.
- `generated_images` rows become `source='generated'` objects with their
  prompt/title/description/provider/model/aspect_ratio/platform/
  conversation_id packed into `provenance` JSONB (all NOT NULL columns of
  the old table survive inside the provenance object). Their description is
  SEEDED from the prompt — Nora is the creator, so no vision call is ever
  needed to know what her own image shows.

`sticker_index` is untouched: the sticker identity layer
(platform, fetch_token) → sha256 lives BESIDE media_objects, never inside
it — an identity is not a media object.

HAND-WRITTEN for the same reason 0028/0029/0030/0031 are: the parity gate
(`tests/infra/test_schema_parity.py`) catches drift between this file and
`nora.infra.sql.schema.MEDIA_OBJECTS`.

Revision ID: 0032_media_objects
Revises: 0031_generated_images
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_media_objects"
down_revision = "0031_generated_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_objects",
        sa.Column("sha256", sa.Text, primary_key=True),
        sa.Column("media_type", sa.Text, nullable=False, server_default=""),
        sa.Column("size", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("describe_model", sa.Text),
        sa.Column(
            "provenance", sa.dialects.postgresql.JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at_ms", sa.BigInteger, nullable=False),
    )

    # media_descriptions -> source='inbound', description stays, model ->
    # describe_model, created_at DateTime -> ms-epoch.
    op.execute(
        """
        INSERT INTO media_objects
            (sha256, media_type, size, source, description, describe_model,
             provenance, created_at_ms)
        SELECT
            d.sha256, '', 0, 'inbound', d.description, d.model,
            '{}'::jsonb,
            (EXTRACT(EPOCH FROM d.created_at) * 1000)::bigint
        FROM media_descriptions d
        """
    )

    # generated_images -> source='generated': prompt/title/description/
    # provider/model/aspect_ratio/platform/conversation_id into provenance;
    # description stays (SEEDED from the prompt at creation), describe_model
    # NULL.
    op.execute(
        """
        INSERT INTO media_objects
            (sha256, media_type, size, source, description, describe_model,
             provenance, created_at_ms)
        SELECT
            g.sha256, g.media_type, g.size, 'generated', g.description, NULL,
            jsonb_build_object(
                'prompt', g.prompt,
                'title', g.title,
                'description', g.description,
                'provider', g.provider,
                'model', g.model,
                'aspect_ratio', g.aspect_ratio,
                'platform', g.platform,
                'conversation_id', g.conversation_id
            ),
            g.created_at_ms
        FROM generated_images g
        """
    )

    # The two source tables are folded: no code reads them any more. Drop
    # AFTER the copy — a crash between create and drop leaves both intact.
    op.drop_table("media_descriptions")
    op.drop_table("generated_images")


def downgrade() -> None:
    # Rebuild the two source tables and reverse the fold. media_descriptions
    # rows are the inbound ones; generated_images rows are the generated ones
    # with their provenance un-packed. `media_descriptions.created_at` is
    # reconstructed from ms-epoch. Content that arrived under neither source
    # (future 'fetched') is preserved nowhere — downgrade is best-effort for
    # the data it can place, and drops the rest, which is the honest
    # statement of a reversible-but-lossy rollback.
    op.create_table(
        "media_descriptions",
        sa.Column("sha256", sa.Text, primary_key=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "generated_images",
        sa.Column("sha256", sa.Text, primary_key=True),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("aspect_ratio", sa.Text),
        sa.Column("media_type", sa.Text, nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("created_at_ms", sa.BigInteger, nullable=False),
    )

    op.execute(
        """
        INSERT INTO media_descriptions (sha256, description, model, created_at)
        SELECT
            m.sha256, m.description,
            COALESCE(m.describe_model, ''),
            to_timestamp(m.created_at_ms / 1000.0)
        FROM media_objects m
        WHERE m.source = 'inbound'
        """
    )
    op.execute(
        """
        INSERT INTO generated_images
            (sha256, prompt, title, description, provider, model, aspect_ratio,
             media_type, size, platform, conversation_id, created_at_ms)
        SELECT
            m.sha256,
            COALESCE(m.provenance ->> 'prompt', ''),
            COALESCE(m.provenance ->> 'title', ''),
            m.description,
            COALESCE(m.provenance ->> 'provider', ''),
            COALESCE(m.provenance ->> 'model', ''),
            m.provenance ->> 'aspect_ratio',
            m.media_type,
            m.size,
            COALESCE(m.provenance ->> 'platform', ''),
            COALESCE(m.provenance ->> 'conversation_id', ''),
            m.created_at_ms
        FROM media_objects m
        WHERE m.source = 'generated'
        """
    )
    op.drop_table("media_objects")
