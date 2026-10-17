"""`nora.app.cognition_wiring` and the session runner's wiring seams (DEC-244): which
read tools a session holds, its budget, what it is told, how it is stopped and how it is traced.

No model and no Strands: the agent runner is a recording double and the
tool source is a fake that answers like the registry's (a list of tool
objects for the names it was asked for).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from nora.agent import factory
from nora.agent.dreamer import AdaptorGrant, cognition_grant
from nora.agent.tools import NATIVE_TOOLS
from nora.app import cognition_agent_runner as agent_runner_module
from nora.app.cognition_agent_runner import LiveCognitionAgentRunner
from nora.app.cognition_session import (
    AdaptorBudget,
    CognitionRunResult,
    CognitionSessionRunner,
    NotReadOnlyToolError,
)
from nora.app.cognition_wiring import (
    COGNITION_READ_CATALOGUE,
    AgendaSkills,
    CognitionShutdown,
    cognition_budget,
    read_surface,
    read_tools_for,
    require_session_hook,
    units_with_home_rooms,
)
from nora.config import DreamBudgetConfig
from nora.domain.cognition_ground import Grounding
from nora.domain.cognition_session import Room
from nora.domain.stimuli import Stimulus, UnitView
from nora.obs.metrics import Metrics
from nora.testing.cognition_grounding import EmptyGrounder, StaticGrounder
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.stimuli import InMemoryStimulusStore
from tests.unit.cognition_view import blocks, items, named

NOW = 1_790_000_000_000
HOME = Room("signal", "home")
OTHER = Room("discord", "other")
READ_ONLY = frozenset(n for n, t in NATIVE_TOOLS.items() if t.read_only)
FLAGS_ON = {"record_query.enabled", "culture.enabled"}


# ---- the read catalogue ------------------------------------------------------

def test_the_catalogue_is_the_designs_and_names_no_live_social_read():
    """The warehouse queries and the tiers' reads. The live
    `instagram_*`, `x_*` and `linkedin_*` reads stay on the tag path."""
    assert {"record_query", "note_read", "about", "event_search"} <= COGNITION_READ_CATALOGUE
    live_social = {n for n in NATIVE_TOOLS if n.startswith(("instagram_", "x_", "linkedin_"))}
    assert live_social, "positive control: the tool table has live social reads"
    assert not (COGNITION_READ_CATALOGUE & live_social)


def test_every_tool_the_session_would_wire_exists_and_is_read_only():
    surface = read_surface(
        read_only=READ_ONLY, enabled_flags=FLAGS_ON, registry_withheld=frozenset())
    assert surface.wired, "positive control"
    assert surface.wired <= READ_ONLY
    assert all(NATIVE_TOOLS[n].read_only for n in surface.wired)


def test_the_designs_tools_the_tree_does_not_have_are_named_missing_not_dropped():
    surface = read_surface(
        read_only=READ_ONLY, enabled_flags=FLAGS_ON, registry_withheld=frozenset())
    assert surface.missing == set()
    assert surface.wired | surface.missing | surface.withheld == COGNITION_READ_CATALOGUE


def test_record_query_is_withheld_until_its_flag_is_on():
    off = read_surface(read_only=READ_ONLY, enabled_flags=(), registry_withheld=frozenset())
    on = read_surface(read_only=READ_ONLY, enabled_flags=FLAGS_ON, registry_withheld=frozenset())
    assert "record_query" in off.withheld and "record_query" not in off.wired
    assert "record_query" in on.wired and "record_query" not in on.withheld


def test_an_act_verb_is_never_wired_even_when_the_catalogue_names_it():
    """The read-only set decides, not the catalogue: a name that is not in it
    is `missing`, never wired."""
    surface = read_surface(
        read_only=frozenset({"note_read"}), enabled_flags=(), registry_withheld=frozenset())
    assert surface.wired == {"note_read"}
    assert "about" in surface.missing


# ---- budgets ------------------------------------------------------------------

def test_the_session_budget_carries_every_field_the_dreamer_budget_gives_it():
    """A field added to one side and not the other would be dropped in silence
    (`_adaptor_budget`'s own reason). The two per-pass call ceilings are the
    exception on purpose (DEC-247): the session carries none, so the Dreamer
    budget's values for them never reach it."""
    configured = DreamBudgetConfig(
        turns=7, total_tokens=11, output_tokens=13, timeout_seconds=17.0,
        max_memory_calls=3, max_tool_calls=19,
    )
    got = cognition_budget(configured)
    assert isinstance(got, AdaptorBudget)
    for f in dataclasses.fields(AdaptorBudget):
        if f.name in {"max_memory_calls", "max_tool_calls"}:
            assert getattr(got, f.name) is None, f.name
        else:
            assert getattr(got, f.name) == getattr(configured, f.name), f.name


def test_every_tool_in_the_catalogue_has_an_audited_side_effect_line():
    """«Shadow writes nothing live» is a claim about EVERY
    tool the session holds, so a tool added to the design's catalogue without an
    audit of what it writes fails here, not in a shadow week. The audit itself
    is run, against real Postgres, by `tests/infra/test_a_cognition_sessions_
    read_tools_write_nothing.py`."""
    from nora.app.cognition_wiring import READ_TOOL_AUDIT

    assert set(READ_TOOL_AUDIT) == set(COGNITION_READ_CATALOGUE)
    assert all(text.strip() for text in READ_TOOL_AUDIT.values())
    # `note_search` records no miss (DEC-277): its audit line says it writes
    # nothing and names no neutralised write.
    assert "No write" in READ_TOOL_AUDIT["note_search"]
    assert "NEUTRALISED" not in READ_TOOL_AUDIT["note_search"]


# ---- read tools for a room ---------------------------------------------------

class _Source:
    """Answers like `RegistryToolSource.build`: the tools for the names asked."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def build(self, **kw: Any) -> list[Any]:
        self.calls.append(kw)
        return [_tool(n) for n in sorted(kw["names"])]

    def read_only(self) -> frozenset[str]:
        return READ_ONLY

    def tool_param_names(self) -> dict[str, frozenset[str]]:
        return {}


def _tool(name: str) -> Any:
    async def call(x: str = "") -> dict[str, object]:
        """Look."""
        return {"status": "ok"}

    call.__name__ = name
    return call


def test_read_tools_are_built_for_the_agenda_room_under_the_cognition_grant():
    source = _Source()
    build = read_tools_for(source, names=frozenset({"note_read", "about"}))
    tools = build("friends", [Room("signal", "g1")])
    assert sorted(t.__name__ for t in tools) == ["about", "note_read"]
    (call,) = source.calls
    assert call["key"] == "g1" and call["unit"] is None       # scope-resolved: the room's own
    assert call["adaptor"] == "cognition"
    assert call["grant"] == cognition_grant() == AdaptorGrant(home_room=True)


# ---- what the runner does with them ------------------------------------------

@dataclass
class _Agent:
    prompts: list[str] = field(default_factory=list)
    system_prompts: list[str] = field(default_factory=list)
    tool_names: list[list[str]] = field(default_factory=list)
    attrs: list[dict[str, str]] = field(default_factory=list)

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: dict[str, str],
    ) -> CognitionRunResult:
        self.system_prompts.append(system_prompt)
        self.prompts.append(prompt)
        self.attrs.append(dict(trace_attributes))
        self.tool_names.append(sorted(getattr(t, "__name__", "") for t in tools))
        return CognitionRunResult(stop_reason="end_turn")


def _stim(sid: int, room: Room, at: int = NOW - 90 * 60_000) -> Stimulus:
    return Stimulus(
        id=sid, unit="u", kind="message", item="message", ref=f"messages:{sid}", at_ms=at,
        recorded_at_ms=at, platform=room.platform, conversation_id=room.conversation_id,
    )


def _runner(
    *, agent: _Agent, read_tools_for: Any = None, wake_ids: tuple[int, ...] = (1,),
    metrics: Any = None, grounder: Any = None, **kw: Any,
) -> tuple[CognitionSessionRunner, InMemoryStimulusStore, InMemoryCognitionStore]:
    ledger = InMemoryCognitionStore()
    stimuli = InMemoryStimulusStore(ledger=ledger)
    for wid in wake_ids:
        stimuli.seed_wake("u", wid)

    async def const(*a: Any, **k: Any) -> Any:
        return kw.pop("_value", ())

    runner = CognitionSessionRunner(
        store=ledger, stimulus_store=stimuli, agent_runner=agent,
        home_rooms={"u": HOME}, read_tools_for=read_tools_for or (lambda unit, rooms: []),
        read_only_tools=frozenset({"note_read", "about"}),
        skill_text_for=kw.pop("skill_text_for", lambda kinds: ""),
        grounder=grounder or EmptyGrounder(),
        doctrine_text="FIXED", doctrine_sha256="fixed-sha",
        open_note_titles=const, open_events=const, prior_standing=const,
        budget=AdaptorBudget(turns=4, total_tokens=1000, output_tokens=100, timeout_seconds=30),
        cooldown_seconds=300.0, units=("u",), session_timeout_seconds=900.0,
        max_items=12, max_strikes=3, metrics=metrics, clock=lambda: NOW / 1000, **kw,
    )
    return runner, stimuli, ledger


async def _wake(runner: CognitionSessionRunner, pending: tuple[Stimulus, ...], wake_id: int = 1):
    view = UnitView(unit="u", pending=pending, last_activity_ms=None, last_wake=None)
    return await runner.on_wake(unit="u", view=view, now_ms=NOW, wake_id=wake_id)


async def test_the_objective_is_read_at_every_session_not_captured_at_startup():
    """DEC-128: the Dreamer's objective hot-reloads, and a text captured when
    the runner was built keeps serving one the operator has already edited."""
    texts = iter([("first text", "sha-1"), ("second text", "sha-2")])
    agent = _Agent()
    runner, stimuli, ledger = _runner(agent=agent, doctrine_source=lambda: next(texts))
    await _wake(runner, (_stim(1, HOME),), wake_id=1)
    stimuli.seed_wake("u", 2)                    # the next wake, written after the first ended
    await _wake(runner, (_stim(2, HOME),), wake_id=2)
    assert agent.system_prompts == ["first text", "second text"]
    assert [s.doctrine_sha256 for _, s, _ in ledger.sessions] == ["sha-1", "sha-2"]


async def test_the_skills_injected_for_the_agenda_kinds_are_recorded_on_the_session():
    seen: list[list[str]] = []

    def text_for(kinds: Any) -> str:
        seen.append(list(kinds))
        return "\n\n# Skill: S\n"

    agent = _Agent()
    runner, _stimuli, ledger = _runner(
        agent=agent, skill_text_for=text_for,
        skill_versions_for=lambda kinds: {"plans": "abc"},
    )
    await _wake(runner, (_stim(1, HOME),))
    assert agent.system_prompts == ["FIXED\n\n# Skill: S\n"]
    assert seen[0] == ["message"]
    assert ledger.sessions[0][1].skill_versions == {"plans": "abc"}


async def test_an_agenda_in_one_room_binds_the_reads_to_that_room():
    asked: list[list[Room]] = []

    def reads(unit: str, rooms: Sequence[Room]) -> list[Any]:
        asked.append(list(rooms))
        return [_tool("note_read")]

    agent = _Agent()
    labels = Grounding(room_labels={"discord:other": "The Other Room"})
    runner, _s, _l = _runner(agent=agent, read_tools_for=reads, grounder=StaticGrounder(labels))
    await _wake(runner, (_stim(1, OTHER),))
    assert asked == [[OTHER]]
    # the prompt says the room's label; its id is for a tool's argument only (m5)
    assert "Your read tools look through room «The Other Room»." in agent.prompts[0]
    assert "discord:other" not in agent.prompts[0]


async def test_an_agenda_spanning_rooms_asks_for_each_rooms_reads_and_never_the_home_room_instead():
    """DEC-231: each item's reads resolve through
    that item's room. The session hands the source EVERY agenda room, in agenda
    order, and never substitutes the unit's home room for an item's; the prompt
    says the reads take a `room` and lists them."""
    asked: list[list[Room]] = []

    def reads(unit: str, rooms: Sequence[Room]) -> list[Any]:
        asked.append(list(rooms))
        return [_tool("note_read"), _tool("about")]

    agent = _Agent()
    labels = Grounding(room_labels={"discord:other": "The Other Room", "whatsapp:third": "Third"})
    runner, _s, _l = _runner(agent=agent, read_tools_for=reads, grounder=StaticGrounder(labels))
    third = Room("whatsapp", "third")
    await _wake(runner, (_stim(1, OTHER), _stim(2, third), _stim(3, OTHER)))
    assert asked == [[OTHER, third]]              # once, both rooms, agenda order
    assert HOME not in asked[0]
    assert (
        "take a `room`, one of: `discord:other` («The Other Room»), `whatsapp:third` («Third»)"
        in agent.prompts[0]
    )
    assert "look through room" not in agent.prompts[0]


async def test_a_read_tool_handed_twice_is_refused_not_kept_last():
    runner, _s, _l = _runner(
        agent=_Agent(), read_tools_for=lambda u, rs: [_tool("note_read"), _tool("note_read")],
    )
    with pytest.raises(NotReadOnlyToolError, match="handed twice"):
        await _wake(runner, (_stim(1, HOME),))


async def test_the_prompt_says_what_time_it_is_and_how_old_each_item_is():
    """A judgement that turns on WHEN needs the time in its input: an agenda
    with none reads every item as «just now»."""
    agent = _Agent()
    runner, _s, _l = _runner(agent=agent)
    await _wake(runner, (_stim(1, HOME, at=NOW - 90 * 60_000),))
    prompt = agent.prompts[0]
    # the clock is the FIRST thing she reads, then which room her reads look through
    assert [b.tag for b in blocks(prompt)][:2] == ["clock", "reads"]
    (clock,) = named(prompt, "clock")
    (item,) = items(prompt)
    assert (clock.text or "").strip().startswith("It is ") and "(UTC)" in (clock.text or "")
    assert "This is your first look at this group." in (clock.text or "")
    assert "1 h 30 min ago" in item.attrib["window"], item.attrib


async def test_probe_runs_one_hand_written_item_and_writes_no_outcome():
    """The seam `tool_selection_cognition` drives: same tools, same doctrine,
    same agent runner; no wake, so no outcome and no carry."""
    agent = _Agent()
    runner, stimuli, ledger = _runner(
        agent=agent, read_tools_for=lambda u, rs: [_tool("note_read")],
    )
    result = await runner.probe(unit="u", summary="room A has 2 new messages", now_ms=NOW)
    assert result.stop_reason == "end_turn"
    assert "room A has 2 new messages" in agent.prompts[0]
    assert {"close", "note_create", "remind", "wake_at", "note_read"} <= set(agent.tool_names[0])
    assert len(ledger.sessions) == 1 and stimuli.outcomes == {}




async def test_a_closed_session_is_counted_by_outcome():
    metrics = Metrics()
    runner, _s, _l = _runner(agent=_Agent(), metrics=metrics)
    await _wake(runner, (_stim(1, HOME),))
    assert metrics.get("nora_cognition_session_ended_total", outcome="closed") == 1
    assert metrics.get("nora_cognition_session_total", mode="live") == 1


# ---- the agent runner: the guard and the trace -------------------------------

class _Guard:
    seen: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **kw: Any) -> None:
        type(self).seen.append(kw)


class _Models:
    def create(self) -> object:
        return object()


def _patch_agent(monkeypatch: pytest.MonkeyPatch, invoke: Any) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []

    @contextmanager
    def fake_span(name: str, **attrs: object):
        spans.append({"name": name, **attrs})
        yield

    def fake_annotate(**attrs: object) -> None:
        spans.append({"annotate": attrs})

    _Guard.seen = []
    monkeypatch.setattr(agent_runner_module, "DreamGuard", _Guard)
    monkeypatch.setattr(agent_runner_module, "span", fake_span)
    monkeypatch.setattr(agent_runner_module, "annotate", fake_annotate)
    monkeypatch.setattr(factory, "create_dream_agent", lambda **kw: object())
    monkeypatch.setattr(factory, "invoke", invoke)
    return spans


def _live(**kw: Any) -> LiveCognitionAgentRunner:
    return LiveCognitionAgentRunner(
        models=_Models(),
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0), **kw,
    )


async def _go(runner: LiveCognitionAgentRunner) -> Any:
    return await runner.run(
        system_prompt="s", prompt="p", tools=[_tool("note_read"), _tool("close")],
        budget=AdaptorBudget(
            turns=1, total_tokens=1, output_tokens=1, timeout_seconds=5,
            max_memory_calls=6, max_tool_calls=60,
        ),
        trace_attributes={"cognition.unit": "u", "cognition.session_id": "7"},
    )


async def test_a_session_is_one_root_span_that_stays_in_langfuse_and_names_its_outcome(
    monkeypatch,
):
    async def invoke(*a: Any, **k: Any) -> Any:
        return SimpleNamespace(stop_reason="end_turn")

    spans = _patch_agent(monkeypatch, invoke)
    result = await _go(_live())
    assert result.stop_reason == "end_turn"
    root = spans[0]
    assert root["name"] == "dream:cognition"
    assert root["nora.trace_kind"] == "generation"          # the sampler keeps generations
    assert root["nora.dreamer.adaptor"] == "cognition"
    assert (root["nora.cognition.unit"], root["nora.cognition.session_id"]) == ("u", "7")
    assert {"annotate": {"nora.outcome": "ok"}} in spans


@pytest.mark.parametrize(("error", "outcome"), [(RuntimeError("x"), "failed"),
                                                  (TimeoutError(), "timeout")])
async def test_a_failed_or_timed_out_session_names_its_outcome_and_still_raises(
    monkeypatch, error, outcome,
):
    async def invoke(*a: Any, **k: Any) -> Any:
        raise error

    spans = _patch_agent(monkeypatch, invoke)
    with pytest.raises(type(error)):
        await _go(_live())
    assert {"annotate": {"nora.outcome": outcome}} in spans


async def test_the_guard_holds_the_safety_checks_and_no_call_ceiling(monkeypatch):
    """R-99's accepted argument names and the result caps are kept; the per-tool
    ceilings, the per-pass memory ceiling and the pass-wide ceiling are not
    built at all (DEC-247)."""
    async def invoke(*a: Any, **k: Any) -> Any:
        return SimpleNamespace(stop_reason="end_turn")

    _patch_agent(monkeypatch, invoke)
    await _go(_live(
        param_names={"note_read": frozenset({"x"})}, result_caps={"about": 4000},
    ))
    (guard,) = _Guard.seen
    assert guard["adaptor"] == "cognition"
    assert guard["per_tool_ceilings"] is False
    assert set(guard["budgets"]) == {"note_read", "close"}       # the membership set
    assert "memory_budget" not in guard and "pass_budget" not in guard
    assert guard["param_names"] == {"note_read": frozenset({"x"})}
    assert guard["result_caps"] == {"about": 4000}


# ---- skills, shutdown, the boot check -----------------------------------------

def test_agenda_skills_inject_in_a_fixed_order_and_record_their_digests():
    skills = {
        "plans": SimpleNamespace(instructions="PLANS", sha256="p1"),
        "places": SimpleNamespace(instructions="PLACES", sha256="q1"),
    }
    injected = AgendaSkills(
        by_kind={"message": "plans", "self": "places"}, resolve=skills.get,
    )
    text = injected.text_for(["self", "message", "time"])   # `time` has no skill
    assert text.index("PLACES") < text.index("PLANS")        # name order, not kind order
    assert injected.versions_for(["message", "self"]) == {"plans": "p1", "places": "q1"}
    assert injected.text_for(["time"]) == "" and injected.versions_for(["time"]) == {}


def test_a_skill_the_table_names_but_nothing_installed_is_an_error_not_an_empty_string():
    injected = AgendaSkills(by_kind={"message": "plans"}, resolve=lambda name: None)
    with pytest.raises(LookupError, match="plans"):
        injected.text_for(["message"])


async def test_shutdown_cancels_the_evaluators_sessions_then_the_runners_and_only_once():
    order: list[str] = []

    class Evaluator:
        async def wait_in_flight(self, timeout_seconds: float) -> None:
            order.append(f"grace {timeout_seconds:g}")

        async def cancel_in_flight(self) -> None:
            order.append("evaluator")

    class Runner:
        async def shutdown(self) -> None:
            order.append("runner")

    stop = CognitionShutdown(evaluator=Evaluator(), runner=Runner(), grace_seconds=30.0)
    await stop()
    await stop()                                   # a second shutdown is a no-op
    # The grace comes FIRST: sessions already running may finish before they are cancelled.
    assert order == ["grace 30", "evaluator", "runner"] and stop.done


def test_units_without_a_home_room_are_named_not_woken():
    homes = {"friends": SimpleNamespace(platform="signal", primary_id="g1")}
    assert units_with_home_rooms(["friends", "wedding"], homes) == (["friends"], ["wedding"])


def test_the_boot_check_refuses_an_enabled_loop_that_built_no_session():
    hooked = SimpleNamespace(has_wake_hook=True)
    bare = SimpleNamespace(has_wake_hook=False)
    require_session_hook(enabled=False, runner=None, evaluator=None)     # off asks for none
    require_session_hook(enabled=True, runner=object(), evaluator=hooked)
    with pytest.raises(RuntimeError, match="no session runner"):
        require_session_hook(enabled=True, runner=None, evaluator=hooked)
    with pytest.raises(RuntimeError, match="no session hook"):
        require_session_hook(enabled=True, runner=object(), evaluator=bare)
    with pytest.raises(RuntimeError, match="no session hook"):
        require_session_hook(enabled=True, runner=object(), evaluator=None)


def test_the_boot_check_refuses_shadow_when_no_unit_can_run_a_session():
    """Units without a home room stay in the evaluator
    (their would-wakes are still recorded), but if NONE can start a session a
    shadow week would record would-wakes and run nothing, and look healthy."""
    hooked = SimpleNamespace(has_wake_hook=True)
    require_session_hook(
        enabled=True, runner=object(), evaluator=hooked, session_units={"friends"},
    )
    with pytest.raises(RuntimeError, match="no unit can run a session"):
        require_session_hook(
            enabled=True, runner=object(), evaluator=hooked, session_units=frozenset(),
        )
    require_session_hook(                            # off asks for none
        enabled=False, runner=None, evaluator=None, session_units=frozenset(),
    )


async def test_the_app_stops_the_cognition_sessions_before_it_closes_the_client_and_the_audit():
    """`App.shutdown` (the `finally` of `main`) cancels the sessions
    BEFORE the model client and the database they use are closed, and stopping
    twice is harmless."""
    from nora.__main__ import App

    order: list[str] = []

    class _Stop:
        async def __call__(self) -> None:
            order.append("cognition")

    class _Closable:
        def __init__(self, name: str) -> None:
            self._name = name

        async def close(self) -> None:
            order.append(self._name)

        aclose = close

    deps = SimpleNamespace(
        cognition_shutdown=_Stop(), subagent_backend=None,
        client=_Closable("client"), audit=_Closable("audit"),
    )
    app = App(cfg=None, deps=deps)              # type: ignore[arg-type]
    await app.shutdown()
    assert order == ["cognition", "client", "audit"]
    deps.cognition_shutdown = None              # sessions off: nothing to stop, nothing breaks
    order.clear()
    await app.shutdown()
    assert order == ["client", "audit"]


# ---- the eval seam: `tool_selection_cognition` runs through `probe` -----------

def _case(text: str = "room A has 3 new messages proposing a time and place") -> Any:
    return SimpleNamespace(input=text)


async def test_the_cognition_corpus_is_driven_through_the_sessions_probe():
    from evals.rebuilt.entry import _cognition_probe

    calls: list[dict[str, Any]] = []

    class _Runner:
        async def probe(self, **kw: Any) -> None:
            calls.append(kw)

    subject = SimpleNamespace(deps=SimpleNamespace(cognition_runner=_Runner()), unit="friends")
    outcome = await _cognition_probe(subject, _case(), now_ms=NOW)
    assert outcome.error is None
    assert calls == [{
        "unit": "friends", "summary": "room A has 3 new messages proposing a time and place",
        "now_ms": NOW,
    }]


async def test_a_subject_with_no_session_runner_is_an_error_never_a_pass():
    """Sessions off in the eval config: a case that reached no model has
    measured nothing, whatever it prints."""
    from evals.rebuilt.entry import _cognition_probe

    subject = SimpleNamespace(deps=SimpleNamespace(cognition_runner=None), unit="friends")
    outcome = await _cognition_probe(subject, _case(), now_ms=NOW)
    assert outcome.source == "fail_closed" and "[cognition] is off" in (outcome.error or "")


async def test_a_probe_that_raised_is_an_error_never_a_pass():
    from evals.rebuilt.entry import _cognition_probe

    class _Runner:
        async def probe(self, **kw: Any) -> None:
            raise RuntimeError("the provider fell over")

    subject = SimpleNamespace(deps=SimpleNamespace(cognition_runner=_Runner()), unit="friends")
    outcome = await _cognition_probe(subject, _case(), now_ms=NOW)
    assert outcome.source == "fail_closed" and "fell over" in (outcome.error or "")


# ---- the objective: what she is told she is reading ---------------

def _objective() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[2] / "nora" / "dreamer" / "objectives"
            / "cognition.md").read_text()


def test_the_objective_names_every_close_reason_the_session_closes_with():
    """`close`'s tool schema offers `written`; an instruction that listed three
    of the four told her a reason the tool accepts was not hers to use."""
    from nora.domain.cognition_session import CLOSE_REASONS

    text = _objective()
    for reason in CLOSE_REASONS:
        if reason == "struck":                      # code's own close, never her argument
            continue
        assert f"- **{reason}**" in text, reason


def test_the_objective_frames_what_she_reads_and_does_not_list_write_kinds():
    """Context framing, not a capability list (AGENTS.md):
    it says what she is handed, in what order, and what is already written down,
    and it does not enumerate the kinds of write she can make."""
    text = _objective()
    # the agenda it describes is the one the renderer produces: blocks, in order
    for phrase in ("a clock, which room your read tools", "the agenda: one `item`",
                   "the group's open notes", "its open events", "marked **returning**"):
        assert phrase in text, phrase
    # the judgement about a returning item, restored: the words may have moved
    assert "the\ngroup's words may have moved since" in text
    # no enumeration of the write verbs (they carry their own descriptions)
    assert "Each note, event, Standing revision" not in text
    assert "note_create" not in text and "event_propose" not in text


def test_the_objective_names_none_of_the_old_shapes_of_the_input():
    """The input is an `agenda` of `item`s, then notes and events. The objective
    uses none of the older line-list vocabulary («the list», «the lines under
    it», «below the list», «the next list»); none of it describes anything the
    model is handed."""
    text = _objective().split("---\n", 2)[2]       # the instruction, not the changelog
    for old in ("[#", "min ago)", "(edited)", "[cues:", "[room ", "Nora (you)",
                "(returning:", "earlier messages not shown",
                "(one of these could not be shown)", "Below the list", "under it",
                "next list", "the list", "the lines", "agenda_item"):
        assert old not in text, old


def test_the_objective_teaches_nothing_the_block_types_already_say():
    """The shape of the input (a message's id, the name to put in a note line, an
    edit, a cue, a reaction, what a cut means, the section order) reaches the
    model through the block types (`nora.domain.block_types`), so the objective
    quotes none of it, and each of those sentences is PINNED where it lives."""
    from nora.domain.block_types import BLOCK_TYPES

    text = _objective()
    for said_by_the_type in ("cite it exactly as [n]", "in parentheses before the ids",
                             "Decided, Open, Owed, Proposed, Ruled out"):
        assert said_by_the_type not in text
    agenda = BLOCK_TYPES["agenda"]
    fields = dict(agenda.annotations)
    # the grammar: what each field is for, in the words the model reads
    assert "cite it exactly as [n] at the end of the note line it supports" in fields["n"]
    assert "or as digits in an event's cited ids" in fields["n"]
    assert "this exact name, as written, goes in parentheses before the ids" in fields["from"]
    assert "Decided, Proposed or Ruled out" in fields["from"]
    assert "its sections run Decided, Open, Owed, Proposed, Ruled out" in agenda.description
    # the citation grammar is said ONCE, in the fields; the description points at them
    assert "as the `n` and `from` fields say" in agenda.description
    assert "[n]" not in agenda.description
    assert agenda.description.count("(names)") == 1          # only «takes no (names)»
    # and the two line shapes nothing else taught, quoted from the validator
    assert "An Owed line is `<name> -> <what> -> <due> [<ids>]`" in agenda.description
    assert "the name is someone on the room's roster and never you" in agenda.description
    assert "`when <event>` or `no date given`" in agenda.description
    assert "takes no `(names)`" in agenda.description
    assert "A Ruled out line is `<option>: <reason> (<names>) [<ids>]`" in agenda.description
    # which field is the message's REAL id, and the note's, and where the room key is
    assert fields["n"].startswith("a message's real id: ")
    assert dict(BLOCK_TYPES["note_index"].annotations)["n"].startswith("the note's real id")
    assert "`platform:conversation_id`" in BLOCK_TYPES["reads"].description
    # and the rest of what the input carries, said on the fields
    assert "changed after they were said" in fields["edited"]
    assert "what code noticed" in fields["cues"] and "never a reading of it" in fields["cues"]
    assert "not readable now" in fields["unreadable"]
    assert "left out to fit" in fields["earlier_not_shown"]
    assert "first and last message ids" in fields["earlier_range"]
    assert "how many earlier looks left the item open" in fields["returning"]
    assert "what you sent" in agenda.description and "marked `own`" in agenda.description
    assert "context only, never cited" in fields["own"]
