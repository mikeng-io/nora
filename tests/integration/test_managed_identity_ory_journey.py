"""The generated managed-identity journey over real PostgreSQL, Kratos and Keto.

One generated person registers independent Signal, Telegram and two Discord
accounts through the authenticated provisioning HTTP surface, consolidates the
four under one explicitly selected canonical person through the production
holder boundary (HTTP `sign_in` decisions for Discord/Telegram, the real
signal-cli private-consent handler for Signal), exercises scoped access in two
units, preserves a generated historical author row through consolidation,
unlinks one exact account, and replays a completed request.

What this measures: the trusted private management boundary — the production
use cases over `SqlBindingStore`, `SqlLinkRequests`, `KratosIdentityRegistry`
and `KetoAuthorityBackend` against disposable, uniquely named Ory containers
on dynamically assigned loopback ports. What it does NOT measure: the gateway
assertions here are generated `Nora-Authentication` tokens signed with a
generated key — they are the trusted-boundary input the production verifier
consumes, not proof of OAuth login, Signal transport ownership on a live
service, browser-to-Python acceptance or live-platform operation. The Signal
side's private transport is the real `SignalLinkConsent`/`parse_link_decision`
envelope path; its two delivery edges (challenge push, consent reply) use the
existing shared fakes (`FakeLinkChallengeDelivery`, `SinkSender`), the only
generated collaborators in the journey — a real signal-cli send is the
platform boundary a disposable stack cannot own.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
import pytest
import sqlalchemy as sa
from aiohttp import web

from nora.config_identity import ManagedOryConfig
from nora.domain.identity import Identifier
from nora.domain.managed import Verdict, room_scope_id
from nora.domain.management import (
    Actor,
    ActorKind,
    Delegation,
    Operation,
    Scope,
    ScopeKind,
    Target,
    TargetKind,
)
from nora.domain.models import NormalizedMessage
from nora.infra.sql import SqlRecordStore, create_sql_engine
from nora.people import AccountSessions, wire
from nora.people.app.holder_link import HolderLinkService
from nora.platform.signal.consent import SignalLinkConsent, SignalLinkDelivery
from nora.policy import Conversation, Policy
from nora.testing.link_delivery import FakeLinkChallengeDelivery
from nora.web.holder_links import register_holder_routes
from nora.web.management_api import ProvisioningCredential, register_management_routes
from nora.web.management_auth import AuthenticationIssuer, AuthenticationVerifier
from nora.web.management_sessions import register_session_routes
from tests.fakes import SinkSender
from tests.integration.managed_ory import _Ory
from tests.integration.managed_ory import ory as ory
from tests.unit.test_management_authentication import ISSUER, KEY, claims, signed

log = logging.getLogger(__name__)


# Generated journey identity material.

OWN_ACCOUNT = "gen-svc-signal"            # the generated signal-cli service account
SIG_PHONE = Identifier("signal", "phone", "gen-sig-num-0001")
SIG_UUID = Identifier("signal", "uuid", "gen-sig-uuid-0001")
TG = Identifier("telegram", "user_id", "gen-tg-0001")
DC1 = Identifier("discord", "user_id", "gen-dc-0001")
DC2 = Identifier("discord", "user_id", "gen-dc-0002")

P_SIG = "gen-person-sig"
P_TG = "gen-person-tg"
P_DC1 = "gen-person-dc1"                  # the explicitly selected survivor
P_DC2 = "gen-person-dc2"
INSTALLATION = "gen-installation"

UNIT_A = "gen-unit-a"
UNIT_B = "gen-unit-b"
ROOM_SIG = "gen-room-sig"
ROOM_TG = "gen-room-tg"
ROOM_DC = "gen-room-dc"

SCOPE_SIG = Scope(ScopeKind.ROOM, room_scope_id("signal", ROOM_SIG))
SCOPE_TG = Scope(ScopeKind.ROOM, room_scope_id("telegram", ROOM_TG))
SCOPE_DC = Scope(ScopeKind.ROOM, room_scope_id("discord", ROOM_DC))
SCOPE_A = Scope(ScopeKind.UNIT, UNIT_A)
SCOPE_B = Scope(ScopeKind.UNIT, UNIT_B)

PROVISIONING_TOKEN = "gen-provisioning-credential-0001"  # noqa: S105
METHODS = {
    "discord_oauth": "generated-oauth-client",
    "telegram_oidc": "generated-oidc-client",
    "signal_code": "generated-signal-issuer",
}
ACCOUNT_METHOD = {
    "discord": "discord_oauth",
    "telegram": "telegram_oidc",
    "signal": "signal_code",
}
GENERATED_PERSONS = {P_SIG, P_TG, P_DC1, P_DC2}

OPERATIONS = "/app/v1/management/operations"
RESOLVE = "/app/v1/management/accounts/resolve"
EXCHANGE = "/app/v1/management/auth/exchange"
CHECK = "/app/v1/management/auth/check"
LINK_REQUESTS = "/app/v1/holder/link-requests"
UNLINK = "/app/v1/holder/unlink"

_POLICY = Policy(
    conversations=(
        convo_sig := Conversation(
            platform="signal", ids=frozenset({ROOM_SIG}), persons=None,
            unit=UNIT_A, primary_id=ROOM_SIG),
        convo_tg := Conversation(
            platform="telegram", ids=frozenset({ROOM_TG}), persons=None,
            unit=UNIT_A, primary_id=ROOM_TG),
        convo_dc := Conversation(
            platform="discord", ids=frozenset({ROOM_DC}), persons=None,
            unit=UNIT_B, primary_id=ROOM_DC),
    ),
    _by_id={
        ("signal", ROOM_SIG): convo_sig,
        ("telegram", ROOM_TG): convo_tg,
        ("discord", ROOM_DC): convo_dc,
    },
)


class _Clock:
    """The explicit data clock the journey injects — ordering is constructor
    collaboration, never a sleep. Whole-second real time offset ten seconds
    back: a consent proof minted on wall time (`now-10s`, see `_assertion`)
    always satisfies the request's `created_at <= authenticated_at <= now`
    window (account_linking.py), while `iat - authenticated_at` stays inside
    the 60-second sign_in freshness bound."""

    def __call__(self) -> datetime:
        return datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=10)


def _ident_json(account: Identifier) -> dict[str, str]:
    return {"platform": account.platform, "kind": account.kind,
            "value": account.value}


def _assertion(
    account: Identifier, *, purpose: str, binding: dict | None = None,
) -> Callable[..., str]:
    """Mint a generated trusted-gateway assertion, not a provider proof."""
    def build(*, path: str, body: bytes, method: str = "POST") -> str:
        value = claims(
            account=_ident_json(account),
            authentication_method=ACCOUNT_METHOD[account.platform],
            proof_issuer=METHODS[ACCOUNT_METHOD[account.platform]],
            purpose=purpose, path=path, method=method,
            authenticated_at=int(time.time()) - 10,
            body_sha256=hashlib.sha256(body).hexdigest(),
            **({"binding": binding} if binding is not None else {}),
        )
        return "Nora-Authentication " + signed(value)
    return build


def _forged(*, path: str, body: bytes, method: str = "POST") -> str:
    """A signature under a key the verifier never trusted — refused at verify."""
    value = claims(
        account=_ident_json(DC1), authentication_method="discord_oauth",
        proof_issuer=METHODS["discord_oauth"], purpose="sign_in", path=path,
        method=method, body_sha256=hashlib.sha256(body).hexdigest(),
    )
    return "Nora-Authentication " + signed(value, key=b"generated-untrusted-key")


@dataclass
class _Journey:
    client: Any
    services: Any
    holder: HolderLinkService
    consent: SignalLinkConsent
    audit: SqlRecordStore
    engine: Any
    ory_session: aiohttp.ClientSession
    ory: _Ory
    pushed: FakeLinkChallengeDelivery
    sink: SinkSender
    bearer: dict[str, str]

    async def post(self, path: str, body: dict, *,
                   auth: Callable[..., str] | None = None,
                   forged: bool = False) -> tuple[int, dict]:
        raw = json.dumps(body, separators=(",", ":")).encode()
        headers = dict(self.bearer)
        if auth is not None:
            headers["Authorization"] = auth(path=path, body=raw)
        if forged:
            headers["Authorization"] = _forged(path=path, body=raw)
        response = await self.client.post(path, data=raw, headers=headers)
        return response.status, await response.json()

    async def get(self, path: str, *, auth: Callable[..., str]) -> tuple[int, dict]:
        response = await self.client.get(
            path, headers={
                "Authorization": auth(path=path, body=b"", method="GET")})
        return response.status, await response.json()

    async def sign_in(self, account: Identifier) -> Callable[..., str]:
        """`sign_in` at the exchange → the retained `session` assertion this
        account's holder carries until the binding changes."""
        status, body = await self.post(
            EXCHANGE, {}, auth=_assertion(account, purpose="sign_in"))
        assert status == 200, f"session exchange for {account} failed: {body}"
        return _assertion(account, purpose="session", binding=body["binding"])

    async def binding_of(self, account: Identifier) -> dict:
        """The live exact binding the trusted code-login gateway embeds —
        read from the real binding store, the same position the gateway holds."""
        record = await self.services.bindings.record_for(account=account)
        assert record is not None, f"no binding for {account}"
        person = await self.services.bindings.person(record.person_id)
        assert person is not None, f"no person for {record.person_id}"
        return {
            "person_id": person.person_id,
            "person_revision": str(person.revision),
            "account_id": record.account_id,
            "account_revision": str(record.revision),
        }

    async def signal_session(self) -> Callable[..., str]:
        """A signal-code session assertion — `signal_code` claims carry the
        gateway-embedded binding on every purpose (management_auth.py)."""
        return _assertion(
            SIG_PHONE, purpose="session",
            binding=await self.binding_of(SIG_PHONE))

    async def resolve(self, account: Identifier,
                      scope: Scope) -> tuple[int, dict]:
        return await self.post(RESOLVE, {
            "account": _ident_json(account),
            "scope": {"kind": scope.kind.value, "id": scope.id},
        })

    async def registration(self, *, person: str, target: Identifier,
                           identifiers: list[Identifier],
                           scope: Scope) -> tuple[int, dict]:
        return await self.post(OPERATIONS, {
            "operation_id": f"gen-reg-{person}",
            "kind": "register_account",
            "target": {"kind": "account", "account": _ident_json(target)},
            "payload": {
                "person_id": person,
                "identifiers": [_ident_json(i) for i in identifiers],
                "scope": {"kind": scope.kind.value, "id": scope.id},
            },
            "observed_revisions": {"ownership": "0", "authority": "0"},
        })

    async def human_op(self, session: Callable[..., str], *, operation_id: str,
                       kind: str, target: dict, payload: dict) -> tuple[int, dict]:
        return await self.post(OPERATIONS, {
            "operation_id": operation_id, "kind": kind, "target": target,
            "payload": payload, "observed_revisions": {"authority": "0"},
        }, auth=session)

    async def decide(self, auth: Callable[..., str] | None, *, request_id: str,
                     operation_id: str, nonce: str, action: str = "approve",
                     forged: bool = False) -> tuple[int, dict]:
        path = f"{LINK_REQUESTS}/{request_id}/decisions"
        return await self.post(path, {
            "operation_id": operation_id, "action": action, "nonce": nonce,
        }, auth=auth, forged=forged)

    async def installation(self, *, kind: str, scope: Scope,
                           payload: Any) -> Any:
        """The installation's out-of-band authority operations through the
        same production `AuthorityService` — never an HTTP surface."""
        operation = Operation(
            operation_id=f"gen-op-{kind}-{uuid.uuid4().hex[:10]}",
            kind=kind,
            actor=Actor(ActorKind.INSTALLATION, person_id=INSTALLATION),
            target=Target(TargetKind.SCOPE, scope=scope),
            payload=payload,
            observed_revisions={"authority": "0"},
        )
        return await self.services.authority.apply(operation)

    async def check(self, scope: Scope, permit: str,
                    person: str = P_DC1) -> Any:
        return await self.services.authority.check(
            scope=scope, person_id=person, permit=permit)

    async def kratos_state(self, person: str) -> Verdict:
        return (await self.services.registry.identity_state(
            external_id=person)).verdict


