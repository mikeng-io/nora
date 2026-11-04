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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
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

#: A verified binding is trusted for this long, then `/v1/identity` is asked
#: again — a VM ownership change is re-verified, never cached forever.
BINDING_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class ScopeBinding:
    """A verified endpoint for one scope: client, endpoint and the VM
    instance identity confirmed on it."""

    endpoint: ScopedEndpoint
    runtime: ComputerdRuntime
    instance: str


@dataclass(frozen=True, slots=True)
class _Bound:
    binding: ScopeBinding
    verified_at: datetime


_R = TypeVar("_R")


def _unavailable(reason: str, cls: Callable[..., _R]) -> _R:
    return cls(status=SandboxStatus.UNAVAILABLE, reason=reason)


class ScopedComputerdRuntime:
    """Satisfies `nora.domain.ports.SandboxRuntime` (duck-typed: the verbs
    and results match; construction needs the registry).

    Binding is exact: a scope resolves only to its own registry record, the
    record must carry the host-published `instance`, and `/v1/identity` must
    answer the SAME scope and the SAME non-empty instance. A VM answering
    for another scope, an empty or mismatched instance, an unresolvable
    scope and an unreachable endpoint all read the same way: `unavailable` —
    there is no shared-VM or other-scope fallback, and an empty registry
    instance never silently authorizes. A failed verb or a stale identity
    invalidates the cached binding, so the next call re-verifies."""

    def __init__(
        self, *, endpoints: ScopeEndpoints, token: str,
        timeout_seconds: float = 30.0, max_output_chars: int = 8000,
        observer: Any = None, verify_seconds: float = 10.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._endpoints = endpoints
        self._token = token
        self._timeout = timeout_seconds
        self._max_output = max_output_chars
        self._observer = observer
        self._verify_seconds = verify_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._bound: dict[str, _Bound] = {}

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
        """The verified binding for `scope`, or None.

        Re-verifies whenever the cache is cold or the registry's record
        moved; an endpoint whose identity no longer matches — another
        scope's answer, a changed or empty instance — is never bound."""
        if not isinstance(scope, str) or not scope:
            return None
        try:
            endpoint = await self._endpoints.resolve(scope)
        except Exception:
            log.exception("scope endpoint resolution failed")
            return None
        if endpoint is None or not endpoint.base_url or not endpoint.instance:
            # No record, no address, or a pre-`ready` record that never
            # published its instance: none authorizes a call.
            return None
        cached = self._bound.get(scope)
        if (cached is not None and cached.binding.endpoint == endpoint
                and (self._clock() - cached.verified_at).total_seconds()
                < BINDING_SECONDS):
            return cached.binding
        body = await self._identity(endpoint)
        if body is None:
            self._bound.pop(scope, None)
            return None
        if body.get("scope") != scope:
            # The VM answers for a DIFFERENT scope — or for none: misrouted
            # or rebound, never adopted.
            log.warning("endpoint answered for another scope")
            self._bound.pop(scope, None)
            return None
        instance = str(body.get("instance") or "")
        if not instance or instance != endpoint.instance:
            # The live instance must equal the host-published one exactly:
            # a recreated VM under an old record is a stale authority.
            log.warning("endpoint instance mismatch (stale authority)")
            self._bound.pop(scope, None)
            return None
        binding = ScopeBinding(
            endpoint=endpoint,
            runtime=ComputerdRuntime(
                base_url=endpoint.base_url, token=self._token,
                timeout_seconds=self._timeout, max_output_chars=self._max_output,
                observer=self._observer),
            instance=instance,
        )
        self._bound[scope] = _Bound(binding=binding, verified_at=self._clock())
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
    #
    # Every verb goes through `_call`: a raised error or an `unavailable`
    # result drops the cached binding, so the next call re-verifies the VM's
    # identity before it is trusted again.

    async def _call(self, scope: str, cls: Callable[..., _R],
                    verb: Callable[[ComputerdRuntime], Awaitable[_R]]) -> _R:
        rt = await self._on(scope)
        if rt is None:
            return _unavailable("no VM for this scope", cls)
        try:
            got = await verb(rt)
        except Exception:
            self.invalidate(scope)
            raise
        if getattr(got, "status", None) is SandboxStatus.UNAVAILABLE:
            self.invalidate(scope)
        return got

    async def run(self, *, scope: str, command: str, timeout_s: float,
                  limits: Any = None) -> RunResult:
        return await self._call(scope, RunResult, lambda rt: rt.run(
            scope=scope, command=command, timeout_s=timeout_s, limits=limits))

    async def read(self, *, scope: str, path: str, offset: int = 0,
                   limit: int | None = None) -> FileResult:
        return await self._call(scope, FileResult, lambda rt: rt.read(
            scope=scope, path=path, offset=offset, limit=limit))

    async def write_text(self, *, scope: str, path: str, content: str) -> OpResult:
        return await self._call(scope, OpResult, lambda rt: rt.write_text(
            scope=scope, path=path, content=content))

    async def edit(self, *, scope: str, path: str, old: str, new: str) -> OpResult:
        return await self._call(scope, OpResult, lambda rt: rt.edit(
            scope=scope, path=path, old=old, new=new))

    async def files(self, *, scope: str, pattern: str = "**/*") -> ListResult:
        return await self._call(scope, ListResult, lambda rt: rt.files(
            scope=scope, pattern=pattern))

    async def fetch(self, *, scope: str, path: str, max_bytes: int) -> BytesResult:
        return await self._call(scope, BytesResult, lambda rt: rt.fetch(
            scope=scope, path=path, max_bytes=max_bytes))

    async def put(self, *, scope: str, path: str, data: bytes) -> OpResult:
        return await self._call(scope, OpResult, lambda rt: rt.put(
            scope=scope, path=path, data=data))

    async def state(self, *, scope: str) -> StateResult:
        return await self._call(scope, StateResult, lambda rt: rt.state(scope=scope))
