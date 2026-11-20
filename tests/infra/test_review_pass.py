"""Run the scheduled judge through its real tools and scoped Postgres reads."""

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
from nora.app.review_pass import ReviewPass
from nora.app.startup import StartupError
from nora.config import ModelRoute
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.instructions.dreamer import DreamerInstructions
from nora.memory import ReviewInput, wire
from nora.memory.domain.pass_declarations import declaration
from nora.obs.metrics import Metrics
from nora.testing.scripted_agent import ScriptedTurn
from tests.fakes import dreamer_runtime_for
from tests.infra.test_graph_propose import BASIS
from tests.infra.test_pass_anchors import NOW, SCOPE, reader
from tests.infra.test_retrieval_scope import ROOM, UNIT, write_edge
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_review_admission import grade_rows, guess, lesson
from tests.infra.test_room_busy import service, slots
from tests.infra.test_wake_runner import WakeModel
from tests.unit.test_dream_memory_budget import _tool_results


class _Room:
    async def scope_for(self, unit, room):
        assert (unit, room) == (UNIT, f"signal:{ROOM}")
        return SCOPE, False


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
    return ReviewPass(
        lessons=wire.SqlReviewLessons(scenario.engine), rooms=_Room(),
        assembler=wire.pass_assembler(scenario.engine, access, lineage, None, None),
        surface=surface, admission=admission, agent=DreamerAgentRunner(
            runtime=runtime, metrics=metrics, agent_name="review", bound_tool_arguments=True,
        ),
        instructions=DreamerInstructions(Path("nora/dreamer"), metrics=metrics),
        budget=AdaptorBudget(turns, 5000, 1000, 30.0), model="scripted-judge",
        revisions=dict, policy=scenario.policy,
    )


def _model(target, outcome, citation, *, model=WakeModel):
    operation = {"op": "create_claim", "part": "grade", "target": target, "grade": outcome,
                 "content": "Generated assessment", "citations": [citation], **BASIS}
    return model([ScriptedTurn(tool_calls=[("graph_propose", {"operations": [operation]})])])


async def _run(review, now=NOW):
    await review.run_once(UNIT, f"signal:{ROOM}", now)


async def test_review_grades_due_guess_once_and_removes_it_from_anchors(scenario):
    await guess(scenario)
    await scenario.item("later")
    model = _model("guess", "refuted", "later")
    review = _review(scenario, model)
    await _run(review)
    [stored] = await grade_rows(scenario)
    assert tuple(stored)[1:] == ("refuted", "scripted-judge")
    assert model.tools == {"graph_propose", "memory_read", "memory_search"}
    assert "Generated prediction" in model.prompt() and "later" in model.prompt()
    anchors = await reader(scenario).build(declaration("review"), ReviewInput(), SCOPE, NOW)
    assert anchors.cause is None and anchors.review_due == ()
    turns = model.turn
    await _run(review, NOW + timedelta(hours=1))
    assert model.turn == turns


async def test_incomplete_judge_writes_nothing_and_retries_on_its_bound(scenario):
    await guess(scenario)
    await scenario.item("later")
    model = _model("guess", "refuted", "later", model=_TruncatedReview)
    review = _review(scenario, model)
    await _run(review)
    assert model.turn == 2
    assert "Proposals staged" in str(_tool_results(model))
    assert await grade_rows(scenario) == []
    await _run(review, NOW + timedelta(seconds=1))
    assert model.turn == 2
    anchors = await reader(scenario).build(declaration("review"), ReviewInput(), SCOPE, NOW)
    assert anchors.review_due == ("guess",)


async def test_guess_profile_renders_its_refuted_grade(scenario):
    await guess(scenario)
    await scenario.entity("nora-profile", pointer_kind="nora", pointer_ref="nora")
    await write_edge(scenario, "guess", "nora-profile", "about")
    await scenario.item("later")
    await _run(_review(scenario, _model("guess", "refuted", "later")))
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
    await _run(_review(scenario, model), now)
    [grade] = await grade_rows(scenario)
    assert "contradiction" in model.prompt() and "first-leak" in model.prompt()
    stimuli = [entry for entry in await SqlGraphStimuli(scenario.engine).read(UNIT)
               if entry.kind == "dependency" and entry.source == "review"]
    assert [(stimulus.graph_id, stimulus.occurrence) for stimulus in stimuli] == [
        ("lesson", grade.id),
    ]
    assert await wire.SqlReviewLessons(scenario.engine).due_lessons(UNIT) == ()


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
