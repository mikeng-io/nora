"""`VoiceControllerHub` (nora/infra/voice/hub.py) against a fake controller:
per-guild session caching, the §12.1 boot reconciliation (409 → adopt the
existing connection via /voice/status), the fail-closed speech paths, and
the bounded exactly-once event-id dedupe.
"""

from __future__ import annotations

import base64
import logging

import pytest
from aiohttp import web

from nora.infra.voice.http_controller import (
    GenerationConflict,
    VoiceControllerClient,
)
from nora.infra.voice.hub import _EVENT_ID_BOUND, VoiceControllerHub


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
    join_status: int = 200,
    status_voice: str = "connected",
    status_channel: str = "cortez-1",
    status_last_event_seq: int | None = None,
    stream_status: int = 200,
    exit_status: int = 200,
    record: list | None = None,
):
    """A fake controller whose /voice/join, /voice/status, /stream and
    /voice/exit behaviours are independently switchable."""
    app = web.Application()

    async def join(request: web.Request) -> web.Response:
        if record is not None:
            record.append(("join", await request.json()))
        if join_status != 200:
            return web.json_response(
                {"error": "connected_elsewhere", "request_id": "r"}, status=join_status,
            )
        return web.json_response({"ok": True, "request_id": "r", "state": _state()})

    async def status(request: web.Request) -> web.Response:
        if record is not None:
            record.append(("status", None))
        body = _state(
            voice=status_voice, channel_id=status_channel,
            generation="gen-boot", playback_id="pb-boot",
        )
        if status_last_event_seq is not None:
            body["last_event_seq"] = status_last_event_seq
        return web.json_response(body)

    async def stream(request: web.Request) -> web.Response:
        if record is not None:
            record.append(("stream", await request.json()))
        if stream_status == 409:
            return web.json_response(
                {"error": "generation_conflict", "request_id": "r"}, status=409,
            )
        if stream_status != 200:
            return web.json_response(
                {"error": "speech_queue_full", "request_id": "r"}, status=stream_status,
            )
        return web.json_response({"ok": True, "request_id": "r", "state": _state()})

    async def exit_(request: web.Request) -> web.Response:
        if record is not None:
            record.append(("exit", await request.json()))
        if exit_status != 200:
            return web.json_response({"error": "down", "request_id": "r"}, status=exit_status)
        return web.json_response(
            {"ok": True, "request_id": "r",
             "state": _state(voice="idle", generation=None, channel_id=None)},
        )

    app.router.add_post("/voice/join", join)
    app.router.add_get("/voice/status", status)
    app.router.add_post("/stream", stream)
    app.router.add_post("/voice/exit", exit_)
    return app


async def _hub(aiohttp_client, app) -> VoiceControllerHub:
    test_client = await aiohttp_client(app)
    return VoiceControllerHub(
        VoiceControllerClient(str(test_client.make_url("")).rstrip("/"))
    )


async def test_join_caches_the_session_per_guild(aiohttp_client):
    record: list = []
    hub = await _hub(aiohttp_client, _app(record=record))

    first = await hub.join("guild-1", "cortez-1")
    second = await hub.join("guild-1", "cortez-1")

    assert first is second
    assert [kind for kind, _ in record if kind == "join"] == ["join"]
    assert hub.session_for("guild-1") is first


async def test_a_join_conflict_adopts_the_controllers_existing_connection(aiohttp_client):
    """§12.1 boot reconciliation: nora restarted, the controller's
    connection survived — the 409 answers 'already there', and /voice/status
    confirms it IS the channel asked for, so its generation is adopted."""
    record: list = []
    hub = await _hub(aiohttp_client, _app(join_status=409, record=record))

    session = await hub.join("guild-1", "cortez-1")

    assert session.generation == "gen-boot"
    assert session.playback_id == "pb-boot"
    assert hub.expected_playback_id("guild-1") == "pb-boot"
    assert [kind for kind, _ in record] == ["join", "status"]


