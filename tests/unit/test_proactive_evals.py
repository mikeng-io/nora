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
    act_case_ok,
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
        "name": "c", "expect": "none", "unit": "friends",
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


def test_act_fails_a_reply_that_says_what_it_must_not():
    """Issue #226's grading half. `must_cite` can only say a reply reached its
    subject; both failures this suite now measures are replies that reached the
    subject the WRONG WAY — one carrying her own bookkeeping's vocabulary, one
    repeating a note's summary where the transcript in the same prompt says
    otherwise. Checked BEFORE `must_cite`, so the reason names the real defect
    rather than a missing token; case-insensitively, because «Note» must not
    pass where «note» fails; and on either expectation, with a silent room
    satisfying it vacuously."""
    case = ActCase.from_dict({
        "name": "b", "expect": "speak", "now": "2026-09-11T16:00:00+08:00",
        "must_cite": ["報名"], "must_not_cite": ["open note", "灣仔"],
        "row": {"note": "Dinner", "intent": "i"},
        "notes": [{"title": "Dinner", "description": "d", "body": "b"}],
    })
    clean = ActResult(outcome="sent", sent=("報名截今晚,報咗未？",), stop_reason="end_turn")
    machinery = ActResult(
        outcome="sent", sent=("見到個 Open Note 未 close,報名截今晚",),
        stop_reason="end_turn",
    )
    stale = ActResult(outcome="sent", sent=("報名同埋灣仔嗰間,幾點？",), stop_reason="end_turn")
    quiet = ActResult(outcome="silent", sent=(), stop_reason="end_turn")

    assert grade_act(case, clean).verdict == "PASS"
    bad = grade_act(case, machinery)
    # Case-INSENSITIVELY: «Open Note» must not pass where «open note» fails.
    assert bad.verdict == "FAIL" and "open note" in bad.reason
    assert grade_act(case, stale).verdict == "FAIL"
    # Silence is graded by the expectation, never by the ban list.
    assert grade_act(case, quiet).verdict == "FAIL"
    assert "heard nothing" in grade_act(case, quiet).reason


def test_an_either_case_grades_the_message_and_is_unmeasured_on_silence():
    """Issue #226's third expectation. On both cases it was written for,
    speaking and staying quiet are defensible and she splits — 2/5 and 3/5
    spoken over identical prompts — so a `speak` label would have graded the
    threshold instead of the defect. Silence is never PASS: there was no
    message, so the thing the case measures did not happen, and an unmeasured
    sample reading as a pass is R-27's forbidden substitution.

    It is not ERROR either, and the distinction is the whole of the fourth
    verdict: ERROR means «something went wrong», UNMEASURED means «there was
    nothing to grade». An outage still outranks both."""
    case = ActCase.from_dict({
        "name": "e", "expect": "either", "now": "2026-09-11T16:00:00+08:00",
        "must_cite": ["觀塘"], "must_not_cite": ["灣仔"],
        "row": {"note": "Dinner", "intent": "i"},
        "notes": [{"title": "Dinner", "description": "d", "body": "b"}],
    })
    grounded = ActResult(outcome="sent", sent=("聽日觀塘,幾點？",), stop_reason="end_turn")
    stale = ActResult(outcome="sent", sent=("聽日灣仔,幾點？",), stop_reason="end_turn")
    off_topic = ActResult(outcome="sent", sent=("食咗飯未？",), stop_reason="end_turn")
    quiet = ActResult(outcome="silent", sent=(), stop_reason="end_turn")

    assert grade_act(case, grounded).verdict == "PASS"
    assert grade_act(case, stale).verdict == "FAIL"
    assert grade_act(case, off_topic).verdict == "FAIL"
    silent = grade_act(case, quiet)
    assert silent.verdict == "UNMEASURED" and "measured nothing" in silent.reason
    # And an outage still outranks it: `failed_*` is ERROR for its own reason.
    assert grade_act(
        case, ActResult(outcome="failed_timeout", sent=(), stop_reason=None)
    ).verdict == "ERROR"
    # As does a refused send: silence the guard imposed is not silence she chose.
    assert grade_act(
        case, ActResult(outcome="silent", sent=(), stop_reason="end_turn",
                        silent_failures=1)
    ).verdict == "ERROR"


