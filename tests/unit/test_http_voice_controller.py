"""`VoiceControllerClient` + `HttpVoiceSession` (nora/infra/voice/) against a
small fake controller aiohttp app — the status-code → exception mapping and
the session's bookkeeping (playback_id, percent conversion, request ids).

The pinned multi-verb SEMANTICS (replace-play, generation invalidation,
stop-keeps-connection) are the contract suite's job
(`tests/contract/test_voice_controller_contract.py`); here the fake answers
every route the same configurable way, which is all the mapping tests need.
"""

from __future__ import annotations

import pytest
from aiohttp import web

from nora.infra.voice.http_controller import (
    GenerationConflict,
    HttpVoiceSession,
    VoiceControllerClient,
    VoiceControllerRejected,
    VoiceControllerUnavailable,
)
from nora.testing.voice import FakeVoiceController


def _state(**overrides):
    state = {
        "voice": "connected", "generation": "gen-1", "channel_id": "cortez-1",
        "playback_id": "pb-1",
        "player": {"status": "playing", "position_ms": 0, "current": None},
        "speech_queue": 0, "crossfade_seconds": 0,
    }
    state.update(overrides)
    return state


_ROUTES = (
    "/voice/join", "/voice/exit", "/music/play", "/music/stop",
    "/music/pause", "/music/resume", "/music/volume", "/stream",
)


def _app(*, status: int = 200, body: dict | None = None, record: list | None = None):
    """Every route answers the same way; request bodies land in `record`."""
    app = web.Application()

    async def handler(request: web.Request) -> web.Response:
        if record is not None:
            record.append((
                request.method, request.path,
                await request.json() if request.can_read_body else None,
            ))
        if body is not None or status != 200:
            return web.json_response(body or {}, status=status)
        return web.json_response({"ok": True, "request_id": "r-1", "state": _state()})

    async def status_handler(request: web.Request) -> web.Response:
        # The GET body IS the snapshot (plus status-only fields) — it does
        # not carry the POSTs' ok/request_id/state envelope.
        body = _state()
        body.update(
            {"last_event_seq": 0, "streaming": False, "last_error": None}
        )
        return web.json_response(body, status=status)

    for path in _ROUTES:
        app.router.add_post(path, handler)
    app.router.add_get("/voice/status", status_handler)
    return app


async def _client(aiohttp_client, app) -> VoiceControllerClient:
    test_client = await aiohttp_client(app)
    return VoiceControllerClient(str(test_client.make_url("")).rstrip("/"))


async def test_join_returns_the_state_snapshot(aiohttp_client):
    vc = await _client(aiohttp_client, _app())

    state = await vc.join("guild-1", "cortez-1", request_id="req-1")

    assert state["voice"] == "connected"
    assert state["generation"] == "gen-1"


async def test_status_returns_the_full_get_body(aiohttp_client):
    vc = await _client(aiohttp_client, _app())

    # The guild rides the query string on this GET: one controller process
    # serves several, so an unqualified status could only answer for whichever
    # it picked.
    snapshot = await vc.status("guild-1")

    assert snapshot["generation"] == "gen-1"
    assert snapshot["player"]["status"] == "playing"


async def test_a_409_maps_to_generation_conflict(aiohttp_client):
    vc = await _client(
        aiohttp_client,
        _app(status=409, body={"error": "generation_conflict", "request_id": "r"}),
    )

    with pytest.raises(GenerationConflict):
        await vc.play("stale-gen", "https://cdn.example/x", guild_id="guild-1", request_id="req-1")


async def test_a_400_maps_to_rejected_carrying_the_reason(aiohttp_client):
    vc = await _client(
        aiohttp_client,
        _app(status=400, body={"error": "wrong_guild", "request_id": "r"}),
    )

    with pytest.raises(VoiceControllerRejected) as excinfo:
        await vc.join("guild-2", "cortez-1", request_id="req-1")
    assert excinfo.value.reason == "wrong_guild"


async def test_a_503_maps_to_unavailable(aiohttp_client):
    vc = await _client(
        aiohttp_client,
        _app(status=503, body={"error": "speech_queue_full", "request_id": "r"}),
    )

    with pytest.raises(VoiceControllerUnavailable):
        await vc.stream("gen-1", b"mp3-bytes", media_type="audio/mpeg", request_id="req-1")


