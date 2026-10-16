"""Migration `0104_external_files` backfills the file links of rows stored
before the column existed, so an old link's handle resolves."""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import command

from nora.infra.sql.migrations_runner import alembic_config, sync_url_for

ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"


def test_the_backfill_records_old_links_and_leaves_other_rows_null(pg_url):
    cfg = alembic_config(pg_url)
    command.downgrade(cfg, "0103_memos")
    engine = sa.create_engine(sync_url_for(pg_url))
    try:
        with engine.begin() as conn:
            for body, is_nora in (
                (f"plan https://drive.google.com/file/d/{ID}/view?resourcekey=0-k", 0),
                ("https://drive.google.com/drive/folders/abc", 0),
                ("hello", 0),
                (f"https://docs.google.com/document/d/{ID}/edit", 1),
            ):
                conn.execute(sa.text(
                    "insert into messages (platform, conversation_id, conversation_type, "
                    "sender_id, ts, body, is_nora, delivery_status) values "
                    "('signal', 'g1', 'group', '+852', 1, :b, :n, 'sent')"
                ), {"b": body, "n": is_nora})
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            rows = conn.execute(sa.text(
                "select body, external_files_json from messages order by id"
            )).all()
    finally:
        engine.dispose()
    assert json.loads(rows[0][1]) == [{
        "provider": "google_drive", "file_id": ID,
        "url": f"https://drive.google.com/file/d/{ID}/view?resourcekey=0-k",
        "label": "Google Drive link", "resource_key": "0-k",
    }]
    assert [r[1] for r in rows[1:]] == [None, None, None]
