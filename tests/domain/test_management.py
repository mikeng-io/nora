"""`nora.domain.management` — the private boundary's typed operation contract.

The executable property under test is the contract itself: an operation that
names a kind outside the vocabulary, an actor the kind does not admit, a
target kind it does not take, a wrong-typed or incoherent payload, a
cross-field value that cannot hold, or omits an observed revision is refused
at construction — before any adapter or store exists. Mutation attempts
against a constructed operation must fail too: a validated request is a
snapshot, not a view of the caller's structures. All ids are synthetic.
"""

from __future__ import annotations

import pytest

from nora.domain.identity import Identifier
from nora.domain.management import (
    ACCESS_PERMITS,
    MANAGEMENT_PERMITS,
    OPERATION_OUTCOMES,
    OPERATIONS,
    AccessChange,
    AccessDirection,
    AccountObservation,
    AccountRegistration,
    Actor,
    ActorKind,
    Delegation,
    LifecycleAction,
    LinkApproval,
    LinkPair,
    LinkRefusal,
    LinkRequest,
    LinkSide,
    MembershipStanding,
    Operation,
    OperationError,
    ProfileEdit,
    RosterSync,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
    Unlink,
)

ACCOUNT = Identifier(platform="signal", kind="uuid",
                     value="a1000001-0000-4000-8000-000000000001")
ACCOUNT_PHONE = Identifier(platform="signal", kind="phone", value="+10000000001")
RECEIVING = Identifier(platform="discord", kind="user_id", value="220000000000000001")

UNIT = Scope(kind=ScopeKind.UNIT, id="family")
ROOM = Scope(kind=ScopeKind.ROOM, id="kitchen")
WORKSPACE = Scope(kind=ScopeKind.WORKSPACE, id="home")

HOLDER = Actor(kind=ActorKind.HOLDER, person_id="miles", account=ACCOUNT,
               proof_issuer="signal-cli:install")
RECEIVER = Actor(kind=ActorKind.HOLDER, person_id="miles-b", account=RECEIVING,
                 proof_issuer="discord-client:1")
SERVICE = Actor(kind=ActorKind.SERVICE, service_id="bridge-signal",
                sources=frozenset({"signal"}))
OPERATOR = Actor(kind=ActorKind.OPERATOR, person_id="op", scope=UNIT)
ROOM_OPERATOR = Actor(kind=ActorKind.OPERATOR, person_id="op2", scope=ROOM)
INSTALLATION = Actor(kind=ActorKind.INSTALLATION, person_id="op")

PAIR = LinkPair(initiating=ACCOUNT, receiving=RECEIVING)


def _op(kind, actor, target, payload, revisions=(), **kw):
    return Operation(
        operation_id=kw.pop("operation_id", "req-0001"),
        kind=kind, actor=actor, target=target, payload=payload,
        observed_revisions={r: f"rev-{r}-1" for r in revisions})


def _link_revisions():
    return ("identity", "ownership", "authority")


def test_holder_link_request_is_a_typed_operation():
    op = _op("request_link", HOLDER,
             Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
             LinkRequest(surviving_person="miles"),
             revisions=_link_revisions())
    assert op.observed_revisions["ownership"] == "rev-ownership-1"
    assert op.payload.surviving_person == "miles"


def test_receiving_holder_approves_the_exact_pair():
    op = _op("approve_link", RECEIVER,
             Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
             LinkApproval(request_id="req-0001", side=LinkSide.RECEIVING,
                          pair=PAIR, surviving_person="miles"),
             revisions=_link_revisions())
    assert op.payload.pair.receiving == RECEIVING


def test_operator_sets_membership_in_their_scope():
    op = _op("set_membership", OPERATOR,
             Target(kind=TargetKind.MEMBERSHIP, person_id="miles", scope=ROOM),
             MembershipStanding(member=True), revisions=("authority",))
    assert op.target.scope is ROOM


def test_an_unknown_kind_is_not_an_operation():
    with pytest.raises(OperationError):
        _op("merge_people", HOLDER,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
            LinkRequest(surviving_person="miles"))


def test_a_service_actor_cannot_approve_a_link():
    """Service authentication is not holder consent: the vocabulary has no
    `approve_link` for a service principal, so a bridge cannot mint one."""
    with pytest.raises(OperationError):
        _op("approve_link", SERVICE,
            Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
            LinkApproval(request_id="r", side=LinkSide.RECEIVING, pair=PAIR,
                         surviving_person="miles"),
            revisions=_link_revisions())


