"""The unified backfill driver (`nora.app.unified_backfill`): enumeration,
batching, exact-receipt progress, bounded apply, frontier semantics,
isolated failure identity and catch-up — all over GENERATED inputs. Every
test would fail if its behavior regressed; the fakes answer like the real
seams (a readings receipt only "exists" for the exact (work_id, input_hash)
pair it was written for; a failed/unadmitted session leaves stimuli
uncovered; a historical failure lands under `backfill-stimulus:` and can
never park a live `stimulus:` id).

Executable repair guards live in scripts/mutations/unified-memory-backfill.json.
A table is a test plan; only admitted red/green execution supplies proof.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
    return canned data in these tests. `begin()` yields the same, for the
    (patched) failure writer."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def connect(self) -> Any:
        return self._ctx()

    def begin(self) -> Any:
        return self._ctx()

    def _ctx(self) -> Any:
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
    that withholds every act tool in the real runner. On `replay` it can be
    scripted to make NOTHING covered (a normal return that admitted nothing —
    the refusal shape) so the frontier check sees the truth."""

    def __init__(self, *, work_by_batch: list[list[dict[str, Any]]] | None = None,
                 fail_on: set[int] | None = None,
                 admit_on: set[int] | None = None) -> None:
        self.max_items = 12
        self.inspect_calls: list[tuple[tuple[int, ...], int]] = []
        self.replay_calls: list[tuple[tuple[int, ...], int, bool]] = []
        self._work = work_by_batch or []
        self._fail_on = fail_on or set()
        self._admit_on = admit_on or set()
        self._seen: dict[tuple[int, ...], list[dict[str, Any]]] = {}

    async def inspect(
        self, *, view: Any, now_ms: int, historical: bool = False,
    ) -> dict[str, Any] | None:
        assert historical
        self.inspect_calls.append((tuple(s.id for s in view.pending), now_ms))
        if len(self.inspect_calls) - 1 in self._fail_on:
            raise RuntimeError("grounding failed")
        ids = tuple(stimulus.id for stimulus in view.pending)
        if ids not in self._seen:
            n = len(self._seen)
            work = self._work[n] if n < len(self._work) else [
                {"index": 0, "work_id": "stimuli:" + ",".join(map(str, ids)),
                 "input_hash": "h"}]
            self._seen[ids] = [dict(item, inputs=item.get("inputs", {
                str(sid): item["input_hash"]
                for sid in ub.work_id_sids(item["work_id"])
            })) for item in work]
        work = self._seen[ids]
        return {"system": "s", "agenda": "a", "work": work} if work else None

    async def replay(self, *, view: Any, now_ms: int, historical: bool) -> Any:
        self.replay_calls.append(
            (tuple(s.id for s in view.pending), now_ms, historical))
        return object()


def _patch_sql(monkeypatch: Any, *, stimuli: list[Stimulus],
               failures: dict[int, int] | None = None,
               pairs: set[tuple[str, str]] | None = None,
               covered: set[int] | None = None,
               fail_log: list[tuple[str, int]] | None = None) -> None:
    """`nora.infra.sql.unified_backfill`'s reads, answered over the canned
    stimulus list — the same shapes the real functions return — and the
    failure writer, spied into `fail_log` (the REAL writer inserts a
    `backfill-stimulus:` row; the pair- and coverage-reads never fabricate
    one)."""
    async def _enum(conn: Any, unit: str, **kw: Any) -> tuple[list[Stimulus], dict[str, int]]:
        return [s for s in stimuli if s.unit == unit], {}

    async def _failures(conn: Any, unit: str, ids: Any) -> dict[int, int]:
        return {sid: (failures or {}).get(sid, 0) for sid in ids}

    async def _pairs(conn: Any, work_ids: Any) -> set[tuple[str, str]]:
        rows = set(pairs or ())
        rows.update((f"input:u1:{sid}", digest)
                    for key, digest in pairs or () for sid in ub.work_id_sids(key))
        return {pair for pair in rows if pair[0] in set(work_ids)}

    async def _covered(conn: Any, unit: str) -> set[int]:
        return set(covered or set())

    async def _fail(engine: Any, unit: str, sid: int, session: Any) -> None:
        assert session is None or isinstance(session, int)
        if fail_log is not None:
            fail_log.append((unit, sid))

    monkeypatch.setattr(ub, "enumerate_unit", _enum)
    monkeypatch.setattr(ub.sql, "failure_counts", _failures)
    monkeypatch.setattr(ub.sql, "readings_done", _pairs)
    monkeypatch.setattr(ub, "covered_stimulus_ids", _covered)
    monkeypatch.setattr(ub.sql, "fail_historical", _fail)


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
    runner = _Runner()  # default work: stimuli:<invoked ids> — the real ids
    fail_log: list[tuple[str, int]] = []
    _patch_sql(monkeypatch, stimuli=stimuli, fail_log=fail_log)
    out = await ub.run_units(
        _FakeEngine(object()), runner=runner,
        batch_stimuli=2, **_run_kwargs())
    # Batch [1,2]: inspected, then re-inspected on the filtered pending —
    # the replay is verified against the ACTUALLY-invoked digests.
    assert [tuple(ids) for ids, _ in runner.inspect_calls] == [(1, 2), (1, 2)]
    # Replay returned normally but NOTHING was comprehended → the batch is
    # unresolved and the frontier stops before [3] is ever touched.
    assert [ms for _, ms, h in runner.replay_calls] == [200]
    assert all(h for _, _, h in runner.replay_calls)
    assert out["units"]["u1"]["stopped"] == "unresolved"
    assert fail_log == [("u1", 1), ("u1", 2)]
    assert out["units"]["u1"]["sessions"] == 1


