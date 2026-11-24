"""`VoiceControllerHub` (nora/infra/voice/hub.py) against a fake controller:
per-guild session caching, the boot reconciliation (a rejected join → adopt
the existing connection via the `status` query), the fail-closed speech paths, and
the per-guild `event_seq` bookmark.
"""

from __future__ import annotations

import base64
import logging

import pytest
from aiohttp import web

from nora.domain.service_api import OperationOutcome, status_for
from nora.infra.voice.http_controller import (
    GenerationConflict,
    VoiceControllerClient,
    VoiceControllerUnavailable,
)
from nora.infra.voice.hub import VoiceControllerHub
from nora.testing.voice import operation_envelope, read_envelope


def _state(**overrides):
    state = {
        "voice": "connected", "generation": "gen-1", "channel_id": "cortez-1",
        "playback_id": "pb-1",
        "player": {"status": "idle", "position_ms": 0, "current": None},
        "speech_queue": 0, "crossfade_seconds": 0,
    }
    state.update(overrides)
    return state


def _app(
    *,
    join: OperationOutcome = "complete",
    status_voice: str = "connected",
    status_channel: str = "cortez-1",
    status_last_event_seq: int | None = None,
    stream: OperationOutcome = "complete",
    leave: OperationOutcome = "complete",
    record: list | None = None,
    join_state: dict | None = None,
):
    """A fake controller whose `join`, `status`, `speak` and `leave` outcomes
    are independently switchable; `rejected` carries the reason each would."""
    app = web.Application()
    names = {"join": "join", "speak": "stream", "leave": "exit"}
    reasons = {"join": "connected_elsewhere", "speak": "generation_conflict"}

    async def operation(request: web.Request) -> web.Response:
        body = await request.json()
        kind = body["kind"]
        if record is not None:
            record.append((names[kind], body["effect"]))
        outcome = {"join": join, "speak": stream, "leave": leave}[kind]
        state = (_state(voice="idle", generation=None, channel_id=None)
                 if kind == "leave" else join_state if kind == "join" and join_state
                 else _state())
        payload: dict = {"state": state}
        if outcome != "complete":
            reason = reasons.get(kind, "down")
            payload["error"] = {"kind": reason, "message": reason}
        return web.json_response(operation_envelope(outcome, payload),
                                 status=status_for(outcome))

    async def status(request: web.Request) -> web.Response:
        if record is not None:
            record.append(("status", None))
        body = _state(
            voice=status_voice, channel_id=status_channel,
            generation="gen-boot", playback_id="pb-boot",
        )
        if status_last_event_seq is not None:
            body["last_event_seq"] = status_last_event_seq
        return web.json_response(read_envelope("ok", body))

    app.router.add_post("/v1/voice/operations", operation)
    app.router.add_post("/v1/voice/queries", status)
    return app


async def _hub(aiohttp_client, app) -> VoiceControllerHub:
    test_client = await aiohttp_client(app)
    # One client per guild. Both test guilds
    # point at the same fake controller: what is under test here is the hub's
    # per-guild bookkeeping, not two real processes.
    client = VoiceControllerClient(str(test_client.make_url("")).rstrip("/"))
    return VoiceControllerHub({"guild-1": client, "guild-2": client})


async def test_a_join_without_its_playback_key_is_unavailable_and_plays_nothing(aiohttp_client):
    """Boot recovery keeps or replays the persisted track by the join's
    `playback_id`; a join that leaves the key out cannot say which, so it is
    `unavailable`: no session, and nothing but the join reached the controller."""
    record: list = []
    state = _state()
    state.pop("playback_id")
    hub = await _hub(aiohttp_client, _app(record=record, join_state=state))

    with pytest.raises(VoiceControllerUnavailable):
        await hub.join("guild-1", "cortez-1")

    assert hub.session_for("guild-1") is None
    assert [name for name, _ in record] == ["join"]


async def test_an_idle_join_names_no_playback(aiohttp_client):
    hub = await _hub(aiohttp_client, _app(join_state=_state(playback_id=None)))

    session = await hub.join("guild-1", "cortez-1")

    assert session.playback_id is None


