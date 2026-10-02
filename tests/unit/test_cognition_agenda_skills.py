"""The Cognition session's agenda skills as production wires them (step 4 PR-1,
`cognition-register-skill`).

Until this step every session ran with `AgendaSkills(by_kind={})`: no skill was
injected for any kind, and the 54 shadow sessions on the deploy host all carry
`skill_versions = {}`. The session had the verbs of the note and event writers it
replaces and none of their judgement (design/cognition-loop/30-cognition.md §4:
«the replaced adaptors' objectives become Cognition's skills»). Now
`AGENDA_SKILLS` maps `message`, `rollup` and `self` to ONE skill,
`cognition-register`, read through the Dreamer's instruction store.

What is pinned, each against the REAL shipped tree (`tests.objectives`):

* each mapped kind injects the skill, exactly, and records its digest;
* `time` injects nothing and the daily floors stay off for session units: a
  mapped `time` would wake the session for curation it has no verb for;
* every agenda that has a mapped kind reads ONE system prompt, whichever kinds it
  holds, so the cached prefix is shared by every session;
* the session row records the skill it ran with (here in memory; over real
  Postgres in `tests/infra/test_cognition_session_records_its_skill.py`);
* the example note body in the skill is one the real validator ACCEPTS: an example
  the validator refuses would teach the model to be refused;
* the skill stays within its size bound.

Rooms, people and words are synthetic.
"""

from __future__ import annotations

import inspect
import re
from typing import Any

import pytest

from nora.app import composition
from nora.app.cognition_session import AdaptorBudget, CognitionRunResult, CognitionSessionRunner
from nora.app.cognition_wiring import AGENDA_SKILLS, REGISTER_SKILL, AgendaSkills, agenda_skills
from nora.domain.cognition_session import Room
from nora.domain.note_body import NORA_NAMES, Shown, ShownMessage, parse_note_body
from nora.domain.stimuli import Stimulus, UnitView
from nora.testing.cognition_dry_run_store import InMemoryCognitionDryRunStore
from nora.testing.cognition_grounding import EmptyGrounder
from nora.testing.stimuli import InMemoryStimulusStore
from tests.objectives import shipped_objectives
from tests.unit.test_wake_evaluator import _floor_setup

NOW = 1_800_000_000_000
MAPPED = ("message", "rollup", "self")
#: The bound the skill is written to (the PR-1 spec): a few thousand characters of
#: cached prefix, against a session's tens of thousands of input tokens.
MAX_SKILL_CHARS = 7_000


def production_skills() -> AgendaSkills:
    return agenda_skills(shipped_objectives())


def _skill() -> Any:
    skill = shipped_objectives().skill(REGISTER_SKILL)
    assert skill is not None, "the Register skill is not installed in nora/dreamer/skills"
    return skill


# -- (a) the mapped kinds ---------------------------------------------------------------

@pytest.mark.parametrize("kind", MAPPED)
def test_each_mapped_kind_injects_the_register_skill_and_records_its_digest(kind):
    skills, skill = production_skills(), _skill()
    assert skills.text_for([kind]) == f"\n\n# Skill: {REGISTER_SKILL}\n\n{skill.instructions}\n"
    assert skills.versions_for([kind]) == {REGISTER_SKILL: skill.sha256}


def test_the_digest_is_over_exactly_the_text_injected():
    import hashlib

    skill = _skill()
    assert skill.sha256 == hashlib.sha256(skill.instructions.encode("utf-8")).hexdigest()
    assert skill.instructions.startswith("## What belongs in the notebook")


# -- (b) `time` stays unmapped and the floors stay off ---------------------------------

def test_time_injects_nothing():
    skills = production_skills()
    assert "time" not in skills.by_kind
    assert skills.text_for(["time"]) == "" and skills.versions_for(["time"]) == {}


async def test_the_floors_stay_off_for_a_session_unit_under_the_production_table():
    """The composition hands the evaluator `"time" in agenda_skills.by_kind`; under
    the production table that is False, and a session unit raises no floor."""
    source = inspect.getsource(composition)
    assert "agenda_skills = cognition_agenda_skills(dreamer_instructions)" in source
    assert 'floors_ready="time" in agenda_skills.by_kind' in source
    ev, store, _clock = _floor_setup(floors_ready="time" in production_skills().by_kind)
    assert await ev.tick() == []
    assert store.stimuli == [] and store.wakes == []


