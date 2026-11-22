"""The combined managed identity journey over real SQL + pinned Kratos/Keto.

One causal path over one live graph — the disposable Ory containers are
function-scoped, so the whole journey runs in a single test: generated
platform accounts register once through the real roster boundary over
`SqlBindingStore` + the Ory graph the `managed_services` factory selects;
bilateral consent over the real holder routes and the real Signal transport
parser/consent seam binds four distinct accounts; session exchange/check, scoped
management and the registered portal data routes hold for the right viewers;
revocation and holder-exact unlink refuse afterwards.

Locally minted `Nora-Authentication` assertions stand in for provider
callbacks — generated gateway evidence only, never provider or browser proof.
The recording sender stands in for the signal-cli push side only; no store,
backend or registry is substituted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import aiohttp
import jwt
import pytest
import sqlalchemy as sa
from aiohttp import web

from nora.app.documents_service import rooms_named
from nora.app.otp import OtpService
from nora.config_identity import ManagedOryConfig
from nora.domain.documents import RevisionIn
from nora.domain.identity import Identifier
from nora.domain.managed import Verdict, room_scope_id
from nora.domain.management import Actor, ActorKind, Scope, ScopeKind
from nora.infra.sql.documents import SqlDocumentStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.otp import SqlOtpStore
from nora.obs.metrics import Metrics
from nora.people import AccountSessions, PortalScopes, wire
from nora.people.app.holder_link import HolderLinkService
from nora.persons import Persons
from nora.platform.signal.consent import SignalLinkConsent, SignalLinkDelivery
from nora.policy import load_policy
from nora.testing import FakeRecordStore
from nora.testing.fakes import FakeAdapter
from nora.testing.reminders import FakeReminderStore
from nora.web.api import register_web_routes
from nora.web.holder_links import register_holder_routes
from nora.web.managed_viewer import register_managed_viewer
from nora.web.management_api import ProvisioningCredential, register_management_routes
from nora.web.management_auth import (
    ASSERTION_AUDIENCE,
    ASSERTION_TYPE,
    AuthenticationIssuer,
    AuthenticationVerifier,
)
from nora.web.management_sessions import register_session_routes
from nora.web.otp import register_otp_routes
from tests.conftest import build_test_doctrine_store
from tests.integration.managed_ory import ory as ory
from tests.web.test_api import NO_SKILLS_DIR, _fake_events

EXCHANGE = "/app/v1/management/auth/exchange"
CHECK = "/app/v1/management/auth/check"
OPERATIONS = "/app/v1/management/operations"
ROSTERS = "/app/v1/management/rosters"
REQUESTS = "/app/v1/holder/link-requests"
UNLINK = "/app/v1/holder/unlink"
DOCUMENTS = "/app/v1/documents"
OTP_REQUEST = "/app/v1/otp/request"
OTP_VERIFY = "/app/v1/otp/verify"

KEY = b"generated-journey-gateway-key-with-32bytes"
# The generated gateway keeps proof issuers bound to their authentication method.
GATEWAY = AuthenticationIssuer("generated-gateway", KEY, {
    "discord_oauth": "generated-discord-client",
    "telegram_oidc": "generated-telegram-client",
    "signal_code": "nora-otp",
    "whatsapp_code": "nora-otp",
})

SERVICE_NUMBER = "+85290000099"
OTP_TOKEN = "generated-otp-api-token"  # noqa: S105 -- generated fixture credential
BRIDGE_TOKEN = "generated-bridge-token"  # noqa: S105 -- generated fixture credential
BEARER = {"Authorization": f"Bearer {BRIDGE_TOKEN}"}
OTP_AUTH = {"Authorization": f"Bearer {OTP_TOKEN}"}

UNIT_A = "generated-unit-a"
UNIT_B = "generated-unit-b"
UNIT_SCOPE_A = Scope(ScopeKind.UNIT, UNIT_A)
ROOM_A = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-a"))
ROOM_B = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-b"))
ROOM_C = Scope(ScopeKind.ROOM, room_scope_id("signal", "generated-room-c"))

SIG_A1 = Identifier("signal", "phone", "+85290000101")
SIG_A2 = Identifier("signal", "phone", "+85290000102")
DISC = Identifier("discord", "user_id", "generated-discord-1")
TG = Identifier("telegram", "user_id", "generated-telegram-1")
OP = Identifier("discord", "user_id", "generated-operator-1")
FOREIGN_ACI = "a1000099-0000-4000-8000-000000000099"
A2_ACI = "a2000099-0000-4000-8000-000000000099"
RECEIPT = Path("/tmp/nora-managed-intake/nor5-journey-proof/receipt.json")  # noqa: S108 -- proof artifact outside the repo


def _write_receipt(evidence: list[dict[str, object]]) -> None:
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(
        {"journey": "managed-identity-integration", "evidence": evidence},
        indent=2))

# Topology stays Nora configuration (`authorization.md`): a generated policy
# names the rooms and their units; `load_policy` builds the Policy the
# FileTopologySource reads.
POLICY_TOML = """
[[conversations]]
platform = "signal"
id = "generated-room-a"
unit = "generated-unit-a"
label = "Room A"
persons = ["generated-member"]

