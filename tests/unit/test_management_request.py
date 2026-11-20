"""Untrusted JSON becomes one typed effect under the server's verified actor."""

from __future__ import annotations

import copy
import json

import pytest

from nora.domain.identity import Identifier
from nora.domain.management import (
    AccountObservation,
    Actor,
    ActorKind,
    LinkApproval,
    LinkSide,
    MembershipStanding,
    Scope,
    ScopeKind,
)
from nora.web.management_request import ManagementRequestError, decode_management_request

INITIATOR = {"platform": "discord", "kind": "user_id", "value": "220000000000000001"}
RECEIVER = {"platform": "signal", "kind": "uuid", "value": "synthetic-receiving-account"}
HOLDER = Actor(
    kind=ActorKind.HOLDER, person_id="synthetic-person",
    account=Identifier(**INITIATOR), proof_issuer="synthetic-discord-client",
)
OPERATOR = Actor(
    kind=ActorKind.OPERATOR, person_id="synthetic-operator",
    scope=Scope(ScopeKind.UNIT, "synthetic-unit"),
)


def request_link():
    return {
        "operation_id": "synthetic-operation", "kind": "request_link",
        "target": {"kind": "account", "account": copy.deepcopy(RECEIVER)},
        "payload": {"surviving_person": "synthetic-person"},
        "observed_revisions": {"identity": "i-1", "ownership": "o-1", "authority": "a-1"},
    }


def decode(body, actor=HOLDER):
    return decode_management_request(json.dumps(body).encode(), actor=actor)


def test_request_uses_the_authenticated_actor_and_typed_domain_effect():
    operation = decode(request_link())
    assert operation.actor is HOLDER
    assert operation.target.account == Identifier(**RECEIVER)
    assert operation.payload.surviving_person == "synthetic-person"
    assert operation.observed_revisions["ownership"] == "o-1"


def test_profile_request_binds_the_actual_name_without_accepting_a_field_only_effect():
    body = {
        "operation_id": "synthetic-profile", "kind": "update_profile",
        "target": {"kind": "person", "person_id": HOLDER.person_id},
        "payload": {"display_name": "Generated name"},
        "observed_revisions": {"identity": "i-1", "authority": "a-1"},
    }
    operation = decode(body)
    assert operation.payload.display_name == "Generated name"
    body["payload"] = {"fields": ["display_name"]}
    with pytest.raises(ManagementRequestError):
        decode(body)
    body["payload"] = {"display_name": True}
    with pytest.raises(ManagementRequestError):
        decode(body)


@pytest.mark.parametrize("sources,accepted", [({"signal"}, True), ({"discord"}, False)])
def test_observation_retains_account_grouping_within_the_authenticated_service_source(
    sources, accepted,
):
    actor = Actor(kind=ActorKind.SERVICE, service_id="synthetic-bridge", sources=sources)
    alternate = {"platform": "signal", "kind": "phone", "value": "synthetic-phone-id"}
    body = {
        "operation_id": "synthetic-observation", "kind": "observe_account",
        "target": {"kind": "account", "account": RECEIVER},
        "payload": {"identifiers": [RECEIVER, alternate]},
        "observed_revisions": {"ownership": "o-1"},
    }
    if not accepted:
        with pytest.raises(ManagementRequestError):
            decode(body, actor)
        return
    operation = decode(body, actor)
    assert operation.actor is actor
    assert isinstance(operation.payload, AccountObservation)
    assert operation.payload.identifiers == frozenset({
        Identifier(**RECEIVER), Identifier(**alternate),
    })


def test_an_explicit_approval_retains_exact_request_pair_and_side():
    body = request_link()
    body["kind"] = "approve_link"
    body["payload"] = {
        "request_id": "synthetic-request", "side": "initiating",
        "pair": {"initiating": INITIATOR, "receiving": RECEIVER},
        "surviving_person": "synthetic-person",
    }
    operation = decode(body)
    assert isinstance(operation.payload, LinkApproval)
    assert operation.payload.side is LinkSide.INITIATING
    assert operation.payload.request_id == "synthetic-request"
    assert operation.payload.pair.initiating == HOLDER.account


