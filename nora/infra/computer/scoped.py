"""`ScopedComputerdRuntime` — `SandboxRuntime` over per-scope microVMs.

One scope, one VM: `ScopeEndpoints` resolves the scope to its provisioned
endpoint, a lazily-built `ComputerdRuntime` talks to it, and `/v1/identity`
verifies the VM that answered is bound to THAT scope before any call runs
(and again when a connection breaks a cached binding). An unresolvable
scope, an unreachable endpoint and a scope/instance mismatch all read the
same way: `unavailable` — there is no shared-VM or other-scope fallback.

`binding(scope)` is the one place client, probe, browser and manager agree
on an endpoint+instance; every handle the browser manager issues carries the
instance it was verified against, so a recreated VM (a new instance) makes
old handles stale instead of silently routing to a replacement.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import aiohttp

from nora.domain.computer import (
    BytesResult,
    FileResult,
    ListResult,
    OpResult,
    RunResult,
    SandboxStatus,
    StateResult,
)
from nora.domain.computer_vms import ScopedEndpoint, ScopeEndpoints
from nora.infra.computer.client import ComputerdRuntime

log = logging.getLogger("nora.computer.scoped")

__all__ = ["ScopeBinding", "ScopedComputerdRuntime"]


@dataclass(frozen=True, slots=True)
class ScopeBinding:
    """A verified endpoint for one scope: client, endpoint and the VM
    instance identity confirmed on it."""

    endpoint: ScopedEndpoint
    runtime: ComputerdRuntime
    instance: str


_R = TypeVar("_R")


def _unavailable(reason: str, cls: Callable[..., _R]) -> _R:
    return cls(status=SandboxStatus.UNAVAILABLE, reason=reason)


class ScopedComputerdRuntime:
    """Satisfies `nora.domain.ports.SandboxRuntime` (duck-typed: the verbs
    and results match; construction needs the registry)."""

    def __init__(
        self, *, endpoints: ScopeEndpoints, token: str,
        timeout_seconds: float = 30.0, max_output_chars: int = 8000,
        observer: Any = None, verify_seconds: float = 10.0,
    ) -> None:
        self._endpoints = endpoints
        self._token = token
        self._timeout = timeout_seconds
        self._max_output = max_output_chars
        self._observer = observer
        self._verify_seconds = verify_seconds
        self._bound: dict[str, ScopeBinding] = {}

    async def _identity(self, endpoint: ScopedEndpoint) -> dict[str, Any] | None:
        """`GET /v1/identity` on the endpoint; its body or None."""
        url = endpoint.base_url + "/v1/identity"
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._verify_seconds)) as s,
                s.get(url, headers={"Authorization": f"Bearer {self._token}"})
                as response,
            ):
                if response.status != 200:
                    return None
                body = await response.json(content_type=None)
        except Exception:  # noqa: BLE001 - an unanswering endpoint is unverified, never bound
            log.info("scope endpoint identity check failed")
            return None
        return body if isinstance(body, dict) else None

    async def binding(self, scope: str) -> ScopeBinding | None:
        """The verified binding for `scope`, or None. Re-verifies whenever the
        cache holds nothing or the registry's instance moved."""
        try:
            endpoint = await self._endpoints.resolve(scope)
        except Exception:
            log.exception("scope endpoint resolution failed")
            return None
        if endpoint is None:
            return None
        cached = self._bound.get(scope)
        if cached is not None and cached.endpoint == endpoint:
            return cached
        body = await self._identity(endpoint)
        if body is None:
            return None
        bound_scope = body.get("scope")
        if bound_scope not in (None, scope):
            # The VM answers for a DIFFERENT scope: misrouted or rebound.
            log.warning("endpoint %.8s… answered for another scope", scope)
            return None
        instance = str(body.get("instance") or "")
        if endpoint.instance and instance and endpoint.instance != instance:
            log.warning("endpoint %.8s… instance changed since provision", scope)
        binding = ScopeBinding(
            endpoint=endpoint,
            runtime=ComputerdRuntime(
                base_url=endpoint.base_url, token=self._token,
                timeout_seconds=self._timeout, max_output_chars=self._max_output,
                observer=self._observer),
            instance=instance,
        )
        self._bound[scope] = binding
        return binding

    def invalidate(self, scope: str) -> None:
        """Drop a cached binding (an identity change or dead client): the
        next call re-verifies before anything is sent."""
        self._bound.pop(scope, None)

    async def instance(self, scope: str) -> str | None:
        """The VM instance `scope` is currently bound to (the browser
        manager's handle prefix), or None when unresolvable."""
        binding = await self.binding(scope)
        return binding.instance if binding is not None else None

    async def _on(self, scope: str) -> ComputerdRuntime | None:
        binding = await self.binding(scope)
        return binding.runtime if binding is not None else None

    # -- the port ---------------------------------------------------------

    async def run(self, *, scope: str, command: str, timeout_s: float,
                  limits: Any = None) -> RunResult:
        rt = await self._on(scope)
        if rt is None:
            return _unavailable("no VM for this scope", RunResult)
        return await rt.run(scope=scope, command=command, timeout_s=timeout_s,
                            limits=limits)

    async def read(self, *, scope: str, path: str, offset: int = 0,
                   limit: int | None = None) -> FileResult:
        rt = await self._on(scope)
        if rt is None:
            return _unavailable("no VM for this scope", FileResult)
        return await rt.read(scope=scope, path=path, offset=offset, limit=limit)

    async def write_text(self, *, scope: str, path: str, content: str) -> OpResult:
        rt = await self._on(scope)
        if rt is None:
            return _unavailable("no VM for this scope", OpResult)
        return await rt.write_text(scope=scope, path=path, content=content)

    async def edit(self, *, scope: str, path: str, old: str, new: str) -> OpResult:
        rt = await self._on(scope)
        if rt is None:
            return _unavailable("no VM for this scope", OpResult)
        return await rt.edit(scope=scope, path=path, old=old, new=new)

    async def files(self, *, scope: str, pattern: str = "**/*") -> ListResult:
        rt = await self._on(scope)
        if rt is None:
            return _unavailable("no VM for this scope", ListResult)
        return await rt.files(scope=scope, pattern=pattern)

    async def fetch(self, *, scope: str, path: str, max_bytes: int) -> BytesResult:
        rt = await self._on(scope)
        if rt is None:
            return _unavailable("no VM for this scope", BytesResult)
        return await rt.fetch(scope=scope, path=path, max_bytes=max_bytes)

    async def put(self, *, scope: str, path: str, data: bytes) -> OpResult:
        rt = await self._on(scope)
        if rt is None:
            return _unavailable("no VM for this scope", OpResult)
        return await rt.put(scope=scope, path=path, data=data)

    async def state(self, *, scope: str) -> StateResult:
        rt = await self._on(scope)
        if rt is None:
            return _unavailable("no VM for this scope", StateResult)
        return await rt.state(scope=scope)
