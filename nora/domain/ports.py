"""Protocol seams every later phase plugs into (spec §4.2). Declarations only —
no implementation moves here; the concrete classes that satisfy these shapes
keep living where they live until the phase that migrates them.

Standard library only, same rule as the rest of `nora/domain`: if a port needs
aiohttp, strands, sqlalchemy, pydantic or prometheus_client to express itself,
the port is wrong. `nora.domain.models` is the one allowed sibling import.

Each shape below encodes a decision that cost something to reach. Simplifying
it back to the "obvious" version silently reintroduces the bug it was built to
avoid:

- `PlatformIdentity.aliases` exists because signal-cli emits `source` OR
  `sourceNumber` and sometimes a UUID, and Telegram has both a numeric id and
  a username. R-12 (ignore Nora's own messages) must test *membership* in
  `aliases`, not equality against a single id, or self-recognition silently
  breaks the moment a second platform is wired in.

- `SendResult` replaces a bare `bool` because Telegram answers rate limiting
  with a 429 and a `retry_after` value, and `Deliverer`'s latch counts
  *attempts*, not just outcomes. A bool would burn the single send the latch
  allows on the most common transient failure instead of letting the caller
  back off and retry.

- `EngageDecision` carries `rationale` and `threshold` (not just `confidence`)
  because design/02 requires the classifier's reason to reach the prefill,
  and a bare confidence is uninterpretable in a trace without the threshold
  it was compared against — 0.82 means nothing on its own.

- `PlatformAdapter` deliberately has **no `parse()`**. Adding one would force
  every adapter to pretend its transport is dict-shaped, which Discord's
  gateway events and Telegram's update batches are not. Parsers stay
  per-adapter, tested directly against their own transport shape.

- `AuditStore.recent` returns `TranscriptRow`, not the `dict[str, Any]` row
  blobs the old `nora/store/db.py` returned, and `delivery_status` is
  `DeliveryStatus`, not a bare string. `nora.infra.sql.audit.SqlAuditStore`
  implements this; the conformance is checked statically (it is inside mypy's
  strict scope) and behaviourally by `tests/contract/test_audit_store_contract.py`.

- `AuditStore` still deliberately has **no `prune`**, even though a caller now
  exists. `SqlAuditStore.prune_all` is a retention sweep owned by the runtime,
  not something any stage programs against through the port. Putting it on the
  port would oblige every future implementation — including fakes — to
  implement a retention policy they do not have.

- `AuditStore.append` has **no `max_per_conversation` parameter**. Retention
  window size is a policy decided at the store's construction, not a value
  every caller re-specifies per call — `Protocol` methods don't inherit
  defaults, so a per-call default is already duplicated wherever it is
  threaded today (`nora/store/db.py:68`).

- `MemoryStore.ensure_*` is declared **idempotent under concurrency**.
  Today's `HonchoClient.ensure_session` is check-then-act, and person-centric
  identity means the same peer id can be hit from two platforms at the same
  moment. The lock that makes this true lands in a later phase; the
  invariant is declared here so that phase has something to implement
  against rather than discover.

- `MemoryStore.health()` returns `MemoryHealth`, mirroring
  `PlatformAdapter.health() -> AdapterHealth`, instead of the bare
  `dict[str, Any]` it used to share with nothing else on this port.

- `MemoryStore.feed()` stays `bool`, deliberately, even though `SendResult`
  exists precisely because "a bool loses retryability on a network call to a
  flaky service." Honcho is explicitly never load-bearing (see this module's
  own docstring in `nora/store/honcho.py`): a failed feed is fire-and-forget,
  logged and counted, never retried. Giving it a retryable/retry_after shape
  would imply a retry policy that deliberately does not exist. This
  asymmetry with `SendResult` is intentional, not an oversight — recorded
  here so it isn't cargo-culted into a `FeedResult` later.

- `EngagePolicy.should_engage` is `async` even though today's trigger-text
  policy is synchronous, because the classifier that replaces it is I/O, and
  a later sync-to-async change would break the whole ingest pipeline at every
  call site.

Convention pinned by `tests/test_ports.py`: every port method is keyword-only
after `self`. A positional parameter added to a port later is a breaking
change at every call site; keyword-only buys free extensibility forever.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from nora.domain.models import (
    ConversationTarget,
    DeliveryStatus,
    NormalizedMessage,
    TagContext,
    TranscriptRow,
)


class ModelTier(Enum):
    FLASH = "flash"
    DEEP = "deep"


@dataclass(frozen=True)
class PlatformIdentity:
    """Nora's own identity on a platform, as a set of aliases rather than a
    single id — see module docstring."""

    platform: str
    primary: str
    aliases: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:  # primary is always an alias of itself
        object.__setattr__(self, "aliases", frozenset(self.aliases) | {self.primary})


@dataclass(frozen=True)
class SendResult:
    """Outcome of a send attempt. See module docstring for why this is not `bool`."""

    ok: bool
    retryable: bool = False
    retry_after: float | None = None


@dataclass(frozen=True)
class AdapterHealth:
    platform: str
    connected: bool
    last_event_age_seconds: float | None = None


@dataclass(frozen=True)
class MemoryHealth:
    """Mirrors `AdapterHealth` — typed, not a bare dict. `MemoryStore.health()`
    used to be the one health check left dict-shaped while `PlatformAdapter.health()`
    was already typed; see module docstring's `MemoryStore` note."""

    reachable: bool


@dataclass(frozen=True)
class EngageDecision:
    """See module docstring for why `rationale` and `threshold` ride along
    with `confidence` instead of a bare bool or a bare number."""

    engage: bool
    reason: str
    tier: ModelTier | None = None
    rationale: str | None = None
    confidence: float | None = None
    threshold: float | None = None
    allow_episode: bool = False


@dataclass(frozen=True)
class EngagementRequest:
    message: NormalizedMessage
    decision: EngageDecision
    tag_id: str


@runtime_checkable
class PlatformAdapter(Protocol):
    """No `parse()` — see module docstring."""

    name: str

    async def resolve_identity(self) -> PlatformIdentity: ...

    async def listen(
        self, *, on_message: Callable[[NormalizedMessage], Awaitable[None]]
    ) -> None: ...

    async def send(self, *, target: ConversationTarget, text: str) -> SendResult: ...

    async def health(self) -> AdapterHealth: ...


@runtime_checkable
class AuditStore(Protocol):
    """No `prune`, no per-call `max_per_conversation` — see module docstring."""

    async def append(
        self,
        *,
        message: NormalizedMessage,
        is_nora: bool = False,
        delivery_status: DeliveryStatus = DeliveryStatus.PENDING,
    ) -> int: ...

    async def recent(self, *, conversation_id: str, limit: int) -> list[TranscriptRow]: ...

    async def set_delivery_status(self, *, row_id: int, status: DeliveryStatus) -> None: ...

    async def stats(self) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class MemoryStore(Protocol):
    """`ensure_*` methods are idempotent under concurrency — see module docstring."""

    async def ensure_workspace(self) -> None: ...

    async def ensure_peer(self, *, peer_id: str) -> None: ...

    async def feed(self, *, peer_id: str, text: str) -> bool: ...

    async def peer_card(self, *, peer_id: str) -> list[str]: ...

    async def health(self) -> MemoryHealth: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class EngagePolicy(Protocol):
    """Async now, ahead of need — see module docstring."""

    async def should_engage(self, *, message: NormalizedMessage) -> EngageDecision: ...


@runtime_checkable
class ContextAssembler(Protocol):
    async def assemble(self, *, request: EngagementRequest) -> TagContext: ...
