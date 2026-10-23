"""`VoiceControllerClient` / `HttpVoiceSession` — nora's HTTP half of the
audio-controller contract.

The audio-controller owns the Discord voice connections; nora keeps every
decision and never touches the voice socket. Every verb here is one POST
against the controller's control plane (`GET` for `/voice/status`), and every
failure maps onto exactly one of the three exception types below — the R-27
discipline applied to a network port: a wiring gap is `unavailable`, never a
silent wrong answer. NOTHING else raises out of this module into callers.

Response-shape contract (controller-side, pinned): a success body is
`{"ok": true, "request_id": ..., "state": {...}}`; an error body is
`{"error": "<reason>", "request_id": ..., "state": ...}`. The control verbs
return the parsed `state` snapshot dict; `status()` returns the whole GET
body (the same snapshot plus `last_event_seq`, `streaming`, `last_error`,
`wake`, `pump`).
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import aiohttp

from nora.domain.playback_liveness import PlaybackReading
from nora.infra.service_auth import service_auth_headers

log = logging.getLogger("nora.infra.voice")


class VoiceControllerError(Exception):
    """Base for every failure this module reports — callers catch this and
    nothing else."""


class VoiceControllerUnavailable(VoiceControllerError):  # noqa: N818 — pinned contract name
    """The controller could not be reached or could not answer: transport
    errors, timeouts, 5xx, and success answers whose body does not parse or
    lacks the pinned shape. Fail-closed — the caller reports «I couldn't
    check», never guesses a state."""


class GenerationConflict(VoiceControllerError):  # noqa: N818 — pinned contract name
    """HTTP 409 — the command's `generation` is not the controller's current
    one (the connection was exited, replaced, or never joined), or an
    arbiter-state rejection such as `pause_during_speech`. Carries the
    controller's own `error` reason string: `generation_conflict` is
    recoverable (reconcile via `/voice/status` and retry), an arbiter
    rejection is NOT — the two must never be conflated."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class VoiceControllerRejected(VoiceControllerError):  # noqa: N818 — pinned contract name
    """HTTP 400 — the controller understood the request and refused it
    (`wrong_guild`, `percent_out_of_range`). Carries the controller's own
    `error` reason string."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class VoiceControllerNoConnection(VoiceControllerUnavailable):
    """HTTP 404 `unknown_guild` — the controller holds NO connection for this
    guild. Since one process serves several guilds this is
    an ordinary answer, not an outage: on a cold boot every guild answers it.

    Subclasses `VoiceControllerUnavailable` ON PURPOSE, so every existing
    handler keeps its fail-closed behaviour unchanged and only the callers
    that care — boot adoption, which must not log a WARNING on every start —
    need to know the difference."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class VoiceControllerClient:
    """One HTTP client for the whole control plane. `base_url` is the
    `[speech.tts] sidecars` config value — that endpoint IS the
    audio-controller's base.

    With no injected `session`, each call opens its own
    `aiohttp.ClientSession` (a per-call session):
    the call volume is a handful per track, and no session lifecycle
    leaks across reconnects."""

    def __init__(
        self,
        base_url: str,
        *,
        session: aiohttp.ClientSession | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def join(
        self, guild_id: str, channel_id: str, *, request_id: str,
    ) -> dict[str, Any]:
        """`POST /voice/join` — idempotent when already in that channel; 409
        `connected_elsewhere` when it is not."""
        return await self._control(
            "/voice/join",
            {"guild_id": guild_id, "channel_id": channel_id, "request_id": request_id},
        )

    async def exit(self, generation: str, *, guild_id: str, request_id: str) -> dict[str, Any]:
        """`POST /voice/exit` — stop everything and destroy the connection."""
        return await self._control(
            "/voice/exit",
            {"guild_id": guild_id, "generation": generation, "request_id": request_id},
        )

    async def play(
        self,
        generation: str,
        stream_url: str,
        *,
        guild_id: str,
        title: str | None = None,
        duration_seconds: float | None = None,
        resumable: bool | None = None,
        request_id: str,
    ) -> dict[str, Any]:
        """`POST /music/play` — REPLACE semantics: the controller has no
        upcoming queue; this supersedes whatever the music slot holds.
        `resumable=False` marks a live/non-seekable source (§5 position
        policy: speech preemption ENDS it instead of reviving from 0);
        `None` (every caller today) leaves the controller's default."""
        body: dict[str, Any] = {
            "guild_id": guild_id, "generation": generation,
            "stream_url": stream_url, "request_id": request_id,
        }
        if title is not None:
            body["title"] = title
        if duration_seconds is not None:
            body["duration_seconds"] = duration_seconds
        if resumable is not None:
            body["resumable"] = resumable
        return await self._control("/music/play", body)

    async def stop_music(
        self, generation: str, *, guild_id: str, request_id: str,
    ) -> dict[str, Any]:
        """`POST /music/stop` — stop the music slot, STAY connected. A no-op
        (200) when idle."""
        return await self._control(
            "/music/stop",
            {"guild_id": guild_id, "generation": generation, "request_id": request_id},
        )

    async def pause(
        self, generation: str, *, guild_id: str, request_id: str,
    ) -> dict[str, Any]:
        """`POST /music/pause` — 409 `pause_during_speech` when speech holds
        the pipeline (surfaces as `GenerationConflict`)."""
        return await self._control(
            "/music/pause",
            {"guild_id": guild_id, "generation": generation, "request_id": request_id},
        )

    async def resume(
        self, generation: str, *, guild_id: str, request_id: str,
    ) -> dict[str, Any]:
        return await self._control(
            "/music/resume",
            {"guild_id": guild_id, "generation": generation, "request_id": request_id},
        )

    async def set_volume(
        self, generation: str, percent: int, *, guild_id: str, request_id: str,
    ) -> dict[str, Any]:
        """`POST /music/volume` — `percent` is 0-200 (400
        `percent_out_of_range` otherwise)."""
        return await self._control(
            "/music/volume",
            {"guild_id": guild_id, "generation": generation,
             "percent": percent, "request_id": request_id},
        )

    async def stream(
        self,
        generation: str,
        audio: bytes,
        *,
        media_type: str,
        guild_id: str | None = None,
        request_id: str,
    ) -> dict[str, Any]:
        """`POST /stream` — the ONLY speech path: synthesized audio (≤2 MB)
        base64'd into the body; the controller hard-cuts it over music.
        409 for a dead generation, 503 `speech_queue_full` when its bounded
        speech queue is full (surfaces as `VoiceControllerUnavailable`)."""
        body: dict[str, Any] = {
            "generation": generation,
            "audio_b64": base64.b64encode(audio).decode("ascii"),
            "media_type": media_type,
            "request_id": request_id,
        }
        if guild_id is not None:
            body["guild_id"] = guild_id
        return await self._control("/stream", body)

    async def status(self, guild_id: str) -> dict[str, Any]:
        """`GET /voice/status` — the full snapshot (voice state, generation,
        channel, playback id, player, speech queue depth, `last_event_seq`,
        `streaming`, `last_error`, `wake`, `pump`) for ONE guild.

        The guild rides the query string because a GET carries no body;
        one controller process serves several, so an unqualified status could
        only answer for whichever it picked."""
        return await self._request("GET", f"/voice/status?guild_id={guild_id}", None)

    async def _control(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """A control POST's return is the `state` snapshot out of the
        success envelope. A 2xx WITHOUT it is a contract violation, reported
        as `unavailable` — never as success."""
        payload = await self._request("POST", path, body)
        state = payload.get("state")
        if not isinstance(state, dict):
            raise VoiceControllerUnavailable(
                f"{path} answered {payload!r} — no state snapshot in the success body"
            )
        return state

    async def _request(
        self, method: str, path: str, body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if self._session is not None:
            return await self._call(self._session, method, path, body)
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            return await self._call(session, method, path, body)

    async def _call(
        self,
        session: aiohttp.ClientSession,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            async with session.request(
                method, f"{self._base_url}{path}", json=body,
                headers=service_auth_headers(),
            ) as resp:
                try:
                    payload: Any = await resp.json(content_type=None)
                except Exception:  # noqa: BLE001 — any parse failure maps below
                    payload = None
                if 200 <= resp.status < 300:
                    if not isinstance(payload, dict):
                        raise VoiceControllerUnavailable(
                            f"{path} answered HTTP {resp.status} with an unparseable body"
                        )
                    return payload
                reason = payload.get("error") if isinstance(payload, dict) else None
                if not isinstance(reason, str) or not reason:
                    reason = f"http_{resp.status}"
                if resp.status == 409:
                    raise GenerationConflict(reason)
                if resp.status == 400:
                    raise VoiceControllerRejected(reason)
                if resp.status == 404 and reason == "unknown_guild":
                    raise VoiceControllerNoConnection(reason)
                raise VoiceControllerUnavailable(
                    f"{path} answered HTTP {resp.status} ({reason})"
                )
        except VoiceControllerError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise VoiceControllerUnavailable(
                f"{path}: {exc or type(exc).__name__}"
            ) from exc


class HttpVoiceSession:
    """The music player's `VoiceSession` over the control plane — one
    instance per (guild, generation), built by `VoiceControllerHub.join`.

    Deliberately does NOT import the `VoiceSession` Protocol from
    `nora.platform.discord.music_player`: `nora.infra` sits BELOW
    `nora.platform` in the layers contract, so the conformance is
    structural (the protocol's methods, awaited, same names).

    Every call mints a fresh `request_id` (the controller dedupes
    retries on it); `playback_id` is recorded from each play's state
    snapshot so the hub can recognise the controller's `track-finished` /
    `stream-failed` callbacks as current or stale.

    The generation can go STALE under a cached session (a controller
    restart re-joins under a new one). The hub and the manager's
    player hold THIS SAME object, so adopting the live generation here
    reconciles both at once: a verb that 409s `generation_conflict` reads
    `/voice/status`, adopts, and retries ONCE."""

    def __init__(
        self, client: VoiceControllerClient, *, guild_id: str, generation: str,
    ) -> None:
        self._client = client
        self._guild_id = guild_id
        self._generation = generation
        self.playback_id: str | None = None

    @property
    def generation(self) -> str:
        return self._generation

    async def read_playback(self) -> PlaybackReading:
        """One `/voice/status` sample, reduced to the liveness fields.

        `playback_id is not None` is true in five arbiter states and audible
        in at most one, so liveness is read from these fields instead.

        NEVER raises. An unreachable or unparseable controller comes back as
        `PlaybackReading.unreachable()`, which the rule turns into UNKNOWN —
        "I could not check" must not reach a caller looking like "not
        playing"."""
        try:
            status = await self._client.status(self._guild_id)
        except VoiceControllerError:
            return PlaybackReading.unreachable()
        if not isinstance(status, dict):
            return PlaybackReading.unreachable()
        player = status.get("player")
        if not isinstance(player, dict):
            return PlaybackReading.unreachable()

        def _int(value: object) -> int | None:
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        player_status = player.get("status")
        playback_id = status.get("playback_id")
        autopaused = status.get("autopaused")
        return PlaybackReading(
            reachable=True,
            status=player_status if isinstance(player_status, str) else None,
            position_ms=_int(player.get("position_ms")),
            # `None`, never `False`, when the controller does not serve the
            # field: "this build cannot tell me" must not read as "it is
            # fine".
            autopaused=autopaused if isinstance(autopaused, bool) else None,
            subscribers=_int(status.get("subscribers")),
            playback_id=playback_id if isinstance(playback_id, str) else None,
        )

    @property
    def guild_id(self) -> str:
        return self._guild_id

    async def _reconcile_generation(self) -> bool:
        """Adopt the controller's CURRENT generation after a 409
        `generation_conflict` (a restart or a replaced connection).
        `False` — and the original conflict climbs — when the controller
        has no live connection to adopt: an exited connection is not a
        stale generation, it is a gone one, and retrying against it would
        resurrect audio nora already tore down."""
        try:
            status = await self._client.status(self._guild_id)
        except VoiceControllerError:
            return False
        if status.get("voice") != "connected":
            return False
        generation = status.get("generation")
        if not isinstance(generation, str) or not generation:
            return False
        log.info(
            "voice session for guild %s: adopting generation %s after a "
            "generation conflict (controller restarted or re-joined)",
            self._guild_id, generation,
        )
        self._generation = generation
        playback_id = status.get("playback_id")
        self.playback_id = playback_id if isinstance(playback_id, str) else None
        return True

    async def _with_reconcile(
        self, verb: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Run a generation-scoped verb; on a `generation_conflict` 409
        reconcile and retry ONCE (a fresh `request_id` — the controller
        memoized the rejection under the first one). Arbiter rejections
        (`pause_during_speech`) are not staleness and propagate as-is."""
        try:
            return await verb(self._request_id())
        except GenerationConflict as exc:
            if exc.reason != "generation_conflict":
                raise
            if not await self._reconcile_generation():
                raise
            return await verb(self._request_id())

    async def play(
        self, stream_url: str, *,
        duration_seconds: float | None = None, resumable: bool | None = None,
    ) -> None:
        state = await self._with_reconcile(
            lambda request_id: self._client.play(
                self._generation, stream_url, guild_id=self._guild_id,
                duration_seconds=duration_seconds, resumable=resumable,
                request_id=request_id,
            )
        )
        playback_id = state.get("playback_id")
        self.playback_id = playback_id if isinstance(playback_id, str) else None

    async def pause(self) -> None:
        await self._with_reconcile(
            lambda request_id: self._client.pause(
                self._generation, guild_id=self._guild_id, request_id=request_id,
            )
        )

    async def resume(self) -> None:
        await self._with_reconcile(
            lambda request_id: self._client.resume(
                self._generation, guild_id=self._guild_id, request_id=request_id,
            )
        )

    async def stop(self) -> None:
        """`/music/stop`. TOLERATES a 409 `generation_conflict` WITHOUT a
        reconcile-retry: a generation the controller no longer recognises
        has no music slot to stop — the end state this call asks for
        already holds. That tolerance is what lets a dead-connection
        teardown complete locally instead of
        dying on a controller that is already gone."""
        try:
            await self._client.stop_music(
                self._generation, guild_id=self._guild_id, request_id=self._request_id(),
            )
        except GenerationConflict as exc:
            if exc.reason != "generation_conflict":
                raise
            log.info(
                "music stop for guild %s: generation already invalid — "
                "the music slot is definitionally stopped",
                self._guild_id,
            )
        self.playback_id = None

    async def set_volume(self, volume: float) -> None:
        """`MusicPlayer`'s 0.0-2.0 linear multiplier → the controller's
        0-200 percent."""
        percent = max(0, min(200, round(volume * 100)))
        await self._with_reconcile(
            lambda request_id: self._client.set_volume(
                self._generation, percent, guild_id=self._guild_id,
                request_id=request_id,
            )
        )

    async def disconnect(self) -> None:
        """`/voice/exit`. TOLERATES `GenerationConflict`: the connection
        being already gone controller-side (a voice-death the controller
        saw first, an exit someone else issued) is exactly the end state
        this call asks for."""
        try:
            await self._client.exit(
                self._generation, guild_id=self._guild_id, request_id=self._request_id(),
            )
        except GenerationConflict:
            log.info(
                "voice exit for guild %s: generation already invalid — treated as done",
                self._guild_id,
            )

    @staticmethod
    def _request_id() -> str:
        return str(uuid4())
