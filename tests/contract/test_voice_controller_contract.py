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

from uuid import uuid4

import pytest

from nora.infra.voice.http_controller import (
    GenerationConflict,
    VoiceControllerClient,
    VoiceControllerRejected,
    VoiceControllerUnavailable,
)
from nora.testing.voice import FakeVoiceController

GUILD = "guild-1"
CHANNEL = "cortez-1"


@pytest.fixture
async def controller(aiohttp_client):
    fake = FakeVoiceController(GUILD, CHANNEL)
    test_client = await aiohttp_client(fake.app())
    client = VoiceControllerClient(str(test_client.make_url("")).rstrip("/"))
    return fake, client, test_client


async def _join(client: VoiceControllerClient) -> str:
    """A FRESH `request_id` every call, never a fixed constant.
    `FakeVoiceController` deliberately does not emulate the real controller's
    5-minute `request_id` retry memo (its own docstring says so) — reusing one
    request_id across two `_join()` calls would pass here (a second call for the
    same id runs as an ordinary fresh join against the fake) while it would fail
    against `controller.mjs`, which returns the FIRST call's memoized response
    (the SAME generation) for a repeated id, not a new join. Any test that joins
    more than once — e.g. `test_exit_invalidates_the_generation`'s rejoin —
    needs the second call to actually reach the handler."""
    state = await client.join(GUILD, CHANNEL, request_id=str(uuid4()))
    return state["generation"]


async def test_a_second_play_replaces_the_first(controller):
    """REPLACE semantics: the second play supersedes the first — a
    fresh playback_id makes the old one stale for any later callback."""
    fake, client, _ = controller
    generation = await _join(client)

    first = await client.play(generation, "https://cdn.example/a", guild_id=GUILD, request_id="r1")
    second = await client.play(generation, "https://cdn.example/b", guild_id=GUILD, request_id="r2")

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
        resumable=False, guild_id=GUILD, request_id="r1",
    )
    await client.play(generation, "https://cdn.example/vod", guild_id=GUILD, request_id="r2")

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
        await client.join(GUILD, "cortez-elsewhere", request_id="r")
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

    state = await client.join(GUILD, "a-channel-nobody-configured", request_id="r")

    assert state["channel_id"] == "a-channel-nobody-configured"
    assert fake.voice == "connected"


async def test_a_join_without_a_guild_id_is_rejected(controller):
    """The real controller compares
    `body.guild_id !== guildId`, so a join MISSING `guild_id` is a 400
    `wrong_guild` — the fake must refuse it the same way, or nora-side
    callers could learn a body shape the real controller rejects."""
    _fake, _client, test_client = controller

    resp = await test_client.post("/voice/join", json={"channel_id": CHANNEL})

    assert resp.status == 400
    assert (await resp.json())["error"] == "wrong_guild"


