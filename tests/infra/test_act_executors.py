"""Production executor paths with generated destinations and persisted act evidence."""

# ruff: noqa: F811 - pytest resolves imported fixtures by name.
import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from nora.agent.delivery_acts import DeliveryActs
from nora.agent.guards import Deliverer
from nora.app.documents_service import DocumentService
from nora.app.subagent_backend import SubagentBackend
from nora.app.subagent_runner import SubagentOutcome
from nora.domain.actions import ActionOwner
from nora.domain.audience import ONE_TIME_CODE
from nora.domain.models import ConversationTarget, DeliveryStatus, NormalizedMessage, TranscriptRow
from nora.domain.ports import LaunchRequest, OutboundQuote, ReactionResult
from nora.engagement.stages.indicate import ACK, begin
from nora.infra.computer.browser import BrowserPage, CdpConnection, connect_browser_cdp
from nora.infra.computer.browser_retrieval import RetrievalAction, dispatch
from nora.infra.computer.client import ComputerdRuntime
from nora.infra.computer.lifecycle import HostLifecycleClient
from nora.infra.computer.manager import ScopeBrowserManager
from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.infra.sql.cognitive_graph_schema import NORA_ACT, NORA_ACT_OUTCOME, REGISTRY
from nora.infra.sql.documents import SqlDocumentStore
from nora.infra.sql.music_queue import SqlMusicQueueStore
from nora.infra.voice.http_controller import VoiceControllerClient
from nora.memory import ActAuthority, EffectRecorder, act_event, act_pass
from nora.obs.metrics import Metrics
from nora.platform.bridge.client import BridgeEndpoint
from nora.platform.bridge.service import ActionReply, DurableActionService
from nora.platform.discord.bridge_control import BridgeControlClient
from nora.platform.discord.command_acts import DiscordCommandActs
from nora.platform.discord.music_notify import make_notify, notice_event
from nora.platform.signal.consent import SignalLinkDelivery
from tests.fakes import SinkSender
from tests.infra.test_act_envelope import PASS, _rows, acts  # noqa: F401 - pytest fixture
from tests.test_guards_deliverer import doctrine  # noqa: F401 - pytest fixture
from tests.unit.test_holder_link import (
    ACCT_I,
    ACCT_R,
    PERSON_A,
    PERSON_B,
    _person,
    _requested,
    _world,
)
from tests.unit.test_music_notify import GUILD, STARTED_IN, _Channel, _Client
from tests.unit.test_music_player_manager import _TRACK_A, _FakeVoiceSession, _manager

NOW = datetime(2026, 2, 1, tzinfo=UTC)
AUTHORITY = ActAuthority(PASS.unit, "fixture-person", room=PASS.room, pass_context=PASS)
TARGET = ConversationTarget("signal", PASS.room, "group")
TOKEN = str(uuid4())


async def _recorded(engine, executor, *, composed=True, unit=PASS.unit, verdict="done"):
    acts = await _rows(engine, NORA_ACT)
    assert len(acts) == 1
    act, = acts
    outcomes = await _rows(engine, NORA_ACT_OUTCOME)
    assert act["executor"] == executor and act["unit"] == unit
    assert [row["act_id"] for row in outcomes] == [act["id"]] * len(outcomes)
    assert [row["verdict"] for row in outcomes] == (
        ["uncertain", verdict] if executor == "subagent.launch" else [verdict]
    )
    for item in (act, *outcomes):
        assert item["writer"] == (PASS.writer if composed else executor)
        assert item["pass"] == (PASS.pass_id if composed else None)
        assert item["trace"] == (PASS.trace if composed else None)
        assert item["model"] == (PASS.model if composed else None)
        assert item["occasion"] == (PASS.occasion if composed else None)
    registered = {row["id"] for row in await _rows(engine, REGISTRY)}
    assert registered == ({row["graph_id"] for row in (act, *outcomes)} if unit else set())
    return act, outcomes[-1]