async def test_a_transport_error_maps_to_unavailable():
    # Port 1 refuses connections — no server needed for this one.
    vc = VoiceControllerClient("http://127.0.0.1:1", timeout_seconds=2.0)

    with pytest.raises(VoiceControllerUnavailable):
        await vc.status("guild-1")


async def test_an_unparseable_success_body_maps_to_unavailable(aiohttp_client):
    app = web.Application()

    async def handler(request: web.Request) -> web.Response:
        return web.Response(text="not json", content_type="text/plain")

    app.router.add_get("/voice/status", handler)
    vc = await _client(aiohttp_client, app)

    with pytest.raises(VoiceControllerUnavailable):
        await vc.status("guild-1")


async def test_a_success_body_without_a_state_snapshot_is_unavailable(aiohttp_client):
    """A 2xx that lacks the pinned `state` envelope is a contract violation,
    reported as `unavailable` — never as success."""
    vc = await _client(
        aiohttp_client, _app(status=200, body={"ok": True, "request_id": "r"}),
    )

    with pytest.raises(VoiceControllerUnavailable):
        await vc.join("guild-1", "cortez-1", request_id="req-1")


async def test_play_records_the_playback_id_from_the_state_snapshot(aiohttp_client):
    vc = await _client(aiohttp_client, _app())
    session = HttpVoiceSession(vc, guild_id="guild-1", generation="gen-1")
    assert session.playback_id is None

    await session.play("https://cdn.example/x", duration_seconds=200.0)

    assert session.playback_id == "pb-1"


async def test_play_sends_the_pinned_body(aiohttp_client):
    record: list = []
    vc = await _client(aiohttp_client, _app(record=record))
    session = HttpVoiceSession(vc, guild_id="guild-1", generation="gen-1")

    await session.play("https://cdn.example/x", duration_seconds=200.0)

    (_, _, body), = [r for r in record if r[1] == "/music/play"]
    assert body["generation"] == "gen-1"
    assert body["stream_url"] == "https://cdn.example/x"
    assert body["duration_seconds"] == 200.0
    assert body["request_id"]


async def test_set_volume_converts_the_float_to_a_percent(aiohttp_client):
    record: list = []
    vc = await _client(aiohttp_client, _app(record=record))
    session = HttpVoiceSession(vc, guild_id="guild-1", generation="gen-1")

    await session.set_volume(0.5)
    await session.set_volume(2.0)

    percents = [r[2]["percent"] for r in record if r[1] == "/music/volume"]
    assert percents == [50, 200]


async def test_every_call_mints_a_fresh_request_id(aiohttp_client):
    record: list = []
    vc = await _client(aiohttp_client, _app(record=record))
    session = HttpVoiceSession(vc, guild_id="guild-1", generation="gen-1")

    await session.pause()
    await session.resume()

    ids = [r[2]["request_id"] for r in record]
    assert len(ids) == 2
    assert ids[0] != ids[1]
    assert all(ids)


async def test_disconnect_tolerates_a_generation_conflict(aiohttp_client):
    """Already gone controller-side IS the end state disconnect asks for."""
    vc = await _client(
        aiohttp_client,
        _app(status=409, body={"error": "generation_conflict", "request_id": "r"}),
    )
    session = HttpVoiceSession(vc, guild_id="guild-1", generation="gen-1")

    await session.disconnect()  # must not raise


async def test_disconnect_propagates_other_failures(aiohttp_client):
    vc = await _client(
        aiohttp_client,
        _app(status=503, body={"error": "controller restarting", "request_id": "r"}),
    )
    session = HttpVoiceSession(vc, guild_id="guild-1", generation="gen-1")

    with pytest.raises(VoiceControllerUnavailable):
        await session.disconnect()


async def test_an_injected_session_is_used_instead_of_a_per_call_one(aiohttp_client):
    test_client = await aiohttp_client(_app())
    vc = VoiceControllerClient(
        str(test_client.make_url("")).rstrip("/"), session=test_client.session,
    )

    state = await vc.join("guild-1", "cortez-1", request_id="req-1")

    assert state["generation"] == "gen-1"