async def test_join_caches_the_session_per_guild(aiohttp_client):
    record: list = []
    hub = await _hub(aiohttp_client, _app(record=record))

    first = await hub.join("guild-1", "cortez-1")
    second = await hub.join("guild-1", "cortez-1")

    assert first is second
    assert [kind for kind, _ in record if kind == "join"] == ["join"]
    assert hub.session_for("guild-1") is first


async def test_a_join_conflict_adopts_the_controllers_existing_connection(aiohttp_client):
    """Boot reconciliation: nora restarted, the controller's
    connection survived — the rejection answers 'already there', and `status`
    confirms it IS the channel asked for, so its generation is adopted."""
    record: list = []
    hub = await _hub(aiohttp_client, _app(join="rejected", record=record))

    session = await hub.join("guild-1", "cortez-1")

    assert session.generation == "gen-boot"
    assert session.playback_id == "pb-boot"
    assert hub.expected_playback_id("guild-1") == "pb-boot"
    assert [kind for kind, _ in record] == ["join", "status"]


async def test_a_conflict_to_a_different_channel_is_reraised(aiohttp_client):
    hub = await _hub(
        aiohttp_client, _app(join="rejected", status_channel="cortez-elsewhere"),
    )

    with pytest.raises(GenerationConflict):
        await hub.join("guild-1", "cortez-1")
    assert hub.session_for("guild-1") is None


async def test_a_conflict_with_no_live_connection_is_reraised(aiohttp_client):
    hub = await _hub(aiohttp_client, _app(join="rejected", status_voice="idle"))

    with pytest.raises(GenerationConflict):
        await hub.join("guild-1", "cortez-1")


async def test_stream_speech_without_a_session_is_false(aiohttp_client):
    hub = await _hub(aiohttp_client, _app())

    assert await hub.stream_speech("guild-1", b"mp3", "audio/mpeg") is False


async def test_stream_speech_posts_the_audio_for_the_sessions_generation(aiohttp_client):
    record: list = []
    hub = await _hub(aiohttp_client, _app(record=record))
    await hub.join("guild-1", "cortez-1")

    assert await hub.stream_speech("guild-1", b"mp3-bytes", "audio/mpeg") is True

    (_, body), = [r for r in record if r[0] == "stream"]
    assert body["generation"] == "gen-1"
    assert body["guild_id"] == "guild-1"
    assert body["media_type"] == "audio/mpeg"
    assert base64.b64decode(body["audio_b64"]) == b"mp3-bytes"


async def test_stream_speech_on_conflict_drops_the_session_and_is_false(aiohttp_client):
    hub = await _hub(aiohttp_client, _app(stream="rejected"))
    await hub.join("guild-1", "cortez-1")

    assert await hub.stream_speech("guild-1", b"mp3", "audio/mpeg") is False
    assert hub.session_for("guild-1") is None, (
        "a generation the controller no longer recognises must not be reused"
    )


async def test_stream_speech_when_unavailable_is_false_and_keeps_the_session(aiohttp_client):
    hub = await _hub(aiohttp_client, _app(stream="unavailable"))
    await hub.join("guild-1", "cortez-1")

    assert await hub.stream_speech("guild-1", b"mp3", "audio/mpeg") is False
    assert hub.session_for("guild-1") is not None


async def test_expected_playback_id_is_none_without_a_session(aiohttp_client):
    hub = await _hub(aiohttp_client, _app())

    assert hub.expected_playback_id("guild-1") is None


async def test_exit_without_a_session_is_false(aiohttp_client):
    hub = await _hub(aiohttp_client, _app())

    assert await hub.exit("guild-1") is False


async def test_exit_disconnects_and_clears_the_cache(aiohttp_client):
    record: list = []
    hub = await _hub(aiohttp_client, _app(record=record))
    await hub.join("guild-1", "cortez-1")

    assert await hub.exit("guild-1") is True
    assert hub.session_for("guild-1") is None
    assert [kind for kind, _ in record if kind == "exit"] == ["exit"]


