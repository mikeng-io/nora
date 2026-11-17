"""Managed-backend vocabulary shared by the binding store, the authority
backend and the services that coordinate them
(`design/managed-identity-access/`).

Standard library only — same rule as the rest of `nora/domain`
(`nora/domain/__init__.py`), enforced by `tests/test_boundaries.py` and
import-linter's "domain is framework-free" contract.

What lives here is the SHAPE of results every managed component speaks in —
the R-27 three-state applied to identity and authority — plus the Keto
encoding of scopes, relations and subjects the committed OPL model
(`deploy/ory/keto/namespaces.ts`) declares. How results are produced (SQL,
HTTP, TOML) belongs to the adapters.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal

from nora.domain.identity import BoundAccount, Identifier
from nora.domain.management import (
    ACCESS_PERMITS,
    MANAGEMENT_PERMITS,
    Scope,
    ScopeKind,
)


class ManagedError(ValueError):
    """A managed-backend value that violates its contract at construction."""


def _nonblank(name: str, value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManagedError(f"{name} must be a non-blank string")
    return value


# ---------------------------------------------------------------------------
# Lifecycle states: suspended person, revoked binding and departed room stay DISTINCT.
# ---------------------------------------------------------------------------


class PersonLifecycle(Enum):
    """A canonical person's global state (`identity.md`).

    `SUSPENDED` is the installation's global hold — every check on the
    person denies, and suspension is not a claim on any account's ownership.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"


class AccountState(Enum):
    """A binding's state (`identity.md`).

    `REVOKED` ends that account's resolution to the person — other accounts
    on the person and the person itself are untouched.
    """

    BOUND = "bound"
    REVOKED = "revoked"


class MembershipState(Enum):
    """A (person, scope) membership's standing — distinct from identity
    lifecycle: leaving a room revokes no binding and suspends nobody."""

    MEMBER = "member"
    LEFT = "left"


# ---------------------------------------------------------------------------
# Outcomes: denied authoritative, unavailable a store that could not answer, incomplete owed.
# ---------------------------------------------------------------------------


class OutcomeStatus(Enum):
    COMPLETE = "complete"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class Outcome:
    """How one mutation ended. `detail` carries a machine-readable reason —
    never a store error message verbatim (those can quote private values)."""

    status: OutcomeStatus
    detail: str = ""


def complete(detail: str = "") -> Outcome:
    return Outcome(OutcomeStatus.COMPLETE, detail)


def denied(detail: str = "") -> Outcome:
    return Outcome(OutcomeStatus.DENIED, detail)


def unavailable(detail: str = "") -> Outcome:
    return Outcome(OutcomeStatus.UNAVAILABLE, detail)


def incomplete(detail: str = "") -> Outcome:
    return Outcome(OutcomeStatus.INCOMPLETE, detail)


class Verdict(Enum):
    """An authority check's answer (`authorization.md` «Failure and
    independent stores»).

    `UNKNOWN_SCOPE` is the provable absence of the scope from Nora's
    topology — kept distinct from `DENIED` so "Keto denied" and "Nora could
    not resolve the scope" stay different outcomes, and from `UNAVAILABLE`
    so a determinate negative is never confused with a check that could not
    run. Both fail closed.
    """

    ALLOWED = "allowed"
    DENIED = "denied"
    UNKNOWN_SCOPE = "unknown_scope"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CheckResult:
    """One `check` answer: the verdict plus a machine-readable detail. A
    caller must read `verdict`, never infer allow from the absence of an
    exception — an error is `UNAVAILABLE`, never an exception-shaped allow."""

    verdict: Verdict
    detail: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOWED


def allowed() -> CheckResult:
    return CheckResult(Verdict.ALLOWED)


def check_denied(detail: str = "") -> CheckResult:
    return CheckResult(Verdict.DENIED, detail)


def unknown_scope(detail: str = "") -> CheckResult:
    return CheckResult(Verdict.UNKNOWN_SCOPE, detail)


def check_unavailable(detail: str = "") -> CheckResult:
    return CheckResult(Verdict.UNAVAILABLE, detail)


