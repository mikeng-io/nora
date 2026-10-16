"""`VoiceControllerHub` — the per-guild voice-session registry nora's
command and application layers talk to.

One `HttpVoiceSession` per guild, cached on first join. The hub is also
where two pieces of cross-request state live, because both are scoped to
"what nora currently believes about the controller", not to any one call:

- **boot reconciliation** — a nora restart loses the generation it
  minted, but the controller's connection survives it. A `/voice/join` that
  comes back 409 means "already connected"; the hub then reads
  `/voice/status` and ADOPTS the live generation when it points at the
  channel nora asked for (and only then — connected elsewhere re-raises).
- **event bookkeeping** — the expected `playback_id` per guild
  (read off the cached session) and the exactly-once `event_id` dedupe the
  `/app/v1/voice/event` consumer relies on.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Iterable, Mapping
from typing import Any
from uuid import uuid4

from nora.domain.playback_liveness import PlaybackReading
from nora.infra.voice.http_controller import (
    GenerationConflict,
    HttpVoiceSession,
    VoiceControllerClient,
    VoiceControllerError,
    VoiceControllerNoConnection,
    VoiceControllerUnavailable,
)

log = logging.getLogger("nora.infra.voice")

#: Bound on the event-id dedupe window (FIFO), PER GUILD. Controller callbacks
#: retry from a ≤256-event outbox each, so 1024 covers several full
#: replays of one controller's outbox.
#:
#: Multiplied by the number of guilds, because the deque is process-global
#: while the topology is one controller per guild. Event ids
#: are `randomUUID()` per controller so they cannot collide, and sharing one
#: deque is right — but sharing one BOUND is not: at four guilds a flat 1024
#: is exactly one outbox, and at five a guild's ids can be evicted while its
#: own outbox is still replaying them.
_EVENT_ID_BOUND_PER_GUILD = 1024


class VoiceControllerHub:
    def __init__(self, clients: Mapping[str, VoiceControllerClient]) -> None:
        # ONE CLIENT PER GUILD. Discord's
        # limit is one voice connection per bot token PER GUILD, so a guild is
        # the unit of ownership — of a CONNECTION, not of a process. One
        # sidecar serves every guild by instantiating its per-guild stack once
        # each; the entries here may therefore all name the same endpoint.
        #
        # An unlisted guild has no controller and therefore no voice at all —
        # this mapping IS the join allow-list (`allowed_guilds`).
        self._clients = dict(clients)
        self._sessions: dict[str, HttpVoiceSession] = {}
        # WHICH CHANNEL each cached session is connected to. `HttpVoiceSession`
        # does not carry it, and the cache is keyed by GUILD, and any channel in
        # a guild can be joined, so a cache hit does not imply the same
        # channel and `join` has to compare.
        self._channels: dict[str, str] = {}
        self._seen_event_ids: deque[str] = deque()
        self._seen_event_id_set: set[str] = set()
        self._event_id_bound = _EVENT_ID_BOUND_PER_GUILD * max(len(self._clients), 1)
        # The highest `event_seq` the consumer has successfully processed,
        # PER GUILD. Read against `/voice/status`'s `last_event_seq`
        # on adoption: a controller AHEAD of it holds events nora never saw.
        #
        # Per guild, and it has to be: each controller mints
        # its own sequence from 1, so a single counter would read the second
        # guild's first event as a jump backwards and the first guild's next
        # as a gap — a warning that exists to mean "events were lost while
        # nora was down" would have fired on every healthy two-guild boot.
        self._last_event_seq: dict[str, int] = {}

    @property
    def guild_ids(self) -> frozenset[str]:
        """The guilds a controller exists for — the join allow-list."""
        return frozenset(self._clients)

    def _client_for(self, guild_id: str) -> VoiceControllerClient:
        client = self._clients.get(guild_id)
        if client is None:
            raise VoiceControllerUnavailable(
                f"no audio-controller is configured for guild {guild_id} "
                "([speech.tts] sidecars)"
            )
        return client

    async def join(self, guild_id: str, channel_id: str) -> HttpVoiceSession:
        """The cached session if this guild already has one; otherwise
        `/voice/join`, adopting the controller's existing connection on a
        409 that turns out to BE the channel asked for."""
        cached = self._sessions.get(guild_id)
        if cached is not None:
            if self._channels.get(guild_id) == channel_id:
                return cached
            # A DIFFERENT channel is not a cache hit — it is the conflict the
            # controller would have raised, and nora must not swallow it by
            # answering with the connection it already holds.
            #
            # THE BUG THIS REPLACES: returning `cached` here played the
            # caller's music out of the channel Nora was ALREADY in, while the
            # music player persisted the CALLER's channel. `channel-emptied`
            # then never matched (the player's channel was not the connected
            # one) so auto-leave stopped firing, and the next boot resumed
            # against the channel nobody was in. `/discord join` escaped it
            # only by having its own `session_for` check; `/music play` and
            # the agent tool bridge did not.
            #
            # Raised as the controller's own `connected_elsewhere` so every
            # caller that already handles a join conflict is unchanged.
            raise GenerationConflict(
                f"already connected to channel {self._channels.get(guild_id)} "
                f"in guild {guild_id}, not {channel_id}"
            )
        try:
            state = await self._client_for(guild_id).join(
                guild_id, channel_id, request_id=str(uuid4()),
            )
        except GenerationConflict:
            state = await self._adopt_existing(guild_id, channel_id)
        session = self._build_session(guild_id, state)
        self._sessions[guild_id] = session
        # The channel the controller reports, not the one asked for: an
        # adopted 409 lands on whatever it was already connected to.
        reported = state.get("channel_id")
        self._channels[guild_id] = (
            reported if isinstance(reported, str) and reported else channel_id
        )
        return session

    async def _adopt_existing(self, guild_id: str, channel_id: str) -> dict[str, Any]:
        """Boot reconciliation: the join 409'd because the controller
        is already connected. Adopt its generation ONLY when the live
        connection is to the very channel requested — anything else is a
        real conflict and climbs back out as one."""
        status = await self._client_for(guild_id).status(guild_id)
        if status.get("voice") == "connected" and status.get("channel_id") == channel_id:
            log.info(
                "voice join for guild %s: adopting the controller's existing "
                "connection (channel %s)", guild_id, channel_id,
            )
            self._warn_on_event_seq_gap(guild_id, status)
            return status
        raise GenerationConflict(
            f"controller is connected elsewhere "
            f"(voice={status.get('voice')!r}, channel_id={status.get('channel_id')!r})"
        )

    def _warn_on_event_seq_gap(self, guild_id: str, status: dict[str, Any]) -> None:
        """Reconciliation guard: the controller's `last_event_seq`
        ahead of the last seq nora consumed means events were emitted
        while nora was down (or outbox deliveries were lost) — nora's
        queue/session state may be behind the controller's playback, and
        only the outbox's own re-delivery closes that. A WARNING, never an
        exception: adoption itself is still the right move. Skipped
        silently when either side carries no seq (an older controller's
        status, or no event consumed yet — nothing to compare)."""
        controller_seq = status.get("last_event_seq")
        # THIS guild's bookmark against THIS guild's controller — each mints
        # its own sequence, so comparing across guilds would compare two
        # unrelated counters (see `_last_event_seq`).
        consumed = self._last_event_seq.get(guild_id)
        if (
            not isinstance(controller_seq, int) or isinstance(controller_seq, bool)
            or consumed is None
            or controller_seq <= consumed
        ):
            return
        log.warning(
            "voice event gap for guild %s: controller last_event_seq=%d but nora "
            "last consumed %d — %d event(s) were emitted while nora was not "
            "consuming; music state may be stale until the outbox re-delivers",
            guild_id, controller_seq, consumed, controller_seq - consumed,
        )

    def _build_session(self, guild_id: str, state: dict[str, Any]) -> HttpVoiceSession:
        generation = state.get("generation")
        if not isinstance(generation, str) or not generation:
            raise VoiceControllerUnavailable(
                "join succeeded but the state snapshot carries no generation"
            )
        session = HttpVoiceSession(
            self._client_for(guild_id), guild_id=guild_id, generation=generation,
        )
        playback_id = state.get("playback_id")
        session.playback_id = playback_id if isinstance(playback_id, str) else None
        return session

    async def exit(self, guild_id: str) -> bool:
        """Drop the cached session and exit the connection. `False` when
        there was no session, or the exit itself failed (the cache is
        dropped either way — a dead connection must not be reused)."""
        session = self._sessions.pop(guild_id, None)
        # Cleared with the session, always: a stale channel left here would
        # make the next `join` to that channel look like a cache hit for a
        # connection that no longer exists.
        self._channels.pop(guild_id, None)
        if session is None:
            return False
        try:
            await session.disconnect()
        except VoiceControllerError as exc:
            log.warning("voice exit for guild %s failed: %s", guild_id, exc)
            return False
        return True

    def session_for(self, guild_id: str) -> HttpVoiceSession | None:
        return self._sessions.get(guild_id)

    def drop(self, guild_id: str) -> None:
        """Forget the session WITHOUT an exit call — for the controller's
        own `voice-death` event, where the connection is already gone.
        (Named `drop`, not `clear`: `clear` is a store method name and the
        store-caller gate must be able to resolve it.)"""
        self._sessions.pop(guild_id, None)
        self._channels.pop(guild_id, None)

    async def stream_speech(self, guild_id: str, audio: bytes, media_type: str) -> bool:
        """`POST /stream` for the guild's current generation. NEVER raises —
        `False` (with a log saying why) when there is no session, the
        generation is stale, or the controller is unavailable. A
        `GenerationConflict` also drops the cached session: the controller
        no longer recognises it, so nothing may stream through it again."""
        session = self._sessions.get(guild_id)
        if session is None:
            log.info("speech for guild %s dropped: no voice session", guild_id)
            return False
        try:
            await self._client_for(guild_id).stream(
                session.generation, audio,
                media_type=media_type, guild_id=guild_id, request_id=str(uuid4()),
            )
        except GenerationConflict as exc:
            log.warning(
                "speech for guild %s dropped: %s — dropping the stale session",
                guild_id, exc,
            )
            self.drop(guild_id)
            return False
        except VoiceControllerError as exc:
            log.warning("speech for guild %s failed: %s", guild_id, exc)
            return False
        return True

    async def read_playback(self, guild_id: str) -> PlaybackReading:
        """The guild's live playback state, for the manager's liveness watch.

        No cached session means nora holds no connection for this guild, so
        there is nothing to ask — reported as UNREACHABLE rather than idle:
        "I have nobody to ask" is not the same claim as "it is not playing",
        and collapsing the two is the fail-open shape this watch exists to
        remove."""
        session = self._sessions.get(guild_id)
        if session is None:
            return PlaybackReading.unreachable()
        return await session.read_playback()

    def expected_playback_id(self, guild_id: str) -> str | None:
        session = self._sessions.get(guild_id)
        return session.playback_id if session is not None else None

    def generation_for(self, guild_id: str) -> str | None:
        """The generation nora currently believes is live for the guild —
        the manager's B8 staleness probe (`generation_probe`) reads this
        UNDER its per-guild lock before letting a lifecycle event tear
        anything down."""
        session = self._sessions.get(guild_id)
        return session.generation if session is not None else None

    def seen_event(self, event_id: str) -> bool:
        """Pure check half of the exactly-once dedupe — the consumer asks
        this FIRST and only commits via `consume_event_id` once the event
        was actually processed (B7: a failed delivery stays retryable, so
        nothing may be marked consumed before its transition succeeded)."""
        return event_id in self._seen_event_id_set

    def last_event_seq(self, guild_id: str) -> int | None:
        """This GUILD's bookmark. A guild nora has consumed nothing for
        answers `None`, which reads as "no baseline yet" — never as a gap."""
        return self._last_event_seq.get(guild_id)

    def note_event_seq(self, guild_id: str, event_seq: int) -> None:
        """Record a successfully processed event's `event_seq` —
        the consumption-side bookmark `_warn_on_event_seq_gap` and the
        consumer's own per-event gap warning read. Monotonic: a replayed
        or out-of-order delivery never moves it backwards."""
        last = self._last_event_seq.get(guild_id)
        if last is None or event_seq > last:
            self._last_event_seq[guild_id] = event_seq

    async def adopt_boot_voice_sessions(self, voice_guild_ids: Iterable[str]) -> None:
        """Boot-time reconciliation for guilds with NO persisted music session.

        `resume_persisted_sessions` (music_commands.py) only calls
        `hub.join` for a guild that has a persisted MUSIC row — a
        wake-only/speech-only guild never calls it at all, so the
        controller's boot-joined connection (its own durable desired-state)
        sits un-adopted in this process: `stream_speech` then
        silently returns `False` for every wake reply until a human
        happens to run `/discord join` or `/music play`.

        **The CONTROLLER says which channel, not config.** Config names guilds, not
        channels: the controller owns `desired_state` and is the only thing that
        knows where it actually reconnected. A config read would re-join the
        CONFIGURED channel even when the controller had come back up somewhere
        else.

        A guild whose controller reports anything but `connected` is skipped,
        not joined: boot adoption exists to pick up a connection that already
        exists, and dragging Nora into a voice channel nobody asked for is the
        opposite of that.

        Idempotent with an already-cached session — `hub.join` short-
        circuits on the cache — and safe to call after
        `resume_persisted_sessions`, which may have already populated it.
        Best-effort: a controller unreachable at this exact boot instant
        must not fail nora's boot; the next `/discord join`, `/music play`,
        or voice-channel send retries the same `hub.join` path."""
        for guild_id in voice_guild_ids:
            if not guild_id:
                continue
            try:
                status = await self._client_for(guild_id).status(guild_id)
                if status.get("voice") != "connected":
                    continue
                channel_id = status.get("channel_id")
                if not isinstance(channel_id, str) or not channel_id:
                    log.warning(
                        "boot voice adoption for guild %s: controller reports "
                        "connected with no channel_id — nothing to adopt",
                        guild_id,
                    )
                    continue
                await self.join(guild_id, channel_id)
            except VoiceControllerNoConnection:
                # The controller holds no connection for this guild, which,
                # since one process serves several, is the ordinary
                # COLD-BOOT state, not a fault: there is nothing to adopt.
                #
                # INFO for exactly the reason the rest of this file logs
                # loudly: a WARNING that fires on EVERY boot is one people
                # learn to scroll past, and then the boot where adoption really
                # did fail looks like the twenty before it.
                log.info(
                    "boot voice adoption for guild %s: the controller holds no "
                    "connection for it — nothing to adopt", guild_id,
                )
            except VoiceControllerError as exc:
                log.warning(
                    "boot voice adoption for guild %s failed: %s — will retry "
                    "on the next join/send", guild_id, exc,
                )

    def consume_event_id(self, event_id: str) -> bool:
        """The COMMIT half of the exactly-once dedupe for controller
        callbacks: called only AFTER an event's transition
        succeeded — a failure leaves the id unconsumed so the controller's
        retry is processed, never acknowledged as a duplicate. `True` the
        first time an `event_id` is committed, `False` on any replay within
        the bounded FIFO window."""
        if event_id in self._seen_event_id_set:
            return False
        self._seen_event_ids.append(event_id)
        self._seen_event_id_set.add(event_id)
        while len(self._seen_event_ids) > self._event_id_bound:
            evicted = self._seen_event_ids.popleft()
            self._seen_event_id_set.discard(evicted)
        return True
