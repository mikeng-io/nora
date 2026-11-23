"""Identity, authority and observed outcomes of outward acts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, get_args

from nora.memory.domain.admission_proposals import PassContext

ActKind = Literal["delivery", "effect", "delegation"]
Guarantee = Literal["idempotent_effect", "read_back", "unknown"]
Verdict = Literal["done", "failed", "uncertain"]


@dataclass(frozen=True, slots=True)
class ActIdentity:
    executor: str
    trigger_id: str
    ordinal: int

    def __post_init__(self) -> None:
        if not self.executor or not self.trigger_id or self.ordinal < 0:
            raise ValueError("an act requires an executor, stable trigger and ordinal")

    @property
    def id(self) -> str:
        parts = json.dumps((self.executor, self.trigger_id, self.ordinal))
        return f"act:{hashlib.sha256(parts.encode()).hexdigest()}"


@dataclass(frozen=True, slots=True)
class ActAuthority:
    unit: str | None
    person: str | None = None
    intention: str | None = None
    room: str | None = None
    pass_context: PassContext | None = None

    def __post_init__(self) -> None:
        context = self.pass_context
        if self.unit == "" or (context is not None and context.unit != self.unit):
            raise ValueError("act authority must name the unit of its pass")


@dataclass(frozen=True, slots=True)
class Act:
    identity: ActIdentity
    kind: ActKind
    destination: str
    authority: ActAuthority
    guarantee: Guarantee
    key: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in get_args(ActKind) or self.guarantee not in get_args(Guarantee):
            raise ValueError("undeclared act kind or guarantee")
        if not self.destination or (self.guarantee == "idempotent_effect") != bool(self.key):
            raise ValueError("an act requires a destination and any declared idempotency key")
        context = self.authority.pass_context
        if context is not None and self.identity.trigger_id != context.pass_id:
            raise ValueError("a model-composed act is triggered by its pass")


@dataclass(frozen=True, slots=True)
class Outcome:
    verdict: Verdict
    result: object = None
