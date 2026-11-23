"""`VoiceControllerClient` / `HttpVoiceSession` — nora's HTTP half of the
audio-controller's `voice` contract (`nora.domain.service_api_voice`).

The audio-controller owns the Discord voice connections; nora keeps every
decision and never touches the voice socket. Every verb here is one operation
on `POST /v1/voice/operations` (the `status` read is a query on
`POST /v1/voice/queries`), read by its outcome through `parse_response`, and
every failure maps onto exactly one of the exception types below — a wiring
gap is `unavailable`, never a silent wrong answer. The one other exception is
the kernel's `OperationConflictError`: an `operation_id` reused for another
effect, which nothing retries.

A connection operation's `complete` payload carries the controller's `state`
snapshot, which the control verbs return; `status()` returns the query's `ok`
payload (the same snapshot plus `last_event_seq`, `streaming`, `last_error`,
`wake`, `pump`).
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import aiohttp

from nora.domain.playback_liveness import PlaybackReading
from nora.domain.service_api import (
    Family,
    OperationConflictError,
    parse_response,
)
from nora.domain.service_api import (
    Outcome as ContractOutcome,
)
from nora.infra.service_api_schema import VOICE_ANSWERS, validator
from nora.infra.trace_propagation import propagation_headers
from nora.memory import ActAuthority, EffectRecorder, Outcome

log = logging.getLogger("nora.infra.voice")

OPERATIONS_PATH = "/v1/voice/operations"
QUERIES_PATH = "/v1/voice/queries"


#: The answer schema for each operation kind and for the status query, as the
#: contract generates them.
_ANSWERS = {kind: validator(component) for kind, component in VOICE_ANSWERS.items()}
_STATUS_ANSWER = validator("VoiceStatusReadEnvelope")


@dataclass(frozen=True)
class _Answer:
    outcome: ContractOutcome
    payload: Any


class VoiceControllerError(Exception):
    """Base for every failure this module reports — callers catch this and
    nothing else."""


class VoiceControllerUnavailable(VoiceControllerError):  # noqa: N818 — pinned contract name
    """The controller could not be reached or could not answer: transport
    errors, timeouts, a redirect, `unavailable`, `uncertain`, `denied`, and
    answers whose body does not parse or lacks the pinned shape. Fail-closed —
    the caller reports «I couldn't check», never guesses a state."""


