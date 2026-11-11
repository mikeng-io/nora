import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from nora.app.composition_helpers import _music_channel_of, _reasoning_effort
from nora.app.raw_clients import _DebriefModelClient, _NormaliseOnly
from nora.app.voice_events import VoiceEventConsumer
from nora.config import Config
from nora.doctrine.loader import DoctrineStore
from nora.domain.ports import DiscordPresenceSource, Speaker, VoicePresenceSource
from nora.infra.music import YtDlpSource
from nora.infra.sql.music_queue import SqlMusicQueueStore
from nora.infra.sql.music_session import SqlMusicSessionStore
from nora.infra.tts import TextFormatter as SpeechTextFormatter
from nora.infra.voice.http_controller import VoiceControllerClient
from nora.infra.voice.hub import VoiceControllerHub
from nora.obs.metrics import Metrics
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.bridge_control import (
    BridgeDiscordPresence,
    BridgeMusicTool,
    BridgeVoicePresence,
)
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.platform.discord.music_tool_bridge import DiscordMusicToolBridge
from nora.voice.adapters.tools import build as _build

"""Compose this area's tool adapters from shared dependencies."""


def tool_specs(deps):
    return _build(deps)


log = logging.getLogger("nora.app.composition")


@dataclass(frozen=True)
class SpeechWiring:
    discord_token: Any
    music_manager: Any
    voice_hub: Any


def speech(_discord_bridge, allowlist, cfg, engine, metrics) -> SpeechWiring:

    # Resolved here, not inside AdapterRegistry — the same convention every other `*_env`
    # secret (the LLM key, the route key) follows: composition.py reads the environment once
    # and hands the already-resolved value to whatever constructs the client that needs it.
    discord_token = cfg.credentials.get(cfg.platforms.discord.token_env, "")
    # The audio-controllers: ONE PER GUILD, because Discord's limit is one voice connection
    # per bot token per guild and each connection stays single. `[speech.tts] sidecars`
    # lists a controller endpoint per guild — usually the SAME one, since a single sidecar…
    voice_hub: VoiceControllerHub | None = None
    if cfg.speech.tts.sidecars and not _discord_bridge:
        # In Discord bridge mode the sidecar mapping belongs to the standalone bridge
        # (`deploy/discord-bridge/entry.py` reads this same `[speech.tts] sidecars` through the
        # mounted config.toml) — the app forwards voice work to the bridge and never owns a…
        voice_hub = VoiceControllerHub(
            {
                guild_id: VoiceControllerClient(endpoint)
                for guild_id, endpoint in cfg.speech.tts.sidecars_by_guild.items()
            }
        )
    # `spawn=asyncio.ensure_future` rather than the `BackgroundTasks` instance built further
    # down this function — safe because `MusicPlayerManager` already holds its own strong
    # reference to every task it spawns (`self._idle_tasks`), the exact GC-mid-flight bug…
    music_manager = (
        MusicPlayerManager(
            queue_store=SqlMusicQueueStore(engine),
            session_store=SqlMusicSessionStore(engine),
            track_source=YtDlpSource(),
            idle_timeout_seconds=cfg.music.idle_timeout_seconds,
            spawn=asyncio.ensure_future,
            autoplay_enabled=cfg.music.autoplay_enabled,
            autoplay_prewarm_seconds=cfg.music.autoplay_prewarm_seconds,
            playback_probe=voice_hub.expected_playback_id if voice_hub is not None else None,
            # The liveness watch: without it nora asks the controller for status exactly twice in
            # her life (boot, and after a version conflict) and otherwise believes a track is
            # playing until an event says otherwise — which a wedged controller never sends.
            read_playback=voice_hub.read_playback if voice_hub is not None else None,
            metrics=metrics,
            generation_probe=voice_hub.generation_for if voice_hub is not None else None,
            drop_voice_session=voice_hub.drop if voice_hub is not None else None,
            exit_voice_session=voice_hub.exit if voice_hub is not None else None,
        )
        if cfg.music.enabled and not _discord_bridge
        # Bridge mode: the standalone bridge owns THE manager (`entry.py`
        # builds it over the same SQL stores); the app's music surface is
        # the `BridgeMusicTool` proxy, so no second mutation path exists.
        else None
    )
    # A room marked `voice = true` with NO controller for its guild is the one combination
    # that is neither useful nor refused: the join fails, so the sidecar never connects, so
    # no utterance is ever produced — and nothing anywhere says why. The other three…
    _joinable = voice_hub.guild_ids if voice_hub is not None else frozenset()
    for _convo in allowlist.rooms():
        if not _convo.voice or _discord_bridge:
            # Bridge mode: the join allow-list lives on the bridge; the
            # app's read of it is `BridgeDiscordAdapter.send`, and this
            # warning's premise ("the app has no controller") is false.
            continue
        _guild = _convo.primary_id.partition("/")[0]
        if _guild not in _joinable:
            log.warning(
                "policy.toml marks %s (guild %s) `voice = true`, but no "
                "audio-controller is configured for that guild in "
                "[speech.tts] sidecars — nothing can join it, so no spoken "
                "utterance will ever be recorded there",
                _convo.label or _convo.primary_id,
                _guild,
            )
    if music_manager is not None and voice_hub is None:
        log.warning(
            "[music] enabled but [speech.tts] sidecars is empty — no "
            "audio-controller is wired for any guild, so every /music join "
            "will fail closed (the join-failed reply); music stays silent "
            "until a guild is mapped to its controller"
        )
    return SpeechWiring(
        discord_token=discord_token, music_manager=music_manager, voice_hub=voice_hub
    )