def test_an_operator_cannot_unlink_an_account():
    """Ownership change is the holder's own power, never a room
    administrator's delegated authority."""
    with pytest.raises(OperationError):
        _op("unlink_account", OPERATOR,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
            Unlink(expected_person="miles"),
            revisions=_link_revisions())


def test_installation_cannot_unlink_an_account():
    """Installation suspends the global identity; it does not assert account
    ownership — a compelled split is not an unlink authority."""
    with pytest.raises(OperationError):
        _op("unlink_account", INSTALLATION,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
            Unlink(expected_person="miles"),
            revisions=_link_revisions())


def test_wrong_target_kind_is_refused():
    with pytest.raises(OperationError):
        _op("set_membership", OPERATOR,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
            MembershipStanding(member=True), revisions=("authority",))


def test_missing_observed_revisions_are_refused():
    """An approval may not outlive the state it was given: `request_link`
    without the authority revision is malformed, not deferred."""
    with pytest.raises(OperationError):
        _op("request_link", HOLDER,
            Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
            LinkRequest(surviving_person="miles"),
            revisions=("identity", "ownership"))


@pytest.mark.parametrize("bad_id", ["", " ", "\t"])
def test_a_blank_operation_id_is_refused(bad_id):
    with pytest.raises(OperationError):
        _op("request_link", HOLDER,
            Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
            LinkRequest(surviving_person="miles"),
            revisions=_link_revisions(), operation_id=bad_id)


def test_a_generic_payload_bag_is_refused():
    """`inputs` as an arbitrary mapping is exactly the hole this contract
    closes: the payload must be the kind's typed value, not a dict."""
    with pytest.raises(OperationError):
        _op("request_link", HOLDER,
            Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
            {"surviving_person": "miles"}, revisions=_link_revisions())


def test_request_link_without_a_surviving_person_is_refused():
    with pytest.raises(OperationError):
        _op("request_link", HOLDER,
            Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
            LinkRequest(surviving_person=" "), revisions=_link_revisions())


def test_request_link_cannot_bind_an_account_to_itself():
    with pytest.raises(OperationError):
        _op("request_link", HOLDER,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
            LinkRequest(surviving_person="miles"),
            revisions=_link_revisions())


@pytest.mark.parametrize("missing", ["request_id", "surviving_person"])
def test_approve_link_missing_required_fields_is_refused(missing):
    fields = {"request_id": "req-1", "side": LinkSide.RECEIVING, "pair": PAIR,
              "surviving_person": "miles"}
    fields[missing] = " "
    with pytest.raises(OperationError):
        _op("approve_link", RECEIVER,
            Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
            LinkApproval(**fields), revisions=_link_revisions())


def test_approve_link_actor_must_be_the_named_side():
    """An approval naming the receiving side but authenticated as the
    initiating account is the forwarded-code case: refused on the account
    comparison, whatever the text says."""
    with pytest.raises(OperationError):
        _op("approve_link", HOLDER,
            Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
            LinkApproval(request_id="r", side=LinkSide.RECEIVING, pair=PAIR,
                         surviving_person="miles"),
            revisions=_link_revisions())


def test_approve_link_target_is_the_receiving_account():
    with pytest.raises(OperationError):
        _op("approve_link", RECEIVER,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
            LinkApproval(request_id="r", side=LinkSide.RECEIVING, pair=PAIR,
                         surviving_person="miles"),
            revisions=_link_revisions())


def test_cancel_link_belongs_to_the_initiating_side():
    with pytest.raises(OperationError):
        _op("cancel_link", RECEIVER,
            Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
            LinkRefusal(request_id="r", side=LinkSide.RECEIVING, pair=PAIR),
            revisions=("ownership",))


def test_cancel_link_by_the_initiating_holder_constructs():
    op = _op("cancel_link", HOLDER,
             Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
             LinkRefusal(request_id="r", side=LinkSide.INITIATING, pair=PAIR),
             revisions=("ownership",))
    assert op.payload.side is LinkSide.INITIATING


def test_unlink_detaches_only_the_actors_own_account():
    with pytest.raises(OperationError):
        _op("unlink_account", HOLDER,
            Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
            Unlink(expected_person="miles"),
            revisions=_link_revisions())


def test_membership_target_needs_person_and_scope():
    """The shared membership shape must actually be expressible: a person in
    a scope, both named — a bare membership marker is malformed."""
    with pytest.raises(OperationError):
        Target(kind=TargetKind.MEMBERSHIP, person_id="miles")
    with pytest.raises(OperationError):
        Target(kind=TargetKind.MEMBERSHIP, scope=ROOM)
    with pytest.raises(OperationError):
        Target(kind=TargetKind.MEMBERSHIP, person_id=" ", scope=ROOM)


