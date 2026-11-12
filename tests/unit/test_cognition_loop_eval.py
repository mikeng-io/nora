"""`scripts.cognition_loop` — the verdict roll-up, the case format, the
budget, the wake verdict, the diff classifier and the hooks the live
lane runs that replay never did. All offline: the session is
`FakeCognitionAgentRunner`, the stores are the `nora.testing` fakes the
contract suite binds to the real SQL stores.
"""

from __future__ import annotations

from typing import Any

import pytest

from nora.app.cognition_session import CognitionRunResult
from nora.domain.stimuli import Stimulus, UnitView, Verdict
from nora.subject.eval_evidence import EvidenceError
from scripts.cognition_loop.__main__ import token_budget
from scripts.cognition_loop.cases import (
    Expectation,
    generated_case,
    load_case,
    write_case,
)
from scripts.cognition_loop.state import UnitState, diff
from scripts.cognition_loop.verdicts import (
    ERROR,
    FAIL,
    PASS,
    REVIEW,
    JudgeAnswer,
    expectation_verdict,
    rollup,
    validate_citations,
)
from scripts.cognition_loop.wakes import eval_verdict
from tests.unit.test_cognition_session_runner import (
    FakeCognitionAgentRunner,
    _pending,
    _runner,
)

NOW = 1_800_000_000_000


def _case() -> Any:
    return generated_case()


# --------------------------------------------------------------------------
# verdict roll-up
# --------------------------------------------------------------------------


def test_both_judges_met_is_pass():
    v = expectation_verdict((
        JudgeAnswer("met", reason="the note is there", entry_ids=("note:1",)),
        JudgeAnswer("met", reason="created this wake", entry_ids=("note:1",)),
    ))
    assert v.verdict == PASS


def test_both_judges_not_met_is_fail():
    v = expectation_verdict((
        JudgeAnswer("not_met", reason="the note is absent",
                    entry_ids=("note:1",)),
        JudgeAnswer("not_met", reason="no created entry",
                    entry_ids=("note:1",)),
    ))
    assert v.verdict == FAIL


def test_disagreement_is_review():
    cited = ("note:1",)
    assert expectation_verdict((
        JudgeAnswer("met", reason="a", entry_ids=cited),
        JudgeAnswer("not_met", reason="b", entry_ids=cited),
    )).verdict == REVIEW
    assert expectation_verdict((
        JudgeAnswer("met", reason="a", entry_ids=cited),
        JudgeAnswer("cannot_tell"),
    )).verdict == REVIEW
    assert expectation_verdict(
        (JudgeAnswer("cannot_tell"), JudgeAnswer("cannot_tell"))
    ).verdict == REVIEW


def test_a_judge_error_is_error_and_error_never_passes():
    v = expectation_verdict((
        JudgeAnswer("met", reason="a", entry_ids=("note:1",)),
        JudgeAnswer("error"),
    ))
    assert v.verdict == ERROR
    assert rollup(ERROR, PASS, PASS) == ERROR
    assert rollup(PASS, REVIEW, FAIL) == FAIL
    assert rollup(REVIEW, PASS) == REVIEW


def test_a_citation_to_a_nonexistent_entry_is_an_error():
    answer = JudgeAnswer("met", reason="the note says so",
                         entry_ids=("note:999",))
    assert validate_citations(answer, ["note:1", "memo:2"]) == "note:999"
    assert validate_citations(answer, ["note:999"]) is None


# --------------------------------------------------------------------------
# case format
# --------------------------------------------------------------------------


def test_the_generated_case_round_trips(tmp_path):
    path = tmp_path / "generated.json"
    write_case(generated_case(), path)
    loaded = load_case(path)
    assert loaded == generated_case()
    assert loaded.expectations[0].polarity == "must"


def test_an_expectation_anchor_must_name_a_wake():
    with pytest.raises(EvidenceError):
        from scripts.cognition_loop.cases import Case
        Case(name="x", unit="u", wakes=(1,),
             expectations=(Expectation(wake=5, polarity="must",
                                       kind="note", text="t"),))


def test_expectation_polarity_and_kind_are_checked():
    with pytest.raises(EvidenceError):
        Expectation(wake=0, polarity="should", kind="note", text="t")
    with pytest.raises(EvidenceError):
        Expectation(wake=0, polarity="must", kind="vibes", text="t")


# --------------------------------------------------------------------------
# the pre-run token budget
# --------------------------------------------------------------------------


