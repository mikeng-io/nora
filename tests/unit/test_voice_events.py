"""`VoiceEventConsumer` (nora/app/voice_events.py) — the controller → nora
callback routing.

The manager and the hub's client are inline stubs: neither is a
`nora/domain/ports.py` port (`MusicPlayerManager` and `VoiceControllerClient`
are concrete application/infra classes), so the port-fake gate does not
apply. What is pinned here is the CONSUMER's own contract: shape validation,
exactly-once dedupe COMMITTED ONLY ON SUCCESS (a failed delivery is
retryable, never acknowledged as a duplicate), stale-playback rejection,
and which manager method each event type reaches with which generation.
The generation/channel staleness checks themselves live in the REAL
manager under its per-guild lock (covered in
`tests/unit/test_music_player_manager.py` and `test_voice_pipeline.py`).
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from nora.app.voice_events import VoiceEventConsumer
from nora.infra.voice.hub import VoiceControllerHub
from nora.web.voice import register_voice
from tests.integration.platform_contract import assert_settled_operation

GUILD = "guild-1"
PLAYBACK = "pb-1"


class _StubClient:
    """Just enough of `VoiceControllerClient` for `hub.join` — one canned
    state snapshot carrying the generation and playback id events are then
    checked against."""

    async def join(self, guild_id: str, channel_id: str, *, operation_id: str) -> dict:
        return {
            "voice": "connected", "generation": "gen-1", "channel_id": channel_id,
            "playback_id": PLAYBACK,
            "player": {"status": "playing", "position_ms": 0, "current": None},
            "speech_queue": 0, "crossfade_seconds": 0,
        }


class _StubManager:
    """The staleness decisions themselves live in the REAL manager (the
    probe matches happen under its per-guild lock — covered in
    `tests/unit/test_music_player_manager.py`); this stub emulates its
    verdicts so the consumer's own routing is what gets pinned here."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failures: int = 0
        #: Holds `on_stream_failed` open until set, so a repeat can arrive mid-transition.
        self.gate: asyncio.Event | None = None
        self.raise_at_gate = False

    async def on_track_finished(self, guild_id: str, *, playback_id: str | None = None) -> bool:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("controller gone mid-transition")
        self.calls.append(f"track-finished:{guild_id}:{playback_id}")
        return playback_id == PLAYBACK

    async def on_stream_failed(
        self, guild_id: str, *, playback_id: str | None = None, reason: str | None = None,
    ) -> bool:
        self.calls.append(f"stream-failed:{guild_id}:{playback_id}:{reason}")
        if self.gate is not None:
            await self.gate.wait()
            if self.raise_at_gate:
                self.raise_at_gate = False
                raise RuntimeError("play commit failed")
        return playback_id == PLAYBACK

    async def on_voice_death(self, guild_id: str, *, generation: str | None = None) -> None:
        self.calls.append(f"voice-death:{guild_id}:{generation}")

    async def on_voice_channel_emptied(
        self, guild_id: str, *, generation: str | None = None, channel_id: str | None = None,
    ) -> None:
        self.calls.append(f"channel-emptied:{guild_id}:{generation}:{channel_id}")


async def _consumer() -> tuple[VoiceEventConsumer, _StubManager, VoiceControllerHub]:
    manager = _StubManager()
    hub = VoiceControllerHub({GUILD: _StubClient()})
    await hub.join(GUILD, "cortez-1")
    return VoiceEventConsumer(manager, hub), manager, hub


def _event(event_type: str, **extra) -> dict:
    body = {
        "type": event_type, "guild_id": GUILD, "event_id": "evt-1",
        "event_seq": 1, "generation": "gen-1",
    }
    body.update(extra)
    return body


async def test_a_non_dict_body_is_a_400():
    consumer, _, _ = await _consumer()

    status, _ = await consumer.consume(["not", "a", "dict"])

    assert status == 400


async def test_a_body_missing_type_or_event_id_is_a_400():
    consumer, _, _ = await _consumer()

    for body in (
        {},
        {"type": "track-finished"},
        {"event_id": "evt-1"},
        {"type": "", "event_id": "evt-1"},
    ):
        status, _ = await consumer.consume(body)
        assert status == 400, body


async def test_an_unknown_event_type_is_a_400():
    consumer, _, _ = await _consumer()

    status, _ = await consumer.consume(_event("something-else"))

    assert status == 400