def test_initiator_cannot_claim_the_receiving_approval_side():
    body = request_link()
    body["kind"] = "approve_link"
    body["payload"] = {
        "request_id": "synthetic-request", "side": "receiving",
        "pair": {"initiating": INITIATOR, "receiving": RECEIVER},
        "surviving_person": "synthetic-person",
    }
    with pytest.raises(ManagementRequestError):
        decode(body)


@pytest.mark.parametrize("location", ["body", "target", "account", "payload"])
def test_actor_or_unknown_fields_cannot_be_smuggled_into_any_level(location):
    body = request_link()
    destinations = {"body": body, "target": body["target"],
                    "account": body["target"]["account"], "payload": body["payload"]}
    destinations[location]["actor"] = {"kind": "installation", "person_id": "forged"}
    with pytest.raises(ManagementRequestError):
        decode(body)


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None])
def test_membership_boolean_is_not_coerced_from_untrusted_values(value):
    body = request_link()
    body.update(kind="set_membership", target={
        "kind": "membership", "person_id": "synthetic-member",
        "scope": {"kind": "room", "id": "synthetic-room"},
    }, payload={"member": value})
    with pytest.raises(ManagementRequestError):
        decode(body, OPERATOR)


def test_real_boolean_constructs_the_membership_effect():
    body = request_link()
    body.update(kind="set_membership", target={
        "kind": "membership", "person_id": "synthetic-member",
        "scope": {"kind": "room", "id": "synthetic-room"},
    }, payload={"member": False})
    operation = decode(body, OPERATOR)
    assert operation.payload == MembershipStanding(member=False)


@pytest.mark.parametrize("change", [
    {"kind": "invented_operation"},
    {"operation_id": " "},
    {"target": {"kind": "account"}},
    {"payload": {}},
    {"observed_revisions": {"identity": "i-1", "ownership": "o-1", "authority": " "}},
    {"observed_revisions": {"identity": "i-1", "ownership": "o-1", "authority": 1}},
])
def test_malformed_effects_and_revisions_are_refused(change):
    body = request_link()
    body.update(change)
    with pytest.raises(ManagementRequestError):
        decode(body)


@pytest.mark.parametrize("raw", [b"{", b"null", b"[]", b"true", b"\xff"])
def test_invalid_or_nonobject_json_is_refused(raw):
    with pytest.raises(ManagementRequestError):
        decode_management_request(raw, actor=HOLDER)


def test_schema_errors_do_not_echo_private_identifiers_or_request_values():
    body = request_link()
    body["payload"]["private-invalid-field"] = "private-generated-marker"
    with pytest.raises(ManagementRequestError) as error:
        decode(body)
    assert str(error.value) == "invalid management request"
    assert error.value.__suppress_context__


def test_decoded_operation_is_independent_of_later_request_mutations():
    body = request_link()
    operation = decode(body)
    body["observed_revisions"].clear()
    body["payload"]["surviving_person"] = "another-person"
    assert operation.payload.surviving_person == "synthetic-person"
    assert operation.observed_revisions["ownership"] == "o-1"


def test_scoped_profile_request_binds_nickname_and_scope_to_the_operator():
    """The operator's scoped nickname decodes with the actor bound to the
    PAYLOAD's scope — the write-time checks revalidate its actual reach."""
    body = {
        "operation_id": "synthetic-scoped-profile", "kind": "update_profile",
        "target": {"kind": "person", "person_id": "synthetic-member"},
        "payload": {"nickname": "Generated nickname",
                    "scope": {"kind": "room", "id": "synthetic-room"}},
        "observed_revisions": {"identity": "i-1", "authority": "a-1"},
    }
    operation = decode_management_request(
        json.dumps(body).encode(), operator_person="synthetic-operator")
    assert operation.actor.kind is ActorKind.OPERATOR
    assert operation.actor.person_id == "synthetic-operator"
    assert operation.actor.scope == Scope(
        ScopeKind.ROOM, "synthetic-room")
    assert operation.payload.nickname == "Generated nickname"
    assert operation.payload.scope == Scope(ScopeKind.ROOM, "synthetic-room")
    # An operator cannot decode a global edit — the local scope is required.
    body["payload"] = {"display_name": "Generated name"}
    with pytest.raises(ManagementRequestError):
        decode_management_request(
            json.dumps(body).encode(), operator_person="synthetic-operator")