def test_the_budget_is_the_wake_count_times_per_wake():
    from scripts.cognition_loop.cases import Case
    cases = [
        Case(name="a", unit="u", wakes=(1, 2, 3)),
        Case(name="b", unit="u", wakes=(4,)),
    ]
    assert token_budget(cases, 200_000) == 800_000


# --------------------------------------------------------------------------
# the wake verdict
# --------------------------------------------------------------------------


def _view() -> UnitView:
    return UnitView(
        unit="unit-1", pending=_pending("unit-1", n=2),
        last_activity_ms=None, last_wake=None,
    )


def test_decide_claim_stands_when_it_fires():
    decided = Verdict(
        reason="message", through_stimulus_id=2, stimulus_count=2,
        eligible_count=2, claimed_ids=frozenset({1, 2}),
    )
    verdict, forced = eval_verdict(_view(), decided, reason="message")
    assert not forced and verdict is decided


def test_a_no_change_wake_still_gets_a_claimable_verdict():
    verdict, forced = eval_verdict(_view(), Verdict(), reason="time")
    assert forced
    assert verdict.reason == "time"
    assert verdict.claimed_ids == frozenset({1, 2})
    assert verdict.through_stimulus_id == 2


def test_inert_stimuli_stay_unclaimed():
    stimulus = _pending("unit-1", n=1)[0]
    inert = Stimulus(
        id=9, unit="unit-1", kind="time", item="floor", ref="time:9",
        at_ms=NOW, recorded_at_ms=NOW,
    )
    view = UnitView(
        unit="unit-1", pending=(stimulus, inert),
        last_activity_ms=None, last_wake=None,
        inert_kinds=frozenset({"time"}),
    )
    verdict, _ = eval_verdict(view, Verdict(), reason="time")
    assert 9 in verdict.unclaimed_ids


# --------------------------------------------------------------------------
# the diff
# --------------------------------------------------------------------------


def _state(**kinds):
    return UnitState(kinds={k: kinds.get(k, {}) for k in (
        "graph", "profile", "note", "memo", "event", "topic",
        "lesson", "standing", "reminder", "wake")})


def test_the_diff_classifies_every_transition():
    before = _state(note={
        "n1": {"title": "kept", "body": "same"},
        "n2": {"title": "old", "body": "v1"},
        "n3": {"title": "closed", "body": "gone"},
    })
    after = _state(note={
        "n1": {"title": "kept", "body": "same"},
        "n2": {"title": "old", "body": "v2"},
        "n4": {"title": "new", "body": "created"},
    })
    d = diff(before, after).kinds["note"]
    assert [e.entry_id for e in d.created] == ["n4"]
    assert [e.entry_id for e in d.revised] == ["n2"]
    assert d.revised[0].before == {"title": "old", "body": "v1"}
    assert d.revised[0].after == {"title": "old", "body": "v2"}
    assert [e.entry_id for e in d.retired] == ["n3"]
    assert [e.entry_id for e in d.unchanged] == ["n1"]


def test_diff_entry_ids_are_kind_scoped():
    before = _state()
    after = _state(note={"n1": {}}, memo={"m1": {}})
    ids = diff(before, after).entry_ids()
    assert ids == {"note:n1", "memo:m1"}


# --------------------------------------------------------------------------
# the live lane's close, and the replay lane's unchanged silence
# --------------------------------------------------------------------------


class _Fold:
    def __init__(self):
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class _TalkingRunner(FakeCognitionAgentRunner):
    # The fold only runs when the session left messages; the scripted fake
    # returns none, so this one answers the way the real agent does.
    async def run(self, **kwargs):
        result = await super().run(**kwargs)
        return CognitionRunResult(
            stop_reason=result.stop_reason, total_tokens=result.total_tokens,
            messages=({"role": "assistant", "content": [{"text": "ok"}]},),
            requirements=result.requirements,
        )


def _runner_with_hooks(monkeypatch):
    # `lesson_settle` is a module-level import on `cognition_session`, so it
    # is patched there; the rest are plain attributes.
    from nora.app import cognition_session
    runner, store, fake = _runner(agent_runner=_TalkingRunner())
    fold = _Fold()
    settle = _Fold()
    runner._standing_fold = fold
    monkeypatch.setattr(cognition_session, "lesson_settle", settle)
    return runner, store, fake, fold, settle


