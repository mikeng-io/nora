"""The private management boundary's typed operation contract
(`design/managed-identity-access/management.md`).

Standard library only — same rule as the rest of `nora/domain`
(`nora/domain/__init__.py`).

These are the SHAPES every caller of the private boundary speaks in — the
file backend never issues them; they exist so the managed service cannot
accept an actor it cannot name, a target kind the operation does not take, or
a mutation submitted without the revisions its approval was based on. The
vocabulary is closed: a new operation kind is a contract change here, not a
string passed at a call site.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from nora.domain.identity import Identifier


class ActorKind(Enum):
    """Who may stand behind an operation.

    `SERVICE` is automated work carrying a service principal inside its
    approved sources. `HOLDER` is an authenticated account holder — the actor
    carries the exact account it proved. `OPERATOR` is scoped delegated
    authority bound to a workspace/unit/room. `INSTALLATION` is the
    installation authority established out of band. A model argument can never
    construct one of these; the boundary derives the actor from the
    authenticated invocation.
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


@dataclass(frozen=True)
class Actor:
    """The verified actor of one invocation.

    `HOLDER` carries the exact `account` it authenticated as. `SERVICE`
    carries `service_id` and its approved `sources`. `OPERATOR` carries
    `person_id` and the `scope` its delegation is bounded to. `INSTALLATION`
    carries `person_id` only — it is established out of band, not derived
    from an in-room event.
    """

    kind: ActorKind
    person_id: str | None = None
    account: Identifier | None = None
    service_id: str | None = None
    sources: frozenset[str] = field(default_factory=frozenset)
    scope: str | None = None

    def __post_init__(self) -> None:
        if self.kind is ActorKind.HOLDER and self.account is None:
            raise ValueError("a holder actor names the exact account it proved")
        if self.kind is ActorKind.SERVICE and not self.service_id:
            raise ValueError("a service actor names its service principal")
        if self.kind is ActorKind.OPERATOR and not self.scope:
            raise ValueError("an operator actor is bounded to a scope")


@dataclass(frozen=True)
class Target:
    """The subject of one operation — typed so `set_membership` cannot be
    handed an account, or `unlink_account` a room.

    `account` is the exact `Identifier` the operation names (account
    targets). `person_id` is the canonical person. `membership` names a
    (person, scope) pair. `scope` is the workspace/unit/room identifier —
    the caller supplies it, but only as a name: the service re-validates its
    actual containment before applying (`authorization.md`).
    """

    kind: TargetKind
    account: Identifier | None = None
    person_id: str | None = None
    scope: str | None = None


@dataclass(frozen=True)
class OperationSpec:
    """The contract one operation kind carries: who may issue it, what it
    targets, which observed revisions its approval was based on, and whether
    the bilateral protocol governs it.

    `revisions` names the state snapshots the submitter observed
    (`identity`, `ownership`, `authority`); the service revalidates them at
    commitment so a changed target or revoked approver cannot apply under an
    earlier interpretation.
    """

    actors: frozenset[ActorKind]
    target: TargetKind
    revisions: frozenset[str]
    bilateral: bool = False


