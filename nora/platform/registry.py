"""`AdapterRegistry` — builds every enabled platform adapter from config and
resolves each one's own identity before returning (spec's platforms phase).

**Fail-fast is the point.** R-12 (`nora.engagement.stages.admission`) checks
membership in a resolved `PlatformIdentity.aliases`, so an adapter whose
identity nobody could establish must never be handed to the rest of the
process pretending everything is fine — that is exactly the shape of bug
that would let Nora trigger herself. `from_config` raises
`AdapterRegistryError` for any enabled platform that cannot be built or
cannot resolve who it is; the caller
(`nora.app.composition.build_dependencies`) routes that through
`StartupError`, the same as every other fatal, ordered startup step.

Only Signal exists today — `nora.config.PlatformsConfig` has one platform
field, and `SignalPlatformConfig.enabled` was previously unread (`SignalAdapter`
started unconditionally). This module is what finally honours it: a disabled
platform is built and resolved by no one, silently.

**Nora's own account is never written to `identity.toml`** (DEC-9) — that
file describes other people, not Nora. This module never writes to it and
never reads it; the identity it resolves comes from the platform's own daemon
(`nora.platform.signal.account.derive_account` and
`nora.platform.signal.self_id.derive_identifiers`), not from person mapping.
Nora's own ids are typed `nora.domain.identity.Identifier`s — the same model
`identity.toml` uses for everybody else (design/10 §5) — so "is this sender
Nora?" and "which person is this sender?" are answered by the same flattening.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from nora.domain.ports import PlatformIdentity
from nora.platform.signal.account import AccountError, derive_account
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.signal.self_id import MetricsSink


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


@dataclass(frozen=True)
class AdapterRegistry:
    """Built adapters and their resolved identities, keyed by platform name.

    Frozen and built once, at startup, by `from_config` — nothing in this
    class does I/O itself; it is a lookup table over work already done.

    `adapters` is typed `dict[str, SignalAdapter]`, not
    `dict[str, nora.domain.ports.PlatformAdapter]`: only `SignalAdapter`
    exists to put in it today, so there is nothing yet for the port
    annotation to buy here — the same reasoning
    `nora.app.composition.Dependencies.adapter` uses for staying concrete.
    `SignalAdapter` does now genuinely satisfy `PlatformAdapter` in full
    (`tests/contract/test_port_conformance.py::
    test_signal_adapter_satisfies_platform_adapter`, with a static proof at
    `nora.app.composition.build_dependencies`'s `_adapter_satisfies_platform_adapter`
    assignment) — this widens to the port the day a second platform exists to
    put alongside it.
    """

    adapters: dict[str, SignalAdapter] = field(default_factory=dict)
    identities: dict[str, PlatformIdentity] = field(default_factory=dict)

    @classmethod
    async def from_config(
        cls, platforms: _PlatformsConfig, *, metrics: MetricsSink
    ) -> AdapterRegistry:
        """Build+resolve every enabled platform. Today that is Signal alone;
        a second platform is one more `if platforms.<x>.enabled:` block, not a
        rewrite of this method's shape. `platforms` is `cfg.platforms`, not
        `cfg` itself — see `_PlatformsConfig`'s docstring.

        `metrics` is threaded to each adapter so `resolve_identity()`'s
        enrichment can count its own degradation (R-17). It is required for the
        reason `SignalAdapter.__init__` gives."""
        adapters: dict[str, SignalAdapter] = {}
        identities: dict[str, PlatformIdentity] = {}

        signal_cfg = platforms.signal
        if signal_cfg.enabled:
            try:
                account = await derive_account(
                    signal_cfg.base_url, override=signal_cfg.account,
                )
            except AccountError as exc:
                raise AdapterRegistryError(f"signal account error: {exc}") from exc

            adapter = SignalAdapter(signal_cfg.base_url, account, metrics=metrics)
            try:
                identity = await adapter.resolve_identity()
            except Exception as exc:
                raise AdapterRegistryError(
                    f"signal adapter could not resolve its own identity: {exc}"
                ) from exc

            adapters["signal"] = adapter
            identities["signal"] = identity

        return cls(adapters=adapters, identities=identities)

    def get(self, platform: str) -> SignalAdapter:
        return self.adapters[platform]

    def identity_for(self, platform: str) -> PlatformIdentity:
        return self.identities[platform]

    def __contains__(self, platform: str) -> bool:
        return platform in self.adapters

    def __iter__(self):
        return iter(self.adapters.values())

    def __len__(self) -> int:
        return len(self.adapters)
