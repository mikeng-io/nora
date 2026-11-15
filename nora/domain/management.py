"""The private management boundary's typed operation contract
(`design/managed-identity-access/management.md`).

Standard library only — same rule as the rest of `nora/domain`
(`nora/domain/__init__.py`).

These are the SHAPES every caller of the private boundary speaks in — the
file backend never issues them; they exist so the managed service cannot
accept an actor it cannot name, a target kind the operation does not take, a
payload missing the fields the effect is made of, or a mutation submitted
without the revisions its approval was based on. The vocabulary is closed: a
new operation kind is a contract change here, not a string passed at a call
site.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import cast

from nora.domain.identity import Identifier


class ActorKind(Enum):
    """Who may stand behind an operation.

    `SERVICE` is automated work carrying a service principal inside its
    approved sources. `HOLDER` is an authenticated account holder — the actor
    carries the exact account it proved and the issuer scope that proof came
    from. `OPERATOR` is scoped delegated authority bound to a
    workspace/unit/room. `INSTALLATION` is the installation authority
    established out of band. A model argument can never construct one of
    these; the boundary derives the actor from the authenticated invocation.
    """

    SERVICE = "service"
    HOLDER = "holder"
    OPERATOR = "operator"
    INSTALLATION = "installation"


class TargetKind(Enum):
    """What an operation names. `ACCOUNT` is one exact platform account —
    the unit a control proof binds. `SCOPE` is a workspace, unit or room."""

    ACCOUNT = "account"
    PERSON = "person"
    MEMBERSHIP = "membership"
    SCOPE = "scope"


class ScopeKind(Enum):
    """The topology's three levels
    (`design/room-topology/10-topology.md`), ordered by containment."""

    WORKSPACE = "workspace"
    UNIT = "unit"
    ROOM = "room"


_SCOPE_DEPTH: MappingProxyType[ScopeKind, int] = MappingProxyType(
    {ScopeKind.WORKSPACE: 0, ScopeKind.UNIT: 1, ScopeKind.ROOM: 2}
)


class LinkSide(Enum):
    """Which side of a link request an approval or refusal speaks for."""

    INITIATING = "initiating"
    RECEIVING = "receiving"


class AccessDirection(Enum):
    """A positive grant or an explicit restriction — deny always wins."""

    GRANT = "grant"
    RESTRICT = "restrict"


#: The permits the shared permission model declares
#: (`deploy/ory/keto/namespaces.ts`): room access and scoped management.
ACCESS_PERMITS: frozenset[str] = frozenset({"speak", "view"})
MANAGEMENT_PERMITS: frozenset[str] = frozenset(
    {"manage_members", "manage_grants", "delegate"}
)
PERMITS: frozenset[str] = ACCESS_PERMITS | MANAGEMENT_PERMITS

#: How an operation ended (`management.md` «one application boundary»).
#: `denied` is an authoritative refusal; `unavailable` means the required
#: store could not answer and nothing was applied; `incomplete` means part of
#: the intended effect committed and reconciliation is still owed — never
#: reported as `complete`. A caller distinguishes all four; refusal is not
#: service failure.
OPERATION_OUTCOMES: tuple[str, ...] = (
    "complete",
    "denied",
    "unavailable",
    "incomplete",
)


class OperationError(ValueError):
    """An operation that violates the contract at construction: an actor the
    kind does not admit, a target kind it does not take, a payload missing or
    wrong-typed for the kind, a cross-field value that cannot hold — refused
    before it can reach the boundary as a malformed request."""


def _nonblank(name: str, value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperationError(f"{name} must be a non-blank string")
    return value


@dataclass(frozen=True)
class Scope:
    """A workspace, unit or room — typed so "the unit it runs in" cannot be
    smuggled in as a bare string whose kind is invented downstream."""

    kind: ScopeKind
    id: str

    def __post_init__(self) -> None:
        _nonblank("scope id", self.id)

    def encloses(self, other: Scope) -> bool:
        """Could a delegation on `self` reach `other` at the KIND level —
        a workspace reaches units and rooms, a unit reaches rooms and itself,
        a room reaches only itself. This checks kinds only: which id contains
        which is Nora's write-time validation
        (`authorization.md`), never something a scope name proves."""
        return _SCOPE_DEPTH[self.kind] <= _SCOPE_DEPTH[other.kind]


@dataclass(frozen=True)
class Actor:
    """The verified actor of one invocation — each kind carries exactly the
    fields that establish it, and carrying another kind's is refused.

    `HOLDER` carries `person_id`, the exact `account` it authenticated as and
    the `proof_issuer` that authentication is scoped to (the OAuth client, the
    Telegram bot, the installation's signal-cli — provenance, not a second
    ownership namespace). `SERVICE` carries `service_id` and its non-empty
    approved `sources`. `OPERATOR` carries `person_id` and the `scope` its
    delegation is bounded to. `INSTALLATION` carries `person_id` only — it is
    established out of band, not derived from an in-room event.
    """

    kind: ActorKind
    person_id: str | None = None
    account: Identifier | None = None
    proof_issuer: str | None = None
    service_id: str | None = None
    sources: frozenset[str] = field(default_factory=frozenset)
    scope: Scope | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", frozenset(self.sources))
        if self.kind is ActorKind.HOLDER:
            _nonblank("holder person_id", self.person_id)
            if self.account is None:
                raise OperationError("a holder actor names the exact account it proved")
            _nonblank("holder proof_issuer", self.proof_issuer)
            self._forbid(service_id=self.service_id, sources=self.sources,
                         scope=self.scope)
        elif self.kind is ActorKind.SERVICE:
            _nonblank("service_id", self.service_id)
            if not self.sources:
                raise OperationError("a service actor names its approved sources")
            self._forbid(person_id=self.person_id, account=self.account,
                         proof_issuer=self.proof_issuer, scope=self.scope)
        elif self.kind is ActorKind.OPERATOR:
            _nonblank("operator person_id", self.person_id)
            if self.scope is None:
                raise OperationError("an operator actor is bounded to a scope")
            self._forbid(account=self.account, proof_issuer=self.proof_issuer,
                         service_id=self.service_id, sources=self.sources)
        else:
            _nonblank("installation person_id", self.person_id)
            self._forbid(account=self.account, proof_issuer=self.proof_issuer,
                         service_id=self.service_id, sources=self.sources,
                         scope=self.scope)

    def _forbid(self, **fields: object) -> None:
        for name, value in fields.items():
            if value:
                raise OperationError(
                    f"a {self.kind.value} actor cannot carry {name} — "
                    "the actor's fields are its kind's evidence"
                )


@dataclass(frozen=True)
class Target:
    """The subject of one operation — typed so `set_membership` cannot be
    handed an account, or `unlink_account` a room, and so each kind carries
    the fields that name it: a membership is a (person, scope) pair, not a
    bare marker.

    `account` is the exact `Identifier` the operation names (account
    targets). `person_id` is the canonical person. `scope` is the
    workspace/unit/room — the caller supplies it as a name only: the service
    re-validates its actual containment before applying
    (`authorization.md`).
    """

    kind: TargetKind
    account: Identifier | None = None
    person_id: str | None = None
    scope: Scope | None = None

    def __post_init__(self) -> None:
        if self.kind is TargetKind.ACCOUNT:
            if self.account is None:
                raise OperationError("an account target names the exact identifier")
            self._forbid(person_id=self.person_id, scope=self.scope)
        elif self.kind is TargetKind.PERSON:
            _nonblank("person target", self.person_id)
            self._forbid(account=self.account, scope=self.scope)
        elif self.kind is TargetKind.MEMBERSHIP:
            _nonblank("membership person", self.person_id)
            if self.scope is None:
                raise OperationError(
                    "a membership target names a person inside a scope — "
                    "both are required"
                )
            self._forbid(account=self.account)
        else:
            if self.scope is None:
                raise OperationError("a scope target names a workspace, unit or room")
            self._forbid(account=self.account, person_id=self.person_id)

    def _forbid(self, **fields: object) -> None:
        for name, value in fields.items():
            if value:
                raise OperationError(
                    f"a {self.kind.value} target cannot carry {name}"
                )


def _same_platform(identifiers: frozenset[Identifier], name: str) -> str:
    platforms = {i.platform for i in identifiers}
    if len(platforms) > 1:
        raise OperationError(f"{name} cannot mix platforms {sorted(platforms)}")
    return next(iter(platforms), "")


@dataclass(frozen=True)
class AccountObservation:
    """`observe_account`: every matchable identifier the observation
    established for the presented account — one platform, and the target
    account itself is among them."""

    identifiers: frozenset[Identifier]

    def __post_init__(self) -> None:
        typed = frozenset(self.identifiers)
        if not typed:
            raise OperationError("an observation carries at least one identifier")
        _same_platform(typed, "an observation")
        object.__setattr__(self, "identifiers", typed)


@dataclass(frozen=True)
class AccountRegistration:
    """`register_account`: the person the account registers under, the
    approved onboarding scope and the account's matchable identifiers."""

    person_id: str
    scope: Scope
    identifiers: frozenset[Identifier]

    def __post_init__(self) -> None:
        _nonblank("registration person_id", self.person_id)
        typed = frozenset(self.identifiers)
        if not typed:
            raise OperationError("a registration carries at least one identifier")
        _same_platform(typed, "a registration")
        object.__setattr__(self, "identifiers", typed)