def test_act_case_ok_is_the_whole_act_verdict_and_every_arm_is_pinned():
    """**The function that flips the act gate's PASS/FAIL, and it shipped with
    no guard at all.** It lives in `evals.proactive` rather than inline in
    `evals.run._run_proactive_act_live` for exactly that reason: reverting the
    `either` arm there turned nothing red, because every test importing the
    runner (five files, 90 tests) reaches the live driver through nothing.

    Falsification, per arm, is to flip that arm's line and re-run this test:

      - drop `"FAIL" in verdicts` and the `silent` row with one speak in five
        goes green — a room that heard it, waved through by a majority;
      - grade `either` on `majority_pass` alone (what shipped) and the
        fully-quiet row goes RED on behaviour `act.yaml` itself calls
        defensible;
      - return True for `fully_unmeasured` on ANY expectation and the quiet
        `speak` row goes green — silence is the defect there, not an abstention;
      - let one ERROR count as unmeasured and the outage row goes green.
    """
    def tally(expect: str, *verdicts: str) -> CaseTally:
        return CaseTally(name=expect, expect=expect, verdicts=list(verdicts))

    # A `silent` case fails on ONE speak in five: the room heard it.
    assert act_case_ok(tally("silent", "PASS", "PASS", "PASS", "PASS", "PASS"))
    assert not act_case_ok(tally("silent", "PASS", "PASS", "FAIL", "PASS", "PASS"))
    # A `speak` case binds on the majority — one quiet sample is a threshold.
    assert act_case_ok(tally("speak", "PASS", "PASS", "PASS", "FAIL", "PASS")) is False
    assert act_case_ok(tally("speak", "PASS", "PASS", "ERROR"))
    # An `either` case fails on ONE bad message, for the `silent` case's reason.
    assert act_case_ok(tally("either", "PASS", "UNMEASURED", "PASS"))
    assert not act_case_ok(tally("either", "PASS", "UNMEASURED", "FAIL"))
    # Fully unmeasured: not a failure, because the YAML declares the silence
    # defensible — but only for `either`, and only with no outage in the run.
    assert act_case_ok(tally("either", *["UNMEASURED"] * 5))
    assert not act_case_ok(tally("either", *["UNMEASURED"] * 4, "ERROR"))
    assert not act_case_ok(tally("speak", *["UNMEASURED"] * 5))
    # And it never reads as a pass silently: the caller has the count.
    assert tally("either", *["UNMEASURED"] * 5).fully_unmeasured is True
    assert tally("either", *["UNMEASURED"] * 4, "ERROR").fully_unmeasured is False


def test_an_unmeasured_sample_is_not_an_outage_in_the_summary():
    """W1's inversion, pinned. `_print_proactive_gate` promises that «an outage
    during a run cannot read as a quiet model»; folding UNMEASURED into
    `errors` made the inverse true instead, and a branch run with zero outages
    reported `errors 9`. The two counts are separate, both are out of both
    rates, and `majority_pass` counts neither as graded."""
    tallies = [
        CaseTally(name="q1", expect="silent", verdicts=["PASS", "PASS"]),
        CaseTally(name="p1", expect="speak", verdicts=["PASS", "ERROR"]),
        CaseTally(name="e1", expect="either",
                  verdicts=["UNMEASURED", "UNMEASURED", "PASS"]),
        CaseTally(name="e2", expect="either", verdicts=["UNMEASURED", "UNMEASURED"]),
    ]
    s = summarise(tallies)
    assert s["errors"] == 1              # the outage, and only the outage
    assert s["unmeasured"] == 4
    assert s["unmeasured_cases"] == 1    # e2 only: e1 has a graded sample
    # Neither count reaches a rate, and neither is graded for the majority.
    assert s["false_positive_rate"] == pytest.approx(0.0)
    assert s["recall"] == pytest.approx(1.0)
    assert tallies[2].majority_pass is True   # 1 of 1 graded
    assert tallies[3].majority_pass is False  # nothing graded at all


def test_a_banned_term_must_not_fire_on_a_word_that_merely_contains_it():
    """`ActCase.must_not_cite` sets its own bar — a term «must be chosen to
    appear only when the reply is actually wrong» — and bare `note` as a plain
    substring failed it, banning «noted», «notebook» and «denote» along with
    the apparatus.

    The anchor is an ASCII-class lookaround, NOT `\\b`, and the last assertion
    here is why: `\\b` is defined over `\\w`, which CJK satisfies, so `\\bnote\\b`
    would silently stop matching 「我個note仲未close」 — an English word pressed
    against Chinese with no space, which is how this room actually writes.
    A CJK end has nothing to anchor on and stays a plain substring."""
    case = ActCase.from_dict({
        "name": "w", "expect": "either", "now": "2026-09-11T16:00:00+08:00",
        "must_not_cite": ["note", "提醒自己"],
        "row": {"note": "Dinner", "intent": "i"},
        "notes": [{"title": "Dinner", "description": "d", "body": "b"}],
    })

    def said(text: str) -> str:
        return grade_act(case, ActResult(
            outcome="sent", sent=(text,), stop_reason="end_turn")).verdict

    assert said("Noted,聽日見") == "PASS"
    assert said("我 denote 咗個 notebook") == "PASS"
    assert said("我個 note 仲未 close") == "FAIL"
    assert said("my NOTE says 灣仔") == "FAIL"
    # No space, CJK on both sides — `\b` would NOT match this, and this is the
    # spelling a Cantonese reply is most likely to use.
    assert said("我個note仲未close") == "FAIL"
    # CJK: matched as a substring, which is why the term is the long one.
    assert said("我提醒自己問你") == "FAIL"
    assert said("提醒你聽日") == "PASS"


