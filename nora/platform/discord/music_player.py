"""`MusicPlayer` — the per-guild state machine (design/discord-music/
03-state-and-persistence.md). Every action here is deterministic; no LLM
judgment is involved in resolving a play request or deciding what plays
next (design/discord-music/00-overview.md).

Now-playing/playback position live ONLY in this object's own attributes —
never persisted (03's "two different lifetimes" split). Only the upcoming
queue, held by the injected `MusicQueueStore`, survives a restart.

`VoiceSession` deliberately does NOT take an `after` callback the way
`discord.py`'s real `VoiceClient.play(source, after=...)` does — that
callback fires off the event loop thread in production, and scheduling it
back onto the loop is the Discord integration layer's job
(`02-control-surface.md`'s own boundary), not this class's. This class
exposes `on_track_finished()` for that layer to call once it has done its
own thread-safe scheduling; `skip()` is the same advance, driven directly
by a command instead."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from nora.domain.music import MusicQueueStore, QueuedTrack
from nora.domain.ports import TrackSource


class VoiceSession(Protocol):
    def is_playing(self) -> bool: ...
    def play(self, stream_url: str) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop(self) -> None: ...
    async def disconnect(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PlayOutcome:
    status: Literal["playing", "queued", "nothing_found"]
    track: QueuedTrack | None


@dataclass(frozen=True, slots=True)
class PlayerStatus:
    now_playing: QueuedTrack | None
    paused: bool
    queue: Sequence[QueuedTrack]


class MusicPlayer:
    def __init__(
        self, *, guild_id: str, queue: MusicQueueStore, source: TrackSource, voice: VoiceSession,
    ) -> None:
        self._guild_id = guild_id
        self._queue = queue
        self._source = source
        self._voice = voice
        self._now_playing: QueuedTrack | None = None
        self._paused = False

    async def play(
        self, *, query_or_url: str, requested_by: str, added_at: datetime,
    ) -> PlayOutcome:
        resolved = await self._source.resolve(query_or_url=query_or_url)
        if resolved is None:
            return PlayOutcome(status="nothing_found", track=None)
        queued = await self._queue.enqueue(
            guild_id=self._guild_id, source=resolved.source, url=resolved.stream_url,
            title=resolved.title, added_by_person_id=requested_by, added_at=added_at,
        )
        if self._now_playing is None:
            await self._advance()
            return PlayOutcome(status="playing", track=queued)
        return PlayOutcome(status="queued", track=queued)

    async def skip(self) -> QueuedTrack | None:
        """Stops whatever is playing (if anything) and immediately advances.
        Returns the track that was skipped, or `None` if nothing was."""
        skipped = self._now_playing
        if skipped is not None:
            self._voice.stop()
        await self._advance()
        return skipped

    async def on_track_finished(self) -> None:
        """The Discord integration layer's `after` callback lands here once
        it has scheduled itself back onto the event loop — the real voice
        client already stopped on its own, so this does NOT call
        `voice.stop()` again."""
        await self._advance()

    def pause(self) -> bool:
        if self._now_playing is None or self._paused:
            return False
        self._voice.pause()
        self._paused = True
        return True

    def resume(self) -> bool:
        if self._now_playing is None or not self._paused:
            return False
        self._voice.resume()
        self._paused = False
        return True

    async def stop(self) -> None:
        """Clears the whole queue, stops playback, and leaves the voice
        channel — `/music stop`."""
        await self._queue.clear(self._guild_id)
        self._now_playing = None
        self._paused = False
        self._voice.stop()
        await self._voice.disconnect()

    async def status(self) -> PlayerStatus:
        queue = await self._queue.queue_for(self._guild_id)
        return PlayerStatus(now_playing=self._now_playing, paused=self._paused, queue=queue)

    async def _advance(self) -> None:
        self._now_playing = await self._queue.pop_next(self._guild_id)
        self._paused = False
        if self._now_playing is not None:
            self._voice.play(self._now_playing.url)
