"""Both authenticated holders approve one immutable request before commitment."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from nora.domain.account_linking import (
    ConsentProof,
    ConsentSurface,
    LinkState,
    LinkStateError,
    PendingLink,
)
from nora.domain.identity import Identifier
from nora.domain.management import (
    Actor,
    ActorKind,
    LinkApproval,
    LinkRefusal,
    LinkRequest,
    LinkSide,
    Operation,
    Target,
    TargetKind,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
INITIATING = Identifier("discord", "user_id", "synthetic-initiating-account")
RECEIVING = Identifier("signal", "uuid", "synthetic-receiving-account")
LEFT = LinkSide.INITIATING
RIGHT = LinkSide.RECEIVING
REVISIONS = {"identity": "i-1", "ownership": "o-1", "authority": "a-1"}
NONCES = {LEFT: "generated-initiating-proof", RIGHT: "generated-receiving-proof"}


def actor(side):
    return Actor(ActorKind.HOLDER, person_id="synthetic-a" if side is LEFT else "synthetic-b",
                 account=INITIATING if side is LEFT else RECEIVING,
                 proof_issuer=("synthetic-discord-client" if side is LEFT
                               else "synthetic-signal-service"))


def pending():
    request = Operation(
        "synthetic-request", "request_link", actor(LEFT),
        Target(TargetKind.ACCOUNT, account=RECEIVING), LinkRequest("synthetic-a"), REVISIONS,
    )
    return PendingLink.request(
        request, receiving_person="synthetic-b", initiating_nonce=NONCES[LEFT],
        receiving_nonce=NONCES[RIGHT], created_at=NOW, expires_at=NOW + timedelta(minutes=5),
    )


def decision(link, side, kind="approve_link", **payload_changes):
    values = {"request_id": link.request_id, "side": side, "pair": link.pair}
    if kind == "approve_link":
        values["surviving_person"] = link.surviving_person
    values.update(payload_changes)
    payload = LinkApproval(**values) if kind == "approve_link" else LinkRefusal(**values)
    return Operation(
        f"synthetic-{kind}-{side.value}", kind, actor(side),
        Target(TargetKind.ACCOUNT, account=payload.pair.receiving), payload, REVISIONS,
    )


def proof(side, **changes):
    values = {
        "account": actor(side).account, "issuer": actor(side).proof_issuer,
        "surface": ConsentSurface.DISCORD_REAUTH if side is LEFT else ConsentSurface.SIGNAL_PRIVATE,
        "authenticated_at": NOW + timedelta(seconds=1),
        "request_id": "synthetic-request", "nonce": NONCES[side],
    }
    values.update(changes)
    return ConsentProof(**values)


def approve(link, side):
    return link.decide(decision(link, side), proof(side), now=NOW + timedelta(seconds=2))


def begin(link, **changes):
    values = {"now": NOW + timedelta(seconds=3),
              "owners": {INITIATING: "synthetic-a", RECEIVING: "synthetic-b"},
              "active_people": frozenset({"synthetic-a", "synthetic-b"}),
              "revisions": REVISIONS}
    values.update(changes)
    return link.begin_commit(**values)


def test_both_sides_approve_before_commit_and_commit_is_not_published_completion():
    initial = pending()
    assert initial.approved == frozenset() and not initial.ready
    one = approve(initial, LEFT)
    assert one.approved == frozenset({LEFT}) and not one.ready
    both = approve(one, RIGHT)
    assert both.ready
    committing = begin(both)
    assert committing.state is LinkState.COMMITTING and not committing.ready
    assert initial.approved == frozenset()


def test_approval_retains_account_exact_evidence_without_the_private_nonce():
    original = pending()
    decided = approve(original, RIGHT)
    receipt = decided.approvals[RIGHT]
    assert receipt.account == RECEIVING
    assert receipt.issuer == "synthetic-signal-service"
    assert receipt.surface is ConsentSurface.SIGNAL_PRIVATE
    assert receipt.authenticated_at == NOW + timedelta(seconds=1)
    assert receipt.approved_at == NOW + timedelta(seconds=2)
    assert receipt.operation_id == "synthetic-approve_link-receiving"
    assert receipt.effect_id == original.effect_id
    assert NONCES[RIGHT] not in repr(receipt)
    with pytest.raises(LinkStateError):
        replace(decided, surviving_person="synthetic-b")
    with pytest.raises(LinkStateError):
        replace(decided, revisions={**REVISIONS, "authority": "changed"})


@pytest.mark.parametrize("side", [LEFT, RIGHT])
def test_missing_either_independent_approval_prevents_commit(side):
    with pytest.raises(LinkStateError):
        begin(approve(pending(), side))


@pytest.mark.parametrize("changes", [
    {"nonce": "forwarded-initiating-proof"}, {"request_id": "another-request"},
    {"issuer": "another-issuer"},
    {"authenticated_at": NOW - timedelta(seconds=1)},
    {"authenticated_at": NOW + timedelta(seconds=3)},
    {"account": Identifier("signal", "uuid", "another-holder")},
])
def test_unrelated_or_stale_proof_cannot_become_receiving_approval(changes):
    link = pending()
    with pytest.raises(LinkStateError):
        link.decide(decision(link, RIGHT), proof(RIGHT, **changes), now=NOW + timedelta(seconds=2))


def test_forwarded_code_does_not_authenticate_the_receiving_holder():
    link = pending()
    with pytest.raises(LinkStateError):
        link.decide(decision(link, RIGHT), proof(LEFT, nonce=NONCES[RIGHT]),
                    now=NOW + timedelta(seconds=2))


@pytest.mark.parametrize("changes", [
    {"request_id": "another-request"}, {"surviving_person": "synthetic-b"},
])
def test_approval_cannot_substitute_another_request_or_surviving_person(changes):
    link = pending()
    with pytest.raises(LinkStateError):
        link.decide(decision(link, RIGHT, **changes), proof(RIGHT), now=NOW + timedelta(seconds=2))


@pytest.mark.parametrize("revision", ["identity", "ownership", "authority"])
def test_approval_and_commit_must_match_each_observed_revision(revision):
    link = pending()
    changed = {**REVISIONS, revision: "changed"}
    op = replace(decision(link, RIGHT), observed_revisions=changed)
    with pytest.raises(LinkStateError):
        link.decide(op, proof(RIGHT), now=NOW + timedelta(seconds=2))
    with pytest.raises(LinkStateError):
        begin(approve(approve(link, LEFT), RIGHT), revisions=changed)


@pytest.mark.parametrize("kind,side,state", [
    ("refuse_link", RIGHT, LinkState.REFUSED), ("cancel_link", LEFT, LinkState.CANCELLED),
])
def test_refusal_or_cancellation_is_terminal(kind, side, state):
    link = approve(pending(), LEFT)
    ended = link.decide(decision(link, side, kind), proof(side),
                        now=NOW + timedelta(seconds=2))
    assert ended.state is state
    with pytest.raises(LinkStateError):
        approve(ended, RIGHT)
    with pytest.raises(LinkStateError):
        begin(ended)


def test_expiry_and_a_consumed_approval_cannot_be_replayed():
    link = approve(pending(), LEFT)
    with pytest.raises(LinkStateError):
        approve(link, LEFT)
    with pytest.raises(LinkStateError):
        link.decide(decision(link, RIGHT), proof(RIGHT), now=link.expires_at)
    assert link.expire(link.expires_at).state is LinkState.EXPIRED
    with pytest.raises(LinkStateError):
        begin(approve(link, RIGHT), now=link.expires_at)


@pytest.mark.parametrize("changes", [
    {"owners": {INITIATING: "synthetic-a", RECEIVING: "another-person"}},
    {"active_people": frozenset({"synthetic-a"})},
    {"active_people": frozenset({"synthetic-b"})},
])
def test_commit_revalidates_both_owners_and_lifecycle(changes):
    with pytest.raises(LinkStateError):
        begin(approve(approve(pending(), LEFT), RIGHT), **changes)


def test_a_shared_nonce_or_third_person_survivor_is_not_a_bilateral_request():
    link = pending()
    with pytest.raises(LinkStateError):
        replace(link, nonce_hashes={LEFT: link.nonce_hashes[LEFT], RIGHT: link.nonce_hashes[LEFT]})
    with pytest.raises(LinkStateError):
        replace(link, surviving_person="unrelated-person")


def test_request_state_snapshots_mutable_inputs_and_never_renders_private_nonces():
    link = pending()
    revisions = dict(REVISIONS)
    owners = dict(link.owners)
    copied = replace(link, revisions=revisions, owners=owners)
    revisions.clear()
    owners.clear()
    assert dict(copied.revisions) == REVISIONS
    assert dict(copied.owners) == dict(link.owners)
    assert NONCES[LEFT] not in repr(proof(LEFT))
    assert NONCES[LEFT] not in repr(link)


def test_an_otp_redemption_or_wrong_platform_surface_is_not_consent():
    with pytest.raises(LinkStateError):
        proof(RIGHT, surface="signal-otp")
    with pytest.raises(LinkStateError):
        proof(RIGHT, surface=ConsentSurface.TELEGRAM_REAUTH)
