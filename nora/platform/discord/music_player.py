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

import asyncio
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
from nora.domain.ports import ResolvedTrack, TrackSource

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
        autoplay_prewarm_seconds: float = 0.0,
        loop: asyncio.AbstractEventLoop | None = None,
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
        # `0` disables prewarm entirely: the next autoplay pick is decided
        # only after the current track ends, the pre-R-64 behaviour.
        self._autoplay_prewarm_seconds = autoplay_prewarm_seconds
        self._loop = loop
        self._now_playing: QueuedTrack | None = None
        # The most recent track that ACTUALLY started in this session --
        # the autoplay re-seed seed (R-67). Unlike `_now_playing`, this is
        # NOT cleared when the queue empties: a chain that ends (autoplay
        # answered empty) must still leave a seed behind, so a boot-time
        # resume can re-seed autoplay from it instead of sitting silent.
        # Persisted in `music_session.last_played_*`; restored into a
        # fresh player by `seed_last_played` (boot-time only).
        self._last_played: QueuedTrack | None = None
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
        # Autoplay prewarm (R-64): the NEXT autoplay pick is resolved
        # while the current track is still audible, so the gap between
        # track-end and the next track starting is a resolve-less,
        # instant `play()` instead of two fresh network calls
        # (`related()` + `resolve()`, design/discord-music/08-autoplay.md).
        # `None` = no pick prewarmed; `_prewarm_task` is the in-flight
        # coroutine that may fill it, and `_prewarm_for_locator` names the
        # track the pick was scheduled FOR — so a stale task arriving late
        # (after a skip/stop already advanced past that track) must discard
        # its result, never enqueue it.
        self._prewarmed: ResolvedTrack | None = None
        self._prewarm_done = False
        self._prewarm_task: asyncio.Task[None] | None = None
        self._prewarm_for_locator: str | None = None
        self._prewarm_timer: asyncio.TimerHandle | None = None
        # The pre-resolved STREAM for a prewarmed pick's locator — so the
        # `_advance` that plays it pays zero network calls at track-end:
        # `resolve()` is skipped when the locator is cached here (it was
        # resolved during prewarm, which itself ran during the previous
        # track). Consumed on pop, cleared on cancel.
        self._prewarm_streams: dict[str, ResolvedTrack] = {}
        # Set when `_advance` finds the voice connection dead (R-65) — the
        # player has torn down its own queue/session/voice state but CANNOT
        # remove itself from `MusicPlayerManager._players` (external audit
        # MAJOR-2, 2026-08-26: the manager kept holding the dead player, so
        # the idle timer still fired and a `/music play` before the timeout
        # reused the dead player). The manager reads this after every
        # transition and calls `_leave` to drop the player entirely.
        self._session_dead = False

    async def _persist_session(self) -> None:
        now_playing = self._now_playing
        last_played = self._last_played
        await self._session.upsert(MusicSession(
            guild_id=self._guild_id, voice_channel_id=self._voice_channel_id,
            notify_channel_id=self._notify_channel_id,
            now_playing_source=now_playing.source if now_playing else None,
            now_playing_locator=now_playing.url if now_playing else None,
            now_playing_title=now_playing.title if now_playing else None,
            paused=self._paused, volume=self._volume, updated_at=self._clock(),
            last_played_source=last_played.source if last_played else None,
            last_played_locator=last_played.url if last_played else None,
            last_played_title=last_played.title if last_played else None,
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
        # R-67: the re-seed seed, same moment as the dedup slot -- a
        # resumed track is the most recent thing that played.
        self._last_played = self._now_playing
        self._paused = False
        try:
            self._voice.play(resolved[0].stream_url, duration_seconds=resolved[0].duration_seconds)
        except Exception:
            # External audit Codex MAJOR-3 (2026-08-26): the same dead-voice
            # teardown as `_advance` — a boot-time resume into a voice
            # connection that reconnect said was up but actually isn't must
            # not strand a zombie player in the manager (it was inserted
            # into `_players` before this call). Mark dead so the caller's
            # `_after_transition` evicts it and the persisted row gets
            # re-scanned next boot instead of blocking a fresh join.
            log.exception(
                "voice connection dead for guild %s during persisted-session resume; "
                "tearing down",
                self._guild_id,
            )
            self._now_playing = None
            self._session_dead = True
            return False
        if paused:
            self._voice.pause()
            self._paused = True
        await self._persist_session()
        return True

    def seed_last_played(self, *, source: str, locator: str, title: str) -> None:
        """Boot-time only (R-67): restores the persisted `last_played_*`
        trio into a fresh player as the autoplay re-seed seed, WITHOUT
        starting playback. Called by the manager when a persisted session
        has nothing currently playing (autoplay's chain had already ended
        before the restart) but a last-played track is on record -- so the
        next `_advance` (the boot-time resume's fall-through) can seed the
        autoplay chain from it instead of sitting silent in a live voice
        channel.

        Deliberately does NOT touch `_played_locators`: the seed track
        itself is not re-played by this call, and if autoplay's first pick
        happens to BE this track, that is a legitimate fresh pick for the
        new session (the dedup window is session-scoped, and this track
        has not played in THIS session yet)."""
        self._last_played = QueuedTrack(
            id=uuid4(), guild_id=self._guild_id, position=-1, source=source,
            url=locator, title=title, added_by_person_id="", added_at=self._clock(),
        )

    async def stop(self) -> None:
        """Clears the whole queue, stops playback, and leaves the voice
        channel — `/music stop`. Deletes the persisted session row too:
        this is the one transition that actually ENDS the session, as
        opposed to every other one just changing what's in it."""
        self._cancel_prewarm()
        await self._queue.clear(self._guild_id)
        self._now_playing = None
        # R-67: a stop ENDS the session -- the re-seed seed dies with it.
        # A later `/music play` starts a fresh session with no seed, which
        # is the correct "nothing to autoplay from" case.
        self._last_played = None
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
        instead of surfacing an error nobody issued a command to see.

        When the queue empties and autoplay is on, a PREWARMED pick is
        consumed before any fresh `related()` call is made (R-64): the
        pick was already resolved during the track that just ended, so the
        only work here is the same `voice.play()` any queued track gets.

        A voice connection that dies mid-session (a gateway/voice websocket
        drop Discord doesn't surface as a state change — found live
        2026-08-26: \"now its NOT even playing\", after a `discord gateway
        resumed`) makes `voice.play()` raise `ClientException: Not
        connected to voice.` The session is torn down like a stop instead
        of letting that exception climb into the `on_finished` handler and
        strand `_now_playing` claiming a track is audible when nothing
        is."""
        while True:
            # Captured before `pop_next` overwrites it -- the SEED autoplay
            # recommends from, once the queue turns out empty below
            # (design/discord-music/08-autoplay.md). Falls back to
            # `_last_played` (R-67): a boot-time resume that found nothing
            # playing (autoplay's chain had already ended before the
            # restart) still has a seed to re-seed the chain from, instead
            # of sitting silent in a live voice channel. `None` on the very
            # first track of a session (nothing has played yet to seed
            # from), which is the correct "nothing to autoplay from" case,
            # not a bug.
            previous = self._now_playing or self._last_played
            # Whether to bother arming the prewarm for the track this call
            # is about to start. The `queue_for` read is skipped entirely
            # when autoplay is off or prewarm is disabled (the default), so
            # an ordinary deployment pays nothing extra per track.
            lookahead = self._autoplay_enabled and self._autoplay_prewarm_seconds > 0
            upcoming_count = None
            if lookahead:
                upcoming_count = len(await self._queue.queue_for(self._guild_id))
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
            resolved: Sequence[ResolvedTrack]
            cached = self._prewarm_streams.pop(self._now_playing.url, None)
            if cached is not None:
                resolved = (cached,)
            else:
                resolved = await self._source.resolve(query_or_url=self._now_playing.url)
            if resolved:
                # Recorded HERE, right before the stream starts -- the
                # played history autoplay's pick selection dedups from
                # (design/discord-music/08-autoplay.md). A track that
                # failed to resolve (the stale-entry warning path below)
                # is never recorded: it never played, so it has no right
                # to occupy a dedup slot.
                self._played_locators.add(self._now_playing.url)
                # R-67: the re-seed seed. Recorded at the same moment as
                # the dedup slot -- a track that actually started is the
                # most recent thing that played, and must survive the
                # queue emptying (unlike `_now_playing`, which is cleared
                # by the next `_advance`).
                self._last_played = self._now_playing
                try:
                    self._voice.play(
                        resolved[0].stream_url, duration_seconds=resolved[0].duration_seconds,
                    )
                except Exception:
                    # The voice connection is dead (see method docstring) --
                    # nothing to resume without a fresh connection, so end
                    # the session the way a stop does and let a human
                    # `/music play` a fresh join.
                    log.exception(
                        "voice connection dead for guild %s; tearing down the session",
                        self._guild_id,
                    )
                    self._cancel_prewarm()
                    self._now_playing = None
                    try:
                        await self._queue.clear(self._guild_id)
                    except Exception:
                        log.warning("could not clear queue after dead voice", exc_info=True)
                    try:
                        await self._voice.disconnect()
                    except Exception:
                        log.warning("voice disconnect after dead voice failed", exc_info=True)
                    try:
                        await self._session.delete(self._guild_id)
                    except Exception:
                        # External audit Codex MAJOR-4 (2026-08-26): a
                        # transient DB failure here must not prevent the
                        # `_session_dead` flag below — the manager's
                        # `_after_transition` evicts the player on it, and a
                        # stranded session row is recovered by the next
                        # boot-time resume scan instead of leaving a zombie.
                        log.warning("session delete after dead voice failed", exc_info=True)
                    # External audit MAJOR-2 (2026-08-26): mark the player
                    # dead so the manager drops it from `_players` on this
                    # transition — otherwise a `/music play` before the idle
                    # timeout reuses this dead player and fails again, and
                    # the voice-death monitor can double-teardown.
                    self._session_dead = True
                    return
                await self._persist_session()
                # Prewarm only when this track is the LAST one in the
                # queue: if more remain, autoplay will never fire at its
                # end and the network calls would be pure waste.
                if lookahead and upcoming_count is not None and upcoming_count <= 1:
                    self._schedule_prewarm(resolved[0].duration_seconds)
                return
            log.warning(
                "queued track no longer resolves, skipping: %s (%s)",
                self._now_playing.title, self._now_playing.url,
            )

    def _schedule_prewarm(self, duration_seconds: float | None) -> None:
        """Arms a timer that picks the next autoplay track `prewarm_seconds`
        BEFORE this track's expected end (R-64, design/discord-music/
        08-autoplay.md). The completed pick lands in `self._prewarmed` for
        `_advance` to consume with zero network calls. Any earlier prewarm
        was for a DIFFERENT track and is cancelled/replaced, never consumed
        by this one.

        Cancels the previous timer/task FIRST: an advance (natural track-end
        or a skip) happens while the last track's prewarm timer may still be
        pending or its task still in flight. Left alone, the old timer fires
        and spawns a second `_prewarm_pick` for the SAME new track (the
        locator check passes — `_prewarm_for_locator` was already updated),
        issuing a wasted `related()` network call and racing to fill
        `_prewarm_streams` (external audit MAJOR-1, 2026-08-26)."""
        now = self._now_playing
        if now is None or not duration_seconds or self._loop is None:
            return
        delay = duration_seconds - self._autoplay_prewarm_seconds
        if delay <= 0:
            return
        self._cancel_prewarm()
        # A stale pick from the previous track must not be consumed as if
        # it belonged to this one (a `related()` answer for the OLD seed
        # would seed the next autoplay from the wrong track).
        self._prewarmed = None
        self._prewarm_done = False
        self._prewarm_for_locator = now.url
        self._prewarm_timer = self._loop.call_later(delay, self._start_prewarm)

    def _start_prewarm(self) -> None:
        self._prewarm_timer = None
        now = self._now_playing
        if now is None or now.url != self._prewarm_for_locator:
            return  # the track it was scheduled for ended/skipped meanwhile
        self._prewarm_task = self._loop.create_task(self._prewarm_pick(now))  # type: ignore[union-attr]

    def _cancel_prewarm(self) -> None:
        task = self._prewarm_task
        self._prewarm_task = None
        self._prewarmed = None
        self._prewarm_done = False
        self._prewarm_streams.clear()
        if self._prewarm_timer is not None:
            self._prewarm_timer.cancel()
            self._prewarm_timer = None
        if task is not None:
            task.cancel()

    async def _prewarm_pick(self, previous: QueuedTrack) -> None:
        """The prewarm coroutine. Runs `_autoplay_pick` early (the
        network-heavy part: `related()`) AND resolves that pick's playable
        stream right away, stashing both — so the `_advance` that plays it
        at track-end pays zero network calls. Every outcome — including a
        fresh pick that is `None`, meaning "nothing to offer" — lands the
        state rather than leaving an exception to escape a background
        task."""
        try:
            pick = await self._autoplay_pick(previous)
        except Exception:
            log.warning("autoplay prewarm failed", exc_info=True)
            return
        if self._prewarm_task is not asyncio.current_task():
            return  # a newer prewarm replaced this one; its result is stale
        if self._now_playing is None or self._now_playing.url != self._prewarm_for_locator:
            return  # the track it was scheduled for ended/skipped meanwhile
        self._prewarmed = pick
        self._prewarm_done = True
        if pick is not None:
            # Resolve the STREAM now, not at track-end. A stale stream URL
            # by play time is `_advance`'s ordinary unresolvable-track
            # outcome, never a crash.
            try:
                resolved = await self._source.resolve(query_or_url=pick.locator)
            except Exception:
                log.warning("autoplay prewarm stream resolve failed", exc_info=True)
                resolved = ()
            if resolved and self._prewarm_task is asyncio.current_task():
                self._prewarm_streams[pick.locator] = resolved[0]
            elif self._prewarm_task is asyncio.current_task():
                # Nothing playable arrived; a later `_advance` will fall
                # back to its own resolve, exactly like a stale queued
                # track today. The stale-pick guard above still applies.
                # NOT an empty answer -- an unresolvable pick is retried at
                # track end so `_advance` can re-resolve it live (the
                # R-62 no-retry rule is about `related()` answering empty,
                # not about a pick whose early stream resolve failed).
                self._prewarmed = None
                self._prewarm_done = False

    async def _autoplay_pick(self, previous: QueuedTrack) -> ResolvedTrack | None:
        """The next autoplay pick: a prewarmed pick is consumed first
        (R-64 — the whole point of prewarm is that this costs nothing at
        track-end); otherwise `related()` is called now, the pre-R-64
        fallback. Either way, played-history dedup (R-63) applies: a pick
        already played this session falls through to a fresh one, and an
        all-repeats Mix returns `None` exactly like an empty one — never
        retried, never substituted with a guess."""
        prewarmed = self._prewarmed
        if self._prewarm_done and prewarmed is None:
            # A completed prewarm answered EMPTY — but that is NOT the
            # final answer (R-68, 2026-09-01): the prewarm ran up to
            # `autoplay_prewarm_seconds` BEFORE the track ended, and a
            # transient YouTube failure (or a Mix that was momentarily
            # empty) at that earlier moment must not permanently kill the
            # chain. Fall through to the live `related()` call below —
            # ONE retry at the actual track end. If THAT also answers
            # empty, the chain ends exactly as before (R-62's
            # never-retried discipline still holds for the live answer).
            # This supersedes external audit Codex MAJOR-1 (2026-08-26),
            # which treated the prewarm's empty as final; the live retry
            # is bounded to one, so it cannot loop.
            self._prewarm_done = False
        if prewarmed is not None:
            self._prewarmed = None
            self._prewarm_done = False
            if prewarmed.locator not in self._played_locators:
                return prewarmed
            # The prewarmed pick was played in the meantime (a skip, a
            # user re-queue) -- fall through to a fresh pick below.
        related = await self._source.related(locator=previous.url)
        if not related:
            return None
        return next(
            (t for t in related if t.locator not in self._played_locators),
            None,
        )

    async def _enqueue_autoplay_pick(self, previous: QueuedTrack) -> bool:
        """`True` only if a track was actually enqueued -- `related()`
        answering empty (no Mix, nothing new in it, or the lookup failed)
        is the ordinary "nothing to offer" outcome (`TrackSource.related`'s
        own NEVER-raises discipline), not an error, and this falls straight
        through to the caller's existing idle behaviour rather than
        retrying or guessing a track."""
        pick = await self._autoplay_pick(previous)
        if pick is None:
            return False
        await self._queue.enqueue(
            guild_id=self._guild_id, source=pick.source, url=pick.locator, title=pick.title,
            added_by_person_id=AUTOPLAY_PERSON_ID, added_at=self._clock(),
        )
        return True