@pytest.fixture
async def journey(clean_pg_url: str, ory: _Ory, aiohttp_client) -> Iterator[_Journey]:
    engine = create_sql_engine(clean_pg_url)
    ory_session = aiohttp.ClientSession()
    services = wire.managed_services(
        managed=ManagedOryConfig(
            kratos_admin_url=ory.kratos_admin,
            kratos_public_url=ory.kratos_public,
            keto_read_url=ory.keto_read,
            keto_write_url=ory.keto_write,
        ),
        session=ory_session, policy=_POLICY, engine=engine, credentials={},
    )
    pushed = FakeLinkChallengeDelivery()
    sink = SinkSender()
    holder = HolderLinkService(
        services.bindings, services.registry, services.links,
        services.publication, services.registration,
        signal_delivery=pushed, clock=_Clock(),
    )
    consent = SignalLinkConsent(
        own_account=OWN_ACCOUNT, decide=holder.decide_from_transport,
        delivery=SignalLinkDelivery("http://generated-signal-cli.invalid",
                                    sender=sink),
    )
    verifier = AuthenticationVerifier([
        AuthenticationIssuer(ISSUER.name, KEY, METHODS),
    ])
    credential = ProvisioningCredential(
        actor=Actor(ActorKind.SERVICE, service_id="gen-provisioning-svc",
                    sources=frozenset({"signal", "telegram", "discord"})),
        token=PROVISIONING_TOKEN,
        scopes=frozenset({SCOPE_SIG, SCOPE_TG, SCOPE_DC, SCOPE_A, SCOPE_B}),
    )
    app = web.Application()
    register_management_routes(
        app, services=services, credentials=(credential,),
        authentication=verifier)
    register_holder_routes(app, holder=holder, verifier=verifier)
    register_session_routes(
        app, sessions=AccountSessions(services.bindings, services.registry),
        verifier=verifier)
    client = await aiohttp_client(app)
    try:
        yield _Journey(
            client=client, services=services, holder=holder, consent=consent,
            audit=SqlRecordStore(engine), engine=engine,
            ory_session=ory_session, ory=ory, pushed=pushed, sink=sink,
            bearer={"Authorization": f"Bearer {PROVISIONING_TOKEN}"},
        )
    finally:
        await services.aclose()
        await ory_session.close()
        await engine.dispose()


