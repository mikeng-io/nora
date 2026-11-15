"""The `_run_wake` seam offline: settlement, judging and frozen reports."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

NOW = 1_800_000_000_000


class _JudgeMarker:
    def bind(self, cfg):
        return self




@pytest.fixture(autouse=True)
def private_lock(tmp_path, monkeypatch):
    from nora.subject import sandbox

    monkeypatch.setattr(sandbox, "EVAL_HOST_LOCK", tmp_path / "eval.lock")


def _async_result(value):
    async def _call(*a, **k):
        return value
    return _call


def _wake_args(tmp_path):
    from types import SimpleNamespace

    policy = tmp_path / "policy.toml"
    policy.write_text("x = 1\n")
    identity = tmp_path / "identity.toml"
    identity.write_text("x = 1\n")
    (tmp_path / "events.jsonl").write_text("")
    return SimpleNamespace(
        workdir=str(tmp_path), overrides={}, channel_directory=None,
        judge_a="a", judge_b="b", policy=policy, identity=identity,
        database_url="postgresql+asyncpg://x",
    ), SimpleNamespace(path=tmp_path / "events.jsonl")


def _runner_subject(inspection=None, outcome=None):
    from types import SimpleNamespace

    class _Runner:
        async def inspect(self, **k):
            return inspection

        async def on_wake(self, **k):
            return outcome

        @property
        def wake_hook(self):
            return self.on_wake

    return SimpleNamespace(unit="u", cfg=SimpleNamespace(),
        environment=SimpleNamespace(guarded=True, check=lambda: None),
        deps=SimpleNamespace(
        cognition_runner=_Runner(),
        wake_evaluator=SimpleNamespace(_policy=SimpleNamespace(
            batch_max=5, session_timeout_seconds=10)),
        metrics=SimpleNamespace(get=lambda *a, **k: 0),
        config_reload=SimpleNamespace(policy=SimpleNamespace(rooms=lambda: [
            SimpleNamespace(unit="u", platform="signal", primary_id="r",
                            ids=frozenset({"r"}), persons=frozenset())])),
        persons=SimpleNamespace(by_person={}),
    ))


def _wake_seam(monkeypatch, tmp_path, subject):
    from types import SimpleNamespace

    from nora.domain.stimuli import Stimulus, UnitView
    from scripts.cognition_loop import run as run_mod

    async def _composed(*a, **k):
        return subject

    async def _noop(*a, **k):
        return None

    async def _wake_record(*a, **k):
        return SimpleNamespace(wake_id=42, forced=False)

    async def _view(*a, **k):
        return UnitView(unit="u", pending=(),
                        last_activity_ms=None, last_wake=None)

    async def _by_id(engine, unit, ids):
        return {
            i: Stimulus(id=i, unit=unit, kind="message", item="message",
                        ref=f"messages:{i}", at_ms=NOW, recorded_at_ms=NOW)
            for i in ids
        }

    async def _state(*a, **k):
        return None

    monkeypatch.setattr(run_mod, "prepare_effect_reads", _noop)
    monkeypatch.setattr(run_mod, "effect_boundary", _noop)
    monkeypatch.setattr(run_mod, "read_effects", _async_result(
        {"status": "observed", "items": [], "tools": []}))
    monkeypatch.setattr(run_mod, "_compose", _composed)
    monkeypatch.setattr(run_mod, "assert_no_egress", lambda s: {})
    monkeypatch.setattr(run_mod, "index_replay_sandbox", _noop)
    monkeypatch.setattr(run_mod, "_close_subject", _noop)
    monkeypatch.setattr(run_mod, "create_async_engine",
                        lambda url: SimpleNamespace(dispose=_noop))
    monkeypatch.setattr(
        run_mod, "SqlStimulusStore",
        lambda engine: SimpleNamespace(view=_view))
    monkeypatch.setattr(run_mod, "_stimuli_by_id", _by_id)
    monkeypatch.setattr(run_mod, "decide", lambda *a, **k: None)
    monkeypatch.setattr(
        run_mod, "eval_verdict",
        lambda *a, **k: (SimpleNamespace(reason="time"), False))
    monkeypatch.setattr(run_mod, "record_eval_wake", _wake_record)
    monkeypatch.setattr(run_mod, "state_mod", SimpleNamespace(
        snapshot=_state, diff=lambda a, b: SimpleNamespace(kinds={})))
    monkeypatch.setattr(run_mod, "read_events", lambda p: [])
    monkeypatch.setattr(run_mod, "session_events", lambda e, **k: [])
    monkeypatch.setattr(run_mod, "wake_trajectory",
                        lambda e, **k: {"blocks": dict.fromkeys(
                            ("system", "instructions", "agenda",
                             "items", "stimuli"))})
    return run_mod


async def test_inspect_only_records_the_assembled_blocks(
    monkeypatch, tmp_path,
):
    """A dry run still shows what Nora would have read: the blocks the
    shared `_session_input` assembled, not a reconstructed copy."""
    import json
    from types import SimpleNamespace

    inspection = {
        "system": "SYSTEM PROMPT", "agenda": {"agenda": True},
        "items": [{"id": "i1"}], "stimuli": [{"id": 3}],
        "instructions": {"doctrine_sha256": "d", "skill_versions": {}},
    }
    run_mod = _wake_seam(monkeypatch, tmp_path,
                         _runner_subject(inspection=inspection))
    args, evidence = _wake_args(tmp_path)
    case = SimpleNamespace(unit="u", expectations=(), read_probes=())
    wake = await run_mod._run_wake(
        args, case, 0, 11, NOW, "postgresql+asyncpg://x", "time", None,
        evidence, tmp_path, SimpleNamespace(platform="signal",
                                            primary_id="r"))
    assert wake["trajectory"]["blocks"]["system"] == "SYSTEM PROMPT"
    assert wake["trajectory"]["blocks"]["items"] == [{"id": "i1"}]
    assert wake["trajectory"]["blocks"]["stimuli"] == [{"id": 3}]
    assert wake["trajectory"]["blocks"]["instructions"]["doctrine_sha256"] == "d"
    frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
    assert frozen["trajectory"]["blocks"]["agenda"] == {"agenda": True}


@pytest.mark.parametrize("report_read_failure", [False, True])
async def test_after_state_failure_preserves_captured_trajectory(
    monkeypatch, tmp_path, report_read_failure,
):
    import json
    from types import SimpleNamespace

    inspection = {"system": "Generated assembled input", "agenda": "Generated agenda"}
    run_mod = _wake_seam(monkeypatch, tmp_path, _runner_subject(inspection=inspection))
    reads = []

    async def snapshot(*args, **kwargs):
        reads.append(1)
        if len(reads) == 2 and report_read_failure:
            raise RuntimeError("generated after-state failure")

    monkeypatch.setattr(run_mod.state_mod, "snapshot", snapshot)
    args, evidence = _wake_args(tmp_path)
    result = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=(), read_probes=()),
        0, 11, NOW, "postgresql+asyncpg://x", "time", None, evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"))
    assert len(reads) == 2
    assert result["status"] == ("ERROR" if report_read_failure else "PASS")
    frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
    assert frozen["trajectory"]["blocks"]["system"] == "Generated assembled input"
    assert result["trajectory"]["at_ms"] == NOW
    if report_read_failure:
        assert result["step"] == "report"
        assert "after-state failure" in frozen["error"]["message"]


async def test_an_unavailable_read_preflight_cannot_pass_inspection(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from nora.subject.eval_evidence import EvidenceError
    from scripts.cognition_loop import reads

    subject = _runner_subject(inspection={"items": []})
    run_mod = _wake_seam(monkeypatch, tmp_path, subject)
    args, evidence = _wake_args(tmp_path)
    called = []

    async def unavailable(runner, case, index, inspection, at_ms):
        called.append(index)
        raise EvidenceError("read preflight: unavailable")

    monkeypatch.setattr(reads, "preflight_reads", unavailable)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 0, 11, NOW,
        "postgresql+asyncpg://x", "time", None, evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"))
    assert called == [0]
    assert wake["status"] == "ERROR"
    assert "read preflight" in wake["error"]


async def test_a_compose_failure_names_compose(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace

    from scripts.cognition_loop import run as run_mod

    async def _boom(*a, **k):
        raise ValueError("agenda room is outside the unit")

    monkeypatch.setattr(run_mod, "_compose", _boom)
    monkeypatch.setattr(run_mod, "_close_subject", _async_result_fn())
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 1, 7, NOW,
        "postgresql+asyncpg://x", "time", None, evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"))
    assert wake["status"] == "ERROR" and wake["step"] == "compose"
    assert "agenda room" in wake["error"]
    frozen = json.loads((tmp_path / "wake-1-7.json").read_text())
    assert frozen["error"]["step"] == "compose"


async def test_a_wake_failure_names_wake_not_compose(monkeypatch, tmp_path):
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path, _runner_subject())

    async def _boom(*a, **k):
        raise RuntimeError("view read failed")

    monkeypatch.setattr(
        run_mod, "SqlStimulusStore",
        lambda engine: SimpleNamespace(view=_boom))
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 0, 9, NOW,
        "postgresql+asyncpg://x", "time", None, evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"))
    assert wake["status"] == "ERROR" and wake["step"] == "wake"


def _async_result_fn():
    async def _noop(*a, **k):
        return None
    return _noop




def _recorded_meta(stimulus_ids=(), had_session=False, self_ids=()):
    return {"stimulus_ids": list(stimulus_ids), "self_ids": list(self_ids),
            "had_session": had_session}


async def test_an_inspect_wake_with_no_session_is_error_when_production_ran(
    monkeypatch, tmp_path,
):
    """inspect-only: production ran a session, the sandbox ran none — the
    dry run cannot claim it inspected that wake."""
    import json
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path, _runner_subject())
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 1, 22, NOW,
        "postgresql+asyncpg://x", "time", None, evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        recorded_meta=_recorded_meta((3, 5, 7), had_session=True))
    assert wake["status"] == "ERROR" and wake["step"] == "wake"
    assert wake["no_session"] == "empty agenda"
    assert wake["batch"] == {"recorded": [3, 5, 7], "offered": [3, 5, 7],
                            "recorded_self": [], "offered_self": [],
                            "production_session": True}
    assert "production" in wake["error"]
    frozen = json.loads((tmp_path / "wake-1-22.json").read_text())
    assert frozen["no_session"] == "empty agenda"
    assert frozen["error"]["step"] == "wake"
    assert frozen["batch"]["recorded"] == [3, 5, 7]


async def test_a_batch_stimulus_absent_from_the_sandbox_is_an_advance_error(
    monkeypatch, tmp_path,
):
    """A recorded batch stimulus the Record advance never delivered is
    ERROR at the advance step, named by id — never silently dropped."""
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path, _runner_subject())
    monkeypatch.setattr(run_mod, "_stimuli_by_id", _async_result({}))
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 2, 31, NOW,
        "postgresql+asyncpg://x", "time", None, evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        recorded_meta=_recorded_meta((3, 5, 7), had_session=True))
    assert wake["status"] == "ERROR" and wake["step"] == "advance"
    assert "[3, 5, 7]" in wake["error"]
    assert wake["batch"]["recorded"] == [3, 5, 7]
    assert wake["batch"]["offered"] is None


async def test_a_no_session_wake_is_named_not_silently_passed(
    monkeypatch, tmp_path,
):
    """Production ran none either: `no_session` names why, and the
    status derives over the wake's (empty) expectations."""
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path, _runner_subject())
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 0, 11, NOW,
        "postgresql+asyncpg://x", "time", None, evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        recorded_meta=_recorded_meta((), had_session=False))
    assert wake["status"] == "PASS"
    assert wake["no_session"] == "empty agenda"
    assert wake["batch"]["production_session"] is False