async def test_a_conflict_to_a_different_channel_is_reraised(aiohttp_client):
    hub = await _hub(
        aiohttp_client, _app(join_status=409, status_channel="cortez-elsewhere"),
    )

    with pytest.raises(GenerationConflict):
        await hub.join("guild-1", "cortez-1")
    assert hub.session_for("guild-1") is None


async def test_a_conflict_with_no_live_connection_is_reraised(aiohttp_client):
    hub = await _hub(aiohttp_client, _app(join_status=409, status_voice="idle"))

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
    hub = await _hub(aiohttp_client, _app(stream_status=409))
    await hub.join("guild-1", "cortez-1")

    assert await hub.stream_speech("guild-1", b"mp3", "audio/mpeg") is False
    assert hub.session_for("guild-1") is None, (
        "a generation the controller no longer recognises must not be reused"
    )


async def test_stream_speech_when_unavailable_is_false_and_keeps_the_session(aiohttp_client):
    hub = await _hub(aiohttp_client, _app(stream_status=503))
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
    hub = await _hub(aiohttp_client, _app(exit_status=503))
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


async def test_consume_event_id_is_true_exactly_once(aiohttp_client):
    hub = await _hub(aiohttp_client, _app())

    assert hub.consume_event_id("evt-1") is True
    assert hub.consume_event_id("evt-1") is False
    assert hub.consume_event_id("evt-2") is True


async def test_the_event_id_window_is_bounded_fifo(aiohttp_client):
    hub = await _hub(aiohttp_client, _app())

    assert hub.consume_event_id("evt-oldest") is True
    for i in range(_EVENT_ID_BOUND):
        assert hub.consume_event_id(f"evt-{i}") is True

    # The oldest entry fell out of the window; newer ones are still held.
    assert hub.consume_event_id("evt-oldest") is True
    assert hub.consume_event_id(f"evt-{_EVENT_ID_BOUND - 1}") is False


async def test_adopting_a_status_with_an_event_seq_gap_warns(aiohttp_client, caplog):
    """§3.3 reconciliation guard: the controller's `last_event_seq` ahead
    of the last seq nora consumed means events were emitted while nora was
    not consuming — the adoption still succeeds, but the gap is surfaced."""
    hub = await _hub(
        aiohttp_client, _app(join_status=409, status_last_event_seq=9),
    )
    hub.note_event_seq(4)

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
            aiohttp_client, _app(join_status=409, status_last_event_seq=last_event_seq),
        )
        hub.note_event_seq(4)

        with caplog.at_level(logging.WARNING, logger="nora.infra.voice"):
            await hub.join("guild-1", "cortez-1")

        assert not any("event gap" in r.message for r in caplog.records), last_event_seq


async def test_note_event_seq_is_monotonic(aiohttp_client):
    hub = await _hub(aiohttp_client, _app())

    assert hub.last_event_seq is None
    hub.note_event_seq(5)
    hub.note_event_seq(3)  # a replayed/out-of-order delivery never moves it back
    hub.note_event_seq(7)

    assert hub.last_event_seq == 7


async def test_seen_event_reads_the_dedupe_window_without_consuming(aiohttp_client):
    """The B7 check-then-commit split: `seen_event` is the pure check the
    consumer runs BEFORE processing; `consume_event_id` is the commit it
    runs only after success."""
    hub = await _hub(aiohttp_client, _app())

    assert hub.seen_event("evt-1") is False
    assert hub.seen_event("evt-1") is False, "a pure check consumes nothing"

    assert hub.consume_event_id("evt-1") is True
    assert hub.seen_event("evt-1") is True


async def test_generation_for_reads_the_cached_sessions_generation(aiohttp_client):
    hub = await _hub(aiohttp_client, _app())

    assert hub.generation_for("guild-1") is None
    await hub.join("guild-1", "cortez-1")

    assert hub.generation_for("guild-1") == "gen-1"