class _Scope:
    def room_unit(self, platform, room):
        return PASS.unit if platform == "signal" and room == PASS.room else None

    async def person_id(self, platform, account):
        return "fixture-person" if platform == "signal" and account else None


@pytest.mark.parametrize("executor", ["platform.reply", "platform.otp", "platform.reaction"])
async def test_guarded_delivery_executors(acts, doctrine, executor):
    envelope, engine = acts
    store = SimpleNamespace(
        delivery_acts=DeliveryActs(envelope, _Scope(), dict),
        append=AsyncMock(return_value=1), mark_delivery=AsyncMock(), append_reaction=AsyncMock(),
    )
    sink = SinkSender()
    sink.react = AsyncMock(return_value=ReactionResult(ok=True, supported=True))
    otp = executor == "platform.otp"
    d = Deliverer(
        store=store, adapter=sink, target=TARGET, doctrine=doctrine, canary="fixture-canary",
        tag_id="otp-fixture-challenge" if otp else PASS.pass_id, nora_sender_id="fixture-nora",
        message_cap=1, audience=ONE_TIME_CODE if otp else "tag", metrics=Metrics(),
    )
    with nullcontext() if otp else act_pass(AUTHORITY):
        if executor == "platform.reaction":
            target_row = TranscriptRow(1, "fixture-sender", None, 12, "hello", False,
                                       DeliveryStatus.SENT, None)
            assert await d.react(
                emoji=ACK, row=target_row, quote=OutboundQuote(12, "fixture-sender"),
            )
            sink.react.assert_awaited_once()
        else:
            assert await d.deliver("Generated delivery.")
            assert sink.texts == ["Generated delivery."]
    act, _ = await _recorded(engine, executor, composed=not otp)
    assert act["trigger_id"] == ("fixture-challenge" if otp else PASS.pass_id)


async def test_progress_reaction_executor(acts):
    envelope, engine = acts
    wire = AsyncMock(return_value=ReactionResult(ok=True, supported=True))
    jobs = []
    message = NormalizedMessage(
        platform="signal", conversation_id=PASS.room, conversation_type="group",
        sender_id="fixture-sender", sender_name=None, timestamp=12, body="Generated input", raw={},
    )
    indicator = begin(
        adapter=SimpleNamespace(react=wire), message=message, tag_id="fixture-tag",
        metrics=Metrics(), spawn=lambda job: jobs.append(asyncio.create_task(job)),
        delivery_acts=DeliveryActs(envelope, _Scope(), dict),
    )
    await asyncio.gather(*jobs)
    assert indicator is not None and indicator.placed.is_set()
    wire.assert_awaited_once()
    assert wire.await_args.kwargs["emoji"] == ACK
    await _recorded(engine, "platform.progress", composed=False)


async def test_private_signal_link_challenge_executor(acts):
    envelope, engine = acts
    transport = SinkSender()
    delivery = SignalLinkDelivery("http://fixture", sender=transport, envelope=envelope)
    world = _world(signal_delivery=delivery)
    await _person(world.store, world.registry, PERSON_A, ACCT_I)
    await _person(world.store, world.registry, PERSON_B, ACCT_R)
    view = await _requested(world.holder)
    link = await world.links.get(view.request_id)
    await world.holder._deliver_signal_challenges(link)
    (target, body), = transport.sent
    assert target.conversation_id == ACCT_R.value and target.conversation_type == "dm"
    assert view.request_id in body
    act, outcome = await _recorded(engine, "signal.private_link", composed=False, unit=None)
    assert act["trigger_id"] == f"{view.request_id}:receiving" and act["ordinal"] == 0
    assert act["authority_person"] == PERSON_A
    assert (act["graph_id"], outcome["graph_id"]) == (None, None)
    assert "!!!link" not in repr(act) + repr(outcome)