@dataclass(frozen=True)
class RosterSync:
    """`sync_membership`: the observed member accounts of the target scope.
    `complete` is the roster's own completeness claim — only a complete,
    relevant roster may establish absence (`management.md` «Platform
    discovery and membership»); a partial one can add, never remove."""

    members: frozenset[Identifier]
    complete: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "members", frozenset(self.members))


@dataclass(frozen=True)
class MembershipStanding:
    """`set_membership`: the standing the (person, scope) target is set to."""

    member: bool


@dataclass(frozen=True)
class AccessChange:
    """`grant_access` and `revoke_access`: which person's which access
    permits are added or removed, and whether the relation is a positive
    grant or an explicit restriction. Restrictions are first-class deny
    tuples — deny always wins over a grant of the same permit
    (`account-linking.md` «Linking grants»)."""

    direction: AccessDirection
    grantee: str
    permits: frozenset[str]

    def __post_init__(self) -> None:
        _nonblank("grantee", self.grantee)
        typed = frozenset(self.permits)
        if not typed:
            raise OperationError("an access change names at least one permit")
        unknown = typed - ACCESS_PERMITS
        if unknown:
            raise OperationError(
                f"{sorted(unknown)} are not access permits — management "
                "permits move through delegation, not grant_access"
            )
        object.__setattr__(self, "permits", typed)


