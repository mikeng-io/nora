"""The app side of bridge-mode Discord control (`mode = "bridge"`).

Under `[platforms.discord] mode = "bridge"` the Gateway,
`MusicPlayerManager`, slash commands and `VoiceControllerHub` live in the
`discord-bridge` process (`deploy/discord-bridge`). This module is the app's
half of that boundary: every Discord-dependent object the composition used
to build over `DiscordAdapter.current_client()` is rebuilt here over
`POST /v1/*` on the bridge's shared surface, behind the same ports, with the
same fail-closed answers.

The composition swap (the parent's one wiring change — this module is the
whole integration API):

- `music_bridge` — `DiscordMusicToolBridge` → `BridgeMusicTool`
  (`POST /v1/music/{op}`; the bridge re-verifies the requester against its
  own live voice cache and the projection-derived guild allow-list).
- `voice_presence` — `DiscordVoicePresence` → `BridgeVoicePresence`
  (the `voice_presence` query).
- `discord_presence` — `DiscordPresence` → `BridgeDiscordPresence`
  (the `member_presence` query).
- `rosters["discord"]` — `DiscordRoster` → `BridgeDiscordRoster`
  (the `roster` query).
- `policy_channels` — `ClientChannelDirectory` → `BridgeChannelDirectory`
  (the `channels` query, TTL'd snapshot: the policy port is
  synchronous, so it reads a freshness-bounded snapshot and answers `None`
  — the same "unavailable" a dead client gave — the moment it goes stale).
- the adapter — `BridgeAdapter` → `BridgeDiscordAdapter`: same adapter, plus
  the `target.voice` send path forwarded as a `speak` on the bridge's
  `voice` operations, to the SAME controller generation the bridge holds.

`BridgeControlClient` deliberately wraps `BridgeEndpoint` rather than
re-inventing auth: one bearer, one session, and every transport failure is
`None`/`False` — never a fabricated answer — matching the port discipline
of every implementation it replaces.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any
from uuid import uuid4

import aiohttp

from nora.domain.actions import ActionOwner
from nora.domain.identity import PlatformIdentity
from nora.domain.models import ConversationTarget, NormalizedMessage
from nora.domain.ports import (
    AdapterHealth,
    DiscordPresenceState,
    LinkPreview,
    Mention,
    MusicToolOutcome,
    OutboundAttachment,
    OutboundQuote,
    ReactionResult,
    Roster,
    SendResult,
    Speaker,
    TextStyle,
    VoiceChannelRef,
    speakable,
)
from nora.domain.service_api import OperationConflictError, status_for
from nora.memory import ActAuthority, EffectRecorder, Outcome
from nora.platform.bridge.client import (
    ACCOUNT_PATH,
    BridgeAdapter,
    BridgeAnswer,
    BridgeEndpoint,
    BridgeUnavailableError,
    bridge_call,
    bridge_query,
)

from .channels import StaticChannelDirectory

log = logging.getLogger("nora.discord.bridge")
VOICE_OPERATIONS = "/v1/voice/operations"
VOICE_QUERIES = "/v1/voice/queries"


class BridgeControlClient:
    """The bridge's `/v1/*` control surface, as the app consumes it."""

    def __init__(self, endpoint: BridgeEndpoint, *,
                 timeout_seconds: float = 30.0, effects: EffectRecorder | None = None) -> None:
        self._endpoint = endpoint
        self._effects = effects
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    def attach_effects(self, effects: EffectRecorder) -> None:
        self._effects = effects

    async def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "Authorization": f"Bearer {self._endpoint.token}"})
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def music(self, op: str, body: dict[str, Any]) -> dict[str, Any] | None:
        """`POST /v1/music/{op}` → the `outcome` dict, or `None` on any
        transport failure — the `unavailable` half of the read."""
        if self._effects is None or op == "status":
            return await self._music(op, body)
        room = body.get("conversation_id")
        return await self._effects.invoke(
            "bridge.music", f"discord-music:{room}", str(uuid4()),
            ActAuthority(None, room=room), lambda: self._music(op, body),
            judge=lambda result: Outcome(
                "uncertain" if result is None or result.get("status") == "unavailable"
                else "done" if result.get("status") == "ok" else "failed"
            ),
        )

    async def _music(self, op: str, body: dict[str, Any]) -> dict[str, Any] | None:
        try:
            session = await self._client()
            async with session.post(
                    f"{self._endpoint.base_url}/v1/music/{op}", json=body,
                    timeout=self._timeout) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except (TimeoutError, aiohttp.ClientError, ValueError):
            return None
        return data.get("outcome") if isinstance(data, dict) else None

    async def read(self, kind: str, params: dict[str, Any]) -> BridgeAnswer | None:
        """A platform query's `ok` answer; any other outcome or a transport
        failure is `None`, which every caller maps to its port's unavailable answer."""
        return await bridge_query(self._endpoint, kind, params, session=await self._client(),
                                  timeout_s=self._timeout.total or 30)

    async def account(self) -> dict[str, Any] | None:
        """The bridge's own account payload, `None` for any other outcome."""
        try:
            answer = await bridge_call(
                await self._client(), "GET", f"{self._endpoint.base_url}{ACCOUNT_PATH}", "read")
        except BridgeUnavailableError:
            return None
        return answer.payload if answer.outcome == "ok" and isinstance(answer.payload, dict) \
            else None

    async def _voice_status(self, guild_id: str) -> BridgeAnswer | None:
        """The bridge's `status` query as stated; `None` for a failed or self-inconsistent read."""
        try:
            answer = await bridge_call(
                await self._client(), "POST", f"{self._endpoint.base_url}{VOICE_QUERIES}",
                "read", json={"kind": "status", "params": {"guild_id": guild_id}})
        except (BridgeUnavailableError, OperationConflictError):
            return None
        return answer if _stated(answer) and answer.outcome != "unavailable" else None

    async def voice_state(self, guild_id: str) -> dict[str, Any] | None:
        """The bridge's `status` query → `{connected, session, generation,
        channel_id}` for `ok`, `None` otherwise — the "does a controller
        session exist" check the send path pays for before paying for synthesis."""
        answer = await self._voice_status(guild_id)
        if answer is None or answer.outcome != "ok" or not isinstance(answer.payload, dict):
            return None
        return answer.payload

    async def speech(self, guild_id: str, audio: bytes,
                     media_type: str) -> bool | None:
        """A `speak` on the bridge, for the generation its `status` query names.

        `True` for `complete`; `False` (the reply falls back to text) only for a
        status read that shows no connection, a stated `rejected`,
        `unsupported`, `denied` or `unavailable`, or a 400; `None` when the
        audio may have played or the read could not tell: a failed or
        self-inconsistent read or answer, `uncertain`, `incomplete`,
        `pending`, a transport failure, a 409.
        """
        if self._effects is None:
            return await self._speech(guild_id, audio, media_type)
        return await self._effects.invoke(
            "bridge.speech", f"discord-voice:{guild_id}", str(uuid4()), ActAuthority(None),
            lambda: self._speech(guild_id, audio, media_type),
            judge=lambda value: Outcome(
                "uncertain" if value is None else "done" if value else "failed"
            ),
        )

    async def _speech(self, guild_id: str, audio: bytes, media_type: str) -> bool | None:
        status = await self._voice_status(guild_id)
        if status is None:
            return None
        state = status.payload if status.outcome == "ok" else None
        generation = state.get("generation") if isinstance(state, dict) else None
        if not isinstance(generation, str) or not generation:
            return False
        try:
            answer = await bridge_call(
                await self._client(), "POST", f"{self._endpoint.base_url}{VOICE_OPERATIONS}",
                "operation", json={"operation_id": str(uuid4()), "kind": "speak", "effect": {
                    "guild_id": guild_id, "generation": generation,
                    "audio_b64": base64.b64encode(audio).decode("ascii"),
                    "media_type": media_type}},
                timeout_s=self._timeout.total or 30)
        except OperationConflictError:
            return None
        except BridgeUnavailableError as exc:
            return False if exc.status == 400 else None
        if answer.outcome == "complete":
            return True
        if answer.outcome in ("rejected", "unsupported", "denied", "unavailable") \
                and _stated(answer):
            return False
        return None