@pytest.mark.parametrize("executor", ["discord.command_reply", "discord.music_notice"])
async def test_discord_scoped_deterministic_executors(acts, executor):
    envelope, engine = acts
    resolve_person = AsyncMock(return_value="canonical-person")
    origin = f"{GUILD}/{STARTED_IN}"
    command = DiscordCommandActs(envelope, lambda r: PASS.unit if r == origin else None,
                                 resolve_person)
    if executor == "discord.command_reply":
        wire = AsyncMock()
        interaction = SimpleNamespace(
            id=20, guild_id=GUILD, channel_id=STARTED_IN, user=SimpleNamespace(id=42), extras={},
            response=SimpleNamespace(send_message=wire),
        )
        await command.respond(interaction, "reply", "Command response", ephemeral=True)
        wire.assert_awaited_once_with("Command response", ephemeral=True)
    else:
        room = _Channel(STARTED_IN)
        notify = make_notify(client=lambda: _Client(room), guild_id=GUILD,
                             fallback_channel_id=STARTED_IN, command_acts=command)
        with notice_event("fixture-queue-item"):
            await notify("Generated music notice")
        assert room.sent == [("Generated music notice", True)]
    act, _ = await _recorded(engine, executor, composed=False)
    if executor == "discord.command_reply":
        assert act["authority_person"] == "canonical-person"


async def test_voice_death_without_generation_sends_one_teardown_notice(acts):
    envelope, engine = acts
    origin = f"{GUILD}/{STARTED_IN}"
    commands = DiscordCommandActs(envelope, lambda room: PASS.unit if room == origin else None)
    channel = _Channel(STARTED_IN)
    notice = make_notify(client=lambda: _Client(channel), guild_id=GUILD,
                         fallback_channel_id=STARTED_IN, command_acts=commands)
    manager = _manager({"fixture track": _TRACK_A})
    started = await manager.play(
        guild_id=GUILD, query_or_url="fixture track", requested_by="fixture-person",
        added_at=NOW, connect=AsyncMock(side_effect=_FakeVoiceSession),
        voice_channel_id="fixture-voice", notify_channel_id=STARTED_IN, notify=notice,
    )
    assert started is not None and started.status == "playing"

    await manager.on_voice_death(GUILD)
    await manager.on_voice_death(GUILD)

    assert channel.sent == [(
        "Music stopped — voice connection lost. Use /music play to restart.", True,
    )]
    act, _ = await _recorded(engine, "discord.music_notice", composed=False)
    assert act["trigger_id"]


@pytest.mark.parametrize("ops", [("followup", "followup"), ("defer", "edit")])
async def test_discord_interaction_records_each_response_in_order(acts, ops):
    envelope, engine = acts
    calls = []

    async def send(text):
        calls.append(text)

    async def defer():
        calls.append("deferred")

    commands = DiscordCommandActs(envelope)
    interaction = SimpleNamespace(
        id=20, extras={}, followup=SimpleNamespace(send=send),
        response=SimpleNamespace(defer=defer, edit_message=send),
    )
    labels = ("deferred" if ops[0] == "defer" else "First", "Second")
    for op, label in zip(ops, labels, strict=True):
        await commands.respond(interaction, op, *(() if op == "defer" else (label,)))

    assert calls == list(labels)
    rows = await _rows(engine, NORA_ACT)
    assert {row["trigger_id"] for row in rows} == {str(interaction.id)}
    assert sorted(row["ordinal"] for row in rows) == [0, 1]
    outcomes = await _rows(engine, NORA_ACT_OUTCOME)
    assert {row["act_id"] for row in outcomes if row["verdict"] == "done"} == {
        row["id"] for row in rows
    }


@pytest.mark.parametrize("op", ["send", "react", "edit", "typing"])
async def test_durable_bridge_action_executors(acts, op):
    envelope, engine = acts
    effect = AsyncMock(return_value=ActionReply("confirmed"))
    service = DurableActionService(
        store=SqlBridgeStore(engine), platform="discord", account_key="fixture", execute=effect,
        receipt_intents=frozenset, effects=EffectRecorder(envelope), room_unit=lambda _: PASS.unit,
    )
    result = await service.run_owned(
        action_id="fixture-bridge-action", owner=ActionOwner(kind="event_receipt", id="fixture"),
        op=op, request={"target": {"id": PASS.room}},
    )
    assert result == "confirmed"
    effect.assert_awaited_once()
    name = "bridge.typing" if op == "typing" else "bridge.delivery"
    await _recorded(engine, name, composed=False)