def _consent_envelope(*, request_id: str, nonce: str,
                      action: str = "approve") -> dict:
    """A signal-cli HTTP-transport event frame carrying the private decision."""
    return {
        "envelope": {
            "sourceNumber": SIG_PHONE.value,
            "sourceUuid": SIG_UUID.value,
            "timestamp": (int(time.time()) - 10) * 1000,
            "dataMessage": {
                "message": f"!!!link {action} {request_id} {nonce}",
            },
        },
        "account": OWN_ACCOUNT,
    }


def _signal_challenge(pushed: FakeLinkChallengeDelivery,
                      request_id: str) -> str:
    """The nonce the generated delivery fake captured for the Signal side."""
    sent = [text for account, text in pushed.sent
            if account in {SIG_PHONE, SIG_UUID}]
    assert sent, f"no signal challenge was pushed for {request_id}"
    match = re.search(rf"!!!link approve {re.escape(request_id)} (\S+)",
                      sent[-1])
    assert match, f"pushed challenge carries no approve line: {sent[-1]!r}"
    return match[1]


async def _register_accounts(j: _Journey):
    bindings = j.services.bindings
    # Absence first: an unregistered account answers `nothing_found`, so the
    # resolutions below are measured against a real negative.
    status, body = await j.resolve(DC1, SCOPE_DC)
    assert (status, body["status"]) == (404, "nothing_found")

    for person, target, idents, scope in (
        (P_SIG, SIG_PHONE, [SIG_PHONE, SIG_UUID], SCOPE_SIG),
        (P_TG, TG, [TG], SCOPE_TG),
        (P_DC1, DC1, [DC1], SCOPE_DC),
        (P_DC2, DC2, [DC2], SCOPE_DC),
    ):
        status, body = await j.registration(
            person=person, target=target, identifiers=idents, scope=scope)
        assert status == 200, f"registration for {person}: {body}"
        assert body["status"] == "complete"
        assert await bindings.person(person) is not None
        # Real Kratos answer: the registered identity exists and is ACTIVE.
        assert await j.kratos_state(person) is Verdict.ALLOWED

    # Four independent persons; no account resolves to another's person.
    persons = {}
    for account, scope in ((SIG_PHONE, SCOPE_SIG), (TG, SCOPE_TG),
                           (DC1, SCOPE_DC), (DC2, SCOPE_DC)):
        status, body = await j.resolve(account, scope)
        assert status == 200, body
        persons[account] = body["person_id"]
    assert persons == {
        SIG_PHONE: P_SIG, TG: P_TG, DC1: P_DC1, DC2: P_DC2}
    assert len(set(persons.values())) == 4

    sessions = {
        SIG_PHONE: await j.signal_session(),
        **{a: await j.sign_in(a) for a in (TG, DC1, DC2)},
    }
    retained_tg = sessions[TG]  # obsolete after consolidation — kept to prove it
    return sessions, retained_tg


