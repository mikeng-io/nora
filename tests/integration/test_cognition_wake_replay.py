"""Generated recorded wakes reach the live session as stimuli, never a summary."""
from datetime import UTC, datetime
from types import SimpleNamespace

import sqlalchemy as sa

from evals.rebuilt.domain import Case
from evals.rebuilt.entry import _cognition_replay
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import COGNITION_WAKE, STIMULUS
from tests.contract.test_comprehend_records_contract import database  # noqa: F401
from tests.unit.test_cognition_session_runner import NOW, _runner, wake_memory


async def test_generated_recorded_wake_replays_own_stimulus_batch(database):  # noqa: F811
    engine = create_sql_engine(database)
    try:
        async with engine.begin() as conn:
            sid = (await conn.execute(sa.insert(STIMULUS).values(unit="unit-1", kind="message",
                item="message", ref="messages:generated", platform="signal",
                conversation_id="home-room", at_ms=NOW,
                recorded_at=datetime.fromtimestamp(NOW / 1000, UTC)).returning(STIMULUS.c.id)
            )).scalar_one()
            wid = (await conn.execute(sa.insert(COGNITION_WAKE).values(unit="unit-1", mode="live",
                reason="settled", from_stimulus_id=0, through_stimulus_id=sid, stimulus_count=1,
                eligible_count=1, at_ms=NOW,
                recorded_at=datetime.fromtimestamp(NOW / 1000, UTC)
                ).returning(COGNITION_WAKE.c.id))).scalar_one()
        runner, store, fake = _runner(memory=wake_memory())
        subject = SimpleNamespace(database_url=database, unit="unit-1",
                                  deps=SimpleNamespace(cognition_runner=runner))
        case = Case(name="generated-wake", entry="dreamer", expect="either",
                    metadata={"wake": wid})
        result = await _cognition_replay(subject, case, now_ms=NOW)
        assert result.error is None
        assert fake.calls == 1
        assert store.sessions[-1][1].agenda["items"][0]["stimulus_ids"] == [sid]
        missing = Case(name="missing-wake", entry="dreamer", expect="either",
                       metadata={"wake": wid + 999})
        result = await _cognition_replay(subject, missing, now_ms=NOW)
        assert result.error
        assert fake.calls == 1
    finally:
        await engine.dispose()


async def test_a_wake_whose_detail_was_written_as_a_string_still_replays(database):  # noqa: F811
    """Older rows hold `detail` as a jsonb STRING of the JSON; the store reads
    both forms, and so must the replay, or one such wake stops every search."""
    import json

    from nora.infra.sql.cognition_replay import recorded_wake

    engine = create_sql_engine(database)
    try:
        async with engine.begin() as conn:
            ids = [(await conn.execute(sa.insert(STIMULUS).values(unit="unit-1", kind="message",
                item="message", ref=f"messages:generated-{n}", platform="signal",
                conversation_id="home-room", at_ms=NOW,
                recorded_at=datetime.fromtimestamp(NOW / 1000, UTC)).returning(STIMULUS.c.id)
            )).scalar_one() for n in range(2)]
            wid = (await conn.execute(sa.insert(COGNITION_WAKE).values(unit="unit-1", mode="live",
                reason="settled", from_stimulus_id=ids[0] - 1, through_stimulus_id=ids[1],
                stimulus_count=1, eligible_count=1, at_ms=NOW,
                recorded_at=datetime.fromtimestamp(NOW / 1000, UTC),
                detail=json.dumps({"unclaimed": [ids[1]]})).returning(COGNITION_WAKE.c.id)
            )).scalar_one()
        at, view = await recorded_wake(engine, wid)
        assert at == NOW
        assert [s.id for s in view.pending] == [ids[0]]
    finally:
        await engine.dispose()


async def test_a_session_agenda_written_as_a_string_still_selects_its_batch(database):  # noqa: F811
    """A recorded session's agenda in the string form picks the same batch."""
    import json

    from nora.infra.sql.cognition_replay import recorded_wake
    from nora.infra.sql.schema import COGNITION_SESSION

    engine = create_sql_engine(database)
    try:
        async with engine.begin() as conn:
            ids = [(await conn.execute(sa.insert(STIMULUS).values(unit="unit-1", kind="message",
                item="message", ref=f"messages:agenda-{n}", platform="signal",
                conversation_id="home-room", at_ms=NOW,
                recorded_at=datetime.fromtimestamp(NOW / 1000, UTC)).returning(STIMULUS.c.id)
            )).scalar_one() for n in range(2)]
            wid = (await conn.execute(sa.insert(COGNITION_WAKE).values(unit="unit-1", mode="live",
                reason="settled", from_stimulus_id=ids[0] - 1, through_stimulus_id=ids[1],
                stimulus_count=1, eligible_count=1, at_ms=NOW,
                recorded_at=datetime.fromtimestamp(NOW / 1000, UTC)).returning(COGNITION_WAKE.c.id)
            )).scalar_one()
            await conn.execute(sa.insert(COGNITION_SESSION).values(unit="unit-1", mode="live",
                wake_id=wid, started_at_ms=NOW, from_stimulus_id=ids[0],
                through_stimulus_id=ids[1], situation={},
                agenda=json.dumps({"items": [{"stimulus_ids": [ids[1]]}]})))
        _at, view = await recorded_wake(engine, wid)
        assert [s.id for s in view.pending] == [ids[1]]
    finally:
        await engine.dispose()


