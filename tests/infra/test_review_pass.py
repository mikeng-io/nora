"""Run the scheduled judge through its real tools and scoped Postgres reads."""

import re
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from nora.access import IdentityAccess
from nora.agent.factory import RetryPolicy
from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.app.cognition_agent_runner import DreamerAgentRunner
from nora.app.cognition_session import AdaptorBudget
from nora.app.composition_review import build_review_runner
from nora.app.review_pass import REVIEW_RETRY_SECONDS, ReviewPass
from nora.app.startup import StartupError
from nora.config import ModelRoute
from nora.domain.lineage import LiveRoom
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.instructions.dreamer import DreamerInstructions
from nora.memory import ReadingRoom, ReviewInput, wire
from nora.memory.domain.pass_declarations import declaration
from nora.obs.metrics import Metrics
from nora.testing.scripted_agent import ScriptedTurn
from tests.fakes import dreamer_runtime_for
from tests.infra.test_graph_propose import BASIS
from tests.infra.test_intention_verdict import _tick, _verdicts
from tests.infra.test_lesson_tally import ended, rebuild, resting, tally_rows
from tests.infra.test_pass_anchors import NOW, SCOPE, reader
from tests.infra.test_retrieval_scope import AT, OTHER, OTHER_ROOM, ROOM, UNIT, write_edge
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_review_admission import grade_rows, guess, lesson
from tests.infra.test_room_busy import service, slots
from tests.infra.test_situation_staleness import intention
from tests.infra.test_wake_runner import WakeModel
from tests.unit.test_dream_memory_budget import _tool_results


class _Room:
    async def scope_for(self, unit, room):
        assert unit == UNIT
        if room == f"signal:{ROOM}":
            return SCOPE, False
        assert room == f"signal:{OTHER_ROOM}"
        reading = ReadingRoom("signal", OTHER_ROOM, LiveRoom(frozenset({OTHER})))
        return replace(SCOPE, room=reading), False


class _TruncatedReview(WakeModel):
    async def stream(self, *args, **kwargs):
        async for event in super().stream(*args, **kwargs):
            if event.get("messageStop", {}).get("stopReason") == "end_turn":
                event = {"messageStop": {"stopReason": "stop_sequence"}}
            yield event


def _review(scenario, model, *, turns=4):
    access = IdentityAccess(file_persons=scenario.persons, file_policy=scenario.policy)
    lineage = LineagePolicy.from_policy(scenario.policy, access=access)
    metrics = Metrics()

    def room_for(platform, id_):
        return Room(platform=platform, conversation_id=id_, roster_source=None, access=access)

    admission = service(scenario, slots(scenario))
    surface = wire.WakeToolSurface(
        admission, wire.graph_read_tools(scenario.engine, access, lineage, None, None),
        room_for, metrics, content_checks=False, offered=wire.WAKE_HISTORY_TOOLS,
        allowed_operations=("create_claim",), allowed_parts=wire.REVIEW_PARTS,
    )
    runtime = dreamer_runtime_for(
        models=model, metrics=metrics,
        retry=RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
    )
    current = NOW
    review = ReviewPass(
        targets=wire.SqlReviewTargets(scenario.engine), rooms=_Room(),
        assembler=wire.pass_assembler(scenario.engine, access, lineage, None, None),
        surface=surface, admission=admission, agent=DreamerAgentRunner(
            runtime=runtime, metrics=metrics, agent_name="review", bound_tool_arguments=True,
        ),
        instructions=DreamerInstructions(Path("nora/dreamer"), metrics=metrics),
        budget=AdaptorBudget(turns, 5000, 1000, 30.0), model="scripted-judge",
        revisions=dict, policy=scenario.policy, clock=lambda: current,
    )

    async def tick(now=NOW):
        nonlocal current
        current = now
        review.poll(now)
        await review.drain()

    return tick


def _model(target, outcome, citation, *, model=WakeModel):
    operation = {"op": "create_claim", "part": "grade", "target": target, "grade": outcome,
                 "content": "Generated assessment", "citations": [citation], **BASIS}
    return model([ScriptedTurn(tool_calls=[("graph_propose", {"operations": [operation]})])])


async def test_review_grades_due_guess_once_and_removes_it_from_anchors(scenario):
    await guess(scenario)
    await scenario.item("later")
    model = _model("guess", "refuted", "later")
    review = _review(scenario, model)
    await review()
    [stored] = await grade_rows(scenario)
    assert tuple(stored)[1:] == ("refuted", "scripted-judge")
    assert model.tools == {"graph_propose", "memory_read", "memory_search"}
    assert "Generated prediction" in model.prompt() and "later" in model.prompt()
    anchors = await reader(scenario).build(declaration("review"), ReviewInput(), SCOPE, NOW)
    assert anchors.cause is None and anchors.review_due == ()
    turns = model.turn
    await review(NOW + timedelta(hours=1))
    assert model.turn == turns


