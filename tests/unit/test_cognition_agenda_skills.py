"""Hot-loaded notebook guidance and the session's instruction provenance."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from nora.app.cognition_session import AdaptorBudget, CognitionRunResult, CognitionSessionRunner
from nora.app.cognition_wiring import (
    REGISTER_SKILL,
    SessionGuidance,
    SkillNotInstalledError,
    session_guidance,
)
from nora.domain.cognition_session import Room
from nora.domain.note_body import (
    Shown,
    ShownMessage,
)
from nora.domain.stimuli import Stimulus, UnitView
from nora.instructions.dreamer import DreamerInstructions
from nora.obs.metrics import Metrics
from nora.testing.cognition_grounding import EmptyGrounder
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.stimuli import InMemoryStimulusStore
from tests.objectives import SHIPPED_DREAMER, shipped_objectives

NORA_NAMES = frozenset({"Nora"})
NOW = 1_800_000_000_000
#: The bound the skill is written to: a few thousand characters of
#: cached prefix, against a session's tens of thousands of input tokens.
MAX_SKILL_CHARS = 3_000


def production_skills() -> SessionGuidance:
    return session_guidance(shipped_objectives())


def _skill() -> Any:
    skill = shipped_objectives().skill(REGISTER_SKILL)
    assert skill is not None, "the Register skill is not installed in nora/dreamer/skills"
    return skill


class _Spy:
    def __init__(self) -> None:
        self.system_prompt = ""
        self.tools: list[Any] = []

    async def run(self, *, system_prompt: str, prompt: str, tools: list[Any],
                  budget: AdaptorBudget, trace_attributes: dict[str, str]) -> CognitionRunResult:
        self.system_prompt = system_prompt
        self.tools = list(tools)
        return CognitionRunResult(stop_reason="end_turn")


def build_runner(
    store: Any, spy: _Spy, stimuli: Any, *, skills: SessionGuidance | None = None,
    grounder: Any = None, memory: Any = None,
) -> CognitionSessionRunner:
    """A runner with the production objective, prelude and skills, wired as
    `build_cognition_runner` wires them (the text, the versions and the one
    resolution that carries both)."""
    skills = skills or production_skills()

    async def none(*args: Any, **kwargs: Any) -> Any:
        return ()

    async def no_head(*args: Any, **kwargs: Any) -> Any:
        return None

    loaded = shipped_objectives().for_objective("cognition")
    return CognitionSessionRunner(
        store=store, stimulus_store=stimuli, agent_runner=spy, memory=memory,
        home_rooms={"u": Room("signal", "room-a")}, read_tools_for=lambda u, r: [],
        skill_text_for=skills.text, skill_versions_for=skills.versions,
        skills_for=skills.resolved,
        grounder=grounder or EmptyGrounder(), doctrine_text=loaded.text,
        doctrine_sha256=loaded.sha256,
        open_note_titles=none, open_events=none, prior_standing=no_head,
        budget=AdaptorBudget(turns=1, total_tokens=1, output_tokens=1, timeout_seconds=5),
        cooldown_seconds=300, units=("u",), session_timeout_seconds=900.0, max_items=12,

    )


def one_message_view() -> UnitView:
    return UnitView(
        unit="u", last_activity_ms=None, last_wake=None,
        pending=(Stimulus(
            id=1, unit="u", kind="message", item="message", ref="messages:1", at_ms=NOW,
            recorded_at_ms=NOW, platform="signal", conversation_id="room-a"),),
    )


async def run_one_session(store: Any) -> _Spy:
    """One wake's session over one `message` item, against `store` (in memory here,
    Postgres in tests/infra)."""
    from tests.unit.test_cognition_session_runner import wake_memory

    spy = _Spy()
    stimuli = InMemoryStimulusStore()
    stimuli.seed_wake("u", 1)
    await build_runner(store, spy, stimuli, memory=wake_memory()).on_wake(
        unit="u", view=one_message_view(), now_ms=NOW, wake_id=1,
    )
    return spy


async def test_the_session_row_records_the_skill_it_ran_with():
    store = InMemoryCognitionStore()
    spy = await run_one_session(store)
    skill = _skill()
    assert [s.skill_versions for _id, s, _at in store.sessions] == [
        {REGISTER_SKILL: skill.sha256},
    ]
    assert skill.instructions in spy.system_prompt


async def test_the_probe_an_eval_arm_drives_records_the_skill_it_injects():
    """`tool_selection_cognition` runs through `probe`, which injected the skill and
    recorded `skill_versions = {}`: an eval arm's rows would have named no skill."""
    store, spy = InMemoryCognitionStore(), _Spy()
    await build_runner(store, spy, InMemoryStimulusStore()).probe(
        unit="u", summary="a plan is being made", now_ms=NOW,
    )
    assert _skill().instructions in spy.system_prompt
    assert [s.skill_versions for _id, s, _at in store.sessions] == [
        {REGISTER_SKILL: _skill().sha256},
    ]