def test_an_either_case_belongs_to_neither_rate():
    """It labels no decision, so counting it as quiet would invent a false
    positive out of every message it approves of, and counting it as positive
    would credit recall to a case that never asked her to act."""
    tallies = [
        CaseTally(name="q1", expect="silent", verdicts=["PASS", "FAIL"]),
        CaseTally(name="p1", expect="speak", verdicts=["PASS", "PASS"]),
        CaseTally(name="e1", expect="either", verdicts=["PASS", "FAIL", "ERROR"]),
    ]
    s = summarise(tallies)
    assert s["message_only_cases"] == 1
    assert s["false_positive_rate"] == pytest.approx(1 / 2)
    assert s["recall"] == pytest.approx(1.0)
    assert s["samples"] == 7


def test_a_case_cannot_require_and_forbid_the_same_string():
    """Refused at LOAD time, like every other ungradeable case in this file —
    a case that can never pass is a broken instrument, not a hard case."""
    with pytest.raises(ValueError, match="both required and forbidden"):
        ActCase.from_dict({
            "name": "x", "expect": "speak", "now": "2026-09-11T16:00:00+08:00",
            "must_cite": ["觀塘"], "must_not_cite": ["觀塘"],
            "row": {"note": "Dinner", "intent": "i"},
            "notes": [{"title": "Dinner", "description": "d", "body": "b"}],
        })


def test_an_either_case_with_nothing_to_grade_is_refused():
    """It would assert nothing at all: the decision is unlabelled, so the only
    things left to grade are what the message must and must not say. Either
    will do; neither is a case that cannot fail."""
    with pytest.raises(ValueError, match="must_cite or must_not_cite"):
        ActCase.from_dict({
            "name": "y", "expect": "either", "now": "2026-09-11T16:00:00+08:00",
            "row": {"note": "Dinner", "intent": "i"},
            "notes": [{"title": "Dinner", "description": "d", "body": "b"}],
        })


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
    assert cases[0].unit == "ridgeline"
    assert cases[0].rows[0].note == "AI Hackathon dinner plan"


def test_the_act_corpus_loads_and_is_majority_silent():
    cases = load_act_cases(CASES / "act.yaml")
    assert len(cases) >= 8
    assert sum(c.expect == "silent" for c in cases) * 2 > len(cases)
    assert any(c.note_moved for c in cases)
    # Issue #226: the corpus measures the two ways a RIGHT decision to speak
    # still produces a wrong message. Without these the suite is blind to both,
    # because every other case grades only whether she spoke.
    by_name = {c.name: c for c in cases}
    vocabulary = by_name["a09_her_own_machinery_is_never_named"]
    assert vocabulary.expect == "either"
    # The apparatus word itself, not the tool layer's whole phrase: with word
    # boundaries on the ASCII terms `note` already catches «open note», and a
    # term subsumed by a sibling is one nobody will keep true.
    assert {"note", "notes"} <= set(vocabulary.must_not_cite)
    assert "open note" not in vocabulary.must_not_cite
    grounding = by_name["a10_the_transcript_outranks_her_own_summary"]
    assert grounding.expect == "either"
    # The cited token is reachable ONLY from the transcript: the note that
    # describes the same gathering says something else, and nothing in the row,
    # the standing line or the note carries it. That is what makes citing it a
    # measurement of grounding rather than of phrasing.
    cited = grounding.must_cite[0]
    assert any(cited in str(t) for t in grounding.transcript)
    assert cited not in grounding.notes[0]["description"]
    assert cited not in grounding.notes[0]["body"]
    assert cited not in str(grounding.standing)
    assert cited not in str(grounding.row)


# ---- the harness pieces ------------------------------------------------------


def test_the_eval_tool_source_builds_remind_for_the_grant_and_never_send_reply():
    """Through the REAL `RegistryToolSource` (39 §5's «eval RegistryToolSource
    with the in-memory reminder store»): the grant admits `remind`, the
    ceiling still refuses `send_reply`, and no grant builds no act verb.
    (`where_is` is read-only and so inside the ceiling either way — the
    room-scoped axis is `verify_tool_subsets`' refusal at startup, not
    `build`'s; what the home-room grant changes for it is the ROOM.)"""
    tools = EvalDreamTools()
    home = HomeRoom(unit="friends", platform="signal", conversation_id="s")
    tools.bind(
        unit="friends", home=home, notes=InMemoryNoteStore(),
        reminders=FakeReminderStore(), memory=FakeMemoryStore(), clock=lambda: NOW,
    )
    asked = frozenset({"remind", "send_reply", "now", "where_is"})

    granted = tools.build(
        key="friends", names=asked, unit="friends", adaptor="proactive",
        grant=proactive_grant(),
    )
    ungranted = tools.build(key="friends", names=asked, unit="friends")

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
            runtime, tools=tools, unit=case.unit, now=case.now,
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
