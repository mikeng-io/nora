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
                eligible_count=1, at_ms=NOW).returning(COGNITION_WAKE.c.id))).scalar_one()
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
