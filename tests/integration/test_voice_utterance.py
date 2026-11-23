"""The utterance `intake` route through the REAL wired pipeline: a recorded
transcript must produce the same artifacts a typed Discord message would —
an audit row in Postgres (with the speaker resolved to their person) — and
the route must answer `complete` without waiting on any of it.

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

from nora.access import IdentityAccess
from nora.app.runtime import BackgroundTasks
from nora.domain.service_api import status_for
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import TriggerTextPolicy
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing import FakeAgentRunner
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
    """Await every spawned background task — work is SPAWNED off
    the ingest path, so a 202 can (correctly) precede it. Same loop as
    `tests/integration/harness.py`'s `drain`."""
    while background._tasks:
        await asyncio.gather(*list(background._tasks), return_exceptions=True)
        await asyncio.sleep(0)


# The wake gate's bearer: the utterance route refuses everything without it.
TOKEN = "test-bridge-token"  # noqa: S105 - a fake
AUTH = {"Authorization": f"Bearer {TOKEN}"}
INTAKE = "/v1/intake/operations"


@pytest.fixture(autouse=True)
def bridge_token(monkeypatch):
    monkeypatch.setenv("NORA_BRIDGE_TOKEN", TOKEN)


@pytest.fixture
async def client(tmp_path, monkeypatch, clean_pg_url, aiohttp_client):
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
    dispatcher, _engagement, background, _trigger = build_offline_pipeline(
        cfg,
        adapter=SinkSender(),
        audit=store,
        access=IdentityAccess.for_files(
            persons=load_persons(persons_path), policy=load_policy(policy_path)),
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
    client.background = background
    yield client
    await background.cancel_all()
    await store.aclose()


async def _record(client, operation_id: str, **effect) -> dict:
    resp = await client.post(INTAKE, headers=AUTH, json={
        "operation_id": operation_id, "kind": "record",
        "effect": {"event_id": "evt-1", "kind": "utterance", "user_id": USER,
                   "text": TEXT, "guild_id": GUILD, **effect}})
    body = await resp.json()
    assert set(body) == {"outcome", "payload", "when", "from", "scope", "trace"}
    assert resp.status == status_for(body["outcome"])
    assert body["when"]["state"] == "settled" and body["scope"]["level"] == "installation"
    return body


async def test_utterance_outcomes_follow_the_contract(client):
    """Accepted is `complete` and lands one ingest row; the same `event_id` under
    a new operation is `complete` and lands none; a guild with no voice room is
    `rejected` and records nothing."""
    first = await _record(client, "op-1", voice_channel_id="499999999999999999")
    await _drain(client.background)
    again = await _record(client, "op-2", voice_channel_id="499999999999999999")
    await _drain(client.background)
    refused = await _record(client, "op-3", event_id="evt-2", guild_id="499999999999999999")
    await _drain(client.background)

    assert [first["outcome"], again["outcome"], refused["outcome"]] == [
        "complete", "complete", "rejected"]
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


async def test_an_utterance_from_an_unmarked_guild_records_nothing(
    tmp_path, monkeypatch, clean_pg_url, aiohttp_client
):
    """The refusal path, through the endpoint, against the REAL policy file —
    `build_offline_pipeline` hardcodes a PERMISSIVE allowlist, so this test
    wires a bare `IngestPipeline` the way the composition root does.

    The guild has to resolve to a room before a message exists at all, so an
    unlisted guild is `rejected` and ingest is never entered: answering
    `complete` for something that was definitely not accepted is the silent
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
    background = BackgroundTasks()
    ingest = IngestPipeline(
        audit=store,
        access=IdentityAccess.for_files(
            load_persons(persons_path), load_policy(policy_path)),
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
        body = await _record(client, "op-1", guild_id="499999999999999999")
        assert body["outcome"] == "rejected"
        await _drain(background)
        assert await store.recent(conversation_id=CONV, limit=10) == []
    finally:
        await background.cancel_all()
        await store.aclose()
