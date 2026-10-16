"""A Cognition session's row in Postgres names the skill it ran with.

A session with no skill injected reads `skill_versions = {}`. A read-back tells
a skill-injected session from one that was not by this column alone, so it is
pinned where it is stored: one session through the production objective,
prelude and `AgendaSkills`, against the real `SqlCognitionStore`, then
`cognition_session.skill_versions` read back with SQL.

Rooms and words are placeholders.
"""

from __future__ import annotations

import sqlalchemy as sa

from nora.app.cognition_wiring import REGISTER_SKILL
from nora.infra.sql.cognition_store import SqlCognitionStore
from nora.infra.sql.engine import create_sql_engine
from tests.objectives import shipped_objectives
from tests.unit.test_cognition_agenda_skills import run_one_session


async def test_the_session_row_in_postgres_records_the_register_skills_digest(pg_url):
    engine = create_sql_engine(pg_url)
    try:
        spy = await run_one_session(SqlCognitionStore(engine))
        async with engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT skill_versions, doctrine_sha256 FROM cognition_session",
            ))).all()
    finally:
        await engine.dispose()
    skill = shipped_objectives().skill(REGISTER_SKILL)
    assert skill is not None
    assert [r.skill_versions for r in rows] == [{REGISTER_SKILL: skill.sha256}]
    # the doctrine digest is the prelude-and-objective pair the model read
    assert rows[0].doctrine_sha256 == shipped_objectives().for_objective("cognition").sha256
    assert spy.system_prompt.startswith('<dreamer_doctrine name="cognition-prelude">')
