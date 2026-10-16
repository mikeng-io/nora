"""`DiscordMusicToolBridge`.

**Partially not unit-tested**, same reason `music_commands.py`'s own
docstring gives for the slash-command layer: `_member_voice_channel`
ultimately needs a real `discord.Guild`/`discord.Member`/`discord.
VoiceChannel` object graph to mean anything, and faking that faithfully
would mean reimplementing discord.py itself (CLAUDE.md's fake-realism
rule — a low-fidelity fake here would be worse than no test).

What IS covered here: everything that does NOT require a live voice-channel
lookup — `skip`/`stop`/`status` (pure `MusicPlayerManager` delegation, no
Discord object graph involved at all), `play`'s early "no live connection"
exit, and the `conversation_id` parsing helper. Verify the voice-join path
by running this against a real bot token and voice channel; there is no
other way to verify it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from nora.domain.music import QueuedTrack
from nora.platform.discord.music_tool_bridge import DiscordMusicToolBridge

T0 = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def _track(title: str, url: str = "https://www.youtube.com/watch?v=x") -> QueuedTrack:
    return QueuedTrack(
        id=uuid4(), guild_id="guild-1", position=0, source="youtube",
        url=url, title=title, added_by_person_id="miles", added_at=T0,
    )


class _DisconnectedAdapter:
    """`current_client()` always `None` -- no live Discord connection."""

    def current_client(self):
        return None


class _StubManager:
    def __init__(
        self, *, skip_result=None, stop_status=None, status_result=None, top_result=None,
        set_volume_result=None, set_volume_error=None,
    ) -> None:
        self._skip_result = skip_result
        self._stop_status = stop_status
        self._status_result = status_result
        self._top_result = top_result
        self._set_volume_result = set_volume_result
        self._set_volume_error = set_volume_error
        self.stopped_guilds: list[str] = []
        self.top_calls: list[tuple[str, int]] = []
        self.set_volume_calls: list[tuple[str, float]] = []

    async def skip(self, guild_id: str):
        return self._skip_result

    async def top(self, guild_id: str, position: int):
        self.top_calls.append((guild_id, position))
        return self._top_result

    async def status(self, guild_id: str):
        return self._status_result if self._stop_status is None else self._stop_status

    async def set_volume(self, guild_id: str, volume: float):
        self.set_volume_calls.append((guild_id, volume))
        if self._set_volume_error is not None:
            raise self._set_volume_error
        return self._set_volume_result

    async def stop(self, guild_id: str) -> None:
        self.stopped_guilds.append(guild_id)


def _bridge(manager, *, adapter=None) -> DiscordMusicToolBridge:
    return DiscordMusicToolBridge(
        adapter=adapter or _DisconnectedAdapter(), manager=manager,  # type: ignore[arg-type]
    )


async def test_guild_id_parses_the_conversation_id_prefix() -> None:
    bridge = _bridge(_StubManager())

    assert bridge._guild_id("1234/5678") == "1234"


async def test_play_with_no_live_discord_connection_is_unavailable() -> None:
    bridge = _bridge(_StubManager(), adapter=_DisconnectedAdapter())

    outcome = await bridge.play(
        conversation_id="1234/5678", sender_ids=frozenset({"9999"}), query_or_url="apt",
    )

    assert outcome.status == "unavailable"


async def test_skip_with_nothing_playing_is_nothing_found() -> None:
    bridge = _bridge(_StubManager(skip_result=None))

    outcome = await bridge.skip(conversation_id="1234/5678")

    assert outcome.status == "nothing_found"


async def test_skip_reports_the_skipped_track() -> None:
    bridge = _bridge(_StubManager(skip_result=_track("Track A")))

    outcome = await bridge.skip(conversation_id="1234/5678")

    assert outcome.status == "ok"
    assert outcome.title == "Track A"


async def test_top_with_nothing_queued_at_that_position_is_nothing_found() -> None:
    bridge = _bridge(_StubManager(top_result=None))

    outcome = await bridge.top(conversation_id="1234/5678", position=3)

    assert outcome.status == "nothing_found"


async def test_top_reports_the_moved_track_and_forwards_the_position() -> None:
    manager = _StubManager(top_result=_track("Track C"))
    bridge = _bridge(manager)

    outcome = await bridge.top(conversation_id="1234/5678", position=3)

    assert outcome.status == "ok"
    assert outcome.title == "Track C"
    assert manager.top_calls == [("1234", 3)]


async def test_stop_with_nothing_playing_is_nothing_found_and_never_calls_stop() -> None:
    manager = _StubManager(status_result=None)
    bridge = _bridge(manager)

    outcome = await bridge.stop(conversation_id="1234/5678")

    assert outcome.status == "nothing_found"
    assert manager.stopped_guilds == []


async def test_status_reports_the_now_playing_track() -> None:
    from nora.platform.discord.music_player import PlayerStatus

    manager = _StubManager(
        status_result=PlayerStatus(now_playing=_track("Track A"), paused=False, queue=()),
    )
    bridge = _bridge(manager)

    outcome = await bridge.status(conversation_id="1234/5678")

    assert outcome.status == "ok"
    assert outcome.title == "Track A"


async def test_status_with_nothing_playing_is_nothing_found() -> None:
    from nora.platform.discord.music_player import PlayerStatus

    manager = _StubManager(
        status_result=PlayerStatus(now_playing=None, paused=False, queue=()),
    )
    bridge = _bridge(manager)

    outcome = await bridge.status(conversation_id="1234/5678")

    assert outcome.status == "nothing_found"


async def test_set_volume_converts_percent_to_float_and_reports_ok() -> None:
    manager = _StubManager(set_volume_result=True)
    bridge = _bridge(manager)

    outcome = await bridge.set_volume(conversation_id="1234/5678", percent=130)

    assert outcome.status == "ok"
    assert manager.set_volume_calls == [("1234", 1.3)]


async def test_set_volume_with_nothing_playing_is_nothing_found() -> None:
    manager = _StubManager(set_volume_result=None)
    bridge = _bridge(manager)

    outcome = await bridge.set_volume(conversation_id="1234/5678", percent=50)

    assert outcome.status == "nothing_found"
    assert manager.set_volume_calls == [("1234", 0.5)]


async def test_set_volume_out_of_range_or_race_is_unavailable_not_nothing_found() -> None:
    """`False` from the manager is out-of-range or the
    N-4 teardown race — never a 'nothing playing' claim. `unavailable`
    (couldn't apply) beats `nothing_found` (there's no session), which
    would contradict the note."""
    manager = _StubManager(set_volume_result=False)
    bridge = _bridge(manager)

    outcome = await bridge.set_volume(conversation_id="1234/5678", percent=50)

    assert outcome.status == "unavailable"
    assert "between 0 and 200" in outcome.note


async def test_set_volume_manager_error_is_unavailable() -> None:
    manager = _StubManager(set_volume_error=RuntimeError("boom"))
    bridge = _bridge(manager)

    outcome = await bridge.set_volume(conversation_id="1234/5678", percent=50)

    assert outcome.status == "unavailable"
