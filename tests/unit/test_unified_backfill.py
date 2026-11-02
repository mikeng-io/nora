"""The unified backfill driver (`nora.app.unified_backfill`): enumeration,
batching, done-check, bounded apply, failure/parking and catch-up — all over
GENERATED inputs. Every test would fail if its behavior regressed; the
fakes answer like the real seams (a readings receipt only "exists" for the
exact (work_id, input_hash) it was written for, a failed session records
`fail_stimulus`, a parked stimulus is skipped)."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from nora.app import unified_backfill as ub
from nora.domain.cognition_session import Room
from nora.domain.stimuli import Stimulus

ROOM = Room("signal", "room-1")
HOME = {ROOM.platform: None, "u1": ROOM}


def _s(sid: int, at: int, *, kind: str = "message", item: str = "message",
       ref: str | None = None, unit: str = "u1",
       platform: str = "signal", cid: str = "room-1") -> Stimulus:
    return Stimulus(
        id=sid, unit=unit, kind=kind, item=item,
        ref=ref or f"messages:{sid}", at_ms=at, recorded_at_ms=at,
        platform=platform, conversation_id=cid, person_id="p1",
        cues=(), waits=False, due_ms=None,
    )


# -- fabricated ids ------------------------------------------------------------


def test_fabricated_ids_are_negative_and_deterministic():
    a = ub.sql.fabricated_id("message", "edit", "messages:5", 1000)
    b = ub.sql.fabricated_id("message", "edit", "messages:5", 1000)
    c = ub.sql.fabricated_id("message", "edit", "messages:5", 1001)
    assert a == b < 0
    assert c != a
    # A fabricated id can never collide with a real stimulus id (positive).
    assert all(
        ub.sql.fabricated_id(k, i, r, t) < 0
        for k, i, r, t in [("message", "message", "messages:1", 1),
                           ("social", "post", "social_read:x", 2)]
    )


# -- enumeration dedup ---------------------------------------------------------


def test_dedup_drops_what_a_recorded_stimulus_already_covers():
    keys = {
        ("message", "message", "messages:1"): {100},
        ("message", "edit", "messages:2"): {200},
        ("message", "reaction", "message_reaction:9"): {300},
        ("social", "post", "social_read:r1"): {400},
        ("observation", "poll", "platform_observation:7"): {500},
    }
    fabricated = [
        _s(-1, 100, ref="messages:1"),                                    # real covers
        _s(-2, 101, ref="messages:6"),                                    # new row → keep
        _s(-3, 200, item="edit", ref="messages:2"),                       # same edit → drop
        _s(-4, 201, item="edit", ref="messages:2"),                       # OTHER edit → keep
        _s(-5, 300, item="reaction", ref="message_reaction:9"),           # drop
        _s(-6, 400, kind="social", item="post", ref="social_read:r1"),    # drop
        _s(-7, 500, kind="observation", item="poll",
          ref="platform_observation:7"),                                  # drop
        _s(-8, 501, kind="observation", item="pin",
          ref="platform_observation:8"),                                  # keep
    ]
    kept = ub._deduped(fabricated, keys)
    assert [s.id for s in kept] == [-2, -4, -8]


# -- batching ------------------------------------------------------------------


def test_batches_are_chronological_prefixes_never_deferred():
    # 10 message stimuli in one room = ONE agenda item; kinds that group
    # separately push items past max_items and the batch trims, never defers.
    stimuli = [
        _s(i, i * 100, kind="message" if i % 3 else "social",
           item="post" if i % 3 == 0 else "message",
           ref=f"r{i}", platform="signal", cid=f"room-{i % 2}")
        for i in range(1, 11)
    ]
    batches = ub.form_batches(stimuli, ROOM, max_stimuli=8, max_items=3)
    flat = [s.id for b in batches for s in b]
    assert flat == [s.id for s in stimuli]  # nothing lost, nothing reordered
    for b in batches:
        assert len(b) <= 8
        agenda = __import__(
            "nora.domain.cognition_session", fromlist=["build_agenda"],
        ).build_agenda(b, home_room=ROOM, max_items=3)
        assert not agenda.deferred_stimulus_ids


# -- the apply loop ------------------------------------------------------------


class _FakeEngine:
    """The engine seam `run_units`/`census` need: `connect()` yields a fake
    conn whose `execute` is never reached — `sql.*` helpers are patched to
    return canned data in these tests, so a conn is a null object. A REAL
    engine's connect would write nothing in these tests either way."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def connect(self) -> Any:
        conn = self._conn

        class _Ctx:
            async def __aenter__(self) -> Any:
                return conn

            async def __aexit__(self, *a: Any) -> None:
                return None

        return _Ctx()