# ---------------------------------------------------------------------------
# The Keto encoding (`deploy/ory/keto/namespaces.ts`): declared relations only, never computed.
# ---------------------------------------------------------------------------

#: Scope kinds to their Keto namespace names — case is the OPL class name.
KETO_NAMESPACES: MappingProxyType[ScopeKind, str] = MappingProxyType(
    {
        ScopeKind.WORKSPACE: "Workspace",
        ScopeKind.UNIT: "Unit",
        ScopeKind.ROOM: "Room",
    }
)

#: The containment relations Nora publishes after validating real topology:
#: `room -> unit`, `unit -> workspace` (`authorization.md`).
CONTAINMENT_RELATIONS: MappingProxyType[ScopeKind, str] = MappingProxyType(
    {ScopeKind.UNIT: "workspace", ScopeKind.ROOM: "unit"}
)

#: Non-permit relations a mutation may carry (`member` is membership,
#: `admin` the full delegated operator, `restricted` the blanket denial).
STRUCTURAL_RELATIONS: frozenset[str] = frozenset({"member", "admin", "restricted"})


def keto_namespace(kind: ScopeKind) -> str:
    return KETO_NAMESPACES[kind]


def room_scope_id(platform: str, conversation_id: str) -> str:
    """A room scope's id: the room's `platform/conversation_id` pair, joined
    so no room can be named on the wrong platform by omitting half its key."""
    _nonblank("platform", platform)
    _nonblank("conversation_id", conversation_id)
    return f"{platform}/{conversation_id}"


def split_room_scope_id(scope_id: str) -> tuple[str, str]:
    """The inverse of `room_scope_id` — refuses a room id that does not carry
    its platform half rather than guessing one."""
    _nonblank("room scope id", scope_id)
    platform, sep, conversation_id = scope_id.partition("/")
    if not sep or not platform.strip() or not conversation_id.strip():
        raise ManagedError(
            f"room scope id {scope_id!r} must be 'platform/conversation_id'"
        )
    return platform, conversation_id


def relation_for(direction: str, permit: str) -> str:
    """The write-side relation name for one permit: `grant_<permit>` for a
    positive grant, `deny_<permit>` for an explicit restriction."""
    if permit not in ACCESS_PERMITS | MANAGEMENT_PERMITS:
        raise ManagedError(f"{permit!r} is not a declared permit")
    if direction not in ("grant", "deny"):
        raise ManagedError(f"{direction!r} is not 'grant' or 'deny'")
    return f"{direction}_{permit}"


# ---------------------------------------------------------------------------
# The records the binding store persists.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManagedPerson:
    """One canonical person in the managed backend — the Kratos identity is
    correlation, not ownership evidence: bindings decide which accounts are
    the person's (`identity.md`). `revision` is the optimistic-concurrency
    value `identity` observed revisions compare against."""

    person_id: str
    lifecycle: PersonLifecycle
    kratos_identity_id: str | None = None
    display_name: str = ""
    revision: int = 0

    def __post_init__(self) -> None:
        _nonblank("person_id", self.person_id)


@dataclass(frozen=True)
class ManagedAccount:
    """One platform account bound to one person, with its lifecycle. The
    `account_id` is the store's surrogate key — the stable handle a
    revocation or identifier listing names without re-deriving which account
    a kind/value belongs to."""

    account_id: str
    person_id: str
    platform: str
    identifiers: frozenset[Identifier]
    state: AccountState
    label: str | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        _nonblank("account_id", self.account_id)
        _nonblank("person_id", self.person_id)
        _nonblank("platform", self.platform)
        typed = frozenset(self.identifiers)
        if not typed:
            raise ManagedError("a managed account carries at least one identifier")
        platforms = {i.platform for i in typed}
        if platforms != {self.platform}:
            raise ManagedError(
                f"account {self.account_id!r}: identifiers disagree with its "
                f"platform {self.platform!r}"
            )
        object.__setattr__(self, "identifiers", typed)

    def bound_account(self) -> BoundAccount:
        """The shared contract view — what file mode's `account_for` returns."""
        return BoundAccount(
            person_id=self.person_id,
            platform=self.platform,
            identifiers=self.identifiers,
            label=self.label,
        )


