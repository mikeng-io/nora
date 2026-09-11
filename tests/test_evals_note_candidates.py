"""The note-candidates scenario suite's deterministic tier (design/dreamer-
agent-loop/37 §7, issue #118): the corpus loads and validates, the structural
grade reads the notebook and nothing else, the summary keeps errors apart
from failures, and the drive path runs the REAL runtime + adaptor end to end
with only the Strands boundary scripted — so a live run measures the model
and not a harness that never reached it.

The ten-case corpus (`evals/cases/note_candidates/note_candidates.yaml`) is
graded LIVE by `make eval-note-candidates`; what this file pins offline is
that the corpus is gradable and that the harness CAN drive the path.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from evals.note_candidates import (
    CaseTally,
    Grade,
    NoteCandidatesCase,
    NoteSnapshot,
    RunResult,
    StagedCandidate,
    grade,
    load_note_candidates_cases,
    summarise,
)
from evals.run import SUITES
from nora.agent import factory
from nora.app.eval_support import (
    build_note_candidates_runtime,
    run_note_candidates_case,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "evals" / "cases" / "note_candidates" / "note_candidates.yaml"


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------


def test_the_corpus_loads_and_is_balanced():
    """Ten cases at first; sixteen after the 2026-09-08 external brainstorm
    added the shapes the first ten could not see — an adversarial negative
    carrying every surface feature of a positive, the solo form of the update
    path, an invitation with no uptake, the Cantonese-only twin of the
    one-person plan, a settled solo plan, and a conditional. Eighteen after
    2026-09-10, when a LIVE miss named one more the brainstorm could not:
    a room where terse plan-shaped chatter is not a plan (#161). Twenty once
    #161 was FIXED: n10b/n11b are n10/n11's controlled pair — the same two
    batches with the room named and described in `policy.toml` — so one live
    run answers «does telling the model what room it is reading change the
    judgement» on identical input rather than across a corpus."""
    cases = load_note_candidates_cases(CORPUS)
    assert len(cases) == 20
    by_expect = {}
    for c in cases:
        by_expect.setdefault(c.expect, []).append(c.name)
    assert len(by_expect["none"]) == 13, by_expect
    assert len(by_expect["create"]) + len(by_expect["update"]) == 7, by_expect
    assert "n6_stated_then_cancelled" in by_expect["none"], (
        "the adversarial negative: every surface feature of a positive, and "
        "the plan is called off in the same batch"
    )
    assert "p7_cantonese_only_solo_plan" in by_expect["create"], (
        "the twin any lexical lever would silently miss"
    )
    assert "n10_games_room_shorthand_is_not_an_arrangement" in by_expect["none"], (
        "the live miss (#161): five words of a games room read as a social "
        "arrangement, because nothing in the prompt says what room it is"
    )
    assert "n11_notebook_subject_is_not_a_licence_to_rewrite_it" in by_expect["none"], (
        "its second half: a subject taken from the notebook index and bound "
        "onto a batch that never mentions it"
    )
    # THE CONTROLLED PAIR (#161). Same batch, same notebook, one variable:
    # whether the operator has said what kind of room this is. A pair that
    # drifts apart on anything else stops being a measurement of the lever and
    # becomes two more cases.
    described = {c.name: c for c in cases}
    for bare, paired in (
        ("n10_games_room_shorthand_is_not_an_arrangement",
         "n10b_a_described_games_room_is_still_not_an_arrangement"),
        ("n11_notebook_subject_is_not_a_licence_to_rewrite_it",
         "n11b_a_described_games_room_does_not_rewrite_the_notebook_subject"),
    ):
        a, b = described[bare], described[paired]
        assert (a.room_label, a.room_description) == (None, None), bare
        assert b.room_label and b.room_description, paired
        assert a.expect == b.expect == "none"
        assert [m.body for m in a.batch] == [m.body for m in b.batch], paired
        assert [n.title for n in a.notebook] == [n.title for n in b.notebook], paired

    assert SUITES["note_candidates"] == (CORPUS,)


def test_the_relative_date_case_is_anchored_to_its_own_timestamp():
    """«今個禮拜六» from a Monday 2026-09-07 message is 2026-09-12 — the
    expectation is pinned so a rewritten case cannot drift off the calendar
    the message itself carries."""
    case = next(c for c in load_note_candidates_cases(CORPUS)
                if c.name == "p2_one_person_stated_plan_with_open_corners")
    assert case.anchor_date == 20260912
    assert case.batch[0].ts_ms == 1_788_804_803_000  # 2026-09-07T18:13:23Z


@pytest.mark.parametrize("bad, message", [
    ({"expect": "maybe"}, "expect must be one of"),
    ({"batch": []}, "at least one message"),
    ({"must_cite": [999]}, "outside the batch"),
    ({"expect": "update"}, "must name a notebook title"),
    ({"expect": "none", "anchor_date": 20260919}, "has no anchor_date"),
])
def test_an_ungradable_case_is_refused_at_load(bad, message):
    raw = {
        "name": "x", "expect": "create", "workspace": "friends",
        "anchor_date": 20260919, "must_cite": [1],
        "batch": [{"id": 1, "ts": "2026-09-07T12:00:00Z", "person": "ada",
                   "name": "Ada", "body": "19號食飯？"}],
    }
    raw.update(bad)
    if bad.get("expect") == "none":
        raw.pop("must_cite")
    with pytest.raises(ValueError, match=message):
        NoteCandidatesCase.from_dict(raw)


# ---------------------------------------------------------------------------
# the grade — reads the notebook, never the envelope
# ---------------------------------------------------------------------------


def _case(expect="create", **over) -> NoteCandidatesCase:
    raw = {
        "name": "t", "expect": expect, "workspace": "friends",
        "batch": [{"id": 1, "ts": "2026-09-07T12:00:00Z", "person": "ada",
                   "name": "Ada", "body": "19號食飯？"}],
    }
    if expect == "create":
        raw.update({"anchor_date": 20260919, "must_cite": [1]})
    if expect == "update":
        raw.update({"updates": "Dinner", "notebook": [
            {"title": "Dinner", "description": "d", "anchor_date": 20260919, "body": "b"}]})
    raw.update(over)
    return NoteCandidatesCase.from_dict(raw)


def _result(status="ok", before=(), after=(), staged=()) -> RunResult:
    return RunResult(status=status, stop_reason="end_turn", instruction_sha256="a" * 64,
                     before=tuple(before), after=tuple(after), staged=tuple(staged))


N1 = NoteSnapshot("n1", "Dinner", 20260919, "r1")
N1_MOVED = NoteSnapshot("n1", "Dinner", 20260919, "r2")
N2 = NoteSnapshot("n2", "Dinner 19 Sept", 20260919, "r9")


def test_unavailable_is_error_for_every_expectation():
    for expect in ("create", "update", "none"):
        g = grade(_case(expect), _result(status="unavailable"))
        assert g.verdict == "ERROR", expect


def test_create_passes_on_exactly_one_new_note_with_the_date_and_the_citation():
    staged = [StagedCandidate("Dinner 19 Sept", 20260919, (1,), "created")]
    assert grade(_case(), _result(after=[N2], staged=staged)).passed
    assert grade(_case(), _result(after=[])).verdict == "FAIL"
    assert "anchor_date" in grade(
        _case(anchor_date=20260920), _result(after=[N2], staged=staged)).reason
    assert "cite" in grade(
        _case(), _result(after=[N2], staged=[StagedCandidate("x", 20260919, (7,), "created")])
    ).reason
    assert "expected one" in grade(
        _case(), _result(after=[N2, NoteSnapshot("n3", "dup", 20260919, "r")], staged=staged)
    ).reason


def test_none_passes_only_on_an_unchanged_notebook():
    assert grade(_case("none"), _result(before=[N1], after=[N1])).passed
    assert grade(_case("none"), _result(before=[N1], after=[N1, N2])).verdict == "FAIL"
    assert grade(_case("none"), _result(before=[N1], after=[N1_MOVED])).verdict == "FAIL"
    # A refused duplicate is still `none`: the staging row is the tier's
    # business, the notebook is what the room sees.
    refused = [StagedCandidate("Dinner", 20260919, (1,), "refused")]
    assert grade(_case("none"), _result(before=[N1], after=[N1], staged=refused)).passed


def test_update_passes_when_the_named_note_moved_and_nothing_was_created():
    assert grade(_case("update"), _result(before=[N1], after=[N1_MOVED])).passed
    assert grade(_case("update"), _result(before=[N1], after=[N1])).verdict == "FAIL"
    assert "second note" in grade(
        _case("update"), _result(before=[N1], after=[N1, N2])).reason


def test_the_summary_keeps_errors_out_of_precision_and_recall():
    t_create = CaseTally("p", "create", ["PASS", "FAIL", "ERROR"], ["", "no note", "down"])
    t_none = CaseTally("n", "none", ["PASS", "PASS", "FAIL"], ["", "", "a note was created"])
    s = summarise([t_create, t_none])
    assert s == {
        "cases": 2, "majority_pass": 1, "samples": 6, "errors": 1,
        "tp": 1, "fp": 1, "fn": 1, "precision": 0.5, "recall": 0.5,
        "verdicts": {"PASS": 3, "FAIL": 2, "ERROR": 1},
    }
    assert t_create.majority_pass is False  # 1 of 2 graded is not a majority
    assert CaseTally("e", "none", ["ERROR"], ["down"]).majority_pass is False


# ---------------------------------------------------------------------------
# the drive path — the real runtime and adaptor, the model scripted
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Result:
    stop_reason: str | None = "end_turn"


def _scripted(monkeypatch, raw: str):
    """Replace the three Strands touch points exactly as
    `tests/unit/test_dreamer_runtime.py` does, so everything the runtime and
    the adaptor do — render, screen, parse, stage, gate, materialise — runs
    for real over a scripted envelope."""
    monkeypatch.setattr(factory, "create_dream_agent", lambda **_kw: object())

    async def _invoke(*_a, **_kw):
        return _Result()

    monkeypatch.setattr(factory, "invoke", _invoke)
    monkeypatch.setattr(factory, "final_text", lambda _result: raw)


def _drive(monkeypatch, cfg, case: NoteCandidatesCase, raw: str):
    """`cfg` is the `example_config` fixture — the template, never the
    deployment's file (`tests/architecture/test_config_comes_from_the_
    template.py`). The drive never reaches the network."""
    _scripted(monkeypatch, raw)
    # The template copy lives in `tmp_path`, so its relative `[dreamer] dir`
    # points at nothing there; the REAL doctrine + objective are what the
    # instruction hash below must bind to, so they are named explicitly.
    runtime, label = build_note_candidates_runtime(
        cfg, client=object(), dreamer_dir=ROOT / "nora" / "dreamer",
    )
    assert "/" in label
    return asyncio.run(run_note_candidates_case(
        runtime, cfg=cfg, workspace=case.workspace, scope="eval:t",
        batch=[dataclasses.asdict(m) for m in case.batch],
        notebook=[dataclasses.asdict(n) for n in case.notebook],
        now_ms=case.batch[-1].ts_ms + 60_000,
    ))


def test_a_scripted_create_reaches_the_notebook_through_the_real_adaptor(monkeypatch,
    example_config):
    case = _case("create")
    raw = json.dumps({"candidates": [{
        "title": "Dinner 19 Sept", "description": "Dinner on the 19th, venue open.",
        "body": "Ada asked whether the 19th works.", "closing_condition": "a venue is picked",
        "anchor_date": 20260919, "message_ids": [1],
    }]})
    drive = _drive(monkeypatch, example_config, case, raw)
    assert drive.status == "ok"
    assert drive.before == ()
    assert len(drive.after) == 1 and drive.after[0][2] == 20260919
    assert drive.staged == (("Dinner 19 Sept", 20260919, (1,), "created"),)
    result = RunResult(
        status=drive.status, stop_reason=drive.stop_reason,
        instruction_sha256=drive.instruction_sha256,
        before=tuple(NoteSnapshot(*n) for n in drive.before),
        after=tuple(NoteSnapshot(*n) for n in drive.after),
        staged=tuple(StagedCandidate(*c) for c in drive.staged),
    )
    assert grade(case, result) == Grade("PASS")
    assert len(drive.instruction_sha256) == 64, "bound to the real doctrine+objective"


def test_a_scripted_empty_envelope_leaves_the_seeded_notebook_alone(monkeypatch, example_config):
    case = _case("none", notebook=[
        {"title": "Dinner", "description": "d", "anchor_date": 20260919, "body": "b"}])
    drive = _drive(
        monkeypatch, example_config, case, '{"candidates": []}',
    )
    assert drive.status == "nothing_found"
    assert drive.before == drive.after and len(drive.after) == 1
    assert drive.staged == ()


def test_a_scripted_same_date_candidate_revises_instead_of_creating(monkeypatch, example_config):
    case = _case("update")
    raw = json.dumps({"candidates": [{
        "title": "Dinner", "description": "Dinner on the 19th, now booked.",
        "body": "Ada booked Luk Fook for 7:30pm on the 19th, four people.",
        "closing_condition": "everyone turns up", "anchor_date": 20260919,
        "message_ids": [1],
    }]})
    drive = _drive(monkeypatch, example_config, case, raw)
    assert drive.status == "ok"
    assert len(drive.after) == 1, "no second note on the same date"
    assert drive.before[0][3] != drive.after[0][3], "the head moved"
    assert drive.staged[0][3] == "created"
