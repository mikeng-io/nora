"""Validate a changed identity/policy file before publishing one new snapshot.

The stable facades inherit the existing value's methods. Only dataclass field
reads go through the holder, so even a method captured at composition reads the
current snapshot. Poll and swap contain no await: readers on the process event
loop cannot observe a partially published value.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from typing import Any, Generic, TypeVar

from nora.memory_routing import build_workspace_router
from nora.obs.metrics import Metrics
from nora.persons import Persons, load_persons
from nora.policy import Policy, load_policy

T = TypeVar("T")

log = logging.getLogger("nora.config_reload")
# ponytail: small local files, synchronously hashed every 3s; use a worker only
# if measured file size or event-loop latency makes this read material.
POLL_SECONDS = 3.0


class _Live(Generic[T]):
    _field_names: frozenset[str]

    def __init__(self, value: T) -> None:
        object.__setattr__(self, "_current", value)

    @property
    def current(self) -> T:
        return object.__getattribute__(self, "_current")

    def swap(self, value: T) -> None:
        object.__setattr__(self, "_current", value)

    def __getattribute__(self, name: str) -> Any:
        if name in object.__getattribute__(self, "_field_names"):
            return getattr(object.__getattribute__(self, "_current"), name)
        return object.__getattribute__(self, name)


class LivePolicy(_Live[Policy], Policy):
    _field_names = frozenset(f.name for f in fields(Policy))


class LivePersons(_Live[Persons], Persons):
    _field_names = frozenset(f.name for f in fields(Persons))


class RestartRequiredError(ValueError):
    """A field's consumer is built at boot; refuse this entire candidate."""


class FileReload(Generic[T]):
    def __init__(
        self, path: Path, holder: _Live[T], loader: Callable[[Path], T],
        validate: Callable[[T], None], metrics: Metrics,
    ) -> None:
        self.path, self.holder, self.loader = path, holder, loader
        self.validate, self.metrics = validate, metrics
        self._seen = self._fingerprint()
        self._applied = self._seen

    def _fingerprint(self) -> tuple[int, bytes]:
        return (self.path.stat().st_mtime_ns, hashlib.sha256(self.path.read_bytes()).digest())

    def poll(self) -> None:
        from nora.domain.config_reload import record_reload

        try:
            signature = self._fingerprint()
        except OSError as exc:
            signature = (-1, type(exc).__name__.encode())
        if signature == self._seen:
            return
        unchanged = signature[1] == self._seen[1]
        self._seen = signature
        if unchanged:
            return
        try:
            candidate = self.loader(self.path)
            if self._fingerprint() != signature:
                raise ValueError("file changed while being validated")
            self.validate(candidate)
        except RestartRequiredError as exc:
            log.warning("%s reload requires restart: %s", self.path.name, exc)
            record_reload(self.metrics, self.path.name, "restart_required")
        except Exception as exc:  # noqa: BLE001 — retain the last good state on any validation failure
            # Loader exceptions may quote private TOML values. Name the error
            # class, never serialize their input or a source path into logs.
            log.warning("%s reload refused: %s", self.path.name, type(exc).__name__)
            record_reload(self.metrics, self.path.name, "refused")
        else:
            self.holder.swap(candidate)
            self._applied = signature
            record_reload(self.metrics, self.path.name, "applied")

    def status(self) -> dict[str, object]:
        try:
            signature = self._fingerprint()
            return {"stale": signature[1] != self._applied[1]}
        except OSError:
            return {"stale": True}

    async def run(self) -> None:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            self.poll()


def validate_pair(policy: Policy, persons: Persons, ai_peer: str, default_workspace: str) -> None:
    if ai_peer in persons.by_person:
        raise ValueError("identity.toml uses the id reserved for Nora's own aiPeer")
    build_workspace_router(
        policy=policy, bindings=persons.honcho_workspaces, default_workspace=default_workspace,
    )


class ReloadFiles:
    def __init__(
        self, *, policy_path: Path, identity_path: Path, ai_peer: str,
        default_workspace: str, metrics: Metrics,
        validate_persons: Callable[[Persons], object] = lambda _: None,
    ) -> None:
        self.persons = LivePersons(load_persons(identity_path))
        self.policy = LivePolicy(load_policy(policy_path))
        validate_pair(self.policy, self.persons, ai_peer, default_workspace)
        validate_persons(self.persons)
        boot_policy, boot_persons = self.policy.current, self.persons.current

        def check_policy(candidate: Policy) -> None:
            validate_pair(candidate, self.persons, ai_peer, default_workspace)
            if (candidate.units_declared, candidate._unit_workspace) != (
                boot_policy.units_declared, boot_policy._unit_workspace,
            ):
                raise RestartRequiredError("policy.units workspace binding")

        def check_persons(candidate: Persons) -> None:
            validate_pair(self.policy, candidate, ai_peer, default_workspace)
            validate_persons(candidate)
            if candidate.honcho_workspaces != boot_persons.honcho_workspaces:
                raise RestartRequiredError("identity.workspaces Honcho binding")

        self.files = (
            FileReload(policy_path, self.policy, load_policy, check_policy, metrics),
            FileReload(identity_path, self.persons, load_persons, check_persons, metrics),
        )

    def poll(self) -> None:
        for file in self.files:
            file.poll()

    def status(self) -> dict[str, dict[str, object]]:
        return {file.path.stem: file.status() for file in self.files}

    async def run(self) -> None:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            self.poll()