class _Runner:
    """Answers like `CognitionSessionRunner` in the historical lane:
    `inspect` returns the per-item work digests `on_wake`'s inspect branch
    builds, `replay` records that it was asked `historical=True` — the flag
    that withholds every act tool in the real runner."""

    def __init__(self, *, work_by_batch: list[list[dict[str, Any]]] | None = None,
                 fail_on: set[int] | None = None) -> None:
        self.max_items = 12
        self.inspect_calls: list[tuple[tuple[int, ...], int]] = []
        self.replay_calls: list[tuple[tuple[int, ...], int, bool]] = []
        self._work = work_by_batch or []
        self._fail_on = fail_on or set()
        self._n = 0

    async def inspect(self, *, view: Any, now_ms: int) -> dict[str, Any] | None:
        self.inspect_calls.append((tuple(s.id for s in view.pending), now_ms))
        if len(self.inspect_calls) - 1 in self._fail_on:
            raise RuntimeError("grounding failed")
        work = self._work[len(self.inspect_calls) - 1] \
            if len(self.inspect_calls) - 1 < len(self._work) else []
        return {"system": "s", "agenda": "a", "work": work} if work else None

    async def replay(self, *, view: Any, now_ms: int, historical: bool) -> Any:
        self.replay_calls.append(
            (tuple(s.id for s in view.pending), now_ms, historical))
        return object()


class _Records:
    """`SqlComprehendRecords`' seam: `fail_stimulus` appends a durable
    failure per stimulus — the parked mechanism `run_units` relies on."""

    def __init__(self) -> None:
        self.failures: list[tuple[str, int]] = []

    async def fail_stimulus(self, unit: str, sid: int, session: Any) -> None:
        self.failures.append((unit, sid))


def _patch_sql(monkeypatch: Any, *, stimuli: list[Stimulus],
               failures: dict[int, int] | None = None,
               readings: dict[str, str] | None = None) -> None:
    """`nora.infra.sql.unified_backfill`'s reads, answered over the canned
    stimulus list — the same shapes the real functions return."""
    async def _enum(conn: Any, unit: str, **kw: Any) -> tuple[list[Stimulus], dict[str, int]]:
        return [s for s in stimuli if s.unit == unit], {}

    async def _failures(conn: Any, unit: str, ids: Any) -> dict[int, int]:
        return {sid: (failures or {}).get(sid, 0) for sid in ids}

    async def _done(conn: Any, work_ids: Any) -> dict[str, str]:
        return {w: h for w, h in (readings or {}).items() if w in work_ids}

    monkeypatch.setattr(ub, "enumerate_unit", _enum)
    monkeypatch.setattr(ub.sql, "failure_counts", _failures)
    monkeypatch.setattr(ub.sql, "readings_done", _done)


def _run_kwargs() -> dict[str, Any]:
    return {
        "units": ["u1"], "home_rooms": {"u1": ROOM},
        "rooms_of_unit": lambda u: {(ROOM.platform, ROOM.conversation_id)},
        "unit_of": lambda p, c: "u1",
        "person_rooms": {}, "self_ids": (),
        "bounds": ub.Bounds(max_batches=10, max_tokens=10**9, max_seconds=3600),
    }


@pytest.mark.asyncio
async def test_every_item_is_run_once_oldest_first_and_historical(
        monkeypatch: Any):
    stimuli = [_s(1, 100), _s(2, 200), _s(3, 300)]
    work = [[{"index": 0, "work_id": "stimuli:1", "input_hash": "h1"}],
            [{"index": 0, "work_id": "stimuli:2,3", "input_hash": "h2"}]]
    runner = _Runner(work_by_batch=work)
    records = _Records()
    _patch_sql(monkeypatch, stimuli=stimuli)
    out = await ub.run_units(
        _FakeEngine(object()), runner=runner, records=records,
        batch_stimuli=2, **_run_kwargs())
    assert [tuple(ids) for ids, _ in runner.inspect_calls] == [(1, 2), (3,)]
    # Sessions got the batch's evidence instant, oldest first.
    assert [ms for _, ms, h in runner.replay_calls] == [200, 300]
    assert all(h for _, _, h in runner.replay_calls)
    assert records.failures == []
    assert out["units"]["u1"]["sessions"] == 2


