"""Ledger storage evidence against PostgreSQL, with generated room messages."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
import sqlalchemy as sa
from alembic import command

from nora.agent.standing_ledger import ledger_permits
from nora.domain.lineage import LiveRoom, RenderRoom, RoomFacts
from nora.domain.models import NormalizedMessage
from nora.domain.note_body import Shown, ShownMessage
from nora.domain.standing_ledger import LedgerOp
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import alembic_config
from nora.infra.sql.standing_ledger import SqlStandingLedger
from nora.obs.metrics import Metrics


@pytest.fixture(scope="module")
def ledger_url(pg_admin, pg_template):
    import uuid

    import psycopg

    from tests.conftest import _async_url

    name = "t_ledger_" + uuid.uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{pg_template}"')
    try:
        yield _async_url(pg_admin, name)
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


@pytest.fixture
async def ledger_db(ledger_url):
    engine = create_sql_engine(ledger_url)
    async with engine.begin() as conn:
        await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
        for table in (
            "standing_decision",
            "standing_decision_revision",
            "standing_commitment",
            "standing_commitment_revision",
            "standing_thread",
            "standing_thread_revision",
            "tag_trace",
            "messages",
            "standing",
            "standing_revision",
        ):
            await conn.execute(sa.text(f"TRUNCATE {table} CASCADE"))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def ledger(ledger_db):
    return SqlStandingLedger(ledger_db, metrics=Metrics())


def target(room="room-a", *, unit=None, people=frozenset({"kit"}), unnamed=0, location=False):
    rooms = (
        *(
            RoomFacts(
                "signal",
                frozenset({name}),
                "u",
                frozenset({"kit", "ana"}),
                primary_id=name,
                label=name,
                location=location,
            )
            for name in ("room-a", "room-b")
        ),
        RoomFacts(
            "signal", frozenset({"room-c"}), "other", frozenset({"kit"}), primary_id="room-c"
        ),
    )
    current = next(r for r in rooms if r.primary_id == room)
    return RenderRoom(
        replace(current, unit=unit or current.unit, persons=people),
        rooms=rooms,
        live=LiveRoom(people, unnamed=unnamed),
        mode="off",
        content_checks=False,
    )


async def message(engine, room="room-a", n=1):
    return await SqlRecordStore(engine).append(
        message=NormalizedMessage(
            platform="signal",
            conversation_id=room,
            conversation_type="group",
            sender_id="generated-account",
            sender_name="Kit",
            timestamp=n * 1000,
            body=f"Generated topic {n}",
            raw={},
        )
    )


def frame(*ids):
    return Shown(rendered=tuple(ShownMessage(id=i, sender="Kit") for i in ids))


def decision(id, **kwargs):
    return LedgerOp(
        "open",
        "decision",
        subject="A choice",
        scope="unit",
        sections={
            "Options": f"Option A has lower travel cost [{id}]",
            "Leaning": "none yet",
        },
        **kwargs,
    )


async def plant(ledger, ledger_db):
    id = await message(ledger_db)
    result = await ledger.apply(decision(id), target=target(), shown=frame(id), now_ms=1000)
    assert result.status == "ok", result.refusal
    return result.entry, id


async def test_retention_is_byte_exact_across_unrelated_revisions(ledger, ledger_db):
    import json
    from dataclasses import asdict

    def exact_bytes(entry):
        return json.dumps(asdict(entry), sort_keys=True, separators=(",", ":")).encode()

    id = await message(ledger_db)
    planted = []
    for n in range(6):
        op = (
            decision(id)
            if n % 2
            else LedgerOp(
                "open",
                "commitment",
                subject=f"Check {n}",
                scope="unit",
                owed_to=("room",),
                sections={"Owed": f"Check generated timetable {n} [{id}]"},
            )
        )
        got = await ledger.apply(op, target=target(), shown=frame(id), now_ms=1000)
        assert got.entry is not None
        planted.append(got.entry)
    from nora.domain.standing import Standing
    from nora.infra.sql.standing import SqlStandingStore

    legacy = SqlStandingStore(ledger_db)
    await legacy.write(
        standing=Standing("signal", "room-a", "\n".join(e.body for e in planted), 1, 1000)
    )
    exact = {e.id: exact_bytes(e) for e in planted}
    unrelated, _ = await plant(ledger, ledger_db)
    for n in range(12):
        changed = await ledger.apply(
            LedgerOp(
                "revise",
                "decision",
                id=unrelated.id,
                base=unrelated.revision,
                sections={"Leaning": f"Option A after comparison {n} [{id}]"},
            ),
            target=target("room-b"),
            shown=frame(id),
            now_ms=2000 + n,
        )
        assert changed.status == "ok"
        # Storage comparison arm: today's paragraph replacement, without a live model call.
        await legacy.write(
            standing=Standing("signal", "room-a", f"Unrelated turn {n}", n + 2, 2000 + n)
        )
        paragraph = await legacy.read(platform="signal", conversation_id="room-a")
        assert sum(e.body in paragraph.body for e in planted) == 0
        unrelated = changed.entry
        rows = await ledger.read(unit="u", platform="signal", conversation_id="room-a")
        assert {e.id: exact_bytes(e) for e in rows if e.id in exact} == exact
    for e in planted:
        assert await ledger.history(unit="u", kind=e.kind, id=e.id) == (e,)


async def test_two_rooms_compare_and_append_from_one_base(ledger, ledger_db):
    entry, id = await plant(ledger, ledger_db)
    # Hold the entry so BOTH rooms are in flight. A positive observation is two
    # database sessions waiting on locks in this isolated database; no zero-only probe.
    from nora.infra.sql.schema import LEDGER_DECISION

    tasks = []
    blocked = 0
    async with ledger_db.begin() as guard:
        await guard.execute(
            sa.select(LEDGER_DECISION).where(LEDGER_DECISION.c.id == entry.id).with_for_update()
        )
        for op, room in (("revise", "room-a"), ("close", "room-b")):
            tasks.append(
                asyncio.create_task(
                    ledger.apply(
                        LedgerOp(
                            op,
                            "decision",
                            id=entry.id,
                            base=entry.revision,
                            sections={"Leaning": f"Option B [{id}]"} if op == "revise" else {},
                            outcome=f"Option A wins [{id}]" if op == "close" else None,
                        ),
                        target=target(room),
                        shown=frame(id),
                        now_ms=2000,
                    )
                )
            )
        for _ in range(200):
            async with ledger_db.connect() as observer:
                blocked = (
                    await observer.execute(
                        sa.text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname = current_database() "
                            "AND wait_event_type = 'Lock'"
                        )
                    )
                ).scalar_one()
            if blocked == 2:
                break
            await asyncio.sleep(0.01)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert blocked == 2, "both competing writes must have reached the database"
    assert all(not isinstance(r, Exception) for r in results)
    assert sorted(r.status for r in results) == ["ok", "refused"]
    assert [r.refusal.reason for r in results if r.refusal] == ["stale_base"]
    history = await ledger.history(unit="u", kind="decision", id=entry.id)
    assert [e.revision for e in history] == [1, 2]
    rows = await ledger.read(unit="u", platform="signal", conversation_id="room-b")
    assert rows == (history[-1],)


async def test_existing_entry_without_base_is_refused(ledger, ledger_db):
    entry, id = await plant(ledger, ledger_db)
    got = await ledger.apply(
        LedgerOp("close", "decision", id=entry.id, outcome=f"Settled [{id}]"),
        target=target(),
        shown=frame(id),
        now_ms=2000,
    )
    assert got.refusal.reason == "missing_base"
    assert len(await ledger.history(unit="u", kind="decision", id=entry.id)) == 1


async def test_unit_entry_lineage_denies_extra_or_unknown_listener(ledger, ledger_db):
    entry, _ = await plant(ledger, ledger_db)
    assert ledger_permits(entry, target("room-b"))
    assert not ledger_permits(entry, target("room-b", people=frozenset({"kit", "new-member"})))
    assert not ledger_permits(entry, target("room-b", unnamed=1))
    assert not ledger_permits(entry, target("room-a", unnamed=1))
    assert not ledger_permits(entry, replace(target("room-b"), live=None))
    assert not ledger_permits(entry, target("room-c"))


@pytest.mark.parametrize("restriction", ["exclusion", "location", "unresolved"])
async def test_cited_turn_exclusions_and_location_survive_in_entry(ledger, ledger_db, restriction):
    from nora.domain.lineage import message as origin_message
    from nora.domain.tag_trace import TagTrace
    from nora.domain.turn_record import DisclosureRequirement
    from nora.infra.sql.tag_traces import SqlTagTraceStore

    traces = SqlTagTraceStore(ledger_db)
    req = {
        "exclusion": DisclosureRequirement(
            "signal:room-a", (origin_message("signal:room-a"),), exclude_persons=("ana",)
        ),
        "location": DisclosureRequirement(
            "signal:room-a",
            (origin_message("signal:room-a"),),
            location=True,
            location_subjects=("kit",),
        ),
        "unresolved": DisclosureRequirement(None, (), resolved=False),
    }[restriction]
    trace = TagTrace(
        tag_id=__import__("uuid").uuid4().hex,
        conversation_id="room-a",
        created_at=1000,
        outcome="send_reply",
        transcript=(),
        messages=(
            {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "call", "name": "source", "input": {}}}],
            },
        ),
        unit="u",
        platform="signal",
        requirements={"call": [req.to_json()]},
    )
    await traces.save(trace)
    saved = (await traces.recent(conversation_id="room-a", limit=1))[0]
    got = await ledger.apply(
        LedgerOp(
            "open",
            "decision",
            scope="unit",
            subject="A choice",
            sections={"Options": f"Option A turn({saved.handle})", "Leaning": "none yet"},
        ),
        target=target(),
        shown=Shown(turn_handles=frozenset({saved.handle})),
        now_ms=1000,
    )
    if not req.known:
        assert got.status == "refused"
        assert got.refusal.reason == "unknown_turn"
    else:
        assert got.entry is not None
        assert not ledger_permits(got.entry, target("room-b", people=frozenset({"ana"})))
        if req.exclude_persons:
            assert ledger_permits(got.entry, target("room-b"))
        else:
            allowed = replace(target("room-b", location=True), circles=(frozenset({"kit"}),))
            assert ledger_permits(got.entry, allowed)


async def test_cross_unit_citations_and_uncited_claims_are_refused(ledger, ledger_db):
    id = await message(ledger_db, "room-c")
    got = await ledger.apply(decision(id), target=target(), shown=frame(id), now_ms=1000)
    assert got.refusal is not None
    got = await ledger.apply(
        LedgerOp(
            "open",
            "decision",
            subject="A choice",
            sections={
                "Options": "Unsupported",
                "Leaning": "none yet",
            },
        ),
        target=target(),
        shown=frame(id),
        now_ms=1000,
    )
    assert got.status == "refused"
    assert got.refusal.reason == "uncited"
    assert await ledger.read(unit="u", platform="signal", conversation_id="room-a") == ()


async def test_thread_spans_are_code_computed_and_touch_does_not_promote(ledger, ledger_db):
    a = await message(ledger_db, n=1)
    b = await message(ledger_db, "room-b", n=2)
    got = await ledger.apply(
        LedgerOp("open", "thread", subject="Travel", message_ids=(a,)),
        target=target(),
        shown=frame(a),
        now_ms=9000,
    )
    entry = got.entry
    assert entry.body == "" and entry.last_moved_at == 1000
    got = await ledger.apply(
        LedgerOp("touch", "thread", id=entry.id, message_ids=(b,)),
        target=target(),
        shown=frame(b),
        now_ms=10000,
    )
    assert got.entry.scope == "room" and got.entry.last_moved_at == 2000
    assert len(got.entry.spans) == 2
    assert await ledger.read(unit="u", platform="signal", conversation_id="room-b") == ()
    promoted = await ledger.apply(
        LedgerOp("revise", "thread", id=entry.id, base=got.entry.revision, scope="unit"),
        target=target(),
        shown=frame(),
        now_ms=11000,
    )
    assert promoted.entry.scope == "unit"
    assert await ledger.read(unit="u", platform="signal", conversation_id="room-b") == (
        promoted.entry,
    )
    rows = await ledger.span_rows(promoted.entry.spans[0], limit=60)
    assert [r.id for r in rows] == [a]


def test_migration_round_trip_preserves_legacy_and_matches_metadata(pg_url):
    from nora.infra.sql.migrations_runner import sync_url_for

    cfg = alembic_config(pg_url)
    command.downgrade(cfg, "0132_turn_records")
    engine = sa.create_engine(sync_url_for(pg_url))
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO standing (platform,conversation_id,body,updated_at) "
                "VALUES ('signal','room-a','A legacy position',1000)"
            )
        )
        assert "standing_decision" not in sa.inspect(conn).get_table_names()
    command.upgrade(cfg, "head")
    command.check(cfg)
    with engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT body FROM standing")).scalar_one() == "A legacy position"
        )
    command.downgrade(cfg, "0132_turn_records")
    with engine.connect() as conn:
        assert "standing_thread_revision" not in sa.inspect(conn).get_table_names()
    command.upgrade(cfg, "head")
    engine.dispose()


def collaborators():
    from nora.agent.lineage import LineagePolicy
    from nora.domain.models import PersonRef
    from nora.persons import Persons
    from nora.policy import Conversation, Policy
    from nora.testing import FakeRosterSource

    kit = PersonRef("kit", "Kit", "kit")
    persons = Persons(by_person={"kit": kit}, index={("signal", "generated-account"): "kit"})
    policy = Policy(
        conversations=tuple(
            Conversation(
                "signal", frozenset({r}), frozenset({"kit", "ana"}), label=r, unit=u, primary_id=r
            )
            for r, u in (("room-a", "u"), ("room-b", "u"), ("room-c", "other"))
        )
    )
    object.__setattr__(
        policy, "_by_id", {(c.platform, cid): c for c in policy.conversations for cid in c.ids}
    )
    roster = FakeRosterSource(
        groups={r: (frozenset({"generated-account"}),) for r in ("room-a", "room-b", "room-c")}
    )
    return persons, policy, roster, LineagePolicy.from_policy(policy, mode="off")


async def toolset(ledger, ledger_db, *, room_id="room-a", context=None, tagger=None):
    from nora.agent.room import Room
    from nora.agent.tools import build_tools
    from nora.engagement.self_presence import PresenceReader
    from nora.infra.sql.standing import SqlStandingStore
    from nora.infra.transit import TransitClient
    from nora.testing import FakeAdapter, FakeMemoryStore
    from tests.fakes import SinkSender

    persons, policy, roster, lineage = context or collaborators()
    directory = PresenceReader(
        policy=policy, adapters={"signal": FakeAdapter(account="self")}, rosters={"signal": roster}
    )
    tools = build_tools(
        store=SqlRecordStore(ledger_db),
        conversation_id=room_id,
        conversation_type="group",
        persons=persons,
        honcho=FakeMemoryStore(),
        deliverer=SinkSender(),
        metrics=Metrics(),
        room=Room(
            platform="signal", conversation_id=room_id, roster_source=roster, persons=persons,
            tagger=tagger,
        ),
        transit_source=TransitClient(
            kmb_base_url="http://127.0.0.1:1",
            mtr_schedule_url="http://127.0.0.1:1/s",
            mtr_routes_url="http://127.0.0.1:1/r",
            timeout_seconds=0.1,
        ),
        rooms=directory,
        lineage=lineage,
        ledger=ledger,
        standing_head=SqlStandingStore(ledger_db),
    )
    return {t.tool_name: t._tool_func for t in tools}


async def test_thread_arguments_read_spans_beyond_the_prompt_window(ledger, ledger_db):
    a = await message(ledger_db, n=1)
    middle = await message(ledger_db, n=2)
    b = await message(ledger_db, n=3)
    other = await message(ledger_db, "room-b", n=4)
    got = await ledger.apply(
        LedgerOp("open", "thread", subject="Travel", scope="unit", message_ids=(a, b, other)),
        target=target(),
        shown=frame(a, b, other),
        now_ms=5000,
    )
    assert got.entry is not None
    for n in range(5, 70):
        await message(ledger_db, n=n)
    tools = await toolset(ledger, ledger_db)
    local = await tools["load_transcript"](thread=got.entry.id)
    assert len(local["messages"]) == 3
    assert [m["body"] for m in local["messages"]] == [
        "Generated topic 1",
        "Generated topic 2",
        "Generated topic 3",
    ]
    assert middle not in got.entry.message_ids  # the span opens context between selected rows too
    remote = await tools["room_read"](room="room-b", thread=got.entry.id)
    assert remote["status"] == "ok" and len(remote["messages"]) == 1
    assert remote["messages"][0]["body"] == "Generated topic 4"
    assert await tools["load_transcript"](thread="th-absent") == (
        await (await toolset(ledger, ledger_db, room_id="room-c"))["load_transcript"](
            thread=got.entry.id
        )
    )
    assert await tools["room_read"](room="room-b", thread="th-absent") == (
        await tools["room_read"](room="room-b", thread="th-foreign")
    )


@pytest.mark.parametrize("reader", ["load_transcript", "room_read"])
@pytest.mark.parametrize("blocked", ["lineage", "unit", "unavailable", "degraded", "unnamed"])
async def test_thread_read_unavailable_is_not_an_empty_record(ledger, ledger_db, reader, blocked):
    a = await message(ledger_db)
    b = await message(ledger_db, "room-b", n=2)
    got = await ledger.apply(
        LedgerOp("open", "thread", subject="Generated topic", scope="unit", message_ids=(a, b)),
        target=target(), shown=frame(a, b), now_ms=3000,
    )
    assert got.entry is not None
    kwargs = {"room": "room-b"} if reader == "room_read" else {}
    allowed = (await toolset(ledger, ledger_db))[reader]
    assert len((await allowed(thread=got.entry.id, **kwargs))["messages"]) == 1
    absent = await allowed(thread="th-absent", **kwargs)
    if reader == "room_read":
        assert absent["status"] == "nothing_found"
    else:
        assert absent["messages"] == [] and "note" not in absent

    persons, policy, roster, lineage = collaborators()
    tagger = None
    if blocked == "lineage":
        # A named listener outside the source audience denies disclosure.
        roster.groups["room-a"] += (frozenset({"generated-extra"}),)
        persons = replace(persons, index={**persons.index, ("signal", "generated-extra"): "extra"},
                          by_person={**persons.by_person,
                                     "extra": replace(persons.by_person["kit"], person_id="extra")})
    elif blocked == "unit":
        lineage = replace(lineage, _by_id={})
    elif blocked in {"unavailable", "degraded"}:
        roster.fail = True
        tagger = persons.by_person["kit"] if blocked == "degraded" else None
    else:
        roster.groups["room-a"] += (frozenset({"generated-unknown"}),)
    tools = await toolset(ledger, ledger_db, context=(persons, policy, roster, lineage),
                          tagger=tagger)
    result = await tools[reader](thread=got.entry.id, **kwargs)
    assert result.get("status") == "unavailable"
    assert result.get("messages", []) == []
    assert got.entry.id not in str(result)
    assert "on record" not in result["note"]
    if blocked != "lineage":
        assert await tools[reader](thread="th-absent", **kwargs) == result


@pytest.mark.parametrize("reader", ["load_transcript", "room_read"])
async def test_thread_paging_keeps_a_same_second_group_together(ledger, ledger_db, reader):
    room_id = "room-b" if reader == "room_read" else "room-a"
    ids = [await message(ledger_db, room_id, n=n) for n in (1, 2, 2, 3, 4)]
    got = await ledger.apply(
        LedgerOp("open", "thread", subject="Generated topic", scope="unit",
                 message_ids=(ids[0], ids[-1])),
        target=target(), shown=frame(*ids), now_ms=5000,
    )
    assert got.entry is not None
    tools = await toolset(ledger, ledger_db)
    kwargs = {"room": room_id} if reader == "room_read" else {}
    newest = await tools[reader](thread=got.entry.id, limit=3, **kwargs)
    assert [m["body"] for m in newest["messages"]] == ["Generated topic 3", "Generated topic 4"]
    before = newest["note"].split('before="')[1].split('"')[0]
    oldest = await tools[reader](thread=got.entry.id, limit=3, before=before, **kwargs)
    assert [m["body"] for m in oldest["messages"]] == [
        "Generated topic 1", "Generated topic 2", "Generated topic 2",
    ]
    assert "note" not in oldest


async def test_standing_read_keeps_all_entries_and_history_outside_render_budget(ledger, ledger_db):
    from datetime import UTC, datetime

    from nora.agent.standing_ledger import render_ledger

    entry, id = await plant(ledger, ledger_db)
    changed = await ledger.apply(
        LedgerOp("close", "decision", id=entry.id, base=entry.revision, outcome=f"Settled [{id}]"),
        target=target(),
        shown=frame(id),
        now_ms=2000,
    )
    rendered = render_ledger(
        (changed.entry,), now=datetime(2026, 10, 1, tzinfo=UTC), section_tokens=1
    )
    assert "+1 older decisions" in rendered.body
    got = await (await toolset(ledger, ledger_db))["standing_read"]()
    assert got["status"] == "ok"
    assert got["entries"][0]["revision"] == 2
    assert [h["revision"] for h in got["entries"][0]["history"]] == [1, 2]
    assert got["entries"][0]["body"] == changed.entry.body


async def test_context_assembly_renders_ledger_beside_legacy_paragraph(
    ledger, ledger_db, example_config
):
    from nora.agent.prefill import build_prefill
    from nora.app.assembler import build_assembler
    from nora.domain.models import NormalizedMessage
    from nora.domain.standing import Standing
    from nora.infra.sql.standing import SqlStandingStore
    from nora.testing import FakeMemoryStore

    entry, _ = await plant(ledger, ledger_db)
    legacy = SqlStandingStore(ledger_db)
    await legacy.write(standing=Standing("signal", "room-b", "A legacy paragraph", 1, 1000))
    persons, policy, roster, _ = collaborators()
    assembler = build_assembler(
        example_config,
        audit=SqlRecordStore(ledger_db),
        memory=FakeMemoryStore(),
        persons=persons,
        policy=policy,
        metrics=Metrics(),
        ledger=ledger,
        standing=legacy,
        rosters={"signal": roster},
    )
    msg = NormalizedMessage(
        "signal", "room-b", "group", "generated-account", "Kit", 9000, "Continue", {}
    )
    # Use the repo's ordinary request builder; no separate prompt construction.
    from tests.unit.test_engagement_context import _request

    ctx = await assembler.assemble(request=_request(msg))
    text = str(build_prefill(ctx, display_name="Nora", context_window=20))
    assert "A legacy paragraph" in text and entry.id in text and "## Decisions" in text
    assert "r1" in text and "Options:" in text


async def test_thread_has_no_body_and_whole_standing_writes_are_refused(ledger, ledger_db):
    id = await message(ledger_db)
    got = await ledger.apply(
        LedgerOp(
            "open",
            "thread",
            subject="Travel",
            message_ids=(id,),
            sections={"Options": f"A summary [{id}]"},
        ),
        target=target(),
        shown=frame(id),
        now_ms=1000,
    )
    assert got.refusal.reason == "unknown_section"
    got = await ledger.apply(
        {"where_it_stands": "A whole replacement"},  # type: ignore[arg-type]
        target=target(),
        shown=frame(id),
        now_ms=1000,
    )
    assert got.refusal.reason == "not_an_op"
    assert await ledger.read(unit="u", platform="signal", conversation_id="room-a") == ()


@pytest.mark.parametrize("kind", ["fire", "participation"])
async def test_fire_and_participation_render_unit_ledger_in_other_room(
    ledger,
    ledger_db,
    doctrine_store,
    kind,
):
    from nora.agent.prefill import build_prefill
    from nora.app.fire import FirePath
    from nora.domain.events import Event
    from nora.testing import FakeMemoryStore

    entry, _ = await plant(ledger, ledger_db)
    persons, policy, roster, lineage = collaborators()
    from datetime import UTC, datetime

    path = FirePath(
        persons=persons,
        records=SqlRecordStore(ledger_db),
        memory=FakeMemoryStore(),
        doctrine_store=doctrine_store,
        metrics=Metrics(),
        runner=None,
        adapters={},
        registry=None,
        canary="test-canary",
        display_name="Nora",
        ai_peer="nora",
        context_window=20,
        limits={},
        timeout_seconds=5,
        clock=lambda: datetime(2026, 10, 1, tzinfo=UTC),
        policy=policy,
        rosters={"signal": roster},
        lineage=lineage,
        ledger=ledger,
    )
    occasion = Event(
        platform="signal",
        conversation_id="room-b",
        conversation_type="group",
        person_id=None,
        body="Continue",
        header=lambda _p, _n: "Continue",
        kind=kind,
    )
    ctx = await path.build_context(occasion)
    assert entry.id in str(build_prefill(ctx, display_name="Nora", context_window=20))


async def test_revising_one_section_preserves_evidence_after_source_retention(ledger, ledger_db):
    from nora.domain.tag_trace import TagTrace
    from nora.infra.sql.schema import TAG_TRACE
    from nora.infra.sql.tag_traces import SqlTagTraceStore

    traces = SqlTagTraceStore(ledger_db)
    await traces.save(
        TagTrace(
            tag_id="generated-turn",
            conversation_id="room-a",
            messages=(),
            transcript=(),
            outcome="send_reply",
            created_at=1000,
            unit="u",
            platform="signal",
        )
    )
    trace = (await traces.recent(conversation_id="room-a", limit=1))[0]
    opened = await ledger.apply(
        LedgerOp(
            "open",
            "decision",
            subject="A choice",
            scope="unit",
            sections={"Options": f"Option A turn({trace.handle})", "Leaning": "none yet"},
        ),
        target=target(),
        shown=Shown(turn_handles=frozenset({trace.handle})),
        now_ms=1000,
    )
    assert opened.entry is not None
    async with ledger_db.begin() as conn:
        await conn.execute(sa.delete(TAG_TRACE).where(TAG_TRACE.c.tag_id == "generated-turn"))
    id = await message(ledger_db, n=2)
    revised = await ledger.apply(
        LedgerOp(
            "revise",
            "decision",
            id=opened.entry.id,
            base=opened.entry.revision,
            sections={"Leaning": f"Option A [{id}]"},
        ),
        target=target(),
        shown=frame(id),
        now_ms=3000,
    )
    assert revised.status == "ok", revised.refusal
    assert f"- Option A turn({trace.handle})" in revised.entry.body
    assert revised.entry.lineage == opened.entry.lineage


async def test_standing_read_records_each_entrys_source_requirements(ledger, ledger_db):
    from types import SimpleNamespace

    from nora.agent.turn_recording import TurnRecording
    from nora.domain.lineage import ROW

    entry, _ = await plant(ledger, ledger_db)
    tool = (await toolset(ledger, ledger_db, room_id="room-b"))["standing_read"]
    recording = TurnRecording()
    recording.before(SimpleNamespace(tool_use={"toolUseId": "ledger-read"}))
    try:
        result = await tool()
    finally:
        recording.after(None)
    assert result["status"] == "ok"
    requirements = recording.requirements["ledger-read"]
    assert all(r.known for r in requirements)
    assert any(r.entries == entry.lineage.section(ROW) for r in requirements)


async def test_room_list_threads_are_topics_by_room_newest_first(ledger, ledger_db):
    from types import SimpleNamespace

    from nora.agent.turn_recording import TurnRecording

    ids = [await message(ledger_db, r, n=n) for r, n in
           (("room-a", 1), ("room-b", 2), ("room-a", 3), ("room-c", 4))]
    for subject, scope, room, id in (
        ("Older topic", "unit", "room-a", ids[0]),
        ("Other room topic", "unit", "room-a", ids[1]),
        ("Newer topic", "room", "room-a", ids[2]),
        ("Other unit topic", "room", "room-c", ids[3]),
    ):
        got = await ledger.apply(
            LedgerOp("open", "thread", subject=subject, scope=scope, message_ids=(id,)),
            target=target(room), shown=frame(id), now_ms=9000,
        )
        assert got.entry is not None, got.refusal
    # An unrelated entry is not a topic preview.
    await plant(ledger, ledger_db)
    tools = await toolset(ledger, ledger_db)
    recording = TurnRecording()
    recording.before(SimpleNamespace(tool_use={"toolUseId": "room-topics"}))
    try:
        got = await tools["room_list"]()
    finally:
        recording.after(None)
    assert got["status"] == "ok"
    rooms = {r["room"]: r for r in got["rooms"]}
    assert set(rooms) == {"room-a", "room-b"}
    assert rooms["room-a"]["threads"] == {
        "status": "ok", "topics": ["Newer topic", "Older topic"],
    }
    assert rooms["room-b"]["threads"] == {
        "status": "ok", "topics": ["Other room topic"],
    }
    assert "Other unit topic" not in str(got)
    assert len(recording.requirements["room-topics"]) == 4
    assert all(r.known for r in recording.requirements["room-topics"])


@pytest.mark.parametrize("blocked", ["withheld", "unwired", "failed", "roster"])
async def test_room_list_never_calls_an_unreadable_ledger_empty(
    ledger, ledger_db, monkeypatch, blocked,
):
    tools = await toolset(ledger, ledger_db)
    empty = await tools["room_list"]()
    assert all(r["threads"]["status"] == "nothing_found" for r in empty["rooms"])
    id = await message(ledger_db, "room-b")
    got = await ledger.apply(
        LedgerOp("open", "thread", subject="Withheld topic", scope="room", message_ids=(id,)),
        target=target("room-b"), shown=frame(id), now_ms=1000,
    )
    assert got.entry is not None
    context = collaborators()
    if blocked == "unwired":
        ledger = None
    elif blocked == "failed":
        read = ledger.read

        async def read_room(*, unit, platform, conversation_id):
            if conversation_id == "room-b":
                raise RuntimeError("ledger unavailable")
            return await read(unit=unit, platform=platform, conversation_id=conversation_id)

        monkeypatch.setattr(ledger, "read", read_room)
    elif blocked == "roster":
        context[2].fail = True
    result = await (await toolset(ledger, ledger_db, context=context))["room_list"]()
    previews = {r["room"]: r["threads"] for r in result["rooms"]}
    assert previews["room-b"]["status"] == "unavailable"
    assert previews["room-b"]["note"]
    assert "topics" not in previews["room-b"]
    assert "Withheld topic" not in str(result)
    if blocked in {"withheld", "failed"}:
        assert previews["room-a"]["status"] == "nothing_found"
