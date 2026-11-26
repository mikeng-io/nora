"""`SqlProfileStore.profile_write`'s receipt rides the write's transaction."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from nora.domain.profile import Account, AddOp, ProfileCitable, Said
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.profile import SqlProfileStore
from nora.infra.sql.schema import COMPREHEND_RESULT, PROFILE, PROFILE_LINE

UNIT = "u1"
PERSON = "ada"
ROOM = "room-a"
ACCOUNT = Account(
    key=f"{UNIT}#character:{PERSON}", entity_kind="human", unit=UNIT, subject=PERSON,
)
DAY1 = 1_735_600_800_000
DAY1B = 1_735_615_200_000
DAY2 = 1_735_790_400_000
NOW = 1_736_049_600_000
WORK = "generated-profile-write"
HASH = "generated-input"


@pytest.fixture
async def store(pg_module_url):
    engine = create_sql_engine(pg_module_url)
    async with engine.begin() as conn:
        await conn.execute(sa.delete(PROFILE_LINE))
        await conn.execute(sa.delete(PROFILE))
        await conn.execute(sa.delete(COMPREHEND_RESULT))
    try:
        yield SqlProfileStore(engine, screen=lambda _t: None), engine
    finally:
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
            messages={41: Said(DAY1, PERSON, ROOM),
                      42: Said(DAY1B, PERSON, ROOM),
                      43: Said(DAY2, PERSON, ROOM)},
            listed_rooms=frozenset({ROOM})),
        "now_ms": NOW, "written_by_session": "cognition:profile:9",
        "apply_decay": True,
    }


async def _generated_receipt(conn, payload):
    await conn.execute(sa.insert(COMPREHEND_RESULT).values(
        work_id=WORK, input_hash=HASH, session_id=9, part="act_result", payload=payload,
    ))


async def _receipt_count(engine) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(
            sa.select(sa.func.count()).select_from(COMPREHEND_RESULT).where(
                COMPREHEND_RESULT.c.work_id == WORK,
            )
        )).scalar_one())


async def test_the_receipt_commits_with_the_write(store) -> None:
    profile, engine = store
    result = await profile.profile_write(
        **_write_args(), expected_revision=0, receipt=_generated_receipt,
    )
    assert result.status == "ok"
    assert await _receipt_count(engine) == 1
    assert (await profile.account_state(key=ACCOUNT.key))[0] == 1
    assert len(await profile.account_heads(key=ACCOUNT.key)) == 1


async def test_a_failed_receipt_rolls_the_write_back(store) -> None:
    profile, engine = store

    async def bad_receipt(conn, payload):
        await _generated_receipt(conn, payload)
        raise RuntimeError("receipt write failed")

    with pytest.raises(RuntimeError, match="receipt write failed"):
        await profile.profile_write(**_write_args(), receipt=bad_receipt)
    assert (await profile.account_state(key=ACCOUNT.key))[0] == 0
    assert await profile.account_heads(key=ACCOUNT.key) == ()
    assert await _receipt_count(engine) == 0
