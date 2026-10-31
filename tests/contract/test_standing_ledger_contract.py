"""`InMemoryStandingLedger` vs `SqlStandingLedger`: the shared contract
`nora/testing`'s fakes are bound to — same ops, same refusals, same CAS."""

import pytest

from nora.domain.lineage import RenderRoom, RoomFacts
from nora.domain.note_body import Shown
from nora.domain.standing_ledger import LedgerOp
from nora.domain.tag_trace import TagTrace
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.standing_ledger import SqlStandingLedger
from nora.infra.sql.tag_traces import SqlTagTraceStore
from nora.obs.metrics import Metrics
from nora.testing.standing_ledger import InMemoryStandingLedger


@pytest.fixture(params=["sql", "fake"])
async def ledger(request):
    """Yields (store, saved Record handle): `turn(<handle>)` resolves on the
    SQL store because a real trace row was saved; the fake validates it
    against `shown.turn_handles` alone."""
    if request.param == "sql":
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        engine = create_sql_engine(pg_url)
        s = SqlStandingLedger(engine, metrics=Metrics())
        traces = SqlTagTraceStore(engine)
        from nora.domain.lineage import message as origin_message
        from nora.domain.turn_record import DisclosureRequirement
        req = DisclosureRequirement(
            "signal:room-a", (origin_message("signal:room-a"),),
        )
        await traces.save(TagTrace(
            tag_id="contract-session", conversation_id="room-a",
            messages=({
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "call",
                                         "name": "source", "input": {}}}],
            },),
            transcript=(), outcome="silent", created_at=1000,
            occasion="cognition", unit="u", platform="signal",
            bound_rooms=("signal:room-a",),
            requirements={"call": [req.to_json()]},
        ))
        saved = (await traces.recent(conversation_id="room-a", limit=1))[0]
        yield s, saved.handle
        await engine.dispose()
    else:
        yield InMemoryStandingLedger(), "t1"


def _shown(handle):
    return Shown(turn_handles=frozenset({handle}))


def _target():
    return RenderRoom(room=RoomFacts(
        platform="signal", ids=frozenset({"room-a"}), unit="u",
        persons=frozenset(), primary_id="room-a"))


async def test_open_with_status_is_refused(ledger):
    ledger, handle = ledger
    got = await ledger.apply(
        LedgerOp("open", "decision", scope="unit", subject="x",
                 status="open"),
        target=_target(), shown=_shown(handle), now_ms=1000)
    assert got.refusal is not None and got.refusal.reason == "bad_status"


async def test_open_missing_required_sections_is_refused(ledger):
    ledger, handle = ledger
    got = await ledger.apply(
        LedgerOp("open", "decision", scope="unit", subject="x"),
        target=_target(), shown=_shown(handle), now_ms=1000)
    assert got.refusal is not None
    assert got.refusal.reason == "missing_required_section"


async def test_open_then_close_under_the_issued_id(ledger):
    ledger, handle = ledger
    if isinstance(ledger, SqlStandingLedger):
        pytest.skip(
            "evidence resolution needs a cited turn — covered by the real-"
            "store fold tests in tests/infra/test_standing_ledger.py")
    opened = await ledger.apply(
        LedgerOp("open", "decision", scope="unit", subject="which venue",
                 sections={"Options": f"a, b turn({handle})", "Leaning": f"a turn({handle})"}),
        target=_target(), shown=_shown(handle), now_ms=1000)
    assert opened.entry is not None
    closed = await ledger.apply(
        LedgerOp("close", "decision", id=opened.entry.id,
                 base=opened.entry.revision, outcome=f"chose a turn({handle})"),
        target=_target(), shown=_shown(handle), now_ms=2000)
    assert closed.entry is not None and closed.entry.status == "closed"
    assert closed.entry.revision == opened.entry.revision + 1


async def test_revise_with_a_stale_base_is_refused(ledger):
    ledger, handle = ledger
    if isinstance(ledger, SqlStandingLedger):
        pytest.skip("as above — the open it builds on needs _evidence")
    opened = await ledger.apply(
        LedgerOp("open", "decision", scope="unit", subject="x",
                 sections={"Options": f"a turn({handle})", "Leaning": f"a turn({handle})"}),
        target=_target(), shown=_shown(handle), now_ms=1000)
    got = await ledger.apply(
        LedgerOp("revise", "decision", id=opened.entry.id, base=99,
                 subject="x", sections={"Leaning": f"b turn({handle})"}),
        target=_target(), shown=_shown(handle), now_ms=2000)
    assert got.refusal is not None and got.refusal.reason == "stale_base"


async def test_revise_an_unknown_entry_is_refused(ledger):
    ledger, handle = ledger
    got = await ledger.apply(
        LedgerOp("revise", "decision", id="nope", base=1,
                 subject="x", sections={"Leaning": f"b turn({handle})"}),
        target=_target(), shown=_shown(handle), now_ms=1000)
    assert got.refusal is not None and got.refusal.reason in (
        "unknown_entry", "missing_base")
