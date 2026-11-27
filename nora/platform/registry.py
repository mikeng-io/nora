"""`AdapterRegistry` — builds every enabled platform from one table, resolves
each one's own identity, and owns each platform's liveness
(docs/design/platform/README.md).

**One platform's failure stays on that platform.** R-12
(`nora.engagement.stages.admission`) checks membership in a resolved
`PlatformIdentity.aliases`, so an adapter whose identity nobody could
establish must never emit an event. The rule is enforced at PLATFORM scope:
an enabled platform whose identity cannot be resolved is registered DEGRADED
— its `DegradeLatch` refuses sends, reactions and health, its inbound gates
stay closed, and it has NO `identities` entry — while every other platform
starts live. A background `recover` re-resolves it and `promote`s it with no
restart: identity registered → latch cleared → inbound opened.

**Misconfiguration still refuses to start** (`AdapterRegistryError`, routed
through `StartupError` by `nora.app.composition_platforms`): a builder that
cannot even be built (no Discord token, an underivable Signal account) and an
identity answer of 401/403 to Nora's own credentials. A retry cannot cure
those.

**The table is the abstraction.** `PLATFORMS` lists every platform as a name
and a builder; startup, the identity wait, degrade, recovery, promotion, the
inbound gates, the state gauge and `/health` run once over it. A new platform
is a config section, an adapter that joins `DegradeLatch`, and a row.

**Nora's own account is never written to `identity.toml`** (DEC-9) — that
file describes other people, not Nora. This module never writes to it and
never reads it; the identity it resolves comes from the platform's own daemon,
not from person mapping.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Set
from dataclasses import dataclass, field
from typing import Any

from nora.domain.live import LiveSet
from nora.domain.platform_state import PLATFORM_STATES, PlatformState
from nora.domain.ports import MediaSource, PlatformAdapter, PlatformIdentity
from nora.domain.summons import SummonsSink
from nora.infra.voice.hub import VoiceControllerHub
from nora.memory import EffectRecorder
from nora.platform.bridge.client import (
    BridgeAdapter,
    BridgeEndpoint,
    BridgeUnavailableError,
    bridge_not_ready,
)
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.bridge_control import (
    BridgeControlClient,
    BridgeDiscordAdapter,
)
from nora.platform.discord.command_acts import DiscordCommandActs
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.platform.discord.music_notify import ResolveMusicChannel, no_music_channel
from nora.platform.instagram.adapter import InstagramAdapter
from nora.platform.signal.account import AccountError, derive_account
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.signal.self_id import MetricsSink
from nora.platform.whatsapp.adapter import WhatsAppAdapter

log = logging.getLogger("nora.platform.registry")


class AdapterRegistryError(Exception):
    """An enabled platform is MISCONFIGURED: it could not be built, or its
    identity read refused Nora's credentials. Fatal — the caller refuses to
    start. (An identity that is merely unavailable degrades instead.)"""


# A deploy that recreates nora and its bridges in the same moment asks
# `/v1/self` before the bridge's platform login has landed, so the first
# answer is the still-starting one (`bridge_not_ready`). Wait it out once
# for every bridge-backed platform — a slow start, never a guessed
# identity; the bound keeps a bridge that never readies a startup failure
# instead of a hang.
_BRIDGE_IDENTITY_WAIT_SECONDS = 60.0
_BRIDGE_IDENTITY_INTERVAL_SECONDS = 2.0


async def _resolve_bridge_identity(
    adapter: PlatformAdapter,
    *,
    sleep: Callable[[float], Awaitable[None]],
    clock: Callable[[], float],
) -> PlatformIdentity:
    """`resolve_identity` with a bounded wait for a bridge that is
    reachable but still starting. Only `bridge_not_ready` answers are
    waited on — any other failure (another status, a malformed identity)
    raises on the first answer, exactly as before.

    The bound is wall time: each attempt is capped to what remains of it
    and each sleep to what remains, so no request starts or is accepted
    past the deadline — an identity that answers at 61 s is refused, not
    returned."""
    deadline = clock() + _BRIDGE_IDENTITY_WAIT_SECONDS
    waited = False
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            log.warning(
                "%s bridge identity still unavailable after %.0fs",
                adapter.platform, _BRIDGE_IDENTITY_WAIT_SECONDS)
            raise BridgeUnavailableError(
                "/v1/self stayed unavailable through the whole identity wait")
        try:
            identity = await asyncio.wait_for(
                adapter.resolve_identity(), timeout=remaining)
        except Exception as exc:
            if not bridge_not_ready(exc):
                raise
            if clock() >= deadline:
                log.warning(
                    "%s bridge identity still unavailable after %.0fs",
                    adapter.platform, _BRIDGE_IDENTITY_WAIT_SECONDS)
                raise
            if not waited:
                waited = True
                log.info(
                    "%s bridge is up but its identity is not ready yet — "
                    "waiting up to %.0fs",
                    adapter.platform, _BRIDGE_IDENTITY_WAIT_SECONDS)
            await sleep(min(_BRIDGE_IDENTITY_INTERVAL_SECONDS,
                            deadline - clock()))
            continue
        if clock() >= deadline:
            log.warning(
                "%s bridge identity still unavailable after %.0fs",
                adapter.platform, _BRIDGE_IDENTITY_WAIT_SECONDS)
            raise BridgeUnavailableError(
                "/v1/self answered only after the identity wait bound")
        if waited:
            log.info("%s bridge identity resolved after waiting",
                     adapter.platform)
        return identity


@dataclass(frozen=True)
class BuildContext:
    """Everything a platform builder may need, threaded from `from_config`'s
    keywords — one value, so a new platform's builder takes the same single
    argument every other one does."""

    metrics: MetricsSink
    discord_token: str = ""
    music_manager: MusicPlayerManager | None = None
    discord_speaker: Any = None
    voice_hub: VoiceControllerHub | None = None
    voice_guild_ids: frozenset[str] = frozenset()
    resolve_music_channel: ResolveMusicChannel = no_music_channel
    discord_command_acts: DiscordCommandActs | None = None
    discord_control_effects: EffectRecorder | None = None
    instagram_summons: SummonsSink | None = None
    dm_recipients: Mapping[str, Set[str]] = field(default_factory=dict)
    dm_gates: Mapping[str, Callable[[str], Awaitable[bool]]] = field(default_factory=dict)
    signal_consent: Callable[
        [str], Callable[[dict], Awaitable[bool]] | None
    ] | None = None
    bridge_token: str = ""


@dataclass(frozen=True)
class Built:
    """A builder's answer: the adapter, and whether it is bridge-backed (a
    bridge gets the bounded startup identity wait)."""

    adapter: Any
    bridge: bool


Builder = Callable[[Any, BuildContext], Awaitable[Built]]


def _is_bridge(cfg: Any) -> bool:
    return getattr(cfg, "mode", "direct") == "bridge"


async def _build_signal(cfg: Any, ctx: BuildContext) -> Built:
    if _is_bridge(cfg):
        # The standalone signal-bridge holds the daemon; `account` is the
        # wire's account_key — no derive_account against signal-cli.
        return Built(BridgeAdapter(
            "signal", BridgeEndpoint(cfg.base_url, ctx.bridge_token),
            dm_recipients=ctx.dm_recipients.get("signal", frozenset()),
            dm_gate=ctx.dm_gates.get("signal")), bridge=True)
    try:
        account = await derive_account(cfg.base_url, override=cfg.account)
    except AccountError as exc:
        # Misconfiguration: the account cannot be derived from this daemon
        # (0.14.7 serves no accounts endpoint) — fatal, never degraded.
        raise AdapterRegistryError(f"signal account error: {exc}") from exc
    return Built(SignalAdapter(
        cfg.base_url, account, metrics=ctx.metrics,
        dm_recipients=ctx.dm_recipients.get("signal", frozenset()),
        # The private consent seam is bound to the DERIVED daemon account
        # (never the configured claim), so provenance the parser checks is
        # the account signal-cli actually serves.
        on_consent=(ctx.signal_consent(account) if ctx.signal_consent else None),
        dm_gate=ctx.dm_gates.get("signal"),
    ), bridge=False)


async def _build_discord(cfg: Any, ctx: BuildContext) -> Built:
    if _is_bridge(cfg):
        # The standalone discord-bridge owns the Gateway, the music manager
        # and the voice-controller hub; the app holds only proxies.
        endpoint = BridgeEndpoint(cfg.base_url, ctx.bridge_token)
        return Built(BridgeDiscordAdapter(
            delegate=BridgeAdapter("discord", endpoint),
            control=BridgeControlClient(endpoint, effects=ctx.discord_control_effects),
            speaker=ctx.discord_speaker), bridge=True)
    if not ctx.discord_token:
        raise AdapterRegistryError(
            f"discord is enabled but no token was resolved from "
            f"${cfg.token_env} — refusing to start with a platform that "
            "cannot authenticate"
        )
    return Built(DiscordAdapter(
        ctx.discord_token, music_manager=ctx.music_manager,
        speaker=ctx.discord_speaker, voice_hub=ctx.voice_hub,
        voice_guild_ids=ctx.voice_guild_ids,
        resolve_music_channel=ctx.resolve_music_channel, command_acts=ctx.discord_command_acts,
    ), bridge=False)


async def _build_whatsapp(cfg: Any, ctx: BuildContext) -> Built:
    if _is_bridge(cfg):
        return Built(BridgeAdapter(
            "whatsapp", BridgeEndpoint(cfg.base_url, ctx.bridge_token),
            dm_recipients=ctx.dm_recipients.get("whatsapp", frozenset()),
            dm_gate=ctx.dm_gates.get("whatsapp")), bridge=True)
    return Built(WhatsAppAdapter(
        cfg.base_url, dm_recipients=ctx.dm_recipients.get("whatsapp", frozenset()),
        dm_gate=ctx.dm_gates.get("whatsapp"),
    ), bridge=False)


async def _build_instagram(cfg: Any, ctx: BuildContext) -> Built:
    if _is_bridge(cfg):
        return Built(BridgeAdapter(
            "instagram", BridgeEndpoint(cfg.base_url, ctx.bridge_token)),
            bridge=True)
    return Built(InstagramAdapter(cfg.base_url, summons=ctx.instagram_summons),
                 bridge=False)


@dataclass(frozen=True)
class PlatformEntry:
    """One row of the platform table: the platform's name (its
    `[platforms.<name>]` section and its `platform` label everywhere) and the
    builder that turns that section into an adapter. Everything else —
    startup, the identity wait, degrade, recovery, promotion, the inbound
    gates, the state gauge, `/health` — is the registry's, once, for every
    row (docs/design/platform/README.md)."""

    name: str
    build: Builder


#: Every platform Nora knows. Adding one is a config section, an adapter that
#: joins `DegradeLatch`, and a row here.
PLATFORMS: tuple[PlatformEntry, ...] = (
    PlatformEntry("signal", _build_signal),
    PlatformEntry("discord", _build_discord),
    PlatformEntry("whatsapp", _build_whatsapp),
    PlatformEntry("instagram", _build_instagram),
)
PLATFORM_NAMES: tuple[str, ...] = tuple(e.name for e in PLATFORMS)


def bridge_platforms(platforms: Any,
                     table: tuple[PlatformEntry, ...] = PLATFORMS) -> list[str]:
    """Every enabled platform in `mode = "bridge"`, in table order — what the
    composition's pre-registry bridge checks and the stream readers cover."""
    out: list[str] = []
    for entry in table:
        cfg = getattr(platforms, entry.name, None)
        if cfg is not None and cfg.enabled and _is_bridge(cfg):
            out.append(entry.name)
    return out