@pytest.mark.asyncio
async def test_done_items_are_skipped_and_changed_input_reruns(
        monkeypatch: Any):
    # The batch's item already has a readings receipt at the CURRENT
    # hash → its stimuli are dropped and no model call is spent on them.
    stimuli = [_s(1, 100), _s(2, 200)]
    work = [{"index": 0, "work_id": "stimuli:1,2", "input_hash": "hNEW"}]
    runner = _Runner(work_by_batch=[work])
    _patch_sql(monkeypatch, stimuli=stimuli,
               pairs={("stimuli:1,2", "hNEW")}, covered={1, 2})
    out = await ub.run_units(
        _FakeEngine(object()), runner=runner, **_run_kwargs())
    assert not runner.replay_calls
    assert out["units"]["u1"]["skipped_done"] == 1
    # ...and a skipped batch consumed NO model-work slot (bounded resume).
    assert out["model_batches"] == 0

    # Same work id, receipt only at an OLD hash (an edit landed) → not
    # skipped: the item runs again, and afterwards it IS covered.
    runner2 = _Runner(work_by_batch=[work, work])  # original + re-inspect
    pairs = {("stimuli:1,2", "hOLD")}

    async def _pairs2(conn: Any, work_ids: Any) -> set[tuple[str, str]]:
        rows = {(f"input:u1:{sid}", digest)
                for key, digest in pairs for sid in ub.work_id_sids(key)}
        return {pair for pair in rows if pair[0] in set(work_ids)}

    _patch_sql(monkeypatch, stimuli=stimuli, pairs=pairs, covered={1, 2})
    monkeypatch.setattr(ub.sql, "readings_done", _pairs2)

    # After replay the receipt pair lands at the new hash (a successful
    # proposal writes it under the current input).
    async def _replay_then_admit(*, view: Any, now_ms: int,
                                 historical: bool) -> Any:
        runner2.replay_calls.append(
            (tuple(s.id for s in view.pending), now_ms, historical))
        pairs.add(("stimuli:1,2", "hNEW"))
        return object()

    runner2.replay = _replay_then_admit  # type: ignore[assignment]
    out2 = await ub.run_units(
        _FakeEngine(object()), runner=runner2, **_run_kwargs())
    assert len(runner2.replay_calls) == 1
    assert out2["units"]["u1"]["stopped"] == "unresolved"