async def test_a_failed_exit_is_false_and_still_clears_the_cache(aiohttp_client):
    hub = await _hub(aiohttp_client, _app(leave="unavailable"))
    await hub.join("guild-1", "cortez-1")

    assert await hub.exit("guild-1") is False
    assert hub.session_for("guild-1") is None, (
        "a dead connection must not be reused even when the exit call failed"
    )


async def test_clear_forgets_without_an_exit_call(aiohttp_client):
    record: list = []
    hub = await _hub(aiohttp_client, _app(record=record))
    await hub.join("guild-1", "cortez-1")

    hub.drop("guild-1")

    assert hub.session_for("guild-1") is None
    assert [kind for kind, _ in record if kind == "exit"] == []


async def test_adopting_a_status_with_an_event_seq_gap_warns(aiohttp_client, caplog):
    """Reconciliation guard: the controller's `last_event_seq` ahead
    of the last seq nora consumed means events were emitted while nora was
    not consuming — the adoption still succeeds, but the gap is surfaced."""
    hub = await _hub(
        aiohttp_client, _app(join="rejected", status_last_event_seq=9),
    )
    hub.note_event_seq("guild-1", 4)

    with caplog.at_level(logging.WARNING, logger="nora.infra.voice"):
        session = await hub.join("guild-1", "cortez-1")

    assert session.generation == "gen-boot"
    assert any(
        "event gap" in record.message and "guild-1" in record.message
        for record in caplog.records
    ), caplog.records


async def test_adoption_without_a_seq_gap_does_not_warn(aiohttp_client, caplog):
    """The null case that keeps the warning meaningful: caught-up seqs
    (and a status carrying no seq at all) stay silent."""
    for last_event_seq in (4, None):
        caplog.clear()
        hub = await _hub(
            aiohttp_client, _app(join="rejected", status_last_event_seq=last_event_seq),
        )
        hub.note_event_seq("guild-1", 4)

        with caplog.at_level(logging.WARNING, logger="nora.infra.voice"):
            await hub.join("guild-1", "cortez-1")

        assert not any("event gap" in r.message for r in caplog.records), last_event_seq


async def test_note_event_seq_is_monotonic(aiohttp_client):
    hub = await _hub(aiohttp_client, _app())

    assert hub.last_event_seq("guild-1") is None
    hub.note_event_seq("guild-1", 5)
    hub.note_event_seq("guild-1", 3)  # a replay never moves it back
    hub.note_event_seq("guild-1", 7)

    assert hub.last_event_seq("guild-1") == 7


async def test_generation_for_reads_the_cached_sessions_generation(aiohttp_client):
    hub = await _hub(aiohttp_client, _app())

    assert hub.generation_for("guild-1") is None
    await hub.join("guild-1", "cortez-1")

    assert hub.generation_for("guild-1") == "gen-1"


async def test_adopt_boot_voice_sessions_adopts_without_a_persisted_music_row(aiohttp_client):
    """A wake-only guild with no persisted music
    session never calls `hub.join` any other way — boot adoption must reach it
    from config, and speech must stream afterwards.

    Config names GUILDS, so the CHANNEL comes from the controller's own
    `status` query: it is the only thing that knows where it reconnected."""
    record: list = []
    hub = await _hub(aiohttp_client, _app(join="rejected", record=record))

    await hub.adopt_boot_voice_sessions(["guild-1"])

    assert hub.session_for("guild-1") is not None
    assert await hub.stream_speech("guild-1", b"mp3", "audio/mpeg") is True
    kinds = [kind for kind, _ in record if kind in ("join", "status")]
    assert kinds == ["status", "join", "status"], (
        "status FIRST to learn the channel, then the join it implies"
    )


async def test_adopt_boot_adopts_the_channel_the_controller_is_actually_in(aiohttp_client):
    """Adoption takes the channel from the controller's `status`, not the
    configured one: a controller that came back up in a different channel —
    ordinary, since any channel is joinable — must not be dragged back to the
    old one."""
    record: list = []
    hub = await _hub(aiohttp_client, _app(status_channel="somewhere-else", record=record))

    await hub.adopt_boot_voice_sessions(["guild-1"])

    joins = [body for kind, body in record if kind == "join"]
    assert [j["channel_id"] for j in joins] == ["somewhere-else"]