async def test_the_evals_live_lane_runs_the_real_close(monkeypatch):
    runner, store, _fake, fold, settle = _runner_with_hooks(monkeypatch)
    view = UnitView(unit="unit-1", pending=_pending("unit-1"),
                    last_activity_ms=None, last_wake=None)
    await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    assert fold.calls, "Standing fold must run on the live lane"
    assert settle.calls, "lesson settlement must run on the live lane"
    assert store.sessions, "the session must be recorded"


async def test_the_replay_lane_still_skips_the_close(monkeypatch):
    runner, _store, _fake, fold, settle = _runner_with_hooks(monkeypatch)
    await runner.replay(
        view=UnitView(unit="unit-1", pending=_pending("unit-1"),
                      last_activity_ms=None, last_wake=None),
        now_ms=NOW,
    )
    assert not fold.calls, "replay must not fold Standing"
    assert not settle.calls, "replay must not settle lessons"


# --------------------------------------------------------------------------
# the Record advance writes only Record
# --------------------------------------------------------------------------


def test_wrote_only_record_accepts_the_record_tables():
    from scripts.cognition_loop.advance import (
        RECORD_APPEND_TABLES,
        RECORD_HISTORY_TABLES,
        AdvanceReport,
    )
    report = AdvanceReport(at_ms=2,
        written=dict.fromkeys(RECORD_HISTORY_TABLES, 1)
        | dict.fromkeys(RECORD_APPEND_TABLES, 1)
        | {"stimulus": 3, "row_history": 9},
    )
    assert report.wrote_only_record()


def test_wrote_only_record_refuses_derived_state():
    from scripts.cognition_loop.advance import AdvanceReport
    for derived in ("cognition_wake", "stimulus_claim", "note",
                    "memory_edge", "lesson", "ledger_standing"):
        report = AdvanceReport(at_ms=2, written={derived: 1})
        assert not report.wrote_only_record(), derived


# --------------------------------------------------------------------------
# no-delivery
# --------------------------------------------------------------------------


def _subject(**deps):
    from types import SimpleNamespace
    registry = SimpleNamespace(adapters=deps.pop("adapters", {}))
    base = {"bridge_runtime": None, "fire_path": None, "dispatcher": None,
                "social_poller": None}
    base.update(deps)
    return SimpleNamespace(deps=SimpleNamespace(registry=registry, **base))


def test_a_live_sender_is_a_refusal():
    from types import SimpleNamespace

    class RealSignalSender:
        pass

    from scripts.cognition_loop.egress import assert_no_egress
    subject = _subject(
        adapters={"signal": SimpleNamespace(_sender=RealSignalSender())})
    with pytest.raises(EvidenceError, match=r"lands? nowhere"):
        assert_no_egress(subject)


def test_a_bridge_runtime_is_a_refusal():
    from scripts.cognition_loop.egress import assert_no_egress
    with pytest.raises(EvidenceError, match="bridge_runtime"):
        assert_no_egress(_subject(bridge_runtime=object()))


def test_the_landed_sender_passes():
    from types import SimpleNamespace
    class LandedSignalSender:
        pass
    from scripts.cognition_loop.egress import assert_no_egress
    report = assert_no_egress(_subject(
        adapters={"signal": SimpleNamespace(_sender=LandedSignalSender())}))
    assert report["senders"]["signal"] == {"_sender": "LandedSignalSender"}


# --------------------------------------------------------------------------
# the pre-run budget refuses before any work
# --------------------------------------------------------------------------