@dataclass(frozen=True)
class ManagedMembership:
    """One (person, scope) membership row with provenance: `source` records
    which path last wrote it, and `sources` the live set of independent
    supports (`registration`, `sync`, `delegation`) — a roster departure
    removes only its own support, so an independently established
    membership stands (`identity.md` «Platform discovery and membership»)."""

    person_id: str
    scope: Scope
    state: MembershipState
    source: str
    revision: int = 0
    sources: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _nonblank("person_id", self.person_id)
        _nonblank("membership source", self.source)
        object.__setattr__(self, "sources", frozenset(self.sources))


# ---------------------------------------------------------------------------
# The operation journal: effect + per-step state — a partial result stays resumable.
# ---------------------------------------------------------------------------


class OperationJournalState(Enum):
    """`pending`: recorded, nothing applied yet. `incomplete`: part of the
    effect committed — reconciliation is owed. `complete`: read-back agreed.
    `blocked`: the operation cannot proceed — a revocation it depended on
    failed and the affected authority is locally blocked until it clears."""

    PENDING = "pending"
    INCOMPLETE = "incomplete"
    COMPLETE = "complete"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class OperationRecord:
    """The journaled state of one operation. `steps` names each store effect
    and its state (`pending`, `done`, `skipped`), so reconciliation resumes
    exactly the steps a crash or outage left — never the whole operation."""

    operation_id: str
    kind: str
    state: OperationJournalState
    steps: MappingProxyType[str, str]
    outcome: str = ""
    detail: str = ""
    snapshot: MappingProxyType[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        _nonblank("operation_id", self.operation_id)
        _nonblank("operation kind", self.kind)


@dataclass(frozen=True)
class AuthorityBlock:
    """A local block on one (person, scope)'s authority, raised when a
    revocation could not be confirmed (`authorization.md`): while the row
    stands, checks on the pair answer `unavailable` — the stale grant must
    not keep working — and stays until reconciliation clears it, including
    across restarts."""

    person_id: str
    scope: Scope
    reason: str
    operation_id: str

    def __post_init__(self) -> None:
        _nonblank("person_id", self.person_id)
        _nonblank("block reason", self.reason)
        _nonblank("operation_id", self.operation_id)


class IdentifierConflictError(ManagedError):
    """A matchable identifier already owned by another account
    (`identity.md`: one matchable identifier, one owner). Raised rather than
    merged — discovery never proves two accounts are one person."""


class OperationConflictError(ManagedError):
    """An `operation_id` replayed with a different effect — a conflict, not
    a retry (`management.md`)."""


# ---------------------------------------------------------------------------
# Platform discovery (`identity.md` «Platform discovery and membership»).
# ---------------------------------------------------------------------------

MemberClassification = Literal["person", "bot", "unknown"]


@dataclass(frozen=True)
class ObservedMember:
    """One account's typed identifiers in a collection roster.

    `classification` retains legacy provider metadata, not an admission or
    permission rule. Missing identifiers remain unproven regardless of label.
    """

    identifiers: frozenset[Identifier]
    classification: MemberClassification = "person"

    def __post_init__(self) -> None:
        typed = frozenset(self.identifiers)
        if not typed and self.classification != "unknown":
            raise ManagedError(
                "a roster member carries at least one identifier unless "
                "it is unclassifiable"
            )
        platforms = {i.platform for i in typed}
        if len(platforms) > 1:
            raise ManagedError(
                f"a roster member cannot mix platforms {sorted(platforms)}"
            )
        object.__setattr__(self, "identifiers", typed)


@dataclass(frozen=True)
class DiscoveryCapabilities:
    """What one platform's collection discovery can honestly supply —
    declared per adapter, never assumed equal across platforms.

    `roster`: the platform can enumerate a collection's member set.
    `member_events`: membership deltas stream as observations.
    `sender_typing`: an observed sender's value can be typed to matchable
    identifiers. `room_audience`: a collection IS one room's audience —
    False wherever the only roster is a superset (a guild's members are
    never a channel's audience), so an `audience="room"` rule refuses to
    build rather than silently over-grant.
    """

    roster: bool
    member_events: bool
    sender_typing: bool
    room_audience: bool


@dataclass(frozen=True)
class ObservedRows:
    """The producer read seam's raw answer: the member rows AND when the
    upstream read actually finished, on the producer's own clock —
    `observed_ms=None` when the transport cannot establish it, because an
    unavailable timestamp is never guessed. Whoever performs the read
    supplies the stamp: a direct REST/RPC answer's own completion, or a
    bridge envelope's `observed_ms`. A cached or delayed answer keeps its
    real observation time instead of wearing the consumer's delivery
    time, and an undated one stays undated.
    """

    rows: tuple[dict[str, Any], ...] = ()
    observed_ms: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "rows", tuple(self.rows))