async def test_due_floor_replay_restores_the_session_clock_and_evidence(pg_url, pg_template):
    """Generated evaluator tick: due floor written ten seconds after watermark."""
    from datetime import timedelta

    from nora.infra.sql.cognition_replay import recorded_wake
    from nora.infra.sql.schema import COGNITION_SESSION, MESSAGES
    from nora.subject import sandbox_as_of
    from tests.integration.test_snapshot_holds_what_was_held import ROWS

    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.insert(MESSAGES).values(ROWS["messages"](0)))
            tick = await conn.scalar(sa.text("SELECT clock_timestamp()"))
            # The writer uses integer milliseconds; keep the historical evidence before it.
            tick_ms = int(tick.timestamp() * 1000) + 1
            tick = datetime.fromtimestamp(tick_ms / 1000, UTC)
            cutoff = tick_ms - 10000
            sid = (await conn.execute(sa.insert(STIMULUS).values(
                unit="unit-1", kind="time", item="floor", ref="floor:generated",
                at_ms=cutoff - 1000, due_ms=cutoff - 1000, recorded_at=tick,
            ).returning(STIMULUS.c.id))).scalar_one()
            # Unclaimed future rows must never become replay inputs.
            future = (await conn.execute(sa.insert(STIMULUS).values(
                unit="unit-1", kind="time", item="floor", ref="floor:future",
                at_ms=tick_ms + 1000, due_ms=tick_ms + 1000,
                recorded_at=tick,
            ).returning(STIMULUS.c.id))).scalar_one()
            wid = (await conn.execute(sa.insert(COGNITION_WAKE).values(
                unit="unit-1", mode="live", reason="time", from_stimulus_id=0,
                through_stimulus_id=future, stimulus_count=1, eligible_count=1,
                at_ms=cutoff, recorded_at=tick + timedelta(milliseconds=30),
            ).returning(COGNITION_WAKE.c.id))).scalar_one()
            session = {"unit": "unit-1", "mode": "live", "wake_id": wid, "started_at_ms": tick_ms,
                           "from_stimulus_id": sid, "through_stimulus_id": sid, "situation": {},
                           "agenda": {"items": [{"stimulus_ids": [sid]}]}, "recorded_at": tick}
            await conn.execute(sa.insert(COGNITION_SESSION).values(session))
        try:
            replay = await recorded_wake(engine, wid)
        except ValueError:
            replay = None
        assert replay is not None, "a recorded due floor must be reconstructible"
        at, view = replay
        assert at == tick_ms, "replay uses execution clock, never pending watermark"
        assert [s.id for s in view.pending] == [sid]
        async with sandbox_as_of(pg_url, datetime.fromtimestamp(at / 1000, UTC),
                                 template=pg_template) as (url, _):
            restored = create_sql_engine(url)
            try:
                async with restored.connect() as conn:
                    assert list(await conn.scalars(
                        sa.select(STIMULUS.c.id).order_by(STIMULUS.c.id))) == [sid, future]
                    assert await conn.scalar(sa.select(sa.func.count()).select_from(MESSAGES)) == 1
            finally:
                await restored.dispose()
        # Without an agenda the range still uses due eligibility AND the physical bound.
        async with engine.begin() as conn:
            await conn.execute(sa.delete(COGNITION_SESSION).where(
                COGNITION_SESSION.c.wake_id == wid))
        _, view = await recorded_wake(engine, wid)
        assert [s.id for s in view.pending] == [sid]
    finally:
        await engine.dispose()


async def test_recorded_membership_refuses_missing_foreign_and_future_rows(pg_url):
    import pytest

    from nora.infra.sql.cognition_replay import recorded_wake
    from nora.infra.sql.schema import COGNITION_SESSION

    engine = create_sql_engine(pg_url)
    at = datetime.fromtimestamp(NOW / 1000, UTC)
    try:
        async with engine.begin() as conn:
            sid = (await conn.execute(sa.insert(STIMULUS).values(
                unit="unit-1", kind="time", item="floor", ref="floor:control",
                at_ms=NOW, due_ms=NOW, recorded_at=at,
            ).returning(STIMULUS.c.id))).scalar_one()
            wid = (await conn.execute(sa.insert(COGNITION_WAKE).values(
                unit="unit-1", mode="live", reason="time", from_stimulus_id=0,
                through_stimulus_id=sid, stimulus_count=1, eligible_count=1,
                at_ms=NOW, recorded_at=at,
            ).returning(COGNITION_WAKE.c.id))).scalar_one()
            await conn.execute(sa.insert(COGNITION_SESSION).values(
                unit="unit-1", mode="live", wake_id=wid, started_at_ms=NOW,
                from_stimulus_id=sid, through_stimulus_id=sid, situation={},
                agenda={"items": [{"stimulus_ids": [sid]}]}, recorded_at=at))
        assert len((await recorded_wake(engine, wid))[1].pending) == 1
        for change in ({"unit": "foreign"}, {"unit": "unit-1",
                       "recorded_at": datetime.fromtimestamp((NOW+1)/1000, UTC)}):
            async with engine.begin() as conn:
                await conn.execute(sa.update(STIMULUS).where(STIMULUS.c.id == sid).values(**change))
            with pytest.raises(ValueError, match="incomplete"):
                await recorded_wake(engine, wid)
        async with engine.begin() as conn:
            await conn.execute(sa.delete(STIMULUS).where(STIMULUS.c.id == sid))
        with pytest.raises(ValueError, match="incomplete"):
            await recorded_wake(engine, wid)
    finally:
        await engine.dispose()
