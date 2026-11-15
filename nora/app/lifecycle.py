"""The production dependency shutdown shared by the daemon and isolated subjects."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any


async def close_dependencies(deps: Any, *, health_runner: Any = None) -> None:
    """Close in dependency order, unwinding remaining owners after a failure."""
    async with AsyncExitStack() as closing:
        closing.push_async_callback(deps.audit.aclose)
        for client in reversed(deps.model_clients):
            closing.push_async_callback(client.close)
        closing.push_async_callback(deps.client.close)
        if deps.computer_lifecycle is not None:
            closing.push_async_callback(deps.computer_lifecycle.close)
        if getattr(deps, "discord_control", None) is not None:
            closing.push_async_callback(deps.discord_control.close)
        for adapter in reversed(tuple(deps.registry.adapters.values())):
            close = getattr(adapter, "close", None)
            if close is not None:
                closing.push_async_callback(close)
        if deps.bridge_runtime is not None:
            closing.push_async_callback(deps.bridge_runtime.redis.aclose)
            closing.callback(deps.bridge_runtime.stop)
        if health_runner is not None:
            closing.push_async_callback(health_runner.cleanup)
        closing.push_async_callback(deps.background.cancel_all)
        if deps.subagent_backend is not None:
            closing.push_async_callback(deps.subagent_backend.shutdown)
        if deps.cognition_shutdown is not None:
            closing.push_async_callback(deps.cognition_shutdown)