def _stated(answer: BridgeAnswer) -> bool:
    """Whether the outcome is the answer's own, not one `parse_response` stood in."""
    return answer.body.get("outcome") == answer.outcome \
        and answer.status == status_for(answer.outcome)


class BridgeMusicTool:
    """`MusicToolBridge` over `POST /v1/music/{op}` — the forwarded calls the
    bridge executes against ITS manager and ITS live voice cache. Same port,
    same `unavailable`-never-`nothing_found` discipline: a transport failure
    or wiring gap is `unavailable`, never a claim about the queue."""

    def __init__(self, *, control: BridgeControlClient) -> None:
        self._control = control

    async def _call(self, op: str, body: dict[str, Any]) -> MusicToolOutcome:
        data = await self._control.music(op, body)
        if data is None:
            return MusicToolOutcome(status="unavailable")
        status = data.get("status")
        if status not in ("ok", "nothing_found", "unavailable"):
            return MusicToolOutcome(status="unavailable")
        return MusicToolOutcome(
            status=status,
            title=data.get("title") if isinstance(data.get("title"), str) else None,
            url=data.get("url") if isinstance(data.get("url"), str) else None,
            queued=bool(data.get("queued")),
            note=str(data.get("note") or ""),
        )

    async def play(
        self, *, conversation_id: str, sender_ids: frozenset[str],
        query_or_url: str,
    ) -> MusicToolOutcome:
        return await self._call("play", {
            "conversation_id": conversation_id,
            "sender_ids": sorted(sender_ids),
            "query_or_url": query_or_url,
        })

    async def skip(self, *, conversation_id: str) -> MusicToolOutcome:
        return await self._call("skip", {"conversation_id": conversation_id})

    async def top(self, *, conversation_id: str, position: int) -> MusicToolOutcome:
        return await self._call("top", {
            "conversation_id": conversation_id, "position": position})

    async def stop(self, *, conversation_id: str) -> MusicToolOutcome:
        return await self._call("stop", {"conversation_id": conversation_id})

    async def status(self, *, conversation_id: str) -> MusicToolOutcome:
        return await self._call("status", {"conversation_id": conversation_id})

    async def set_volume(self, *, conversation_id: str, percent: int) -> MusicToolOutcome:
        return await self._call("set_volume", {
            "conversation_id": conversation_id, "percent": percent})


