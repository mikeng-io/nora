"""The production dependency shutdown shared by the daemon and isolated subjects."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from contextlib import AsyncExitStack
from typing import Any, TypeVar

log = logging.getLogger(__name__)

CloseFn = Callable[[], Awaitable[None]]
_T = TypeVar("_T")


def _push_close_order(
    stack: AsyncExitStack,
    *,
    audit: Any = None,
    managed_session: Any = None,
    blobs: Any = None,
    model_clients: Iterable[Any] = (),
    client: Any = None,
    computer_close: CloseFn | None = None,
    discord_control: Any = None,
    adapter_closes: Iterable[CloseFn] = (),
    bridge_redis: Any = None,
    bridge_stop: Callable[[], None] | None = None,
    health_runner: Any = None,
    background: Any = None,
    subagent_backend: Any = None,
    cognition_shutdown: CloseFn | None = None,
) -> None:
    """Push every closable onto `stack` in the one close order (see `StartupOwner`)."""
    if audit is not None:
        stack.push_async_callback(audit.aclose)
    if managed_session is not None:
        stack.push_async_callback(managed_session.close)
    blob_close = getattr(blobs, "aclose", None)
    if blob_close is not None:
        stack.push_async_callback(blob_close)
    for model_client in reversed(tuple(model_clients)):
        stack.push_async_callback(model_client.close)
    if client is not None:
        stack.push_async_callback(client.close)
    if computer_close is not None:
        stack.push_async_callback(computer_close)
    if discord_control is not None:
        stack.push_async_callback(discord_control.close)
    for close in reversed(tuple(adapter_closes)):
        stack.push_async_callback(close)
    if bridge_redis is not None:
        stack.push_async_callback(bridge_redis.aclose)
    if bridge_stop is not None:
        stack.callback(bridge_stop)
    if health_runner is not None:
        stack.push_async_callback(health_runner.cleanup)
    if background is not None:
        stack.push_async_callback(background.cancel_all)
    if subagent_backend is not None:
        stack.push_async_callback(subagent_backend.shutdown)
    if cognition_shutdown is not None:
        stack.push_async_callback(cognition_shutdown)


async def close_dependencies(deps: Any, *, health_runner: Any = None) -> None:
    """Close in dependency order, unwinding remaining owners after a failure."""
    async with AsyncExitStack() as closing:
        _push_close_order(
            closing,
            audit=deps.audit,
            managed_session=getattr(deps, "managed_session", None),
            blobs=getattr(deps, "blobs", None),
            model_clients=deps.model_clients,
            client=deps.client,
            computer_close=(
                deps.computer_lifecycle.close
                if deps.computer_lifecycle is not None
                else None
            ),
            discord_control=getattr(deps, "discord_control", None),
            adapter_closes=tuple(
                close
                for adapter in deps.registry.adapters.values()
                if (close := getattr(adapter, "close", None)) is not None
            ),
            bridge_redis=(
                deps.bridge_runtime.redis if deps.bridge_runtime is not None else None
            ),
            bridge_stop=(
                deps.bridge_runtime.stop if deps.bridge_runtime is not None else None
            ),
            health_runner=health_runner,
            background=deps.background,
            subagent_backend=deps.subagent_backend,
            cognition_shutdown=deps.cognition_shutdown,
        )


class StartupOwner:
    """What `build_dependencies` owns before `Dependencies` exists.

    Each resource is registered at acquisition — before the next `await`
    that could strand it — so a startup step that fails or is cancelled
    unwinds everything it already built. The registration records only the
    close operation; the caller's `except` runs `aclose()`, which unwinds
    through the same `_push_close_order` a normal shutdown uses. A successful
    build transfers ownership by simply not unwinding: every registered
    resource is reachable from `Dependencies` and `close_dependencies` —
    nothing is held twice and nothing is closed twice. `own_computer` and
    `own_bridge` REPLACE: the factory re-registers as it completes more of
    itself, so the unwind runs the widest close that was reached.

    `_push_close_order` is the shared half: one push order drives both
    unwinds — an `AsyncExitStack` runs it back to front and keeps going
    through a failing close, so one bad resource never strands the ones
    pushed after it.
    """

    @classmethod
    async def run(
        cls, build: Callable[..., Awaitable[_T]], /, *args: Any, **kwargs: Any
    ) -> _T:
        """`await build(*args, owner=<fresh owner>, **kwargs)`, unwinding on failure.

        A raise OR a cancellation anywhere below runs `aclose`, then the
        ORIGINAL exception propagates — even when the unwind itself raises
        or is cancelled: the cleanup failure is logged, never substituted
        for what the caller must see.
        """
        owner = cls()
        try:
            return await build(*args, owner=owner, **kwargs)
        except BaseException:
            try:
                await owner.aclose()
            except BaseException:
                log.exception("startup unwind failed alongside the build failure")
            raise

    def __init__(self) -> None:
        self._audit: Any = None
        self._managed_session: Any = None
        self._blobs: Any = None
        self._model_clients: list[Any] | None = None
        self._client: Any = None
        self._computer_close: CloseFn | None = None
        self._discord_control: Any = None
        self._adapter_closes: list[CloseFn] = []
        self._bridge_redis: Any = None
        self._bridge_stop: Callable[[], None] | None = None
        self._background: Any = None
        self._subagent_backend: Any = None
        self._cognition_shutdown: CloseFn | None = None

    def own_audit(self, audit: Any) -> None:
        self._audit = audit

    def own_managed(self, session: Any) -> None:
        self._managed_session = session

    def own_blobs(self, blobs: Any) -> None:
        self._blobs = blobs

    def own_client(self, client: Any) -> None:
        self._client = client

    def own_model_clients(self, clients: list[Any]) -> None:
        # The list object, not a copy: the model lane and the media
        # describers append routed clients as later steps build them.
        self._model_clients = clients

    def own_computer(self, close: CloseFn) -> None:
        self._computer_close = close

    def own_discord_control(self, control: Any) -> None:
        self._discord_control = control

    def own_adapter(self, close: CloseFn) -> None:
        self._adapter_closes.append(close)

    def own_bridge(self, *, redis: Any, stop: Callable[[], None] | None = None) -> None:
        self._bridge_redis = redis
        self._bridge_stop = stop

    def own_background(self, background: Any) -> None:
        self._background = background

    def own_subagent(self, subagent_backend: Any) -> None:
        self._subagent_backend = subagent_backend

    def own_cognition(self, shutdown: CloseFn) -> None:
        self._cognition_shutdown = shutdown

    async def aclose(self) -> None:
        """Unwind every registered resource in normal shutdown order."""
        async with AsyncExitStack() as closing:
            _push_close_order(
                closing,
                audit=self._audit,
                managed_session=self._managed_session,
                blobs=self._blobs,
                model_clients=self._model_clients or (),
                client=self._client,
                computer_close=self._computer_close,
                discord_control=self._discord_control,
                adapter_closes=self._adapter_closes,
                bridge_redis=self._bridge_redis,
                bridge_stop=self._bridge_stop,
                background=self._background,
                subagent_backend=self._subagent_backend,
                cognition_shutdown=self._cognition_shutdown,
            )
