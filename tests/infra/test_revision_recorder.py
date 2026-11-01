"""What was in force at T: the recorder writes each load, `in_force` and
`materialise` read the newest at or before T.

The corpora are the repo's OWN `nora/doctrine` and `nora/dreamer`, copied
into a temp directory and loaded by the production stores, so a load here
is a real scan of real files; a hot edit is a real file write the watcher's
fingerprint sees.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from nora.app.revisions import RevisionRecorder
from nora.doctrine.loader import DoctrineStore
from nora.infra.sql import create_sql_engine
from nora.infra.sql.revisions import SqlRevisionStore
from nora.infra.sql.snapshot_at import in_force, materialise
from nora.instructions import DreamerInstructions
from nora.obs.metrics import Metrics

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _clock(_engine: Any) -> datetime:
    """Match instruction loads' process clock, independently of Docker's clock."""
    return datetime.now(UTC)


async def test_each_load_is_recorded_and_in_force_answers_by_time(
    engine: Any, tmp_path: Path,
) -> None:
    doctrine_dir = tmp_path / "doctrine"
    dreamer_dir = tmp_path / "dreamer"
    shutil.copytree(REPO / "nora" / "doctrine", doctrine_dir)
    shutil.copytree(REPO / "nora" / "dreamer", dreamer_dir)
    config = tmp_path / "config.toml"
    config.write_text("# a config\n", encoding="utf-8")
    policy = tmp_path / "policy.toml"
    policy.write_text("# a policy\n", encoding="utf-8")
    metrics = Metrics()
    doctrine = DoctrineStore(doctrine_dir, metrics=metrics)
    dreamer = DreamerInstructions(dreamer_dir, metrics=metrics)
    recorder = RevisionRecorder(
        store=SqlRevisionStore(engine), doctrine=doctrine, dreamer=dreamer,
        config_files=(("config.toml", config), ("policy.toml", policy)),
        metrics=metrics, manifest_path=tmp_path / "absent-version.json",
        hostname="test-host", environ={"NORA_GIT_SHA": "0" * 40},
    )

    # The deploy write fails at start (its table is briefly not there) and
    # is retried on the next tick, not lost.
    async with engine.begin() as conn:
        await conn.execute(sa.text("ALTER TABLE deploy_revision RENAME TO deploy_revision_away"))
    await recorder.record_start(started_at=await _clock(engine))
    assert metrics.get("nora_revision_record_failures_total", kind="deploy") == 1
    async with engine.begin() as conn:
        await conn.execute(sa.text("ALTER TABLE deploy_revision_away RENAME TO deploy_revision"))
    await recorder.poll()  # the deploy retried; no corpus moved, so no second row
    first = await _clock(engine)

    objective = sorted((dreamer_dir / "objectives").glob("*.md"))[0]
    objective.write_text(objective.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    await recorder.poll()
    second = await _clock(engine)

    async with engine.connect() as conn:
        counts = dict((await conn.execute(sa.text(
            "SELECT corpus, count(*) FROM doctrine_revision GROUP BY corpus"
        ))).all())
        deploys = (await conn.execute(sa.text("SELECT count(*) FROM deploy_revision"))).scalar_one()
        then = await in_force(conn, first)
        now = await in_force(conn, second)
    assert counts == {"doctrine": 1, "dreamer": 2}
    assert deploys == 1  # written once on the retry, and not again on later ticks
    assert then.doctrine is not None and then.dreamer is not None
    assert then.doctrine.assembled_sha256 == doctrine.get(applies_to="tag").sha256
    assert now.dreamer is not None and now.dreamer.content_sha256 != then.dreamer.content_sha256
    assert then.config["identity.toml"] is None  # never recorded: unavailable, not guessed
    assert then.deploy is not None and then.deploy["manifest_status"] == "absent"
    assert then.deploy["git_sha"] == "0" * 40  # the baked sha, the manifest absent
    assert metrics.get("nora_revision_record_failures_total") == 1  # the one retried

    out = tmp_path / "restored"
    forced, _written = await materialise(engine, first, out)
    assert forced.missing() == ("identity.toml",)
    rel = objective.relative_to(dreamer_dir)
    restored = (out / "dreamer" / rel).read_text(encoding="utf-8")
    assert restored + "\n" == objective.read_text(encoding="utf-8")
    assert (out / "config.toml").read_text(encoding="utf-8") == "# a config\n"
    for path in doctrine_dir.rglob("*.md"):
        assert (out / "doctrine" / path.relative_to(doctrine_dir)).read_text(
            encoding="utf-8",
        ) == path.read_text(encoding="utf-8")


async def test_instruction_history_cuts_do_not_depend_on_database_clock(
    engine: Any, tmp_path: Path,
) -> None:
    def skew_clock(conn, cursor, statement, parameters, context, executemany):
        if statement == "SELECT clock_timestamp()":
            statement += " + interval '5 seconds'"
        return statement, parameters

    sa.event.listen(engine.sync_engine, "before_cursor_execute", skew_clock, retval=True)
    try:
        async with engine.connect() as conn:
            database_now = (await conn.execute(sa.text("SELECT clock_timestamp()"))).scalar_one()
        assert database_now > datetime.now(UTC) + timedelta(seconds=4)
        await test_each_load_is_recorded_and_in_force_answers_by_time(engine, tmp_path)
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", skew_clock)


async def test_a_deploy_that_names_no_code_is_unavailable(engine: Any, tmp_path: Path) -> None:
    """An image built with no `NORA_GIT_SHA` and no manifest records a row
    with every identifier empty: that names no code, so it is missing."""
    store = SqlRevisionStore(engine)
    at = await _clock(engine)
    await store.record_deploy(
        started_at=at, manifest_status="absent", version=None, git_sha=None,
        built_at=None, image_id=None, hostname="h",
    )
    async with engine.connect() as conn:
        forced = await in_force(conn, await _clock(engine))
    assert forced.deploy is not None
    assert "deploy" in forced.missing()