async def test_adopt_boot_skips_a_guild_whose_controller_is_idle(aiohttp_client):
    """Boot adoption picks up a connection that already EXISTS. An idle
    controller has none, and joining one anyway would pull Nora into a voice
    channel nobody asked for, on every restart."""
    record: list = []
    hub = await _hub(aiohttp_client, _app(status_voice="idle", record=record))

    await hub.adopt_boot_voice_sessions(["guild-1"])

    assert [kind for kind, _ in record if kind == "join"] == []
    assert hub.session_for("guild-1") is None


async def test_adopt_boot_voice_sessions_is_idempotent_with_an_existing_session(aiohttp_client):
    record: list = []
    hub = await _hub(aiohttp_client, _app(record=record))
    await hub.join("guild-1", "cortez-1")

    await hub.adopt_boot_voice_sessions(["guild-1"])

    assert [kind for kind, _ in record if kind == "join"] == ["join"], (
        "an already-cached session must not re-join"
    )


async def test_adopt_boot_voice_sessions_skips_empty_entries(aiohttp_client):
    record: list = []
    hub = await _hub(aiohttp_client, _app(record=record))

    await hub.adopt_boot_voice_sessions([""])

    assert record == []
    assert hub.session_for("guild-1") is None


async def test_adopt_boot_voice_sessions_does_not_raise_when_the_controller_is_down(
    aiohttp_client,
):
    hub = await _hub(aiohttp_client, _app(join="unavailable"))

    await hub.adopt_boot_voice_sessions(["guild-1"])  # must not raise

    assert hub.session_for("guild-1") is None


async def test_the_event_seq_bookmark_is_per_guild(aiohttp_client):
    """The `event_seq` counter is per guild. Each controller
    mints its own from 1. With ONE counter, guild-2's first event
    would read as a jump backwards from guild-1's 40th, and guild-1's next
    would read as a 39-event gap — so the warning that exists to mean "events
    were lost while nora was down" would fire on every healthy
    two-guild boot, which is how a real gap stops being noticed."""
    hub = await _hub(aiohttp_client, _app())

    hub.note_event_seq("guild-1", 40)

    assert hub.last_event_seq("guild-2") is None, (
        "a guild nora has consumed nothing for has no baseline, not another "
        "guild's"
    )
    hub.note_event_seq("guild-2", 1)
    assert hub.last_event_seq("guild-1") == 40, "guild-2 must not move guild-1"
    assert hub.last_event_seq("guild-2") == 1


async def test_an_unmapped_guild_has_no_controller_and_fails_closed(aiohttp_client):
    """The clients map IS the allow-list: a guild with no controller cannot be
    joined, and says so rather than reaching for another guild's."""
    hub = await _hub(aiohttp_client, _app())

    assert "guild-3" not in hub.guild_ids
    with pytest.raises(VoiceControllerUnavailable, match="no audio-controller"):
        await hub.join("guild-3", "cortez-1")
    assert hub.session_for("guild-3") is None


async def test_guild_ids_is_the_join_allow_list(aiohttp_client):
    hub = await _hub(aiohttp_client, _app())

    assert hub.guild_ids == frozenset({"guild-1", "guild-2"})