# -- (c) one system prompt for every agenda ---------------------------------------------

def test_every_agenda_with_a_mapped_kind_reads_one_system_prompt():
    skills = production_skills()
    doctrine = shipped_objectives().for_objective("cognition").text
    prompts = {
        doctrine + skills.text_for(sorted(kinds))
        for kinds in ({"message"}, {"rollup", "self"}, {"message", "rollup", "self"},
                      {"self"}, {"message", "time"})
    }
    assert len(prompts) == 1
    assert REGISTER_SKILL in next(iter(prompts))


def test_the_mapped_kinds_all_name_one_skill():
    assert set(AGENDA_SKILLS) == set(MAPPED)
    assert set(AGENDA_SKILLS.values()) == {REGISTER_SKILL}


# -- (d) the session row records the skill ----------------------------------------------

class _Spy:
    def __init__(self) -> None:
        self.system_prompt = ""

    async def run(self, *, system_prompt: str, prompt: str, tools: list[Any],
                  budget: AdaptorBudget, trace_attributes: dict[str, str]) -> CognitionRunResult:
        self.system_prompt = system_prompt
        return CognitionRunResult(stop_reason="end_turn")


def build_runner(store: Any, spy: _Spy, stimuli: Any) -> CognitionSessionRunner:
    """A runner with the production objective, prelude and skills."""
    skills = production_skills()

    async def none(*args: Any, **kwargs: Any) -> Any:
        return ()

    async def no_head(*args: Any, **kwargs: Any) -> Any:
        return None

    loaded = shipped_objectives().for_objective("cognition")
    return CognitionSessionRunner(
        dry_run_store=store, stimulus_store=stimuli, agent_runner=spy,
        home_rooms={"u": Room("signal", "room-a")}, read_tools_for=lambda u, r: [],
        skill_text_for=skills.text_for, skill_versions_for=skills.versions_for,
        grounder=EmptyGrounder(), doctrine_text=loaded.text, doctrine_sha256=loaded.sha256,
        open_note_titles=none, open_events=none, prior_standing=no_head,
        pending_self_wakes=none, pending_mirrored_reminds=none, session_mode="shadow",
        budget=AdaptorBudget(turns=1, total_tokens=1, output_tokens=1, timeout_seconds=5),
        cooldown_seconds=300, units=("u",), session_timeout_seconds=900.0, max_items=12,
        max_strikes=3,
    )


async def run_one_session(store: Any) -> _Spy:
    """One wake's session over one `message` item, against `store` (in memory here,
    Postgres in tests/infra)."""
    spy = _Spy()
    stimuli = InMemoryStimulusStore()
    stimuli.seed_wake("u", 1)
    view = UnitView(
        unit="u", last_activity_ms=None, last_wake=None,
        pending=(Stimulus(
            id=1, unit="u", kind="message", item="message", ref="messages:1", at_ms=NOW,
            recorded_at_ms=NOW, platform="signal", conversation_id="room-a"),),
    )
    await build_runner(store, spy, stimuli).on_wake(unit="u", view=view, now_ms=NOW, wake_id=1)
    return spy


async def test_the_session_row_records_the_skill_it_ran_with():
    store = InMemoryCognitionDryRunStore()
    spy = await run_one_session(store)
    skill = _skill()
    assert [s.skill_versions for _id, s, _at in store.sessions] == [
        {REGISTER_SKILL: skill.sha256},
    ]
    assert spy.system_prompt.endswith(f"# Skill: {REGISTER_SKILL}\n\n{skill.instructions}\n")


async def test_the_probe_an_eval_arm_drives_records_the_skill_it_injects():
    """`tool_selection_cognition` runs through `probe`, which injected the skill and
    recorded `skill_versions = {}`: an eval arm's rows would have named no skill."""
    store, spy = InMemoryCognitionDryRunStore(), _Spy()
    await build_runner(store, spy, InMemoryStimulusStore()).probe(
        unit="u", summary="a plan is being made", now_ms=NOW,
    )
    assert REGISTER_SKILL in spy.system_prompt
    assert [s.skill_versions for _id, s, _at in store.sessions] == [
        {REGISTER_SKILL: _skill().sha256},
    ]


