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

import logging

from nora.app.voice_events import VoiceEventConsumer
from nora.infra.voice.hub import VoiceControllerHub

GUILD = "guild-1"
PLAYBACK = "pb-1"


class _StubClient:
    """Just enough of `VoiceControllerClient` for `hub.join` — one canned
    state snapshot carrying the generation and playback id events are then
    checked against."""

    async def join(self, guild_id: str, channel_id: str, *, request_id: str) -> dict:
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

    assert status == 400
    assert payload == {"error": "unknown guild"}
    assert manager.calls == [], "nothing was acted on"
    assert hub.last_event_seq("a-guild-nobody-configured") is None, (
        "no bookmark was created for an unknown guild"
    )
