"""Read-only finder against a generated local eval database, and the
confirmer's real end-to-end: `obtain` inside case-owned recorded services,
the production `inspect`, and the bound `Judge` on a finite model recording."""
import functools
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import sqlalchemy as sa

from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.notes import SqlNoteStore
from nora.subject import sandbox_as_of
from nora.subject.eval_evidence import read_events
from nora.subject.obtain import SubjectSpec, build_config
from nora.subject.recorded_services import RecordedServices
from scripts import confirm_cognition_anchors as confirmation
from scripts.find_cognition_anchors import scan

COGNITION_DDL = """
CREATE TABLE stimulus (
  id bigint PRIMARY KEY, unit text NOT NULL, kind text NOT NULL, item text NOT NULL,
  ref text NOT NULL, platform text, conversation_id text, person_id text,
  cues text[] NOT NULL DEFAULT '{}', waits int NOT NULL DEFAULT 0,
  at_ms bigint NOT NULL, due_ms bigint, due_tick bigint, recorded_at timestamptz NOT NULL
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

POLICY = ('default = "deny"\n[[conversations]]\nplatform = "signal"\n'
          'id = "generated-room"\nlabel = "generated-room"\npersons = "*"\n'
          'unit = "generated-unit"\nhome_room = true\n')
IDENTITY = ('[persons.miles_novak]\nname = "Miles"\n'
            '[[persons.miles_novak.signal]]\nphone = "+852111"\n')
ROOM = ("signal", "generated-room")


@contextmanager
def _eval_database(pg_admin, pg_template, name):
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as admin:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (pg_template,))
        admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{pg_template}"')
    try:
        yield (f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
               f"@{pg_admin.host}:{pg_admin.port}/{name}")
    finally:
        with psycopg.connect(
                pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            admin.execute(f'DROP DATABASE "{name}"')


async def test_finder_reads_generated_eval_clone(
    pg_admin, monkeypatch, tmp_path,
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
        finally:
            admin.execute(f'DROP DATABASE "{name}"')


async def test_confirmation_runs_the_obtained_subject_and_bound_judge(
    pg_admin, pg_template, tmp_path, monkeypatch, evaluation_config,
):
    """The whole run inside one generated case: the eval clone carries a
    migrated history written through the production stores, the confirmer
    restores it to a case sandbox, obtains the subject inside a recording it
    owns, assembles the real session input, and judges it through the bound
    `Judge` — one recorded model call, never a fabricated response."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    name = "t_confirm_" + uuid4().hex + "_eval"
    before_ms = int(time.time() * 1000) - 600_000
    with _eval_database(pg_admin, pg_template, name) as url:
        await _run_confirmation(
            pg_admin, pg_template, name, url, before_ms, tmp_path,
            monkeypatch, evaluation_config)


