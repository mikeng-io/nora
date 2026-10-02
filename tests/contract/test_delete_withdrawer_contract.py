"""One suite, both implementations of `DeleteWithdrawer`: the SQL withdrawer that
hides the row and runs the cascade, and the in-memory one the unit tests inject.
The outcome rules are the contract; what a hide does to a transcript read is
`tests/infra/test_delete_withdraws.py`'s, against Postgres only.
"""

from __future__ import annotations

import pytest

from nora.app.delete_withdrawal import SqlDeleteWithdrawer
from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing import FakeDeleteWithdrawer, FakeRecordStore


def _msg(ts: int) -> NormalizedMessage:
    return NormalizedMessage("signal", "g1", "group", "+852111", "Miles", ts, "hello", {})


@pytest.fixture(params=["sql", "fake"])
async def world(request):
    """(a withdrawer, a function that stores a message and returns its row id)."""
    if request.param == "sql":
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        engine = create_sql_engine(pg_url)
        records = SqlRecordStore(engine)
        yield SqlDeleteWithdrawer(engine), lambda ts: records.append(message=_msg(ts))
        await records.aclose()
    else:
        fake_records = FakeRecordStore()
        yield (
            FakeDeleteWithdrawer(fake_records),
            lambda ts: fake_records.append(message=_msg(ts)),
        )


async def test_the_first_withdrawal_hides_and_the_second_finds_it_already_hidden(world):
    withdrawer, store = world
    mid = await store(1)
    first = await withdrawer.withdraw(message_id=mid, now_ms=100)
    second = await withdrawer.withdraw(message_id=mid, now_ms=200)
    assert (first.outcome, second.outcome) == ("hidden", "already_hidden")
    assert first.marked == 0 and second.marked == 0, "nothing cites it"


async def test_a_message_that_was_never_stored_is_not_stored(world):
    withdrawer, _store = world
    assert (await withdrawer.withdraw(message_id=987_654, now_ms=100)).outcome == "not_stored"


async def test_withdrawing_one_message_leaves_another_withdrawable(world):
    withdrawer, store = world
    a, b = await store(1), await store(2)
    assert (await withdrawer.withdraw(message_id=a, now_ms=100)).outcome == "hidden"
    assert (await withdrawer.withdraw(message_id=b, now_ms=100)).outcome == "hidden"