@dataclass(frozen=True)
class CollectionSnapshot:
    """One roster read's answer — the fail-closed rule `Roster` carries,
    kept for the management path: `available` distinguishes "the source
    could not answer" from "the collection is empty", and only a complete,
    available snapshot may establish departures. `complete` is the source's
    own completeness claim (a paginated read that finished is complete; a
    capability returning a subset is not). `observed_ms` stamps when the
    upstream read happened — freshness a caller compares without inventing
    a clock of its own. `0` means the transport could not establish it:
    undated evidence, never a claim of the epoch.
    """

    available: bool
    members: tuple[ObservedMember, ...] = ()
    complete: bool = False
    observed_ms: int = 0
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "members", tuple(self.members))


# ---------------------------------------------------------------------------
# Observation ordering: one producer-time policy, one durable per-source mark.
# ---------------------------------------------------------------------------

#: Clock disagreement bound on `observed_ms`: a stamp further into the
#: future than one transport timeout is a read that never happened.
_OBSERVATION_FUTURE_SKEW_MS = 30_000

#: The default departure window — one default onboarding interval.
_DEFAULT_OBSERVATION_MAX_AGE_MS = 300_000


@dataclass(frozen=True)
class ObservationFreshness:
    """The ONE producer-time admission policy every roster path shares —
    a read may only act on the instant the producer stamped itself:
    `observed_ms` must be a real positive instant, never absent and never
    further into the future than bridge↔app clock disagreement allows.
    A COMPLETE roster — the only shape that can establish absence — must
    additionally sit within one sync interval of now (`departure`).
    `wall_clock` is the constructor clock seam tests pin."""

    max_age_ms: int = _DEFAULT_OBSERVATION_MAX_AGE_MS
    future_skew_ms: int = _OBSERVATION_FUTURE_SKEW_MS
    wall_clock: Callable[[], float] = time.time

    def instant(self, observed_ms: int) -> bool:
        """The stamp is usable at all: a real positive instant, never
        absent or future-dated beyond skew."""
        return (
            isinstance(observed_ms, int)
            and not isinstance(observed_ms, bool)
            and 0 < observed_ms <= self.wall_clock() * 1000 + self.future_skew_ms
        )

    def departure(self, observed_ms: int) -> bool:
        """`instant` AND fresh enough to establish absence."""
        return self.instant(observed_ms) and (
            self.wall_clock() * 1000 - observed_ms <= self.max_age_ms
        )


@dataclass(frozen=True)
class SourceObservation:
    """The newest roster observation a (service, collection, scope)
    accepted — the durable ordering mark each sync re-validates against.
    `effect` is the journaled effect digest: an exact replay carries the
    same instant AND the same effect; a same-instant DIFFERENT effect is
    conflicting evidence that can never replace the accepted one."""

    service: str
    collection: str
    scope: Scope
    observed_ms: int
    operation_id: str
    effect: str

    def __post_init__(self) -> None:
        _nonblank("service", self.service)
        if not isinstance(self.collection, str):
            raise ManagedError("an observation mark requires a collection")
        _nonblank("operation_id", self.operation_id)
        _nonblank("effect", self.effect)
        if (not isinstance(self.observed_ms, int)
                or isinstance(self.observed_ms, bool)
                or self.observed_ms <= 0):
            raise ManagedError("an observation mark requires a positive instant")