async def test_a_validation_failure_leaves_the_event_retryable():
    """Consumption is committed ONLY on success — a 400 must not burn
    the `event_id`, so a corrected re-delivery of the same id is
    processed, not answered `duplicate`."""
    consumer, manager, _ = await _consumer()

    bad, _ = await consumer.consume(_event("something-else", event_id="evt-9"))
    good, payload = await consumer.consume(
        _event("track-finished", event_id="evt-9", playback_id=PLAYBACK)
    )

    assert bad == 400
    assert (good, payload["status"]) == (200, "ok")
    assert manager.calls == [f"track-finished:{GUILD}:{PLAYBACK}"]


async def test_an_event_id_is_consumed_exactly_once():
    consumer, manager, _ = await _consumer()
    event = _event("track-finished", playback_id=PLAYBACK)

    first, _ = await consumer.consume(event)
    second, payload = await consumer.consume({**event, "event_seq": 2})

    assert first == 200
    assert (second, payload["status"]) == (200, "duplicate")
    assert manager.calls == [f"track-finished:{GUILD}:{PLAYBACK}"], (
        "a replayed delivery must not advance the queue twice"
    )


async def test_a_failed_delivery_is_500_and_the_retry_is_processed():
    """The first delivery raises mid-transition —
    the `event_id` must NOT be consumed, so the controller's outbox retry
    is PROCESSED instead of acknowledged as a duplicate while the work is
    still undone."""
    consumer, manager, _ = await _consumer()
    manager.failures = 1
    event = _event("track-finished", playback_id=PLAYBACK)

    first, payload = await consumer.consume(event)
    second, payload2 = await consumer.consume({**event, "event_seq": 2})

    assert first == 500, payload
    assert (second, payload2["status"]) == (200, "ok")
    assert manager.calls == [f"track-finished:{GUILD}:{PLAYBACK}"], (
        "the retry advances exactly once — the failure was never acknowledged"
    )


async def test_track_finished_advances_only_for_the_expected_playback():
    consumer, manager, _ = await _consumer()

    status, payload = await consumer.consume(_event("track-finished", playback_id=PLAYBACK))

    assert (status, payload["status"]) == (200, "ok")
    assert manager.calls == [f"track-finished:{GUILD}:{PLAYBACK}"]


async def test_a_stale_track_finished_never_advances():
    """The tail of a skipped/superseded track: 200 (no controller retry),
    and the manager's verdict is NOT an advance."""
    consumer, _, _ = await _consumer()

    for playback_id in ("pb-old", None, 42):
        status, payload = await consumer.consume(
            _event("track-finished", playback_id=playback_id, event_id=f"evt-{playback_id}"),
        )
        assert (status, payload["status"]) == (200, "stale"), playback_id


async def test_stream_failed_is_its_own_transition_not_a_track_end():
    """A track that failed to START has not finished, so this must NOT reach
    `on_track_finished`: routing a failure to the advance makes one rejected CDN
    address cost a whole session — every failure advances, the next track
    resolves through the same broken source session and fails identically, and
    autoplay empties the queue into silence.

    The reason is forwarded because the manager logs it — a failure whose
    cause is unreadable is hard to diagnose."""
    consumer, manager, _ = await _consumer()

    status, _ = await consumer.consume(
        _event("stream-failed", playback_id=PLAYBACK, reason="ffmpeg-exit-1"),
    )

    assert status == 200
    assert manager.calls == [f"stream-failed:{GUILD}:{PLAYBACK}:ffmpeg-exit-1"]


async def test_a_stale_stream_failed_never_advances():
    consumer, _, _ = await _consumer()

    status, payload = await consumer.consume(
        _event("stream-failed", playback_id="pb-old", reason="ffmpeg-exit-1"),
    )

    assert (status, payload["status"]) == (200, "stale")


async def test_voice_death_reaches_the_manager_with_its_generation():
    """The generation check and the hub-session drop live INSIDE the manager
    (under the guild lock) — the consumer's own contract is just forwarding the
    generation faithfully."""
    consumer, manager, hub = await _consumer()

    status, _ = await consumer.consume(_event("voice-death"))

    assert status == 200
    assert manager.calls == [f"voice-death:{GUILD}:gen-1"]
    assert hub.session_for(GUILD) is not None, (
        "the consumer no longer drops the hub session itself — that is the "
        "manager's drop seam, exercised under the guild lock"
    )


