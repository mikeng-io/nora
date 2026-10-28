"""`AdapterRegistry` — builds every enabled platform adapter from config and
resolves each one's own identity before returning.

**Fail-fast is the point.** R-12 (`nora.engagement.stages.admission`) checks
membership in a resolved `PlatformIdentity.aliases`, so an adapter whose
identity nobody could establish must never be handed to the rest of the
process pretending everything is fine — that is exactly the shape of bug
that would let Nora trigger herself. `from_config` raises
`AdapterRegistryError` for any enabled platform that cannot be built or
cannot resolve who it is; the caller
(`nora.app.composition.build_dependencies`) routes that through
`StartupError`, the same as every other fatal, ordered startup step.

Each enabled platform is one more `if platforms.<x>.enabled:` block — Signal,
Discord (DEC-102), WhatsApp and Instagram. A disabled platform is built and
resolved by no one, silently.

**Instagram is the one exception to fail-fast, and it is Instagram-shaped.**
Failing to resolve identity means a misconfiguration on Signal and Discord;
on Instagram it means *a captcha happened overnight*, and under the unmodified rule one
checkpoint stops Nora starting AT ALL — Signal and Discord included. So an
enabled Instagram adapter that cannot authenticate is built and registered
DEGRADED: it answers no sends, emits no events, logs one ERROR line naming the
reason, and reports `degraded` on Nora's `/health`. It is also registered with
NO entry in `identities`, deliberately — see the block itself. This is an
exception with a stated reason, not a general loosening; do not extend it to
WhatsApp.

**Nora's own account is never written to `identity.toml`** (DEC-9) — that
file describes other people, not Nora. This module never writes to it and
never reads it; the identity it resolves comes from the platform's own daemon
(`nora.platform.signal.account.derive_account` and
`nora.platform.signal.self_id.derive_identifiers`), not from person mapping.
Nora's own ids are typed `nora.domain.identity.Identifier`s — the same model
`identity.toml` uses for everybody else — so "is this sender
Nora?" and "which person is this sender?" are answered by the same flattening.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass, field
from typing import Any, Protocol

from nora.domain.ports import MediaSource, PlatformAdapter, PlatformIdentity
from nora.domain.summons import SummonsSink
from nora.infra.voice.hub import VoiceControllerHub
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.music_manager import MusicPlayerManager
from nora.platform.discord.music_notify import ResolveMusicChannel, no_music_channel
from nora.platform.instagram.adapter import InstagramAdapter
from nora.platform.signal.account import AccountError, derive_account
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.signal.self_id import MetricsSink
from nora.platform.whatsapp.adapter import WhatsAppAdapter

log = logging.getLogger("nora.platform.registry")


class AdapterRegistryError(Exception):
    """An enabled adapter could not be built, or could not resolve its own
    identity. Fatal — the caller is expected to refuse to start."""


class _SignalPlatformConfig(Protocol):
    @property
    def base_url(self) -> str: ...
    @property
    def account(self) -> str: ...
    @property
    def enabled(self) -> bool: ...


class _DiscordPlatformConfig(Protocol):
    @property
    def enabled(self) -> bool: ...
    @property
    def token_env(self) -> str: ...


class _WhatsAppPlatformConfig(Protocol):
    """No secret and no account field — the bridge holds the paired session
    and answers `GET /self`; see `nora.config.WhatsAppPlatformConfig`."""
    @property
    def enabled(self) -> bool: ...
    @property
    def base_url(self) -> str: ...


class _InstagramPlatformConfig(Protocol):
    """Same shape as WhatsApp's and for the same reason — the bridge holds the
    cookie session and answers `GET /health`; see
    `nora.config.InstagramPlatformConfig`."""
    @property
    def enabled(self) -> bool: ...
    @property
    def base_url(self) -> str: ...


class _PlatformsConfig(Protocol):
    """The narrow slice of `nora.config.Config` this module needs — not
    `nora.config.Config` itself, which `nora.platform` is forbidden from
    importing (`layered architecture` / `only app and __main__ read config`,
    both enforced by `lint-imports`). `nora.app.composition` passes
    `cfg.platforms` (a `nora.config.PlatformsConfig`), which satisfies this
    shape structurally without this module ever seeing `nora.config`.

    Every member is a read-only `@property`, not a plain attribute: mypy
    checks a mutable Protocol attribute for exact nominal type equality
    (so a mutation through the Protocol view can't violate what the
    concrete type promised), which a nested Protocol attribute — `signal:
    _SignalPlatformConfig` — can never satisfy structurally against
    `nora.config.PlatformsConfig`'s pydantic-model `signal` field. A
    read-only property is checked covariantly instead, which is what
    structural (duck-typed) compatibility actually needs here.
    """

    @property
    def signal(self) -> _SignalPlatformConfig: ...
    @property
    def discord(self) -> _DiscordPlatformConfig: ...
    @property
    def whatsapp(self) -> _WhatsAppPlatformConfig: ...
    @property
    def instagram(self) -> _InstagramPlatformConfig: ...


@dataclass(frozen=True)
class AdapterRegistry:
    """Built adapters and their resolved identities, keyed by platform name.

    Frozen and built once, at startup, by `from_config` — nothing in this
    class does I/O itself; it is a lookup table over work already done.

    `adapters` is typed `dict[str, PlatformAdapter]`, the port, not a concrete
    adapter. Both
    concrete adapters satisfy `PlatformAdapter` in full
    (`tests/contract/test_port_conformance.py`), so a caller reading through
    this registry never needs to know which one it got.
    """

    adapters: dict[str, PlatformAdapter] = field(default_factory=dict)
    identities: dict[str, PlatformIdentity] = field(default_factory=dict)

    @classmethod
    async def from_config(
        cls,
        platforms: _PlatformsConfig,
        *,
        metrics: MetricsSink,
        discord_token: str = "",
        music_manager: MusicPlayerManager | None = None,
        discord_speaker: Any = None,
        voice_hub: VoiceControllerHub | None = None,
        voice_guild_ids: frozenset[str] = frozenset(),
        resolve_music_channel: ResolveMusicChannel = no_music_channel,
        instagram_summons: SummonsSink | None = None,
        dm_recipients: Mapping[str, Set[str]] | None = None,
    ) -> AdapterRegistry:
        """Build+resolve every enabled platform — Signal, Discord, WhatsApp,
        Instagram; each one more `if platforms.<x>.enabled:` block, not a
        rewrite of this method's shape. Instagram's block is the one that
        degrades instead of raising — see this module's docstring.

        `platforms` is `cfg.platforms`, not `cfg` itself — see
        `_PlatformsConfig`'s docstring.

        `discord_token` is the one exception to "this module never reads
        secrets": it arrives already resolved from `platforms.discord.
        token_env` by the caller (`nora.app.composition.build_dependencies`,
        the same place every other `*_env` secret — the LLM key, the route
        key — is resolved before being handed to whatever constructs the
        client that needs it). An enabled platform with an empty token is a
        startup failure, not a broken adapter allowed to build (fail-fast is
        the point — see this module's own docstring).

        `metrics` is threaded to each adapter so `resolve_identity()`'s
        enrichment can count its own degradation (R-17). It is required for the
        reason `SignalAdapter.__init__` gives.

        `music_manager` is `None` unless
        `[music] enabled` — composition builds it, this module only threads it
        through to `DiscordAdapter`, same shape as `discord_token`.

        `voice_hub` is the audio-controller's
        per-guild session registry — `None` when `[speech.tts]
        sidecars` is empty, which leaves every voice join and speech
        stream failing closed.

        `resolve_music_channel`
        maps a guild id to the channel its music announcements belong in, from
        a `policy.toml` room's `music_channel`. Threaded, never consulted here
        — same posture as `music_manager`. The default answers `None` for every
        guild (announce where the session started), so a harness that builds a
        registry without it is
        unaffected.

        `instagram_summons` is where the
        Instagram adapter hands a `summons` SSE event, built by composition
        (`nora.app.summons_inbox.SummonsInbox`). Threaded, never consulted
        here — same posture as `music_manager`.

        `dm_recipients` maps a platform to every direct-message address its
        people hold in `identity.toml` (`Persons.direct_addresses`). Signal's
        and WhatsApp's senders refuse a 1:1 message to any other address; a
        platform missing from it refuses every 1:1 message."""
        dms = dm_recipients or {}
        adapters: dict[str, PlatformAdapter] = {}
        identities: dict[str, PlatformIdentity] = {}

        signal_cfg = platforms.signal
        if signal_cfg.enabled:
            try:
                account = await derive_account(
                    signal_cfg.base_url, override=signal_cfg.account,
                )
            except AccountError as exc:
                raise AdapterRegistryError(f"signal account error: {exc}") from exc

            adapter = SignalAdapter(
                signal_cfg.base_url, account, metrics=metrics,
                dm_recipients=dms.get("signal", frozenset()),
            )
            try:
                identity = await adapter.resolve_identity()
            except Exception as exc:
                raise AdapterRegistryError(
                    f"signal adapter could not resolve its own identity: {exc}"
                ) from exc

            adapters["signal"] = adapter
            identities["signal"] = identity

        discord_cfg = platforms.discord
        if discord_cfg.enabled:
            if not discord_token:
                raise AdapterRegistryError(
                    f"discord is enabled but no token was resolved from "
                    f"${discord_cfg.token_env} — refusing to start with a "
                    "platform that cannot authenticate"
                )
            discord_adapter = DiscordAdapter(
                discord_token, music_manager=music_manager,
                speaker=discord_speaker,
                voice_hub=voice_hub,
                voice_guild_ids=voice_guild_ids,
                resolve_music_channel=resolve_music_channel,
            )
            try:
                discord_identity = await discord_adapter.resolve_identity()
            except Exception as exc:
                raise AdapterRegistryError(
                    f"discord adapter could not resolve its own identity: {exc}"
                ) from exc

            adapters["discord"] = discord_adapter
            identities["discord"] = discord_identity

        whatsapp_cfg = platforms.whatsapp
        if whatsapp_cfg.enabled:
            # Same shape as Discord's block, one platform later: no token to
            # resolve (the bridge holds the session), so the ONLY failure mode
            # here is identity — an unpaired or unreachable bridge must fail
            # startup, not produce a deaf adapter (fail-fast is the point —
            # this module's own docstring).
            whatsapp_adapter = WhatsAppAdapter(
                whatsapp_cfg.base_url, dm_recipients=dms.get("whatsapp", frozenset()),
            )
            try:
                whatsapp_identity = await whatsapp_adapter.resolve_identity()
            except Exception as exc:
                raise AdapterRegistryError(
                    f"whatsapp adapter could not resolve its own identity: {exc}"
                ) from exc

            adapters["whatsapp"] = whatsapp_adapter
            identities["whatsapp"] = whatsapp_identity

        instagram_cfg = platforms.instagram
        if instagram_cfg.enabled:
            # THE ONE BLOCK THAT DOES NOT FAIL FAST. See this module's
            # docstring for why, and `nora.platform.instagram.adapter` for what
            # "degraded" is defined to mean.
            instagram_adapter = InstagramAdapter(
                instagram_cfg.base_url, summons=instagram_summons,
            )
            try:
                instagram_identity = await instagram_adapter.resolve_identity()
            except Exception as exc:  # noqa: BLE001 — degrade, never refuse to start
                instagram_adapter.degrade(f"{type(exc).__name__}: {exc}")
                adapters["instagram"] = instagram_adapter
                # NO `identities` ENTRY, and this is load-bearing rather than
                # an omission. `self_aliases()` is a UNION over this mapping
                # and `PlatformIdentity` derives `aliases` from `primary`, so a
                # placeholder identity built to keep the shape rectangular
                # would put its placeholder value — `""` for the obvious
                # choice — into the very set `MentionPolicy` and
                # `ReplyToNoraPolicy` test membership in. Inventing an answer
                # to "who is Nora" is exactly the R-12 bug class fail-fast
                # exists to prevent, and a degraded adapter emits no messages,
                # so nothing ever asks `identity_for("instagram")`.
            else:
                adapters["instagram"] = instagram_adapter
                identities["instagram"] = instagram_identity

        return cls(adapters=adapters, identities=identities)

    def get(self, platform: str) -> PlatformAdapter:
        return self.adapters[platform]

    def identity_for(self, platform: str) -> PlatformIdentity:
        return self.identities[platform]

    def self_aliases(self) -> frozenset[str]:
        """Every id form Nora answers to, ACROSS every enabled platform.

        What `MentionPolicy`/`ReplyToNoraPolicy` test membership in, so that
        "did somebody tag Nora?" is answered the same way wherever the message
        came from. Built here rather than inline at the composition root
        because it is a property of the registry — and because an expression
        living only inside `build_dependencies` cannot be called by a test
        without re-typing it, which is a test that asserts about its own copy
        rather than about the code.

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
        if not self.identities:
            return frozenset()
        return frozenset().union(
            *(identity.aliases for identity in self.identities.values())
        )

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
