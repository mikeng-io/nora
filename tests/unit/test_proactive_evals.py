"""The proactive eval suites' deterministic half (design/dreamer-agent-loop/39
§5, DEC-162 (10)): case loading and validation, the two structural graders,
the summary, the shipped corpora's shape, and the harness pieces — with no
model anywhere. The drive path is smoked with a scripted runner so the
seams (`EvalDreamTools` through the real `RegistryToolSource`, the act
drive through the real `ProactiveScreen` and `FirePath`) are exercised, not
merely typed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.proactive import (
    ActCase,
    ActResult,
    CaseTally,
    DecideCase,
    DecideResult,
    WrittenRow,
    grade_act,
    grade_decide,
    load_act_cases,
    load_decide_cases,
    summarise,
)
from nora.agent.dreamer import HomeRoom, proactive_grant
from nora.app.eval_support import EvalDreamTools, run_proactive_act_case
from nora.obs.metrics import Metrics
from nora.testing import FakeAgentRunner, InMemoryNoteStore, ScriptedTurn
from nora.testing.fakes import FakeMemoryStore
from nora.testing.reminders import FakeReminderStore

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "evals" / "cases" / "proactive"
NOW = datetime(2026, 9, 10, 23, 0, tzinfo=UTC)   # 2026-09-11 07:00 +08:00


def _decide(**over) -> dict:
    raw = {
        "name": "c", "expect": "none", "workspace": "friends",
        "now": "2026-09-11T07:00:00+08:00",
        "rooms": [{"platform": "signal", "conversation_id": "s", "label": "Friends", "home": True}],
        "notes": [{"title": "Dinner", "description": "d", "body": "b", "anchor_date": 20260912}],
    }
    raw.update(over)
    return raw


def _row(
    note: str | None = "Dinner", due="2026-09-11T15:00:00+08:00", who="nora",
    kind="proactive",
):
    return WrittenRow(
        note=note, due_at=datetime.fromisoformat(due), requester=who, kind=kind, intent="i",
    )


# ---- loading / validation ----------------------------------------------------


def test_a_bad_expect_is_refused_at_load():
    with pytest.raises(ValueError, match="expect"):
        DecideCase.from_dict(_decide(expect="maybe"))
    with pytest.raises(ValueError, match="expect"):
        ActCase.from_dict({
            "name": "a", "expect": "loud", "now": "2026-09-11T16:00:00+08:00",
            "row": {"note": "Dinner", "intent": "i"},
            "notes": [{"title": "Dinner", "description": "d", "body": "b"}],
        })


def test_a_none_case_with_rows_and_a_remind_case_without_are_refused():
    with pytest.raises(ValueError, match="no rows"):
        DecideCase.from_dict(_decide(rows=[{
            "note": "Dinner",
            "due_between": ["2026-09-11T12:00:00+08:00", "2026-09-11T18:00:00+08:00"],
        }]))
    with pytest.raises(ValueError, match="needs at least one row"):
        DecideCase.from_dict(_decide(expect="remind"))


def test_a_row_must_name_a_seeded_note_and_a_future_window():
    with pytest.raises(ValueError, match="not in the case"):
        DecideCase.from_dict(_decide(expect="remind", rows=[{
            "note": "Nope",
            "due_between": ["2026-09-11T12:00:00+08:00", "2026-09-11T18:00:00+08:00"],
        }]))
    with pytest.raises(ValueError, match="before `now`"):
        DecideCase.from_dict(_decide(expect="remind", rows=[{
            "note": "Dinner",
            "due_between": ["2026-09-10T12:00:00+08:00", "2026-09-10T18:00:00+08:00"],
        }]))


def test_exactly_one_home_room():
    with pytest.raises(ValueError, match="home room"):
        DecideCase.from_dict(_decide(rooms=[
            {"platform": "signal", "conversation_id": "s", "label": "A"},
        ]))


def test_a_silent_act_case_has_no_must_cite_and_a_speak_case_is_never_screened():
    base = {
        "name": "a", "now": "2026-09-11T16:00:00+08:00",
        "row": {"note": "Dinner", "intent": "i"},
        "notes": [{"title": "Dinner", "description": "d", "body": "b"}],
    }
    with pytest.raises(ValueError, match="must_cite"):
        ActCase.from_dict({**base, "expect": "silent", "must_cite": ["x"]})
    with pytest.raises(ValueError, match="note_moved"):
        ActCase.from_dict({**base, "expect": "speak", "note_moved": True})


# ---- the graders -------------------------------------------------------------


def _result(*rows: WrittenRow, status="ok", memory_calls: int = 1) -> DecideResult:
    """A result that ASKED memory once by default, so the window and owner
    graders below measure their own clause and not objective v2's."""
    return DecideResult(
        status=status, stop_reason=None, instruction_sha256="a" * 64, rows=rows,
        memory_calls=memory_calls,
    )