async def test_incomplete_judge_writes_nothing_and_retries_on_its_bound(scenario):
    await guess(scenario)
    await scenario.item("later")
    model = _model("guess", "refuted", "later", model=_TruncatedReview)
    review = _review(scenario, model)
    await review()
    assert model.turn == 2
    assert "Proposals staged" in str(_tool_results(model))
    assert await grade_rows(scenario) == []
    await review(NOW + timedelta(seconds=1))
    assert model.turn == 2
    anchors = await reader(scenario).build(declaration("review"), ReviewInput(), SCOPE, NOW)
    assert anchors.review_due == ("guess",)


async def test_two_review_ticks_page_guesses_and_show_the_lesson_with_its_tally(scenario):
    tally = await lesson(scenario)
    end = AT + timedelta(seconds=2)
    for i in range(60):
        await scenario.claim(f"guess-{i:02}", part="guess", prediction="Generated", window_end=end)
    await scenario.item("in-window", at=AT + timedelta(seconds=1))
    now = await scenario.now()
    model = WakeModel([])
    review = _review(scenario, model)
    for tick in (now, now + timedelta(seconds=REVIEW_RETRY_SECONDS)):
        await review(tick)
    assert len(model.seen) == 2
    dockets = [re.findall(r"<item [^>]+>.*?</item>", call[0]["content"][0]["text"], re.DOTALL)
               for call in model.seen]
    assert any('role="due"' in item and "claim: lesson" in item for item in dockets[0])
    assert any(tally["graph_id"] in item and '"contradiction": 1' in item for item in dockets[0])
    pages = []
    for items in dockets:
        guesses = {id_ for item in items if 'role="due"' in item
                   for id_ in re.findall(r"\bguess-\d\d\b", item)}
        assert guesses and any("in-window" in item
                               and set(re.findall(r"\bguess-\d\d\b", item)) == guesses
                               for item in items)
        pages.append(guesses)
    assert pages[0].isdisjoint(pages[1])
    assert await grade_rows(scenario) == []


async def test_scheduled_review_uses_claim_room_in_unit_without_home(scenario):
    assert UNIT not in scenario.policy.home_rooms()
    await guess(scenario)
    await scenario.claim("roomless-guess", part="guess", prediction="Roomless prediction",
                         window_end=AT, origin_room=None)
    await scenario.item("later")
    model = _model("guess", "refuted", "later")
    review = _review(scenario, model)
    await review()
    assert [tuple(row)[1:] for row in await grade_rows(scenario)] == [("refuted", "scripted-judge")]
    assert "Generated prediction" in model.prompt()
    assert "Roomless prediction" not in model.prompt()
    await review(NOW + timedelta(seconds=REVIEW_RETRY_SECONDS))
    assert model.turn == 2


async def test_review_retry_bound_holds_across_rooms_in_one_unit(scenario):
    await guess(scenario)
    await scenario.claim("other-guess", part="guess", prediction="Other room prediction",
                         window_end=AT, origin_room=OTHER_ROOM)
    model = WakeModel([])
    review = _review(scenario, model)
    await review()
    assert model.turn == 1 and "Other room prediction" in model.prompt()
    assert "Generated prediction" not in model.prompt()
    await review(NOW + timedelta(seconds=1))
    assert model.turn == 1
    await review(NOW + timedelta(seconds=REVIEW_RETRY_SECONDS))
    assert model.turn == 2 and "Generated prediction" in model.seen[1][0]["content"][0]["text"]


async def test_unit_review_grants_preserve_the_retry_bound_and_rotate_rooms(scenario):
    await guess(scenario)
    await scenario.claim("busy-first", part="guess", prediction="Busy room prediction",
                         window_end=AT, origin_room=OTHER_ROOM)
    model = WakeModel([])
    review = _review(scenario, model)
    await review()
    assert "Busy room prediction" in model.prompt()
    for minute in range(1, 7):
        tick = NOW + timedelta(minutes=minute)
        await review(tick)
        assert model.turn == minute, f"unchanged due set at minute {minute}"
        await scenario.claim(f"busy-{minute}", part="guess", prediction="Busy room prediction",
                             asserted_at=tick, window_end=tick, origin_room=OTHER_ROOM)
        await review(tick)
        assert model.turn == minute + 1, f"changed due set at minute {minute}"
    latest = NOW + timedelta(minutes=6)
    await review(latest + timedelta(seconds=REVIEW_RETRY_SECONDS - 1))
    assert model.turn == 7
    await review(latest + timedelta(seconds=REVIEW_RETRY_SECONDS))
    assert model.turn == 8

    prompts = [call[0]["content"][0]["text"] for call in model.seen]
    assert all("Busy room prediction" in prompt for prompt in prompts[::2])
    assert all("Generated prediction" in prompt for prompt in prompts[1::2])
    assert all("Busy room prediction" not in prompt for prompt in prompts[1::2])


