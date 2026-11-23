"""The pinned audio-controller endpoint semantics — driven through the REAL
`VoiceControllerClient` over real HTTP against `nora/testing/voice.py`'s
`FakeVoiceController`, the in-suite controller that implements the state
machine (single guild, one serialized connection, generation-scoped verbs,
replace-not-enqueue play) AND its wrong-call shapes (missing `guild_id`, wrong
channel, non-object body — the validations
`deploy/voice-sidecar/src/controller.mjs` enforces).

This suite is what the Node-side controller must agree with: if the real
controller and this fake ever disagree, one of them is off-contract.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from aiohttp import web

from nora.domain.service_api import OperationConflictError
from nora.infra.voice.http_controller import (
    GenerationConflict,
    HttpVoiceSession,
    VoiceControllerClient,
    VoiceControllerNoConnection,
    VoiceControllerRejected,
    VoiceControllerUnavailable,
)
from nora.testing.voice import FakeVoiceController

GUILD = "100000000000000101"
CHANNEL = "cortez-1"
OPERATIONS = "/v1/voice/operations"


@pytest.fixture
async def controller(aiohttp_client):
    fake = FakeVoiceController(GUILD, CHANNEL)
    test_client = await aiohttp_client(fake.app())
    client = VoiceControllerClient(str(test_client.make_url("")).rstrip("/"))
    return fake, client, test_client


async def _join(client: VoiceControllerClient) -> str:
    """A FRESH `operation_id` every call: a repeated one is answered from the
    controller's ledger, so a second join under it would never reach a handler."""
    state = await client.join(GUILD, CHANNEL, operation_id=str(uuid4()))
    return state["generation"]


async def test_a_second_play_replaces_the_first(controller):
    """REPLACE semantics: the second play supersedes the first — a
    fresh playback_id makes the old one stale for any later callback."""
    fake, client, _ = controller
    generation = await _join(client)

    first = await client.play(generation, "https://cdn.example/a",
        guild_id=GUILD, operation_id="r1")
    second = await client.play(generation, "https://cdn.example/b",
        guild_id=GUILD, operation_id="r2")

    assert first["playback_id"] != second["playback_id"]
    assert [p["stream_url"] for p in fake.plays] == [
        "https://cdn.example/a", "https://cdn.example/b",
    ]
    assert fake.playback_id == second["playback_id"]


async def test_play_accepts_the_optional_resumable_flag(controller):
    """Position policy: `resumable` is an optional boolean the
    controller reads when present (a live source gets NO resume after
    speech preemption)."""
    fake, client, _ = controller
    generation = await _join(client)

    await client.play(
        generation, "https://cdn.example/live",
        resumable=False, guild_id=GUILD, operation_id="r1",
    )
    await client.play(generation, "https://cdn.example/vod", guild_id=GUILD, operation_id="r2")

    assert fake.plays[0]["resumable"] is False
    assert "resumable" not in fake.plays[1], (
        "omitted means the controller's default — never an explicit value "
        "the controller would have to second-guess"
    )


async def test_join_is_idempotent_in_the_same_channel(controller):
    fake, client, _ = controller
    first = await _join(client)

    second = await _join(client)

    assert first == second
    assert fake.voice == "connected"


async def test_join_elsewhere_is_a_conflict(controller):
    _fake, client, _ = controller
    await _join(client)

    with pytest.raises(GenerationConflict) as excinfo:
        await client.join(GUILD, "cortez-elsewhere", operation_id="r")
    assert excinfo.value.reason == "connected_elsewhere", (
        "§12.2 reversed 2026-09-17: a second channel is refused because "
        "Discord allows ONE voice connection per bot per guild (409), NOT "
        "because it is off an allow-list (the old 400 not_in_allowed_channel)"
    )


