from __future__ import annotations

import functools
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nora.agent.room import Room
from nora.agent.whereis import Circle, check_disclosure_invariant
from nora.app.composition_helpers import _presence_reader
from nora.app.startup import StartupError
from nora.domain.live import LiveSet
from nora.domain.ports import ChannelDirectory, EngagePolicy, PlatformAdapter, RosterSource
from nora.ingest.policy import (
    CompositePolicy,
    MentionPolicy,
    RelevanceGatePolicy,
    ReplyToNoraPolicy,
    TriggerTextPolicy,
    VoiceNotePolicy,
    VoiceWakePolicy,
)
from nora.obs.metrics import Metrics
from nora.persons import DIRECT_PLATFORMS, Persons
from nora.platform.bridge.client import BridgeAdapter, BridgeEndpoint, BridgeRoster
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.bridge_control import (
    BridgeChannelDirectory,
    BridgeControlClient,
    BridgeDiscordRoster,
)
from nora.platform.discord.channels import ClientChannelDirectory
from nora.platform.registry import AdapterRegistry, AdapterRegistryError
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.signal.roster import SignalRoster
from nora.policy import Policy

"""Platform and channel wiring steps for the composition root."""


async def _presence_preflight(
    allowlist: Policy, rosters: Mapping[str, RosterSource], persons: Persons,
    circles: Sequence[Circle], metrics: Metrics, self_ids: frozenset[str] = frozenset(),
) -> None:
    """The R-21/R-31 disclosure pre-flight: for every policy"""
    for conv in allowlist.rooms():
        # Every platform that HAS a roster, not just Signal. Skipping a platform here does not
        # make it safe — the authoritative check is per-call in `Whereis.locate` — but the gauge
        # and the operator's boot-time warning would silently cover only part of the rooms.
        roster_source = rosters.get(conv.platform)
        if roster_source is None:
            continue
        try:
            view = None
            # `ids` is the id plus its aliases; the first readable one is the room.
            for conversation_id in sorted(conv.ids):
                candidate = await Room(
                    platform=conv.platform,
                    conversation_id=conversation_id,
                    roster_source=roster_source,
                    persons=persons,
                    self_ids=self_ids,
                ).members()
                if candidate.available:
                    view = candidate
                    break
            if view is None:
                log.warning(
                    "presence pre-flight: roster unreadable for %s conversation "
                    "%r (%s) — subset invariant unverified there; whereis's "
                    "per-call guard still refuses anything unverifiable",
                    conv.platform,
                    conv.label or conv.primary_id,
                    conv.primary_id,
                )
                continue
            if view.unnamed:
                log.warning(
                    "presence pre-flight: %d unmapped account(s) in %s "
                    "conversation %r (%s) — whereis refuses there until "
                    "identity.toml names them (`nora persons pending`)",
                    view.unnamed,
                    conv.platform,
                    conv.label or conv.primary_id,
                    conv.primary_id,
                )
            if conv.location:
                # DEC-74: this room is location-TRUSTED, so its group is DELIBERATELY not a subse…
                # the circle. Running the subset invariant here would log a permanent false «R-21…
                # … whereis must refuse» and pin `nora_presence_circle_subset_ok=0` for the circl…
                log.info(
                    "presence pre-flight: %s conversation %r is location-trusted "
                    "(DEC-74) — the circle-subset invariant is intentionally "
                    "skipped here",
                    conv.platform,
                    conv.label or conv.primary_id,
                )
                continue
            group_person_ids = frozenset(p.person_id for p in view.people)
            for circle in circles:
                check_disclosure_invariant(
                    circle=circle,
                    group_person_ids=group_person_ids,
                    metrics=metrics,
                    # WHICH room, so the gauge is per-conversation: circle alone would be last-wr…
                    # across every allowlisted room, and a failing room could be overwritten by a…
                    # one and the alarm lost.
                    platform=conv.platform,
                    conversation=conv.primary_id or "",
                )
        except Exception:
            log.exception("presence pre-flight failed for a policy conversation")


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelsWiring:
    _discord_bridge: Any
    channel_directory: Any
    discord_box: Any
    discord_control: Any


