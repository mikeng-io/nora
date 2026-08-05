"""Protocol seams every later phase plugs into (spec §4.2). Declarations only —
no implementation moves here; the concrete classes that satisfy these shapes
keep living where they live until the phase that migrates them.

Standard library only, same rule as the rest of `nora/domain`: if a port needs
aiohttp, strands, sqlalchemy, pydantic or prometheus_client to express itself,
the port is wrong. `nora.domain.models` and `nora.domain.identity` are the only
allowed sibling imports.

Each shape below encodes a decision that cost something to reach. Simplifying
it back to the "obvious" version silently reintroduces the bug it was built to
avoid:

- `PlatformIdentity.aliases` exists because signal-cli emits `source` OR
  `sourceNumber` and sometimes a UUID, and Telegram has both a numeric id and
  a username. R-12 (ignore Nora's own messages) must test *membership* in
  `aliases`, not equality against a single id, or self-recognition silently
  breaks the moment a second platform is wired in. The type now lives in
  `nora.domain.identity` (re-exported below) and carries typed `identifiers`
  alongside the flat `aliases` — see its own docstring, and R-17 for why a
  degraded derivation of it must be counted rather than merely logged.

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

- `react()` lives on THIS port, not a separate capability protocol: reacting
  is a platform capability like sending, and the alternative has the
  engagement layer `isinstance`-testing a narrower protocol, putting platform
  knowledge back in the layer whose whole job is not to have any.
  `ReactionResult` carries `supported` as well as `ok`, so "this platform has
  no reactions" (never an exception) stays distinguishable from "the reaction
  failed" — only one of those is worth an alert. There is deliberately no
  shared `UNSUPPORTED_REACTION` constant: DEC-13's gate forbids binding a
  locally-defined class's instance at module level, and it checks the *shape*,
  not mutability, so a frozen dataclass is no exception.
  `nora.engagement.stages.indicate` also tolerates an adapter with no `react`
  attribute at all — a decoration may never crash a tag. Parameters are
  `to_author`/`to_timestamp` because `target` here already means the
  `ConversationTarget`; Signal's `targetAuthor`/`targetTimestamp` are
  translated in `nora.platform.signal.react` with every other wire name.

- `AuditStore.recent` returns `TranscriptRow`, not the `dict[str, Any]` row
  blobs the old `nora/store/db.py` returned, and `delivery_status` is
  `DeliveryStatus`, not a bare string. `nora.infra.sql.audit.SqlAuditStore`
  implements this. Static conformance is checked by mypy (`nora.infra` is
  inside the strict override); the behavioural half is
  `tests/contract/test_audit_store_contract.py`, one suite parametrized over
  `SqlAuditStore` and `nora.testing.FakeAuditStore` so the fake cannot drift
  from production unnoticed.

- `AuditStore` still deliberately has **no `prune`**, even though a caller now
  exists. `SqlAuditStore.prune_all` is a retention sweep owned by the runtime,
  not something any stage programs against through the port. Putting it on the
  port would oblige every future implementation — including fakes — to
  implement a retention policy they do not have.

- `AuditStore` DOES carry `unattributed`/`set_person_id`, which looks like the
  same "retention chore" shape `prune` was refused for above, and is not. The
  distinction is who programs against it: `prune_all` is a policy the store
  decides for itself and no caller reasons about, whereas lazy adoption
  (`nora.app.adoption`) is a caller with a question only the store can answer —
  "which audited messages are still attributed to nobody?" — followed by a
  decision that same caller makes. A `MemoryStore`-backed or Postgres-backed
  `AuditStore` would have to answer that question too, so it belongs on the
  port. `set_person_id` mirrors `set_delivery_outcome` exactly: one row, by id,
  unknown id is a silent no-op.

- `set_delivery_outcome` is the old `set_delivery_status` plus an optional
  `ts`, and NOT a second write — `nora.infra.sql.audit` documents why the
  platform's timestamp must land on this already-existing update. `ts=None`
  leaves the row's timestamp alone.

- `AuditStore.append` has **no `max_per_conversation` parameter**. Retention
  window size is a policy decided at the store's construction, not a value
  every caller re-specifies per call — `Protocol` methods don't inherit
  defaults, so a per-call default is already duplicated wherever it is
  threaded today (`nora/infra/sql/audit.py:35`, `SqlAuditStore.__init__`'s
  `max_per_conversation` parameter).

- `MemoryStore.ensure_*` is declared **idempotent under concurrency**.
  `HonchoClient.ensure_peer`/`ensure_session` are check-then-act over
  per-peer state, and person-centric identity means the same peer id can be
  hit from two platforms at the same moment. The lock that makes this true
  landed here, in this phase — a per-peer `asyncio.Lock`
  (`HonchoClient._peer_lock` in `nora/infra/honcho/client.py`), not in a
  later one; the invariant is
  declared here so a static conformance check (mypy on `nora.infra`) has
  something to hold the implementation to.

- `MemoryStore.health()` returns `MemoryHealth`, mirroring
  `PlatformAdapter.health() -> AdapterHealth`, instead of the bare
  `dict[str, Any]` it used to share with nothing else on this port.

- `MemoryStore.feed()` stays `bool`, deliberately, even though `SendResult`
  exists precisely because "a bool loses retryability on a network call to a
  flaky service." Honcho is explicitly never load-bearing (see this module's
  own docstring in `nora/infra/honcho/client.py`): a failed feed is fire-and-forget,
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
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from nora.domain.identity import PlatformIdentity as PlatformIdentity
from nora.domain.models import (
    ConversationTarget,
    DeliveryStatus,
    NormalizedMessage,
    TagContext,
    TranscriptRow,
    UnattributedRow,
)

# `PlatformIdentity` is re-exported, not declared here — it moved next to
# `Identifier` in `nora.domain.identity` when it grew a typed `identifiers`
# field (design/10 §5), which also bought this module back some of its 300-line
# cap. The `X as X` above is not a typo: it is PEP 484's explicit re-export
# marker, which mypy's `no_implicit_reexport` requires before another module may
# import the name through here. `__all__` would satisfy mypy too, but would also
# narrow `from nora.domain.ports import *` to that one name.


@dataclass(frozen=True)
class SendResult:
    """Outcome of a send attempt. See module docstring for why this is not `bool`.

    `timestamp` is the PLATFORM's own sent-timestamp, and it is what a later
    reply's quote points at. `None` = the platform supplied none.
    """

    ok: bool
    retryable: bool = False
    retry_after: float | None = None
    timestamp: int | None = None


@dataclass(frozen=True)
class ReactionResult:
    """Outcome of a reaction attempt. `supported` is a separate axis from `ok` —
    see module docstring."""

    ok: bool
    supported: bool = True


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
    """No `parse()` — see module docstring.

    `platform: str`, not `name: str` — the platforms phase's fifth and final
    conformance gap (found while closing the other four; see
    `tests/contract/test_port_conformance.py`). Every other type on this port
    already says `platform` (`PlatformIdentity.platform`, `AdapterHealth.
    platform`), and so does the `platform=` metrics label everywhere else in
    the codebase — `name` was the outlier here, not `SignalAdapter`, so the
    port is what changed.
    """

    platform: str

    async def resolve_identity(self) -> PlatformIdentity: ...

    async def listen(
        self, *, on_message: Callable[[NormalizedMessage], Awaitable[None]]
    ) -> None: ...

    async def send(self, *, target: ConversationTarget, text: str) -> SendResult: ...

    async def react(
        self,
        *,
        target: ConversationTarget,
        emoji: str,
        to_author: str,
        to_timestamp: int,
        remove: bool = False,
    ) -> ReactionResult: ...

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
        person_id: str | None = None,
    ) -> int: ...

    async def recent(self, *, conversation_id: str, limit: int) -> list[TranscriptRow]: ...

    async def set_delivery_outcome(
        self, *, row_id: int, status: DeliveryStatus, ts: int | None = None
    ) -> None: ...

    async def unattributed(self, *, limit: int) -> list[UnattributedRow]: ...

    async def set_person_id(self, *, row_id: int, person_id: str) -> None: ...

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
