"""Canonical SQL versions retain effects absent from the final active view."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from nora.domain.notes import NoteSupersedeReason
from nora.domain.stimuli import NewStimulus
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.memo import SqlMemoStore
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import ENTITY
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.subject.effects import effect_boundary, prepare_effect_reads, read_effects
from nora.subject.environment import IsolatedServices

NOW = 2_000_000_000_000


async def memo(store, *, unit="generated-unit", title="Generated retained fact"):
    return await store.create(unit=unit, platform="signal", conversation_id="generated-room",
        title=title, body="## Kept\n- Generated retained fact [1]", citations=("1",), mode="told",
        established_by="generated-cognition", model=None, now_ms=NOW)


async def note(store):
    return await store.create(unit="generated-unit", title="Generated open venue question",
        description="The generated venue is undecided.", uncertainty="Which venue?",
        closing_condition="A generated venue is selected.", established_in="generated-room",
        body="## Decided\n\n## Open\n- Which generated venue? [1]",
        established_by="generated-cognition", now_ms=NOW, anchor_entity_ids=("Miles",))


async def effects(engine, boundary, events=()):
    return await read_effects(engine, since=boundary, unit="generated-unit",
        rooms=(("signal", "generated-room"),), profile_keys=(), events=list(events))


@pytest.mark.asyncio
async def test_versions_survive_creation_retirement_withdrawal_and_deletion(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        notes, memos = SqlNoteStore(engine), SqlMemoStore(engine)
        prior = await memo(memos, title="Generated prior state")
        boundary = await effect_boundary(engine)
        stimulus = await SqlStimulusStore(engine).append(stimulus=NewStimulus(
            unit="generated-unit", kind="message", item="message", ref="messages:1",
            at_ms=1000, platform="signal", conversation_id="generated-room"), recorded_at_ms=1000)
        opened = await note(notes)
        assert await notes.supersede(key="generated-unit", target=opened.id, replaced_by=None,
            because=NoteSupersedeReason.RETIRED_CLOSED, source=None, now_ms=NOW + 1) == 1
        kept = await memo(memos)
        await memos.append(unit="generated-unit", memo_id=kept.id, base_revision_id=kept.head.id,
            title=kept.head.title, body=kept.head.body, citations=kept.head.citations, mode="told",
            state="withdrawn", established_by="generated-cognition", model=None, now_ms=NOW + 1)
        foreign = await memo(memos, unit="another-generated-unit", title="Foreign generated fact")
        assert await notes.standing(unit="generated-unit") == ()
        assert [row.id for row in await memos.kept(unit="generated-unit")] == [prior.id]
        result = await effects(engine, boundary)
        assert result["status"] == "observed"
        assert any(row["source"] == "stimulus" and row["row"]["id"] == stimulus
                   for row in result["items"])
        history = [row for row in result["items"] if row["source"] == "row_history"
                   and row["row"]["table_name"] == "note"]
        assert [row["operation"] for row in history] == ["insert", "update"]
        assert history[0]["row"]["image"]["retired_at"] is None
        assert history[-1]["row"]["image"]["retired_at"] == NOW + 1
        revisions = [row["row"] for row in result["items"] if row["source"] == "memo_revision"]
        assert [row["state"] for row in revisions] == ["kept", "withdrawn"]
        assert {str(row["memo_id"]) for row in revisions} == {kept.id}
        assert any(row["source"] == "note_revision" for row in result["items"])
        assert foreign.id not in str(result) and prior.id not in str(result)
        await check_consumers(result)
        deletion = await effect_boundary(engine)
        async with engine.begin() as conn:
            await conn.execute(sa.insert(ENTITY).values(id="generated-ephemeral-entity",
                unit="generated-unit", kind="place", name="Generated deleted venue"))
            await conn.execute(sa.delete(ENTITY).where(ENTITY.c.id == "generated-ephemeral-entity"))
        removed = await effects(engine, deletion)
        assert any(row["source"] == "row_history" and row["operation"] == "delete"
                   and row["row"]["image"]["id"] == "generated-ephemeral-entity"
                   for row in removed["items"])
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_refused_tool_effects_keep_domain_and_transport_outcomes_separate(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        boundary = await effect_boundary(engine)
        events = [{"kind": "tool_finished", "event_id": "e1", "invocation_id": "i1",
                   "attempt_id": "a1", "tool": {"name": "memo_create", "input": {}},
                   "result": {"status": "success", "content": [{"json": {
                       "status": "refused", "reason": "generated-grounding-absent"}}]}}]
        result = await effects(engine, boundary, events)
        assert result["items"] == []
        [refused] = result["tools"]
        assert refused["ref"] == "tool:e1"
        assert refused["transport_status"] == "success"
        assert refused["outcome"] == {
            "status": "refused", "reason": "generated-grounding-absent"}
    finally:
        await engine.dispose()


async def check_consumers(result):
    import html
    import json

    from scripts.cognition_loop.cases import Expectation
    from scripts.cognition_loop.html import _did
    from scripts.cognition_loop.judges import judge_expectation
    from scripts.cognition_loop.state import StateDiff, UnitState

    note = next(row for row in result["items"] if row["kind"] == "note")

    class Judges:
        def __init__(self):
            self.served, self.calls = {}, []
            self.ref = note["ref"]

        async def call(self, model, instruction, payload):
            self.calls.append(payload)
            self.served[model] = [{"x-omniroute-model": model}]
            return json.dumps({"verdict": "met", "reason": "Generated lifecycle control",
                               "entry_ids": [self.ref]})

    judges = Judges()
    expectation = Expectation(0, "must", "note", "Generated note was created and retired.")
    trajectory = {"effects": result}
    args = (judges, ("a", "b"), expectation, StateDiff(kinds={}), UnitState(kinds={}), trajectory)
    verdict = await judge_expectation(*args)
    assert verdict.verdict == "PASS" and len(judges.calls) == 2
    assert all(payload["effects"] == result for payload in judges.calls)
    rendered = _did({"trajectory": trajectory})
    assert html.escape(json.dumps(result, indent=2, ensure_ascii=False, default=str)) in rendered
    judges.ref = "note:row_history:unshown-generated-version"
    assert (await judge_expectation(*args)).verdict == "ERROR"


async def standing_read_plan(engine, boundary):
    from sqlalchemy import event
    from sqlalchemy.dialects import postgresql

    from nora.infra.sql.schema import STANDING_REVISION

    queries = []

    def capture(conn, clause, multiparams, params, execution_options):
        if isinstance(clause, sa.sql.Select) and clause.get_final_froms() == [STANDING_REVISION]:
            queries.append(clause)

    event.listen(engine.sync_engine, "before_execute", capture)
    try:
        result = await effects(engine, boundary)
    finally:
        event.remove(engine.sync_engine, "before_execute", capture)
    [query] = queries
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    async with engine.connect() as conn:
        plan = await conn.scalar(sa.text("EXPLAIN (ANALYZE, FORMAT JSON) " + sql))
    return result, plan[0]["Plan"]


def visited(plan):
    own = (plan.get("Actual Rows", 0) + plan.get("Rows Removed by Filter", 0)) * plan.get(
        "Actual Loops", 1) if plan.get("Relation Name") == "standing_revision" else 0
    return own + sum(visited(child) for child in plan.get("Plans", []))


@pytest.mark.asyncio
@pytest.mark.fresh_database
async def test_effect_reads_do_not_rescan_old_unrelated_revisions(clean_pg_url):
    from datetime import UTC, datetime

    from nora.infra.sql.schema import STANDING_REVISION

    engine = create_sql_engine(clean_pg_url)
    counts = []
    try:
        async with IsolatedServices(database_url=clean_pg_url, applications={}) as environment:
            await prepare_effect_reads(engine, environment)
        for size in (1_000, 6_000):
            async with engine.begin() as conn:
                await conn.execute(sa.insert(STANDING_REVISION), [{
                    "platform": "signal",
                    "conversation_id": "generated-room" if i % 2 else "another-generated-room",
                    "body": "Generated unrelated standing", "fold_count": 1, "updated_at": 1,
                    "recorded_at": datetime(2000, 1, 1, tzinfo=UTC),
                } for i in range(size)])
                await conn.execute(sa.text("ANALYZE standing_revision"))
            boundary = await effect_boundary(engine)
            async with engine.begin() as conn:
                await conn.execute(sa.insert(STANDING_REVISION).values(platform="signal",
                    conversation_id="generated-room", body="Generated owned standing",
                    fold_count=1, updated_at=NOW))
            result, plan = await standing_read_plan(engine, boundary)
            assert len([row for row in result["items"]
                        if row["source"] == "standing_revision"]) == 1
            counts.append(visited(plan))
        assert max(counts) <= 16, counts
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.fresh_database
async def test_effect_indexes_refuse_an_unowned_or_closed_database(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    foreign = create_sql_engine(engine.url.set(database="another-generated-database"))
    try:
        async with IsolatedServices(database_url=clean_pg_url, applications={}) as environment:
            with pytest.raises(ValueError, match="active case-owned database"):
                await prepare_effect_reads(foreign, environment)
            await prepare_effect_reads(engine, environment)
        with pytest.raises(ValueError, match="active case-owned database"):
            await prepare_effect_reads(engine, environment)
    finally:
        await engine.dispose()
        await foreign.dispose()


@pytest.mark.asyncio
async def test_cancelled_reminder_and_replaced_note_keep_their_versions(clean_pg_url):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from nora.domain.reminders import Reminder, ReminderState
    from nora.infra.sql.reminders import SqlReminderStore

    engine = create_sql_engine(clean_pg_url)
    try:
        boundary = await effect_boundary(engine)
        notes, reminders = SqlNoteStore(engine), SqlReminderStore(engine)
        original, successor = await note(notes), await note(notes)
        assert await notes.supersede(key="generated-unit", target=original.id,
            replaced_by=successor.id, because=NoteSupersedeReason.RETIRED_MERGED,
            source=None, now_ms=NOW + 1) == 1
        now = datetime.now(UTC)
        reminder = Reminder(uuid4(), "miles", "signal", "generated-room", "group",
            "Generated reminder to cancel", now, now + timedelta(hours=1),
            ReminderState.PENDING, None, None)
        await reminders.create(reminder)
        assert await reminders.cancel(reminder.id, conversation_id="generated-room")
        assert await reminders.all_pending(limit=10) == []
        result = await effects(engine, boundary)
        versions = [row["row"] for row in result["items"] if row["source"] == "row_history"]
        assert {row["image"]["state"] for row in versions
                if row["table_name"] == "reminders"} == {"pending", "cancelled"}
        replaced = [row["image"] for row in versions
                    if row["table_name"] == "note" and row["image"]["id"] == original.id]
        assert replaced[0]["replaced_by"] is None
        assert replaced[-1]["replaced_by"] == successor.id
        assert replaced[-1]["retired_at"] == NOW + 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_prior_delivery_action_completed_inside_the_boundary_is_retained(clean_pg_url):
    from datetime import UTC, datetime

    from nora.infra.sql.schema import BRIDGE_ACTION

    engine = create_sql_engine(clean_pg_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.insert(BRIDGE_ACTION).values(action_id="generated-prior-action",
                platform="signal", account_key="generated-account", owner_kind="live_invocation",
                owner_id="generated-invocation", op="send", request={"target": {
                    "id": "generated-room"}}, request_hash="generated-digest",
                created_at=datetime(2000, 1, 1, tzinfo=UTC)))
        boundary = await effect_boundary(engine)
        async with engine.begin() as conn:
            await conn.execute(sa.update(BRIDGE_ACTION).values(state="confirmed",
                completed_at=sa.func.clock_timestamp(), platform_ref="generated-native-ref",
                platform_ms=NOW).where(BRIDGE_ACTION.c.action_id == "generated-prior-action"))
        result = await effects(engine, boundary)
        [receipt] = [row for row in result["items"] if row["kind"] == "delivery"]
        assert receipt["source"] == "bridge_action"
        assert receipt["row"]["state"] == "confirmed"
        assert receipt["row"]["platform_ref"] == "generated-native-ref"
        assert receipt["row"]["platform_ms"] == NOW
        assert datetime.fromisoformat(receipt["at"]) == datetime.fromisoformat(
            receipt["row"]["completed_at"])
    finally:
        await engine.dispose()
