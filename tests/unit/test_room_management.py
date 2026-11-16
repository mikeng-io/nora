"""The in-room `!!!member` command and `member_update` tool reach the ONE
authenticated invocation — and nothing else can.

`design/managed-identity-access/management.md` «In-room management», over
the in-memory fakes:

- the actor is the transport-verified sender resolved live — body text,
  quoted content and caller-supplied fields cannot move the effect to
  another person or another room, and a request carries no actor/scope
  field at all;
- every submit re-runs `AccountSessions.check` inside the mutation gate:
  a delegation revoked between two messages refuses at commitment, a
  suspended/down identity is `denied`/`unavailable`, never a silent apply;
- file mode is an authoritative `denied`, an outage is `unavailable`,
  edits/touches are not fresh instructions, ambiguous targets ask rather
  than guess, and a redelivered message replays its journaled effect
  instead of applying twice;
- the tool adapter shares this object: same store, same outcomes, and it
  answers `unavailable` unwired.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from nora.domain.identity import Identifier
from nora.domain.managed import (
    AccountState,
    ManagedAccount,
    ManagedMembership,
    ManagedPerson,
    MembershipState,
    OutcomeStatus,
    PersonLifecycle,
    room_scope_id,
)
from nora.domain.management import Scope, ScopeKind
from nora.domain.models import EditTarget, NormalizedMessage, Quote, Touch
from nora.obs.metrics import Metrics
from nora.people import (
    Clarify,
    RoomManagement,
    RoomRequest,
    is_room_command,
    parse_room_command,
)
from nora.people.adapters.tools import _make_member_update
from nora.people.app.managed import ManagedServices
from nora.people.wire import file_services
from nora.persons import load_persons
from nora.policy import load_policy
from nora.testing.managed import (
    FakeAuthorityBackend,
    FakeBindingStore,
    FakeIdentityRegistry,
    FakeLinkRequests,
    FakeTopologySource,
)

WS_ID = "ws-main"
UNIT_ID = "unit-a"
CONV = "conv-1"
ROOM_ID = room_scope_id("signal", CONV)
ROOM = Scope(ScopeKind.ROOM, ROOM_ID)
UNIT = Scope(ScopeKind.UNIT, UNIT_ID)

OP_ID = "+85290000101"
OP_PERSON = "person_op"
TARGET_ID = "+85290000102"
TARGET_PERSON = "person_target"
ALICE_PERSON = "person_alice"
ALICE_ID = "+85290000103"


def _message(
    body: str,
    *,
    sender: str = OP_ID,
    mentions: frozenset[str] = frozenset(),
    edit_of: EditTarget | None = None,
    touch: Touch | None = None,
    quote: Quote | None = None,
    message_id: str = "",
    timestamp: int = 1_700_000_000_000,
    conversation_id: str = CONV,
) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal",
        conversation_id=conversation_id,
        conversation_type="group",
        sender_id=sender,
        sender_name=None,
        timestamp=timestamp,
        body=body,
        raw={},
        mentions=mentions,
        quote=quote,
        message_id=message_id,
        edit_of=edit_of,
        touch=touch,
    )


def _services(
    bindings: FakeBindingStore | None = None,
) -> tuple[
    FakeBindingStore,
    FakeAuthorityBackend,
    FakeTopologySource,
    FakeIdentityRegistry,
    ManagedServices,
]:
    bindings = bindings or FakeBindingStore()
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={ROOM_ID: (UNIT_ID, False)},
        units={UNIT_ID: WS_ID},
        workspaces={WS_ID},
    )
    registry = FakeIdentityRegistry()
    services = ManagedServices(
        bindings, backend, topology, registry, FakeLinkRequests()
    )
    return bindings, backend, topology, registry, services


async def _person(
    bindings: FakeBindingStore,
    registry: FakeIdentityRegistry,
    person_id: str,
    phone: str,
    *,
    member: bool = False,
    display_name: str = "",
) -> None:
    await bindings.register(
        person=ManagedPerson(
            person_id=person_id,
            lifecycle=PersonLifecycle.ACTIVE,
            display_name=display_name,
        ),
        account=ManagedAccount(
            account_id=f"acct-{person_id}",
            person_id=person_id,
            platform="signal",
            identifiers=frozenset(
                {Identifier(platform="signal", kind="phone", value=phone)}
            ),
            state=AccountState.BOUND,
        ),
        membership=(
            ManagedMembership(
                person_id=person_id, scope=ROOM,
                state=MembershipState.MEMBER, source="registration",
            )
            if member
            else None
        ),
    )
    registry.identities[person_id] = {
        "id": f"kratos-{person_id}", "state": "active", "traits": {},
    }


async def _operator(
    bindings: FakeBindingStore,
    backend: FakeAuthorityBackend,
    registry: FakeIdentityRegistry,
) -> None:
    await _person(bindings, registry, OP_PERSON, OP_ID)
    await backend.put_relation(
        scope=ROOM, relation="grant_manage_members", person_id=OP_PERSON
    )


async def _member_phone(bindings: FakeBindingStore, person_id: str) -> bool:
    return any(
        m.person_id == person_id and m.state is MembershipState.MEMBER
        for m in await bindings.members_of(ROOM)
    )


# -- command surface ----------------------------------------------------


def test_a_fresh_room_command_is_detected_and_parsed():
    message = _message("!!!member add @alice")
    assert is_room_command(message)
    request = parse_room_command(message)
    assert request == RoomRequest(
        kind="add", reference="alice", reference_kind="name"
    )


def test_only_the_messages_own_fresh_body_can_be_a_command():
    assert not is_room_command(_message("thanks for the help"))
    assert not is_room_command(_message("!!!link approve whatever"))
    # An edit carrying `!!!member` text is not a fresh instruction; a
    # reaction is not a message at all; a quote is metadata, never body.
    edited = _message("!!!member add", edit_of=EditTarget(sent_ts=5))
    touched = _message(
        "",
        touch=Touch(
            kind="reaction", target=EditTarget(sent_ts=5), emoji="👍"
        ),
    )
    quoted = _message(
        "sounds good",
        quote=Quote(
            author_id="+85290000104", text="!!!member remove", quoted_ts=3
        ),
    )
    for message in (edited, touched, quoted):
        assert not is_room_command(message)
        assert parse_room_command(message) is None


def test_malformed_command_replies_with_usage_and_effects_nothing():
    assert parse_room_command(_message("!!!member")) == (
        "try `!!!member register <account or @who>` to register one account, "
        "or `!!!member add @who` / `!!!member remove @who` for room membership"
    )
    assert isinstance(
        parse_room_command(_message("!!!member fire @who")), str
    )


def test_the_request_type_carries_no_actor_scope_or_credential_field():
    # The intent both entries submit is the three resolution fields —
    # anything authority-relevant comes off the verified message itself.
    assert set(RoomRequest.__dataclass_fields__) == {
        "kind", "reference", "reference_kind",
    }


# -- the happy paths ------------------------------------------------------


async def test_register_mentions_one_unbound_account_as_a_new_member():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    management = RoomManagement(services)
    outcome = await management.invoke(
        _message("!!!member register", mentions=frozenset({TARGET_ID})),
        RoomRequest(kind="register"),
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    bound = await bindings.resolve("signal", TARGET_ID)
    assert bound is not None
    assert bound.person_id != OP_PERSON  # a new independent person
    assert await _member_phone(bindings, bound.person_id)
    assert registry.identities[bound.person_id]["state"] == "active"


async def test_same_phone_on_two_platforms_registers_independent_people():
    bindings, backend, topology, registry, services = _services()
    await _operator(bindings, backend, registry)
    other_room = Scope(ScopeKind.ROOM, room_scope_id("whatsapp", CONV))
    topology.rooms[other_room.id] = (UNIT_ID, False)
    await backend.put_relation(
        scope=other_room, relation="grant_manage_members", person_id=OP_PERSON
    )
    operator = await bindings.person(OP_PERSON)
    assert operator is not None
    await bindings.register(
        person=operator,
        account=ManagedAccount(
            account_id="acct-operator-whatsapp", person_id=OP_PERSON,
            platform="whatsapp", state=AccountState.BOUND,
            identifiers=frozenset({Identifier("whatsapp", "phone", OP_ID)}),
        ),
    )
    management = RoomManagement(services)
    people = []
    for platform in ("signal", "whatsapp"):
        outcome = await management.invoke(
            replace(_message("!!!member register", mentions=frozenset({TARGET_ID})),
                    platform=platform),
            RoomRequest(kind="register"),
        )
        assert outcome.status is OutcomeStatus.COMPLETE, (platform, outcome)
        account = await bindings.resolve(platform, TARGET_ID)
        assert account is not None
        assert account.identifiers == frozenset({Identifier(platform, "phone", TARGET_ID)})
        people.append(account.person_id)
    assert len(set(people)) == 2
    assert OP_PERSON not in people


async def test_add_and_remove_flip_one_scoped_membership():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    await _person(bindings, registry, TARGET_PERSON, TARGET_ID)
    management = RoomManagement(services)
    add = await management.invoke(
        _message("!!!member add", mentions=frozenset({TARGET_ID})),
        RoomRequest(kind="add"),
    )
    assert add.status is OutcomeStatus.COMPLETE
    assert await _member_phone(bindings, TARGET_PERSON)
    remove = await management.invoke(
        _message(
            "!!!member remove",
            mentions=frozenset({TARGET_ID}),
            timestamp=1_700_000_001_000,
        ),
        RoomRequest(kind="remove"),
    )
    assert remove.status is OutcomeStatus.COMPLETE
    assert not await _member_phone(bindings, TARGET_PERSON)


async def test_a_redelivered_message_never_applies_twice():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    await _person(bindings, registry, TARGET_PERSON, TARGET_ID)
    management = RoomManagement(services)
    # Membership ops replay through the journal: the message key pins the
    # operation id, so a platform redelivery answers `replayed` and adds
    # nothing.
    add_message = _message(
        "!!!member add",
        mentions=frozenset({TARGET_ID}),
        message_id="m-1",
    )
    first = await management.invoke(add_message, RoomRequest(kind="add"))
    again = await management.invoke(add_message, RoomRequest(kind="add"))
    assert first.status is OutcomeStatus.COMPLETE
    assert again.status is OutcomeStatus.COMPLETE
    assert again.detail in ("replayed", "already-observed")
    # A redelivered register finds the account it itself bound — the
    # honest `denied`, never a second person.
    register_message = _message(
        "!!!member register",
        mentions=frozenset({ALICE_ID}),
        message_id="m-2",
    )
    first = await management.invoke(
        register_message, RoomRequest(kind="register")
    )
    again = await management.invoke(
        register_message, RoomRequest(kind="register")
    )
    assert first.status is OutcomeStatus.COMPLETE
    assert again.status is OutcomeStatus.DENIED
    assert again.detail == "account-already-registered"
    assert len(bindings.people) == 3


# -- authority: nobody but the verified sender can act --------------------


async def test_an_unregistered_sender_cannot_invoke():
    _, _, _, _, services = _services()
    management = RoomManagement(services)
    outcome = await management.invoke(
        _message("!!!member add", sender="+85290000105"),
        RoomRequest(kind="add"),
    )
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "sender-unregistered"


async def test_a_member_without_the_permit_is_denied_not_guessed_at():
    bindings, _, _, registry, services = _services()
    # Target person IS a permitted operator's relative; the sender is a
    # plain member — the request's content never confers the sender's
    # authority.
    await _person(bindings, registry, OP_PERSON, OP_ID)
    await _person(bindings, registry, TARGET_PERSON, TARGET_ID)
    management = RoomManagement(services)
    outcome = await management.invoke(
        _message("!!!member add", mentions=frozenset({TARGET_ID})),
        RoomRequest(kind="add"),
    )
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "operator-not-authorized"
    assert not await _member_phone(bindings, TARGET_PERSON)


async def test_revocation_between_messages_refuses_at_commitment():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    await _person(bindings, registry, TARGET_PERSON, TARGET_ID)
    management = RoomManagement(services)
    assert (
        await management.invoke(
            _message("!!!member add", mentions=frozenset({TARGET_ID})),
            RoomRequest(kind="add"),
        )
    ).status is OutcomeStatus.COMPLETE
    await backend.drop_relation(
        scope=ROOM, relation="grant_manage_members", person_id=OP_PERSON
    )
    outcome = await management.invoke(
        _message(
            "!!!member remove",
            mentions=frozenset({TARGET_ID}),
            timestamp=1_700_000_001_000,
        ),
        RoomRequest(kind="remove"),
    )
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "operator-not-authorized"
    assert await _member_phone(bindings, TARGET_PERSON)


async def test_a_suspended_sender_denies_at_the_gate():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    bindings.people[OP_PERSON] = ManagedPerson(
        person_id=OP_PERSON,
        lifecycle=PersonLifecycle.SUSPENDED,
        revision=bindings.people[OP_PERSON].revision,
    )
    management = RoomManagement(services)
    outcome = await management.invoke(
        _message("!!!member register", mentions=frozenset({TARGET_ID})),
        RoomRequest(kind="register"),
    )
    assert outcome.status is OutcomeStatus.DENIED


async def test_an_edited_or_touched_command_is_refused_before_any_effect():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    management = RoomManagement(services)
    for message in (
        _message("!!!member add", edit_of=EditTarget(sent_ts=5)),
        _message(
            "!!!member add",
            touch=Touch(
                kind="reaction", target=EditTarget(sent_ts=5), emoji="👍"
            ),
        ),
    ):
        outcome = await management.invoke(
            message, RoomRequest(kind="add")
        )
        assert outcome.status is OutcomeStatus.DENIED
        assert outcome.detail == "not-a-fresh-message"


# -- scope and targets ----------------------------------------------------


async def test_a_room_the_topology_does_not_know_is_denied():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    management = RoomManagement(services)
    outcome = await management.invoke(
        _message("!!!member add", conversation_id="conv-elsewhere"),
        RoomRequest(kind="add"),
    )
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "unknown-scope"


async def test_an_ambiguous_mention_asks_instead_of_choosing():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    await _person(bindings, registry, TARGET_PERSON, TARGET_ID)
    await _person(bindings, registry, ALICE_PERSON, ALICE_ID)
    management = RoomManagement(services)
    result = await management.invoke(
        _message(
            "!!!member remove",
            mentions=frozenset({TARGET_ID, ALICE_ID}),
        ),
        RoomRequest(kind="remove"),
    )
    assert isinstance(result, Clarify)
    assert result.status == "ambiguous"


async def test_an_unresolvable_reference_is_nothing_found_not_a_guess():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    management = RoomManagement(services)
    result = await management.invoke(
        _message("!!!member add nobody-here"),
        RoomRequest(
            kind="add", reference="nobody-here", reference_kind="name"
        ),
    )
    assert isinstance(result, Clarify)
    assert result.status == "nothing_found"


async def test_a_room_member_can_be_named_for_membership():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    await _person(
        bindings, registry, ALICE_PERSON, ALICE_ID,
        member=True, display_name="Alice",
    )
    management = RoomManagement(services)
    outcome = await management.invoke(
        _message("!!!member remove alice"),
        RoomRequest(kind="remove", reference="alice", reference_kind="name"),
    )
    assert outcome.status is OutcomeStatus.COMPLETE
    assert not await _member_phone(bindings, ALICE_PERSON)


async def test_an_already_bound_account_cannot_register_again():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    management = RoomManagement(services)
    outcome = await management.invoke(
        _message("!!!member register", mentions=frozenset({OP_ID})),
        RoomRequest(kind="register"),
    )
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "account-already-registered"
    assert len(bindings.people) == 1


# -- failure honesty ------------------------------------------------------


async def test_file_mode_is_an_authoritative_denial_not_a_pretend_apply():
    persons = load_persons(Path("identity.example.toml"))
    policy = load_policy(Path("policy.example.toml"))
    management = RoomManagement(file_services(persons, policy))
    outcome = await management.invoke(
        _message("!!!member register", mentions=frozenset({TARGET_ID})),
        RoomRequest(kind="register"),
    )
    assert outcome.status is OutcomeStatus.DENIED
    assert outcome.detail == "file-mode"


async def test_a_down_backend_is_unavailable_never_denied():
    bindings, backend, topology, registry, services = _services()
    await _operator(bindings, backend, registry)
    management = RoomManagement(services)
    message = _message(
        "!!!member register", mentions=frozenset({TARGET_ID})
    )
    request = RoomRequest(kind="register")

    topology.down = True
    outcome = await management.invoke(message, request)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    topology.down = False

    bindings.down = True
    outcome = await management.invoke(message, request)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    bindings.down = False

    registry.down = True
    outcome = await management.invoke(message, request)
    assert outcome.status is OutcomeStatus.UNAVAILABLE
    assert not await _member_phone(bindings, OP_PERSON)


# -- the tool is the same invocation ----------------------------------------


def _tool_deps(management, message) -> SimpleNamespace:
    return SimpleNamespace(
        metrics=Metrics(), room_management=management, message=message
    )


async def _tool(deps, **kwargs):
    spec_fn = _make_member_update(deps)
    return await spec_fn(**kwargs)


async def test_the_tool_lands_the_same_effect_as_the_command():
    bindings, backend, _, registry, services = _services()
    await _operator(bindings, backend, registry)
    management = RoomManagement(services)
    message = _message(
        "@nora please register the new person",
        mentions=frozenset({TARGET_ID}),
    )
    result = await _tool(_tool_deps(management, message), action="register")
    assert result["status"] == "ok"
    bound = await bindings.resolve("signal", TARGET_ID)
    assert bound is not None
    assert await _member_phone(bindings, bound.person_id)


async def test_the_tool_maps_refusal_clarify_and_unavailable():
    bindings, backend, _, registry, services = _services()
    await _person(bindings, registry, OP_PERSON, OP_ID)  # no permit
    await _person(bindings, registry, TARGET_PERSON, TARGET_ID)
    await _person(bindings, registry, ALICE_PERSON, ALICE_ID)
    management = RoomManagement(services)
    message = _message(
        "@nora add them", mentions=frozenset({TARGET_ID})
    )
    refused = await _tool(_tool_deps(management, message), action="add")
    assert refused["status"] == "refused"
    await backend.put_relation(
        scope=ROOM, relation="grant_manage_members", person_id=OP_PERSON
    )
    ambiguous = await _tool(
        _tool_deps(
            management,
            _message(
                "@nora remove them",
                mentions=frozenset({TARGET_ID, ALICE_ID}),
            ),
        ),
        action="remove",
    )
    assert ambiguous["status"] == "ambiguous"


async def test_an_unwired_tool_answers_unavailable_with_no_invocation():
    message = _message("@nora add them", mentions=frozenset({TARGET_ID}))
    for deps in (
        SimpleNamespace(metrics=Metrics(), room_management=None, message=message),
        _tool_deps(None, None),
    ):
        result = await _tool(deps, action="add")
        assert result["status"] == "unavailable"
