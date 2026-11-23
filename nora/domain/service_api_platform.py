"""The `platform` contract: the bridges' delivery operations, reads and blob custody.

Its routes, closed kinds and payload shapes build on `nora.domain.service_api`;
the OpenAPI document in `docs/service-api/openapi.json` is generated from them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from nora.domain.actions import ActionOwner
from nora.domain.service_api import (
    Family,
    OperationEnvelope,
    OperationRequest,
    ReadEnvelope,
)

PlatformOperationKind = Literal["send", "react", "edit", "typing"]
PlatformQueryKind = Literal[
    "roster", "members", "channels", "channel",
    "member_presence", "voice_presence", "groups",
]
#: The account id a bridge answers for its own logged-in account.
SELF_ACCOUNT_ID: Final = "self"


@dataclass(frozen=True)
class PlatformOperationRequest(OperationRequest):
    """A delivery operation; `owner` is who may run it, `effect` its kind's content."""

    kind: PlatformOperationKind
    owner: ActionOwner
    effect: Mapping[str, object]


@dataclass(frozen=True)
class OperationError:
    """A classified, sanitised reason; never raw upstream text."""

    kind: str
    message: str


@dataclass(frozen=True)
class PlatformOperationPayload:
    """What a delivery applied, or why it did not."""

    platform_refs: tuple[str, ...] = ()
    platform_ms: int | None = None
    message_id: int | None = None
    error: OperationError | None = None


@dataclass(frozen=True)
class PlatformOperationEnvelope(OperationEnvelope):
    """The answer to a delivery operation."""

    payload: PlatformOperationPayload


@dataclass(frozen=True)
class OperationReadEnvelope(ReadEnvelope):
    """An operation read back: `ok` carries the operation's own envelope."""

    payload: PlatformOperationEnvelope | None


@dataclass(frozen=True)
class PlatformQuery:
    """A read whose filter names platform identifiers."""

    kind: PlatformQueryKind
    params: Mapping[str, object]


@dataclass(frozen=True)
class PlatformAccount:
    """The bridge's own account; a platform may add its own fields."""

    platform: str
    primary: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountReadEnvelope(ReadEnvelope):
    """An account read: `unavailable` until the platform login has landed."""

    payload: PlatformAccount | None


@dataclass(frozen=True)
class BridgeStatus:
    """A bridge's own detail: whether it can serve, and why not."""

    ok: bool
    detail: str | None = None
    degraded: bool = False
    connected_at_ms: int | None = None


@dataclass(frozen=True)
class StatusReadEnvelope(ReadEnvelope):
    """The `operations` contract's status read of a bridge."""

    payload: BridgeStatus | None


@dataclass(frozen=True)
class Route:
    """One documented route. `bytes` as a body is raw octets, not an envelope."""

    name: str
    method: Literal["GET", "POST", "PUT"]
    path: str
    family: Family
    response: type
    request: type | None = None
    answers_bytes: bool = False
    conflicts: bool = False


PLATFORM_ROUTES: Final[tuple[Route, ...]] = (
    Route("submit_operation", "POST", "/v1/platform/operations", "operation",
          PlatformOperationEnvelope, PlatformOperationRequest, conflicts=True),
    Route("read_operation", "GET", "/v1/platform/operations/{operation_id}", "read",
          OperationReadEnvelope),
    Route("query", "POST", "/v1/platform/queries", "read", ReadEnvelope, PlatformQuery),
    Route("read_account", "GET", "/v1/platform/accounts/{account_id}", "read",
          AccountReadEnvelope),
    Route("put_blob", "PUT", "/v1/platform/blobs/{key}", "operation",
          OperationEnvelope, bytes),
    Route("get_blob", "GET", "/v1/platform/blobs/{key}", "read",
          ReadEnvelope, answers_bytes=True),
    Route("read_status", "GET", "/v1/operations/status", "read", StatusReadEnvelope),
)