def _recording(cfg) -> RecordedServices:
    """The finite external world for one confirmation; each call answered once."""
    bindings = {"llm.base_url": "model", "platforms.signal.base_url": "bridge"}
    bindings.update(
        {f"providers.{n}.base_url": "model" for n in cfg.providers})
    return RecordedServices({"version": 1, "bindings": bindings,
        "services": {
            "bridge": [
                {"method": "POST", "path": "/api/v1/rpc",
                 "response": {"status": 200, "json": [
                     {"id": "nora-self-contacts", "result": [
                         {"number": "generated-account",
                          "uuid": "generated-aci"}]},
                     {"id": "nora-self-status", "result": [
                         {"number": "generated-account",
                          "uuid": "generated-pni"}]}]}},
                *[{"method": "POST", "path": "/api/v1/rpc",
                   "response": {"status": 200, "json": {
                       "id": "nora-listGroups", "result": [
                           {"id": "generated-room", "name": "generated",
                            "isMember": True, "isBlocked": False,
                            "members": [{"number": "+852111",
                                         "uuid": "generated-member"}]}]}}}
                  for _ in range(2)],
            ],
            "model": [{
                "method": "POST", "path": "/v1/chat/completions",
                "response": {"status": 200, "json": {
                    "id": "generated-completion", "model": cfg.llm.model,
                    "created": 1, "object": "chat.completion",
                    "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant",
                                    "content": '{"match": true}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                              "total_tokens": 12}}},
            }],
        }})


async def _run_confirmation(
        pg_admin, pg_template, name, url, before_ms, tmp_path,
        monkeypatch, evaluation_config) -> None:
    import evals.subject_config
    import nora.subject.sandbox

    # The source's history is written through the production stores, the way
    # the deployment's clone holds it — a snapshot that returned today's
    # state could not produce this moment.
    engine = create_sql_engine(url)
    try:
        message_id = await SqlRecordStore(engine).append(
            message=NormalizedMessage(
                platform="signal", conversation_id="generated-room",
                conversation_type="group", sender_id="+852111",
                sender_name="Miles", timestamp=before_ms,
                body="anyone up for friday", raw={}),
            person_id="miles_novak")
        await SqlNoteStore(engine).create(
            unit="generated-unit", title="Friday ride",
            description="whether friday holds", uncertainty="open",
            closing_condition="somebody says yes",
            established_in="generated-room",
            body="nobody has confirmed friday", established_by="debrief",
            now_ms=before_ms, anchor_date=20260701)
        async with engine.connect() as conn:
            clock_ms = int((await conn.execute(sa.text(
                "SELECT clock_timestamp()"))).scalar_one().timestamp() * 1000)
    finally:
        await engine.dispose()
    at_ms = clock_ms + 60_000  # after every seeded row's recorded_at
    agenda = {"items": [{"stimulus_ids": [1]}]}
    with psycopg.connect(pg_admin.dsn(name)) as conn:
        conn.execute(
            "INSERT INTO stimulus (id, unit, kind, item, ref, platform, "
            "conversation_id, at_ms, recorded_at) VALUES (1, 'generated-unit', "
            "'message', 'message', %s, 'signal', 'generated-room', %s, "
            "to_timestamp(%s / 1000.0))",
            (f"messages:{message_id}", before_ms, before_ms))
        conn.execute(
            "INSERT INTO cognition_wake (id, unit, mode, reason, "
            "from_stimulus_id, through_stimulus_id, stimulus_count, "
            "eligible_count, at_ms, recorded_at) VALUES (9, 'generated-unit', "
            "'live', 'batch', 0, 1, 1, 1, %s, to_timestamp(%s / 1000.0))",
            (at_ms - 60_000, at_ms - 60_000))
        conn.execute(
            "INSERT INTO cognition_session (id, unit, mode, wake_id, "
            "started_at_ms, from_stimulus_id, through_stimulus_id, situation, "
            "agenda) VALUES (1, 'generated-unit', 'live', 9, %s, 0, 1, "
            "'{}'::jsonb, %s::jsonb)", (at_ms, json.dumps(agenda)))

    config = tmp_path / "config" / "config.toml"
    config.parent.mkdir()
    config.write_text(evaluation_config.read_text())
    (config.parent / "identity.toml").write_text(IDENTITY)
    (config.parent / "policy.toml").write_text(POLICY)
    # The confirmer's private rebuild: the fake writes the deployment's own
    # config where the run asked for it, as the real materialise would.
    def materialise(*, example, deployment, target):
        Path(target).write_text(evaluation_config.read_text())
        return target
    monkeypatch.setattr(evals.subject_config, "materialise", materialise)
    monkeypatch.setattr(
        nora.subject.sandbox, "sandbox_as_of",
        functools.partial(sandbox_as_of, template=pg_template))

    cfg = build_config(SubjectSpec(
        database_url=url, workdir=tmp_path / "declared", room=ROOM,
        overrides=confirmation._overrides(False), policy=POLICY,
        identity=IDENTITY))
    recording = _recording(cfg)
    confirmed = await confirmation.confirm(
        url, config, [{"room": "room_a", "wake": 9, "scenario": 3}],
        {"generated-room": "room_a"}, {9: recording})
    assert confirmed == [{"room": "room_a", "wake": 9, "scenario": 3,
                          "result": "match"}]

    # The verdict's provenance: one judge model request captured on the
    # recorded boundary, never an uncounted call.
    root = os.environ["NORA_EVAL_EXCHANGES"]
    newest = Path(max((os.path.join(root, d) for d in os.listdir(root)),
                      key=os.path.getmtime))
    requests = [e for e in read_events(newest)
                if e.get("name") == "nora.model.request"]
    assert len(requests) == 1


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
