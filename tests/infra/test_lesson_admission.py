"""Graph lesson proposals consume scoped evidence and an independent verdict."""

from argparse import Namespace
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from nora.agent.egress import LeakGuard
from nora.app import cli
from nora.doctrine.loader import Doctrine
from nora.domain.doctrine_check import CheckVerdict, LawRule
from nora.domain.graph_stimuli import GraphStimulus
from nora.infra.sql.cognitive_graph_schema import CLAIM
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.tension import SqlTensionStore
from nora.memory import wire
from nora.memory.app.admission import Stored
from nora.memory.domain.admission import Refusal
from nora.memory.domain.admission_proposals import Retire, Revise, Scope
from nora.memory.domain.graph_operations import WAKE_OPERATIONS, parse_graph_operation
from nora.memory.domain.pass_inputs import WakeInput
from tests.infra.test_cognitive_graph_admission import _rows
from tests.infra.test_graph_propose import WAKE, about, claim_payload
from tests.infra.test_intention_verdict import _tick, _verdicts
from tests.infra.test_pass_anchors import NOW, SCOPE
from tests.infra.test_pass_assembly import _assembler
from tests.infra.test_retrieval_scope import AT, PERSON, ROOM, UNIT
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_room_busy import slots
from tests.infra.test_situation_staleness import intention

BODY = "Pause briefly before summarizing a decision."
CONFLICT = "Treat every unanswered question as permission to continue."
ERROR = "Ask a follow-up when the subject shifts."
LAW = LawRule("core/authority.md", 8, "Obtain consent before disclosing private records.")


class LessonChecker:
    model = "fixture-lesson-check"

    def __init__(self):
        self.calls = []

    def rules(self):
        return (LAW,)

    async def judge(self, lesson):
        self.calls.append(lesson)
        if lesson == CONFLICT:
            return CheckVerdict("conflict", LAW, "Silence cannot establish permission.")
        if lesson == ERROR:
            raise TimeoutError
        if lesson == BODY:
            return CheckVerdict("consistent")
        raise AssertionError(f"Unexpected lesson: {lesson}")


def service(scenario, checker):
    return wire.admission(
        scenario.engine, scenario.persons, scenario.policy,
        LeakGuard(Doctrine((), "", "", "fixture", "", 0), "fixture-canary"),
        content_checks=False, room_slots=slots(scenario), lesson_checker=checker,
    )


def proposal(citations, *, context=WAKE, body=BODY, target="nora-pointer"):
    links = [about(target)] if target is not None else []
    return parse_graph_operation(
        claim_payload(part="lesson", citations=list(citations), content=body, about=links),
        context, WAKE_OPERATIONS, {},
    )


async def correction(scenario, id_="correction", *, aimed=True, own=False):
    await scenario.item(id_, is_nora=int(own))
    if aimed:
        await SqlGraphStimuli(scenario.engine).append(GraphStimulus(
            UNIT, "strong_cue", id_, AT, "messages", ROOM, PERSON, ("addresses_nora",),
        ))


async def verdicts(scenario, *, offsets=(1, 2, 3), **fields):
    for offset in offsets:
        await intention(scenario, f"ended:{offset}", state="ended",
                        ending="achieved", ended_at=AT + timedelta(minutes=offset), **fields)
    await _tick(scenario.engine, NOW)
    rows = await _verdicts(scenario.engine)
    return tuple(rows[f"ended:{offset}"]["graph_id"] for offset in offsets)


@pytest.fixture
async def lesson_setup(scenario):
    await scenario.entity("nora-pointer", pointer_kind="nora", pointer_ref="nora")
    checker = LessonChecker()
    return service(scenario, checker), checker


async def admit(svc, citations, *, context=WAKE, **changes):
    candidate = proposal(citations, context=context, **changes)
    if isinstance(candidate, Refusal):
        return candidate
    items = await svc.admit((candidate,), context, (*citations, "nora-pointer"))
    return items.outcomes[0]


async def test_three_distinct_verdicts_admit_a_checked_lesson(scenario, lesson_setup):
    svc, checker = lesson_setup
    ids = await verdicts(scenario)
    outcome = await admit(svc, ids)
    assert isinstance(outcome, Stored)
    assert checker.calls == [BODY]
    assert (await _rows(scenario.engine, CLAIM))[outcome.id]["content"] == BODY


async def test_a_person_addressing_nora_can_supply_the_correction_floor(scenario, lesson_setup):
    svc, checker = lesson_setup
    await correction(scenario)
    outcome = await admit(svc, ("correction",))
    assert isinstance(outcome, Stored)
    assert checker.calls == [BODY]