#: The synthetic messages the example cites: `n` -> `from`, as an agenda shows them.
EXAMPLE_SHOWN = {5201: "Ben", 5202: "Ada", 5203: "Ben", 5204: "Cat", 5205: "Cat", 5206: "Cat"}
EXAMPLE_ROSTER = {"Ada": "p-ada", "Ben": "p-ben", "Cat": "p-cat"}


#: Fixed phrases of the note grammar: keys the validator reads (`nora.domain.note_body`).
FIXED_TOKENS = {"no reason given", "no date given"}


#: A snake_case word used by generated tool names and arguments.
SNAKE = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b")


RELATIVE_DAY = re.compile(
    r"\b(?:today|tomorrow|tonight|yesterday|this|next|last|coming)\b|聽日|今晚|聽晚|尋日",
    re.IGNORECASE,
)


#: Shown messages for the bodies below.
OFF_SHOWN = Shown(
    rendered=tuple(ShownMessage(id=n, sender=s, person_id=EXAMPLE_ROSTER[s])
                   for n, s in {5301: "Ben", 5302: "Cat"}.items()),
    roster=EXAMPLE_ROSTER, nora_names=NORA_NAMES,
)

ONLY_RULED_OUT = "## Decided\n## Open\n## Ruled out\n- Hike on Saturday: rain (Cat) [5302]\n"
RULED_OUT_WITH_AN_OPEN_LINE = (
    "## Decided\n## Open\n- Whether to hike another Saturday [5302]\n"
    "## Ruled out\n- Hike on Saturday: rain (Cat) [5302]\n"
)


#: A sentence that says a thing is turned away.
REFUSAL = re.compile(
    r"\brefus|\breject|will fail|not accepted|is not allowed|\binvalid|\bbounced?\b",
    re.IGNORECASE,
)

#: Each rule the skill states, as a pattern that finds the sentences about it, and whether
#: the validator or the verbs REALLY turn a breach away. Only an enforced rule may sit in a
#: sentence with a refusal word; the rest are requests.
RULES: dict[str, tuple[str, bool]] = {
    "Decided and Open both empty": (r"under Decided|under Open", True),
    "a line with no cited message": (r"point to a message", True),
    "an Owed line's owner": (r"\bOwed\b|who owes", False),
    "the closing condition": (r"closing_condition|closing condition", False),
    "the anchor date and its weekday": (r"anchor_date|weekday", False),
    "a day word in a line": (r"this Saturday|as the date", False),
    "the ids an update keeps": (r"\bids?\b", False),
    "where people were in a line": (r"where people were|whereabouts", False),
    "reasoning modes": (r"\btold\b|\bdeduced\b|\babduced\b", False),
}


class _MovingSkills:
    """A resolver that reloads on a cue: each load names a new generation, as an
    edit of the file on the bind mount does."""

    def __init__(self) -> None:
        self.generation = 0

    def resolve(self, name: str) -> Any:
        import hashlib
        from types import SimpleNamespace

        text = f"skill text, load {self.generation}"
        return SimpleNamespace(
            instructions=text, sha256=hashlib.sha256(text.encode()).hexdigest(),
        )

    def reload(self) -> None:
        self.generation += 1


class _ReloadingGrounder(EmptyGrounder):
    """The awaited grounding read: the window in which the corpus reloads."""

    def __init__(self, moving: _MovingSkills) -> None:
        self._moving = moving

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self._moving.reload()
        return await super().__call__(*args, **kwargs)


async def test_a_reload_during_grounding_leaves_the_digest_over_the_text_the_model_read():
    """`skill_versions` and the text are read together, not either side of the grounding
    await: a hot reload between them would record a hash of text the model never saw."""
    import hashlib

    moving = _MovingSkills()
    skills = SessionGuidance(resolve=moving.resolve)
    store, spy, stimuli = InMemoryCognitionStore(), _Spy(), InMemoryStimulusStore()
    stimuli.seed_wake("u", 1)
    runner = build_runner(
        store, spy, stimuli, skills=skills, grounder=_ReloadingGrounder(moving),
    )
    await runner.on_wake(unit="u", view=one_message_view(), now_ms=NOW, wake_id=1)

    assert moving.generation == 1, "the reload happened during the session setup"
    [(_id, session, _at)] = store.sessions
    read = "skill text, load 0"
    assert read in spy.system_prompt and "skill text, load 1" not in spy.system_prompt
    assert session.skill_versions == {REGISTER_SKILL: hashlib.sha256(read.encode()).hexdigest()}


