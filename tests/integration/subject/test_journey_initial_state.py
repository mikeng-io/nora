"""The live-clock journey's declared initial state, on real Postgres.

`evals.backbone._drive_journey` opens the case's store through
`nora.subject.journey_state`: a `snapshot:` on a live-clock case restores
every restorable store as of the declared instant ONCE (`sandbox_as_of`)
before composition; no `snapshot:` keeps the generated blank world
(`sandbox_database`); and a malformed declaration, a restore request with
no anchor, or a world that disagrees with the declared room refuses
before the App or any model runs. The controls are real restores over
generated rows — the post-cut change is dated unambiguously AFTER the
cut in `row_history`, so the read needs no sleep and no wall-clock luck.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from opentelemetry import trace
from strands_evals import Case
from strands_evals.types.evaluation import EvaluationData

from evals import backbone
from evals.backbone import Spec, state_of
from evals.rebuilt.domain import Outcome
from evals.rebuilt.entry import ENTRY_POINTS, EntryPoint
from nora.infra.sql import create_sql_engine
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import RunEvidence, read_events, validate_events
from nora.subject.journey_state import JourneyState, journey_initial_state
from nora.subject.sandbox import eval_host_lock

ROOM = ("signal", "g-anchor")
OTHER = ("signal", "g-elsewhere")


def _live_clock() -> datetime:
    return datetime.now(tz=UTC)


async def _aclose() -> None:
    pass


def _subject(room: tuple[str, str], database_url: str, *,
             clock: Any = _live_clock) -> SimpleNamespace:
    return SimpleNamespace(
        room=room, database_url=database_url,
        deps=SimpleNamespace(clock=clock), cfg=_fake_subject_cfg(),
        aclose=_aclose)


def _fake_subject_cfg() -> Any:
    return SimpleNamespace(
        computer=SimpleNamespace(enabled=False,
                                 browse=SimpleNamespace(enabled=False)),
        llm=SimpleNamespace(defer_tools=False),
        engagement=SimpleNamespace(trigger=SimpleNamespace(text="nora")))


async def _seed(clean_pg_url: str) -> dict[str, Any]:
    """Generated rows, a cut past the newest, and a line dated 60s after it."""
    engine = create_sql_engine(clean_pg_url)
    insert = (
        "INSERT INTO messages (platform, conversation_id, conversation_type, "
        "sender_id, sender_name, ts, body, is_nora, created_at) VALUES "
        "('signal', :room, 'group', '+852111', 'Generated', :ts, :b, 0, '') "
        "RETURNING id"
    )
    try:
        async with engine.begin() as conn:
            base = 1_750_000_000_000
            ids = [
                int((await conn.execute(sa.text(insert), {
                    "room": ROOM[1], "ts": base + i * 60_000,
                    "b": f"generated line {i}",
                })).scalar_one())
                for i in range(3)
            ]
            foreign = int((await conn.execute(sa.text(insert), {
                "room": OTHER[1], "ts": base + 120_000, "b": "another room's line",
            })).scalar_one())
            cut = (await conn.execute(sa.text(
                "SELECT max(recorded_at) + interval '1 millisecond' "
                "FROM row_history WHERE table_name = 'messages'"
            ))).scalar_one()
            late = int((await conn.execute(sa.text(insert), {
                "room": ROOM[1], "ts": base + 180_000,
                "b": "the post-cut line",
            })).scalar_one())
            await conn.execute(sa.text(
                "UPDATE row_history SET recorded_at = :after "
                "WHERE table_name = 'messages' AND (image->>'id')::bigint = :i"
            ), {"after": cut + timedelta(seconds=60), "i": late})
    finally:
        await engine.dispose()
    return {"ids": ids, "foreign": foreign, "late": late,
            "before_ms": int(cut.timestamp() * 1000)}


async def _room_rows(url: str, room: tuple[str, str] = ROOM) -> dict[int, Any]:
    engine = create_sql_engine(url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT id, ts, body FROM messages WHERE platform = :p "
                "AND conversation_id = :c ORDER BY id"
            ), {"p": room[0], "c": room[1]})).mappings().all()
            return {int(r["id"]): r for r in rows}
    finally:
        await engine.dispose()


def _snapshot(seed: dict[str, Any], room: tuple[str, str] = ROOM) -> dict[str, Any]:
    return {"platform": room[0], "conversation_id": room[1],
            "before_ms": seed["before_ms"]}


@pytest.fixture
def evidence_capture(tmp_path):
    backbone._prefill_capture()
    with eval_host_lock() as lock_fd:
        evidence = RunEvidence(
            run_id="generated-journey-initial", lock_fd=lock_fd,
            private_root=tmp_path / "capture",
            metadata={"behavior": "UNMEASURED"})
        with evidence.capture(trace.get_tracer_provider()):
            yield evidence
        assert validate_events(read_events(evidence.path))["status"] == "PASS"


async def test_the_declared_instant_is_restored_once_and_the_candidate_moves_on(
    clean_pg_url: str, pg_template: str, evidence_capture: Any,
) -> None:
    """The anchored path restores the room's pre-cut rows verbatim, excludes
    the post-cut line, keeps the foreign room's rows (whole-store scope),
    lets the candidate mutate its sandbox freely without touching the
    source, verifies, and records the cut and receipt in canonical
    evidence with the live clock beside it — distinct facts."""
    seed = await _seed(clean_pg_url)
    now = datetime.now(tz=UTC)
    with evidence_capture.case(
            "anchored-journey", input="x",
            metadata={"snapshot": _snapshot(seed)}):
        async with journey_initial_state(
            _snapshot(seed), case_name="anchored-journey",
            source_url=clean_pg_url, template=pg_template,
            full_restore=False, now=now,
        ) as state:
            rows = await _room_rows(state.database_url)
            assert set(rows) == set(seed["ids"]), (
                "the room's pre-cut ids are restored verbatim, nothing else")
            for position, i in enumerate(seed["ids"]):
                assert rows[i]["body"] == f"generated line {position}"
                assert rows[i]["ts"] == 1_750_000_000_000 + position * 60_000
            assert seed["late"] not in rows, "the post-cut change stays out"
            other = await _room_rows(state.database_url, OTHER)
            assert set(other) == {seed["foreign"]}, "scope is the whole store"

            await state.verify(_subject(ROOM, state.database_url))

            # The candidate's own writes land and persist — the restore is
            # initial state, not a read-only window.
            engine = create_sql_engine(state.database_url)
            try:
                async with engine.begin() as conn:
                    new_id = int((await conn.execute(sa.text(
                        "INSERT INTO messages (platform, conversation_id, "
                        "conversation_type, sender_id, sender_name, ts, body, "
                        "is_nora, created_at) VALUES ('signal', :room, "
                        "'group', '+852111', 'Generated', :ts, 'the "
                        "journey’s own line', 0, '') RETURNING id"
                    ), {"room": ROOM[1], "ts": seed["before_ms"] + 1})).scalar_one())
            finally:
                await engine.dispose()
            assert new_id in await _room_rows(state.database_url)
            source_rows = await _room_rows(clean_pg_url)
            assert source_rows[seed["late"]]["body"] == "the post-cut line", (
                "a sandbox write never reaches the source — ids may collide, "
                "the source's row stays the post-cut one")
        event = next(e for e in read_events(evidence_capture.path)
                     if e["kind"] == "journey_initial_state")
    assert event["declared"] is True
    assert event["platform"] == ROOM[0]
    assert event["conversation_id"] == ROOM[1]
    assert event["before_ms"] == seed["before_ms"]
    assert event["room_message_ids"] == sorted(seed["ids"])
    assert event["copied"]["messages"] >= len(seed["ids"]) + 1, (
        "the restore's own receipt counts the whole store, not only the room")
    assert event["live_start_ms"] >= event["before_ms"]
    assert event["subject_clock_ms"] > event["before_ms"], (
        "the live journey clock is honestly later than the declared cut")


async def test_verify_refuses_a_world_disagreeing_with_the_anchor(
    clean_pg_url: str, pg_template: str,
) -> None:
    """Every inconsistency between the composed subject and the declared
    anchor refuses: another room, another database, a clock stopped at the
    cut, a room the source never held, and a restore that lost a row —
    the positive check runs first, so the zero-assertions sit beside it."""
    seed = await _seed(clean_pg_url)
    now = datetime.now(tz=UTC)
    async with journey_initial_state(
        _snapshot(seed), case_name="anchored-journey",
        source_url=clean_pg_url, template=pg_template,
        full_restore=True, now=now,
    ) as state:
        await state.verify(_subject(ROOM, state.database_url))
        with pytest.raises(ValueError, match="anchor names"):
            await state.verify(_subject(OTHER, state.database_url))
        with pytest.raises(ValueError, match="restored into"):
            await state.verify(_subject(ROOM, clean_pg_url))
        frozen = datetime.fromtimestamp(seed["before_ms"] / 1000, tz=UTC)
        with pytest.raises(ValueError, match="at or before the declared cut"):
            await state.verify(
                _subject(ROOM, state.database_url, clock=lambda: frozen))
        engine = create_sql_engine(state.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(sa.text(
                    "DELETE FROM messages WHERE id = :i"),
                    {"i": seed["ids"][0]})
        finally:
            await engine.dispose()
        with pytest.raises(ValueError, match="identity"):
            await state.verify(_subject(ROOM, state.database_url))

    async with journey_initial_state(
        _snapshot(seed, room=("signal", "g-never")),
        case_name="anchored-journey",
        source_url=clean_pg_url, template=pg_template,
        full_restore=False, now=now,
    ) as empty:
        with pytest.raises(ValueError, match="held no row"):
            await empty.verify(
                _subject(("signal", "g-never"), empty.database_url))


@pytest.mark.parametrize("snapshot, match", [
    ("not-a-mapping", "must map"),
    ({}, "missing"),
    ({"platform": "signal", "conversation_id": "g1"}, "missing"),
    ({"platform": "signal", "conversation_id": "g1", "before_ms": 1, "extra": 2},
     "states"),
    ({"platform": "", "conversation_id": "g1", "before_ms": 1}, "non-empty"),
    ({"platform": "signal", "conversation_id": "g1", "before_ms": "later"},
     "integer"),
])
async def test_malformed_declarations_refuse_before_any_store_opens(
    snapshot: Any, match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        async with journey_initial_state(
            snapshot, case_name="bad-anchor",
            source_url="postgresql://u:p@h/unreached", template=None,
            full_restore=False, now=datetime.now(tz=UTC),
        ):
            raise AssertionError("a refused declaration never opens a store")


async def test_a_future_cut_and_an_anchorless_restore_both_refuse() -> None:
    now = datetime.now(tz=UTC)
    future = {"platform": "signal", "conversation_id": "g1",
              "before_ms": int(now.timestamp() * 1000) + 3_600_000}
    with pytest.raises(ValueError, match="future"):
        async with journey_initial_state(
            future, case_name="bad-anchor",
            source_url="postgresql://u:p@h/unreached", template=None,
            full_restore=True, now=now,
        ):
            raise AssertionError("a refused declaration never opens a store")
    with pytest.raises(ValueError, match="no `snapshot:`"):
        async with journey_initial_state(
            None, case_name="anchorless-restore",
            source_url="postgresql://u:p@h/unreached", template=None,
            full_restore=True, now=now,
        ):
            raise AssertionError("a refused declaration never opens a store")


async def test_the_generated_blank_world_stays_the_unanchored_path(
    clean_pg_url: str, pg_template: str,
) -> None:
    async with journey_initial_state(
        None, case_name="blank-journey", source_url=clean_pg_url,
        template=pg_template, full_restore=False, now=datetime.now(tz=UTC),
    ) as state:
        assert isinstance(state, JourneyState)
        assert state.anchor is None and state.report is None
        assert await _room_rows(state.database_url) == {}
        await state.verify(_subject(("signal", "anything"), state.database_url))


# --- the `_drive_journey` wiring: the case's declaration reaches the store ---


@contextlib.asynccontextmanager
async def _world(room: tuple[str, str]):
    """A plan-level stand-in for `JourneyServices.open`."""
    @contextlib.asynccontextmanager
    async def open_env(database_url: str, redis: bool = False):
        async with IsolatedServices(database_url=database_url, applications={}) as env:
            env.world = SimpleNamespace(
                subject_spec=lambda url, env, workdir: SimpleNamespace(
                    database_url=url, room=room, environment=env))
            yield env

    yield SimpleNamespace(open=open_env)


def _spec(clean_pg_url: str, pg_template: str, name: str, plan: Any,
          **kwargs: Any) -> Spec:
    return Spec(
        source_url=clean_pg_url, entry="autonomous_journey",
        services={name: plan}, template=pg_template, **kwargs)


async def test_the_drive_restores_the_declared_anchor_before_the_world_runs(
    clean_pg_url: str, pg_template: str, evidence_capture: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`backbone._drive` on an anchored live-clock case: the real restore
    fills the sandbox before `obtain`, `verify` passes, the drive sees the
    pre-cut rows (and only them), and a candidate write inside the drive
    persists without reaching the source."""
    seed = await _seed(clean_pg_url)
    name = "anchored-journey"
    case = Case(name=name, input="x", metadata={"snapshot": _snapshot(seed)})
    seen: dict[str, Any] = {}

    async def fake_obtain(spec_arg: Any) -> SimpleNamespace:
        return _subject(spec_arg.room, spec_arg.database_url)

    async def fake_drive(subject: Any, _case: Any, *, now_ms: int) -> Outcome:
        rows = await _room_rows(subject.database_url)
        seen["ids"] = sorted(rows)
        seen["live_ms"] = now_ms
        engine = create_sql_engine(subject.database_url)
        try:
            async with engine.begin() as conn:
                await conn.execute(sa.text(
                    "INSERT INTO messages (platform, conversation_id, "
                    "conversation_type, sender_id, sender_name, ts, body, "
                    "is_nora, created_at) VALUES ('signal', :room, 'group', "
                    "'+852111', 'Generated', :ts, 'the journey’s own line', "
                    "0, '')"), {"room": ROOM[1], "ts": now_ms})
        finally:
            await engine.dispose()
        seen["after"] = set(await _room_rows(subject.database_url))
        return Outcome(admitted=None)

    async with _world(ROOM) as plan:
        monkeypatch.setattr(backbone, "obtain", fake_obtain)
        monkeypatch.setitem(
            ENTRY_POINTS, "autonomous_journey",
            EntryPoint(drive=fake_drive, live_clock=True))
        result = await backbone._drive(
            case, _spec(clean_pg_url, pg_template, name, plan,
                        full_restore=True))

    assert seen["ids"] == sorted(seed["ids"])
    assert seed["late"] not in seen["ids"]
    assert seen["after"] - set(seed["ids"]), "the candidate write persisted"
    assert set(await _room_rows(clean_pg_url)) == set(seed["ids"]) | {seed["late"]}, (
        "the source is untouched by the candidate's sandbox")
    assert seen["live_ms"] >= seed["before_ms"]
    data = EvaluationData(
        input="x", actual_environment_state=result["environment_state"])
    assert state_of(data, backbone.NORA_ERROR) is None
    event = next(e for e in read_events(evidence_capture.path)
                 if e["kind"] == "journey_initial_state")
    assert event["declared"] is True
    assert event["room_message_ids"] == sorted(seed["ids"])