async def test_channel_emptied_reaches_the_manager_with_generation_and_channel():
    consumer, manager, _ = await _consumer()

    status, _ = await consumer.consume(_event("channel-emptied", channel_id="cortez-1"))

    assert status == 200
    assert manager.calls == [f"channel-emptied:{GUILD}:gen-1:cortez-1"]


async def test_speech_finished_is_a_noop():
    consumer, manager, _ = await _consumer()

    status, payload = await consumer.consume(_event("speech-finished", request_id="req-1"))

    assert (status, payload["status"]) == (200, "ok")
    assert manager.calls == []


async def test_a_missing_guild_id_is_a_400():
    consumer, _, _ = await _consumer()

    event = _event("speech-finished")
    del event["guild_id"]
    status, _ = await consumer.consume(event)

    assert status == 400


async def test_an_event_seq_gap_is_warned_about(caplog):
    """The consumption guard: an event whose seq skips ahead of the last
    successfully processed one means deliveries were lost in between."""
    consumer, _manager, hub = await _consumer()

    await consumer.consume(_event("track-finished", playback_id=PLAYBACK, event_seq=2))
    with caplog.at_level(logging.WARNING, logger="nora.app.voice_events"):
        status, _ = await consumer.consume(
            _event("track-finished", playback_id=PLAYBACK, event_id="evt-2", event_seq=5)
        )

    assert status == 200
    assert any(
        "seq gap" in record.message and "guild-1" in record.message
        for record in caplog.records
    )
    assert hub.last_event_seq(GUILD) == 5, (
        "a SUCCESSFUL delivery moves the bookmark; the gap warning compared "
        "against the previous one"
    )


async def test_a_failed_delivery_does_not_move_the_seq_bookmark():
    consumer, manager, hub = await _consumer()
    manager.failures = 1

    await consumer.consume(_event("track-finished", playback_id=PLAYBACK, event_seq=3))

    assert hub.last_event_seq(GUILD) is None, (
        "a failed delivery stays retryable — its seq is not consumed either"
    )


async def test_an_event_for_a_guild_with_no_controller_is_refused():
    """An event for a guild that is not configured is refused. This route has no
    auth by design — its only caller is a controller on the private compose
    network. The hub's seq bookmark is a per-guild DICT, so without a check of
    the `guild_id` against the configured controllers every distinct value a
    caller invented would add a permanent entry to a map keyed by request input.

    Accepting it would also be fail-OPEN — an event for a guild nora can do
    nothing about would be answered `200 ok`."""
    consumer, manager, hub = await _consumer()

    status, payload = await consumer.consume(
        _event("speech-finished", guild_id="a-guild-nobody-configured", event_seq=1),
    )

    assert status == 403
    assert payload == {"error": "unknown guild"}
    assert manager.calls == [], "nothing was acted on"
    assert hub.last_event_seq("a-guild-nobody-configured") is None, (
        "no bookmark was created for an unknown guild"
    )


async def test_report_outcomes_follow_the_contract(aiohttp_client, monkeypatch):
    """A controller's report through the route the hub serves: applied and a
    repeated `event_id` are `complete`; a transition that raises is
    `unavailable` and left unconsumed, so the next report (a new operation)
    applies it; an event for a guild the hub does not know is `denied`."""
    monkeypatch.setenv("NORA_BRIDGE_TOKEN", "tok")
    consumer, manager, _ = await _consumer()
    app = web.Application()
    register_voice(app, ingest=_never, resolve_voice_room=lambda _g: None,
                   on_voice_event=consumer.consume)
    client = await aiohttp_client(app)

    async def report(operation_id: str, event: dict) -> dict:
        resp = await client.post("/v1/voice/operations", headers={
            "Authorization": "Bearer tok"}, json={
            "operation_id": operation_id, "kind": "report", "effect": event})
        body = await resp.json()
        assert_settled_operation(resp.status, body)
        return body

    applied = await report("op-1", _event("track-finished", playback_id=PLAYBACK))
    repeated = await report("op-2", _event("track-finished", playback_id=PLAYBACK))
    assert (applied["outcome"], applied["payload"]) == ("complete", {"status": "ok"})
    assert (repeated["outcome"], repeated["payload"]) == ("complete", {"status": "duplicate"})
    assert manager.calls == [f"track-finished:{GUILD}:{PLAYBACK}"]

    manager.failures = 1
    failing = _event("track-finished", event_id="evt-2", playback_id=PLAYBACK, event_seq=2)
    assert (await report("op-3", failing))["outcome"] == "unavailable"
    assert (await report("op-4", failing))["outcome"] == "complete"
    assert manager.calls[-1] == f"track-finished:{GUILD}:{PLAYBACK}" and len(manager.calls) == 2

    stranger = _event("speech-finished", event_id="evt-3", guild_id="guild-nobody-has")
    assert (await report("op-5", stranger))["outcome"] == "denied"