def channels(cfg: Any, reload_files: Any)-> ChannelsWiring:

    discord_box: dict[str, Any] = {}
    # Discord bridge mode: the standalone discord-bridge owns the Gateway, the music
    # manager, the voice-controller hub and the controller-event consumer. This process
    # builds ONLY the proxy side — `BridgeControlClient` + the `BridgeDiscordAdapter` the…
    _discord_bridge = (
        cfg.platforms.discord.enabled
        and getattr(cfg.platforms.discord, "mode", "direct") == "bridge"
    )
    discord_control: BridgeControlClient | None = (
        BridgeControlClient(
            BridgeEndpoint(
                cfg.platforms.discord.base_url, cfg.credentials.get(cfg.bridges.token_env, "")
            )
        )
        if _discord_bridge
        else None
    )
    channel_directory: ChannelDirectory
    if discord_control is not None:
        # `guild_ids` reads the LIVE policy each refresh — a reloaded file
        # that names a new server entry starts enumerating it on the next
        # pass. `reload_files` binds below; the callable is lazy by design.
        channel_directory = BridgeChannelDirectory(
            control=discord_control,
            guild_ids=lambda: {c.guild for c in reload_files().policy.conversations if c.guild},
        )
    else:
        channel_directory = ClientChannelDirectory(
            lambda: (
                a.current_client()
                if isinstance(a := discord_box.get("adapter"), DiscordAdapter)
                else None
            )
        )
    return ChannelsWiring(
        _discord_bridge=_discord_bridge,
        channel_directory=channel_directory,
        discord_box=discord_box,
        discord_control=discord_control,
    )


@dataclass(frozen=True)
class AdaptersWiring:
    account: Any
    adapter: Any
    registry: Any


async def adapters(
    cfg: Any, discord_box: Any, discord_token: Any, metrics: Any,
    music_manager: Any, persons: Any, resolve_music_channel: Any, speaker: Any,
    summons_inbox: Any, voice_guild_ids: Any, voice_hub: Any,
) -> AdaptersWiring:
    try:
        registry = await AdapterRegistry.from_config(
            cfg.platforms,
            metrics=metrics,
            discord_token=discord_token,
            music_manager=music_manager,
            discord_speaker=speaker,
            voice_hub=voice_hub,
            voice_guild_ids=voice_guild_ids,
            resolve_music_channel=resolve_music_channel,
            instagram_summons=summons_inbox,
            bridge_token=cfg.credentials.get(cfg.bridges.token_env, ""),
            # A 1:1 message goes only to somebody in identity.toml; the
            # senders hold this set and refuse the rest.
            dm_recipients={
                p: LiveSet(functools.partial(persons.direct_addresses, p)) for p in DIRECT_PLATFORMS
            },
        )
    except AdapterRegistryError as exc:
        raise StartupError(str(exc)) from exc
    if "signal" not in registry:
        # The rest of this function (the engagement pipeline, delivery) has no code path that
        # runs without an adapter — a disabled Signal with nothing else enabled is not a valid
        # startup state, even though AdapterRegistry itself tolerates zero enabled platforms.
        raise StartupError("no platform adapter is enabled (platforms.signal.enabled = false)")
    # The directory's adapter, now that one exists — see the declaration by
    # `reload_files` above. `registry.get` answers the port type, so the
    # getter's isinstance check is also the narrowing.
    discord_box["adapter"] = registry.adapters.get("discord")
    signal_adapter = registry.get("signal")
    # `AdapterRegistry.get` returns the port type — narrowed back to the concrete class here
    # because `Dependencies.adapter` stays SignalAdapter (nora.app only builds Signal-
    # specific tool wiring around it; see that field's own docstring). An internal…
    if not isinstance(signal_adapter, (SignalAdapter, BridgeAdapter)):
        raise StartupError("internal error: registry's 'signal' entry is not a SignalAdapter")
    adapter = signal_adapter
    # Static conformance proof (DEC-12): `isinstance` against a `runtime_checkable`
    # `Protocol` only checks method/attribute NAMES, not signatures
    # (tests/contract/test_port_conformance.py::…
    _adapter_satisfies_platform_adapter: PlatformAdapter = adapter
    account = registry.identity_for("signal").primary
    return AdaptersWiring(account=account, adapter=adapter, registry=registry)


@dataclass(frozen=True)
class PoliciesWiring:
    policy: Any
    presence_reader: Any
    rosters: Any


