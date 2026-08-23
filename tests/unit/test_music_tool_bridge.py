"""`DiscordMusicToolBridge` (design/discord-music/07-agent-tool.md).

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
        self, *, skip_result=None, stop_status=None, status_result=None,
    ) -> None:
        self._skip_result = skip_result
        self._stop_status = stop_status
        self._status_result = status_result
        self.stopped_guilds: list[str] = []

    async def skip(self, guild_id: str):
        return self._skip_result

    async def status(self, guild_id: str):
        return self._status_result if self._stop_status is None else self._stop_status

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
        conversation_id="1234/5678", sender_id="9999", query_or_url="apt",
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
