"""`evals.backbone`'s graders — pure, no network/DB, cheap to pin.

`AbsenceGrader`: regression for the gap found while re-running
`design/evaluation/GOAL-2-backbone.md`'s Step 0 falsification against
`relevance_gate`: it read only `NORA_SAID`, which the `gate` entry point
never populates (it answers `admitted`, not `said`), so it graded every gate
case as silence regardless of the actual verdict. `NORA_ADMITTED` fixes it.

`ContextGrader`: S4 (`10-the-scenario-map.md` §3) — deterministic, on
`NORA_BLOCKS_EMPTY`.

`render_transcript_at`/`SubstringGrader`: S5 — deterministic, calling the
REAL `nora.domain.blocks.render_messages`/`nora.domain.prompt.age` directly
(pure functions, no `Subject`/database/network).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from strands_evals import Case, Experiment
from strands_evals.evaluators.evaluator import Evaluator
from strands_evals.types.evaluation import EnvironmentState, EvaluationData, EvaluationOutput
from strands_evals.types.trace import Session

import evals.backbone as backbone
from evals.backbone import (
    ERROR,
    FAIL,
    NORA_ADMITTED,
    NORA_BLOCKS_EMPTY,
    NORA_CHANGED_COUNT_2,
    NORA_ERROR,
    NORA_ERROR_2,
    NORA_SAID,
    NORA_STAGED_COUNT,
    NORA_STAGED_COUNT_2,
    NORA_WRITTEN_2,
    PASS,
    UNSTABLE,
    AbsenceGrader,
    ContextGrader,
    IdempotenceGrader,
    SampleReading,
    Spec,
    SubstringGrader,
    render_transcript_at,
    sample_read,
    sampled,
    state_of,
    traced_nora_task,
)
from evals.rebuilt.domain import Outcome
from evals.rebuilt.entry import ENTRY_POINTS, EntryPoint

grader = AbsenceGrader()
context_grader = ContextGrader()
substring_grader = SubstringGrader()
idempotence_grader = IdempotenceGrader()


def _data(
    *, expect: str, admitted: bool | None = None, said: int = 0, error: str = "",
) -> EvaluationData:
    return EvaluationData(
        input="x",
        metadata={"expect": expect},
        actual_environment_state=[
            EnvironmentState(name=NORA_ERROR, state=error),
            EnvironmentState(name=NORA_SAID, state=said),
            EnvironmentState(name=NORA_ADMITTED, state=admitted),
        ],
    )


def test_a_gate_no_go_case_that_was_admitted_fails():
    """THE BUG: before `NORA_ADMITTED`, this scored PASS — `said` is always
    `0` for a gate case, so any `no_go`-expecting case looked silent no
    matter what the gate actually decided."""
    out = grader.evaluate(_data(expect="no_go", admitted=True))[0]
    assert out.label == FAIL


def test_a_gate_no_go_case_that_was_refused_passes():
    out = grader.evaluate(_data(expect="no_go", admitted=False))[0]
    assert out.label == PASS


def test_a_gate_go_case_that_was_admitted_passes():
    out = grader.evaluate(_data(expect="go", admitted=True))[0]
    assert out.label == PASS


def test_a_gate_go_case_that_was_refused_fails():
    out = grader.evaluate(_data(expect="go", admitted=False))[0]
    assert out.label == FAIL


def test_a_participation_style_case_is_unaffected_by_the_gate_signal():
    """`admitted=None` (never ran a gate) must fall back to `said`, exactly
    as before this fix — `participation`/`tag`/`dreamer` cases carry no
    `NORA_ADMITTED` signal at all."""
    assert grader.evaluate(_data(expect="silence", admitted=None, said=0))[0].label == PASS
    assert grader.evaluate(_data(expect="silence", admitted=None, said=1))[0].label == FAIL
    assert grader.evaluate(_data(expect="speak", admitted=None, said=1))[0].label == PASS
    assert grader.evaluate(_data(expect="speak", admitted=None, said=0))[0].label == FAIL


def test_an_error_is_never_a_pass_regardless_of_the_admitted_signal():
    out = grader.evaluate(_data(expect="no_go", admitted=False, error="boom"))[0]
    assert out.label == ERROR


def test_a_gate_case_under_either_passes_on_either_verdict():
    """M1 (audit): the `either` branch was fixed for `silence`/`speak` in
    `747348bd` but not for itself — a gate case with `expect: either` and
    `admitted=False` (a correct, successful refusal) fell through to "not
    spoke" and scored ERROR, "nothing ran and nothing was said", when the
    gate had in fact run and decided. Both verdicts are real answers for a
    gate; only the absence of `NORA_ERROR` distinguishes a real refusal from
    nothing having happened, and that is checked first."""
    assert grader.evaluate(_data(expect="either", admitted=True))[0].label == PASS
    assert grader.evaluate(_data(expect="either", admitted=False))[0].label == PASS


def _context_data(
    *, must_render: tuple[str, ...], blocks_empty: str | None = "", error: str = "",
) -> EvaluationData:
    return EvaluationData(
        input="x",
        metadata={"must_render": must_render},
        actual_environment_state=[
            EnvironmentState(name=NORA_ERROR, state=error),
            EnvironmentState(name=NORA_BLOCKS_EMPTY, state=blocks_empty),
        ],
    )


def test_a_block_the_case_needed_but_came_back_empty_fails():
    out = context_grader.evaluate(
        _context_data(must_render=("standing",), blocks_empty="notes,standing"),
    )[0]
    assert out.label == FAIL
    assert "standing" in out.reason


def test_blocks_the_case_did_not_ask_about_do_not_affect_the_verdict():
    out = context_grader.evaluate(
        _context_data(must_render=("standing",), blocks_empty="notes,self_card"),
    )[0]
    assert out.label == PASS


def test_a_case_naming_no_block_is_ungradable():
    out = context_grader.evaluate(_context_data(must_render=()))[0]
    assert out.label == ERROR


def test_a_context_error_is_never_a_pass():
    out = context_grader.evaluate(
        _context_data(must_render=("standing",), blocks_empty="", error="boom"),
    )[0]
    assert out.label == ERROR


def test_no_prefill_span_captured_is_an_error_not_a_pass():
    """H1, found by BOTH independent reviews: `None` (no `agent.prefill` span
    seen at all — the entry point never calls `build_prefill`, or the
    capture itself failed) and `""` (a span WAS seen and nothing was empty)
    collapsed to the same value before this fix, so a `gate`/`dreamer` case
    mistakenly given `must_render`, or any capture failure, scored a clean
    PASS with a positively-worded reason."""
    out = context_grader.evaluate(
        _context_data(must_render=("standing",), blocks_empty=None),
    )[0]
    assert out.label == ERROR


def _time_case(*, minutes_ago: int, expect_age: str) -> Case:
    return Case(
        name="s5",
        input="x",
        expected_output=f'age="{expect_age}"',
        metadata={
            "now": "2026-09-19T12:00:00+00:00",
            "rows": [{"body": "去邊食", "name": "Grant", "minutes_ago": minutes_ago}],
        },
    )


def _rendered(case: Case) -> EvaluationData:
    """Drive the REAL `render_transcript_at`, then wrap its actual returned
    dict — output-only on success, `environment_state` on a caught error —
    into a real `EvaluationData` (review, 2026-09-19: the earlier hand-rolled
    stub here had no `actual_environment_state` attribute at all, so an
    SDK/grader change checking for one would not have reddened this file —
    it silently AttributeErrored the moment `SubstringGrader` gained an
    error check, which is exactly the kind of gap L14 named)."""
    result = render_transcript_at(case)
    return EvaluationData(
        input=case.input,
        actual_output=result.get("output"),
        expected_output=case.expected_output,
        actual_environment_state=result.get("environment_state"),
    )


def test_a_row_181_minutes_old_renders_3h_ago():
    """The real `nora.domain.prompt.age` boundary: `seconds < 5400` (90 min)
    is minutes, `< 172800` (48h) is hours. 181 minutes = 10860s, hours."""
    out = substring_grader.evaluate(_rendered(_time_case(minutes_ago=181, expect_age="3h ago")))[0]
    assert out.label == PASS


def test_a_row_1_minute_old_renders_just_now():
    out = substring_grader.evaluate(_rendered(_time_case(minutes_ago=1, expect_age="just now")))[0]
    assert out.label == PASS


def test_the_wrong_expected_age_fails_rather_than_passing_silently():
    """Falsification: a substring check that always passes would be
    decoration. A deliberately wrong expectation must FAIL."""
    out = substring_grader.evaluate(_rendered(_time_case(minutes_ago=181, expect_age="5h ago")))[0]
    assert out.label == FAIL


def test_a_case_with_no_expected_output_is_ungradable():
    case = _time_case(minutes_ago=181, expect_age="3h ago")
    case.expected_output = ""
    out = substring_grader.evaluate(_rendered(case))[0]
    assert out.label == ERROR


def test_render_transcript_at_never_raises_on_a_malformed_case():
    """H2 (review): this task raised on a malformed case before — missing
    `now`, an unparseable timestamp, a row missing a key — and
    `Experiment._worker` (module docstring) turns a raising task into
    `test_pass=False, score=0` for every evaluator WITHOUT writing a row to
    `detailed_results`, so the case vanishes from `read()`'s count rather
    than landing in it as a FAIL. `render_transcript_at` must catch it and
    report an ERROR instead."""
    case = Case(name="malformed", input="x", expected_output="x", metadata={})  # no `now`
    out = substring_grader.evaluate(_rendered(case))[0]
    assert out.label == ERROR


def _idempotence_data(
    *, written_2: int | None, changed_2: int = 0, staged_1: int = 0, staged_2: int = 0,
    error: str = "",
) -> EvaluationData:
    return EvaluationData(
        input="x",
        actual_environment_state=[
            EnvironmentState(name=NORA_ERROR, state=error),
            EnvironmentState(name=NORA_WRITTEN_2, state=written_2),
            EnvironmentState(name=NORA_CHANGED_COUNT_2, state=changed_2),
            EnvironmentState(name=NORA_STAGED_COUNT, state=staged_1),
            EnvironmentState(name=NORA_STAGED_COUNT_2, state=staged_2),
        ],
    )


def test_no_repeat_run_is_ungradable():
    out = idempotence_grader.evaluate(_idempotence_data(written_2=None))[0]
    assert out.label == ERROR


def test_a_second_pass_that_wrote_nothing_new_passes():
    out = idempotence_grader.evaluate(
        _idempotence_data(written_2=0, changed_2=0, staged_1=3, staged_2=3),
    )[0]
    assert out.label == PASS


def test_a_second_pass_that_wrote_something_new_fails():
    """THE BUG S6 exists to catch: #236/#338, nothing bounds a duplicate
    standing note or reminder."""
    out = idempotence_grader.evaluate(
        _idempotence_data(written_2=1, changed_2=0, staged_1=3, staged_2=3),
    )[0]
    assert out.label == FAIL


def test_a_staged_count_that_moved_fails_even_with_nothing_written():
    """`staged` is a snapshot, not a diff — written_2=0 alone cannot clear it."""
    out = idempotence_grader.evaluate(
        _idempotence_data(written_2=0, changed_2=0, staged_1=3, staged_2=4),
    )[0]
    assert out.label == FAIL


def test_an_idempotence_error_is_never_a_pass():
    out = idempotence_grader.evaluate(_idempotence_data(written_2=0, error="boom"))[0]
    assert out.label == ERROR


def test_traced_nora_task_adds_a_trajectory_onto_nora_tasks_own_shape():
    """S2's whole build: `eval_task(TracedHandler())(nora_task(spec))` must
    add `trajectory` without disturbing `nora_task`'s own `output`/
    `environment_state` keys. A bad database_url exercises the full
    before/after wrap without needing a live database — `_drive()` raises
    inside `obtain()`, `nora_task`'s own catch turns that into the usual
    ERROR-shaped dict, and this only checks that the WRAPPING survives it.
    """
    spec = Spec(database_url="postgresql://nope:nope@localhost:1/nope", entry="participation")
    task, _handler = traced_nora_task(spec)

    case = Case(name="wiring-probe", input="x", session_id="wiring-probe")
    result = task(case)

    assert "trajectory" in result
    assert isinstance(result["trajectory"], Session)
    assert "output" in result
    assert "environment_state" in result
    assert state_of(
        EvaluationData(input="x", actual_environment_state=result["environment_state"]),
        NORA_ERROR,
    )


# --- `_drive()`'s own mechanisms — found by review, 2026-09-19, to have
# ZERO test coverage: gutting `_prefill_capture()` or disabling
# `Spec.repeat`'s loop entirely left every test above green, because they
# all exercise the GRADERS on hand-built `EvaluationData`, never `_drive()`
# itself. These use fake ports/obtain/know to exercise the real `_drive()`
# without a live database. -----------------------------------------------


class _FakeAwaitable:
    async def cancel_all(self) -> None: ...
    async def aclose(self) -> None: ...
    async def close(self) -> None: ...


class _FakeDeps:
    background = _FakeAwaitable()
    audit = _FakeAwaitable()
    client = _FakeAwaitable()


class _FakeSubject:
    deps = _FakeDeps()
    room = ("signal", "g1")


async def _fake_obtain(_spec: object) -> _FakeSubject:
    return _FakeSubject()


async def _fake_know(*_args: object, **_kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(now_ms=1_700_000_000_000)


async def test_drive_calls_port_drive_repeat_times(monkeypatch):
    """The bug the audit found by disabling `if spec.repeat > 1:` entirely:
    every existing test stayed green. This drives `_drive()` for real, with
    a fake port counting its own calls."""
    calls: list[int] = []

    async def fake_drive(_subject: object, _case: object, *, now_ms: int) -> Outcome:
        calls.append(now_ms)
        return Outcome(admitted=None, written=len(calls), changed=(), staged=())

    monkeypatch.setitem(ENTRY_POINTS, "dreamer", EntryPoint(drive=fake_drive))
    monkeypatch.setattr(backbone, "obtain", _fake_obtain)
    monkeypatch.setattr(backbone, "know", _fake_know)

    case = Case(name="repeat-probe", input="x")
    result = await backbone._drive(case, Spec(database_url="x", entry="dreamer", repeat=3))

    assert len(calls) == 3, "port.drive must run once, then twice more for repeat=3"
    data = EvaluationData(input="x", actual_environment_state=result["environment_state"])
    assert state_of(data, NORA_WRITTEN_2) == 3, "the LAST run's Outcome, not the first's"


async def test_drive_refuses_repeat_for_an_entry_point_that_delivers_input(monkeypatch):
    async def fake_drive(_subject: object, _case: object, *, now_ms: int) -> Outcome:
        return Outcome(admitted=None)

    monkeypatch.setitem(
        ENTRY_POINTS, "tag", EntryPoint(drive=fake_drive, delivers_input=True),
    )
    monkeypatch.setattr(backbone, "obtain", _fake_obtain)
    monkeypatch.setattr(backbone, "know", _fake_know)

    case = Case(name="repeat-tag-probe", input="x")
    try:
        await backbone._drive(case, Spec(database_url="x", entry="tag", repeat=2))
    except ValueError as exc:
        assert "re-deliver" in str(exc)
    else:
        raise AssertionError("repeat>1 on a delivers_input entry point must refuse loudly")


async def test_drive_captures_a_real_prefill_span_emitted_during_the_drive(monkeypatch):
    """The audit's other zero-coverage finding: swapping `_prefill_capture()`
    for a fresh, unattached exporter also left every test green. This drives
    `_drive()` for real and has the fake port open a genuine
    `nora.obs.tracing.span("agent.prefill", ...)` the way `build_prefill`
    does, checking `_drive()` actually reads it back."""
    from nora.obs.tracing import annotate, span

    async def fake_drive(_subject: object, _case: object, *, now_ms: int) -> Outcome:
        with span("agent.prefill"):
            annotate(**{"nora.prefill.blocks_empty": "notes,standing"})
        return Outcome(admitted=None, said=("hi",))

    monkeypatch.setitem(
        ENTRY_POINTS, "participation", EntryPoint(drive=fake_drive, delivers_input=True),
    )
    monkeypatch.setattr(backbone, "obtain", _fake_obtain)
    monkeypatch.setattr(backbone, "know", _fake_know)

    case = Case(name="prefill-probe", input="x")
    result = await backbone._drive(case, Spec(database_url="x", entry="participation"))

    data = EvaluationData(input="x", actual_environment_state=result["environment_state"])
    assert state_of(data, NORA_BLOCKS_EMPTY) == "notes,standing"


async def test_idempotence_grader_reads_the_repeat_runs_own_error(monkeypatch):
    """H3 (review): the repeat run's OWN error was never carried, so a repeat
    that failed closed (written=0) could score PASS as 'added nothing'."""
    calls = {"n": 0}

    async def fake_drive(_subject: object, _case: object, *, now_ms: int) -> Outcome:
        calls["n"] += 1
        if calls["n"] == 1:
            return Outcome(admitted=None, written=1, changed=(("create", "x"),))
        return Outcome(admitted=None, error="fail_closed: no provider reachable")

    monkeypatch.setitem(ENTRY_POINTS, "dreamer", EntryPoint(drive=fake_drive))
    monkeypatch.setattr(backbone, "obtain", _fake_obtain)
    monkeypatch.setattr(backbone, "know", _fake_know)

    case = Case(name="repeat-error-probe", input="x")
    result = await backbone._drive(case, Spec(database_url="x", entry="dreamer", repeat=2))

    data = EvaluationData(input="x", actual_environment_state=result["environment_state"])
    assert state_of(data, NORA_ERROR_2)
    out = IdempotenceGrader().evaluate(data)[0]
    assert out.label == ERROR, "a repeat run that broke must never read as idempotent"


async def test_drive_seeds_from_the_cases_own_knows_metadata(monkeypatch):
    """Found while moving `_knowledge`/`_close` out of `evals/rebuilt/runner.py`
    (2026-09-20): `_drive()` called `_knowledge({})` unconditionally, ignoring
    whatever a case's own `metadata["knows"]` stated. Every S4/S5/S6 case this
    session wrote happened to seed nothing, so the bug never showed. A future
    case that seeds a transcript needs this read for real."""
    seeded_with: dict[str, Any] = {}

    async def fake_drive(_subject: object, _case: object, *, now_ms: int) -> Outcome:
        return Outcome(admitted=None)

    async def capturing_know(
        _subject: object, knowledge: object, **_kwargs: object,
    ) -> SimpleNamespace:
        seeded_with["transcript"] = knowledge.transcript
        return SimpleNamespace(now_ms=1_700_000_000_000)

    monkeypatch.setitem(ENTRY_POINTS, "dreamer", EntryPoint(drive=fake_drive))
    monkeypatch.setattr(backbone, "obtain", _fake_obtain)
    monkeypatch.setattr(backbone, "know", capturing_know)

    case = Case(
        name="knows-probe", input="x",
        metadata={"knows": {"transcript": ["今日食咩好呢"]}},
    )
    await backbone._drive(case, Spec(database_url="x", entry="dreamer"))

    assert len(seeded_with["transcript"]) == 1
    assert seeded_with["transcript"][0].body == "今日食咩好呢"


async def test_drive_reads_room_now_policy_config_from_case_metadata(monkeypatch):
    """The port (`10-the-scenario-map.md`, 2026-09-20): `proactive_decide.yaml`
    names a real room/policy/now PER CASE — which room she picks IS the
    assertion, and whether a note is due IS about time — which one `Spec`
    shared across a whole `Experiment` batch cannot express. A ported case
    states these in `metadata`; `_drive()` must read them, falling through
    to `Spec`'s own defaults for a case that states none (every S4/S5/S6/S2
    case above)."""
    captured: dict[str, Any] = {}

    async def capturing_obtain(spec: Any) -> _FakeSubject:
        captured["spec"] = spec
        return _FakeSubject()

    async def fake_drive(_subject: object, _case: object, *, now_ms: int) -> Outcome:
        return Outcome(admitted=None)

    monkeypatch.setitem(ENTRY_POINTS, "dreamer", EntryPoint(drive=fake_drive))
    monkeypatch.setattr(backbone, "obtain", capturing_obtain)
    monkeypatch.setattr(backbone, "know", _fake_know)

    case = Case(
        name="d01_dinner_note_the_day_before",
        input="x",
        metadata={
            "room": ["signal", "ridgeline-sig"],
            "now": "2026-09-11T07:00:00+08:00",
            "config": {"memory.workspace_id": "ridgeline", "notes.enabled": True},
            "policy": (
                'default = "deny"\n\n[[conversations]]\nplatform = "{platform}"\n'
                'id = "{room}"\npersons = "*"\nworkspace = "ridgeline"\n'
                "proactive = true\nhome_room = true\n"
            ),
        },
    )
    await backbone._drive(case, Spec(database_url="x", entry="dreamer"))

    spec = captured["spec"]
    assert spec.room == ("signal", "ridgeline-sig")
    assert spec.now.isoformat() == "2026-09-11T07:00:00+08:00"
    assert spec.overrides["notes.enabled"] is True
    assert 'id = "ridgeline-sig"' in spec.policy
    assert 'platform = "signal"' in spec.policy
    # the isolated workspace, not the case's own stated "ridgeline" literally —
    # `workspace_for` derives a unique name so cases sharing a database do
    # not accumulate notes in each other's workspace.
    assert spec.overrides["memory.workspace_id"] != "ridgeline"


async def test_drive_falls_through_to_spec_when_a_case_states_no_room_or_now(monkeypatch):
    """A case that states none of `room`/`now`/`policy`/`config` (every
    hand-written S4/S5/S6/S2 case above) must be unaffected by the port."""
    captured: dict[str, Any] = {}

    async def capturing_obtain(spec: Any) -> _FakeSubject:
        captured["spec"] = spec
        return _FakeSubject()

    async def fake_drive(_subject: object, _case: object, *, now_ms: int) -> Outcome:
        return Outcome(admitted=None)

    monkeypatch.setitem(ENTRY_POINTS, "dreamer", EntryPoint(drive=fake_drive))
    monkeypatch.setattr(backbone, "obtain", capturing_obtain)
    monkeypatch.setattr(backbone, "know", _fake_know)

    case = Case(name="plain-case", input="x")
    spec_in = Spec(database_url="x", entry="dreamer", room=("signal", "fixed-room"))
    await backbone._drive(case, spec_in)

    spec = captured["spec"]
    assert spec.room == ("signal", "fixed-room")
    assert spec.now is None
    assert spec.policy == ""


# `sampled()`/`sample_read()`: `09-the-backbone-measured.md` §3.1 measured two
# model-dependent cases SWAP verdicts across two runs at `sample: 1` — the SDK
# has no repeat parameter (`08-the-sdk-surface.md` §4.4), and `Case`/caching
# key on `case_name`, so these tests drive a REAL `Experiment` (matching the
# probe that found `report.cases`/`report.detailed_results` are flattened per
# (case x evaluator) pair, not per case) rather than asserting the shape.


_labelled_seq = iter(range(1_000_000))


class _Labelled(Evaluator):
    """Always answers the same label, regardless of which draw is asked."""

    def __init__(self, label: str) -> None:
        super().__init__(name=f"labelled-{label}-{next(_labelled_seq)}")
        self._label = label

    def evaluate(self, evaluation_data: EvaluationData) -> list[EvaluationOutput]:
        test_pass = self._label == PASS
        score = 1.0 if test_pass else 0.0
        return [EvaluationOutput(score=score, test_pass=test_pass, label=self._label)]


class _ByDrawName(Evaluator):
    """Answers a label keyed by the draw's own (sampled-suffixed) name — the
    shape a real stochastic grader takes when successive draws disagree."""

    def __init__(self, labels: dict[str, str]) -> None:
        super().__init__(name="by-draw-name")
        self._labels = labels

    def evaluate(self, evaluation_data: EvaluationData) -> list[EvaluationOutput]:
        label = self._labels[evaluation_data.name]
        test_pass = label == PASS
        return [EvaluationOutput(score=1.0 if test_pass else 0.0, test_pass=test_pass, label=label)]


def test_sampled_draws_are_uniquely_named_off_the_base_case():
    base = Case(name="speak-1", input="x")
    draws = sampled(base, 3)
    assert [d.name for d in draws] == ["speak-1#1", "speak-1#2", "speak-1#3"]
    assert [d.session_id for d in draws] == ["speak-1#1", "speak-1#2", "speak-1#3"]


def test_base_name_reverses_a_sampled_suffix():
    assert backbone._base_name("speak-1#1") == "speak-1"
    assert backbone._base_name("speak-1#12") == "speak-1"


def test_base_name_is_unchanged_for_a_name_sampled_never_drew():
    assert backbone._base_name("speak-1") == "speak-1"
    assert backbone._base_name("room#general") == "room#general", (
        "a '#' not followed by a digit is not a sampled() suffix"
    )


def test_sample_read_all_draws_agreeing_reads_the_shared_verdict():
    base = Case(name="speak-1", input="x")
    cases = [*sampled(base, 2), Case(name="silence-1", input="y")]
    experiment = Experiment(cases=cases, evaluators=[_Labelled(PASS)])
    report = experiment.run_evaluations(task=lambda case: {})

    readings = sample_read(report)

    assert readings["speak-1"] == SampleReading(verdict=PASS, passed=2, failed=0, errored=0)
    assert readings["silence-1"] == SampleReading(verdict=PASS, passed=1, failed=0, errored=0)


def test_sample_read_disagreement_reads_unstable_not_a_majority_vote():
    """The point of `sample_read()`: two draws splitting PASS/FAIL must never
    collapse into whichever one happened to win — that would hide exactly the
    instability `09-the-backbone-measured.md` §3.1 measured."""
    base = Case(name="speak-1", input="x")
    draws = sampled(base, 2)
    labels = {draws[0].name: PASS, draws[1].name: FAIL}
    experiment = Experiment(cases=draws, evaluators=[_ByDrawName(labels)])
    report = experiment.run_evaluations(task=lambda case: {})

    reading = sample_read(report)["speak-1"]

    assert reading.verdict == UNSTABLE
    assert (reading.passed, reading.failed, reading.errored) == (1, 1, 0)


def test_sample_read_every_draw_erroring_reads_error_not_unstable():
    """Rule 3 (ERROR is not FAIL and is never PASS): a base case whose every
    draw errored reads ERROR, not UNSTABLE — it never once produced a verdict
    to disagree about."""
    base = Case(name="speak-1", input="x")
    draws = sampled(base, 2)
    experiment = Experiment(cases=draws, evaluators=[_Labelled(ERROR)])
    report = experiment.run_evaluations(task=lambda case: {})

    assert sample_read(report)["speak-1"].verdict == ERROR


def test_sample_read_groups_across_multiple_evaluators_per_draw():
    """`report.detailed_results` is flattened per (case x evaluator), not
    per case — 2 draws x 2 evaluators must still group back to one base."""
    base = Case(name="speak-1", input="x")
    draws = sampled(base, 2)
    experiment = Experiment(cases=draws, evaluators=[_Labelled(PASS), _Labelled(PASS)])
    report = experiment.run_evaluations(task=lambda case: {})

    reading = sample_read(report)["speak-1"]
    assert reading == SampleReading(verdict=PASS, passed=4, failed=0, errored=0)