@pytest.mark.asyncio
async def test_done_items_are_skipped_and_changed_input_reruns(
        monkeypatch: Any):
    # Item 'stimuli:1,2' already has a readings receipt at the CURRENT
    # hash → its stimuli are dropped and no model call is spent on them.
    stimuli = [_s(1, 100), _s(2, 200)]
    work = [{"index": 0, "work_id": "stimuli:1,2", "input_hash": "hNEW"}]
    runner = _Runner(work_by_batch=[work])
    _patch_sql(monkeypatch, stimuli=stimuli,
               readings={"stimuli:1,2": "hNEW"})
    out = await ub.run_units(
        _FakeEngine(object()), runner=runner, records=_Records(),
        **_run_kwargs())
    assert not runner.replay_calls
    assert out["units"]["u1"]["skipped_done"] == 1

    # Same work id, receipt at an OLD hash (an edit landed, a reading
    # arrived) → the item is not skipped: it runs again.
    runner2 = _Runner(work_by_batch=[work])
    _patch_sql(monkeypatch, stimuli=stimuli,
               readings={"stimuli:1,2": "hOLD"})
    out2 = await ub.run_units(
        _FakeEngine(object()), runner=runner2, records=_Records(),
        **_run_kwargs())
    assert len(runner2.replay_calls) == 1


@pytest.mark.asyncio
async def test_apply_stops_at_the_batch_bound(monkeypatch: Any):
    stimuli = [_s(i, i * 100) for i in range(1, 7)]
    runner = _Runner(work_by_batch=[
        [{"index": 0, "work_id": "stimuli:1,2", "input_hash": "h"}],
        [{"index": 0, "work_id": "stimuli:3,4", "input_hash": "h"}],
        [{"index": 0, "work_id": "stimuli:5,6", "input_hash": "h"}],
    ])
    _patch_sql(monkeypatch, stimuli=stimuli)
    kw = _run_kwargs()
    kw["bounds"] = ub.Bounds(max_batches=1, max_tokens=10**9, max_seconds=3600)
    out = await ub.run_units(
        _FakeEngine(object()), runner=runner, records=_Records(),
        batch_stimuli=2, **kw)
    assert len(runner.replay_calls) == 1
    assert out["stopped"] == "max_batches"


@pytest.mark.asyncio
async def test_a_failed_session_is_recovered_and_parked_at_five(
        monkeypatch: Any):
    stimuli = [_s(1, 100)]
    records = _Records()
    _patch_sql(monkeypatch, stimuli=stimuli)
    runner = _Runner(work_by_batch=[
        [{"index": 0, "work_id": "stimuli:1", "input_hash": "h"}]],
        fail_on={0})
    out = await ub.run_units(
        _FakeEngine(object()), runner=runner, records=records, **_run_kwargs())
    assert out["units"]["u1"]["failures"] == 1
    assert records.failures == [("u1", 1)]

    # At the parked threshold the stimulus is never even inspected.
    runner2 = _Runner()
    _patch_sql(monkeypatch, stimuli=stimuli, failures={1: ub.sql.PARK_AFTER})
    out2 = await ub.run_units(
        _FakeEngine(object()), runner=runner2, records=records, **_run_kwargs())
    assert not runner2.inspect_calls and not runner2.replay_calls
    assert out2["units"]["u1"]["parked"] == 1


