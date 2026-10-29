"""Read-only finder against a generated local eval database."""
from uuid import uuid4

import psycopg

from scripts.find_cognition_anchors import scan


async def test_finder_reads_generated_eval_clone(pg_admin, monkeypatch, tmp_path):
    name = "t_finder_" + uuid4().hex + "_eval"
    mapping = tmp_path / "pseudonyms.txt"
    mapping.write_text("room_generated==>generated-room\n")
    monkeypatch.setenv("NORA_EVAL_PSEUDONYM_MAP", str(mapping))
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
        try:
            with psycopg.connect(pg_admin.dsn(name)) as conn:
                conn.execute("CREATE TABLE messages (id int, conversation_id text, ts bigint, "
                             "sender_id text, body text, is_nora int)")
                conn.execute("INSERT INTO messages VALUES (1, %s, 1000, %s, %s, 0)",
                             ("generated-room", "generated-author", "k"))
            url = (f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
                   f"@{pg_admin.host}:{pg_admin.port}/{name}")
            result = await scan(url)
            assert any(r["scenario"] == 2 and r["room"] == "room_generated" for r in result)
            with psycopg.connect(pg_admin.dsn(name)) as conn:
                assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
        finally:
            admin.execute(f'DROP DATABASE "{name}"')