@dataclass(frozen=True)
class Delegation:
    """`delegate` and `revoke_delegation`: which person's which management
    permits are delegated or withdrawn inside the target scope."""

    delegatee: str
    permits: frozenset[str]

    def __post_init__(self) -> None:
        _nonblank("delegatee", self.delegatee)
        typed = frozenset(self.permits)
        if not typed:
            raise OperationError("a delegation names at least one permit")
        unknown = typed - MANAGEMENT_PERMITS
        if unknown:
            raise OperationError(
                f"{sorted(unknown)} are not management permits — access "
                "permits move through grant_access, not delegation"
            )
        object.__setattr__(self, "permits", typed)


@dataclass(frozen=True)
class LinkPair:
    """The two exact accounts a link request binds — never a display name,
    and never the same account twice."""

    initiating: Identifier
    receiving: Identifier

    def __post_init__(self) -> None:
        if self.initiating == self.receiving:
            raise OperationError("a link cannot bind an account to itself")

    def account(self, side: LinkSide) -> Identifier:
        return self.initiating if side is LinkSide.INITIATING else self.receiving


@dataclass(frozen=True)
class LinkRequest:
    """`request_link`: the canonical person the initiator proposes survives
    the link. The pair itself is (actor account, target account)."""

    surviving_person: str

    def __post_init__(self) -> None:
        _nonblank("surviving_person", self.surviving_person)


@dataclass(frozen=True)
class LinkApproval:
    """`approve_link`: an explicit approval bound to ONE request — its id,
    the side this holder speaks for, the exact pair and the surviving person
    it saw. A forwarded or replayed approval binds different fields and is
    refused on comparison, not trusted (`account-linking.md`)."""

    request_id: str
    side: LinkSide
    pair: LinkPair
    surviving_person: str

    def __post_init__(self) -> None:
        _nonblank("request_id", self.request_id)
        _nonblank("surviving_person", self.surviving_person)