#: The closed operation vocabulary. A kind absent here is not an operation;
#: the boundary cannot be asked for it. Ownership operations (link lifecycle,
#: unlink) are bilateral or holder-owned and never an operator power;
#: membership and grant changes are scoped operations; registration and
#: roster synchronisation are service operations within approved sources.
OPERATIONS: MappingProxyType[str, OperationSpec] = MappingProxyType(
    {
        # Discovery: a platform observation registers an unclaimed account
        # under an independent person. It grants no authority.
        "observe_account": OperationSpec(
            actors=frozenset({ActorKind.SERVICE}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"ownership"}),
        ),
        # Explicit registration under an approved onboarding rule.
        "register_account": OperationSpec(
            actors=frozenset({ActorKind.SERVICE, ActorKind.OPERATOR}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"ownership", "authority"}),
        ),
        # Roster synchronisation within the approved collection.
        "sync_membership": OperationSpec(
            actors=frozenset({ActorKind.SERVICE}),
            target=TargetKind.SCOPE,
            revisions=frozenset({"authority"}),
        ),
        # Membership and grant changes are scoped delegated operations.
        "set_membership": OperationSpec(
            actors=frozenset({ActorKind.OPERATOR}),
            target=TargetKind.MEMBERSHIP,
            revisions=frozenset({"authority"}),
        ),
        "grant_access": OperationSpec(
            actors=frozenset({ActorKind.OPERATOR}),
            target=TargetKind.SCOPE,
            revisions=frozenset({"authority"}),
        ),
        "revoke_access": OperationSpec(
            actors=frozenset({ActorKind.OPERATOR, ActorKind.INSTALLATION}),
            target=TargetKind.SCOPE,
            revisions=frozenset({"authority"}),
        ),
        # Delegation is a separate operation from membership, and never a
        # room administrator's power over the operator's own authority.
        "delegate": OperationSpec(
            actors=frozenset({ActorKind.OPERATOR, ActorKind.INSTALLATION}),
            target=TargetKind.SCOPE,
            revisions=frozenset({"authority"}),
        ),
        "revoke_delegation": OperationSpec(
            actors=frozenset({ActorKind.OPERATOR, ActorKind.INSTALLATION}),
            target=TargetKind.SCOPE,
            revisions=frozenset({"authority"}),
        ),
        # The bilateral link lifecycle. Only holders approve/refuse; only the
        # initiating holder cancels their own request.
        "request_link": OperationSpec(
            actors=frozenset({ActorKind.HOLDER}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"identity", "ownership", "authority"}),
            bilateral=True,
        ),
        "approve_link": OperationSpec(
            actors=frozenset({ActorKind.HOLDER}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"identity", "ownership", "authority"}),
            bilateral=True,
        ),
        "refuse_link": OperationSpec(
            actors=frozenset({ActorKind.HOLDER}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"identity", "ownership"}),
            bilateral=True,
        ),
        "cancel_link": OperationSpec(
            actors=frozenset({ActorKind.HOLDER}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"ownership"}),
            bilateral=True,
        ),
        # Unlink is an independent authenticated ownership change — holder
        # for their own account, installation for a compelled split.
        "unlink_account": OperationSpec(
            actors=frozenset({ActorKind.HOLDER, ActorKind.INSTALLATION}),
            target=TargetKind.ACCOUNT,
            revisions=frozenset({"identity", "ownership", "authority"}),
        ),
        # Global identity lifecycle: separate from membership or grant.
        "suspend_identity": OperationSpec(
            actors=frozenset({ActorKind.INSTALLATION}),
            target=TargetKind.PERSON,
            revisions=frozenset({"identity", "authority"}),
        ),
        "reinstate_identity": OperationSpec(
            actors=frozenset({ActorKind.INSTALLATION}),
            target=TargetKind.PERSON,
            revisions=frozenset({"identity", "authority"}),
        ),
        # Descriptive profile: holder for the global profile, operator for a
        # local nickname inside their scope.
        "update_profile": OperationSpec(
            actors=frozenset({ActorKind.HOLDER, ActorKind.OPERATOR}),
            target=TargetKind.PERSON,
            revisions=frozenset({"identity", "authority"}),
        ),
    }
)


class OperationError(ValueError):
    """An operation that violates the contract at construction: an actor the
    kind does not admit, a target kind it does not take, or missing observed
    revisions — refused before it can reach the boundary as a malformed
    request."""


@dataclass(frozen=True)
class Operation:
    """One typed request through the private management boundary.

    `operation_id` is the idempotency identity — a repeated event or retried
    request reuses it and gets the recorded result, never a second effect.
    `observed_revisions` maps each revision name the spec requires to the
    observed value; commitment revalidates them, so they must be present at
    submission. `inputs` carries the operation's typed arguments (e.g. the
    surviving person for a link, the delegated role for a grant).
    """

    operation_id: str
    kind: str
    actor: Actor
    target: Target
    observed_revisions: Mapping[str, str] = field(default_factory=dict)
    inputs: Mapping[str, object] = field(default_factory=dict)

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
        missing = spec.revisions - self.observed_revisions.keys()
        if missing:
            raise OperationError(
                f"{self.kind!r} must observe {sorted(missing)} — an approval "
                "cannot outlive the state it was given"
            )
        if not self.operation_id:
            raise OperationError("an operation names its idempotency identity")
