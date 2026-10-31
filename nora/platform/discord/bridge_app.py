"""`DiscordBridge` — the discord-bridge's owned Discord graph.

`design/platform-bridges/02-wire-and-platforms.md`: the bridge
owns the Discord Gateway connection, the deterministic command surface, the
music runtime and the voice-controller seam, so an app-only release leaves
all of it running and nothing a member typed is lost. The app forwards into
the routes this module serves (`nora.platform.discord.bridge_control`).

ONE OF EACH, no shadow path:

- ONE `discord.Client` + intents + `CommandTree` — driven through the SAME
  `DiscordListener` the adapter used, so `connected`/`last_event_at`
  bookkeeping and raw-event dispatch keep identical semantics.
- ONE `MusicPlayerManager` over the existing SQL queue/session stores —
  `/music` + `/discord` slash commands AND the app's forwarded tool calls
  (`POST /v1/music/{op}`) mutate through the same per-guild locks. The
  forwarded surface IS `DiscordMusicToolBridge`, not a copy of it.
- ONE `VoiceControllerHub` — join/adoption/reconciliation against the
  external audio-controllers, which keep the voice connection AND the
  playout. The bridge holds NO discord.py voice client and never opens a
  voice connection itself: voice membership is the controller's job,
  driven only through the hub.
- ONE controller-event sink — `POST /v1/voice/event` runs the SAME
  `VoiceEventConsumer` the app's `/app/v1/voice/event` ran (injected; it
  lives in `nora.app` which this package may not import).
- `POST /v1/reads` — the common read ops (roster, channels,
  member_presence, voice_presence) over the REAL implementations
  (`ClientChannelDirectory`, `DiscordVoicePresence`, `DiscordPresence`,
  `DiscordRoster`) on the live client cache, fail-closed: an unreadable
  cache answers `available=false`, never a fabricated empty/offline state.
- `POST /v1/voice/{guild}/speech` — bounded speech bytes to the SAME hub
  session, the bridge half of the app's forwarded `send(target.voice)`.

Deliberately NOT here: the wake→`/app/v1/voice/utterance` non-queued route
stays app-owned, and the durable queue/action core
(envelope/journal/`StreamProducer`, `POST /v1/actions`) is the core
worker's — this module consumes it unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import urlsplit

if TYPE_CHECKING:
    pass

import aiohttp
from aiohttp import typedefs, web

from nora.domain.models import ConversationTarget
from nora.domain.ports import (
    Mention,
    MusicToolOutcome,
    OutboundAttachment,
    OutboundQuote,
)
from nora.infra.voice.http_controller import VoiceControllerError
from nora.infra.voice.hub import VoiceControllerHub
from nora.obs.metrics import Metrics
from nora.platform.bridge import telemetry as bt
from nora.platform.bridge.envelope import (
    Envelope,
    canonical_source_key,
    event_id,
)
from nora.platform.bridge.journal import Journal
from nora.platform.bridge.rehydrate import message_to_dict
from nora.platform.bridge.service import (
    ActionReply,
    JournalPublisher,
    _reply_from_row,
    action_http_status,
    action_response,
    commit_transition,
    resolve_clockless_transition,
)

from . import music_messages as m
from .adapter import DiscordAdapter
from .channels import ClientChannelDirectory
from .discord_presence import DiscordPresence
from .listener import DiscordListener
from .media import DEFAULT_ATTACHMENT_HOSTS
from .music_commands import register as register_music_commands
from .music_commands import resume_persisted_sessions
from .music_manager import MusicPlayerManager
from .music_notify import ResolveMusicChannel
from .music_tool_bridge import DiscordMusicToolBridge
from .parser import (
    parse_discord_delete,
    parse_discord_edit,
    parse_discord_message,
    parse_discord_reaction,
)
from .react import DiscordReactor
from .roster import DiscordRoster
from .self_id import render_handle
from .send import DEFAULT_API_BASE, DiscordSender
from .voice_presence import DiscordVoicePresence

log = logging.getLogger("discord-bridge")
PLATFORM = "discord"

# A spoken reply is a handful of seconds of PCM — the app's `_send_voice`
# passes whatever the TTS returned, so this bound is a wire sanity check, not
# a product limit. Over it, the request is rejected rather than streamed.
MAX_SPEECH_BYTES = 24 * 1024 * 1024
# Inbound attachment custody: anything bigger than the capture pipeline would
# accept anyway is not journaled bytes here.
MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024

#: Cap on the wire-provenance fingerprint table — beyond it the buffer is
#: cleared wholesale (provenance falls to the durable ledger) rather than
#: grown without bound.
_MAX_FRAME_FINGERPRINTS = 4096


def _emoji_key(e: Any) -> str:
    """A raw `d["emoji"]` dict OR a parsed `PartialEmoji` → the same key:
    `name:id` for a custom emoji, `name` for a unicode one — the shape
    `str(PartialEmoji)` produces on both sides."""
    if e is None:
        return ""
    if isinstance(e, Mapping):
        name, eid = str(e.get("name") or ""), e.get("id")
    else:
        name, eid = str(getattr(e, "name", "") or ""), getattr(e, "id", None)
    return f"{name}:{eid}" if eid else name


def _reaction_frame_key(t: str, d: Mapping[str, Any]) -> tuple[str, ...] | None:
    """The FIFO join key between a raw Gateway frame and the parsed
    `RawReactionActionEvent` it dispatched — exactly the fields that make
    two reaction dispatches differ."""
    channel_id = str(d.get("channel_id") or "")
    message_id = str(d.get("message_id") or "")
    user_id = str(d.get("user_id") or "")
    emoji = _emoji_key(d.get("emoji"))
    if not (channel_id and message_id and user_id and emoji):
        return None
    rtype = d.get("type")
    return (t, channel_id, message_id, user_id, emoji,
            str(rtype if rtype is not None else 0))


def disabled_projection() -> dict[str, Any]:
    """The absent `/bridge-projection/{platform}.json`: revision 0, disabled —
    capture admits nothing until the app publishes one."""
    return {"revision": 0, "enabled": False}


# The projection contract's exactness bound: `revision` must stay inside
# [0, 2^53-1] so JS/Go/Python all read the same integer out of JSON.
_MAX_ADMISSION_REV = 9_007_199_254_740_991


def _admission_rev(proj: Mapping[str, Any]) -> int:
    rev = proj.get("revision")
    if not isinstance(rev, int) or isinstance(rev, bool):
        return 0
    return max(0, min(rev, _MAX_ADMISSION_REV))


def _reject(action_id: str, kind: str, message: str,
            outcome: str = "rejected") -> web.Response:
    return web.json_response({
        "action_id": action_id, "state": outcome, "outcome": outcome,
        "error": {"kind": kind, "message": message}})


class _VoiceEventSink(Protocol):
    """`VoiceEventConsumer`'s shape, declared locally: it lives in
    `nora.app.voice_events`, and `nora.platform` may not import `nora.app`
    (pyproject import-linter layers), so the composition entry injects it."""

    async def consume(self, body: object) -> tuple[int, dict[str, Any]]: ...


class ActionService(Protocol):
    """The injected `/v1/actions` executor — the shared
    `DurableActionService` (`nora.platform.bridge.service`): owner gate +
    reservation CAS under one transaction, then the platform executor.
    The bridge never fabricates an outcome: no service → `unavailable`."""

    async def run(self, action: Mapping[str, Any]) -> ActionReply: ...

    async def get(self, action_id: str) -> dict[str, Any] | None: ...


class CaptureSink(Protocol):
    """The injected durable-handoff for one admitted inbound event —
    the shared `JournalCaptureSink`: receipt-intent freeze → journal
    append → wake the ONE `JournalPublisher` (head-first retry through
    Redis outages, never skipped). A bridge with NO sink does not
    capture: admitting an event and losing it silently is worse than
    refusing the room, so absent is dropped."""

    def preflight(self) -> None: ...

    async def capture(self, envelope: Envelope, *,
                      transition: dict[str, Any] | None = None) -> bool: ...

    def note_ambiguous(self, record: dict[str, Any]) -> None: ...


class _ClientHolder:
    """The `adapter`-shaped shim the existing read/control implementations
    take — each one only ever calls `adapter.current_client()`."""

    def __init__(self, getter: Callable[[], Any]) -> None:
        self._getter = getter

    def current_client(self) -> Any:
        return self._getter()


class DiscordBridge:
    """The bridge's whole Discord graph. Constructed with its collaborators,
    started by `start()`, served by `routes()`."""

    def __init__(
        self,
        *,
        api_token: str,
        account_key: str,
        client: Any = None,
        client_factory: Callable[[], Any] | None = None,
        bot_token: str = "",
        api_base: str = DEFAULT_API_BASE,
        correlation_key: bytes = b"",
        key_version: int = 1,
        custody_dir: Path | None = None,
        projection: Callable[[], Mapping[str, Any]] = disabled_projection,
        manager: MusicPlayerManager | None = None,
        hub: VoiceControllerHub | None = None,
        voice_guild_ids: frozenset[str] = frozenset(),
        resolve_music_channel: ResolveMusicChannel | None = None,
        roster: DiscordRoster | None = None,
        voice_event_sink: _VoiceEventSink | None = None,
        max_speech_bytes: int = MAX_SPEECH_BYTES,
        actions: ActionService | None = None,
        capture: CaptureSink | None = None,
        publisher: JournalPublisher | None = None,
        journal: Journal | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        if client is None and client_factory is None:
            raise ValueError("a client or a client_factory is required")
        self._api_token = api_token
        self._metrics = metrics
        self._account_key = account_key
        self._client = client
        self._client_factory = client_factory
        self._bot_token = bot_token
        self._correlation_key = correlation_key
        self._key_version = key_version
        self._custody_dir = custody_dir
        self._projection = projection
        self._manager = manager
        self._hub = hub
        self._voice_guild_ids = frozenset(voice_guild_ids)
        # `None` → the production resolver: `music_channels[guild_id]` out
        # of the CURRENT projection (the publisher derives it from
        # `policy.music_channel_for`), read fresh on every call — ready,
        # resume and every announcement see the live value, and an absent
        # map/key is the existing fallback (`make_notify` → the channel
        # the session started in). A non-None kwarg stays injectable.
        self._resolve_music_channel = (
            resolve_music_channel
            if resolve_music_channel is not None
            else self._projection_music_channel)
        self._roster = roster
        self._voice_event_sink = voice_event_sink
        self._max_speech_bytes = max_speech_bytes
        # The collaborator fence: `None` means the surface FAILS CLOSED —
        # `/v1/actions` answers unavailable and capture drops admitted
        # events — rather than performing a raw SDK send or a
        # journal/stream write outside the wired collaborator. `entry.py`
        # attaches the shared `DurableActionService` +
        # `JournalCaptureSink`/`JournalPublisher`.
        self._actions = actions
        self._capture_sink = capture
        self._publisher = publisher
        # The durable transition ledger — read through the Journal's own
        # records, never a second store. `None` means clockless upstream
        # events (reactions) cannot establish durable identity here and
        # are retained `ambiguous` rather than given a guessed one.
        self._journal = journal
        self._publisher_task: asyncio.Task | None = None
        # Gateway wire provenance for clockless dispatches: fingerprint →
        # FIFO of `(session_id, s)` captured by `_record_wire_frame`.
        # Bounded — a provenance buffer that grows without bound is a leak.
        self._frames: dict[tuple[str, ...], Any] = {}
        # Outbound sends/reacts go through the SAME REST implementations the
        # adapter used — mention resolution, allowed_mentions, rate-limit and
        # thread semantics are theirs, not reimplemented here.
        self._sender = DiscordSender(bot_token, base_url=api_base)
        self._reactor = DiscordReactor(bot_token, base_url=api_base)

        self.self_id = ""
        self.connected_at_ms = 0
        self._listener: DiscordListener | None = None
        # ONE sequencer for the whole account: a frame's provenance join and
        # the transition ledger step must be ordered across channels AND any
        # custody await — a per-channel lock would let two callbacks observe
        # the same ledger state concurrently.
        self._capture_lock = asyncio.Lock()

        # The read surfaces — the SAME implementations the app used, driven
        # off this bridge's client getter (see _ClientHolder).
        self._holder = _ClientHolder(self.current_client)
        self._channels = ClientChannelDirectory(self.current_client)
        # `cast` is honest here: each consumer only ever calls
        # `adapter.current_client()`, which is the shim's entire surface.
        shim = cast(DiscordAdapter, self._holder)
        self._voice_presence = DiscordVoicePresence(adapter=shim)
        self._presence = DiscordPresence(adapter=shim)
        # The forwarded deterministic command surface — the real
        # `MusicToolBridge` implementation, so the slash commands and the
        # app's tool calls are literally the same decision path.
        self._tool: DiscordMusicToolBridge | None = (
            DiscordMusicToolBridge(
                adapter=shim, manager=manager, hub=hub,
                voice_guild_ids=self._voice_guild_ids,
                resolve_music_channel=self._resolve_music_channel,
            )
            if manager is not None
            else None
        )

    def attach(
        self, *, actions: ActionService | None = None,
        capture: CaptureSink | None = None,
        publisher: JournalPublisher | None = None,
        journal: Journal | None = None,
    ) -> None:
        """Post-construction collaborator wiring — the action service's
        executor needs the BUILT bridge (its `current_client` getter,
        custody dir), so `entry.py` constructs first and attaches second."""
        if actions is not None:
            self._actions = actions
        if capture is not None:
            self._capture_sink = capture
        if publisher is not None:
            self._publisher = publisher
        if journal is not None:
            self._journal = journal

    # ---- admission: room + requester, read off the CURRENT projection --------

    def _admit_room(self, conversation_id: str) -> bool:
        """Deny-closed room admission — the CURRENT projection must be
        enabled AND name the room; a disabled/absent projection and an
        unknown room both refuse, before any effect."""
        proj = self._projection()
        if not proj.get("enabled") or not conversation_id:
            return False
        return conversation_id in set(proj.get("rooms") or ())

    def _admit_requester(
        self, conversation_id: str, sender_ids: Iterable[str],
    ) -> bool:
        """The requester half of a mutation grant —
        `room_receipt_senders[conversation_id]` membership (any of the
        requester's ids), or the policy wildcard `"*"`. A missing map or
        room entry denies. `dm_allowed_senders` is a DM grant — it is
        never used as a group allowlist. Live voice-cache membership is
        verified separately inside `DiscordMusicToolBridge`; it is a
        Discord fact, not the Policy grant."""
        m = self._projection().get("room_receipt_senders")
        senders = m.get(conversation_id) if isinstance(m, Mapping) else None
        if not isinstance(senders, (list, tuple, set, frozenset)):
            return False
        granted = {str(s) for s in senders}
        if "*" in granted:
            return True
        return bool({str(s) for s in sender_ids} & granted)

    def _room_id_for(self, guild_id: str, channel: Any) -> str:
        """`"{guild}/{room_channel}"` — the parser's own room shape: a
        thread names its PARENT channel, never itself."""
        ctype = getattr(getattr(channel, "type", None), "value", None)
        parent = getattr(channel, "parent_id", None)
        room = (
            str(parent) if ctype in (10, 11, 12) and parent is not None
            else str(getattr(channel, "id", ""))
        )
        return f"{guild_id}/{room}"

    async def _slash_admission(self, interaction: Any) -> bool:
        """The tree-wide `interaction_check`: every standalone slash
        command (`/music …`, `/discord …`) answers to the SAME projection
        as capture and the forwarded tool surface — room admitted AND
        requester holding the room's sender grant — not just the voice
        guild allow-list (that still gates `connect()` itself)."""
        guild_id = getattr(interaction, "guild_id", None)
        if guild_id is None:
            with contextlib.suppress(Exception):
                await interaction.response.send_message(
                    m.music_not_in_server_line(), ephemeral=True)
            return False
        channel = getattr(interaction, "channel", None)
        channel_id = getattr(interaction, "channel_id", None)
        if channel is None and channel_id is not None:
            channel = self._channel(channel_id)
        conv_id = (
            self._room_id_for(str(guild_id), channel)
            if channel is not None
            else f"{guild_id}/{channel_id}")
        user_id = str(getattr(getattr(interaction, "user", None), "id", "") or "")
        if self._admit_room(conv_id) and self._admit_requester(conv_id, (user_id,)):
            return True
        log.info("slash admission denied in guild %s", guild_id)
        with contextlib.suppress(Exception):
            await interaction.response.send_message(
                m.room_not_admitted_line(), ephemeral=True)
        return False

    def _projection_music_channel(self, guild_id: str) -> str | None:
        """`music_channels[guild_id]` out of the CURRENT projection —
        the publisher derives the map from `policy.music_channel_for`, so
        the bridge sees policy's answer without importing `nora.policy`.
        Absent map/guild → `None`: the existing `make_notify` fallback to
        the channel the session started in."""
        m = self._projection().get("music_channels")
        if not isinstance(m, Mapping):
            return None
        v = m.get(guild_id)
        return str(v) if isinstance(v, str) and v else None

    # ---- client lifecycle ---------------------------------------------------

    def current_client(self) -> Any:
        """`None` whenever there is no live connection attempt right now —
        the same `unavailable` answer `DiscordAdapter.current_client` gave."""
        client = self._client
        if client is None or client.is_closed():
            return None
        return client

    async def start(self, token: str | None = None) -> None:
        """Build the client if none was injected, then run the listener
        forever (`Client.start()` owns the Gateway lifecycle). Raises on a
        closed client — the supervisor retries and calls `start` again on a
        fresh bridge or client."""
        # Disk-full preflight BEFORE the Gateway receives a byte — an
        # unwritable journal refuses the receive window outright.
        if self._capture_sink is not None:
            self._capture_sink.preflight()
        # The journal→stream publisher is a REAL registered loop: it runs
        # for the process, independent of the Gateway connection — a
        # dropped client must not stop the durable drain. A dead task is
        # respawned on the next start; `close()` stops it for real.
        if self._publisher is not None and (
                self._publisher_task is None
                or self._publisher_task.done()):
            self._publisher_task = asyncio.ensure_future(
                self._publisher.run())
            self._publisher_task.add_done_callback(self._publisher_died)
        client = self._client
        if client is None:
            if self._client_factory is None:
                raise RuntimeError("no client and no client_factory")
            client = self._client_factory()
            self._client = client
        if client.is_closed():
            # discord.py clients are single-use; a fresh attempt needs a
            # fresh one (DiscordAdapter.listen's own reasoning).
            if self._client_factory is None:
                raise RuntimeError("client is closed and cannot be restarted")
            client = self._client_factory()
            self._client = client
        # The wire-provenance hook — dispatches BEFORE the parsed handlers,
        # where `s` (the Gateway dispatch sequence) is still attached. It
        # fires only on a client built `enable_debug_events=True`
        # (`entry.py`); without it the frame buffer stays empty and
        # clockless events resolve through the durable transition ledger.
        async def on_socket_raw_receive(payload: Any) -> None:
            await self._record_wire_frame(payload)

        client.event(on_socket_raw_receive)
        self._listener = DiscordListener(
            client, token or self._bot_token,
            on_ready_extra=self.on_gateway_ready,
        )
        await self._listener.listen(
            self._capture_message, self._capture_edit, self._capture_touch,
        )

    async def on_gateway_ready(self) -> None:
        """The ONE graph-attach point — `DiscordListener.on_ready_extra`.

        The same sequence `DiscordAdapter` ran on every `on_ready`: register
        the slash tree on THIS client, resume persisted music sessions,
        start the liveness watch, and adopt any voice session the controller
        kept alive while this process was down. Idempotent by construction —
        each half guards against clobbering a live one."""
        client = self._client
        user = getattr(client, "user", None) if client is not None else None
        if user is not None:
            self.self_id = str(user.id)
        self.connected_at_ms = int(time.time() * 1000)
        # A READY is a NEW session: dispatch `s` resets and any frames a
        # previous session queued name sequence numbers that mean nothing
        # now — provenance must reset with the session it describes.
        self._frames.clear()
        log.info("discord gateway connected as %s", self.self_id)
        if self._manager is not None and client is not None:
            await register_music_commands(
                client, self._manager,
                hub=self._hub, voice_guild_ids=self._voice_guild_ids,
                resolve_music_channel=self._resolve_music_channel,
                client_getter=self.current_client,
                admission=self._slash_admission,
            )
            await resume_persisted_sessions(
                client, self._manager, hub=self._hub,
                resolve_music_channel=self._resolve_music_channel,
                client_getter=self.current_client,
            )
            self._manager.start_liveness_watch()
        if self._hub is not None:
            await self._hub.adopt_boot_voice_sessions(self._voice_guild_ids)

    @staticmethod
    def _publisher_died(task: asyncio.Task) -> None:
        """A dead publisher must be LOUD — events still spool to the
        journal (durable), but nothing drains until the next supervised
        `start()`. Cancellation on `close()` is the ordinary stop."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("journal publisher died: %s", type(exc).__name__)

    async def close(self) -> None:
        client = self._client
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()
        self._client = None
        task, self._publisher_task = self._publisher_task, None
        if self._publisher is not None:
            self._publisher.stop()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # ---- inbound capture (durable queue — core worker's wire, unchanged) ----

    async def _capture_message(self, raw: Any) -> None:
        msg = parse_discord_message(raw, own_id=self.self_id)
        if msg is None:
            return
        async with self._capture_lock:
            await self._journal_and_enqueue(msg, raw)

    async def _capture_edit(self, raw_message: Any) -> None:
        msg = parse_discord_edit(raw_message, own_id=self.self_id)
        if msg is None:
            return
        async with self._capture_lock:
            await self._journal_and_enqueue(msg, raw_message)

    async def _capture_touch(self, kind: str, payload: Any) -> None:
        channel_id = str(getattr(payload, "channel_id", "") or "")
        parent = self._channels.parent_channel(channel_id=channel_id) if channel_id else None
        if kind == "delete":
            msg = parse_discord_delete(payload, room_channel_id=parent)
        elif kind in ("reaction_add", "reaction_remove"):
            msg = parse_discord_reaction(
                payload, own_id=self.self_id,
                removed=kind == "reaction_remove", room_channel_id=parent,
            )
        else:
            return
        if msg is None:
            return
        async with self._capture_lock:
            await self._journal_and_enqueue(msg, payload)

    # ---- Gateway wire provenance (`on_socket_raw_receive`) -------------------

    async def _record_wire_frame(self, payload: Any) -> None:
        """The raw Gateway dispatch BEFORE the parsed handlers, where `s`
        (the per-session dispatch sequence) is still attached. Only the
        clockless reaction dispatches need it — the fingerprint joins each
        parsed `RawReactionActionEvent` to the frame that produced it, FIFO."""
        if isinstance(payload, (str, bytes)):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                return
        if not isinstance(payload, Mapping):
            return
        t = payload.get("t")
        if t not in ("MESSAGE_REACTION_ADD", "MESSAGE_REACTION_REMOVE"):
            return
        d = payload.get("d")
        s = payload.get("s")
        ws = getattr(self._client, "ws", None)
        session = str(getattr(ws, "session_id", "") or "") if ws is not None else ""
        if not isinstance(d, Mapping) or not isinstance(s, int) or not session:
            return
        fp = _reaction_frame_key(t, d)
        if fp is None:
            return
        if len(self._frames) >= _MAX_FRAME_FINGERPRINTS:
            # Losing provenance under a storm is honest — the events fall to
            # the durable ledger; growing this dict without bound is not.
            self._frames.clear()
        self._frames.setdefault(fp, []).append((session, s))

    def _wire_identity(self, kind: str, payload: Any) -> str | None:
        """The `g:{session}:{s}` identity for a parsed reaction event, from
        the frame that dispatched it — the upstream sequence Discord DOES
        send, captured at the socket boundary. `None` means no frame was
        recorded for this event (debug events off, or a synthetic caller):
        the durable transition ledger answers instead."""
        d = {
            "channel_id": getattr(payload, "channel_id", None),
            "message_id": getattr(payload, "message_id", None),
            "user_id": getattr(payload, "user_id", None),
            "emoji": getattr(payload, "emoji", None),
            "type": getattr(getattr(payload, "type", 0), "value",
                            getattr(payload, "type", 0)),
        }
        fp = _reaction_frame_key(
            "MESSAGE_REACTION_REMOVE" if kind == "reaction_remove"
            else "MESSAGE_REACTION_ADD", d)
        if fp is None:
            return None
        queue = self._frames.get(fp)
        if not queue:
            return None
        session, s = queue.pop(0)
        if not queue:
            self._frames.pop(fp, None)
        return f"g:{session}:{s}"

    # ---- upstream transition identity (design 00 §upstream identity) ---------

    def _reaction_ledger_key(self, msg: Any, raw: Any) -> str | None:
        """The durable per-(conversation,target,reactor,emoji,type) ledger
        key. `None` when the fields it needs are absent — a key built from
        half an identity is worse than none: the event is retained
        `ambiguous` instead of risking a false dedupe."""
        target = str(getattr(msg.touch.target, "message_id", "") or "")
        emoji = str(getattr(msg.touch, "emoji", "") or "")
        if not (target and emoji and msg.sender_id and msg.conversation_id):
            return None
        rtype = getattr(raw, "type", 0)
        rtype = getattr(rtype, "value", rtype)
        return (f"r:{msg.conversation_id}:{target}:{msg.sender_id}"
                f":{emoji}:{rtype}")

    def _upstream_identity(
        self, kind: str, msg: Any, raw: Any,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """`(upstream, transition_record)` — the PROVEN upstream revision
        where one exists, the durable transition ledger's sequence where
        Discord sends none. `(None, ambiguous_record)` when neither can
        establish identity: the event is retained as diagnostic evidence,
        never deduplicated under a guessed key (design 00)."""
        if msg.touch is not None:
            if msg.touch.kind == "delete":
                # A delete names the message it removes — the target IS the
                # transition identity (it cannot fire twice legitimately).
                return f"d:{msg.touch.target.message_id}", None
            # A reaction carries no revision and no clock. Prefer the
            # Gateway `s` the wire captured; fall back to the journal's
            # durable transition ledger — a restart replays the same
            # answer the process had when it journaled.
            state = "remove" if msg.touch.removed else "add"
            wire = self._wire_identity(
                "reaction_remove" if msg.touch.removed else "reaction_add",
                raw)
            ledger_key = self._reaction_ledger_key(msg, raw)
            if wire is not None:
                if ledger_key is not None and self._journal is not None:
                    # The wire revision IS the upstream identity; the ledger
                    # records it so a later frame-less same-state callback
                    # resolves to the identity this event committed under.
                    return wire, commit_transition(
                        self._journal, ledger_key, state, wire)
                return wire, None
            # No wire revision: a state change here is INDISTINGUISHABLE
            # from a delayed replay of an old transition (design 02 §wire
            # semantics) — only the recorded same-state redelivery may
            # re-resolve, under the identity it originally committed.
            if ledger_key is not None and self._journal is not None:
                identity, transition = resolve_clockless_transition(
                    self._journal, ledger_key, state)
                if identity is not None:
                    return identity, transition
                reason = ("no upstream sequence; cannot distinguish a new"
                          " transition from reconnect replay")
            else:
                reason = ("no upstream sequence and no durable transition"
                          " ledger")
            return None, {
                "kind": kind, "platform": PLATFORM,
                "conversation_id": msg.conversation_id,
                "target": str(getattr(
                    msg.touch.target, "message_id", "") or ""),
                "state": state, "received_ms": int(time.time() * 1000),
                "reason": reason,
            }
        if msg.edit_of is not None:
            # `parse_discord_edit` proves `edited_at` exists (it drops
            # updates that carry none) — target + upstream edit timestamp
            # is a real revision identity.
            return f"e:{msg.edit_of.message_id}:{msg.timestamp}", None
        return msg.message_id or f"{msg.sender_id}:{msg.timestamp}", None

    async def _journal_and_enqueue(self, msg: Any, raw: Any) -> None:
        """Admission → custody → the injected `CaptureSink`. The room gate
        is the projection's (`enabled` + `rooms`) — denied rooms produce
        no bytes anywhere. With NO sink wired the admitted event is
        dropped: a journal/stream write outside the collaborator is
        exactly the unsupervised side-effect the fence exists to refuse,
        and `available=false`-style silence beats a half-held event."""
        proj = self._projection()
        if not proj.get("enabled"):
            return
        if msg.conversation_id not in set(proj.get("rooms") or ()):
            return
        if self._capture_sink is None:
            return
        kind = (
            "message" if msg.edit_of is None and msg.touch is None
            else ("edit" if msg.edit_of is not None else msg.touch.kind)
        )
        # The ONE short producer root for this capture — created at the
        # boundary, its context frozen onto the envelope BEFORE the
        # journal append inside `capture` (design 04 §producer).
        with bt.producer_span(PLATFORM, kind=kind) as carrier:
            if self._custody_dir is not None:
                # Custody is a bounded op LINKED to this capture — never a
                # child of whatever ambient context happens to exist.
                with bt.operation_span(
                        "custody", platform=PLATFORM,
                        links=bt.carrier_links(carrier)):
                    await self._capture_attachments(msg, raw)
            upstream, transition = self._upstream_identity(kind, msg, raw)
            if upstream is None:
                note = getattr(self._capture_sink, "note_ambiguous", None)
                if note is not None:
                    note(transition or {})
                else:
                    log.warning(
                        "capture sink cannot retain an ambiguous event"
                        " (kind=%s)", kind)
                return
            now = int(time.time() * 1000)
            sk = canonical_source_key(
                PLATFORM, self._account_key, msg.conversation_id, kind,
                upstream,
            )
            env = Envelope(
                platform=PLATFORM, account_key=self._account_key, kind=kind,
                source_key=sk,
                event_id=event_id(
                    sk, key=self._correlation_key,
                    key_version=self._key_version),
                conversation_id=msg.conversation_id,
                conversation_type=msg.conversation_type,
                sender_id=msg.sender_id,
                sender_name=msg.sender_name,
                sent_ms=msg.timestamp or None,
                received_ms=now, queued_ms=now,
                time_quality="upstream" if msg.timestamp else "bridge_local",
                message=message_to_dict(msg), attachments=(),
                trace=dict(carrier),
                admission_rev=_admission_rev(proj),
            )
            # The transition ledger step commits ON this event's record.
            ok = (await self._capture_sink.capture(
                env, transition=transition) if transition is not None
                else await self._capture_sink.capture(env))
        if not ok:
            log.warning("capture sink refused an admitted event (kind=%s)", kind)

    async def _capture_attachments(self, msg: Any, raw: Any) -> None:
        """Custody the message's attachments under their `platform_id` keys —
        the app's `BridgeMediaSource` resolves them from
        `GET /v1/attachments/{key}`. CDN-host allowlisted, size-bounded,
        best-effort: a custody failure must not drop the event itself."""
        if self._custody_dir is None:
            return
        for att in getattr(raw, "attachments", ()):
            url = getattr(att, "url", "") or ""
            if urlsplit(url).hostname not in DEFAULT_ATTACHMENT_HOSTS:
                continue
            size = getattr(att, "size", 0)
            if isinstance(size, int) and size > MAX_ATTACHMENT_BYTES:
                continue
            key = str(getattr(att, "id", "") or "")
            if not key:
                continue
            try:
                async with (
                    aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=60)) as session,
                    session.get(url) as resp,
                ):
                    if resp.status != 200:
                        continue
                    data = await resp.read()
            except (TimeoutError, aiohttp.ClientError):
                continue
            if len(data) > MAX_ATTACHMENT_BYTES:
                continue
            try:
                (self._custody_dir / key).write_bytes(data)
            except OSError:
                log.warning("attachment custody write failed for key %s", key)

    # ---- HTTP surface --------------------------------------------------------

    def routes(self) -> web.Application:
        # A closure, not a bound method — `web.middleware` tags the function
        # object (`__middleware_version__`), which a bound method can't hold.
        @web.middleware
        async def _auth(
            request: web.Request, handler: typedefs.Handler,
        ) -> web.StreamResponse:
            if request.headers.get("Authorization") != f"Bearer {self._api_token}":
                return web.json_response({"error": "unauthorized"}, status=401)
            return await handler(request)

        app = web.Application(middlewares=[_auth])
        app.router.add_get("/metrics", self.get_metrics)
        app.router.add_post("/v1/actions", self.post_action)
        app.router.add_get("/v1/actions/{aid}", self.get_action)
        app.router.add_get("/v1/self", self.get_self)
        app.router.add_get("/v1/health", self.get_health)
        app.router.add_get("/health", self.get_health)
        app.router.add_get("/ready", self.get_ready)
        app.router.add_get("/v1/groups", self.get_groups)
        app.router.add_get("/v1/guilds/{guild_id}/members", self.get_members)
        app.router.add_get("/v1/guilds/{guild_id}/members/{user_id}",
                           self.get_member)
        app.router.add_get("/v1/channels/{channel_id}", self.get_channel)
        app.router.add_get("/v1/presence/{guild_id}/{user_id}", self.get_presence)
        app.router.add_put("/v1/attachments/{key}", self.put_attachment)
        app.router.add_get("/v1/attachments/{key}", self.get_attachment)
        # The shared read surface (common bridge client contract).
        app.router.add_post("/v1/reads", self.post_reads)
        # Deterministic control — the app's forwarded music tool calls.
        app.router.add_post("/v1/music/{op}", self.post_music_op)
        # Voice — through the hub only; the controller owns the connection.
        app.router.add_post("/v1/voice/{guild_id}/join", self.post_voice_join)
        app.router.add_post("/v1/voice/{guild_id}/leave", self.post_voice_leave)
        app.router.add_post("/v1/voice/{guild_id}/speech", self.post_voice_speech)
        app.router.add_get("/v1/voice/{guild_id}", self.get_voice_state)
        # Controller → nora lifecycle events (was /app/v1/voice/event).
        app.router.add_post("/v1/voice/event", self.post_voice_event)
        return app

    async def get_metrics(self, request: web.Request) -> web.Response:
        if self._metrics is None:
            return web.Response(status=503, text="metrics unavailable")
        return web.Response(text=self._metrics.render(),
                            headers={"Content-Type": "text/plain; version=0.0.4"})

    def _guild(self, guild_id: str) -> Any:
        client = self.current_client()
        if client is None:
            return None
        try:
            return client.get_guild(int(guild_id))
        except (TypeError, ValueError):
            return None

    def _channel(self, channel_id: Any) -> Any:
        client = self.current_client()
        if client is None:
            return None
        try:
            return client.get_channel(int(channel_id))
        except (TypeError, ValueError):
            return None

    # ---- durable actions (core worker's contract — unchanged semantics) ------

    async def post_action(self, req: web.Request) -> web.Response:
        """`/v1/actions` → the injected `ActionService`. With none wired
        the route FAILS CLOSED — 503 `unavailable`, never a raw SDK effect
        and never a stubbed confirmation (the shared client's non-200 →
        `BridgeUnavailableError` maps the same way). The body renders the
        SAME frozen vocabulary Signal's route does (`action_response` /
        `action_http_status`) — one contract across platforms."""
        try:
            body = await req.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "body must be JSON"}, status=400)
        aid = str(body.get("action_id") or "")
        if self._actions is None:
            reply = ActionReply(
                "unavailable", kind="unavailable",
                message="no action service")
        else:
            reply = await self._actions.run(body, links=bt.carrier_links({
                "traceparent": req.headers.get("traceparent") or "",
                "tracestate": req.headers.get("tracestate") or ""}))
        return web.json_response(
            action_response(aid, reply), status=action_http_status(reply))

    async def get_action(self, req: web.Request) -> web.Response:
        """Persisted, scoped result — Signal parity: 404 only for a
        definitely-absent (or foreign-platform/account) id; `started` is
        a 200 with the row's truth; a dead ledger is a 503, not a 404."""
        aid = req.match_info["aid"]
        if self._actions is None:
            return web.json_response({"error": "store unavailable"}, status=503)
        try:
            row = await self._actions.get(aid)
        except Exception:  # noqa: BLE001 — ledger unreachable is a 503, not a 404
            return web.json_response({"error": "store unavailable"}, status=503)
        if row is None:
            return web.json_response({"error": "unknown action"}, status=404)
        return web.json_response(action_response(aid, _reply_from_row(row)))

    # ---- identity / health ----------------------------------------------------

    async def get_self(self, req: web.Request) -> web.Response:
        user = getattr(self._client, "user", None) if self._client else None
        return web.json_response({
            "platform": PLATFORM, "primary": self.self_id,
            "aliases": [self.self_id] if self.self_id else [],
            "handle": render_handle(
                getattr(user, "name", None),
                getattr(user, "discriminator", None))})

    async def get_health(self, req: web.Request) -> web.Response:
        client = self.current_client()
        ready = client is not None and client.is_ready()
        return web.json_response({
            "ok": ready, "connected_at_ms": self.connected_at_ms,
            "detail": None if ready else "gateway_down"})

    async def get_ready(self, req: web.Request) -> web.Response:
        """Readiness — the gateway is up AND the HTTP surface is serving.
        `/v1/health` is the same read for the app-side client."""
        return await self.get_health(req)

    # ---- reads (compat routes first, then the common /v1/reads op surface) ---

    async def get_groups(self, req: web.Request) -> web.Response:
        out = []
        client = self.current_client()
        for guild in (client.guilds if client else ()):
            for ch in guild.text_channels:
                out.append({"id": str(ch.id), "guild_id": str(guild.id),
                            "name": ch.name,
                            "members": [str(m.id) for m in ch.members]})
        return web.json_response(out)

    async def get_members(self, req: web.Request) -> web.Response:
        guild = self._guild(req.match_info["guild_id"])
        if guild is None:
            return web.json_response({"error": "unknown guild"}, status=404)
        return web.json_response([
            {"id": str(m.id), "name": m.name, "display": m.display_name,
             "bot": bool(m.bot)}
            async for m in guild.fetch_members(limit=None)])

    async def get_member(self, req: web.Request) -> web.Response:
        guild = self._guild(req.match_info["guild_id"])
        if guild is None:
            return web.json_response({"error": "unknown guild"}, status=404)
        member = guild.get_member(int(req.match_info["user_id"]))
        if member is None:
            try:
                member = await guild.fetch_member(int(req.match_info["user_id"]))
            except Exception as exc:  # noqa: BLE001 — not-found is typed per-library
                log.debug("member fetch failed: %s", type(exc).__name__)
                return web.json_response({"error": "unknown member"}, status=404)
        return web.json_response({
            "id": str(member.id), "name": member.name,
            "display": member.display_name, "bot": bool(member.bot),
            "roles": [str(r.id) for r in member.roles]})

    async def get_channel(self, req: web.Request) -> web.Response:
        ch = self._channel(req.match_info["channel_id"])
        if ch is None:
            return web.json_response({"error": "unknown channel"}, status=404)
        return web.json_response({
            "id": str(ch.id), "name": getattr(ch, "name", ""),
            "guild_id": str((getattr(ch, "guild", None) and ch.guild.id) or ""),
            "parent_id": str(getattr(ch, "parent_id", None) or "") or None,
            "type": str(ch.type)})

    async def get_presence(self, req: web.Request) -> web.Response:
        guild = self._guild(req.match_info["guild_id"])
        member = guild.get_member(int(req.match_info["user_id"])) if guild else None
        if member is None:
            return web.json_response({"error": "unknown"}, status=404)
        return web.json_response({
            "user_id": str(member.id), "status": str(member.status),
            "activities": [getattr(a, "name", "") for a in member.activities]})

    async def post_reads(self, req: web.Request) -> web.Response:
        """`{op, params}` → `{available, value, observed_ms, reason?}` —
        the common read ops over the real implementations.
        `available=false` means the cache could not be read — with a
        `reason` so an unavailable cache can never read as an empty one;
        a real "nothing found" is the per-op empty/absent shape with
        `available=true`. `observed_ms` stamps when the underlying cache
        read happened (these reads are live, so it is the read's own
        time — never a replayed staleness)."""
        try:
            body = await req.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "body must be JSON"}, status=400)
        op = body.get("op")
        params = body.get("params") or {}

        def reply(available: bool, value: Any,
                  reason: str | None = None) -> web.Response:
            out: dict[str, Any] = {
                "available": available, "value": value,
                "observed_ms": int(time.time() * 1000)}
            if reason is not None:
                out["reason"] = reason
            return web.json_response(out)

        try:
            if op == "roster":
                roster = await self._read_roster(
                    conversation_id=str(params.get("conversation_id") or ""))
                available = roster is not None and roster.available
                value: Any = None if not available else {
                    "members": [sorted(ids) for ids in roster.members]}
                return reply(
                    available, value,
                    None if available else "cache_unavailable")
            if op == "channels":
                guild_id = str(params.get("guild_id") or "")
                channels = self._channels.guild_channels(guild_id=guild_id)
                if channels is None:
                    return reply(False, None, "cache_unavailable")
                return reply(True, {
                    "channels": channels,
                    "threads": self._guild_threads(guild_id)})
            if op == "channel":
                # `available` is "the cache could be read"; `value` None is a
                # real "not found". A dead client is not a missing channel.
                if self.current_client() is None:
                    return reply(False, None, "cache_unavailable")
                return reply(
                    True, self._read_channel(str(params.get("channel_id") or "")))
            if op == "member_presence":
                states = await self._presence.presence(member_ids=frozenset(
                    str(m) for m in params.get("member_ids") or ()))
                value = None if states is None else {
                    mid: dataclasses.asdict(s) for mid, s in states.items()}
                return reply(
                    states is not None, value,
                    None if states is not None else "cache_unavailable")
            if op == "voice_presence":
                members = await self._voice_presence.in_voice(member_ids=frozenset(
                    str(m) for m in params.get("member_ids") or ()))
                value = None if members is None else {
                    mid: {"channel_id": ref.channel_id,
                          "channel_name": ref.channel_name}
                    for mid, ref in members.items()}
                return reply(
                    members is not None, value,
                    None if members is not None else "cache_unavailable")
        except Exception as exc:  # noqa: BLE001 — read boundary; logged by type
            log.error("read op %s failed: %s", op, type(exc).__name__)
            return reply(False, None, "read_failed")
        return web.json_response({"error": "unknown op"}, status=400)

    async def _read_roster(self, *, conversation_id: str) -> Any:
        if self._roster is None:
            return None
        return await self._roster.roster(conversation_id=conversation_id)

    def _guild_threads(self, guild_id: str) -> dict[str, str]:
        """`thread_id -> parent channel_id` for the guild — the second half
        of the directory snapshot the app's `ChannelDirectory.parent_channel`
        needs (a touch in a thread lands in its parent's room)."""
        guild = self._guild(guild_id)
        if guild is None:
            return {}
        return {
            str(t.id): str(t.parent_id)
            for t in getattr(guild, "threads", ()) or ()
            if getattr(t, "parent_id", None) is not None
        }

    def _read_channel(self, channel_id: str) -> dict[str, Any] | None:
        ch = self._channel(channel_id)
        if ch is None:
            return None
        return {
            "id": str(ch.id), "name": getattr(ch, "name", ""),
            "guild_id": str((getattr(ch, "guild", None) and ch.guild.id) or ""),
            "parent_id": (
                str(ch.parent_id) if getattr(ch, "parent_id", None)
                is not None else None),
            "type": getattr(getattr(ch, "type", None), "name",
                            str(getattr(ch, "type", ""))),
        }

    # ---- forwarded deterministic music control -------------------------------

    async def post_music_op(self, req: web.Request) -> web.Response:
        """`POST /v1/music/{op}` — the app's forwarded `MusicToolBridge` call,
        executed against THIS bridge's manager + live voice cache. The
        outcome shape is `MusicToolOutcome`'s (`status` is the R-27 word;
        `unavailable` means the wiring/cache could not answer, never that
        nothing was playing)."""
        if self._tool is None:
            return web.json_response({"error": "music disabled"}, status=404)
        try:
            body = await req.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "body must be JSON"}, status=400)
        op = req.match_info["op"]
        conversation_id = str(body.get("conversation_id") or "")
        # Deny-closed BEFORE any effect: a disabled/absent projection, a
        # missing conversation_id, and a room the projection does not name
        # all refuse — the same gate `_journal_and_enqueue` holds capture
        # to. An enabled projection with the room admitted is not enough
        # for a mutation that carries a requester: `play`'s sender_ids
        # must intersect `room_receipt_senders` (or `*`) — voice-cache
        # membership is the tool's own second check, not the grant.
        if not self._admit_room(conversation_id):
            return web.json_response({"error": "room_not_admitted"}, status=403)
        try:
            if op == "play":
                sender_ids = frozenset(
                    str(s) for s in body.get("sender_ids") or ())
                if not self._admit_requester(conversation_id, sender_ids):
                    return web.json_response(
                        {"error": "sender_not_admitted"}, status=403)
                outcome = await self._tool.play(
                    conversation_id=conversation_id,
                    sender_ids=sender_ids,
                    query_or_url=str(body.get("query_or_url") or ""),
                )
            elif op == "skip":
                outcome = await self._tool.skip(conversation_id=conversation_id)
            elif op == "top":
                outcome = await self._tool.top(
                    conversation_id=conversation_id,
                    position=int(body.get("position") or 0))
            elif op == "stop":
                outcome = await self._tool.stop(conversation_id=conversation_id)
            elif op == "status":
                outcome = await self._tool.status(conversation_id=conversation_id)
            elif op == "set_volume":
                outcome = await self._tool.set_volume(
                    conversation_id=conversation_id,
                    percent=int(body.get("percent") or 0))
            else:
                return web.json_response({"error": "unknown op"}, status=400)
        except Exception as exc:  # noqa: BLE001 — music boundary; logged by type
            log.error("music op %s failed: %s", op, type(exc).__name__)
            outcome = MusicToolOutcome(status="unavailable")
        return web.json_response({"outcome": dataclasses.asdict(outcome)})

    # ---- voice: join/leave/state/speech/events, all through the hub ----------

    async def post_voice_join(self, req: web.Request) -> web.Response:
        if self._hub is None:
            return web.json_response({"error": "voice disabled"}, status=404)
        body = await req.json()
        guild_id = req.match_info["guild_id"]
        channel_id = str(body.get("channel_id") or "")
        if not channel_id:
            return web.json_response({"error": "channel_id required"}, status=400)
        try:
            await self._hub.join(guild_id, channel_id)
        except VoiceControllerError as exc:
            log.error("voice join failed: %s", type(exc).__name__)
            return web.json_response({"error": "controller_unavailable"}, status=503)
        return web.json_response({"ok": True, "channel_id": channel_id})

    async def post_voice_leave(self, req: web.Request) -> web.Response:
        guild_id = req.match_info["guild_id"]
        try:
            if self._manager is not None:
                await self._manager.leave_voice(guild_id)
            left = await self._hub.exit(guild_id) if self._hub is not None else False
        except VoiceControllerError as exc:
            log.error("voice leave failed: %s", type(exc).__name__)
            return web.json_response({"error": "controller_unavailable"}, status=503)
        return web.json_response({"ok": True, "left": bool(left)})

    async def get_voice_state(self, req: web.Request) -> web.Response:
        guild_id = req.match_info["guild_id"]
        session = self._hub.session_for(guild_id) if self._hub is not None else None
        return web.json_response({
            "connected": session is not None,
            "session": session is not None,
            "generation": session.generation if session is not None else None,
            "channel_id": (
                self._hub.channel_for(guild_id) if self._hub is not None else None),
        })

    async def post_voice_speech(self, req: web.Request) -> web.Response:
        """Bounded speech bytes → the SAME controller generation the hub
        holds. `409 no_connection` when the guild has no live session —
        the app already checked, this is the authoritative second check."""
        if self._hub is None:
            return web.json_response({"error": "voice disabled"}, status=404)
        guild_id = req.match_info["guild_id"]
        data = await req.read()
        if not data:
            return web.json_response({"error": "empty body"}, status=400)
        if len(data) > self._max_speech_bytes:
            return web.json_response({"error": "speech too large"}, status=413)
        media_type = req.headers.get("X-Speech-Media-Type") or "audio/wav"
        session = self._hub.session_for(guild_id)
        if session is None:
            return web.json_response({"error": "no_connection"}, status=409)
        ok = await self._hub.stream_speech(guild_id, data, media_type)
        if not ok:
            return web.json_response({"error": "stream_failed"}, status=503)
        return web.Response(status=204)

    async def post_voice_event(self, req: web.Request) -> web.Response:
        """The controller → nora lifecycle callback — the SAME consumer the
        app's `/app/v1/voice/event` ran: event-id dedupe, seq bookmarking,
        generation-checked teardown, playback-checked advance."""
        if self._voice_event_sink is None:
            return web.json_response({"error": "voice events disabled"}, status=404)
        try:
            body = await req.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None
        status, payload = await self._voice_event_sink.consume(body)
        return web.json_response(payload, status=status)

    # ---- attachment custody -----------------------------------------------------

    async def put_attachment(self, req: web.Request) -> web.Response:
        if self._custody_dir is None:
            return web.json_response({"error": "custody disabled"}, status=404)
        key = req.match_info["key"]
        data = await req.read()
        if len(data) > MAX_ATTACHMENT_BYTES:
            return web.json_response({"error": "too large"}, status=413)
        (self._custody_dir / key).write_bytes(data)
        return web.json_response({"ok": True, "key": key})

    async def get_attachment(self, req: web.Request) -> web.Response:
        if self._custody_dir is None:
            return web.json_response({"error": "custody disabled"}, status=404)
        p = self._custody_dir / req.match_info["key"]
        if not p.is_file():
            return web.json_response({"error": "unknown"}, status=404)
        return web.Response(body=p.read_bytes())


