"""`MusicPlayer` — the per-guild state machine. Every action here
is deterministic; no LLM judgment is involved in resolving a play request or
deciding what plays next.

The upcoming queue (`MusicQueueStore`) and the session snapshot
(`MusicSessionStore`) are both persisted; only PLAYBACK POSITION within the
current track never is (resuming mid-frame is not attempted, only "what
track, paused-or-not, what volume, which channels").
Every transition that changes what a restart would need to know writes
through to `self._session` before returning.

`VoiceSession` is an AWAITABLE port: the one
real implementation, `nora/infra/voice/http_controller.py`'s
`HttpVoiceSession`, drives the audio-controller over HTTP rather than
holding a local voice connection. Track completion arrives as the
controller's `track-finished` event (`POST /app/v1/voice/event`), which the
voice-event consumer routes to `MusicPlayerManager.on_track_finished` —
advancement happens on ACTUAL completion only, never on a local timer.
`skip()` is the same advance, driven directly by a command instead."""

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
    """The player's handle on the voice connection — awaitable because the
    real implementation POSTs to the audio-controller's HTTP control plane
    (`play` is REPLACE semantics there, so a `play()` while audible
    supersedes the current track, matching what skip/advance rely on).

    Speech (TTS) deliberately has NO verb here: spoken replies ride the
    controller's `/stream` endpoint through `VoiceControllerHub`, which owns
    the per-guild generation — never through the music session."""

    async def play(
        self, stream_url: str, *,
        duration_seconds: float | None = None, resumable: bool | None = None,
    ) -> None: ...
    async def pause(self) -> None: ...
    async def resume(self) -> None: ...
    async def stop(self) -> None: ...
    async def set_volume(self, volume: float) -> None: ...
    async def disconnect(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PlayOutcome:
    status: Literal["playing", "queued", "nothing_found"]
    # The FIRST track — what's now playing, or the first of what got queued.
    # `queued_count` is 1 for an ordinary play/search and >1 only when
    # `query_or_url` resolved to a playlist.
    track: QueuedTrack | None
    queued_count: int = 1


@dataclass(frozen=True, slots=True)
class PlayerStatus:
    now_playing: QueuedTrack | None
    paused: bool
    queue: Sequence[QueuedTrack]


@dataclass(frozen=True, slots=True)
class _PendingPlay:
    """A track `_advance_prepare` decided to START whose outbound
    `voice.play` POST has not landed yet (the manager deliberately RELEASES
    its per-guild lock around that POST so a hung audio-controller cannot
    block skip/stop/exit).
    `schedule_prewarm` carries the lookahead decision made under the lock
    so the finish step needs no queue re-read."""

    track: QueuedTrack
    stream_url: str
    duration_seconds: float | None
    schedule_prewarm: bool


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
        # only after the current track ends.
        self._autoplay_prewarm_seconds = autoplay_prewarm_seconds
        self._loop = loop
        self._now_playing: QueuedTrack | None = None
        # The most recent track that ACTUALLY started in this session --
        # the autoplay re-seed seed (R-88). Unlike `_now_playing`, this is
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
        # anything already here (played-history dedup): reseeding one at a
        # time from a YouTube Mix that only excludes its own seed drifts a
        # long autoplay chain into repeating the same few tracks.
        # In-memory and session-scoped on purpose, like every other
        # playback-state field: a restart begins a fresh window, since only
        # the queue/now-playing row survives a restart.
        self._played_locators: set[str] = set()
        # Autoplay prewarm (R-64): the NEXT autoplay pick is resolved
        # while the current track is still audible, so the gap between
        # track-end and the next track starting is a resolve-less,
        # instant `play()` instead of two fresh network calls
        # (`related()` + `resolve()`).
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
        # remove itself from `MusicPlayerManager._players`; left there, the idle
        # timer would still fire and a `/music play` before the timeout would
        # reuse the dead player. The manager reads this after every
        # transition and calls `_leave` to drop the player entirely.
        self._session_dead = False
        # Serializes the outbound play POSTs THEMSELVES: the manager
        # runs `commit_pending` with its guild lock RELEASED, so two
        # advances whose prepares overlapped could otherwise reach the
        # controller out of order — and replace semantics make the LAST
        # arrival win. This lock is per-player and held only around the
        # POST, so stop/skip never wait on it.
        self._commit_lock = asyncio.Lock()
        # Bumped by `stop()` (and therefore `leave()`, which calls it) and
        # compared by `set_volume`'s post-await re-check. Unlike
        # `pause`/`resume`, volume has no track to compare
        # identity against (nothing needs to be playing for it to apply,
        # so `self._now_playing` can be `None` before AND after a
        # concurrent stop), so a plain counter is the signal instead.
        self._teardown_count = 0

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
        time).

        Composed convenience over the three phases (`play_prepare` →
        `commit_pending` → `finish_pending`) for callers holding no guild
        lock; `MusicPlayerManager` drives the phases itself so the outbound
        POST runs with the lock released."""
        queued, pending, was_idle = await self.play_prepare(
            query_or_url=query_or_url, requested_by=requested_by, added_at=added_at,
        )
        if pending is not None:
            ok, playback_id, teardown_count = await self.commit_pending(pending)
            await self.finish_pending(
                pending, ok=ok, playback_id=playback_id, teardown_count_before=teardown_count,
            )
        return self.play_outcome(queued=queued, was_idle=was_idle)

    async def play_prepare(
        self, *, query_or_url: str, requested_by: str, added_at: datetime,
    ) -> tuple[list[QueuedTrack], _PendingPlay | None, bool]:
        """Resolve + enqueue + the advance DECISION, with NO outbound play
        POST — the prepare phase. Returns the enqueued
        tracks (empty when the query resolved to nothing), the pending
        play (`None` when nothing needs to start), and whether the session
        was idle beforehand (the playing-vs-queued distinction
        `play_outcome` needs once the commit has run)."""
        resolved = await self._source.resolve(query_or_url=query_or_url)
        if not resolved:
            return [], None, self._now_playing is None
        # Sequential `await`s, deliberately not `asyncio.gather` — firing a
        # playlist's enqueues concurrently would race on position assignment
        # the same way two concurrent `/music play` calls would without the
        # per-guild lock; one guild's plays are already serialized by that
        # lock, so this loop staying sequential keeps it that way for a single
        # playlist expansion too.
        queued = [
            await self._queue.enqueue(
                guild_id=self._guild_id, source=track.source, url=track.locator,
                title=track.title, added_by_person_id=requested_by, added_at=added_at,
            )
            for track in resolved
        ]
        was_idle = self._now_playing is None
        pending = await self._advance_prepare() if was_idle else None
        return queued, pending, was_idle

    def play_outcome(
        self, *, queued: Sequence[QueuedTrack], was_idle: bool,
    ) -> PlayOutcome:
        """Computed AFTER `finish_pending` — a commit that found the voice
        connection dead tore the session down, so `self._now_playing` (not
        the prepare-time expectation) is what the reply must describe.
        NOT `queued[0]` for the playing case: `_advance_prepare()` pops
        from the FRONT of the persisted queue, which is `queued[0]` only
        when the queue was empty before this call. A guild carrying
        leftover tracks from an earlier session (e.g. a boot-time-
        discarded session whose queue wasn't drained) would otherwise have
        this claim the just-resolved track is playing while a stale one
        actually started."""
        if not queued:
            return PlayOutcome(status="nothing_found", track=None)
        if was_idle:
            return PlayOutcome(status="playing", track=self._now_playing, queued_count=len(queued))
        return PlayOutcome(status="queued", track=queued[0], queued_count=len(queued))

    async def skip(self) -> QueuedTrack | None:
        """Advances immediately. Returns the track that was skipped, or
        `None` if nothing was.

        Deliberately does NOT call `voice.stop()` up front when there IS a
        next track: the advance's own `voice.play()` call for it REPLACES
        whatever's currently audible (the controller's `/music/play` is
        replace-not-enqueue) — the same
        mechanism a natural track-end relies on, just triggered by a command
        instead of the controller's `track-finished` event. Only when the
        queue is EMPTY is there nothing to supersede the skipped track's
        still-live audio, so `stop()` is the explicit fallback for that case.

        Composed over the three phases like `play()`; the manager drives
        `skip_prepare`/`commit_pending`/`finish_pending` itself."""
        skipped, pending, stop_audio = await self.skip_prepare()
        if pending is not None:
            ok, playback_id, teardown_count = await self.commit_pending(pending)
            await self.finish_pending(
                pending, ok=ok, playback_id=playback_id, teardown_count_before=teardown_count,
            )
        elif stop_audio:
            await self._voice.stop()
        return skipped

    async def skip_prepare(self) -> tuple[QueuedTrack | None, _PendingPlay | None, bool]:
        """Skip's under-lock phase: (the skipped track, the pending play,
        stop-audio). `stop-audio` marks the queue-ran-out case — NOTHING
        supersedes the skipped track's still-live audio, so the controller
        must be told to go silent (replace semantics cover every other
        case)."""
        skipped = self._now_playing
        pending = await self._advance_prepare()
        stop_audio = pending is None and self._now_playing is None
        return skipped, pending, stop_audio

    async def stop_audio(self) -> None:
        """Just the controller stop — NO queue/session mutation. The
        manager's skip path calls it with the guild lock released
        when a skip leaves nothing to supersede the skipped track's audio."""
        await self._voice.stop()

    async def on_track_finished(self) -> None:
        """The controller's `track-finished` event lands here via the
        voice-event consumer — the controller already stopped the track on
        its own, so this does NOT call `voice.stop()` again. Composed over
        the three phases; the manager drives them itself."""
        pending = await self._advance_prepare()
        if pending is None:
            return
        ok, playback_id, teardown_count = await self.commit_pending(pending)
        await self.finish_pending(
            pending, ok=ok, playback_id=playback_id, teardown_count_before=teardown_count,
        )

    async def prepare_advance(self) -> _PendingPlay | None:
        """`on_track_finished`'s prepare phase, public for the manager's
        lock scoping (staleness check and decision under the guild lock, the
        POST outside it)."""
        return await self._advance_prepare()

    @property
    def now_playing_locator(self) -> str | None:
        """The locator of the track currently playing, for the manager's
        per-track stale-stream retry budget. The budget is keyed by THIS,
        not by playback id: every retry starts a new playback, so a
        playback-keyed budget would reset itself on each attempt and never
        run out."""
        track = self._now_playing
        return None if track is None else track.url

    async def retry_current_prepare(self) -> _PendingPlay | None:
        """Re-resolve the CURRENTLY-playing track for a fresh stream
        address — the prepare phase of the stale-stream retry. `None` when
        there is nothing playing or the track no longer resolves, and the
        manager advances instead.

        Deliberately does NOT touch the queue. The track did not finish;
        the address it was handed went bad. `pop_next` is exactly the wrong
        move: every failure would advance, the next track would be resolved
        through the same broken session, fail the same way, and autoplay
        would drain the queue.

        The source's session is refreshed FIRST (`TrackSource.
        refresh_session`) — a locator is only as good as the session that
        signed it, so re-resolving through the same one reproduces the
        failure and spends a round-trip doing it.

        Three things this must NOT redo, all of them already done by the
        advance that started this track: the prewarm cache is bypassed
        (`_prewarm_streams` is never read here — a cached address is
        precisely the kind of thing that just failed), `schedule_prewarm`
        is `False` (the failed play's own `finish_pending` already armed it
        if it was going to be — the POST succeeded, it was ffmpeg that
        died), and `_played_locators`/`_last_played` keep the entries that
        advance recorded. A retry is the same track continuing, not a new
        one starting."""
        track = self._now_playing
        if track is None:
            return None
        await self._source.refresh_session()
        resolved = await self._source.resolve(query_or_url=track.url)
        if not resolved:
            log.warning(
                "stale-stream retry for guild %s: %s (%s) no longer resolves — advancing",
                self._guild_id, track.title, track.url,
            )
            return None
        return _PendingPlay(
            track=track,
            stream_url=resolved[0].stream_url,
            duration_seconds=resolved[0].duration_seconds,
            schedule_prewarm=False,
        )

    async def pause(self) -> bool:
        if self._now_playing is None or self._paused:
            return False
        track = self._now_playing
        await self._voice.pause()
        if self._now_playing is not track:
            # The pause response outlived the track it was for — a
            # stop/teardown landed while the POST was in flight (reachable
            # only by a caller that bypasses the manager's guild lock; the
            # manager serializes this properly). Persisting now would
            # RECREATE the session row the teardown deleted.
            return False
        self._paused = True
        await self._persist_session()
        return True

    async def resume(self) -> bool:
        if self._now_playing is None or not self._paused:
            return False
        track = self._now_playing
        await self._voice.resume()
        if self._now_playing is not track:
            # Same as `pause`: the response landed after a teardown.
            return False
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
        teardown_count = self._teardown_count
        await self._voice.set_volume(volume)
        if self._teardown_count != teardown_count:
            # A stop/leave landed while this awaited — persisting now would
            # RECREATE the session row the teardown deleted. Same shape as
            # `pause`/`resume`'s post-await re-check, but volume has no track to compare
            # identity against (`self._now_playing` can be `None` before
            # AND after a concurrent stop), hence the counter instead.
            return False
        self._volume = volume
        await self._persist_session()
        return True

    async def resume_from_persisted(
        self, *, source: str, locator: str, title: str, paused: bool, volume: float,
    ) -> bool:
        """Boot-time resume — restarts the track a restart interrupted, from position
        0 (never mid-frame, same limitation `_advance`'s lazy-resolve
        already has). Returns `False` if the persisted track no longer
        resolves (deleted, region-locked); the caller falls back to
        `on_track_finished()` to pick up wherever the ACTUAL queue
        continues, exactly like a stale queued track already does."""
        self._volume = volume
        await self._voice.set_volume(volume)
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
        # R-88: the re-seed seed, same moment as the dedup slot -- a
        # resumed track is the most recent thing that played.
        self._last_played = self._now_playing
        self._paused = False
        try:
            await self._voice.play(
                resolved[0].stream_url, duration_seconds=resolved[0].duration_seconds,
                # Same seekable-vs-live forwarding as `commit_pending`.
                resumable=resolved[0].duration_seconds is not None,
            )
        except Exception:
            # The same dead-voice teardown as `_advance` — a boot-time resume into a voice
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
            await self._voice.pause()
            self._paused = True
        await self._persist_session()
        return True

    @property
    def voice_channel_id(self) -> str:
        return self._voice_channel_id

    def seed_last_played(self, *, source: str, locator: str, title: str) -> None:
        """Boot-time only (R-88): restores the persisted `last_played_*`
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

    def adopt_persisted_now_playing(self, *, source: str, locator: str, title: str) -> None:
        """Boot-time reconcile ONLY: the
        controller outlived nora's restart and is STILL PLAYING on the
        connection the hub just adopted. Record the persisted track as
        now-playing WITHOUT a `voice.play` — replaying it would cut the
        live audio, and the `track-finished` the controller owes for it is
        what advances the queue (the hub's expected playback id already
        matches it, so the event passes the staleness probe). The
        played-history and last-played slots are set exactly as a real
        start would set them, so autoplay dedups this track and can
        re-seed from it."""
        self._now_playing = QueuedTrack(
            id=uuid4(), guild_id=self._guild_id, position=-1, source=source,
            url=locator, title=title, added_by_person_id="", added_at=self._clock(),
        )
        self._played_locators.add(locator)
        self._last_played = self._now_playing
        self._paused = False

    async def stop(self) -> None:
        """END-MUSIC: clears the whole
        queue, stops playback, and deletes the persisted session row — but
        does NOT leave the voice channel (`/music stop` ≠ `/discord exit`;
        the controller keeps the connection, e.g. for the wake path). This
        is the one transition that actually ENDS the session, as opposed to
        every other one just changing what's in it.

        `self._session.delete` runs in a `finally`
        around `self._voice.stop()` — a controller outage
        (`VoiceControllerUnavailable`) must not leave the persisted row
        behind for the next boot to wrongly resume. The original exception
        still propagates to the caller (`MusicPlayerManager`/the command
        layer decide how to report it); this only guarantees the row is
        gone either way."""
        self._cancel_prewarm()
        await self._queue.clear(self._guild_id)
        self._now_playing = None
        # R-88: a stop ENDS the session -- the re-seed seed dies with it.
        # A later `/music play` starts a fresh session with no seed, which
        # is the correct "nothing to autoplay from" case.
        self._last_played = None
        self._paused = False
        self._teardown_count += 1
        try:
            await self._voice.stop()
        finally:
            await self._session.delete(self._guild_id)

    async def leave(self) -> None:
        """LEAVE-VOICE: end-music AND
        destroy the voice connection — `/discord exit`, an emptied channel,
        a voice-death event. `disconnect` tolerates a connection that is
        already gone controller-side (the controller saw the death first),
        so this is safe on a half-torn session."""
        await self.stop()
        await self._voice.disconnect()

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
        """The composed advance for callers holding no guild lock; the
        manager drives the three phases itself."""
        pending = await self._advance_prepare()
        if pending is None:
            return
        ok, playback_id, teardown_count = await self.commit_pending(pending)
        await self.finish_pending(
            pending, ok=ok, playback_id=playback_id, teardown_count_before=teardown_count,
        )

    async def _advance_prepare(self) -> _PendingPlay | None:
        """The advance's DECISION phase — everything except the outbound
        play POST (the manager holds its guild lock through this, releases it
        for `commit_pending`'s HTTP round-trip, and re-acquires for `finish_pending`).

        `QueuedTrack.url` is always a `locator` (`ResolvedTrack`'s own
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
        only work left for it is the same `voice.play()` any queued track
        gets."""
        while True:
            # Captured before `pop_next` overwrites it -- the SEED autoplay
            # recommends from, once the queue turns out empty below
            # Falls back to `_last_played` (R-88): a boot-time resume that found nothing
            # playing (autoplay's chain had already ended before the
            # restart) still has a seed to re-seed the chain from, instead
            # of sitting silent in a live voice channel. `None` on the very
            # first track of a session (nothing has played yet to seed
            # from), which is the correct "nothing to autoplay from" case.
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
                # Without this line, a queue that empties and stays empty has
                # nothing distinguishing WHY: disabled, no seed, or a pick
                # attempt that came back empty.
                log.info(
                    "autoplay check for guild %s: enabled=%s has_seed=%s",
                    self._guild_id, self._autoplay_enabled, previous is not None,
                )
                if (
                    self._autoplay_enabled and previous is not None
                    and await self._enqueue_autoplay_pick(previous)
                ):
                    continue  # `pop_next` above picks it right back up
                await self._persist_session()
                return None
            resolved: Sequence[ResolvedTrack]
            cached = self._prewarm_streams.pop(self._now_playing.url, None)
            if cached is not None:
                resolved = (cached,)
            else:
                resolved = await self._source.resolve(query_or_url=self._now_playing.url)
            if resolved:
                # Recorded HERE, right before the stream starts -- the
                # played history autoplay's pick selection dedups from
                # A track that failed to resolve (the stale-entry warning path below)
                # is never recorded: it never played, so it has no right
                # to occupy a dedup slot.
                self._played_locators.add(self._now_playing.url)
                # R-88: the re-seed seed. Recorded at the same moment as
                # the dedup slot -- a track that actually started is the
                # most recent thing that played, and must survive the
                # queue emptying (unlike `_now_playing`, which is cleared
                # by the next `_advance`).
                self._last_played = self._now_playing
                return _PendingPlay(
                    track=self._now_playing,
                    stream_url=resolved[0].stream_url,
                    duration_seconds=resolved[0].duration_seconds,
                    # Prewarm only when this track is the LAST one in the
                    # queue: if more remain, autoplay will never fire at
                    # its end and the network calls would be pure waste.
                    schedule_prewarm=bool(
                        lookahead and upcoming_count is not None and upcoming_count <= 1
                    ),
                )
            log.warning(
                "queued track no longer resolves, skipping: %s (%s)",
                self._now_playing.title, self._now_playing.url,
            )

    async def commit_pending(self, pending: _PendingPlay) -> tuple[bool, str | None, int]:
        """The advance's OUTBOUND phase — the `voice.play` POST, the one
        call in this path that can hang on a real network. The manager
        runs it with the guild lock RELEASED: a hanging audio-controller
        must not block `/music skip`, `/music stop` or `/discord exit` for
        the guild, which holding the lock across the round-trip would do.

        Serialized per player (`_commit_lock`) so two advances whose
        prepares overlapped still reach the controller in the order nora
        decided them (replace semantics make the LAST arrival win).
        Returns `(False, None, teardown_count)` (and logs) when the
        connection is dead — the teardown itself is `finish_pending`'s,
        back under the lock. The second element of a `(True, ...)` return
        is the `playback_id` THIS play landed as;
        the third is `self._teardown_count` as measured BEFORE the play
        was even attempted — `finish_pending`'s own re-check needs both,
        see there."""
        teardown_count = self._teardown_count
        async with self._commit_lock:
            try:
                await self._voice.play(
                    pending.stream_url, duration_seconds=pending.duration_seconds,
                    # Forward the resolve step's own seekable-vs-live signal — a
                    # `ResolvedTrack` with no known duration is a
                    # live/non-seekable source (yt-dlp reports no `duration`
                    # for a live stream), which gets no resume after speech
                    # preemption instead of restarting from 0.
                    resumable=pending.duration_seconds is not None,
                )
            except Exception:
                # The voice connection is dead (a drop the controller
                # reports as `voice-death`, or one it hasn't noticed yet).
                # Nothing to resume without a
                # fresh connection; `finish_pending` ends the session the
                # way a stop does and a human `/music play` joins anew.
                log.exception(
                    "voice connection dead for guild %s; tearing down the session",
                    self._guild_id,
                )
                return False, None, teardown_count
        return True, getattr(self._voice, "playback_id", None), teardown_count

    async def finish_pending(
        self, pending: _PendingPlay, *, ok: bool,
        playback_id: str | None = None, teardown_count_before: int | None = None,
    ) -> None:
        """The advance's COMMIT phase, back under the guild lock.

        `ok=False` is the dead-connection teardown (R-65). The `ok=True`
        re-check covers the race the released lock opens: while the POST was
        in flight with the lock released, a skip may have advanced PAST
        `pending.track` or a stop/teardown ended the session. A NEWER
        pending play supersedes this one controller-side (replace
        semantics) — nothing to do; but a stop leaves NOTHING to
        supersede this track's just-started audio, so it is stopped
        explicitly or it plays on as a zombie nora no longer tracks.

        `self._now_playing is None` alone is NOT
        enough to conclude "nothing supersedes it" — `stop()`/`leave()`
        never disconnect the underlying voice connection, so a `/music
        stop` immediately followed by a fresh `/music play` for the SAME
        guild reuses the very same `VoiceSession` (`VoiceControllerHub`
        caches it per guild). THIS player is orphaned at that point
        (popped from the manager's `_players`), but its own `self._voice`
        is the SAME object the new player is now driving — an unconditional
        `self._voice.stop()` here would silence the NEW session's own
        just-started track, not a zombie of this one's. `playback_id` (the
        id THIS pending's play landed as, from `commit_pending`) is
        compared against the connection's CURRENT one: unchanged means
        nothing newer has played on it since, so the stop is genuinely this
        pending's own leftover audio; changed means someone else's play —
        old or new player alike — already superseded it, and stopping now
        would silence the wrong playback.

        The `ok=False` side has the same hazard: `self._queue`/`self._session`
        are keyed by `guild_id`, not by connection — a belated failure's
        `queue.clear()`/`session.delete()` would erase a BRAND NEW
        session's rows, not just this one's stale leftovers, if a fresh
        `/music play` already wrote them. `teardown_count_before` (from
        `commit_pending`) answers "did an EXPLICIT stop/leave already run
        on THIS player while the play was in flight" — if so, that call
        already did this player's real cleanup, and this belated failure
        has nothing legitimate left to clean up (the row, if any, belongs
        to whoever came after); skip entirely rather than guess."""
        if not ok:
            if (
                teardown_count_before is not None
                and self._teardown_count != teardown_count_before
            ):
                log.info(
                    "guild %s: a stale play failure landed after an explicit "
                    "stop/leave already ran — skipping its redundant (and "
                    "possibly guild-row-destroying) cleanup",
                    self._guild_id,
                )
                return
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
                # A transient DB failure here must not prevent the
                # `_session_dead` flag below — the manager's
                # `_after_transition` evicts the player on it, and a
                # stranded session row is recovered by the next
                # boot-time resume scan instead of leaving a zombie.
                log.warning("session delete after dead voice failed", exc_info=True)
            # Mark the player dead so the manager drops it from `_players` on this
            # transition — otherwise a `/music play` before the idle
            # timeout reuses this dead player and fails again, and
            # the voice-death monitor can double-teardown.
            self._session_dead = True
            return
        if self._session_dead or self._now_playing is not pending.track:
            current_playback_id = getattr(self._voice, "playback_id", None)
            if self._now_playing is None and (
                playback_id is None or current_playback_id == playback_id
            ):
                try:
                    await self._voice.stop()
                except Exception:
                    log.warning(
                        "could not kill superseded playback for guild %s",
                        self._guild_id, exc_info=True,
                    )
            return
        await self._persist_session()
        if pending.schedule_prewarm:
            self._schedule_prewarm(pending.duration_seconds)

    def _schedule_prewarm(self, duration_seconds: float | None) -> None:
        """Arms a timer that picks the next autoplay track `prewarm_seconds`
        BEFORE this track's expected end (R-64). The completed pick lands in `self._prewarmed` for
        `_advance` to consume with zero network calls. Any earlier prewarm
        was for a DIFFERENT track and is cancelled/replaced, never consumed
        by this one.

        Cancels the previous timer/task FIRST: an advance (natural track-end
        or a skip) happens while the last track's prewarm timer may still be
        pending or its task still in flight. Left alone, the old timer fires
        and spawns a second `_prewarm_pick` for the SAME new track (the
        locator check passes — `_prewarm_for_locator` was already updated),
        issuing a wasted `related()` network call and racing to fill
        `_prewarm_streams`."""
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
            log.info("autoplay prewarm for %s superseded by a newer prewarm", previous.url)
            return  # a newer prewarm replaced this one; its result is stale
        if self._now_playing is None or self._now_playing.url != self._prewarm_for_locator:
            # This pick (found or not) is DISCARDED here with nothing landing
            # anywhere else -- a prewarm that outlasted the track it was for
            # would otherwise lose a valid pick with no trace. Logged so it
            # is distinguishable from "related() found nothing".
            log.info(
                "autoplay prewarm for %s discarded as stale (now_playing=%s, pick=%s)",
                previous.url, self._now_playing.url if self._now_playing else None,
                pick.locator if pick is not None else None,
            )
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
        track-end); otherwise `related()` is called now. Either way, played-history dedup
        (R-63) applies: a pick already played this session falls through to a fresh one, and an
        all-repeats Mix returns `None` exactly like an empty one — never
        retried, never substituted with a guess."""
        prewarmed = self._prewarmed
        if self._prewarm_done and prewarmed is None:
            # A completed prewarm answered EMPTY — but that is NOT the
            # final answer (R-67): the prewarm ran up to
            # `autoplay_prewarm_seconds` BEFORE the track ended, and a
            # transient YouTube failure (or a Mix that was momentarily
            # empty) at that earlier moment must not permanently kill the
            # chain. Fall through to the live `related()` call below —
            # ONE retry at the actual track end. If THAT also answers
            # empty, the chain ends (R-62's never-retried discipline holds for the
            # live answer). The live retry is bounded to one, so it cannot loop.
            self._prewarm_done = False
        if prewarmed is not None:
            self._prewarmed = None
            self._prewarm_done = False
            if prewarmed.locator not in self._played_locators:
                log.info("autoplay pick: consumed prewarmed %s", prewarmed.locator)
                return prewarmed
            # The prewarmed pick was played in the meantime (a skip, a
            # user re-queue) -- fall through to a fresh pick below.
            log.info(
                "autoplay pick: prewarmed %s already played this session, falling "
                "through to a fresh related() call", prewarmed.locator,
            )
        # Timed: a slow or contended `related()` call
        # here is otherwise invisible -- the caller only ever sees the final
        # pick or `None`, never how long the live yt-dlp extraction took.
        started = self._clock()
        related = await self._source.related(locator=previous.url)
        elapsed = (self._clock() - started).total_seconds()
        if not related:
            log.info(
                "autoplay pick: related() for %s returned EMPTY after %.1fs",
                previous.url, elapsed,
            )
            return None
        pick = next(
            (t for t in related if t.locator not in self._played_locators),
            None,
        )
        if pick is None:
            log.info(
                "autoplay pick: related() for %s returned %d candidate(s) after "
                "%.1fs, but ALL already played this session",
                previous.url, len(related), elapsed,
            )
        else:
            log.info(
                "autoplay pick: related() for %s returned %d candidate(s) after "
                "%.1fs, picked %s",
                previous.url, len(related), elapsed, pick.locator,
            )
        return pick

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