async def _link_signal(j: _Journey, sessions: dict):
    bindings = j.services.bindings
    req_a = f"gen-link-a-{uuid.uuid4().hex[:8]}"
    status, body = await j.post(LINK_REQUESTS, {
        "request_id": req_a, "receiving": _ident_json(SIG_PHONE),
        "surviving_person": P_DC1,
    }, auth=sessions[DC1])
    assert status == 200, body
    dc_nonce = body["request"]["challenge"]["nonce"]
    assert body["request"]["side"] == "initiating"
    initiating_view = body
    status, receiving_view = await j.get(
        f"{LINK_REQUESTS}/{req_a}", auth=sessions[SIG_PHONE])
    assert status == 200
    assert receiving_view["request"]["side"] == "receiving"
    receiving_nonce = receiving_view["request"]["challenge"]["nonce"]
    assert receiving_nonce != dc_nonce
    assert receiving_nonce not in json.dumps(initiating_view)
    assert dc_nonce not in json.dumps(receiving_view)
    status, foreign_view = await j.get(
        f"{LINK_REQUESTS}/{req_a}", auth=sessions[TG])
    assert status == 403 and "request" not in foreign_view

    # Refusals, each beside its positive control below:
    # a retained session is never consent — the decision route wants sign_in.
    status, _ = await j.decide(sessions[DC1], request_id=req_a,
                               operation_id=f"gen-dec-{req_a}-x",
                               nonce=dc_nonce)
    assert status == 401
    # a wrong account cannot speak for either named side.
    status, body = await j.decide(
        _assertion(TG, purpose="sign_in"), request_id=req_a,
        operation_id=f"gen-dec-{req_a}-y", nonce=dc_nonce)
    assert status == 403 and body["status"] == "denied"
    # a foreign request answers for nothing.
    status, body = await j.decide(
        _assertion(DC1, purpose="sign_in"), request_id="gen-link-foreign",
        operation_id="gen-dec-foreign", nonce=dc_nonce)
    assert status == 403 and body["status"] == "denied"
    # a forged signature never reaches the service at all.
    status, body = await j.decide(
        None, request_id=req_a, operation_id="gen-dec-forged",
        nonce=dc_nonce, forged=True)
    assert status == 401 and body["status"] == "denied"

    # a refused decision must not consume the request: the positive controls
    # below publish it through the two real consent surfaces.

    # The initiating side alone records its approval — nothing is published.
    status, body = await j.decide(
        _assertion(DC1, purpose="sign_in"), request_id=req_a,
        operation_id=f"gen-dec-{req_a}-i", nonce=dc_nonce)
    assert status == 200, body
    assert body["state"] == "pending"
    status, body = await j.resolve(SIG_PHONE, SCOPE_SIG)
    assert status == 200
    assert body["person_id"] == P_SIG
    assert await bindings.canonical_person(P_SIG) == P_SIG

    # The Signal side answers over its trusted private transport — the real
    # signal-cli envelope path, challenge pushed through the shared fake.
    sig_nonce = _signal_challenge(j.pushed, req_a)
    assert sig_nonce == receiving_nonce
    assert await j.consent.dispatch(
        _consent_envelope(request_id=req_a, nonce=sig_nonce)) is True
    assert j.sink.sent, "the private consent reply never left the handler"
    assert req_a in j.sink.sent[-1][1]

    # Both exact holders approved → published: aliases, retirement, canonical.
    status, body = await j.resolve(SIG_PHONE, SCOPE_SIG)
    assert status == 200
    assert body["person_id"] == P_DC1
    status, body = await j.resolve(SIG_UUID, SCOPE_SIG)
    assert status == 200
    assert body["person_id"] == P_DC1
    status, body = await j.resolve(DC1, SCOPE_DC)
    assert status == 200
    assert body["person_id"] == P_DC1
    assert await bindings.canonical_person(P_SIG) == P_DC1
    assert await j.kratos_state(P_SIG) is Verdict.DENIED  # retired → inactive
    assert await j.kratos_state(P_DC1) is Verdict.ALLOWED
    return req_a, dc_nonce