async def test_join_to_a_different_channel_is_a_conflict_not_a_cache_hit(aiohttp_client):
    """The session cache is keyed by GUILD, and a cache hit must mean the same
    channel. The join gate admits any channel in the guild, so "cached session
    is for a DIFFERENT channel" is reachable and must not be answered with the
    connection Nora already holds.

    Otherwise `/music play` from channel B while Nora sits in A plays
    the music out of A, while the player persists B. `channel-emptied` for A
    then never matches the player's channel, so auto-leave stops firing, and
    the next boot resumes against B. `/discord join` escapes it only by having
    its own `session_for` check.

    The controller's own rejection never fires because nora never sends the request."""
    record: list = []
    hub = await _hub(aiohttp_client, _app(record=record))
    await hub.join("guild-1", "cortez-1")
    joins_before = len([k for k, _ in record if k == "join"])

    with pytest.raises(GenerationConflict, match="already connected to channel"):
        await hub.join("guild-1", "cortez-2")

    assert [k for k, _ in record if k == "join"] == ["join"] * joins_before, (
        "a conflicting join must not reach the controller either"
    )
    assert hub.session_for("guild-1") is not None, "the live session survives"


async def test_rejoining_the_same_channel_is_still_a_cache_hit(aiohttp_client):
    record: list = []
    hub = await _hub(aiohttp_client, _app(record=record))
    first = await hub.join("guild-1", "cortez-1")

    again = await hub.join("guild-1", "cortez-1")

    assert again is first
    assert [k for k, _ in record if k == "join"] == ["join"], "no second join"


async def test_the_channel_is_forgotten_with_the_session(aiohttp_client):
    """A stale channel left behind would make the next join to it look like a
    cache hit for a connection that no longer exists."""
    hub = await _hub(aiohttp_client, _app())
    await hub.join("guild-1", "cortez-1")

    hub.drop("guild-1")
    await hub.join("guild-1", "cortez-2")  # must not raise

    assert hub.session_for("guild-1") is not None


async def test_an_adopted_join_records_the_channel_it_adopted(aiohttp_client):
    """A rejected join that IS the channel asked for adopts the live connection
    (`_adopt_existing`), and the cache must record that channel — otherwise
    the very next send would re-join and take a second connection.

    Note the shape this pins: adoption can only ever succeed for the channel
    asked for, because `_adopt_existing` refuses a controller connected
    anywhere else. So "recorded" and "requested" cannot diverge here — the
    controller's own value is used anyway, because deriving cache state from
    what we asked for rather than what we were told is how the cache ends up
    naming the wrong channel."""
    hub = await _hub(
        aiohttp_client, _app(join="rejected", status_channel="cortez-1"),
    )

    await hub.join("guild-1", "cortez-1")

    again = await hub.join("guild-1", "cortez-1")
    assert again is not None, "the adopted session is cached under its channel"
    with pytest.raises(GenerationConflict, match="already connected to channel"):
        await hub.join("guild-1", "cortez-2")


async def test_boot_adoption_is_quiet_when_a_guild_has_no_connection(aiohttp_client, caplog):
    """A controller holding no connection for a guild answers the `status`
    query `nothing_found`, and since one process serves N guilds that is the ordinary
    COLD-BOOT state — not a fault. It is logged at INFO ("nothing to adopt"),
    never as a failed adoption at WARNING, which would fire on every boot.

    A warning that always fires is one people scroll past, and then the boot
    where adoption really did fail looks like the twenty before it."""
    app = web.Application()

    async def status(request: web.Request) -> web.Response:
        return web.json_response(read_envelope("nothing_found", None))

    app.router.add_post("/v1/voice/queries", status)
    hub = await _hub(aiohttp_client, app)

    with caplog.at_level(logging.INFO, logger="nora.infra.voice"):
        await hub.adopt_boot_voice_sessions(["guild-1"])

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "a guild with no connection is not a failure"
    )
    assert any("nothing to adopt" in r.message for r in caplog.records)
    assert hub.session_for("guild-1") is None


async def test_boot_adoption_still_warns_on_a_real_refusal(aiohttp_client, caplog):
    """The other side of it: a 400 refusal still warns."""
    app = web.Application()

    async def status(request: web.Request) -> web.Response:
        return web.json_response({"error": "wrong_guild"}, status=400)

    app.router.add_post("/v1/voice/queries", status)
    hub = await _hub(aiohttp_client, app)

    with caplog.at_level(logging.INFO, logger="nora.infra.voice"):
        await hub.adopt_boot_voice_sessions(["guild-1"])

    assert [r for r in caplog.records if r.levelno >= logging.WARNING]
