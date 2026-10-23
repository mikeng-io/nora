"""The voice utterance endpoint through the REAL wired pipeline: a POSTed
transcript must produce the same artifacts a typed Discord message would —
an audit row in Postgres (with the speaker resolved to their person) and a
Honcho memory feed as a user input — and the route must answer 202 without
waiting on any of it.

Same wiring posture as `tests/integration/harness.py` (real ingest +
dispatcher + `SqlRecordStore` against a throwaway database, fakes only at
the edges), with a DISCORD conversation in policy because the voice path's
`conversation_id` is Discord's flattened `"<guild>/<channel>"` — a Signal
room id would prove nothing about the namespace the wake service sends.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest
from aiohttp import web

from nora.app.runtime import BackgroundTasks
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import TriggerTextPolicy
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import FakeAgentRunner, FakeMemoryStore
from nora.web.voice import register_voice
from tests.builders import load_example_config, override_config
from tests.conftest import build_test_doctrine_store
from tests.fakes import SinkSender
from tests.offline_pipeline import build_offline_pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]

GUILD = "411111111111111111"
CHANNEL = "422222222222222222"
CONV = f"{GUILD}/{CHANNEL}"
USER = "433333333333333333"
TEXT = "喂 Nora 聽日天氣點"


async def _drain(background) -> None:
    """Await every spawned background task — the memory feed is SPAWNED off
    the ingest path, so a 202 can (correctly) precede it. Same loop as
    `tests/integration/harness.py`'s `drain`."""
    while background._tasks:
        await asyncio.gather(*list(background._tasks), return_exceptions=True)
        await asyncio.sleep(0)


# The wake gate's bearer: the utterance route refuses everything without it.
TOKEN = "test-bridge-token"  # noqa: S105 - a fake
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def bridge_token(monkeypatch):
    monkeypatch.setenv("NORA_BRIDGE_TOKEN", TOKEN)


@pytest.fixture
async def client(tmp_path, monkeypatch, pg_url, aiohttp_client):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    persons_path = tmp_path / "identity.toml"
    persons_path.write_text(
        '[persons.miles]\nname = "Miles"\n'
        "[[persons.miles.discord]]\n"
        f'user_id = "{USER}"\n'
    )
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text(
        'default = "deny"\n'
        "[[conversations]]\n"
        'platform = "discord"\n'
        f'id = "{CONV}"\n'
        f'label = "{CONV}"\n'
        'persons = "*"\n'
        # The guild's speech is recorded HERE: this room is a TEXT channel, and
        # voice is a surface of it, not a room of its own.
        "voice = true\n"
    )
    cfg = override_config(
        load_example_config(tmp_path),
        {
            "store.data_dir": tmp_path,
            "dreamer.dir": REPO_ROOT / "nora" / "dreamer",
            "identity_file": persons_path,
            "policy_file": policy_path,
        },
    )
    database_url = os.environ[cfg.store.database_url_env]
    run_migrations(database_url)
    engine = create_sql_engine(database_url)
    store = SqlRecordStore(engine)
    memory = FakeMemoryStore()
    dispatcher, _engagement, background, _trigger = build_offline_pipeline(
        cfg,
        adapter=SinkSender(),
        audit=store,
        memory=memory,
        persons=load_persons(persons_path),
        doctrine_store=build_test_doctrine_store(),
        runner=FakeAgentRunner([]),
        metrics=Metrics(),
    )
    app = web.Application()
    allowlist = load_policy(policy_path)
    register_voice(
        app, ingest=dispatcher.on_message,
        resolve_voice_room=lambda guild_id: (
            room.primary_id
            if (room := allowlist.voice_room("discord", guild_id)) is not None
            else None
        ),
    )
    client = await aiohttp_client(app)
    client.store = store
    client.memory = memory
    client.background = background
    yield client
    await background.cancel_all()
    await store.aclose()


async def test_a_posted_utterance_lands_as_an_ingest_row_and_a_memory_feed(client):
    resp = await client.post(
        "/app/v1/voice/utterance",
        headers=AUTH,
        json={"user_id": USER, "text": TEXT, "guild_id": GUILD,
              "voice_channel_id": "499999999999999999"},
    )
    assert resp.status == 202
    await _drain(client.background)

    rows = await client.store.recent(conversation_id=CONV, limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row.body == TEXT
    assert row.sender_id == USER
    assert not row.is_nora
    # The sender resolved through identity.toml — the voice path carries the
    # same attribution a typed message gets, not an anonymous speaker.
    assert row.person_id == "miles"
    assert json.loads(row.raw_json)["voice"] is True

    # Honcho was fed the utterance as MILES's input — the memory-feed half of
    # "the reactive path receives the transcript as a user input".
    assert client.memory.fed == [("miles", TEXT, CONV)]


async def test_an_utterance_from_an_unmarked_guild_records_nothing(
    tmp_path, monkeypatch, pg_url, aiohttp_client
):
    """The refusal path, through the endpoint, against the REAL policy file —
    `build_offline_pipeline` hardcodes a PERMISSIVE allowlist, so this test
    wires a bare `IngestPipeline` the way the composition root does.

    The guild has to resolve to a room before a message exists at all, so an
    unlisted guild is a 400 and ingest is never entered. 400 rather than 202 on
    purpose: the only caller is our own wake container, and answering
    "accepted" for something that was definitely not accepted is the silent
    failure R-5 exists to forbid — a misconfigured guild would look exactly like
    a quiet room."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    persons_path = tmp_path / "identity.toml"
    persons_path.write_text(
        '[persons.miles]\nname = "Miles"\n'
        "[[persons.miles.discord]]\n"
        f'user_id = "{USER}"\n'
    )
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text(
        'default = "deny"\n'
        "[[conversations]]\n"
        'platform = "discord"\n'
        f'id = "{CONV}"\n'
        f'label = "{CONV}"\n'
        'persons = "*"\n'
        # The guild's speech is recorded HERE: this room is a TEXT channel, and
        # voice is a surface of it, not a room of its own.
        "voice = true\n"
    )
    cfg = override_config(
        load_example_config(tmp_path),
        {"store.data_dir": tmp_path, "identity_file": persons_path,
         "policy_file": policy_path},
    )
    database_url = os.environ[cfg.store.database_url_env]
    run_migrations(database_url)
    engine = create_sql_engine(database_url)
    store = SqlRecordStore(engine)
    memory = FakeMemoryStore()
    background = BackgroundTasks()
    ingest = IngestPipeline(
        audit=store, memory=memory,
        persons=load_persons(persons_path),
        policy=TriggerTextPolicy(cfg.engagement.trigger.text),
        allowlist=load_policy(policy_path),
        metrics=Metrics(), spawn=background.spawn,
    )

    async def sink(message):
        await ingest.run(message=message)

    app = web.Application()
    allowlist = load_policy(policy_path)
    register_voice(
        app, ingest=sink,
        resolve_voice_room=lambda guild_id: (
            room.primary_id
            if (room := allowlist.voice_room("discord", guild_id)) is not None
            else None
        ),
    )
    client = await aiohttp_client(app)
    try:
        resp = await client.post(
            "/app/v1/voice/utterance",
            headers=AUTH,
            json={"user_id": USER, "text": TEXT,
                  "guild_id": "499999999999999999"},
        )
        assert resp.status == 400
        await _drain(background)
        assert await store.recent(conversation_id=CONV, limit=10) == []
        assert memory.fed == []
    finally:
        await background.cancel_all()
        await store.aclose()