async def _link_telegram(j: _Journey, sessions: dict):
    req_b = f"gen-link-b-{uuid.uuid4().hex[:8]}"
    status, body = await j.post(LINK_REQUESTS, {
        "request_id": req_b, "receiving": _ident_json(DC1),
        "surviving_person": P_DC1,
    }, auth=sessions[TG])
    assert status == 200, body
    tg_nonce = body["request"]["challenge"]["nonce"]
    status, body = await j.get(
        f"{LINK_REQUESTS}/{req_b}", auth=await j.sign_in(DC1))
    dc_nonce_b = body["request"]["challenge"]["nonce"]
    status, body = await j.decide(
        _assertion(TG, purpose="sign_in"), request_id=req_b,
        operation_id=f"gen-dec-{req_b}-i", nonce=tg_nonce)
    assert status == 200 and body["state"] == "pending"
    status, body = await j.decide(
        _assertion(DC1, purpose="sign_in"), request_id=req_b,
        operation_id=f"gen-dec-{req_b}-r", nonce=dc_nonce_b)
    assert status == 200 and body["state"] == "committed"
    status, body = await j.resolve(TG, SCOPE_TG)
    assert status == 200
    assert body["person_id"] == P_DC1
    assert await j.kratos_state(P_TG) is Verdict.DENIED