class GenerationConflict(VoiceControllerError):  # noqa: N818 — pinned contract name
    """`rejected` — the operation's `generation` is not the controller's current
    one (the connection was exited, replaced, or never joined), it is
    `connected_elsewhere`, or an arbiter-state rejection such as
    `pause_during_speech`. Carries the controller's `error.kind`:
    `generation_conflict` is recoverable (reconcile via the `status` query and
    retry), an arbiter rejection is NOT — the two must never be conflated."""

    def __init__(self, reason: str, state: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        #: The controller's snapshot the rejection carried, `None` when it carried none.
        self.state = state


class VoiceControllerRejected(VoiceControllerError):  # noqa: N818 — pinned contract name
    """HTTP 400 — the controller refused the request as malformed
    (`wrong_guild`, `percent_out_of_range`). Carries the controller's own
    `error` reason string."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class VoiceControllerNoConnection(VoiceControllerUnavailable):
    """`rejected` `unknown_guild`, or a `status` query that is `nothing_found`
    — the controller holds NO connection for this guild. Since one process
    serves several guilds this is an ordinary answer, not an outage: on a
    cold boot every guild answers it.

    Subclasses `VoiceControllerUnavailable` ON PURPOSE, so every existing
    handler keeps its fail-closed behaviour unchanged and only the callers
    that care — boot adoption, which must not log a WARNING on every start —
    need to know the difference."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@contextmanager
def conflicts_fail() -> Iterator[None]:
    """A 409 (an `operation_id` reused for another effect) as a failed call.

    The session and the hub answer every caller through `VoiceControllerError`;
    the conflict is `VoiceControllerUnavailable`, so nothing reconciles or retries it.
    """
    try:
        yield
    except OperationConflictError as exc:
        raise VoiceControllerUnavailable(f"operation_id reused: {exc}") from exc


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
        effects: EffectRecorder | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._effects = effects
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def join(
        self, guild_id: str, channel_id: str, *, operation_id: str,
    ) -> dict[str, Any]:
        """`join` — idempotent when already in that channel; `rejected`
        `connected_elsewhere` when it is not."""
        return await self._control(
            "join", {"guild_id": guild_id, "channel_id": channel_id}, operation_id,
        )

    async def exit(self, generation: str, *, guild_id: str, operation_id: str) -> dict[str, Any]:
        """`leave` — stop everything and destroy the connection."""
        return await self._control(
            "leave", {"guild_id": guild_id, "generation": generation}, operation_id,
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
        operation_id: str,
    ) -> dict[str, Any]:
        """`play` — REPLACE semantics: the controller has no
        upcoming queue; this supersedes whatever the music slot holds.
        `resumable=False` marks a live/non-seekable source (§5 position
        policy: speech preemption ENDS it instead of reviving from 0);
        `None` (every caller today) leaves the controller's default."""
        effect: dict[str, Any] = {
            "guild_id": guild_id, "generation": generation, "stream_url": stream_url,
        }
        if title is not None:
            effect["title"] = title
        if duration_seconds is not None:
            effect["duration_seconds"] = duration_seconds
        if resumable is not None:
            effect["resumable"] = resumable
        return await self._control("play", effect, operation_id)

    async def stop_music(
        self, generation: str, *, guild_id: str, operation_id: str,
    ) -> dict[str, Any]:
        """`stop` — stop the music slot, STAY connected. `complete` when idle."""
        return await self._control(
            "stop", {"guild_id": guild_id, "generation": generation}, operation_id,
        )

    async def pause(
        self, generation: str, *, guild_id: str, operation_id: str,
    ) -> dict[str, Any]:
        """`pause` — `rejected` `pause_during_speech` when speech holds
        the pipeline (surfaces as `GenerationConflict`)."""
        return await self._control(
            "pause", {"guild_id": guild_id, "generation": generation}, operation_id,
        )

    async def resume(
        self, generation: str, *, guild_id: str, operation_id: str,
    ) -> dict[str, Any]:
        return await self._control(
            "resume", {"guild_id": guild_id, "generation": generation}, operation_id,
        )

    async def set_volume(
        self, generation: str, percent: int, *, guild_id: str, operation_id: str,
    ) -> dict[str, Any]:
        """`set_volume` — `percent` is 0-200 (400 `percent_out_of_range` otherwise)."""
        return await self._control(
            "set_volume",
            {"guild_id": guild_id, "generation": generation, "percent": percent},
            operation_id,
        )

    async def stream(
        self,
        generation: str,
        audio: bytes,
        *,
        media_type: str,
        guild_id: str,
        operation_id: str,
    ) -> dict[str, Any]:
        """`speak` — the ONLY speech path: synthesized audio (≤2 MB)
        base64'd into the effect; the controller hard-cuts it over music.
        `rejected` for a dead generation, `unavailable` `speech_queue_full`
        when its bounded speech queue is full (`VoiceControllerUnavailable`)."""
        return await self._control("speak", {
            "guild_id": guild_id, "generation": generation,
            "audio_b64": base64.b64encode(audio).decode("ascii"),
            "media_type": media_type,
        }, operation_id)

    async def status(self, guild_id: str) -> dict[str, Any]:
        """The `status` query — the full snapshot (voice state, generation,
        channel, playback id, player, speech queue depth, `last_event_seq`,
        `streaming`, `last_error`, `wake`, `pump`) for ONE guild.
        `nothing_found` (no connection for the guild) is `VoiceControllerNoConnection`."""
        answer = await self._request(
            QUERIES_PATH, {"kind": "status", "params": {"guild_id": guild_id}}, "read",
        )
        if answer.outcome == "nothing_found":
            raise VoiceControllerNoConnection("unknown_guild")
        if answer.outcome != "ok" or not isinstance(answer.payload, dict):
            raise VoiceControllerUnavailable(f"status answered {answer.outcome}")
        return answer.payload

    async def _control(self, kind: str, effect: dict[str, Any],
                       operation_id: str) -> dict[str, Any]:
        """A connection operation's return is the `state` snapshot out of its
        `complete` envelope. A `complete` WITHOUT it is a contract violation,
        reported as `unavailable` — never as success."""
        if self._effects is None:
            return await self._operation(kind, effect, operation_id)

        async def call() -> dict[str, Any]:
            return await self._operation(kind, effect, operation_id)

        return await self._effects.invoke(
            "voice.speech" if kind == "speak" else "voice.control",
            f"voice-controller:{effect.get('guild_id', '')}", operation_id,
            ActAuthority(None), call, judge=lambda _result: Outcome("done"),
        )

    async def _operation(self, kind: str, effect: dict[str, Any],
                         operation_id: str) -> dict[str, Any]:
        answer = await self._request(OPERATIONS_PATH, {
            "operation_id": operation_id, "kind": kind, "effect": effect}, "operation")
        payload = answer.payload if isinstance(answer.payload, dict) else {}
        error = payload.get("error")
        reason = error.get("kind") if isinstance(error, dict) else None
        reason = reason if isinstance(reason, str) and reason else answer.outcome
        if answer.outcome == "rejected":
            if reason == "unknown_guild":
                raise VoiceControllerNoConnection(reason)
            state = payload.get("state")
            raise GenerationConflict(reason, state if isinstance(state, dict) else None)
        if answer.outcome != "complete":
            raise VoiceControllerUnavailable(f"{kind} answered {answer.outcome} ({reason})")
        state = payload.get("state")
        if not isinstance(state, dict):
            raise VoiceControllerUnavailable(
                f"{kind} answered {payload!r} — no state snapshot in the complete envelope"
            )
        return state

    async def _request(self, path: str, body: dict[str, Any], family: Family) -> _Answer:
        if self._session is not None:
            return await self._call(self._session, path, body, family)
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            return await self._call(session, path, body, family)

    async def _call(
        self, session: aiohttp.ClientSession, path: str, body: dict[str, Any], family: Family,
    ) -> _Answer:
        """One POST read through `parse_response`; a redirect is never followed."""
        try:
            async with session.request(
                "POST", f"{self._base_url}{path}", json=body, allow_redirects=False,
                headers=propagation_headers(),
            ) as resp:
                try:
                    payload: Any = await resp.json(content_type=None)
                except Exception:  # noqa: BLE001 — any parse failure maps below
                    payload = None
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise VoiceControllerUnavailable(
                f"{path}: {exc or type(exc).__name__}"
            ) from exc
        parsed = parse_response(family, status, payload)
        if parsed.caller_fault == 409:
            raise OperationConflictError(str(body.get("operation_id") or ""))
        if parsed.caller_fault == 400:
            reason = payload.get("error") if isinstance(payload, dict) else None
            raise VoiceControllerRejected(reason if isinstance(reason, str) and reason
                                          else "http_400")
        if parsed.outcome is None:
            raise VoiceControllerUnavailable(f"{path} answered HTTP {status}")
        schema = _STATUS_ANSWER if path == QUERIES_PATH else _ANSWERS[str(body["kind"])]
        if not schema.is_valid(payload) or (
                family == "operation"
                and (payload["when"]["state"] == "settled") == (parsed.outcome == "pending")):
            raise VoiceControllerUnavailable(f"{path} answered {parsed.outcome} off the contract")
        return _Answer(parsed.outcome, payload["payload"])


class HttpVoiceSession:
    """The music player's `VoiceSession` over the control plane — one
    instance per (guild, generation), built by `VoiceControllerHub.join`.

    Deliberately does NOT import the `VoiceSession` Protocol from
    `nora.platform.discord.music_player`: `nora.infra` sits BELOW
    `nora.platform` in the layers contract, so the conformance is
    structural (the protocol's methods, awaited, same names).

    Every call mints a fresh `operation_id` (the controller answers a
    repeat of it once); `playback_id` is recorded from each play's state
    snapshot so the hub can recognise the controller's `track-finished` /
    `stream-failed` callbacks as current or stale.

    The generation can go STALE under a cached session (a controller
    restart re-joins under a new one). The hub and the manager's
    player hold THIS SAME object, so adopting the live generation here
    reconciles both at once: a verb `rejected` with `generation_conflict`
    reads the `status` query, adopts, and retries ONCE."""

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
        """One `status` sample, reduced to the liveness fields.

        `playback_id is not None` is true in five arbiter states and audible
        in at most one, so liveness is read from these fields instead.

        NEVER raises. An unreachable or unparseable controller comes back as
        `PlaybackReading.unreachable()`, which the rule turns into UNKNOWN —
        "I could not check" must not reach a caller looking like "not
        playing"."""
        try:
            with conflicts_fail():
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

    async def reconcile_generation(self) -> bool:
        """Adopt the controller's CURRENT generation after a
        `generation_conflict` (a restart or a replaced connection).
        `False` — and the original conflict climbs — when the controller
        has no live connection to adopt: an exited connection is not a
        stale generation, it is a gone one, and retrying against it would
        resurrect audio nora already tore down."""
        try:
            with conflicts_fail():
                status = await self._client.status(self._guild_id)
        except VoiceControllerError:
            return False
        return self.adopt(status)

    def adopt(self, state: dict[str, Any]) -> bool:
        """Take the generation of a controller snapshot that shows a live
        connection; `False`, changing nothing, for any other snapshot."""
        generation = state.get("generation")
        if state.get("voice") != "connected" or not isinstance(generation, str) or not generation:
            return False
        log.info(
            "voice session for guild %s: adopting generation %s after a "
            "generation conflict (controller restarted or re-joined)",
            self._guild_id, generation,
        )
        self._generation = generation
        playback_id = state.get("playback_id")
        self.playback_id = playback_id if isinstance(playback_id, str) else None
        return True

    async def _with_reconcile(
        self, verb: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Run a generation-scoped verb; on a `generation_conflict` 409
        reconcile and retry ONCE (a fresh `operation_id` — the controller
        keeps the rejection under the first one). Arbiter rejections
        (`pause_during_speech`) are not staleness and propagate as-is."""
        with conflicts_fail():
            try:
                return await verb(self._operation_id())
            except GenerationConflict as exc:
                if exc.reason != "generation_conflict":
                    raise
                if not await self.reconcile_generation():
                    raise
                return await verb(self._operation_id())

    async def play(
        self, stream_url: str, *,
        duration_seconds: float | None = None, resumable: bool | None = None,
    ) -> None:
        state = await self._with_reconcile(
            lambda operation_id: self._client.play(
                self._generation, stream_url, guild_id=self._guild_id,
                duration_seconds=duration_seconds, resumable=resumable,
                operation_id=operation_id,
            )
        )
        playback_id = state.get("playback_id")
        self.playback_id = playback_id if isinstance(playback_id, str) else None

    async def pause(self) -> None:
        await self._with_reconcile(
            lambda operation_id: self._client.pause(
                self._generation, guild_id=self._guild_id, operation_id=operation_id,
            )
        )

    async def resume(self) -> None:
        await self._with_reconcile(
            lambda operation_id: self._client.resume(
                self._generation, guild_id=self._guild_id, operation_id=operation_id,
            )
        )

    async def stop(self) -> None:
        """`stop`. TOLERATES a `generation_conflict` WITHOUT a
        reconcile-retry: a generation the controller no longer recognises
        has no music slot to stop — the end state this call asks for
        already holds. That tolerance is what lets a dead-connection
        teardown complete locally instead of
        dying on a controller that is already gone."""
        try:
            with conflicts_fail():
                await self._client.stop_music(
                    self._generation, guild_id=self._guild_id, operation_id=self._operation_id(),
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
            lambda operation_id: self._client.set_volume(
                self._generation, percent, guild_id=self._guild_id,
                operation_id=operation_id,
            )
        )

    async def disconnect(self) -> None:
        """`leave`. TOLERATES `GenerationConflict`: the connection
        being already gone controller-side (a voice-death the controller
        saw first, an exit someone else issued) is exactly the end state
        this call asks for."""
        try:
            with conflicts_fail():
                await self._client.exit(
                    self._generation, guild_id=self._guild_id, operation_id=self._operation_id(),
                )
        except GenerationConflict:
            log.info(
                "voice exit for guild %s: generation already invalid — treated as done",
                self._guild_id,
            )

    @staticmethod
    def _operation_id() -> str:
        return str(uuid4())