@pytest.mark.parametrize("executor", [
    "bridge.music", "bridge.speech", "voice.control", "voice.speech",
])
async def test_controller_executors(acts, executor, monkeypatch):
    envelope, engine = acts
    effects = EffectRecorder(envelope)
    bridge = BridgeControlClient(BridgeEndpoint("http://fixture", "fixture"), effects=effects)
    voice = VoiceControllerClient("http://fixture", effects=effects)
    with act_pass(AUTHORITY):
        if executor == "bridge.music":
            wire = AsyncMock(return_value={"status": "ok"})
            monkeypatch.setattr(bridge, "_music", wire)
            assert await bridge.music("skip", {"conversation_id": PASS.room}) == {"status": "ok"}
        elif executor == "bridge.speech":
            wire = AsyncMock(return_value=True)
            monkeypatch.setattr(bridge, "_speech", wire)
            assert await bridge.speech("fixture-guild", b"speech", "audio/wav") is True
        else:
            wire = AsyncMock(return_value={"generation": "fixture-generation"})
            monkeypatch.setattr(voice, "_operation", wire)
            if executor == "voice.control":
                result = await voice.join("guild", "voice-room", operation_id="fixture-join")
            else:
                result = await voice.stream("fixture-generation", b"speech", media_type="audio/wav",
                                            guild_id="guild", operation_id="fixture-stream")
            assert result["generation"] == "fixture-generation"
    wire.assert_awaited_once()
    await _recorded(engine, executor)


async def test_music_queue_executor(acts):
    envelope, engine = acts
    queue = SqlMusicQueueStore(engine, effects=EffectRecorder(envelope))
    with act_event("fixture-queue-event", ActAuthority(PASS.unit, room=PASS.room)):
        track = await queue.enqueue(guild_id="fixture-guild", source="youtube",
                                    url="https://example.test/track", title="Generated track",
                                    added_by_person_id="fixture-person", added_at=NOW)
    assert track.title == "Generated track"
    await _recorded(engine, "music.queue", composed=False)


async def test_document_write_executor(acts):
    from tests.unit.test_documents_service import _doc

    envelope, engine = acts
    service = DocumentService(
        store=SqlDocumentStore(engine), records=object(), media=object(), media_window=4,
        room_ids=lambda _platform, room: frozenset({room}), is_person=lambda _: True,
        writes=EffectRecorder(envelope), room_unit=lambda *_: PASS.unit,
    )
    with act_pass(AUTHORITY):
        result = await service.publish(
            platform="signal", conversation_id=PASS.room, person_id="fixture-person",
            written_by=PASS.pass_id, raw=_doc({"type": "Markdown", "props": {"text": "Generated"}}),
        )
    assert result.status == "ok"
    await _recorded(engine, "document.write")


@pytest.mark.parametrize("executor", ["computer.run", "computer.workspace", "computer.lifecycle",
                                      "browser.connect", "browser.mutation", "browser.stop"])