async def _grant_scoped_access(j: _Journey):
    services = j.services
    # The installation's out-of-band delegation seeds operator standing; the
    # holder then runs the supported scoped operations over the private HTTP
    # boundary under their retained session.
    for scope in (SCOPE_A, SCOPE_B):
        outcome = await j.installation(
            kind="delegate", scope=scope,
            payload=Delegation(
                delegatee=P_DC1,
                permits=frozenset({"manage_members", "manage_grants"})))
        assert outcome.status.value == "complete", outcome

    dc_session = await j.sign_in(DC1)
    for unit in (SCOPE_A, SCOPE_B):
        status, body = await j.human_op(
            dc_session, operation_id=f"gen-op-mem-{unit.id}",
            kind="set_membership",
            target={"kind": "membership", "person_id": P_DC1,
                    "scope": {"kind": unit.kind.value, "id": unit.id}},
            payload={"member": True})
        assert status == 200, f"set_membership {unit.id}: {body}"
    for unit, permits, grantee in (
            (SCOPE_A, ["view", "speak"], P_DC1),
            (SCOPE_B, ["view", "speak"], P_DC1)):
        status, body = await j.human_op(
            dc_session, operation_id=f"gen-op-grant-{unit.id}",
            kind="grant_access",
            target={"kind": "scope",
                    "scope": {"kind": unit.kind.value, "id": unit.id}},
            payload={"direction": "grant", "grantee": grantee,
                     "permits": permits})
        assert status == 200, f"grant_access {unit.id}: {body}"
    # An explicit restriction on the SOON-TO-BE-LOSER person — normalization
    # must carry the deny to the survivor, not drop it.
    status, body = await j.human_op(
        dc_session, operation_id="gen-op-restrict-b",
        kind="grant_access",
        target={"kind": "scope",
                "scope": {"kind": SCOPE_B.kind.value, "id": SCOPE_B.id}},
        payload={"direction": "restrict", "grantee": P_DC2,
                 "permits": ["view"]})
    assert status == 200, f"restriction: {body}"

    # Positive allows and their refusals, through the one authoritative path.
    for scope, permit in (
            (SCOPE_A, "view"), (SCOPE_B, "view"), (SCOPE_B, "speak")):
        result = await j.check(scope, permit)
        assert result.verdict is Verdict.ALLOWED and result.allowed, (
            f"expected allow {scope.id}/{permit}: {result}")
    result = await j.check(SCOPE_B, "view", person=P_DC2)
    assert result.verdict is Verdict.DENIED
    result = await j.check(SCOPE_A, "view", person="gen-person-absent")
    assert result.verdict is Verdict.DENIED  # unknown person, not a pass
    # Real Keto read-back: the restriction tuple exists against P_DC2 now.
    relations = await services.backend.relations_on(scope=SCOPE_B)
    assert isinstance(relations, frozenset)
    assert any(
        person == P_DC2 and relation != "member"
        for relation, person in relations), relations


