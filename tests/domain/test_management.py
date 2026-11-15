"""`nora.domain.management` — the private boundary's typed operation contract.

The executable property under test is the contract itself: an operation that
names a kind outside the vocabulary, an actor the kind does not admit, a
target kind it does not take, or omits an observed revision is refused at
construction — before any adapter or store exists. All ids are synthetic.
"""

from __future__ import annotations

import pytest

from nora.domain.identity import Identifier
from nora.domain.management import (
    OPERATION_OUTCOMES,
    OPERATIONS,
    Actor,
    ActorKind,
    Operation,
    OperationError,
    Target,
    TargetKind,
)

ACCOUNT = Identifier(platform="signal", kind="uuid",
                     value="a1000001-0000-4000-8000-000000000001")

HOLDER = Actor(kind=ActorKind.HOLDER, person_id="miles", account=ACCOUNT)
SERVICE = Actor(kind=ActorKind.SERVICE, service_id="bridge-signal",
                sources=frozenset({"signal"}))
OPERATOR = Actor(kind=ActorKind.OPERATOR, person_id="op",
                 scope="unit:family")
INSTALLATION = Actor(kind=ActorKind.INSTALLATION, person_id="op")


def _op(kind, actor, target, revisions=(), **kw):
    return Operation(
        operation_id=kw.pop("operation_id", "req-0001"),
        kind=kind, actor=actor, target=target,
        observed_revisions={r: f"rev-{r}-1" for r in revisions}, **kw)


def test_holder_link_request_is_a_typed_operation():
    op = _op("request_link", HOLDER,
             Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
             revisions=("identity", "ownership", "authority"),
             inputs={"surviving_person": "miles"})
    assert op.observed_revisions["ownership"] == "rev-ownership-1"


def test_an_unknown_kind_is_not_an_operation():
    with pytest.raises(OperationError):
        _op("merge_people", HOLDER,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT))


def test_a_service_actor_cannot_approve_a_link():
    """Service authentication is not holder consent: the vocabulary has no
    `approve_link` for a service principal, so a bridge cannot mint one."""
    with pytest.raises(OperationError):
        _op("approve_link", SERVICE,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
            revisions=("identity", "ownership", "authority"))


def test_an_operator_cannot_unlink_an_account():
    """Ownership change is holder or installation authority, never a room
    administrator's delegated power."""
    with pytest.raises(OperationError):
        _op("unlink_account", OPERATOR,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
            revisions=("identity", "ownership", "authority"))


def test_wrong_target_kind_is_refused():
    with pytest.raises(OperationError):
        _op("set_membership", OPERATOR,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
            revisions=("authority",))


def test_missing_observed_revisions_are_refused():
    """An approval may not outlive the state it was given: `request_link`
    without the authority revision is malformed, not deferred."""
    with pytest.raises(OperationError):
        _op("request_link", HOLDER,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
            revisions=("identity", "ownership"))


def test_an_empty_operation_id_is_refused():
    with pytest.raises(OperationError):
        _op("request_link", HOLDER,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
            revisions=("identity", "ownership", "authority"),
            operation_id="")


def test_every_operation_declares_actor_target_and_revisions():
    for kind, spec in OPERATIONS.items():
        assert spec.actors, kind
        assert spec.target is not None, kind
        assert spec.revisions, kind


def test_only_the_link_lifecycle_is_bilateral():
    assert {k for k, s in OPERATIONS.items() if s.bilateral} == {
        "request_link", "approve_link", "refuse_link", "cancel_link"}


def test_outcomes_keep_refusal_and_unavailability_distinct():
    assert set(OPERATION_OUTCOMES) == {
        "complete", "denied", "unavailable", "incomplete"}


def test_holder_actor_requires_the_exact_account():
    with pytest.raises(ValueError):
        Actor(kind=ActorKind.HOLDER, person_id="miles")


def test_service_actor_requires_its_principal():
    with pytest.raises(ValueError):
        Actor(kind=ActorKind.SERVICE)


def test_operator_actor_requires_a_scope():
    with pytest.raises(ValueError):
        Actor(kind=ActorKind.OPERATOR, person_id="op")