async def test_play_sends_resumable_only_when_given(aiohttp_client):
    """Position policy: `resumable` rides the play body when the caller
    marks a live source, and is absent otherwise (the controller's default
    applies)."""
    record: list = []
    vc = await _client(aiohttp_client, _app(record=record))

    await vc.play(
        "gen-1", "https://cdn.example/live",
        resumable=False, guild_id="guild-1", request_id="r1",
    )
    await vc.play("gen-1", "https://cdn.example/vod", guild_id="guild-1", request_id="r2")

    bodies = [r[2] for r in record if r[1] == "/music/play"]
    assert bodies[0]["resumable"] is False
    assert "resumable" not in bodies[1]


async def test_a_generation_conflict_reconciles_and_retries_once(aiohttp_client):
    """Stale generation after a controller restart: the session
    reads `/voice/status`, ADOPTS the live generation into itself — the
    hub's cache and the manager's player hold this same object, so both
    agree afterwards — and retries the verb, which then succeeds. No
    409-loop."""
    fake = FakeVoiceController("guild-1", "cortez-1")
    test_client = await aiohttp_client(fake.app())
    vc = VoiceControllerClient(str(test_client.make_url("")).rstrip("/"))
    state = await vc.join("guild-1", "cortez-1", request_id="r-join")
    session = HttpVoiceSession(vc, guild_id="guild-1", generation=state["generation"])
    fake.restart()  # connection survives under a NEW generation; ours is stale
    assert fake.generation != session.generation

    await session.play("https://cdn.example/a", duration_seconds=10.0)

    assert session.generation == fake.generation
    assert fake.plays, "the retried play reached the controller"
    assert session.playback_id == fake.playback_id


async def test_a_conflict_with_no_live_connection_does_not_reconcile(aiohttp_client):
    """An EXITED connection is not a stale generation — it is a gone one.
    There is nothing to adopt, and the conflict climbs."""
    fake = FakeVoiceController("guild-1", "cortez-1")
    test_client = await aiohttp_client(fake.app())
    vc = VoiceControllerClient(str(test_client.make_url("")).rstrip("/"))
    state = await vc.join("guild-1", "cortez-1", request_id="r-join")
    session = HttpVoiceSession(vc, guild_id="guild-1", generation=state["generation"])
    await vc.exit(state["generation"], guild_id="guild-1", request_id="r-exit")

    with pytest.raises(GenerationConflict):
        await session.play("https://cdn.example/a")
    assert not fake.plays


async def test_an_arbiter_rejection_is_not_retried(aiohttp_client):
    """`pause_during_speech` is a refusal, not staleness: no status read,
    no retry, the conflict climbs as-is."""
    fake = FakeVoiceController("guild-1", "cortez-1")
    test_client = await aiohttp_client(fake.app())
    vc = VoiceControllerClient(str(test_client.make_url("")).rstrip("/"))
    state = await vc.join("guild-1", "cortez-1", request_id="r-join")
    session = HttpVoiceSession(vc, guild_id="guild-1", generation=state["generation"])
    await session.play("https://cdn.example/a")
    await vc.stream(
        state["generation"], b"speech", media_type="audio/mpeg",
        guild_id="guild-1", request_id="r-s",
    )

    with pytest.raises(GenerationConflict) as excinfo:
        await session.pause()
    assert excinfo.value.reason == "pause_during_speech"


async def test_stop_tolerates_a_dead_generation(aiohttp_client):
    """A 409 `generation_conflict` on /music/stop means the controller
    already forgot this generation — the music slot is definitionally
    stopped, so the teardown that called stop COMPLETES instead of dying
    on a controller that is already gone."""
    fake = FakeVoiceController("guild-1", "cortez-1")
    test_client = await aiohttp_client(fake.app())
    vc = VoiceControllerClient(str(test_client.make_url("")).rstrip("/"))
    state = await vc.join("guild-1", "cortez-1", request_id="r-join")
    session = HttpVoiceSession(vc, guild_id="guild-1", generation=state["generation"])
    await session.play("https://cdn.example/a")
    fake.restart()  # our generation is now unrecognised

    await session.stop()  # must not raise

    assert session.playback_id is None