def test_decide_grades_none_and_unavailable():
    case = DecideCase.from_dict(_decide())
    assert grade_decide(case, _result()).verdict == "PASS"
    assert grade_decide(case, _result(_row())).verdict == "FAIL"
    # ERROR, never PASS: a timeout that looks like a correct `none`.
    assert grade_decide(case, _result(status="unavailable")).verdict == "ERROR"


def test_decide_grades_remind_on_exact_rows_in_their_windows():
    case = DecideCase.from_dict(_decide(expect="remind", rows=[{
        "note": "Dinner",
        "due_between": ["2026-09-11T12:00:00+08:00", "2026-09-11T18:00:00+08:00"],
    }]))
    assert grade_decide(case, _result(_row())).verdict == "PASS"
    assert grade_decide(case, _result()).reason == "no row about 'Dinner'"
    late = grade_decide(case, _result(_row(due="2026-09-11T21:00:00+08:00")))
    assert late.verdict == "FAIL" and "outside" in late.reason
    extra = grade_decide(case, _result(_row(), _row(note=None)))
    assert extra.verdict == "FAIL" and "extra" in extra.reason
    wrong_owner = grade_decide(case, _result(_row(who="miles")))
    assert wrong_owner.verdict == "FAIL" and "owner" in wrong_owner.reason
    wrong_kind = grade_decide(case, _result(_row(kind="reminder")))
    assert wrong_kind.verdict == "FAIL"


def test_act_grades_speak_silent_screened_and_error():
    speak = ActCase.from_dict({
        "name": "s", "expect": "speak", "now": "2026-09-11T16:00:00+08:00",
        "must_cite": ["數碼港"], "row": {"note": "Dinner", "intent": "i"},
        "notes": [{"title": "Dinner", "description": "d", "body": "b"}],
    })
    silent = ActCase.from_dict({
        "name": "q", "expect": "silent", "now": "2026-09-11T16:00:00+08:00",
        "row": {"note": "Dinner", "intent": "i"},
        "notes": [{"title": "Dinner", "description": "d", "body": "b"}],
    })
    said = ActResult(outcome="sent", sent=("聽日數碼港食咩？",), stop_reason="end_turn")
    quiet = ActResult(outcome="silent", sent=(), stop_reason="end_turn")
    screened = ActResult(outcome="cancelled_note_moved", sent=(), stop_reason=None,
                         screened="cancelled_note_moved")
    uncited = ActResult(outcome="sent", sent=("食飯？",), stop_reason="end_turn")
    timed_out = ActResult(outcome="failed_timeout", sent=(), stop_reason=None)

    assert grade_act(speak, said).verdict == "PASS"
    assert grade_act(speak, uncited).reason == "reply did not cite ['數碼港']"
    assert "screened" in grade_act(speak, screened).reason
    assert grade_act(silent, quiet).verdict == "PASS"
    assert grade_act(silent, screened).verdict == "PASS"
    assert grade_act(silent, said).verdict == "FAIL"
    assert grade_act(silent, timed_out).verdict == "ERROR"
    assert grade_act(speak, timed_out).verdict == "ERROR"


def test_summary_puts_the_false_positive_rate_first_and_errors_apart():
    tallies = [
        CaseTally(name="q1", expect="none", verdicts=["PASS", "PASS", "FAIL"]),
        CaseTally(name="q2", expect="silent", verdicts=["PASS", "ERROR"]),
        CaseTally(name="p1", expect="remind", verdicts=["PASS", "FAIL", "PASS"]),
        CaseTally(name="p2", expect="speak", verdicts=["ERROR", "ERROR"]),
    ]
    s = summarise(tallies)
    assert s["quiet_cases"] == 2
    assert s["false_positive_rate"] == pytest.approx(1 / 4)
    assert s["recall"] == pytest.approx(2 / 3)
    assert s["errors"] == 3
    assert s["majority_pass"] == 3          # p2 has no graded sample
    assert s["samples"] == 10


# ---- the shipped corpora -----------------------------------------------------


def test_the_decide_corpus_loads_and_is_majority_none():
    cases = load_decide_cases(CASES / "decide.yaml")
    assert len(cases) >= 10
    assert sum(c.expect == "none" for c in cases) * 2 > len(cases)
    # 39 §4's first real case is first.
    assert cases[0].name.startswith("d01_dinner")
    assert cases[0].expect == "remind"
    assert cases[0].workspace == "ridgeline"
    assert cases[0].rows[0].note == "AI Hackathon dinner plan"


def test_the_act_corpus_loads_and_is_majority_silent():
    cases = load_act_cases(CASES / "act.yaml")
    assert len(cases) >= 8
    assert sum(c.expect == "silent" for c in cases) * 2 > len(cases)
    assert any(c.note_moved for c in cases)


