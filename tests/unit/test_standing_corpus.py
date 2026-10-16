"""The standing corpus's grading, without a database or a model.

The integration file drives
the harness; this pins what a reading MEANS: a `fold` case is graded only on a
judge's verdict over a written position, a typo in a case is refused at load
(before a fold is paid for), and the corpus holds the mix of kinds the design
asks for.
"""
from __future__ import annotations

import collections
import json

import pytest
from strands_evals.types.evaluation import EnvironmentState, EvaluationData

from evals.backbone import (
    ERROR,
    FAIL,
    NORA_ERROR,
    NORA_JUDGED,
    NORA_JUDGED_WHY,
    NORA_POSITION,
    NORA_WORKING_SET,
    PASS,
    MeetsGrader,
)
from evals.rebuilt.domain import Outcome, WorkingSetFinding
from evals.rebuilt.fold_evidence import render_working_set
from evals.rebuilt.grading import Judgement
from evals.rebuilt.judge import (
    POSITION_CRITERION,
    _build_position_prompt,
    criterion_verdict,
)
from evals.rebuilt.rubric import FOLD_KINDS
from evals.scenarios import CORPUS_ENTRY, JUDGE_REQUIRED, JUDGED_CORPORA, _check, load_all


@pytest.mark.parametrize(
    ("position", "judged", "expected"),
    [
        ("held", "PASS", True),
        ("held", "FAIL", False),
        # A position no judge read has not been measured against anything.
        ("held", "", None),
        ("held", "REVIEW", None),
        # Nothing written is not a verdict about a position.
        (None, "PASS", None),
        (None, "", None),
    ],
)
def test_a_fold_is_graded_only_on_a_judged_position(position, judged, expected) -> None:
    assert Outcome(admitted=None, position=position, judged=judged).meets("fold") is expected


def test_a_fold_that_errored_is_ungradable_whatever_else_it_carries() -> None:
    outcome = Outcome(admitted=None, position="held", judged="PASS", error="timeout")
    assert outcome.meets("fold") is None


def _data(**state: object) -> EvaluationData:
    return EvaluationData(
        input="", actual_output="",
        actual_environment_state=[EnvironmentState(name=k, state=v) for k, v in state.items()],
        metadata={"expect": "fold"},
    )


def test_the_grader_reads_the_position_and_carries_the_judges_reason() -> None:
    [passed] = MeetsGrader().evaluate(_data(**{
        NORA_ERROR: None, NORA_POSITION: "held", NORA_JUDGED: "PASS",
        NORA_JUDGED_WHY: "the owed answer is still there",
    }))
    assert passed.label == PASS
    assert "the owed answer is still there" in passed.reason

    [failed] = MeetsGrader().evaluate(_data(**{
        NORA_ERROR: None, NORA_POSITION: "held", NORA_JUDGED: "FAIL",
        NORA_JUDGED_WHY: "the older threads are gone",
    }))
    assert failed.label == FAIL
    assert "the older threads are gone" in failed.reason

    [unjudged] = MeetsGrader().evaluate(_data(**{
        NORA_ERROR: None, NORA_POSITION: "held", NORA_JUDGED: "",
    }))
    assert unjudged.label == ERROR


def test_the_position_prompt_carries_the_evidence_the_kind_and_the_case() -> None:
    prompt = _build_position_prompt(
        kind="stale", prop="THE-CASE-PROPERTY", evidence="THE-RECORD", position="THE-POSITION",
    )
    for part in ("THE-CASE-PROPERTY", "THE-RECORD", "THE-POSITION", FOLD_KINDS["stale"]):
        assert part in prompt
    assert POSITION_CRITERION in prompt
    with pytest.raises(ValueError, match="unknown standing kind"):
        _build_position_prompt(kind="vibes", prop="p", evidence="e", position="x")


def test_the_position_prompt_omits_the_working_set_section_when_there_is_none() -> None:
    """A case's judge input must GAIN nothing when the fold's
    working set is empty or absent — the prompt without `working_set` and the
    prompt with `working_set=""` must read identically, and neither carries
    the section's own header."""
    bare = _build_position_prompt(
        kind="carry", prop="p", evidence="e", position="THE-POSITION",
    )
    explicit_empty = _build_position_prompt(
        kind="carry", prop="p", evidence="e", position="THE-POSITION", working_set="",
    )
    assert bare == explicit_empty
    assert "working set" not in bare.lower()