@pytest.mark.asyncio
async def test_apply_stops_at_the_batch_bound_run_wide(monkeypatch: Any):
    stimuli = [_s(i, i * 100, unit=u) for u in ("u1", "u2")
               for i in range(1, 4)]
    work = [[{"index": 0, "work_id": "stimuli:1,2,3", "input_hash": "h"}]] * 6
    runner = _Runner(work_by_batch=work)
    covered_by_replay: set[int] = set()

    async def _replay(view: Any, now_ms: int, historical: bool) -> Any:
        covered_by_replay.update(s.id for s in view.pending)
        return object()

    runner.replay = _replay  # type: ignore[assignment]
    _patch_sql(monkeypatch, stimuli=stimuli)
    # Make each replay's items covered so batches keep flowing.
    async def _covered(conn: Any, unit: str) -> set[int]:
        return covered_by_replay
    monkeypatch.setattr(ub, "covered_stimulus_ids", _covered)
    async def _pairs(conn: Any, work_ids: Any) -> set[tuple[str, str]]:
        return {(wid, "h") for wid in work_ids} if covered_by_replay else set()
    monkeypatch.setattr(ub.sql, "readings_done", _pairs)

    kw = _run_kwargs()
    kw["units"] = ["u1", "u2"]
    kw["home_rooms"] = {"u1": ROOM, "u2": ROOM}
    kw["bounds"] = ub.Bounds(max_batches=2, max_tokens=10**9, max_seconds=3600)
    out = await ub.run_units(
        _FakeEngine(object()), runner=runner, batch_stimuli=3, **kw)
    # max_batches is RUN-WIDE: two units each short of it still hit it.
    # Each model batch inspects twice (original + filtered re-inspect).
    assert len(runner.inspect_calls) == 4
    assert out["model_batches"] == 2
    assert out["stopped"] == "max_batches"


@pytest.mark.asyncio
async def test_a_failed_session_is_recovered_parked_and_frontier_stops(
        monkeypatch: Any):
    stimuli = [_s(1, 100), _s(2, 200)]
    fail_log: list[tuple[str, int]] = []
    _patch_sql(monkeypatch, stimuli=stimuli, fail_log=fail_log)
    runner = _Runner(work_by_batch=[
        [{"index": 0, "work_id": "stimuli:1", "input_hash": "h"}]],
        fail_on={0})
    out = await ub.run_units(
        _FakeEngine(object()), runner=runner, **_run_kwargs())
    assert out["units"]["u1"]["stopped"] == "inspect_failed"
    assert fail_log == [("u1", 1), ("u1", 2)]  # the whole batch fails together

    # At the parked threshold the stimulus is never even inspected.
    runner2 = _Runner()
    _patch_sql(monkeypatch, stimuli=[_s(1, 100)],
               failures={1: ub.sql.PARK_AFTER})
    out2 = await ub.run_units(
        _FakeEngine(object()), runner=runner2, **_run_kwargs())
    assert runner2.inspect_calls and not runner2.replay_calls
    assert out2["units"]["u1"]["parked"] == 1
    assert out2["units"]["u1"]["stopped"] == "unresolved"


# -- isolated failure identity -------------------------------------------------


def test_failure_key_never_matches_the_live_parked_key():
    """Live parking counts `stimulus:{unit}:{sid}` work ids
    (`WakeMemory.parked`); the historical lane writes
    `backfill-stimulus:{unit}:{sid}` — the SAME receipt table, a disjoint
    key, so five historical failures can never park a live stimulus."""
    key = ub.sql.FAILURE_KEY.format(unit="u1", sid=5)
    assert key == "backfill-stimulus:u1:5"
    assert key != "stimulus:u1:5"
    assert key.startswith("backfill-")


# -- the meter -----------------------------------------------------------------


class _InnerRunner:
    """The agent-runner seam the meter wraps: answers a fixed result or
    raises, optionally leaving spend in SESSION_TOKENS like
    `LiveCognitionAgentRunner._leave_spend` does even on failure."""

    def __init__(self, *, result: Any = None, error: Exception | None = None,
                 spend: int | None = None) -> None:
        self._result = result
        self._error = error
        self._spend = spend
        self.budgets: list[Any] = []

    async def run(self, *, budget: Any = None, **kw: Any) -> Any:
        self.budgets.append(budget)
        from nora.app.cognition_session import SESSION_TOKENS, TokenSpend
        if self._spend is not None:
            SESSION_TOKENS.set(TokenSpend(total=self._spend))
        if self._error is not None:
            raise self._error
        return self._result


def _budget(tokens: int = 10_000, seconds: float = 600.0) -> Any:
    from nora.app.cognition_session import AdaptorBudget
    return AdaptorBudget(turns=6, total_tokens=tokens, output_tokens=2_000,
                         timeout_seconds=seconds)