def test_operator_actor_requires_person_id_and_scope():
    with pytest.raises(OperationError):
        Actor(kind=ActorKind.OPERATOR, scope=UNIT)
    with pytest.raises(OperationError):
        Actor(kind=ActorKind.OPERATOR, person_id="op")


def test_holder_actor_requires_account_person_and_proof_issuer():
    with pytest.raises(OperationError):
        Actor(kind=ActorKind.HOLDER, person_id="miles")
    with pytest.raises(OperationError):
        Actor(kind=ActorKind.HOLDER, person_id="miles", account=ACCOUNT)
    with pytest.raises(OperationError):
        Actor(kind=ActorKind.HOLDER, account=ACCOUNT,
              proof_issuer="signal-cli:install")


def test_service_actor_requires_principal_and_sources():
    with pytest.raises(OperationError):
        Actor(kind=ActorKind.SERVICE)
    with pytest.raises(OperationError):
        Actor(kind=ActorKind.SERVICE, service_id="bridge-signal")


def test_an_actor_cannot_carry_another_kinds_fields():
    with pytest.raises(OperationError):
        Actor(kind=ActorKind.HOLDER, person_id="miles", account=ACCOUNT,
              proof_issuer="x", scope=UNIT)
    with pytest.raises(OperationError):
        Actor(kind=ActorKind.OPERATOR, person_id="op", scope=UNIT,
              service_id="svc")


def test_operator_delegation_never_reaches_upward():
    """A room-scoped operator cannot grant on a unit: at the kind level the
    containment is already impossible, whatever id the request carries."""
    with pytest.raises(OperationError):
        _op("grant_access", ROOM_OPERATOR,
            Target(kind=TargetKind.SCOPE, scope=UNIT),
            AccessChange(direction=AccessDirection.GRANT, grantee="miles",
                         permits=frozenset({"view"})),
            revisions=("authority",))


def test_unit_operator_grants_in_a_contained_room():
    op = _op("grant_access", OPERATOR,
             Target(kind=TargetKind.SCOPE, scope=ROOM),
             AccessChange(direction=AccessDirection.GRANT, grantee="miles",
                          permits=frozenset({"speak", "view"})),
             revisions=("authority",))
    assert op.payload.direction is AccessDirection.GRANT


def test_access_change_rejects_management_permits():
    """Grants and delegations are separate operations: a manage_* permit in
    grant_access is a privilege-escalation shape, refused at construction."""
    with pytest.raises(OperationError):
        AccessChange(direction=AccessDirection.GRANT, grantee="miles",
                     permits=frozenset({"manage_grants"}))


def test_delegation_rejects_access_permits():
    with pytest.raises(OperationError):
        Delegation(delegatee="miles", permits=frozenset({"view"}))


def test_access_change_restriction_is_first_class():
    change = AccessChange(direction=AccessDirection.RESTRICT, grantee="miles",
                          permits=frozenset({"speak"}))
    assert change.direction is AccessDirection.RESTRICT


def test_service_cannot_observe_outside_its_sources():
    with pytest.raises(OperationError):
        _op("observe_account", SERVICE,
            Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
            AccountObservation(identifiers=frozenset({RECEIVING})),
            revisions=("ownership",))


def test_observation_must_include_the_target_and_share_its_platform():
    with pytest.raises(OperationError):
        _op("observe_account", SERVICE,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
            AccountObservation(identifiers=frozenset({ACCOUNT_PHONE})),
            revisions=("ownership",))
    with pytest.raises(OperationError):
        AccountObservation(identifiers=frozenset({ACCOUNT, RECEIVING}))


def test_register_operator_must_reach_the_onboarding_scope():
    with pytest.raises(OperationError):
        _op("register_account", ROOM_OPERATOR,
            Target(kind=TargetKind.ACCOUNT, account=ACCOUNT),
            AccountRegistration(person_id="miles", scope=UNIT,
                                identifiers=frozenset({ACCOUNT})),
            revisions=("ownership", "authority"))


def test_sync_membership_rejects_roster_outside_sources():
    with pytest.raises(OperationError):
        _op("sync_membership", SERVICE,
            Target(kind=TargetKind.SCOPE, scope=UNIT),
            RosterSync(members=frozenset({ACCOUNT, RECEIVING}), complete=True),
            revisions=("authority",))