def test_the_position_prompt_shows_the_working_set_as_a_separate_labelled_thing() -> None:
    """The paragraph is her position; the working set is options she found and
    has not given the room (tell the judge what it
    is READING, not only what it can do). Both sections are present, in order,
    and the working-set one says out loud that it is not her position."""
    prompt = _build_position_prompt(
        kind="residue", prop="THE-CASE-PROPERTY", evidence="THE-RECORD",
        position="THE-POSITION", working_set="- an unsent restaurant (source: web_search)",
    )
    assert "THE-POSITION" in prompt
    assert "an unsent restaurant (source: web_search)" in prompt
    position_at = prompt.index("THE-POSITION")
    working_set_at = prompt.index("an unsent restaurant")
    assert position_at < working_set_at, "the position is shown before the working set"
    # Labelled as NOT the position, in the text right above the rendered items.
    header = prompt[:working_set_at]
    assert "not her position" in header.lower()


def test_a_working_set_item_does_not_read_as_the_position_recapping() -> None:
    """Recap (control): `FOLD_KINDS['recap']` grades ONE thing — whether the
    WRITTEN POSITION is a transcript recap — and that text is untouched by a
    working set being present. The item lives in its own section, clearly not
    part of "the position she wrote after the turn" the property is graded
    against, so a working-set entry cannot itself satisfy or violate a recap
    property; it never appears inside the position's own fenced block."""
    prompt = _build_position_prompt(
        kind="recap", prop="THE-CASE-PROPERTY", evidence="THE-RECORD",
        position="HER WORKING STATE, NOT A RECAP",
        working_set="- the member said they would confirm tomorrow (source: web_search)",
    )
    # The rubric's own text says "working
    # state" (her holding-open state), never
    # "working set" (the tier's name), so the property itself never asks the
    # judge to read the section below as part of "the position".
    assert "working set" not in FOLD_KINDS["recap"].lower()
    position_block = prompt[
        prompt.index("```\nHER WORKING STATE") : prompt.index("```\nHER WORKING STATE") + 200
    ]
    assert "the member said they would confirm tomorrow" not in position_block


def test_a_working_set_item_with_a_stale_relative_date_does_not_fail_a_stale_case() -> None:
    """Stale (control): `FOLD_KINDS['stale']` grades whether the WRITTEN
    POSITION re-affirms a relative-time clause the turn's own clock has
    passed. A working-set item lives in its own, separately-labelled section
    — never inside the position's fenced block — so one carrying its own
    stale relative date ("tonight") cannot itself trip the property, since
    that rubric is read against the position alone (mirrors the recap
    control above)."""
    prompt = _build_position_prompt(
        kind="stale", prop="THE-CASE-PROPERTY", evidence="THE-RECORD",
        position="THE POSITION, WITH NO STALE CLAUSE",
        working_set="- a table booked for tonight (source: web_search)",
    )
    assert "working set" not in FOLD_KINDS["stale"].lower()
    position_block = prompt[
        prompt.index("```\nTHE POSITION") : prompt.index("```\nTHE POSITION") + 200
    ]
    assert "a table booked for tonight" not in position_block


def test_a_working_set_item_does_not_count_toward_what_a_carry_position_holds() -> None:
    """Carry (control): `FOLD_KINDS['carry']` grades whether the WRITTEN
    POSITION still holds a thread the turn did not touch. A working-set item
    is not part of that block, so one cannot itself satisfy — or, read the
    other way, cannot be blamed for NOT satisfying — "still held" (mirrors
    the recap control above)."""
    prompt = _build_position_prompt(
        kind="carry", prop="THE-CASE-PROPERTY", evidence="THE-RECORD",
        position="THE POSITION, HOLDING THE THREAD",
        working_set="- an unrelated finding (source: web_search)",
    )
    assert "working set" not in FOLD_KINDS["carry"].lower()
    position_block = prompt[
        prompt.index("```\nTHE POSITION") : prompt.index("```\nTHE POSITION") + 200
    ]
    assert "an unrelated finding" not in position_block


