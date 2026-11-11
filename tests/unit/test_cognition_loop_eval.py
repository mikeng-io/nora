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
    v = expectation_verdict((JudgeAnswer("met"), JudgeAnswer("met")))
    assert v.verdict == PASS


def test_both_judges_not_met_is_fail():
    v = expectation_verdict((JudgeAnswer("not_met"), JudgeAnswer("not_met")))
    assert v.verdict == FAIL


def test_disagreement_is_review():
    assert expectation_verdict(
        (JudgeAnswer("met"), JudgeAnswer("not_met"))).verdict == REVIEW
    assert expectation_verdict(
        (JudgeAnswer("met"), JudgeAnswer("cannot_tell"))).verdict == REVIEW
    assert expectation_verdict(
        (JudgeAnswer("cannot_tell"), JudgeAnswer("cannot_tell"))
    ).verdict == REVIEW


def test_a_judge_error_is_error_and_error_never_passes():
    v = expectation_verdict((JudgeAnswer("met"), JudgeAnswer("error")))
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

    from scripts.cognition_loop.egress import assert_no_egress
    subject = _subject(
        adapters={"signal": SimpleNamespace(_sender=object())})
    with pytest.raises(EvidenceError, match=r"lands? nowhere"):
        assert_no_egress(subject)


def test_a_bridge_runtime_is_a_refusal():
    from scripts.cognition_loop.egress import assert_no_egress
    with pytest.raises(EvidenceError, match="bridge runtime"):
        assert_no_egress(_subject(bridge_runtime=object()))


def test_the_landed_sender_passes():
    from types import SimpleNamespace
    class LandedSignalSender:
        pass
    from scripts.cognition_loop.egress import assert_no_egress
    report = assert_no_egress(_subject(
        adapters={"signal": SimpleNamespace(_sender=LandedSignalSender())}))
    assert report["senders"]["signal"] == "LandedSignalSender"


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
