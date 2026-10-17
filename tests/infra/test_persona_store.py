"""Persona through Postgres (`nora.infra.sql.persona.SqlPersonaStore`).

Real Postgres: the write is judged inside its own transaction, a refused
write leaves nothing behind, the revision is the compare-and-set a session
writes against, and the row CHECKs refuse what a bypassing insert would put
there. The rules themselves are `tests/unit/test_persona_rules.py`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from nora.domain.persona import (
    Account,
    AddOp,
    PersonaCitable,
    RetireOp,
    Said,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.persona import SqlPersonaStore
from nora.infra.sql.schema import PERSONA, PERSONA_LINE

UNIT = "u1"
KIT = "p-kit"
NOW = 1_790_000_000_000
DAY = 24 * 3600 * 1000
ACCOUNT = Account(key=f"{UNIT}#character:{KIT}", entity_kind="human", unit=UNIT, subject=KIT)
CITABLE = PersonaCitable(
    messages={n: Said(NOW - (110 - n) * DAY, KIT, "room-a") for n in range(101, 110)},
    listed_rooms=frozenset({"room-a"}),
)


def _no_offence(_text: str) -> None:
    return None


@pytest.fixture
async def env(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield SimpleNamespace(engine=engine, store=SqlPersonaStore(engine, screen=_no_offence))
    finally:
        await engine.dispose()


def _told(text: str, cite: str = "101") -> AddOp:
    return AddOp(part="identity", kind="attribute", text=text, mode="told",
                 citations=(cite,), told_by=KIT)


async def _write(env, ops, **kw):
    return await env.store.persona_write(
        account=ACCOUNT, ops=ops, citable=CITABLE, now_ms=kw.pop("now", NOW),
        written_by_session="test", apply_decay=False, **kw)


async def test_a_committed_write_is_the_accounts_new_head(env) -> None:
    result = await _write(env, [_told("vegetarian")])
    assert result.status == "ok" and result.revision == 1 and len(result.written) == 1
    heads = await env.store.account_heads(key=ACCOUNT.key)
    assert [(h.mode, h.told_by, h.state) for h in heads] == [("told", KIT, "current")]
    async with env.engine.connect() as conn:
        assert (await conn.execute(sa.select(PERSONA.c.revision))).scalar_one() == 1


async def test_a_change_supersedes_and_the_old_row_stays(env) -> None:
    await _write(env, [_told("vegetarian")])
    retired = await _write(env, [RetireOp(line="P1", why="said otherwise")], now=NOW + 1)
    assert retired.status == "ok"
    async with env.engine.connect() as conn:
        lines = (await conn.execute(
            sa.select(PERSONA_LINE.c.id, PERSONA_LINE.c.state, PERSONA_LINE.c.supersedes)
            .order_by(PERSONA_LINE.c.written_at))).all()
    assert [line.state for line in lines] == ["current", "retired"]
    assert lines[1].supersedes == lines[0].id
    assert [h.state for h in await env.store.account_heads(key=ACCOUNT.key)] == ["retired"]


async def test_a_refused_write_leaves_nothing(env) -> None:
    result = await _write(env, [_told("vegetarian"), _told("x", cite="999")])
    assert result.status == "refused"
    assert [r.reason for r in result.refusals] == ["unresolved_citation"]
    async with env.engine.connect() as conn:
        assert (await conn.execute(sa.select(sa.func.count()).select_from(PERSONA_LINE))
                ).scalar_one() == 0
        assert (await conn.execute(sa.select(sa.func.count()).select_from(PERSONA))
                ).scalar_one() == 0


async def test_a_write_against_a_stale_revision_is_a_conflict(env) -> None:
    await _write(env, [_told("vegetarian")])
    result = await _write(env, [_told("cycles to work", "102")], expected_revision=0)
    assert result.status == "conflict" and result.revision == 1
    assert len(await env.store.account_heads(key=ACCOUNT.key)) == 1


async def test_the_cap_is_judged_against_the_stored_account(env) -> None:
    for n in range(25):
        assert (await _write(env, [_told(f"fact {n}")], now=NOW + n)).status == "ok"
    over = await _write(env, [_told("one more")], now=NOW + 99)
    assert [r.reason for r in over.refusals] == ["over_cap"]


async def test_the_row_checks_refuse_a_bypassing_insert(env) -> None:
    """The CHECKs back the judge: a told row with no teller, an abduced row
    with no prediction and an induced identity row cannot be stored at all."""
    base = {"persona_key": ACCOUNT.key, "citations": ["101"], "state": "current",
            "unit": UNIT, "written_at": NOW, "written_by_session": "x", "support_count": 1,
            "text": "t"}
    bad = [
        {"part": "identity", "kind": "attribute", "mode": "told"},
        {"part": "hypothesis", "kind": "explanation", "mode": "abduced", "verdict": "open",
         "draws_on": ["00000000-0000-0000-0000-000000000001"]},
        {"part": "identity", "kind": "attribute", "mode": "induced"},
        {"part": "character", "kind": "trait", "mode": "deduced"},
    ]
    for n, row in enumerate(bad):
        async with env.engine.connect() as conn:
            with pytest.raises(sa.exc.IntegrityError):
                await conn.execute(sa.insert(PERSONA_LINE).values(
                    id=f"00000000-0000-0000-0000-0000000001{n:02d}", **base, **row))