[[conversations]]
platform = "signal"
id = "generated-room-c"
unit = "generated-unit-a"
label = "Room C"
persons = ["generated-member"]

[[conversations]]
platform = "signal"
id = "generated-room-b"
unit = "generated-unit-b"
label = "Room B"
persons = ["generated-member"]
"""

CREDENTIAL = ProvisioningCredential(
    Actor(ActorKind.SERVICE, service_id="generated-bridge",
          sources={"signal", "discord", "telegram"}),
    BRIDGE_TOKEN,
    frozenset({ROOM_A, ROOM_B, ROOM_C}),
)


#: Each platform's sign-in method — an assertion naming another platform's
#: method is refused before it can bind the account.
METHOD = {"signal": "signal_code", "discord": "discord_oauth",
          "telegram": "telegram_oidc"}


def claims(*, account: Identifier, purpose: str, method="discord_oauth",
           path=EXCHANGE, body=b"", http="POST", authenticated_at=None,
           binding=None):
    now = int(time.time())
    value = {"iss": GATEWAY.name, "aud": ASSERTION_AUDIENCE, "sub": str(uuid4()),
             "jti": str(uuid4()), "iat": now, "exp": now + 60,
             "account": {"platform": account.platform, "kind": account.kind,
                         "value": account.value},
             "authentication_method": method,
             "proof_issuer": GATEWAY.methods[method],
             "authenticated_at": authenticated_at if authenticated_at is not None else now,
             "purpose": purpose, "method": http, "path": path,
             "body_sha256": hashlib.sha256(body).hexdigest()}
    if binding is not None:
        value["binding"] = binding
    return value


def auth(value) -> dict[str, str]:
    token = jwt.encode(value, KEY, algorithm="HS256",
                       headers={"kid": GATEWAY.name, "typ": ASSERTION_TYPE})
    return {"Authorization": "Nora-Authentication " + token}


def ident_json(account: Identifier) -> dict[str, str]:
    return {"platform": account.platform, "kind": account.kind, "value": account.value}


def roster(operation_id, scope, members, *, platform, complete=True,
           audience="room", collection=None):
    return {"operation_id": operation_id,
            "collection": collection or f"generated-{platform}-collection",
            "platform": platform, "scope": {"kind": scope.kind.value, "id": scope.id},
            "audience": audience, "complete": complete,
            "observed_ms": int(time.time() * 1000),
            "members": [{"account": ident_json(account), "classification": kind}
                        for account, kind in members]}


def envelope(*, sender_number, request_id, nonce, action="approve",
             timestamp_ms=None, sender_uuid=FOREIGN_ACI, **changes):
    env = {"sourceNumber": sender_number, "sourceUuid": sender_uuid,
           "timestamp": timestamp_ms if timestamp_ms is not None
           else int(time.time() * 1000),
           "dataMessage": {"message": f"!!!link {action} {request_id} {nonce}"}}
    env.update(changes)
    return {"envelope": env, "account": SERVICE_NUMBER}


class _Sender:
    """Records what `SignalLinkDelivery` would push privately; no daemon."""

    def __init__(self) -> None:
        self.sent: list[tuple[object, str]] = []

    async def send(self, target, text):
        self.sent.append((target, text))

        class _Result:
            ok = True
        return _Result()


class _World:
    def __init__(self, engine, services, holder, client, consent, sender,
                 adapter, documents):
        self.engine = engine
        self.services = services
        self.holder = holder
        self.client = client
        self.consent = consent
        self.sender = sender
        self.adapter = adapter
        self.documents = documents
        self.sessions: dict[str, dict] = {}
        self.evidence: list[dict] = []

    def note(self, step: str, **fields) -> None:
        self.evidence.append({"step": step, **fields})

    async def person_id(self, account: Identifier) -> str:
        record = await self.services.bindings.record_for(account=account)
        assert record is not None, f"{account} is not bound"
        return record.person_id

    async def exchange(self, account: Identifier, *, method="discord_oauth",
                       authenticated_at=None) -> dict:
        body = b"{}"
        response = await self.client.post(
            EXCHANGE, data=body,
            headers=auth(claims(account=account, purpose="sign_in", method=method,
                                path=EXCHANGE, body=body,
                                authenticated_at=authenticated_at)))
        assert response.status == 200, await response.text()
        binding = (await response.json())["binding"]
        self.sessions[account.value] = binding
        return binding

    async def check(self, account: Identifier) -> int:
        return (await self.client.post(
            CHECK, data=b"",
            headers=auth(claims(account=account, purpose="session",
                                method=METHOD[account.platform], path=CHECK,
                                body=b"",
                                binding=self.sessions[account.value])))).status

    def session_headers(self, account: Identifier, *, path, body=b"", http="POST"):
        return auth(claims(account=account, purpose="session",
                           method=METHOD[account.platform], path=path,
                           body=body, http=http,
                           binding=self.sessions[account.value]))

    def sign_in_headers(self, account: Identifier, *, method, path, body=b"",
                        http="POST"):
        # A `_code` sign-in is only well-formed with its receipt binding.
        binding = (self.sessions.get(account.value)
                   if method.endswith("_code") else None)
        return auth(claims(account=account, purpose="sign_in", method=method,
                           path=path, body=body, http=http, binding=binding))

    async def otp_sign_in(self, phone: Identifier) -> dict:
        """A real code challenge through the OTP routes over the SQL store —
        the receipt's authentication and binding feed the session exchange."""
        request = await self.client.post(
            OTP_REQUEST, headers=OTP_AUTH,
            json={"identifier": phone.value, "platform": "signal",
                  "purpose": "sign_in"})
        assert request.status == 202
        challenge_id = (await request.json())["challengeId"]
        code = re.search(r"\b(\d{6})\b", self.adapter.sent[-1][1]).group(1)
        verified = await self.client.post(
            OTP_VERIFY, headers=OTP_AUTH,
            json={"challengeId": challenge_id, "code": code, "purpose": "sign_in"})
        assert verified.status == 200, await verified.text()
        receipt = (await verified.json())["managedIdentity"]
        body = b"{}"
        response = await self.client.post(
            EXCHANGE, data=body,
            headers=auth(claims(
                account=phone, purpose="sign_in", method="signal_code",
                path=EXCHANGE, body=body,
                authenticated_at=receipt["authentication"]["authenticatedAt"],
                binding=receipt["binding"])))
        assert response.status == 200, await response.text()
        binding = (await response.json())["binding"]
        self.sessions[phone.value] = binding
        return binding