def test_the_no_working_set_prompt_matches_the_pre_pr_text_byte_for_byte() -> None:
    """The widened scoring line ("...the property holds and 0.0...") stays INSIDE
    the `if working_set:` branch: leaking out would silently change every
    case's prompt — including every case with no working set at all — and
    contradict the "gains nothing" claim the tests above only check loosely (no
    header, no extra words named). This pins the no-working-set prompt against
    the original `_build_position_prompt` text in `evals/rebuilt/judge.py`,
    transcribed byte for byte, so an edit to the unconditional branch is
    caught, not just an edit that adds a header."""
    kind, prop, evidence, position = "carry", "THE-CASE-PROPERTY", "THE-RECORD", "THE-POSITION"
    pre_pr_text = (
        "You are grading one STANDING POSITION that Nora — an AI member of a "
        "Hong Kong group chat — wrote about one room, right after one of her "
        "turns there. Standing is the compact of her work in that room: what "
        "she is holding open, what she owes, and what she found and has not "
        "given the room yet. It is rewritten after every turn from the "
        "position before it plus the turn, and it is the ONLY thing that "
        "carries her working state to her next turn — the next turn sees the "
        "transcript and this, nothing else. It is Cantonese prose.\n\n"
        "Below is the record of the turn: her position before it (with when it "
        "was written), the room's messages around the turn, and her steps on "
        "the turn — every tool she called and what came back, in full. Then "
        "the position she wrote after it. Grade ONE property, stated below; "
        "ignore style, length and anything the property does not ask about.\n\n"
        f"{evidence}\n"
        "## The position she wrote after the turn (what you are grading)\n"
        f"```\n{position.strip()}\n```\n\n"
        f"## The property ({kind})\n{FOLD_KINDS[kind]}\n\n"
        f"## This case\n{prop.strip()}\n\n"
        "Score 1.0 when the property holds in the written position and 0.0 when "
        "it does not; state your confidence (0.0-1.0). Return ONLY a JSON array "
        "with one object, in this exact shape (a ```json fenced block is "
        "tolerated):\n"
        f'[{{"criterion": "{POSITION_CRITERION}", "score": <0-1 float>, '
        '"confidence": <0-1 float>, "rationale": "<one sentence naming what '
        'was or was not held>"}]'
    )
    current = _build_position_prompt(kind=kind, prop=prop, evidence=evidence, position=position)
    assert current == pre_pr_text


def test_grade_position_renders_the_working_set_into_the_prompt(monkeypatch) -> None:
    """`Judge.grade_position` is a thin wrapper over `_build_position_prompt`
    (module docstring) — this pins that its own new `working_set` argument
    actually reaches the prompt, without a model call."""
    import evals.rebuilt.grading as grading_mod

    captured: dict[str, object] = {}

    def fake_build(**kwargs: object) -> str:
        captured.update(kwargs)
        return "PROMPT"

    async def fake_draw(self, prompt: str, bound: tuple[str, ...]) -> Judgement:
        return Judgement(criterion=POSITION_CRITERION, verdict="PASS", samples=())

    monkeypatch.setattr(grading_mod, "_build_position_prompt", fake_build)
    monkeypatch.setattr(grading_mod.Judge, "_draw", fake_draw)
    judge = grading_mod.Judge(base_url="http://x")
    import asyncio

    asyncio.run(judge.grade_position(
        position="POS", evidence="EV", kind="residue", prop="PROP",
        working_set="- an unsent option (source: web_search)",
    ))
    assert captured["working_set"] == "- an unsent option (source: web_search)"


def test_a_position_verdict_parses_fail_closed() -> None:
    answer = json.dumps([{
        "criterion": POSITION_CRITERION, "score": 0.0, "confidence": 0.9,
        "rationale": "dropped the thread",
    }])
    assert criterion_verdict(answer, POSITION_CRITERION) == "FAIL"
    assert criterion_verdict("not json", POSITION_CRITERION) == "REVIEW"
    assert criterion_verdict("[]", POSITION_CRITERION) == "REVIEW"
    judgement = Judgement(criterion=POSITION_CRITERION, verdict="FAIL", samples=(answer, answer))
    assert judgement.why == "dropped the thread"


def test_the_corpus_is_registered_as_judge_only() -> None:
    assert CORPUS_ENTRY["standing"] == "standing"
    assert "standing" in JUDGED_CORPORA
    assert "standing" in JUDGE_REQUIRED