@pytest.mark.parametrize(("reason", "body", "target", "aimed", "own"), [
    ("lesson_not_about_nora", BODY, None, True, False),
    ("thin_evidence", BODY, "nora-pointer", False, False),
    ("echo", BODY, "nora-pointer", True, True),
    ("too_short", "!!! Pause !!!", "nora-pointer", True, False),
    ("too_long", "long " * 90, "nora-pointer", True, False),
    ("echoes_doctrine", LAW.text, "nora-pointer", True, False),
], ids=["about", "thin", "echo", "short", "long", "doctrine"])
async def test_typed_form_refusals_run_before_the_checker(
    scenario, lesson_setup, reason, body, target, aimed, own,
):
    svc, checker = lesson_setup
    await correction(scenario, aimed=aimed, own=own)
    result = await admit(svc, ("correction",), body=body, target=target)
    assert isinstance(result, Refusal)
    assert result.reason == reason
    assert checker.calls == []
    assert await _rows(scenario.engine, CLAIM) == {}


@pytest.mark.parametrize("reason", ["cap", "echoes_lesson"])
async def test_inventory_refuses_over_budget_and_echoed_lessons(scenario, lesson_setup, reason):
    svc, checker = lesson_setup
    await correction(scenario)
    if reason == "cap":
        for index in range(12):
            await scenario.claim(f"held:{index}", part="lesson", content=f"practice {index}")
    else:
        await scenario.claim("held", part="lesson", content=BODY)
    result = await admit(svc, ("correction",))
    assert result == Refusal(reason, "content")
    assert checker.calls == []


async def test_called_pass_refuses_lesson_even_with_good_evidence(scenario, lesson_setup):
    svc, checker = lesson_setup
    await correction(scenario)
    context = replace(WAKE, occasion="called")
    result = proposal(("correction",), context=context)
    assert result == Refusal("occasion_basis", "basis")
    candidate = proposal(("correction",))
    assert not isinstance(candidate, Refusal)
    decision = await svc.admit((candidate,), context, ("correction", "nora-pointer"))
    assert decision.outcomes == (result,)
    assert checker.calls == []


@pytest.mark.parametrize(("body", "reason"), [
    (CONFLICT, "loosens_law"), (ERROR, "check_unavailable"),
], ids=["conflict", "timeout"])
async def test_conflict_records_one_operator_tension_and_error_writes_nothing(
    scenario, lesson_setup, body, reason,
):
    svc, checker = lesson_setup
    await correction(scenario)
    result = await admit(svc, ("correction",), body=body)
    assert result == Refusal(reason, "content")
    assert checker.calls == [body]
    assert await _rows(scenario.engine, CLAIM) == {}
    rows = await SqlTensionStore(scenario.engine).recent(unit=UNIT)
    if body == ERROR:
        assert rows == ()
    else:
        [tension] = rows
        assert (tension.would_have_said, tension.citations, tension.check_model) == (
            body, ("correction",), checker.model,
        )


async def test_wake_shows_earlier_same_class_verdicts_and_can_cite_them(scenario, lesson_setup):
    svc, _checker = lesson_setup
    previous = await verdicts(scenario)
    [current] = await verdicts(scenario, offsets=(10,))
    [other] = await verdicts(scenario, offsets=(4,), owed_to="different-fixture-person")
    data = WakeInput((GraphStimulus(UNIT, "verdict", current, NOW, "verify", ROOM),))
    assembled = await _assembler(scenario).assemble("wake", data, (), SCOPE, NOW)
    rendered = assembled.render()
    assert set(previous) | {current, "nora-pointer"} <= assembled.shown
    assert all(graph_id in rendered for graph_id in previous)
    assert other not in rendered
    candidate = proposal(previous)
    assert not isinstance(candidate, Refusal)
    admitted = await svc.admit((candidate,), WAKE, assembled.shown)
    assert isinstance(admitted.outcomes[0], Stored)