class DiscordRestActionService:
    """The REAL outbound execution the bridge used to run inline —
    `DiscordSender`/`DiscordReactor` for send/react (mention resolution,
    `allowed_mentions`, quote, custody-backed attachments, rate-limit
    semantics, thread addressing) and the live client's channel objects
    for edit/typing. Kept whole behind the `ActionService` fence so the
    shared ledger/reservation helper can wrap it without a second
    implementation — and so its absence means `unavailable`, never a raw
    SDK side-effect."""

    def __init__(self, bridge: DiscordBridge) -> None:
        self._bridge = bridge

    async def run(self, action: Mapping[str, Any]) -> ActionReply:
        bridge = self._bridge
        r = action.get("request") or {}
        aid = str(action.get("action_id") or "")
        op = str(action.get("op") or "")
        target = self._target(r.get("target"))
        try:
            if op == "send":
                result = await bridge._sender.send(
                    target, str(r.get("text") or ""),
                    mentions=tuple(
                        Mention(
                            start=int(m.get("start") or 0),
                            length=int(m.get("length") or 0),
                            recipient=str(m.get("recipient") or ""),
                            handle=m.get("handle"))
                        for m in r.get("mentions") or ()),
                    attachments=tuple(
                        a for a in (
                            self._attachment(att)
                            for att in r.get("attachments") or ())
                        if a is not None),
                    quote=self._quote(r.get("quote")),
                    request_id=aid,
                )
                if result.ok:
                    return ActionReply(
                        "confirmed", platform_ms=result.timestamp)
                return ActionReply(
                    "rejected",
                    kind="rate_limited" if result.retryable else "send_failed",
                    message="send refused")
            if op == "react":
                reacted = await bridge._reactor.react(
                    target=target, emoji=str(r.get("emoji") or ""),
                    to_message_id=str(r.get("to_message_id") or ""),
                    remove=bool(r.get("remove")))
                if reacted.ok:
                    return ActionReply("confirmed")
                if not reacted.supported:
                    return ActionReply(
                        "rejected", kind="unsupported",
                        message="no message id")
                # `ReactionResult` collapses every non-204 into ok=False;
                # whether the emoji landed cannot be proven here, so the
                # honest state is `uncertain`, never `confirmed`.
                return ActionReply("uncertain", kind="reaction_failed")
            if op == "edit":
                channel = self._target_channel(target)
                if channel is None:
                    return ActionReply(
                        "rejected", kind="no_channel",
                        message="unknown target")
                message = await channel.fetch_message(int(r["message_id"]))
                await message.edit(content=r["text"])
                return ActionReply(
                    "confirmed", platform_ref=str(message.id))
            if op == "typing":
                channel = self._target_channel(target)
                if channel is None:
                    return ActionReply(
                        "rejected", kind="no_channel",
                        message="unknown target")
                await channel.typing()
                return ActionReply("confirmed")
            return ActionReply("rejected", kind="unsupported", message=op)
        except Exception as exc:  # noqa: BLE001 — unknown after upstream call
            return ActionReply(
                "uncertain", kind="transport", message=type(exc).__name__)

    @staticmethod
    def _target(t: Any) -> ConversationTarget:
        t = t or {}
        return ConversationTarget(
            platform=PLATFORM,
            conversation_id=str(t.get("id") or ""),
            conversation_type="dm" if t.get("kind") == "dm" else "group",
            thread=str(t.get("thread") or ""))

    def _target_channel(self, target: ConversationTarget) -> Any:
        """`edit`/`typing` resolve the channel the way `DiscordSender` does:
        `conversation_id` is `"<guild>/<channel>"`, `thread` overrides."""
        _guild, _, channel_id = target.conversation_id.partition("/")
        return self._bridge._channel(target.thread or channel_id)

    def _quote(self, q: Any) -> OutboundQuote | None:
        if not isinstance(q, Mapping) or not q.get("platform_message_id"):
            return None
        return OutboundQuote(
            ts=int(q.get("ts") or 0), author_id=str(q.get("author_id") or ""),
            platform_message_id=str(q["platform_message_id"]))

    def _attachment(self, att: Any) -> OutboundAttachment | None:
        """Custody bytes live under `/v1/attachments/{key}` on the bridge;
        read them back so the send carries real bytes, not a stale ref."""
        custody_dir = self._bridge._custody_dir
        if custody_dir is None or not isinstance(att, Mapping):
            return None
        key = str(att.get("key") or "")
        path = custody_dir / key
        if not key or not path.is_file():
            return None
        return OutboundAttachment(
            content=path.read_bytes(),
            media_type=str(att.get("media_type") or "application/octet-stream"),
            filename=str(att.get("file_name") or "attachment"))