@pytest.fixture
async def world(pg_module_url, ory, aiohttp_client, tmp_path):
    engine = create_sql_engine(pg_module_url)
    async with engine.begin() as connection:
        # This module owns its database; the row-history guard is lifted
        # deliberately for a clean generated slate, as its error prescribes.
        await connection.execute(
            sa.text("SET LOCAL nora.row_history = 'off'"))
        rows = await connection.execute(sa.text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            " AND tablename <> 'alembic_version'"))
        for (table,) in rows:
            await connection.execute(sa.text(f'TRUNCATE "{table}" CASCADE'))
    try:
        async with aiohttp.ClientSession() as session:
            policy_path = tmp_path / "policy.toml"
            policy_path.write_text(POLICY_TOML)
            policy = load_policy(policy_path)
            services = wire.managed_services(
                managed=ManagedOryConfig(
                    kratos_admin_url=ory.kratos_admin,
                    kratos_public_url=ory.kratos_public,
                    keto_read_url=ory.keto_read,
                    keto_write_url=ory.keto_write,
                    timeout_seconds=15.0),
                session=session, policy=policy, engine=engine, credentials={})
            sender = _Sender()
            delivery = SignalLinkDelivery("http://generated-daemon", sender=sender)
            # The same HolderLinkService `wire.holder_links` builds, with the
            # injected floored clock the link lifecycle's own tests pin: a
            # whole-second `authenticated_at` must sit inside [created, now].
            now = [datetime.now(UTC).replace(microsecond=0)]
            holder = HolderLinkService(
                services.bindings, services.registry, services.links,
                services.publication, services.registration,
                signal_delivery=delivery, clock=lambda: now[0])
            consent = SignalLinkConsent(
                own_account=SERVICE_NUMBER, decide=holder.decide_from_transport,
                delivery=delivery)
            adapter = FakeAdapter()
            doctrine = build_test_doctrine_store()
            otp = OtpService(
                store=SqlOtpStore(engine), persons=Persons(by_person={}, index={}),
                bindings=services.bindings, registry=services.registry,
                adapters={"signal": adapter}, records=FakeRecordStore(),
                doctrine_store=doctrine, canary="generated-canary",
                metrics=Metrics(), secret=b"generated-otp-key",
                ttl=timedelta(minutes=5), max_attempts=5, max_per_hour=20,
                max_per_day=40, clock=lambda: datetime.now(UTC))
            documents = SqlDocumentStore(engine)
            app = web.Application()
            register_web_routes(
                app, store=FakeRecordStore(),
                reminders=FakeReminderStore(),
                persons=Persons(by_person={}, index={}),
                started_at=0.0, presence=None, presence_circles=(),
                skills_dir=NO_SKILLS_DIR, events=_fake_events(),
                doctrine_store=doctrine, documents=documents,
                rooms_of=lambda pid: rooms_named(policy, pid))
            verifier = AuthenticationVerifier([GATEWAY])
            register_session_routes(
                app, sessions=AccountSessions(services.bindings, services.registry),
                verifier=verifier)
            register_holder_routes(app, holder=holder, verifier=verifier)
            register_otp_routes(app, otp=otp, api_token=OTP_TOKEN)
            register_management_routes(
                app, services=services, credentials=(CREDENTIAL,),
                authentication=verifier)
            register_managed_viewer(
                app, bindings=services.bindings, registry=services.registry,
                scopes=PortalScopes(services.bindings, services.topology,
                                    services.authority),
                verifier=verifier)
            client = await aiohttp_client(app)
            journey = _World(engine, services, holder, client, consent, sender,
                             adapter, documents)
            journey.now = now
            yield journey
    finally:
        await engine.dispose()