async def test_join_while_idle_admits_any_channel_in_the_guild(controller):
    """`hub.join` is called with the CALLER'S channel, which can be any of the
    guild's; the fake must accept every one, not only a configured channel, or
    this whole path would be untestable."""
    fake, client, _ = controller

    state = await client.join(GUILD, "a-channel-nobody-configured", operation_id="r")

    assert state["channel_id"] == "a-channel-nobody-configured"
    assert fake.voice == "connected"


async def test_a_join_without_a_guild_id_is_rejected(controller):
    """The real controller compares
    `body.guild_id !== guildId`, so a join MISSING `guild_id` is a 400
    `wrong_guild` — the fake must refuse it the same way, or nora-side
    callers could learn a body shape the real controller rejects."""
    _fake, _client, test_client = controller

    resp = await test_client.post(OPERATIONS, json={
        "operation_id": "r", "kind": "join", "effect": {"channel_id": CHANNEL}})

    assert resp.status == 400
    assert (await resp.json())["error"] == "wrong_guild"


async def test_a_non_object_body_is_rejected(controller):
    """`null` (and arrays/scalars) parse fine as JSON but are not command
    bodies — the real controller 400s them BEFORE any field access."""
    _fake, client, test_client = controller
    generation = await _join(client)

    for body in ("null", "[1, 2]", "42"):
        resp = await test_client.post(
            OPERATIONS, data=body, headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400, body
        assert (await resp.json())["error"] in ("invalid_body", "invalid_json")
    # The rejected bodies never reached the handler: the state is untouched.
    assert _fake.voice == "connected"
    assert _fake.generation == generation


async def test_music_stop_keeps_the_connection(controller):
    """`stop` ≠ leaving: after a stop, a play with the SAME
    generation still succeeds."""
    fake, client, _ = controller
    generation = await _join(client)
    await client.play(generation, "https://cdn.example/a", guild_id=GUILD, operation_id="r1")

    state = await client.stop_music(generation, guild_id=GUILD, operation_id="r2")

    assert state["voice"] == "connected"
    assert fake.voice == "connected"
    again = await client.play(generation, "https://cdn.example/b",
        guild_id=GUILD, operation_id="r3")
    assert again["playback_id"] is not None


async def test_music_stop_when_idle_is_a_noop_200(controller):
    _fake, client, _ = controller
    generation = await _join(client)

    state = await client.stop_music(generation, guild_id=GUILD, operation_id="r")

    assert state["voice"] == "connected"
    assert state["player"]["status"] == "idle"


async def test_exit_invalidates_the_generation(controller):
    """Every generation-scoped verb 409s once the connection is gone — and
    a re-join mints a NEW generation, never revives the old one."""
    fake, client, _ = controller
    generation = await _join(client)

    state = await client.exit(generation, guild_id=GUILD, operation_id="r-exit")

    assert state["voice"] == "idle"
    assert fake.generation is None
    with pytest.raises(GenerationConflict):
        await client.play(generation, "https://cdn.example/a", guild_id=GUILD, operation_id="r")
    rejoined = await _join(client)
    assert rejoined != generation


async def test_every_generation_scoped_verb_rejects_a_stale_generation(controller):
    _fake, client, _ = controller
    generation = await _join(client)
    await client.exit(generation, guild_id=GUILD, operation_id="r-exit")

    with pytest.raises(GenerationConflict):
        await client.exit(generation, guild_id=GUILD, operation_id="r1")
    with pytest.raises(GenerationConflict):
        await client.play(generation, "https://cdn.example/a", guild_id=GUILD, operation_id="r2")
    with pytest.raises(GenerationConflict):
        await client.stop_music(generation, guild_id=GUILD, operation_id="r3")
    with pytest.raises(GenerationConflict):
        await client.pause(generation, guild_id=GUILD, operation_id="r4")
    with pytest.raises(GenerationConflict):
        await client.resume(generation, guild_id=GUILD, operation_id="r5")
    with pytest.raises(GenerationConflict):
        await client.set_volume(generation, 100, guild_id=GUILD, operation_id="r6")
    with pytest.raises(GenerationConflict):
        await client.stream(
            generation, b"mp3", media_type="audio/mpeg",
            guild_id=GUILD, operation_id="r7",
        )


async def test_a_wrong_guild_is_rejected(controller):
    """Single-guild scope: any other guild_id is a 400, surfaced as
    `VoiceControllerRejected` carrying the controller's reason."""
    _fake, client, _ = controller

    with pytest.raises(VoiceControllerRejected) as excinfo:
        await client.join("guild-2", CHANNEL, operation_id="r")
    assert excinfo.value.reason == "wrong_guild"


async def test_a_volume_out_of_range_is_rejected(controller):
    _fake, client, _ = controller
    generation = await _join(client)

    with pytest.raises(VoiceControllerRejected) as excinfo:
        await client.set_volume(generation, 201, guild_id=GUILD, operation_id="r")
    assert excinfo.value.reason == "percent_out_of_range"


async def test_a_stream_url_ffmpeg_could_not_take_is_rejected(controller):
    """The controller refuses a URL that is not http(s), or that carries
    whitespace or a control character, before it applies anything."""
    fake, client, _ = controller
    generation = await _join(client)

    for url in ("https://cdn.example/a\x00b", "https://cdn.example/a\nb", "file:///etc/passwd",
                "ftp://cdn.example/a", "not a url", " https://cdn.example/a",
                "https://cdn.example/a ", "https://cdn.example/a\u00a0b"):
        with pytest.raises(VoiceControllerRejected) as excinfo:
            await client.play(generation, url, guild_id=GUILD, operation_id=str(uuid4()))
        assert excinfo.value.reason == "invalid_body", url
    assert fake.playback_id is None, "nothing was applied"


async def test_resume_during_speech_is_a_409(controller):
    """Speech owns the pipeline; a music resume (like a pause)
    while it does is undefined and rejected LOUDLY — surfaced to nora as
    `GenerationConflict` carrying `pause_during_speech`, never applied."""
    fake, client, _ = controller
    generation = await _join(client)
    await client.play(generation, "https://cdn.example/a", guild_id=GUILD, operation_id="r1")
    await client.stream(
        generation, b"speech-bytes", media_type="audio/mpeg", guild_id=GUILD, operation_id="r2",
    )

    with pytest.raises(GenerationConflict) as excinfo:
        await client.resume(generation, guild_id=GUILD, operation_id="r3")
    assert excinfo.value.reason == "pause_during_speech"
    with pytest.raises(GenerationConflict) as excinfo:
        await client.pause(generation, guild_id=GUILD, operation_id="r4")
    assert excinfo.value.reason == "pause_during_speech"

    # Once speech drains, the verbs work again.
    fake.finish_speech()
    await client.pause(generation, guild_id=GUILD, operation_id="r5")
    await client.resume(generation, guild_id=GUILD, operation_id="r6")


async def test_status_carries_the_reconciliation_fields(controller):
    """The snapshot nora's hub reconciles from: `generation`,
    `playback_id`, `last_event_seq` (the outbox bookmark the gap guard
    compares against), `state_write_ok`, and the player's
    `position_policy` (approximate-seek posture: a revive seeks to the cut)."""
    _fake, client, _ = controller
    generation = await _join(client)
    await client.play(generation, "https://cdn.example/a", guild_id=GUILD, operation_id="r1")

    status = await client.status(GUILD)

    assert status["generation"] == generation
    assert status["playback_id"] is not None
    assert isinstance(status["last_event_seq"], int)
    assert status["state_write_ok"] is True
    assert status["player"]["position_policy"] == "approximate_seek"


async def test_stream_speech_encodes_the_audio(controller):
    fake, client, _ = controller
    generation = await _join(client)

    await client.stream(
        generation, b"mp3-bytes", media_type="audio/mpeg", guild_id=GUILD, operation_id="r",
    )

    assert fake.speech_active is True


async def test_stream_rejects_with_unavailable_once_the_speech_queue_is_full(aiohttp_client):
    """The bounded speech queue's 503 `speech_queue_full` must be reachable
    through the fake, and the client must surface it as
    `VoiceControllerUnavailable` — not just be documented in
    `VoiceControllerClient.stream()`'s docstring while dead in the fake. A small
    `speech_queue_max` keeps the test fast."""
    speech_queue_max = 2
    fake = FakeVoiceController(GUILD, CHANNEL, speech_queue_max=speech_queue_max)
    test_client = await aiohttp_client(fake.app())
    client = VoiceControllerClient(str(test_client.make_url("")).rstrip("/"))
    generation = await _join(client)

    # First call becomes the active stream.
    await client.stream(
        generation, b"active", media_type="audio/mpeg",
        guild_id=GUILD, operation_id="r0",
    )
    # The next `speech_queue_max` calls queue behind it.
    for i in range(speech_queue_max):
        await client.stream(
            generation, b"queued", media_type="audio/mpeg",
                guild_id=GUILD, operation_id=f"r{i + 1}",
        )
    assert fake.speech_queue == speech_queue_max

    # The queue is now full: one more is rejected 503, surfaced as
    # `VoiceControllerUnavailable`.
    with pytest.raises(VoiceControllerUnavailable):
        await client.stream(
            generation, b"overflow", media_type="audio/mpeg",
            guild_id=GUILD, operation_id="r-overflow",
        )

    # Once the active speech (and its queue) drains, streaming works again.
    fake.finish_speech()
    await client.stream(
        generation, b"after-drain", media_type="audio/mpeg",
        guild_id=GUILD, operation_id="r-after",
    )


async def test_no_connection_is_raised_for_an_unjoined_guild(controller):
    """A guild the controller holds no connection for is `nothing_found` on the
    status query and `rejected` `unknown_guild` on an operation: both are
    `VoiceControllerNoConnection`, the ordinary cold-boot answer, never a
    generation conflict to reconcile."""
    _fake, client, _ = controller

    with pytest.raises(VoiceControllerNoConnection):
        await client.status(GUILD)
    with pytest.raises(VoiceControllerNoConnection):
        await client.play("gen-0", "https://cdn.example/a", guild_id=GUILD,
                          operation_id=str(uuid4()))
    generation = await _join(client)
    assert (await client.status(GUILD))["generation"] == generation


async def test_a_redirect_is_unavailable(controller, aiohttp_client):
    """A redirect is never followed: the operation reads `unavailable` and the
    controller it points at never sees it."""
    fake, _client, test_client = controller
    target = str(test_client.make_url(OPERATIONS))
    hits = []

    async def redirect(request: web.Request) -> web.Response:
        hits.append(request.path)
        raise web.HTTPTemporaryRedirect(location=target)

    app = web.Application()
    app.router.add_post(OPERATIONS, redirect)
    redirector = await aiohttp_client(app)
    client = VoiceControllerClient(str(redirector.make_url("")).rstrip("/"))

    with pytest.raises(VoiceControllerUnavailable):
        await client.join(GUILD, CHANNEL, operation_id=str(uuid4()))
    assert hits == [OPERATIONS]
    assert fake.applied == 0 and fake.voice == "idle"


async def test_a_conflict_is_never_retried(aiohttp_client):
    """A 409 is an `operation_id` reused for another effect: the client raises
    the kernel's `OperationConflictError`, and the session fails the call as
    unavailable without reconciling and sending again, as it would for a
    `generation_conflict`."""
    operations = []

    async def conflict(request: web.Request) -> web.Response:
        operations.append(await request.json())
        return web.json_response({"error": "operation_id_reused"}, status=409)

    async def status(request: web.Request) -> web.Response:
        return web.json_response({
            "outcome": "ok", "payload": {"voice": "connected", "generation": "gen-2",
                                         "channel_id": CHANNEL},
            "when": {"observed_at_ms": 1, "known_by": "controller_clock"},
            "from": {"source": "voice-controller", "healthy": True, "failed_sources": []},
            "scope": {"level": "installation"}, "trace": ""})

    app = web.Application()
    app.router.add_post(OPERATIONS, conflict)
    app.router.add_post("/v1/voice/queries", status)
    server = await aiohttp_client(app)
    client = VoiceControllerClient(str(server.make_url("")).rstrip("/"))
    session = HttpVoiceSession(client, guild_id=GUILD, generation="gen-1")

    with pytest.raises(OperationConflictError):
        await client.pause("gen-1", guild_id=GUILD, operation_id="op-reused")
    with pytest.raises(VoiceControllerUnavailable) as failed:
        await session.pause()
    assert isinstance(failed.value.__cause__, OperationConflictError)
    assert [op["kind"] for op in operations] == ["pause", "pause"]
    assert session.generation == "gen-1"


async def test_a_reconciled_retry_is_a_new_operation(controller):
    """The controller keeps every settled answer, a rejection too, so the retry
    after a `generation_conflict` must be a new `operation_id`: the old one
    with the adopted generation would be a reused id with another effect."""
    fake, client, _ = controller
    generation = await _join(client)
    session = HttpVoiceSession(client, guild_id=GUILD, generation=generation)
    fake.restart()

    await session.play("https://cdn.example/a")

    assert session.generation == fake.generation != generation
    assert [p["generation"] for p in fake.plays] == [fake.generation]


OTHER_GUILD = "100000000000000102"
FORBIDDEN_GUILD = "100000000000000103"


async def test_a_malformed_forbidden_and_unjoined_guild_are_told_apart(aiohttp_client):
    """A guild id that is not a snowflake is a 400; a forbidden guild is `denied`
    on an operation and `nothing_found` on the status query; an admitted guild
    with no connection is `rejected` `unknown_guild`. The last two carry no state."""
    fake = FakeVoiceController(GUILD, CHANNEL, forbidden_guilds=frozenset({FORBIDDEN_GUILD}))
    test_client = await aiohttp_client(fake.app())

    async def op(guild_id: object, kind: str = "leave") -> tuple[int, dict]:
        resp = await test_client.post(OPERATIONS, json={
            "operation_id": str(uuid4()), "kind": kind,
            "effect": {"guild_id": guild_id, "generation": "gen-1"}})
        return resp.status, await resp.json()

    async def status(guild_id: object) -> tuple[int, dict]:
        resp = await test_client.post("/v1/voice/queries", json={
            "kind": "status", "params": {"guild_id": guild_id}})
        return resp.status, await resp.json()

    assert (await op("guild-x"))[0] == 400
    assert (await op(None))[0] == 400
    code, body = await op(FORBIDDEN_GUILD)
    assert (code, body["outcome"], body["payload"]["state"]) == (403, "denied", None)
    code, body = await op(OTHER_GUILD)
    assert (code, body["outcome"], body["payload"]["error"]["kind"]) == (
        200, "rejected", "unknown_guild")
    assert body["payload"]["state"] is None
    for guild_id in (FORBIDDEN_GUILD, OTHER_GUILD, "guild-x"):
        code, body = await status(guild_id)
        assert (code, body["outcome"]) == (200, "nothing_found"), guild_id
    for guild_id in (None, "", 7):
        assert (await status(guild_id))[0] == 400, guild_id
    assert fake.applied == 0


async def test_the_front_door_refuses_what_the_controller_refuses(controller):
    """An operation id outside the grammar, a non-boolean `resumable`, an
    over-long channel and a control body over 64 KiB are refused before any
    effect, as is a `speak` body over 4 MiB; a null `resumable` reads as absent."""
    fake, client, test_client = controller
    generation = await _join(client)

    async def post(operation_id: str, kind: str, **effect) -> int:
        resp = await test_client.post(OPERATIONS, json={
            "operation_id": operation_id, "kind": kind, "effect": {"guild_id": GUILD, **effect}})
        return resp.status

    play = {"generation": generation, "stream_url": "https://cdn.example/a"}
    assert await post("no spaces", "play", **play) == 400
    assert await post("x" * 65, "play", **play) == 400
    assert await post(str(uuid4()), "play", **play, resumable="yes") == 400
    assert await post(str(uuid4()), "play", **play, title="t" * 70_000) == 413
    assert await post(str(uuid4()), "speak", generation=generation, media_type="audio/wav",
                      audio_b64="A" * (4 * 1024 * 1024)) == 413
    assert fake.plays == []
    assert await post(str(uuid4()), "play", **play, resumable=None) == 200
    assert len(fake.plays) == 1


async def test_a_duplicate_in_flight_awaits_the_first_answer(controller):
    """The same id and effect while the first is still running is answered with
    the first's envelope, never a second execution."""
    fake, client, test_client = controller
    generation = await _join(client)
    fake.play_gate = asyncio.Event()
    body = {"operation_id": "op-dup", "kind": "play", "effect": {
        "guild_id": GUILD, "generation": generation, "stream_url": "https://cdn.example/a"}}

    first = asyncio.ensure_future(test_client.post(OPERATIONS, json=body))
    await fake.play_waiting.wait()
    second = asyncio.ensure_future(test_client.post(OPERATIONS, json=body))
    read = await (await test_client.get(f"{OPERATIONS}/op-dup")).json()
    assert read["payload"]["outcome"] == "pending"
    fake.play_gate.set()
    answers = [await (await r).json() for r in (first, second)]

    assert answers[0] == answers[1] and answers[0]["outcome"] == "complete"
    assert len(fake.plays) == 1


async def test_the_body_bound_is_checked_before_the_request_is_read(controller):
    """Size comes first, keyed only on `kind`: a non-`speak` body over 64 KiB is
    413 with a null effect, a `speak` over 4 MiB is 413 with a malformed id."""
    _fake, _client, test_client = controller

    control = await test_client.post(OPERATIONS, json={
        "operation_id": "op-1", "kind": "leave", "effect": None, "pad": "x" * 70_000})
    speech = await test_client.post(OPERATIONS, json={
        "operation_id": "no spaces", "kind": "speak", "effect": {},
        "pad": "x" * (4 * 1024 * 1024 + 1)})

    assert (control.status, speech.status) == (413, 413)


async def test_a_join_takes_any_channel_id(controller):
    fake, client, _ = controller

    await client.join(GUILD, "c" * 65, operation_id=str(uuid4()))

    assert fake.channel_id == "c" * 65


async def test_a_reservation_in_flight_never_expires(aiohttp_client):
    """Past five minutes, an operation still running reads back `pending` and
    a replay waits for it instead of applying a second effect."""
    now = [0.0]
    fake = FakeVoiceController(GUILD, CHANNEL, clock=lambda: now[0])
    test_client = await aiohttp_client(fake.app())
    client = VoiceControllerClient(str(test_client.make_url("")).rstrip("/"))
    generation = await _join(client)
    fake.play_gate = asyncio.Event()
    body = {"operation_id": "op-slow", "kind": "play", "effect": {
        "guild_id": GUILD, "generation": generation, "stream_url": "https://cdn.example/a"}}
    first = asyncio.ensure_future(test_client.post(OPERATIONS, json=body))
    await fake.play_waiting.wait()

    now[0] = 301.0
    read = await (await test_client.get(f"{OPERATIONS}/op-slow")).json()
    replay = asyncio.ensure_future(test_client.post(OPERATIONS, json=body))
    await asyncio.sleep(0)
    fake.play_gate.set()

    assert read["payload"]["outcome"] == "pending"
    assert (await (await first).json()) == (await (await replay).json())
    assert len(fake.plays) == 1
