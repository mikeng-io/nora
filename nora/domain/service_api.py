"""The canonical service API's shared vocabulary: outcomes, envelope, operation
record and path grammar (`design/service-api/conventions.md`).

Every contract builds on these types; the OpenAPI document in
`docs/service-api/openapi.json` is generated from them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final, Literal, get_args

Contract = Literal[
    "intake", "platform", "sources", "graph", "management",
    "documents", "voice", "computer", "operations",
]
ReadOutcome = Literal["ok", "nothing_found", "unavailable"]
OperationOutcome = Literal[
    "complete", "pending", "denied", "rejected",
    "unsupported", "incomplete", "uncertain", "unavailable",
]
Outcome = ReadOutcome | OperationOutcome
Family = Literal["read", "operation"]
OperationState = Literal["prepared", "started", "settled"]
ScopeLevel = Literal[
    "room", "unit", "agent", "identity", "workspace", "installation", "none",
]

_STATUS: Final[Mapping[str, int]] = {
    "ok": 200, "nothing_found": 200, "unavailable": 503,
    "complete": 200, "pending": 202, "denied": 403, "rejected": 200,
    "unsupported": 200, "incomplete": 200, "uncertain": 200,
}
_MEMBERS: Final[Mapping[Family, frozenset[str]]] = {
    "read": frozenset(get_args(ReadOutcome)),
    "operation": frozenset(get_args(OperationOutcome)),
}
# The request never became a read or an operation: malformed, no principal,
# no such route, an operation_id reused for another effect.
NO_ENVELOPE_STATUSES: Final[frozenset[int]] = frozenset({400, 401, 404, 409, 422})


def status_for(outcome: Outcome) -> int:
    """The HTTP status an outcome is answered with; never read instead of it."""
    return _STATUS[outcome]


@dataclass(frozen=True)
class ParsedResponse:
    """What a response says: an outcome, or a caller fault with no outcome."""

    outcome: Outcome | None
    caller_fault: int | None = None


def parse_response(family: Family, status: int, body: object) -> ParsedResponse:
    """Read a response of one family; anything not self-consistent is `unavailable`."""
    if status in NO_ENVELOPE_STATUSES:
        return ParsedResponse(outcome=None, caller_fault=status)
    outcome = body.get("outcome") if isinstance(body, Mapping) else None
    if outcome not in _MEMBERS[family] or _STATUS[outcome] != status:
        return ParsedResponse(outcome="unavailable")
    return ParsedResponse(outcome=outcome)


@dataclass(frozen=True)
class Scope:
    """The scope a request resolved to, at its contract's level."""

    level: ScopeLevel
    id: str | None = None


@dataclass(frozen=True)
class ReadWhen:
    """When the source observed the answer, by its own clock."""

    observed_at_ms: int
    known_by: str


@dataclass(frozen=True)
class ReadFrom:
    """The source, its health, and the sources that failed when coverage is partial."""

    source: str
    healthy: bool
    failed_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperationWhen:
    """Where the record is in its lifecycle, and when it got there."""

    state: OperationState
    started_at_ms: int | None = None
    settled_at_ms: int | None = None


@dataclass(frozen=True)
class OperationFrom:
    """The target of the effect and its platform reference when one exists."""

    target: str
    platform_ref: str | None = None


@dataclass(frozen=True)
class ReadEnvelope:
    """The body of every read response. `from_` is `from` on the wire."""

    outcome: ReadOutcome
    payload: object
    when: ReadWhen
    from_: ReadFrom
    scope: Scope
    trace: str


@dataclass(frozen=True)
class OperationEnvelope:
    """The body of every operation response. `from_` is `from` on the wire."""

    outcome: OperationOutcome
    payload: object
    when: OperationWhen
    from_: OperationFrom
    scope: Scope
    trace: str


class OperationConflictError(Exception):
    """An `operation_id` reused for another effect (409); nothing changes."""


@dataclass(frozen=True)
class OperationRequest:
    """A caller's operation: its caller-minted id, kind and effect."""

    operation_id: str
    kind: str
    effect: object


@dataclass(frozen=True)
class OperationRecord:
    """One operation's record: `prepared → started → settled`, final once settled."""

    request: OperationRequest
    state: OperationState = "prepared"
    outcome: OperationOutcome = "pending"
    started_at_ms: int | None = None
    settled_at_ms: int | None = None

    def __post_init__(self) -> None:
        if (self.state == "settled") == (self.outcome == "pending"):
            raise ValueError(f"a {self.state} record cannot be {self.outcome}")

    def start(self, now_ms: int) -> OperationRecord:
        if self.state != "prepared":
            raise ValueError(f"a {self.state} record cannot start")
        return replace(self, state="started", started_at_ms=now_ms)

    def settle(self, outcome: OperationOutcome, now_ms: int) -> OperationRecord:
        if self.state != "started":
            raise ValueError(f"a {self.state} record cannot settle")
        return replace(self, state="settled", outcome=outcome, settled_at_ms=now_ms)


def admit(request: OperationRequest, existing: OperationRecord | None) -> OperationRecord:
    """The record for a request: the existing one for the same effect, else a new one."""
    if existing is None:
        return OperationRecord(request=request)
    if existing.request != request:
        raise OperationConflictError(request.operation_id)
    return existing


ROOT_PROBES: Final[frozenset[str]] = frozenset({"/health", "/ready", "/metrics"})
_VERSION = re.compile(r"v[1-9][0-9]*")
_KEBAB = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_PARAM = re.compile(r"\{[a-z][a-z0-9_]*\}")
_CONTRACTS: Final[frozenset[str]] = frozenset(get_args(Contract))


def path_conforms(path: str) -> bool:
    """Whether a route path fits the decidable part of the path grammar.

    It cannot judge whether a collection is plural or a segment is a verb.
    """
    if path in ROOT_PROBES:
        return True
    segments = path.split("/")
    if len(segments) < 4 or segments[0]:
        return False
    _, version, contract, *rest = segments
    if not _VERSION.fullmatch(version) or contract not in _CONTRACTS:
        return False
    match rest:
        case ["operations"] | ["queries"]:
            return True
        case ["operations" | "blobs", key]:
            return bool(_PARAM.fullmatch(key))
        case ["operations" | "queries" | "blobs", *_]:
            return False
    shape = [_KEBAB, _PARAM, _KEBAB]
    return len(rest) <= 3 and all(p.fullmatch(s) for p, s in zip(shape, rest, strict=False))