# ---- the harness pieces ------------------------------------------------------


def test_the_eval_tool_source_builds_remind_for_the_grant_and_never_send_reply():
    """Through the REAL `RegistryToolSource` (39 §5's «eval RegistryToolSource
    with the in-memory reminder store»): the grant admits `remind`, the
    ceiling still refuses `send_reply`, and no grant builds no act verb.
    (`where_is` is read-only and so inside the ceiling either way — the
    room-scoped axis is `verify_tool_subsets`' refusal at startup, not
    `build`'s; what the home-room grant changes for it is the ROOM.)"""
    tools = EvalDreamTools()
    home = HomeRoom(workspace="friends", platform="signal", conversation_id="s")
    tools.bind(
        workspace="friends", home=home, notes=InMemoryNoteStore(),
        reminders=FakeReminderStore(), memory=FakeMemoryStore(), clock=lambda: NOW,
    )
    asked = frozenset({"remind", "send_reply", "now", "where_is"})

    granted = tools.build(
        key="friends", names=asked, workspace="friends", adaptor="proactive",
        grant=proactive_grant(),
    )
    ungranted = tools.build(key="friends", names=asked, workspace="friends")

    def names(built):
        return {getattr(t, "tool_name", None) or getattr(t, "__name__", "") for t in built}

    assert names(granted) == {"remind", "now", "where_is"}
    assert names(ungranted) == {"now", "where_is"}
    assert tools.builds[0] == ("proactive", frozenset({"remind", "now", "where_is"}))


def test_the_eval_tool_source_refuses_to_build_before_a_case_is_bound():
    with pytest.raises(RuntimeError, match="bind"):
        EvalDreamTools().build(key="friends", names=frozenset({"now"}))


@pytest.fixture
def cfg(example_config):
    """The template config, with the doctrine and dreamer dirs pointed at the
    real files: the template's paths resolve against the copied file's own
    directory (`tmp_path`), which holds neither corpus."""
    return example_config.model_copy(update={
        "doctrine": example_config.doctrine.model_copy(
            update={"dir": ROOT / "nora" / "doctrine"},
        ),
        "dreamer": example_config.dreamer.model_copy(
            update={"dir": ROOT / "nora" / "dreamer"},
        ),
    })


async def test_the_act_drive_screens_a_moved_note_before_any_model_call(cfg):
    """39 D11 through the drive: a tag-path revision since the row was linked
    cancels it in the REAL `ProactiveScreen`, the runner is never invoked,
    and the grade reads it as silent."""
    runner = FakeAgentRunner([ScriptedTurn(tool_calls=[("send_reply", {"text": "hi"})],
                                           final_text="hi")])
    drive = await run_proactive_act_case(
        cfg, runner=runner, now=datetime(2026, 9, 11, 8, 0, tzinfo=UTC),
        row={"note": "Dinner", "intent": "ask where"}, transcript=["hello"],
        standing="dinner open",
        notes=[{"title": "Dinner", "description": "d", "body": "b", "anchor_date": 20260912}],
        note_moved=True,
    )
    assert drive.outcome == "cancelled_note_moved"
    assert drive.screened == "cancelled_note_moved"
    assert drive.sent == ()
    assert runner.invocations == 0


async def test_the_act_drive_runs_the_real_fire_path_and_reads_the_room_back(cfg):
    """A scripted runner that speaks: the drive reports `sent` and the text;
    one that declines (prose, no send under auto): `silent`, and the
    proactive silence counter moved."""
    spoke = FakeAgentRunner([ScriptedTurn(tool_calls=[("send_reply", {"text": "聽日數碼港食咩"})],
                                          final_text="聽日數碼港食咩")])
    kwargs = {
        "now": datetime(2026, 9, 11, 8, 0, tzinfo=UTC),
        "row": {"note": "Dinner", "intent": "ask where"}, "transcript": ["hello"],
        "standing": "dinner open",
        "notes": [{"title": "Dinner", "description": "d", "body": "b", "anchor_date": 20260912}],
    }
    said = await run_proactive_act_case(cfg, runner=spoke, **kwargs)
    assert said.outcome == "sent"
    assert said.sent == ("聽日數碼港食咩",)
    assert said.screened is None

    quiet = await run_proactive_act_case(
        cfg, runner=FakeAgentRunner([ScriptedTurn(final_text="nothing to add")]), **kwargs,
    )
    assert quiet.outcome == "silent"
    assert quiet.sent == ()
    assert quiet.silence == 1


def test_metrics_is_importable_for_the_drive():
    assert Metrics().get("nora_proactive_silence_total", platform="signal") == 0