async def _link_discord_with_history(j: _Journey, sessions: dict):
    bindings = j.services.bindings
    row_id = await j.audit.append(
        message=NormalizedMessage(
            platform="discord", conversation_id=ROOM_DC,
            conversation_type="group", sender_id=DC2.value,
            sender_name="generated-author", timestamp=1_700_000_000_000,
            body="generated historical text", raw={"generated": True},
        ),
        person_id=P_DC2,
    )

    req_c = f"gen-link-c-{uuid.uuid4().hex[:8]}"
    status, body = await j.post(LINK_REQUESTS, {
        "request_id": req_c, "receiving": _ident_json(DC1),
        "surviving_person": P_DC1,
    }, auth=sessions[DC2])
    assert status == 200, body
    dc2_nonce = body["request"]["challenge"]["nonce"]
    status, body = await j.get(
        f"{LINK_REQUESTS}/{req_c}", auth=await j.sign_in(DC1))
    dc_nonce_c = body["request"]["challenge"]["nonce"]
    status, body = await j.decide(
        _assertion(DC2, purpose="sign_in"), request_id=req_c,
        operation_id=f"gen-dec-{req_c}-i", nonce=dc2_nonce)
    assert status == 200 and body["state"] == "pending"
    status, body = await j.decide(
        _assertion(DC1, purpose="sign_in"), request_id=req_c,
        operation_id=f"gen-dec-{req_c}-r", nonce=dc_nonce_c)
    assert status == 200 and body["state"] == "committed"

    # All four exact accounts now resolve to the selected canonical person.
    for account, scope in ((SIG_PHONE, SCOPE_SIG), (SIG_UUID, SCOPE_SIG),
                           (TG, SCOPE_TG), (DC1, SCOPE_DC), (DC2, SCOPE_DC)):
        status, body = await j.resolve(account, scope)
        assert status == 200, (account, body)
        assert body["person_id"] == P_DC1, (account, body)
    assert await bindings.canonical_person(P_DC2) == P_DC1
    assert set(await bindings.former_persons(P_DC1)) == {P_SIG, P_TG, P_DC2}
    for retired in (P_SIG, P_TG, P_DC2):
        assert await j.kratos_state(retired) is Verdict.DENIED

    # The restriction followed the merge — deny still wins over the grant.
    result = await j.check(SCOPE_B, "view")
    assert result.verdict is Verdict.DENIED, (
        f"normalized restriction lost: {result}")
    result = await j.check(SCOPE_B, "speak")
    assert result.verdict is Verdict.ALLOWED  # the untouched permit stands
    result = await j.check(SCOPE_A, "view")
    assert result.verdict is Verdict.ALLOWED

    # The historical row kept its author person_id; canonical join names the
    # survivor — provenance preserved, never rewritten.
    async with j.engine.connect() as conn:
        stored = (await conn.execute(
            sa.text("SELECT person_id FROM messages WHERE id = :i"),
            {"i": row_id})).scalar_one()
    assert stored == P_DC2
    assert await bindings.canonical_person(stored) == P_DC1


