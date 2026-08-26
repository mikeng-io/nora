"""`MusicPlayer` — the per-guild state machine (design/discord-music/
03-state-and-persistence.md, 06-session-persistence.md). Every action here
is deterministic; no LLM judgment is involved in resolving a play request or
deciding what plays next (design/discord-music/00-overview.md).

The upcoming queue (`MusicQueueStore`) and the session snapshot
(`MusicSessionStore`) are both persisted; only PLAYBACK POSITION within the
current track never is (06's own scope: resuming mid-frame is not
attempted, only "what track, paused-or-not, what volume, which channels").
Every transition that changes what a restart would need to know writes
through to `self._session` before returning.

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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from nora.domain.music import (
    AUTOPLAY_PERSON_ID,
    MusicQueueStore,
    MusicSession,
    MusicSessionStore,
    QueuedTrack,
)
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
        session: MusicSessionStore, voice_channel_id: str, notify_channel_id: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        autoplay_enabled: bool = False,
    ) -> None:
        self._guild_id = guild_id
        self._queue = queue
        self._source = source
        self._voice = voice
        self._session = session
        self._voice_channel_id = voice_channel_id
        self._notify_channel_id = notify_channel_id
        self._clock = clock
        self._autoplay_enabled = autoplay_enabled
        self._now_playing: QueuedTrack | None = None
        self._paused = False
        self._volume = 1.0
        # Locators of every track that actually STARTED playing in this
        # session (recorded in `_advance`/`resume_from_persisted`, right
        # before the stream starts). Autoplay's pick selection skips
        # anything already here (design/discord-music/08-autoplay.md —
        # played-history dedup): one-at-a-time reseeding from a YouTube
        # Mix that only excludes its own seed is how a long autoplay chain
        # drifts into repeating the same few tracks (found live 2026-08-26
        # — \"it keep repeating some of the songs\"). In-memory and
        # session-scoped on purpose, like every other playback-state field:
        # a restart begins a fresh window, matching the session-persistence
        # boundary (06) where only the queue/now-playing row survives.
        self._played_locators: set[str] = set()

    async def _persist_session(self) -> None:
        now_playing = self._now_playing
        await self._session.upsert(MusicSession(
            guild_id=self._guild_id, voice_channel_id=self._voice_channel_id,
            notify_channel_id=self._notify_channel_id,
            now_playing_source=now_playing.source if now_playing else None,
            now_playing_locator=now_playing.url if now_playing else None,
            now_playing_title=now_playing.title if now_playing else None,
            paused=self._paused, volume=self._volume, updated_at=self._clock(),
        ))

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
            # NOT `queued[0]`: `_advance()` pops from the FRONT of the
            # persisted queue, which is `queued[0]` only when the queue was
            # empty before this call. A guild carrying leftover tracks from
            # an earlier session (e.g. a boot-time-discarded session whose
            # queue wasn't drained) would otherwise have this claim the
            # just-resolved track is playing while a stale one actually
            # started (found live 2026-08-24: "play apt" reported APT
            # while an orphaned K-pop queue entry played instead).
            # `self._now_playing` is what `_advance()` actually started.
            return PlayOutcome(status="playing", track=self._now_playing, queued_count=len(queued))
        return PlayOutcome(status="queued", track=queued[0], queued_count=len(queued))

    async def skip(self) -> QueuedTrack | None:
        """Advances immediately. Returns the track that was skipped, or
        `None` if nothing was.

        Deliberately does NOT call `voice.stop()` up front when there IS a
        next track: `_advance()`'s own `voice.play()` call for it replaces
        whatever's currently audible (a crossfade if `[music]
        crossfade_seconds` is set, otherwise `DiscordVoiceSession`'s own
        live-swap does an instant cut) — the same mechanism a natural
        track-end crossfade already relies on, just triggered by a command
        instead of the early-fire timer. Only when the queue is EMPTY is
        there nothing to supersede the skipped track's still-live audio,
        so `stop()` is the explicit fallback for that case."""
        skipped = self._now_playing
        await self._advance()
        if self._now_playing is None:
            self._voice.stop()
        return skipped

    async def on_track_finished(self) -> None:
        """The Discord integration layer's `after` callback lands here once
        it has scheduled itself back onto the event loop — the real voice
        client already stopped on its own, so this does NOT call
        `voice.stop()` again."""
        await self._advance()

    async def pause(self) -> bool:
        if self._now_playing is None or self._paused:
            return False
        self._voice.pause()
        self._paused = True
        await self._persist_session()
        return True

    async def resume(self) -> bool:
        if self._now_playing is None or not self._paused:
            return False
        self._voice.resume()
        self._paused = False
        await self._persist_session()
        return True

    async def set_volume(self, volume: float) -> bool:
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
        self._volume = volume
        await self._persist_session()
        return True

    async def resume_from_persisted(
        self, *, source: str, locator: str, title: str, paused: bool, volume: float,
    ) -> bool:
        """Boot-time resume (design/discord-music/06-session-persistence.md
        stage 2) — restarts the track a restart interrupted, from position
        0 (never mid-frame, same limitation `_advance`'s lazy-resolve
        already has). Returns `False` if the persisted track no longer
        resolves (deleted, region-locked); the caller falls back to
        `on_track_finished()` to pick up wherever the ACTUAL queue
        continues, exactly like a stale queued track already does."""
        self._volume = volume
        self._voice.set_volume(volume)
        resolved = await self._source.resolve(query_or_url=locator)
        if not resolved:
            return False
        # Not popped from `self._queue` — this track already WAS popped,
        # before the restart. `added_by_person_id`/`position` carry no
        # meaningful value for a resumed track; left blank/sentinel rather
        # than guessed.
        self._now_playing = QueuedTrack(
            id=uuid4(), guild_id=self._guild_id, position=-1, source=source,
            url=resolved[0].locator, title=title or resolved[0].title,
            added_by_person_id="", added_at=self._clock(),
        )
        # Same played-history record as `_advance`: a track resumed at
        # boot HAS played in this session and must occupy a dedup slot,
        # or the first autoplay pick after a restart could repeat it.
        self._played_locators.add(self._now_playing.url)
        self._paused = False
        self._voice.play(resolved[0].stream_url, duration_seconds=resolved[0].duration_seconds)
        if paused:
            self._voice.pause()
            self._paused = True
        await self._persist_session()
        return True

    async def stop(self) -> None:
        """Clears the whole queue, stops playback, and leaves the voice
        channel — `/music stop`. Deletes the persisted session row too:
        this is the one transition that actually ENDS the session, as
        opposed to every other one just changing what's in it."""
        await self._queue.clear(self._guild_id)
        self._now_playing = None
        self._paused = False
        self._voice.stop()
        await self._voice.disconnect()
        await self._session.delete(self._guild_id)

    async def top(self, position: int) -> QueuedTrack | None:
        """`/music top` / the `top_music` agent tool: moves the `position`-th
        queued track (1-based, matching `/music queue`'s own display) to the
        front so it plays next. `None` for an out-of-range `position`
        (including an empty queue) — never guesses which track was meant.

        Never touches `self._now_playing` or `self._voice` — reordering the
        UPCOMING queue doesn't affect what's currently playing, the same way
        `skip()` leaves the rest of the queue's order alone until
        `_advance()` runs."""
        return await self._queue.move_to_front(self._guild_id, position=position)

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
            # Captured before `pop_next` overwrites it -- the SEED autoplay
            # recommends from, once the queue turns out empty below
            # (design/discord-music/08-autoplay.md). `None` on the very
            # first track of a session (nothing has played yet to seed
            # from), which is the correct "nothing to autoplay from" case,
            # not a bug.
            previous = self._now_playing
            self._now_playing = await self._queue.pop_next(self._guild_id)
            self._paused = False
            if self._now_playing is None:
                if (
                    self._autoplay_enabled and previous is not None
                    and await self._enqueue_autoplay_pick(previous)
                ):
                    continue  # `pop_next` above picks it right back up
                await self._persist_session()
                return
            resolved = await self._source.resolve(query_or_url=self._now_playing.url)
            if resolved:
                # Recorded HERE, right before the stream starts -- the
                # played history autoplay's pick selection dedups from
                # (design/discord-music/08-autoplay.md). A track that
                # failed to resolve (the stale-entry warning path below)
                # is never recorded: it never played, so it has no right
                # to occupy a dedup slot.
                self._played_locators.add(self._now_playing.url)
                self._voice.play(
                    resolved[0].stream_url, duration_seconds=resolved[0].duration_seconds,
                )
                await self._persist_session()
                return
            log.warning(
                "queued track no longer resolves, skipping: %s (%s)",
                self._now_playing.title, self._now_playing.url,
            )

    async def _enqueue_autoplay_pick(self, previous: QueuedTrack) -> bool:
        """`True` only if a track was actually enqueued -- `related()`
        answering empty (no Mix, nothing new in it, or the lookup failed)
        is the ordinary "nothing to offer" outcome (`TrackSource.related`'s
        own NEVER-raises discipline), not an error, and this falls straight
        through to the caller's existing idle behaviour rather than
        retrying or guessing a track."""
        related = await self._source.related(locator=previous.url)
        if not related:
            return False
        # Played-history dedup: a Mix only excludes its own seed, so a
        # chain that keeps reseeding from the latest pick drifts into the
        # same few tracks (found live 2026-08-26 -- "it keep repeating
        # some of the songs"). Anything already played this session is
        # skipped; an all-repeats Mix falls through to the caller's idle
        # behaviour exactly like an empty one -- never retried, never
        # substituted with a guess (the existing autoplay discipline).
        pick = next(
            (t for t in related if t.locator not in self._played_locators),
            None,
        )
        if pick is None:
            return False
        await self._queue.enqueue(
            guild_id=self._guild_id, source=pick.source, url=pick.locator, title=pick.title,
            added_by_person_id=AUTOPLAY_PERSON_ID, added_at=self._clock(),
        )
        return True
