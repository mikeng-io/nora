"""The admission invocation's captured effect and loader indexes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, replace
from types import MappingProxyType
from typing import Any

from nora.domain.identity import BoundAccount
from nora.domain.management import Scope
from nora.persons import Persons
from nora.policy import Policy


@dataclass(frozen=True)
class _PersonPlan:
    person_id: str
    display_name: str
    social: bool


@dataclass(frozen=True)
class PreparedImport:
    """The exact intended effect — captured and validated ONCE per
    invocation, immutable for every consumer. The loaders' model, the
    journaled snapshot, the retained private source text and the derived
    effect rows all describe the same read: a source change during setup
    either keeps this captured effect or never reaches a write — it never
    substitutes a second read behind the plan the first one reported."""
    import_id: str
    _snapshot_json: str = field(repr=False)
    persons: Persons
    policy: Policy
    people: MappingProxyType[str, _PersonPlan]
    accounts: MappingProxyType[str, tuple[BoundAccount, ...]]   # person_id -> accounts
    aliases: MappingProxyType[str, tuple[str, ...]]            # person_id -> former ids
    memberships: frozenset[tuple[str, Scope]]         # (person_id, room scope)
    room_scopes: MappingProxyType[Scope, tuple[str, str]]       # scope -> (platform, conv_id)
    containment: frozenset[tuple[Scope, Scope]]       # (child, parent)


    def __post_init__(self) -> None:
        for name in ("persons", "policy"):
            model = getattr(self, name)
            indexes = {
                entry.name: MappingProxyType(dict(value))
                for entry in fields(model)
                if isinstance(value := getattr(model, entry.name), dict)
            }
            object.__setattr__(self, name, replace(model, **indexes))

    @property
    def snapshot(self) -> MappingProxyType[str, Any]:
        """A detached JSON-shaped view; edits never change the captured effect."""
        return MappingProxyType(json.loads(self._snapshot_json))