async def _register_accounts(world):
    client, services = world.client, world.services

    # --- leg 1: generated accounts register once, gain no implicit power ---
    members_a = [(SIG_A1, "person"), (SIG_A2, "person")]
    first = roster("op-ra-sig", ROOM_A, members_a, platform="signal")
    assert (await client.post(ROSTERS, headers=BEARER, json=first)).status == 200
    for operation_id, platform, scope, members in (
            ("op-ra-dis", "discord", ROOM_A, [(OP, "person")]),
            ("op-rc-dis", "discord", ROOM_C, [(DISC, "bot")]),
            ("op-rc-tg", "telegram", ROOM_C, [(TG, "unknown")]),
            ("op-rb-tg", "telegram", ROOM_B, [(TG, "unknown")])):
        response = await client.post(
            ROSTERS, headers=BEARER,
            json=roster(operation_id, scope, members, platform=platform))
        assert response.status == 200, await response.text()

    assert len(await services.bindings.persons()) == 5
    a1 = await world.person_id(SIG_A1)
    a2 = await world.person_id(SIG_A2)
    disc = await world.person_id(DISC)
    tg = await world.person_id(TG)
    op = await world.person_id(OP)
    assert len({a1, a2, disc, tg, op}) == 5 and a1 != a2

    # Exact retry replays the journaled outcome; a fresh operation over the
    # same members registers nobody new — no duplicate row appears anywhere.
    assert (await client.post(ROSTERS, headers=BEARER, json=first)).status == 200
    assert (await client.post(ROSTERS, headers=BEARER, json=roster(
        "op-ra-sig-b", ROOM_A, members_a, platform="signal"))).status == 200
    duplicates = await asyncio.gather(*(client.post(
        ROSTERS, headers=BEARER, json=first) for _ in range(2)))
    assert [r.status for r in duplicates] == [200, 200]
    assert len(await services.bindings.persons()) == 5

    # What the credential does not admit refuses: no bearer, a collection
    # audience claiming a room, a real scope outside its grant — topology
    # would admit the unit, so only the credential guard can deny this.
    assert (await client.post(ROSTERS, json=roster(
        "op-x0", ROOM_A, members_a, platform="signal"))).status == 401
    assert (await client.post(ROSTERS, headers=BEARER, json=roster(
        "op-x1", ROOM_A, members_a, platform="signal",
        audience="collection"))).status == 403
    assert (await client.post(ROSTERS, headers=BEARER, json=roster(
        "op-x2", UNIT_SCOPE_A, members_a, platform="signal"))).status == 403

    # Membership is exactly what the rosters said: no admin, no sibling reach,
    # and a "bot"/"unknown" classification never changed admission.
    members = {m.person_id for m in await services.bindings.members_of(ROOM_A)}
    assert members == {a1, a2, op}
    for pid in (a1, a2, disc, tg, op):
        assert not (await services.authority.check(
            scope=ROOM_A, person_id=pid, permit="manage_members")).allowed
    assert (await services.authority.check(
        scope=ROOM_A, person_id=a1, permit="view")).allowed
    assert not (await services.authority.check(
        scope=ROOM_B, person_id=a1, permit="view")).allowed
    assert (await services.authority.check(
        scope=ROOM_C, person_id=disc, permit="view")).allowed
    world.note("registration", persons=5, retry="replayed",
               implicit_admin="none")

    return a1, a2, disc, tg, op