def test_the_corpus_holds_the_mix_the_design_asks_for() -> None:
    """≥3 carry, ≥3 stale, ≥2 recap on REAL moments, and the operator's own
    residue case running — ACTIVE cases, every one a named turn at its own
    instant.

    Residue is the exception: no post-chain turn has the shape (options the
    room could act on, some sent), so the one residue case is the operator's
    real moment with its prior declared absent — a counterfactual, counted as
    one."""
    cases = load_all("standing")
    active = [c for c in cases if not c.metadata.get("parked")]
    real = [c for c in active if not c.metadata.get("counterfactual")]
    kinds = collections.Counter(c.metadata["kind"] for c in real)
    assert kinds["carry"] >= 3 and kinds["stale"] >= 3, kinds
    assert kinds["recap"] >= 2, kinds
    residue = [c for c in active if c.metadata["kind"] == "residue"]
    assert residue, "the corpus measures no residue at all"
    assert all(c.metadata["counterfactual"].get("varies") == "prior" for c in residue), (
        "a residue case became real: move it out of this assertion"
    )
    for case in active:
        assert case.metadata["snapshot"], case.name
        assert case.metadata["tag_id"], case.name
        assert case.expected_output.strip(), case.name
    anchors = [(c.metadata["tag_id"], c.metadata["kind"]) for c in cases]
    assert len(set(anchors)) == len(anchors), "a turn is graded twice on one kind"


@pytest.mark.parametrize(
    ("patch", "complaint"),
    [
        ({"metadata": {"tag_id": "t", "kind": "vibes"}}, "metadata.kind"),
        ({"metadata": {"kind": "carry"}}, "metadata.tag_id"),
        ({"expected_output": ""}, "expected_output"),
        ({"expect": "reply"}, "expect must be `fold`"),
    ],
)
def test_a_malformed_fold_case_is_refused_at_load(patch, complaint) -> None:
    """At LOAD, because at run time the fold's model call is already spent."""
    row = {
        "name": "probe", "entry": "standing", "expect": "fold",
        "expected_output": "a thread is still held",
        "metadata": {"tag_id": "t", "kind": "carry"},
        **patch,
    }
    with pytest.raises(ValueError, match=complaint):
        _check("standing", row)


def test_the_suite_refuses_a_judge_only_corpus_without_a_judge(monkeypatch, capsys) -> None:
    """Refused BEFORE any subject is composed or model is paid for."""
    import sys

    from evals.__main__ import main

    monkeypatch.setattr(sys, "argv", ["python -m evals", "standing"])
    assert main() == 2
    assert "--judge" in capsys.readouterr().err


def test_print_input_renders_one_case_without_asking_for_a_verdict(monkeypatch, capsys) -> None:
    """`--print-input`: the case goes down the ONE path flagged `print_input`,
    and what prints is the model input the drive recorded — nothing else."""
    from evals import __main__ as cli
    from evals.backbone import NORA_SHOWN

    seen = []

    def fake_task(spec):
        def run(case):
            seen.append((spec.entry, case.name, case.metadata.get("print_input")))
            return {"environment_state": [EnvironmentState(name=NORA_SHOWN, state="THE-INPUT")]}
        return run

    monkeypatch.setattr(cli, "nora_task", fake_task)
    name = next(c.name for c in load_all("standing") if not c.metadata.get("parked"))
    assert cli._print_input("standing", name, source_url="x") == 0
    assert capsys.readouterr().out.strip() == "THE-INPUT"
    assert seen == [("standing", name, True)]

    # A parked case has no moment to render, and a corpus whose entry cannot
    # render its input is refused by name.
    parked = next(c.name for c in load_all("standing") if c.metadata.get("parked"))
    assert cli._print_input("standing", parked, source_url="x") == 2
    assert cli._print_input("no_trigger", "anything", source_url="x") == 2


def _row(i: int, body: str, *, ts: int, nora: bool = False) -> object:
    from nora.domain.models import DeliveryStatus, TranscriptRow

    return TranscriptRow(
        id=i, sender_id="+1", sender_name="self-set", ts=ts, body=body, is_nora=nora,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
        person_name=None if nora else "Canonical",
    )