@dataclass(frozen=True)
class LinkRefusal:
    """`refuse_link` and `cancel_link`: a refusal or cancellation bound to
    the same request triple — id, side, pair. Cancellation additionally
    requires the initiating side; that is checked against the request, which
    this shape cannot supply — `Operation` enforces it."""

    request_id: str
    side: LinkSide
    pair: LinkPair

    def __post_init__(self) -> None:
        _nonblank("request_id", self.request_id)


@dataclass(frozen=True)
class Unlink:
    """`unlink_account`: the person the holder believes this exact account is
    bound to, so a drifted or already-changed binding revalidates instead of
    detaching what the holder never saw."""

    expected_person: str

    def __post_init__(self) -> None:
        _nonblank("expected_person", self.expected_person)


@dataclass(frozen=True)
class LifecycleAction:
    """`suspend_identity` / `reinstate_identity`: a global identity change
    carries its reason, because installation authority is the one actor whose
    exercise must be explainable after the fact."""

    reason: str

    def __post_init__(self) -> None:
        _nonblank("reason", self.reason)


@dataclass(frozen=True)
class ProfileEdit:
    """`update_profile`: the trait fields being changed and the scope a
    local nickname lives in — `None` is the holder's global profile, which an
    operator cannot touch."""

    fields: frozenset[str]
    scope: Scope | None = None

    def __post_init__(self) -> None:
        typed = frozenset(self.fields)
        if not typed:
            raise OperationError("a profile edit names the fields it changes")
        if any(not f or not f.strip() for f in typed):
            raise OperationError("a profile edit's field names must be non-blank")
        object.__setattr__(self, "fields", typed)


@dataclass(frozen=True)
class OperationSpec:
    """The contract one operation kind carries: who may issue it, what it
    targets, which observed revisions its approval was based on, the payload
    type its inputs are checked against, and whether the bilateral protocol
    governs it.

    `revisions` names the state snapshots the submitter observed
    (`identity`, `ownership`, `authority`); the service revalidates them at
    commitment so a changed target or revoked approver cannot apply under an
    earlier interpretation.
    """

    actors: frozenset[ActorKind]
    target: TargetKind
    revisions: frozenset[str]
    payload: type
    bilateral: bool = False


