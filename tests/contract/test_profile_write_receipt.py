"""`SqlProfileStore.profile_write`'s `receipt=` rides the write's own
transaction: the result row and the lines commit or roll back together, so a
crash between them neither loses the receipt nor leaves it behind on a
refused plan (the Register act-result pattern)."""
from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.profile import Account, AddOp, ProfileCitable, Said
from nora.infra.sql.comprehend import SqlComprehendRecords
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.schema import COMPREHEND_RESULT, PROFILE, PROFILE_LINE
from tests.conftest import _async_url

UNIT = "u1"
ACCOUNT = Account(key=f"{UNIT}#character:ada", entity_kind="human",
                  unit=UNIT, subject="ada")
DAY1 = 1_735_600_800_000   # 10:00 HKT 2025-01-01
DAY1B = 1_735_615_200_000  # 14:00 HKT 2025-01-01
DAY2 = 1_735_790_400_000   # 12:00 HKT 2025-01-02
NOW = 1_736_049_600_000    # 2025-01-05


@pytest.fixture(scope="module")
def database(pg_admin, pg_template):
    name = "profile_receipt_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (pg_template,),
        )
        conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{pg_template}"')
    yield _async_url(pg_admin, name)
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        conn.execute(f'DROP DATABASE "{name}"')


@pytest.fixture
async def store(database):
    engine = create_sql_engine(database)
    async with engine.begin() as conn:
        await conn.execute(sa.delete(PROFILE_LINE))
        await conn.execute(sa.delete(PROFILE))
        await conn.execute(sa.delete(COMPREHEND_RESULT))
    yield (
        SqlProfileStore(engine, screen=lambda _t: None),
        SqlComprehendRecords(
            engine, media_join=None, rooms_by_unit={}, persons_by_unit={}),
        engine,
    )
    await engine.dispose()


def _write_args() -> dict:
    return {
        "account": ACCOUNT,
        "ops": (AddOp(
            part="traits", kind="habit",
            content={"statement": "walks daily", "context": "weeknights",
                     "action": "walks", "rhythm": "daily", "setting": "unknown"},
            text="walks daily", mode="induced",
            citations=("41", "42", "43")),),
        "citable": ProfileCitable(
            messages={41: Said(DAY1, "ada", "room-a"),
                      42: Said(DAY1B, "ada", "room-a"),
                      43: Said(DAY2, "ada", "room-a")},
            listed_rooms=frozenset({"room-a"})),
        "now_ms": NOW, "written_by_session": "cognition:profile:9",
        "apply_decay": True,
    }


async def test_the_receipt_commits_with_the_write(store) -> None:
    profile, records, _engine = store
    result = await profile.profile_write(
        **_write_args(), expected_revision=0,
        receipt=lambda conn, payload: records.act_result_on(conn, "k", 9, payload))
    assert result.status == "ok"
    assert (await records.act_result("k", 9))["status"] == "ok"
    assert (await profile.account_state(key=ACCOUNT.key))[0] == 1
    assert len(await profile.account_heads(key=ACCOUNT.key)) == 1


async def test_a_failed_receipt_rolls_the_write_back(store) -> None:
    profile, records, _engine = store

    async def bad_receipt(conn, payload):
        raise RuntimeError("receipt write failed")

    with pytest.raises(RuntimeError, match="receipt write failed"):
        await profile.profile_write(**_write_args(), receipt=bad_receipt)
    assert (await profile.account_state(key=ACCOUNT.key))[0] == 0
    assert await profile.account_heads(key=ACCOUNT.key) == ()
    assert await records.act_result("k", 9) is None