async def _never(message) -> None:
    raise AssertionError("a report never ingests a message")


async def test_a_report_without_an_integer_event_seq_is_a_400():
    consumer, manager, _ = await _consumer()

    for event_seq in (None, "1", True, 1.5):
        event = _event("speech-finished", event_seq=event_seq)
        if event_seq is None:
            del event["event_seq"]
        status, _ = await consumer.consume(event)
        assert status == 400, event_seq
    assert manager.calls == []


async def _reporter(aiohttp_client, monkeypatch, consumer):
    monkeypatch.setenv("NORA_BRIDGE_TOKEN", "tok")
    app = web.Application()
    register_voice(app, ingest=_never, resolve_voice_room=lambda _g: None,
                   on_voice_event=consumer.consume)
    client = await aiohttp_client(app)

    async def report(operation_id: str, event: dict) -> dict:
        resp = await client.post("/v1/voice/operations", headers={
            "Authorization": "Bearer tok"}, json={
            "operation_id": operation_id, "kind": "report", "effect": event})
        body = await resp.json()
        assert_settled_operation(resp.status, body)
        return body

    return report


async def _until(condition) -> None:
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the condition never held")


async def test_a_retry_while_the_first_transition_runs_applies_it_once(
        aiohttp_client, monkeypatch):
    """The outbox retries an event under a new operation id while its first
    transition is still running: the retry waits and answers `duplicate`, and
    the stream failure starts one play commit, not two."""
    consumer, manager, _ = await _consumer()
    manager.gate = asyncio.Event()
    report = await _reporter(aiohttp_client, monkeypatch, consumer)
    event = _event("stream-failed", playback_id=PLAYBACK, reason="eof")

    first = asyncio.ensure_future(report("op-1", event))
    await _until(lambda: manager.calls)
    retry = asyncio.ensure_future(report("op-2", event))
    await asyncio.sleep(0.05)
    assert not retry.done(), "the retry was answered before the first transition settled"
    manager.gate.set()

    applied, repeated = await first, await retry
    assert (applied["outcome"], applied["payload"]) == ("complete", {"status": "ok"})
    assert (repeated["outcome"], repeated["payload"]) == ("complete", {"status": "duplicate"})
    assert manager.calls == [f"stream-failed:{GUILD}:{PLAYBACK}:eof"]


async def test_a_failed_first_transition_fails_its_waiter_and_a_later_retry_applies(
        aiohttp_client, monkeypatch):
    """The first transition raises while a retry waits on it: both are
    `unavailable`, nothing is kept, and the next retry applies it once."""
    consumer, manager, hub = await _consumer()
    manager.gate, manager.raise_at_gate = asyncio.Event(), True
    report = await _reporter(aiohttp_client, monkeypatch, consumer)
    event = _event("stream-failed", playback_id=PLAYBACK, reason="eof")

    first = asyncio.ensure_future(report("op-1", event))
    await _until(lambda: manager.calls)
    waiting = asyncio.ensure_future(report("op-2", event))
    await asyncio.sleep(0.05)
    manager.gate.set()

    assert (await first)["outcome"] == "unavailable"
    assert (await waiting)["outcome"] == "unavailable"
    assert hub.last_event_seq(GUILD) is None, "a failed transition moved the seq bookmark"
    later = await report("op-3", event)
    assert (later["outcome"], later["payload"]) == ("complete", {"status": "ok"})
    assert manager.calls == [f"stream-failed:{GUILD}:{PLAYBACK}:eof"] * 2