#: The closed operation vocabulary. A kind absent here is not an operation;
#: the boundary cannot be asked for it. Ownership operations (link lifecycle,
#: unlink) are bilateral or holder-owned — unlink is the account holder's own
#: operation, not an installation power: installation suspends the global
#: identity but never asserts account ownership. Membership and grant changes
#: are scoped operations; registration and roster synchronisation are service
#: operations within approved sources.
OPERATIONS: MappingProxyType[str, OperationSpec] = MappingProxyType(
    {
        # Discovery: a platform observation registers an unclaimed account
        # under an independent person. It grants no authority.
        "observe_account": OperationSpec(
            actors=frozenset({ActorKind.SERVICE}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"ownership"}),
            payload=AccountObservation,
        ),
        # Explicit registration under an approved onboarding rule.
        "register_account": OperationSpec(
            actors=frozenset({ActorKind.SERVICE, ActorKind.OPERATOR}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"ownership", "authority"}),
            payload=AccountRegistration,
        ),
        # Roster synchronisation within the approved collection.
        "sync_membership": OperationSpec(
            actors=frozenset({ActorKind.SERVICE}),
            target=TargetKind.SCOPE,
            revisions=frozenset({"authority"}),
            payload=RosterSync,
        ),
        # Membership and grant changes are scoped delegated operations.
        "set_membership": OperationSpec(
            actors=frozenset({ActorKind.OPERATOR}),
            target=TargetKind.MEMBERSHIP,
            revisions=frozenset({"authority"}),
            payload=MembershipStanding,
        ),
        "grant_access": OperationSpec(
            actors=frozenset({ActorKind.OPERATOR}),
            target=TargetKind.SCOPE,
            revisions=frozenset({"authority"}),
            payload=AccessChange,
        ),
        "revoke_access": OperationSpec(
            actors=frozenset({ActorKind.OPERATOR, ActorKind.INSTALLATION}),
            target=TargetKind.SCOPE,
            revisions=frozenset({"authority"}),
            payload=AccessChange,
        ),
        # Delegation is a separate operation from membership, and never a
        # room administrator's power over the operator's own authority.
        "delegate": OperationSpec(
            actors=frozenset({ActorKind.OPERATOR, ActorKind.INSTALLATION}),
            target=TargetKind.SCOPE,
            revisions=frozenset({"authority"}),
            payload=Delegation,
        ),
        "revoke_delegation": OperationSpec(
            actors=frozenset({ActorKind.OPERATOR, ActorKind.INSTALLATION}),
            target=TargetKind.SCOPE,
            revisions=frozenset({"authority"}),
            payload=Delegation,
        ),
        # The bilateral link lifecycle. Only holders approve/refuse; only the
        # initiating holder cancels their own request.
        "request_link": OperationSpec(
            actors=frozenset({ActorKind.HOLDER}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"identity", "ownership", "authority"}),
            payload=LinkRequest,
            bilateral=True,
        ),
        "approve_link": OperationSpec(
            actors=frozenset({ActorKind.HOLDER}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"identity", "ownership", "authority"}),
            payload=LinkApproval,
            bilateral=True,
        ),
        "refuse_link": OperationSpec(
            actors=frozenset({ActorKind.HOLDER}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"identity", "ownership"}),
            payload=LinkRefusal,
            bilateral=True,
        ),
        "cancel_link": OperationSpec(
            actors=frozenset({ActorKind.HOLDER}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"ownership"}),
            payload=LinkRefusal,
            bilateral=True,
        ),
        # Unlink is an independent authenticated ownership change — the
        # holder of that exact account, and only the holder.
        "unlink_account": OperationSpec(
            actors=frozenset({ActorKind.HOLDER}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"identity", "ownership", "authority"}),
            payload=Unlink,
        ),
        # Global identity lifecycle: separate from membership or grant, and
        # the whole of installation power over an identity — suspension is
        # not a claim on the account's ownership.
        "suspend_identity": OperationSpec(
            actors=frozenset({ActorKind.INSTALLATION}),
            target=TargetKind.PERSON,
            revisions=frozenset({"identity", "authority"}),
            payload=LifecycleAction,
        ),
        "reinstate_identity": OperationSpec(
            actors=frozenset({ActorKind.INSTALLATION}),
            target=TargetKind.PERSON,
            revisions=frozenset({"identity", "authority"}),
            payload=LifecycleAction,
        ),
        # Descriptive profile: holder for the global profile, operator for a
        # local nickname inside their scope.
        "update_profile": OperationSpec(
            actors=frozenset({ActorKind.HOLDER, ActorKind.OPERATOR}),
            target=TargetKind.PERSON,
            revisions=frozenset({"identity", "authority"}),
            payload=ProfileEdit,
        ),
    }
)


def _scoped(actor: Actor, scope: Scope, kind: str) -> None:
    """An operator's delegation must reach the scope it mutates — at the
    kind level here; id containment is the service's write-time check."""
    if actor.scope is None or not actor.scope.encloses(scope):
        raise OperationError(
            f"{kind!r} is outside the operator's bounded scope "
            f"{actor.scope} — delegation never reaches upward"
        )


