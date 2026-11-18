"""Real OTP rows bind delivered account proofs to their original managed revision."""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from nora.app.otp import OtpService
from nora.doctrine.loader import DoctrineStore
from nora.domain.identity import Identifier
from nora.domain.managed import AccountState, ManagedAccount, ManagedPerson, PersonLifecycle
from nora.domain.otp import OtpUnavailableError
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.otp import SqlOtpStore
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing.fakes import FakeAdapter, FakeRecordStore
from nora.testing.managed import FakeBindingStore, FakeIdentityRegistry
from tests.test_doctrine_loader import write

PHONE = Identifier("signal", "phone", "+85290000101")
ACI = Identifier("signal", "uuid", "a1000001-0000-4000-8000-000000000001")


@pytest.fixture
async def world(pg_module_url, tmp_path):
    engine = create_sql_engine(pg_module_url)
    async with engine.begin() as connection:
        await connection.execute(sa.text("TRUNCATE otp_attempt, otp_challenge"))
    bindings, registry = FakeBindingStore(), FakeIdentityRegistry()
    await bindings.register(person=ManagedPerson("generated-person", PersonLifecycle.ACTIVE),
        account=ManagedAccount("generated-account", "generated-person", "signal",
                               frozenset({PHONE, ACI}), AccountState.BOUND))
    await registry.ensure_identity(external_id="generated-person", traits={})
    identity = tmp_path / "identity.toml"
    identity.write_text('''
[persons.file_only]
name = "Generated File Person"
[[persons.file_only.signal]]
phone = "+85290000102"
''')
    doctrine = tmp_path / "doctrine"
    doctrine.mkdir()
    write(doctrine, "constraints.md", name="guardrails", order=40, body="# Guardrails\nBe kind.")
    metrics, adapter, records = Metrics(), FakeAdapter(), FakeRecordStore()

    def service(store):
        return OtpService(store=store, persons=load_persons(identity),
            bindings=bindings, registry=registry, adapters={"signal": adapter}, records=records,
            doctrine_store=DoctrineStore(doctrine, canary="CANARY-1", display_name="Nora",
                                         metrics=metrics), canary="CANARY-1", metrics=metrics,
            secret=b"generated-otp-storage-key", ttl=timedelta(minutes=5), max_attempts=3,
            max_per_hour=10, max_per_day=20, clock=lambda: datetime.now(UTC))

    try:
        yield service(SqlOtpStore(engine)), service, engine, adapter, bindings, registry
    finally:
        await engine.dispose()


async def issue(world):
    service, _, _, adapter, _, _ = world
    challenge = await service.request(identifier=PHONE.value, platform="signal", purpose="sign_in")
    assert adapter.sent[-1][0].conversation_id == ACI.value
    code = re.search(r"\b(\d{6})\b", adapter.sent[-1][1]).group(1)
    return challenge, code


async def test_managed_account_proof_survives_a_new_store_and_is_spent_once(world):
    _, factory, engine, _, _, _ = world
    challenge, code = await issue(world)
    other = create_sql_engine(engine.url.render_as_string(hide_password=False))
    try:
        resumed = factory(SqlOtpStore(other))
        proof = await resumed.verify_account(challenge_id=challenge, code=code, purpose="sign_in")
        assert proof.account == PHONE
        assert proof.binding.person_id == "generated-person"
        assert proof.binding.account_id == "generated-account"
        assert proof.binding.person_revision == proof.binding.account_revision == "0"
        assert await resumed.verify_account(challenge_id=challenge, code=code,
                                             purpose="sign_in") is None
    finally:
        await other.dispose()


@pytest.mark.parametrize("failure", ["revision", "owner", "revoked", "suspended", "inactive"])
async def test_delivered_proof_does_not_follow_a_changed_binding_or_lifecycle(world, failure):
    service, _, _, _, bindings, registry = world
    challenge, code = await issue(world)
    account = bindings.accounts["generated-account"]
    if failure == "revision":
        bindings.accounts[account.account_id] = replace(account, revision=account.revision + 1)
    elif failure == "owner":
        bindings.accounts[account.account_id] = replace(account, person_id="another-person")
    elif failure == "revoked":
        await bindings.revoke_binding(account=PHONE)
    elif failure == "suspended":
        await bindings.set_lifecycle(person_id="generated-person",
                                      lifecycle=PersonLifecycle.SUSPENDED)
    else:
        await registry.set_state(external_id="generated-person", active=False)
    result = await service.verify_account(challenge_id=challenge, code=code, purpose="sign_in")
    assert result is None