async def test_a_non_object_body_is_rejected(controller):
    """`null` (and arrays/scalars) parse fine as JSON but are not command
    bodies — the real controller 400s them BEFORE any field access."""
    _fake, client, test_client = controller
    generation = await _join(client)

    for body in ("null", "[1, 2]", "42"):
        resp = await test_client.post(
            "/music/stop", data=body, headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400, body
        assert (await resp.json())["error"] in ("invalid_body", "invalid_json")
    # The rejected bodies never reached the handler: the state is untouched.
    assert _fake.voice == "connected"
    assert _fake.generation == generation


async def test_music_stop_keeps_the_connection(controller):
    """`/music/stop` ≠ leaving: after a stop, a play with the SAME
    generation still succeeds."""
    fake, client, _ = controller
    generation = await _join(client)
    await client.play(generation, "https://cdn.example/a", guild_id=GUILD, request_id="r1")

    state = await client.stop_music(generation, guild_id=GUILD, request_id="r2")

    assert state["voice"] == "connected"
    assert fake.voice == "connected"
    again = await client.play(generation, "https://cdn.example/b", guild_id=GUILD, request_id="r3")
    assert again["playback_id"] is not None


async def test_music_stop_when_idle_is_a_noop_200(controller):
    _fake, client, _ = controller
    generation = await _join(client)

    state = await client.stop_music(generation, guild_id=GUILD, request_id="r")

    assert state["voice"] == "connected"
    assert state["player"]["status"] == "idle"


async def test_exit_invalidates_the_generation(controller):
    """Every generation-scoped verb 409s once the connection is gone — and
    a re-join mints a NEW generation, never revives the old one."""
    fake, client, _ = controller
    generation = await _join(client)

    state = await client.exit(generation, guild_id=GUILD, request_id="r-exit")

    assert state["voice"] == "idle"
    assert fake.generation is None
    with pytest.raises(GenerationConflict):
        await client.play(generation, "https://cdn.example/a", guild_id=GUILD, request_id="r")
    rejoined = await _join(client)
    assert rejoined != generation


async def test_every_generation_scoped_verb_rejects_a_stale_generation(controller):
    _fake, client, _ = controller
    generation = await _join(client)
    await client.exit(generation, guild_id=GUILD, request_id="r-exit")

    with pytest.raises(GenerationConflict):
        await client.exit(generation, guild_id=GUILD, request_id="r")
    with pytest.raises(GenerationConflict):
        await client.play(generation, "https://cdn.example/a", guild_id=GUILD, request_id="r")
    with pytest.raises(GenerationConflict):
        await client.stop_music(generation, guild_id=GUILD, request_id="r")
    with pytest.raises(GenerationConflict):
        await client.pause(generation, guild_id=GUILD, request_id="r")
    with pytest.raises(GenerationConflict):
        await client.resume(generation, guild_id=GUILD, request_id="r")
    with pytest.raises(GenerationConflict):
        await client.set_volume(generation, 100, guild_id=GUILD, request_id="r")
    with pytest.raises(GenerationConflict):
        await client.stream(
            generation, b"mp3", media_type="audio/mpeg",
            guild_id=GUILD, request_id="r",
        )


async def test_a_wrong_guild_is_rejected(controller):
    """Single-guild scope: any other guild_id is a 400, surfaced as
    `VoiceControllerRejected` carrying the controller's reason."""
    _fake, client, _ = controller

    with pytest.raises(VoiceControllerRejected) as excinfo:
        await client.join("guild-2", CHANNEL, request_id="r")
    assert excinfo.value.reason == "wrong_guild"


async def test_a_volume_out_of_range_is_rejected(controller):
    _fake, client, _ = controller
    generation = await _join(client)

    with pytest.raises(VoiceControllerRejected) as excinfo:
        await client.set_volume(generation, 201, guild_id=GUILD, request_id="r")
    assert excinfo.value.reason == "percent_out_of_range"


async def test_resume_during_speech_is_a_409(controller):
    """Speech owns the pipeline; a music resume (like a pause)
    while it does is undefined and rejected LOUDLY — surfaced to nora as
    `GenerationConflict` carrying `pause_during_speech`, never applied."""
    fake, client, _ = controller
    generation = await _join(client)
    await client.play(generation, "https://cdn.example/a", guild_id=GUILD, request_id="r1")
    await client.stream(
        generation, b"speech-bytes", media_type="audio/mpeg", guild_id=GUILD, request_id="r2",
    )

    with pytest.raises(GenerationConflict) as excinfo:
        await client.resume(generation, guild_id=GUILD, request_id="r3")
    assert excinfo.value.reason == "pause_during_speech"
    with pytest.raises(GenerationConflict) as excinfo:
        await client.pause(generation, guild_id=GUILD, request_id="r4")
    assert excinfo.value.reason == "pause_during_speech"

    # Once speech drains, the verbs work again.
    fake.finish_speech()
    await client.pause(generation, guild_id=GUILD, request_id="r5")
    await client.resume(generation, guild_id=GUILD, request_id="r6")


async def test_status_carries_the_reconciliation_fields(controller):
    """The snapshot nora's hub reconciles from: `generation`,
    `playback_id`, `last_event_seq` (the outbox bookmark the gap guard
    compares against), `state_write_ok`, and the player's
    `position_policy` (approximate-seek posture: a revive seeks to the cut)."""
    _fake, client, _ = controller
    generation = await _join(client)
    await client.play(generation, "https://cdn.example/a", guild_id=GUILD, request_id="r1")

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
        generation, b"mp3-bytes", media_type="audio/mpeg", guild_id=GUILD, request_id="r",
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
        guild_id=GUILD, request_id="r0",
    )
    # The next `speech_queue_max` calls queue behind it.
    for i in range(speech_queue_max):
        await client.stream(
            generation, b"queued", media_type="audio/mpeg", guild_id=GUILD, request_id=f"r{i + 1}",
        )
    assert fake.speech_queue == speech_queue_max

    # The queue is now full: one more is rejected 503, surfaced as
    # `VoiceControllerUnavailable`.
    with pytest.raises(VoiceControllerUnavailable):
        await client.stream(
            generation, b"overflow", media_type="audio/mpeg",
            guild_id=GUILD, request_id="r-overflow",
        )

    # Once the active speech (and its queue) drains, streaming works again.
    fake.finish_speech()
    await client.stream(
        generation, b"after-drain", media_type="audio/mpeg",
        guild_id=GUILD, request_id="r-after",
    )