@dataclass(frozen=True)
class SpeakerWiring:
    speaker: Any


def speaker(cfg, doctrine_store, metrics) -> SpeakerWiring:

    speaker: Speaker | None = None
    if cfg.speech.tts.enabled:
        tts_key = cfg.credentials.get(cfg.speech.tts.api_key_env, "")
        if cfg.speech.tts.provider == "minimax_cn" and tts_key:
            from nora.infra.tts import MiniMaxTtsSpeaker

            speaker = MiniMaxTtsSpeaker(
                api_key=tts_key,
                voice=cfg.speech.tts.voice or "Cantonese_CuteGirl",
                speed=cfg.speech.tts.speed,
                pitch=cfg.speech.tts.pitch,
                vol=cfg.speech.tts.vol,
                emotion=cfg.speech.tts.emotion or None,
                text_normalization=cfg.speech.tts.text_normalization,
                pronunciation=tuple(cfg.speech.tts.pronunciation),
                formatter=_speech_formatter(cfg, doctrine_store, metrics),
            )
        else:
            log.warning(
                "[speech.tts] enabled but provider=%r or %s unset — no speaker "
                "is wired; voice answers stay text-only",
                cfg.speech.tts.provider,
                cfg.speech.tts.api_key_env,
            )
    return SpeakerWiring(speaker=speaker)


@dataclass(frozen=True)
class RoomsWiring:
    resolve_music_channel: Any
    voice_event_consumer: Any
    voice_guild_ids: Any


def rooms(_discord_bridge, allowlist, music_manager, voice_hub) -> RoomsWiring:
    # The join allow-list IS the set of guilds a controller exists for — one
    # source of truth, so the gate and the client map can never disagree.
    voice_guild_ids = voice_hub.guild_ids if voice_hub is not None else frozenset()
    # The controller → nora callback consumer (`POST /app/v1/voice/event`) — only when BOTH
    # halves it glues exist: the hub (whose event-id dedupe and expected-playback
    # bookkeeping it reads) and the music manager (whose per-guild-locked methods it…
    voice_event_consumer = (
        VoiceEventConsumer(music_manager, voice_hub)
        if music_manager is not None and voice_hub is not None
        # Bridge mode: the controller posts to the bridge's
        # `/v1/voice/event`, whose sink is the bridge's own consumer —
        # there is no app-side session for an event to reconcile.
        else None
    )
    # Where a guild's music ANNOUNCEMENTS go. Built from the room policy, not from
    # `cfg.music`: the answer is about a room's people, so it belongs beside the room's
    # trust flags. The channel it names is a DESTINATION, not a room — see…
    resolve_music_channel = _music_channel_of(allowlist)
    # A `music_channel` named for a guild that has no audio-controller is DEAD
    # CONFIGURATION: `[speech.tts] sidecars` — not `policy.toml` — is what gates `/music
    # play` (`voice_gate`), so nothing will ever announce for that guild. A warning rather…
    for convo in allowlist.rooms():
        if convo.music_channel is None or _discord_bridge:
            # Bridge mode: `[speech.tts] sidecars` maps on the BRIDGE, not
            # here — `voice_guild_ids` is empty by design and this warning
            # would announce a dead channel that is not dead.
            continue
        music_guild, _, _ = convo.primary_id.partition("/")
        if music_guild not in voice_guild_ids:
            log.warning(
                "%s names music_channel %s but guild %s has no audio-controller "
                "in [speech.tts] sidecars — music cannot play there, so the "
                "channel will never be announced into",
                convo.label or convo.primary_id,
                convo.music_channel,
                music_guild,
            )
    return RoomsWiring(
        resolve_music_channel=resolve_music_channel,
        voice_event_consumer=voice_event_consumer,
        voice_guild_ids=voice_guild_ids,
    )


