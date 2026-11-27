"""`DegradeLatch` — the liveness half of the `PlatformAdapter` contract
(docs/design/platform/README.md «One contract for every platform»).

Every adapter, real or fake, joins it. While latched, the adapter's own
`send`, `react` and `health` are answered HERE, before the adapter's code runs:
a refusal, a refusal, and a degraded health report — no upstream call, so no
adapter can forget the guard and no hung transport can stall `/health`. Once
promoted, each call reaches the adapter's own implementation unchanged.

The wrapping is structural: `__init_subclass__` wraps whichever of the three a
subclass defines, so a new platform's adapter gets it by inheriting this class.
The latch state is ONE object (`LatchState`); a facade derived from an adapter
(`BridgeAdapter.bind_owner`, a voice front) shares the root's object through
`share_latch`, so it cannot send past it.

Lives in `nora.domain` so `nora.platform` adapters and `nora.testing` fakes can
both depend on it under the `layered architecture` import contract.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any

from nora.domain.ports import AdapterHealth, ReactionResult, SendResult

log = logging.getLogger("nora.domain.latch")

_GATED = ("send", "react", "health")


class LatchState:
    """The one mutable fact: why this adapter is degraded, or `""` when it is not."""

    __slots__ = ("reason",)

    def __init__(self) -> None:
        self.reason = ""


def _refusal(name: str, adapter: Any) -> Any:
    if name == "react":
        # `supported=False`: a degraded adapter cannot try at all, the same
        # shape as a wiring gap (`indicate` counts the two differently).
        return ReactionResult(ok=False, supported=False)
    if name == "health":
        return AdapterHealth(platform=adapter.platform, connected=False,
                             degraded=True, detail=adapter._latch.reason)
    # `send` and every extra send path a subclass names: NOT retryable — the
    # registry's recovery, not a caller's retry, is what clears it.
    return SendResult(ok=False)


def _gate(name: str, method: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(method, "__latch_gated__", False):
        return method

    @functools.wraps(method)
    async def gated(self: Any, *args: Any, **kwargs: Any) -> Any:
        if self._latch.reason:
            if name != "health":
                log.debug("refusing %s on %s: degraded (%s)",
                          name, self.platform, self._latch.reason)
            return _refusal(name, self)
        return await method(self, *args, **kwargs)

    gated.__latch_gated__ = True  # type: ignore[attr-defined]
    return gated


class DegradeLatch:
    """Mixin: `degrade(reason)`, `promote()`, `degraded`, `degraded_reason`."""

    platform: str
    #: Further public SEND paths a subclass exposes besides `send` (answered
    #: with `SendResult(ok=False)` while latched), e.g. a body other adapters
    #: delegate to.
    latch_send_paths: tuple[str, ...] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for name in (*_GATED, *cls.__dict__.get("latch_send_paths", ())):
            method = cls.__dict__.get(name)
            if method is not None:
                setattr(cls, name, _gate(name, method))

    @property
    def _latch(self) -> LatchState:
        state = self.__dict__.get("_latch_state")
        if state is None:
            state = LatchState()
            self.__dict__["_latch_state"] = state
        return state

    def share_latch(self, root: DegradeLatch) -> None:
        """Bind this facade to `root`'s latch — one state, never a copy."""
        self.__dict__["_latch_state"] = root._latch

    def degrade(self, reason: str) -> None:
        self._latch.reason = reason or "degraded"
        log.error("%s is ENABLED but DEGRADED: %s. It hears and sends nothing "
                  "until its identity resolves; the other platforms are "
                  "unaffected.", self.platform, self._latch.reason)

    def promote(self) -> None:
        self._latch.reason = ""

    @property
    def degraded(self) -> bool:
        return bool(self._latch.reason)

    @property
    def degraded_reason(self) -> str:
        return self._latch.reason