async def _refuse_changed_request(world, request_id, survivor):
    client = world.client
    changed = json.dumps({"request_id": request_id, "receiving": ident_json(TG),
                          "surviving_person": survivor}).encode()
    refused = await client.post(
        REQUESTS, data=changed,
        headers=world.session_headers(SIG_A1, path=REQUESTS, body=changed))
    assert refused.status == 403, await refused.text()



async def _link_first_pair(world, a1, a2, disc, tg, op):
    client, services = world.client, world.services
    survivor, loser = a1, disc

    # Private bilateral consent collapses only the named pair.
    # Out-of-band installation bootstrap: the operator's unit delegation and
    # the loser's restriction on ROOM_C — fixture setup, not admission power.
    await services.backend.put_relation(
        scope=UNIT_SCOPE_A, relation="admin", person_id=op)
    await services.backend.put_relation(
        scope=ROOM_C, relation="restricted", person_id=loser)

    # Real Signal code sign-in through the OTP routes — authentication, not
    # consent; the receipt feeds a signal_code assertion into exchange.
    await world.otp_sign_in(SIG_A1)
    await world.exchange(DISC, method="discord_oauth")
    await world.exchange(TG, method="telegram_oidc")
    await world.exchange(OP, method="discord_oauth")
    assert await world.check(SIG_A1) == 200

    # The assertion binds the exact request and the account's own method: a
    # proof minted for another platform's method, another path or another
    # body is refused at the verifier before it reaches sign-in.
    body = b"{}"
    assert (await client.post(
        EXCHANGE, data=body,
        headers=auth(claims(account=DISC, purpose="sign_in",
                            method="telegram_oidc", path=EXCHANGE,
                            body=body)))).status == 401
    assert (await client.post(
        EXCHANGE, data=b'{"forged":true}',
        headers=auth(claims(account=DISC, purpose="sign_in",
                            method="discord_oauth", path=EXCHANGE,
                            body=body)))).status == 401
    assert (await client.post(
        EXCHANGE, data=body,
        headers=auth(claims(account=DISC, purpose="sign_in",
                            method="discord_oauth", path=CHECK,
                            body=body)))).status == 401

    request_id = f"generated-link-{uuid4().hex[:8]}"
    body = json.dumps({"request_id": request_id, "receiving": ident_json(DISC),
                       "surviving_person": survivor}).encode()
    created = await client.post(
        REQUESTS, data=body,
        headers=world.session_headers(SIG_A1, path=REQUESTS, body=body))
    assert created.status == 200, await created.text()
    initiating_nonce = (await created.json())["request"]["challenge"]["nonce"]
    await _refuse_changed_request(world, request_id, survivor)

    view_path = f"{REQUESTS}/{request_id}"
    received = await client.get(
        view_path, headers=world.session_headers(DISC, path=view_path, http="GET"))
    assert received.status == 200
    receiving_nonce = (await received.json())["request"]["challenge"]["nonce"]
    assert receiving_nonce != initiating_nonce
    assert (await client.get(
        view_path, headers=world.session_headers(TG, path=view_path,
                                                 http="GET"))).status == 403

    decisions = f"{REQUESTS}/{request_id}/decisions"

    async def decide(account, *, nonce, method, purpose="sign_in"):
        decision = json.dumps({"operation_id": f"op-{uuid4()}",
                               "action": "approve", "nonce": nonce}).encode()
        headers = (world.session_headers(account, path=decisions, body=decision)
                   if purpose == "session" else world.sign_in_headers(
                       account, method=method, path=decisions, body=decision))
        return await client.post(decisions, data=decision, headers=headers)

    # A retained session is never consent; a code sign-in is authentication
    # only. Per-request clock captures place actual gateway/transport proof
    # instants inside the request window without sleeping or changing guards.
    world.now[0] = datetime.now(UTC) + timedelta(seconds=5)
    assert (await decide(SIG_A1, nonce=initiating_nonce, method="signal_code",
                         purpose="session")).status == 401
    assert (await decide(SIG_A1, nonce=initiating_nonce,
                         method="signal_code")).status == 403
    # A wrong nonce, the other side's forwarded nonce, and a foreign party's
    # fresh proof are all denied — the request stays pending.
    assert (await decide(DISC, nonce="generated-wrong",
                         method="discord_oauth")).status == 403
    assert (await decide(DISC, nonce=initiating_nonce,
                         method="discord_oauth")).status == 403
    assert (await decide(TG, nonce=receiving_nonce,
                         method="telegram_oidc")).status == 403

    # Room text never becomes a decision (not consumed); a non-party sender's
    # private envelope is consumed and denied without touching the request.
    group = envelope(sender_number=SIG_A1.value, request_id=request_id,
                     nonce=initiating_nonce,
                     dataMessage={"message": f"!!!link approve {request_id} "
                                             f"{initiating_nonce}",
                                  "groupInfo": {"groupId": "generated-group"}})
    assert await world.consent.dispatch(group) is False

    # A registered account outside the named pair forwards the receiving
    # nonce: the seam answers that sender's own proven alias with the
    # refusal — not an acceptance, not an unavailable — and the pending
    # request is untouched. The valid private approval below is the control.
    foreign = envelope(sender_number=SIG_A2.value, request_id=request_id,
                       nonce=receiving_nonce, sender_uuid=A2_ACI)
    pending = (await (await client.get(
        view_path, headers=world.session_headers(
            SIG_A1, path=view_path, http="GET"))).json())["request"]["approved"]
    replies = len(world.sender.sent)
    assert await world.consent.dispatch(foreign) is True
    assert [(target.conversation_id, text)
            for target, text in world.sender.sent[replies:]] == [
        (A2_ACI, f"Link request {request_id} refused: not-a-party.")]
    assert (await (await client.get(
        view_path, headers=world.session_headers(
            SIG_A1, path=view_path, http="GET"))).json()
            )["request"]["approved"] == pending == []

    # Signal consents through the private parser and exact sender binding.
    assert await world.consent.dispatch(
        envelope(sender_number=SIG_A1.value, request_id=request_id,
                 nonce=initiating_nonce)) is True
    target, text = world.sender.sent[-1]
    assert (target.conversation_id, text) == (
        FOREIGN_ACI, f"Approval recorded for link request {request_id}.")
    view = (await (await client.get(
        view_path, headers=world.session_headers(SIG_A1, path=view_path,
                                                 http="GET"))).json())
    assert view["request"]["approved"] == ["initiating"]
    # The receiving side consents over a fresh provider re-authentication.
    response = await decide(DISC, nonce=receiving_nonce, method="discord_oauth")
    assert response.status == 200, await response.text()
    assert (await response.json())["state"] == "committed"

    # Bindings, canonical redirect, live Keto and retired Kratos agree.
    assert (await services.bindings.record_for(account=DISC)).person_id == survivor
    assert await services.bindings.canonical_person(loser) == survivor
    retired = await services.registry.identity_state(external_id=loser)
    assert retired.verdict is Verdict.DENIED
    relations = await services.backend.scope_relations_on(scope=ROOM_C)
    member_of_c = {r.subject for r in relations if r.relation == "member"}
    restricted_c = {r.subject for r in relations if r.relation == "restricted"}
    assert survivor in member_of_c and loser not in member_of_c
    # The restriction moved with the person and still wins over the grant.
    assert survivor in restricted_c and loser not in restricted_c
    assert all(r.subject != loser for r in relations)
    assert not (await services.authority.check(
        scope=ROOM_C, person_id=survivor, permit="view")).allowed
    # Both retained sessions become stale after the ownership commit.
    assert await world.check(DISC) == 403
    assert await world.check(SIG_A1) == 403
    await world.otp_sign_in(SIG_A1)
    assert await world.check(SIG_A1) == 200
    # A fresh sign-in for the losing account lands on the survivor — the
    # stable redirect every later consumer sees.
    redirected = await world.exchange(DISC, method="discord_oauth")
    assert redirected["person_id"] == survivor
    # The link touched only its pair: the same-platform sibling is untouched.
    assert (await services.bindings.record_for(account=SIG_A2)).person_id == a2
    world.note("link", request_id=request_id, survivor=survivor,
               loser=loser, readback="bindings+keto+kratos agree")