def policies(
    allowlist: Any, cfg: Any, discord_control: Any, discord_token: Any,
    registry: Any,
) -> PoliciesWiring:


    # First match wins, deterministic text first. The mention policy uses the same alias set
    # R-12 uses to refuse self-tags — an @-mention arrives as U+FFFC in the body plus a
    # `mentions` array, so a substring trigger can never see it. EVERY platform's aliases,…
    self_aliases = registry.self_aliases()
    # `[engagement.trigger].enabled` (default True — see TriggerConfig) gates ONLY the
    # spoken/typed trigger phrase out of the composite: a real `@mention` or a reply to one
    # of her own messages still engage her when this is off, so turning it off narrows the…
    engage_policies: list[EngagePolicy] = []
    if cfg.engagement.trigger.enabled:
        engage_policies.append(TriggerTextPolicy(cfg.engagement.trigger.text))
    engage_policies.append(MentionPolicy(self_aliases))
    engage_policies.append(ReplyToNoraPolicy(self_aliases))
    # Any voice note engages her, independent of trigger text — the transcription is
    # conversation content in the body by the time the gate runs. Last so the deterministic
    # text paths keep their reasons, but it fires on any note regardless.
    engage_policies.append(VoiceNotePolicy())
    # Any transcript that arrived via the voice-wake channel engages her too: the wake
    # service only POSTs after the operator said 「喂 Nora」, which is the voice channel's
    # closest thing to an explicit tag — without this, a transcript lands in the audit row…
    engage_policies.append(VoiceWakePolicy())
    # The participation occasion's room gate: LAST, and only when the occasion is enabled at
    # all. It never engages — it marks an untagged message in an opted-in room with
    # `EngageDecision.relevance_gate`, which the dispatcher NOTES with the quiescence…
    if cfg.relevance_gate.enabled:
        engage_policies.append(RelevanceGatePolicy(allowlist))
    policy = CompositePolicy(tuple(engage_policies))
    # The room-scoped tools' one platform dependency, built BEFORE the presence reader
    # because the room tools read member counts through it. Built here rather than taken off
    # the adapter because membership is a read-only DIRECTORY query…
    rosters: dict[str, RosterSource] = {}
    if "signal" in registry:
        rosters["signal"] = (
            BridgeRoster(
                "signal",
                BridgeEndpoint(
                    cfg.platforms.signal.base_url, cfg.credentials.get(cfg.bridges.token_env, "")
                ),
            )
            if cfg.platforms.signal.mode == "bridge"
            else SignalRoster(
                base_url=cfg.platforms.signal.base_url, account=cfg.platforms.signal.account
            )
        )
    if "discord" in registry:
        if discord_control is not None:
            # Bridge mode: the roster read crosses the bridge's `/v1/reads`
            # surface — the app holds no Discord client for a live answer.
            rosters["discord"] = BridgeDiscordRoster(control=discord_control)
        else:
            from nora.platform.discord.roster import DiscordRoster

            rosters["discord"] = DiscordRoster(discord_token)
    if "instagram" in registry:
        # Built for a DEGRADED adapter too, and that is deliberate: the registry registers an
        # Instagram adapter that could not authenticate rather than refusing to start
        # (`nora.platform.registry`), and a checkpoint clears without a restart. A roster…
        from nora.platform.instagram.roster import InstagramRoster

        rosters["instagram"] = InstagramRoster(cfg.platforms.instagram.base_url)
    # `<presence>`: ONE reader, handed to the tag path's assembler and the fire path, so
    # none of the two holds its own idea of which rooms exist or which networks are up. Over
    # the SAME allowlist ingest admits against and the SAME adapter map the tag path sends…
    presence_reader = _presence_reader(cfg, allowlist, registry, rosters)
    return PoliciesWiring(policy=policy, presence_reader=presence_reader, rosters=rosters)


@dataclass(frozen=True)
class PresencePreflightWiring:
    pass


def presence_preflight(
    allowlist: Any, background: Any, content_checks: Any, metrics: Any,
    persons: Any, presence_circles: Any, presence_store: Any, registry: Any,
    rosters: Any,
) -> PresencePreflightWiring:
    if presence_store is not None and content_checks:
        # The R-21/R-31 startup invariant (DEC-54(4)): alarm the day a policy conversation is no
        # longer a subset of a circle. A BACKGROUND task, not a startup gate — it needs the
        # signal-cli roster, and an unreadable roster must not stop Nora (the AUTHORITATIVE…
        background.spawn(
            _presence_preflight(
                allowlist=allowlist,
                rosters=rosters,
                persons=persons,
                circles=presence_circles,
                metrics=metrics,
                # Without her aliases Nora HERSELF is the «1 unmapped account»: her own membership
                # resolves to nobody in identity.toml, exactly the stranger-shape the warning exists
                # for. The tag path's Room gets the same set (`EngagementPipeline._run_admitted`).…
                self_ids=registry.self_aliases(),
            )
        )
    return PresencePreflightWiring()
