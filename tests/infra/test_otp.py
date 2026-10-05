"""One-time codes (`nora.app.otp`, `nora.infra.sql.otp`) against real Postgres.

Real Postgres for the reason `test_announcement_log.py` gives: spending a code
once is decided by a unique partial index and a row lock, and a double would be
re-implementing the statements under test.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nora.app.otp import OtpService
from nora.doctrine.loader import DoctrineStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.otp import SqlOtpStore
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing.fakes import FakeAdapter, FakeRecordStore
from tests.test_doctrine_loader import write

T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
ACI = "8185b8fa-359a-4c0a-c6ed-24814dc655bb"
IDENTITY = f"""
[persons.miles]
name = "Miles"
[[persons.miles.signal]]
phone = "+85212345678"
uuid = "{ACI}"

[persons.ada]
name = "Ada"
[[persons.ada.discord]]
user_id = "123456789012345678"
"""


@pytest.fixture
async def store(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield SqlOtpStore(engine)
    finally:
        await engine.dispose()


class _Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def parts(tmp_path, store):
    identity = tmp_path / "identity.toml"
    identity.write_text(IDENTITY)
    doctrine = tmp_path / "doctrine"
    doctrine.mkdir()
    write(doctrine, "constraints.md", name="guardrails", order=40, body="# Guardrails\n\nBe kind.")
    adapter, records, metrics, clock = FakeAdapter(), FakeRecordStore(), Metrics(), _Clock()
    service = OtpService(
        store=store, persons=load_persons(identity), adapters={"signal": adapter},
        records=records,
        doctrine_store=DoctrineStore(doctrine, canary="CANARY-1", display_name="Nora",
                                     metrics=metrics),
        canary="CANARY-1", metrics=metrics, secret=b"k" * 32,
        ttl=timedelta(minutes=5), max_attempts=3, max_per_hour=2, clock=clock,
    )
    return service, adapter, records, metrics, clock


def _code(adapter: FakeAdapter) -> str:
    return re.search(r"\b(\d{6})\b", adapter.sent[-1][1]).group(1)


async def test_a_code_goes_to_the_persons_own_account_and_verifies_once(parts):
    service, adapter, records, metrics, _ = parts
    challenge = await service.request(identifier="+852 1234 5678", platform="signal",
                                      purpose="sign_in")
    target, _ = adapter.sent[-1]
    assert (target.conversation_id, target.conversation_type) == (ACI, "dm")
    code = _code(adapter)
    # The audit row says a code went out, never which code.
    assert code not in records.rows[-1]["body"]
    assert metrics.get("nora_otp_requests_total", platform="signal", outcome="sent") == 1
    assert await service.verify(challenge_id=challenge, code=code, purpose="sign_in") == "miles"
    assert await service.verify(challenge_id=challenge, code=code, purpose="sign_in") is None


async def test_somebody_not_in_identity_toml_is_sent_nothing_and_answered_the_same(parts):
    service, adapter, _, metrics, _ = parts
    challenge = await service.request(identifier="+85299999999", platform="signal",
                                      purpose="sign_in")
    uuid.UUID(challenge)                    # an id all the same
    no_account = await service.request(identifier="ada", platform="signal", purpose="sign_in")
    uuid.UUID(no_account)
    assert adapter.sent == []
    assert metrics.get("nora_otp_requests_total", platform="signal", outcome="unknown") == 1
    assert metrics.get("nora_otp_requests_total", platform="signal", outcome="no_account") == 1
    assert await service.verify(challenge_id=challenge, code="000000", purpose="sign_in") is None


async def test_a_code_dies_after_the_attempt_limit_even_when_then_right(parts):
    service, adapter, _, _, _ = parts
    challenge = await service.request(identifier="miles", platform="signal", purpose="sign_in")
    code = _code(adapter)
    wrong = f"{(int(code) + 1) % 10**6:06d}"
    for _ in range(3):
        assert await service.verify(challenge_id=challenge, code=wrong, purpose="sign_in") is None
    assert await service.verify(challenge_id=challenge, code=code, purpose="sign_in") is None


async def test_a_code_expires_and_a_newer_one_supersedes_it(parts):
    service, adapter, _, _, clock = parts
    first = await service.request(identifier="miles", platform="signal", purpose="sign_in")
    first_code = _code(adapter)
    second = await service.request(identifier="miles", platform="signal", purpose="sign_in")
    second_code = _code(adapter)
    assert await service.verify(challenge_id=first, code=first_code, purpose="sign_in") is None
    clock.now = T0 + timedelta(minutes=6)
    assert await service.verify(challenge_id=second, code=second_code, purpose="sign_in") is None


async def test_a_person_is_sent_at_most_max_per_hour_codes(parts):
    service, adapter, _, metrics, clock = parts
    for _ in range(3):
        await service.request(identifier="miles", platform="signal", purpose="sign_in")
    assert len(adapter.sent) == 2
    assert metrics.get(
        "nora_otp_requests_total", platform="signal", outcome="rate_limited",
    ) == 1
    clock.now = T0 + timedelta(hours=1, seconds=1)
    await service.request(identifier="miles", platform="signal", purpose="sign_in")
    assert len(adapter.sent) == 3


async def test_two_right_answers_at_once_sign_in_only_once(store):
    """The race the row lock and `uq_otp_attempt_ok` exist for."""
    challenge = uuid.uuid4()
    await store.issue(
        challenge_id=challenge, person_id="miles", purpose="sign_in", platform="signal",
        code_hash="a" * 64, issued_at=T0, expires_at=T0 + timedelta(minutes=5),
    )
    results = await asyncio.gather(*(
        store.attempt(challenge_id=challenge, purpose="sign_in", code_hash="a" * 64, now=T0,
                      max_attempts=5)
        for _ in range(5)
    ))
    assert results.count("miles") == 1


async def test_a_code_is_good_only_for_the_purpose_it_was_asked_for(parts):
    service, adapter, _, _, _ = parts
    challenge = await service.request(identifier="miles", platform="signal", purpose="adopt")
    code = _code(adapter)
    assert await service.verify(challenge_id=challenge, code=code, purpose="sign_in") is None
    assert await service.verify(challenge_id=challenge, code=code, purpose="adopt") == "miles"


async def test_a_code_with_anything_but_six_ascii_digits_is_refused_unread(parts):
    service, adapter, _, _, _ = parts
    challenge = await service.request(identifier="miles", platform="signal", purpose="sign_in")
    code = _code(adapter)
    for bad in (code + "\n", "１２３４５６", code[:5]):
        assert await service.verify(challenge_id=challenge, code=bad, purpose="sign_in") is None
    # None of those used up an attempt: the right code still works.
    assert await service.verify(challenge_id=challenge, code=code, purpose="sign_in") == "miles"