def _refused(exc: BaseException) -> bool:
    """A 401/403 to Nora's own credentials: misconfiguration, never degraded."""
    return getattr(exc, "status", None) in (401, 403)


#: Background recovery limits (platform-liveness.md «Promotion»): each
#: attempt is bounded; the wait between attempts doubles from the first value
#: up to the cap; attempts never stop — a logged-out bridge is re-paired by a
#: person at a time nobody can predict.
RECOVERY_ATTEMPT_TIMEOUT_SECONDS = 10.0
RECOVERY_FIRST_BACKOFF_SECONDS = 5.0
RECOVERY_MAX_BACKOFF_SECONDS = 300.0


async def _resolve(
    adapter: Any, *, bridge: bool,
    sleep: Callable[[float], Awaitable[None]], clock: Callable[[], float],
) -> PlatformIdentity:
    if bridge:
        return await _resolve_bridge_identity(adapter, sleep=sleep, clock=clock)
    identity: PlatformIdentity = await adapter.resolve_identity()
    return identity


@dataclass
class AdapterRegistry:
    """Built adapters, their resolved identities and each platform's
    liveness state, keyed by platform name.

    `adapters` holds every ENABLED platform, live or degraded; `identities`
    holds only the live ones — a degraded platform never has an entry, and no
    placeholder identity is ever built. `states` covers every platform in the
    table, `disabled` included.

    The one mutation after startup is `promote`, run by the background
    recovery: identity registered → latch cleared → inbound opened, in that
    order (R-12 depends on it).

    `adapters` is typed `dict[str, PlatformAdapter]`, the port, not a concrete
    adapter (`tests/contract/test_port_conformance.py`).
    """

    adapters: dict[str, PlatformAdapter] = field(default_factory=dict)
    identities: dict[str, PlatformIdentity] = field(default_factory=dict)
    states: dict[str, PlatformState] = field(default_factory=dict)
    metrics: MetricsSink | None = None
    _bridged: dict[str, bool] = field(default_factory=dict, repr=False)
    _live: dict[str, asyncio.Event] = field(default_factory=dict, repr=False)
    _aliases: LiveSet[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        for name in self.adapters:
            self.states.setdefault(
                name, "live" if name in self.identities else "degraded")
        for name, state in self.states.items():
            event = self._live.setdefault(name, asyncio.Event())
            if state == "live":
                event.set()
        self._aliases = LiveSet(self._union_aliases)

    @classmethod
    async def from_config(
        cls,
        platforms: Any,
        *,
        metrics: MetricsSink,
        discord_token: str = "",
        music_manager: MusicPlayerManager | None = None,
        discord_speaker: Any = None,
        voice_hub: VoiceControllerHub | None = None,
        voice_guild_ids: frozenset[str] = frozenset(),
        resolve_music_channel: ResolveMusicChannel = no_music_channel,
        discord_command_acts: DiscordCommandActs | None = None,
        discord_control_effects: EffectRecorder | None = None,
        instagram_summons: SummonsSink | None = None,
        dm_recipients: Mapping[str, Set[str]] | None = None,
        signal_consent: Callable[
            [str], Callable[[dict], Awaitable[bool]] | None
        ] | None = None,
        dm_gates: Mapping[str, Callable[[str], Awaitable[bool]]] | None = None,
        bridge_token: str = "",
        owner: Any = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        table: tuple[PlatformEntry, ...] = PLATFORMS,
    ) -> AdapterRegistry:
        """Build and admit every enabled platform in `table`.

        `platforms` is `cfg.platforms` (a `nora.config.PlatformsConfig`), read
        structurally: `getattr(platforms, entry.name)` is the section. A
        missing section is a disabled platform.

        A builder raising `AdapterRegistryError` (a missing token, an
        underivable Signal account) and an identity answer of 401/403 are
        MISCONFIGURATION and refuse startup. Any other identity failure
        degrades that platform alone (platform-liveness.md «Startup»).

        `owner` is the startup's acquisition register (structural — this
        layer does not import `nora.app`): each adapter is owned the moment
        it exists, BEFORE the `resolve_identity` await that can strand its
        session; a raise unwinds every adapter built so far.

        `sleep`/`clock` are the time seam for the bounded bridge wait.
        `table` is the platform table — a test passes its own row."""
        ctx = BuildContext(
            metrics=metrics, discord_token=discord_token,
            music_manager=music_manager, discord_speaker=discord_speaker,
            voice_hub=voice_hub, voice_guild_ids=voice_guild_ids,
            resolve_music_channel=resolve_music_channel,
            discord_command_acts=discord_command_acts,
            discord_control_effects=discord_control_effects,
            instagram_summons=instagram_summons,
            dm_recipients=dm_recipients or {}, dm_gates=dm_gates or {},
            signal_consent=signal_consent, bridge_token=bridge_token,
        )
        adapters: dict[str, PlatformAdapter] = {}
        identities: dict[str, PlatformIdentity] = {}
        states: dict[str, PlatformState] = {}
        bridged: dict[str, bool] = {}

        for entry in table:
            section = getattr(platforms, entry.name, None)
            if section is None or not section.enabled:
                states[entry.name] = "disabled"
                continue
            built = await entry.build(section, ctx)
            adapter = built.adapter
            if owner is not None:
                close = getattr(adapter, "close", None)
                if close is not None:
                    owner.own_adapter(close)
            adapters[entry.name] = adapter
            bridged[entry.name] = built.bridge
            try:
                identity = await _resolve(
                    adapter, bridge=built.bridge, sleep=sleep, clock=clock)
            except Exception as exc:
                kind = "bridge" if built.bridge else "adapter"
                if _refused(exc):
                    raise AdapterRegistryError(
                        f"{entry.name} {kind} refused Nora's credentials while "
                        f"resolving its identity: {exc}") from exc
                # NO `identities` entry, and this is load-bearing: a
                # placeholder identity would put its placeholder value into
                # the very set R-12 and `MentionPolicy` test membership in.
                adapter.degrade(
                    f"{entry.name} {kind} could not resolve its own identity: "
                    f"{type(exc).__name__}: {exc}")
                states[entry.name] = "degraded"
            else:
                identities[entry.name] = identity
                states[entry.name] = "live"

        registry = cls(adapters=adapters, identities=identities, states=states,
                       metrics=metrics, _bridged=bridged)
        registry.publish_states()
        return registry

    # ---- liveness ----------------------------------------------------------

    def state_of(self, platform: str) -> PlatformState:
        return self.states.get(platform, "disabled")

    def is_live(self, platform: str) -> bool:
        return self.states.get(platform) == "live"

    def live_platforms(self) -> frozenset[str]:
        return frozenset(p for p, s in self.states.items() if s == "live")

    def degraded_platforms(self) -> tuple[str, ...]:
        return tuple(p for p, s in self.states.items() if s == "degraded")

    async def wait_live(self, platform: str) -> None:
        """Return once `platform` is live — the gate every inbound path
        (supervised listen, stream reader) awaits before its first read.
        Returns at once for a live platform."""
        await self._live.setdefault(platform, asyncio.Event()).wait()

    def publish_states(self) -> None:
        """`nora_platform_state{platform,state}`, one-hot over
        `PLATFORM_STATES`, for every platform the registry knows."""
        if self.metrics is None:
            return
        for platform, current in self.states.items():
            for state in PLATFORM_STATES:
                self.metrics.gauge("nora_platform_state",
                                   1.0 if state == current else 0.0,
                                   platform=platform, state=state)

    def promote(self, platform: str, identity: PlatformIdentity) -> None:
        """Make a degraded platform live. THE ORDER IS R-12's: the identity is
        registered (so `identity_for` and the live `self_aliases()` view hold
        it) BEFORE the latch clears and BEFORE inbound opens — no event from
        this platform can be admitted while its identity is absent."""
        self.identities[platform] = identity
        self.adapters[platform].promote()  # type: ignore[attr-defined]
        self.states[platform] = "live"
        self.publish_states()
        self._live.setdefault(platform, asyncio.Event()).set()
        log.warning("%s identity resolved — platform promoted to live", platform)

    async def recover(
        self, platform: str, *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        wait_for: Callable[..., Awaitable[Any]] = asyncio.wait_for,
    ) -> None:
        """Re-resolve one degraded platform until it answers, then promote it.

        Each attempt is bounded by `RECOVERY_ATTEMPT_TIMEOUT_SECONDS`; the
        wait after a failure starts at `RECOVERY_FIRST_BACKOFF_SECONDS` and
        doubles to `RECOVERY_MAX_BACKOFF_SECONDS`. Never gives up. A 401/403
        is logged once per occurrence and keeps retrying: at runtime the
        process stays up, and the operator's credential fix is what clears it.

        `sleep`/`wait_for` are the time seam, so a test drives the backoff and
        the attempt bound on a fake clock.
        """
        adapter = self.adapters[platform]
        delay = RECOVERY_FIRST_BACKOFF_SECONDS
        while self.states.get(platform) == "degraded":
            try:
                identity = await wait_for(
                    adapter.resolve_identity(),
                    timeout=RECOVERY_ATTEMPT_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a failed attempt retries
                if _refused(exc):
                    log.error("%s refused Nora's credentials: %s", platform, exc)
                await sleep(delay)
                delay = min(delay * 2, RECOVERY_MAX_BACKOFF_SECONDS)
                continue
            self.promote(platform, identity)

    async def recover_forever(
        self, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """One `recover` per platform degraded at startup, concurrently —
        one platform's backoff never delays another's promotion."""
        degraded = self.degraded_platforms()
        if degraded:
            await asyncio.gather(*(self.recover(p, sleep=sleep) for p in degraded))

    # ---- lookup ------------------------------------------------------------

    def get(self, platform: str) -> PlatformAdapter:
        return self.adapters[platform]

    def identity_for(self, platform: str) -> PlatformIdentity:
        return self.identities[platform]

    def _union_aliases(self) -> frozenset[str]:
        if not self.identities:
            return frozenset()
        return frozenset().union(
            *(identity.aliases for identity in self.identities.values()))

    def self_aliases(self) -> LiveSet[str]:
        """Every id form Nora answers to, ACROSS every LIVE platform — a LIVE
        view, read at each membership test, never a snapshot: a platform
        promoted after composition reaches every holder of this view with no
        rebuild (platform-liveness.md «Promotion»). Holders keep the view;
        a copy (`frozenset(...)`) would freeze the startup answer.

        What `MentionPolicy`/`ReplyToNoraPolicy` test membership in, so that
        "did somebody tag Nora?" is answered the same way wherever the message
        came from.

        A UNION. A Signal phone is `+…` and its ACI a hyphenated UUID, so
        neither can collide with anything; a WhatsApp phone is `+…` on a
        DEDICATED number. The BARE-DIGIT spaces are a different story: a Discord
        snowflake (17-19 digits), a WhatsApp lid and an Instagram interop fbid
        (15-17 digits measured, and Nora's own is 17) are all bare digits drawn
        from overlapping ranges, so they are disjoint by CONVENTION and luck,
        not by construction. The reachable consequence is narrow and is stated
        rather than assumed: a cross-platform false positive needs somebody
        else's id on one platform to equal one of NORA's OWN ids on another,
        and it would make `MentionPolicy` engage a turn that did not tag her —
        never make her miss one, and never resolve a person to the wrong
        `person_id` (`nora.persons` indexes on `(platform, value)` and is
        unaffected). R-12's self-tag check is per-platform and cannot be
        reached by it at all.

        Identity is matched on IMMUTABLE ids only — a renameable handle can
        never appear here, because `username` is not a matchable kind on any
        platform (`nora.domain.identity`) and `Identifier` refuses to carry
        one.

        R-12's self-tag check is deliberately NOT this set: it must stay
        strictly per-platform and reads `identity_for(message.platform)`
        (DEC-102).
        """
        return self._aliases

    def __contains__(self, platform: str) -> bool:
        return platform in self.adapters

    def __iter__(self):
        return iter(self.adapters.values())

    def __len__(self) -> int:
        return len(self.adapters)


class MediaSourceRegistry:
    """The `MediaSource` for one platform, or None — the sibling of
    `AdapterRegistry`, and for the same reason.

    One `SignalMediaSource` for every ref would fetch a Discord attachment by
    asking signal-cli for a Discord snowflake. A
    registry makes "this platform has no fetcher" a value the stage can count
    rather than a wrong call it cannot see.
    """

    def __init__(self, sources: Iterable[MediaSource]) -> None:
        self._by_platform: dict[str, MediaSource] = {s.platform: s for s in sources}

    def for_platform(self, platform: str) -> MediaSource | None:
        return self._by_platform.get(platform)
