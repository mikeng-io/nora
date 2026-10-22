"""Synthetic SQL sources, append-only card writes and the bounded spike seam."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest
import sqlalchemy as sa
from alembic import command

from nora.app.persona_spike import history_days, recorded_sessions, report, window
from nora.domain.character_card import format_line
from nora.domain.persona import ReviseOp
from nora.infra.sql.migrations_runner import alembic_config, run_migrations
from nora.infra.sql.persona import SqlPersonaStore
from nora.infra.sql.schema import (
    PERSONA_LINE,
    PRESENCE_CORRIDORS,
    PRESENCE_DERIVED_PLACES,
    PRESENCE_STAYS,
    ROLLUP_BUILD,
)
from tests.infra.test_persona_adaptor import BATCH, KEY, PK, _adaptor
from tests.infra.test_persona_source_inputs import (
    KEY as SOURCE_KEY,
)
from tests.infra.test_persona_source_inputs import (
    PERSON,
    ROOMS,
    T0,
    env,
    message,
    source_inputs,
)
from tests.unit.test_character_card import trait
from tests.unit.test_persona_rules import CITABLE, HUMAN, NOW, _plan

# Pytest fixtures are intentionally shared with the existing owning-store tests.
__all__ = ["env"]


async def test_new_content_roundtrips_and_supersedes_without_rewriting_history(env):
    store = SqlPersonaStore(env, screen=lambda _: None)
    result = await store.persona_write(
        account=HUMAN, ops=[trait()], citable=CITABLE, now_ms=NOW,
        written_by_session="synthetic", apply_decay=False)
    assert result.status == "ok"
    [line] = await store.account_heads(key=HUMAN.key)
    assert line.content == trait().content and line.strength["occasions"] == 3
    assert format_line(line) == format_line(_plan([trait()]).inserts[0])
    result = await store.persona_write(account=HUMAN,
        ops=[ReviseOp(line="P1", citations=("101", "102", "103"),
                      content={**line.content, "stance": "compares interpretations"})],
        citable=CITABLE, now_ms=NOW + 1, written_by_session="synthetic-next", apply_decay=False)
    assert result.status == "ok"
    [revised] = await store.account_heads(key=HUMAN.key)
    assert revised.supersedes == line.id
    async with env.connect() as conn:
        original = (await conn.execute(sa.select(PERSONA_LINE).where(
            PERSONA_LINE.c.id == line.id))).one()
    assert original.content == dict(line.content) and original.state == "current"


async def test_presence_labels_corridors_and_rhythm_reach_writer_never_coordinates(env):
    # Generated synthetic fixture, never copied from a person's fixes.
    from random import Random
    rng = Random(47)
    latitude, longitude = rng.uniform(-60, 60), rng.uniform(-120, 120)
    async with env.begin() as conn:
        build = (await conn.execute(sa.insert(ROLLUP_BUILD).values(code_version="synthetic",
            params_sha256="a" * 64, status="current", through_ms=T0).returning(
                ROLLUP_BUILD.c.id))).scalar_one()
        place = (await conn.execute(sa.insert(PRESENCE_DERIVED_PLACES).values(
            subject_id=PERSON, circle_id="invented-circle", lat=latitude, lng=longitude,
            radius_m=50, kind="work", confidence=1, label="Lotus Library",
            first_seen_ts=T0, last_seen_ts=T0 + 3 * 86400000, build_id=build,
            lineage=[["presence", PERSON, None]]).returning(
                PRESENCE_DERIVED_PLACES.c.id))).scalar_one()
        for n in range(3):
            await conn.execute(sa.insert(PRESENCE_STAYS).values(subject_id=PERSON,
                person_id=PERSON, circle_id="invented-circle", started_ts=T0 + n * 86400000,
                ended_ts=T0 + n * 86400000 + 3600000, lat=latitude, lng=longitude,
                radius_m=50, fix_count=4, place_id=place, build_id=build,
                lineage=[["presence", PERSON, None]]))
        await conn.execute(sa.insert(PRESENCE_CORRIDORS).values(subject_id=PERSON,
            sequence=["Lotus Library", "Garden Stop"], sample_count=3,
            first_seen_ts=T0, last_seen_ts=T0 + 3 * 86400000, build_id=build,
            lineage=[["presence", PERSON, None]]))
    until = T0 + 3 * 86400000
    async with env.begin() as conn:
        for first_seen, last_seen in ((T0, until + 1), (until + 1, until + 2)):
            await conn.execute(sa.insert(PRESENCE_DERIVED_PLACES).values(
                subject_id=PERSON, circle_id="invented-circle", lat=latitude, lng=longitude,
                radius_m=50, kind="work", confidence=1, label="Future Venue",
                first_seen_ts=first_seen, last_seen_ts=last_seen, build_id=build,
                lineage=[["presence", PERSON, None]]))
            await conn.execute(sa.insert(PRESENCE_CORRIDORS).values(subject_id=PERSON,
                sequence=["Future Venue"], sample_count=99,
                first_seen_ts=first_seen, last_seen_ts=last_seen, build_id=build,
                lineage=[["presence", PERSON, None]]))
        # A future stay at an otherwise eligible place must not inflate its
        # aggregate count or the trailing rhythm.
        await conn.execute(sa.insert(PRESENCE_STAYS).values(subject_id=PERSON,
            person_id=PERSON, circle_id="invented-circle", started_ts=until + 1,
            ended_ts=until + 3600001, lat=latitude, lng=longitude,
            radius_m=50, fix_count=4, place_id=place, build_id=build,
            lineage=[["presence", PERSON, None]]))
    source = source_inputs(env)
    first = await source.presence_revisions(key=SOURCE_KEY, unit="friends", person=PERSON,
                                             limit=2, until_ms=until)
    contexts = [r for r in first if "routine" in dict(r.properties)]
    assert len(contexts) == 1
    [context] = contexts
    assert len(first) == 3 and all(r.evidence for r in first[1:])
    assert context.citable is None and not context.evidence and not context.roots
    assert context.as_of == until
    routine = json.loads(dict(context.properties)["routine"])
    assert [p["label"] for p in routine["places"]] == ["Lotus Library"]
    assert routine["places"][0]["stay_count"] == 3
    assert [c["sequence"] for c in routine["corridors"]] == [["Lotus Library", "Garden Stop"]]
    assert sum(b["total_dwell_ms"] for b in routine["rhythm"]) == 3 * 3600000
    assert all(set(dict(r.properties)) == {"observation"} for r in first[1:])
    pk = replace(PK, person=replace(PK.person, person_id=PERSON))
    adaptor = _adaptor(env)
    adaptor._keys = {KEY: pk}
    prompt = await adaptor.inputs(key=KEY, batch=first)
    assert "Lotus Library" in prompt and "Garden Stop" in prompt and "rhythm" in prompt
    assert prompt.count('"routine":') == 1 and "Future Venue" not in prompt
    assert str(latitude) not in prompt and str(longitude) not in prompt
    await source.settle(key=SOURCE_KEY, revisions=first)
    following = await source.presence_revisions(key=SOURCE_KEY, unit="friends", person=PERSON,
                                                 limit=2, until_ms=until)
    assert sum("routine" in dict(r.properties) for r in following) == 1
    [last] = [r for r in following if r.evidence]
    assert last.effective_from > first[-1].effective_from
    # Earlier day: summaries first seen before the cutoff but last seen later
    # stay out; the rhythm still ends at that day's boundary.
    earlier = await source.presence_revisions(key=SOURCE_KEY, unit="friends", person=PERSON,
        limit=2, until_ms=T0 + 86400000 // 2, include_settled=True)
    [routine_day] = [json.loads(dict(r.properties)["routine"]) for r in earlier
                     if "routine" in dict(r.properties)]
    assert routine_day["places"] == [] and routine_day["corridors"] == []
    assert sum(b["total_dwell_ms"] for b in routine_day["rhythm"]) == 3600000


async def test_window_dry_read_is_oldest_first_day_by_day_and_report_counts_only(env):
    start, end = window("2026-10-01", "2026-10-02")
    for n, ts in enumerate((start - 1, start, start + 86400000, end), start=1):
        await message(env, ident=n, ts=ts, body="invented book discussion")
    pages = [page async for page in history_days(env, rooms=ROOMS,
        person=PERSON, since=start, until=end)]
    assert [[m.row.id for m in batch] for _, batch in pages] == [[2], [3]]
    async with env.connect() as conn:
        count = (await conn.execute(sa.select(sa.func.count()).select_from(PERSONA_LINE))).scalar()
        assert count == 0
    store = SqlPersonaStore(env, screen=lambda _: None)
    assert (await store.persona_write(account=HUMAN, ops=[trait()], citable=CITABLE,
        now_ms=NOW, written_by_session="synthetic", apply_decay=False)).status == "ok"
    result = await report(env, key=HUMAN.key, since=None, until=None)
    assert result["part_kind"] == {"character/topic": 1} and "card" not in result
    assert result["citations"] == {"resolved": 0, "unresolved": 3}
    assert result["usage_per_person_day"] is None and result["refused"] is None
    assert (await report(env, key=HUMAN.key, since=None, until=None, card=True))["card"]


def test_recorded_usage_and_refusals_do_not_invent_missing_cost():
    rows = recorded_sessions([
        {"type": "GENERATION", "metadata": {"attributes": {"dreamer.key": "invented"}},
         "startTime": "2026-10-01T00:00:00Z", "usageDetails": {"input": 40, "output": 2},
         "costDetails": {"total": 0.01}},
        {"type": "GENERATION", "metadata": {"attributes": {"dreamer.key": "invented"}},
         "startTime": "2026-10-01T00:00:01Z", "model": "invented-model"},
        {"type": "TOOL", "metadata": {"attributes": {"dreamer.key": "invented"}},
         "startTime": "2026-10-01T00:00:02Z",
         "output": {"refusals": [{"reason": "induced_instances"}]}},
    ])
    assert rows[0]["tokens"] == 42 and rows[0]["cost"] == 0.01
    assert rows[1]["tokens"] is None and rows[1]["cost"] is None
    assert rows[2]["refusals"] == ["induced_instances"]


def test_migration_up_down_keeps_one_head_and_legacy_rows(pg_url):
    run_migrations(pg_url)
    from nora.infra.sql.migrations_runner import sync_url_for
    sync = sa.create_engine(sync_url_for(pg_url))
    with sync.begin() as conn:
        conn.execute(sa.insert(PERSONA_LINE).values(id="00000000-0000-0000-0000-000000000001",
            persona_key="invented#unit", part="character", kind="trait", text="legacy prose",
            mode="induced", citations=["1"], state="current", support_count=1,
            unit="invented", written_at=1, written_by_session="synthetic"))
    cfg = alembic_config(pg_url)
    command.downgrade(cfg, "0126_social_backfill_end")
    command.upgrade(cfg, "head")
    with sync.connect() as conn:
        assert conn.execute(sa.select(PERSONA_LINE.c.text)).scalar_one() == "legacy prose"
    sync.dispose()
    from alembic.script import ScriptDirectory
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0127_character_content"]


async def test_coordinate_input_is_refused_before_any_model_can_read_it(env):
    from nora.agent.security import looks_like_coordinate
    from nora.domain.errors import AccountNotRecordableError
    adaptor = _adaptor(env)
    adaptor._coordinate_screen = looks_like_coordinate
    ordinary = await adaptor.inputs(key=KEY, batch=BATCH)
    assert "booked 8pm again" in ordinary
    row = replace(BATCH[0], row=replace(BATCH[0].row, body="raw pin 22.12345, 114.98765"))
    with pytest.raises(AccountNotRecordableError, match="coordinate"):
        await adaptor.inputs(key=KEY, batch=(row,))