@pytest.mark.asyncio
async def test_catch_up_refuses_incomplete_and_queues_one_time_wake(
        monkeypatch: Any):
    appended: list[Any] = []

    class _Stimuli:
        async def append(self, *, stimulus: Any, recorded_at_ms: int) -> int:
            appended.append(stimulus)
            return 77

    async def _covered(conn: Any, unit: str) -> set[int]:
        return {1, 2}

    async def _failures(conn: Any, unit: str, ids: Any) -> dict[int, int]:
        return {sid: 0 for sid in ids}

    stimuli = [_s(1, 100), _s(2, 200)]

    async def _enum(conn: Any, unit: str, **kw: Any) -> tuple[list[Stimulus], dict]:
        return stimuli, {}

    monkeypatch.setattr(ub, "enumerate_unit", _enum)
    monkeypatch.setattr(ub, "covered_stimulus_ids", _covered)
    monkeypatch.setattr(ub.sql, "failure_counts", _failures)

    out = await ub.catch_up(
        _FakeEngine(object()), stimulus_store=_Stimuli(),
        units=["u1"], rooms_of_unit=lambda u: set(), unit_of=lambda p, c: "u1",
        person_rooms={}, self_ids=(), now_ms=86_400_000 * 10)
    assert out["units"]["u1"]["status"] == "queued"
    assert appended and appended[0].kind == "time"
    assert appended[0].item == ub.CATCH_UP_ITEM

    # One uncovered stimulus → refused, nothing appended.
    async def _covered_partial(conn: Any, unit: str) -> set[int]:
        return {1}

    monkeypatch.setattr(ub, "covered_stimulus_ids", _covered_partial)
    appended.clear()
    out2 = await ub.catch_up(
        _FakeEngine(object()), stimulus_store=_Stimuli(),
        units=["u1"], rooms_of_unit=lambda u: set(), unit_of=lambda p, c: "u1",
        person_rooms={}, self_ids=(), now_ms=86_400_000 * 10)
    assert out2["units"]["u1"]["status"] == "incomplete"
    assert not appended


# -- dry-run census ------------------------------------------------------------


@pytest.mark.asyncio
async def test_census_writes_nothing_and_builds_no_runner(monkeypatch: Any):
    """The dry-run guarantee: zero writes AND zero model calls. The positive
    control is the census signature itself — it takes no runner, no deps and
    no agent runner to invoke. `execute` records every statement so a stray
    write would trip the assert below (and the readonly engine refuses it in
    production)."""

    class _Conn:
        def __init__(self) -> None:
            self.statements: list[Any] = []

        async def execute(self, stmt: Any, *a: Any, **kw: Any) -> Any:
            self.statements.append(stmt)
            raise AssertionError(
                "census must reach the sql.* seams, never raw statements")

    conn = _Conn()

    async def _enum(conn: Any, unit: str, **kw: Any) -> tuple[list[Stimulus], dict]:
        return [_s(1, 100), _s(-1, 50, ref="messages:9")], {"message": 1}

    async def _failures(conn: Any, unit: str, ids: Any) -> dict[int, int]:
        return {1: 0, -1: 0}

    async def _covered(conn: Any, unit: str) -> set[int]:
        return {1}

    async def _lane(conn: Any, unit: str) -> dict[str, Any]:
        return {"sessions": 0, "first_ms": None, "last_ms": None, "parts": {}}

    monkeypatch.setattr(ub, "enumerate_unit", _enum)
    monkeypatch.setattr(ub.sql, "failure_counts", _failures)
    monkeypatch.setattr(ub, "covered_stimulus_ids", _covered)
    monkeypatch.setattr(ub.sql, "lane_counts", _lane)

    out = await ub.census(
        _FakeEngine(conn), units=["u1"],
        rooms_of_unit=lambda u: set(), unit_of=lambda p, c: "u1",
        person_rooms={}, self_ids=())
    u = out["units"]["u1"]
    assert u["enumerated"] == 2 and u["recorded_stimuli"] == 1
    assert u["fabricated"] == {"message": 1}
    assert u["attempted_stimuli"] == 1 and u["uncovered_stimuli"] == 1
    assert not conn.statements  # nothing was even issued


# -- the runner variant ---------------------------------------------------------


def test_historical_runner_withholds_every_read_and_keeps_collaborators():
    """The historical runner IS the live one with explicit deltas; a test
    spy records exactly which collaborators changed — read tools, the
    register/self/live seams and the agent runner, never doctrine, budgets,
    clocks or stores."""
    changes: dict[str, Any] = {}

    class _Live:
        def with_collaborators(self, **kw: Any) -> "_Live":
            changes.update(kw)
            return self

    runner = ub.historical_runner(_Live(), grounder="G", agent_runner="A")
    assert changes["grounder"] == "G" and changes["agent_runner"] == "A"
    assert changes["read_only_tools"] == frozenset()
    assert changes["read_tools_for"]("u", []) == []
    assert changes["shared"] is None and changes["standing_fold"] is None
    for untouched in ("doctrine_source", "budget", "store", "clock",
                      "notes", "events", "standing", "reminders"):
        assert untouched not in changes
    assert runner is not None