class BridgeVoicePresence:
    """`VoicePresenceSource` over the `voice_presence` query. NEVER
    raises; `None` is "the bridge could not read the cache", an absent key
    is that member's real "not in voice"."""

    def __init__(self, *, control: BridgeControlClient) -> None:
        self._control = control

    async def in_voice(
        self, *, member_ids: frozenset[str],
    ) -> dict[str, VoiceChannelRef] | None:
        if not member_ids:
            return {}
        data = await self._control.read(
            "voice_presence", {"member_ids": sorted(member_ids)})
        if data is None:
            return None
        members = data.payload or {}
        return {
            mid: VoiceChannelRef(
                channel_id=str(ref.get("channel_id") or ""),
                channel_name=ref.get("channel_name"),
            )
            for mid, ref in members.items()
            if isinstance(ref, dict) and ref.get("channel_id")
        }


class BridgeDiscordPresence:
    """`DiscordPresenceSource` over the `member_presence` query.
    NEVER raises; `None` is "the bridge could not read the cache"."""

    def __init__(self, *, control: BridgeControlClient) -> None:
        self._control = control

    async def presence(
        self, *, member_ids: frozenset[str],
    ) -> dict[str, DiscordPresenceState] | None:
        if not member_ids:
            return {}
        data = await self._control.read(
            "member_presence", {"member_ids": sorted(member_ids)})
        if data is None:
            return None
        states = data.payload or {}
        return {
            mid: DiscordPresenceState(
                status=str(s.get("status") or "offline"),
                game=s.get("game"), listening=s.get("listening"),
                custom_status=s.get("custom_status"),
            )
            for mid, s in states.items() if isinstance(s, dict)
        }


class BridgeDiscordRoster:
    """`RosterSource` over the `roster` query — the bridge runs the real
    `DiscordRoster` (REST members list) and this mirrors its contract:
    `available=False` on any read failure, never an empty room."""

    platform = "discord"

    def __init__(self, *, control: BridgeControlClient) -> None:
        self._control = control

    async def roster(self, *, conversation_id: str) -> Roster:
        data = await self._control.read(
            "roster", {"conversation_id": conversation_id})
        if data is None:
            return Roster(available=False)
        members = (data.payload or {}).get("members") or []
        return Roster(
            available=True,
            members=tuple(frozenset(str(i) for i in ids) for ids in members))