async def _manage_portal(world, a1, a2, disc, tg, op):
    client, services = world.client, world.services
    survivor = a1

    # Scoped reads, scoped management and current revocation.
    sha = "d" * 64

    def revision(title):
        return RevisionIn(title=title, spec={"version": 1, "root": {
            "type": "Page", "props": {"title": title}, "children": []}},
            text=title, sha256=sha, media=(), written_by="generated")

    for room, title in (("generated-room-a", "A note"), ("generated-room-c", "C note"),
                        ("generated-room-b", "B note")):
        await world.documents.create(document_id=uuid4(), platform="signal",
            conversation_id=room, created_by="generated", revision=revision(title))

    async def documents(account, person_id):
        path = f"{DOCUMENTS}?personId={person_id}"
        return await client.get(
            path, headers=world.session_headers(account, path=path, http="GET"))

    # The survivor's portal shows its unit's member rooms — ROOM_C is its own
    # moved restriction, so deny wins there too.
    survivor_docs = await documents(SIG_A1, survivor)
    assert survivor_docs.status == 200
    assert {d["title"] for d in (await survivor_docs.json())["documents"]} == {"A note"}
    tg_docs = await documents(TG, tg)
    assert {d["title"] for d in (await tg_docs.json())["documents"]} == {
        "B note", "C note"}
    # No cross-viewer selection, no anonymous or service-bearer reach.
    assert (await documents(SIG_A1, tg)).status == 403
    assert (await client.get(f"{DOCUMENTS}?personId={survivor}")).status == 401
    assert (await client.get(
        f"{DOCUMENTS}?personId={survivor}", headers=BEARER)).status == 401

    await world.otp_sign_in(SIG_A2)

    def membership_op(person_id, scope, member):
        return json.dumps({"operation_id": f"op-{uuid4()}",
            "kind": "set_membership",
            "target": {"kind": "membership", "person_id": person_id,
                       "scope": {"kind": "room", "id": scope.id}},
            "payload": {"member": member},
            "observed_revisions": {"authority": "observed"}}).encode()

    # Delegated membership changes preserve roster support; sibling scope
    # and service-bearer operator requests refuse.
    body = membership_op(tg, ROOM_A, True)
    assert (await client.post(
        OPERATIONS, data=body,
        headers=world.session_headers(OP, path=OPERATIONS, body=body))).status == 200
    assert tg in {m.person_id for m in await services.bindings.members_of(ROOM_A)}
    assert (await services.authority.check(
        scope=ROOM_A, person_id=tg, permit="view")).allowed
    body = membership_op(tg, ROOM_A, False)
    assert (await client.post(
        OPERATIONS, data=body,
        headers=world.session_headers(OP, path=OPERATIONS, body=body))).status == 200
    assert tg not in {
        m.person_id for m in await services.bindings.members_of(ROOM_A)}
    assert not (await services.authority.check(
        scope=ROOM_A, person_id=tg, permit="view")).allowed
    denied = membership_op(tg, ROOM_B, False)
    assert (await client.post(
        OPERATIONS, data=denied,
        headers=world.session_headers(OP, path=OPERATIONS, body=denied))).status == 403
    # A service bearer cannot mint a scoped human operation: the closed
    # vocabulary refuses a service-actor `set_membership` at decode.
    assert (await client.post(OPERATIONS, data=denied, headers=BEARER)).status == 400

    # Current revocation: drop the delegation and the SAME kind of operation
    # refuses, even though the operator's session remains valid.
    await services.backend.drop_relation(
        scope=UNIT_SCOPE_A, relation="admin", person_id=op)
    body = membership_op(a1, ROOM_A, False)
    assert (await client.post(
        OPERATIONS, data=body,
        headers=world.session_headers(OP, path=OPERATIONS, body=body))).status == 403
    assert await world.check(OP) == 200

    world.note("management-portal", operator_scope=UNIT_A,
               revocation="delegation-drop+unlink", unlink="session-dead")



