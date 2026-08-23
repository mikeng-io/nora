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

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from nora.domain.music import MusicQueueStore, QueuedTrack
from nora.domain.ports import TrackSource

log = logging.getLogger("nora.music")


class VoiceSession(Protocol):
    def is_playing(self) -> bool: ...
    def play(self, stream_url: str, *, duration_seconds: float | None = None) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop(self) -> None: ...
    def set_volume(self, volume: float) -> None: ...
    async def disconnect(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PlayOutcome:
    status: Literal["playing", "queued", "nothing_found"]
    # The FIRST track — what's now playing, or the first of what got queued.
    # `queued_count` is 1 for an ordinary play/search and >1 only when
    # `query_or_url` resolved to a playlist (design/discord-music/
    # 01-playback-and-source.md's playlist expansion).
    track: QueuedTrack | None
    queued_count: int = 1


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
        """More than one resolved track means `query_or_url` was a playlist
        link — every one of them is enqueued, in order, before this decides
        whether to start playback (matching the single-track path: only the
        FIRST call for an idle guild advances; the rest of a playlist just
        joins the queue behind it, same as if they'd been added one at a
        time)."""
        resolved = await self._source.resolve(query_or_url=query_or_url)
        if not resolved:
            return PlayOutcome(status="nothing_found", track=None)
        # Sequential `await`s, deliberately not `asyncio.gather` — firing a
        # playlist's enqueues concurrently would race on position assignment
        # the same way two concurrent `/music play` calls did before the
        # per-guild lock (subagent review, 2026-08-23); one guild's plays are
        # already serialized by that lock, so this loop staying sequential
        # is what keeps it that way for a single playlist expansion too.
        queued = [
            await self._queue.enqueue(
                guild_id=self._guild_id, source=track.source, url=track.locator,
                title=track.title, added_by_person_id=requested_by, added_at=added_at,
            )
            for track in resolved
        ]
        if self._now_playing is None:
            await self._advance()
            return PlayOutcome(status="playing", track=queued[0], queued_count=len(queued))
        return PlayOutcome(status="queued", track=queued[0], queued_count=len(queued))

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

    def set_volume(self, volume: float) -> bool:
        """`volume` is a linear multiplier — 1.0 unchanged, 0.0 silent, 2.0
        double (`discord.PCMVolumeTransformer`'s own range). Refused
        (`False`) outside `[0.0, 2.0]` rather than clamped, so a caller's
        mistake is visible rather than silently reinterpreted. Unlike
        `pause`/`resume`, nothing needs to be playing for this to succeed —
        it's a setting, not an action on in-progress playback, and applies
        to whatever plays next either way."""
        if not 0.0 <= volume <= 2.0:
            return False
        self._voice.set_volume(volume)
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
        """`QueuedTrack.url` is always a `locator` (`ResolvedTrack`'s own
        contract, `nora/domain/ports.py`) — a resolve INPUT, never a cached
        stream URL — so the actual, playable stream is resolved fresh here,
        right before it's needed. A track that's gone stale by play time (a
        deleted video, a stale playlist entry) resolves to nothing; that's
        the same ordinary `nothing_found` outcome `/music play` already
        treats as unremarkable, so this just moves on to the next track
        instead of surfacing an error nobody issued a command to see."""
        while True:
            self._now_playing = await self._queue.pop_next(self._guild_id)
            self._paused = False
            if self._now_playing is None:
                return
            resolved = await self._source.resolve(query_or_url=self._now_playing.url)
            if resolved:
                self._voice.play(
                    resolved[0].stream_url, duration_seconds=resolved[0].duration_seconds,
                )
                return
            log.warning(
                "queued track no longer resolves, skipping: %s (%s)",
                self._now_playing.title, self._now_playing.url,
            )