@dataclass(frozen=True)
class PlatformsWiring:
    discord_presence: Any
    music_bridge: Any
    voice_presence: Any


def platforms(
    _discord_bridge,
    cfg,
    discord_control,
    music_manager,
    registry,
    resolve_music_channel,
    voice_guild_ids,
    voice_hub,
) -> PlatformsWiring:
    # `DiscordMusicToolBridge` — the four music agent tools' own collaborator. Built ONLY
    # when BOTH a Discord adapter and a music manager exist: `music_manager` is already
    # `None` under `cfg.music.enabled = false` (its own construction above), and `"discord"…
    music_bridge: DiscordMusicToolBridge | BridgeMusicTool | None = None
    if _discord_bridge and "discord" in registry:
        # Bridge mode: the app holds NO manager — the deterministic music
        # surface forwards to the bridge's `/v1/music/{op}`, which executes
        # against the bridge's own manager and voice cache.
        if cfg.music.enabled and discord_control is not None:
            music_bridge = BridgeMusicTool(control=discord_control)
    elif music_manager is not None and "discord" in registry:
        discord_adapter = registry.get("discord")
        # The same defensive narrowing `signal_adapter` above needs, for the identical reason:
        # `AdapterRegistry.get` returns the port type, and the bridge needs
        # `DiscordAdapter.current_client()`, which the port does not declare.
        if isinstance(discord_adapter, DiscordAdapter):
            music_bridge = DiscordMusicToolBridge(
                adapter=discord_adapter,
                manager=music_manager,
                hub=voice_hub,
                voice_guild_ids=voice_guild_ids,
                resolve_music_channel=resolve_music_channel,
            )
        else:
            log.error(
                "internal error: registry's 'discord' entry is not a DiscordAdapter "
                "— music agent tools stay unavailable"
            )
    # `DiscordVoicePresence` — `online_now`'s voice arm. Built whenever a Discord adapter
    # exists: it reads the live client's guild cache (`voice_states` is already in the
    # default intents), so unlike the Steam half there is no `[online]` gate and no key —…
    voice_presence: VoicePresenceSource | None = None
    # `DiscordPresence` — the `online` arm's Discord half: per-person presence (status +
    # rich presence), NOT voice membership. Same construction rule as `voice_presence`: it
    # reads the live Discord client (with the `presences` intent enabled), so no `[online]`…
    discord_presence: DiscordPresenceSource | None = None
    if "discord" in registry:
        discord_adapter = registry.get("discord")
        if _discord_bridge and discord_control is not None:
            # Bridge mode: both presence reads cross `/v1/reads` — a dead
            # bridge answers `unavailable`, never a stale live-cache read.
            voice_presence = BridgeVoicePresence(control=discord_control)
            discord_presence = BridgeDiscordPresence(control=discord_control)
        elif isinstance(discord_adapter, DiscordAdapter):
            from nora.platform.discord.discord_presence import DiscordPresence
            from nora.platform.discord.voice_presence import DiscordVoicePresence

            voice_presence = DiscordVoicePresence(adapter=discord_adapter)
            discord_presence = DiscordPresence(adapter=discord_adapter)
        else:
            log.error(
                "internal error: registry's 'discord' entry is not a DiscordAdapter "
                "— online_now's Discord arms stay unavailable"
            )
    return PlatformsWiring(
        discord_presence=discord_presence, music_bridge=music_bridge, voice_presence=voice_presence
    )


def _speech_formatter(
    cfg: Config,
    prompts: DoctrineStore,
    metrics: Metrics,
) -> SpeechTextFormatter | None:
    """`[speech.tts.format]`'s formatter, or None."""
    fmt = cfg.speech.tts.format
    if fmt.mode == "off":
        return None
    if fmt.mode == "normalise":
        return _NormaliseOnly()
    from nora.agent.speech_format import SpeechFormatter

    return SpeechFormatter(
        client=_DebriefModelClient(
            base_url=cfg.endpoint("speech_format")[0],
            api_key=cfg.endpoint("speech_format")[1],
            max_tokens=fmt.max_tokens,
            temperature=0.0,
            model=cfg.endpoint("speech_format", fmt.model or cfg.llm.model)[2],
            # The client's own bound sits above the formatter's so the
            # formatter's `wait_for` is the one that fires and is counted.
            timeout_seconds=fmt.timeout_seconds + 1.0,
            reasoning_effort=_reasoning_effort(
                fmt.reasoning_effort or cfg.llm.reasoning_effort,
                source="[speech.tts.format] reasoning_effort",
            ),
            agent_name="speech-format",
        ),
        prompts=prompts,
        metrics=metrics,
        timeout_seconds=fmt.timeout_seconds,
        min_chars=fmt.min_chars,
        max_chars=fmt.max_chars,
    )