async def test_computer_and_browser_executors(acts, executor, monkeypatch):
    envelope, engine = acts
    effects = EffectRecorder(envelope)
    with act_pass(AUTHORITY):
        if executor == "computer.lifecycle":
            client = HostLifecycleClient(socket="fixture.sock", token=TOKEN, effects=effects)
            wire = AsyncMock(return_value={"status": "ok"})
            monkeypatch.setattr(client, "_request", wire)
            assert (await client.request("acquire", "fixture-scope", "fixture-op")) == {
                "status": "ok",
            }
        elif executor.startswith("computer."):
            client = ComputerdRuntime(base_url="http://fixture", token=TOKEN, effects=effects)
            wire = AsyncMock(return_value={"status": "ok", "exit_code": 0})
            monkeypatch.setattr(client, "_post_json", wire)
            if executor == "computer.run":
                out = await client.run(scope="fixture", command="fixture command", timeout_s=1)
            else:
                out = await client.edit(scope="fixture", path="fixture.txt", old="a", new="b")
            assert out.status.value == "ok"
        elif executor == "browser.stop":
            client = ScopeBrowserManager(runtime=object(), tabs=object(), token=TOKEN,
                                         clock=lambda: NOW, idle_after=timedelta(seconds=10),
                                         effects=effects)
            wire = AsyncMock(return_value=True)
            monkeypatch.setattr(client, "_stop_guest", wire)
            assert await client.browser_stop("fixture")
        elif executor == "browser.mutation":
            stopped = asyncio.Event()
            socket = SimpleNamespace(close=AsyncMock(), receive=stopped.wait)
            client = CdpConnection(socket, effects=effects, scope="fixture")
            wire = AsyncMock(return_value={"frameId": "frame"})
            monkeypatch.setattr(client, "_request", wire)
            try:
                assert await client.call("Page.navigate", {"url": "https://fixture.test"})
            finally:
                await client.close()
        else:
            socket = SimpleNamespace(close=AsyncMock(), receive=asyncio.Event().wait)
            wire = AsyncMock(return_value=socket)
            session = SimpleNamespace(ws_connect=wire)
            session_context = AsyncMock()
            session_context.__aenter__.return_value = session
            monkeypatch.setattr("nora.infra.computer.browser.aiohttp.ClientSession",
                                lambda **_: session_context)
            async with connect_browser_cdp(base_url="http://fixture", token=TOKEN,
                                           scope="fixture", effects=effects) as cdp:
                assert isinstance(cdp, CdpConnection)
    wire.assert_awaited_once()
    await _recorded(engine, executor)


async def test_web_browse_dispatch_records_a_browser_mutation(acts, monkeypatch):
    envelope, engine = acts
    stopped = asyncio.Event()
    socket = SimpleNamespace(close=AsyncMock(), receive=stopped.wait)
    client = CdpConnection(socket, effects=EffectRecorder(envelope), scope="fixture")
    calls: list[str] = []

    async def wire(method, params=None, *, session_id=None, wait_s=0):
        calls.append(method)
        if method == "Page.getFrameTree":
            moved = "Runtime.callFunctionOn" in calls
            return {"frameTree": {"frame": {
                "id": "fixture-frame", "loaderId": "L2" if moved else "L1",
            }}}
        if method == "Page.createIsolatedWorld":
            return {"executionContextId": 7}
        if method == "DOM.resolveNode":
            return {"object": {"objectId": "fixture-object"}}
        if method == "Runtime.callFunctionOn":
            return {"result": {"value": True}}
        if method == "Runtime.evaluate":
            return {"result": {"value": "complete"}}
        raise AssertionError(f"unexpected CDP call {method}")

    monkeypatch.setattr(client, "_request", wire)
    page = BrowserPage(cdp=client, session_id="fixture-session")
    page.refs["e1"] = 1
    try:
        with act_pass(AUTHORITY):
            assert await dispatch(
                page, RetrievalAction("e1", "Click fixture", ["button", "fixture", "", []]),
            )
        assert "Runtime.callFunctionOn" in calls
        await _recorded(engine, "browser.mutation")
    finally:
        await client.close()


async def test_subagent_launch_executor(acts):
    envelope, engine = acts
    release = asyncio.Event()

    async def run(**_):
        await release.wait()
        return SubagentOutcome(report="Generated worker result.")

    backend = SubagentBackend(runner=SimpleNamespace(run=run), metrics=Metrics(),
                              clock=lambda: NOW, requester_name=str, acts=envelope)
    with act_pass(AUTHORITY):
        job = await backend.launch(request=LaunchRequest(
            "Generated task", "signal", PASS.room, "group", "fixture-person",
        ))
    release.set()
    await backend._jobs[job].task
    act, outcome = await _recorded(engine, "subagent.launch")
    assert act["destination"] == f"subagent:{job}"
    assert outcome["result"]["report"] == "Generated worker result."