def test_holder_profile_edit_is_global_and_own_person():
    op = _op("update_profile", HOLDER,
             Target(kind=TargetKind.PERSON, person_id="miles"),
             ProfileEdit(display_name="Generated display name"),
             revisions=("identity", "authority"))
    assert op.payload.scope is None
    with pytest.raises(OperationError):
        _op("update_profile", HOLDER,
            Target(kind=TargetKind.PERSON, person_id="other"),
            ProfileEdit(display_name="Generated display name"),
            revisions=("identity", "authority"))
    with pytest.raises(OperationError):
        _op("update_profile", HOLDER,
            Target(kind=TargetKind.PERSON, person_id="miles"),
            ProfileEdit(nickname="Generated local nickname", scope=ROOM),
            revisions=("identity", "authority"))


def test_operator_profile_edit_needs_a_reached_scope():
    with pytest.raises(OperationError):
        _op("update_profile", OPERATOR,
            Target(kind=TargetKind.PERSON, person_id="miles"),
            ProfileEdit(display_name="Generated global name"),
            revisions=("identity", "authority"))


@pytest.mark.parametrize("values", [
    {}, {"display_name": 1}, {"nickname": "Generated"},
    {"display_name": "Generated", "scope": ROOM},
    {"display_name": "Generated", "nickname": "Local", "scope": ROOM},
    {"nickname": 1, "scope": ROOM},
])
def test_profile_edit_requires_the_actual_value_inside_its_own_scope(values):
    with pytest.raises(OperationError):
        ProfileEdit(**values)


def test_profile_update_idempotency_distinguishes_values_and_allows_clearing():
    first = ProfileEdit(display_name="Generated first")
    second = ProfileEdit(display_name="Generated second")
    assert first != second
    assert ProfileEdit(display_name="").display_name == ""
    assert ProfileEdit(nickname="", scope=ROOM).nickname == ""


def test_suspend_identity_is_installation_only_and_reasoned():
    op = _op("suspend_identity", INSTALLATION,
             Target(kind=TargetKind.PERSON, person_id="miles"),
             LifecycleAction(reason="holder request"),
             revisions=("identity", "authority"))
    assert op.payload.reason == "holder request"
    with pytest.raises(OperationError):
        _op("suspend_identity", OPERATOR,
            Target(kind=TargetKind.PERSON, person_id="miles"),
            LifecycleAction(reason="x"),
            revisions=("identity", "authority"))
    with pytest.raises(OperationError):
        LifecycleAction(reason=" ")


def test_constructed_operation_is_a_snapshot():
    """Mutating the caller's revision dict after construction must not
    change what was validated — and the stored mapping is read-only."""
    revisions = {"identity": "r1", "ownership": "r2", "authority": "r3"}
    op = Operation(
        operation_id="req-0001", kind="request_link", actor=HOLDER,
        target=Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
        payload=LinkRequest(surviving_person="miles"),
        observed_revisions=revisions)
    revisions.clear()
    assert op.observed_revisions["ownership"] == "r2"
    with pytest.raises(TypeError):
        op.observed_revisions["ownership"] = "forged"
    with pytest.raises(AttributeError):
        op.operation_id = "other"


def test_every_operation_declares_its_full_contract():
    for kind, spec in OPERATIONS.items():
        assert spec.actors, kind
        assert spec.target is not None, kind
        assert spec.revisions, kind
        assert spec.payload is not None, kind


def test_only_the_link_lifecycle_is_bilateral():
    assert {k for k, s in OPERATIONS.items() if s.bilateral} == {
        "request_link", "approve_link", "refuse_link", "cancel_link"}


def test_outcomes_keep_refusal_and_unavailability_distinct():
    assert set(OPERATION_OUTCOMES) == {
        "complete", "denied", "unavailable", "incomplete"}


def test_permit_vocabularies_match_the_declared_model():
    assert frozenset({"speak", "view"}) == ACCESS_PERMITS
    assert frozenset(
        {"manage_members", "manage_grants", "delegate"}) == MANAGEMENT_PERMITS


@pytest.mark.parametrize("value", ["", " ", "\t", " \n\t"])
def test_identifiers_reject_blank_values_at_the_shared_boundary(value):
    with pytest.raises(ValueError, match="non-blank"):
        Identifier("signal", "uuid", value)


@pytest.mark.parametrize("revision", ["identity", "ownership", "authority"])
@pytest.mark.parametrize("value", ["", " ", "\t", None, 1])
def test_required_revisions_name_nonblank_snapshot_tokens(revision, value):
    revisions = dict.fromkeys(_link_revisions(), "revision-1")
    revisions[revision] = value
    with pytest.raises(OperationError, match=r"revision.*non-blank"):
        Operation(
            operation_id="req-0001", kind="request_link", actor=HOLDER,
            target=Target(kind=TargetKind.ACCOUNT, account=RECEIVING),
            payload=LinkRequest(surviving_person="miles"),
            observed_revisions=revisions,
        )