async def _rebind_retained_session(j: _Journey, retained_tg: Callable[..., str]):
    status, body = await j.post(CHECK, {}, auth=retained_tg)
    assert status == 403 and body["status"] == "denied"
    rebound_tg = await j.sign_in(TG)
    status, body = await j.post(CHECK, {}, auth=rebound_tg)
    assert status == 200 and body["binding"]["person_id"] == P_DC1
    # The survivor still acts: the same holder surface, the same person.
    status, body = await j.human_op(
        rebound_tg, operation_id="gen-op-survivor-membership",
        kind="set_membership",
        target={"kind": "membership", "person_id": P_DC1,
                "scope": {"kind": SCOPE_B.kind.value, "id": SCOPE_B.id}},
        payload={"member": True})
    assert status == 200, body


async def _unlink_account(j: _Journey):
    dc2_session = await j.sign_in(DC2)
    status, body = await j.post(UNLINK, {
        "operation_id": f"gen-unlink-{uuid.uuid4().hex[:8]}",
        "expected_person": P_DC1,
    }, auth=dc2_session)
    assert status == 200, body
    status, body = await j.resolve(DC2, SCOPE_DC)
    assert (status, body["status"]) == (404, "nothing_found")
    status, body = await j.post(CHECK, {}, auth=dc2_session)
    assert status == 403 and body["status"] == "denied"
    # A still-bound account still acts — rebound after the last merge, since
    # a session retained across a binding change is exactly what must refuse.
    rebound_dc1 = await j.sign_in(DC1)
    status, body = await j.post(CHECK, {}, auth=rebound_dc1)
    assert status == 200 and body["binding"]["person_id"] == P_DC1


async def _replay_link(j: _Journey, req_a: str, dc_nonce: str):
    bindings, services = j.services.bindings, j.services
    # The EXACT initiating approval replayed verbatim — same operation id,
    # same effect — replays the recorded result; a second decision under a
    # fresh operation id is refused, and neither publishes the merge again.
    status, body = await j.decide(
        _assertion(DC1, purpose="sign_in"), request_id=req_a,
        operation_id=f"gen-dec-{req_a}-i", nonce=dc_nonce)
    assert status == 200 and body["state"] == "committed", body
    status, body = await j.decide(
        _assertion(DC1, purpose="sign_in"), request_id=req_a,
        operation_id=f"gen-dec-{req_a}-i2", nonce=dc_nonce)
    assert status == 403 and body["status"] == "denied", body
    link_a = await services.links.get(req_a)
    assert link_a is not None and link_a.state.value == "committed"
    assert set(await bindings.former_persons(P_DC1)) == {P_SIG, P_TG, P_DC2}
    status, body = await j.resolve(SIG_PHONE, SCOPE_SIG)
    assert status == 200
    assert body["person_id"] == P_DC1


async def _check_owned_state(j: _Journey):
    bindings, services = j.services.bindings, j.services
    # Kratos answers for exactly the generated persons; Keto tuples name only
    # generated persons; the link store holds only generated requests.
    async with j.ory_session.get(
            f"{j.ory.kratos_admin}/admin/identities") as response:
        assert response.status == 200
        identities = await response.json()
    external = {identity.get("external_id") for identity in identities}
    assert external <= GENERATED_PERSONS, external
    assert P_DC1 in external
    for scope in (SCOPE_A, SCOPE_B):
        relations = await services.backend.relations_on(scope=scope)
        assert isinstance(relations, frozenset)
        assert {person for _, person in relations} <= (
            GENERATED_PERSONS | {P_DC1}), relations
    journal = await bindings.operation(f"gen-reg-{P_DC1}")
    assert journal is not None and journal.state.value == "complete"


async def test_managed_identity_ory_journey(journey: _Journey) -> None:
    """Register, link bilaterally, enforce scope, preserve history and revoke over real Ory."""
    sessions, retained_tg = await _register_accounts(journey)
    request_id, nonce = await _link_signal(journey, sessions)
    await _link_telegram(journey, sessions)
    await _grant_scoped_access(journey)
    await _link_discord_with_history(journey, sessions)
    await _rebind_retained_session(journey, retained_tg)
    await _unlink_account(journey)
    await _replay_link(journey, request_id, nonce)
    await _check_owned_state(journey)