async def test_a_live_wake_reads_its_settlement_back(monkeypatch, tmp_path):
    """Live: the wake's session id and close are read back from the
    outcome row the production settle path recorded."""
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path,
                         _runner_subject(outcome=SimpleNamespace(status="ok")))
    monkeypatch.setattr(run_mod, "_wake_outcome", _async_result({
        "session_id": 9, "outcome": "closed",
        "detail": "", "cause": "none"}))
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 0, 11, NOW,
        "postgresql+asyncpg://x", "time", _JudgeMarker(), evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        recorded_meta=_recorded_meta((1,), had_session=True))
    assert wake["status"] == "PASS"
    assert wake["session_id"] == 9
    assert wake["no_session"] is None


async def test_a_live_wake_threads_the_earlier_wakes_and_clock_to_the_judge(
    monkeypatch, tmp_path,
):
    """The live digest carries its recorded clock and only earlier wake evidence."""
    import json
    from types import SimpleNamespace

    from scripts.cognition_loop.verdicts import ExpectationVerdict

    subject = _runner_subject(outcome=SimpleNamespace(status="ok"))
    run_mod = _wake_seam(monkeypatch, tmp_path, subject)
    monkeypatch.setattr(run_mod, "_wake_outcome", _async_result({
        "session_id": 9, "outcome": "closed",
        "detail": "", "cause": "none"}))
    seen = {}
    bound = object()

    class Judge:
        def bind(self, cfg):
            seen["cfg"] = cfg
            return bound

    async def _judge_call(judges, models, expectation, diff, after,
                          trajectory, earlier_wakes):
        seen["judge"] = judges
        seen["trajectory"] = trajectory
        seen["earlier_wakes"] = earlier_wakes
        return ExpectationVerdict(verdict="PASS", answers=())

    monkeypatch.setattr(run_mod, "judge_expectation", _judge_call)
    rooms = [("signal", "r1"), ("signal", "r2")]
    scope = SimpleNamespace(unit="u", rooms=rooms, profile_keys=())
    monkeypatch.setattr(run_mod, "effect_scope", lambda subject: scope)
    prior = (SimpleNamespace(kinds={}), {"at_ms": NOW - 60_000, "blocks": {}})
    args, evidence = _wake_args(tmp_path)
    expectation = SimpleNamespace(
        wake=0, polarity="must", kind="note", text="generated expectation")
    case = SimpleNamespace(unit="u", expectations=(expectation,))
    wake = await run_mod._run_wake(
        args, case, 0, 11, NOW, "postgresql+asyncpg://x", "time", Judge(),
        evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        recorded_meta=_recorded_meta((1,), had_session=True),
        earlier_wakes=(prior,))
    assert seen["judge"] is bound and seen["cfg"] is subject.cfg
    assert seen["earlier_wakes"] == (prior,)
    assert seen["trajectory"].get("at_ms") == NOW
    assert seen["trajectory"].get("unit") == "u"
    assert seen["trajectory"].get("unit_rooms") == rooms
    assert wake["trajectory"].get("at_ms") == NOW
    frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
    assert frozen["trajectory"].get("at_ms") == NOW
    assert frozen["trajectory"].get("unit_rooms") == [
        ["signal", "r1"], ["signal", "r2"]]