class BridgeChannelDirectory:
    """The policy's `ChannelDirectory` over the `channels` query — a TTL'd snapshot,
    because the policy port is SYNCHRONOUS and the bridge is not. Reads
    answer the last successfully refreshed map; the moment it goes stale
    they answer `None` — the same "unavailable" a dead client gave, never a
    stale half-answer re-presented as current (the same rule
    `ClientChannelDirectory`'s docstring states for `current_client()`).

    `refresh()` is the one async surface; the composition spawns `run()` and
    everything else is the sync port.

    `guild_ids` may be a callable — resolved fresh on every refresh, so a
    policy reload that names a new guild entry starts enumerating it on the
    next pass instead of waiting for a restart."""

    def __init__(
        self, *, control: BridgeControlClient,
        guild_ids: Iterable[str] | Callable[[], Iterable[str]] = (),
        ttl_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._control = control
        self._guild_ids = guild_ids
        self._ttl = ttl_seconds
        self._clock = clock
        self._fetched_at = 0.0
        self._snapshot = StaticChannelDirectory()

    async def refresh(self) -> None:
        """One read per guild → one swapped snapshot. A guild the bridge
        cannot enumerate is simply absent from the new map (its reads go
        `None`, unavailable) — it is never synthesized as empty."""
        channels: dict[str, dict[str, str]] = {}
        threads: dict[str, str] = {}
        guild_ids = (
            self._guild_ids() if callable(self._guild_ids) else self._guild_ids)
        for guild_id in guild_ids:
            data = await self._control.read("channels", {"guild_id": guild_id})
            if data is None:
                continue
            value = data.payload or {}
            gmap = value.get("channels")
            if isinstance(gmap, dict):
                channels[guild_id] = {str(k): str(v) for k, v in gmap.items()}
            tmap = value.get("threads")
            if isinstance(tmap, dict):
                threads.update({str(k): str(v) for k, v in tmap.items()})
        self._snapshot = StaticChannelDirectory(channels=channels, threads=threads)
        self._fetched_at = self._clock()

    async def run(self, *, interval_seconds: float = 5.0) -> None:
        """The refresh loop the composition spawns as a background task —
        keeps the snapshot younger than `ttl_seconds` so policy reads stay
        live while the bridge is up."""
        while True:
            try:
                await self.refresh()
            except Exception:  # the loop outlives one failed round
                log.exception("bridge channel directory refresh failed")
            await asyncio.sleep(interval_seconds)

    def _fresh(self) -> bool:
        return self._fetched_at > 0 and (self._clock() - self._fetched_at) <= self._ttl

    def guild_channels(self, *, guild_id: str) -> Mapping[str, str] | None:
        if not self._fresh():
            return None
        return self._snapshot.guild_channels(guild_id=guild_id)

    def parent_channel(self, *, channel_id: str) -> str | None:
        if not self._fresh():
            return None
        return self._snapshot.parent_channel(channel_id=channel_id)


class BridgeDiscordAdapter:
    """`PlatformAdapter` for `mode = "bridge"`: the shared `BridgeAdapter`
    for identity/listen/react/health/text-send, plus the ONE thing the
    shared adapter cannot express — the `target.voice` send path, forwarded
    to the bridge's controller-generation-scoped speech route.

    `send` reproduces `DiscordAdapter._send_voice`'s decision order exactly:
    refuse before paying (no speaker → no session → no synthesis), then
    bounded bytes through the bridge to the same `VoiceControllerHub`
    session, then text fallback on any failure — the app-side half of the
    design's "same controller generation" rule."""

    def __init__(
        self, *,
        delegate: BridgeAdapter,
        control: BridgeControlClient,
        speaker: Speaker | None = None,
    ) -> None:
        self.platform = delegate.platform
        self._delegate = delegate
        self._control = control
        self._speaker = speaker
        self._owns_control = True
        self._own_handle: str | None = None
        self._handle_fetched = False

    def bind_owner(self, owner: ActionOwner | None) -> BridgeDiscordAdapter:
        bound = BridgeDiscordAdapter(
            delegate=self._delegate.bind_owner(owner),
            control=self._control, speaker=self._speaker)
        bound._owns_control = False
        return bound

    @property
    def action_owner(self) -> ActionOwner | None:
        """The delegate's bound owner, surfaced so `Deliverer`'s record-first
        `live_invocation` binding never rebinds over a real work owner."""
        return self._delegate.action_owner

    def degrade(self, reason: str) -> None:
        self._delegate.degrade(reason)

    @property
    def degraded(self) -> bool:
        return self._delegate.degraded

    async def close(self) -> None:
        await self._delegate.close()
        if self._owns_control:
            await self._control.close()

    async def resolve_identity(self) -> PlatformIdentity:
        return await self._delegate.resolve_identity()

    async def own_handle(self) -> str | None:
        """Her `@handle`, memoized from the bridge's account read — the same
        once-a-process read `DiscordAdapter.own_handle` made off
        `/users/@me`. Display text for `<presence>` only; `None` when the
        bridge cannot answer (a missing handle degrades a display, never a
        reply decision)."""
        if not self._handle_fetched:
            self._handle_fetched = True
            data = await self._control.account()
            handle = (data or {}).get("handle")
            self._own_handle = handle if isinstance(handle, str) else None
        return self._own_handle

    async def listen(
        self, *, on_message: Callable[[NormalizedMessage], Awaitable[None]],
    ) -> None:
        # Inbound is owned by the stream reader in bridge mode; the adapter's
        # listen idles exactly as `BridgeAdapter.listen` does.
        await self._delegate.listen(on_message=on_message)

    async def send(
        self,
        *,
        target: ConversationTarget,
        text: str,
        mentions: tuple[Mention, ...] = (),
        styles: tuple[TextStyle, ...] = (),
        preview: LinkPreview | None = None,
        attachments: tuple[OutboundAttachment, ...] = (),
        quote: OutboundQuote | None = None,
    ) -> SendResult:
        if target.voice:
            spoken = await self._send_voice(target=target, text=text)
            if spoken.ok or spoken.uncertain:
                # An UNCERTAIN voice send may already have played — falling
                # back to text would be a second effect, never allowed.
                return spoken
            # The same fallback the direct adapter made: speech is a surface
            # of an ordinary room — an answer the room can read beats an
            # answer nobody gets.
            log.info(
                "voice reply for %s could not be spoken — sending it as text",
                target.conversation_id,
            )
        return await self._delegate.send_via_bridge(
            target, text, mentions=mentions, styles=styles,
            preview=preview, attachments=attachments, quote=quote)

    async def _send_voice(
        self, *, target: ConversationTarget, text: str,
    ) -> SendResult:
        if self._speaker is None:
            log.warning(
                "voice-channel send to %s but no speaker wired — voice "
                "replies stay silent (config [speech.tts])",
                target.conversation_id,
            )
            return SendResult(ok=False)
        if not speakable(text):
            log.info(
                "voice-channel send refused: not speakable (target=%s)",
                target.conversation_id,
            )
            return SendResult(ok=False)
        # REFUSE BEFORE PAYING — `DiscordAdapter._send_voice`'s own rule:
        # check the bridge has a live controller session for this guild
        # before MiniMax is billed for a synthesis nobody can hear. A bridge
        # that cannot answer is the same refusal (unavailable, not absent).
        guild_id, _, _ = target.conversation_id.partition("/")
        state = await self._control.voice_state(guild_id)
        if state is None or not state.get("session"):
            log.info(
                "voice reply for %s not synthesized: no live voice session "
                "for guild %s (bridge %s)",
                target.conversation_id, guild_id,
                "unreachable" if state is None else "has none",
            )
            return SendResult(ok=False)
        result = await self._speaker.speak(text=text)
        if not result.available or not result.audio:
            log.warning(
                "voice-channel send failed: tts unavailable (reason=%s)",
                result.reason,
            )
            return SendResult(ok=False)
        # The bounded forwarding: PCM to the bridge, which streams it to the
        # SAME controller generation. `None` (transport unknown — the bytes
        # may already have played) is `uncertain`, never a plain failure the
        # caller could retry; `False` (bridge refused) is the ordinary
        # fallback-eligible miss the direct path's hub failure was.
        ok = await self._control.speech(guild_id, result.audio, result.media_type)
        return SendResult(ok=bool(ok), uncertain=ok is None)

    async def react(
        self,
        *,
        target: ConversationTarget,
        emoji: str,
        to_author: str,
        to_timestamp: int,
        to_message_id: str = "",
        remove: bool = False,
    ) -> ReactionResult:
        return await self._delegate.react(
            target=target, emoji=emoji, to_author=to_author,
            to_timestamp=to_timestamp, to_message_id=to_message_id,
            remove=remove)

    async def health(self) -> AdapterHealth:
        return await self._delegate.health()


__all__ = [
    "BridgeChannelDirectory",
    "BridgeControlClient",
    "BridgeDiscordAdapter",
    "BridgeDiscordPresence",
    "BridgeDiscordRoster",
    "BridgeMusicTool",
    "BridgeVoicePresence",
]