async def test_review_shows_all_three_counted_verdicts_and_cites_the_contradiction(scenario):
    await scenario.claim("lesson", part="lesson", asserted_at=AT - timedelta(hours=1))
    await ended(scenario, "earlier-leak", "leaked", lesson="lesson")
    for support in ("later-support-a", "later-support-b"):
        await intention(scenario, support, state="ended", ending="achieved", ended_at=NOW)
        await resting(scenario, support, "lesson")
    await _tick(scenario.engine, NOW)
    tally = (await tally_rows(scenario))["lesson"]
    assert (tally["support"], tally["contradiction"]) == (2, 1)
    verdicts = await _verdicts(scenario.engine)
    model = _model("lesson", "weakened", verdicts["earlier-leak"]["graph_id"])
    review = _review(scenario, model)
    await review(await scenario.now())

    assert model.turn >= 1
    assert '"contradiction": 1' in model.prompt()
    for verdict in verdicts.values():
        assert verdict["graph_id"] in model.prompt(), verdict["intention_id"]
    assert [row.grade for row in await grade_rows(scenario)] == ["weakened"]


async def test_review_pages_all_contradictions_when_they_exceed_the_row_budget(scenario):
    old = AT - timedelta(days=400)
    await scenario.claim("lesson", part="lesson", asserted_at=old - timedelta(hours=1))
    await intention(scenario, "old-leak", state="ended", ending="leaked", ended_at=old,
                    asserted_at=old - timedelta(minutes=1))
    leaks = [f"leak-{i:02}" for i in range(59)]
    for leak in leaks:
        await ended(scenario, leak, "leaked")
    await _tick(scenario.engine, NOW)
    for leak in (*leaks, "old-leak"):
        await resting(scenario, leak, "lesson")
    await rebuild(scenario, "lesson")
    verdicts = {row["graph_id"] for row in (await _verdicts(scenario.engine)).values()}
    assert len(verdicts) == 60
    now = await scenario.now()
    model = WakeModel([])
    review = _review(scenario, model)
    for tick in (now, now + timedelta(seconds=REVIEW_RETRY_SECONDS)):
        await review(tick)

    assert len(model.seen) == 2
    pages = []
    for call in model.seen:
        prompt = call[0]["content"][0]["text"]
        assert prompt.count("<item ") <= 60
        assert "claim: lesson" in prompt and '"contradiction": 60' in prompt
        pages.append({ref for ref in verdicts if ref in prompt})
    assert [len(page) for page in pages] == [58, 2]
    assert pages[0].isdisjoint(pages[1]) and set.union(*pages) == verdicts


async def test_guess_profile_renders_its_refuted_grade(scenario):
    await guess(scenario)
    await scenario.entity("nora-profile", pointer_kind="nora", pointer_ref="nora")
    await write_edge(scenario, "guess", "nora-profile", "about")
    await scenario.item("later")
    review = _review(scenario, _model("guess", "refuted", "later"))
    await review()
    from nora.memory.domain.pass_inputs import WakeInput
    from tests.infra.test_pass_assembly import _assembler

    assembled = await _assembler(scenario).assemble("wake", WakeInput(()), (), SCOPE, NOW)
    profile = next(block for block in assembled.blocks if block.name == "graph_profile")
    assert "guess" in str(profile.body)
    assert "grade=refuted" in str(profile.body)


async def test_weakened_grade_publishes_dependency_on_the_lesson(scenario):
    tally = await lesson(scenario)
    now = await scenario.now()
    model = _model("lesson", "weakened", tally["graph_id"])
    review = _review(scenario, model)
    await review(now)
    [grade] = await grade_rows(scenario)
    assert "contradiction" in model.prompt() and "first-leak" in model.prompt()
    stimuli = [entry for entry in await SqlGraphStimuli(scenario.engine).read(UNIT)
               if entry.kind == "dependency" and entry.source == "review"]
    assert [(stimulus.graph_id, stimulus.occurrence) for stimulus in stimuli] == [
        ("lesson", grade.id),
    ]
    assert await wire.SqlReviewTargets(scenario.engine).due_review_targets(UNIT, now) == ()


def test_review_fails_startup_when_the_resolved_judge_is_the_writer(example_config):
    metrics = Metrics()
    cfg = example_config.model_copy(update={
        "cognition": example_config.cognition.model_copy(update={"enabled": True}),
        "lessons": example_config.lessons.model_copy(update={
            "check_model": "generated-configured-judge",
        }),
        "model_routes": {
            "lesson_check": ModelRoute(model="generated-shared-model"),
            "dreamer": ModelRoute(model="generated-shared-model"),
        },
    })
    instructions = DreamerInstructions(Path("nora/dreamer"), metrics=metrics)
    with pytest.raises(StartupError, match="independent model"):
        build_review_runner(
            cfg, instructions, None, lambda _purpose: object(), metrics, content_checks=False,
        )