def _cross_check(op: Operation) -> None:
    """The field/cross-field invariants a kind carries beyond its actor,
    target and payload types — what makes an operation not merely
    well-typed but coherent."""
    actor, target, payload = op.actor, op.target, op.payload
    if op.kind in ("observe_account", "register_account"):
        ids = cast(AccountObservation | AccountRegistration, payload).identifiers
        if target.account is None or target.account not in ids:
            raise OperationError(
                f"{op.kind!r}: the target account must be one of the "
                "identifiers the operation establishes"
            )
        if target.account.platform != _same_platform(ids, op.kind):
            raise OperationError(
                f"{op.kind!r}: identifiers disagree with the account's platform"
            )
        if actor.kind is ActorKind.SERVICE:
            if target.account.platform not in actor.sources:
                raise OperationError(
                    f"{op.kind!r}: {target.account.platform!r} is outside "
                    "the service's approved sources"
                )
        elif isinstance(payload, AccountRegistration):
            _scoped(actor, payload.scope, op.kind)
    elif op.kind == "sync_membership":
        outside = {i.platform for i in cast(RosterSync, payload).members} - actor.sources
        if outside:
            raise OperationError(
                f"sync_membership: roster platforms {sorted(outside)} are "
                "outside the service's approved sources"
            )
    elif op.kind == "set_membership":
        _scoped(actor, cast(Scope, target.scope), op.kind)
    elif op.kind in ("grant_access", "revoke_access", "delegate",
                     "revoke_delegation"):
        if actor.kind is ActorKind.OPERATOR:
            _scoped(actor, cast(Scope, target.scope), op.kind)
    elif op.kind == "request_link":
        if actor.account == target.account:
            raise OperationError(
                "request_link cannot bind an account to itself"
            )
    elif op.kind in ("approve_link", "refuse_link", "cancel_link"):
        payload = cast(LinkApproval | LinkRefusal, payload)
        if op.kind == "cancel_link" and payload.side is not LinkSide.INITIATING:
            raise OperationError(
                "cancel_link belongs to the initiating side alone"
            )
        if target.account != payload.pair.receiving:
            raise OperationError(
                f"{op.kind!r}: the target account is the request's "
                "receiving account — approval speaks for the pair, not a "
                "third account"
            )
        if actor.account != payload.pair.account(payload.side):
            raise OperationError(
                f"{op.kind!r}: the acting holder's account is not the "
                f"{payload.side.value} account it claims to speak for — "
                "a forwarded or replayed approval fails here"
            )
    elif op.kind == "unlink_account":
        if actor.account != target.account:
            raise OperationError(
                "unlink_account: the holder detaches exactly the account it "
                "proved — no other account, no other person's"
            )
    elif op.kind == "update_profile":
        payload = cast(ProfileEdit, payload)
        if actor.kind is ActorKind.HOLDER:
            if payload.scope is not None:
                raise OperationError(
                    "a holder edits the global profile — a scoped edit is an "
                    "operator's local nickname, not the holder's"
                )
            if actor.person_id != target.person_id:
                raise OperationError(
                    "a holder edits only their own profile"
                )
        elif payload.scope is None:
            raise OperationError(
                "an operator's profile edit names the scope its local "
                "nickname lives in"
            )
        else:
            _scoped(actor, payload.scope, op.kind)


@dataclass(frozen=True)
class Operation:
    """One typed request through the private management boundary.

    `operation_id` is the idempotency identity — a repeated event or retried
    request reuses it and gets the recorded result, never a second effect,
    and a reused id carrying a different effect is a conflict, not a retry.
    `observed_revisions` maps each revision name the spec requires to the
    observed value; commitment revalidates them, so they must be present at
    submission. `payload` is the operation's typed effect — the exact fields
    the kind is made of, checked against the type the spec declares, so a
    `request_link` cannot arrive without its surviving person and an
    `approve_link` cannot arrive without its request id, side, pair and
    survivor.

    The snapshot is taken at construction: revisions are copied behind a
    read-only mapping and every payload is a frozen value, so mutating the
    caller's structures afterwards cannot change what was validated.
    """

    operation_id: str
    kind: str
    actor: Actor
    target: Target
    payload: object
    observed_revisions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        spec = OPERATIONS.get(self.kind)
        if spec is None:
            raise OperationError(f"{self.kind!r} is not a management operation")
        if self.actor.kind not in spec.actors:
            raise OperationError(
                f"{self.kind!r} does not admit a {self.actor.kind.value} actor"
            )
        if self.target.kind is not spec.target:
            raise OperationError(
                f"{self.kind!r} targets {spec.target.value}, "
                f"not {self.target.kind.value}"
            )
        if not isinstance(self.payload, spec.payload):
            raise OperationError(
                f"{self.kind!r} carries a {spec.payload.__name__} payload, "
                f"not {type(self.payload).__name__}"
            )
        object.__setattr__(
            self,
            "observed_revisions",
            MappingProxyType(dict(self.observed_revisions)),
        )
        missing = spec.revisions - self.observed_revisions.keys()
        if missing:
            raise OperationError(
                f"{self.kind!r} must observe {sorted(missing)} — an approval "
                "cannot outlive the state it was given"
            )
        _nonblank("operation_id", self.operation_id)
        _cross_check(self)