async def test_the_run_hands_each_wake_only_the_earlier_wakes_evidence(
    monkeypatch, tmp_path,
):
    """The chain exposes settled evidence without exposing later wakes."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from scripts.cognition_loop import run as run_mod
    from scripts.cognition_loop.verdicts import Rollup

    class _Sandbox:
        async def __aenter__(self):
            return "postgresql+asyncpg://sandbox", None

        async def __aexit__(self, *a):
            return None

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(run_mod, "sandbox_as_of", lambda *a, **k: _Sandbox())
    monkeypatch.setattr(run_mod, "create_async_engine",
                        lambda url: SimpleNamespace(dispose=_noop))
    monkeypatch.setattr(run_mod, "bump_sequences", _noop)
    monkeypatch.setattr(
        run_mod, "advance_record",
        _async_result(SimpleNamespace(
            written=[], wrote_only_record=lambda: True)))
    received = []
    sentinels = {}

    async def _wake(_args, _case, index, _wake_id, at_ms, _url, _reason,
                    _judges, _evidence, _output, _room, **kwargs):
        received.append(kwargs.get("earlier_wakes", ()))
        sentinels[index] = (
            SimpleNamespace(marker=index),
            {"at_ms": at_ms, "unit": "u", "unit_rooms": [["signal", "r1"]],
             "blocks": {"agenda": f"generated {index}"}},
        )
        diff, trajectory = sentinels[index]
        return {
            "_diff": diff, "trajectory": trajectory,
            "loop": {"link_ids": []}, "step": None, "status": "PASS",
            "expectations": [],
        }

    monkeypatch.setattr(run_mod, "_run_wake", _wake)

    @contextmanager
    def _case_ctx(*a, **k):
        yield

    @asynccontextmanager
    async def _recording(*args, **kwargs):
        yield SimpleNamespace(check=lambda: None)

    await run_mod._wakes(
        SimpleNamespace(database_url="postgresql+asyncpg://x"),
        SimpleNamespace(unit="u", wakes=(11, 22)),
        {0: 1000, 1: 2000}, {0: "time", 1: "time"}, {0: {}, 1: {}},
        _JudgeMarker(), SimpleNamespace(case=_case_ctx), tmp_path,
        ("signal", "r"), SimpleNamespace(open=_recording), Rollup(), [])
    assert received[0] == ()
    assert received[1] == (sentinels[0],)
    assert received[1][0][1]["at_ms"] == 1000
    assert received[1][0][1]["unit_rooms"] == [["signal", "r1"]]


async def test_a_live_wake_without_an_outcome_row_is_error(
    monkeypatch, tmp_path,
):
    """No settlement row: the lane never ran the wake — ERROR, never
    a silent empty-agenda pass."""
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path,
                         _runner_subject(outcome=SimpleNamespace(status="ok")))
    monkeypatch.setattr(run_mod, "_wake_outcome", _async_result(None))
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 0, 11, NOW,
        "postgresql+asyncpg://x", "time", _JudgeMarker(), evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        recorded_meta=_recorded_meta((1,), had_session=True))
    assert wake["status"] == "ERROR" and wake["step"] == "wake"


async def test_a_live_no_session_names_the_outcome_rows_detail(
    monkeypatch, tmp_path,
):
    from types import SimpleNamespace

    run_mod = _wake_seam(monkeypatch, tmp_path,
                         _runner_subject(outcome=SimpleNamespace(status="ok")))
    monkeypatch.setattr(run_mod, "_wake_outcome", _async_result({
        "session_id": None, "outcome": "closed",
        "detail": "empty agenda", "cause": "none"}))
    args, evidence = _wake_args(tmp_path)
    wake = await run_mod._run_wake(
        args, SimpleNamespace(unit="u", expectations=()), 0, 11, NOW,
        "postgresql+asyncpg://x", "time", _JudgeMarker(), evidence, tmp_path,
        SimpleNamespace(platform="signal", primary_id="r"),
        recorded_meta=_recorded_meta((1,), had_session=True))
    assert wake["no_session"] == "empty agenda"
    assert wake["status"] == "PASS"


async def test_a_live_wake_that_never_settled_spends_no_judge_call(
    monkeypatch, tmp_path,
):
    """A session that ended without closing is a wake-step ERROR: the
    diff and trajectory still freeze, but no judge call is spent on a
    wake that produced nothing to measure. A settled wake still judges —
    positive control in the same run."""
    from types import SimpleNamespace

    from scripts.cognition_loop.verdicts import ExpectationVerdict

    run_mod = _wake_seam(monkeypatch, tmp_path,
                         _runner_subject(outcome=SimpleNamespace(status="ok")))
    monkeypatch.setattr(run_mod, "_wake_outcome", _async_result({
        "session_id": None, "outcome": "failed",
        "detail": "capture interrupted", "cause": "model_error"}))
    judged = []

    async def _judge_call(*a, **k):
        judged.append(1)
        return ExpectationVerdict(verdict="PASS", answers=())

    monkeypatch.setattr(run_mod, "judge_expectation", _judge_call)
    args, evidence = _wake_args(tmp_path)
    case = SimpleNamespace(unit="u", expectations=(
        SimpleNamespace(wake=0, polarity="must", kind="note",
                        text="generated expectation"),
        SimpleNamespace(wake=1, polarity="must", kind="note",
                        text="generated expectation"),))
    room = SimpleNamespace(platform="signal", primary_id="r")
    wake = await run_mod._run_wake(
        args, case, 0, 11, NOW, "postgresql+asyncpg://x", "time", _JudgeMarker(),
        evidence, tmp_path, room,
        recorded_meta=_recorded_meta((1,), had_session=True))
    assert wake["status"] == "ERROR" and wake["step"] == "wake"
    assert wake["expectations"] == []
    assert judged == []

    # Positive control: the next wake, settled closed, reaches the judge.
    monkeypatch.setattr(run_mod, "_wake_outcome", _async_result({
        "session_id": 9, "outcome": "closed",
        "detail": "", "cause": "none"}))
    wake = await run_mod._run_wake(
        args, case, 1, 22, NOW, "postgresql+asyncpg://x", "time", _JudgeMarker(),
        evidence, tmp_path, room,
        recorded_meta=_recorded_meta((1,), had_session=True))
    assert judged == [1]
    assert wake["status"] == "PASS"


