"""Scope → VM resolution: which microVM serves one computer scope.

One policy unit (or one canonical private-DM scope, `(person, anchor unit)`)
owns ONE sbx microVM; its workspace tools, computerd and browser all live in
it. `nora-computer scope provision` writes the registry this reads: one JSON
file per scope under `[computer] scope_registry` naming the scope's endpoint
(`base_url`) and the sandbox name. `nora/domain/computer.computer_unit_scope`
is the scope key; a DM's scope comes from the private-DM hash, and neither is
EVER resolved to another scope's VM: an unknown scope resolves to `None`,
which the caller reads as `unavailable`, never as «use the shared VM».

`ScopedEndpoint.instance` is the VM's boot identity (`/v1/identity`): a
recreated VM is a NEW instance, and every handle bound to the old one is
stale. Resolution verifies the endpoint's claimed scope against the registry
before a handle is issued, and re-verifies on reconnect — the endpoint alone
proves nothing (a port can be rebound).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

log = logging.getLogger("nora.computer.vms")

__all__ = ["FileScopeEndpoints", "ScopeEndpoints", "ScopedEndpoint"]


@dataclass(frozen=True, slots=True)
class ScopedEndpoint:
    """Where one scope's VM answers: the endpoint and what provisioned it."""

    scope: str
    base_url: str
    name: str = ""
    instance: str = ""


@runtime_checkable
class ScopeEndpoints(Protocol):
    """The registry of provisioned scope VMs. Implementations:
    `FileScopeEndpoints` (the `nora-computer`-written directory) and
    `nora.testing.FakeScopeEndpoints`. Never raises: a scope it cannot
    resolve is `None`, read by the caller as `unavailable`."""

    async def resolve(self, scope: str) -> ScopedEndpoint | None:
        """The endpoint for `scope`, or None when no VM is provisioned for it
        or its record is unreadable. Never a fallback to another scope."""
        ...

    async def registered(self) -> tuple[str, ...]:
        """Every scope the registry currently names (probe/sweep breadth)."""
        ...


class FileScopeEndpoints:
    """`ScopeEndpoints` over the `nora-computer scope provision` registry:
    `<dir>/<scope>.json` files holding `{scope, endpoint, name, instance}` —
    `instance` empty until `nora-computer scope observe` publishes the VM's
    own /v1/identity (the two-phase readiness contract; an empty instance
    authorizes nothing). Read fresh
    each call (the directory is the provisioning seam; a stale cache would
    route to a destroyed VM). A malformed record is logged and skipped, never
    trusted."""

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)

    async def resolve(self, scope: str) -> ScopedEndpoint | None:
        if not scope or not all(c in "0123456789abcdef" for c in scope):
            return None
        try:
            raw = json.loads((self._dir / f"{scope}.json").read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            log.exception("scope endpoint record unreadable")
            return None
        if not isinstance(raw, dict) or raw.get("scope") != scope:
            log.warning("scope endpoint record for %.8s… fails its binding", scope)
            return None
        endpoint = raw.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
            log.warning("scope endpoint record for %.8s… has no endpoint", scope)
            return None
        return ScopedEndpoint(
            scope=scope, base_url=endpoint.rstrip("/"),
            name=str(raw.get("name") or ""), instance=str(raw.get("instance") or ""),
        )

    async def registered(self) -> tuple[str, ...]:
        try:
            names = sorted(p.name for p in self._dir.glob("*.json"))
        except OSError:
            log.exception("scope endpoint registry unreadable")
            return ()
        return tuple(n[:-5] for n in names)