async def _link_additional_account(world, account, former, survivor):
    client, services = world.client, world.services
    world.now[0] = datetime.now(UTC).replace(microsecond=0)
    await world.otp_sign_in(SIG_A1)
    if account.platform == "signal":
        await world.otp_sign_in(account)
    else:
        await world.exchange(account, method=METHOD[account.platform])
    request_id = f"generated-link-{uuid4().hex[:8]}"
    body = json.dumps({"request_id": request_id, "receiving": ident_json(account),
                       "surviving_person": survivor}).encode()
    response = await client.post(
        REQUESTS, data=body,
        headers=world.session_headers(SIG_A1, path=REQUESTS, body=body))
    assert response.status == 200, await response.text()
    initiating_nonce = (await response.json())["request"]["challenge"]["nonce"]
    path = f"{REQUESTS}/{request_id}"
    response = await client.get(
        path, headers=world.session_headers(account, path=path, http="GET"))
    assert response.status == 200, await response.text()
    receiving_nonce = (await response.json())["request"]["challenge"]["nonce"]
    assert receiving_nonce != initiating_nonce
    world.now[0] = datetime.now(UTC) + timedelta(seconds=5)
    assert await world.consent.dispatch(envelope(
        sender_number=SIG_A1.value, request_id=request_id,
        nonce=initiating_nonce)) is True
    assert await world.person_id(account) == former
    if account.platform == "signal":
        assert await world.consent.dispatch(envelope(
            sender_number=account.value, request_id=request_id,
            nonce=receiving_nonce)) is True
    else:
        decisions = f"{path}/decisions"
        body = json.dumps({"operation_id": f"op-{uuid4()}",
                           "action": "approve", "nonce": receiving_nonce}).encode()
        response = await client.post(decisions, data=body, headers=world.sign_in_headers(
            account, method=METHOD[account.platform], path=decisions, body=body))
        assert response.status == 200, await response.text()
        assert (await response.json())["state"] == "committed"
    assert await world.person_id(account) == survivor
    assert await services.bindings.canonical_person(former) == survivor
    assert (await services.registry.identity_state(external_id=former)).verdict is Verdict.DENIED
    assert await world.check(account) == 403
    assert await world.check(SIG_A1) == 403