async def test_over_budget_refuses_before_starting(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from scripts.cognition_loop import __main__ as cli
    monkeypatch.setattr(cli, "load", lambda p: SimpleNamespace(
        store=SimpleNamespace(database_url_env="X")))
    import nora.secret
    monkeypatch.setattr(nora.secret, "credentials",
                        lambda: {"X": "postgresql://u@h/snap"})
    monkeypatch.setattr(cli, "snapshot_only", lambda *a: None)
    monkeypatch.setattr(cli, "private_directory", lambda p: tmp_path)
    case_path = tmp_path / "case.json"
    write_case(generated_case(), case_path)
    args = SimpleNamespace(
        config=tmp_path / "config.toml", policy=tmp_path / "p.toml",
        identity=tmp_path / "i.toml", database_url="postgresql://u@h/snap",
        snapshot=True, case=case_path, case_dir=None, output=tmp_path,
        judge_a="a", judge_b="b", smoke=False, inspect_only=False,
        per_wake_tokens=200_000, token_budget=1, stop_after_tokens=None,
        seed=0,
    )
    with pytest.raises(ValueError, match="refuse rather than start"):
        await cli.run(args)


# --------------------------------------------------------------------------
# no-egress guard is fail-closed
# --------------------------------------------------------------------------


def test_the_egress_guard_refuses_every_delivery_worker():
    from scripts.cognition_loop.egress import assert_no_egress

    for worker in ("fire_path", "social_poller",
                   "computer_lifecycle", "bridge_runtime"):
        subject = _subject(**{worker: object()})
        with pytest.raises(EvidenceError, match=worker):
            assert_no_egress(subject)


def test_the_egress_guard_refuses_a_real_sender_on_any_platform():
    from types import SimpleNamespace

    from scripts.cognition_loop.egress import assert_no_egress

    class RealSender:
        pass

    for platform in ("signal", "discord", "instagram", "whatsapp", "anychat"):
        subject = _subject(
            adapters={platform: SimpleNamespace(_sender=RealSender())})
        with pytest.raises(EvidenceError, match="senders"):
            assert_no_egress(subject)


def test_an_adapter_whose_sender_cannot_be_identified_is_a_refusal():
    from types import SimpleNamespace

    from scripts.cognition_loop.egress import assert_no_egress

    class BridgeLikeClient:
        pass

    subject = _subject(adapters={
        "signal": SimpleNamespace(_endpoint=object(), _client=BridgeLikeClient()),
    })
    with pytest.raises(EvidenceError, match="cannot be identified"):
        assert_no_egress(subject)

    subject = _subject()
    subject.deps.registry = SimpleNamespace()  # no `adapters`
    with pytest.raises(EvidenceError, match="cannot be enumerated"):
        assert_no_egress(subject)


def test_a_sender_under_another_attribute_name_is_still_found():
    from types import SimpleNamespace

    from scripts.cognition_loop.egress import assert_no_egress

    class BridgeSender:
        pass

    subject = _subject(adapters={
        "discord": SimpleNamespace(transport=BridgeSender()),
    })
    with pytest.raises(EvidenceError, match="senders"):
        assert_no_egress(subject)


def test_the_egress_guard_passes_only_when_everything_is_landed():
    from types import SimpleNamespace

    from scripts.cognition_loop.egress import assert_no_egress

    class LandedSignalSender:
        pass

    # A dispatcher is a required inbound entry point on every composed
    # subject — present, and not a delivery worker.
    subject = _subject(dispatcher=object(),
        adapters={"signal": SimpleNamespace(_sender=LandedSignalSender())})
    report = assert_no_egress(subject)
    assert not any(report["workers"].values())


# --------------------------------------------------------------------------
# advance copies Record history only
# --------------------------------------------------------------------------


def test_the_advance_receipt_checks_each_history_table_name():
    from scripts.cognition_loop.advance import (
        RECORD_HISTORY_TABLES,
        AdvanceReport,
    )

    assert AdvanceReport(
        at_ms=1, written={"row_history": 3},
        history_tables=frozenset(RECORD_HISTORY_TABLES[:2]),
    ).wrote_only_record()
    assert not AdvanceReport(
        at_ms=1, written={"row_history": 4},
        history_tables=frozenset({"messages", "note"}),
    ).wrote_only_record()


async def test_the_history_window_skips_derived_tables(pg_url):
    from datetime import UTC, datetime

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    from scripts.cognition_loop.advance import _history_window

    engine = create_async_engine(pg_url)
    try:
        moment = datetime(2025, 1, 2, tzinfo=UTC)
        async with engine.begin() as conn:
            for table_name, row_key in (
                ("messages", {"id": 1}),
                ("note", {"id": 2}),
                ("platform_observation", {"id": 3}),
                ("relation", {"id": 4}),
            ):
                await conn.execute(sa.text(
                    "INSERT INTO row_history (table_name, row_key, op, image,"
                    " recorded_at) VALUES (:t, CAST(:k AS jsonb), 'insert',"
                    " '{}'::jsonb, :ts)"
                ), {"t": table_name, "k": __import__("json").dumps(row_key),
                    "ts": moment})
        async with engine.connect() as conn:
            rows = await _history_window(
                conn,
                datetime(2025, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 3, tzinfo=UTC),
            )
        assert {r["table_name"] for r in rows} == {
            "messages", "platform_observation",
        }
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------
# the config under test is bound
# --------------------------------------------------------------------------


def test_overrides_parse_key_value_pairs_with_toml_types():
    from scripts.cognition_loop.__main__ import parse_overrides

    assert parse_overrides(
        ["cognition.enabled=false", "llm.model=\"claude-test\"", "wake.x=1.5"]
    ) == {
        "cognition.enabled": False, "llm.model": "claude-test", "wake.x": 1.5,
    }
    with pytest.raises(ValueError, match="key=value"):
        parse_overrides(["not-a-pair"])


async def test_compose_binds_arm_overrides_and_disables_delivery(
    monkeypatch, tmp_path,
):
    from types import SimpleNamespace

    from scripts.cognition_loop import run as run_mod

    captured = {}

    async def fake_obtain(spec):
        captured["spec"] = spec
        return SimpleNamespace(spec=spec)

    monkeypatch.setattr(run_mod, "obtain", fake_obtain)
    args = SimpleNamespace(
        workdir=str(tmp_path),
        overrides={"llm.model": "claude-arm"},
    )
    room = SimpleNamespace(platform="signal", primary_id="room-1")
    await run_mod._compose(
        args, "postgresql+asyncpg://x", 1_700_000_000_000, room,
        "policy", "identity", workspace="nora-eval-run-case-abc",
    )
    overrides = captured["spec"].overrides
    assert overrides["llm.model"] == "claude-arm"
    assert overrides["computer.enabled"] is False
    assert overrides["computer.scoped.enabled"] is False
    assert overrides["honcho.workspace_id"] == "nora-eval-run-case-abc"


# --------------------------------------------------------------------------
# trajectories bind to their own wake
# --------------------------------------------------------------------------


def _session(name: str, **body):
    import json

    return {
        "kind": "event",
        "name": name,
        "attributes": {"body": json.dumps(body)},
    }


def test_a_trajectory_binds_to_its_own_session_not_the_last_one():
    from scripts.cognition_loop.trajectory import (
        session_events,
        wake_trajectory,
    )

    events = [
        _session("nora.session.started", session_id="s-old", wake_id=10,
                 system="OLD PROMPT", agenda={"a": 1}),
        _session("nora.session.terminal", session_id="s-old", wake_id=10,
                 outcome="session_end"),
        _session("nora.session.started", session_id="s-new", wake_id=11,
                 system="NEW PROMPT", agenda={"a": 2}),
        _session("nora.session.instructions", doctrine_sha256="abc"),
        _session("nora.session.terminal", session_id="s-new", wake_id=11,
                 outcome="budget"),
    ]
    bound = session_events(events, session_id="s-new", wake_id=11)
    trajectory = wake_trajectory(
        bound, wake_id=11, session_id="s-new", tokens=None)
    assert trajectory["blocks"]["system"] == "NEW PROMPT"
    assert trajectory["blocks"]["instructions"] == {"doctrine_sha256": "abc"}
    assert trajectory["stop_reason"] == "budget"


def test_an_empty_agenda_wake_gets_an_empty_trajectory():
    from scripts.cognition_loop.trajectory import (
        session_events,
        wake_trajectory,
    )

    events = [
        _session("nora.session.started", session_id="s-old", wake_id=10,
                 system="OLD PROMPT"),
        _session("nora.session.terminal", session_id="s-old", wake_id=10,
                 outcome="session_end"),
    ]
    bound = session_events(events, session_id=None, wake_id=None)
    assert bound == []
    trajectory = wake_trajectory(
        bound, wake_id=11, session_id=None, tokens=None)
    assert trajectory["blocks"]["system"] is None
    assert trajectory["model_calls"] == []
    assert trajectory["stop_reason"] is None


# --------------------------------------------------------------------------
# run verdicts are fail-closed
# --------------------------------------------------------------------------


def test_run_status_keeps_fail_and_review_and_folds_evidence_error():
    from scripts.cognition_loop.__main__ import run_status

    ok = {"status": "PASS"}
    assert run_status([{"status": "FAIL"}, {"status": "PASS"}], ok) == "FAIL"
    assert run_status(
        [{"status": "REVIEW"}, {"status": "PASS"}], ok) == "REVIEW"
    assert run_status([{"status": "PASS"}], ok) == "PASS"
    assert run_status(
        [{"status": "PASS"}], {"status": "ERROR"}) == "ERROR"
    assert run_status([{"status": "DONE"}, {"status": "PASS"}], ok) == "ERROR"
    assert run_status([], ok) == "ERROR"


# --------------------------------------------------------------------------
# a pass needs grounded, identified judges
# --------------------------------------------------------------------------


class _FakeJudges:
    def __init__(self, *, text: str, header: str | None, routed: str):
        self._text = text
        self._header = header
        self._routed = routed
        self.served: dict[str, list[dict[str, str]]] = {}

    def _endpoint(self, model: str):
        return ("http://x", "key", self._routed)

    async def call(self, model, instruction, payload):
        if self._header is not None:
            self.served.setdefault(model, []).append(
                {"x-omniroute-model": self._header})
        return self._text


async def test_a_missing_served_header_is_an_error_answer():
    from scripts.cognition_loop.judges import judge_one

    judges = _FakeJudges(
        text='{"verdict": "met", "reason": "x", "entry_ids": ["note:1"]}',
        header=None, routed="routed-model",
    )
    answer = await judge_one(judges, "alias", {})
    assert answer.verdict == "error"


async def test_a_wrong_served_header_is_an_error_answer():
    from scripts.cognition_loop.judges import judge_one

    judges = _FakeJudges(
        text='{"verdict": "met", "reason": "x", "entry_ids": ["note:1"]}',
        header="different-model", routed="routed-model",
    )
    answer = await judge_one(judges, "alias", {})
    assert answer.verdict == "error"


def test_an_ungrounded_met_is_error_never_pass():
    assert expectation_verdict((
        JudgeAnswer("met", reason="because", entry_ids=()),
        JudgeAnswer("met", reason="because", entry_ids=("note:1",)),
    )).verdict == ERROR
    assert expectation_verdict((
        JudgeAnswer("met", reason="", entry_ids=("note:1",)),
        JudgeAnswer("met", reason="because", entry_ids=("note:1",)),
    )).verdict == ERROR
    assert expectation_verdict((
        JudgeAnswer("met"), JudgeAnswer("met"),
    )).verdict == ERROR


# --------------------------------------------------------------------------
# the graph-only scorer stays retired
# --------------------------------------------------------------------------


def test_the_graph_only_scorer_and_its_entries_stay_retired():
    from scripts import memory_graph_spike as spike

    for name in ("run", "run_rescore", "run_diagnostic",
                 "spike_contract", "report_exit"):
        assert not hasattr(spike, name), name
    with pytest.raises(SystemExit, match="retired"):
        spike.main()


# --------------------------------------------------------------------------
# state diffs scope to the unit's rooms
# --------------------------------------------------------------------------


def test_reminders_scope_to_the_units_rooms_not_a_unit_fallback():
    from types import SimpleNamespace

    from scripts.cognition_loop.state import reminder_in_rooms

    rooms = [("signal", "room-a")]
    ours = SimpleNamespace(platform="signal", conversation_id="room-a")
    foreign = SimpleNamespace(platform="signal", conversation_id="room-b")
    no_unit = SimpleNamespace(platform="discord", conversation_id="room-a")
    assert reminder_in_rooms(ours, rooms)
    assert not reminder_in_rooms(foreign, rooms)
    assert not reminder_in_rooms(no_unit, rooms)


def test_profile_keys_cover_every_room_of_the_unit():
    from types import SimpleNamespace

    from scripts.cognition_loop.run import _profile_keys

    convos = [
        SimpleNamespace(unit="u1", platform="signal", persons={"alice"}),
        SimpleNamespace(unit="u1", platform="discord", persons={"bob"}),
        SimpleNamespace(unit="u2", platform="signal", persons={"carol"}),
    ]
    allowlist = SimpleNamespace(rooms=lambda: convos)
    subject = SimpleNamespace(
        deps=SimpleNamespace(config_reload=SimpleNamespace(policy=allowlist),
                             persons=SimpleNamespace(by_person={})),
    )
    keys = _profile_keys(subject, "u1")
    assert any(k.endswith(":alice") for k in keys)
    assert any(k.endswith(":bob") for k in keys)
    assert not any(k.endswith(":carol") for k in keys)


# --------------------------------------------------------------------------
# recorded wakes validate unit and order
# --------------------------------------------------------------------------


def test_recorded_wakes_must_be_the_cases_unit_and_ordered():
    from scripts.cognition_loop.run import validate_recorded_wakes

    assert validate_recorded_wakes(
        [(1, "u1", 100), (2, "u1", 200)], "u1") is None
    assert "u2" in validate_recorded_wakes(
        [(1, "u1", 100), (2, "u2", 200)], "u1")
    assert "does not follow" in validate_recorded_wakes(
        [(1, "u1", 200), (2, "u1", 200)], "u1")
    assert "does not follow" in validate_recorded_wakes(
        [(1, "u1", 300), (2, "u1", 200)], "u1")