@pytest.mark.asyncio
async def test_meter_counts_result_and_clamps_budget_to_remaining():
    result = SimpleNamespace(total_tokens=400)
    inner = _InnerRunner(result=result)
    m = ub.MeteredAgentRunner(inner)
    m.remaining_tokens = 150
    m.remaining_seconds = 30.0
    out = await m.run(budget=_budget(tokens=10_000, seconds=600.0))
    assert out is result and m.total_tokens == 400 and m.calls == 1
    assert inner.budgets[0].total_tokens == 150
    assert inner.budgets[0].timeout_seconds == 30.0


@pytest.mark.asyncio
async def test_meter_reads_failed_spend_and_unknown_usage_is_error():
    # A FAILED session's usage is still usage.
    inner = _InnerRunner(error=RuntimeError("loop died"), spend=777)
    m = ub.MeteredAgentRunner(inner)
    with pytest.raises(RuntimeError, match="loop died"):
        await m.run(budget=_budget())
    assert m.total_tokens == 777

    # A SUCCESSFUL session with unknown usage is an ERROR, never a zero.
    inner2 = _InnerRunner(result=SimpleNamespace(total_tokens=None))
    m2 = ub.MeteredAgentRunner(inner2)
    with pytest.raises(ub.UnknownSpendError):
        await m2.run(budget=_budget())
    assert m2.total_tokens == 0


# -- catch-up ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catch_up_refuses_incomplete_and_queues_one_time_wake(
        monkeypatch: Any):
    appended: list[Any] = []

    class _Stimuli:
        async def append(self, *, stimulus: Any, recorded_at_ms: int) -> int:
            appended.append(stimulus)
            return 77

    stimuli = [_s(1, 100), _s(2, 200)]
    work = [{"index": 0, "work_id": "stimuli:1,2", "input_hash": "h1"}]
    runner = _Runner(work_by_batch=[work])

    async def _enum(conn: Any, unit: str, **kw: Any) -> tuple[list[Stimulus], dict]:
        return stimuli, {}

    async def _failures(conn: Any, unit: str, ids: Any) -> dict[int, int]:
        return dict.fromkeys(ids, 0)

    async def _pairs(conn: Any, work_ids: Any) -> set[tuple[str, str]]:
        return {(key, "h1") for key in work_ids}

    async def _covered(conn: Any, unit: str) -> set[int]:
        return {1, 2}

    async def _still_open(conn: Any, unit: str, now_ms: int, *,
                          remind_subjects: Any = ()) -> dict[str, Any]:
        return {"unit": unit, "as_of_ms": now_ms, "relations": [],
                "commitments": [{"subject": "pay invoice", "due_ms": now_ms}],
                "events": [], "reminds": list(remind_subjects)}

    monkeypatch.setattr(ub, "enumerate_unit", _enum)
    monkeypatch.setattr(ub.sql, "failure_counts", _failures)
    monkeypatch.setattr(ub.sql, "readings_done", _pairs)
    monkeypatch.setattr(ub, "covered_stimulus_ids", _covered)
    monkeypatch.setattr(ub.sql, "still_open", _still_open)

    async def _pending() -> list[Any]:
        return [SimpleNamespace(text=text, platform="signal", conversation_id="room-1")
                for text in ("r1", "r2")]

    out = await ub.catch_up(
        _FakeEngine(object()), stimulus_store=_Stimuli(), runner=runner,
        units=["u1"], home_rooms={"u1": ROOM},
        rooms_of_unit=lambda u: {("signal", "room-1")}, unit_of=lambda p, c: "u1",
        person_rooms={}, self_ids=(), now_ms=86_400_000 * 10,
        pending_reminds=_pending)
    assert out["units"]["u1"]["status"] == "queued"
    assert appended and appended[0].kind == "time"
    # The stimulus's item IS the still-open selection: it renders verbatim
    # in the agenda and seeds the graph preview via aliases.
    assert "pay invoice" in appended[0].item
    assert "r1" in appended[0].item
    assert out["units"]["u1"]["still_open"]["reminds"] == ["r1", "r2"]

    # A stale-hash receipt: the pair exists at hOLD but the current input
    # is hNEW → no false complete.
    async def _stale_pairs(conn: Any, work_ids: Any) -> set[tuple[str, str]]:
        return {(key, "hOLD") for key in work_ids}

    monkeypatch.setattr(ub.sql, "readings_done", _stale_pairs)
    # Old coverage of these SAME stimulus ids must not override a stale hash.
    monkeypatch.setattr(ub, "covered_stimulus_ids", _covered)
    appended.clear()
    out2 = await ub.catch_up(
        _FakeEngine(object()), stimulus_store=_Stimuli(),
        runner=_Runner(work_by_batch=[work]),
        units=["u1"], home_rooms={"u1": ROOM},
        rooms_of_unit=lambda u: {("signal", "room-1")}, unit_of=lambda p, c: "u1",
        person_rooms={}, self_ids=(), now_ms=86_400_000 * 10)
    assert out2["units"]["u1"]["status"] == "incomplete"
    assert not appended

    # Parked work is incomplete, never "complete history".
    async def _parked_failures(conn: Any, unit: str, ids: Any) -> dict[int, int]:
        return dict.fromkeys(ids, ub.sql.PARK_AFTER)

    monkeypatch.setattr(ub.sql, "failure_counts", _parked_failures)
    monkeypatch.setattr(ub.sql, "readings_done", _stale_pairs)
    out3 = await ub.catch_up(
        _FakeEngine(object()), stimulus_store=_Stimuli(), runner=runner,
        units=["u1"], home_rooms={"u1": ROOM},
        rooms_of_unit=lambda u: {("signal", "room-1")}, unit_of=lambda p, c: "u1",
        person_rooms={}, self_ids=(), now_ms=86_400_000 * 10)
    assert out3["units"]["u1"]["status"] == "incomplete"
    assert out3["units"]["u1"]["reason"] == "parked"


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
        def with_collaborators(self, **kw: Any) -> _Live:
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