async def _four_account_readback(world, survivor, former_ids):
    services = world.services
    accounts = [await services.bindings.record_for(account=a)
                for a in (SIG_A1, SIG_A2, DISC, TG)]
    assert {a.person_id for a in accounts} == {survivor}
    assert len({a.account_id for a in accounts}) == 4
    for old in former_ids:
        assert await services.bindings.canonical_person(old) == survivor
    assert await world.person_id(OP) != survivor
    for account in (SIG_A1, SIG_A2):
        assert (await world.otp_sign_in(account))["person_id"] == survivor
    for account in (DISC, TG):
        binding = await world.exchange(account, method=METHOD[account.platform])
        assert binding["person_id"] == survivor
    for account in (SIG_A1, SIG_A2, DISC, TG):
        assert await world.check(account) == 200
        path = f"{DOCUMENTS}?personId={survivor}"
        response = await world.client.get(
            path, headers=world.session_headers(account, path=path, http="GET"))
        assert response.status == 200, await response.text()
        assert {d["title"] for d in (await response.json())["documents"]} == {"A note", "B note"}
    assert not (await services.authority.check(
        scope=ROOM_C, person_id=survivor, permit="view")).allowed
    assert not (await services.authority.check(
        scope=ROOM_A, person_id=survivor, permit="manage_members")).allowed
    world.note("canonical-accounts", accounts=4, platforms=3,
               distinct_accounts=True, independent_bilateral_links=3)


async def _unlink_one_account(world, survivor):
    client, services = world.client, world.services
    body = json.dumps({"operation_id": f"op-{uuid4()}",
                       "expected_person": await world.person_id(OP)}).encode()
    response = await client.post(
        UNLINK, data=body,
        headers=world.session_headers(SIG_A2, path=UNLINK, body=body))
    assert response.status == 403
    body = json.dumps({"operation_id": f"op-{uuid4()}",
                       "expected_person": survivor}).encode()
    response = await client.post(
        UNLINK, data=body,
        headers=world.session_headers(SIG_A2, path=UNLINK, body=body))
    assert response.status == 200, await response.text()
    assert await services.bindings.record_for(account=SIG_A2) is None
    assert await world.check(SIG_A2) == 403
    path = f"{DOCUMENTS}?personId={survivor}"
    assert (await client.get(path, headers=world.session_headers(
        SIG_A2, path=path, http="GET"))).status == 403
    for account in (SIG_A1, DISC, TG):
        assert await world.person_id(account) == survivor
    await world.otp_sign_in(SIG_A1)
    assert await world.check(SIG_A1) == 200
    assert (await client.get(path, headers=world.session_headers(
        SIG_A1, path=path, http="GET"))).status == 200
    world.note("unlink", removed_accounts=1, surviving_accounts=3)


async def test_managed_identity_journey(world):
    ids = await _register_accounts(world)
    await _link_first_pair(world, *ids)
    await _manage_portal(world, *ids)
    a1, a2, disc, tg, _ = ids
    await _link_additional_account(world, TG, tg, a1)
    await _link_additional_account(world, SIG_A2, a2, a1)
    await _four_account_readback(world, a1, (a2, disc, tg))
    await _unlink_one_account(world, a1)
    _write_receipt(world.evidence)