async def test_the_decide_drive_seeds_every_case_shape_and_binds_the_real_tools(
    monkeypatch, cfg,
):
    """The decide drive over the shipped corpus's shapes — a dateless note, a
    struck note, a log row of every outcome, per-room Standing, memory —
    with the Strands boundary scripted to answer an empty envelope. What is
    measured is the SEEDING and the BINDING: every case renders, the real
    tool source builds `remind` for the proactive grant, and a run that
    writes nothing reads back zero rows."""
    from nora.agent import factory
    from nora.app.eval_support import build_proactive_runtime, run_proactive_decide_case

    class _Result:
        stop_reason = "end_turn"

    async def _invoke(*_a, **_kw):
        return _Result()

    monkeypatch.setattr(factory, "create_dream_agent", lambda **kw: object())
    monkeypatch.setattr(factory, "invoke", _invoke)
    monkeypatch.setattr(factory, "final_text", lambda _r: '{"decisions": []}')
    tools = EvalDreamTools()
    runtime, _label = build_proactive_runtime(cfg, client=object(), tools=tools)

    for case in load_decide_cases(CASES / "decide.yaml"):
        drive = await run_proactive_decide_case(
            runtime, tools=tools, workspace=case.workspace, now=case.now,
            rooms=case.rooms, notes=case.notes, standing=case.standing,
            log=case.log, memory=case.memory,
        )
        assert drive.status in ("nothing_found", "ok"), (case.name, drive)
        assert drive.rows == ()
        if case.notes:
            assert "remind" in drive.tools_built, case.name
            assert "send_reply" not in drive.tools_built
        assert grade_decide(case, DecideResult(
            status=drive.status, stop_reason=drive.stop_reason,
            instruction_sha256=drive.instruction_sha256, rows=(),
        )).verdict == ("PASS" if case.expect == "none" else "FAIL")


def test_a_refused_remind_attempt_fails_a_quiet_decide_case():
    """Audit 2026-09-09: the store shows only the rows the closure CREATED. A
    `none` case where the model called `remind` and was refused (too_soon,
    screened, unknown title) is a false positive the primary number must
    count, not a pass."""
    case = DecideCase.from_dict(_decide(expect="none"))
    quiet = grade_decide(case, _result())
    tried = grade_decide(case, DecideResult(
        status="ok", stop_reason=None, instruction_sha256="a" * 64, rows=(),
        remind_attempts=2,
    ))
    assert quiet.verdict == "PASS"
    assert tried.verdict == "FAIL" and "attempted" in tried.reason


def test_a_silent_failure_behind_a_silent_act_turn_is_an_error():
    """Audit 2026-09-09: `silent` with a guard-refused send or a budget cut
    behind it is a malfunction, and a malfunction is ERROR — never a PASS on
    a silent case (R-27 one layer up)."""
    case = ActCase.from_dict({
        "name": "q", "expect": "silent", "now": "2026-09-11T16:00:00+08:00",
        "row": {"note": "Dinner", "intent": "i"},
        "notes": [{"title": "Dinner", "description": "d", "body": "b"}],
    })
    quiet = grade_act(case, ActResult(outcome="silent", sent=(), stop_reason="end_turn"))
    broken = grade_act(case, ActResult(
        outcome="silent", sent=(), stop_reason="end_turn", silent_failures=1,
    ))
    assert quiet.verdict == "PASS"
    assert broken.verdict == "ERROR"


def test_a_reminder_set_without_a_memory_read_fails_the_case():
    """Objective version 2: the first live run set its reminder from the note
    body alone. A well-timed row with `memory_calls == 0` FAILS; the same row
    with one read passes."""
    case = DecideCase.from_dict(_decide(expect="remind", rows=[{
        "note": "Dinner",
        "due_between": ["2026-09-11T12:00:00+08:00", "2026-09-11T18:00:00+08:00"],
    }]))
    blind = grade_decide(case, _result(_row(), memory_calls=0))
    asked = grade_decide(case, _result(_row(), memory_calls=1))
    assert blind.verdict == "FAIL" and "memory" in blind.reason
    assert asked.verdict == "PASS"


def test_memory_calls_are_read_off_the_tools_own_counters():
    """`about`/`ask_memory` count on the room-lookup series and
    `search_memory_workspace` on the tool-call series; every status counts,
    and no other tool does."""
    from nora.app.eval_support import Metrics as _Metrics
    from nora.app.eval_support import memory_calls_from

    m = _Metrics()
    m.inc("nora_room_lookups_total", tool="about", status="ok")
    m.inc("nora_room_lookups_total", tool="ask_memory", status="unavailable")
    m.inc("nora_tool_calls_total", tool="search_memory_workspace", status="nothing_found")
    m.inc("nora_tool_calls_total", tool="note_read", status="ok")
    m.inc("nora_room_lookups_total", tool="where_is", status="ok")

    assert memory_calls_from(m) == 3