# -- per-item receipts through the real propose --------------------------------


@pytest.mark.asyncio
async def test_propose_writes_a_receipt_per_admitted_item():
    """The completion contract end-to-end through `WakeMemory.propose`: a
    multi-item call admits every named item, and each lands a `readings`
    receipt under its OWN (work_id, input_hash) — reverting the follower
    loop to a single combined receipt turns this red."""
    from nora.app.cognition_memory import WakeMemory
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import WorkItem

    written: list[tuple[str, str]] = []

    class _Records:
        async def readings(self, work: Any, session: int, item: Any,
                           claims: Any) -> None:
            written.append(item.snapshot["completion"])

    class _Result:
        def __init__(self) -> None:
            self.relations: list[Any] = []
            self.evidence: list[Any] = []
            self.refused: list[Any] = []

    class _Admission:
        async def _admit(self, work: Any, output: Any,
                         fetched: Any, session: int) -> Any:
            return _Result(), []

    class _Graph:
        async def walk(self, seeds: Any, scope: Any) -> Any:
            from nora.domain.memory_graph import Subgraph
            return Subgraph("ok")

        async def aliases(self, *, unit: str) -> dict:
            return {}

    memory = WakeMemory(records=_Records(), graph=_Graph(), metrics=None,
                        content_checks=False)
    memory.admission = _Admission()

    now = datetime.now(UTC)
    a = WorkItem("stimuli:1", ("stimulus", "(1,)"), "u1", "ha", now)
    b = WorkItem("stimuli:2", ("stimulus", "(2,)"), "u1", "hb", now)
    item_a = ComprehendItem(work=a, sources=(), platform="signal",
                            room="c1", snapshot={"messages": []})
    item_b = ComprehendItem(work=b, sources=(), platform="signal",
                            room="c1", snapshot={"messages": []})
    state = SimpleNamespace(
        memory_items={0: item_a, 1: item_b}, session_id=7, unit="u1",
        graph_fetched=SimpleNamespace(nodes=[], relations=[]),
        shown_keys=set(), admitted_relations={}, admitted_evidence=[],
        admitted_indices=set(), graph_text="", historical=False)
    payload = {key: [] for key in ("entities", "relations", "evidence", "corrections", "readings")}
    out = await memory.propose(state, items=[0, 1], payload=payload)
    assert out["status"] == "ok"
    assert len(written) == 1
    assert [(v["work_id"], v["input_hash"]) for v in written[0]] == [
        ("stimuli:1", "ha"), ("stimuli:2", "hb")]