async def test_the_drive_refuses_a_world_room_mismatch_before_any_model(
    clean_pg_url: str, pg_template: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The world's room is not the anchor's room: `verify` refuses and the
    port never runs — the assertion inside the fake drive proves it."""
    seed = await _seed(clean_pg_url)
    name = "mismatched-journey"
    case = Case(name=name, input="x", metadata={"snapshot": _snapshot(seed)})

    async def fake_obtain(spec_arg: Any) -> SimpleNamespace:
        return _subject(spec_arg.room, spec_arg.database_url)

    async def forbidden_drive(subject: Any, _case: Any, *, now_ms: int) -> Outcome:
        raise AssertionError("a refused journey must never reach the drive")

    async with _world(OTHER) as plan:
        monkeypatch.setattr(backbone, "obtain", fake_obtain)
        monkeypatch.setitem(
            ENTRY_POINTS, "autonomous_journey",
            EntryPoint(drive=forbidden_drive, live_clock=True))
        with pytest.raises(ValueError, match="anchor names"):
            await backbone._drive_journey(
                case, _spec(clean_pg_url, pg_template, name, plan,
                            full_restore=True), recording=plan)


async def test_the_drive_keeps_the_blank_world_for_an_unanchored_case(
    clean_pg_url: str, pg_template: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "blank-journey"
    case = Case(name=name, input="x", metadata={})
    seen: dict[str, Any] = {}

    async def fake_obtain(spec_arg: Any) -> SimpleNamespace:
        return _subject(spec_arg.room, spec_arg.database_url)

    async def fake_drive(subject: Any, _case: Any, *, now_ms: int) -> Outcome:
        seen["ids"] = sorted(await _room_rows(subject.database_url))
        return Outcome(admitted=None)

    async with _world(ROOM) as plan:
        monkeypatch.setattr(backbone, "obtain", fake_obtain)
        monkeypatch.setitem(
            ENTRY_POINTS, "autonomous_journey",
            EntryPoint(drive=fake_drive, live_clock=True))
        await backbone._drive_journey(
            case, _spec(clean_pg_url, pg_template, name, plan,
                        full_restore=False), recording=plan)

    assert seen["ids"] == []