async def test_operator_retirement_hides_graph_lesson_and_sets_evidence_floor(
    scenario, lesson_setup, clean_pg_url, monkeypatch, capsys,
):
    svc, _checker = lesson_setup
    ids = await verdicts(scenario)
    lesson = await admit(svc, ids)
    assert isinstance(lesson, Stored)

    monkeypatch.setattr(cli, "_config", lambda _args: SimpleNamespace())
    monkeypatch.setattr(cli, "_database_url", lambda _cfg: clean_pg_url)
    monkeypatch.setattr(cli, "_policy", lambda _cfg: scenario.policy)
    listing = Namespace(unit=UNIT, all=False)
    assert await cli.cmd_lessons_list(listing) == 0
    output = capsys.readouterr().out
    assert lesson.id in output and "support=0 contradiction=0" in output
    assert all(id_ in output for id_ in ids)
    assert await cli.cmd_lessons_show(Namespace(lesson_id=lesson.id)) == 0
    shown = capsys.readouterr().out
    assert lesson.id in shown and "[live]" in shown and BODY in shown
    assert "support=0 contradiction=0" in shown
    assert all(id_ in shown for id_ in ids)
    with pytest.raises(cli.CliError, match="no unique graph lesson"):
        await cli.cmd_lessons_show(Namespace(lesson_id="not-a-lesson"))

    args = Namespace(lesson_id=lesson.id, why="Generated operator correction", apply=False)
    assert await cli.cmd_lessons_retire(args) == 0
    assert "nothing written" in capsys.readouterr().out
    assert (await _rows(scenario.engine, CLAIM))[lesson.id]["state"] == "live"
    args.apply = True
    assert await cli.cmd_lessons_retire(args) == 0

    claims = await _rows(scenario.engine, CLAIM)
    [retirement] = [row for row in claims.values() if row["state"] == "retired"]
    assert (retirement["writer"], retirement["occasion"], retirement["replaces"]) == (
        "operator", "operator", lesson.id,
    )
    assert retirement["pass"].startswith("operator:")
    capsys.readouterr()
    assert await cli.cmd_lessons_show(Namespace(lesson_id=retirement["id"])) == 0
    gone = capsys.readouterr().out
    assert retirement["id"] in gone and "[retired]" in gone
    assert "support=0 contradiction=0" in gone and all(id_ in gone for id_ in ids)
    with pytest.raises(cli.CliError, match="no unique graph lesson"):
        await cli.cmd_lessons_show(Namespace(lesson_id=lesson.id))
    view = await _assembler(scenario).assemble(
        "wake", WakeInput(()), (), SCOPE, await scenario.now(),
    )
    assert BODY not in view.render()
    assert await admit(svc, ids) == Refusal("relearn", "content")

    await scenario.claim("existing-fact")
    candidate = proposal(ids)
    assert not isinstance(candidate, Refusal)
    conversion = Revise(base="existing-fact", replacement=candidate)
    result = await svc.admit((conversion,), WAKE, (*ids, "existing-fact", "nora-pointer"))
    assert result.outcomes == (Refusal("relearn", "content"),)

    for id_, offset in (("same-time", 0), ("new-verdict", 1)):
        await intention(scenario, id_, state="ended", ending="achieved",
                        ended_at=retirement["asserted_at"] + timedelta(seconds=offset))
    await _tick(scenario.engine, await scenario.now())
    after = await _verdicts(scenario.engine)
    assert await admit(svc, (*ids[1:], after["same-time"]["graph_id"])) == Refusal(
        "relearn", "content",
    )
    assert isinstance(await admit(svc, (*ids[1:], after["new-verdict"]["graph_id"])), Stored)

    args.lesson_id = "21111111-1111-4111-8111-111111111111"
    with pytest.raises(cli.CliError, match="no unique live graph lesson"):
        await cli.cmd_lessons_retire(args)


async def test_wake_retirement_sets_no_operator_relearn_floor(scenario, lesson_setup):
    svc, _checker = lesson_setup
    ids = await verdicts(scenario)
    lesson = await admit(svc, ids)
    assert isinstance(lesson, Stored)
    retirement = Retire(
        id="wake-retirement", base=lesson.id, basis="inferred",
        reasoning="deduced", confidence="high", citations=ids,
        scope=Scope(UNIT, exposure=None),
    )
    assert (await svc.admit((retirement,), WAKE, (*ids, lesson.id))).outcomes == (
        Stored("wake-retirement", lesson.id),
    )
    assert isinstance(await admit(svc, ids), Stored)


async def test_show_refuses_a_lesson_outside_policy_a_non_lesson_and_an_id_in_two_units(
    scenario, clean_pg_url, monkeypatch,
):
    outside = "unit-outside-policy"
    assert outside not in scenario.policy.unit_workspaces()
    await scenario.claim("outside-lesson", unit=outside, part="lesson")
    await scenario.claim("plain-fact")
    await scenario.claim("twin-lesson", part="lesson")
    await scenario.claim("twin-lesson", unit=outside, part="lesson")
    here, sibling, dm = scenario.policy.conversations
    rooms = (here, replace(sibling, unit="second-unit"), dm)
    policy = replace(scenario.policy, conversations=rooms, _by_id={
        (room.platform, key): room for room in rooms for key in room.ids
    })
    assert {UNIT, "second-unit"} <= set(policy.unit_workspaces())
    await scenario.claim("shared-lesson", part="lesson")
    await scenario.claim("shared-lesson", unit="second-unit", part="lesson")
    monkeypatch.setattr(cli, "_config", lambda _args: SimpleNamespace())
    monkeypatch.setattr(cli, "_database_url", lambda _cfg: clean_pg_url)
    monkeypatch.setattr(cli, "_policy", lambda _cfg: policy)

    for graph_id in ("outside-lesson", "plain-fact", "shared-lesson"):
        with pytest.raises(cli.CliError, match="no unique graph lesson"):
            await cli.cmd_lessons_show(Namespace(lesson_id=graph_id))
    assert await cli.cmd_lessons_show(Namespace(lesson_id="twin-lesson")) == 0
