"""Read-only finder against a generated local eval database."""
import json
import tomllib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import psycopg
import tomlkit

from scripts import confirm_cognition_anchors as confirmation
from scripts.find_cognition_anchors import scan

COGNITION_DDL = """
CREATE TABLE stimulus (
  id bigint PRIMARY KEY, unit text NOT NULL, kind text NOT NULL, item text NOT NULL,
  ref text NOT NULL, platform text, conversation_id text, person_id text,
  cues text[] NOT NULL DEFAULT '{}', waits int NOT NULL DEFAULT 0,
  at_ms bigint NOT NULL, due_ms bigint, recorded_at timestamptz NOT NULL
);
CREATE TABLE cognition_wake (
  id bigint PRIMARY KEY, unit text NOT NULL, mode text NOT NULL, reason text NOT NULL,
  from_stimulus_id bigint NOT NULL, through_stimulus_id bigint NOT NULL,
  stimulus_count int NOT NULL, eligible_count int NOT NULL, oldest_pending_ms bigint,
  cut_cooldown int NOT NULL DEFAULT 0, detail jsonb NOT NULL DEFAULT '{}',
  at_ms bigint NOT NULL, recorded_at timestamptz NOT NULL, tracked int NOT NULL DEFAULT 0
);
CREATE TABLE cognition_session (wake_id bigint, agenda jsonb, started_at_ms bigint);
"""

STIMULUS_ROW = ("INSERT INTO stimulus (id, unit, kind, item, ref, platform, "
                "conversation_id, at_ms, recorded_at) "
                "VALUES (%s, 'generated-unit', 'message', 'message', %s, 'signal', %s, %s, "
                "to_timestamp(%s / 1000.0))")

WAKE_ROW = ("INSERT INTO cognition_wake (id, unit, mode, reason, from_stimulus_id, "
            "through_stimulus_id, stimulus_count, eligible_count, at_ms, recorded_at) "
            "VALUES (%s, 'generated-unit', 'live', 'batch', %s, %s, %s, 1, %s, "
            "to_timestamp(%s / 1000.0))")


async def test_finder_reads_generated_eval_clone(
    pg_admin, monkeypatch, tmp_path, example_config_path,
):
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
                conn.execute(COGNITION_DDL)
                # Wake 9's batch holds message 1; wake 10's recorded count is
                # incomplete, so it is skipped even though its row claims 2.
                conn.execute(STIMULUS_ROW, (1, "messages:1", "generated-room", 1000, 1000))
                conn.execute(WAKE_ROW, (9, 0, 1, 1, 1500, 1500))
                conn.execute(WAKE_ROW, (10, 0, 1, 2, 1600, 1600))
            url = (f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
                   f"@{pg_admin.host}:{pg_admin.port}/{name}")
            result = await scan(url)
            assert result == [{"room": "room_generated", "scenario": 2, "wake": 9,
                               "strength": 1}]
            config = tmp_path / "confirmation.toml"
            config.write_text('[llm]\nbase_url="https://generated.invalid/v1"\n'
                              'api_key_env="GENERATED_CONFIRM_KEY"\nmodel="generated-model"\n'
                              'timeout_seconds=1\nmax_tokens=2000\ntemperature=0.7\n')
            settings = tomllib.loads(example_config_path.read_text())
            settings["llm"].update(tomllib.loads(config.read_text())["llm"])
            config.write_text(tomlkit.dumps(settings))
            for sidecar in ("identity.toml", "policy.toml"):
                (tmp_path / sidecar).write_text("generated")
            monkeypatch.setenv("GENERATED_CONFIRM_KEY", "generated-key")
            with psycopg.connect(pg_admin.dsn(name)) as conn:
                conn.execute("CREATE TABLE note (id text, unit text, head_revision_id text, "
                             "retired_at timestamptz)")
                conn.execute("CREATE TABLE note_revision (id text, body text)")
                conn.execute("INSERT INTO note_revision VALUES ('r1', 'generated open note')")
                conn.execute("INSERT INTO note VALUES ('n1', 'generated-unit', 'r1', NULL)")
                conn.execute("CREATE TABLE events (unit_id text, status text, title text)")
                conn.execute("INSERT INTO events VALUES ('generated-unit', 'proposed', 'dinner')")
                from sqlalchemy.dialects import postgresql

                from nora.infra.sql.schema import PROFILE_LINE

                # The generated clone carries the store's real column set: the
                # chain-heads read maps every column through row_to_line.
                columns = ", ".join(
                    f"{c.name} {c.type.compile(dialect=postgresql.dialect())}"
                    for c in PROFILE_LINE.c)
                conn.execute(f"CREATE TABLE profile_line ({columns})")
                conn.execute("INSERT INTO profile_line (id, profile_key, part, kind, text, "
                             "mode, state, unit, written_at, written_by_session, citations, "
                             "draws_on, exclude_persons, source_medium) VALUES "
                             "('00000000-0000-0000-0000-0000000000aa', 'pk', 'facts', "
                             "'fact', 'prior', 'told', 'current', 'generated-unit', 1, "
                             "'gen', '{}', '{}', '{}', '{}')")
            import evals.backbone
            import evals.subject_config
            import nora.subject.sandbox
            monkeypatch.setattr(evals.subject_config, "materialise", lambda **kw: None)
            @asynccontextmanager
            async def restored(source, now, **kw):
                yield url, None
            monkeypatch.setattr(nora.subject.sandbox, "sandbox_as_of", restored)
            runner = SimpleNamespace(inspect=AsyncMock(
                return_value={"system": "generated system", "agenda": "generated agenda"}))
            subject = SimpleNamespace(
                deps=SimpleNamespace(cognition_runner=runner), unit="generated-unit")
            monkeypatch.setattr(nora.subject, "obtain", AsyncMock(return_value=subject))
            monkeypatch.setattr(evals.backbone, "_close", AsyncMock())
            original_client = httpx.AsyncClient
            for mode in ("match", "no_match", "wrong_shape", "timeout", "error"):
                def serve(request, mode=mode):
                    payload = json.loads(request.content)
                    # The moment arrives as ONE typed evidence block.
                    from xml.sax.saxutils import unescape
                    user = payload["messages"][1]["content"]
                    moment = json.loads(unescape(
                        user.split(">\n", 1)[1].rsplit("\n</wake_moment>", 1)[0]))
                    assert moment["at_ms"] == 1500
                    assert moment["room"] == "generated-room"
                    assert moment["system"] == "generated system"
                    assert moment["agenda"] == "generated agenda"
                    assert moment["memory"]["open_notes"][0]["body"] == "generated open note"
                    assert moment["memory"]["open_events"][0]["status"] == "proposed"
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
                    {"room": "room_generated", "wake": 9, "scenario": 2}],
                    {"generated-room": "room_generated"})
                expected = mode if mode in {"match", "no_match"} else "unavailable"
                assert confirmed == [{"room": "room_generated", "wake": 9,
                                      "scenario": 2, "result": expected}]
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
                conn.execute(COGNITION_DDL)
                conn.execute(STIMULUS_ROW, (1, "messages:1", "generated-room", 1000, 1000))
                conn.execute(STIMULUS_ROW, (2, "messages:2", "generated-room", 2000, 2000))
                conn.execute(WAKE_ROW, (9, 0, 2, 2, 2500, 2500))
            url = (f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
                   f"@{pg_admin.host}:{pg_admin.port}/{name}")
            result = await scan(url)
            assert result == [{"room": "room_generated", "scenario": 3, "wake": 9,
                               "strength": 1}]
        finally:
            admin.execute(f'DROP DATABASE "{name}"')