def test_render_evidence_is_the_record_in_the_deployments_zone() -> None:
    """What the judge reads: the prior with when it was written, the rows the
    fold READ (not only the trace's), every step, a long result past the fold's
    own bound, and times placed in the zone it is told — not the machine's."""
    from zoneinfo import ZoneInfo

    from evals.rebuilt.fold_evidence import render_evidence
    from nora.domain.standing import Standing
    from nora.domain.tag_trace import TagTrace
    from nora.subject.standing_fold import FoldTurn

    turn_at = 1_790_148_435_400                      # 15:27 in Hong Kong
    long_result = "x" * 9000 + "DEEP-FINDING" + "y" * 20000
    trace = TagTrace(
        tag_id="t", conversation_id="c", outcome="recovered", created_at=turn_at,
        transcript=(_row(2, "IN-THE-TRACE", ts=turn_at - 60_000),),
        messages=(
            {"role": "assistant",
             "content": [{"toolUse": {"name": "web_search", "input": {"q": 1}}}]},
            {"role": "user", "content": [{"toolResult": {"content": [{"text": long_result}]}}]},
        ),
    )
    turn = FoldTurn(
        platform="signal", trace=trace, delivered={},
        prior=Standing(platform="signal", conversation_id="c", body="THE-PRIOR",
                       fold_count=3, updated_at=turn_at - 20 * 3_600_000),
        seen=(
            _row(1, "BATCH-ONLY", ts=turn_at - 120_000),
            _row(2, "IN-THE-TRACE", ts=turn_at - 60_000),
            _row(3, "HER-LATE-REPLY", ts=turn_at + 300, nora=True),
        ),
    )
    text = render_evidence(turn, display_name="Nora", tz=ZoneInfo("Asia/Hong_Kong"))

    assert "THE-PRIOR" in text and "20.0 hours before the turn" in text
    assert "2026-09-23 15:27 (Wed) UTC+0800" in text, "not in the deployment's zone"
    assert text.index("BATCH-ONLY") < text.index("IN-THE-TRACE") < text.index("HER-LATE-REPLY")
    assert "] Nora: HER-LATE-REPLY" in text
    assert "] Canonical: BATCH-ONLY" in text, "the roster's name, never the self-set one"
    assert "[she called web_search]" in text
    assert "DEEP-FINDING" in text, "a result past the fold's own 4000 bound must reach the judge"
    assert "cut at 16000 of" in text, "a result past the judge's bound is cut AND says so"

    utc = render_evidence(turn, display_name="Nora", tz=ZoneInfo("UTC"))
    assert "2026-09-23 07:27 (Wed) UTC+0000" in utc


def test_a_row_id_the_sandbox_did_not_seed_is_refused_not_guessed() -> None:
    from nora.subject.standing_fold import _translated

    assert _translated(7, {7: 1}, what="w") == 1
    with pytest.raises(LookupError, match="was not seeded with"):
        _translated(8, {7: 1}, what="the turn's transcript")


@pytest.mark.parametrize(
    ("corpus", "varies", "complaint"),
    [
        ("standing", "culture", "may vary only its prior"),
        ("no_trigger", "prior", "which only"),
    ],
)
def test_the_prior_is_varied_in_the_standing_corpus_and_nothing_else_is(
    corpus, varies, complaint,
) -> None:
    """The counterfactual set stays closed per corpus: an absent prior means
    nothing outside the fold, and the fold varies nothing but its prior."""
    row = {
        "name": "probe", "entry": "standing", "expect": "fold",
        "expected_output": "a thread is still held",
        "metadata": {"tag_id": "t", "kind": "carry"},
        "snapshot": {"platform": "signal", "conversation_id": "c", "before_ms": 1},
        "counterfactual": {"varies": varies, "why": "because"},
    }
    with pytest.raises(ValueError, match=complaint):
        _check(corpus, row)


# --- the working_set capture ------------------------------------------------