async def test_a_mapped_skill_deleted_by_a_hot_reload_fails_the_wake_cleanly():
    """The file is gone on a live reload: the session that needs it records a `failed`
    wake (cause `infra`: the unit backs off, no message is struck), names the skill,
    and the model never runs without it."""
    skills = SessionGuidance(resolve=lambda name: None)
    store, spy, stimuli = InMemoryCognitionStore(), _Spy(), InMemoryStimulusStore()
    stimuli.seed_wake("u", 1)
    runner = build_runner(store, spy, stimuli, skills=skills)
    with pytest.raises(SkillNotInstalledError, match=REGISTER_SKILL):
        await runner.on_wake(unit="u", view=one_message_view(), now_ms=NOW, wake_id=1)
    outcome = stimuli.outcomes[1]
    assert (outcome.outcome, outcome.cause) == ("failed", "infra")
    assert REGISTER_SKILL in outcome.detail
    assert spy.system_prompt == "" and store.sessions == []        # the model never ran


def _editable_tree(tmp_path: Path) -> Path:
    root = tmp_path / "dreamer"
    shutil.copytree(SHIPPED_DREAMER, root)
    return root


def test_a_skill_edited_on_the_bind_mount_is_live_at_the_next_session(tmp_path):
    """The production `resolve` is the instruction store's, not a cached copy, so a
    memoised `resolve` would fail here."""
    import os
    import time

    root = _editable_tree(tmp_path)
    store = DreamerInstructions(root, metrics=Metrics())        # reload = mtime
    skills = session_guidance(store)
    before_text, before = skills.resolved()
    assert "Use the notebook to continue the same matter" in before_text

    path = root / "skills" / "cognition-register.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Use the notebook to continue the same matter",
            "Use the notebook to continue the existing matter",
        ),
        encoding="utf-8",
    )
    future = time.time() + 5
    os.utime(path, (future, future))

    after_text, after = skills.resolved()
    assert "Use the notebook to continue the existing matter" in after_text
    assert after != before


def _build_with(instructions: Any, skills: SessionGuidance) -> Any:
    """`build_cognition_runner` over stubs, as far as its startup checks: they all run
    before the runner is built, so a stub is enough to see them refuse."""
    from types import SimpleNamespace

    from nora.app.cognition_wiring import build_cognition_runner

    stub: Any = SimpleNamespace()
    return build_cognition_runner(
        cfg=stub, models=stub, retry=stub, metrics=Metrics(),
        tools=SimpleNamespace(tool_param_names=lambda: {}),
        instructions=instructions, store=stub, stimulus_store=stub,
        note_titles=stub, room_proactive=stub, notes=stub, events=stub, standing=stub,
        reminders=stub, ai_peer="nora",
        reminder_caps=stub, open_events_in=stub, standing_of=stub, homes={}, units=(),
        skills=skills, grounder=stub, render_budget=stub,
        surface=SimpleNamespace(wired=frozenset()), clock=lambda: 0.0, model_id="m",
        model_route="r", budget=stub, result_caps={},
    )


def test_a_mapped_skill_that_is_not_installed_stops_the_runner_at_startup():
    """Not the first wake's failure: a pull that ships the mapping's image without
    the skill file (or a typo in its name) is refused where the composition can
    say which skill, before any session runs."""
    missing = SessionGuidance(resolve=lambda name: None)
    with pytest.raises(SkillNotInstalledError, match=REGISTER_SKILL):
        _build_with(shipped_objectives(), missing)


PRELUDE_FILE = Path("preludes") / "cognition-prelude.md"


def _without_prelude(tmp_path: Path, how: str) -> DreamerInstructions:
    """The shipped tree with its Cognition prelude gone, emptied of what it applies
    to, or aimed at another objective: each leaves a tree the LOADER accepts and
    serves the shared Dreamer doctrine («you look; you do not act») to `cognition`."""
    root = _editable_tree(tmp_path)
    path = root / PRELUDE_FILE
    if how == "deleted":
        path.unlink()
    else:
        assert how == "elsewhere"            # an empty list is the loader's own refusal
        text = path.read_text(encoding="utf-8")
        assert "applies_to: [cognition]" in text
        path.write_text(text.replace("applies_to: [cognition]", "applies_to: [culture]"),
                        encoding="utf-8")
    return DreamerInstructions(root, metrics=Metrics())


@pytest.mark.parametrize("how", ["deleted", "elsewhere"])
def test_a_cognition_objective_with_no_prelude_stops_the_runner_at_startup(tmp_path, how):
    """The silent fallback: the loader serves the shared doctrine to an objective that
    has no prelude, with no error and no counter."""
    from nora.app.cognition_wiring import PreludeMissingError

    instructions = _without_prelude(tmp_path, how)
    served = instructions.for_objective("cognition")
    assert "one bounded reflection" in served.text, "the loader falls back, silently"
    with pytest.raises(PreludeMissingError, match="cognition-prelude"):
        _build_with(instructions, production_skills())
