"""`nora snapshot --at T` end to end, through the real parser, against a real
Postgres: a dry run writes nothing anywhere, `--into` seeds an empty sandbox,
and each refusal is one sentence and exit 1 rather than a partial copy.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa

from nora.__main__ import build_parser
from nora.app import cli
from nora.infra.sql import create_sql_engine
from nora.subject import sandbox_database

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _run(*argv: str) -> int:
    return await cli.execute(build_parser().parse_args(["snapshot", *argv]))


async def _count(url: str, sql: str) -> int:
    eng = create_sql_engine(url)
    try:
        async with eng.connect() as conn:
            return int((await conn.execute(sa.text(sql))).scalar_one())
    finally:
        await eng.dispose()


async def _state(engine: Any) -> tuple[str, int]:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO tag_debounce_state (platform, conversation_id, last_allowed_at) "
            "VALUES ('signal', 'g1', 1)"
        ))
    async with engine.connect() as conn:
        at = (await conn.execute(sa.text("SELECT clock_timestamp()"))).scalar_one()
    async with engine.begin() as conn:
        await conn.execute(sa.text("UPDATE tag_debounce_state SET last_allowed_at = 2"))
    return at.isoformat(), 1


async def test_a_dry_run_reports_and_writes_nothing(
    pg_url: str, engine: Any, capsys: Any,
) -> None:
    at, _ = await _state(engine)
    before = await _count(pg_url, "SELECT count(*) FROM row_history")
    assert await _run("--at", at, "--source", pg_url) == 0
    out = capsys.readouterr().out
    assert "dry run: nothing written" in out
    assert "tag_debounce_state" in out
    assert "doctrine: UNAVAILABLE" in out  # no load recorded: said, not guessed
    assert "memory (Honcho)" in out and "step 2" in out
    assert await _count(pg_url, "SELECT count(*) FROM row_history") == before


async def test_into_seeds_an_empty_sandbox_and_refuses_it_twice(
    pg_url: str, pg_template: str, engine: Any, capsys: Any,
) -> None:
    at, held = await _state(engine)
    async with sandbox_database(pg_url, template=pg_template) as target:
        assert await _run("--at", at, "--source", pg_url, "--into", target) == 0
        assert await _count(
            target, "SELECT last_allowed_at FROM tag_debounce_state",
        ) == held
        capsys.readouterr()
        assert await _run("--at", at, "--source", pg_url, "--into", target) == 1
        assert "already holds rows" in capsys.readouterr().err


async def test_the_source_is_never_the_target(pg_url: str, engine: Any, capsys: Any) -> None:
    at, _ = await _state(engine)
    assert await _run("--at", at, "--source", pg_url, "--into", pg_url) == 1
    assert "never writes the source" in capsys.readouterr().err


async def test_a_time_without_an_offset_is_refused(pg_url: str, capsys: Any) -> None:
    assert await _run("--at", "2026-09-28T12:00:00", "--source", pg_url) == 1
    assert "no offset" in capsys.readouterr().err


async def test_a_date_typed_as_digits_is_refused_not_read_as_1970(
    pg_url: str, capsys: Any,
) -> None:
    assert await _run("--at", "20260927", "--source", pg_url) == 1
    assert "before 2020" in capsys.readouterr().err


async def test_a_target_at_another_migration_is_refused(
    pg_url: str, pg_template: str, engine: Any, capsys: Any,
) -> None:
    at, _ = await _state(engine)
    async with sandbox_database(pg_url, template=pg_template) as target:
        other = create_sql_engine(target)
        try:
            async with other.begin() as conn:
                await conn.execute(sa.text(
                    "UPDATE alembic_version SET version_num = '0067_summons_message_key'"
                ))
        finally:
            await other.dispose()
        assert await _run("--at", at, "--source", pg_url, "--into", target) == 1
        assert "one schema only" in capsys.readouterr().err
        assert await _count(target, "SELECT count(*) FROM tag_debounce_state") == 0