@pytest.mark.parametrize("failure", ["registry", "hold"])
async def test_unreadable_identity_is_unavailable_instead_of_a_valid_code_proof(world, failure):
    service, _, _, _, bindings, registry = world
    challenge, code = await issue(world)
    if failure == "registry":
        registry.down = True
    else:
        await bindings.hold_identity(person_id="generated-person", operation_id="generated-link")
    with pytest.raises(OtpUnavailableError):
        await service.verify_account(challenge_id=challenge, code=code, purpose="sign_in")


async def test_managed_otp_never_falls_back_to_a_file_account_or_mints_an_adoption_login(world):
    service, _, _, adapter, _, _ = world
    await service.request(identifier="+85290000102", platform="signal", purpose="sign_in")
    assert adapter.sent == []
    challenge = await service.request(identifier=PHONE.value, platform="signal", purpose="adopt")
    code = re.search(r"\b(\d{6})\b", adapter.sent[-1][1]).group(1)
    assert await service.verify_account(challenge_id=challenge, code=code, purpose="adopt") is None
    refused = await service.verify_account(challenge_id=challenge, code=code, purpose="sign_in")
    assert refused is None
    challenge, code = await issue(world)
    result = await service.verify_account(challenge_id=challenge, code=code, purpose="sign_in")
    assert result is not None


async def test_a_legacy_person_only_challenge_cannot_become_managed_login(world):
    import hashlib
    import hmac
    import uuid

    service, _, engine, _, _, _ = world
    challenge = uuid.uuid4()
    now = datetime.now(UTC)
    hashed = hmac.new(b"generated-otp-storage-key", f"{challenge}:123456".encode(),
                      hashlib.sha256).hexdigest()
    await SqlOtpStore(engine).issue(challenge_id=challenge, person_id="generated-person",
        purpose="sign_in", platform="signal", code_hash=hashed,
        issued_at=now, expires_at=now + timedelta(minutes=5))
    proof = await service.verify_account(challenge_id=str(challenge), code="123456",
                                          purpose="sign_in")
    assert proof is None
    challenge, code = await issue(world)
    assert await service.verify_account(challenge_id=challenge, code=code,
                                         purpose="sign_in") is not None


async def test_whatsapp_proof_keeps_provider_ownership_with_the_same_number(world, tmp_path):
    import hashlib
    import hmac
    import uuid

    from nora.app.otp_identity import OtpIdentities
    from nora.persons import direct_account_target

    service, _, engine, _, bindings, registry = world
    phone = Identifier("whatsapp", "phone", PHONE.value)
    person = "generated-whatsapp-person"
    await bindings.register(person=ManagedPerson(person, PersonLifecycle.ACTIVE),
        account=ManagedAccount("generated-whatsapp-account", person, "whatsapp",
                               frozenset({phone}), AccountState.BOUND))
    await registry.ensure_identity(external_id="generated-whatsapp-person", traits={})
    identities = OtpIdentities(load_persons(tmp_path / "identity.toml"), bindings, registry)
    recipient = await identities.resolve("whatsapp", PHONE.value)
    assert recipient.account.person_id == "generated-whatsapp-person"
    assert direct_account_target(recipient.account).conversation_id == "85290000101@s.whatsapp.net"
    challenge = uuid.uuid4()
    now = datetime.now(UTC)
    hashed = hmac.new(b"generated-otp-storage-key", f"{challenge}:123456".encode(),
                      hashlib.sha256).hexdigest()
    await SqlOtpStore(engine).issue(challenge_id=challenge, person_id="generated-whatsapp-person",
        purpose="sign_in", platform="whatsapp", code_hash=hashed, account=recipient.proof,
        issued_at=now, expires_at=now + timedelta(minutes=5))
    verified = await service.verify_account(challenge_id=str(challenge), code="123456",
                                             purpose="sign_in")
    assert verified.account == phone
    assert verified.binding.person_id == "generated-whatsapp-person"