# -- (g) the example body is one the validator accepts ----------------------------------

#: The synthetic messages the example cites: `n` -> `from`, as an agenda shows them.
EXAMPLE_SHOWN = {5201: "Ben", 5202: "Ada", 5203: "Ben", 5204: "Cat", 5205: "Cat", 5206: "Cat"}
EXAMPLE_ROSTER = {"Ada": "p-ada", "Ben": "p-ben", "Cat": "p-cat"}


def example_body(text: str) -> str:
    blocks = re.findall(r"^```\n(.*?)^```$", text, flags=re.MULTILINE | re.DOTALL)
    assert len(blocks) == 1, "the skill shows exactly one example body"
    return blocks[0]


def test_the_example_body_is_accepted_by_the_real_validator():
    body = example_body(_skill().instructions)
    shown = Shown(
        rendered=tuple(ShownMessage(id=n, sender=s, person_id=EXAMPLE_ROSTER[s])
                       for n, s in EXAMPLE_SHOWN.items()),
        roster=EXAMPLE_ROSTER, nora_names=NORA_NAMES,
    )
    parsed = parse_note_body(body, shown=shown, require_open=True)
    # every section the model writes is demonstrated, each line resting on its ids
    assert [s.heading for s in parsed.sections] == [
        "Decided", "Open", "Owed", "Proposed", "Ruled out",
    ]
    assert parsed.cited_ids == frozenset(EXAMPLE_SHOWN)
    assert parsed.text == body.rstrip("\n")          # already in normal form: nothing repaired


# -- (h) the size bound -------------------------------------------------------------------

def test_the_skill_stays_within_its_size_bound():
    assert 0 < len(_skill().instructions) <= MAX_SKILL_CHARS


# -- every name the skill and the prelude use exists ------------------------------------

async def test_every_name_in_backticks_is_a_close_reason_or_a_fixed_note_token():
    """No tool or token the catalogue does not have: a name the model cannot call
    teaches it to call it. The note grammar's fixed phrases are keys the validator
    reads (`nora.domain.note_body`), so they are names too."""
    from nora.domain.cognition_session import CLOSE_REASONS

    spy = await run_one_session(InMemoryCognitionDryRunStore())
    prelude = spy.system_prompt.split("</dreamer_doctrine>")[0]
    skill = spy.system_prompt.split(f"# Skill: {REGISTER_SKILL}")[1]
    named = set(re.findall(r"`([^`\n]+)`", prelude + skill))
    assert named, "the positive control: the skill names a close reason"
    fixed_tokens = {"no reason given", "no date given"}
    assert named <= (set(CLOSE_REASONS) - {"struck"}) | fixed_tokens, named


def test_a_mapped_skill_that_is_not_installed_stops_the_runner_at_startup():
    """Not the first wake's failure: a pull that ships the mapping's image without
    the skill file (or a typo in its name) is refused where the composition can
    say which skill, before any session runs."""
    from types import SimpleNamespace

    from nora.app.cognition_wiring import build_cognition_runner
    from nora.obs.metrics import Metrics

    stub: Any = SimpleNamespace()
    missing = AgendaSkills(by_kind={"message": "not-installed"}, resolve=lambda name: None)
    with pytest.raises(LookupError, match="not-installed"):
        build_cognition_runner(
            cfg=stub, models=stub, retry=stub, metrics=Metrics(),
            tools=SimpleNamespace(tool_param_names=lambda: {}),
            instructions=shipped_objectives(), dry_run_store=stub, stimulus_store=stub,
            note_titles=stub, note_head=stub, room_proactive=stub, requester_pending=stub,
            reminder_caps=stub, open_events_in=stub, standing_of=stub, homes={}, units=(),
            skills=missing, grounder=stub, render_budget=stub,
            surface=SimpleNamespace(wired=frozenset()), clock=lambda: 0.0, model_id="m",
            model_route="r", budget=stub, result_caps={},
        )
