"""The act receipt rides the register row's own transaction.

A crash after a create verb's commit but before the receipt used to lose the
idempotency key and let a retry write a second row. This probe drives a
real session over the SQL register store and receipt table. A receipt-boundary
crash rolls the row back atomically, a later post-commit crash leaves row and
receipt, and a restart's identical retry
replays that receipt instead of writing a second row, then re-links the same
register association. Generated data only.
"""
import pytest
import sqlalchemy as sa

from nora.infra.sql.comprehend import SqlComprehendRecords
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import COMPREHEND_RESULT as RESULT
from nora.infra.sql.schema import NOTE as NOTE_TABLE
from nora.obs.metrics import Metrics
from nora.testing.memory_graph import FakeMemoryGraph
from tests.unit.cognition_rig import GOOD_BODY, NOTE, NOW, RECORDS, Stores
from tests.unit.cognition_rig import session as rig_session
from tests.unit.test_cognition_session_runner import _about_proposal, _seed_event

pytestmark = pytest.mark.anyio


async def _count(conn, table, *where) -> int:
    stmt = sa.select(sa.func.count()).select_from(table)
    if where:
        stmt = stmt.where(*where)
    return int((await conn.execute(stmt)).scalar_one())


async def test_receipt_boundary_crash_restart_retry_does_not_duplicate(clean_pg_url):

    from nora.app.cognition_memory import WakeMemory
    from nora.domain.cognition_ground import Grounding
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Source
    from nora.testing.cognition_grounding import StaticGrounder

    engine = create_sql_engine(clean_pg_url)
    try:
        records = SqlComprehendRecords(
            engine, media_join=None,
            rooms_by_unit={"u": [("signal", "room-a")]},
            persons_by_unit={"u": {"p_ada"}},
        )
        real_load = records.load

        async def load(work):
            # The register row's own text plus its cited record source, the
            # shape the SQL loader returns; an unprojected stimulus answers
            # NoResultFound, exactly as the real load does.
            if work.source[0] == "note":
                return ComprehendItem(work, (
                    Source(work.source, "notebook", "u", (), GOOD_BODY),
                    Source(("message", "101"), "record", "u", ("p_ada",), "generated"),
                ), "signal", "room-a", snapshot={"messages": [], "context": []})
            return await real_load(work)

        records.load = load
        graph = FakeMemoryGraph()
        memory = WakeMemory(graph=graph, records=records, metrics=Metrics(), content_checks=False)
        stores = Stores(notes=SqlNoteStore(engine))
        event = _seed_event(memory, "u:event:generated-gala")
        answers = []

        async def behave(by, tools):
            await by["memory_propose"](**_about_proposal(event.id))
            answers.append(await by["note_create"](
                body=GOOD_BODY, associations=[["event", event.id]], **NOTE))

        kwargs = {"memory": memory,
                  "grounder": StaticGrounder(Grounding(records=dict(RECORDS)))}

        # Attempt 1: the receipt write crashes inside the register transaction.
        async def crash(conn, key, session, result):
            raise RuntimeError("generated crash at the receipt boundary")

        records.act_result_on = crash
        await rig_session(behave, stores=stores, runner_kwargs=kwargs)
        assert answers[-1]["status"] == "unavailable", answers[-1]
        async with engine.connect() as conn:
            assert await _count(conn, NOTE_TABLE) == 0
            assert await _count(conn, RESULT, RESULT.c.part == "act_result") == 0

        # Attempt 2: row + receipt commit; the projection step crashes after.
        records.act_result_on = SqlComprehendRecords.act_result_on

        async def link_outage(state, result, *, targets=()):
            raise RuntimeError("generated projection outage")

        memory.link_result = link_outage
        await rig_session(behave, stores=stores, runner_kwargs=kwargs)
        assert answers[-1]["status"] == "unavailable", answers[-1]
        async with engine.connect() as conn:
            assert await _count(conn, NOTE_TABLE) == 1
            assert await _count(conn, RESULT, RESULT.c.part == "act_result") == 1

        # Attempt 3 (restart): the identical call replays the receipt, never
        # writes a second row, and re-links the same register row.
        del memory.link_result
        await rig_session(behave, stores=stores, runner_kwargs=kwargs)
        assert answers[-1]["status"] == "ok", answers[-1]
        async with engine.connect() as conn:
            assert await _count(conn, NOTE_TABLE) == 1
            assert await _count(conn, RESULT, RESULT.c.part == "act_result") == 1
        note_id = answers[-1]["note_id"]
        assert any(
            r.src == ("note", note_id) and r.rel == "concerns" and r.dst == event.key
            for r in graph.relations.values()
        )
    finally:
        await engine.dispose()


async def test_revision_receipt_commits_inside_the_append_transaction(clean_pg_url):
    """A revision's receipt rides the same append transaction: a crash leaves
    the head unmoved and no receipt; the retry writes revision + receipt."""
    from nora.infra.sql.schema import NOTE, NOTE_REVISION

    engine = create_sql_engine(clean_pg_url)
    try:
        notes = SqlNoteStore(engine)
        records = SqlComprehendRecords(
            engine, media_join=None,
            rooms_by_unit={"u": [("signal", "room-a")]},
            persons_by_unit={"u": {"p_ada"}},
        )
        note = await notes.create(
            unit="u", title="generated", description="d", uncertainty="q",
            closing_condition="c", established_in="signal:room-a",
            body="generated body", established_by="nora", now_ms=NOW,
            anchor_date=20251101,
        )

        async def crash(conn, revision):
            raise RuntimeError("generated crash at the receipt boundary")

        with pytest.raises(RuntimeError, match="generated crash"):
            await notes.append_revision(
                unit="u", note_id=note.id, body="generated revision",
                parent_revision_id=note.head_revision_id, established_by="nora",
                now_ms=NOW, receipt=crash)
        async with engine.connect() as conn:
            assert await _count(
                conn, NOTE_REVISION,
                NOTE_REVISION.c.body == "generated revision") == 0
            assert (await conn.execute(sa.select(NOTE.c.head_revision_id)
                    .where(NOTE.c.id == note.id))).scalar_one() == note.head_revision_id
            assert await _count(conn, RESULT, RESULT.c.part == "act_result") == 0

        async def write(conn, revision):
            await records.act_result_on(
                conn, "generated-revise-key", 1,
                {"status": "ok", "revision_id": revision.id})

        revision = await notes.append_revision(
            unit="u", note_id=note.id, body="generated revision",
            parent_revision_id=note.head_revision_id, established_by="nora",
            now_ms=NOW, receipt=write)
        async with engine.connect() as conn:
            assert await _count(
                conn, NOTE_REVISION,
                NOTE_REVISION.c.body == "generated revision") == 1
            assert await _count(conn, RESULT, RESULT.c.part == "act_result") == 1
            assert revision.id != note.head_revision_id
    finally:
        await engine.dispose()