def test_the_outcome_working_set_capture_reads_a_seeded_working_set() -> None:
    """The one boundary the fold's write-back crosses: `nora.domain.standing.
    WorkingSetItem` (`nora`-side) into `evals.rebuilt.domain.WorkingSetFinding`
    (domain-side, no `nora` import — module docstring). Seed one, as the store
    would hold it after a fold wrote it, and check the capture carries
    `what`/`source`/`bears_on` and drops `found_at` (`Outcome.working_set`'s
    own docstring: not a property a corpus or a judge grades against)."""
    from evals.rebuilt.entry import _outcome_working_set
    from nora.domain.standing import WorkingSetItem

    seeded = (
        WorkingSetItem(
            what="Fu Kee chicken hotpot, Kai Tak",
            source="web_search: 啟德 雞煲",
            found_at=1_790_000_000_000,
            bears_on="the bowling outing",
        ),
        WorkingSetItem(what="a second find", source="place_info", found_at=1),
    )
    captured = _outcome_working_set(seeded)
    assert captured == (
        WorkingSetFinding(
            what="Fu Kee chicken hotpot, Kai Tak",
            source="web_search: 啟德 雞煲",
            bears_on="the bowling outing",
        ),
        WorkingSetFinding(what="a second find", source="place_info", bears_on=None),
    )
    # `found_at` is nowhere on the captured type at all — not merely unset.
    assert not hasattr(captured[0], "found_at")


def test_the_outcome_working_set_capture_reads_empty_as_empty() -> None:
    from evals.rebuilt.entry import _outcome_working_set

    assert _outcome_working_set(()) == ()


def test_folded_carries_a_working_set_beside_its_position() -> None:
    """`nora.subject.standing_fold.Folded` — the read-back's own type — holds
    `working_set` beside `position`, defaulted to `()` the same way `position`
    defaults to `None`, so a caller that does not pass one gets the same
    "nothing found" reading `working_set_from_json` gives a row with nothing
    in the column."""
    from nora.domain.standing import WorkingSetItem
    from nora.subject.standing_fold import Folded

    item = WorkingSetItem(what="w", source="s", found_at=1)
    folded = Folded(outcome="written", position="THE POSITION", working_set=(item,))
    assert folded.working_set == (item,)

    bare = Folded(outcome="malformed", position=None)
    assert bare.working_set == ()


def test_render_working_set_reads_both_findings_and_plain_dicts() -> None:
    """`render_working_set` is fed `Outcome.working_set` directly (a tuple of
    `WorkingSetFinding`) inside a live run, and a report's `actual_
    environment_state` after it has gone through `EvaluationData.model_dump()`
    — which turns each `WorkingSetFinding` into a plain dict with the same
    three keys (`evals.__main__._print_positions`'s own read path). Both must
    render the same text."""
    finding = WorkingSetFinding(what="a place", source="web_search", bears_on="an outing")
    as_dict = {"what": "a place", "source": "web_search", "bears_on": "an outing"}
    assert render_working_set((finding,)) == render_working_set((as_dict,))
    assert "a place" in render_working_set((finding,))
    assert "source: web_search" in render_working_set((finding,))
    assert "bears on: an outing" in render_working_set((finding,))


def test_render_working_set_omits_bears_on_when_absent_and_is_empty_when_there_is_none() -> None:
    no_plan = WorkingSetFinding(what="a place", source="web_search")
    rendered = render_working_set((no_plan,))
    assert "bears on" not in rendered
    assert render_working_set(()) == ""


def test_nora_working_set_is_its_own_state_distinct_from_the_position() -> None:
    """`NORA_WORKING_SET` is a real, separate `EnvironmentState` name — not a
    second spelling of `NORA_POSITION` — so a reader (`--positions`,
    `evals.backbone._drive`'s grade_position call) can tell "no working set
    was captured" from "the position itself was empty"."""
    assert NORA_WORKING_SET != NORA_POSITION
    assert NORA_WORKING_SET == "nora.working_set"


def test_the_residue_case_accepts_the_finding_in_either_the_position_or_the_working_set() -> None:
    """The fold has somewhere DESIGNED to hold an
    unsent option — `working_set` — and the one active residue case has to
    say so, or a fold that correctly used it reads as a miss. The kind-asked
    and near-the-venue conditions must still be there, unweakened."""
    [case] = [
        c for c in load_all("standing")
        if c.name == "residue-restaurants-found-two-sent"
    ]
    text = case.expected_output.lower()
    assert "working set" in text
    assert "either" in text
    assert "chicken hotpot" in text and "near the venue" in text
