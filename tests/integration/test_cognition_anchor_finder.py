"""Read-only finder against a generated local eval database."""
import json
from uuid import uuid4

import httpx
import psycopg

from scripts import confirm_cognition_anchors as confirmation
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
            config = tmp_path / "config.toml"
            config.write_text('[llm]\nbase_url="https://generated.invalid/v1"\n'
                              'api_key_env="GENERATED_CONFIRM_KEY"\nmodel="generated-model"\n'
                              'timeout_seconds=1\nmax_tokens=2000\ntemperature=0.7\n')
            monkeypatch.setenv("GENERATED_CONFIRM_KEY", "generated-key")
            with psycopg.connect(pg_admin.dsn(name)) as conn:
                conn.execute("INSERT INTO messages VALUES (2, %s, 2000, %s, %s, 0)",
                             ("generated-room", "generated-author", "future generated text"))
                conn.execute("INSERT INTO messages VALUES (3, %s, 500, %s, %s, 1)",
                             ("other-generated-room", "other-author", "other room text"))
            original_client = httpx.AsyncClient
            for mode in ("match", "no_match", "wrong_shape", "timeout", "error"):
                def serve(request, mode=mode):
                    payload = json.loads(request.content)
                    moment = json.loads(payload["messages"][1]["content"])
                    assert moment == {"before_ms": 1001, "messages": [
                        {"at_ms": 1000, "speaker": "speaker_1", "text": "k"}]}
                    if mode == "timeout":
                        raise httpx.ReadTimeout("generated private error", request=request)
                    if mode == "error":
                        return httpx.Response(500, text="generated private error")
                    content = ({"match": "yes"} if mode == "wrong_shape"
                               else {"match": mode == "match"})
                    return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                        "message": {"content": json.dumps(content)}}]})

                monkeypatch.setattr(confirmation.httpx, "AsyncClient", lambda **kwargs:
                                    original_client(transport=httpx.MockTransport(serve), **kwargs))
                confirmed = await confirmation.confirm(url, config, [
                    {"room": "room_generated", "before_ms": 1001, "scenario": 2}],
                    {"generated-room": "room_generated"})
                expected = mode if mode in {"match", "no_match"} else "unavailable"
                assert confirmed == [{"room": "room_generated", "before_ms": 1001,
                                      "scenario": 2, "result": expected}]
            with psycopg.connect(pg_admin.dsn(name)) as conn:
                assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 3
        finally:
            admin.execute(f'DROP DATABASE "{name}"')


async def test_finder_keeps_nora_in_generated_window(pg_admin, monkeypatch, tmp_path):
    """A Nora proposal must prevent a lone-ack candidate, just as in confirmation."""
    name = "t_window_" + uuid4().hex + "_eval"
    mapping = tmp_path / "pseudonyms.txt"
    mapping.write_text("room_generated==>generated-room\n")
    monkeypatch.setenv("NORA_EVAL_PSEUDONYM_MAP", str(mapping))
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
        try:
            with psycopg.connect(pg_admin.dsn(name)) as conn:
                conn.execute("CREATE TABLE messages (id int, conversation_id text, ts bigint, "
                             "sender_id text, body text, is_nora int)")
                with conn.cursor() as cursor:
                    cursor.executemany("INSERT INTO messages VALUES (%s, %s, %s, %s, %s, %s)", [
                        (1, "generated-room", 1000, "nora", "dinner at 7?", 1),
                        (2, "generated-room", 2000, "generated-author", "k", 0)])
            url = (f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
                   f"@{pg_admin.host}:{pg_admin.port}/{name}")
            result = await scan(url)
            assert [r["scenario"] for r in result] == [3]
            assert result[0]["counts"] == {"prior": 1, "questions": 1, "same_sender": 0}
        finally:
            admin.execute(f'DROP DATABASE "{name}"')
